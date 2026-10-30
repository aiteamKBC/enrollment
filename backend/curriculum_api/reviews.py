"""Programme Review Templates ("Reviews ID" tab).

A Review here is a reusable *template* belonging to one Programme -- its
recurrence, eligibility, participant/signature/visibility settings, and a
Form Builder made of Sections, each holding Fields (questions). A Field of
type ``boolean_case_block`` may itself carry conditional child Fields, shown
only when the parent is answered "yes" or "no":

    Review
      Section ("Meeting & Close")
        Field: boolean_case_block "Please confirm the next session is booked"
          yesFields: [ Field: date "The date for the next session is" ]
          noFields:  [ Field: text "Why?" ]

It is deliberately not a learner's completed review: that domain
(``learner_review_instances`` / ``learner_review_answers``) does not exist yet
and can be added later, referencing ``review_templates.id`` /
``review_sections.id`` / ``review_fields.id``, without touching this module.

Storage mirrors week_templates / week_template_components (see
``views.provision_week_template_tables``): three tables in the ``curriculum``
schema, provisioned by ``sql/2026-09-10_curriculum_review_templates.sql`` and
``sql/2026-09-10_curriculum_review_sections_and_advanced_fields.sql`` on
Neon, or created in-process for sqlite tests / local dev via
``provision_review_template_tables`` -- see ``schema_gate`` for why request
paths never provision schema themselves in production.

Every id is generated with ``views.unique_prefixed_id(...)``, the same
generator every other curriculum entity (MOD-, PROG-, KSBP-, WT-) uses:
Reviews get ``REV-``, Sections get ``REVS-``, Fields (top-level and
conditional children alike) get ``REVF-``.

Backward compatibility: a write payload may send either the new
``sections: [{ title, estimatedMinutes, fields: [...] }]`` shape, or the
original flat ``fields: [...]`` shape from the first implementation -- the
latter is treated as sugar for one implicit section titled "General". Every
read response includes both the canonical nested ``sections`` and a
flattened ``fields`` convenience array (every field across every section,
excluding conditional children) so a caller written against the first
version keeps working unmodified.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

from django.db import connection, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from . import review_types
from . import schema_gate
from . import views as curriculum_views

logger = logging.getLogger(__name__)

REVIEW_TEMPLATES_TABLE = 'review_templates'
REVIEW_SECTIONS_TABLE = 'review_sections'
REVIEW_FIELDS_TABLE = 'review_fields'

RECURRENCE_UNITS = ('days', 'weeks', 'months')

FIELD_TYPES = (
    'text', 'boolean', 'numeric', 'date', 'list_item', 'boolean_case_block',
    'email', 'phone', 'postcode_address', 'title_description', 'text_multiline',
    'action_button',
)

# Every field type carries a title/question, including title_description
# ("Title & Description" is a display block whose own Title is that title)
# and action_button (its Title is the text shown on the button itself, e.g.
# "Take snapshot" -- chosen by whoever builds the form).
FIELD_TYPES_REQUIRING_TITLE = FIELD_TYPES

# Field types that never expect a learner-entered answer -- Required/Optional
# is not a meaningful control for these in the Form Builder UI.
DISPLAY_ONLY_FIELD_TYPES = ('title_description', 'action_button')

# Only a boolean_case_block field may own conditional yes/no children, and
# only these two condition values are valid on a child.
CONDITIONAL_FIELD_TYPE = 'boolean_case_block'
CONDITION_VALUES = ('yes', 'no')

# Stable field meaning used by the canonical MCM integration. It lives in the
# existing JSON field configuration and is frozen into each Review Instance.
MEETING_SUMMARY_SEMANTIC_KEY = 'meeting_summary'

PARTICIPANT_ROLES = ('advisor', 'employer', 'participant', 'referrer')

# Non-advisor roles a Field's own configuration may opt into answering it.
# The advisor (coach) can always answer every field regardless of this list --
# it only ever widens who *else* may write an answer, never who may read one
# (see visible_advisor/visible_employer/visible_participant on the template).
# Stored in the field's existing JSON `configuration`, like semanticKey and
# placeholder, rather than a new column -- see the module docstring for why
# Review schema changes are avoided when the JSON configuration already fits.
FIELD_RESPONDENT_ROLES = ('participant', 'employer')

# Every Review carries a Review Type -- its classification, chosen in the
# General tab next to the Review's name. The catalogue of types lives in
# review_types.py; this module only stores which one a template points at.
# Type never implies recurrence, questions, signatures or eligibility: a
# "Monthly Coaching Meeting" scheduled every 6 weeks is valid and supported.

_REVIEW_TABLES_READY = False


def ensure_review_tables():
    """Verify Review tables exist; provision only outside production."""
    global _REVIEW_TABLES_READY
    if _REVIEW_TABLES_READY:
        return
    if not schema_gate.runtime_bootstrap_allowed():
        schema_gate.require_tables(
            REVIEW_TEMPLATES_TABLE, REVIEW_SECTIONS_TABLE, REVIEW_FIELDS_TABLE,
            review_types.REVIEW_TYPES_TABLE,
        )
        _REVIEW_TABLES_READY = True
        return
    provision_review_template_tables()


def provision_review_template_tables():
    """Mirrors the two Review SQL files for sqlite/local dev.

    NOT for production request paths -- schema there is owned by the SQL
    files applied to Neon directly. See schema_gate module docstring.
    """
    global _REVIEW_TABLES_READY
    if _REVIEW_TABLES_READY:
        return
    json_type = curriculum_views.authoring_json_type()
    with connection.cursor() as cursor:
        if connection.vendor == 'postgresql':
            cursor.execute(f'create schema if not exists {curriculum_views.quote_ident(curriculum_views.CURRICULUM_SCHEMA)}')
        cursor.execute(f'''
            create table if not exists {curriculum_views.authoring_table_name(REVIEW_TEMPLATES_TABLE)} (
                id varchar(128) primary key,
                programme_id varchar(255) not null,
                name varchar(500) not null default '',
                enabled boolean not null default true,
                recurrence_interval integer not null default 1,
                recurrence_unit varchar(16) not null default 'weeks',
                schedule_anchor_date date not null default current_date,
                occurrence_count integer,
                review_type_id varchar(128),
                applicable_statuses {json_type},
                applicability {json_type},
                signature_advisor boolean not null default false,
                signature_employer boolean not null default false,
                signature_participant boolean not null default false,
                signature_referrer boolean not null default false,
                visible_advisor boolean not null default true,
                visible_employer boolean not null default true,
                visible_participant boolean not null default true,
                visible_referrer boolean not null default true,
                record_time_spent boolean not null default false,
                expected_otjh numeric(8, 2) not null default 0,
                counts_towards_otjh boolean not null default false,
                allow_editing_prior_days integer not null default 0,
                notify_employer boolean not null default false,
                notify_participant boolean not null default false,
                incomplete_marker varchar(255) not null default '',
                field_count integer not null default 0,
                deleted_at timestamp,
                deleted_by varchar(255),
                deleted_via_parent varchar(255),
                created_by varchar(255) not null default '',
                updated_by varchar(255) not null default '',
                created_at timestamp not null default current_timestamp,
                updated_at timestamp not null default current_timestamp
            )
        ''')
        cursor.execute(f'''
            create table if not exists {curriculum_views.authoring_table_name(REVIEW_SECTIONS_TABLE)} (
                id varchar(128) primary key,
                review_id varchar(128) not null,
                title varchar(500) not null default '',
                estimated_minutes integer not null default 0,
                display_order integer not null default 0,
                enabled boolean not null default true,
                deleted_at timestamp,
                deleted_by varchar(255),
                deleted_via_parent varchar(255),
                created_by varchar(255) not null default '',
                updated_by varchar(255) not null default '',
                created_at timestamp not null default current_timestamp,
                updated_at timestamp not null default current_timestamp
            )
        ''')
        if connection.vendor == 'postgresql':
            cursor.execute(f'alter table {curriculum_views.authoring_table_name(REVIEW_TEMPLATES_TABLE)} add column if not exists applicability {json_type}')
        else:
            cursor.execute(f'pragma table_info({curriculum_views.quote_ident(REVIEW_TEMPLATES_TABLE)})')
            if 'applicability' not in {row[1] for row in cursor.fetchall()}:
                cursor.execute(f'alter table {curriculum_views.authoring_table_name(REVIEW_TEMPLATES_TABLE)} add column applicability {json_type}')
        cursor.execute(f'''
            create table if not exists {curriculum_views.authoring_table_name(REVIEW_FIELDS_TABLE)} (
                id varchar(128) primary key,
                review_id varchar(128) not null,
                section_id varchar(128) not null,
                parent_field_id varchar(128),
                condition_value varchar(16),
                title varchar(500) not null default '',
                field_type varchar(32) not null default 'text',
                required boolean not null default false,
                display_order integer not null default 0,
                configuration {json_type},
                created_at timestamp not null default current_timestamp,
                updated_at timestamp not null default current_timestamp
            )
        ''')
    # A Review cannot be saved without a Review Type, so the type catalogue is
    # part of the same provisioning step rather than a separate opt-in.
    review_types.provision_review_types_table()
    _REVIEW_TABLES_READY = True


# --------------------------------------------------------------------- reads

def get_review_template_rows(where_sql='', params=None, *, include_deleted=False):
    conditions = [where_sql] if where_sql else []
    if not include_deleted:
        conditions.append('deleted_at is null')
    where = ' and '.join(conditions) or '1 = 1'
    return curriculum_views.fetch_all(
        f'select * from {curriculum_views.table_name(REVIEW_TEMPLATES_TABLE)} '
        f'where {where} order by updated_at desc',
        params or [],
    )


def get_review_template_row(review_id, *, include_deleted=False):
    rows = get_review_template_rows('id = %s', [review_id], include_deleted=include_deleted)
    return rows[0] if rows else None


def get_review_section_rows(review_id):
    return curriculum_views.fetch_all(
        f'select * from {curriculum_views.table_name(REVIEW_SECTIONS_TABLE)} '
        f'where review_id = %s and deleted_at is null order by display_order asc',
        [review_id],
    )


def get_review_field_rows(review_id):
    """All fields (top-level and conditional children alike), one query."""
    return curriculum_views.fetch_all(
        f'select * from {curriculum_views.table_name(REVIEW_FIELDS_TABLE)} '
        f'where review_id = %s order by display_order asc',
        [review_id],
    )


# ------------------------------------------------------------------ payload

def review_field_payload(row, children_of):
    """``children_of(parent_id, condition)`` looks up a field's direct
    yes/no children within its own section. Recurses to any depth -- a
    conditional child may itself be a ``boolean_case_block`` with its own
    yesFields/noFields, and so on.
    """
    payload = {
        'id': row.get('id'),
        'reviewId': row.get('review_id'),
        'sectionId': row.get('section_id'),
        'parentFieldId': row.get('parent_field_id'),
        'conditionValue': row.get('condition_value'),
        'title': row.get('title') or '',
        'fieldType': row.get('field_type') or 'text',
        'required': bool(row.get('required')),
        'displayOrder': curriculum_views.parse_int(row.get('display_order'), 0),
        'configuration': curriculum_views.as_json_value(row.get('configuration'), {}),
        'createdAt': row.get('created_at'),
        'updatedAt': row.get('updated_at'),
    }
    if row.get('field_type') == CONDITIONAL_FIELD_TYPE:
        payload['yesFields'] = [review_field_payload(f, children_of) for f in children_of(row.get('id'), 'yes')]
        payload['noFields'] = [review_field_payload(f, children_of) for f in children_of(row.get('id'), 'no')]
    return payload


def assemble_review_sections(review_id, *, section_rows=None, field_rows=None):
    """Build the nested sections[].fields[].(yesFields|noFields) tree in memory.

    Exactly two queries regardless of how many sections/fields/children a
    Review has -- callers with 10 sections and 100 fields never issue more
    than get_review_section_rows + get_review_field_rows. The tree can be
    arbitrarily deep (a case block nested inside a case block, and so on);
    every level is resolved from the same two in-memory lists.
    """
    section_rows = get_review_section_rows(review_id) if section_rows is None else section_rows
    field_rows = get_review_field_rows(review_id) if field_rows is None else field_rows

    fields_by_section = defaultdict(list)
    for row in field_rows:
        fields_by_section[row.get('section_id')].append(row)

    sections = []
    for section in section_rows:
        section_id = section.get('id')
        section_rows_list = fields_by_section.get(section_id, [])

        def children_of(parent_id, condition, _rows=section_rows_list):
            return [
                row for row in _rows
                if row.get('parent_field_id') == parent_id and row.get('condition_value') == condition
            ]

        top_fields = [row for row in section_rows_list if not row.get('parent_field_id')]
        field_payloads = [review_field_payload(field_row, children_of) for field_row in top_fields]
        sections.append({
            'id': section.get('id'),
            'reviewId': section.get('review_id'),
            'title': section.get('title') or '',
            'estimatedMinutes': curriculum_views.parse_int(section.get('estimated_minutes'), 0),
            'displayOrder': curriculum_views.parse_int(section.get('display_order'), 0),
            'enabled': bool(section.get('enabled')),
            'fields': field_payloads,
        })
    return sections


def flatten_fields(sections):
    """Every top-level field across every section, in section then field order.

    A convenience view for callers of the first implementation, which only
    ever knew a single flat field list. Conditional children are deliberately
    excluded here -- they only exist nested under their parent.
    """
    flattened = []
    for section in sections:
        for field in section['fields']:
            flattened.append({k: v for k, v in field.items() if k not in ('yesFields', 'noFields')})
    return flattened


def review_template_summary_payload(row, *, type_index=None):
    """``type_index`` is review_types.review_type_index()'s {id: row} map --
    pass it when serialising a list so the type catalogue is read once for the
    whole response instead of once per Review."""
    type_row = _resolve_review_type(row.get('review_type_id'), type_index)
    return {
        'id': row.get('id'),
        'programmeId': row.get('programme_id') or '',
        'name': row.get('name') or '',
        'enabled': bool(row.get('enabled')),
        'recurrence': {
            'interval': curriculum_views.parse_int(row.get('recurrence_interval'), 1),
            'unit': row.get('recurrence_unit') or 'weeks',
        },
        # The date the first occurrence is calculated from -- see
        # review_schedule.py, which is the only other reader of this column.
        'scheduleAnchorDate': curriculum_views.format_date(row.get('schedule_anchor_date')),
        # None/unset means unlimited -- the review keeps recurring indefinitely.
        'occurrenceCount': row.get('occurrence_count'),
        # The Review's classification. ``reviewTypeId`` is the stable
        # identity everything downstream keys on; the code/name are echoed
        # for display and for Coach's calendar routing. Nothing anywhere
        # classifies a Review by ``name`` -- "Monthly Learner Catch-up" of
        # type Monthly Coaching Meeting still routes as MCM.
        'reviewTypeId': row.get('review_type_id') or '',
        'reviewTypeCode': (type_row or {}).get('code') or '',
        'reviewTypeName': (type_row or {}).get('name') or '',
        'applicableStatuses': curriculum_views.as_json_value(row.get('applicable_statuses'), []),
        'applicability': curriculum_views.as_json_value(row.get('applicability'), {'scope': 'programme', 'ids': []}),
        'fieldCount': curriculum_views.parse_int(row.get('field_count'), 0),
        'createdAt': row.get('created_at'),
        'updatedAt': row.get('updated_at'),
    }


def review_template_detail_payload(row, sections=None, *, type_index=None):
    sections = assemble_review_sections(row.get('id')) if sections is None else sections
    return {
        **review_template_summary_payload(row, type_index=type_index),
        'signatures': {
            'advisor': bool(row.get('signature_advisor')),
            'employer': bool(row.get('signature_employer')),
            'participant': bool(row.get('signature_participant')),
            'referrer': bool(row.get('signature_referrer')),
        },
        'visibleTo': {
            'advisor': bool(row.get('visible_advisor')),
            'employer': bool(row.get('visible_employer')),
            'participant': bool(row.get('visible_participant')),
            'referrer': bool(row.get('visible_referrer')),
        },
        'recordTimeSpent': bool(row.get('record_time_spent')),
        'expectedOtjh': float(row.get('expected_otjh') or 0),
        # Whether completing an occurrence of this review adds expectedOtjh
        # hours to the learner's total OTJH.
        'countsTowardsOtjh': bool(row.get('counts_towards_otjh')),
        'allowEditingPriorDays': curriculum_views.parse_int(row.get('allow_editing_prior_days'), 0),
        'notifications': {
            'employer': bool(row.get('notify_employer')),
            'participant': bool(row.get('notify_participant')),
        },
        'incompleteMarker': row.get('incomplete_marker') or '',
        'createdBy': row.get('created_by') or '',
        'updatedBy': row.get('updated_by') or '',
        'sections': sections,
        # Flattened convenience view -- see module docstring.
        'fields': flatten_fields(sections),
    }


def review_detail_response(review_id, **extra):
    row = get_review_template_row(review_id)
    if not row:
        return curriculum_views.json_error('Review not found.', status=404)
    payload = {'schema': curriculum_views.CURRICULUM_SCHEMA, 'review': review_template_detail_payload(row)}
    payload.update(extra)
    return JsonResponse(payload)


# -------------------------------------------------------------- validation

def _programme_exists(programme_id):
    return bool(curriculum_views.programme_config_by_identifier(programme_id) or curriculum_views.programme_response(programme_id))


def validate_recurrence(payload, errors, *, required=True):
    interval_raw = payload.get('recurrence', {}) if isinstance(payload.get('recurrence'), dict) else {}
    interval = interval_raw.get('interval', payload.get('recurrenceInterval'))
    unit = curriculum_views.clean_str(interval_raw.get('unit', payload.get('recurrenceUnit'))).lower()

    if interval is None and not required:
        return None, None
    try:
        interval = int(interval)
    except (TypeError, ValueError):
        errors['recurrenceInterval'] = 'Repeat interval must be a whole number.'
        return None, None
    if interval <= 0:
        errors['recurrenceInterval'] = 'Repeat interval must be a positive number.'
    if not unit:
        unit = 'weeks'
    if unit not in RECURRENCE_UNITS:
        errors['recurrenceUnit'] = f'Repeat unit must be one of: {", ".join(RECURRENCE_UNITS)}.'
    return interval, unit


def validate_schedule_anchor_date(payload, errors, *, required=True):
    """The date recurrence is calculated from -- see review_schedule.py.

    A canonical ISO date string ('2027-01-15') is required whenever the field
    is present at all; an unparseable value is an error rather than silently
    falling back, since a wrong anchor silently produces a wrong occurrence
    schedule with no error ever surfacing to the user.
    """
    raw = payload.get('scheduleAnchorDate')
    if raw is None:
        if required:
            return datetime.utcnow().date()
        return None
    parsed = curriculum_views.parse_date(raw)
    if not parsed:
        errors['scheduleAnchorDate'] = 'Schedule anchor date must be a valid date.'
        return None
    return parsed


def _resolve_review_type(review_type_id, type_index=None):
    review_type_id = curriculum_views.clean_str(review_type_id)
    if not review_type_id:
        return None
    if type_index is not None:
        return type_index.get(review_type_id)
    return review_types.get_review_type(review_type_id)


def validate_review_type_id(payload, errors):
    """The Review's classification -- required, and always a review_types.id.

    Never a name: the type is what Coach routes on, so it has to survive the
    Review being renamed. An archived (inactive) type is still accepted on an
    existing Review so that deactivating a type never blocks editing the
    Reviews already classified with it.
    """
    raw = curriculum_views.clean_str(payload.get('reviewTypeId'))
    if not raw:
        errors['reviewTypeId'] = 'Review type is required.'
        return None
    if not review_types.get_review_type(raw):
        errors['reviewTypeId'] = 'Choose a valid review type.'
        return None
    return raw


def validate_occurrence_count(payload, errors):
    """How many times a Review recurs before it stops -- optional; blank/None
    means unlimited (the field's existing, still-default behaviour).
    """
    raw = payload.get('occurrenceCount')
    if raw is None or raw == '':
        return None
    try:
        count = int(raw)
    except (TypeError, ValueError):
        errors['occurrenceCount'] = 'Number of occurrences must be a whole number.'
        return None
    if count <= 0:
        errors['occurrenceCount'] = 'Number of occurrences must be a positive number.'
        return None
    return count


def validate_expected_otjh(payload, errors):
    """Expected OTJH hours for one occurrence of this review -- see
    ``validate_review_payload``'s ``countsTowardsOtjh`` handling for whether
    completing it actually adds that many hours to the learner's total (the
    module docstring's future ``learner_review_instances`` is where that flag
    gets acted on)."""
    try:
        expected_otjh = float(payload.get('expectedOtjh', 0) or 0)
    except (TypeError, ValueError):
        errors['expectedOtjh'] = 'Expected OTJH must be a number.'
        return 0
    if expected_otjh < 0:
        errors['expectedOtjh'] = 'Expected OTJH must be zero or a positive number.'
        return 0
    return expected_otjh


def validate_statuses(payload, errors):
    statuses = payload.get('applicableStatuses')
    if statuses is None:
        return []
    if not isinstance(statuses, list):
        errors['applicableStatuses'] = 'applicableStatuses must be a list.'
        return []
    canonical = set(curriculum_views.PROGRAMME_STATUS_CHOICES)
    cleaned = []
    invalid = []
    for value in statuses:
        text = curriculum_views.clean_str(value)
        if not text:
            continue
        if text not in canonical:
            invalid.append(text)
            continue
        if text not in cleaned:
            cleaned.append(text)
    if invalid:
        errors['applicableStatuses'] = f'Unknown programme status: {", ".join(invalid)}.'
    return cleaned


def _validate_field_configuration(field_type, configuration, path, errors):
    """Returns the cleaned configuration for this field type."""
    if field_type == 'list_item':
        options = configuration.get('options')
        cleaned_options = [curriculum_views.clean_str(o) for o in options if curriculum_views.clean_str(o)] if isinstance(options, list) else []
        if not cleaned_options:
            errors[f'{path}.configuration.options'] = 'List item fields need at least one option.'
        # De-duplicate while preserving order -- accidental duplicate options
        # (e.g. pasted twice) are silently folded rather than rejected.
        seen = set()
        deduped = []
        for option in cleaned_options:
            if option in seen:
                continue
            seen.add(option)
            deduped.append(option)
        return {**configuration, 'options': deduped}

    if field_type == 'title_description':
        return {**configuration, 'description': curriculum_views.clean_str(configuration.get('description'))}

    return configuration if isinstance(configuration, dict) else {}


# Case blocks may nest inside case blocks to any depth, but a payload is
# still attacker/mistake-controlled input -- cap recursion so a malformed or
# malicious deeply-nested body can't blow the stack or produce a runaway tree.
MAX_FIELD_NESTING_DEPTH = 8


def validate_field_payload(field, path, errors, *, depth=0):
    """Validate one Field (top-level or a conditional child, at any depth).
    Returns cleaned dict.

    ``path`` is the dotted/bracketed location used in error keys, e.g.
    ``sections[0].fields[1]`` or ``sections[0].fields[1].yesFields[0].yesFields[2]``.
    """
    if not isinstance(field, dict):
        field = {}

    field_type = curriculum_views.clean_str(field.get('fieldType')).lower() or 'text'
    if field_type not in FIELD_TYPES:
        errors[f'{path}.fieldType'] = f'Unknown field type "{field_type}".'
        field_type = 'text'

    title = curriculum_views.clean_str(field.get('title'))
    if not title:
        errors[f'{path}.title'] = "Title can't be empty."

    required = bool(field.get('required')) if field_type not in DISPLAY_ONLY_FIELD_TYPES else False
    configuration = field.get('configuration') if isinstance(field.get('configuration'), dict) else {}
    configuration = _validate_field_configuration(field_type, configuration, path, errors)

    # Who besides the advisor (coach) may answer this field, e.g. so a
    # Learner or Employer can complete their own part of the meeting record.
    # A display-only block (title_description/action_button) never collects
    # an answer, so it never carries a respondent list.
    if field_type in DISPLAY_ONLY_FIELD_TYPES:
        configuration = {k: v for k, v in configuration.items() if k != 'respondentRoles'}
    else:
        posted_roles = configuration.get('respondentRoles')
        respondent_roles = sorted({
            role for role in (posted_roles if isinstance(posted_roles, list) else [])
            if role in FIELD_RESPONDENT_ROLES
        })
        configuration = {**configuration, 'respondentRoles': respondent_roles}

    cleaned = {
        'id': curriculum_views.clean_str(field.get('id')),
        'title': title,
        'field_type': field_type,
        'required': required,
        'configuration': configuration,
        'yes_fields': [],
        'no_fields': [],
    }

    yes_fields_payload = field.get('yesFields')
    no_fields_payload = field.get('noFields')
    has_conditional_payload = bool(yes_fields_payload) or bool(no_fields_payload)

    if field_type != CONDITIONAL_FIELD_TYPE:
        if has_conditional_payload:
            errors[f'{path}.yesFields'] = 'Only "Boolean with case block" fields may have conditional fields.'
        return cleaned

    if depth >= MAX_FIELD_NESTING_DEPTH:
        errors[f'{path}.fieldType'] = f'"Boolean with case block" fields cannot nest more than {MAX_FIELD_NESTING_DEPTH} levels deep.'
        return cleaned

    for branch_key, branch_payload in (('yes_fields', yes_fields_payload), ('no_fields', no_fields_payload)):
        branch_name = 'yesFields' if branch_key == 'yes_fields' else 'noFields'
        if branch_payload is None:
            continue
        if not isinstance(branch_payload, list):
            errors[f'{path}.{branch_name}'] = f'{branch_name} must be a list.'
            continue
        cleaned[branch_key] = [
            validate_field_payload(child, f'{path}.{branch_name}[{index}]', errors, depth=depth + 1)
            for index, child in enumerate(branch_payload)
        ]

    return cleaned


def validate_section_payload(section, index, errors):
    if not isinstance(section, dict):
        section = {}

    path = f'sections[{index}]'
    title = curriculum_views.clean_str(section.get('title'))
    if not title:
        errors[f'{path}.title'] = "Section title can't be empty."

    try:
        estimated_minutes = int(section.get('estimatedMinutes', 0) or 0)
    except (TypeError, ValueError):
        estimated_minutes = -1
    if estimated_minutes < 0:
        errors[f'{path}.estimatedMinutes'] = 'Estimated time must be zero or a positive number of minutes.'
        estimated_minutes = 0

    fields_payload = section.get('fields')
    cleaned_fields = []
    if fields_payload is not None:
        if not isinstance(fields_payload, list):
            errors[f'{path}.fields'] = 'fields must be a list.'
        else:
            cleaned_fields = [
                validate_field_payload(field, f'{path}.fields[{i}]', errors)
                for i, field in enumerate(fields_payload)
            ]

    return {
        'id': curriculum_views.clean_str(section.get('id')),
        'title': title,
        'estimated_minutes': estimated_minutes,
        'enabled': bool(section.get('enabled', True)),
        'fields': cleaned_fields,
    }


def _sections_payload_from(payload):
    """Accepts either the new ``sections`` shape or the legacy flat ``fields`` shape."""
    if payload.get('sections') is not None:
        return payload.get('sections')
    if payload.get('fields') is not None:
        return [{'title': 'General', 'estimatedMinutes': 0, 'enabled': True, 'fields': payload.get('fields')}]
    return None


def validate_applicability(value, programme_id, errors):
    if not isinstance(value, dict) or value.get('scope') not in ('programme', 'cohort', 'group'):
        errors['applicability'] = 'Choose programme, cohort or group applicability.'
        return None
    scope, identifiers = value['scope'], value.get('ids', [])
    if not isinstance(identifiers, list) or any(not isinstance(item, str) or not item.strip() for item in identifiers):
        errors['applicability'] = 'Select valid cohort or group identifiers.'
        return None
    identifiers = list(dict.fromkeys(item.strip() for item in identifiers))
    if scope == 'programme':
        if identifiers:
            errors['applicability'] = 'Programme applicability cannot include cohort or group identifiers.'
        return {'scope': scope, 'ids': []}
    if not identifiers:
        errors['applicability'] = 'Select at least one cohort or group.'
        return None
    table = curriculum_views.COHORT_AUTHORING_DETAILS_TABLE if scope == 'cohort' else curriculum_views.GROUPS_TABLE
    rows = curriculum_views.authoring_fetch_all(table, 'programme_id = %s', [programme_id])
    allowed = {curriculum_views.clean_str(row.get(f'{scope}_id')) for row in rows}
    if not set(identifiers).issubset(allowed):
        errors['applicability'] = 'Selected cohorts or groups must belong to this programme.'
    return {'scope': scope, 'ids': identifiers}


def validate_review_payload(
    payload, *, partial=False, programme_id=None, current_review_type_id=None,
    current_signature_referrer=False,
):
    """Returns (cleaned, errors). ``partial`` allows PATCH to omit unset fields.

    ``current_signature_referrer`` is the stored template's value on update, so
    a legacy template already requiring a Referrer signature can still be saved
    unchanged while no new Referrer requirement can be introduced."""
    errors = {}
    cleaned = {}

    if not partial or 'name' in payload:
        name = curriculum_views.clean_str(payload.get('name'))
        if not name:
            errors['name'] = 'Review name is required.'
        cleaned['name'] = name

    if not partial or 'enabled' in payload:
        cleaned['enabled'] = bool(payload.get('enabled', True))

    if not partial or 'recurrence' in payload or 'recurrenceInterval' in payload or 'recurrenceUnit' in payload:
        interval, unit = validate_recurrence(payload, errors, required=not partial)
        if interval is not None:
            cleaned['recurrence_interval'] = interval
            cleaned['recurrence_unit'] = unit

    if not partial or 'scheduleAnchorDate' in payload:
        anchor = validate_schedule_anchor_date(payload, errors, required=not partial)
        if anchor is not None:
            cleaned['schedule_anchor_date'] = anchor

    if not partial or 'reviewTypeId' in payload:
        review_type_id = validate_review_type_id(payload, errors)
        if review_type_id is not None:
            cleaned['review_type_id'] = review_type_id

    if not partial or 'occurrenceCount' in payload:
        cleaned['occurrence_count'] = validate_occurrence_count(payload, errors)

    if not partial or 'applicableStatuses' in payload:
        cleaned['applicable_statuses'] = validate_statuses(payload, errors)

    if not partial or 'applicability' in payload:
        cleaned['applicability'] = validate_applicability(
            payload.get('applicability', {'scope': 'programme', 'ids': []}), programme_id, errors,
        )

    signatures = payload.get('signatures') if isinstance(payload.get('signatures'), dict) else {}
    if not partial or 'signatures' in payload:
        for role in PARTICIPANT_ROLES:
            cleaned[f'signature_{role}'] = bool(signatures.get(role))
        # Referrer stays a structural role, but nobody can sign as one yet, so a
        # new Referrer requirement would leave its Reviews stuck awaiting signature.
        if cleaned['signature_referrer'] and not current_signature_referrer:
            errors['signatures'] = 'Referrer signatures are not currently supported.'

    visible_to = payload.get('visibleTo') if isinstance(payload.get('visibleTo'), dict) else {}
    if not partial or 'visibleTo' in payload:
        for role in PARTICIPANT_ROLES:
            cleaned[f'visible_{role}'] = bool(visible_to.get(role, True))

    if not partial or 'recordTimeSpent' in payload:
        cleaned['record_time_spent'] = bool(payload.get('recordTimeSpent'))

    if not partial or 'expectedOtjh' in payload:
        cleaned['expected_otjh'] = validate_expected_otjh(payload, errors)

    if not partial or 'countsTowardsOtjh' in payload:
        cleaned['counts_towards_otjh'] = bool(payload.get('countsTowardsOtjh'))

    if not partial or 'allowEditingPriorDays' in payload:
        try:
            prior_days = int(payload.get('allowEditingPriorDays', 0) or 0)
        except (TypeError, ValueError):
            prior_days = -1
        if prior_days < 0:
            errors['allowEditingPriorDays'] = 'Allow editing prior days must be zero or a positive number.'
            prior_days = 0
        cleaned['allow_editing_prior_days'] = prior_days

    notifications = payload.get('notifications') if isinstance(payload.get('notifications'), dict) else {}
    if not partial or 'notifications' in payload:
        cleaned['notify_employer'] = bool(notifications.get('employer'))
        cleaned['notify_participant'] = bool(notifications.get('participant'))

    if not partial or 'incompleteMarker' in payload:
        cleaned['incomplete_marker'] = curriculum_views.clean_str(payload.get('incompleteMarker'))

    sections_payload = _sections_payload_from(payload)
    cleaned_sections = None
    if sections_payload is not None:
        if not isinstance(sections_payload, list):
            errors['sections'] = 'sections must be a list.'
        else:
            cleaned_sections = [validate_section_payload(section, index, errors) for index, section in enumerate(sections_payload)]
            semantic_fields = []

            def _collect_semantic_fields(fields):
                for field in fields or []:
                    if (field.get('configuration') or {}).get('semanticKey') == MEETING_SUMMARY_SEMANTIC_KEY:
                        semantic_fields.append(field)
                    _collect_semantic_fields(field.get('yes_fields'))
                    _collect_semantic_fields(field.get('no_fields'))

            for section in cleaned_sections:
                _collect_semantic_fields(section.get('fields'))
            if semantic_fields:
                review_type_id = (
                    cleaned.get('review_type_id')
                    or curriculum_views.clean_str(payload.get('reviewTypeId'))
                    or curriculum_views.clean_str(current_review_type_id)
                )
                type_row = review_types.get_review_type(review_type_id) if review_type_id else None
                if (type_row or {}).get('code') not in {
                    review_types.REVIEW_TYPE_CODE_MCM,
                    review_types.REVIEW_TYPE_CODE_PROGRESS_REVIEW,
                }:
                    errors['sections'] = 'Only a Monthly Coaching Meeting or Progress Review may define a Meeting Summary field.'
                elif len(semantic_fields) > 1:
                    errors['sections'] = 'A Review can define only one Meeting Summary field.'
                elif semantic_fields[0].get('field_type') not in ('text', 'text_multiline'):
                    errors['sections'] = 'The Meeting Summary field must be a text field.'
    cleaned['_sections'] = cleaned_sections

    return cleaned, errors


# ---------------------------------------------------------------- mutation

def _insert_field_row(review_id, section_id, field, index, *, parent_field_id=None, condition_value=None):
    field_id = field.get('id') or curriculum_views.unique_prefixed_id('REVF')
    payload = {
        'id': field_id,
        'review_id': review_id,
        'section_id': section_id,
        'title': field['title'],
        'field_type': field['field_type'],
        'required': field['required'],
        'display_order': index,
        'configuration': curriculum_views.json_db_value(field['configuration']),
        'created_at': datetime.utcnow(),
        'updated_at': datetime.utcnow(),
    }
    if parent_field_id:
        payload['parent_field_id'] = parent_field_id
        payload['condition_value'] = condition_value
    curriculum_views.insert_row(REVIEW_FIELDS_TABLE, payload)
    return field_id


def save_section_fields(review_id, section_id, fields, *, parent_field_id=None, condition_value=None):
    """Insert one level of fields and recurse into their yes/no children --
    a case block's children may themselves be case blocks, to any depth."""
    count = 0
    for index, field in enumerate(fields or []):
        field_id = _insert_field_row(review_id, section_id, field, index, parent_field_id=parent_field_id, condition_value=condition_value)
        count += 1
        if field['field_type'] == CONDITIONAL_FIELD_TYPE:
            count += save_section_fields(review_id, section_id, field.get('yes_fields'), parent_field_id=field_id, condition_value='yes')
            count += save_section_fields(review_id, section_id, field.get('no_fields'), parent_field_id=field_id, condition_value='no')
    return count


