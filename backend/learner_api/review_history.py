"""Read-only learner access to reviews imported into the Learner schema."""

import json
from datetime import date, datetime

from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from login.permissions import learner_self_or_staff

from .learner_detail import SOURCE_MODELS
from .mappers import _s
from coach_api.review_sources import review_profile_for_source, source_for_learner


REVIEW_TYPES = {
    # Aptem exports have used all three labels over time; they represent the
    # same MCM record and must be presented together in the learner workspace.
    "monthly-coaching": ("Monthly Coaching Meeting", "Monthly Coaching", "MCM"),
    "progress-review": ("Progress Review", "Progress Review (+ Skills Radar)"),
    # The learner Reviews workspace includes every imported review that is
    # not a Monthly Coaching Meeting. The query intentionally does not
    # enumerate types because Aptem can add new review templates over time.
    "reviews": None,
}


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


def _normalise_status(value):
    normalised = _s(value).strip().lower().replace(" ", "-").replace("_", "-")
    # Aptem has emitted both workflow wording (Finished/Complete/Planned) and
    # the platform status wording over different exports. Keep one vocabulary
    # for the Planned/Finished tabs in the learner UI.
    return {
        "finished": "completed",
        "complete": "completed",
        "planned": "not-scheduled",
        "not-booked": "not-scheduled",
        "inprogress": "in-progress",
        "awaitingsignature": "awaiting-signature",
    }.get(normalised, normalised) or "unknown"


def _json_value(value, expected_type, fallback):
    if isinstance(value, expected_type):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return fallback
        return decoded if isinstance(decoded, expected_type) else fallback
    return fallback


def _parse_imported_datetime(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = _s(value).strip()
    if not text:
        return None
    for pattern in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d %b %Y at %H:%M",
        "%d %B %Y at %H:%M",
        "%d %b %Y",
        "%d %B %Y",
    ):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def _iso_date(value):
    parsed = _parse_imported_datetime(value)
    return parsed.date().isoformat() if parsed else None


def _iso_time(value):
    parsed = _parse_imported_datetime(value)
    return parsed.strftime("%H:%M") if parsed and parsed.hour + parsed.minute else None


def _learner_profile_id(cursor, source, kind):
    profile = review_profile_for_source(source)
    return profile.id if profile is not None and source_for_learner(source, profile).kind == "aptem" else None


def _review_rows(cursor, learner_id, review_types):
    select_sql = '''
        SELECT id, aptem_review_id, review_name, review_type, reviewer_name,
               learner_name,
               planned_scheduled_date, completed_date, status, review_data,
               extraction_status, last_error
        FROM "Learner".reviews
    '''
    if review_types is None:
        # MCM has its own workspace. Keep every other imported template in
        # Reviews, including templates introduced after this code ships.
        excluded = [value.casefold() for value in REVIEW_TYPES["monthly-coaching"]]
        cursor.execute(
            select_sql + '''
        WHERE learner_id = %s
          AND NULLIF(BTRIM(review_type), '') IS NOT NULL
          AND LOWER(BTRIM(review_type)) <> ALL(%s)
        ORDER BY COALESCE(completed_date, planned_scheduled_date) DESC NULLS LAST, id DESC
        ''',
            [learner_id, excluded],
        )
    else:
        cursor.execute(
            select_sql + '''
        WHERE learner_id = %s AND review_type = ANY(%s)
        ORDER BY COALESCE(completed_date, planned_scheduled_date) DESC NULLS LAST, id DESC
        ''',
            [learner_id, list(review_types)],
        )
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _sections_by_review(cursor, review_ids):
    if not review_ids:
        return {}
    cursor.execute(
        '''
        SELECT id, review_id, section_name, section_order, fields_json, tables_json, raw_text
        FROM "Learner".review_sections
        WHERE review_id = ANY(%s)
        ORDER BY review_id, section_order, id
        ''',
        [review_ids],
    )
    sections = {}
    for section_id, review_id, name, order, fields, tables, raw_text in cursor.fetchall():
        sections.setdefault(review_id, []).append({
            "id": section_id,
            "name": _s(name) or "Review section",
            "order": order,
            "fields": _json_value(fields, list, []),
            "tables": _json_value(tables, list, []),
            "rawText": _s(raw_text),
        })
    return sections


