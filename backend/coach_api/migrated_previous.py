"""Stored-only previous Aptem review context. Never initializes or saves a form."""
from django.db import connections
from django.http import JsonResponse

from learner_api.review_history import _json_value, _sections_by_review, _serialize_review

from .migrated_reviews import FAMILY_BY_SOURCE_TYPE, booking_event_type, review_family, source_is_completed
from .migrated_summary_binding import binding_state
from .models import ImportedReviewInstance
from .review_sources import ReviewIdentityConflict, booking_for_overlay


def unavailable(reason):
    return JsonResponse({
        "available": False, "reason": reason, "instance": None, "review": None,
        "summaryText": "", "transcriptText": "",
        "transcriptAvailable": False, "transcriptTruncated": False,
    })


def _text(value):
    return str(value or "").strip()


def _normal(value):
    return " ".join(_text(value).split()).casefold()


def _sources(profile_id, family):
    from .views import get_learner_db_alias

    types = [key for key, value in FAMILY_BY_SOURCE_TYPE.items() if value == family]
    with connections[get_learner_db_alias()].cursor() as cursor:
        cursor.execute('''
            SELECT id, learner_id, aptem_review_id, review_name, review_type,
                   planned_scheduled_date, completed_date, status, review_data
            FROM "Learner".reviews
            WHERE learner_id = %s AND LOWER(BTRIM(review_type)) = ANY(%s)
        ''', [profile_id, types])
        columns = [column[0] for column in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        sections = _sections_by_review(cursor, [row["id"] for row in rows])
    return [(row, _serialize_review(row, sections, historical_presentation=True)) for row in rows]


def _source_identity(row, profile_id, aptem_id):
    data = _json_value(row.get("review_data"), dict, {})
    odata = _json_value(data.get("live_odata"), dict, {})
    return (
        row["learner_id"] == profile_id and bool(_text(row.get("aptem_review_id")))
        and all(_text(value) == str(aptem_id) for value in (
            data.get("aptem_learner_id"), odata.get("LearnerId"),
        ) if value not in (None, ""))
        and all(_text(value) == str(row["aptem_review_id"]) for value in (
            data.get("aptem_review_id"), odata.get("Id"),
        ) if value not in (None, ""))
    )


def _programme(row, review):
    data = _json_value(row.get("review_data"), dict, {})
    odata = _json_value(data.get("live_odata"), dict, {})
    return _normal(review.get("historicalProgramme") or odata.get("ProgramName"))


def _historical_summary(review):
    # This reads a captured Aptem section, not a mapping of editable/AI fields.
    sections = [s for s in review.get("sections", []) if _normal(s.get("name")) == "meeting summary"]
    if len(sections) != 1:
        return ""
    section = sections[0]
    values = [field["value"].strip() for field in section.get("fields", [])
              if isinstance(field, dict) and isinstance(field.get("value"), str)
              and field["value"].strip() not in {"", "EMPTY_STRING"}]
    return "\n\n".join(values) if values else _text(section.get("rawText"))


def previous_review(owner, event_key):
    from . import views

    owner = owner.strip().casefold()
    learners = views.fetch_caseload_dashboard_profiles(owner)
    definition = views._imported_review_definition(owner, event_key, pdf_only=True, learners=learners)
    if not definition:
        return JsonResponse({"detail": "Imported review not found for this coach."}, status=404)
    profile_id = definition["instance"]["learnerId"]
    aptem_ids, conflicts = views.resolve_effective_aptem_ids(learners)
    if profile_id in conflicts or profile_id not in aptem_ids:
        return JsonResponse({"detail": "Imported review identity could not be verified."}, status=404)
    family = review_family(definition["historicalReview"].get("type"))
    if not family:
        return unavailable("Previous session context is not available for this review type.")

    sources = _sources(profile_id, family)
    current_id = str(definition["historicalReview"]["id"])
    current = [(row, review) for row, review in sources if str(row["id"]) == current_id]
    if len(current) != 1 or not _source_identity(current[0][0], profile_id, aptem_ids[profile_id]):
        return JsonResponse({"detail": "Imported review identity could not be verified."}, status=404)
    current_row, current_review = current[0]
    target = current_review["plannedDate"]
    if not target:
        return unavailable("This imported review has no planned date, so its previous session cannot be identified.")

    programme = _programme(current_row, current_review) or _normal(definition.get("programme"))
    candidates = []
    for row, review in sources:
        if str(row["id"]) == current_id:
            continue
        # The learner profile is the enrolment boundary. Explicit captured
        # programme metadata additionally excludes reviews from an older programme.
        prior_programme = _programme(row, review)
        if programme and prior_programme and programme != prior_programme:
            continue
        planned = review["plannedDate"]
        if not planned:
            return unavailable("An imported review has no planned date, so the previous session order cannot be verified.")
        if planned == target:
            return unavailable("Multiple imported reviews share this planned date. The previous session is ambiguous.")
        if planned < target:
            candidates.append((row, review))
    if not candidates:
        return unavailable("There is no previous imported session for this review type and programme.")
    latest = max(review["plannedDate"] for _, review in candidates)
    previous = [(row, review) for row, review in candidates if review["plannedDate"] == latest]
    if len(previous) != 1:
        return unavailable("Multiple imported reviews share the previous planned date. The previous session is ambiguous.")
    row, review = previous[0]
    if not _source_identity(row, profile_id, aptem_ids[profile_id]):
        return unavailable("The previous imported review's learner identity could not be verified.")

    overlays = list(ImportedReviewInstance.objects.filter(source_review_id=row["id"])[:2])
    if len(overlays) > 1:
        return unavailable("The previous imported review has conflicting working copies.")
    overlay = overlays[0] if overlays else None
    if overlay and (overlay.learner_id != profile_id or overlay.owner_email.casefold() != owner):
        return unavailable("The previous session is not assigned to this coach.")
    # Recheck the selected source through the same caseload gate as a form read.
    key = overlay.event_key if overlay else f"imported-review:{review['aptemReviewId']}"
    prior_definition = views._imported_review_definition(owner, key, pdf_only=True, learners=learners)
    if (not prior_definition or prior_definition["instance"]["learnerId"] != profile_id
            or str(prior_definition["historicalReview"]["id"]) != str(row["id"])):
        return unavailable("The previous session is no longer available to this coach.")

    historical = source_is_completed(review)
    status = "completed" if historical else overlay.status if overlay else review["status"]
    if status not in {"in-progress", "awaiting-signature", "completed"}:
        return unavailable("The previous session has not started yet.")
    summary = _historical_summary(review) if historical else ""
    transcript = ""
    if overlay and not historical:
        source = (overlay.template_snapshot or {}).get("templateSource") or {}
        if source.get("family") and source["family"] != family:
            return unavailable("The previous review's template does not match its imported type.")
        # Global templates carry no programme key. Never compare Aptem programme
        # IDs with LMS IDs; only a frozen LMS programme key uses this namespace.
        expected_key = views.migrated_programme_key(definition.get("programmeId"), definition.get("programme"))
        if source.get("programmeKey") and source["programmeKey"] != expected_key:
            return unavailable("The previous review belongs to a different programme.")
        binding = binding_state(overlay)
        if binding.get("status") == "invalid-binding":
            return unavailable("The previous review's saved summary mapping needs attention.")
        if binding.get("answerPresent"):
            value = binding.get("answer")
            summary = value.strip() if isinstance(value, str) else ""
        try:
            record = booking_for_overlay(overlay)
        except ReviewIdentityConflict:
            return unavailable("The previous session's calendar association could not be verified.")
        if record:
            if record.owner_email.casefold() != owner or record.event_type != booking_event_type(family):
                return unavailable("The previous session's calendar association could not be verified.")
            # Previous imported Reviews are bound to a verified Teams meeting,
            # not to the appointment's planned time window. Keep source/event
            # identity checks, then read only transcript IDs selected for this
            # exact saved meeting. Native callers keep the legacy selector.
            from .migrated_session_scope import stored_transcript_selection

            snapshot = views.stored_coach_meeting_snapshot(record)
            selection = stored_transcript_selection(record, snapshot, meeting_scoped=True)
            stored = (views.stored_coach_meeting_transcript_for_summary(
                record, artifact_ids=selection['artifactIds'])
                if selection['code'] is None and selection['artifactIds'] else None)
            transcript = _text(stored.get("text")) if stored else ""
            if binding.get('status') == 'no-binding':
                from .migrated_session_scope import summary_matches_session
                state = overlay.meeting_intelligence or {}
                if (state.get('summaryStatus') in {'ready', 'edited'}
                        and (state.get('source') == 'upload' or summary_matches_session(state, record, stored))):
                    summary = views.meeting_summary_plain_text(state.get('summary') or {})

    limit = views.COACH_MEETING_SUMMARY_TRANSCRIPT_CHARS
    completed_at = review["completedDate"] if historical else overlay.completed_at if overlay else None
    return JsonResponse({
        "available": True, "reason": "",
        "instance": {
            "id": key, "occurrenceNumber": None, "targetDate": review["plannedDate"],
            "completedAt": completed_at.isoformat() if hasattr(completed_at, "isoformat") else completed_at,
            "status": status,
        },
        "review": {"name": review["name"], "reviewTemplateId": None,
                   "reviewTypeCode": "aptem_mcm" if family == "MCM" else "aptem_progress_review"},
        "summaryText": summary, "transcriptText": transcript[:limit],
        "transcriptAvailable": bool(transcript), "transcriptTruncated": len(transcript) > limit,
    })
