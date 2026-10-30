"""Learner-scoped, read-only sources for the Training Plan dashboard."""
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
import logging
import math
import re
from zoneinfo import ZoneInfo

from . import journal_sources
from django.db import DatabaseError, connections
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from login.permissions import learner_self_or_staff
from old_otjh.coach_booking import booking_url
from old_otjh.service import ServiceError
from .learner_detail import SOURCE_MODELS
from .models import LearnerProfile, StaffUser
from .learning_plan import _aptem_subject_modules, _effective_plan_ids, request_preset_cache
from .coach_assignment import current_coach, source_coach
from .student_activity import _builder_subject_metadata, builder_subject_links
from .subject_content import as_list, clean_text, safe_url
from .subject_dates import is_introduction_section
from .training_plan_contract import read_contract, contract_extract_metadata, selected_contract
from .projection_performance import measure_projection
from . import canonical_learning
from .replaced_teams_attendance import REPLACED_ATTENDANCE_LATERAL_SQL
from curriculum_api.week_visibility import visible_child_sql, visible_week_sql

log = logging.getLogger(__name__)

NON_TEACHING_LIVE_SESSION_WEEK_TITLES = {
    'welcome, module guide and assessment resources',
}


def is_educational_live_session_week(title):
    """Keep teaching weeks while excluding learner onboarding sections."""
    normalized = clean_text(title).casefold()
    return not is_introduction_section(title) and normalized not in NON_TEACHING_LIVE_SESSION_WEEK_TITLES


# Keep the projection identity reads narrow. ``Created_users`` contains many
# large JSON/text columns and the dashboard previously selected every one of
# them before issuing its real projection queries.
TRAINING_PLAN_SOURCE_FIELDS = (
    'id', 'aptem_id', 'email', 'status', 'programme_status',
    'programme', 'cohort', 'group',
    'learning_plan', 'training_plan', 'modules', 'weeks', 'components',
    'case_owner', 'coach_name', 'coach_email',
    'start_date', 'end_date', 'learner_start_date',
    'practical_period_end_date', 'apprenticeship_end_date',
    # Read by the imported Review calendar; deferring it cost a query.
    'learner_type',
)

TRAINING_PLAN_PROFILE_FIELDS = (
    'id', 'full_name', 'email', 'lifecycle_status',
    'programme', 'programme_id', 'programme_status',
    'cohort', 'cohort_id', 'group_name', 'group_id',
    'coach_name', 'coach_email', 'start_date', 'end_date',
    # Review source and imported Review identity; deferring them cost a query each.
    'aptem_id', 'enrolment_id', 'learner_type',
)


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError):
        return None


def valid_aptem_id(value):
    """Only a positive stable Aptem identifier selects the Aptem projection."""
    try:
        parsed = int(str(value or '').strip())
    except (ValueError, TypeError):
        return None
    return parsed if parsed > 0 else None


def rows(cursor):
    return [dict(zip([field[0] for field in cursor.description], row)) for row in cursor.fetchall()]


def instant(value):
    # Curriculum stores UTC in timestamp-without-time-zone columns.
    if not value:
        return None
    if isinstance(value, datetime) and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def date_only(value):
    """Serialize contract programme dates without applying a machine timezone."""
    return str(value)[:10] if value else None


def plan_session(row):
    start = row['scheduled_start'] or row['start_datetime']
    end = row['scheduled_end']
    minutes = (end - start).total_seconds() / 60 if end and end > start else row['duration_minutes']
    return {'id': row['occurrence_id'] or row['session_id'], 'moduleId': row['module_id'],
            'title': clean_text(row['module_title']) or 'Live session', 'start': instant(start),
            'end': instant(end), 'minutes': minutes,
            'joinUrl': safe_url(row['join_url'] or row['series_join_url']) or None,
            'status': row['status'] or row['series_status'] or 'scheduled', 'attended': row['attended']}


def plan_module(row):
    text_fields = ('title', 'description', 'tutor_name', 'coach_name', 'programme_name',
                   'cohort_name', 'group_name', 'session_week_day', 'session_start_time', 'session_end_time')
    return {'id': row['id'], **{key: clean_text(row.get(key)) for key in text_fields},
            **{key: row[key].isoformat() if row.get(key) else None for key in ('start_date', 'end_date')},
            **{key: number(row.get(key)) for key in ('total_otjh', 'weeks_number', 'sessions_number')},
            'educational_session_count': 0,
            'learning_outcomes': []}


