"""The Review Engine: turns a Curriculum Review Template into one learner's
occurrences, and the durable per-learner Review Instance/answers/signatures.

Ownership, per the architecture this module implements:

    Curriculum (reviews.py)   owns WHAT a Review is -- recurrence, eligibility,
                              participants, visibility, signatures, sections,
                              questions.
    This module              owns WHEN it happens for one learner, and the
                              learner's own record of having done it.
    Coach (coach_api)         owns execution -- scheduling, calendar, Teams,
                              opening the Review, and is the only caller of
                              this module's public functions. It never
                              hard-codes a Review's identity or behaviour by
                              name; every Coach caller passes a
                              ``review_template_id``/Review Type code and
                              reads the display title from the template's own
                              ``name`` at read time.

Two different lifetimes live here, deliberately kept apart:

  * A RAW occurrence: pure arithmetic (``resolve_learner_occurrences``), one
    row projected in-memory per learner/template/window. Cheap, and never
    written anywhere -- asking for the same window twice must return the same
    answer, and calling it 100 times before a coach ever opens one must not
    create 100 rows.
  * A Review Instance (``curriculum.review_instances``): only created the
    first time Coach actually needs a durable handle for one occurrence --
    scheduling it, opening its form, or recording an answer/signature. Its
    identity is ``(review_template_id, learner_id, occurrence_number)``, so
    calling ``ensure_review_instance`` twice for the same occurrence returns
    the same row rather than duplicating it (section 24 of the brief: expected
    occurrence vs. persisted instance are kept strictly separate).

Historical integrity: ``definition_snapshot`` is immutable once an instance
reaches awaiting-signature/completed (or has a signature). Before that gate,
Curriculum section/field edits remain visible: an untouched instance adopts
the current section tree, while one with saved answers uses an answer-safe
merge. The effective tree is persisted only by a draft-save/completion write,
never by a passive GET. Signature/visibility/lifecycle rules remain frozen
throughout. The display TITLE also stays live, as it did before this policy.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db import transaction
from coach_api.reconciliation_links import native_write

from . import reviews
from . import review_schedule
from . import review_types
from . import schema_gate
from . import views as curriculum_views

logger = logging.getLogger(__name__)

REVIEW_INSTANCES_TABLE = 'review_instances'
REVIEW_INSTANCE_ANSWERS_TABLE = 'review_instance_answers'
REVIEW_INSTANCE_SIGNATURES_TABLE = 'review_instance_signatures'
#: Manual scheduled -> in-progress overrides ONLY (see
#: mark_review_instance_in_progress_manually). Never written for a Teams-
#: attendance transition, a completion, or a signature -- one row per manual
#: override, and nothing else.
REVIEW_INSTANCE_MANUAL_OVERRIDES_TABLE = 'review_instance_manual_overrides'
#: Authorised completed/awaiting-signature -> in-progress REOPENS only (see
#: reopen_review_instance_for_editing). Deliberately a separate table from
#: REVIEW_INSTANCE_MANUAL_OVERRIDES_TABLE above, whose contract is one row per
#: manual scheduled -> in-progress override and nothing else -- see
#: sql/2026-09-21_curriculum_review_instance_reopens.sql for why that contract
#: is kept true rather than widened to cover a second lifecycle event.
REVIEW_INSTANCE_REOPENS_TABLE = 'review_instance_reopens'
#: One row per coach-created additional Review for a single learner (see
#: create_learner_review_addition). Deliberately NOT
#: review_occurrence_overrides -- that table is skip-only (a hard CHECK
#: constraint), with no concept of adding a date; see
#: sql/2026-09-15_curriculum_learner_review_additions.sql for why this is a
#: separate table instead of an extension.
LEARNER_REVIEW_ADDITIONS_TABLE = 'learner_review_additions'
OVERRIDES_TABLE = review_schedule.OCCURRENCE_OVERRIDES_TABLE

STATUS_NOT_SCHEDULED = 'not-scheduled'
STATUS_SCHEDULED = 'scheduled'
STATUS_IN_PROGRESS = 'in-progress'
STATUS_AWAITING_SIGNATURE = 'awaiting-signature'
STATUS_COMPLETED = 'completed'

EDITABLE_DEFINITION_STATUSES = frozenset({
    STATUS_NOT_SCHEDULED,
    STATUS_SCHEDULED,
    STATUS_IN_PROGRESS,
})

#: review_instances.occurrence_source values -- see
#: sql/2026-09-15_curriculum_learner_review_additions.sql for the identity
#: design this backs (occurrence_ref, not occurrence_number, is what lets a
#: generated and a manual occurrence coexist for the same learner+template).
OCCURRENCE_SOURCE_GENERATED = 'generated'
OCCURRENCE_SOURCE_MANUAL = 'manual'

#: Reason codes a coach may give for a learner-specific addition. Free text
#: is also captured (reason) -- these are for reporting/filtering, and the
#: DB enforces the same list via a CHECK constraint.
LEARNER_REVIEW_ADDITION_REASON_CODES = (
    'additional-coaching', 'learner-request', 'employer-request',
    'performance-concern', 'safeguarding-follow-up', 'other',
)

SIGNATURE_ROLES = reviews.PARTICIPANT_ROLES

_TABLES_READY = False


def required_signature_roles(signature_requirements):
    """Roles required by one Review Instance's frozen signature rules.

    ``definition_snapshot.signatures`` stores booleans, while the serialized
    form definition exposes the same frozen decision as
    ``signatures[role].required``. Accepting both shapes keeps lifecycle,
    signature validation and PDF availability on one resolver without ever
    consulting the live Curriculum template.
    """
    requirements = signature_requirements or {}
    return tuple(
        role
        for role in SIGNATURE_ROLES
        if bool(
            requirements.get(role, {}).get('required')
            if isinstance(requirements.get(role), dict)
            else requirements.get(role)
        )
    )


def ensure_review_instance_tables():
    global _TABLES_READY
    if _TABLES_READY:
        return
    if not schema_gate.runtime_bootstrap_allowed():
        schema_gate.require_tables(
            REVIEW_INSTANCES_TABLE, REVIEW_INSTANCE_ANSWERS_TABLE, REVIEW_INSTANCE_SIGNATURES_TABLE,
        )
        _TABLES_READY = True
        return
    provision_review_instance_tables()


#: Set once this process has provisioned/verified the manual-overrides table,
#: separately from _TABLES_READY (the three core tables) -- see
#: ensure_review_instance_manual_overrides_table for why this stays its own
#: gate rather than joining ensure_review_instance_tables().
_MANUAL_OVERRIDES_TABLE_READY = False


def ensure_review_instance_manual_overrides_table():
    """Verify/provision review_instance_manual_overrides on its own, narrower
    gate than ensure_review_instance_tables().

    Deliberately not folded into the three core tables' gate: those three are
    required before ANY review-instance operation (schedule, save an answer,
    complete, sign), so adding this one there would mean a plain Teams-
    attendance transition -- which never touches this table -- starts failing
    in production the moment this code deploys, if the new table's SQL
    (sql/<date>_curriculum_review_instance_manual_overrides.sql) has not been
    run against Neon yet. Keeping it separate means only the manual-override
    action itself is affected by that ordering, not the rest of the review
    engine.
    """
    global _MANUAL_OVERRIDES_TABLE_READY
    if _MANUAL_OVERRIDES_TABLE_READY:
        return
    if not schema_gate.runtime_bootstrap_allowed():
        schema_gate.require_tables(REVIEW_INSTANCE_MANUAL_OVERRIDES_TABLE)
        _MANUAL_OVERRIDES_TABLE_READY = True
        return
    _provision_review_instance_manual_overrides_table()
    _MANUAL_OVERRIDES_TABLE_READY = True


def _provision_review_instance_manual_overrides_table():
    """Mirrors sql/<date>_curriculum_review_instance_manual_overrides.sql for
    sqlite/local dev. NOT for production request paths -- see
    ensure_review_instance_manual_overrides_table/schema_gate."""
    from django.db import connection
    with connection.cursor() as cursor:
        if connection.vendor == 'postgresql':
            cursor.execute(f'create schema if not exists {curriculum_views.quote_ident(curriculum_views.CURRICULUM_SCHEMA)}')
        cursor.execute(f'''
            create table if not exists {curriculum_views.authoring_table_name(REVIEW_INSTANCE_MANUAL_OVERRIDES_TABLE)} (
                id varchar(128) primary key,
                review_instance_id varchar(128) not null,
                calendar_event_id integer,
                previous_status varchar(32) not null,
                new_status varchar(32) not null,
                reason_code varchar(64) not null,
                note text not null default '',
                changed_by varchar(255) not null,
                changed_at timestamp not null default current_timestamp,
                manual_started_at timestamp
            )
        ''')
        cursor.execute(f'''
            create index if not exists review_instance_manual_overrides_instance_idx
            on {curriculum_views.authoring_table_name(REVIEW_INSTANCE_MANUAL_OVERRIDES_TABLE)} (review_instance_id, changed_at desc)
        ''')


#: Set once this process has provisioned/verified the reopens table, on its own
#: gate for exactly the reason ensure_review_instance_manual_overrides_table
#: has one -- see ensure_review_instance_reopens_table.
_REOPENS_TABLE_READY = False


def ensure_review_instance_reopens_table():
    """Verify/provision review_instance_reopens on its own, narrow gate.

    Same reasoning as ensure_review_instance_manual_overrides_table: folding
    this into the three core tables' gate would mean every ordinary review
    operation (schedule, answer, complete, sign) starts failing the moment
    this code deploys, if sql/2026-09-21_curriculum_review_instance_reopens.sql
    has not been run against Neon yet. Keeping it separate confines that
    ordering risk to the reopen action itself.
    """
    global _REOPENS_TABLE_READY
    if _REOPENS_TABLE_READY:
        return
    if not schema_gate.runtime_bootstrap_allowed():
        schema_gate.require_tables(REVIEW_INSTANCE_REOPENS_TABLE)
        _REOPENS_TABLE_READY = True
        return
    _provision_review_instance_reopens_table()
    _REOPENS_TABLE_READY = True


def _provision_review_instance_reopens_table():
    """Mirrors sql/2026-09-21_curriculum_review_instance_reopens.sql for
    sqlite/local dev. NOT for production request paths -- see
    ensure_review_instance_reopens_table/schema_gate."""
    from django.db import connection
    json_type = curriculum_views.authoring_json_type()
    with connection.cursor() as cursor:
        if connection.vendor == 'postgresql':
            cursor.execute(f'create schema if not exists {curriculum_views.quote_ident(curriculum_views.CURRICULUM_SCHEMA)}')
        cursor.execute(f'''
            create table if not exists {curriculum_views.authoring_table_name(REVIEW_INSTANCE_REOPENS_TABLE)} (
                id varchar(128) primary key,
                review_instance_id varchar(128) not null,
                calendar_event_id integer,
                previous_status varchar(32) not null,
                new_status varchar(32) not null,
                reason_code varchar(64) not null,
                note text not null default '',
                changed_by varchar(255) not null,
                changed_at timestamp not null default current_timestamp,
                previous_state_snapshot {json_type}
            )
        ''')
        cursor.execute(f'''
            create index if not exists review_instance_reopens_instance_idx
            on {curriculum_views.authoring_table_name(REVIEW_INSTANCE_REOPENS_TABLE)} (review_instance_id, changed_at desc)
        ''')


#: Set once this process has provisioned/verified learner_review_additions,
#: separately from _TABLES_READY for the same reason
#: ensure_review_instance_manual_overrides_table is its own gate: this table
#: is only needed by the learner-specific-addition feature, not by every
#: existing review-instance operation.
_LEARNER_REVIEW_ADDITIONS_TABLE_READY = False


def ensure_learner_review_additions_table():
    global _LEARNER_REVIEW_ADDITIONS_TABLE_READY
    if _LEARNER_REVIEW_ADDITIONS_TABLE_READY:
        return
    if not schema_gate.runtime_bootstrap_allowed():
        schema_gate.require_tables(LEARNER_REVIEW_ADDITIONS_TABLE)
        _LEARNER_REVIEW_ADDITIONS_TABLE_READY = True
        return
    _provision_learner_review_additions_table()
    _LEARNER_REVIEW_ADDITIONS_TABLE_READY = True


def _provision_learner_review_additions_table():
    """Mirrors sql/2026-09-15_curriculum_learner_review_additions.sql for
    sqlite/local dev. NOT for production request paths -- see
    ensure_learner_review_additions_table/schema_gate."""
    from django.db import connection
    with connection.cursor() as cursor:
        if connection.vendor == 'postgresql':
            cursor.execute(f'create schema if not exists {curriculum_views.quote_ident(curriculum_views.CURRICULUM_SCHEMA)}')
        cursor.execute(f'''
            create table if not exists {curriculum_views.authoring_table_name(LEARNER_REVIEW_ADDITIONS_TABLE)} (
                id varchar(128) primary key,
                review_template_id varchar(128) not null,
                programme_id varchar(255) not null,
                learner_id integer not null,
                target_date date not null,
                reason_code varchar(64) not null default '',
                reason varchar(1000) not null default '',
                created_by varchar(255) not null default '',
                created_at timestamp not null default current_timestamp,
                updated_by varchar(255) not null default '',
                updated_at timestamp not null default current_timestamp,
                deleted_at timestamp,
                deleted_by varchar(255)
            )
        ''')
        cursor.execute(f'''
            create index if not exists learner_review_additions_learner_idx
            on {curriculum_views.authoring_table_name(LEARNER_REVIEW_ADDITIONS_TABLE)} (learner_id)
        ''')


def provision_review_instance_tables():
    """Mirrors sql/2026-09-13_curriculum_review_instances.sql for sqlite/local
    dev. NOT for production request paths -- see reviews.py/schema_gate."""
    global _TABLES_READY
    if _TABLES_READY:
        return
    from django.db import connection
    json_type = curriculum_views.authoring_json_type()
    with connection.cursor() as cursor:
        if connection.vendor == 'postgresql':
            cursor.execute(f'create schema if not exists {curriculum_views.quote_ident(curriculum_views.CURRICULUM_SCHEMA)}')
        cursor.execute(f'''
            create table if not exists {curriculum_views.authoring_table_name(REVIEW_INSTANCES_TABLE)} (
                id varchar(128) primary key,
                review_template_id varchar(128) not null,
                learner_id integer not null,
                learner_kind varchar(32) not null default '',
                programme_id varchar(255) not null,
                occurrence_number integer,
                occurrence_source varchar(16) not null default 'generated',
                occurrence_ref varchar(160),
                target_date date not null,
                coach_email varchar(255) not null default '',
                calendar_event_id integer,
                definition_snapshot {json_type} not null,
                progress_snapshot {json_type},
                status varchar(32) not null default 'not-scheduled',
                started_at timestamp,
                completed_at timestamp,
                created_by varchar(255) not null default '',
                updated_by varchar(255) not null default '',
                created_at timestamp not null default current_timestamp,
                updated_at timestamp not null default current_timestamp
            )
        ''')
        cursor.execute(f'''
            create table if not exists {curriculum_views.authoring_table_name(REVIEW_INSTANCE_ANSWERS_TABLE)} (
                id varchar(128) primary key,
                review_instance_id varchar(128) not null,
                field_id varchar(128) not null,
                answer {json_type},
                answered_by varchar(255) not null default '',
                answered_at timestamp not null default current_timestamp,
                created_at timestamp not null default current_timestamp,
                updated_at timestamp not null default current_timestamp
            )
        ''')
        cursor.execute(f'''
            create table if not exists {curriculum_views.authoring_table_name(REVIEW_INSTANCE_SIGNATURES_TABLE)} (
                id varchar(128) primary key,
                review_instance_id varchar(128) not null,
                role varchar(32) not null,
                signed_by varchar(255) not null default '',
                signed_name varchar(255) not null default '',
                signature text,
                signed_at timestamp,
                created_at timestamp not null default current_timestamp,
                updated_at timestamp not null default current_timestamp
            )
        ''')
    _TABLES_READY = True


# ---------------------------------------------------------- template lookup

def get_review_type_template(programme_id, review_type_code):
    """One enabled Review template a programme has classified with the given
    Review Type code ('mcm' | 'progress_review'), or None.

    Resolution is Review Type code -> review_types.id -> review_templates
    .review_type_id. It never looks at a template's name, and never at the
    retired coach_surface column.

    A programme is deliberately NOT limited to one Review per type -- a type
    classifies, it does not claim a slot. This returns the most recently
    updated match (get_review_template_rows orders by updated_at desc), which
    only matters to progress_reviews_api's PPTX interval fallback; both
    calendars iterate every enabled template instead of asking for one.
    """
    if not programme_id or not review_type_code:
        return None
    type_row = review_types.get_review_type_by_code(review_type_code)
    if not type_row:
        return None
    rows = reviews.get_review_template_rows(
        'programme_id = %s and enabled = true and review_type_id = %s',
        [programme_id, type_row.get('id')],
    )
    return rows[0] if rows else None


def list_enabled_review_templates(programme_id):
    """Every enabled Review template a programme has configured, in display
    order. Consumers (Coach timetable, learner calendar) iterate this rather
    than asking for named/known Reviews -- a Review Curriculum adds tomorrow
    reaches both calendars with no code change."""
    if not programme_id:
        return []
    return reviews.get_review_template_rows(
        'programme_id = %s and enabled = true',
        [programme_id],
    )


def resolve_programme_review_occurrences(
    programme_id,
    learner_id,
    learner_status,
    learner_start_date,
    window_start,
    window_end,
    *,
    template_cache=None,
    learner_scope=None,
):
    """Every Curriculum Review occurrence due for one learner in a window,
    across ALL of their programme's enabled Review templates.

    This is the single entry point both calendars use -- there is no list of
    Review types anywhere in the consumers, so whichever Reviews a programme
    has configured are exactly the Reviews its learners see.
    """
    if template_cache is None:
        template_cache = {}
    if programme_id not in template_cache:
        template_cache[programme_id] = list_enabled_review_templates(programme_id)

    # The type catalogue is a handful of rows; read once per call rather than
    # once per template, and never per learner.
    type_index = review_types.review_type_index()

    applicable_templates = [
        template_row for template_row in template_cache[programme_id]
        if review_applies_to_placement(template_row, learner_scope)
    ]
    templates_by_id = {template_row.get('id'): template_row for template_row in applicable_templates}
    # Overrides used to be fetched once per template. A programme with four
    # Review templates therefore paid four network round-trips on every Coach
    # and Learner calendar read. Fetch the same rows in one bounded query and
    # hand each template only its own slice.
    overrides_by_review = _fetch_learner_scoped_overrides_for_reviews(
        templates_by_id, learner_id,
    )

    occurrences = []
    for template_row in applicable_templates:
        type_row = type_index.get(curriculum_views.clean_str(template_row.get('review_type_id')))
        for occurrence in resolve_learner_occurrences(
            template_row, learner_id, learner_status, learner_start_date, window_start, window_end,
            overrides_by_date=overrides_by_review.get(template_row.get('id'), {}),
        ):
            # The Review's classification travels with every occurrence, so
            # Coach and the learner calendar can bucket it without ever
            # reading its name. A template with no type yet (pre-backfill)
            # carries a blank code and lands in the generic review bucket.
            #
            # The type's NAME and system flag travel too: a consumer building
            # filter buckets needs a label and a deterministic order, and
            # re-reading the catalogue downstream would be a second query for
            # data already in hand here. The name is the FILTER label only --
            # the event's own title stays review_templates.name, so renaming a
            # template renames the card without moving its bucket.
            occurrence['reviewTypeId'] = curriculum_views.clean_str(template_row.get('review_type_id'))
            occurrence['reviewTypeCode'] = curriculum_views.clean_str((type_row or {}).get('code'))
            occurrence['reviewTypeName'] = curriculum_views.clean_str((type_row or {}).get('name'))
            occurrence['reviewTypeIsSystem'] = bool((type_row or {}).get('is_system'))
            occurrence['occurrenceSource'] = OCCURRENCE_SOURCE_GENERATED
            occurrence['occurrenceRef'] = f"{OCCURRENCE_SOURCE_GENERATED}:{occurrence['occurrenceNumber']}"
            occurrences.append(occurrence)

    # Learner-specific additions (coach "Add Review") merge in alongside the
    # generated occurrences -- same learner, same window -- without touching
    # programme recurrence, other learners, or occurrence numbering in any
    # way. See resolve_learner_manual_occurrences: each is tagged
    # occurrenceSource='manual' and never carries an occurrenceNumber.
    for occurrence in resolve_learner_manual_occurrences(
        programme_id, learner_id, window_start=window_start, window_end=window_end,
        templates_by_id=templates_by_id, type_index=type_index,
    ):
        occurrences.append(occurrence)

    occurrences.sort(key=lambda item: item['targetDate'])
    return occurrences


# ------------------------------------------------- learner-specific additions

def find_active_learner_review_addition(review_template_id, learner_id, target_date):
    ensure_learner_review_additions_table()
    rows = curriculum_views.fetch_all(
        f'select * from {curriculum_views.table_name(LEARNER_REVIEW_ADDITIONS_TABLE)} '
        f'where review_template_id = %s and learner_id = %s and target_date = %s and deleted_at is null',
        [review_template_id, learner_id, target_date],
    )
    return rows[0] if rows else None


def get_learner_review_addition(addition_id):
    ensure_learner_review_additions_table()
    rows = curriculum_views.fetch_all(
        f'select * from {curriculum_views.table_name(LEARNER_REVIEW_ADDITIONS_TABLE)} where id = %s',
        [addition_id],
    )
    return rows[0] if rows else None


def list_active_learner_review_additions(programme_id, learner_id):
    ensure_learner_review_additions_table()
    return curriculum_views.fetch_all(
        f'select * from {curriculum_views.table_name(LEARNER_REVIEW_ADDITIONS_TABLE)} '
        f'where programme_id = %s and learner_id = %s and deleted_at is null',
        [programme_id, learner_id],
    )


def create_learner_review_addition(
    *, review_template_id, programme_id, learner_id, target_date, reason_code='', reason='', actor='system',
):
    """Idempotent get-or-create for one learner's one additional Review on
    one date -- a repeated request for the exact same
    (review_template_id, learner_id, target_date) returns the SAME addition
    rather than creating a duplicate (see the partial unique index in
    sql/2026-09-15_curriculum_learner_review_additions.sql). This is the ONLY
    way a learner-specific occurrence is created -- it never touches
    review_occurrence_overrides (skip-only) or review_templates (programme-
    wide) at all.
    """
    ensure_learner_review_additions_table()
    existing = find_active_learner_review_addition(review_template_id, learner_id, target_date)
    if existing:
        return existing

    from coach_api.review_sources import require_native_review_creation
    require_native_review_creation(learner_id)

    addition_id = curriculum_views.unique_prefixed_id('LRA')
    with transaction.atomic():
        try:
            row = curriculum_views.insert_row(LEARNER_REVIEW_ADDITIONS_TABLE, {
                'id': addition_id,
                'review_template_id': review_template_id,
                'programme_id': programme_id,
                'learner_id': learner_id,
                'target_date': target_date,
                'reason_code': reason_code or '',
                'reason': reason or '',
                'created_by': actor,
                'updated_by': actor,
                'created_at': datetime.utcnow(),
                'updated_at': datetime.utcnow(),
            })
        except Exception:
            # Lost the race with a concurrent identical request -- the unique
            # index rejected the insert. The row that won is what we want.
            existing = find_active_learner_review_addition(review_template_id, learner_id, target_date)
            if existing:
                return existing
            raise
    return row


def resolve_learner_manual_occurrences(
    programme_id, learner_id, *, window_start=None, window_end=None, templates_by_id=None, type_index=None,
):
    """Every active learner-specific addition for this learner's programme,
    shaped exactly like a generated occurrence dict (same keys consumers
    already read) so both calendars can merge the two lists with no special
    casing beyond occurrenceSource/occurrenceRef.

    An addition whose template is no longer enabled is silently excluded --
    the same rule a disabled template already applies to its generated
    occurrences (see list_enabled_review_templates) -- rather than raising,
    since this runs on every calendar read.

    Fails safe to [] on any error (including
    schema_gate.SchemaNotProvisioned before
    sql/2026-09-15_curriculum_learner_review_additions.sql has been applied in
    production) -- exactly like resolve_curriculum_review_occurrences already
    does for the rest of Curriculum: a problem reading manual additions must
    never blank a learner's GENERATED occurrences, and this feature deploying
    ahead of its own migration must never break every existing calendar read.
    """
    if not programme_id or not learner_id:
        return []
    try:
        return _resolve_learner_manual_occurrences(
            programme_id, learner_id, window_start=window_start, window_end=window_end,
            templates_by_id=templates_by_id, type_index=type_index,
        )
    except Exception:
        logger.warning(
            'Could not resolve learner-specific Review additions for programme %s learner %s',
            programme_id, learner_id, exc_info=True,
        )
        return []


def _resolve_learner_manual_occurrences(
    programme_id, learner_id, *, window_start, window_end, templates_by_id, type_index,
):
    if templates_by_id is None:
        templates_by_id = {row.get('id'): row for row in list_enabled_review_templates(programme_id)}
    if type_index is None:
        type_index = review_types.review_type_index()

    occurrences = []
    for addition in list_active_learner_review_additions(programme_id, learner_id):
        template_row = templates_by_id.get(addition.get('review_template_id'))
        if not template_row:
            continue
        raw_target_date = addition.get('target_date')
        if isinstance(raw_target_date, datetime):
            target_date = raw_target_date.date()
        elif isinstance(raw_target_date, date):
            target_date = raw_target_date
        else:
            target_date = date.fromisoformat(curriculum_views.clean_str(raw_target_date)[:10])
        if window_start and target_date < window_start:
            continue
        if window_end and target_date > window_end:
            continue
        type_row = type_index.get(curriculum_views.clean_str(template_row.get('review_type_id')))
        occurrences.append({
            'reviewTemplateId': template_row.get('id'),
            'reviewName': template_row.get('name') or '',
            'occurrenceNumber': None,
            'targetDate': target_date,
            'recurrenceLabel': 'Additional review',
            'reviewTypeId': curriculum_views.clean_str(template_row.get('review_type_id')),
            'reviewTypeCode': curriculum_views.clean_str((type_row or {}).get('code')),
            'reviewTypeName': curriculum_views.clean_str((type_row or {}).get('name')),
            'reviewTypeIsSystem': bool((type_row or {}).get('is_system')),
            'occurrenceSource': OCCURRENCE_SOURCE_MANUAL,
            'occurrenceRef': f'{OCCURRENCE_SOURCE_MANUAL}:{addition.get("id")}',
            'additionId': addition.get('id'),
            'reasonCode': curriculum_views.clean_str(addition.get('reason_code')),
            'reason': curriculum_views.clean_str(addition.get('reason')),
        })
    return occurrences


def review_applies_to_placement(template_row, learner_scope=None):
    applicability = curriculum_views.as_json_value(template_row.get('applicability'), {})
    scope = applicability.get('scope', 'programme')
    if scope == 'programme':
        return True
    if scope not in ('cohort', 'group') or not learner_scope:
        return False
    identifiers = set(applicability.get('ids') or [])
    placement_id = curriculum_views.clean_str(learner_scope.get(f'{scope}_id'))
    if placement_id:
        return placement_id in identifiers
    # Older learner mirrors carry names only. Resolve within this programme,
    # and within the cohort for groups; ambiguous names never grant access.
    table = curriculum_views.COHORT_AUTHORING_DETAILS_TABLE if scope == 'cohort' else curriculum_views.GROUPS_TABLE
    if '_placement_rows' not in template_row:
        template_row['_placement_rows'] = curriculum_views.authoring_fetch_all(
            table, 'programme_id = %s', [template_row['programme_id']],
        )
    name = curriculum_views.clean_str(learner_scope.get(scope)).casefold()
    if not name:
        return False
    matches = []
    for row in template_row['_placement_rows']:
        if curriculum_views.clean_str(row.get(f'{scope}_name')).casefold() != name:
            continue
        if scope == 'group':
            cohort_id = curriculum_views.clean_str(learner_scope.get('cohort_id'))
            cohort_name = curriculum_views.clean_str(learner_scope.get('cohort')).casefold()
            if cohort_id and curriculum_views.clean_str(row.get('cohort_id')) != cohort_id:
                continue
            if not cohort_id and (not cohort_name or curriculum_views.clean_str(row.get('cohort_name')).casefold() != cohort_name):
                continue
        matches.append(curriculum_views.clean_str(row.get(f'{scope}_id')))
    return len(matches) == 1 and matches[0] in identifiers


def review_calendar_event_key(learner_id, template_id, occurrence_number):
    """An occurrence survives renames, reclassification and target-date edits."""
    return f'review:{learner_id}:{template_id}:{occurrence_number}'


def review_calendar_event_key_manual(learner_id, template_id, addition_id):
    """A learner-specific additional Review's event key -- identity-based and
    date-independent, like the generated form above, but keyed by the
    learner_review_additions row rather than a calendar-derived occurrence
    number (a manual addition has none -- see
    sql/2026-09-15_curriculum_learner_review_additions.sql). Never contains
    target_date, so rescheduling or editing the addition's own record never
    changes the row's identity."""
    return f'review:{learner_id}:{template_id}:manual:{addition_id}'


