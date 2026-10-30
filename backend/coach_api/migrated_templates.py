"""Management and resolution of migrated forms only; no native review imports."""
from copy import deepcopy
import hashlib
import json

from django.db import connections

from .migrated_reviews import FIELD_TYPES, candidate_definition, meeting_summary_field, render_sections, review_family
from .models import MigratedReviewTemplate
from .migrated_signature_rules import validate_template_signatures


FAMILIES = ("MCM", "PR", "PR_SKILLS_RADAR", "PSP")


def template_family(source_type):
    """Use the same explicit source family for library and continuation forms."""
    return review_family(source_type)


def validate_slot(scope, family, key):
    if family not in FAMILIES:
        raise ValueError("Choose MCM, PR, PR_SKILLS_RADAR or PSP.")
    if family == "PSP" and scope != "GLOBAL":
        raise ValueError("Personal Support Plan templates are managed globally.")
    if scope == "GLOBAL":
        if key:
            raise ValueError("A Global template cannot have a programme key.")
    elif scope == "PROGRAMME":
        if not isinstance(key, str) or len(key) > 255 or not key.startswith(("id:", "name:")) or not key.split(":", 1)[1].strip():
            raise ValueError("Use an exact id:<programme id> or name:<programme name> key.")
        if key != key.strip() or (key.startswith("name:") and key != key.casefold()):
            raise ValueError("Programme keys must be trimmed; name keys must be lowercase.")
    else:
        raise ValueError("Scope must be GLOBAL or PROGRAMME.")


def validate_managed_definition(definition, *, family=None):
    """Strict authoring boundary. Legacy frozen snapshots retain their renderer."""
    if not isinstance(definition, dict) or "sections" not in definition or set(definition) - {"sections", "otjh", "signatures"}:
        raise ValueError("The definition may contain sections, OTJH and signature settings only, without answers or source data.")
    validate_template_signatures(definition, family)
    from .migrated_otjh import policy
    policy(definition)
    sections = definition["sections"]
    if not isinstance(sections, list) or not 1 <= len(sections) <= 100:
        raise ValueError("A template needs between 1 and 100 sections.")
    keys, section_keys = set(), set()

    def text(value, label, limit=4000):
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError(f"{label} must be text of at most {limit} characters.")

    def ordered(item):
        if type(item.get("order", 0)) is not int or item.get("order", 0) < 0:
            raise ValueError("Order must be a non-negative integer.")

    def field_check(field, depth=0):
        if depth > 10 or len(keys) >= 500:
            raise ValueError("Use at most 500 fields and 10 conditional levels.")
        allowed = {"key", "name", "title", "aptemType", "order", "mandatory", "description", "options", "ifTrue", "ifFalse", "semanticKey", "respondentRoles"}
        if not isinstance(field, dict) or set(field) - allowed:
            raise ValueError("Fields must contain form metadata only.")
        for label in ("key", "title"):
            text(field.get(label), f"Field {label}")
            if not field[label].strip():
                raise ValueError(f"Field {label} is required.")
        if field["key"] in keys:
            raise ValueError("Field keys must be unique, including conditional fields.")
        keys.add(field["key"])
        kind = field.get("aptemType")
        if type(kind) is not int or kind not in FIELD_TYPES:
            raise ValueError(f"Unsupported field type for {field['key']}.")
        roles = field.get("respondentRoles", [])
        if (not isinstance(roles, list)
                or any(role not in ("participant", "employer") for role in roles)
                or len(roles) != len(set(roles))):
            raise ValueError("Respondent roles must be unique Learner or Employer roles.")
        if kind == 11 and roles:
            raise ValueError("Headings cannot have respondent roles.")
        if family in ("MCM", "PSP") and "employer" in roles:
            raise ValueError("Employer answers are supported for Progress Reviews only.")
        ordered(field)
        if type(field.get("mandatory", False)) is not bool:
            raise ValueError("Mandatory must be true or false.")
        text(field.get("description", ""), "Description", 20000)
        text(field.get("name", ""), "Field name")
        options = field.get("options", [])
        if not isinstance(options, list) or any(not isinstance(o, str) or not o.strip() or len(o) > 4000 for o in options):
            raise ValueError("Options must be non-empty text values.")
        if len(set(options)) != len(options) or (kind == 5 and not options):
            raise ValueError("List fields need unique, non-empty options.")
        for branch in ("ifTrue", "ifFalse"):
            children = field.get(branch, [])
            if not isinstance(children, list) or (children and kind != 6):
                raise ValueError("Conditional fields belong to a Yes/No conditional block.")
            for child in children:
                field_check(child, depth + 1)

    for section in sections:
        if not isinstance(section, dict) or set(section) - {"key", "title", "order", "fields"}:
            raise ValueError("Sections must contain form metadata only.")
        for label in ("key", "title"):
            text(section.get(label), f"Section {label}")
            if not section[label].strip():
                raise ValueError(f"Section {label} is required.")
        if section["key"] in section_keys:
            raise ValueError("Section keys must be unique.")
        section_keys.add(section["key"])
        ordered(section)
        if not isinstance(section.get("fields"), list) or not section["fields"]:
            raise ValueError("Each section needs at least one field.")
        for field in section["fields"]:
            field_check(field)
    meeting_summary_field(definition)
    render_sections(definition, {})
    return definition