def attach_curriculum_slots(module_rows, by_id, week_counts, weeks_by_number=None, *, strict=False, holiday_loader=None):
    """Give every learner module the curriculum slot spine the Builder shows.

    This is a READ of the curriculum scheduler, not a second one. The holidays
    come from ``cohort_selected_holidays_by_cohort`` -- the same resolution the
    Module Builder, the Teams series and the tutor conflict check use, with the
    cohort's own ``excluded_holiday_ids`` deny-list already applied upstream --
    and the spine comes from ``module_session_plan_for_count``, which is the
    single door onto ``build_module_session_plan``. Nothing about holidays or
    Reading Weeks is decided here, so the learner cannot be shown a timeline the
    curriculum does not itself hold.

    ``slots`` is what makes a holiday visible to a learner at all: a closed
    delivery slot delivers no session, so a learner reading only session dates
    sees an unexplained gap. Reading Weeks must come from this spine and never
    be inferred from gaps between session dates -- a gap is also what a term
    break, an unauthored week or a module that simply does not deliver that week
    looks like.

    Holidays are resolved once for every cohort on the page rather than per
    module, because every module of a cohort shares that cohort's holidays.

    ``weeks_by_number`` maps ``(module_id, week_number)`` to that authored
    week's ``id``/``title``/``learningOutcomes``/``holidayNote`` (see
    ``curriculum.weeks``). ``holidayNote`` is already blank unless the author
    published it, and it is attached only to a slot a holiday lands on.
    A taught slot's ``sessionNumber`` is the same content-week numbering, so a
    live-session slot picks up its week's own title and outcomes here rather
    than the module-wide aggregate. Omitted entirely when the caller has none
    -- the spine then matches the scheduler's own output exactly, which the
    Reading Week regression tests rely on.
    """
    from curriculum_api.views import (
        cohort_selected_holidays_by_cohort, module_session_plan_for_count,
        module_stored_session_count,
    )

    weeks_by_number = weeks_by_number or {}
    try:
        holidays = (holiday_loader or cohort_selected_holidays_by_cohort)(
            [row.get('cohort_id') for row in module_rows]
        )
    except Exception:
        if strict:
            raise
        log.warning('Could not resolve cohort holidays for the training plan.', exc_info=True)
        holidays = {}

    for row in module_rows:
        module = by_id.get(row['id'])
        if module is None:
            continue
        week_count = week_counts.get(row['id'], 0)
        try:
            plan = module_session_plan_for_count(
                row,
                module_stored_session_count(row, week_count),
                holidays=holidays.get(str(row.get('cohort_id') or ''), []),
            )
        except Exception:
            if strict:
                raise
            log.warning('Could not plan curriculum slots for module %s.', row['id'], exc_info=True)
            plan = {}
        module['curriculumSlots'] = plan.get('slots') or []
        for slot in module['curriculumSlots']:
            if slot.get('type') != 'live-session' or not slot.get('sessionNumber'):
                continue
            week = weeks_by_number.get((row['id'], slot['sessionNumber']))
            if week:
                slot['weekId'] = week['id']
                slot['weekTitle'] = week['title']
                slot['learningOutcomes'] = week['learningOutcomes']
                # The author's holiday hint, and only on a slot a holiday
                # actually lands on. The note is written against a clash, so a
                # week whose dates have since moved off the holiday carries
                # nothing -- the same test the author's own notice is drawn from.
                if week.get('holidayNote') and slot.get('holidays'):
                    slot['holidayNote'] = week['holidayNote']
        # The effective delivery end is the scheduler's own -- the last
        # DELIVERED session, holiday shifts included. Deliberately separate from
        # the stored `end_date` the module row carries, which a human may have
        # typed and which no longer describes the run once a closure moves it.
        module['effectiveEndDate'] = plan.get('finalEndDate') or ''
        module['originalEndDate'] = plan.get('originalEndDate') or ''


def assigned_group_coach(source, modules):
    """A different cohort's assigned module must not supply this learner's coach."""
    placement = [clean_text(getattr(source, key, '')).casefold() for key in ('programme', 'cohort', 'group')]
    if not all(placement):
        return ''
    coaches = {clean_text(module.get('coach_name')) for module in modules
               if [clean_text(module.get(key)).casefold() for key in ('programme_name', 'cohort_name', 'group_name')] == placement}
    coaches.discard('')
    return next(iter(coaches)) if len(coaches) == 1 else ''


def coach_phone(email):
    """Return the assigned staff member's phone without making it required."""
    email = clean_text(email)
    if not email:
        return ''
    try:
        return clean_text(StaffUser.objects.filter(email__iexact=email)
                          .values_list('phone_number', flat=True).first())
    except DatabaseError:
        return ''


