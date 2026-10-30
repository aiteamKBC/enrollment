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
from decimal import Decimal, InvalidOperation

from django.db import DatabaseError, connections, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .auth import enrolment_login_required

logger = logging.getLogger(__name__)

CONN = "enrolment"
LAYOUT_TABLE = "Wizard_Form_Layouts"

#: A layout is a few KB; this only stops a runaway payload.
MAX_LAYOUT_BYTES = 512 * 1024
MAX_STEPS = 40
MAX_ITEMS_PER_STEP = 200
MAX_OPTIONS = 200
MAX_TEXT_ANSWER = 10_000

FIELD_TYPES = {"text", "number", "upload", "dropdown"}

#: SQL type of each custom field's column.
COLUMN_TYPES = {"text": "text", "dropdown": "text", "number": "numeric", "upload": "jsonb"}

#: Built-in steps -> (their table, its one-row-per-learner unique index, whether
#: this module may create the table). The first six already exist (see
#: apply_enrolment_wizard_tables / apply_extended_ilr_table); the rest have no
#: per-learner table until a custom field is added to them.
BUILTIN_STEP_TABLES = {
    "personal-details": ("Wizard_Personal_Details", "wizard_personal_details_learner_uniq", False),
    "ilr-details": ("Wizard_Ilr_Learner_Details", "wizard_ilr_learner_details_learner_uniq", False),
    "ilr": ("Extended_ILR", "extended_ilr_learner_uniq", False),
    "cv-job": ("Wizard_Cv_Job", "wizard_cv_job_learner_uniq", False),
    "plr": ("Wizard_Plr", "wizard_plr_learner_uniq", False),
    "skills-radar": ("Wizard_Skills_Radar", "wizard_skills_radar_learner_uniq", False),
    "introduction": ("Wizard_Introduction", "wizard_introduction_learner_uniq", True),
    "before-you-begin": ("Wizard_Before_You_Begin", "wizard_before_you_begin_learner_uniq", True),
    "inclusion": ("Wizard_Inclusion", "wizard_inclusion_learner_uniq", True),
    "policies": ("Wizard_Policies", "wizard_policies_learner_uniq", True),
    "next-steps": ("Wizard_Next_Steps", "wizard_next_steps_learner_uniq", True),
}

