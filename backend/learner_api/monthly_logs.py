"""Monthly journals: retained transition reports plus live, owner-scoped activity.

Reads never create records. New signatures use the existing signoff table with
an LMS identity namespace and a separate key for each reviewed revision.
"""
import base64
import json
import logging
import re
from collections import defaultdict
from functools import wraps
from types import SimpleNamespace

from django.db import DatabaseError, transaction
from django.conf import settings
from django.http import HttpResponse, JsonResponse, HttpResponseRedirect
from django.utils.http import content_disposition_header
from django.utils import timezone
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_protect

from login.permissions import audit_admin_learner_action, login_required
from old_otjh import repository as old_repo, service as old, storage
from old_otjh.views import public_detail, public_state
from . import monthly_log_sources as sources
from . import monthly_log_history as history
from . import canonical_learning as canonical
from .journal_sources import learner_journal_view
from . import journal_sources
from .subject_content import ContentUnavailable

logger = logging.getLogger(__name__)
VERSION = 'lms-monthly-log-v1'
MONTH_LOCKS = '"Audit".monthly_log_locks'


def endpoint(*methods):
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            try:
                if request.method not in methods:
                    raise old.ServiceError('Method not allowed.', 'method_not_allowed', 405)
                response = learner_journal_view(view)(request, *args, **kwargs)
            except old.ServiceError as error:
                response = JsonResponse({'error': str(error), 'code': error.code}, status=error.status)
            except DatabaseError:
                logger.exception('Monthly logs could not be loaded')
                response = JsonResponse({'error': 'Monthly logs are temporarily unavailable. Please try again.'}, status=503)
            except ContentUnavailable as error:
                response = JsonResponse({'error': str(error)}, status=503)
            if getattr(request, 'admin_learner_action', False):
                audit_admin_learner_action(request, request.admin_learner_id, response)
            response['Cache-Control'] = 'private, no-store'
            response['Vary'] = 'Cookie'
            return response
        return login_required(csrf_protect(wrapped))
    return decorate


def is_own_learner_record(account, learner_id):
    """A staff member or administrator who is also studying, on their own record.

    Resolved on the server from the account's address, the same lookup that
    offers them the Learner workspace (login.identity), never from a client id.
    """
    if account.role == 'learner' or account.subject_type != 'staff' or not account.is_active:
        return False
    from login.learner_enrolment import existing_learner_record
    record = existing_learner_record(account.email)
    return record is not None and str(record.pk) == str(learner_id)


def scope(request, learner_id):
    account = request.login_account
    if account.role == 'learner':
        if not account.is_active or account.subject_type != 'learner' or str(account.subject_id) != str(learner_id):
            raise old.ServiceError('Learner not found.', 'not_found', 404)
        role = 'learner'
    elif request.GET.get('perspective') == 'learner' and is_own_learner_record(account, learner_id):
        # Their own record in the learner workspace is the ordinary learner
        # experience; every other record keeps the coach/admin rules below.
        role = 'learner'
    else:
        actor = old.coach_actor(account)
        if actor['role'] == 'monitor':
            raise old.ServiceError('Coach access is required.', 'forbidden', 403)
        role = actor['role']
    canonical_profile = canonical.require_profile(learner_id)
    learner = {**canonical_profile, 'id': learner_id}
    if role == 'learner' and old.normalize(account.email) != old.normalize(learner.get('email')):
        raise old.ServiceError('Learner not found.', 'not_found', 404)
    profile = canonical_profile
    learner = {**learner, 'aptem_id': profile['aptem_id'], 'name': profile['name'],
               'programme': profile['programme']}
    coach_email = old.normalize((profile or {}).get('coach_email') or learner.get('coach_email'))
    if role == 'coach' and coach_email != actor['email']:
        raise old.ServiceError('Learner not found.', 'not_found', 404)
    # An admin opening the learner workspace can perform learner actions.
    # So can the learner's own coach: the coach_email check above already
    # limits a coach to their caseload.
    from coach_api.auth import _requested_view_as_email
    learner_preview = request.GET.get('perspective') == 'learner' and role != 'learner'
    admin_action = learner_preview and ((account.role == 'admin' and role == 'admin') or role == 'coach')
    if learner_preview and not admin_action and request.method != 'GET':
        raise old.ServiceError('The learner preview is read-only.', 'forbidden', 403)
    view_as = _requested_view_as_email(request) if role == 'admin' and not learner_preview else None
    if view_as and (coach_email != old.normalize(view_as) or request.method != 'GET'):
        raise old.ServiceError('This coach workspace is read-only.', 'forbidden', 403)
    request.admin_learner_action = admin_action
    request.coach_learner_action = admin_action and role == 'coach'
    request.admin_learner_id = learner_id
    return {**learner, '_profile': profile, '_canonical_profile': canonical_profile,
            '_view_as': bool(view_as) or (learner_preview and not admin_action)}, 'learner' if admin_action else role