def find_contract(cursor, aptem_id):
    cursor.execute('''SELECT c.id,c.azure_path,c.training_plan_planned_hours,
        c.document_name AS original_name,
        coalesce(nullif(a.display_name,''),c.document_name) AS document_name,
        c.date,c.fetched_at,c.fully_signed_date,c.raw AS extraction_metadata,
        c.program_start_date,c.planned_end_date
        FROM fetching_evidence.aptem_cv_contracts_probe c
        LEFT JOIN "Audit".contract_document_archive a ON a.contract_id=c.id
        WHERE c.learner_id=%s
          AND lower(coalesce(nullif(a.display_name,''),c.document_name)) ~ 'training[[:space:]_-]*plan'
          AND a.archived_at IS NULL AND a.deleted_at IS NULL
        ORDER BY coalesce(c.fully_signed_date,c.date) DESC NULLS LAST,c.id DESC''', [aptem_id])
    return selected_contract(rows(cursor))


def contract_plan(source, contract):
    months, status = {}, 'not-available'
    if contract and contract['azure_path']:
        try:
            # Keep the expensive extraction until this exact source contract
            # changes instead of discarding it on an arbitrary time bucket.
            version = ':'.join(str(value or '') for value in (
                contract.get('id'), contract.get('azure_path'), contract.get('fetched_at'),
                contract.get('date'), contract.get('fully_signed_date'),
            ))
            extracted = read_contract(contract['azure_path'], contract['training_plan_planned_hours'], version,
                                      contract_extract_metadata(contract.get('extraction_metadata')))
            if extracted:
                months = {key: {**value, 'label': '', 'source': 'contract'} for key, value in extracted.items()}
                status = 'ready'
            else:
                status = 'unverified'
        except Exception:
            log.warning('Training-plan contract could not be read for enrolment %s', source.pk)
            status = 'unavailable'
    journal = {}
    if journal_sources.enabled():
        from . import canonical_learning
        if canonical_learning.enabled(source.pk):
            journal['journalTargets'] = canonical_learning.targets(source.pk)
    return {**journal, 'months': months, 'contractStatus': status,
            'programmeStartDate': date_only(contract.get('program_start_date')) if contract else None,
            'programmeEndDate': date_only(contract.get('planned_end_date')) if contract else None}


def _canonical_actual_rows(owner, records=None):
    totals = defaultdict(lambda: {'hours': 0, 'count': 0})
    for record in records if records is not None else canonical_learning.entries_for(owner):
        if not canonical_learning.counts_as_actual(record):
            continue
        for allocation in canonical_learning.allocations(record):
            month = allocation.get('reporting_month')
            if not month:
                continue
            group_id = str(record.get('module_ref') or '').strip() or None
            key = (month, group_id)
            totals[key]['hours'] += canonical_learning.number(allocation.get('actual_seconds')) / 3600
            totals[key]['count'] += 1
    return [
        {'month': month, 'groupId': group_id, 'hours': round(value['hours'], 4), 'count': value['count']}
        for (month, group_id), value in sorted(
            totals.items(), key=lambda item: (item[0][0], item[0][1] or '')
        )
    ]


def module_record_index(records, modules):
    """Canonical module attribution, retaining input positions and title ambiguity."""
    by_id = {str(module['id']): module for module in modules}
    ids_by_title = defaultdict(list)
    for module_id, module in by_id.items():
        title = clean_text(module.get('title')).casefold()
        if title:
            ids_by_title[title].append(module_id)

    grouped = defaultdict(list)
    for position,record in enumerate(records):
        direct = str(record.get('module_ref') or '').strip()
        if direct in by_id:
            module_id = direct
        else:
            title = clean_text(record.get('module_title')).casefold()
            matches = ids_by_title.get(title, [])
            module_id = matches[0] if len(matches) == 1 else None
        if module_id is not None:
            grouped[module_id].append((position,record))
    return by_id,grouped


def _canonical_module_progress(records, modules, *, record_index=None):
    """Project learner-progress-entry facts onto the assigned module ids."""
    by_id,grouped = module_record_index(records,modules) if record_index is None else record_index

    result = {
        module_id: {'hours': {'actual': 0}, 'ksb': {'completed': 0, 'total': 0},
                    'attendance': {'attended': 0, 'total': 0}}
        for module_id in by_id
    }
    achieved_codes = defaultdict(set)
    for module_id,record in ((mid,record) for mid,items in grouped.items() for _,record in items):
        metric = result[module_id]
        if canonical_learning.counts_as_actual(record):
            metric['hours']['actual'] += sum(
                canonical_learning.number(allocation.get('actual_seconds')) / 3600
                for allocation in canonical_learning.allocations(record)
            )
        for code in set(record.get('ksbs') or []):
            if not str(code).strip():
                continue
            metric['ksb']['total'] += 1
            metric['ksb']['completed'] += record.get('accepted') is True
            if record.get('accepted') is True:
                achieved_codes[module_id].add(str(code).strip())
        if (record.get('kind') == 'activity_event'
                and record.get('feed_kind') == 'attendance_confirmation'):
            metric['attendance']['attended'] += 1

    for module_id, metric in result.items():
        module = by_id[module_id]
        authored_sessions = number(module.get('educational_session_count'))
        if not authored_sessions:
            authored_sessions = number(module.get('sessions_number'))
        attended = metric['attendance']['attended']
        metric['hours']['actual'] = round(metric['hours']['actual'], 4)
        metric['ksb']['achievedCodes'] = sorted(achieved_codes[module_id])
        # Confirmations prove attendance; the authored plan remains the session target.
        metric['attendance']['total'] = max(attended, int(authored_sessions or 0))
    return result


