"""The eligibility form's editable layout -- what the "Edit eligibility form"
builder publishes.

    GET /enrolment_api/public/eligibility-form-layout/   -> {layout, version}  (no sign-in)
    GET /enrolment_api/eligibility-form-layout/          -> {layout, version, updatedAt, updatedBy}
    PUT /enrolment_api/eligibility-form-layout/publish/  -> body {layout, baseVersion}; same shape back

Storage
-------
enrolment."Eligibility_Form_Layouts" is append-only, like the wizard's
"Wizard_Form_Layouts" (wizard_layout.py): every publish is a new row, the newest
row is the form in force, and the earlier rows record who changed it and when.

A layout is a list of sections, each a list of items:

  * built-in items -- the questions of eligibility_form.FIELDS and the
    representative note -- by key, with whether each is shown, whether it is
    required, and for the three choice questions their answer list;
  * custom questions added in the builder -- text, number, date, yes/no or
    dropdown -- optionally shown only for certain answers to another question.

Built-in wording (labels, hints, the note, the page heading) is edited as text
slots in layout["texts"]; which slots exist is the frontend registry's business
(pages/apprenticeship-application/layout/registry.ts), so only their shape is
checked here.

Each custom question gets a custom_* column in enrolment.eligibility_form, added
by the publish in the same transaction as the layout row, so a layout can never
name a column that does not exist. The server chooses every column name, and a
question keeps its column and type once published: removing it only hides it,
and a layout that leaves a published question out gets it back, hidden. Nothing
here ever drops a column.

Publishing is for staff with enrolment access (auth.py). The public form reads
the layout in force through the public endpoint, which carries no names.
"""
import json
import logging
import re
import secrets

from django.db import DatabaseError, connections, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from . import eligibility_form as form
from .auth import enrolment_login_required
from .wizard_layout import LayoutError, _actor, _clean_options, _clean_text, _clean_texts, _slug

logger = logging.getLogger(__name__)

CONN = form.CONN
LAYOUT_TABLE = "Eligibility_Form_Layouts"

#: A layout is a few KB; this only stops a runaway payload.
MAX_LAYOUT_BYTES = 256 * 1024
MAX_SECTIONS = 20
MAX_ITEMS_PER_SECTION = 80
#: Each custom question is a column; this keeps the table far from Postgres's limit.
MAX_CUSTOM_QUESTIONS = 300

#: The built-in sections: their standard title, and the items they hold in the standard form.
BUILTIN_SECTIONS = {
    "personal-details": (
        "Personal Details",
        ("firstName", "surname", "workEmail", "phoneNumber", "fullAddress", "postcode", "dateOfBirth"),
    ),
    "employment": (
        "Employment",
        ("employmentStatus", "majorityHoursInEngland", "employerName", "employerAddress", "employerPostcode",
         "note.representative", "representativeName", "representativeEmail"),
    ),
    "residency": ("Residency", ("livesInUk", "residenceNotForEducation", "residencyStatus")),
    "programme": (
        "Programme Suitability",
        ("programme", "attendedFundedTraining", "fundedTrainingCompletedOn", "notPreviouslyCompletedProgramme"),
    ),
}
NOTES = frozenset({"note.representative"})
HOME_SECTION = {key: slug for slug, (_title, keys) in BUILTIN_SECTIONS.items() for key in keys}

LOCKED_LABELS = {"firstName": "First Name", "surname": "Surname", "workEmail": "Work Email Address"}
TYPE_NAMES = {"text": "text", "number": "number", "date": "date", "yesno": "yes/no", "dropdown": "dropdown"}

CUSTOM_KEY_RE = re.compile(r"^cf_[a-z0-9_]{1,50}$")
#: Any item key a condition may name, including the temporary key the builder
#: gives a question it has not published yet.
ITEM_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{0,79}$")
MAX_CONDITION_VALUES = 200


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


# ── reading ──────────────────────────────────────────────────────────────────


def read_current(cursor):
    """(version, layout, created_at, created_by) of the layout in force, or None."""
    cursor.execute("SELECT to_regclass(%s)", [f'enrolment."{LAYOUT_TABLE}"'])
    if cursor.fetchone()[0] is None:
        return None
    cursor.execute(
        f'SELECT id, "Layout", "Created_at", "Created_by" FROM enrolment."{LAYOUT_TABLE}" ORDER BY id DESC LIMIT 1'
    )
    row = cursor.fetchone()
    if row is None:
        return None
    layout = row[1]
    if isinstance(layout, str):
        layout = json.loads(layout)
    return row[0], layout, row[2], row[3]


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


