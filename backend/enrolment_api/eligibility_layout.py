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
    dropdown -- optionally shown only for certain answers to another question;
    required yes/no and dropdown questions can carry a versioned screening rule;
  * content-only heading and description blocks, stored in the layout without
    creating answer columns.

Built-in wording (labels, hints, the note, the page heading) is edited as text
slots in layout["texts"]; which slots exist is the frontend registry's business
(pages/apprenticeship-application/layout/registry.ts), so only their shape is
checked here.

Each custom question gets a custom_* column in enrolment.eligibility_form, added
by the publish in the same transaction as the layout row, so a layout can never
name a column that does not exist. The server chooses every column name, and a
question keeps its column and type once published: removing it only hides it,
and a layout that leaves a published question out gets it back, hidden. Heading
and description blocks are stored in the layout JSON and do not collect answers.
Nothing here ever drops a column.

Publishing is for staff with enrolment access (auth.py). The public form reads
the layout in force through the public endpoint, which carries no names.
"""
import json
import logging
import re
import secrets
from copy import deepcopy
from html.parser import HTMLParser

from django.db import DatabaseError, connections, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from . import eligibility_form as form
from .auth import enrolment_login_required
from .wizard_layout import LayoutError, _actor, _clean_options, _clean_text, _clean_texts, _slug

logger = logging.getLogger(__name__)


class _EmailHtmlValidator(HTMLParser):
    """Reject active content and unsafe links in an administrator's email fragment."""

    ALLOWED_TAGS = {"p", "div", "span", "h1", "h2", "h3", "h4", "h5", "h6", "table", "tbody", "thead", "tfoot", "tr", "td", "th", "a", "strong", "b", "em", "i", "u", "ul", "ol", "li", "br", "blockquote", "hr"}
    ALLOWED_ATTRS = {"style", "href", "title", "role", "cellpadding", "cellspacing", "width", "height", "bgcolor", "align", "valign", "colspan", "rowspan"}

    def handle_starttag(self, tag, attrs):
        if tag not in self.ALLOWED_TAGS:
            raise LayoutError(f"The activation email cannot contain <{tag}>.")
        for name, value in attrs:
            if name not in self.ALLOWED_ATTRS:
                raise LayoutError("The activation email contains an unsafe HTML attribute.")
            if name == "href" and value:
                lowered = "".join(value.split()).lower()
                if not lowered.startswith(("https://", "http://", "mailto:")):
                    raise LayoutError("The activation email contains an unsafe link.")
            if name == "style" and value and any(word in value.lower() for word in ("url(", "expression(", "@import")):
                raise LayoutError("The activation email contains unsafe CSS.")

    handle_startendtag = handle_starttag

    def handle_endtag(self, tag):
        if tag not in self.ALLOWED_TAGS:
            raise LayoutError(f"The activation email cannot contain </{tag}>.")


def _clean_activation_email_html(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 40000 or not value.strip():
        raise LayoutError("The activation email HTML must be 1–40,000 characters.")
    if "{{" in value.replace("{{greeting}}", "").replace("{{start_text}}", ""):
        raise LayoutError("Only {{greeting}} and {{start_text}} are supported placeholders.")
    validator = _EmailHtmlValidator()
    validator.feed(value)
    validator.close()
    return value

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
        ("firstName", "surname", "workEmail", "phoneNumber", "dateOfBirth", "programme"),
    ),
    "employment": (
        "Employment",
        ("employmentStatus", "note.employmentRequirements", "startDateWithEmployer", "jobTitle", "employerName", *form.ADDRESS_INPUT_GROUPS["employerAddress"], "employerPostcode",
         "workplaceArrangement", "mainWorkplacePostcode",
         "representativeName", "representativeEmail", "majorityHoursInEngland", "note.representative",
         ),
    ),
    "residency": (
        "Residency",
        ("countryOfResidence", "nationality", "residencyStatus", "residentPrev3Years", "yearsInUk",
         "residenceNotForEducation", *form.ADDRESS_INPUT_GROUPS["fullAddress"], "postcode", "fullTimeEducationPrior", "expectedLeavingDate",
         "attendedFundedTraining", "fundedTrainingCompletedOn", "livesInUk"),
    ),
}
NOTES = frozenset({"note.representative", "note.employmentRequirements"})
HOME_SECTION = {key: slug for slug, (_title, keys) in BUILTIN_SECTIONS.items() for key in keys}
# Recognize legacy publications without restoring their retired question.
HOME_SECTION.update({key: "personal-details" for key in form.RETIRED_PUBLIC_FIELDS})
HOME_SECTION.update(employerAddress="employment", fullAddress="residency")