def reconcile_review_event_keys(events, records):
    """Keep existing booking keys, matching legacy keys only when unambiguous.

    Identity-tuple matching (by_identity) is scoped to events/records that
    carry a real occurrenceNumber -- i.e. GENERATED occurrences only. A
    manual (learner-specific addition) occurrence has no occurrence_number by
    design (occurrence_ref is its identity instead -- see
    curriculum.review_instances.occurrence_ref), so it is deliberately
    excluded from this dict rather than falling back to record.sequence:
    that fallback would collide a manual review with a generated occurrence
    that happens to carry the same sequence value for the same
    learner+template. A manual occurrence is still matched correctly below,
    via its own stable, unique event_key (by_key) -- it never needs the
    identity-tuple path at all.
    """
    by_identity = {}
    by_key = {record.event_key: record for record in records}
    legacy_candidates = {}
    for event in events:
        legacy_key = f"{event['source']}:{event['learnerId']}:{event['sequence']}:{event['targetDate']}"
        legacy_candidates.setdefault(legacy_key, []).append(event)
    for record in records:
        template_id = getattr(record, 'review_template_id', '')
        occurrence_number = getattr(record, 'occurrence_number', None)
        if template_id and occurrence_number is not None:
            identity = (str(record.learner_id), template_id, occurrence_number)
            by_identity[identity] = record
    matched = {}
    for event in events:
        occurrence_number = event.get('occurrenceNumber')
        identity = (str(event['learnerId']), event.get('reviewTemplateId'), occurrence_number) if occurrence_number is not None else None
        record = (by_identity.get(identity) if identity is not None else None) or by_key.get(event['eventKey'])
        if record is None:
            legacy_key = f"{event['source']}:{event['learnerId']}:{event['sequence']}:{event['targetDate']}"
            candidate = by_key.get(legacy_key)
            if candidate and not getattr(candidate, 'review_template_id', '') and len(legacy_candidates[legacy_key]) == 1:
                record = candidate
        if record is not None:
            event['id'] = event['eventKey'] = record.event_key
            matched[record.event_key] = record
    return matched