def custom_questions(layout):
    """Every custom question in the layout by key, with the slug of its section."""
    out = {}
    for section in (layout or {}).get("sections") or []:
        for item in section.get("items") or []:
            if isinstance(item, dict) and not item.get("builtin") and item.get("key"):
                out[item["key"]] = (item, section.get("slug"))
    return out


# ── validating a layout ──────────────────────────────────────────────────────


def _answers_of(item):
    """The answers a question offers, as a condition names them; None when it has no fixed set."""
    if item.get("builtin"):
        spec = form.FIELD_BY_KEY.get(item["key"])
        if spec is None:
            return None
        _column, kind, _required, limit = spec
        if kind == "bool":
            return ["Yes", "No"]
        if kind == "choice":
            return list(item.get("options") or limit)
        return None
    if item.get("type") == "yesno":
        return ["Yes", "No"]
    if item.get("type") == "dropdown":
        return list(item.get("options") or [])
    return None


def _clean_condition(raw):
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise LayoutError("A condition must be an object.")
    field, values = raw.get("field"), raw.get("values")
    if not isinstance(field, str) or not ITEM_KEY_RE.match(field):
        raise LayoutError("A condition must name the question it depends on.")
    if not isinstance(values, list) or not values or len(values) > MAX_CONDITION_VALUES:
        raise LayoutError("A condition needs at least one answer to show the question for.")
    return {"field": field, "values": [_clean_text(v, "A condition answer", 300) for v in values]}


def _normalize_builtin(raw_item):
    key = raw_item.get("key")
    if key not in HOME_SECTION:
        raise LayoutError("The form names a built-in question that does not exist.")
    item = {"key": key, "builtin": True, "hidden": bool(raw_item.get("hidden"))}
    if key in NOTES:
        return item
    if isinstance(raw_item.get("required"), bool):
        item["required"] = raw_item["required"]
    if form.FIELD_BY_KEY[key][1] == "choice" and raw_item.get("options") is not None:
        item["options"] = _clean_options(raw_item["options"])
    return item


def _normalize_custom(raw_item, section_title, prev_custom, used_keys, used_columns):
    key = raw_item.get("key")
    prior = prev_custom[key][0] if isinstance(key, str) and key in prev_custom else None
    label = _clean_text(raw_item.get("label"), f"The label of a question in '{section_title}'", 300)
    question_type = raw_item.get("type")
    if question_type not in form.CUSTOM_SQL_TYPES:
        raise LayoutError(f"'{label}' needs a type: text, number, date, yes/no or dropdown.")

    if prior:
        # The column's SQL type, and the answers already in it, fix the type.
        if question_type != prior.get("type"):
            raise LayoutError(
                f"'{label}' was published as a {TYPE_NAMES.get(prior.get('type'), 'different')} question; "
                "its type cannot change. Remove it and add a new question instead."
            )
        column = prior.get("column")
    else:
        stem = _slug(label)
        key, n = f"cf_{stem}", 2
        while key in used_keys:
            key, n = f"cf_{stem}_{n}", n + 1
        used_keys.add(key)
        column, n = f"custom_{key[3:]}", 2
        while column in used_columns:
            column, n = f"custom_{key[3:]}_{n}", n + 1
        used_columns.add(column)

    if not CUSTOM_KEY_RE.match(key) or not form.CUSTOM_COLUMN_RE.match(str(column or "")):
        raise LayoutError(f"'{label}' has invalid storage.")

    item = {
        "key": key,
        "builtin": False,
        "hidden": bool(raw_item.get("hidden")),
        "required": bool(raw_item.get("required")),
        "label": label,
        "type": question_type,
        "column": column,
    }
    help_text = _clean_text(raw_item.get("helpText"), "Help text", 500, required=False)
    if help_text:
        item["helpText"] = help_text
    if question_type == "dropdown":
        item["options"] = _clean_options(raw_item.get("options"))
    condition = _clean_condition(raw_item.get("condition"))
    if condition:
        item["condition"] = condition
    return item


