"""The enrolment wizard's editable layout — what the wizard builder publishes.

    GET /enrolment_api/wizard-layout/           -> {layout, version, updatedAt, updatedBy}
    PUT /enrolment_api/wizard-layout/publish/   -> body {layout, baseVersion}; same shape back

Storage
-------
enrolment."Wizard_Form_Layouts" is append-only: every publish is a new row and
the newest row is the layout in force. Earlier rows are the audit trail of who
changed the form and when; nothing ever rewrites them.

The layout says where each built-in item of the wizard sits, whether it is
shown and whether it is required (the built-in items themselves are defined by
the frontend registry). Custom fields — added in the builder — get a real column
in the table of the step they were created on, so the answers are as queryable
as every other wizard answer:

    Personal Details   -> enrolment."Wizard_Personal_Details"
    ILR                -> enrolment."Wizard_Ilr_Learner_Details"
    Extended ILR       -> enrolment."Extended_ILR"
    CV/Job Description -> enrolment."Wizard_Cv_Job"
    PLR                -> enrolment."Wizard_Plr"
    Skills Radar       -> enrolment."Wizard_Skills_Radar"
    Welcome, Before You Begin, Inclusion, Policies, What Happens Now? -> a one-row-per-learner
        enrolment."Wizard_<Step>" table, created with the step's first custom field
    a step added in the builder -> its own enrolment."Wizard_Custom_<name>" table

Publishing runs the DDL (CREATE TABLE / ADD COLUMN ... IF NOT EXISTS) in the same
transaction as the new layout row, so a layout can never name a column that does
not exist. The server, never the client, chooses every table and column name, and
once a field has a column it keeps it: removing a field only hides it, so its
column and the answers in it survive and come back if the field is restored.
Nothing here ever drops a column or a table.

Answers reach those columns through project_custom_fields(), called from the
Extended ILR save in the same transaction as the rest of the wizard projection.
"""
import json
import logging
import re
import secrets
from copy import deepcopy
from decimal import Decimal, InvalidOperation

from django.db import DatabaseError, connections, transaction
from django.http import JsonResponse
from django.core import signing
from django.views.decorators.csrf import csrf_exempt

from .auth import enrolment_login_required
from programme_suitability.wizard_step import with_suitability_step
from programme_suitability.modules_configuration import with_modules

logger = logging.getLogger(__name__)

CONN = "enrolment"
LAYOUT_TABLE = "Wizard_Form_Layouts"
VISIBILITY_TABLE = "Wizard_Field_Visibility"

#: A layout is a few KB; this only stops a runaway payload.
MAX_LAYOUT_BYTES = 4 * 1024 * 1024  # Includes four complete Skills Radar catalogues.
MAX_STEPS = 40
MAX_ITEMS_PER_STEP = 200
MAX_OPTIONS = 200
MAX_SINGLE_LINE_ANSWER = 500
MAX_MULTILINE_ANSWER = 10_000
MAX_HEADING_LENGTH = 300
MAX_DESCRIPTION_LENGTH = 20_000

CONTENT_FIELD_TYPES = {"heading", "description", "divider"}
CHOICE_FIELD_TYPES = {"radio", "checkbox"}
FIELD_TYPES = {"text", "single_line", "multi_line", "number", "upload", "dropdown", *CONTENT_FIELD_TYPES, *CHOICE_FIELD_TYPES}

#: SQL type of each custom field's column.
COLUMN_TYPES = {
    "text": "text",  # Legacy single-line fields published before the split.
    "single_line": "text",
    "multi_line": "text",
    "dropdown": "text",
    "radio": "text",
    "checkbox": "jsonb",
    "number": "numeric",
    "upload": "jsonb",
}

#: Built-in steps -> (their table, its one-row-per-learner unique index, whether
#: this module may create the table). The first six already exist (see
#: apply_enrolment_wizard_tables / apply_extended_ilr_table); the rest have no
#: per-learner table until a custom field is added to them.
BUILTIN_STEP_TABLES = {
    "personal-details": ("Wizard_Personal_Details", "wizard_personal_details_learner_uniq", False),
    "ilr-details": ("Wizard_Ilr_Learner_Details", "wizard_ilr_learner_details_learner_uniq", False),
    "ilr": ("Extended_ILR", "extended_ilr_learner_uniq", False),
    "cv-job": ("Wizard_Cv_Job", "wizard_cv_job_learner_uniq", False),
    "programme-suitability": ("Wizard_Programme_Suitability", "wizard_programme_suitability_learner_uniq", True),
    "plr": ("Wizard_Plr", "wizard_plr_learner_uniq", False),
    "skills-radar": ("Wizard_Skills_Radar", "wizard_skills_radar_learner_uniq", False),
    "introduction": ("Wizard_Introduction", "wizard_introduction_learner_uniq", True),
    "before-you-begin": ("Wizard_Before_You_Begin", "wizard_before_you_begin_learner_uniq", True),
    "inclusion": ("Wizard_Inclusion", "wizard_inclusion_learner_uniq", True),
    "policies": ("Wizard_Policies", "wizard_policies_learner_uniq", True),
    "initial-assessments": ("Wizard_Initial_Assessments", "wizard_initial_assessments_learner_uniq", True),
    "next-steps": ("Wizard_Next_Steps", "wizard_next_steps_learner_uniq", True),
}

BUILTIN_STEP_LABELS = {
    "introduction": "Welcome",
    "before-you-begin": "Before You Begin",
    "personal-details": "Personal Details",
    "ilr-details": "ILR",
    "ilr": "Extended ILR",
    "programme-suitability": "Programme Suitability & Modules",
    "cv-job": "CV/Job Description",
    "plr": "Personal Learning Record",
    "skills-radar": "Skills Radar",
    "inclusion": "Inclusion",
    "policies": "Policies",
    "initial-assessments": "Initial Assessments",
    "next-steps": "What Happens Now?",
}

# Identifier shapes. Every table and column name is checked against these
# immediately before it is formatted into SQL, wherever it came from.
TABLE_RE = re.compile(r"^(Extended_ILR|Wizard_[A-Za-z0-9_]{1,60})$")
COLUMN_RE = re.compile(r"^Custom_[a-z0-9_]{1,60}$")
INDEX_RE = re.compile(r"^[a-z0-9_]{1,63}$")
CUSTOM_KEY_RE = re.compile(r"^cf_[a-z0-9_]{1,50}$")
BUILTIN_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{0,79}$")
CUSTOM_STEP_RE = re.compile(r"^custom-[a-z0-9-]{1,50}$")
PATH_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*){0,5}$")
TEXT_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,120}$")