def programme_review_template_identifiers(programme_id, *, template_cache=None):
    """[(review_template_id, review_type_code)] for a programme's enabled
    Review templates -- what a diagnostic needs to NAME the Reviews it did not
    generate, without generating anything.

    Shares ``resolve_programme_review_occurrences``'s template cache, so a
    caller already walking a caseload pays nothing for the programmes it has
    seen.
    """
    if not programme_id:
        return []
    if template_cache is None:
        template_cache = {}
    if programme_id not in template_cache:
        template_cache[programme_id] = list_enabled_review_templates(programme_id)
    type_index = review_types.review_type_index()
    identifiers = []
    for template_row in template_cache[programme_id]:
        type_row = type_index.get(curriculum_views.clean_str(template_row.get('review_type_id')))
        identifiers.append((
            template_row.get('id'),
            curriculum_views.clean_str((type_row or {}).get('code')) or None,
        ))
    return identifiers


def learner_is_eligible(template_row, learner_status):
    """applicable_statuses is Curriculum's only eligibility source -- an empty
    list means no restriction has been configured, not "nobody is eligible"."""
    statuses = curriculum_views.as_json_value(template_row.get('applicable_statuses'), [])
    if not statuses:
        return True
    return curriculum_views.clean_str(learner_status) in statuses


# --------------------------------------------------------- learner-scoped
# occurrence overrides (extends review_schedule.py's programme-wide table
# with an optional learner_id column, without touching that module's own
# programme-level clash-preview reads/writes).

def _fetch_learner_scoped_overrides(review_id, learner_id):
    """Active overrides for one Review, either programme-wide (learner_id is
    null) or scoped to this learner specifically -- keyed by occurrence date
    so a learner-specific skip never touches another learner's schedule."""
    rows = curriculum_views.fetch_all(
        f'select * from {curriculum_views.table_name(OVERRIDES_TABLE)} '
        f'where review_id = %s and deleted_at is null '
        f'and (learner_id is null or learner_id = %s)',
        [review_id, learner_id],
    )
    return {curriculum_views.format_date(row.get('occurrence_date')): row for row in rows}


def _fetch_learner_scoped_overrides_for_reviews(review_ids, learner_id):
    """All active programme/learner overrides, grouped by Review template."""
    wanted = sorted({str(review_id).strip() for review_id in review_ids if str(review_id or '').strip()})
    if not wanted:
        return {}
    rows = curriculum_views.fetch_all(
        f'select * from {curriculum_views.table_name(OVERRIDES_TABLE)} '
        f"where review_id in ({', '.join(['%s'] * len(wanted))}) and deleted_at is null "
        f'and (learner_id is null or learner_id = %s)',
        [*wanted, learner_id],
    )
    grouped = {review_id: {} for review_id in wanted}
    for row in rows:
        review_id = str(row.get('review_id') or '').strip()
        if review_id in grouped:
            grouped[review_id][curriculum_views.format_date(row.get('occurrence_date'))] = row
    return grouped


def skip_occurrence_for_learner(review_id, occurrence_date_iso, programme_id, learner_id, *, actor='staff', reason=''):
    """Learner-specific skip -- unlike review_schedule.skip_occurrence, this
    never affects any other learner on the same programme/template."""
    existing_rows = curriculum_views.fetch_all(
        f'select * from {curriculum_views.table_name(OVERRIDES_TABLE)} '
        f'where review_id = %s and occurrence_date = %s and learner_id = %s and deleted_at is null',
        [review_id, occurrence_date_iso, learner_id],
    )
    if existing_rows:
        curriculum_views.update_rows(OVERRIDES_TABLE, 'id = %s', [existing_rows[0]['id']], {
            'reason': reason, 'updated_by': actor, 'updated_at': datetime.utcnow(),
        })
        return existing_rows[0]['id']
    override_id = curriculum_views.unique_prefixed_id('REVOL')
    curriculum_views.insert_row(OVERRIDES_TABLE, {
        'id': override_id,
        'review_id': review_id,
        'programme_id': programme_id,
        'occurrence_date': occurrence_date_iso,
        'learner_id': learner_id,
        'action': 'skip',
        'reason': reason,
        'created_by': actor,
        'updated_by': actor,
        'created_at': datetime.utcnow(),
        'updated_at': datetime.utcnow(),
    })
    return override_id


# -------------------------------------------------------- occurrence engine

def template_recurrence_config(row):
    return {
        'interval': curriculum_views.parse_int(row.get('recurrence_interval'), 1),
        'unit': row.get('recurrence_unit') or 'weeks',
        'occurrenceLimit': row.get('occurrence_count'),
    }


def resolve_learner_occurrences(
    template_row, learner_id, learner_status, learner_start_date, window_start, window_end,
    *, overrides_by_date=None,
):
    """RAW occurrences of one Review template for one learner, anchored to
    their own start date -- never the template's own schedule_anchor_date,
    which only drives the Curriculum-side programme schedule preview.

    Returns [] outright (no occurrences, ever) when the template is disabled,
    the learner is not currently eligible, or there is no start date to
    anchor from -- callers must not fall back to any hard-coded rule when
    this returns empty; an empty result means "Curriculum does not produce
    this Review for this learner right now", which is a valid, correct
    answer, not a gap to patch over.
    """
    if not template_row or not bool(template_row.get('enabled')):
        return []
    if not learner_is_eligible(template_row, learner_status):
        return []
    if not learner_start_date:
        return []

    recurrence = template_recurrence_config(template_row)
    review_id = template_row.get('id')
    if overrides_by_date is None:
        overrides_by_date = _fetch_learner_scoped_overrides(review_id, learner_id)

    # Occurrence #1 is one interval AFTER the learner's start date, never the
    # start date itself (see the brief's own MCM/PR worked examples: a 2 Aug
    # start with a 1-month interval produces 2 Sep as occurrence #1, not
    # 2 Aug). generate_occurrence_steps treats its anchor as occurrence #1,
    # so the anchor passed to it here is shifted forward by one interval --
    # this is the only place that shift happens, so occurrence numbering
    # everywhere downstream (instance identity, "occurrence #2", overrides)
    # stays in the learner-facing 1-based scheme the rest of the app expects.
    interval, unit = recurrence['interval'], recurrence['unit']
    if unit == 'months':
        virtual_anchor = review_schedule.add_calendar_months(learner_start_date, interval)
    else:
        step_days = interval * (7 if unit == 'weeks' else 1)
        virtual_anchor = learner_start_date + timedelta(days=step_days)

    steps = review_schedule.generate_occurrence_steps(
        virtual_anchor, interval, unit,
        window_start, window_end, occurrence_limit=recurrence['occurrenceLimit'],
    )

    occurrences = []
    for occurrence_number, occurrence_date in steps:
        if curriculum_views.format_date(occurrence_date) in overrides_by_date:
            continue
        occurrences.append({
            'reviewTemplateId': review_id,
            'reviewName': template_row.get('name') or '',
            'occurrenceNumber': occurrence_number,
            'targetDate': occurrence_date,
            'recurrenceLabel': review_schedule.recurrence_label(recurrence['interval'], recurrence['unit']),
        })
    return occurrences


# ---------------------------------------------------------- review instance

def existing_review_instance_ids():
    rows = curriculum_views.fetch_all(
        f'select id from {curriculum_views.table_name(REVIEW_INSTANCES_TABLE)}', [],
    )
    return [row.get('id') for row in rows]


def get_review_instance(instance_id, *, for_update=False):
    # Booking callers hold a transaction while checking the linked lifecycle.
    lock = ' for update' if for_update and curriculum_views.connection.vendor == 'postgresql' else ''
    rows = curriculum_views.fetch_all(
        f'select * from {curriculum_views.table_name(REVIEW_INSTANCES_TABLE)} where id = %s{lock}',
        [instance_id],
    )
    return rows[0] if rows else None


def find_review_instance(review_template_id, learner_id, occurrence_number):
    rows = curriculum_views.fetch_all(
        f'select * from {curriculum_views.table_name(REVIEW_INSTANCES_TABLE)} '
        f'where review_template_id = %s and learner_id = %s and occurrence_number = %s',
        [review_template_id, learner_id, occurrence_number],
    )
    return rows[0] if rows else None


def find_review_instance_by_ref(review_template_id, learner_id, occurrence_ref):
    """Identity lookup for a MANUAL (learner-specific addition) occurrence --
    the generated-occurrence identity (find_review_instance, by
    occurrence_number) is untouched and remains the lookup generated rows
    use. See review_instances_occurrence_ref_uniq in
    sql/2026-09-15_curriculum_learner_review_additions.sql."""
    rows = curriculum_views.fetch_all(
        f'select * from {curriculum_views.table_name(REVIEW_INSTANCES_TABLE)} '
        f'where review_template_id = %s and learner_id = %s and occurrence_ref = %s',
        [review_template_id, learner_id, occurrence_ref],
    )
    return rows[0] if rows else None


def list_review_instances_for_learner(learner_id, *, window_start=None, window_end=None):
    conditions = ['learner_id = %s']
    params = [learner_id]
    if window_start:
        conditions.append('target_date >= %s')
        params.append(window_start)
    if window_end:
        conditions.append('target_date <= %s')
        params.append(window_end)
    where = ' and '.join(conditions)
    return curriculum_views.fetch_all(
        f'select * from {curriculum_views.table_name(REVIEW_INSTANCES_TABLE)} where {where} order by target_date',
        params,
    )


def json_safe(value):
    """The same structure with every value JSON can actually hold.

    The template payload carries live ``datetime``/``date`` objects (a row's
    createdAt/updatedAt), which the response encoder handles but a plain
    ``json.dumps`` into JSONB does not. Normalising on the way in also keeps
    the snapshot round-trippable: what is read back out of the column has the
    same shape as what was frozen into it.
    """
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def build_definition_snapshot(template_row):
    return json_safe(reviews.review_template_detail_payload(template_row))