def valid_month(month):
    valid_month_key(month)
    if month > timezone.localdate().strftime('%Y-%m'):
        raise old.ServiceError('Choose a valid report month.')


def valid_month_key(month):
    """Validate a YYYY-MM key without applying the ordinary future-month gate."""
    if not re.fullmatch(r'[0-9]{4}-(0[1-9]|1[0-2])', month or ''):
        raise old.ServiceError('Choose a valid report month.')


def require_closed_month(month):
    if month >= timezone.localdate().strftime('%Y-%m'):
        raise old.ServiceError('This monthly log will be available after the month ends.', 'month_not_closed', 409)


def legacy_summary(learner):
    if canonical.enabled(learner['id']):
        return {'months': []}
    if not learner.get('aptem_id'):
        return {'months': []}
    if history.enabled(learner):
        return public_state(history.summary(learner))
    # Include every retained month, without starting/finalizing a transition on GET.
    result = public_state(old.summary({**learner, '_read_only': True}))
    targets = signed_training_plan_targets(learner)
    for month in result['months']:
        if month['month'] in targets:
            month['training_plan_target'] = targets[month['month']]
            month['training_plan_target_source'] = 'signed_training_plan'
    return result


def signed_training_plan_targets(learner):
    """Monthly targets from the same signed Aptem contract shown by Audit."""
    if not learner.get('aptem_id'):
        return {}
    from .training_plan_dashboard import contract_plan, find_contract

    with old_repo.source_connection().cursor() as cursor:
        contract = find_contract(cursor, learner['aptem_id'])
    plan = contract_plan(SimpleNamespace(pk=learner['id']), contract)
    if plan.get('contractStatus') != 'ready':
        return {}
    targets = {}
    for month, value in (plan.get('months') or {}).items():
        raw_target = value.get('planned') if isinstance(value, dict) else None
        target = sources.number(raw_target) if raw_target is not None else None
        if month <= old_repo.CUTOFF and target is not None:
            targets[month] = target
    return targets


def signatures(learner):
    if canonical.enabled(learner['id']):
        return canonical.signatures(learner['id'])
    return old_repo.query(f'''SELECT report_month, signer_role, signer_name,
        signed_at, snapshot_hash,
        CASE WHEN signature_data LIKE '{{%%' THEN signature_data::jsonb->>'url'
             ELSE signature_data END AS url
        FROM {old_repo.SIGNOFFS} WHERE learner_id=%s AND audit_version=%s
          AND review_confirmed IS TRUE ORDER BY signed_at DESC, id DESC''',
        [f"lms:{learner['id']}", VERSION])


def lock_state(learner_id, month):
    """Return the explicit lock without making old deployments unusable."""
    if canonical.enabled(learner_id):
        owner = canonical.profile(learner_id)
        records = old_repo.query('''SELECT locked_at,unlocked_at,unlocked_by
            FROM "Learner".learner_monthly_locks WHERE learner_id=%s AND report_month=%s''', [owner['id'], month])
        record = records[0] if records else {}
        return {'locked': bool(record.get('locked_at') and not record.get('unlocked_at')),
                'locked_at': record['locked_at'].isoformat() if record.get('locked_at') else None,
                'unlocked_at': record['unlocked_at'].isoformat() if record.get('unlocked_at') else None,
                'unlocked_by': record.get('unlocked_by')}
    try:
        rows = old_repo.query(
            f'''SELECT locked_at, unlocked_at, unlocked_by FROM {MONTH_LOCKS}
                WHERE learner_id=%s AND report_month=%s LIMIT 1''',
            [str(learner_id), month],
        )
    except DatabaseError:
        return {'locked': False, 'locked_at': None, 'unlocked_at': None, 'unlocked_by': None}
    row = rows[0] if rows else None
    return {
        'locked': bool(row and row.get('locked_at') and not row.get('unlocked_at')),
        'locked_at': row.get('locked_at').isoformat() if row and row.get('locked_at') else None,
        'unlocked_at': row.get('unlocked_at').isoformat() if row and row.get('unlocked_at') else None,
        'unlocked_by': row.get('unlocked_by') if row else None,
    }