def save_review_sections(review_id, sections):
    """Delete-then-reinsert the whole section/field tree, same replace approach
    the first implementation used for its flat field list."""
    curriculum_views.delete_rows(REVIEW_FIELDS_TABLE, 'review_id = %s', [review_id])
    curriculum_views.delete_rows(REVIEW_SECTIONS_TABLE, 'review_id = %s', [review_id])
    total_fields = 0
    for index, section in enumerate(sections or []):
        section_id = section.get('id') or curriculum_views.unique_prefixed_id('REVS')
        curriculum_views.insert_row(REVIEW_SECTIONS_TABLE, {
            'id': section_id,
            'review_id': review_id,
            'title': section['title'],
            'estimated_minutes': section['estimated_minutes'],
            'display_order': index,
            'enabled': section['enabled'],
            'created_at': datetime.utcnow(),
            'updated_at': datetime.utcnow(),
        })
        total_fields += save_section_fields(review_id, section_id, section['fields'])
    return total_fields


def existing_review_ids():
    return [row.get('id') for row in get_review_template_rows(include_deleted=True)]


def create_review(programme_id, payload, *, actor='system'):
    cleaned, errors = validate_review_payload(payload, partial=False, programme_id=programme_id)
    if errors:
        return None, errors

    review_id = curriculum_views.unique_prefixed_id('REV', payload.get('id'), existing_review_ids)
    sections = cleaned.pop('_sections') or []

    with transaction.atomic():
        curriculum_views.insert_row(REVIEW_TEMPLATES_TABLE, {
            'id': review_id,
            'programme_id': programme_id,
            **{k: v for k, v in cleaned.items()},
            'applicable_statuses': curriculum_views.json_db_value(cleaned['applicable_statuses']),
            'applicability': curriculum_views.json_db_value(cleaned['applicability']),
            'field_count': 0,
            'created_by': actor,
            'updated_by': actor,
            'created_at': datetime.utcnow(),
            'updated_at': datetime.utcnow(),
        })
        field_count = save_review_sections(review_id, sections)
        curriculum_views.update_rows(REVIEW_TEMPLATES_TABLE, 'id = %s', [review_id], {
            'field_count': field_count, 'updated_at': datetime.utcnow(),
        })

    curriculum_views.invalidate_curriculum_cache()
    return review_id, None


