"""Programme Review schedule preview and clash resolution.

A Programme can have several recurring Review templates (``reviews.py``) --
e.g. a Progress Review every 12 weeks and a Monthly Coaching Meeting every
month. Projected forward, two or more of those recurring templates can land
in the same calendar month. This module:

  * Projects each enabled Review template's RAW occurrence dates from its
    ``schedule_anchor_date`` (reviews.py), over a bounded preview window.
  * Groups raw occurrences by Programme + calendar month -- the clash unit is
    the month, not the exact date, so an 8 Mar review and a 20 Mar review are
    still "a March clash" even though they land on different days.
  * Applies persisted occurrence-level decisions (skip / keep) to turn RAW
    occurrences into the EFFECTIVE schedule a learner-review-instance
    generator would eventually consume.

Two small tables carry the decisions, both idempotent and additive so a
skip/restore cycle stays fully auditable rather than overwriting history:

  * ``review_occurrence_overrides`` -- one Review's one occurrence is
    skipped. Restoring it soft-deletes the override row (the standard
    curriculum soft-delete convention); it does not delete the audit trail.
    A skip is looked up by the exact (review_id, occurrence_date) pair, so
    if a template's recurrence is edited and that date is no longer produced
    by the raw projection, the old override simply never matches anything
    again -- it goes stale on its own, with nothing to clean up.

  * ``review_clash_resolutions`` -- "keep both/all" for a whole month's
    clash. Its identity is (programme_id, month, clash_signature), where
    ``clash_signature`` is a deterministic digest of exactly which raw
    occurrences made up that clash. If the raw set for that month changes
    later (a template edit, a new Review added), the signature changes and
    the old acknowledgement stops applying -- the month goes back to
    "unresolved" automatically, rather than a stale "keep all" silently
    surviving a different set of Reviews.

Recurrence math deliberately never shifts because of a skip: every
occurrence is generated straight from the template's fixed anchor date, so
skipping March does not move April. See ``generate_occurrences``.

Skipping/restoring/keeping-all never touches ``reviews.py``'s tables: a
Review template, its Sections and Fields are untouched by any decision made
here.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from django.db import connection, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from . import reviews
from . import schema_gate
from . import views as curriculum_views

OCCURRENCE_OVERRIDES_TABLE = 'review_occurrence_overrides'
CLASH_RESOLUTIONS_TABLE = 'review_clash_resolutions'

DEFAULT_PREVIEW_MONTHS = 12
MAX_PREVIEW_MONTHS = 24
RECURRENCE_UNIT_NOUNS = {'days': 'day', 'weeks': 'week', 'months': 'month'}

_REVIEW_SCHEDULE_TABLES_READY = False


def ensure_review_schedule_tables():
    global _REVIEW_SCHEDULE_TABLES_READY
    if _REVIEW_SCHEDULE_TABLES_READY:
        return
    if not schema_gate.runtime_bootstrap_allowed():
        schema_gate.require_tables(OCCURRENCE_OVERRIDES_TABLE, CLASH_RESOLUTIONS_TABLE)
        _REVIEW_SCHEDULE_TABLES_READY = True
        return
    provision_review_schedule_tables()


def provision_review_schedule_tables():
    """Mirrors sql/2026-09-10_curriculum_review_schedule_clash_resolution.sql
    for sqlite/local dev. NOT for production request paths -- see reviews.py
    and schema_gate for why."""
    global _REVIEW_SCHEDULE_TABLES_READY
    if _REVIEW_SCHEDULE_TABLES_READY:
        return
    with connection.cursor() as cursor:
        if connection.vendor == 'postgresql':
            cursor.execute(f'create schema if not exists {curriculum_views.quote_ident(curriculum_views.CURRICULUM_SCHEMA)}')
        cursor.execute(f'''
            create table if not exists {curriculum_views.authoring_table_name(OCCURRENCE_OVERRIDES_TABLE)} (
                id varchar(128) primary key,
                review_id varchar(128) not null,
                programme_id varchar(255) not null,
                occurrence_date date not null,
                -- NULL keeps the programme-wide skip; a set value scopes the
                -- override to one learner (see review_instances.py). Mirrors
                -- sql/2026-09-13_curriculum_review_instances.sql.
                learner_id integer,
                action varchar(16) not null default 'skip',
                reason varchar(1000) not null default '',
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
            create table if not exists {curriculum_views.authoring_table_name(CLASH_RESOLUTIONS_TABLE)} (
                id varchar(128) primary key,
                programme_id varchar(255) not null,
                clash_month varchar(7) not null,
                clash_signature varchar(2000) not null default '',
                resolution varchar(16) not null default 'keep_all',
                note varchar(1000) not null default '',
                deleted_at timestamp,
                deleted_by varchar(255),
                created_by varchar(255) not null default '',
                updated_by varchar(255) not null default '',
                created_at timestamp not null default current_timestamp,
                updated_at timestamp not null default current_timestamp
            )
        ''')
    _REVIEW_SCHEDULE_TABLES_READY = True


# --------------------------------------------------------------- date maths

def add_calendar_months(base, months):
    """Calendar-month arithmetic, day clamped to the target month's length.

    The exact rule already used for cohort end dates (views.calculate_cohort_end_date):
    31 Jan + 1 month lands on 28/29 Feb, never rolls into March. "every 12
    weeks" and "every 3 months" are intentionally NOT the same thing -- weeks
    stays pure +84 days, months always goes through this function.

    Every occurrence clamps against the ANCHOR's own day-of-month, not a
    drifting previous occurrence -- generate_occurrences always calls this as
    ``add_calendar_months(anchor, interval * step)``, so a 31 Jan anchor
    produces Jan 31, Feb 28, Mar 31, Apr 30: March is not stuck at 28 just
    because February clamped.
    """
    month_index = base.month - 1 + months
    year = base.year + month_index // 12
    month = month_index % 12 + 1
    day = min(base.day, _month_length(year, month))
    return date(year, month, day)


def _month_length(year, month):
    if month == 12:
        return (date(year + 1, 1, 1) - date(year, 12, 1)).days
    return (date(year, month + 1, 1) - date(year, month, 1)).days


def _month_bounds(month_key):
    """('2027-03') -> (date(2027,3,1), date(2027,3,31))."""
    year, month = int(month_key[:4]), int(month_key[5:7])
    start = date(year, month, 1)
    end = add_calendar_months(start, 1) - timedelta(days=1)
    return start, end


def recurrence_label(interval, unit):
    noun = RECURRENCE_UNIT_NOUNS.get(unit, unit)
    return f'Every {interval} {noun}{"" if interval == 1 else "s"}'


def generate_occurrence_steps(anchor, interval, unit, window_start, window_end, *, max_count=500, occurrence_limit=None):
    """(occurrence_number, date) pairs for one recurrence rule, bounded to a window.

    ``occurrence_number`` is 1-based, counted from ``anchor`` itself as
    occurrence #1 -- the same numbering ``occurrence_limit`` counts against.
    ``generate_occurrences`` is a thin wrapper around this that keeps its
    original dates-only return shape; anything that also needs the occurrence
    number (e.g. a learner-specific Review instance's identity) calls this
    directly rather than re-deriving it, so there is exactly one place the
    recurrence math lives.

    Never an infinite sequence: capped both by the window and by max_count.
    Jumps close to window_start first rather than iterating from the anchor
    one step at a time, so an old anchor date does not cost one loop
    iteration per elapsed interval.
    """
    if not anchor or interval <= 0 or window_start > window_end:
        return []

    if unit in ('days', 'weeks'):
        step_days = interval * (7 if unit == 'weeks' else 1)
        delta = (window_start - anchor).days
        start_step = max(0, delta // step_days - 1)
        steps = []
        step = start_step
        while len(steps) < max_count:
            if occurrence_limit is not None and step >= occurrence_limit:
                break
            occ = anchor + timedelta(days=step_days * step)
            if occ > window_end:
                break
            if occ >= window_start:
                steps.append((step + 1, occ))
            step += 1
        return steps

    # months: calendar arithmetic, not "interval * 30 days".
    months_between = (window_start.year - anchor.year) * 12 + (window_start.month - anchor.month)
    start_step = max(0, months_between // interval - 1)
    steps = []
    step = start_step
    while len(steps) < max_count:
        if occurrence_limit is not None and step >= occurrence_limit:
            break
        occ = add_calendar_months(anchor, interval * step)
        if occ > window_end:
            break
        if occ >= window_start:
            steps.append((step + 1, occ))
        step += 1
    return steps


def generate_occurrences(anchor, interval, unit, window_start, window_end, *, max_count=500, occurrence_limit=None):
    """RAW occurrence dates for one recurrence rule, bounded to a window.

    ``occurrence_limit``, when given, is the total number of occurrences the
    template ever produces (counted from ``anchor`` as occurrence #1) -- the
    Nth-and-later occurrence is never projected, even if the window would
    otherwise include it. See ``generate_occurrence_steps`` for the
    occurrence-numbered variant.
    """
    return [
        occ for _, occ in generate_occurrence_steps(
            anchor, interval, unit, window_start, window_end,
            max_count=max_count, occurrence_limit=occurrence_limit,
        )
    ]


def clash_signature(pairs):
    """Deterministic identity for one month's raw clash set.

    ``pairs`` is an iterable of (review_id, occurrence_date_iso). Sorted so
    the signature does not depend on projection order, then joined -- if the
    underlying set of raw occurrences ever changes (a template's anchor or
    recurrence edited, a Review added/removed), the signature changes with
    it, and any "keep all" acknowledgement keyed to the old signature stops
    applying on its own.
    """
    return '|'.join(f'{review_id}:{occurrence_date}' for review_id, occurrence_date in sorted(pairs))


# ------------------------------------------------------------------- reads

def _raw_occurrences_for_programme(programme_id, window_start, window_end):
    """Every enabled, non-deleted Review template's raw occurrences in the window.

    One query (the Review rows already carry recurrence + anchor); the
    date projection itself is pure in-memory arithmetic.
    """
    rows = reviews.get_review_template_rows('programme_id = %s and enabled = true', [programme_id])
    items = []
    for row in rows:
        anchor = curriculum_views.parse_date(row.get('schedule_anchor_date')) or curriculum_views.parse_date(row.get('created_at'))
        interval = curriculum_views.parse_int(row.get('recurrence_interval'), 1)
        unit = row.get('recurrence_unit') or 'weeks'
        occurrence_limit = row.get('occurrence_count')
        for occ in generate_occurrences(anchor, interval, unit, window_start, window_end, occurrence_limit=occurrence_limit):
            items.append({
                'reviewId': row.get('id'),
                'reviewName': row.get('name') or '',
                'recurrenceLabel': recurrence_label(interval, unit),
                'occurrenceDate': occ,
            })
    return items, [row.get('id') for row in rows]


def _fetch_active_overrides(review_ids):
    if not review_ids:
        return {}
    if connection.vendor == 'postgresql':
        rows = curriculum_views.fetch_all(
            f'select * from {curriculum_views.table_name(OCCURRENCE_OVERRIDES_TABLE)} '
            f'where review_id = any(%s) and deleted_at is null',
            [review_ids],
        )
    else:
        placeholders = ', '.join(['%s'] * len(review_ids))
        rows = curriculum_views.fetch_all(
            f'select * from {curriculum_views.table_name(OCCURRENCE_OVERRIDES_TABLE)} '
            f'where review_id in ({placeholders}) and deleted_at is null',
            review_ids,
        )
    return {(row.get('review_id'), curriculum_views.format_date(row.get('occurrence_date'))): row for row in rows}


def _fetch_active_clash_resolutions(programme_id):
    rows = curriculum_views.fetch_all(
        f'select * from {curriculum_views.table_name(CLASH_RESOLUTIONS_TABLE)} '
        f'where programme_id = %s and deleted_at is null',
        [programme_id],
    )
    return {(row.get('clash_month'), row.get('clash_signature')): row for row in rows}


def _find_active_override(review_id, occurrence_date_iso):
    rows = curriculum_views.fetch_all(
        f'select * from {curriculum_views.table_name(OCCURRENCE_OVERRIDES_TABLE)} '
        f'where review_id = %s and occurrence_date = %s and deleted_at is null',
        [review_id, occurrence_date_iso],
    )
    return rows[0] if rows else None


def build_programme_schedule(programme_id, *, months=DEFAULT_PREVIEW_MONTHS, today=None):
    """Raw + effective Review occurrence schedule for a Programme, grouped by month.

    Exactly three queries regardless of how many Reviews/occurrences/months
    are involved: the Review template rows, the active occurrence overrides
    for those Reviews, and the active clash resolutions for this Programme.
    """
    today = today or date.today()
    window_start = date(today.year, today.month, 1)
    window_end = add_calendar_months(window_start, months) - timedelta(days=1)

    raw_items, review_ids = _raw_occurrences_for_programme(programme_id, window_start, window_end)
    overrides_by_key = _fetch_active_overrides(review_ids)
    resolutions_by_key = _fetch_active_clash_resolutions(programme_id)

    by_month = defaultdict(list)
    for item in raw_items:
        by_month[item['occurrenceDate'].strftime('%Y-%m')].append(item)

    months_payload = []
    for month_key in sorted(by_month.keys()):
        items = sorted(by_month[month_key], key=lambda i: (i['occurrenceDate'], i['reviewId']))
        signature = clash_signature([(i['reviewId'], i['occurrenceDate'].isoformat()) for i in items])
        occurrence_payloads = []
        skipped_count = 0
        for item in items:
            date_iso = item['occurrenceDate'].isoformat()
            override = overrides_by_key.get((item['reviewId'], date_iso))
            status = 'skipped' if override else 'scheduled'
            if override:
                skipped_count += 1
            occurrence_payloads.append({
                'reviewId': item['reviewId'],
                'reviewName': item['reviewName'],
                'occurrenceDate': date_iso,
                'recurrenceLabel': item['recurrenceLabel'],
                'status': status,
                'skippedBy': override.get('updated_by') if override else None,
                'skippedAt': curriculum_views.format_created_at(override.get('updated_at')) if override else None,
                'reason': (override.get('reason') or None) if override else None,
            })

        has_clash = len(items) >= 2
        if not has_clash:
            resolution_status = 'none'
        elif skipped_count > 0:
            resolution_status = 'resolved'
        elif (month_key, signature) in resolutions_by_key:
            resolution_status = 'kept_all'
        else:
            resolution_status = 'unresolved'

        months_payload.append({
            'month': month_key,
            'hasClash': has_clash,
            'resolutionStatus': resolution_status,
            'clashSignature': signature if has_clash else None,
            'occurrences': occurrence_payloads,
        })

    return {
        'programmeId': programme_id,
        'windowStart': window_start.isoformat(),
        'windowEnd': window_end.isoformat(),
        'monthsPreviewed': months,
        'months': months_payload,
    }


# --------------------------------------------------------------- mutations

def skip_occurrence(review_id, occurrence_date_iso, programme_id, *, actor='staff', reason=''):
    existing = _find_active_override(review_id, occurrence_date_iso)
    if existing:
        curriculum_views.update_rows(OCCURRENCE_OVERRIDES_TABLE, 'id = %s', [existing['id']], {
            'reason': reason, 'updated_by': actor, 'updated_at': datetime.utcnow(),
        })
        return existing['id']
    override_id = curriculum_views.unique_prefixed_id('REVO')
    curriculum_views.insert_row(OCCURRENCE_OVERRIDES_TABLE, {
        'id': override_id,
        'review_id': review_id,
        'programme_id': programme_id,
        'occurrence_date': occurrence_date_iso,
        'action': 'skip',
        'reason': reason,
        'created_by': actor,
        'updated_by': actor,
        'created_at': datetime.utcnow(),
        'updated_at': datetime.utcnow(),
    })
    return override_id


def restore_occurrence(review_id, occurrence_date_iso, *, actor='staff'):
    """Undo a skip. The override row is soft-deleted, not hard-deleted -- it
    stays as an auditable "this was skipped, then restored by X" record."""
    existing = _find_active_override(review_id, occurrence_date_iso)
    if not existing:
        return False
    curriculum_views.soft_delete_rows(OCCURRENCE_OVERRIDES_TABLE, 'id = %s', [existing['id']], deleted_by=actor)
    return True


def _upsert_keep_all_resolution(programme_id, month, signature, *, actor='staff', note=''):
    existing = curriculum_views.fetch_all(
        f'select * from {curriculum_views.table_name(CLASH_RESOLUTIONS_TABLE)} '
        f'where programme_id = %s and clash_month = %s and clash_signature = %s and deleted_at is null',
        [programme_id, month, signature],
    )
    if existing:
        return existing[0]['id']
    resolution_id = curriculum_views.unique_prefixed_id('REVOC')
    curriculum_views.insert_row(CLASH_RESOLUTIONS_TABLE, {
        'id': resolution_id,
        'programme_id': programme_id,
        'clash_month': month,
        'clash_signature': signature,
        'resolution': 'keep_all',
        'note': note,
        'created_by': actor,
        'updated_by': actor,
        'created_at': datetime.utcnow(),
        'updated_at': datetime.utcnow(),
    })
    return resolution_id


# -------------------------------------------------------------- validation

_MONTH_RE = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')
_ACTIONS = ('skip', 'keep')


def validate_clash_resolution_payload(programme_id, payload):
    """Returns (month, cleaned_items, errors).

    Every submitted (reviewId, occurrenceDate) pair must be a RAW occurrence
    this Programme's Reviews actually produce for that month -- this is what
    keeps the decision scoped to the correct Programme and rejects stale or
    fabricated dates rather than trusting the client.
    """
    errors = {}
    month = curriculum_views.clean_str(payload.get('month'))
    if not _MONTH_RE.match(month):
        errors['month'] = 'A valid month (YYYY-MM) is required.'
        return month, [], errors

    month_start, _ = _month_bounds(month)
    raw_items, _ = _raw_occurrences_for_programme(programme_id, month_start, _month_bounds(month)[1])
    raw_keys = {(i['reviewId'], i['occurrenceDate'].isoformat()) for i in raw_items}

    items = payload.get('occurrences')
    if not isinstance(items, list) or not items:
        errors['occurrences'] = 'Select at least one review occurrence.'
        return month, [], errors

    cleaned_items = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            item = {}
        path = f'occurrences[{index}]'
        review_id = curriculum_views.clean_str(item.get('reviewId'))
        occurrence_date = curriculum_views.clean_str(item.get('occurrenceDate'))
        action = curriculum_views.clean_str(item.get('action')).lower()

        if (review_id, occurrence_date) not in raw_keys:
            errors[path] = 'This review occurrence is not currently scheduled for this Programme in this month.'
            continue
        if action not in _ACTIONS:
            errors[f'{path}.action'] = 'action must be "skip" or "keep".'
            continue
        cleaned_items.append({
            'review_id': review_id,
            'occurrence_date': occurrence_date,
            'action': action,
            'reason': curriculum_views.clean_str(item.get('reason')),
        })

    return month, cleaned_items, errors


def resolve_clash_decision(programme_id, month, items, *, actor='staff'):
    """Applies a set of skip/keep decisions, then -- if every raw occurrence
    in the month is still effective (nothing was skipped) -- persists an
    explicit "keep all" acknowledgement so the month stops reading as an
    unresolved clash. Returns the recomputed single-month schedule payload."""
    with transaction.atomic():
        for item in items:
            if item['action'] == 'skip':
                skip_occurrence(item['review_id'], item['occurrence_date'], programme_id, actor=actor, reason=item['reason'])
            else:
                restore_occurrence(item['review_id'], item['occurrence_date'], actor=actor)

        month_start, _ = _month_bounds(month)
        result = build_programme_schedule(programme_id, months=1, today=month_start)
        month_payload = result['months'][0] if result['months'] else None

        if month_payload and month_payload['hasClash'] and month_payload['resolutionStatus'] == 'unresolved':
            _upsert_keep_all_resolution(programme_id, month, month_payload['clashSignature'], actor=actor)
            result = build_programme_schedule(programme_id, months=1, today=month_start)
            month_payload = result['months'][0]

    curriculum_views.invalidate_curriculum_cache()
    return month_payload or {'month': month, 'hasClash': False, 'resolutionStatus': 'none', 'clashSignature': None, 'occurrences': []}


# --------------------------------------------------------------------- views

@csrf_exempt
def curriculum_programme_review_schedule(request, programme_id):
    reviews.ensure_review_tables()
    ensure_review_schedule_tables()
    programme_id = curriculum_views.clean_str(programme_id)

    if request.method != 'GET':
        return curriculum_views.json_error('Method not allowed.', status=405)
    if not reviews._programme_exists(programme_id):
        return curriculum_views.json_error('Programme not found.', status=404)

    months = curriculum_views.parse_int(request.GET.get('months'), DEFAULT_PREVIEW_MONTHS)
    months = max(1, min(months, MAX_PREVIEW_MONTHS))

    payload = build_programme_schedule(programme_id, months=months)
    return JsonResponse({'schema': curriculum_views.CURRICULUM_SCHEMA, **payload})


@csrf_exempt
def curriculum_programme_review_clash_resolve(request, programme_id):
    """POST body: {"month": "2027-03", "occurrences": [{"reviewId", "occurrenceDate", "action": "skip"|"keep"}]}

    "skip" removes one occurrence from the effective schedule without
    touching the recurring template; "keep" restores a previously skipped
    occurrence (or is a no-op if it was already scheduled). Selecting "keep"
    for every occurrence in a clash persists an explicit "kept both/all"
    acknowledgement so the same clash does not keep reappearing as
    unresolved.
    """
    reviews.ensure_review_tables()
    ensure_review_schedule_tables()
    programme_id = curriculum_views.clean_str(programme_id)

    if request.method != 'POST':
        return curriculum_views.json_error('Method not allowed.', status=405)
    if not reviews._programme_exists(programme_id):
        return curriculum_views.json_error('Programme not found.', status=404)

    payload = curriculum_views.json_body(request)
    if payload is None:
        return curriculum_views.json_error('Invalid JSON body.')

    month, items, errors = validate_clash_resolution_payload(programme_id, payload)
    if errors:
        return curriculum_views.json_error('Please fix the highlighted fields.', fields=errors)

    actor = reviews._actor(request)
    month_payload = resolve_clash_decision(programme_id, month, items, actor=actor)

    curriculum_views.log_curriculum_decision(
        'review.clash_resolve', outcome='resolved', entity_id=programme_id, reason=f'month={month}',
    )
    return JsonResponse({'resolved': True, 'programmeId': programme_id, 'month': month_payload})