def lock_states(learner_id, months, *, canonical_owner=None):
    """Read summary lock state in one round trip instead of once per month."""
    month_keys = sorted(set(months or []))
    def empty():
        return {'locked': False, 'locked_at': None, 'unlocked_at': None, 'unlocked_by': None}
    result = {month: empty() for month in month_keys}
    if not month_keys:
        return result
    if canonical_owner is not None:
        records = old_repo.query('''SELECT report_month,locked_at,unlocked_at,unlocked_by
            FROM "Learner".learner_monthly_locks
            WHERE learner_id=%s AND report_month=ANY(%s)''', [canonical_owner['id'], month_keys])
    elif canonical.enabled(learner_id):
        owner = canonical.profile(learner_id)
        records = old_repo.query('''SELECT report_month,locked_at,unlocked_at,unlocked_by
            FROM "Learner".learner_monthly_locks
            WHERE learner_id=%s AND report_month=ANY(%s)''', [owner['id'], month_keys])
    else:
        try:
            records = old_repo.query(f'''SELECT report_month,locked_at,unlocked_at,unlocked_by
                FROM {MONTH_LOCKS} WHERE learner_id=%s AND report_month=ANY(%s)''',
                [str(learner_id), month_keys])
        except DatabaseError:
            return result
    for record in records:
        month = record.get('report_month')
        if month in result:
            result[month] = {
                'locked': bool(record.get('locked_at') and not record.get('unlocked_at')),
                'locked_at': record['locked_at'].isoformat() if record.get('locked_at') else None,
                'unlocked_at': record['unlocked_at'].isoformat() if record.get('unlocked_at') else None,
                'unlocked_by': record.get('unlocked_by'),
            }
    return result


def lock_if_fully_signed(learner_id, month):
    """Lock only after both independent signatures exist; never overwrite either."""
    if canonical.enabled(learner_id):
        signs = canonical.signatures(learner_id)
        if {s['signer_role'] for s in signs if s['report_month'] == month} >= {'learner', 'coach'}:
            old_repo.query('''INSERT INTO "Learner".learner_monthly_locks
                (learner_id,report_month,locked_at,source_system) VALUES (%s,%s,now(),'lms')
                ON CONFLICT (learner_id,report_month) DO UPDATE
                SET locked_at=now(),unlocked_at=NULL,unlocked_by=NULL,updated_at=now()''',
                [canonical.profile(learner_id)['id'], month])
        return
    try:
        rows = old_repo.query(
            f'''SELECT signer_role FROM {old_repo.SIGNOFFS}
                WHERE learner_id=%s AND report_month=%s AND audit_version=%s
                  AND review_confirmed IS TRUE AND signer_role IN ('learner','coach')''',
            [f'lms:{learner_id}', month, VERSION],
        )
        if {row['signer_role'] for row in rows} != {'learner', 'coach'}:
            return
        old_repo.query(
            f'''INSERT INTO {MONTH_LOCKS} (learner_id, report_month, locked_at)
                VALUES (%s,%s,now())
                ON CONFLICT (learner_id, report_month) DO UPDATE
                SET locked_at=now(), unlocked_at=NULL, unlocked_by=NULL''',
            [str(learner_id), month],
        )
    except DatabaseError:
        logger.warning('Monthly log lock table is unavailable; signatures remain valid.')


def month_state(month, rows, signs, training_plan_target=None, *, learner_id=None, lock=None):
    # ``source_system`` is a read-only classification added to canonical rows
    # for the unified Actual predicate.  It is not learner-entered content and
    # must not invalidate an existing signed month's snapshot hash.
    digest_rows = [{key: value for key, value in item.items() if key != 'source_system'} for item in rows]
    digest = old.digest(digest_rows)
    actual_rows = [r for r in rows if canonical.counts_as_actual(r)]
    counted_rows = list(rows)
    # As in Previous learning record, source updates never erase a saved
    # signature or revoke completion. Each capture retains its reviewed rows.
    matching = [s for s in signs if s['report_month'] == month]
    def sign(role):
        return next(({k: s[k] for k in ('signed_at', 'signer_name', 'url')}
                     for s in matching if s['signer_role'] == role), None)
    student, coach = sign('learner'), sign('coach')
    lock = lock if lock is not None else (
        lock_state(learner_id, month) if learner_id is not None
        else {'locked': False, 'locked_at': None, 'unlocked_at': None, 'unlocked_by': None}
    )
    return {'month': month, 'source': 'lms', 'is_required': False,
            'is_open': month == timezone.localdate().strftime('%Y-%m'),
            'status': 'complete' if student else 'awaiting_signature',
            'student_signature': student, 'coach_signature': coach,
            'row_count': len({r.get('progress_id') or r.get('source_ref') for r in rows}),
            'planned_hours': sum(float(r['planned_hours'] or 0) for r in rows),
            'actual_hours': sum(float(r['actual_hours'] or 0) for r in actual_rows),
            'estimated_hours': sum(float(r['actual_hours'] or 0) for r in rows
                                   if r.get('actual_estimated') is True),
            'not_accepted_hours': sum(float(r['actual_hours'] or 0) for r in counted_rows if not r['accepted']),
            'total_actual_hours': sum(float(r['actual_hours'] or 0) for r in counted_rows
                                      if r.get('actual_estimated') is not True),
            'training_plan_target': training_plan_target, 'pending_revisions': 0, 'can_complete': False,
            'source_finalization': None, 'snapshot_digest': digest,
            'locked': lock['locked'], 'locked_at': lock['locked_at'],
            'can_unlock': False}