def ensure_review_instance(
    template_row, *, learner_id, learner_kind, programme_id, occurrence_number, target_date,
    coach_email='', actor='system', occurrence_source=OCCURRENCE_SOURCE_GENERATED, occurrence_ref=None,
):
    """Idempotent get-or-create for one learner's one occurrence.

    Identity for a GENERATED occurrence (occurrence_source, the default) is
    (review_template_id, learner_id, occurrence_number) -- see the unique
    index in the SQL migration -- and is completely unchanged by the
    occurrence_source/occurrence_ref parameters below: every existing caller
    passes neither, so its behaviour, lookup and stored values are identical
    to before this parameter pair existed.

    Identity for a MANUAL (learner-specific addition) occurrence is instead
    (review_template_id, learner_id, occurrence_ref) -- occurrence_number is
    stored as NULL for these rows (see
    sql/2026-09-15_curriculum_learner_review_additions.sql for why a manual
    occurrence cannot safely share the generated numbering space). Callers
    creating a manual instance MUST pass occurrence_source='manual' and an
    explicit occurrence_ref (see review_calendar_event_key_manual for the
    matching event key).

    A repeated call (the caseload timetable is recomputed on every page
    load) always returns the same row rather than creating a duplicate.
    """
    ensure_review_instance_tables()
    review_template_id = template_row.get('id')
    is_manual = occurrence_source == OCCURRENCE_SOURCE_MANUAL
    ref = occurrence_ref or f'{OCCURRENCE_SOURCE_GENERATED}:{occurrence_number}'

    def _existing():
        if is_manual:
            row = find_review_instance_by_ref(review_template_id, learner_id, ref)
        else:
            row = find_review_instance(review_template_id, learner_id, occurrence_number)
        if row:
            from coach_api.reconciliation_links import for_native
            if for_native(row['id']):
                raise ValueError('This legacy occurrence is reconciled. Open its imported Aptem Review.')
        return row

    existing = _existing()
    if existing:
        return existing

    from coach_api.review_sources import require_native_review_creation
    require_native_review_creation(learner_id)

    instance_id = curriculum_views.unique_prefixed_id('REVI', '', existing_review_instance_ids)
    with transaction.atomic():
        try:
            row = curriculum_views.insert_row(REVIEW_INSTANCES_TABLE, {
                'id': instance_id,
                'review_template_id': review_template_id,
                'learner_id': learner_id,
                'learner_kind': learner_kind or '',
                'programme_id': programme_id,
                'occurrence_number': None if is_manual else occurrence_number,
                'occurrence_source': occurrence_source,
                'occurrence_ref': ref,
                'target_date': target_date,
                'coach_email': coach_email or '',
                'definition_snapshot': curriculum_views.json_db_value(build_definition_snapshot(template_row)),
                'status': STATUS_NOT_SCHEDULED,
                'created_by': actor,
                'updated_by': actor,
                'created_at': datetime.utcnow(),
                'updated_at': datetime.utcnow(),
            })
        except Exception:
            # Lost the race with a concurrent identical request -- the unique
            # index rejected the insert. The row that won is what we want.
            existing = _existing()
            if existing:
                return existing
            raise
    return row


def force_review_instance_status_for_tests(instance_id, status, *, actor='system', extra=None):
    """TEST FIXTURES ONLY -- an UNGUARDED status write. NEVER call from runtime code.

    This is the one writer in this module with no compare-and-swap and no
    transition rules: ``where id = %s`` and nothing else. It exists purely so a
    test can arrange an instance into a starting lifecycle state in one step.

    It had exactly one runtime caller (signature completion). That caller now
    performs a guarded ``awaiting-signature -> completed`` CAS inside the same
    transaction as its Calendar mirror -- see
    ``record_review_instance_signature`` -- so nothing in the application calls
    this any more, and nothing should. The deliberately alarming name is the
    isolation: every legitimate runtime transition already has a guarded writer
    (``mark_review_instance_scheduled``, ``mark_review_instance_not_scheduled``,
    ``mark_review_instance_in_progress_from_attendance``,
    ``mark_review_instance_in_progress_manually``, ``complete_review_instance``,
    ``record_review_instance_signature``). If you need a new transition, add a
    guarded writer beside those -- do not reach for this.
    """
    payload = {'status': status, 'updated_by': actor, 'updated_at': datetime.utcnow(), **(extra or {})}
    rows = curriculum_views.update_rows(REVIEW_INSTANCES_TABLE, 'id = %s', [instance_id], payload)
    return rows[0] if rows else None


@native_write
def mark_review_instance_scheduled(instance_id, *, actor='system'):
    """not-scheduled -> scheduled, once a real booking (date/time) exists on
    the linked CoachCalendarEvent -- see coach_api.views
    persist_calendar_sync_reservation, which calls this for both new and
    already-linked bookings. Guarded in the UPDATE
    itself (``and status = 'not-scheduled'``) rather than read-then-write, so
    it is atomic and a repeat call is a no-op instead of a race.
    """
    rows = curriculum_views.update_rows(
        REVIEW_INSTANCES_TABLE, 'id = %s and status = %s', [instance_id, STATUS_NOT_SCHEDULED],
        {'status': STATUS_SCHEDULED, 'updated_by': actor, 'updated_at': datetime.utcnow()},
    )
    return rows[0] if rows else None


@native_write
def mark_review_instance_not_scheduled(
    instance_id,
    *,
    calendar_event_id,
    learner_id,
    review_template_id,
    coach_email,
    actor='system',
):
    """Cancel an unstarted linked review without weakening its lifecycle.

    The only write this function can make is ``scheduled -> not-scheduled``.
    Link identity and current status are all predicates in the UPDATE itself,
    so a stale request cannot cancel a different learner's review or regress
    an instance that attendance, completion or signatures already advanced.
    Returning the already-not-scheduled row makes cancellation retries
    idempotent; every other no-op returns ``None`` for the caller to reject.
    """
    where = (
        'id = %s and status = %s and calendar_event_id = %s '
        'and learner_id = %s and review_template_id = %s '
        'and lower(trim(coach_email)) = lower(trim(%s))'
    )
    identity = [
        instance_id,
        STATUS_SCHEDULED,
        calendar_event_id,
        learner_id,
        review_template_id,
        coach_email,
    ]
    rows = curriculum_views.update_rows(
        REVIEW_INSTANCES_TABLE,
        where,
        identity,
        {
            'status': STATUS_NOT_SCHEDULED,
            'updated_by': actor,
            'updated_at': datetime.utcnow(),
        },
    )
    if rows:
        return rows[0]

    current = get_review_instance(instance_id)
    if not current or current.get('status') != STATUS_NOT_SCHEDULED:
        return None
    if (
        current.get('calendar_event_id') != calendar_event_id
        or current.get('learner_id') != learner_id
        or curriculum_views.clean_str(current.get('review_template_id'))
        != curriculum_views.clean_str(review_template_id)
        or curriculum_views.clean_str(current.get('coach_email')).lower()
        != curriculum_views.clean_str(coach_email).lower()
    ):
        return None
    return current


@native_write
def mark_review_instance_in_progress_from_attendance(instance_id, *, started_at, actor='attendance-sync'):
    """The ONLY writer of review_instances.status -> in-progress that reflects
    a real, Microsoft-Graph-confirmed Teams join by an expected coach or
    learner (see coach_api.views.apply_teams_attendance_status_transition for
    how the caller established that, and where it is invoked from). Never
    call this because a form was opened, a draft was saved, a "Start" button
    was clicked, or the scheduled time has passed.

    Strictly monotonic and idempotent: the UPDATE only ever matches a row
    still at exactly ``scheduled`` (guarded in the WHERE clause, not by a
    separate read first, so there is no race and no possibility of
    regressing an instance already at awaiting-signature/completed/
    not-scheduled back to in-progress). A second sync pass that finds the
    same attendance again is a no-op -- ``started_at`` is not rewritten and no
    duplicate transition is recorded.
    """
    rows = curriculum_views.update_rows(
        REVIEW_INSTANCES_TABLE, 'id = %s and status = %s', [instance_id, STATUS_SCHEDULED],
        {'status': STATUS_IN_PROGRESS, 'started_at': started_at, 'updated_by': actor, 'updated_at': datetime.utcnow()},
    )
    return rows[0] if rows else None


#: Stable, machine-readable reasons for a manual scheduled -> in-progress
#: override (Phase 4). The frontend maps these to friendly labels; the
#: backend never accepts free text as the reason itself.
MANUAL_OVERRIDE_REASON_CODES = (
    'coach-confirmed-live-start',
    'teams-link-issue',
    'graph-unavailable',
    'attendance-not-detected',
    'meeting-held-outside-teams',
    'scheduler-delay',
    'other',
)

#: Reason codes a coach may give for reopening a completed/awaiting-signature
#: review for editing (see reopen_review_instance_for_editing). Deliberately
#: NOT MANUAL_OVERRIDE_REASON_CODES: every code in that tuple describes a
#: Teams/attendance detection failure, which is never why an already-finished
#: review is reopened. As there, 'other' requires a note.
REOPEN_REASON_CODES = (
    'correction-required',
    'incorrect-answer',
    'signature-error',
    'employer-requested-change',
    'other',
)

#: The only two statuses a review may be reopened FROM.
REOPENABLE_STATUSES = (STATUS_AWAITING_SIGNATURE, STATUS_COMPLETED)

_manual_override_logger = logging.getLogger('curriculum_api.review_instance_manual_override')
_reopen_logger = logging.getLogger('curriculum_api.review_instance_reopen')


def _manual_override_status_rejection(current_status):
    if current_status == STATUS_IN_PROGRESS:
        return 'This review is already in progress.'
    if current_status == STATUS_AWAITING_SIGNATURE:
        return 'This review has already been submitted and is awaiting signature.'
    if current_status == STATUS_COMPLETED:
        return 'This review has already been completed.'
    if current_status == STATUS_NOT_SCHEDULED:
        return 'This review has not been scheduled yet.'
    return 'This review cannot be manually marked in progress from its current status.'


def _log_manual_in_progress_override(*, review_instance_id, calendar_event_id, reason_code, note, changed_by):
    """Operational log line for a manual override -- useful for debugging,
    but NOT the authoritative record. curriculum.review_instance_manual_
    overrides (record_review_instance_manual_override, below) is the
    persistent business record; this log may still be handy for
    operational debugging alongside it."""
    _manual_override_logger.info(
        'review_instance_manual_in_progress_override',
        extra={
            'review_instance_id': review_instance_id,
            'calendar_event_id': calendar_event_id,
            'previous_status': STATUS_SCHEDULED,
            'new_status': STATUS_IN_PROGRESS,
            'source': 'manual',
            'reason_code': reason_code,
            'note': note,
            'changed_by': changed_by,
            'changed_at': datetime.utcnow().isoformat(),
        },
    )


def record_review_instance_manual_override(
    *, review_instance_id, calendar_event_id, previous_status, new_status,
    reason_code, note, changed_by, manual_started_at,
):
    """Persist ONE row in curriculum.review_instance_manual_overrides.

    Append-only, and ONLY ever called for a manual scheduled -> in-progress
    override that has already succeeded (see
    mark_review_instance_in_progress_manually, its sole caller). Never called
    for a Teams-attendance transition, a completion, a signature, or any
    other lifecycle event -- those are not manual overrides and must never
    appear in this table.
    """
    ensure_review_instance_manual_overrides_table()
    return curriculum_views.insert_row(REVIEW_INSTANCE_MANUAL_OVERRIDES_TABLE, {
        'id': curriculum_views.unique_prefixed_id('REVIOV'),
        'review_instance_id': review_instance_id,
        'calendar_event_id': calendar_event_id,
        'previous_status': previous_status,
        'new_status': new_status,
        'reason_code': reason_code,
        'note': note,
        'changed_by': changed_by,
        'changed_at': datetime.utcnow(),
        'manual_started_at': manual_started_at,
    })


def list_review_instance_manual_overrides(review_instance_id):
    """This instance's manual-override history, most recent first. Empty for
    every instance that has never had a manual override (i.e. almost all of
    them) -- this table has no rows for a normal Teams-attendance-driven
    lifecycle."""
    ensure_review_instance_manual_overrides_table()
    return curriculum_views.fetch_all(
        f'select * from {curriculum_views.table_name(REVIEW_INSTANCE_MANUAL_OVERRIDES_TABLE)} '
        f'where review_instance_id = %s order by changed_at desc',
        [review_instance_id],
    )


def latest_review_instance_manual_override(review_instance_id):
    """The most recent manual override for this instance, or None. Cheap
    convenience for a UI that only ever wants to show the latest one."""
    rows = list_review_instance_manual_overrides(review_instance_id)
    return rows[0] if rows else None


@native_write
def mark_review_instance_in_progress_manually(instance_row, *, reason_code, note='', started_at=None, actor='system'):
    """Authorised fallback for scheduled -> in-progress when Microsoft Teams
    attendance cannot be detected (Graph outage, a delayed attendance report,
    a meeting held on another channel, a scheduler failure, or another
    exceptional operational problem). This is the EXCEPTION path --
    apply_teams_attendance_status_transition (a confirmed Teams join) remains
    the normal one. Never touches Teams identifiers, review answers,
    signatures, target_date, or scheduled_date/time.

    Authorization (the assigned coach, or a super-admin acting through the
    existing view-as/attributed-write mechanism) is the CALLER's
    responsibility -- see
    coach_api.views.coach_review_instance_mark_in_progress_manually. This
    function only enforces the lifecycle/data rules:

    * a valid reason code, with a required note when the reason is "other"
    * the instance is currently exactly `scheduled` (atomic compare-and-swap,
      same pattern as mark_review_instance_in_progress_from_attendance --
      cannot regress not-scheduled/awaiting-signature/completed, and a
      second call after a successful override is rejected, not a silent
      no-op, so a repeat click can't be mistaken for success)
    * started_at: an already-set value is never overwritten; otherwise the
      caller's optional actual-start-time, otherwise now. This value is
      never presented as Teams attendance evidence -- the instance's own
      audit trail (see _log_manual_in_progress_override) records that the
      source was manual, and that is not overwritten by a later attendance
      sync (mark_review_instance_in_progress_from_attendance's own guard
      already refuses to fire once status has moved past `scheduled`).

    Returns (ok, errors_or_row): errors is {'reason': [...]}, {'note': [...]}
    or {'status': [...]} depending on which check failed.
    """
    reason_code = curriculum_views.clean_str(reason_code).lower()
    note = curriculum_views.clean_str(note)
    if reason_code not in MANUAL_OVERRIDE_REASON_CODES:
        return False, {'reason': ['Choose a valid reason for this manual override.']}
    if reason_code == 'other' and not note:
        return False, {'note': ['Add details when the reason is "Other".']}

    current_status = instance_row.get('status')
    if current_status != STATUS_SCHEDULED:
        return False, {'status': [_manual_override_status_rejection(current_status)]}

    effective_started_at = instance_row.get('started_at') or started_at or datetime.utcnow()

    # Provisioned here, before the transaction below opens -- a lazy
    # first-time CREATE TABLE run *inside* transaction.atomic() interacts
    # badly with sqlite (DDL there implicitly ends the surrounding
    # transaction underneath Django's savepoint bookkeeping, which then
    # leaves the following pragma/column-lookup reading against a
    # connection state Django still thinks is mid-transaction). Every other
    # table's provisioning call already runs outside any atomic() block for
    # the same reason; this one is no different, it is just triggered lazily
    # instead of from a test's setUp().
    ensure_review_instance_manual_overrides_table()

    # The status UPDATE and the audit INSERT happen inside one transaction on
    # the same ('default') connection review_instances itself already lives
    # on (curriculum_views.update_rows/insert_row both go through
    # django.db.connection, never 'enrolment') -- so either both apply or,
    # on any error, neither does. A rejected attempt (wrong status, bad
    # reason, lost race) never reaches this block, so no row is ever written
    # for one.
    with transaction.atomic():
        updated = curriculum_views.update_rows(
            REVIEW_INSTANCES_TABLE, 'id = %s and status = %s',
            [instance_row.get('id'), STATUS_SCHEDULED],
            {
                'status': STATUS_IN_PROGRESS, 'started_at': effective_started_at,
                'updated_by': actor, 'updated_at': datetime.utcnow(),
            },
        )
        if not updated:
            return False, {'status': ['This review is no longer scheduled. Reload it and try again.']}

        row = updated[0]
        record_review_instance_manual_override(
            review_instance_id=row.get('id'), calendar_event_id=row.get('calendar_event_id'),
            previous_status=STATUS_SCHEDULED, new_status=STATUS_IN_PROGRESS,
            reason_code=reason_code, note=note, changed_by=actor,
            manual_started_at=effective_started_at,
        )

    _log_manual_in_progress_override(
        review_instance_id=row.get('id'), calendar_event_id=row.get('calendar_event_id'),
        reason_code=reason_code, note=note, changed_by=actor,
    )
    return True, row


