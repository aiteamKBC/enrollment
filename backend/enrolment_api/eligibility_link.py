"""Eligibility application provenance and invitation delivery."""
import logging
import uuid
from django.db import connections, transaction
from django.utils import timezone

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
        cursor.execute(f'SELECT application_id, accepted_at FROM {TABLE} WHERE learner_id=%s', [learner_id])
        return cursor.fetchone()


def may_enter_onboarding(learner_id):
    """An accepted eligibility invitation or accepted manual learner invitation."""
    link = link_for_learner(learner_id)
    if link is not None:
        return link[1] is not None
    from login.models import LoginAccount
    return LoginAccount.objects.filter(subject_type='learner', subject_id=learner_id,
                                       password_set_at__isnull=False, is_active=True).exists()


def verified_application(learner_id):
    with connections[CONN].cursor() as cursor:
        if not available(cursor):
            return None
        cursor.execute(f'''SELECT to_jsonb(a), to_jsonb(u) FROM {TABLE} l JOIN enrolment.eligibility_form a ON a.id=l.application_id
            JOIN enrolment."Created_users" u ON u.id=l.learner_id
            WHERE l.learner_id=%s AND l.accepted_at IS NOT NULL
            AND (a.eligible IS TRUE OR to_jsonb(l)->>'override_approved_at' IS NOT NULL)''', [learner_id])
        row = cursor.fetchone()
    if not row:
        return None
    import json
    result = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    # New submissions store answers in JSONB; historic rows can still expose
    # their old columns until the retirement migration is complete.
    result = {**result, **(result.get('answers') or {})}
    result['_learner'] = json.loads(row[1]) if isinstance(row[1], str) else row[1]
    return result


def add_link(application_id, learner_id=None):
    # The source link exists before an account. Acceptance fills learner_id.
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
            cursor.execute(f'''UPDATE {TABLE} l SET accepted_at=COALESCE(l.accepted_at, now()),
                invitation_status='accepted'
                FROM enrolment.eligibility_form a WHERE l.learner_id=%s AND a.id=l.application_id
                AND (a.eligible IS TRUE OR to_jsonb(l)->>'override_approved_at' IS NOT NULL)
                RETURNING l.learner_id''', [account.subject_id])
            cursor.fetchone()


def delivery_authorized(application_id, learner_id):
    with connections[CONN].cursor() as cursor:
        cursor.execute(f'''SELECT 1 FROM {TABLE} l JOIN enrolment.eligibility_form a ON a.id=l.application_id
            WHERE l.application_id=%s AND l.learner_id=%s AND l.invitation_status='sending'
            AND l.accepted_at IS NULL AND (a.eligible IS TRUE OR l.override_approved_at IS NOT NULL)''', [application_id, learner_id])
        return cursor.fetchone() is not None


def dispatch(application_id, *, ip=None, user_agent=None):
    """Claim after commit; send an application invitation without an account.

    A five-minute lease permits recovery after a stopped process. An already
    recorded successful send is reused, not emailed again. Public retries never
    change identity or get a token. Unknown external delivery after a process
    crash still requires the normal provider/staff recovery checks.
    """
    from login.models import Invitation
    from login.invitations import send_eligibility_invitation
    claim = uuid.uuid4()
    try:
        if connections[CONN].in_atomic_block:
            transaction.on_commit(lambda: dispatch(application_id, ip=ip, user_agent=user_agent), using=CONN)
            return 'invitePending'
        already_sent = Invitation.objects.filter(eligibility_application_id=application_id,
            sent_at__isnull=False, used_at__isnull=True, expires_at__gt=timezone.now()).first()
        if already_sent:
            with connections[CONN].cursor() as cursor:
                cursor.execute(f'''UPDATE {TABLE} SET invitation_status='sent', invitation_id=%s,
                    invited_at=COALESCE(invited_at, now())
                    WHERE application_id=%s AND accepted_at IS NULL
                    AND (invitation_id IS NULL OR invitation_id=%s)''',
                    [already_sent.pk, application_id, already_sent.pk])
                if cursor.rowcount:
                    return 'invited'
        with transaction.atomic(using=CONN), connections[CONN].cursor() as cursor:
            cursor.execute(f'''UPDATE {TABLE} AS link SET invitation_status='sending', invitation_claim=%s,
                invitation_claimed_at=now(), invitation_error=NULL
                WHERE link.application_id=%s AND link.accepted_at IS NULL AND
                (link.invitation_status IN ('pending','failed','expired','sent') OR
                 (link.invitation_status='sending' AND link.invitation_claimed_at < now()-interval '5 minutes'))
                AND EXISTS (SELECT 1 FROM enrolment.eligibility_form a WHERE a.id=link.application_id
                    AND (a.eligible IS TRUE OR link.override_approved_at IS NOT NULL))
                RETURNING link.application_id''', [claim, application_id])
            row = cursor.fetchone()
        if not row:
            return 'invitePending'
        with connections[CONN].cursor() as cursor:
            cursor.execute('SELECT first_name, surname, work_email FROM enrolment.eligibility_form WHERE id=%s',
                           [application_id])
            applicant = cursor.fetchone()
        invitation, sent, detail = send_eligibility_invitation(
            application_id, email=applicant[2],
            display_name=' '.join(part for part in applicant[:2] if part),
            ip=ip, user_agent=user_agent)
        outcome = 'invited' if sent else 'invitePending'
        with transaction.atomic(using=CONN), connections[CONN].cursor() as cursor:
            cursor.execute(f'''UPDATE {TABLE} SET invitation_status=%s, invitation_error=%s,
                invitation_id=%s, invited_at=CASE WHEN %s THEN COALESCE(invited_at, now()) ELSE invited_at END
                WHERE application_id=%s AND invitation_claim=%s''',
                ['sent' if sent else 'failed', str(detail or '')[:500] or None,
                 invitation.pk if invitation else None, sent, application_id, claim])
            # Only linked new applications have mutable delivery receipts.
            cursor.execute('''UPDATE enrolment.eligibility_form SET submission_response=jsonb_set(submission_response,
                '{outcome}', to_jsonb(%s::text)) WHERE id=%s AND submission_response->>'outcome'='invitePending' ''', [outcome, application_id])
        return outcome
    except Exception:
        logger.exception('Linked Eligibility invitation delivery failed for application %s', application_id)
        # The committed source record survives. The lease enables retry.
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