# These questions were historically stored under Personal Details or
# Programme Suitability.  Their identifiers and columns stay the same, but a
# published layout is canonicalized into Residency so old layouts cannot put
# them back in the wrong section.
MOVED_TO_RESIDENCY = ("fullAddress", *form.ADDRESS_GROUPS["fullAddress"], "postcode", "attendedFundedTraining", "fundedTrainingCompletedOn")

LOCKED_LABELS = {"firstName": "First Name", "surname": "Surname", "workEmail": "Work Email Address"}
TYPE_NAMES = {"text": "text", "number": "number", "date": "date", "yesno": "yes/no", "dropdown": "dropdown", "heading": "heading", "description": "description"}

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


def activation_email_for_application(application_id):
    """Read the email body from the version this applicant actually submitted."""
    with connections[CONN].cursor() as cursor:
        cursor.execute(
            f'''SELECT l."Layout"->>'activationEmailHtml'
                FROM enrolment.eligibility_form AS a
                LEFT JOIN enrolment."{LAYOUT_TABLE}" AS l ON l.id = a.layout_version
                WHERE a.id = %s''',
            [application_id],
        )
        row = cursor.fetchone()
    return row[0] if row else None


def _payload(current):
    from login.email_azure import default_invitation_intro_html

    default_email = {"defaultActivationEmailHtml": default_invitation_intro_html()}
    if current is None:
        return {"layout": None, "version": None, "updatedAt": "", "updatedBy": "", **_workplace_feature(), **default_email}
    version, layout, created_at, created_by = current
    return {
        "layout": layout,
        "version": version,
        "updatedAt": created_at.isoformat() if created_at else "",
        "updatedBy": created_by or "",
        **_workplace_feature(),
        **default_email,
    }


def _workplace_feature():
    return {"workplaceEnabled": True} if form.workplace_enabled() else {}


def structured_address_layout(layout):
    """Replace legacy inputs at their existing positions without rewriting storage.

    Explicit structured items keep their order/visibility/wording. Missing parts
    inherit the former address input's visibility; legacy wording belongs to Line 1.
    """
    result = deepcopy(layout)
    sections = result.get("sections") or []
    present = {item.get("key") for section in sections if isinstance(section, dict)
               for item in (section.get("items") if isinstance(section.get("items"), list) else [])
               if isinstance(item, dict) and item.get("builtin")}
    texts = result.get("texts")
    for section in sections:
        if not isinstance(section, dict) or not isinstance(section.get("items"), list):
            continue
        items = []
        for item in section["items"]:
            if isinstance(item, dict) and item.get("builtin") and item.get("key") in form.LEGACY_ADDRESS_FIELDS:
                continue
            keys = form.ADDRESS_INPUT_GROUPS.get(item.get("key")) if isinstance(item, dict) and item.get("builtin") else None
            if not keys:
                items.append(item)
                continue
            for key in keys:
                if key not in present:
                    items.append({"key": key, "builtin": True, "hidden": bool(item.get("hidden"))})
                    present.add(key)
            if isinstance(texts, dict):
                for slot in ("label", "hint"):
                    source, target = f"{item['key']}.{slot}", f"{keys[0]}.{slot}"
                    if source in texts and target not in texts:
                        texts[target] = texts[source]
        section["items"] = items
    return result