def current_months(learner, signs=(), *, include_open=False, include_future=False, ensure_month=None):
    grouped = defaultdict(list)
    open_month = timezone.localdate().strftime('%Y-%m')
    def allowed_month(month):
        return month < open_month or (include_open and month == open_month) or (include_future and month > open_month)

    if canonical.enabled(learner['id']):
        for item in canonical.activity_rows(learner['id']):
            month = item.get('reporting_month')
            if month and allowed_month(month):
                grouped[month].append(item)
        for month in {*canonical.targets(learner['id']), *(s['report_month'] for s in signs)}:
            if allowed_month(month):
                grouped.setdefault(month, [])
        if ensure_month and allowed_month(ensure_month):
            grouped.setdefault(ensure_month, [])
        return grouped
    for signature in signs:
        month = signature['report_month']
        if allowed_month(month) and (not learner.get('aptem_id') or month > old_repo.CUTOFF):
            grouped[month] = []
    for row in sources.activity_rows(learner):
        month = row['activity_date'][:7]
        if month > open_month or (month == open_month and not include_open):
            continue
        if learner.get('aptem_id') and month <= old_repo.CUTOFF:
            continue
        grouped[month].append(row)
    if history.enabled(learner):
        for month, audit_rows in history.later_rows(learner, open_month).items():
            if month == open_month and not include_open:
                continue
            public_detail(learner, {'rows': audit_rows})
            grouped[month] = history.merge_rows(grouped.get(month, []), audit_rows)
    for rows in grouped.values():
        rows.sort(key=lambda row: (str(row['activity_date'] or ''), row['source_ref'] or ''))
    # An MCM can be signed before any learning activity has been recorded for
    # its target month.  Keep that month visible in the read-only MCM-linked
    # view without creating a database row; the real signature is mirrored only
    # when the learner signs the MCM itself.
    if ensure_month and (not learner.get('aptem_id') or ensure_month > old_repo.CUTOFF):
        if allowed_month(ensure_month):
            grouped.setdefault(ensure_month, [])
    return grouped


def summary_data(learner, *, include_open=False, include_future=False, ensure_month=None):
    canonical_owner = learner.get('_canonical_profile')
    if canonical_owner is not None:
        retained = {'months': []}
        records = canonical.entries_for(canonical_owner)
        signs = canonical.signatures_for(canonical_owner)
        targets = canonical.targets_for(canonical_owner)
        grouped = defaultdict(list)
        open_month = timezone.localdate().strftime('%Y-%m')
        def allowed_month(month):
            return (
                month < open_month
                or (include_open and month == open_month)
                or (include_future and month > open_month)
            )
        for item in canonical.rows_for(canonical_owner, records):
            month = item.get('reporting_month')
            if month and allowed_month(month):
                grouped[month].append(item)
        for month in {*targets, *(sign['report_month'] for sign in signs)}:
            if allowed_month(month):
                grouped.setdefault(month, [])
        if ensure_month and allowed_month(ensure_month):
            grouped.setdefault(ensure_month, [])
        locks = lock_states(learner['id'], grouped, canonical_owner=canonical_owner)
        months = [month_state(month, rows, signs, targets.get(month), learner_id=learner['id'],
                              lock=locks[month])
                  for month, rows in grouped.items()]
        programme_metrics = canonical.metrics_from_records(records, targets)['otjh']
        training_plan_totals = {
            'accepted_hours': programme_metrics.get('completed_actual', programme_metrics.get('actual')),
            'planned_hours': programme_metrics['planned'],
        }
    else:
        retained = legacy_summary(learner)
        signs = signatures(learner)
        months = [{**m, 'source': 'legacy'} for m in retained['months']]
        current = current_months(learner, signs, include_open=include_open,
                                 include_future=include_future, ensure_month=ensure_month)
        locks = lock_states(learner['id'], current)
        months.extend(month_state(month, rows, signs, sources.monthly_target(learner, month),
                                  learner_id=learner['id'], lock=locks[month])
                      for month, rows in current.items())
        training_plan_totals = {
            'accepted_hours': round(sum(float(month.get('actual_hours') or 0) for month in months), 4),
            'planned_hours': round(sum(float(month.get('training_plan_target') or 0) for month in months), 4),
        }
    months.sort(key=lambda m: m['month'])
    profile = learner.get('_profile') or {}
    audit_profile = retained.get('profile') or {}
    return {'learner': {'id': learner['id'], 'aptem_id': learner.get('aptem_id'),
                       'name': learner['name'], 'programme': learner['programme'],
                       'coach_name': profile.get('coach_name') or learner.get('coach_name'),
                       'planned_end_date': audit_profile.get('planned_end_date') or profile.get('end_date')},
            'months': months, 'total_months': len(months),
            'completed_months': sum(m['status'] == 'complete' for m in months),
            'training_plan_totals': training_plan_totals,
            'read_only': learner['_view_as']}