def _review_data_sections(data):
    """Normalise sections embedded in the imported review payload.

    Older imports predate ``Learner.review_sections`` and keep the same
    extracted content under ``reviews.review_data.sections``. Keep that data
    available to the learner page when the normalised rows are absent.
    """
    result = []
    captured_sections = data.get("sections")
    for index, section in enumerate(captured_sections if isinstance(captured_sections, list) else []):
        if not isinstance(section, dict):
            continue
        fields = section.get("fields")
        tables = section.get("tables")
        result.append({
            "id": f"review-data-{index}",
            "name": _s(section.get("section_name") or section.get("name")) or "Review section",
            "order": index,
            "fields": fields if isinstance(fields, list) else [],
            "tables": tables if isinstance(tables, list) else [],
            "rawText": _s(section.get("raw_text") or section.get("rawText")),
        })
    return result


def _field_value(sections, label):
    expected = label.casefold()
    for section in sections:
        for field in section.get("fields", []):
            if isinstance(field, dict) and _s(field.get("label")).strip().rstrip(":").casefold() == expected:
                value = field.get("value")
                if value not in (None, "", "EMPTY_STRING"):
                    return _s(value)
    return ""


def _serialize_review(row, sections, *, historical_presentation=False):
    data = _json_value(row.get("review_data"), dict, {})
    metadata = _json_value(data.get("source_metadata"), dict, {})
    planned_raw = row.get("planned_scheduled_date") or metadata.get("Planned / Scheduled Date")
    completed_raw = row.get("completed_date") or metadata.get("Completed Date")
    review_sections = sections.get(row["id"], [])
    if not review_sections:
        review_sections = _review_data_sections(data)
    manager_name = _field_value(review_sections, "Manager")
    if not manager_name:
        manager_name = _field_value(_review_data_sections(data), "Manager")
    review = {
        "id": str(row["id"]),
        "aptemReviewId": _s(row.get("aptem_review_id")),
        "aptemLearnerId": _s(data.get("aptem_learner_id")),
        "name": _s(row.get("review_name")) or _s(row.get("review_type")) or "Review",
        "type": _s(row.get("review_type")),
        "reviewerName": _s(row.get("reviewer_name")) or _s(metadata.get("Reviewer")),
        "learnerName": _s(row.get("learner_name")),
        "managerName": manager_name,
        "plannedDate": _iso_date(planned_raw),
        "plannedTime": _iso_time(planned_raw),
        "completedDate": _iso_date(completed_raw),
        "status": _normalise_status(row.get("status") or metadata.get("Status")),
        "rawStatus": _s(row.get("status") or metadata.get("Status")),
        "extractionStatus": _s(row.get("extraction_status")) or "partial",
        "detailsAvailable": bool(review_sections),
        "sections": review_sections,
    }
    if historical_presentation:
        from .historical_review_presentation import enrich_historical_review
        return enrich_historical_review(review, data)
    return review


@require_GET
@learner_self_or_staff(kwarg="pk")
def learner_review_history(request, kind, pk):
    category = _s(request.GET.get("category"))
    if category not in REVIEW_TYPES:
        return _error("Unknown review category.", 400)
    review_types = REVIEW_TYPES[category]
    model = SOURCE_MODELS.get(kind)
    if model is None:
        return _error("Learner not found.", 404)
    try:
        source = model.all_learners.only("id", "email", "aptem_id").filter(pk=pk).first()
        if source is None:
            return _error("Learner not found.", 404)
        # Imported Aptem reviews are intentionally isolated from the live
        # programme-cycle data. A learner without a valid Aptem id has no rows
        # in this source and must continue through the normal calendar path.
        profile = review_profile_for_source(source)
        review_source = source_for_learner(source, profile)
        if review_source.kind != "aptem":
            return JsonResponse({"learnerId": None, "category": category, "reviews": []})
        with connection.cursor() as cursor:
            learner_id = profile.id if profile else None
            if learner_id is None:
                return JsonResponse({"learnerId": None, "category": category, "reviews": []})
            # Review rows are the persisted source of truth. A read must never
            # infer a booking by matching a date/name to a calendar event.
            rows = _review_rows(cursor, learner_id, review_types)
            sections = _sections_by_review(cursor, [row["id"] for row in rows])
    except DatabaseError:
        return _error("Could not load review history.", 503)
    serialized = [_serialize_review(row, sections, historical_presentation=True) for row in rows]
    serialized = [review for review in serialized if not review.get("aptemLearnerId")
                  or review["aptemLearnerId"] == str(review_source.aptem_id)]
    serialized.sort(
        key=lambda item: (
            item["completedDate"] or item["plannedDate"] or "",
            item["plannedTime"] or "",
            int(item["id"]),
        ),
        reverse=True,
    )
    return JsonResponse({
        "learnerId": learner_id,
        "category": category,
        "reviews": serialized,
    })
