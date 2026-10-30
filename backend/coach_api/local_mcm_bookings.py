"""Resolve local MCM rows to saved bookings without changing meeting identities."""
from collections import defaultdict

from django.db import connections, router

from learner_api.models import LearnerProfile
from .models import CoachCalendarEvent


MONTHLY_TYPES = ("monthly coaching meeting", "monthly coaching", "mcm")


def _review_rows(where, params):
    alias = router.db_for_read(LearnerProfile) or "default"
    with connections[alias].cursor() as cursor:
        cursor.execute(
            """
            SELECT r.id, r.learner_id, r.aptem_review_id,
                   p.enrolment_id, p.learner_type, r.planned_scheduled_date
              FROM "Learner".reviews r
              JOIN "Learner".learners p ON p.id = r.learner_id
             WHERE LOWER(TRIM(r.review_type)) IN (%s, %s, %s)
               AND NULLIF(TRIM(r.aptem_review_id), '') IS NOT NULL
               AND """ + where,
            [*MONTHLY_TYPES, *params],
        )
        return cursor.fetchall()


def local_mcm_event_key(learner_id, review_id):
    """Only the exact learner-owned local review can name a shared meeting."""
    rows = _review_rows("r.id = %s AND r.learner_id = %s", [review_id, learner_id])
    if len(rows) != 1:
        raise ValueError("This Monthly Coaching Meeting is not available for this learner.")
    return "imported-review:" + rows[0][2].strip()


def local_mcm_metadata(record):
    if record.event_type != "mcr" or not record.event_key.startswith("imported-review:"):
        return None
    rows = _review_rows(
        "r.aptem_review_id = %s AND r.learner_id = %s",
        [record.event_key.removeprefix("imported-review:"), record.learner_id],
    )
    if len(rows) != 1:
        return None
    review_id, _, _, _, _, planned = rows[0]
    return str(review_id), planned.strftime("%Y-%m") if planned else ""


def legacy_mcm_calendar_records(owner_email, event_keys, *, lock=False):
    """Map exact local review IDs in learner booking keys to coach review keys.

    A date, name, or email alone never associates a meeting. Both the local
    profile and its enrolment identity must agree with the saved booking.
    Multiple bookings remain visible to callers as a conflict, never a guess.
    """
    keys = {key for key in event_keys if key.startswith("imported-review:")}
    if not keys:
        return {}
    query = CoachCalendarEvent.objects.filter(
        event_type="mcr", idempotency_key__startswith="learner-book:mcm:",
    )
    if owner_email is not None:
        query = query.filter(owner_email__iexact=owner_email)
    if lock:
        query = query.select_for_update()
    candidates = []
    for record in query:
        parts = (record.idempotency_key or "").split(":")
        if (len(parts) != 6 or parts[2] not in {"commercial", "apprenticeship"}
                or not parts[3].isdigit() or not parts[5].isdigit()
                or record.review_template_id or record.review_instance_id):
            continue
        candidates.append((record, parts))
    if not candidates:
        return {}
    review_ids = sorted({int(parts[5]) for _, parts in candidates})
    placeholders = ", ".join(["%s"] * len(review_ids))
    rows = _review_rows(f"r.id IN ({placeholders})", review_ids)
    by_id = {str(row[0]): row for row in rows}
    result = defaultdict(list)
    for record, parts in candidates:
        row = by_id.get(parts[5])
        if not row:
            continue
        _, profile_id, imported_id, enrolment_id, kind, _ = row
        key = "imported-review:" + imported_id.strip()
        if (key in keys and record.learner_id == profile_id
                and str(enrolment_id) == parts[3] and (kind or "").lower() == parts[2]):
            result[key].append(record)
    return dict(result)
