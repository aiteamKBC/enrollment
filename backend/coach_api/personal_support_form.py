"""Preserve captured PSP answers when explicitly starting its Global form."""
import json
from django.db import connections

from .migrated_reviews import all_fields, candidate_definition, validate_answers
from .migrated_template_sync import active_answers


def initial_answers(source_id, learner_id, snapshot):
    from .views import get_learner_db_alias
    with connections[get_learner_db_alias()].cursor() as cursor:
        cursor.execute('SELECT review_data, status, completed_date FROM "Learner".reviews '
                       'WHERE id = %s AND learner_id = %s AND review_type = %s',
                       [source_id, learner_id, "Personal Support Plan"])
        row = cursor.fetchone()
    if not row or row[2] or str(row[1]).strip().casefold() not in {"not scheduled", "scheduled", "in progress"}:
        raise ValueError("This Personal Support Plan is no longer editable.")
    data = json.loads(row[0]) if isinstance(row[0], str) else row[0] or {}
    if not any(isinstance(section.get("source_payload"), dict) and section["source_payload"].get("fields")
               for section in data.get("sections", [])):
        return {}
    source = candidate_definition(data)
    target_fields = {field["key"]: field for field in all_fields(snapshot)}
    answers = {}
    for section in source["sections"]:
        payload = data["sections"][section["order"]]["source_payload"]
        for field in all_fields({"sections": [section]}):
            target = target_fields.get(field["key"])
            if not target or field["aptemType"] == 11 or target["aptemType"] != field["aptemType"]:
                continue
            value = payload.get(field["name"])
            if isinstance(value, bool) and field["aptemType"] in (2, 6):
                value = "yes" if value else "no"
            if value is not None:
                answers[field["key"]] = value
    # Keep captured hidden responses in storage, like later local edits. A
    # malformed source value must remain visible as an initialization error.
    validate_answers(snapshot, active_answers(snapshot, answers))
    return answers