def detail_data(learner, month, *, include_open=False, include_future=False, demo=False, ensure_month=None):
    (valid_month_key if include_future else valid_month)(month)
    consolidated = canonical.enabled(learner['id'])
    if not consolidated and learner.get('aptem_id') and month <= old_repo.CUTOFF:
        if history.enabled(learner):
            detail = public_detail(learner, history.detail(learner, month, demo=demo))
        else:
            detail = public_detail(learner, old.month_detail({**learner, '_read_only': True}, month))
        targets = signed_training_plan_targets(learner)
        if month in targets:
            detail['training_plan_target'] = targets[month]
            detail['training_plan_target_source'] = 'signed_training_plan'
        return {**detail, 'source': 'legacy', 'demo_only': demo}
    if not include_open:
        require_closed_month(month)
    signs = signatures(learner)
    rows = current_months(learner, signs, include_open=include_open, include_future=include_future,
                          ensure_month=ensure_month).get(month)
    if rows is None:
        raise old.ServiceError('No activities are recorded for this month.', 'not_found', 404)
    profile = learner.get('_profile') or {}
    report_profile = old_repo.report_profile(learner) if learner.get('aptem_id') and not consolidated else {
        'start_date': profile.get('start_date'), 'planned_end_date': profile.get('end_date'),
        'first_evidence_date': None if consolidated else sources.first_evidence_date(learner['id'])}
    target = canonical.targets(learner['id']).get(month, 0 if journal_sources.enabled() else None) if consolidated else sources.monthly_target(learner, month)
    return {**month_state(month, rows, signs, target, learner_id=learner['id']), 'rows': rows,
            'profile': report_profile}


def _canonical_signature_scope(learner, owner):
    """Return the lineage keys required by canonical monthly signatures.

    ``learner_id`` is the consolidated profile id, while the table's unique
    scope is the source learner plus programme/enrolment key.  New LMS
    records may not have an Aptem id, so the enrolment id is the stable
    source identity fallback.
    """
    enrolment_id = owner.get('enrolment_id') or learner.get('id')
    source_learner_id = str(owner.get('aptem_id') or enrolment_id or learner.get('id') or '')
    programme_key = f'lms:{enrolment_id or learner.get("id")}'
    return source_learner_id, programme_key


def mirror_mcm_learner_signature(learner, month, signature, name):
    """Copy a learner's MCM signature into that MCM's monthly log.

    The MCM and monthly-log records live in different parts of the LMS, so the
    copy is deliberately idempotent and never replaces an existing monthly
    learner signature.  ``include_open=True`` is intentional: an MCM may be
    signed while its reporting month is still in progress, but the ordinary
    monthly-log signing endpoint remains closed until month end.
    """
    # An MCM can be completed before the curriculum target month starts. The
    # ordinary Monthly Logs view still blocks future months, but this mirror
    # must accept the MCM's own target month so the two signatures stay linked.
    valid_month_key(month)
    if not str(signature or '').startswith('data:image/'):
        raise old.ServiceError('A valid learner signature is required.')

    if not canonical.enabled(learner['id']) and learner.get('aptem_id') and month <= old_repo.CUTOFF:
        return {'status': 'skipped', 'reason': 'legacy_month', 'month': month}

    try:
        report = detail_data(learner, month, include_open=True, include_future=True, ensure_month=month)
    except old.ServiceError as error:
        # A month with no activity rows is still a valid MCM sign-off. Keep an
        # empty, immutable snapshot so the signed month appears in the log list.
        if error.code != 'not_found':
            raise
        report = {
            'source': 'lms', 'rows': [], 'profile': None,
            'snapshot_digest': old.digest([]),
        }

    if report.get('source') != 'lms':
        return {'status': 'skipped', 'reason': 'legacy_month', 'month': month}

    signed_name = str(name or '').strip() or str(learner.get('name') or '').strip() or 'Learner'
    capture = json.dumps({
        'url': signature,
        'rows': report.get('rows') or [],
        'profile': report.get('profile'),
        'capture_method': 'mcm',
    }, default=str)
    snapshot_digest = report.get('snapshot_digest') or old.digest(report.get('rows') or [])
    source_ref = f'lms-mcm:{learner["id"]}:{month}:{snapshot_digest}'

    with transaction.atomic(using='enrolment'):
        # The learner row lock serializes this copy with an ordinary monthly
        # sign request, so a coach/learner race cannot overwrite history.
        old_repo.query('SELECT id FROM enrolment."Created_users" WHERE id=%s FOR UPDATE', [learner['id']])
        if canonical.enabled(learner['id']):
            owner = canonical.profile(learner['id'])
            source_learner_id, programme_key = _canonical_signature_scope(learner, owner)
            existing = old_repo.query('''SELECT id FROM "Learner".learner_monthly_signatures
                WHERE learner_id=%s AND report_month=%s AND signer_role='learner'
                  AND review_confirmed IS TRUE LIMIT 1''', [owner['id'], month])
            if existing:
                return {'status': 'already-synced', 'month': month}
            inserted = old_repo.query('''INSERT INTO "Learner".learner_monthly_signatures
                (learner_id,report_month,signer_role,signer_name,review_confirmed,
                 signature_url,signature_data,capture_method,signed_at,snapshot_digest,
                 source_system,source_ref,source_learner_id,programme_key)
                VALUES (%s,%s,'learner',%s,true,%s,%s,'mcm',now(),%s,'lms',%s,%s,%s)
                ON CONFLICT (source_learner_id,programme_key,report_month,signer_role) DO NOTHING
                RETURNING id''',
                [owner['id'], month, signed_name, signature, capture, snapshot_digest, source_ref,
                 source_learner_id, programme_key])
        else:
            learner_key = f'lms:{learner["id"]}'
            existing = old_repo.query(f'''SELECT id FROM {old_repo.SIGNOFFS}
                WHERE learner_id=%s AND report_month=%s AND signer_role='learner'
                  AND audit_version=%s AND review_confirmed IS TRUE LIMIT 1''',
                [learner_key, month, VERSION])
            if existing:
                return {'status': 'already-synced', 'month': month}
            inserted = old_repo.query(f'''INSERT INTO {old_repo.SIGNOFFS}
                (learner_id,programme_key,report_month,signer_role,signer_name,
                 review_confirmed,signature_data,signed_at,snapshot_hash,audit_version)
                VALUES (%s,%s,%s,'learner',%s,true,%s,now(),%s,%s)
                ON CONFLICT (learner_id,programme_key,report_month,signer_role) DO NOTHING
                RETURNING id''',
                [learner_key, source_ref, month, signed_name, capture, snapshot_digest, VERSION])
        if not inserted:
            # An unconfirmed imported row can occupy the unique key. DO NOTHING
            # preserves that record, but it is not a successful learner sign-off.
            raise old.ServiceError('The Monthly Log signature was not saved. Please try again from Monthly Logs.')

    return {'status': 'synced', 'month': month, 'snapshot_digest': snapshot_digest}


