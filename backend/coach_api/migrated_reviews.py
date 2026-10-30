"""Aptem migrated-form definitions and validation; no native Review dependency."""

from datetime import date
import re


FAMILY_BY_SOURCE_TYPE = {
    "monthly coaching meeting": "MCM",
    "monthly coaching": "MCM",
    "mcm": "MCM",
    "progress review": "PR",
    "progress review (+ skills radar)": "PR_SKILLS_RADAR",
    "personal support plan": "PSP",
}
INITIAL_STATUSES = {"not-scheduled", "scheduled"}
EDITABLE_STATUSES = {"not-scheduled", "scheduled", "in-progress"}
# Work after scheduling does not require the optional Start transition.
# Keep pre-booking drafts distinct from submission and legacy summary edits.
SCHEDULED_WORK_STATUSES = frozenset({"scheduled", "in-progress"})

# Supported by captured Aptem field metadata, option lists, and saved value shapes.
FIELD_TYPES = {
    1: "text", 2: "boolean", 4: "date", 5: "list_item", 6: "boolean_case_block",
    11: "title_description", 13: "text_multiline",
}
TEXT_ANSWER_LIMIT = 4000


def meeting_summary_field(definition):
    """Resolve explicit metadata from a frozen migrated definition, never labels."""
    matches = []

    def visit(field, depth=0):
        if "semanticKey" in field:
            if field["semanticKey"] != "meeting_summary":
                raise ValueError("Unknown migrated field purpose.")
            if type(field.get("aptemType")) is not int or field["aptemType"] not in (1, 13):
                raise ValueError("AI Meeting Summary requires a text or long text field.")
            if depth:
                raise ValueError("AI Meeting Summary cannot be inside a conditional question.")
            matches.append(field)
        for branch in ("ifTrue", "ifFalse"):
            for child in field.get(branch, []):
                visit(child, depth + 1)

    for section in definition.get("sections", []):
        for field in section.get("fields", []):
            visit(field)
    if len(matches) > 1:
        raise ValueError("A migrated template can have only one AI Meeting Summary field.")
    if matches:
        validate_definition(definition)
    return matches[0] if matches else None


def review_family(source_type):
    return FAMILY_BY_SOURCE_TYPE.get(str(source_type or "").strip().casefold())


def booking_event_type(family):
    """Keep template families distinct while sharing existing calendar types."""
    # PSP retains its separate coach-only booking policy.
    return {"MCM": "mcr", "PR": "progress-review", "PR_SKILLS_RADAR": "progress-review"}.get(family)


def programme_key(programme_id, programme_name):
    """Prefer the stable LMS programme id; isolate exact-name legacy fallback."""
    stable = str(programme_id or "").strip()
    if stable:
        return f"id:{stable}"
    name = " ".join(str(programme_name or "").split()).casefold()
    return f"name:{name}" if name else None


def source_is_completed(review):
    return review.get("status") == "completed" or bool(review.get("completedDate"))


def initial_local_status(source_status):
    status = str(source_status or "").strip().casefold().replace(" ", "-")
    return status if status in INITIAL_STATUSES else None


def _field_from_source(raw, section_key, path):
    if not isinstance(raw, dict):
        raise ValueError("Aptem field metadata must be an object.")
    name = str(raw.get("name") or "").strip()
    if not name or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError("An Aptem field has no stable name.")
    try:
        aptem_type = int(raw.get("type"))
    except (TypeError, ValueError):
        raise ValueError("An Aptem field has no numeric type.") from None
    options = raw.get("listAnswers") or []
    if not isinstance(options, list) or any(not isinstance(item, str) for item in options):
        raise ValueError("Aptem choices must be a list of text options.")
    key = f"{section_key}:{path}{name}"
    return {
        "key": key,
        "name": name,
        "title": str(raw.get("title") or name).strip(),
        "aptemType": aptem_type,
        "order": int(raw.get("order") or 0),
        "mandatory": bool(raw.get("isMandatory")),
        "description": str(raw.get("description") or "").strip(),
        "options": options,
        "ifTrue": [
            _field_from_source(child, section_key, f"{path}{name}:yes:")
            for child in (raw.get("ifTrue") or [])
        ],
        "ifFalse": [
            _field_from_source(child, section_key, f"{path}{name}:no:")
            for child in (raw.get("ifFalse") or [])
        ],
    }


def candidate_definition(review_data):
    """Copy metadata only; source_payload answer values never enter a template."""
    sections = review_data.get("sections") if isinstance(review_data, dict) else None
    if not isinstance(sections, list):
        raise ValueError("The selected source review has no captured sections.")
    result = []
    for index, raw in enumerate(sections):
        if not isinstance(raw, dict):
            continue
        payload = raw.get("source_payload")
        fields = payload.get("fields") if isinstance(payload, dict) else None
        if not isinstance(fields, list) or not fields:
            continue
        section_key = f"section-{index}"
        result.append({
            "key": section_key,
            "title": str(raw.get("section_name") or f"Section {index + 1}").strip(),
            "order": index,
            "fields": [_field_from_source(field, section_key, "") for field in fields],
        })
    if not result:
        raise ValueError("The selected source review has no reusable Aptem field metadata.")
    definition = {"sections": result}
    validate_definition(definition)
    return definition