#: Edited wording (layout["texts"]): how many slots, and how long each may be.
#: The wording is rendered by the wizard as text, never as HTML.
MAX_TEXTS = 600
MAX_TEXT_LENGTH = 20_000


class LayoutError(ValueError):
    """A layout the server will not publish; the message is shown to the admin."""


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


def _q(name, pattern):
    """A double-quoted identifier, refused unless it has the expected shape."""
    if not isinstance(name, str) or not pattern.match(name):
        raise LayoutError(f"Refusing unsafe identifier {name!r}.")
    return f'"{name}"'


def _slug(text, limit=40):
    out = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    return out[:limit].strip("_") or "field"


# ── reading ──────────────────────────────────────────────────────────────────


def _table_exists(cursor):
    cursor.execute("SELECT to_regclass(%s)", [f'enrolment."{LAYOUT_TABLE}"'])
    return cursor.fetchone()[0] is not None


def read_current(cursor):
    """(version, layout, updated_at, updated_by) of the layout in force, or None."""
    if not _table_exists(cursor):
        return None
    cursor.execute(
        f'SELECT id, "Layout", "Created_at", "Created_by" FROM enrolment."{LAYOUT_TABLE}" '
        'WHERE COALESCE("Scope", \'default\') = \'default\' AND NOT COALESCE("Archived", false) '
        "ORDER BY id DESC LIMIT 1"
    )
    row = cursor.fetchone()
    if row is None:
        return None
    layout = row[1]
    if isinstance(layout, str):
        layout = json.loads(layout)
    return row[0], layout, row[2], row[3]


def current_layout():
    """The published layout as a dict, or None when nothing is published."""
    with connections[CONN].cursor() as cursor:
        current = read_current(cursor)
        layout = apply_field_visibility(current[1], read_field_visibility(cursor)) if current else None
    from .skills_radar.catalogue import with_catalogues
    return with_catalogues(with_modules(with_suitability_step(layout))) if layout else None


def _payload(current):
    if current is None:
        return {"layout": None, "version": None, "updatedAt": "", "updatedBy": ""}
    version, layout, created_at, created_by = current
    from .skills_radar.catalogue import with_catalogues
    return {
        "layout": with_catalogues(with_modules(with_suitability_step(layout))),
        "version": version,
        "updatedAt": created_at.isoformat() if created_at else "",
        "updatedBy": created_by or "",
    }


ASSIGNMENT_TABLE = "Wizard_Layout_Assignments"


def read_field_visibility(cursor):
    """Live global field overrides. Missing table is safe before the schema command runs."""
    cursor.execute("SELECT to_regclass(%s)", [f'enrolment."{VISIBILITY_TABLE}"'])
    if cursor.fetchone()[0] is None:
        return {}
    cursor.execute(f'SELECT "Field_key", "Hidden" FROM enrolment."{VISIBILITY_TABLE}"')
    return dict(cursor.fetchall())


def apply_field_visibility(layout, overrides):
    """Overlay visibility without changing the stored version JSON."""
    if not layout or not overrides:
        return layout
    result = deepcopy(layout)
    for step in result.get("steps", []):
        for item in step.get("items", []):
            if item.get("key") in overrides:
                item["hidden"] = overrides[item["key"]]
    return result


def read_layout_assignment(cursor, kind, learner_id):
    cursor.execute(
        f'SELECT "Override_version", "Submitted_version", "Pinned_version", '
        f'to_jsonb(saved)->\'Visibility_snapshot\' FROM enrolment."{ASSIGNMENT_TABLE}" saved '
        'WHERE "Learner_kind" = %s AND "Learner_id" = %s',
        [kind, learner_id],
    )
    row = cursor.fetchone()
    if row and isinstance(row[3], str):
        return (*row[:3], json.loads(row[3]))
    return row


def uses_global_layout(cursor, kind, learner_id):
    assignment = read_layout_assignment(cursor, kind, learner_id)
    return not assignment or all(value is None for value in assignment[:3])


def has_visibility_snapshot_column(cursor):
    cursor.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = to_regclass(%s) "
        "AND attname = 'Visibility_snapshot' AND NOT attisdropped)",
        [f'enrolment."{ASSIGNMENT_TABLE}"'],
    )
    return bool(cursor.fetchone()[0])


def read_pinned_visibility(cursor, kind, learner_id, version):
    """Read the assignment's frozen visibility, including before the column is installed."""
    assignment = read_layout_assignment(cursor, kind, learner_id)
    if not assignment or version not in assignment[1:3]:
        return None
    return assignment[3]


def capture_field_visibility(cursor, kind, learner_id):
    """Capture live overrides for the first pin or submission in the same save."""
    if not uses_global_layout(cursor, kind, learner_id):
        return None
    snapshot = read_field_visibility(cursor)
    return snapshot or None


def read_version(cursor, version):
    cursor.execute(
        f'SELECT id, "Layout", "Created_at", "Created_by", "Scope", "Variant_name", "Parent_version" '
        f'FROM enrolment."{LAYOUT_TABLE}" WHERE id = %s AND NOT COALESCE("Archived", false)',
        [version],
    )
    row = cursor.fetchone()
    if row is None:
        return None
    layout = json.loads(row[1]) if isinstance(row[1], str) else row[1]
    return row[0], layout, row[2], row[3], row[4], row[5], row[6]


def effective_layout(cursor, kind, learner_id, assignment=None):
    """Resolve explicit admin assignment, submission snapshot, checkpoint pin, then default."""
    if assignment is None:
        assignment = read_layout_assignment(cursor, kind, learner_id)
    version = next((value for value in assignment[:3] if value is not None), None) if assignment else None
    return read_version(cursor, version) if version is not None else read_current(cursor)


def learner_layout_with_visibility(cursor, kind, learner_id):
    assignment = read_layout_assignment(cursor, kind, learner_id)
    current = effective_layout(cursor, kind, learner_id, assignment=assignment) if assignment else read_current(cursor)
    if current is None:
        return None
    if not assignment or all(value is None for value in assignment[:3]):
        overrides = read_field_visibility(cursor)
    else:
        overrides = assignment[3] if current[0] in assignment[1:3] else {}
    return (current[0], apply_field_visibility(current[1], overrides), *current[2:])