@native_write
def link_calendar_event(instance_id, calendar_event_id, *, actor='system'):
    return curriculum_views.update_rows(REVIEW_INSTANCES_TABLE, 'id = %s', [instance_id], {
        'calendar_event_id': calendar_event_id, 'updated_by': actor, 'updated_at': datetime.utcnow(),
    })


# -------------------------------------------------------------- form + answers

def get_review_instance_answers(instance_id):
    rows = curriculum_views.fetch_all(
        f'select * from {curriculum_views.table_name(REVIEW_INSTANCE_ANSWERS_TABLE)} where review_instance_id = %s',
        [instance_id],
    )
    return {row.get('field_id'): row for row in rows}


def get_review_instance_signatures(instance_id):
    rows = curriculum_views.fetch_all(
        f'select * from {curriculum_views.table_name(REVIEW_INSTANCE_SIGNATURES_TABLE)} where review_instance_id = %s',
        [instance_id],
    )
    return {row.get('role'): row for row in rows}


def _fetch_rows_in(table, column, values, *, where='', order_by=''):
    """`select * ... where column in (values)` -- one query for many keys."""
    values = list(dict.fromkeys(values))
    if not values:
        return []
    placeholders = ', '.join(['%s'] * len(values))
    extra = f' and {where}' if where else ''
    order = f' order by {order_by}' if order_by else ''
    return curriculum_views.fetch_all(
        f'select * from {curriculum_views.table_name(table)} where {column} in ({placeholders}){extra}{order}',
        values,
    )


def list_review_instances_for_learners(learner_ids):
    """{str(learner_id): [instance rows by target_date]} in one query --
    list_review_instances_for_learner for many learners at once."""
    grouped = {}
    for row in _fetch_rows_in(REVIEW_INSTANCES_TABLE, 'learner_id', learner_ids, order_by='target_date'):
        grouped.setdefault(str(row.get('learner_id')), []).append(row)
    return grouped


def _rows_by_instance(table, instance_ids, key):
    grouped = {}
    for row in _fetch_rows_in(table, 'review_instance_id', instance_ids):
        grouped.setdefault(row.get('review_instance_id'), {})[row.get(key)] = row
    return grouped


def review_instance_signing_definitions(instance_rows):
    """{instance id: the signing-relevant slice of review_instance_form_definition}.

    Lists that show many Reviews only need who must sign, who has, and what the
    Review is called -- not answers, overrides, RAG history or PDF state. The
    full definition costs about eight queries per instance; this costs a fixed
    handful for any number of instances. The values it does return are computed
    exactly as review_instance_form_definition computes them.
    """
    instance_rows = [row for row in instance_rows if row.get('id')]
    if not instance_rows:
        return {}
    instance_ids = [row.get('id') for row in instance_rows]
    answers = _rows_by_instance(REVIEW_INSTANCE_ANSWERS_TABLE, instance_ids, 'field_id')
    signatures = _rows_by_instance(REVIEW_INSTANCE_SIGNATURES_TABLE, instance_ids, 'role')
    templates = {
        row.get('id'): row
        for row in _fetch_rows_in(
            reviews.REVIEW_TEMPLATES_TABLE, 'id', [row.get('review_template_id') for row in instance_rows],
        )
    }
    type_index = review_types.review_type_index()

    # Only an editable Review nobody has signed follows the live, non-deleted
    # form (see effective_review_definition_snapshot), so only those templates'
    # current sections are read -- for all of them in two queries, not two each.
    def follows_live_form(instance_row):
        template = templates.get(instance_row.get('review_template_id'))
        return (
            instance_row.get('status') in EDITABLE_DEFINITION_STATUSES
            and not any(row.get('signed_at') for row in signatures.get(instance_row.get('id'), {}).values())
            and template is not None and not template.get('deleted_at')
        )

    live_template_ids = [row.get('review_template_id') for row in instance_rows if follows_live_form(row)]
    section_rows, field_rows = defaultdict(list), defaultdict(list)
    for row in _fetch_rows_in(reviews.REVIEW_SECTIONS_TABLE, 'review_id', live_template_ids,
                              where='deleted_at is null', order_by='display_order asc'):
        section_rows[row.get('review_id')].append(row)
    for row in _fetch_rows_in(reviews.REVIEW_FIELDS_TABLE, 'review_id', live_template_ids,
                              order_by='display_order asc'):
        field_rows[row.get('review_id')].append(row)
    live_snapshots = {
        template_id: json_safe(reviews.review_template_detail_payload(
            templates[template_id],
            reviews.assemble_review_sections(
                template_id, section_rows=section_rows[template_id], field_rows=field_rows[template_id],
            ),
            type_index=type_index,
        ))
        for template_id in dict.fromkeys(live_template_ids)
    }

    definitions = {}
    for instance_row in instance_rows:
        instance_id = instance_row.get('id')
        answers_by_field = answers.get(instance_id, {})
        signatures_by_role = signatures.get(instance_id, {})
        stored_snapshot = curriculum_views.as_json_value(instance_row.get('definition_snapshot'), {})
        live_template = templates.get(instance_row.get('review_template_id'))
        snapshot = effective_review_definition_snapshot(
            instance_row,
            snapshot=stored_snapshot,
            live_snapshot=live_snapshots.get(instance_row.get('review_template_id'), {}),
            answers_by_field=answers_by_field,
            signatures_by_role=signatures_by_role,
        )
        type_row = type_index.get((live_template or {}).get('review_type_id')) if live_template else None
        definitions[instance_id] = {
            'template': {
                'name': (live_template or {}).get('name') or stored_snapshot.get('name') or '',
                'reviewTypeCode': snapshot.get('reviewTypeCode') or (type_row or {}).get('code'),
                'visibleTo': snapshot.get('visibleTo', {}),
            },
            'sections': snapshot.get('sections', []),
            'signatures': _signature_payload(snapshot, signatures_by_role),
        }
    return definitions


def _signature_payload(snapshot, signatures_by_role):
    required_roles = set(required_signature_roles(snapshot.get('signatures', {})))
    return {
        role: {
            'required': role in required_roles,
            'signed': bool(signatures_by_role.get(role, {}).get('signed_at')),
            'signedBy': signatures_by_role.get(role, {}).get('signed_by'),
            'signedName': signatures_by_role.get(role, {}).get('signed_name'),
            'signedAt': curriculum_views.format_created_at(signatures_by_role.get(role, {}).get('signed_at')),
            # Return the recorded mark as well as the sign-off metadata.
            # Old sign-offs may contain a non-image acknowledgement; clients
            # must only render supported image data URIs, never infer a mark.
            'signature': (
                signatures_by_role.get(role, {}).get('signature') or None
                if signatures_by_role.get(role, {}).get('signed_at') else None
            ),
        }
        for role in SIGNATURE_ROLES
    }


def _flatten_snapshot_fields(sections):
    """Every field in a definition_snapshot's sections tree, conditional
    children included -- unlike reviews.flatten_fields (which is a
    top-level-only convenience view), a saved answer/validation pass needs to
    see every field, at any nesting depth."""
    flat = []

    def _walk(fields):
        for field in fields or []:
            flat.append(field)
            _walk(field.get('yesFields'))
            _walk(field.get('noFields'))

    for section in sections or []:
        _walk(section.get('fields'))
    return flat


def semantic_review_fields(definition, semantic_key):
    """Fields carrying one stable semantic marker, at any nesting depth.

    ``definition`` may be either a frozen ``definition_snapshot`` or the
    serialized form definition built from it. Runtime consumers deliberately
    do not fall back to editable titles such as "Summary".
    """
    semantic_key = curriculum_views.clean_str(semantic_key)
    if not semantic_key:
        return []
    return [
        field
        for field in _flatten_snapshot_fields((definition or {}).get('sections', []))
        if curriculum_views.clean_str((field.get('configuration') or {}).get('semanticKey')) == semantic_key
    ]


def meeting_summary_field(definition):
    """Return the single explicitly mapped formal Review summary field.

    Multiple markers are ambiguous and therefore behave like no mapping.
    Template validation prevents new duplicates; this defensive rule keeps
    existing persisted definitions from being guessed at runtime.
    """
    fields = semantic_review_fields(definition, reviews.MEETING_SUMMARY_SEMANTIC_KEY)
    return fields[0] if len(fields) == 1 else None


def _field_index(sections):
    return {
        field.get('id'): field
        for field in _flatten_snapshot_fields(sections)
        if field.get('id')
    }


def _field_tree_has_answer(field, answered_field_ids):
    if field.get('id') in answered_field_ids:
        return True
    return any(
        _field_tree_has_answer(child, answered_field_ids)
        for branch in ('yesFields', 'noFields')
        for child in field.get(branch, []) or []
    )


def _retained_removed_field(field, *, live_field_ids, answered_field_ids):
    """Keep only answered legacy branches removed from the live template.

    This is the fail-safe half of the merge: deleting an unanswered question
    from Curriculum removes it from an editable Review, while deleting one
    that already owns an answer never makes that answer disappear from the
    form/PDF. Fields moved elsewhere in the live tree are not duplicated.
    """
    if field.get('id') in live_field_ids:
        return None
    retained = deepcopy(field)
    has_answer = field.get('id') in answered_field_ids
    for branch in ('yesFields', 'noFields'):
        children = [
            kept
            for child in field.get(branch, []) or []
            if (kept := _retained_removed_field(
                child,
                live_field_ids=live_field_ids,
                answered_field_ids=answered_field_ids,
            )) is not None
        ]
        if branch in retained or children:
            retained[branch] = children
        has_answer = has_answer or bool(children)
    return retained if has_answer else None


def _merge_answer_safe_fields(
    old_fields, live_fields, *, old_field_index, live_field_ids, answered_field_ids,
):
    merged = []
    for live_field in live_fields or []:
        field_id = live_field.get('id')
        old_field = old_field_index.get(field_id)
        if not old_field or not _field_tree_has_answer(old_field, answered_field_ids):
            merged.append(deepcopy(live_field))
            continue

        # An answer keeps the old field's data contract (type, required rule,
        # options and semantic configuration). Presentation text/order may
        # safely follow Curriculum, and new conditional children are merged in
        # without discarding answered legacy children.
        field = deepcopy(old_field)
        for key in ('title', 'displayOrder'):
            if key in live_field:
                field[key] = deepcopy(live_field[key])
        for branch in ('yesFields', 'noFields'):
            if branch in old_field or branch in live_field:
                field[branch] = _merge_answer_safe_fields(
                    old_field.get(branch, []),
                    live_field.get(branch, []),
                    old_field_index=old_field_index,
                    live_field_ids=live_field_ids,
                    answered_field_ids=answered_field_ids,
                )
        merged.append(field)

    for old_field in old_fields or []:
        retained = _retained_removed_field(
            old_field,
            live_field_ids=live_field_ids,
            answered_field_ids=answered_field_ids,
        )
        if retained is not None:
            merged.append(retained)
    return merged


def merge_editable_definition_sections(old_sections, live_sections, answered_field_ids):
    """Return the live form tree without losing any already-answered field.

    With no answer rows the result is exactly the current Curriculum section
    tree. Once answers exist, new/unanswered fields follow Curriculum while
    answered fields keep their old structural contract. Removed answered
    fields/sections remain visible; removed unanswered ones do not.
    """
    answered_field_ids = set(answered_field_ids or ())
    if not answered_field_ids:
        return deepcopy(live_sections or [])

    old_sections = old_sections or []
    live_sections = live_sections or []
    old_sections_by_id = {
        section.get('id'): section for section in old_sections if section.get('id')
    }
    old_field_index = _field_index(old_sections)
    live_field_ids = set(_field_index(live_sections))
    live_section_ids = {section.get('id') for section in live_sections if section.get('id')}
    merged = []

    for live_section in live_sections:
        old_section = old_sections_by_id.get(live_section.get('id'))
        old_section_has_answer = bool(old_section) and any(
            _field_tree_has_answer(field, answered_field_ids)
            for field in old_section.get('fields', []) or []
        )
        section = deepcopy(old_section if old_section_has_answer else live_section)
        if old_section_has_answer:
            for key in ('title', 'estimatedMinutes', 'displayOrder'):
                if key in live_section:
                    section[key] = deepcopy(live_section[key])
        section['fields'] = _merge_answer_safe_fields(
            old_section.get('fields', []) if old_section else [],
            live_section.get('fields', []),
            old_field_index=old_field_index,
            live_field_ids=live_field_ids,
            answered_field_ids=answered_field_ids,
        )
        merged.append(section)

    for old_section in old_sections:
        if old_section.get('id') in live_section_ids:
            continue
        retained_fields = [
            retained
            for field in old_section.get('fields', []) or []
            if (retained := _retained_removed_field(
                field,
                live_field_ids=live_field_ids,
                answered_field_ids=answered_field_ids,
            )) is not None
        ]
        if retained_fields:
            section = deepcopy(old_section)
            section['fields'] = retained_fields
            merged.append(section)
    return merged