def public_layout(layout, *, include_screening_rules=False):
    """Return a canonical copy; only server-side screening receives pass criteria."""
    if not isinstance(layout, dict):
        return layout
    result = structured_address_layout(layout)
    result.pop("activationEmailHtml", None)
    if not include_screening_rules:
        for section in result.get("sections") or []:
            for item in section.get("items") or []:
                if isinstance(item, dict):
                    item.pop("screeningRule", None)
    sections = result.get("sections") or []
    _retire_programme_section(sections)
    by_slug = {section.get("slug"): section for section in sections if isinstance(section, dict)}
    seen = {item.get("key") for section in sections for item in section.get("items") or [] if isinstance(item, dict)}
    residency = by_slug.get("residency")
    employment = by_slug.get("employment")
    for section in sections:
        section["items"] = [item for item in section.get("items") or []
                            if not (item.get("builtin") and item.get("key") in form.WORKPLACE_KEYS)]
    if employment and form.workplace_enabled():
        employment["hidden"] = False
        items = employment.setdefault("items", [])
        for key in form.SCREENING_REQUIRED_FIELDS:
            existing = next((item for section in sections for item in section.get("items") or []
                             if item.get("builtin") and item.get("key") == key), None)
            if existing is None:
                existing = {"key": key, "builtin": True}
                items.append(existing)
            elif existing not in items:
                for section in sections:
                    section["items"] = [item for item in section.get("items") or [] if item is not existing]
                items = employment["items"]
                items.append(existing)
            existing.update(hidden=False, required=True)
            if key == "employmentStatus":
                existing["options"] = list(form.EMPLOYMENT_STATUSES)
        position = next((i + 1 for i, item in enumerate(items) if item.get("key") == "employerPostcode"), len(items))
        items[position:position] = [{"key": key, "builtin": True, "hidden": False} for key in ("workplaceArrangement", "mainWorkplacePostcode")]
    if residency or employment:
        for key in ("startDateWithEmployer", "jobTitle", "countryOfResidence", "nationality",
                    "residentPrev3Years", "yearsInUk", "fullTimeEducationPrior"):
            if key in seen:
                continue
            target = employment if key in {"startDateWithEmployer", "jobTitle"} else residency
            if target is not None:
                target.setdefault("items", []).append({"key": key, "builtin": True, "hidden": False})
                seen.add(key)
    _move_residency_items(result.get("sections") or [])
    # Older publications may have removed a screening input. Restore it only
    # on the public copy so the applicant sees the evidence the server checks.
    for key in form.SCREENING_PROTECTED_FIELDS:
        home = by_slug.get(HOME_SECTION[key])
        if home is None:
            title = BUILTIN_SECTIONS[HOME_SECTION[key]][0]
            home = {"slug": HOME_SECTION[key], "title": title, "builtin": True, "items": []}
            sections.append(home)
            by_slug[HOME_SECTION[key]] = home
        found = next((item for section in sections for item in section.get("items") or []
                      if item.get("builtin") and item.get("key") == key), None)
        if found is None:
            found = {"key": key, "builtin": True}
            home.setdefault("items", []).append(found)
        elif found not in home["items"]:
            for section in sections:
                section["items"] = [item for item in section.get("items") or [] if item is not found]
            home.setdefault("items", []).append(found)
        home["hidden"] = False
        found["hidden"] = False
    # Keep the conditional confirmation attached to its visible parent, even
    # in older layouts. Work on the public copy; never rewrite stored layouts.
    for parent_key, child_key in (("residentPrev3Years", "residenceNotForEducation"),
                                  ("fullTimeEducationPrior", "expectedLeavingDate")):
        parent_section = next((section for section in sections if not section.get("hidden") and any(
            item.get("builtin") and item.get("key") == parent_key and not item.get("hidden")
            for item in section.get("items") or [])), None)
        if parent_section is None:
            continue
        confirmation = next((item for section in sections for item in section.get("items") or []
                             if item.get("builtin") and item.get("key") == child_key),
                            {"key": child_key, "builtin": True})
        for section in sections:
            section["items"] = [item for item in section.get("items") or []
                                if not (item.get("builtin") and item.get("key") == child_key)]
        confirmation["hidden"] = False
        position = next(i for i, item in enumerate(parent_section["items"]) if item.get("key") == parent_key)
        parent_section["items"].insert(position + 1, confirmation)
    for section in result.get("sections") or ():
        if not isinstance(section, dict):
            continue
        for item in section.get("items") or ():
            if isinstance(item, dict) and item.get("builtin") and item.get("key") in form.RETIRED_PUBLIC_ITEMS:
                item["hidden"] = True
            if isinstance(item, dict) and item.get("builtin") and item.get("key") in form.ALWAYS_VISIBLE_PUBLIC_FIELDS:
                item["hidden"] = False
    # Age evidence is now mandatory, including publications that hid/omitted DOB.
    birth_section = next((section for section in sections if any(
        item.get("builtin") and item.get("key") == "dateOfBirth" for item in section.get("items") or ()
    )), None)
    if birth_section is None:
        birth_section = by_slug.get("personal-details")
        if birth_section is None:
            birth_section = {"slug": "personal-details", "title": "Personal Details", "builtin": True, "items": []}
            sections.insert(0, birth_section)
        birth_section.setdefault("items", []).append({"key": "dateOfBirth", "builtin": True, "hidden": False, "required": True})
    birth_section["hidden"] = False
    for item in birth_section["items"]:
        if item.get("builtin") and item.get("key") == "dateOfBirth":
            item.update(hidden=False, required=True)
    return result