def record_layout_progress(cursor, kind, learner_id, version, *, checkpoint=False, submitted=False, actor=None, visibility_snapshot=None):
    """Pin only after the Skills Radar checkpoint; preserve the first submission reference."""
    if not version:
        return
    if read_version(cursor, version) is None:
        raise LayoutError("The wizard layout version being saved is no longer available.")
    # A deployment can serve the old wizard while its schema command is pending.
    has_snapshot_column = has_visibility_snapshot_column(cursor)
    snapshot_column = ', "Visibility_snapshot"' if has_snapshot_column else ''
    snapshot_value = ', %s::jsonb' if has_snapshot_column else ''
    snapshot_update = (
        ', "Visibility_snapshot" = CASE WHEN saved."Pinned_version" IS NULL '
        'AND saved."Submitted_version" IS NULL THEN EXCLUDED."Visibility_snapshot" '
        'ELSE saved."Visibility_snapshot" END'
    ) if has_snapshot_column else ''
    cursor.execute(
        f'''INSERT INTO enrolment."{ASSIGNMENT_TABLE}" AS saved
            ("Learner_kind", "Learner_id", "Pinned_version", "Submitted_version", "Pinned_at", "Submitted_at", "Updated_at"{snapshot_column})
            VALUES (%s, %s, CASE WHEN %s THEN %s ELSE NULL END,
                    CASE WHEN %s THEN %s ELSE NULL END,
                    CASE WHEN %s THEN now() ELSE NULL END,
                    CASE WHEN %s THEN now() ELSE NULL END, now(){snapshot_value})
            ON CONFLICT ("Learner_kind", "Learner_id") DO UPDATE SET
              "Pinned_version" = CASE WHEN %s THEN COALESCE(saved."Pinned_version", EXCLUDED."Pinned_version") ELSE saved."Pinned_version" END,
              "Pinned_at" = CASE WHEN %s THEN COALESCE(saved."Pinned_at", EXCLUDED."Pinned_at") ELSE saved."Pinned_at" END,
              "Submitted_version" = CASE WHEN %s THEN COALESCE(saved."Submitted_version", EXCLUDED."Submitted_version") ELSE saved."Submitted_version" END,
              "Submitted_at" = CASE WHEN %s THEN COALESCE(saved."Submitted_at", EXCLUDED."Submitted_at") ELSE saved."Submitted_at" END,
              "Updated_at" = now(){snapshot_update}''',
        [kind, learner_id, checkpoint, version, submitted, version, checkpoint, submitted,
         *([json.dumps(visibility_snapshot) if visibility_snapshot else None] if has_snapshot_column else []),
         checkpoint, checkpoint, submitted, submitted],
    )


def custom_fields(layout):
    """Every custom field in the layout by key, with the slug of its step."""
    out = {}
    for step in (layout or {}).get("steps") or []:
        for item in step.get("items") or []:
            if isinstance(item, dict) and not item.get("builtin") and item.get("key"):
                out[item["key"]] = (item, step.get("slug"))
    return out


# ── validating a layout ──────────────────────────────────────────────────────


def _clean_text(value, field, limit, required=True):
    text = str(value or "").strip()
    if required and not text:
        raise LayoutError(f"{field} is required.")
    if len(text) > limit:
        raise LayoutError(f"{field} must be {limit} characters or fewer.")
    return text


def _clean_condition(raw):
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise LayoutError("A condition must be an object.")
    field = raw.get("field")
    values = raw.get("values")
    if not isinstance(field, str) or not (CUSTOM_KEY_RE.match(field) or BUILTIN_KEY_RE.match(field)):
        raise LayoutError("A condition must name the dropdown it depends on.")
    if not isinstance(values, list) or not values or len(values) > MAX_OPTIONS:
        raise LayoutError("A condition needs at least one answer to show the field for.")
    clean = {"field": field, "values": [_clean_text(v, "A condition answer", 300) for v in values]}
    path = raw.get("path")
    if path:
        if not isinstance(path, str) or not PATH_RE.match(path):
            raise LayoutError("A condition's answer path is not valid.")
        clean["path"] = path
    return clean


def _clean_options(raw, kind="dropdown"):
    if not isinstance(raw, list):
        raise LayoutError(f"A {kind} needs a list of options.")
    seen, out = set(), []
    for option in raw:
        text = _clean_text(option, "A dropdown option", 300, required=False)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    if not out:
        raise LayoutError(f"A {kind} needs at least one option.")
    if len(out) > MAX_OPTIONS:
        raise LayoutError(f"A {kind} can have at most {MAX_OPTIONS} options.")
    return out


