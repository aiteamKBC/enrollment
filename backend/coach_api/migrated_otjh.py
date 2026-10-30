"""Prospective OTJ credit for completed Aptem continuation reviews.

Configured hours are a review allowance, not measured Teams attendance. A
completion writes one canonical activity in the same transaction as signatures.
Existing completed reviews and imported historical hours are never backfilled.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
from zoneinfo import ZoneInfo
from types import SimpleNamespace

from django.db import connection, transaction, OperationalError
from django.utils import timezone

UK = ZoneInfo("Europe/London")
BASIS = "migrated-review:configured-hours"


def policy(definition):
    value = definition.get("otjh", {"expectedHours": 0, "countsTowardsOtjh": False})
    if not isinstance(value, dict) or set(value) != {"expectedHours", "countsTowardsOtjh"}:
        raise ValueError("OTJH settings need expectedHours and countsTowardsOtjh only.")
    if type(value["countsTowardsOtjh"]) is not bool or isinstance(value["expectedHours"], bool):
        raise ValueError("OTJH requires a numeric hours value and a true/false counting setting.")
    try:
        hours = Decimal(str(value["expectedHours"]))
    except (InvalidOperation, ValueError):
        raise ValueError("Expected OTJH must be a number.") from None
    if not hours.is_finite() or not 0 <= hours <= Decimal("999999.99") or hours != hours.quantize(Decimal("0.01")):
        raise ValueError("Expected OTJH must be non-negative with at most two decimal places (maximum 999999.99).")
    return {"expectedHours": float(hours), "countsTowardsOtjh": value["countsTowardsOtjh"]}


def hours_for(definition):
    settings = policy(definition)
    return settings["expectedHours"] if settings["countsTowardsOtjh"] else 0.0


def frozen_hours(overlay):
    credit = (overlay.meeting_intelligence or {}).get("otjhCredit")
    if credit:
        return credit["hours"]
    if (overlay.meeting_intelligence or {}).get("reopenCompletedAt"):
        return 0.0
    return hours_for(overlay.template_snapshot) if overlay.status == "awaiting-signature" else 0.0


def table(name):
    return f'"Learner"."{name}"' if connection.vendor == "postgresql" else f'"otjh_test_{name}"'


def query(sql, params=()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return [dict(zip([col[0] for col in cursor.description], row)) for row in cursor.fetchall()] if cursor.description else []


def record_completion(overlay):
    """Caller owns the overlay lock; never re-credit or revalue a completion."""
    if not connection.in_atomic_block:
        raise RuntimeError("Review OTJH credit requires the completion transaction.")
    hours = hours_for(overlay.template_snapshot)
    if hours <= 0:
        return
    if overlay.status != "completed" or not overlay.completed_at or not overlay.source_review_id:
        raise ValueError("OTJH requires a completed review with a source association.")
    if (overlay.meeting_intelligence or {}).get("otjhCredit"):
        raise ValueError("This review already has an OTJH completion receipt.")
    suffix = " FOR UPDATE" if connection.vendor == "postgresql" else ""
    owners = query(f'SELECT id,enrolment_id,aptem_id FROM {table("learners")} WHERE id=%s{suffix}', [overlay.learner_id])
    sources = query(f'''SELECT id,learner_id,aptem_review_id,status,completed_date
        FROM {table("reviews")} WHERE id=%s AND learner_id=%s''', [overlay.source_review_id, overlay.learner_id])
    if (not owners or not owners[0]["enrolment_id"] or not owners[0]["aptem_id"] or not sources
            or str(sources[0]["status"] or "").strip().casefold() == "completed" or sources[0]["completed_date"]
            or overlay.event_key != f'imported-review:{sources[0]["aptem_review_id"]}'):
        raise ValueError("The review OTJH source association needs review; no hours were recorded.")
    # Only explicit reconciliations establish Native equivalence. Never match
    # unrelated learners or meetings by dates, names or duration.
    from .reconciliation_links import active_links
    from curriculum_api.review_instances import get_review_instance
    from curriculum_api.review_otjh import template_otjh_hours
    from curriculum_api.reviews import get_review_template_row
    for link in active_links(learner_id=overlay.learner_id, overlay_id=overlay.pk):
        native = get_review_instance(link.native_review_instance_id)
        if native and native.get("status") == "completed" and template_otjh_hours(get_review_template_row(link.native_template_id, include_deleted=True)):
            raise ValueError("This review already has Native OTJH credit. Review the linked hours before completing it.")
    reference = f"migrated-review:{overlay.source_review_id}"
    existing = query(f'''SELECT id FROM {table("learner_progress_entries")}
        WHERE learner_id=%s AND canonical_activity_key=%s''', [overlay.learner_id, reference])
    if existing:
        raise ValueError("This review already has an Activity Log allocation; no duplicate hours were recorded.")
    owner = owners[0]
    at = overlay.completed_at
    # Monthly Log signing locks the enrolment first. Existing review writers
    # already hold the learner/calendar locks, so do not wait in reverse order.
    if connection.vendor == "postgresql":
        try:
            with transaction.atomic():
                accounts = query('SELECT id FROM enrolment."Created_users" WHERE id=%s FOR UPDATE NOWAIT', [owner["enrolment_id"]])
                if not accounts:
                    raise ValueError("The review learner account is unavailable.")
        except OperationalError as exc:
            if getattr(exc.__cause__, "sqlstate", None) == "55P03":
                raise ValueError("The learner's hours are being updated. Please retry the review completion.") from exc
            raise
    from curriculum_api.learner_activity_status import require_open_month
    from old_otjh.service import ServiceError
    try:
        require_open_month(owner, at.astimezone(UK).strftime("%Y-%m"))
    except ServiceError as exc:
        raise ValueError(str(exc)) from exc
    seconds = Decimal(str(hours)) * 3600
    payload = {"original_source_ref": reference, "reviewOtjh": {
        "overlayId": overlay.pk, "sourceReviewId": overlay.source_review_id,
        "eventKey": overlay.event_key, "templateId": overlay.migrated_template_id,
        "hours": hours, "basis": BASIS, "completedAt": at.isoformat()}}
    rows = query(f'''INSERT INTO {table("learner_progress_entries")}
        (learner_id,enrolment_id,entry_order,kind,module_title,week_title,component_ref,component_title,
         component_type,expected_otjh,actual_seconds,actual_basis,reporting_started_at,
         reporting_ended_at,reporting_month,reporting_timestamp_label,source_system,
         canonical_activity_key,component_link_source,component_link_status,accepted,
         activity_status,submitted_at,source_payload)
        SELECT %s,%s,coalesce(max(entry_order),0)+1,'migrated_review','','',%s,%s,'review',%s,%s,%s,
            %s,%s,%s,'Review completion','lms_review',%s,'migrated_review','non_component_activity',true,
            'Completed',%s,%s FROM {table("learner_progress_entries")} WHERE learner_id=%s RETURNING id''',
        [owner["id"], owner["enrolment_id"], reference, overlay.template_snapshot.get("name") or "Review",
         hours, seconds, BASIS, at, at, at.astimezone(UK).strftime("%Y-%m"), reference, at,
         json.dumps(payload), owner["id"]])
    # Retain the existing aggregate, including imported hours; don't rebuild
    # history from expected durations. The learner lock serializes this increment.
    query(f'''UPDATE {table("learners")} SET completed_hours=completed_hours+%s WHERE id=%s''', [hours, owner["id"]])
    overlay.meeting_intelligence = {**deepcopy(overlay.meeting_intelligence or {}),
        "otjhCredit": {**payload["reviewOtjh"], "progressId": rows[0]["id"]}}


def day(value):
    if isinstance(value, datetime):
        return value.astimezone(UK).date() if timezone.is_aware(value) else value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10]) if value else None


def source_rows(profile_ids):
    identity = "review_data ->> 'aptem_learner_id'" if connection.vendor == "postgresql" else "json_extract(review_data, '$.aptem_learner_id')"
    metadata = """jsonb_build_object('source_metadata',jsonb_build_object(
        'Planned / Scheduled Date',review_data #>> '{source_metadata,Planned / Scheduled Date}',
        'Completed Date',review_data #>> '{source_metadata,Completed Date}',
        'Status',review_data #>> '{source_metadata,Status}'))""" if connection.vendor == "postgresql" else "review_data"
    placeholders = ",".join(["%s"] * len(profile_ids))
    return query(f'''SELECT id,learner_id,aptem_review_id,review_type,planned_scheduled_date,completed_date,status,
        {identity} AS source_learner_id,{metadata} AS review_data
        FROM {table("reviews")} WHERE learner_id IN ({placeholders}) ORDER BY id''', profile_ids)


def overlays_for(profile_ids):
    """Totals need settings and receipts, not answers, transcripts or evidence."""
    from .models import ImportedReviewInstance
    for row in ImportedReviewInstance.objects.filter(learner_id__in=profile_ids).values(
            "learner_id", "source_review_id", "status", "migrated_template_id",
            "template_snapshot__otjh", "template_snapshot__name", "meeting_intelligence__otjhCredit",
            "meeting_intelligence__reopenCompletedAt"):
        settings = row.pop("template_snapshot__otjh")
        name = row.pop("template_snapshot__name")
        receipt = row.pop("meeting_intelligence__otjhCredit")
        reopened_completion = row.pop("meeting_intelligence__reopenCompletedAt")
        yield SimpleNamespace(**row, template_snapshot={"name": name, **({"otjh": settings} if settings is not None else {})},
                              meeting_intelligence={"otjhCredit": receipt, "reopenCompletedAt": reopened_completion})


def planned_meetings(profile, window_start, window_end, *, templates=None, overlays=None, rows=None):
    """Project real imported occurrences; no Native recurrence generation."""
    from .migrated_reviews import programme_key, review_family
    from .migrated_templates import resolve_template
    from learner_api.review_history import _serialize_review

    key = programme_key(getattr(profile, "programme_id", None), getattr(profile, "programme", None))
    if templates is None:
        templates = {family: resolve_template(key, family) for family in ("MCM", "PR", "PR_SKILLS_RADAR")}
    if overlays is None:
        overlays = {o.source_review_id: o for o in overlays_for([profile.id])}
    if not any(t and hours_for(t.definition_json) for t in templates.values()) and not any(
            frozen_hours(o) for o in overlays.values()):
        return []
    # Select only scheduling metadata; never pull answers/evidence for a total.
    rows = source_rows([profile.id]) if rows is None else rows
    grouped, seen = {}, set()
    for row in rows:
        if str(row.get("source_learner_id") or "").strip() not in {"", str(profile.aptem_id)}:
            continue
        review = _serialize_review(row, {})
        reference = review["aptemReviewId"]
        family = review_family(review["type"])
        if not reference or reference in seen or family not in {"MCM", "PR", "PR_SKILLS_RADAR"}:
            continue
        seen.add(reference)
        overlay = overlays.get(row["id"])
        credit = (overlay.meeting_intelligence or {}).get("otjhCredit") if overlay else None
        if review["status"] == "cancelled" or ((review["status"] == "completed" or review["completedDate"]) and not credit):
            continue
        was_completed = overlay and (overlay.status == "completed" or (overlay.meeting_intelligence or {}).get("reopenCompletedAt"))
        if was_completed and not credit:
            continue
        template = templates[family]
        frozen = overlay and (was_completed or overlay.status == "awaiting-signature")
        definition = overlay.template_snapshot if frozen else template.definition_json if template else {}
        hours = credit["hours"] if credit else hours_for(definition)
        target = day(review["plannedDate"])
        if hours <= 0 or not target or not window_start <= target <= window_end:
            continue
        identity = (family, hours, overlay.migrated_template_id if frozen else template.pk if template else None)
        item = grouped.setdefault(identity, {"reviewTemplateId": f"aptem:{identity[2]}",
            "name": definition.get("name", family) if frozen else template.name if template else family,
            "hoursEach": hours, "occurrences": 0, "hours": 0, "recurrenceLabel": "Imported review dates",
            "startDate": target.isoformat(), "endDate": target.isoformat(), "reviewSource": "aptem"})
        item["occurrences"] += 1
        item["hours"] = round(item["occurrences"] * hours, 2)
        item["startDate"] = min(item["startDate"], target.isoformat())
        item["endDate"] = max(item["endDate"], target.isoformat())
    return list(grouped.values())


def planned_hours_bulk(owners):
    """Bounded reads for dashboard/caseload callers; keys are enrolment IDs."""
    from .models import MigratedReviewTemplate
    from .migrated_reviews import programme_key
    from .review_sources import resolve_review_source
    eligible = [owner for owner in owners if resolve_review_source(
        owner.get("account_aptem_id"), owner.get("aptem_id")).kind == "aptem"
        and owner.get("start_date") and owner.get("end_date")]
    if not eligible:
        return {}
    templates = list(MigratedReviewTemplate.objects.filter(is_active=True))
    by_slot = {(t.programme_key, t.review_family): t for t in templates}
    ids = [owner["id"] for owner in eligible]
    by_owner = defaultdict(dict)
    for overlay in overlays_for(ids):
        by_owner[overlay.learner_id][overlay.source_review_id] = overlay
    if not any(hours_for(t.definition_json) for t in templates) and not any(
            frozen_hours(o) for group in by_owner.values() for o in group.values()):
        return {}
    sources = defaultdict(list)
    for row in source_rows(ids):
        sources[row["learner_id"]].append(row)
    result = {}
    for owner in eligible:
        key = programme_key(owner.get("programme_id"), owner.get("programme"))
        assigned = {family: by_slot.get((key, family)) or by_slot.get(("", family))
                    for family in ("MCM", "PR", "PR_SKILLS_RADAR")}
        rows = planned_meetings(SimpleNamespace(**owner), day(owner["start_date"]), day(owner["end_date"]),
            templates=assigned, overlays=by_owner[owner["id"]], rows=sources[owner["id"]])
        result[owner["enrolment_id"]] = round(sum(row["hours"] for row in rows), 2)
    return result


def include_planned(result, hours):
    """Keep an unavailable whole-programme total unavailable; expose its part."""
    if not hours:
        return result
    result["otjh"]["review_planned"] = hours
    if result["otjh"].get("planned") is not None:
        result["otjh"]["planned"] = round(float(result["otjh"]["planned"]) + hours, 4)
    return result