def plan_module_ids(cur, source, owner):
    """Every module on the learner's plan, as the dashboard schedule lists them."""
    # Effective current assignments are authoritative for the schedule.
    # Builder metadata enriches those assignments (and supplies legacy
    # links), but a missing builder row must not hide an assigned module.
    current_module_ids = list(dict.fromkeys(_effective_plan_ids(source, request_preset_cache(source))))
    # Imported learners can have real assigned subjects in the Aptem
    # mirror without those ids ever being written to the native learning
    # plan. Include those subjects in the same dashboard projection unless
    # staff explicitly limited the learner to a native assignment set.
    if owner is not None:
        current_module_ids.extend(canonical_learning.curriculum_module_ids_for(owner))
    current_module_ids = list(dict.fromkeys(current_module_ids))
    refs = [f'current:{module_id}' for module_id in current_module_ids]
    links = builder_subject_links(cur, source, refs, read=_builder_subject_metadata)
    builder_ids = {
        item['id'] for item in links.values() if item.get('id')
    }
    return sorted(builder_ids | set(current_module_ids)), links


def plan_planned_hours(source, owner, *, ids=None):
    """Whole-programme planned OTJ hours: the authored total_otjh of the plan's modules.

    This is the learning-plan total the Apprenticeship Agreement states, over
    the same modules the dashboard lists. Callers use it only when the SSOT
    holds no programme plan for the enrolment. ``ids`` lets a caller that has
    already resolved ``plan_module_ids`` for this source skip resolving it again.
    """
    with connections['enrolment'].cursor() as cur:
        if ids is None:
            ids, _ = plan_module_ids(cur, source, owner)
        if not ids:
            return None
        cur.execute('''SELECT sum(total_otjh) FROM curriculum.modules
            WHERE module_catalogue_id=ANY(%s)''', [ids])
        total = cur.fetchone()[0]
    total = number(total)
    return round(total, 2) if total else None


def _plan_session_rows(cur, email, ids, since=None, until=None, *, rail_only=False, navigation_only=False):
    """Session rows on the plan's modules, earliest first, starting in ``[since, until)`` when given."""
    bounds = [(since, '>='), (until, '<')]
    window = ''.join(f' AND coalesce(o.scheduled_start,s.start_datetime){op}%s' for bound, op in bounds if bound)
    # Bounds never consume attendance. Removing this scalar subquery and its
    # LEFT LATERAL lookup preserves the session population (its aggregate
    # returns at most one row) while avoiding a historical lookup per occurrence.
    attendance_sql = 'NULL' if navigation_only else '''CASE WHEN coalesce(CASE WHEN replaced_attendance.occurrence_id IS NOT NULL
                                THEN replaced_attendance.attendance_report_id
                                ELSE o.attendance_report_id END,'')='' THEN NULL
             ELSE EXISTS(SELECT 1 FROM curriculum.live_session_attendance a
                 WHERE a.occurrence_id=COALESCE(replaced_attendance.occurrence_id,o.id)
                   AND lower(btrim(a.email))=%s AND a.total_attendance_seconds>0) END'''
    statement = f'''WITH RECURSIVE owners(module_id,owner_id,depth) AS (
        SELECT m.module_catalogue_id,
               coalesce(nullif(btrim(m.teams_shared_source_module_id),''),m.module_catalogue_id),0
        FROM curriculum.modules m WHERE m.module_catalogue_id=ANY(%s)
        UNION ALL
        SELECT owners.module_id,btrim(m.teams_shared_source_module_id),owners.depth+1
        FROM owners JOIN curriculum.modules m ON m.module_catalogue_id=owners.owner_id
        WHERE coalesce(btrim(m.teams_shared_source_module_id),'')<>'' AND owners.depth<5
    ), delivery AS (
        SELECT DISTINCT ON (module_id) module_id,owner_id
        FROM owners ORDER BY module_id,depth DESC
    )
        SELECT s.id AS session_id,delivery.module_id,target_module.title AS module_title,
        s.start_datetime,s.duration_minutes,{"NULL" if rail_only else "s.join_url"} AS series_join_url,s.repeat_pattern,s.status AS series_status,
        o.id AS occurrence_id,o.scheduled_start,o.scheduled_end,{"NULL" if rail_only else "o.join_url"} AS join_url,o.status,
        {attendance_sql} AS attended
        FROM delivery
        JOIN curriculum.live_sessions s ON s.module_catalogue_id=delivery.owner_id
        JOIN curriculum.modules target_module ON target_module.module_catalogue_id=delivery.module_id
        LEFT JOIN curriculum.live_session_occurrences o ON o.live_session_id=s.id
        {'' if navigation_only else REPLACED_ATTENDANCE_LATERAL_SQL}
        WHERE lower(s.status) NOT IN ('cancelled','deleted','failed','superseded')
          AND (o.id IS NULL OR lower(o.status) NOT IN ('cancelled','deleted','failed','superseded')){window}
        ORDER BY coalesce(o.scheduled_start,s.start_datetime)'''
    if navigation_only:
        statement = f'''SELECT module_id, min(coalesce(scheduled_start,start_datetime)) AS first_start,
            max(coalesce(scheduled_start,start_datetime)) AS last_start,
            array_agg(DISTINCT to_char(coalesce(scheduled_start,start_datetime) AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/London','YYYY-MM')) AS session_months
            FROM ({statement}) monthly_session_bounds
            WHERE occurrence_id IS NOT NULL OR repeat_pattern IS NULL OR repeat_pattern IN ('none','')
            GROUP BY module_id'''
    cur.execute(statement, [ids, *([] if navigation_only else [email]),
                            *(bound.astimezone(timezone.utc).replace(tzinfo=None) for bound, _ in bounds if bound)])
    return rows(cur)