BUILTIN_STEP_LABELS = {
    "introduction": "Welcome",
    "before-you-begin": "Before You Begin",
    "personal-details": "Personal Details",
    "ilr-details": "ILR",
    "ilr": "Extended ILR",
    "cv-job": "CV/Job Description",
    "plr": "Personal Learning Record",
    "skills-radar": "Skills Radar",
    "inclusion": "Inclusion",
    "policies": "Policies",
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
    return current[1] if current else None


def _payload(current):
    if current is None:
        return {"layout": None, "version": None, "updatedAt": "", "updatedBy": ""}
    version, layout, created_at, created_by = current
    return {
        "layout": layout,
        "version": version,
        "updatedAt": created_at.isoformat() if created_at else "",
        "updatedBy": created_by or "",
    }


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


def _clean_options(raw):
    if not isinstance(raw, list):
        raise LayoutError("A dropdown needs a list of options.")
    seen, out = set(), []
    for option in raw:
        text = _clean_text(option, "A dropdown option", 300, required=False)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    if not out:
        raise LayoutError("A dropdown needs at least one option.")
    if len(out) > MAX_OPTIONS:
        raise LayoutError(f"A dropdown can have at most {MAX_OPTIONS} options.")
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
            table = item["table"]
            index, create = _table_meta(table, step_tables)
            entry = plan.setdefault(table, {"index": index, "create": create, "columns": {}})
            entry["columns"][item["column"]] = COLUMN_TYPES[item["type"]]
    layout = {"steps": steps}
    if texts:
        layout["texts"] = texts
    return layout, plan


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
    label = _clean_text(raw_item.get("label"), f"A field on '{step_label}' needs a label;", 300)
    field_type = raw_item.get("type")
    if field_type not in FIELD_TYPES:
        raise LayoutError(f"'{label}' needs a type: text, number, upload or dropdown.")

    if prior:
        # Storage is fixed once assigned; so is the type, since it decides the
        # column's SQL type and the answers already stored in it.
        if field_type != prior.get("type"):
            raise LayoutError(
                f"'{label}' was published as a {prior.get('type')} field; its type cannot change. "
                "Remove it and add a new field instead."
            )
        table, column = prior.get("table"), prior.get("column")
    else:
        stem = _slug(label)
        key, n = f"cf_{stem}", 2
        while key in used_keys:
            key, n = f"cf_{stem}_{n}", n + 1
        used_keys.add(key)
        table = step_table
        column, n = f"Custom_{key[3:]}", 2
        while (table, column) in used_columns:
            column, n = f"Custom_{key[3:]}_{n}", n + 1
        used_columns.add((table, column))

    if not (isinstance(table, str) and TABLE_RE.match(table)) or not (isinstance(column, str) and COLUMN_RE.match(column)):
        raise LayoutError(f"'{label}' has invalid storage.")

    item = {
        "key": key,
        "builtin": False,
        "hidden": bool(raw_item.get("hidden")),
        "required": bool(raw_item.get("required")),
        "label": label,
        "type": field_type,
        "table": table,
        "column": column,
    }
    help_text = _clean_text(raw_item.get("helpText"), "Help text", 500, required=False)
    if help_text:
        item["helpText"] = help_text
    if field_type == "dropdown":
        item["options"] = _clean_options(raw_item.get("options"))
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
            "Created_at" timestamptz NOT NULL DEFAULT now()
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


@enrolment_login_required
def wizard_layout(request):
    """The layout in force. Readable by anyone who can open a wizard."""
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    try:
        with connections[CONN].cursor() as cursor:
            return JsonResponse(_payload(read_current(cursor)))
    except DatabaseError as exc:
        logger.warning("Could not read the wizard layout: %s", exc)
        return _error("Could not load the wizard layout.", 502)


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
    base_version = body.get("baseVersion")
    if base_version is not None and not isinstance(base_version, int):
        return _error("'baseVersion' must be a number or null.", 400)

    try:
        with transaction.atomic(using=CONN):
            with connections[CONN].cursor() as cursor:
                ensure_layout_table(cursor)
                # One publish at a time, so two admins cannot both pass the
                # version check below and the later one silently win.
                cursor.execute(f'LOCK TABLE enrolment."{LAYOUT_TABLE}" IN SHARE ROW EXCLUSIVE MODE')
                current = read_current(cursor)
                current_version = current[0] if current else None
                if current_version != base_version:
                    return _error(
                        "Someone else has published the wizard since you opened it. "
                        "Reload the builder to see their changes, then make yours again.",
                        409,
                    )
                layout, plan = normalize_layout(body.get("layout"), current[1] if current else None)
                apply_plan(cursor, plan)
                cursor.execute(
                    f'INSERT INTO enrolment."{LAYOUT_TABLE}" ("Layout", "Created_by") VALUES (%s::jsonb, %s) '
                    'RETURNING id, "Created_at"',
                    [json.dumps(layout), _actor(request)],
                )
                version, created_at = cursor.fetchone()
    except LayoutError as exc:
        return _error(str(exc), 400)
    except DatabaseError as exc:
        logger.warning("Could not publish the wizard layout: %s", exc)
        return _error(f"Could not publish the wizard layout: {exc}", 502)

    return JsonResponse(_payload((version, layout, created_at, _actor(request))))


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
    text = value.strip()[:MAX_TEXT_ANSWER]
    if not text:
        return None
    if field_type == "dropdown" and text not in (item.get("options") or []):
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
    for key, value in custom.items():
        item = fields.get(key)
        # Removed fields (and fields on removed steps) keep what they hold.
        if item is None or item.get("hidden") or ctx[1].get(key) in hidden_steps or item.get("type") == "upload":
            continue
        table, column = item.get("table"), item.get("column")
        if not (isinstance(table, str) and TABLE_RE.match(table) and isinstance(column, str) and COLUMN_RE.match(column)):
            continue
        shown = _is_shown(item, ctx, draft, answers)
        by_table.setdefault(table, {})[column] = _column_value(item, value) if shown else None

    with connections[CONN].cursor() as cursor:
        for table, columns in by_table.items():
            names = list(columns)
            quoted = [_q(c, COLUMN_RE) for c in names]
            cursor.execute(
                f'INSERT INTO enrolment.{_q(table, TABLE_RE)} ("Learner_kind", "Learner_id", {", ".join(quoted)}) '
                f"VALUES (%s, %s, {', '.join(['%s'] * len(names))}) "
                'ON CONFLICT ("Learner_kind", "Learner_id") DO UPDATE SET '
                + ", ".join(f"{q} = EXCLUDED.{q}" for q in quoted)
                + ', "Updated_at" = now()',
                [kind, learner_id, *[columns[c] for c in names]],
            )