def effective_review_definition_snapshot(
    instance_row, *, snapshot=None, live_snapshot=None,
    answers_by_field=None, signatures_by_role=None,
):
    """Resolve the section tree an instance may safely present right now.

    Passive callers receive the effective snapshot in memory. They never
    write it. Draft-save/completion persist the same value while holding the
    Review Instance lock, closing the race with signature/completion.
    """
    snapshot = deepcopy(
        curriculum_views.as_json_value(instance_row.get('definition_snapshot'), {})
        if snapshot is None else snapshot
    )
    if instance_row.get('status') not in EDITABLE_DEFINITION_STATUSES:
        return snapshot

    instance_id = instance_row.get('id')
    answers_by_field = (
        get_review_instance_answers(instance_id)
        if answers_by_field is None else answers_by_field
    )
    signatures_by_role = (
        get_review_instance_signatures(instance_id)
        if signatures_by_role is None else signatures_by_role
    )
    if any(row.get('signed_at') for row in signatures_by_role.values()):
        return snapshot

    if live_snapshot is None:
        live_template = reviews.get_review_template_row(instance_row.get('review_template_id'))
        if not live_template:
            return snapshot
        live_snapshot = build_definition_snapshot(live_template)
    if not live_snapshot:
        return snapshot

    snapshot['sections'] = merge_editable_definition_sections(
        snapshot.get('sections', []),
        live_snapshot.get('sections', []),
        answers_by_field.keys(),
    )
    snapshot['fieldCount'] = len(_flatten_snapshot_fields(snapshot['sections']))
    return snapshot


def review_instance_form_definition(instance_row):
    """The hierarchical {instance, template, sections[].fields[].answer} shape
    a dynamic form renderer consumes. Sections/fields/signature-and-visibility
    rules come from the effective definition_snapshot policy above; terminal
    Reviews remain frozen while editable Reviews follow safe Curriculum form
    updates. The display title/status/target date are read live."""
    stored_snapshot = curriculum_views.as_json_value(instance_row.get('definition_snapshot'), {})
    live_template = reviews.get_review_template_row(instance_row.get('review_template_id'), include_deleted=True)
    live_name = (live_template or {}).get('name') or stored_snapshot.get('name') or ''
    type_row = next((row for row in review_types.review_type_index().values()
                     if row.get('id') == (live_template or {}).get('review_type_id')), None)

    answers_by_field = get_review_instance_answers(instance_row.get('id'))
    signatures_by_role = get_review_instance_signatures(instance_row.get('id'))
    snapshot = effective_review_definition_snapshot(
        instance_row,
        snapshot=stored_snapshot,
        answers_by_field=answers_by_field,
        signatures_by_role=signatures_by_role,
    )

    def _attach_answers(fields):
        decorated = []
        for field in fields or []:
            field = dict(field)
            saved = answers_by_field.get(field.get('id'))
            field['answer'] = curriculum_views.as_json_value(saved.get('answer'), None) if saved else None
            field['answeredBy'] = saved.get('answered_by') if saved else None
            field['answeredAt'] = curriculum_views.format_created_at(saved.get('answered_at')) if saved else None
            if field.get('fieldType') == reviews.CONDITIONAL_FIELD_TYPE:
                field['yesFields'] = _attach_answers(field.get('yesFields'))
                field['noFields'] = _attach_answers(field.get('noFields'))
            decorated.append(field)
        return decorated

    sections = [
        {**section, 'fields': _attach_answers(section.get('fields'))}
        for section in snapshot.get('sections', [])
    ]

    result = {
        'instance': {
            'id': instance_row.get('id'),
            'reviewTemplateId': instance_row.get('review_template_id'),
            'learnerId': instance_row.get('learner_id'),
            'programmeId': instance_row.get('programme_id'),
            'occurrenceNumber': instance_row.get('occurrence_number'),
            'targetDate': curriculum_views.format_date(instance_row.get('target_date')),
            'status': instance_row.get('status'),
            'startedAt': curriculum_views.format_created_at(instance_row.get('started_at')),
            'completedAt': curriculum_views.format_created_at(instance_row.get('completed_at')),
        },
        'template': {
            'id': instance_row.get('review_template_id'),
            'name': live_name,
            # Classification is part of the frozen definition. Only legacy
            # snapshots that predate Review Types fall back to the live row;
            # the display title above deliberately remains live.
            'reviewTypeId': snapshot.get('reviewTypeId') or (live_template or {}).get('review_type_id'),
            'reviewTypeCode': snapshot.get('reviewTypeCode') or (type_row or {}).get('code'),
            'reviewTypeName': snapshot.get('reviewTypeName') or (type_row or {}).get('name'),
            'signatures': snapshot.get('signatures', {}),
            'visibleTo': snapshot.get('visibleTo', {}),
            'recurrence': snapshot.get('recurrence', {}),
            'notifications': snapshot.get('notifications', {}),
            'allowEditingPriorDays': snapshot.get('allowEditingPriorDays', 0),
        },
        'sections': sections,
        'signatures': _signature_payload(snapshot, signatures_by_role),
        # None for the overwhelming majority of instances -- only ever set
        # for one that was moved to in-progress by an authorised manual
        # override rather than real Teams attendance (Phase 5).
        'manualOverride': _serialize_manual_override(latest_review_instance_manual_override(instance_row.get('id'))),
        # What a coach last calculated for THIS instance, exactly as it was
        # stored -- never recomputed while reading, so an instance that was
        # never calculated reads as None and a signed one keeps its figures.
        'progressSnapshot': review_instance_progress_snapshot(instance_row),
    }
    if result['template']['reviewTypeCode'] == review_types.REVIEW_TYPE_CODE_PROGRESS_REVIEW:
        # Only this Review Type presents a RAG history, and only from the
        # learner's own completed Progress Reviews -- each row showing the RAG
        # that review itself recorded, never the learner's current coach_rag.
        result['ragHistory'] = progress_review_rag_history(instance_row.get('learner_id'))
    from .review_pdf import pdf_availability
    result['pdf'] = pdf_availability(result)
    return result


def _serialize_manual_override(row):
    if not row:
        return None
    return {
        'reasonCode': row.get('reason_code'),
        'note': row.get('note') or '',
        'changedBy': row.get('changed_by'),
        'changedAt': curriculum_views.format_created_at(row.get('changed_at')),
        'manualStartedAt': curriculum_views.format_created_at(row.get('manual_started_at')),
    }


def _visible_required_unanswered_fields(sections, answers_by_field):
    """Visible (per the boolean_case_block condition its parent answered)
    required fields with no answer yet -- a hidden conditional field can
    never block completion, whatever it was answered before being hidden."""
    missing = []

    def _walk(fields, visible=True):
        for field in fields or []:
            field_visible = visible
            if field.get('fieldType') in reviews.DISPLAY_ONLY_FIELD_TYPES:
                pass
            elif field_visible and field.get('required'):
                saved = answers_by_field.get(field.get('id'))
                answer = curriculum_views.as_json_value(saved.get('answer'), None) if saved else None
                if answer in (None, '', []):
                    missing.append(field.get('id'))

            if field.get('fieldType') == reviews.CONDITIONAL_FIELD_TYPE:
                saved = answers_by_field.get(field.get('id'))
                answer = curriculum_views.as_json_value(saved.get('answer'), None) if saved else None
                _walk(field.get('yesFields'), visible=field_visible and answer == 'yes')
                _walk(field.get('noFields'), visible=field_visible and answer == 'no')

    _walk(sections and [f for s in sections for f in s.get('fields', [])])
    return missing


def _refresh_locked_editable_definition_snapshot(instance_row, *, actor):
    """Persist the effective editable form while the instance lock is held."""
    stored = curriculum_views.as_json_value(instance_row.get('definition_snapshot'), {})
    effective = effective_review_definition_snapshot(instance_row, snapshot=stored)
    if effective == stored:
        return instance_row

    updated = curriculum_views.update_rows(
        REVIEW_INSTANCES_TABLE,
        'id = %s and status = %s',
        [instance_row.get('id'), instance_row.get('status')],
        {
            'definition_snapshot': curriculum_views.json_db_value(effective),
            'updated_by': actor,
            'updated_at': datetime.utcnow(),
        },
    )
    if not updated:
        raise ValueError('This review changed while its Curriculum form was being refreshed. Reload it and try again.')
    refreshed = dict(instance_row)
    refreshed['definition_snapshot'] = effective
    refreshed['updated_by'] = actor
    return refreshed


def writable_field_ids_for_role(sections, role):
    """Field ids in this frozen definition whose configuration opts a
    non-advisor ``role`` ('participant' or 'employer') into answering them.

    The advisor (coach) is never checked against this list -- coach_api's
    answer endpoint always allows every field. This is only for the Learner
    and Employer surfaces, which share the same ReviewFormRenderer but must
    stay read-only outside whatever the Curriculum template opted them into.
    """
    return {
        field.get('id')
        for field in _flatten_snapshot_fields(sections)
        if role in ((field.get('configuration') or {}).get('respondentRoles') or [])
    }


def save_review_instance_answers_for_role(instance_row, answers, role, *, actor='system'):
    """Like ``save_review_instance_answers``, restricted to the fields this
    respondent role (participant/employer) is allowed to answer.

    Fails closed: posting even one field id outside the role's allowed set
    raises rather than silently dropping it, so a stale or tampered payload
    is rejected instead of partially applied.
    """
    definition = review_instance_form_definition(instance_row)
    allowed_ids = writable_field_ids_for_role(definition.get('sections', []), role)
    posted_ids = set((answers or {}).keys())
    disallowed = posted_ids - allowed_ids
    if disallowed:
        raise PermissionError(
            f"Not authorised to answer: {', '.join(sorted(disallowed))}."
        )
    return save_review_instance_answers(instance_row, answers, actor=actor)


@native_write
def save_review_instance_answers(instance_row, answers, *, actor='system'):
    """Draft save -- merges posted {fieldId: value} into whatever is already
    stored. A conditional field hidden by its parent's current answer is
    still saved as posted (its previous answer is not corrupted/dropped just
    because it is not visible right now)."""
    if instance_row.get('status') in (STATUS_AWAITING_SIGNATURE, STATUS_COMPLETED):
        raise ValueError('Submitted review answers cannot be changed after the signature step begins.')

    with transaction.atomic():
        # Answer writers and completion lock this same parent first. A stale
        # save that started before submission therefore re-reads the terminal
        # status and is rejected instead of changing signed content.
        locked = get_review_instance(instance_row.get('id'), for_update=True)
        if not locked:
            raise ValueError('Review not found.')
        if locked.get('status') in (STATUS_AWAITING_SIGNATURE, STATUS_COMPLETED):
            raise ValueError('Submitted review answers cannot be changed after the signature step begins.')
        _save_review_instance_answers_locked(locked, answers, actor=actor)

        # Saving a draft answer is not evidence the meeting started -- it used
        # to flip status to in-progress here, which is exactly the "opened/
        # edited the form" trigger Phase 2 removes. The real trigger is a
        # confirmed Microsoft Teams attendance signal; see
        # mark_review_instance_in_progress_from_attendance and
        # coach_api.views.apply_teams_attendance_status_transition.

    return review_instance_form_definition(get_review_instance(instance_row.get('id')))


def _save_review_instance_answers_locked(instance_row, answers, *, actor):
    """Merge answer values while the caller holds the instance row lock."""
    instance_row = _refresh_locked_editable_definition_snapshot(instance_row, actor=actor)
    snapshot = curriculum_views.as_json_value(instance_row.get('definition_snapshot'), {})
    valid_field_ids = {
        field.get('id')
        for field in _flatten_snapshot_fields(snapshot.get('sections', []))
    }
    for field_id, value in (answers or {}).items():
        if field_id not in valid_field_ids:
            continue
        existing_rows = curriculum_views.fetch_all(
            f'select * from {curriculum_views.table_name(REVIEW_INSTANCE_ANSWERS_TABLE)} '
            f'where review_instance_id = %s and field_id = %s',
            [instance_row.get('id'), field_id],
        )
        payload = {
            'answer': curriculum_views.json_db_value(value),
            'answered_by': actor,
            'answered_at': datetime.utcnow(),
            'updated_at': datetime.utcnow(),
        }
        if existing_rows:
            curriculum_views.update_rows(
                REVIEW_INSTANCE_ANSWERS_TABLE, 'id = %s', [existing_rows[0]['id']], payload,
                allow_null_columns=['answer'],
            )
        else:
            curriculum_views.insert_row(REVIEW_INSTANCE_ANSWERS_TABLE, {
                'id': curriculum_views.unique_prefixed_id('REVIA'),
                'review_instance_id': instance_row.get('id'),
                'field_id': field_id,
                'created_at': datetime.utcnow(),
                **payload,
            })
    return instance_row


# ------------------------------------------------- progress + RAG snapshots