def resolve_template(key, family, *, lock=False):
    if family not in FAMILIES:
        return None
    templates = MigratedReviewTemplate.objects
    if lock:
        templates = templates.select_for_update()
    if key:
        override = templates.filter(scope="PROGRAMME", programme_key=key, review_family=family, is_active=True).first()
        if override:
            return override
    return templates.filter(scope="GLOBAL", programme_key="", review_family=family, is_active=True).first()


def resolution_metadata(template, family):
    return {
        "resolved_template_id": template.pk if template else None,
        "resolved_scope": template.scope if template else None,
        "inherited_from_global": bool(template and template.scope == "GLOBAL"),
        "programme_override_exists": bool(template and template.scope == "PROGRAMME"),
        "review_family": family,
    }


def snapshot_for(template):
    snapshot = {**deepcopy(template.definition_json), "name": template.name,
            "templateSource": {"id": template.pk, "scope": template.scope,
                               "programmeKey": template.programme_key, "family": template.review_family}}
    from .migrated_template_sync import snapshot_fingerprint
    from django.utils import timezone
    snapshot["templateSync"] = {"fingerprint": snapshot_fingerprint(snapshot),
                                "synchronizedAt": timezone.now().isoformat(), "retiredFields": {}}
    return snapshot


def snapshot_assignment_valid(overlay, family, key):
    """Read the frozen assignment, even after its source template is deactivated."""
    if not overlay or not getattr(overlay, "migrated_template_id", None):
        return False
    snapshot = overlay.template_snapshot
    if not isinstance(snapshot, dict) or not snapshot.get("sections"):
        return False
    source = snapshot.get("templateSource")
    if source:
        return bool(source.get("id") == overlay.migrated_template_id and source.get("family") == family
                    and (source.get("scope") == "GLOBAL" or source.get("programmeKey") == key))
    # Before family separation, initialized Skills Radar reviews used PR. Their
    # original FK and snapshot remain unchanged; this exception is read-only.
    template = overlay.migrated_template
    return bool(template.programme_key == key
                and (template.review_family == family or (family == "PR_SKILLS_RADAR" and template.review_family == "PR")))


def fingerprint(definition):
    return hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()


def historical_candidate(source_id, family):
    """Read one completed source; return only whitelisted definition metadata."""
    with connections["default"].cursor() as cursor:
        cursor.execute('''SELECT id, aptem_review_id, review_type, status, learner_id,
                          review_data->'sections' FROM "Learner".reviews WHERE id = %s''', [source_id])
        row = cursor.fetchone()
        if row is None or row[3] != "Completed" or template_family(row[2]) != family:
            raise ValueError("Select a Completed Aptem source review in this family using its internal review ID.")
        sections = json.loads(row[5]) if isinstance(row[5], str) else row[5]
        definition = candidate_definition({"sections": sections})
        validate_managed_definition(definition, family=family)
        cursor.execute('SELECT programme_id, programme FROM "Learner".learners WHERE id = %s', [row[4]])
        programme = cursor.fetchone()
    return definition, {
        "sourceReviewId": row[0], "aptemReviewId": row[1], "reviewType": row[2],
        "programmeId": programme[0] if programme else None,
        "programme": programme[1] if programme else None,
        "fingerprint": fingerprint(definition),
    }


def template_definition_counts(definition):
    """Match the library's counts, including both conditional question branches."""
    def questions(fields):
        return sum(
            (0 if field.get('aptemType') == 11 else 1)
            + questions(field.get('ifTrue') or []) + questions(field.get('ifFalse') or [])
            for field in fields
        )
    sections = definition.get('sections') or []
    return {'sections': len(sections), 'questions': sum(questions(section.get('fields') or []) for section in sections)}


def serialize_template(template, *, definition=False):
    result = {"id": template.pk, "scope": template.scope, "programme_key": template.programme_key,
              "review_family": template.review_family, "name": template.name,
              "is_active": template.is_active, "updated_at": template.updated_at.isoformat(),
              "source_metadata": template.source_metadata, "fingerprint": fingerprint(template.definition_json),
              "statistics": template_definition_counts(template.definition_json)}
    if definition:
        result["definition"] = template.definition_json
    return result