def _restore_missing(sections, prev_custom, seen_keys):
    """Put back whatever the new layout leaves out.

    Built-in sections and questions always exist, so a missing one comes back
    hidden (the questions that identify the applicant come back shown). A
    published custom question owns a column of answers, so it comes back too,
    hidden, rather than orphaning that column.
    """
    by_slug = {s["slug"]: s for s in sections}
    for slug, (title, _keys) in BUILTIN_SECTIONS.items():
        if slug not in by_slug:
            section = {"slug": slug, "title": title, "builtin": True, "hidden": True, "items": []}
            sections.append(section)
            by_slug[slug] = section
    for key, slug in HOME_SECTION.items():
        if key not in seen_keys:
            by_slug[slug]["items"].append({"key": key, "builtin": True, "hidden": key not in form.LOCKED})
            seen_keys.add(key)
    for key, (prior, slug) in prev_custom.items():
        if key not in seen_keys:
            (by_slug.get(slug) or sections[0])["items"].append({**prior, "hidden": True})
            seen_keys.add(key)


def _check_conditions(sections):
    items = {i["key"]: i for s in sections for i in s["items"]}
    for item in items.values():
        condition = item.get("condition")
        # A removed question's condition is checked again if it is restored.
        if not condition or item.get("hidden"):
            continue
        label = item.get("label") or item["key"]
        trigger = items.get(condition["field"])
        if trigger is None:
            raise LayoutError(f"'{label}' depends on a question that is not in the form.")
        if trigger is item:
            raise LayoutError(f"'{label}' cannot depend on itself.")
        answers = _answers_of(trigger)
        if answers is None:
            raise LayoutError(
                f"'{label}' can only depend on a question with set answers: a list of choices, "
                "a dropdown or a yes/no question."
            )
        unknown = [value for value in condition["values"] if value not in answers]
        if unknown:
            raise LayoutError(f"'{label}' depends on answers the form no longer offers: {', '.join(unknown)}.")
        seen, current = {item["key"]}, trigger
        while current.get("condition"):
            following = current["condition"]["field"]
            if following in seen:
                raise LayoutError(f"'{label}' is part of a loop of questions that depend on each other.")
            seen.add(following)
            current = items.get(following) or {}


def _check_locked(sections):
    for section in sections:
        for item in section["items"]:
            if item["key"] in form.LOCKED:
                if item["hidden"] or section["hidden"]:
                    raise LayoutError(f"'{LOCKED_LABELS[item['key']]}' identifies the applicant, so it must stay on the form.")
                # Always required; an override would only mislead.
                item.pop("required", None)