#: The instance's own frozen copy of what a coach calculated (see
#: learner_api.review_progress_snapshot for what goes in it). Kept beside
#: definition_snapshot rather than inside it: that one freezes the QUESTION
#: SET at creation time and is written exactly once, this one is written when
#: a coach presses Calculate and may be replaced until signing begins.
PROGRESS_SNAPSHOT_COLUMN = 'progress_snapshot'
LEGACY_PROGRESS_SNAPSHOT_SCHEMA_VERSION = 1
LEGACY_PROGRESS_SNAPSHOT_FORMULA_VERSION = 'legacy_unversioned'

#: How a Curriculum-authored question declares itself to BE the RAG question,
#: so the RAG value is found by the template's own stable marker rather than
#: by matching a question's title. Set in the field's configuration, frozen
#: per instance with the rest of the definition, so a historical review is
#: always read through the marker IT was created with.
RAG_SEMANTIC_KEY = 'rag_status'


def review_instance_progress_snapshot(instance_row):
    """What was frozen the last time a coach pressed Calculate, or None.

    Never recalculated on read: an instance with no snapshot reads as None so
    the caller can say "not calculated yet", rather than quietly substituting
    the learner's current figures into a historical review.
    """
    snapshot = curriculum_views.as_json_value(instance_row.get(PROGRESS_SNAPSHOT_COLUMN), None)
    if not isinstance(snapshot, dict):
        return snapshot
    # Interpret old stored JSON explicitly without rewriting it or pretending it
    # used the current formula. Existing UI/PDF readers ignore these additive
    # fields and continue to render the frozen values unchanged.
    return {
        **snapshot,
        'schemaVersion': snapshot.get('schemaVersion', LEGACY_PROGRESS_SNAPSHOT_SCHEMA_VERSION),
        'formulaVersion': snapshot.get('formulaVersion', LEGACY_PROGRESS_SNAPSHOT_FORMULA_VERSION),
    }


@native_write
def save_review_instance_progress_snapshot(instance_row, snapshot, *, actor='system'):
    """Freeze (or replace) this instance's calculated progress.

    Same lifecycle rule the answers themselves follow: once the instance has
    reached awaiting-signature/completed, the figures a party is about to sign
    -- or has already signed -- can no longer move underneath them. Guarded in
    the UPDATE's own WHERE clause as well as checked up front, so a Calculate
    racing a completion cannot slip in after the transition.
    """
    if instance_row.get('status') in (STATUS_AWAITING_SIGNATURE, STATUS_COMPLETED):
        raise ValueError('Progress cannot be recalculated after the signature step begins.')
    if PROGRESS_SNAPSHOT_COLUMN not in curriculum_views.column_names(REVIEW_INSTANCES_TABLE):
        # filtered_payload would otherwise drop the snapshot silently and the
        # write would look like it succeeded. Fail loudly instead, naming the
        # migration that adds the column.
        raise ValueError(
            'This deployment cannot store a Progress Review snapshot yet: run '
            'sql/2026-09-16_curriculum_review_instance_progress_snapshot.sql.'
        )

    rows = curriculum_views.update_rows(
        REVIEW_INSTANCES_TABLE,
        'id = %s and status not in (%s, %s)',
        [instance_row.get('id'), STATUS_AWAITING_SIGNATURE, STATUS_COMPLETED],
        {
            PROGRESS_SNAPSHOT_COLUMN: curriculum_views.json_db_value(json_safe(snapshot)),
            'updated_by': actor,
            'updated_at': datetime.utcnow(),
        },
    )
    if not rows:
        raise ValueError('Progress cannot be recalculated after the signature step begins.')
    return rows[0]


def _rag_field_ids(snapshot):
    return [
        field.get('id')
        for field in _flatten_snapshot_fields(snapshot.get('sections', []))
        if (field.get('configuration') or {}).get('semanticKey') == RAG_SEMANTIC_KEY
    ]


def review_instance_rag_value(instance_row):
    """This instance's own recorded RAG answer, or ''.

    Read from the answer saved against this instance, located through the
    RAG marker in the definition THIS instance froze -- never from the
    learner's current/live coach_rag, which a coach can change at any time and
    which would otherwise rewrite the history of an already-signed review.
    """
    snapshot = curriculum_views.as_json_value(instance_row.get('definition_snapshot'), {})
    field_ids = _rag_field_ids(snapshot)
    if not field_ids:
        return ''
    answers = get_review_instance_answers(instance_row.get('id'))
    for field_id in field_ids:
        saved = answers.get(field_id)
        value = curriculum_views.as_json_value(saved.get('answer'), None) if saved else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


def list_learner_review_instances(learner_id, review_type_code, *, statuses=(STATUS_COMPLETED,)):
    """This learner's own instances of one Review Type, newest target date
    first. Scoped by the Review Type's stable code -- never by template name --
    so a Progress Review history can never pick up a Monthly Coaching Meeting
    or any other Review the programme happens to run."""
    ensure_review_instance_tables()
    type_row = review_types.get_review_type_by_code(review_type_code)
    if not type_row or not learner_id:
        return []
    placeholders = ', '.join(['%s'] * len(statuses)) if statuses else ''
    status_clause = f' and i.status in ({placeholders})' if statuses else ''
    return curriculum_views.fetch_all(
        f'select i.* from {curriculum_views.table_name(REVIEW_INSTANCES_TABLE)} i '
        f'join {curriculum_views.table_name(reviews.REVIEW_TEMPLATES_TABLE)} t '
        f'on t.id = i.review_template_id '
        f'where i.learner_id = %s and t.review_type_id = %s{status_clause} '
        f'order by i.target_date desc, i.occurrence_number desc',
        [learner_id, type_row.get('id'), *statuses],
    )


def progress_review_rag_history(learner_id, *, limit=8):
    """Completed Progress Reviews for this learner, newest first, each with the
    RAG answer IT recorded.

    A review that never captured a RAG value contributes an empty rag rather
    than being dropped, so the history shows the occurrence took place -- the
    same way the legacy export shows an entry with no RAG against it.
    """
    history = []
    for row in list_learner_review_instances(learner_id, review_types.REVIEW_TYPE_CODE_PROGRESS_REVIEW):
        template = reviews.get_review_template_row(row.get('review_template_id'), include_deleted=True)
        history.append({
            'reviewInstanceId': row.get('id'),
            'reviewName': (template or {}).get('name') or '',
            'occurrenceNumber': row.get('occurrence_number'),
            'targetDate': curriculum_views.format_date(row.get('target_date')),
            'completedAt': curriculum_views.format_created_at(row.get('completed_at')),
            'rag': review_instance_rag_value(row),
        })
        if len(history) >= limit:
            break
    return history


def _all_required_signatures_present(instance_id, snapshot):
    required_roles = required_signature_roles(snapshot.get('signatures', {}))
    if not required_roles:
        return True
    signatures_by_role = get_review_instance_signatures(instance_id)
    return all(signatures_by_role.get(role, {}).get('signed_at') for role in required_roles)



def _locked_linked_calendar_for_mirror(instance_row):
    """Lock this instance's linked Calendar row and prove it is really the same
    review, or raise.

    Shared by every mirror that projects an instance-owned status onto Calendar
    (_mirror_linked_calendar_after_signature and
    _mirror_linked_calendar_after_reopen): identity, learner, template and
    coach must all still agree before either is allowed to write. Returns the
    locked CoachCalendarEvent, or None when this instance has no linked
    calendar row at all -- a legitimate, silent no-op for both callers.
    """
    calendar_event_id = instance_row.get('calendar_event_id')
    if not calendar_event_id:
        return None

    # Import lazily: coach_api.views imports this module at application start.
    from coach_api.models import CoachCalendarEvent

    calendar = CoachCalendarEvent.objects.select_for_update().filter(
        pk=calendar_event_id,
    ).first()
    if calendar is None:
        raise ValueError(
            'The linked Calendar event is missing; review reconciliation is required.'
        )

    instance_id = curriculum_views.clean_str(instance_row.get('id'))
    if curriculum_views.clean_str(calendar.review_instance_id) != instance_id:
        raise ValueError(
            'The linked Calendar event does not point back to this Review Instance; '
            'review reconciliation is required.'
        )
    if str(calendar.learner_id) != str(instance_row.get('learner_id')):
        raise ValueError(
            'The linked Calendar event belongs to a different learner; '
            'review reconciliation is required.'
        )
    template_id = curriculum_views.clean_str(instance_row.get('review_template_id'))
    calendar_template_id = curriculum_views.clean_str(calendar.review_template_id)
    if template_id and calendar_template_id and template_id != calendar_template_id:
        raise ValueError(
            'The linked Calendar event has a different Review template; '
            'review reconciliation is required.'
        )
    coach_email = curriculum_views.clean_str(instance_row.get('coach_email')).lower()
    owner_email = curriculum_views.clean_str(calendar.owner_email).lower()
    if coach_email and owner_email and coach_email != owner_email:
        raise ValueError(
            'The linked Calendar event has a different coach; '
            'review reconciliation is required.'
        )
    return calendar


def _mirror_linked_calendar_after_signature(instance_row, *, status, completed_at=None):
    """Project a signature-owned status onto its linked Calendar row atomically."""
    from coach_api.models import CoachCalendarEvent

    calendar = _locked_linked_calendar_for_mirror(instance_row)
    if calendar is None:
        return

    if status == STATUS_AWAITING_SIGNATURE:
        allowed = {
            CoachCalendarEvent.STATUS_IN_PROGRESS,
            CoachCalendarEvent.STATUS_AWAITING_SIGNATURE,
        }
        if calendar.status not in allowed:
            raise ValueError(
                'The linked Calendar event cannot enter awaiting-signature from its '
                f'current status ({calendar.status}).'
            )
        if calendar.status != CoachCalendarEvent.STATUS_AWAITING_SIGNATURE:
            calendar.status = CoachCalendarEvent.STATUS_AWAITING_SIGNATURE
            calendar.save(update_fields=['status', 'updated_at'])
        return

    if status == STATUS_COMPLETED:
        allowed = {
            CoachCalendarEvent.STATUS_IN_PROGRESS,
            CoachCalendarEvent.STATUS_AWAITING_SIGNATURE,
            CoachCalendarEvent.STATUS_COMPLETED,
        }
        if calendar.status not in allowed:
            raise ValueError(
                'The linked Calendar event cannot enter completed from its '
                f'current status ({calendar.status}).'
            )
        calendar.status = CoachCalendarEvent.STATUS_COMPLETED
        calendar.review_completed_at = completed_at or datetime.utcnow()
        calendar.save(update_fields=['status', 'review_completed_at', 'updated_at'])


@native_write
def record_review_instance_signature(instance_row, role, *, signed_by, signed_name, signature, actor='system'):
    """Record one party's sign-off and mirror its lifecycle status atomically."""
    if role not in SIGNATURE_ROLES:
        raise ValueError(f'Unknown signature role "{role}".')
    snapshot = curriculum_views.as_json_value(instance_row.get('definition_snapshot'), {})
    if role not in required_signature_roles(snapshot.get('signatures', {})):
        raise ValueError(f'This review does not require a "{role}" signature.')
    if instance_row.get('status') not in (STATUS_AWAITING_SIGNATURE, STATUS_COMPLETED):
        raise ValueError('This review must be finished before it can be signed.')

    with transaction.atomic():
        existing_rows = curriculum_views.fetch_all(
            f'select * from {curriculum_views.table_name(REVIEW_INSTANCE_SIGNATURES_TABLE)} '
            f'where review_instance_id = %s and role = %s',
            [instance_row.get('id'), role],
        )
        signed_at = datetime.utcnow() if signature else None
        payload = {
            'signed_by': signed_by or '', 'signed_name': signed_name or '',
            'signature': signature or '', 'signed_at': signed_at,
            'updated_at': datetime.utcnow(),
        }
        if existing_rows:
            curriculum_views.update_rows(
                REVIEW_INSTANCE_SIGNATURES_TABLE, 'id = %s', [existing_rows[0]['id']], payload,
                allow_null_columns=['signed_at'],
            )
        else:
            curriculum_views.insert_row(REVIEW_INSTANCE_SIGNATURES_TABLE, {
                'id': curriculum_views.unique_prefixed_id('REVIS'),
                'review_instance_id': instance_row.get('id'),
                'role': role,
                'created_at': datetime.utcnow(),
                **payload,
            })

        current_status = instance_row.get('status')
        if _all_required_signatures_present(instance_row.get('id'), snapshot):
            completed_at = datetime.utcnow()
            if current_status == STATUS_AWAITING_SIGNATURE:
                updated = curriculum_views.update_rows(
                    REVIEW_INSTANCES_TABLE,
                    'id = %s and status = %s',
                    [instance_row.get('id'), STATUS_AWAITING_SIGNATURE],
                    {
                        'status': STATUS_COMPLETED, 'completed_at': completed_at,
                        'updated_by': actor, 'updated_at': completed_at,
                    },
                )
                if not updated:
                    raise ValueError(
                        'This review status changed while the signature was being recorded. '
                        'Reload it and try again.'
                    )
                completed_at = updated[0].get('completed_at') or completed_at
            elif current_status != STATUS_COMPLETED:
                raise ValueError('This review must be awaiting signature before it can be completed.')
            _mirror_linked_calendar_after_signature(
                instance_row, status=STATUS_COMPLETED, completed_at=completed_at,
            )
        else:
            # A completed instance is terminal even if a legacy/concurrent
            # signature row is being backfilled. Keep its Calendar projection
            # completed instead of trying to regress it to awaiting-signature.
            if current_status == STATUS_COMPLETED:
                _mirror_linked_calendar_after_signature(
                    instance_row,
                    status=STATUS_COMPLETED,
                    completed_at=instance_row.get('completed_at'),
                )
            else:
                _mirror_linked_calendar_after_signature(
                    instance_row, status=STATUS_AWAITING_SIGNATURE,
                )

    return review_instance_form_definition(get_review_instance(instance_row.get('id')))


def _complete_review_instance_status_rejection(current_status):
    if current_status in (STATUS_NOT_SCHEDULED, STATUS_SCHEDULED):
        return 'This review cannot be completed until attendance has been confirmed for the scheduled Teams meeting.'
    if current_status == STATUS_AWAITING_SIGNATURE:
        return 'This review has already been submitted and is awaiting signature.'
    if current_status == STATUS_COMPLETED:
        return 'This review has already been completed.'
    return 'This review cannot be completed from its current status.'


