"""Live, read-only cohort monitoring; six bulk reads, never per-learner I/O."""
from collections import defaultdict
from datetime import datetime, timezone
import json
import math

from . import repository as repo
from .service import ServiceError, _state, normalize


def cohort_records():
    return repo.query('''WITH enrolment_link_counts AS (
        SELECT ltrim(btrim(aptem_id),'0') AS aptem_key, count(*) AS link_count
        FROM enrolment."Created_users"
        GROUP BY ltrim(btrim(aptem_id),'0')
    ), audit_link_counts AS (
        SELECT aptem_id, count(*) AS link_count
        FROM "Last_audit".learners
        GROUP BY aptem_id
    )
        SELECT DISTINCT ON (c.id)
        c.id AS enrolment_id, l.aptem_id AS id, l.learner_name AS name,
        c."Email" AS email, l.programme_name AS programme,
        c."Programme_status" AS enrolment_status, l.programme_status AS audit_status,
        l.coach_name, lower(btrim(coalesce(l.coach_email,''))) AS coach_email,
        coalesce(enrolment_counts.link_count, 0) AS enrolment_links,
        coalesce(audit_counts.link_count, 0) AS audit_links
        FROM enrolment."Created_users" c JOIN "Last_audit".learners l
          ON ltrim(btrim(c.aptem_id),'0')=l.aptem_id::text
        LEFT JOIN enrolment_link_counts enrolment_counts
          ON enrolment_counts.aptem_key=l.aptem_id::text
        LEFT JOIN audit_link_counts audit_counts
          ON audit_counts.aptem_id=l.aptem_id
        WHERE l.aptem_id>0 AND lower(btrim(l.programme_status))='active'
        ORDER BY c.id, l.learner_id''')


def load_records():
    learners = cohort_records()
    if not learners:
        return []
    aptem_ids = list({r['id'] for r in learners})
    enrolment_ids = [r['enrolment_id'] for r in learners]
    sources = repo.query(f'''SELECT aptem_id, month, count(*) AS row_count,
        coalesce(sum(planned_hours),0) AS planned_hours,
        coalesce(sum(actual_hours) FILTER (WHERE accepted),0) AS actual_hours,
        coalesce(sum(actual_hours) FILTER (WHERE NOT accepted),0) AS not_accepted_hours
        FROM {repo.ROWS} WHERE aptem_id=ANY(%s) AND deleted_at IS NULL
        AND month ~ '^[0-9]{{4}}-(0[1-9]|1[0-2])$' AND month<=%s
        GROUP BY aptem_id, month''', [aptem_ids, repo.CUTOFF])
    transitions = repo.query(f'SELECT learner_id, aptem_id, required_months FROM {repo.TRANSITIONS} '
                             'WHERE learner_id=ANY(%s)', [enrolment_ids])
    signs = repo.query(f'''SELECT t.learner_id AS enrolment_id, s.report_month, s.signer_role,
        s.signed_at FROM {repo.SIGNOFFS} s JOIN {repo.TRANSITIONS} t
          ON s.learner_id=t.aptem_id::text AND s.programme_key='otjh-transition:' || t.learner_id::text
        WHERE t.learner_id=ANY(%s) AND s.audit_version=%s
          AND s.review_confirmed IS TRUE AND coalesce(s.signature_data,'')<>'' ''', [enrolment_ids, repo.VERSION])
    finals = repo.query(f'''SELECT DISTINCT ON (aptem_id, report_month)
        aptem_id, report_month, event_type, created_at FROM {repo.FINALIZATIONS}
        WHERE aptem_id=ANY(%s) AND report_month<=%s
        ORDER BY aptem_id, report_month, id DESC''', [aptem_ids, repo.CUTOFF])
    pending = repo.query('''SELECT aptem_id, selected_month AS month, count(*) AS count
        FROM structured_manual_activities.manual_activity_hours_revision
        WHERE aptem_id=ANY(%s) AND status='pending' GROUP BY aptem_id, selected_month''', [aptem_ids])
    return assemble(learners, sources, transitions, signs, finals, pending)


def assemble(learners, sources, transitions, signs, finals, pending):
    source_map, sign_map, final_map, pending_map = (defaultdict(dict) for _ in range(4))
    for row in sources:
        source_map[row['aptem_id']][row['month']] = row
    for row in signs:
        sign_map[row['enrolment_id']][row['report_month'], row['signer_role']] = row
    for row in finals:
        final_map[row['aptem_id']][row['report_month']] = row
    for row in pending:
        pending_map[row['aptem_id']][row['month']] = row['count']
    transition_map = {r['learner_id']: r for r in transitions}
    result = []
    for learner in learners:
        ident, aptem = learner['enrolment_id'], learner['id']
        transition = transition_map.get(ident)
        source = source_map[aptem]
        required = transition['required_months'] if transition else sorted(source)
        if isinstance(required, str):
            required = json.loads(required)
        invalid = (learner['enrolment_links'] != 1 or learner['audit_links'] != 1
                   or bool(transition and transition['aptem_id'] != aptem))
        # Ambiguous identities must not borrow another person's signatures.
        months = [] if invalid else [
            _state(m, source.get(m), sign_map[ident], final_map[aptem].get(m), pending_map[aptem].get(m, 0))
            for m in required]
        total = len(months)
        complete = sum(m['status'] == 'complete' for m in months)
        learner_signed = sum(bool(m['student_signature']) for m in months)
        coach_signed = sum(bool(m['coach_signature']) for m in months)
        revisions = sum(m['pending_revisions'] for m in months)
        ready = bool(total and transition and complete == total)
        status = ('link_issue' if invalid else 'no_data' if not total else 'completed' if ready
                  else 'not_started' if not transition else 'pending_revisions' if revisions
                  else 'awaiting_both' if learner_signed < total and coach_signed < total
                  else 'awaiting_learner' if learner_signed < total
                  else 'awaiting_coach' if coach_signed < total else 'ready_to_complete')
        last_signed = max((str(s['signed_at']) for m in months
                           for s in (m['student_signature'], m['coach_signature']) if s), default=None)
        first_outstanding = next((m['month'] for m in months if m['status'] != 'complete'), None)
        result.append({**{k: v for k, v in learner.items() if k not in {'enrolment_links', 'audit_links'}},
            'status': status, 'needs_start': not transition, 'can_open': not invalid,
            'can_access_lms': ready, 'total_months': total, 'completed_months': complete,
            'remaining_months': total - complete, 'learner_signed_months': learner_signed,
            'coach_signed_months': coach_signed, 'learner_unsigned_months': total - learner_signed,
            'coach_unsigned_months': total - coach_signed, 'pending_revisions': revisions,
            'ready_months': sum(m['can_complete'] for m in months),
            'empty_months': sum(not m['row_count'] for m in months),
            'activity_count': sum(s['row_count'] for s in source.values()) if not invalid else 0,
            'additional_months': len(set(source) - set(required)) if not invalid else 0,
            'first_outstanding_month': first_outstanding, 'last_signed_at': last_signed,
            # Lightweight month data supports totals without material downloads.
            'months': [{'month': m['month'], 'complete': m['status'] == 'complete',
                        'learner_signed': bool(m['student_signature']), 'coach_signed': bool(m['coach_signature'])}
                       for m in months]})
    return result