def normalize_layout(raw, previous):
    """Validate `raw` against the previous layout and assign storage.

    Returns (layout, plan): the cleaned layout to store, and the DDL it needs as
    {table: {"index": name, "create": bool, "columns": {column: sql_type}}}.
    Raises LayoutError with an admin-facing message.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("steps"), list):
        raise LayoutError("The layout must have a list of steps.")
    if not raw["steps"]:
        raise LayoutError("The wizard needs at least one step.")
    if len(raw["steps"]) > MAX_STEPS:
        raise LayoutError(f"The wizard can have at most {MAX_STEPS} steps.")

    prev_fields = custom_fields(previous)
    prev_steps = {
        s.get("slug"): s for s in (previous or {}).get("steps") or [] if isinstance(s, dict) and not s.get("builtin")
    }
    used_tables = {t for t, _i, _c in BUILTIN_STEP_TABLES.values()}
    used_tables.update(s.get("table") for s in prev_steps.values() if s.get("table"))
    used_columns = {(f.get("table"), f.get("column")) for f, _s in prev_fields.values()}
    used_keys = set(prev_fields)

    steps, step_tables, seen_slugs, seen_keys = [], {}, set(), set()
    # The builder gives a new field a temporary key; the server assigns the real
    # one. A condition in the same publish may point at the temporary key.
    renamed = {}

    for raw_step in raw["steps"]:
        if not isinstance(raw_step, dict):
            raise LayoutError("Each step must be an object.")
        slug = raw_step.get("slug")
        builtin = slug in BUILTIN_STEP_TABLES
        label = _clean_text(raw_step.get("label") or BUILTIN_STEP_LABELS.get(slug), "A step name", 120)
        if builtin:
            table, index, create = BUILTIN_STEP_TABLES[slug]
        else:
            # A step keeps the slug it was first published with; a new one gets
            # a fresh slug from the server, whatever the client called it.
            if slug not in prev_steps or slug in seen_slugs:
                slug = f"custom-{_slug(label, 30).replace('_', '-')}-{secrets.token_hex(2)}"
            prior = prev_steps.get(slug)
            table = prior.get("table") if prior else None
            if not table or not TABLE_RE.match(table):
                base = f"Wizard_Custom_{_slug(label, 40)}"
                table, n = base, 2
                while table in used_tables:
                    table, n = f"{base}_{n}", n + 1
                used_tables.add(table)
            index, create = f"{table.lower()}_learner_uniq"[:63], True
        if slug in seen_slugs:
            raise LayoutError(f"The step '{label}' appears twice.")
        seen_slugs.add(slug)
        step_tables[slug] = (table, index, create)

        raw_items = raw_step.get("items") or []
        if not isinstance(raw_items, list) or len(raw_items) > MAX_ITEMS_PER_STEP:
            raise LayoutError(f"'{label}' has too many items.")
        items = []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                raise LayoutError("Each item must be an object.")
            if raw_item.get("builtin"):
                key = raw_item.get("key")
                if not isinstance(key, str) or not BUILTIN_KEY_RE.match(key) or key.startswith("cf_"):
                    raise LayoutError("A built-in item has an invalid key.")
                item = {"key": key, "builtin": True, "hidden": bool(raw_item.get("hidden"))}
                if isinstance(raw_item.get("required"), bool):
                    item["required"] = raw_item["required"]
                condition = _clean_condition(raw_item.get("condition"))
                if condition:
                    item["condition"] = condition
            else:
                item = _normalize_custom(raw_item, slug, table, label, prev_fields, used_keys, used_columns)
                key = item["key"]
                if raw_item.get("key") and raw_item.get("key") != key:
                    renamed[raw_item["key"]] = key
            if key in seen_keys:
                raise LayoutError(f"An item appears twice in the wizard ({key}).")
            seen_keys.add(key)
            items.append(item)

        step = {"slug": slug, "label": label, "builtin": builtin, "hidden": bool(raw_step.get("hidden")), "items": items}
        if not builtin:
            step["table"] = table
        steps.append(step)

    _restore_missing(steps, step_tables, prev_steps, prev_fields, seen_keys)
    for s_ in steps:
        for i_ in s_["items"]:
            if i_.get("condition") and i_["condition"]["field"] in renamed:
                i_["condition"]["field"] = renamed[i_["condition"]["field"]]
    _check_conditions(steps)

    texts = _clean_texts(raw.get("texts"))

    plan = {}
    for step in steps:
        for item in step["items"]:
            if item.get("builtin"):
                continue
            if item.get("type") in CONTENT_FIELD_TYPES:
                continue
            table = item["table"]
            index, create = _table_meta(table, step_tables)
            entry = plan.setdefault(table, {"index": index, "create": create, "columns": {}})
            entry["columns"][item["column"]] = COLUMN_TYPES[item["type"]]
    layout = {"steps": steps}
    if texts:
        layout["texts"] = texts
    from programme_suitability.configuration import ConfigurationError, integrate_layout
    try:
        integrate_layout(raw, previous, layout)
        from programme_suitability.modules_configuration import integrate_layout as integrate_modules
        integrate_modules(raw, previous, layout)
    except ConfigurationError as exc:
        raise LayoutError(str(exc)) from exc
    from .skills_radar.catalogue import CatalogueError, integrate_layout as integrate_radar
    try:
        integrate_radar(raw, previous, layout)
    except CatalogueError as exc:
        raise LayoutError(str(exc)) from exc
    return with_suitability_step(layout), plan


def _clean_texts(raw):
    """The builder's edited wording, by text slot. Blank edits are dropped (the
    standard wording applies); which slots exist is the frontend registry's
    business, so only the shape is checked here."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise LayoutError("Edited wording must be an object.")
    if len(raw) > MAX_TEXTS:
        raise LayoutError("Too many edited texts.")
    out = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not TEXT_KEY_RE.match(key):
            raise LayoutError("Edited wording has an invalid key.")
        if not isinstance(value, str):
            raise LayoutError("Edited wording must be text.")
        if len(value) > MAX_TEXT_LENGTH:
            raise LayoutError(f"Edited wording must be {MAX_TEXT_LENGTH:,} characters or fewer.")
        if value.strip():
            out[key] = value
    return out


def _table_meta(table, step_tables):
    for t, index, create in list(step_tables.values()) + list(BUILTIN_STEP_TABLES.values()):
        if t == table:
            return index, create
    return f"{table.lower()}_learner_uniq"[:63], True


def _normalize_custom(raw_item, step_slug, step_table, step_label, prev_fields, used_keys, used_columns):
    key = raw_item.get("key")
    prior = prev_fields.get(key)[0] if key in prev_fields else None
    field_type = raw_item.get("type")
    if field_type not in FIELD_TYPES:
        raise LayoutError(f"A field on '{step_label}' needs a type: single_line, multi_line, number, upload, dropdown, heading, description, divider, radio or checkbox.")

    content_type = field_type in CONTENT_FIELD_TYPES
    if field_type == "divider":
        label = ""
    else:
        label = _clean_text(
            raw_item.get("label"),
            f"A field on '{step_label}' needs a label;",
            MAX_DESCRIPTION_LENGTH if field_type == "description" else MAX_HEADING_LENGTH,
        )

    if prior:
        prior_type = prior.get("type")
        prior_content = prior_type in CONTENT_FIELD_TYPES
        # Storage is fixed once assigned; so is the type, since it decides the
        # column's SQL type and the answers already stored in it.
        # Legacy text fields used the same PostgreSQL text storage as the new
        # explicit text types. Allow those fields to be upgraded in place so
        # their existing answers and columns are preserved.
        legacy_text_upgrade = prior_type == "text" and field_type in {"single_line", "multi_line"}
        if field_type != prior_type and not legacy_text_upgrade and not (prior_content and content_type):
            raise LayoutError(
                f"'{label or prior.get('label') or 'This field'}' was published as a {prior_type} field; its type cannot change. "
                "Remove it and add a new field instead."
            )
        if prior_content and not content_type:
            table = step_table
            column, n = f"Custom_{_slug(label)}", 2
            while (table, column) in used_columns:
                column, n = f"Custom_{_slug(label)}_{n}", n + 1
            used_columns.add((table, column))
        else:
            table, column = prior.get("table"), prior.get("column")
    else:
        stem = _slug(label)
        key, n = f"cf_{stem}", 2
        while key in used_keys:
            key, n = f"cf_{stem}_{n}", n + 1
        used_keys.add(key)
        table, column = step_table, None
        if not content_type:
            column, n = f"Custom_{key[3:]}", 2
            while (table, column) in used_columns:
                column, n = f"Custom_{key[3:]}_{n}", n + 1
            used_columns.add((table, column))

    if not (isinstance(table, str) and TABLE_RE.match(table)):
        raise LayoutError(f"'{label}' has invalid storage.")
    if not content_type and not (isinstance(column, str) and COLUMN_RE.match(column)):
        raise LayoutError(f"'{label}' has invalid storage.")

    item = {
        "key": key,
        "builtin": False,
        "hidden": bool(raw_item.get("hidden")),
        "required": False if content_type else bool(raw_item.get("required")),
        "label": label,
        "type": field_type,
    }
    if not content_type:
        item["table"] = table
        item["column"] = column
    help_text = _clean_text(raw_item.get("helpText"), "Help text", 500, required=False)
    if help_text:
        item["helpText"] = help_text
    if field_type == "dropdown" or field_type in CHOICE_FIELD_TYPES:
        kind = "choice field" if field_type in CHOICE_FIELD_TYPES else "dropdown"
        item["options"] = _clean_options(raw_item.get("options"), kind)
    condition = _clean_condition(raw_item.get("condition"))
    if condition:
        item["condition"] = condition
    return item