def all_fields(definition):
    for section in definition.get("sections", []):
        for field in section.get("fields", []):
            yield from _walk_field(field)


def _walk_field(field):
    yield field
    for branch in ("ifTrue", "ifFalse"):
        for child in field.get(branch, []):
            yield from _walk_field(child)


def validate_definition(definition):
    if not isinstance(definition, dict) or not isinstance(definition.get("sections"), list) or not definition["sections"]:
        raise ValueError("A migrated template needs at least one section.")
    keys = set()
    for section in definition["sections"]:
        if not isinstance(section, dict) or not section.get("key") or not isinstance(section.get("fields"), list):
            raise ValueError("Invalid migrated template section.")
    for field in all_fields(definition):
        if not isinstance(field, dict) or not field.get("key") or not field.get("title"):
            raise ValueError("Invalid migrated template field.")
        if field["key"] in keys:
            raise ValueError("Migrated template field keys must be unique.")
        keys.add(field["key"])
        if not isinstance(field.get("aptemType"), int):
            raise ValueError("Migrated template field type must be numeric Aptem metadata.")
        if not isinstance(field.get("options", []), list):
            raise ValueError("Migrated template options must be a list.")
    return definition


def _render_field(field, answers, warnings, answer_audit):
    aptem_type = field["aptemType"]
    field_type = FIELD_TYPES.get(aptem_type, "text_multiline")
    if aptem_type not in FIELD_TYPES:
        warnings.append({"fieldKey": field["key"], "aptemType": aptem_type})
    configuration = {
        "migrated": True,
        "aptemType": aptem_type,
        "description": field.get("description") or "",
        "options": field.get("options") or [],
        "respondentRoles": field.get("respondentRoles") or [],
    }
    if aptem_type not in FIELD_TYPES:
        configuration["typeWarning"] = "Unverified Aptem field type; using text entry."
    if "semanticKey" in field:
        configuration["semanticKey"] = field["semanticKey"]
    return {
        "id": field["key"], "title": field["title"], "fieldType": field_type,
        "required": bool(field.get("mandatory")) and field_type != "title_description",
        "displayOrder": field.get("order", 0), "configuration": configuration,
        "parentFieldId": None, "conditionValue": None,
        "answer": answers.get(field["key"]),
        "answeredBy": (answer_audit.get(field["key"]) or {}).get("actor"),
        "answeredAt": (answer_audit.get(field["key"]) or {}).get("at"),
        "yesFields": [_render_field(child, answers, warnings, answer_audit) for child in field.get("ifTrue", [])],
        "noFields": [_render_field(child, answers, warnings, answer_audit) for child in field.get("ifFalse", [])],
    }


def render_sections(definition, answers, *, answer_audit=None):
    validate_definition(definition)
    warnings = []
    sections = [{
        "id": section["key"], "title": section["title"], "estimatedMinutes": 0,
        "displayOrder": section.get("order", 0), "enabled": True,
        "fields": [_render_field(field, answers, warnings, answer_audit or {}) for field in section["fields"]],
    } for section in definition["sections"]]
    return sections, warnings


def _visible_fields(definition, answers):
    def walk(field):
        yield field
        branch = "ifTrue" if answers.get(field["key"]) == "yes" else "ifFalse" if answers.get(field["key"]) == "no" else None
        for child in field.get(branch, []) if branch else []:
            yield from walk(child)
    for section in definition["sections"]:
        for field in section["fields"]:
            yield from walk(field)


def validate_answers(definition, answers, *, completing=False):
    validate_definition(definition)
    if not isinstance(answers, dict):
        raise ValueError("answers must be an object keyed by field id.")
    fields = {field["key"]: field for field in all_fields(definition)}
    if set(answers) - set(fields):
        raise ValueError("answers contains a field that is not part of this review.")
    visible = {field["key"]: field for field in _visible_fields(definition, answers)}
    if set(answers) - set(visible):
        raise ValueError("answers contains a hidden conditional field.")
    for key, value in answers.items():
        field = fields[key]
        kind = FIELD_TYPES.get(field["aptemType"], "text_multiline")
        if kind == "title_description":
            raise ValueError("Static headings cannot be answered.")
        if value is None or value == "":
            continue
        if kind in ("boolean", "boolean_case_block") and value not in ("yes", "no"):
            raise ValueError(f"{key} must be yes or no.")
        if kind == "list_item" and (not isinstance(value, str) or value not in field.get("options", [])):
            raise ValueError(f"{key} must be one of its listed options.")
        if kind == "date":
            try:
                date.fromisoformat(value)
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be an ISO date.") from None
        if kind in ("text", "text_multiline") and (not isinstance(value, str) or len(value) > TEXT_ANSWER_LIMIT):
            raise ValueError(f"{key} must be text of at most {TEXT_ANSWER_LIMIT} characters.")
    if completing:
        missing = [key for key, field in visible.items()
                   if field.get("mandatory") and FIELD_TYPES.get(field["aptemType"]) != "title_description"
                   and answers.get(key) in (None, "")]
        if missing:
            raise ValueError("Required review fields are unanswered: " + ", ".join(missing))
    return answers