@native_write
def complete_review_instance(instance_row, *, actor='system', answers=None):
    """Finishes the form: validates every visible required field is
    answered, then moves the instance to Awaiting Signature (if the
    Curriculum template requires any signature) or straight to Completed (if
    it requires none). Returns (ok, errors).

    The only valid transition is in-progress -> awaiting-signature/completed
    (Phase 3). Completion is not a client-side button-visibility rule: this
    is the backend enforcement, so an API call cannot skip attendance by
    calling this directly. The DB write itself is guarded on the instance
    still being in-progress (an atomic compare-and-swap, not a read-then-
    write), so a concurrent completion attempt or an out-of-order request
    can't silently move the same instance twice or race a legitimate
    transition -- ``errors`` names the reason as ``{'fields': [...]}`` for
    unmet field requirements (as before), or ``{'status': [...]}`` for an
    invalid status transition, so callers can tell the two apart.
    """
    current_status = instance_row.get('status')
    if current_status != STATUS_IN_PROGRESS:
        return False, {'status': [_complete_review_instance_status_rejection(current_status)]}

    with transaction.atomic():
        # This is the same first lock used by save_review_instance_answers.
        # Optional browser answers are saved, validated and frozen within this
        # one critical section, making Send for Signatures an atomic action.
        locked = get_review_instance(instance_row.get('id'), for_update=True)
        if not locked:
            return False, {'status': ['Review not found.']}
        current_status = locked.get('status')
        if current_status != STATUS_IN_PROGRESS:
            return False, {'status': [_complete_review_instance_status_rejection(current_status)]}
        if answers is not None:
            locked = _save_review_instance_answers_locked(locked, answers, actor=actor)
        else:
            locked = _refresh_locked_editable_definition_snapshot(locked, actor=actor)

        snapshot = curriculum_views.as_json_value(locked.get('definition_snapshot'), {})
        answers_by_field = get_review_instance_answers(locked.get('id'))
        missing_fields = _visible_required_unanswered_fields(snapshot.get('sections', []), answers_by_field)
        if missing_fields:
            return False, {'fields': missing_fields}

        requires_signature = bool(required_signature_roles(snapshot.get('signatures', {})))
        if requires_signature:
            updated = curriculum_views.update_rows(
                REVIEW_INSTANCES_TABLE, 'id = %s and status = %s',
                [locked.get('id'), STATUS_IN_PROGRESS],
                {'status': STATUS_AWAITING_SIGNATURE, 'updated_by': actor, 'updated_at': datetime.utcnow()},
            )
        else:
            updated = curriculum_views.update_rows(
                REVIEW_INSTANCES_TABLE, 'id = %s and status = %s',
                [locked.get('id'), STATUS_IN_PROGRESS],
                {
                    'status': STATUS_COMPLETED, 'completed_at': datetime.utcnow(),
                    'updated_by': actor, 'updated_at': datetime.utcnow(),
                },
            )
        if not updated:
            return False, {'status': ['This review is no longer in progress. Reload it and try again.']}
    return True, None


# ------------------------------------------------------------------ reopen

def _reopen_review_instance_status_rejection(current_status):
    """Why this instance cannot be reopened, phrased for the coach.

    Same shape and purpose as _complete_review_instance_status_rejection: the
    string is returned inside an ``errors`` dict, never raised.
    """
    if current_status == STATUS_NOT_SCHEDULED:
        return 'This review has not been scheduled yet.'
    if current_status == STATUS_SCHEDULED:
        return 'This review has not started yet, so there is nothing to reopen.'
    if current_status == STATUS_IN_PROGRESS:
        return 'This review is already in progress.'
    return 'This review cannot be reopened from its current status.'


def _reopen_state_snapshot(instance_row):
    """Freeze what this instance looked like immediately before a reopen.

    This is the ONLY surviving record of the signatures the reopen is about to
    clear -- _clear_review_instance_signatures blanks them in place -- so the
    full signature image (a base64 data URI) is captured here verbatim rather
    than hashed or summarised. Audit evidence only: nothing reads this back
    into the live review.
    """
    instance_id = instance_row.get('id')
    answers = get_review_instance_answers(instance_id)
    signatures = get_review_instance_signatures(instance_id)
    return json_safe({
        'capturedAt': datetime.utcnow(),
        'instance': {
            'status': instance_row.get('status'),
            'startedAt': instance_row.get('started_at'),
            'completedAt': instance_row.get('completed_at'),
        },
        'answers': [
            {
                'fieldId': field_id,
                'answer': curriculum_views.as_json_value(row.get('answer'), None),
                'answeredBy': row.get('answered_by') or '',
                'answeredAt': row.get('answered_at'),
            }
            for field_id, row in sorted(answers.items())
        ],
        'signatures': [
            {
                'role': role,
                'signedBy': row.get('signed_by') or '',
                'signedName': row.get('signed_name') or '',
                'signature': row.get('signature') or '',
                'signedAt': row.get('signed_at'),
            }
            for role, row in sorted(signatures.items())
        ],
    })


def record_review_instance_reopen(
    *, review_instance_id, calendar_event_id, previous_status, reason_code, note,
    changed_by, previous_state_snapshot,
):
    """Persist ONE row in curriculum.review_instance_reopens.

    Append-only, and ONLY ever called for a reopen that has already succeeded
    (see reopen_review_instance_for_editing, its sole caller). Never called for
    a manual scheduled -> in-progress override -- that is a different event
    with its own table (record_review_instance_manual_override), whose
    documented contract this deliberately leaves untouched.
    """
    ensure_review_instance_reopens_table()
    return curriculum_views.insert_row(REVIEW_INSTANCE_REOPENS_TABLE, {
        'id': curriculum_views.unique_prefixed_id('REVIRO'),
        'review_instance_id': review_instance_id,
        'calendar_event_id': calendar_event_id,
        'previous_status': previous_status,
        'new_status': STATUS_IN_PROGRESS,
        'reason_code': reason_code,
        'note': note,
        'changed_by': changed_by,
        'changed_at': datetime.utcnow(),
        'previous_state_snapshot': curriculum_views.json_db_value(previous_state_snapshot),
    })


def list_review_instance_reopens(review_instance_id):
    """This instance's reopen history, most recent first. Empty for every
    instance that has never been reopened (i.e. almost all of them)."""
    ensure_review_instance_reopens_table()
    return curriculum_views.fetch_all(
        f'select * from {curriculum_views.table_name(REVIEW_INSTANCE_REOPENS_TABLE)} '
        f'where review_instance_id = %s order by changed_at desc',
        [review_instance_id],
    )


def latest_review_instance_reopen(review_instance_id):
    """The most recent reopen for this instance, or None."""
    rows = list_review_instance_reopens(review_instance_id)
    return rows[0] if rows else None


def _clear_review_instance_signatures(instance_id):
    """Blank every signature row for this instance back to its unsigned shape.

    Rows are blanked, never deleted: signed_by/signed_name/signature to '' and
    signed_at to NULL is exactly what record_review_instance_signature writes
    for an unsigned submission, and _all_required_signatures_present tests
    ``signed_at`` truthiness -- so a blanked row correctly reads as "not yet
    signed" everywhere, with no row churn and no new primary keys.

    The values discarded here are preserved in the reopen's
    previous_state_snapshot (see _reopen_state_snapshot), written in the same
    transaction. Returns the number of rows blanked.
    """
    rows = curriculum_views.fetch_all(
        f'select id from {curriculum_views.table_name(REVIEW_INSTANCE_SIGNATURES_TABLE)} '
        f'where review_instance_id = %s',
        [instance_id],
    )
    for row in rows:
        curriculum_views.update_rows(
            REVIEW_INSTANCE_SIGNATURES_TABLE, 'id = %s', [row.get('id')],
            {
                'signed_by': '', 'signed_name': '', 'signature': '',
                'signed_at': None, 'updated_at': datetime.utcnow(),
            },
            # Without this, filtered_payload drops the None and signed_at keeps
            # its old value -- the row would still read as signed.
            allow_null_columns=['signed_at'],
        )
    return len(rows)


def _mirror_linked_calendar_after_reopen(instance_row):
    """Project a reopen back onto its linked Calendar row atomically.

    The inverse of _mirror_linked_calendar_after_signature's completed branch,
    reusing that function's identity/ownership validation wholesale via
    _locked_linked_calendar_for_mirror -- an instance may only move a Calendar
    row it still demonstrably owns.
    """
    from coach_api.models import CoachCalendarEvent

    calendar = _locked_linked_calendar_for_mirror(instance_row)
    if calendar is None:
        return

    # Strictly the two states a finished review's Calendar row is ever in. The
    # normal paths keep the two exactly in step -- coach_review_instance_complete
    # calls _sync_calendar_record_to_review_instance_status immediately after
    # completing, and the signature mirror advances it again -- so anything else
    # here means instance and Calendar have genuinely diverged. That is a
    # reconciliation problem, and this fails loudly rather than quietly
    # accepting the wider set and writing over it.
    allowed = {
        CoachCalendarEvent.STATUS_AWAITING_SIGNATURE,
        CoachCalendarEvent.STATUS_COMPLETED,
    }
    if calendar.status not in allowed:
        raise ValueError(
            'The linked Calendar event is not in a finished state and cannot be '
            f'reopened: expected awaiting-signature or completed, found '
            f'{calendar.status}. Review reconciliation is required.'
        )
    calendar.status = CoachCalendarEvent.STATUS_IN_PROGRESS
    calendar.review_completed_at = None
    calendar.save(update_fields=['status', 'review_completed_at', 'updated_at'])


@native_write
def reopen_review_instance_for_editing(instance_row, *, reason_code, note='', actor='system'):
    """Authorised awaiting-signature/completed -> in-progress reopen, so a coach
    can correct a finished review and re-complete it.

    This edits the SAME review_instances row in place -- no new instance is
    created, and no revision/series concept is involved. The consequence is
    deliberate and destructive: every signature already collected is blanked
    (see _clear_review_instance_signatures), because the answers they were
    given against are about to change, and a signature must never outlive the
    content it signed. The pre-reopen answers AND signatures are frozen into
    curriculum.review_instance_reopens.previous_state_snapshot first, in this
    same transaction, and that row is the only surviving copy.

    Authorization (the assigned coach, or a super-admin acting through the
    existing view-as/attributed-write mechanism) is the CALLER's
    responsibility -- see coach_api.views.coach_review_instance_reopen. This
    function only enforces the lifecycle/data rules:

    * a valid reason code, with a required note when the reason is "other"
    * the instance is currently exactly awaiting-signature or completed, held
      under SELECT ... FOR UPDATE and re-checked after the lock, with the
      status UPDATE guarded on the status observed under that lock (an atomic
      compare-and-swap, not a read-then-write) -- so a double submit or a
      concurrent completion is rejected, never a silent no-op
    * started_at and target_date are left untouched: this review still
      happened when it happened. Only status, completed_at, the signatures and
      the Calendar projection move.

    Returns (ok, errors_or_row): errors is {'reason': [...]}, {'note': [...]}
    or {'status': [...]} depending on which check failed -- the same contract
    as mark_review_instance_in_progress_manually. A failed Calendar mirror
    raises ValueError instead (as it does for signatures), rolling the whole
    transaction back, since a Calendar row that no longer matches its instance
    is a reconciliation problem, not a user input error.
    """
    reason_code = curriculum_views.clean_str(reason_code).lower()
    note = curriculum_views.clean_str(note)
    if reason_code not in REOPEN_REASON_CODES:
        return False, {'reason': ['Choose a valid reason for reopening this review.']}
    if reason_code == 'other' and not note:
        return False, {'note': ['Add details when the reason is "Other".']}

    current_status = instance_row.get('status')
    if current_status not in REOPENABLE_STATUSES:
        return False, {'status': [_reopen_review_instance_status_rejection(current_status)]}

    # Provisioned before the transaction opens -- same sqlite/DDL-inside-
    # atomic() reason documented on mark_review_instance_in_progress_manually.
    ensure_review_instance_reopens_table()

    with transaction.atomic():
        # The same lock the completion path takes, so a reopen and a concurrent
        # completion/signature serialise against each other rather than
        # interleaving.
        locked = get_review_instance(instance_row.get('id'), for_update=True)
        if not locked:
            return False, {'status': ['Review not found.']}
        entry_status = locked.get('status')
        if entry_status not in REOPENABLE_STATUSES:
            return False, {'status': [_reopen_review_instance_status_rejection(entry_status)]}

        # Captured BEFORE anything is cleared -- this is the audit record.
        snapshot = _reopen_state_snapshot(locked)

        updated = curriculum_views.update_rows(
            REVIEW_INSTANCES_TABLE, 'id = %s and status = %s',
            [locked.get('id'), entry_status],
            {
                'status': STATUS_IN_PROGRESS, 'completed_at': None,
                'updated_by': actor, 'updated_at': datetime.utcnow(),
            },
            # completed_at must actually become NULL; filtered_payload would
            # otherwise drop the None and leave the old completion time behind.
            allow_null_columns=['completed_at'],
        )
        if not updated:
            return False, {'status': [
                'This review changed while it was being reopened. Reload it and try again.',
            ]}
        row = updated[0]

        cleared = _clear_review_instance_signatures(row.get('id'))

        record_review_instance_reopen(
            review_instance_id=row.get('id'), calendar_event_id=row.get('calendar_event_id'),
            previous_status=entry_status, reason_code=reason_code, note=note,
            changed_by=actor, previous_state_snapshot=snapshot,
        )

        _mirror_linked_calendar_after_reopen(row)

    _reopen_logger.info(
        'review_instance_reopened',
        extra={
            'review_instance_id': row.get('id'),
            'calendar_event_id': row.get('calendar_event_id'),
            'previous_status': entry_status,
            'new_status': STATUS_IN_PROGRESS,
            'reason_code': reason_code,
            'note': note,
            'signatures_cleared': cleared,
            'changed_by': actor,
            'changed_at': datetime.utcnow().isoformat(),
        },
    )
    return True, row
