import hashlib
import json
import logging
import re
from decimal import Decimal, InvalidOperation

from django.conf import settings

from . import repository as repo
from .coach_booking import booking_url

logger = logging.getLogger(__name__)
SUPPORT = 'Please contact your coach to complete the review and signing of your previous learning record before entering the LMS.'


class ServiceError(Exception):
    def __init__(self, message, code='invalid_request', status=400):
        super().__init__(message)
        self.code, self.status = code, status


def enabled():
    return getattr(settings, 'OLD_OTJH_ENABLED', False)


def identity_error():
    # No email, name, signature or other learner's details in the logs.
    logger.warning('old_otjh_identity_link_requires_review')
    raise ServiceError('We could not confirm your previous learning record. Please contact support.',
                       'identity_review_required', 409)


def normalize(value):
    return str(value or '').strip().lower()


def resolve_record(learner_id, *, links=None, history=None):
    learner = repo.student(learner_id)
    if not learner or 'aptem_id' not in learner:
        identity_error()
    raw = str(learner.get('aptem_id') or '').strip()
    if not raw:
        return {**learner, 'aptem_id': None}
    if not re.fullmatch(r'[0-9]{1,19}', raw) or not 0 < int(raw) <= 9223372036854775807:
        identity_error()
    aptem_id = int(raw)
    links = repo.linked_learners(aptem_id) if links is None else links
    history = repo.historical_learner(aptem_id) if history is None else history
    if len(links) != 1 or len(history) != 1:
        identity_error()
    old = history[0]
    return {**learner, 'aptem_id': aptem_id, 'lms_id': old['learner_id'],
            'name': old.get('learner_name') or learner['name'],
            'programme': old.get('programme_name') or learner['programme'],
            'planned_hours_monthly': old.get('planned_hours_monthly'),
            'coach_email': old.get('coach_email'), 'coach_name': old.get('coach_name')}


def resolve_authenticated_learner(account):
    if not account or not account.is_active or account.role != 'learner' or account.subject_type != 'learner':
        raise ServiceError('Learner access is required.', 'forbidden', 403)
    learner = resolve_record(account.subject_id)
    if learner['aptem_id'] and (not normalize(account.email)
                               or normalize(account.email) != normalize(learner['email'])):
        identity_error()
    return learner


def coach_actor(account):
    if not account or not account.is_active or account.role not in {'admin', 'staff'} or account.subject_type != 'staff':
        raise ServiceError('Coach access is required.', 'forbidden', 403)
    staff = repo.staff(account.subject_id)
    access = normalize(staff.get('access')) if staff else ''
    if access not in {'coach', 'super-admin', 'record-monitor'} or not normalize(staff.get('email')):
        raise ServiceError('Coach access is required.', 'forbidden', 403)
    return {'email': normalize(staff['email']), 'role': {'super-admin': 'admin', 'record-monitor': 'monitor'}.get(access, 'coach')}


def is_monitor(account):
    if not account or account.subject_type != 'staff':
        return False
    if hasattr(account, '_staff_access'):
        return account._staff_access == 'record-monitor'
    staff = repo.staff(account.subject_id)
    return bool(staff and normalize(staff.get('access')) == 'record-monitor')


def require_writer(actor_role):
    if actor_role not in {'learner', 'coach', 'admin'}:
        raise ServiceError('This account can view records only.', 'read_only', 403)


def coach_learner(account, aptem_id):
    actor = coach_actor(account)
    try:
        aptem_id = int(aptem_id)
    except (ValueError, TypeError):
        raise ServiceError('Learner not found.', 'not_found', 404)
    if not 0 < aptem_id <= 9223372036854775807:
        raise ServiceError('Learner not found.', 'not_found', 404)
    historical = repo.historical_learner(aptem_id)
    if len(historical) != 1 or (actor['role'] == 'coach' and normalize(historical[0]['coach_email']) != actor['email']):
        raise ServiceError('Learner not found.', 'not_found', 404)
    links = repo.linked_learners(aptem_id)
    if len(links) != 1:
        raise ServiceError('The learner link needs administrative review.', 'identity_review_required', 409)
    if actor['role'] == 'monitor' and normalize(historical[0].get('programme_status')) != 'active':
        raise ServiceError('Learner not found.', 'not_found', 404)
    learner = resolve_record(links[0]['id'], links=links, history=historical)
    if actor['role'] == 'monitor':
        learner['_read_only'] = True
    return learner, actor['role']


