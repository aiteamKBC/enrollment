"""Review Types -- the configurable classification a Review Template carries.

A Review Type answers "what KIND of review is this", and nothing else. It is
deliberately NOT:

  * a schedule -- recurrence lives on the template ("Monthly Coaching Meeting"
    every 6 weeks is a perfectly valid, supported configuration);
  * a form -- sections/fields/signatures/eligibility/participants all stay on
    the template;
  * a name -- "Monthly Learner Catch-up" (name) can be of type "Monthly
    Coaching Meeting", and renaming it changes nothing about its routing.

It exists so Coach can route a Review's occurrences to the right calendar
bucket by a STABLE code rather than by a display string, and so a new kind of
Review ("Career Review", "Six Week Review") can be introduced from the
Curriculum UI without a code change or a migration.

Identity and stability
----------------------
``id`` is the only thing ``review_templates.review_type_id`` ever stores, and
follows the same generator every other Curriculum entity uses
(``views.unique_prefixed_id``) with the ``REVT-`` prefix. ``code`` is derived
once from the name at creation time and then frozen -- renaming the type later
must not change how anything routes, so nothing ever regenerates it.

Only two types are system types, seeded here and by
``sql/2026-09-13_curriculum_review_types.sql``:

    Monthly Coaching Meeting   code 'mcm'                is_system
    Progress Review            code 'progress_review'    is_system

These replace the old ``review_templates.coach_surface`` enum, which could
only ever hold those same two values and had no identity of its own. See
``COACH_SURFACE_LEGACY_CODES`` for the one-time mapping the backfill uses.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

from django.db import connection
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from . import schema_gate
from . import views as curriculum_views

logger = logging.getLogger(__name__)

REVIEW_TYPES_TABLE = 'review_types'

# The two system types, with fixed ids so every environment -- production,
# a local sqlite test run, a developer's Postgres -- agrees on the same
# review_type_id for the same type, and the backfill is deterministic.
REVIEW_TYPE_CODE_MCM = 'mcm'
REVIEW_TYPE_CODE_PROGRESS_REVIEW = 'progress_review'

SYSTEM_REVIEW_TYPES = (
    {'id': 'REVT-MCM', 'name': 'Monthly Coaching Meeting', 'code': REVIEW_TYPE_CODE_MCM},
    {'id': 'REVT-PROGRESS_REVIEW', 'name': 'Progress Review', 'code': REVIEW_TYPE_CODE_PROGRESS_REVIEW},
)

SYSTEM_REVIEW_TYPE_CODES = tuple(entry['code'] for entry in SYSTEM_REVIEW_TYPES)

# The retired review_templates.coach_surface value -> the Review Type code that
# replaced it. Used ONLY by the one-time backfill; no runtime path reads
# coach_surface any more.
COACH_SURFACE_LEGACY_CODES = {
    'mcr': REVIEW_TYPE_CODE_MCM,
    'progress_review': REVIEW_TYPE_CODE_PROGRESS_REVIEW,
}

DUPLICATE_NAME_ERROR = 'A review type with this name already exists.'

_TABLE_READY = False


def reset_ready_flag():
    """Test hook -- mirrors views.reset_schema_ready_flags()."""
    global _TABLE_READY
    _TABLE_READY = False


def ensure_review_types_table():
    """Verify the table exists; provision + seed only outside production."""
    global _TABLE_READY
    if _TABLE_READY:
        return
    if not schema_gate.runtime_bootstrap_allowed():
        schema_gate.require_tables(REVIEW_TYPES_TABLE)
        _TABLE_READY = True
        return
    provision_review_types_table()


def provision_review_types_table():
    """Mirrors sql/2026-09-13_curriculum_review_types.sql for sqlite/local dev.

    NOT for production request paths -- schema there is owned by the SQL file
    applied to Neon directly. See schema_gate's module docstring.
    """
    global _TABLE_READY
    if _TABLE_READY:
        return
    with connection.cursor() as cursor:
        if connection.vendor == 'postgresql':
            cursor.execute(f'create schema if not exists {curriculum_views.quote_ident(curriculum_views.CURRICULUM_SCHEMA)}')
        cursor.execute(f'''
            create table if not exists {curriculum_views.authoring_table_name(REVIEW_TYPES_TABLE)} (
                id varchar(128) primary key,
                name varchar(255) not null default '',
                code varchar(64) not null,
                is_system boolean not null default false,
                is_active boolean not null default true,
                created_by varchar(255) not null default '',
                updated_by varchar(255) not null default '',
                created_at timestamp not null default current_timestamp,
                updated_at timestamp not null default current_timestamp
            )
        ''')
    _TABLE_READY = True
    seed_system_review_types()


def seed_system_review_types():
    """Insert any missing system type. Idempotent: matched on ``code``, so a
    row someone renamed in the UI is left exactly as it is."""
    existing = {
        curriculum_views.clean_str(row.get('code')).lower()
        for row in curriculum_views.fetch_all(
            f'select code from {curriculum_views.table_name(REVIEW_TYPES_TABLE)}'
        )
    }
    seeded = []
    for entry in SYSTEM_REVIEW_TYPES:
        if entry['code'] in existing:
            continue
        curriculum_views.insert_row(REVIEW_TYPES_TABLE, {
            'id': entry['id'],
            'name': entry['name'],
            'code': entry['code'],
            'is_system': True,
            'is_active': True,
            'created_by': 'system',
            'updated_by': 'system',
            'created_at': datetime.utcnow(),
            'updated_at': datetime.utcnow(),
        })
        seeded.append(entry['code'])
    return seeded


# --------------------------------------------------------------------- reads

def get_review_type_rows(where_sql='', params=None):
    where = where_sql or '1 = 1'
    return curriculum_views.fetch_all(
        f'select * from {curriculum_views.table_name(REVIEW_TYPES_TABLE)} '
        f'where {where} order by is_system desc, name asc',
        params or [],
    )


def list_review_types(*, include_inactive=False):
    if include_inactive:
        return get_review_type_rows()
    return get_review_type_rows('is_active = true')


def get_review_type(type_id):
    type_id = curriculum_views.clean_str(type_id)
    if not type_id:
        return None
    rows = get_review_type_rows('id = %s', [type_id])
    return rows[0] if rows else None


def get_review_type_by_code(code):
    code = curriculum_views.clean_str(code).lower()
    if not code:
        return None
    rows = get_review_type_rows('lower(code) = %s', [code])
    return rows[0] if rows else None


def review_type_index():
    """{id: row} for every type, active or not.

    One query, handed to loops that would otherwise ask per Review row. Not
    cached: production runs nine two-worker services off one process-local
    Django cache, so a cached lookup table would serve a type created in
    another worker as "unknown" until that worker restarted -- and this is a
    handful of rows.
    """
    return {row.get('id'): row for row in get_review_type_rows()}


def review_type_payload(row):
    return {
        'id': row.get('id'),
        'name': row.get('name') or '',
        'code': row.get('code') or '',
        'isSystem': bool(row.get('is_system')),
        'isActive': bool(row.get('is_active')),
    }


# ---------------------------------------------------------------- code minting

def generate_review_type_code(name, existing_codes=None):
    """A stable snake_case code for a display name.

    Frozen at creation: a later rename keeps the original code, because every
    calendar/routing decision downstream is keyed on it.
    """
    base = re.sub(r'[^a-z0-9]+', '_', curriculum_views.clean_str(name).lower()).strip('_')
    base = base or 'review_type'
    taken = {curriculum_views.clean_str(code).lower() for code in (existing_codes or [])}
    if base not in taken:
        return base
    suffix = 2
    while f'{base}_{suffix}' in taken:
        suffix += 1
    return f'{base}_{suffix}'


def _normalised_name(value):
    """Trimmed and case-folded -- how duplicate names are compared."""
    return ' '.join(curriculum_views.clean_str(value).split()).lower()


def duplicate_active_name_exists(name, *, exclude_id=None):
    target = _normalised_name(name)
    if not target:
        return False
    for row in list_review_types():
        if exclude_id and row.get('id') == exclude_id:
            continue
        if _normalised_name(row.get('name')) == target:
            return True
    return False


# -------------------------------------------------------------------- writes

def create_review_type(name, *, actor='staff'):
    """Create a custom type from the ONE field a user supplies: its name.

    Everything else -- id, code, is_system, is_active -- is generated here;
    none of it is accepted from the payload, so the create UI stays a single
    text box.
    """
    ensure_review_types_table()
    clean_name = ' '.join(curriculum_views.clean_str(name).split())
    if not clean_name:
        return None, {'name': 'Review type name is required.'}
    if len(clean_name) > 255:
        return None, {'name': 'Review type name must be 255 characters or fewer.'}
    if duplicate_active_name_exists(clean_name):
        return None, {'name': DUPLICATE_NAME_ERROR}

    existing_rows = get_review_type_rows()
    code = generate_review_type_code(clean_name, [row.get('code') for row in existing_rows])
    type_id = curriculum_views.unique_prefixed_id(
        'REVT', '', [row.get('id') for row in existing_rows],
    )
    row = curriculum_views.insert_row(REVIEW_TYPES_TABLE, {
        'id': type_id,
        'name': clean_name,
        'code': code,
        'is_system': False,
        'is_active': True,
        'created_by': actor,
        'updated_by': actor,
        'created_at': datetime.utcnow(),
        'updated_at': datetime.utcnow(),
    })
    curriculum_views.invalidate_curriculum_cache()
    return row, None


def rename_review_type(type_id, name, *, actor='staff'):
    """Rename a custom type. ``code`` is deliberately untouched: routing must
    survive a rename (a Review Type is classification, not a label)."""
    ensure_review_types_table()
    row = get_review_type(type_id)
    if not row:
        return None, {'_': 'Review type not found.'}
    if bool(row.get('is_system')):
        return None, {'_': 'System review types cannot be renamed.'}
    clean_name = ' '.join(curriculum_views.clean_str(name).split())
    if not clean_name:
        return None, {'name': 'Review type name is required.'}
    if duplicate_active_name_exists(clean_name, exclude_id=type_id):
        return None, {'name': DUPLICATE_NAME_ERROR}
    rows = curriculum_views.update_rows(REVIEW_TYPES_TABLE, 'id = %s', [type_id], {
        'name': clean_name, 'updated_by': actor, 'updated_at': datetime.utcnow(),
    })
    curriculum_views.invalidate_curriculum_cache()
    return (rows[0] if rows else get_review_type(type_id)), None


def archive_review_type(type_id, *, actor='staff'):
    """Deactivate a custom type -- never a hard delete.

    Templates already classified with it keep working and keep classifying
    exactly as before; the type simply stops being offered for new Reviews.
    System types cannot be archived.
    """
    ensure_review_types_table()
    row = get_review_type(type_id)
    if not row:
        return None, {'_': 'Review type not found.'}
    if bool(row.get('is_system')):
        return None, {'_': 'Monthly Coaching Meeting and Progress Review are system review types and cannot be deleted.'}
    rows = curriculum_views.update_rows(REVIEW_TYPES_TABLE, 'id = %s', [type_id], {
        'is_active': False, 'updated_by': actor, 'updated_at': datetime.utcnow(),
    })
    curriculum_views.invalidate_curriculum_cache()
    return (rows[0] if rows else get_review_type(type_id)), None


# --------------------------------------------------------------------- views

def _actor(request):
    # Matches reviews.py: curriculum_api writes are not per-account today.
    return 'staff'


@csrf_exempt
def curriculum_review_type_collection(request):
    """GET  -- every active Review Type (add ?includeInactive=1 for archived).
    POST -- create a custom type from {"name": "Career Review"} alone. Every
            other column (id, code, is_system, is_active) is generated here,
            so the create UI stays a single text box.
    """
    ensure_review_types_table()

    if request.method == 'GET':
        include_inactive = curriculum_views.clean_str(request.GET.get('includeInactive')).lower() in ('1', 'true', 'yes')
        rows = list_review_types(include_inactive=include_inactive)
        return curriculum_views.curriculum_results_response([review_type_payload(row) for row in rows])

    if request.method != 'POST':
        return curriculum_views.json_error('Method not allowed.', status=405)

    payload = curriculum_views.json_body(request)
    if payload is None:
        return curriculum_views.json_error('Invalid JSON body.')

    row, errors = create_review_type(payload.get('name'), actor=_actor(request))
    if errors:
        return curriculum_views.json_error(
            errors.get('name') or 'Please fix the highlighted fields.', fields=errors,
        )

    curriculum_views.log_curriculum_decision('review-type.create', outcome='created', entity_id=row.get('id'))
    return JsonResponse({'created': True, 'reviewType': review_type_payload(row)}, status=201)


@csrf_exempt
def curriculum_review_type_detail(request, review_type_id):
    """PATCH  -- rename a custom type (its code, and therefore its routing,
                 is untouched).
    DELETE -- archive a custom type: deactivated, never hard-deleted, so the
              Reviews already classified with it keep working. A system type
              is refused.
    """
    ensure_review_types_table()
    review_type_id = curriculum_views.clean_str(review_type_id)
    row = get_review_type(review_type_id)
    if not row:
        return curriculum_views.json_error('Review type not found.', status=404)

    if request.method == 'GET':
        return JsonResponse({'reviewType': review_type_payload(row)})

    if request.method == 'DELETE':
        archived, errors = archive_review_type(review_type_id, actor=_actor(request))
        if errors:
            return curriculum_views.json_error(errors.get('_') or 'Unable to delete this review type.', status=400, fields=errors)
        curriculum_views.log_curriculum_decision('review-type.archive', outcome='archived', entity_id=review_type_id)
        return JsonResponse({
            'deleted': True, 'permanent': False, 'archived': True,
            'id': review_type_id, 'reviewType': review_type_payload(archived),
        })

    if request.method not in {'PATCH', 'PUT'}:
        return curriculum_views.json_error('Method not allowed.', status=405)

    payload = curriculum_views.json_body(request)
    if payload is None:
        return curriculum_views.json_error('Invalid JSON body.')

    renamed, errors = rename_review_type(review_type_id, payload.get('name'), actor=_actor(request))
    if errors:
        return curriculum_views.json_error(
            errors.get('_') or errors.get('name') or 'Please fix the highlighted fields.', fields=errors,
        )
    curriculum_views.log_curriculum_decision('review-type.update', outcome='updated', entity_id=review_type_id)
    return JsonResponse({'updated': True, 'reviewType': review_type_payload(renamed)})