def matches_status(row, status):
    if status == 'all':
        return True
    if status == 'learner_outstanding':
        return row['learner_unsigned_months'] > 0
    if status == 'coach_outstanding':
        return row['coach_unsigned_months'] > 0
    if status == 'learner_signed':
        return row['total_months'] > 0 and row['learner_unsigned_months'] == 0
    if status == 'coach_signed':
        return row['total_months'] > 0 and row['coach_unsigned_months'] == 0
    if status == 'needs_attention':
        return row['status'] in {'no_data', 'link_issue'} or row['pending_revisions'] > 0 or row['empty_months'] > 0
    if status == 'pending_revisions':
        return row['pending_revisions'] > 0
    return row['status'] == status


STATUSES = {'all', 'completed', 'not_started', 'no_data', 'link_issue', 'pending_revisions',
            'awaiting_both', 'awaiting_learner', 'awaiting_coach', 'ready_to_complete',
            'learner_outstanding', 'coach_outstanding', 'learner_signed', 'coach_signed', 'needs_attention'}


def dashboard(params, *, records=None, updated_at=None):
    try:
        page = max(1, int(params.get('page', 1)))
    except (ValueError, TypeError):
        raise ServiceError('Invalid page.')
    status = params.get('status', 'all')
    if status not in STATUSES:
        raise ServiceError('Invalid review status.')
    search = normalize(params.get('search', ''))[:200]
    coach, programme = params.get('coach'), params.get('programme')
    records = load_records() if records is None else records
    stats = {key: sum(matches_status(r, key) for r in records) for key in
             ('completed', 'not_started', 'learner_outstanding', 'coach_outstanding',
              'learner_signed', 'coach_signed', 'ready_to_complete', 'needs_attention', 'no_data', 'link_issue')}
    stats['total_learners'] = len(records)
    for key in ('total_months', 'completed_months', 'remaining_months', 'learner_signed_months',
                'coach_signed_months', 'learner_unsigned_months', 'coach_unsigned_months',
                'activity_count', 'pending_revisions', 'ready_months', 'additional_months'):
        stats[key] = sum(r[key] for r in records)
    by_month, by_coach = {}, {}
    for row in records:
        coach_key = row['coach_email'] or ''
        group = by_coach.setdefault(coach_key, {'key': coach_key, 'name': row['coach_name'] or 'Unassigned',
            'learners': 0, 'completed': 0, 'outstanding_signatures': 0})
        group['learners'] += 1
        group['completed'] += int(row['can_access_lms'])
        group['outstanding_signatures'] += row['coach_unsigned_months']
        for month in row['months']:
            bucket = by_month.setdefault(month['month'], {'month': month['month'], 'total': 0,
                'complete': 0, 'learner_signed': 0, 'coach_signed': 0})
            bucket['total'] += 1
            for key in ('complete', 'learner_signed', 'coach_signed'):
                bucket[key] += int(month[key])
    filtered = [r for r in records if matches_status(r, status)
        and (not search or search in ' '.join(str(r.get(k) or '') for k in ('name', 'email', 'id')).lower())
        and (coach is None or (r['coach_email'] or '') == coach)
        and (not programme or r['programme'] == programme)]
    filtered.sort(key=lambda r: (normalize(r['name']), r['enrolment_id']))
    page_size = 25
    pages = max(1, math.ceil(len(filtered) / page_size))
    page = min(page, pages)
    return {'stats': stats, 'learners': [{k: v for k, v in r.items() if k != 'months'}
                for r in filtered[(page - 1) * page_size:page * page_size]],
            'total': len(filtered), 'page': page, 'page_size': page_size, 'pages': pages,
            'coaches': sorted(by_coach.values(), key=lambda r: normalize(r['name'])),
            'programmes': sorted({r['programme'] for r in records if r['programme']}),
            'monthly': [by_month[key] for key in sorted(by_month)],
            'updated_at': updated_at or datetime.now(timezone.utc).isoformat(), 'cutoff_date': '2026-08-31',
            'scope': 'Active in the previous audit, linked to enrolment', 'read_only': True}
