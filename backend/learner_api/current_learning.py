"""Read current LMS saves alongside consolidated records; never write or sync."""
from datetime import datetime
import json
from math import isfinite
from zoneinfo import ZoneInfo

from old_otjh.repository import query
from .active_users import completed_hours_value_from_progress
from .progress_rules import progress_counts_as_achieved
from .retake_timing import attempt_order, native_choices, measured_choice
from .extra_activity_month import valid_activity_month


def instant(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return value.astimezone(ZoneInfo('Europe/London')) if value else None


def actual_time_seconds(value):
    """Return saved reflection time as seconds without changing its source value.

    Native reflection saves use decimal hours. Older submissions may contain a
    legacy ``MM:SS`` clock, while imported records can contain ``HH:MM:SS``.
    Invalid or empty values remain unavailable.
    """
    if value in (None, ''):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if ':' in text:
            parts = text.split(':')
            if len(parts) == 2:
                minutes, seconds = (int(part) for part in parts)
                if minutes < 0 or not 0 <= seconds < 60:
                    return None
                return minutes * 60 + seconds
            if len(parts) == 3:
                hours_text, minutes_text, seconds_text = parts
                if not hours_text.isdigit() or not minutes_text.isdigit():
                    return None
                hours = int(hours_text)
                minutes = int(minutes_text)
                seconds = float(seconds_text)
                if not 0 <= minutes < 60 or not isfinite(seconds) or not 0 <= seconds < 60:
                    return None
                return hours * 3600 + minutes * 60 + seconds
            return None
        hours = float(text)
        return hours * 3600 if isfinite(hours) and hours >= 0 else None
    except (TypeError, ValueError):
        return None


def is_unsynchronised_direct_save(record):
    """A current LMS save that no sync or reconciliation has given seconds yet.

    Imported/normalised seconds (including zero) and reporting segments remain
    authoritative, so those rows are never unsynchronised.
    """
    return (record.get('component_link_source') in {'direct', 'quiz_ref'}
            and not record.get('source_system') and record.get('actual_seconds') is None
            and not record.get('segments'))


def project_current(records, markings):
    """Project unsynchronised direct saves using the existing time/pass rules.

    Planned duration is never substituted for recorded time.
    """
    result, native, candidates = [], {}, []
    retained = {source_reference(p) for p in records if p.get('source_system')}
    # A staff hours correction is scoped to one activity. Retain every attempt
    # as evidence, but do not add native attempt time to the corrected ledger
    # allocation. No other activity's dedupe policy changes.
    manual_keys = {(p.get('learner_id'), p.get('component_ref')) for p in records
                   if p.get('actual_basis') == 'staff:module-progress'
                   and p.get('actual_seconds') is not None and p.get('component_ref')}
    for original in records:
        p = dict(original)
        if not is_unsynchronised_direct_save(p):
            result.append(p)
            candidates.append(p)
            continue
        if p.get('kind') == 'activity_event' or not p.get('submitted_at'):
            continue
        if (p.get('learner_id'), p.get('component_ref')) in manual_keys:
            continue
        if f"progress:{p['id']}" in retained:
            continue
        at = instant(p['submitted_at'])
        accepted = progress_counts_as_achieved(p.get('kind'), p.get('passed'))
        timing = {k: v for k, v in p.items() if k not in {'expected_otjh', 'expectedOtjh'}}
        seconds = completed_hours_value_from_progress([timing]) * 3600
        component = p.get('component_ref')
        marking = markings.get(str(component))
        if p.get('component_type') == 'assignment':
            accepted = bool(marking and marking.get('status') in {'accepted', 'partial'})
            marked_seconds = actual_time_seconds(marking.get('actual_time_hours')) if marking else None
            if marked_seconds is not None:
                seconds = marked_seconds
        p.update(accepted=accepted, completed=accepted, actual_seconds=seconds,
                 reporting_month=at.strftime('%Y-%m'), reporting_started_at=at,
                 reporting_ended_at=at, activity_status='Completed' if accepted else 'Submitted')
        candidates.append(p)
        # Count the latest eligible attempt, even when its duration decreases.
        # Failed retries cannot displace an accepted completion.
        key = ('quiz', p['quiz_ref']) if p.get('quiz_ref') else ('component', component) if component else ('entry', p['id'])
        key = (p.get('learner_id'), *key)
        previous = native.get(key)
        if previous is None or (accepted, attempt_order(p['submitted_at'], p['id'])) > (
                previous['accepted'], attempt_order(previous['submitted_at'], previous['id'])):
            native[key] = p
    return native_choices(result + list(native.values()), candidates)


def source_payload_metadata(value):
    """Read keyed lineage metadata without changing non-object saved payloads."""
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


def source_reference(record):
    return source_payload_metadata(record.get('source_payload')).get('original_source_ref')


def merge_attempts(records, attempts):
    """Project completion and independently verified local time beside history."""
    result = [dict(p) for p in records]
    superseded = set()
    # One record can be reached through several lineage links. Index positions,
    # preserving original row order and merging each matching row only once.
    by_route = {}
    for index, p in enumerate(result):
        source_ref = source_reference(p)
        routes, typed_courses = set(), set()
        for link in [*(p.get('sources') or []), *(p.get('historical_components') or [])]:
            if link.get('source_system') == 'old_lms':
                course = str(link.get('source_course_ref'))
                typed_courses.add(course)
                activity = link.get('source_activity_id')
                if isinstance(activity, str):
                    if activity.startswith('material:'):
                        routes.add(f"la:{course}:{activity[9:]}")
                    elif activity.startswith('quiz:'):
                        routes.add(f"subject:quiz:{course}:{activity[5:]}")
        # Typed lineage overrides a historical untyped reference for that
        # course only. A quiz and a material can share the same numeric id.
        if isinstance(source_ref, str):
            parts = source_ref.split(':')
            course = (parts[1] if len(parts) == 3 and parts[0] == 'la' else
                      parts[2] if len(parts) == 4 and parts[:2] == ['subject', 'quiz'] else None)
            if course is not None and course not in typed_courses:
                routes.add(source_ref)
        for route in routes:
            by_route.setdefault(route, []).append(index)
    for attempt in attempts:
        catalogue_kind = attempt.get('catalogue_kind', 'material')
        ref = f"{'la' if catalogue_kind == 'material' else 'subject:quiz'}:{attempt['group_id']}:{attempt['activity_id']}"
        route = {'source_system': 'old_lms', 'source_course_ref': str(attempt['group_id']),
                 'source_activity_id': f"{catalogue_kind}:{attempt['activity_id']}"}
        matches = [result[index] for index in by_route.get(ref, ())]
        has_progress_record = bool(matches)
        if not matches:
            at = instant(attempt['submitted_at'])
            record_id = (f"subject:{attempt['group_id']}:{attempt['activity_id']}" if catalogue_kind == 'material'
                         else f"subject:quiz:{attempt['group_id']}:{attempt['activity_id']}")
            matches = [{'id': record_id,
                'component_title': attempt.get('title'), 'kind': 'quiz', 'accepted': False,
                'actual_seconds': None, 'ksbs': [], 'sources': [route], 'segments': [],
                'source_payload': {'original_source_ref': ref},
                'reporting_month': at.strftime('%Y-%m'), 'reporting_started_at': at}]
            result.extend(matches)
            by_route.setdefault(ref,[]).append(len(result)-1)
        for p in matches:
            p['completed'] = bool(p.get('completed', p.get('accepted')) or attempt['completed'])
            if attempt.get('best_percent') is not None:
                maximum = p.get('total_score')
                old = float(p['achieved_score']) / float(maximum) * 100 if maximum and p.get('achieved_score') is not None else None
                p['achieved_score'] = max(float(attempt['best_percent']), old) if old is not None else float(attempt['best_percent'])
                p['total_score'] = 100
        measured = measured_attempt_record(attempt, route, has_accepted_history=any(
            p.get('accepted') is True and p.get('actual_seconds') is not None for p in matches))
        if measured and not any(str(p['id']) == measured['id'] for p in result):
            if measured.pop('retake_replace_existing', False):
                measured['retake_superseded_ids'] = [str(p['id']) for p in matches if p.get('id')]
                measured['ksbs'] = sorted({code for old in matches for code in old.get('ksbs', [])})
                measured['achieved_score'] = attempt.get('best_percent')
                measured['total_score'] = 100
                for field in ('module_ref', 'module_title', 'group_ref', 'group_title'):
                    measured[field] = next((old[field] for old in matches if old.get(field)), None)
                superseded.update(str(old['id']) for old in matches)
                measured['hours_only'] = False
            else:
                measured['hours_only'] = has_progress_record
            # Additional time for an existing activity is evidence, not another
            # activity in programme/recorded-course completion denominators.
            result.append(measured)
    return [record for record in result if str(record['id']) not in superseded]


def measured_attempt_record(attempt, route, *, has_accepted_history=False):
    """One selected completed local duration per placement, as for native saves.

    Untimed historical attempts contribute no hours. Keep this row separate
    from audited source time, and give retries/re-reads a stable identity.
    """
    candidates = []
    saved_attempts = attempt.get('timed_attempts') or []
    if isinstance(saved_attempts, str):
        saved_attempts = json.loads(saved_attempts)
    for saved in saved_attempts:
        timing = saved.get('timing') or {}
        seconds = timing.get('verifiedSeconds')
        if (saved.get('completed') is True and timing.get('version') == 1
                and isinstance(seconds, int) and not isinstance(seconds, bool) and seconds >= 0):
            candidates.append(saved)
    if not candidates:
        return None
    saved, replace_history = measured_choice(candidates, has_accepted_history)
    if saved is None:
        return None
    timing = saved['timing']
    at = instant(timing.get('declaredCompletedAt') or timing.get('submittedAt'))
    if at is None:
        return None
    ident = f"subject:timing:{attempt['group_id']}:{attempt.get('catalogue_kind', 'material')}:{attempt['activity_id']}"
    return {'id': ident, 'component_title': attempt.get('title'),
        'kind': 'quiz' if attempt.get('catalogue_kind') == 'quiz' else 'component',
        'accepted': True, 'completed': True, 'actual_seconds': timing['verifiedSeconds'],
        'actual_basis': 'legacy_activity_verified_timer', 'ksbs': [], 'sources': [route], 'segments': [],
        'retake_replace_existing': replace_history,
        'reporting_month': at.strftime('%Y-%m'), 'reporting_started_at': at, 'reporting_ended_at': at,
        'source_payload': {'original_source_ref': ident, 'subject_attempt_id': str(saved['id']), 'studyTime': timing}}


def merge_submissions(records, submissions):
    result = list(records)
    ids = {str(p['id']) for p in records}
    components = {str(p.get('component_ref')) for p in records if p.get('component_ref')}
    retained = {source_reference(p) for p in records}
    for s in submissions:
        if (s.get('imported') or f"reflection:{s['id']}" in retained
                or str(s.get('progress_entry_id')) in ids
                or (s.get('component_ref') and str(s['component_ref']) in components)):
            continue
        at = instant(s.get('submitted_at'))
        if at is None or s.get('status') == 'draft':
            continue
        accepted = s.get('status') in {'accepted', 'partial'}
        codes = s.get('ksb_codes') or []
        if isinstance(codes, str):
            codes = json.loads(codes)
        selected_month = s.get('activity_month') if s['activity_type'] == 'extra_activity' else None
        month = selected_month if valid_activity_month(selected_month) else at.strftime('%Y-%m')
        result.append({'id': f"reflection:{s['id']}", 'component_title': s.get('activity_title'),
            'component_ref': s.get('component_ref'), 'module_title': s.get('module_title'),
            'kind': s['activity_type'], 'accepted': accepted, 'completed': accepted,
            'actual_seconds': actual_time_seconds(s.get('actual_time_hours')),
            'reporting_month': month, 'reporting_started_at': at,
            'activity_status': s.get('status'), 'ksbs': codes, 'sources': [], 'segments': [],
            'source_payload': {'original_source_ref': f"reflection:{s['id']}"}})
        if s['activity_type'] == 'extra_activity':
            result[-1].update(actual_basis='extra_activity_input', expected_otjh=0)
    return result


def attempts_relation_exists():
    """Whether the Aptem attempts table exists; a found table is remembered.

    Schema does not change under a running process. An absent answer is
    asked again, so a table provisioned later is still picked up.
    """
    if getattr(attempts_relation_exists, 'present', False):
        return True
    present = bool(query('SELECT to_regclass(%s) AS name',
                         ['"Learner".subject_activity_attempts'])[0]['name'])
    if present:
        attempts_relation_exists.present = True
    return present


def current_records(owner, records):
    submissions = query('''SELECT id,component_ref,progress_entry_id,status,actual_time_hours,
        submitted_at,activity_type,activity_title,module_title,ksb_codes,
        full_submission #>> '{monthlyAssignment,month}' AS activity_month,
        full_submission->>'submissionOrigin' = 'imported_legacy' AS imported
        FROM "Learner".learning_reflection_submissions
        WHERE learner_id=%s AND learner_kind=%s AND activity_type IN ('assignment','extra_activity')
        ORDER BY submitted_at NULLS FIRST,id''', [str(owner['enrolment_id']), owner.get('learner_type')])
    markings = {str(s['component_ref']): s for s in submissions if s.get('component_ref')}
    projected = merge_submissions(project_current(records, markings), submissions)
    return merge_attempts(projected, subject_attempt_rows(owner))


def subject_attempt_rows(owner, *, timed_only=False):
    """Owned persisted attempts, shared by full and hours-only projections."""
    if not owner.get('aptem_id') or not attempts_relation_exists():
        return []
    return query('''SELECT group_id,activity_id,
        coalesce(definition->>'catalogue_kind','material') AS catalogue_kind,bool_or(completed) AS completed,
        max(score_percent) AS best_percent,max(submitted_at) AS submitted_at,
        max(definition->>'title') AS title,
        jsonb_agg(jsonb_build_object('id',id,'completed',completed,'timing',definition->'studyTime'))
            FILTER (WHERE definition ? 'studyTime') AS timed_attempts
        FROM "Learner".subject_activity_attempts
        WHERE enrolment_id=%s AND aptem_id=%s AND submitted_at IS NOT NULL'''
        + (" AND definition ? 'studyTime'" if timed_only else '') + '''
        GROUP BY group_id,activity_id,coalesce(definition->>'catalogue_kind','material')''', [owner['enrolment_id'], owner['aptem_id']])


def current_records_bulk(owners, records_by_learner_id):
    """Project current saves for many canonical learners in bounded queries.

    ``current_records`` is intentionally convenient for a single learner page,
    but calling it for a coach caseload repeats submissions, relation and
    attempt lookups once per learner.  This is the same projection with the
    shared reads performed once and partitioned by stable enrolment identity.
    """
    owners_by_enrolment = {
        int(owner['enrolment_id']): owner
        for owner in (owners or [])
        if owner.get('enrolment_id') is not None
    }
    if not owners_by_enrolment:
        return {}

    identity_pairs = [
        (str(enrolment_id), str(owner.get('learner_type') or ''))
        for enrolment_id, owner in owners_by_enrolment.items()
    ]
    placeholders = ','.join(['(%s,%s)'] * len(identity_pairs))
    params = [value for pair in identity_pairs for value in pair]
    submissions = query(f'''SELECT learner_id,learner_kind,id,component_ref,progress_entry_id,
        status,actual_time_hours,submitted_at,activity_type,activity_title,module_title,ksb_codes,
        full_submission #>> '{{monthlyAssignment,month}}' AS activity_month,
        full_submission->>'submissionOrigin' = 'imported_legacy' AS imported
        FROM "Learner".learning_reflection_submissions
        WHERE (learner_id,learner_kind) IN ({placeholders})
          AND activity_type IN ('assignment','extra_activity')
        ORDER BY submitted_at NULLS FIRST,id''', params)
    submissions_by_identity = {pair: [] for pair in identity_pairs}
    for submission in submissions:
        key = (str(submission.get('learner_id')), str(submission.get('learner_kind') or ''))
        submissions_by_identity.setdefault(key, []).append(submission)

    projected = {}
    for enrolment_id, owner in owners_by_enrolment.items():
        key = (str(enrolment_id), str(owner.get('learner_type') or ''))
        learner_submissions = submissions_by_identity.get(key, [])
        markings = {
            str(submission['component_ref']): submission
            for submission in learner_submissions
            if submission.get('component_ref')
        }
        base_records = records_by_learner_id.get(int(owner['id']), [])
        projected[enrolment_id] = merge_submissions(
            project_current(base_records, markings), learner_submissions,
        )

    attempt_pairs = []
    for enrolment_id, owner in owners_by_enrolment.items():
        raw_aptem_id = owner.get('aptem_id')
        try:
            aptem_id = int(str(raw_aptem_id).strip())
        except (TypeError, ValueError):
            continue
        attempt_pairs.append((enrolment_id, aptem_id))
    if not attempt_pairs:
        return projected

    relation = query('SELECT to_regclass(%s) AS name', ['"Learner".subject_activity_attempts'])
    if not relation or not relation[0].get('name'):
        return projected

    placeholders = ','.join(['(%s,%s)'] * len(attempt_pairs))
    params = [value for pair in attempt_pairs for value in pair]
    attempts = query(f'''SELECT enrolment_id,aptem_id,group_id,activity_id,
        coalesce(definition->>'catalogue_kind','material') AS catalogue_kind,
        bool_or(completed) AS completed,max(score_percent) AS best_percent,
        max(submitted_at) AS submitted_at,max(definition->>'title') AS title,
        jsonb_agg(jsonb_build_object('id',id,'completed',completed,'timing',definition->'studyTime'))
            FILTER (WHERE definition ? 'studyTime') AS timed_attempts
        FROM "Learner".subject_activity_attempts
        WHERE (enrolment_id,aptem_id) IN ({placeholders}) AND submitted_at IS NOT NULL
        GROUP BY enrolment_id,aptem_id,group_id,activity_id,coalesce(definition->>'catalogue_kind','material')''', params)
    attempts_by_identity = {pair: [] for pair in attempt_pairs}
    for attempt in attempts:
        key = (int(attempt['enrolment_id']), int(attempt['aptem_id']))
        attempts_by_identity.setdefault(key, []).append(attempt)
    for enrolment_id, aptem_id in attempt_pairs:
        projected[enrolment_id] = merge_attempts(
            projected[enrolment_id], attempts_by_identity.get((enrolment_id, aptem_id), []),
        )
    return projected