def update_review(review_id, payload, *, actor='system'):
    row = get_review_template_row(review_id)
    if not row:
        return None, {'_': 'Review not found.'}

    cleaned, errors = validate_review_payload(
        payload,
        partial=True,
        programme_id=row['programme_id'],
        current_review_type_id=row.get('review_type_id'),
        current_signature_referrer=bool(row.get('signature_referrer')),
    )
    if errors:
        return None, errors

    sections = cleaned.pop('_sections')
    updates = dict(cleaned)
    if 'applicable_statuses' in updates:
        updates['applicable_statuses'] = curriculum_views.json_db_value(updates['applicable_statuses'])
    if 'applicability' in updates:
        updates['applicability'] = curriculum_views.json_db_value(updates['applicability'])
    updates['updated_by'] = actor
    updates['updated_at'] = datetime.utcnow()

    with transaction.atomic():
        if updates:
            curriculum_views.update_rows(REVIEW_TEMPLATES_TABLE, 'id = %s', [review_id], updates)
        if sections is not None:
            field_count = save_review_sections(review_id, sections)
            curriculum_views.update_rows(REVIEW_TEMPLATES_TABLE, 'id = %s', [review_id], {
                'field_count': field_count, 'updated_at': datetime.utcnow(),
            })

    curriculum_views.invalidate_curriculum_cache()
    return review_id, None