def normalize_layout(raw, previous):
    """Validate `raw` against the previous layout and assign storage.

    Returns (layout, columns): the cleaned layout to store, and the custom
    columns it needs as {column: sql_type}. Raises LayoutError with an
    admin-facing message.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("sections"), list):
        raise LayoutError("The form must have a list of sections.")
    if not raw["sections"]:
        raise LayoutError("The form needs at least one section.")
    if len(raw["sections"]) > MAX_SECTIONS:
        raise LayoutError(f"The form can have at most {MAX_SECTIONS} sections.")

    prev_custom = custom_questions(previous)
    prev_sections = {
        s.get("slug") for s in (previous or {}).get("sections") or [] if isinstance(s, dict) and not s.get("builtin")
    }
    used_keys = set(prev_custom)
    used_columns = {item.get("column") for item, _slug_ in prev_custom.values()}

    sections, seen_slugs, seen_keys = [], set(), set()
    # The builder gives a new question a temporary key; the server assigns the
    # real one. A condition in the same publish may name the temporary key.
    renamed = {}

    for raw_section in raw["sections"]:
        if not isinstance(raw_section, dict):
            raise LayoutError("Each section must be an object.")
        slug = raw_section.get("slug")
        builtin = slug in BUILTIN_SECTIONS
        default_title = BUILTIN_SECTIONS[slug][0] if builtin else None
        title = _clean_text(raw_section.get("title") or default_title, "A section name", 120)
        if not builtin and (slug not in prev_sections or slug in seen_slugs):
            # A section keeps the slug it was first published with; a new one
            # gets a fresh slug from the server, whatever the client called it.
            slug = f"custom-{_slug(title, 30).replace('_', '-')}-{secrets.token_hex(2)}"
        if slug in seen_slugs:
            raise LayoutError(f"The section '{title}' appears twice.")
        seen_slugs.add(slug)

        raw_items = raw_section.get("items") or []
        if not isinstance(raw_items, list) or len(raw_items) > MAX_ITEMS_PER_SECTION:
            raise LayoutError(f"'{title}' has too many questions.")
        items = []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                raise LayoutError("Each question must be an object.")
            if raw_item.get("builtin"):
                item = _normalize_builtin(raw_item)
            else:
                item = _normalize_custom(raw_item, title, prev_custom, used_keys, used_columns)
                if raw_item.get("key") and raw_item["key"] != item["key"]:
                    renamed[raw_item["key"]] = item["key"]
            if item["key"] in seen_keys:
                raise LayoutError(f"A question appears twice in the form ({item['key']}).")
            seen_keys.add(item["key"])
            items.append(item)

        section = {"slug": slug, "title": title, "builtin": builtin, "hidden": bool(raw_section.get("hidden")), "items": items}
        if raw_section.get("description") is not None:
            section["description"] = _clean_text(raw_section.get("description"), "A section description", 500, required=False)
        sections.append(section)

    _restore_missing(sections, prev_custom, seen_keys)
    for section in sections:
        for item in section["items"]:
            if item.get("condition") and item["condition"]["field"] in renamed:
                item["condition"]["field"] = renamed[item["condition"]["field"]]
    _check_conditions(sections)
    _check_locked(sections)

    custom = [item for section in sections for item in section["items"] if not item["builtin"]]
    if len(custom) > MAX_CUSTOM_QUESTIONS:
        raise LayoutError(f"The form can have at most {MAX_CUSTOM_QUESTIONS} custom questions, removed ones included.")

    layout = {"sections": sections}
    texts = _clean_texts(raw.get("texts"))
    if texts:
        layout["texts"] = texts
    return layout, {item["column"]: form.CUSTOM_SQL_TYPES[item["type"]] for item in custom}


# ── views ────────────────────────────────────────────────────────────────────


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


def public_eligibility_form_layout(request):
    """The form in force, for /apprenticeship_application. No sign-in, no names."""
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    try:
        with connections[CONN].cursor() as cursor:
            current = read_current(cursor)
    except DatabaseError as exc:
        logger.warning("Could not read the eligibility form layout: %s", exc)
        return _error("The application form is not available just now. Please try again in a few minutes.", 503)
    return JsonResponse({"layout": current[1] if current else None, "version": current[0] if current else None})


@enrolment_login_required
def eligibility_form_layout(request):
    """The form in force, with who published it and when. Staff with enrolment access only."""
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    try:
        with connections[CONN].cursor() as cursor:
            return JsonResponse(_payload(read_current(cursor)))
    except DatabaseError as exc:
        logger.warning("Could not read the eligibility form layout: %s", exc)
        return _error("Could not load the eligibility form.", 502)


@enrolment_login_required
@csrf_exempt
def publish_eligibility_form_layout(request):
    """Publish a new layout. Staff with enrolment access only (see auth.py)."""
    if request.method not in ("PUT", "POST"):
        return _error("Method not allowed.", 405)
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        return _error("Missing X-Requested-With header.", 403)
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
    if base_version is not None and (isinstance(base_version, bool) or not isinstance(base_version, int)):
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
                        "Someone else has published the eligibility form since you opened it. "
                        "Reload the builder to see their changes, then make yours again.",
                        409,
                    )
                layout, columns = normalize_layout(body.get("layout"), current[1] if current else None)
                form.ensure_table(cursor)
                form.upgrade_table(cursor)
                form.add_custom_columns(cursor, columns)
                cursor.execute(
                    f'INSERT INTO enrolment."{LAYOUT_TABLE}" ("Layout", "Created_by") VALUES (%s::jsonb, %s) '
                    'RETURNING id, "Created_at"',
                    [json.dumps(layout), _actor(request)],
                )
                version, created_at = cursor.fetchone()
    except LayoutError as exc:
        return _error(str(exc), 400)
    except DatabaseError as exc:
        logger.warning("Could not publish the eligibility form layout: %s", exc)
        return _error(f"Could not publish the eligibility form: {exc}", 502)

    return JsonResponse(_payload((version, layout, created_at, _actor(request))))