@endpoint('GET')
def summary(request, learner_id):
    learner, _ = scope(request, learner_id)
    # The current month is useful as a live activity log even though its final
    # signatures remain unavailable until month-end.
    ensure_month = request.GET.get('month') if request.GET.get('workflow') == 'mcm' else None
    if ensure_month and not re.fullmatch(r'[0-9]{4}-(0[1-9]|1[0-2])', ensure_month):
        ensure_month = None
    mcm_workflow = request.GET.get('workflow') == 'mcm'
    return JsonResponse({**summary_data(learner, include_open=True, include_future=mcm_workflow,
                                         ensure_month=ensure_month), 'csrf_token': get_token(request)})


@endpoint('GET')
def detail(request, learner_id, month):
    learner, _ = scope(request, learner_id)
    mcm_workflow = request.GET.get('workflow') == 'mcm'
    return JsonResponse(detail_data(learner, month, include_open=True, include_future=mcm_workflow,
                                   demo=request.GET.get('demo') == '1',
                                   ensure_month=month if mcm_workflow else None))


@endpoint('GET')
def content(request, learner_id, month, row_id):
    learner, _ = scope(request, learner_id)
    return content_response(learner, month, row_id)


def content_response(learner, month, row_id):
    """The shared activity reader; callers must authorize the learner first."""
    learner_id = learner['id']
    if canonical.enabled(learner_id):
        valid_month(month)
        row, record = canonical.content_row(learner['_profile'], month, row_id)
        payload = canonical.content(row, learner['_profile'], record)
        for part in payload['parts']:
            if part.get('url'):
                part['url'] = f'/learner_api/monthly-logs/{learner_id}/{month}/activities/{row_id}/materials/{part["id"]}/'
        response = JsonResponse(payload)
        response['Cache-Control'] = 'private, no-store'
        return response
    report = detail_data(learner, month, include_open=True)
    if report['source'] == 'legacy':
        return JsonResponse(old.activity_content({**learner, '_read_only': True}, month, row_id))
    row = next((r for r in report['rows'] if r['id'] == row_id), None)
    if row is None:
        raise old.ServiceError('Activity not found.', 'not_found', 404)
    if row.get('_audit_row_id'):
        original = old_repo.activity_row(learner, month, row['_audit_row_id'])
        if original is None:
            raise old.ServiceError('Activity not found.', 'not_found', 404)
        return JsonResponse({'id': row_id, 'parts': old_repo.activity_parts(learner, original)})
    if str(row.get('source_ref') or '').startswith('la:'):
        # LMS rows projected into the historical journal have no manual-row
        # database id.  Resolve their read-only material by the stable
        # group/activity source reference instead.
        from old_otjh.content import resolve
        resolved = resolve(learner, [row], companions=True).get(row_id)
        if resolved is not None:
            return JsonResponse({'id': row_id, 'parts': resolved['parts']})
    return JsonResponse(sources.activity_content(learner, row))