def _restore_missing(steps, step_tables, prev_steps, prev_fields, seen_keys):
    """Put back, hidden, any custom step or field the new layout leaves out.

    A field once published owns a column holding learners' answers; dropping it
    from the layout would orphan that column, so it is kept as removed instead.
    """
    by_slug = {s["slug"]: s for s in steps}
    for slug, prior in prev_steps.items():
        if slug not in by_slug and isinstance(prior.get("table"), str) and TABLE_RE.match(prior["table"]):
            step = {"slug": slug, "label": prior.get("label") or slug, "builtin": False, "hidden": True,
                    "items": [], "table": prior["table"]}
            steps.append(step)
            by_slug[slug] = step
            step_tables[slug] = (prior["table"], f"{prior['table'].lower()}_learner_uniq"[:63], True)
    for key, (prior, slug) in prev_fields.items():
        if key in seen_keys:
            continue
        home = by_slug.get(slug) or steps[0]
        home["items"].append({**prior, "hidden": True})
        seen_keys.add(key)


def _check_conditions(steps):
    items = {i["key"]: i for s in steps for i in s["items"]}
    for item in items.values():
        cond = item.get("condition")
        if not cond:
            continue
        trigger = items.get(cond["field"])
        label = item.get("label") or item["key"]
        if trigger is None:
            raise LayoutError(f"'{label}' depends on a field that is not in the wizard.")
        if trigger is item:
            raise LayoutError(f"'{label}' cannot depend on itself.")
        if not trigger.get("builtin"):
            if trigger.get("type") != "dropdown":
                raise LayoutError(f"'{label}' can only depend on a dropdown field.")
            unknown = [v for v in cond["values"] if v not in trigger.get("options", [])]
            if unknown:
                raise LayoutError(f"'{label}' depends on answers the dropdown does not offer: {', '.join(unknown)}.")
            cond.pop("path", None)
        elif not cond.get("path"):
            raise LayoutError(f"'{label}' depends on a built-in question without saying where its answer is.")


# ── publishing ───────────────────────────────────────────────────────────────


def apply_plan(cursor, plan):
    """Create the tables and columns a layout needs. Idempotent; never drops anything."""
    for table, entry in plan.items():
        qt = _q(table, TABLE_RE)
        if entry["create"]:
            cursor.execute(
                f"""CREATE TABLE IF NOT EXISTS enrolment.{qt} (
                    id             bigserial PRIMARY KEY,
                    "Learner_kind" varchar(32) NOT NULL,
                    "Learner_id"   bigint      NOT NULL,
                    "Created_at"   timestamptz NOT NULL DEFAULT now(),
                    "Updated_at"   timestamptz NOT NULL DEFAULT now()
                )"""
            )
        # The projection upserts on (Learner_kind, Learner_id). The built-in
        # tables already carry this index under this name, so it is a no-op there.
        cursor.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {_q(entry['index'], INDEX_RE)} "
            f'ON enrolment.{qt} ("Learner_kind", "Learner_id")'
        )
        for column, sql_type in entry["columns"].items():
            if sql_type not in COLUMN_TYPES.values():
                raise LayoutError(f"Refusing column type {sql_type!r}.")
            cursor.execute(f"ALTER TABLE enrolment.{qt} ADD COLUMN IF NOT EXISTS {_q(column, COLUMN_RE)} {sql_type}")


def ensure_layout_table(cursor):
    cursor.execute("CREATE SCHEMA IF NOT EXISTS enrolment")
    cursor.execute(
        f'''CREATE TABLE IF NOT EXISTS enrolment."{LAYOUT_TABLE}" (
            id           bigserial PRIMARY KEY,
            "Layout"     jsonb NOT NULL,
            "Created_by" text,
            "Created_at" timestamptz NOT NULL DEFAULT now(),
            "Scope"      text NOT NULL DEFAULT 'default',
            "Variant_name" text,
            "Parent_version" bigint,
            "Archived"   boolean NOT NULL DEFAULT false
        )'''
    )
    cursor.execute(f'ALTER TABLE enrolment."{LAYOUT_TABLE}" ADD COLUMN IF NOT EXISTS "Scope" text NOT NULL DEFAULT \'default\'')
    cursor.execute(f'ALTER TABLE enrolment."{LAYOUT_TABLE}" ADD COLUMN IF NOT EXISTS "Variant_name" text')
    cursor.execute(f'ALTER TABLE enrolment."{LAYOUT_TABLE}" ADD COLUMN IF NOT EXISTS "Parent_version" bigint')
    cursor.execute(f'ALTER TABLE enrolment."{LAYOUT_TABLE}" ADD COLUMN IF NOT EXISTS "Archived" boolean NOT NULL DEFAULT false')
    cursor.execute(
        f'''CREATE TABLE IF NOT EXISTS enrolment."{ASSIGNMENT_TABLE}" (
            "Learner_kind" text NOT NULL,
            "Learner_id" bigint NOT NULL,
            "Pinned_version" bigint,
            "Override_version" bigint,
            "Submitted_version" bigint,
            "Reason" text,
            "Assigned_by" text,
            "Assigned_at" timestamptz,
            "Pinned_at" timestamptz,
            "Submitted_at" timestamptz,
            "Updated_at" timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY ("Learner_kind", "Learner_id")
        )'''
    )


def _actor(request):
    account = getattr(request, "login_account", None)
    if account is not None:
        return getattr(account, "display_name", None) or getattr(account, "email", None) or f"{account.role}:{account.subject_id}"
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return f"django:{user.get_username()}"
    return None


def _target_learners_exist(kind, learner_ids):
    from learner_api.models import CommercialUser, EnrolmentUser

    model = EnrolmentUser if kind == "apprenticeship" else CommercialUser
    return model.objects.filter(pk__in=learner_ids).count() == len(learner_ids)


