"""Read the date of an employer's migrated review without changing its history."""
from django.db import connections

from coach_api.migrated_reviews import review_family
from coach_api.review_sources import load_imported_links
from .review_history import _iso_date


def imported_document_dates(overlays):
    """Batch the same booked-date / source-date precedence used by review PDFs.

    Only the supplied continuation's source review and learner identity can
    contribute a date. Completion/signature timestamps are not review dates.
    """
    if not overlays:
        return {}
    from coach_api.views import get_learner_db_alias

    owners = {(overlay.learner_id, overlay.source_review_id): overlay for overlay in overlays}
    with connections[get_learner_db_alias()].cursor() as cursor:
        cursor.execute('''
            SELECT r.learner_id, r.id, r.aptem_review_id, r.planned_scheduled_date,
                   r.review_data #>> '{source_metadata,Planned / Scheduled Date}',
                   l.enrolment_id, l.learner_type, r.review_type
            FROM "Learner".reviews r
            JOIN "Learner".learners l ON l.id = r.learner_id
            WHERE r.learner_id = ANY(%s) AND r.id = ANY(%s)
        ''', [sorted({key[0] for key in owners}), sorted({key[1] for key in owners})])
        rows = cursor.fetchall()

    events, source_dates = [], {}
    for learner_id, review_id, aptem_id, planned, metadata_date, enrolment_id, kind, review_type in rows:
        key = (learner_id, review_id)
        overlay = owners.get(key)
        if overlay is None:
            continue
        source_dates[key] = _iso_date(planned or metadata_date) or ""
        events.append({
            "eventKey": overlay.event_key, "reviewId": str(review_id),
            "aptemReviewId": str(aptem_id or ""), "learnerId": str(learner_id),
            "enrolmentId": str(enrolment_id) if enrolment_id is not None else None,
            "learnerType": "commercial" if kind == "commercial" else "apprenticeship",
            "source": "mcr" if review_family(review_type) == "MCM" else "progress-review",
        })

    # This resolver also covers legacy booking keys, and rejects conflicting
    # learner/coach associations instead of choosing an unrelated appointment.
    bookings, _ = load_imported_links(events, compact=True)
    for event in events:
        record = bookings.get(event["eventKey"])
        if record is not None and record.scheduled_date:
            key = (int(event["learnerId"]), int(event["reviewId"]))
            source_dates[key] = record.scheduled_date.isoformat()
    return source_dates