@endpoint('GET')
def canonical_material(request, learner_id, month, row_id, material_id):
    learner, _ = scope(request, learner_id)
    return material_response(learner, month, row_id, material_id)


def material_response(learner, month, row_id, material_id):
    learner_id = learner['id']
    if not canonical.enabled(learner_id):
        raise old.ServiceError('Material not found.', 'not_found', 404)
    valid_month(month)
    row, record = canonical.content_row(learner['_profile'], month, row_id)
    part = next((p for p in canonical.material_parts(row, learner['_profile'], record)
                 if p['id'] == material_id and p.get('url')), None)
    if part is None:
        raise old.ServiceError('Material is not available for this activity.', 'not_found', 404)
    response = HttpResponseRedirect(part['url'])
    response['Cache-Control'] = 'private, no-store'
    response['Referrer-Policy'] = 'no-referrer'
    return response


@endpoint('GET')
def canonical_document(request, learner_id, file_id):
    scope(request, learner_id)
    return canonical_document_response(learner_id, file_id)


def canonical_document_response(learner_id, file_id):
    owner = canonical.profile(learner_id)
    if owner is None:
        raise old.ServiceError('Document not found.', 'not_found', 404)
    records = old_repo.query('''SELECT d.container,d.blob_name,d.display_name,d.content_type
        FROM "Learner".learner_activity_documents d
        JOIN "Learner".learner_progress_entries p ON p.id=d.progress_id AND p.learner_id=d.learner_id
        WHERE d.id=%s AND d.learner_id=%s AND d.deleted_at IS NULL AND p.deleted_at IS NULL
          ''', [file_id, owner['id']])
    if not records:
        raise old.ServiceError('Document not found.', 'not_found', 404)
    from .evidence_storage import download_blob_bytes
    doc = records[0]
    response = HttpResponse(download_blob_bytes(doc['container'], doc['blob_name']),
                            content_type=doc['content_type'] or 'application/octet-stream')
    response['Content-Disposition'] = content_disposition_header(False, doc['display_name'])
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Security-Policy'] = 'sandbox'
    return response


@endpoint('GET')
def document(request, learner_id, file_id):
    scope(request, learner_id)
    return document_response(learner_id, file_id)


def document_response(learner_id, file_id):
    rows = old_repo.query('''SELECT blob_name,original_filename,content_type FROM "Learner".evidence_files
        WHERE id=%s AND learner_id=%s AND status='approved' ''', [str(file_id), str(learner_id)])
    if not rows:
        raise old.ServiceError('Document not found.', 'not_found', 404)
    from .evidence_storage import download_blob_bytes, azure_configured
    if not azure_configured():
        raise old.ServiceError('Document storage is unavailable.', 'storage_unavailable', 503)
    doc = rows[0]
    response = HttpResponse(download_blob_bytes(settings.AZURE_APPROVED_CONTAINER, doc['blob_name']),
                            content_type=doc['content_type'] or 'application/octet-stream')
    response['Content-Disposition'] = content_disposition_header(False, doc['original_filename'])
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Security-Policy'] = "sandbox"
    return response