def archive_review(review_id, *, actor='review-delete'):
    row = get_review_template_row(review_id)
    if not row:
        return False
    curriculum_views.soft_delete_rows(
        REVIEW_TEMPLATES_TABLE, 'id = %s', [review_id], deleted_by=actor,
    )
    # Sections follow the Review: no orphan active section for an archived
    # Review, even though its fields (and the sections themselves) are left
    # in the database rather than hard-deleted, matching how fields already
    # survive a Review archive.
    curriculum_views.soft_delete_rows(
        REVIEW_SECTIONS_TABLE, 'review_id = %s', [review_id], deleted_by=actor, via_parent=review_id,
    )
    curriculum_views.invalidate_curriculum_cache()
    return True


def clone_review(source_row, destination_programme_id, *, actor='review-clone'):
    """Deep-copy one Review -- every Section, Field and conditional child --
    into another Programme, with fresh ids throughout. Parent ids on cloned
    conditional children are remapped to the *new* parent Field id; the
    source Review is never touched.

    Deliberately does not touch review_schedule.py's tables: a clone starts
    with no occurrence overrides and no clash-resolution acknowledgements --
    those are schedule *history* for the source Programme, not part of the
    template definition."""
    new_id = curriculum_views.unique_prefixed_id('REV', '', existing_review_ids)
    copy_columns = (
        'name', 'enabled', 'recurrence_interval', 'recurrence_unit', 'schedule_anchor_date', 'occurrence_count', 'applicable_statuses',
        'review_type_id',
        'signature_advisor', 'signature_employer', 'signature_participant', 'signature_referrer',
        'visible_advisor', 'visible_employer', 'visible_participant', 'visible_referrer',
        'record_time_spent', 'expected_otjh', 'counts_towards_otjh', 'allow_editing_prior_days', 'notify_employer', 'notify_participant',
        'incomplete_marker', 'field_count',
    )
    insert_payload = {column: source_row.get(column) for column in copy_columns}
    applicability = curriculum_views.as_json_value(source_row.get('applicability'), {'scope': 'programme', 'ids': []})
    if source_row.get('programme_id') == destination_programme_id:
        insert_payload['applicability'] = curriculum_views.json_db_value(applicability)
    elif applicability.get('scope') != 'programme':
        # The destination has different placement IDs. Require an explicit
        # applicability choice before a restricted clone starts generating.
        insert_payload['enabled'] = False
        insert_payload['applicability'] = curriculum_views.json_db_value({'scope': 'programme', 'ids': []})
    curriculum_views.insert_row(REVIEW_TEMPLATES_TABLE, {
        'id': new_id,
        'programme_id': destination_programme_id,
        **insert_payload,
        'created_by': actor,
        'updated_by': actor,
        'created_at': datetime.utcnow(),
        'updated_at': datetime.utcnow(),
    })

    for section in get_review_section_rows(source_row.get('id')):
        new_section_id = curriculum_views.unique_prefixed_id('REVS')
        curriculum_views.insert_row(REVIEW_SECTIONS_TABLE, {
            'id': new_section_id,
            'review_id': new_id,
            'title': section.get('title'),
            'estimated_minutes': section.get('estimated_minutes'),
            'display_order': section.get('display_order'),
            'enabled': section.get('enabled'),
            'created_by': actor,
            'updated_by': actor,
            'created_at': datetime.utcnow(),
            'updated_at': datetime.utcnow(),
        })

        section_fields = [f for f in get_review_field_rows(source_row.get('id')) if f.get('section_id') == section.get('id')]
        # Grouped by parent so a case block nested inside a case block (to any
        # depth) clones parent-before-child at every level, not just two.
        children_by_parent = defaultdict(list)
        for field_row in section_fields:
            children_by_parent[field_row.get('parent_field_id') or None].append(field_row)

        def _clone_field_row(field_row, parent_field_id=None):
            new_field_id = curriculum_views.unique_prefixed_id('REVF')
            payload = {
                'id': new_field_id,
                'review_id': new_id,
                'section_id': new_section_id,
                'title': field_row.get('title'),
                'field_type': field_row.get('field_type'),
                'required': field_row.get('required'),
                'display_order': field_row.get('display_order'),
                'configuration': curriculum_views.json_db_value(curriculum_views.as_json_value(field_row.get('configuration'), {})),
                'created_at': datetime.utcnow(),
                'updated_at': datetime.utcnow(),
            }
            if parent_field_id:
                payload['parent_field_id'] = parent_field_id
                payload['condition_value'] = field_row.get('condition_value')
            curriculum_views.insert_row(REVIEW_FIELDS_TABLE, payload)
            return new_field_id

        def _clone_subtree(field_row, parent_field_id=None):
            new_field_id = _clone_field_row(field_row, parent_field_id=parent_field_id)
            for child_row in children_by_parent.get(field_row.get('id'), []):
                _clone_subtree(child_row, parent_field_id=new_field_id)

        for field_row in children_by_parent.get(None, []):
            _clone_subtree(field_row)

    return new_id


