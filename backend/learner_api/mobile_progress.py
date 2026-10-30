"""Mobile course projections over saved canonical learner records; no persistence."""
from datetime import datetime, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo
from .progress_rules import progress_counts_as_achieved

UK = ZoneInfo("Europe/London")


def _saved_rows(detail):
    for field, kind in (("componentProgress", "component"), ("videoProgress", "video"), ("quizAttempts", "quiz")):
        for value in detail.get(field, []) or []:
            if isinstance(value, dict):
                yield field, {**value, "kind": value.get("kind") or kind}


def _scope(detail, module_id):
    components = [row for row in detail.get("components", [])
                  if isinstance(row, dict) and row.get("moduleId") == module_id and row.get("componentId")]
    by_id = {str(row["componentId"]): row for row in components}
    quiz_components = {}
    for row in components:
        meta = row.get("quizMeta") or {}
        qid = meta.get("quizId") if isinstance(meta, dict) else None
        if row.get("isQuiz") and qid is not None and not isinstance(qid, bool):
            quiz_components.setdefault(str(qid), []).append(str(row["componentId"]))
    return components, by_id, quiz_components


def _row_component_ids(row, by_id, quizzes):
    cid = str(row.get("componentId") or "")
    if row.get("kind") == "quiz":
        # The maintained projection can retain an exact component association
        # after its quiz bank changes. Never credit a different course's slot
        # merely because it now references the same quiz ID.
        if cid:
            return [cid] if cid in by_id else []
        return quizzes.get(str(row.get("quizId")), [])
    return [cid] if cid in by_id else []


def project_progress(detail, module_id):
    components, by_id, quizzes = _scope(detail, module_id)
    markings = detail.get("componentMarkingStatus") or {}
    submitted, completed, pending_ids, rows = set(), set(), set(), []
    filtered = {"componentProgress": [], "videoProgress": [], "quizAttempts": []}
    for field, row in _saved_rows(detail):
        ids = _row_component_ids(row, by_id, quizzes)
        if not ids:
            continue
        filtered[field].append(row)
        submitted.update(ids)
        achieved = progress_counts_as_achieved(row.get("kind"), row.get("passed"))
        accepted_ids = []
        for cid in ids:
            marking = markings.get(cid) or {}
            if achieved and (not by_id[cid].get("tutorValidationRequired") or marking.get("status") == "accepted"):
                completed.add(cid)
                accepted_ids.append(cid)
        pending_review = any(by_id[cid].get("tutorValidationRequired") and
            (markings.get(cid) or {}).get("status") == "submitted_for_tutor_review" for cid in ids)
        pending_ids.update(cid for cid in ids if by_id[cid].get("tutorValidationRequired") and
            (markings.get(cid) or {}).get("status") == "submitted_for_tutor_review")
        rows.append((row, bool(accepted_ids), pending_review))
    order = list(by_id)
    return {
        **filtered,
        "componentMarkingStatus": {cid: markings[cid] for cid in order if cid in markings},
        "completedComponentIds": [cid for cid in order if cid in completed],
        "submittedComponentIds": [cid for cid in order if cid in submitted],
        "pendingReviewComponentIds": [cid for cid in order if cid in pending_ids and cid not in completed],
    }, rows


def _instant(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _monday(instant):
    day = instant.astimezone(UK).date()
    return day - timedelta(days=day.weekday())


def measured_time(rows, module_id, now=None):
    """Signed active aggregates only. Never invent active intervals or week splits."""
    current = now or datetime.now(timezone.utc)
    start = _monday(current)
    end = start + timedelta(days=6)
    sessions, conflicts = {}, set()
    unmeasured = 0
    for entry in rows:
        row, achieved = entry[:2]
        pending = bool(entry[2]) if len(entry) > 2 else False
        token = row.get("timeTrackingSessionId")
        seconds = row.get("verifiedSeconds")
        provenance = str(row.get("timeTrackingSource") or "")
        if (not isinstance(token, str) or not token
                or not provenance.startswith("signed_session_capped_active_")
                or provenance.endswith(":input")
                or isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                or not isfinite(seconds) or seconds < 0):
            unmeasured += 1
            continue
        # These are the server-signed session bounds. A manually declared date is
        # not evidence of when the active seconds occurred.
        began, finished = _instant(row.get("startedAt")), _instant(row.get("submittedAt"))
        valid_span = (began is not None and finished is not None and finished >= began
                      and finished <= current and seconds <= (finished-began).total_seconds()+0.002)
        value = (float(seconds), began if valid_span else None,
                 finished if valid_span else None, bool(achieved), pending)
        if token in sessions and sessions[token] != value:
            conflicts.add(token)
        else:
            sessions[token] = value

    # Quarantine both/all overlapping aggregate envelopes. They may contain idle
    # time, so proportional subtraction or keeping one would invent evidence.
    overlaps = set()
    dated = sorted((value[1], value[2], token) for token, value in sessions.items()
                   if token not in conflicts and value[1] is not None and value[0] > 0)
    cluster, cluster_end = [], None
    for began, finished, token in dated:
        if cluster_end is not None and began < cluster_end:
            cluster.append(token)
            overlaps.update(cluster)
            cluster_end = max(cluster_end, finished)
        else:
            cluster, cluster_end = [token], finished

    totals = {key: 0.0 for key in (
        "weeklySubmittedSeconds", "weeklyCompletedActivitySeconds", "weeklyPendingReviewSeconds",
        "moduleSubmittedSeconds", "moduleCompletedActivitySeconds", "modulePendingReviewSeconds",
        "unallocatedMeasuredSeconds", "excludedOverlapSeconds")}
    unallocated_count = 0
    for token, (seconds, began, finished, achieved, pending) in sessions.items():
        if token in conflicts:
            continue
        if token in overlaps:
            totals["excludedOverlapSeconds"] += seconds
            continue
        totals["moduleSubmittedSeconds"] += seconds
        if achieved:
            totals["moduleCompletedActivitySeconds"] += seconds
        if pending:
            totals["modulePendingReviewSeconds"] += seconds
        # A half-open envelope ending exactly Monday midnight belongs wholly to
        # the previous week. Cross-week or missing/invalid bounds are unallocated.
        last_instant = max(began, finished - timedelta(microseconds=1)) if began else None
        if began is None or _monday(began) != _monday(last_instant):
            totals["unallocatedMeasuredSeconds"] += seconds
            unallocated_count += 1
        elif _monday(began) == start:
            totals["weeklySubmittedSeconds"] += seconds
            if achieved:
                totals["weeklyCompletedActivitySeconds"] += seconds
            if pending:
                totals["weeklyPendingReviewSeconds"] += seconds
    return {
        "source": "lms", "moduleId": module_id,
        "weekStart": start.isoformat(), "weekEnd": end.isoformat(),
        "periodLabel": f"{start.day} {start.strftime('%b')} - {end.day} {end.strftime('%b %Y')}",
        "timezone": "Europe/London", "basis": "saved_signed_active_seconds",
        **{key: round(value, 3) for key, value in totals.items()},
        "weeklyAcceptedOtjhSeconds": None, "programmeMeasuredSeconds": None,
        "acceptedMeasuredTimeAvailable": False, "crossDeviceOverlapReconciled": False,
        "excludedUnmeasuredRows": unmeasured, "excludedAmbiguousSessions": len(conflicts),
        "excludedOverlappingSessions": len(overlaps), "unallocatedMeasuredSessions": unallocated_count,
        "note": "Saved signed study is separate from accepted OTJ hours. Overlapping session envelopes are excluded; cross-week or invalid bounds remain unallocated. Planned/manual hours never enter these totals.",
    }