@enrolment_login_required
def wizard_layout(request, kind=None, learner_id=None):
    """The layout in force. Readable by anyone who can open a wizard."""
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    if kind is not None and kind not in {"apprenticeship", "commercial"}:
        return _error("Unknown learner kind.", 400)
    try:
        with connections[CONN].cursor() as cursor:
            current = learner_layout_with_visibility(cursor, kind, learner_id) if kind is not None and learner_id is not None else read_current(cursor)
            payload = _payload(current)
            if kind is None:
                payload["fieldVisibility"] = read_field_visibility(cursor) if current else {}
            if kind is not None and learner_id is not None and current is not None:
                payload["layoutToken"] = signing.dumps(
                    {"kind": kind, "learnerId": int(learner_id), "version": current[0]},
                    salt="enrolment.wizard-layout.learner-version",
                )
            else:
                payload["layoutToken"] = None
            return JsonResponse(payload)
    except DatabaseError as exc:
        logger.warning("Could not read the wizard layout: %s", exc)
        return _error("Could not load the wizard layout.", 502)


@enrolment_login_required
@csrf_exempt
def set_wizard_field_visibility(request):
    """Hide or restore a field for learners using the live global layout."""
    if request.method != "PUT":
        return _error("Method not allowed.", 405)
    from django.middleware.csrf import CsrfViewMiddleware
    rejection = CsrfViewMiddleware(lambda r: None).process_view(request, lambda r: None, (), {})
    if rejection is not None:
        return rejection
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return _error("Request body must be valid JSON.", 400)
    if not isinstance(body, dict):
        return _error("Request body must be a JSON object.", 400)
    version, key, hidden = body.get("version"), body.get("fieldKey"), body.get("hidden")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        return _error("A current published version is required.", 400)
    if not isinstance(key, str) or len(key) > 80 or not (BUILTIN_KEY_RE.fullmatch(key) or CUSTOM_KEY_RE.fullmatch(key)):
        return _error("Invalid field key.", 400)
    if not isinstance(hidden, bool):
        return _error("'hidden' must be a boolean.", 400)
    try:
        with transaction.atomic(using=CONN):
            with connections[CONN].cursor() as cursor:
                cursor.execute(
                    f'SELECT id, "Layout" @> %s::jsonb FROM enrolment."{LAYOUT_TABLE}" '
                    "WHERE COALESCE(\"Scope\", 'default') = 'default' AND NOT COALESCE(\"Archived\", false) "
                    'ORDER BY id DESC LIMIT 1',
                    [json.dumps({"steps": [{"items": [{"key": key}]}]})],
                )
                current = cursor.fetchone()
                if not current or current[0] != version:
                    return _error("The global wizard changed. Reload the builder before hiding fields.", 409)
                if not current[1]:
                    return _error("This field is not in the published wizard.", 404)
                cursor.execute("SELECT to_regclass(%s)", [f'enrolment."{VISIBILITY_TABLE}"'])
                if cursor.fetchone()[0] is None:
                    return _error("Field visibility storage is not installed yet.", 503)
                if not has_visibility_snapshot_column(cursor):
                    return _error("Field visibility snapshot storage is not installed yet.", 503)
                cursor.execute(
                    f'''INSERT INTO enrolment."{VISIBILITY_TABLE}" ("Field_key", "Hidden", "Updated_by")
                        VALUES (%s, %s, %s)
                        ON CONFLICT ("Field_key") DO UPDATE SET
                            "Hidden" = EXCLUDED."Hidden", "Updated_by" = EXCLUDED."Updated_by", "Updated_at" = now()''',
                    [key, hidden, _actor(request)],
                )
        return JsonResponse({"version": version, "fieldKey": key, "hidden": hidden})
    except DatabaseError as exc:
        logger.warning("Could not update wizard field visibility: %s", exc)
        return _error("Could not update field visibility.", 502)