# --------------------------------------------------------------------- views

def _actor(request):
    # No per-request account is threaded into curriculum_api writes anywhere
    # else in this module (see week_template deletes, which hardcode a reason
    # string); Reviews follows the same convention rather than inventing one.
    return 'staff'


@csrf_exempt
def curriculum_programme_review_collection(request, programme_id):
    ensure_review_tables()
    programme_id = curriculum_views.clean_str(programme_id)

    if request.method == 'GET':
        if not _programme_exists(programme_id):
            return curriculum_views.json_error('Programme not found.', status=404)
        rows = get_review_template_rows('programme_id = %s', [programme_id])
        type_index = review_types.review_type_index()
        return curriculum_views.curriculum_results_response(
            [review_template_summary_payload(row, type_index=type_index) for row in rows]
        )

    if request.method != 'POST':
        return curriculum_views.json_error('Method not allowed.', status=405)

    if not _programme_exists(programme_id):
        return curriculum_views.json_error('Programme not found.', status=404)

    payload = curriculum_views.json_body(request)
    if payload is None:
        return curriculum_views.json_error('Invalid JSON body.')

    review_id, errors = create_review(programme_id, payload, actor=_actor(request))
    if errors:
        return curriculum_views.json_error('Please fix the highlighted fields.', fields=errors)

    curriculum_views.log_curriculum_decision('review.create', outcome='created', entity_id=review_id)
    return review_detail_response(review_id, created=True)