def _plan_session_list(found):
    sessions = []
    for row in found:
        # Recurring dates come from actual occurrences; never manufacture them.
        if not row['occurrence_id'] and row['repeat_pattern'] not in ('none', '', None):
            continue
        start = row['scheduled_start'] or row['start_datetime']
        if not start:
            continue
        sessions.append(plan_session(row))
    return sessions


def plan_sessions(cur, email, ids, since=None):
    """Live sessions on the plan's modules, optionally only those starting from ``since`` (UTC)."""
    return _plan_session_list(_plan_session_rows(cur, email, ids, since))


REVIEW_EVENT_FIELDS = ('id', 'eventKey', 'title', 'source', 'sequence', 'status', 'date', 'targetDate',
                       'scheduledDate', 'scheduledTime', 'durationMinutes', 'coachName', 'invited',
                       'reviewTemplateId', 'reviewInstanceId')


def plan_reviews(source, profile, sources, dates_only=False, *, header_only=False):
    from .calendar import coaching_events_for_learner
    # Use the same active programme cycle as the calendar booking destination.
    active_profile = profile if profile and profile.lifecycle_status == 'active' else None
    options = {'header_only': True} if header_only else {}
    events = coaching_events_for_learner(source, active_profile, only_sources=frozenset(sources),
                                         dates_only=dates_only, **options)
    return [{**{key: event.get(key) for key in REVIEW_EVENT_FIELDS},
             'meetingLink': safe_url(event.get('meetingLink')) or None} for event in events
            if event.get('source') in sources]


UK = ZoneInfo('Europe/London')
# Statuses that never make an event upcoming (matched trimmed, case-insensitively).
INACTIVE_EVENT_STATUSES = frozenset({'cancelled', 'canceled', 'completed', 'deleted', 'failed',
                                     'superseded', 'awaiting-signature'})
# Equal instants keep the card's original lecture / assignment / review order.
UPCOMING_TYPE_ORDER = {'session': 0, 'assignment': 1, 'review': 2}
DATE_ONLY = re.compile(r'\d{4}-\d{2}-\d{2}')


def _active(status):
    return str(status or '').strip().lower() not in INACTIVE_EVENT_STATUSES


def _end_of_uk_day(day):
    # A dated-only event is due by, or still open until, the end of its UK day.
    return datetime.combine(day, time(23, 59), tzinfo=UK)


def next_week_window(now):
    """Next week's UK Monday and Friday, and the instants [Monday 00:00, Saturday 00:00) between them."""
    today = now.astimezone(UK).date()
    monday = today + timedelta(days=7 - today.weekday())
    friday = monday + timedelta(days=4)
    return (monday, friday, datetime.combine(monday, time(), tzinfo=UK),
            datetime.combine(friday + timedelta(days=1), time(), tzinfo=UK))


def _upcoming_review(review, start, end):
    """A progress review's moment and output date when it falls in ``[start, end)``, else None.

    A review is dated by its booking, else its target date, then its own date.
    A booked UK date keeps its time; a date without a time is ordered at the
    end of its UK day.
    """
    value = str(review.get('scheduledDate') or review.get('targetDate') or review.get('date') or '')
    if not DATE_ONLY.fullmatch(value):
        try:
            moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
        moment = moment.replace(tzinfo=UK) if moment.tzinfo is None else moment
        return (moment, moment.isoformat()) if start <= moment < end else None
    try:
        day = date.fromisoformat(value)
    except ValueError:
        return None
    if not start.date() <= day < end.date():
        return None
    clock = str(review.get('scheduledTime') or '') if review.get('scheduledDate') else ''
    try:
        moment = datetime.combine(day, time.fromisoformat(clock[:5]), tzinfo=UK)
    except ValueError:
        # No usable time: the date alone, ordered at the end of its day.
        return _end_of_uk_day(day), value
    return moment, moment.isoformat()


