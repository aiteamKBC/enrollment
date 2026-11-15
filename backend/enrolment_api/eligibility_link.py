"""Server-owned Eligibility provenance and committed invitation delivery.

Public submissions link only newly created eligible learners. Administrators can
record a separately approved exception for an ineligible application. No token
is stored here or returned to public submission callers.
"""
import logging
import uuid
from django.db import connections, transaction

CONN = 'enrolment'
TABLE = 'enrolment.eligibility_learner_links'
logger = logging.getLogger(__name__)


def available(cursor):
    cursor.execute('SELECT to_regclass(%s)', [TABLE])
    return cursor.fetchone()[0] is not None


def link_for_learner(learner_id):
    with connections[CONN].cursor() as cursor:
        if not available(cursor):
            return None
        cursor.execute(f'SELECT application_id, activation_verified_at FROM {TABLE} WHERE learner_id=%s', [learner_id])
        return cursor.fetchone()


def verified_application(learner_id):
    with connections[CONN].cursor() as cursor:
        if not available(cursor):
            return None
        cursor.execute(f'''SELECT to_jsonb(a), to_jsonb(u) FROM {TABLE} l JOIN enrolment.eligibility_form a ON a.id=l.application_id
            JOIN enrolment."Created_users" u ON u.id=l.learner_id
            WHERE l.learner_id=%s AND l.activation_verified_at IS NOT NULL
            AND (a.eligible IS TRUE OR to_jsonb(l)->>'override_approved_at' IS NOT NULL)''', [learner_id])
        row = cursor.fetchone()
    if not row:
        return None
    import json
    result = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    result['_learner'] = json.loads(row[1]) if isinstance(row[1], str) else row[1]
    return result


def add_link(application_id, learner_id):
    # Caller holds the account/application creation transaction. Missing schema
    # fails closed, rolling back the new learner and account together.
    with connections[CONN].cursor() as cursor:
        cursor.execute(f'''INSERT INTO {TABLE} (application_id, learner_id)
            SELECT id, %s FROM enrolment.eligibility_form WHERE id=%s AND eligible IS TRUE''', [learner_id, application_id])
        if cursor.rowcount != 1:
            raise ValueError('Only a saved eligible application may create a source link.')


def mark_activated(account):
    if account.subject_type != 'learner':
        return
    with transaction.atomic(using=CONN), connections[CONN].cursor() as cursor:
        if available(cursor):
            cursor.execute(f'''UPDATE {TABLE} l SET activation_verified_at=COALESCE(l.activation_verified_at, now())
                FROM enrolment.eligibility_form a WHERE l.learner_id=%s AND a.id=l.application_id
                AND (a.eligible IS TRUE OR to_jsonb(l)->>'override_approved_at' IS NOT NULL)
                RETURNING l.learner_id''', [account.subject_id])
            if cursor.fetchone():
                from django.db.models import Q
                from learner_api.constants import DEFAULT_PROGRAMME_STATUS
                from learner_api.models import EnrolmentUser
                EnrolmentUser.all_learners.filter(pk=account.subject_id).filter(
                    Q(programme_status=DEFAULT_PROGRAMME_STATUS)
                    | Q(programme_status__isnull=True) | Q(programme_status='')
                ).update(programme_status='Onboarding')


def delivery_authorized(application_id, learner_id):
    with connections[CONN].cursor() as cursor:
        cursor.execute(f'''SELECT 1 FROM {TABLE} l JOIN enrolment.eligibility_form a ON a.id=l.application_id
            WHERE l.application_id=%s AND l.learner_id=%s AND l.invitation_status='sending'
            AND l.activation_verified_at IS NULL AND a.eligible IS TRUE''', [application_id, learner_id])
        return cursor.fetchone() is not None


def dispatch(application_id, *, ip=None, user_agent=None):
    """Claim after commit; use the existing account/token/mail service.

    A five-minute lease permits recovery after a stopped process. An already
    recorded successful send is reused, not emailed again. Public retries never
    change identity or get a token. Unknown external delivery after a process
    crash still requires the normal provider/staff recovery checks.
    """
    from login.models import Invitation
    from login.services import invite_eligible_applicant
    from learner_api.models import EnrolmentUser
    claim = uuid.uuid4()
    try:
        if connections[CONN].in_atomic_block:
            transaction.on_commit(lambda: dispatch(application_id, ip=ip, user_agent=user_agent), using=CONN)
            return 'invitePending'
        with transaction.atomic(using=CONN), connections[CONN].cursor() as cursor:
            cursor.execute(f'''UPDATE {TABLE} SET invitation_status='sending', invitation_claim=%s,
                invitation_claimed_at=now(), invitation_error=NULL
                WHERE application_id=%s AND activation_verified_at IS NULL AND
                (invitation_status IN ('pending','failed') OR (invitation_status='sending' AND invitation_claimed_at < now()-interval '5 minutes'))
                RETURNING learner_id''', [claim, application_id])
            row = cursor.fetchone()
        if not row:
            return 'invitePending'
        learner = EnrolmentUser.all_learners.get(pk=row[0])
        already_sent = Invitation.objects.filter(account__subject_type='learner', account__subject_id=learner.pk,
            sent_at__isnull=False).exists()
        sent, detail = (True, None) if already_sent else invite_eligible_applicant(
            learner, linked_application_id=application_id, ip=ip, user_agent=user_agent)
        outcome = 'invited' if sent else 'invitePending'
        with transaction.atomic(using=CONN), connections[CONN].cursor() as cursor:
            cursor.execute(f'''UPDATE {TABLE} SET invitation_status=%s, invitation_error=%s
                WHERE application_id=%s AND invitation_claim=%s''', ['sent' if sent else 'failed', str(detail or '')[:500] or None, application_id, claim])
            # Only linked new applications have mutable delivery receipts.
            cursor.execute('''UPDATE enrolment.eligibility_form SET submission_response=jsonb_set(submission_response,
                '{outcome}', to_jsonb(%s::text)) WHERE id=%s AND submission_response->>'outcome'='invitePending' ''', [outcome, application_id])
        return outcome
    except Exception:
        logger.exception('Linked Eligibility invitation delivery failed for application %s', application_id)
        # The committed account/source record survives. The lease enables retry.
        return 'invitePending'


def refresh_response(application_id, response):
    if response.get('outcome') != 'invitePending':
        return response
    with connections[CONN].cursor() as cursor:
        if not available(cursor):
            return response
        cursor.execute(f'SELECT invitation_status FROM {TABLE} WHERE application_id=%s', [application_id])
        row = cursor.fetchone()
    return {**response, 'outcome': 'invited'} if row and row[0] == 'sent' else response