def custom_questions(layout):
    """Every custom question or content block by key and section slug."""
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
    if key in form.FORM_PROTECTED_FIELDS and raw_item.get("hidden"):
        raise LayoutError(f"'{key}' is protected and cannot be removed from the form.")
    # Retired public fields keep their identifiers and storage columns, but a
    # layout cannot make them visible again until they are deliberately
    # reintroduced in a future change.
    item = {
        "key": key,
        "builtin": True,
        "hidden": (bool(raw_item.get("hidden")) and key not in form.ALWAYS_VISIBLE_PUBLIC_FIELDS)
        or (key in form.RETIRED_PUBLIC_ITEMS and key not in form.ALWAYS_VISIBLE_PUBLIC_FIELDS),
    }
    if key in NOTES:
        return item
    if key in form.WORKPLACE_KEYS:
        item["required"] = key == "workplaceArrangement"
        if key == "workplaceArrangement":
            item["options"] = list(form.WORKPLACE_ARRANGEMENTS)
        return item
    if key in form.REQUIRED_ADDRESS_FIELDS or key == "dateOfBirth":
        item["required"] = True
    elif key in form.ADDRESS_INPUT_FIELDS:
        item["required"] = False
    elif isinstance(raw_item.get("required"), bool):
        item["required"] = raw_item["required"]
    if form.FIELD_BY_KEY[key][1] == "choice" and raw_item.get("options") is not None:
        item["options"] = _clean_options(raw_item["options"])
    return item


def _normalize_custom(raw_item, section_title, prev_custom, used_keys, used_columns):
    key = raw_item.get("key")
    prior = prev_custom[key][0] if isinstance(key, str) and key in prev_custom else None
    question_type = raw_item.get("type")
    if question_type not in (*form.CUSTOM_SQL_TYPES, "heading", "description"):
        raise LayoutError("A custom item needs a supported field type.")
    content_only = question_type in {"heading", "description"}
    label = _clean_text(
        raw_item.get("label"),
        "A heading" if question_type == "heading" else "A description" if question_type == "description" else f"The label of a question in '{section_title}'",
        20_000 if question_type == "description" else 300,
    )

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
        column = None
        if not content_only:
            column, n = f"custom_{key[3:]}", 2
            while column in used_columns:
                column, n = f"custom_{key[3:]}_{n}", n + 1
            used_columns.add(column)

    if not CUSTOM_KEY_RE.match(key):
        raise LayoutError(f"'{label}' has an invalid identifier.")
    if not content_only and not form.CUSTOM_COLUMN_RE.match(str(column or "")):
        raise LayoutError(f"'{label}' has invalid storage.")

    item = {
        "key": key,
        "builtin": False,
        "hidden": bool(raw_item.get("hidden")),
        "required": False if content_only else bool(raw_item.get("required")),
        "label": label,
        "type": question_type,
    }
    if not content_only:
        item["column"] = column
    if content_only:
        if raw_item.get("screeningRule") is not None:
            raise LayoutError(f"'{label}' cannot affect eligibility because it does not collect an answer.")
        return item
    help_text = _clean_text(raw_item.get("helpText"), "Help text", 500, required=False)
    if help_text:
        item["helpText"] = help_text
    if question_type == "dropdown":
        item["options"] = _clean_options(raw_item.get("options"))
    condition = _clean_condition(raw_item.get("condition"))
    if condition:
        item["condition"] = condition
    rule = raw_item.get("screeningRule")
    if rule is not None:
        if question_type not in {"yesno", "dropdown"}:
            raise LayoutError(f"'{label}' must be a yes/no or dropdown question to affect eligibility.")
        if not isinstance(rule, dict) or not isinstance(rule.get("eligibleAnswers"), list):
            raise LayoutError(f"'{label}' needs a list of passing answers.")
        answers = ["Yes", "No"] if question_type == "yesno" else item["options"]
        passing = rule["eligibleAnswers"]
        if (not passing or len(passing) >= len(answers) or len(set(map(str, passing))) != len(passing)
                or any(not isinstance(value, str) or value not in answers for value in passing)):
            raise LayoutError(f"'{label}' needs at least one valid passing answer and one failing answer.")
        if not item["required"] or item["hidden"] or condition:
            raise LayoutError(f"'{label}' must be mandatory, shown and unconditional to affect eligibility.")
        item["screeningRule"] = {"eligibleAnswers": passing}
    return item