def read_upcoming(source, now=None):
    """Student Home's events for next week, Monday to Friday (UK): live sessions,
    assignment deadlines and progress reviews.

    Each source keeps its own scope and eligibility rules (the ``overview``
    session SQL and review projection, and the ``home-summary`` assignment
    deadlines), limited to next Monday 00:00 up to Saturday 00:00 UK time.
    Sessions are bounded in SQL. Every event in the window is returned, ordered
    by its UK moment, with only the fields the card reads.
    """
    from .overview_week import upcoming_assignment_deadlines

    monday, friday, start, end = next_week_window(now or datetime.now(timezone.utc))
    owner = getattr(source, '_canonical_profile', None)
    email = str(source.email or '').strip().casefold()
    profile = getattr(source, '_case_file_profile', None) or (
        LearnerProfile.objects.filter(enrolment_id=source.pk)
        .only(*TRAINING_PLAN_PROFILE_FIELDS)
        .first()
    )
    events = []
    with connections['enrolment'].cursor() as cur:
        ids, _ = plan_module_ids(cur, source, owner)
        found = _plan_session_list(_plan_session_rows(cur, email, ids, since=start, until=end)) if ids else []
    for session in found:
        if _active(session['status']):
            events.append((datetime.fromisoformat(session['start']), {
                'id': str(session['id']), 'type': 'session', 'title': session['title'], 'date': session['start']}))
    for deadline in upcoming_assignment_deadlines(source, profile, monday, friday):
        events.append((_end_of_uk_day(date.fromisoformat(deadline['date'])), {
            'id': deadline['id'], 'type': 'assignment', 'title': deadline['title'], 'date': deadline['date']}))
    # Only identity, title, status and dates reach the card.
    for review in plan_reviews(source, profile, ('progress-review',), dates_only=True):
        dated = _active(review.get('status')) and _upcoming_review(review, start, end)
        if dated:
            events.append((dated[0], {'id': str(review.get('id')), 'type': 'review', 'title': review.get('title'),
                                      'date': dated[1], 'booked': bool(review.get('scheduledDate'))}))
    events.sort(key=lambda item: (item[0], UPCOMING_TYPE_ORDER[item[1]['type']], item[1]['id']))
    return {'events': [event for _, event in events]}