@enrolment_login_required
@csrf_exempt
def publish_wizard_layout(request):
    """Publish a new layout. Staff with enrolment access only (see auth.py)."""
    if request.method not in ("PUT", "POST"):
        return _error("Method not allowed.", 405)
    if not request.body:
        return _error("Request body is required.", 400)
    if len(request.body) > MAX_LAYOUT_BYTES:
        return _error("Layout too large.", 413)
    try:
        body = json.loads(request.body)
    except ValueError:
        return _error("Request body must be valid JSON.", 400)
    if not isinstance(body, dict):
        return _error("Request body must be a JSON object.", 400)
    from programme_suitability.permissions import is_staff
    if isinstance(body.get('layout'), dict) and 'skillsRadarCatalogues' in body['layout']:
        if not is_staff(request):
            return _error('Admissions staff access required to publish Skills Radar configuration.', 403)
        # Legacy layout clients predate CSRF. Catalogue imports always use the
        # secure builder client, so enforce CSRF for this new publishing path.
        from django.middleware.csrf import CsrfViewMiddleware
        rejection = CsrfViewMiddleware(lambda r: None).process_view(request, lambda r: None, (), {})
        if rejection is not None:
            return rejection
    if isinstance(body.get('layout'), dict) and 'programmeModules' in body['layout'] and not is_staff(request):
        return _error('Admissions staff access required to publish modules configuration.', 403)
    if isinstance(body.get("layout"), dict) and "programmeSuitability" in body["layout"] and not is_staff(request):
        return _error("Admissions staff access required to publish suitability configuration.", 403)
    base_version = body.get("baseVersion")
    if base_version is not None and not isinstance(base_version, int):
        return _error("'baseVersion' must be a number or null.", 400)
    scope = body.get("scope", "default")
    if not isinstance(scope, str) or scope not in {"default", "targeted"}:
        return _error("'scope' must be 'default' or 'targeted'.", 400)
    if scope == "targeted":
        from django.middleware.csrf import CsrfViewMiddleware
        rejection = CsrfViewMiddleware(lambda r: None).process_view(request, lambda r: None, (), {})
        if rejection is not None:
            return rejection
    variant_name = str(body.get("name") or "").strip()
    parent_version = body.get("parentVersion", base_version)
    if scope == "targeted" and (not variant_name or len(variant_name) > 120):
        return _error("A targeted version name of 1 to 120 characters is required.", 400)
    if scope == "targeted" and (not isinstance(parent_version, int) or isinstance(parent_version, bool)):
        return _error("A targeted version must reference a published parent version.", 400)
    target_assignment = body.get("assignTo") if scope == "targeted" else None
    target_kind = target_assignment.get("learnerKind") if isinstance(target_assignment, dict) else None
    target_ids = target_assignment.get("learnerIds") if isinstance(target_assignment, dict) else None
    target_reason = str(target_assignment.get("reason") or "").strip()[:500] if isinstance(target_assignment, dict) else ""
    if scope == "targeted":
        if not isinstance(target_kind, str) or target_kind not in {"apprenticeship", "commercial"}:
            return _error("Choose the learner type for this targeted version.", 400)
        if not isinstance(target_ids, list) or not target_ids or len(target_ids) > 500:
            return _error("Select between 1 and 500 learners for this targeted version.", 400)
        try:
            if any(isinstance(value, bool) for value in target_ids):
                raise ValueError
            target_ids = list(dict.fromkeys(int(value) for value in target_ids))
        except (TypeError, ValueError):
            return _error("Learner ids must be integers.", 400)
        if any(value <= 0 for value in target_ids):
            return _error("Learner ids must be positive integers.", 400)
        try:
            if not _target_learners_exist(target_kind, target_ids):
                return _error("One or more selected learners could not be found.", 404)
        except DatabaseError:
            return _error("Could not verify the selected learners.", 503)

    try:
        with transaction.atomic(using=CONN):
            with connections[CONN].cursor() as cursor:
                ensure_layout_table(cursor)
                # One publish at a time, so two admins cannot both pass the
                # version check below and the later one silently win.
                cursor.execute(f'LOCK TABLE enrolment."{LAYOUT_TABLE}" IN SHARE ROW EXCLUSIVE MODE')
                current = read_current(cursor)
                if scope == "default":
                    current_version = current[0] if current else None
                    if current_version != base_version:
                        return _error(
                            "Someone else has published the wizard since you opened it. "
                            "Reload the builder to see their changes, then make yours again.",
                            409,
                        )
                    parent = current
                else:
                    parent = read_version(cursor, parent_version)
                    if parent is None:
                        return _error("The selected parent wizard version does not exist.", 404)
                    cursor.execute(
                        f'SELECT "Learner_id" FROM enrolment."{ASSIGNMENT_TABLE}" '
                        'WHERE "Learner_kind" = %s AND "Learner_id" = ANY(%s) AND "Submitted_version" IS NOT NULL',
                        [target_kind, target_ids],
                    )
                    if cursor.fetchall():
                        return _error("Submitted learners keep their submitted layout and cannot receive a new assignment.", 409)
                layout, plan = normalize_layout(body.get("layout"), parent[1] if parent else None)
                apply_plan(cursor, plan)
                cursor.execute(
                    f'INSERT INTO enrolment."{LAYOUT_TABLE}" '
                    '("Layout", "Created_by", "Scope", "Variant_name", "Parent_version") '
                    'VALUES (%s::jsonb, %s, %s, %s, %s) RETURNING id, "Created_at"',
                    [json.dumps(layout), _actor(request), scope, variant_name or None, parent_version if scope == "targeted" else None],
                )
                version, created_at = cursor.fetchone()
                if scope == "targeted":
                    cursor.execute(
                        f'''INSERT INTO enrolment."{ASSIGNMENT_TABLE}"
                            ("Learner_kind", "Learner_id", "Override_version", "Reason", "Assigned_by", "Assigned_at", "Updated_at")
                            SELECT %s, learner_id, %s, %s, %s, now(), now()
                            FROM unnest(%s::bigint[]) AS selected(learner_id)
                            ON CONFLICT ("Learner_kind", "Learner_id") DO UPDATE SET
                              "Override_version" = EXCLUDED."Override_version", "Reason" = EXCLUDED."Reason",
                              "Assigned_by" = EXCLUDED."Assigned_by", "Assigned_at" = now(), "Updated_at" = now()''',
                        [target_kind, version, target_reason or None, _actor(request), target_ids],
                    )
    except LayoutError as exc:
        return _error(str(exc), 400)
    except DatabaseError as exc:
        logger.warning("Could not publish the wizard layout: %s", exc)
        return _error(f"Could not publish the wizard layout: {exc}", 502)

    response = _payload((version, layout, created_at, _actor(request)))
    response.update({"scope": scope, "name": variant_name or None, "parentVersion": parent_version if scope == "targeted" else None,
                     "assigned": len(target_ids) if scope == "targeted" else 0})
    return JsonResponse(response)


@enrolment_login_required
def wizard_layout_versions(request):
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    try:
        with connections[CONN].cursor() as cursor:
            if not _table_exists(cursor):
                return JsonResponse({"results": []})
            cursor.execute(
                f'SELECT id, "Scope", "Variant_name", "Parent_version", "Created_at", "Created_by" '
                f'FROM enrolment."{LAYOUT_TABLE}" WHERE NOT COALESCE("Archived", false) ORDER BY id DESC'
            )
            rows = cursor.fetchall()
        return JsonResponse({"results": [
            {"version": row[0], "scope": row[1], "name": row[2] or ("Global version" if row[1] == "default" else "Targeted version"),
             "parentVersion": row[3], "createdAt": row[4].isoformat() if row[4] else "", "createdBy": row[5] or ""}
            for row in rows
        ]})
    except DatabaseError as exc:
        logger.warning("Could not list wizard versions: %s", exc)
        return _error("Could not load wizard versions.", 502)