def validate_month(month):
    if not isinstance(month, str) or not re.fullmatch(r'[0-9]{4}-(0[1-9]|1[0-2])', month) or month > repo.CUTOFF:
        raise ServiceError('Choose a valid month through August 2026.')
    return month


def require_month(transition, month):
    validate_month(month)
    if not transition or month not in transition['required_months']:
        raise ServiceError('This month is not part of your previous record review.', 'not_found', 404)


def transition_for(learner, lock=False):
    transition = repo.transition(learner['id'], lock=lock)
    if transition and transition['aptem_id'] != learner['aptem_id']:
        identity_error()
    return transition


def readable_transition(learner):
    transition = transition_for(learner)
    if learner.get('_read_only'):
        # Viewing must never start/freeze a learner's review. Also expose new
        # source months without adding them to the required signing months.
        months = {m['month'] for m in repo.source_months(learner['aptem_id'])}
        months.update(transition['required_months'] if transition else [])
        return {**(transition or {}), 'required_months': sorted(months)}
    return transition


def _state(month, source, signs, finalization, pending):
    student = signs.get((month, 'learner'))
    coach = signs.get((month, 'coach'))
    complete = bool(student and finalization and finalization['event_type'] == 'finalized')
    can_complete = bool(student and not complete)
    status = 'complete' if complete else 'ready_to_complete' if can_complete else 'awaiting_signature'
    return {'month': month, 'status': status, 'is_required': True,
            'student_signature': student, 'coach_signature': coach,
            'pending_revisions': pending, 'can_complete': can_complete,
            'source_finalization': finalization,
            'row_count': source['row_count'] if source else 0,
            'planned_hours': source['planned_hours'] if source else 0,
            'actual_hours': source['actual_hours'] if source else 0,
            'not_accepted_hours': source['not_accepted_hours'] if source else 0}


def _month_range(start, end):
    year, month = map(int, start.split('-'))
    end_year, end_month = map(int, end.split('-'))
    result = []
    while (year, month) <= (end_year, end_month):
        result.append(f'{year:04d}-{month:02d}')
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return result


def _read_only_months(learner, required, sources):
    """Mirror Audit's start-month-to-August reporting window on read paths."""
    visible = sorted(set(required) | set(sources))
    profile = repo.programme_dates(learner)
    start = str(profile.get('start_date') or '')[:7]
    if not re.fullmatch(r'[0-9]{4}-(0[1-9]|1[0-2])', start):
        return visible
    if start > repo.CUTOFF:
        return []
    return _month_range(start, repo.CUTOFF)


def summary(learner, transition=None):
    if not learner['aptem_id']:
        return {'is_legacy': False, 'state': 'not_required', 'can_access_lms': True, 'months': []}
    transition = transition or transition_for(learner)
    if transition and transition['aptem_id'] != learner['aptem_id']:
        identity_error()
    sources = {s['month']: s for s in repo.source_months(learner['aptem_id'])}
    required = transition['required_months'] if transition else sorted(sources)
    signs = {(s['report_month'], s['signer_role']): s for s in repo.signatures(learner)}
    finals = {f['report_month']: f for f in repo.finalizations(learner['aptem_id'])}
    pending = {p['month']: p['count'] for p in repo.pending_revisions(learner['aptem_id'])}
    visible = _read_only_months(learner, required, sources) if learner.get('_read_only') else required
    months = [{**_state(m, sources.get(m), signs, finals.get(m), pending.get(m, 0)),
               'is_required': m in required} for m in visible]
    targets = learner.get('planned_hours_monthly')
    from learner_api import journal_sources
    if journal_sources.enabled():
        targets = {month: row['planned_hours'] for month, row in sources.items()}
    if isinstance(targets, str):
        try:
            targets = json.loads(targets)
        except ValueError:
            targets = None
    for month in months:
        source = sources.get(month['month'], {})
        month['total_actual_hours'] = source.get('total_actual_hours',
            month['actual_hours'] + month['not_accepted_hours'])
        month['training_plan_target'] = None
        try:
            target = Decimal(str(targets[month['month']])) if isinstance(targets, dict) and month['month'] in targets else None
            if target is not None and target.is_finite() and target >= 0:
                month['training_plan_target'] = target
        except (InvalidOperation, ValueError, TypeError):
            pass
    complete_count = sum(m['status'] == 'complete' and m['is_required'] for m in months)
    complete = bool(transition and required and complete_count == len(required))
    return {'is_legacy': True, 'learner': {'id': learner['id'], 'aptem_id': learner['aptem_id'],
            'name': learner['name'], 'programme': learner['programme'],
            'coach_name': learner.get('coach_name'), 'coach_email': learner.get('coach_email'),
            'coach_booking_url': booking_url(learner.get('coach_email'))}, 'cutoff_date': '2026-08-31',
            'needs_start': transition is None, 'read_only': bool(learner.get('_read_only')), 'total_months': len(required),
            'completed_months': complete_count, 'months': months,
            'can_access_lms': complete, 'state': 'completed' if complete else 'in_progress' if months else 'no_data',
            'completed_at': transition.get('completed_at') if complete else None,
            'message': None if complete else SUPPORT,
            'additional_source_months': sorted(set(sources) - set(required))}