@csrf_exempt
def curriculum_review_detail(request, review_id):
    ensure_review_tables()
    review_id = curriculum_views.clean_str(review_id)
    row = get_review_template_row(review_id)
    if not row:
        return curriculum_views.json_error('Review not found.', status=404)

    if request.method == 'GET':
        return review_detail_response(review_id)

    if request.method == 'DELETE':
        archive_review(review_id, actor=_actor(request))
        curriculum_views.log_curriculum_decision('review.archive', outcome='archived', entity_id=review_id)
        return JsonResponse({'deleted': True, 'permanent': False, 'archived': True, 'id': review_id})

    if request.method not in {'PATCH', 'PUT'}:
        return curriculum_views.json_error('Method not allowed.', status=405)

    payload = curriculum_views.json_body(request)
    if payload is None:
        return curriculum_views.json_error('Invalid JSON body.')

    _, errors = update_review(review_id, payload, actor=_actor(request))
    if errors:
        status = 404 if errors.get('_') == 'Review not found.' else 400
        return curriculum_views.json_error(errors.get('_') or 'Please fix the highlighted fields.', status=status, fields=errors)

    curriculum_views.log_curriculum_decision('review.update', outcome='updated', entity_id=review_id)
    return review_detail_response(review_id, updated=True)


@csrf_exempt
def curriculum_review_clone(request, programme_id):
    """Clone one or more Reviews from another Programme into this one.

    POST body: {"sourceProgrammeId": "...", "reviewIds": ["REV-...", ...]}
    """
    ensure_review_tables()
    if request.method != 'POST':
        return curriculum_views.json_error('Method not allowed.', status=405)

    destination_programme_id = curriculum_views.clean_str(programme_id)
    if not _programme_exists(destination_programme_id):
        return curriculum_views.json_error('Programme not found.', status=404)

    payload = curriculum_views.json_body(request)
    if payload is None:
        return curriculum_views.json_error('Invalid JSON body.')

    source_programme_id = curriculum_views.clean_str(payload.get('sourceProgrammeId'))
    if not source_programme_id:
        return curriculum_views.json_error('Choose a source Programme.', fields=['sourceProgrammeId'])
    if not _programme_exists(source_programme_id):
        return curriculum_views.json_error('Source Programme not found.', status=404, fields=['sourceProgrammeId'])

    review_ids = payload.get('reviewIds')
    if not isinstance(review_ids, list) or not [r for r in review_ids if curriculum_views.clean_str(r)]:
        return curriculum_views.json_error('Please choose one or more types.', fields=['reviewIds'])
    review_ids = [curriculum_views.clean_str(r) for r in review_ids if curriculum_views.clean_str(r)]

    source_rows = get_review_template_rows(
        'programme_id = %s and id = any(%s)' if connection.vendor == 'postgresql' else 'programme_id = %s',
        [source_programme_id, review_ids] if connection.vendor == 'postgresql' else [source_programme_id],
    )
    if connection.vendor != 'postgresql':
        source_rows = [row for row in source_rows if row.get('id') in review_ids]
    found_ids = {row.get('id') for row in source_rows}
    missing = [r for r in review_ids if r not in found_ids]
    if missing:
        return curriculum_views.json_error(
            f'Some selected reviews were not found in the source Programme: {", ".join(missing)}.',
            status=404, fields=['reviewIds'],
        )

    actor = _actor(request)
    new_ids = []
    with transaction.atomic():
        for row in source_rows:
            new_ids.append(clone_review(row, destination_programme_id, actor=actor))

    curriculum_views.invalidate_curriculum_cache()
    curriculum_views.log_curriculum_decision(
        'review.clone', outcome='cloned', entity_id=destination_programme_id,
        reason=f'from={source_programme_id} count={len(new_ids)}',
    )

    rows = get_review_template_rows('programme_id = %s', [destination_programme_id])
    type_index = review_types.review_type_index()
    return JsonResponse({
        'cloned': True,
        'sourceProgrammeId': source_programme_id,
        'programmeId': destination_programme_id,
        'reviewIds': new_ids,
        'reviews': [review_template_summary_payload(row, type_index=type_index) for row in rows],
    })