@enrolment_login_required
@csrf_exempt
def assign_wizard_layout(request):
    if request.method not in ("PUT", "POST"):
        return _error("Method not allowed.", 405)
    from django.middleware.csrf import CsrfViewMiddleware
    rejection = CsrfViewMiddleware(lambda r: None).process_view(request, lambda r: None, (), {})
    if rejection is not None:
        return rejection
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return _error("Request body must be valid JSON.", 400)
    if not isinstance(body, dict):
        return _error("Request body must be a JSON object.", 400)
    kind = body.get("learnerKind")
    learner_ids = body.get("learnerIds")
    version = body.get("version")
    reason = str(body.get("reason") or "").strip()[:500]
    if not isinstance(kind, str) or kind not in {"apprenticeship", "commercial"}:
        return _error("'learnerKind' must be 'apprenticeship' or 'commercial'.", 400)
    if not isinstance(learner_ids, list) or not learner_ids or len(learner_ids) > 500:
        return _error("Select between 1 and 500 learners.", 400)
    try:
        if any(isinstance(value, bool) for value in learner_ids):
            raise ValueError
        learner_ids = list(dict.fromkeys(int(value) for value in learner_ids))
    except (TypeError, ValueError):
        return _error("Learner ids must be integers.", 400)
    if any(value <= 0 for value in learner_ids):
        return _error("Learner ids must be positive integers.", 400)
    try:
        if not _target_learners_exist(kind, learner_ids):
            return _error("One or more selected learners could not be found.", 404)
    except DatabaseError:
        return _error("Could not verify the selected learners.", 503)
    clear = body.get("clear") is True
    if not clear and (not isinstance(version, int) or isinstance(version, bool)):
        return _error("A published version is required.", 400)
    try:
        with transaction.atomic(using=CONN):
            with connections[CONN].cursor() as cursor:
                if not clear and read_version(cursor, version) is None:
                    return _error("The selected wizard version does not exist.", 404)
                if not clear:
                    cursor.execute(
                        f'SELECT "Learner_id" FROM enrolment."{ASSIGNMENT_TABLE}" '
                        'WHERE "Learner_kind" = %s AND "Learner_id" = ANY(%s) AND "Submitted_version" IS NOT NULL',
                        [kind, learner_ids],
                    )
                    if cursor.fetchall():
                        return _error("Submitted learners keep their submitted layout and cannot receive a new assignment.", 409)
                cursor.execute(
                    f'''INSERT INTO enrolment."{ASSIGNMENT_TABLE}"
                        ("Learner_kind", "Learner_id", "Override_version", "Reason", "Assigned_by", "Assigned_at", "Updated_at")
                        SELECT %s, learner_id, %s, %s, %s, CASE WHEN %s THEN NULL ELSE now() END, now()
                        FROM unnest(%s::bigint[]) AS selected(learner_id)
                        ON CONFLICT ("Learner_kind", "Learner_id") DO UPDATE SET
                          "Override_version" = EXCLUDED."Override_version", "Reason" = EXCLUDED."Reason",
                          "Assigned_by" = EXCLUDED."Assigned_by", "Assigned_at" = EXCLUDED."Assigned_at", "Updated_at" = now()''',
                    [kind, None if clear else version, reason or None, _actor(request), clear, learner_ids],
                )
        return JsonResponse({"assigned": len(learner_ids), "version": None if clear else version, "cleared": clear})
    except DatabaseError as exc:
        logger.warning("Could not assign wizard version: %s", exc)
        return _error("Could not assign the wizard version.", 502)


# ── answers -> columns ───────────────────────────────────────────────────────


def _read_path(source, path):
    cur = source
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _is_shown(item, ctx, draft, answers, depth=0):
    """Mirror of the wizard's isShown (layout/resolve.ts) for a custom field."""
    items, item_step, hidden_steps, hidden_builtin = ctx
    if item.get("hidden") or item_step.get(item["key"]) in hidden_steps:
        return False
    cond = item.get("condition")
    if not cond:
        return True
    if depth > 8:
        return False
    trigger = items.get(cond.get("field"))
    if trigger is None:
        if cond.get("field") in hidden_builtin:
            return False
        # A built-in trigger is not in the custom index; read its answer by path.
        path = cond.get("path") or ""
        # The Extended ILR's answers travel apart from the rest of the draft.
        value = _read_path(answers or {}, path[4:]) if path.startswith("ilr.") else _read_path(draft, path)
    else:
        if not _is_shown(trigger, ctx, draft, answers, depth + 1):
            return False
        value = (draft.get("custom") or {}).get(trigger["key"])
    return isinstance(value, str) and value in (cond.get("values") or [])


def _column_value(item, value):
    field_type = item.get("type")
    if field_type == "checkbox":
        if not isinstance(value, list):
            return None
        options = item.get("options") or []
        selected = []
        for option in value:
            if isinstance(option, str) and option in options and option not in selected:
                selected.append(option)
        return selected or None
    if field_type == "number":
        text = str(value).strip() if value is not None else ""
        if not text:
            return None
        try:
            number = Decimal(text)
        except InvalidOperation:
            return None
        return number if number.is_finite() else None
    if not isinstance(value, str):
        return None
    limit = MAX_SINGLE_LINE_ANSWER if field_type == "single_line" else MAX_MULTILINE_ANSWER
    text = value.strip()[:limit]
    if not text:
        return None
    if field_type in {"dropdown", "radio"} and text not in (item.get("options") or []):
        return None
    return text


def project_custom_fields(kind, learner_id, draft, answers=None, layout=None):
    """Write the draft's custom answers into their columns, one upsert per table.

    Runs inside the caller's transaction (the Extended ILR save). Only keys the
    draft actually carries are written — an absent key leaves its column alone,
    an explicitly blank one clears it. Removed fields are not written, so their
    stored answers are kept; an answer whose condition is not met is cleared,
    so a question the learner was not shown never keeps a stale answer. Uploads
    are written by their own endpoint, never from the draft.
    """
    custom = (draft or {}).get("custom")
    if not isinstance(custom, dict) or not custom:
        return
    if layout is None:
        layout = current_layout()
    indexed = custom_fields(layout)
    fields = {key: item for key, (item, _slug_) in indexed.items()}
    if not fields:
        return
    steps = (layout or {}).get("steps") or []
    hidden_steps = {s.get("slug") for s in steps if s.get("hidden")}
    hidden_builtin = {
        i.get("key") for s in steps for i in s.get("items") or []
        if i.get("builtin") and (i.get("hidden") or s.get("hidden"))
    }
    ctx = (fields, {key: slug for key, (_item, slug) in indexed.items()}, hidden_steps, hidden_builtin)

    by_table = {}
    by_table_types = {}
    for key, value in custom.items():
        item = fields.get(key)
        # Removed fields (and fields on removed steps) keep what they hold.
        if item is None or item.get("hidden") or ctx[1].get(key) in hidden_steps or item.get("type") in {"upload", *CONTENT_FIELD_TYPES}:
            continue
        table, column = item.get("table"), item.get("column")
        if not (isinstance(table, str) and TABLE_RE.match(table) and isinstance(column, str) and COLUMN_RE.match(column)):
            continue
        shown = _is_shown(item, ctx, draft, answers)
        by_table.setdefault(table, {})[column] = _column_value(item, value) if shown else None
        by_table_types.setdefault(table, {})[column] = item.get("type")

    with connections[CONN].cursor() as cursor:
        for table, columns in by_table.items():
            names = list(columns)
            quoted = [_q(c, COLUMN_RE) for c in names]
            placeholders = ["%s::jsonb" if by_table_types[table].get(c) == "checkbox" else "%s" for c in names]
            values = [
                json.dumps(columns[c]) if by_table_types[table].get(c) == "checkbox" and columns[c] is not None else columns[c]
                for c in names
            ]
            cursor.execute(
                f'INSERT INTO enrolment.{_q(table, TABLE_RE)} ("Learner_kind", "Learner_id", {", ".join(quoted)}) '
                f"VALUES (%s, %s, {', '.join(placeholders)}) "
                'ON CONFLICT ("Learner_kind", "Learner_id") DO UPDATE SET '
                + ", ".join(f"{q} = EXCLUDED.{q}" for q in quoted)
                + ', "Updated_at" = now()',
                [kind, learner_id, *values],
            )