def read_dashboard(source, section=None, *, records=None, targets=None, dashboard_tab=None, weekly_spine=False, compact_monthly=False):
    from .weekly_hours import module_activity_hours

    if section == 'upcoming':
        return read_upcoming(source)
    # Monthly renders its board, Overview its progress cards. Only Training
    # renders module descriptions and curriculum learning outcomes. Legacy
    # callers keep their complete projection unless explicitly selecting a tab.
    rich_modules = section != 'my-learning' and dashboard_tab not in ('monthly', 'overview', 'weekly')
    owner = getattr(source, '_canonical_profile', None)
    email = str(source.email or '').strip().casefold()
    actual, modules, sessions = [], [], []
    progress_records = []
    historical = None
    targets = targets if targets is not None else (canonical_learning.targets_for(owner) if owner is not None and section not in ('weekly', 'my-learning', 'learning') else {})
    contract_data = {
        'months': {
            month: {'label': month, 'topics': [], 'planned': hours, 'source': 'ssot'}
            for month, hours in targets.items()
        },
        'contractStatus': 'ready' if targets else 'not-available',
        'programmeStartDate': date_only(owner.get('start_date')) if owner else None,
        'programmeEndDate': date_only(owner.get('end_date')) if owner else None,
    }
    profile = getattr(source, '_case_file_profile', None) or (
        LearnerProfile.objects.filter(enrolment_id=source.pk)
        .only(*TRAINING_PLAN_PROFILE_FIELDS)
        .first()
        if section not in ('contract', 'learning', 'my-learning') else None
    )
    with connections['enrolment'].cursor() as cur:
        if section == 'contract':
            return contract_data
        if section != 'learning' and owner is not None and not weekly_spine and not compact_monthly:
            # Hours and module progress read lineage, never raw evidence bodies
            # (as the canonical metrics read), so the payload JSON stays in Neon.
            progress_records = records if records is not None else canonical_learning.entries_for(owner, lineage_only=True)
            if section not in ('weekly', 'my-learning'):
                actual = _canonical_actual_rows(owner, progress_records)
        if section == 'weekly' and hasattr(source, '_dashboard_module_ids'):
            ids, links = source._dashboard_module_ids, {}
        else:
            ids, links = plan_module_ids(cur, source, owner)
        if ids:
            # cohort_id is read so the curriculum scheduler can resolve this
            # module's cohort holidays -- including the ones a delivery team
            # unticked. It is not published to the learner.
            description_column = 'm.description' if rich_modules else "''::text AS description"
            cur.execute(f'''SELECT m.module_catalogue_id AS id,m.title,{description_column},m.start_date,m.end_date,m.tutor_name,
                coalesce(nullif(btrim(g.coach_name),''),m.coach_name) AS coach_name,
                m.programme_name,m.cohort_name,m.group_name,m.total_otjh,m.weeks_number,m.sessions_number,
                m.cohort_id,
                coalesce(nullif(m.session_week_day,''),g.session_week_day) AS session_week_day,
                coalesce(nullif(m.session_start_time,''),g.session_start_time) AS session_start_time,
                coalesce(nullif(m.session_end_time,''),g.session_end_time) AS session_end_time
                FROM curriculum.modules m LEFT JOIN curriculum.groups g ON g.group_id=m.group_id
                WHERE m.module_catalogue_id=ANY(%s) ORDER BY m.title''', [ids])
            module_rows = rows(cur)
            modules = [plan_module(row) for row in module_rows]
            by_id = {module['id']: module for module in modules}
            week_counts = defaultdict(int)
            weeks_by_number = {}
            outcomes_column = 'w.learning_outcomes' if rich_modules else "'[]'::jsonb AS learning_outcomes"
            # Only module progress reads the authored live-session count (as its
            # attendance target); Monthly's compact spine returns no progress.
            live_sessions = '0 AS live_session_count' if compact_monthly else '''(SELECT count(*) FROM curriculum.components c
                 WHERE c.week_id=w.id AND c.module_catalogue_id=w.module_catalogue_id
                   AND lower(replace(coalesce(c.type, ''), '-', '_'))='live_session'
                   AND ''' + visible_child_sql('c') + '''
                ) AS live_session_count'''
            # My Learning counts authored sessions once for the complete module
            # set rather than executing a correlated component count per week.
            live_join = ''
            live_params = [ids]
            if section == 'my-learning':
                live_sessions = 'coalesce(live_counts.live_session_count,0) AS live_session_count'
                live_join = '''LEFT JOIN (
                    SELECT c.module_catalogue_id,c.week_id,count(*) AS live_session_count
                    FROM curriculum.components c WHERE c.module_catalogue_id=ANY(%s)
                      AND lower(replace(coalesce(c.type,''),'-','_'))='live_session'
                      AND ''' + visible_child_sql('c') + '''
                    GROUP BY c.module_catalogue_id,c.week_id
                ) live_counts ON live_counts.module_catalogue_id=w.module_catalogue_id
                    AND live_counts.week_id=w.id'''
                live_params = [ids, ids]
            cur.execute(f'''SELECT w.id,w.module_catalogue_id,w.week_number,w.title,{outcomes_column},
                w.holiday_note_enabled,w.holiday_note,{live_sessions}
                FROM curriculum.weeks w {live_join}
                WHERE w.module_catalogue_id=ANY(%s)
                  AND ''' + visible_week_sql('w') + '''
                ORDER BY w.display_order,w.week_number,w.id''', live_params)
            for row in rows(cur):
                week_counts[row['module_catalogue_id']] += 1
                outcomes = [text for text in (clean_text(o) if isinstance(o, str) else '' for o in as_list(row['learning_outcomes'])) if text]
                module = by_id.get(row['module_catalogue_id'])
                if module is not None:
                    if is_educational_live_session_week(row.get('title')):
                        module['educational_session_count'] += max(0, int(row.get('live_session_count') or 0))
                    for text in outcomes:
                        if text not in module['learning_outcomes']:
                            module['learning_outcomes'].append(text)
                # The first authored week at a given number wins, matching the
                # display order the module-level aggregate above already reads in.
                key = (row['module_catalogue_id'], row['week_number'])
                if key not in weeks_by_number:
                    weeks_by_number[key] = {
                        'id': row['id'], 'title': clean_text(row['title']), 'learningOutcomes': outcomes,
                        # Published only when the author turned the switch on.
                        # Text alone is a draft, and a draft is nobody's to read.
                        'holidayNote': clean_text(row['holiday_note']) if row['holiday_note_enabled'] else '',
                    }
            if section == 'weekly':
                attach_curriculum_slots(module_rows, by_id, week_counts, weeks_by_number, strict=True)
            else:
                attach_curriculum_slots(module_rows, by_id, week_counts, weeks_by_number)
            if not rich_modules:
                for module in modules:
                    module.pop('description', None)
                    module.pop('learning_outcomes', None)
                    for slot in module.get('curriculumSlots', []):
                        slot.pop('learningOutcomes', None)
            if section != 'learning' and not weekly_spine and not compact_monthly:
                sessions = plan_sessions(cur, email, ids)
    if section == 'weekly':
        return {'modules': modules, 'sessions': sessions,
                'moduleActivityHours': module_activity_hours(progress_records, modules) if owner is not None else None}
    if section == 'learning':
        return {
            'modules': modules,
            'moduleLinks': links,
            'generatedAt': datetime.now(timezone.utc).isoformat(),
        }
    if section == 'my-learning':
        # One owner for cards, their accepted hours and upcoming sessions. Do
        # not build programme allocations, reviews or coach contact details.
        module_progress = _canonical_module_progress(progress_records, modules) if owner is not None else {}
        if module_progress:
            from .live_session_progress import read_live_credits, supplement_module_hours
            module_progress = supplement_module_hours(module_progress, progress_records,
                read_live_credits(source, module_ids=[module['id'] for module in modules]))
        return {'modules': modules, 'moduleLinks': links, 'sessions': sessions,
                'moduleProgress': module_progress,
                'generatedAt': datetime.now(timezone.utc).isoformat()}
    # The overview must never wait for an Azure PDF download. Older clients
    # still receive the complete response when no section was requested.
    contact = current_coach(source, profile, historical)
    coach_name, coach_email = contact['coach_name'], contact['coach_email']
    if not coach_name and not coach_email and source_coach(source) is None:
        coach_name = assigned_group_coach(source, modules)
    reviews = plan_reviews(source, profile, ('mcr', 'progress-review', 'student-support'))
    coach = {'name': coach_name, 'bookingUrl': booking_url(coach_email)}
    if coach_email:
        coach['email'] = coach_email
    phone = None if compact_monthly else coach_phone(coach_email)
    if phone:
        coach['phone'] = phone
    if compact_monthly:
        return {**contract_data, 'modules': modules, 'moduleLinks': links, 'reviews': reviews, 'coach': coach}
    module_progress = _canonical_module_progress(progress_records, modules) if owner is not None else {}
    if module_progress:
        from .live_session_progress import read_live_credits, supplement_module_hours
        module_progress = supplement_module_hours(module_progress, progress_records,
            read_live_credits(source, module_ids=[module['id'] for module in modules]))
    return {**contract_data, 'actual': actual, 'actualAvailable': owner is not None,
            'moduleActivityHours': module_activity_hours(progress_records, modules) if owner is not None else None,
            'moduleProgress': module_progress,
            'modules': modules, 'moduleLinks': links,
            'sessions': sessions, 'reviews': reviews, 'coach': coach,
            'generatedAt': datetime.now(timezone.utc).isoformat()}