def start(learner, account, actor_role):
    require_writer(actor_role)
    if not learner['aptem_id']:
        return summary(learner)
    with repo.atomic():
        existing = transition_for(learner, lock=True)
        if existing:
            return summary(learner, existing)
        months = [m['month'] for m in repo.source_months(learner['aptem_id'])]
        if not months:
            return summary(learner)
        transition = repo.create_transition(learner, months)
        # Row creation is protected by the unique constraint. Only its creator
        # records a start event, even when browser/coach requests arrive together.
        existing_events = repo.query(f'SELECT id FROM {repo.EVENTS} WHERE transition_id=%s LIMIT 1', [transition['id']])
        if not existing_events:
            repo.event(transition['id'], None, 'started', account, actor_role, {'required_months': months})
        return summary(learner, transition)


def digest(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def month_detail(learner, month, *, demo=False):
    record = summary(learner)
    state = review_month(record, month)
    rows = repo.month_rows(learner, month, **({'demo': True} if demo else {}))
    return {**state, 'rows': rows, 'profile': repo.report_profile(learner), 'snapshot_digest': digest(rows), 'demo_only': demo}


def review_month(record, month):
    validate_month(month)
    if record.get('needs_start') and not record.get('read_only'):
        raise ServiceError('Start your previous record review first.', 'not_found', 404)
    state = next((item for item in record['months'] if item['month'] == month), None)
    if state is None:
        raise ServiceError('This month is not part of your previous record review.', 'not_found', 404)
    return state


def signoff_state(learner, month):
    """Small live read: no activity content, profile or full report rebuild."""
    require_month(readable_transition(learner), month)
    signs = {s['signer_role']: s for s in repo.signatures(learner) if s['report_month'] == month}
    return {'month': month, 'student_signature': signs.get('learner'), 'coach_signature': signs.get('coach')}


def signing_review(learner, month, actor_role):
    require_writer(actor_role)
    state = review_month(summary(learner), month)
    own = 'student_signature' if actor_role == 'learner' else 'coach_signature'
    if (state['status'] == 'complete' and (actor_role == 'learner' or state[own])) or (state[own] and not state['can_complete']):
        return {'month': month, 'ready': False, 'reason': 'Your signature is already saved for this month.'}
    rows = repo.month_rows(learner, month)
    return {'month': month, 'ready': True, 'snapshot_digest': digest(rows),
            'reason': 'The learner signature is saved. This month will be completed.' if state['can_complete'] else None,
            'issues': []}


def sign_months(learner, months, account, actor_role, image_bytes, request_metadata):
    """One confirmed capture for the entire stored month scope, without content I/O.

    A learner signature completes the record. The receipt records monthly totals,
    not a claim that material availability or individual activity bodies were checked.
    """
    require_writer(actor_role)
    if not isinstance(months, list) or not 1 <= len(months) <= 240:
        raise ServiceError('Choose the months in your previous learning record.')
    if any(not isinstance(month, str) for month in months):
        raise ServiceError('Refresh the page to use the current signing form.', 'refresh_required', 409)
    selected = [validate_month(month) for month in months]
    if len(set(selected)) != len(selected):
        raise ServiceError('Choose each month only once.')
    role = 'learner' if actor_role == 'learner' else 'coach'
    own = 'student_signature' if role == 'learner' else 'coach_signature'
    from . import storage
    file_id = None
    signed, skipped, completed = [], [], []
    record = summary(learner)
    if record.get('needs_start') or set(selected) != {m['month'] for m in record['months'] if m['is_required']}:
        raise ServiceError('Your month list has changed. Refresh the page before signing.', 'record_changed', 409)
    needs_image = any(not m[own] and (m['status'] != 'complete' or role == 'coach') for m in record['months'])
    try:
        # A single capture is shared only across this learner's monthly signoffs.
        # Keep Azure upload outside the DB lock and perform it just once.
        if needs_image:
            file_id = storage.save(image_bytes)
        with repo.atomic():
            transition = transition_for(learner, lock=True)
            if not transition or set(selected) != set(transition['required_months']):
                raise ServiceError('Your month list has changed. Refresh the page before signing.', 'record_changed', 409)
            fresh = summary(learner, transition)
            states = {m['month']: m for m in fresh['months']}
            for month in selected:
                state = states[month]
                if state['status'] == 'complete' and (role == 'learner' or state[own]):
                    skipped.append(month)
                    continue
                if not state[own] and file_id is None:
                    raise ServiceError('Your signatures have changed. Refresh the page before signing.', 'record_changed', 409)
                receipt = {'schema': repo.VERSION, 'scope': 'monthly_summary',
                           'aptem_id': learner['aptem_id'], 'month': month,
                           **{key: state[key] for key in ('row_count', 'planned_hours', 'actual_hours',
                                                         'not_accepted_hours', 'pending_revisions')}}
                snapshot = digest([receipt])
                if not state[own]:
                    repo.save_signature(learner, month, role, account.display_name or '', file_id, snapshot)
                    repo.event(transition['id'], month, 'signed', account, actor_role,
                               {**request_metadata, 'file_id': file_id, 'signer_role': role,
                                'file_sha256': hashlib.sha256(image_bytes).hexdigest(),
                                'snapshot_digest': snapshot, 'snapshot_kind': 'monthly_summary',
                                'capture_scope': 'all_required_months', 'confirmed_months': selected,
                                'material_check_performed': False})
                    signed.append(month)
                else:
                    skipped.append(month)
                if state['status'] != 'complete' and (role == 'learner' or state['student_signature']):
                    repo.finalize(learner, month, snapshot, state['row_count'], account,
                                  metadata={'snapshot_kind': 'monthly_summary', 'completion_rule': 'learner_signature',
                                            'material_check_performed': False})
                    repo.event(transition['id'], month, 'completed', account, actor_role,
                               {'snapshot_digest': snapshot, 'completion_method': 'record_signoff',
                                'completion_rule': 'learner_signature', 'material_check_performed': False})
                    completed.append(month)
            if role == 'learner' and (signed or completed):
                repo.save_learner_signature(learner, account.display_name or '', image_bytes)
            result = summary(learner, transition)
            result['completed_at'] = repo.set_completed(transition['id'], result['can_access_lms'])
    except Exception:
        if file_id:
            storage.delete(file_id)
        raise
    if file_id and not signed:
        storage.delete(file_id)
    return {'signed_months': signed, 'skipped_months': skipped, 'completed_months': completed, 'summary': result}


def activity_content(learner, month, row_id):
    require_month(readable_transition(learner), month)
    if not re.fullmatch(r'[0-9]{1,18}', str(row_id)):
        raise ServiceError('Activity not found.', 'not_found', 404)
    row = repo.activity_row(learner, month, int(row_id))
    if not row:
        raise ServiceError('Activity not found.', 'not_found', 404)
    return {'id': row['id'], 'parts': repo.activity_parts(learner, row)}


def content_review(learner, month):
    require_month(readable_transition(learner), month)
    rows = repo.month_rows(learner, month)
    return {**repo.content_review(learner, rows), 'snapshot_digest': digest(rows)}


def _locked_scope(learner, month):
    transition = transition_for(learner, lock=True)
    require_month(transition, month)
    state = next(m for m in summary(learner, transition)['months'] if m['month'] == month)
    return transition, state


def _review_snapshot_metadata(rows):
    provisional = [row for row in rows if row.get('provisional')]
    metadata = {'material_check_performed': False}
    if provisional:
        metadata.update({
            'provisional_snapshot': True,
            'provisional_source': 'formula_reconstruction',
            'provisional_row_count': len(provisional),
            'provisional_fields': sorted({field for row in provisional
                                          for field in (row.get('provisional_fields') or [])}),
        })
    return metadata


def _finalize_signed_month(learner, month, rows, transition, account, actor_role):
    # Called under the transition lock after checking the report snapshot.
    # Signing does not depend on material availability or pending hour revisions.
    snapshot = digest(rows)
    metadata = _review_snapshot_metadata(rows)
    repo.finalize(learner, month, snapshot, len(rows), account, metadata=metadata)
    repo.event(transition['id'], month, 'completed', account, actor_role,
               {'snapshot_digest': snapshot, 'completion_method': 'automatic_after_signing', **metadata})


def sign(learner, month, account, actor_role, image_bytes, expected_digest, request_metadata):
    require_writer(actor_role)
    role = 'learner' if actor_role == 'learner' else 'coach'
    from . import storage
    file_id = None
    try:
        with repo.atomic():
            transition, state = _locked_scope(learner, month)
            if state['status'] == 'complete' and (role == 'learner' or state['coach_signature']):
                raise ServiceError('This month is complete and its signatures are read-only.', 'month_complete', 409)
            rows = repo.month_rows(learner, month)
            current_digest = digest(rows)
            if expected_digest != current_digest:
                raise ServiceError('The record has changed. Review the latest details before signing.', 'record_changed', 409)
            file_id = storage.save(image_bytes)
            repo.save_signature(learner, month, role, account.display_name or '', file_id, current_digest)
            snapshot_metadata = _review_snapshot_metadata(rows)
            repo.event(transition['id'], month, 'signed', account, actor_role,
                       {**request_metadata, 'file_id': file_id, 'signer_role': role,
                        'file_sha256': hashlib.sha256(image_bytes).hexdigest(), 'snapshot_digest': current_digest,
                        **snapshot_metadata})
            # Admins may sign a month on behalf of its learner, but their image
            # must not replace the learner's saved profile signature.
            if role == 'learner' and account.role == 'learner':
                repo.save_learner_signature(learner, account.display_name or '', image_bytes)
            if state['status'] != 'complete' and (role == 'learner' or state['student_signature']):
                _finalize_signed_month(learner, month, rows, transition, account, actor_role)
            result = summary(learner, transition)
            repo.set_completed(transition['id'], result['can_access_lms'])
    except Exception:
        if file_id:
            storage.delete(file_id)
        raise
    return month_detail(learner, month)


def complete(learner, month, account, actor_role):
    if actor_role != 'learner':
        raise ServiceError('Only the learner can complete their review.', 'forbidden', 403)
    with repo.atomic():
        transition, state = _locked_scope(learner, month)
        if state['status'] == 'complete':
            return month_detail(learner, month)
        if not state['student_signature']:
            raise ServiceError('The learner signature is required.', 'signatures_required', 409)
        rows = repo.month_rows(learner, month)
        repo.finalize(learner, month, digest(rows), len(rows), account, metadata={'material_check_performed': False})
        repo.event(transition['id'], month, 'completed', account, actor_role,
                   {'snapshot_digest': digest(rows), 'material_check_performed': False})
        result = summary(learner, transition)
        repo.set_completed(transition['id'], result['can_access_lms'])
    return month_detail(learner, month)


def reopen(learner, month, account, actor_role, reason):
    if actor_role != 'admin':
        raise ServiceError('Administrator access is required.', 'forbidden', 403)
    if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 1000:
        raise ServiceError('Provide a reason for reopening the month.')
    with repo.atomic():
        transition, state = _locked_scope(learner, month)
        if state['source_finalization'] and state['source_finalization']['event_type'] == 'finalized':
            repo.finalize(learner, month, None, state['row_count'], account, reason.strip())
            repo.event(transition['id'], month, 'reopened', account, actor_role, {'reason': reason.strip()})
            repo.set_completed(transition['id'], False)
    return month_detail(learner, month)


def refresh_months(learner, account, actor_role, reason):
    if actor_role != 'admin':
        raise ServiceError('Administrator access is required.', 'forbidden', 403)
    if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 1000:
        raise ServiceError('Provide a reason for changing the review months.')
    with repo.atomic():
        transition = transition_for(learner, lock=True)
        if not transition:
            return start(learner, account, actor_role)
        # Preserve already required months even if an external system removed
        # their rows. Only add actual source months, never invent calendar gaps.
        months = sorted(set(transition['required_months']) | {m['month'] for m in repo.source_months(learner['aptem_id'])})
        if months != transition['required_months']:
            repo.query(f'UPDATE {repo.TRANSITIONS} SET required_months=%s::jsonb, completed_at=NULL, updated_at=now() WHERE id=%s',
                       [json.dumps(months), transition['id']])
            repo.event(transition['id'], None, 'months_refreshed', account, actor_role,
                       {'reason': reason.strip(), 'required_months': months})
    return summary(learner)