def _restore_missing(sections, prev_custom, seen_keys):
    """Put back whatever the new layout leaves out.

    Built-in sections and questions always exist, so a missing one comes back
    hidden (identity and screening questions come back shown). A
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
        if key not in seen_keys and key not in form.RETIRED_PUBLIC_FIELDS:
            protected = key in form.non_removable_fields()
            by_slug[slug]["items"].append({
                "key": key,
                "builtin": True,
                "hidden": (
                    key not in form.LOCKED and not protected and key not in form.NEW_PUBLIC_FIELDS
                    and key not in form.ALWAYS_VISIBLE_PUBLIC_FIELDS
                ) or (key in form.RETIRED_PUBLIC_ITEMS and key not in form.ALWAYS_VISIBLE_PUBLIC_FIELDS),
            })
            if protected:
                by_slug[slug]["hidden"] = False
            seen_keys.add(key)
    for key, (prior, slug) in prev_custom.items():
        if key not in seen_keys:
            (by_slug.get(slug) or sections[0])["items"].append({**prior, "hidden": True})
            seen_keys.add(key)


def _move_residency_items(sections):
    """Move legacy residency questions to their canonical section in-place."""
    target = next((section for section in sections if section.get("slug") == "residency"), None)
    if target is None:
        return
    moved = {}
    for section in sections:
        if section is target:
            continue
        keep = []
        for item in section.get("items") or []:
            if item.get("key") in MOVED_TO_RESIDENCY:
                moved[item["key"]] = item
            else:
                keep.append(item)
        section["items"] = keep
    if moved:
        existing = {item.get("key") for item in target.get("items") or []}
        target["items"] = [item for item in target.get("items") or [] if item.get("key") not in moved]
        target["items"].extend(moved[key] for key in MOVED_TO_RESIDENCY if key in moved and key not in existing)


def _retire_programme_section(sections):
    """Move the stable programme answer; retire its old container on a copy only."""
    personal = next((section for section in sections if section.get("slug") == "personal-details"), None)
    if personal is None:
        personal = {"slug": "personal-details", "title": "Personal Details", "builtin": True,
                    "hidden": False, "items": []}
        sections.insert(0, personal)
    programme = next((item for section in sections for item in section.get("items") or []
                      if item.get("builtin") and item.get("key") == "programme"),
                     {"key": "programme", "builtin": True})
    for section in sections:
        section["items"] = [item for item in section.get("items") or [] if not (
            item.get("builtin") and (item.get("key") == "programme" or item.get("key") in form.RETIRED_PUBLIC_FIELDS))]
    programme["hidden"] = False
    personal.setdefault("items", []).append(programme)
    for section in list(sections):
        if section.get("slug") != "programme":
            continue
        # Keep custom columns/questions when their former section is removed.
        personal["items"].extend({**item, "hidden": bool(section.get("hidden") or item.get("hidden"))}
                                 for item in section["items"])
        sections.remove(section)


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
            elif item["key"] in form.screening_protected_fields() and (item["hidden"] or section["hidden"]):
                raise LayoutError(f"'{item['key']}' is used for automatic screening, so it must stay on the form.")
            elif item["key"] in form.FORM_PROTECTED_FIELDS and (item["hidden"] or section["hidden"]):
                raise LayoutError(f"'{item['key']}' is protected and cannot be removed from the form.")
            elif item.get("screeningRule") and (item["hidden"] or section["hidden"]):
                raise LayoutError(f"'{item['label']}' affects eligibility, so it must stay on the form.")


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

    raw = structured_address_layout(raw)
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
        builtin = slug in BUILTIN_SECTIONS or slug == "programme"
        default_title = BUILTIN_SECTIONS[slug][0] if slug in BUILTIN_SECTIONS else "Programme Suitability" if builtin else None
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
            # Older published layouts still contain this retired answer. Do
            # not reintroduce it when an admin publishes a newer layout.
            if raw_item.get("builtin") and raw_item.get("key") == "title":
                continue
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
    _move_residency_items(sections)
    _retire_programme_section(sections)
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
    texts.pop("title.label", None)
    texts.pop("title.hint", None)
    if texts:
        layout["texts"] = texts
    email_html = _clean_activation_email_html(raw.get("activationEmailHtml"))
    if email_html:
        layout["activationEmailHtml"] = email_html
    return layout, {
        item["column"]: form.CUSTOM_SQL_TYPES[item["type"]]
        for item in custom if item.get("type") in form.CUSTOM_SQL_TYPES and item.get("column")
    }


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
    return JsonResponse({
        "layout": public_layout(current[1]) if current else None,
        "version": current[0] if current else None,
        **_workplace_feature(),
    })


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