@require_GET
@learner_self_or_staff(kwarg='pk')
@journal_sources.learner_journal_view
def training_plan_dashboard(request, kind, pk):
    model = SOURCE_MODELS.get(kind)
    if model is None:
        return JsonResponse({'error': 'Learner not found.'}, status=404)
    section = request.GET.get('section')
    measurement = None
    try:
        with measure_projection('training-plan-dashboard', kind=kind, learner_id=pk, section=section) as measurement:
            from .case_file_sources import source_for_case_file
            source = source_for_case_file(request, kind, pk)
            if source is None:
                source = model.all_learners.only(*TRAINING_PLAN_SOURCE_FIELDS).get(pk=pk)
            source._canonical_profile = canonical_learning.require_profile(pk)
        # ``learning`` is the detailed weekly dashboard projection.  Keep it
        # on the same read path as overview so clients can request the weekly
        # modules/sessions without being rejected by the section guard.
            if section not in (None, 'overview', 'learning', 'contract', 'upcoming', 'my-learning'):
                return JsonResponse({'error': 'Invalid training plan section.'}, status=400)
            with measurement.stage(section or 'complete'):
                payload = read_dashboard(source, section=section)
    except model.DoesNotExist:
        return JsonResponse({'error': 'Learner not found.'}, status=404)
    except LookupError as error:
        return JsonResponse({'error': str(error)}, status=404)
    except ServiceError as error:
        return JsonResponse({'error': str(error)}, status=error.status)
    except DatabaseError as error:
        cause = getattr(error, '__cause__', None)
        sqlstate = getattr(error, 'sqlstate', None) or getattr(cause, 'sqlstate', None)
        log.exception(
            'Could not read training-plan dashboard exception_class=%s sqlstate=%s section=%s kind=%s learner_id=%s stage=%s',
            type(error).__name__, sqlstate, section, kind, pk,
            (measurement.failed_stage or measurement.current_stage) if measurement else 'source',
        )
        return JsonResponse({'error': 'Could not load your training plan. Please try again.'}, status=503)
    response = JsonResponse(payload)
    response['Cache-Control'] = 'private, no-store'
    return response