@endpoint('POST')
def sign(request, learner_id, month):
    learner, role = scope(request, learner_id)
    data = request.POST
    if (set(data) != {'snapshot_digest', 'confirmed', 'capture_method'} or
            set(request.FILES) != {'signature'} or data.get('confirmed') != 'true' or
            data.get('capture_method') not in {'draw', 'upload', 'import'}):
        raise old.ServiceError('Confirm your own signature before saving.')
    image = storage.sanitize(request.FILES['signature'])
    valid_month(month)
    if not canonical.enabled(learner_id) and learner.get('aptem_id') and month <= old_repo.CUTOFF:
        old.start(learner, request.login_account, role)
        return JsonResponse({**public_detail(learner, old.sign(learner, month, request.login_account,
            role, image, data.get('snapshot_digest'), {'capture_method': data['capture_method']})), 'source': 'legacy'})
    require_closed_month(month)
    with transaction.atomic(using='enrolment'):
        # Serialize concurrent sign requests for this learner, including first signatures.
        old_repo.query('SELECT id FROM enrolment."Created_users" WHERE id=%s FOR UPDATE', [learner_id])
        include_open = request.GET.get('workflow') == 'mcm'
        report = detail_data(learner, month, include_open=include_open)
        signer_role = 'learner' if role == 'learner' else 'coach'
        if lock_state(learner_id, month)['locked']:
            raise old.ServiceError('This monthly log is locked. An administrator must unlock it first.', 'month_locked', 409)
        if report['student_signature' if signer_role == 'learner' else 'coach_signature']:
            return JsonResponse(report)
        if report['snapshot_digest'] != data.get('snapshot_digest'):
            raise old.ServiceError('Activities have changed. Review the updated month before signing.', 'record_changed', 409)
        name = request.login_account.display_name or request.login_account.email
        # Keep the reviewed revision with its capture; later source edits do
        # not overwrite it or require the person to sign the month again.
        key = f"lms-monthly:{learner_id}:{report['snapshot_digest']}"
        capture = json.dumps({'url': 'data:image/png;base64,' + base64.b64encode(image).decode('ascii'),
            'rows': report['rows'], 'profile': report.get('profile'),
            'actor_account_id': request.login_account.id, 'capture_method': data['capture_method']}, default=str)
        if canonical.enabled(learner_id):
            owner = canonical.profile(learner_id)
            source_learner_id, programme_key = _canonical_signature_scope(learner, owner)
            old_repo.query('''INSERT INTO "Learner".learner_monthly_signatures
                (learner_id,report_month,signer_role,signer_name,review_confirmed,signature_url,
                 signature_data,capture_method,signed_at,snapshot_digest,source_system,source_ref,
                 source_learner_id,programme_key)
                VALUES (%s,%s,%s,%s,true,%s,%s,%s,now(),%s,'lms',%s,%s,%s)
                ON CONFLICT (source_learner_id,programme_key,report_month,signer_role) DO NOTHING''',
                [owner['id'], month, signer_role, name, json.loads(capture)['url'], capture,
                 data['capture_method'], report['snapshot_digest'], key, source_learner_id, programme_key])
        else:
            old_repo.query(f'''INSERT INTO {old_repo.SIGNOFFS}
            (learner_id, programme_key, report_month, signer_role, signer_name,
             review_confirmed, signature_data, signed_at, snapshot_hash, audit_version)
            VALUES (%s,%s,%s,%s,%s,true,%s,now(),%s,%s)
            ON CONFLICT (learner_id,programme_key,report_month,signer_role) DO NOTHING''',
            [f'lms:{learner_id}', key, month, signer_role, name, capture, report['snapshot_digest'], VERSION])
        lock_if_fully_signed(learner_id, month)
        return JsonResponse(detail_data(learner, month, include_open=include_open))


@endpoint('POST')
def complete(request, learner_id, month):
    learner, role = scope(request, learner_id)
    if role != 'learner':
        raise old.ServiceError('Open the learner workspace to complete this month.', 'forbidden', 403)
    valid_month(month)
    if not canonical.enabled(learner_id) and learner.get('aptem_id') and month <= old_repo.CUTOFF:
        result = old.complete(learner, month, request.login_account, role)
        return JsonResponse({**public_detail(learner, result), 'source': 'legacy'})
    # Current LMS months complete when their learner signature is saved.
    report = detail_data(learner, month)
    if report['status'] != 'complete':
        raise old.ServiceError('The learner signature is required.', 'signatures_required', 409)
    return JsonResponse(report)


@endpoint('POST')
def unlock(request, learner_id, month):
    """Admin-only unlock; existing learner/coach signatures are retained."""
    learner, _ = scope(request, learner_id)
    account = getattr(request, 'login_account', None)
    if getattr(account, 'role', None) != 'admin' or request.GET.get('perspective') != 'learner':
        raise old.ServiceError('Only an administrator in the learner workspace can unlock this log.', 'forbidden', 403)
    valid_month(month)
    if canonical.enabled(learner_id):
        old_repo.query('''UPDATE "Learner".learner_monthly_locks
            SET unlocked_at=now(),unlocked_by=%s,updated_at=now()
            WHERE learner_id=%s AND report_month=%s''',
            [str(account.id), canonical.profile(learner_id)['id'], month])
        return JsonResponse(detail_data(learner, month, include_open=request.GET.get('workflow') == 'mcm'))
    old_repo.query(
        f'''UPDATE {MONTH_LOCKS} SET unlocked_at=now(), unlocked_by=%s
            WHERE learner_id=%s AND report_month=%s''',
        [str(account.id), str(learner_id), month],
    )
    return JsonResponse(detail_data(learner, month, include_open=request.GET.get('workflow') == 'mcm'))


@endpoint('GET')
def learners(request):
    actor = old.coach_actor(request.login_account)
    if actor['role'] == 'monitor':
        raise old.ServiceError('Coach access is required.', 'forbidden', 403)
    from coach_api.auth import _requested_view_as_email
    email = _requested_view_as_email(request) if actor['role'] == 'admin' else actor['email']
    search = (request.GET.get('search') or '').strip()[:100]
    return JsonResponse({'learners': sources.learners(old.normalize(email), search)})
