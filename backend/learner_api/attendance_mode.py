"""Learner attendance preference and single-use manager approval."""
import json
import logging
import os
from html import escape
from urllib.parse import urlencode
from uuid import uuid4

from django.core import signing
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import connections, transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_http_methods

from login.email_azure import is_configured, send_mail
from login.permissions import learner_self_or_admin, require_role
from .learner_detail import SOURCE_MODELS
from .models import Employer
from .training_plan_dashboard import rows
from .training_plan_document import _extended_ilr_answers

TABLE = '"Learner".attendance_preferences'
SALT = 'learner-attendance-mode-v1'
MAX_AGE = 7 * 24 * 60 * 60
log = logging.getLogger(__name__)


def _manager(source):
    employer_id = getattr(source, 'employer_id', None)
    employer = Employer.objects.filter(pk=employer_id).first() if employer_id else None
    if employer and employer.email:
        email = employer.email.strip()
    else:
        answers = _extended_ilr_answers(source.id)
        block = answers.get('employer') or {}
        email = str(block.get('lineManagerEmail') or '').strip() if isinstance(block, dict) else ''
    try:
        validate_email(email)
    except ValidationError:
        return ''
    # Approval must belong to a different person from the requesting learner.
    return email if email.casefold() != str(source.email or '').strip().casefold() else ''


def _ready(cur):
    cur.execute('SELECT to_regclass(%s)', [TABLE])
    return cur.fetchone()[0] is not None


def _state(cur, learner_id, lock=False):
    cur.execute(f'SELECT * FROM {TABLE} WHERE learner_id=%s' + (' FOR UPDATE' if lock else ''), [learner_id])
    result = rows(cur)
    return result[0] if result else None


def _payload(state, *, available=True, manager_available=False):
    state = state or {}
    expired = (state.get('status') == 'pending' and state.get('updated_at') is not None
               and (timezone.now() - state['updated_at']).total_seconds() > MAX_AGE)
    return {'available': available, 'mode': state.get('mode', 'live'),
            'requestedMode': None if expired else state.get('requested_mode'),
            'status': 'expired' if expired else state.get('status', 'active'),
            'emailSent': state.get('email_sent', False),
            'managerAvailable': manager_available,
            'remindersEnabled': state.get('mode', 'live') == 'live' and (expired or state.get('requested_mode') != 'lazy'),
            'updatedAt': state['updated_at'].isoformat() if state.get('updated_at') else None}


def read_mode(source, kind):
    del kind
    with connections['enrolment'].cursor() as cur:
        if not _ready(cur):
            return _payload(None, available=False)
        state = _state(cur, source.id)
    return _payload(state, manager_available=bool(_manager(source)))


def _send_request(source, kind, state):
    token = signing.dumps({'learner': source.id, 'kind': kind, 'request': str(state['request_id'])}, salt=SALT)
    origin = (os.environ.get('FRONTEND_URL') or 'http://localhost:5173').rstrip('/')
    url = f'{origin}/learner_api/attendance-mode/review/?{urlencode({"token": token})}'
    name = escape(source.username or 'Your learner')
    sent, _ = send_mail(to=state['manager_email'], subject='Attendance mode approval requested',
        html_body=f'<p>{name} has requested recorded learning (Lazy Mode).</p>'
                  '<p>Absence reminders are paused while this request is pending. On approval, remaining planned lecture hours '
                  'will be allocated to recorded sessions. Historical attendance stays on the register.</p>'
                  f'<p><a href="{escape(url, quote=True)}">Review the request</a> (expires in 7 days).</p>')
    with connections['enrolment'].cursor() as cur:
        cur.execute(f'''UPDATE {TABLE} SET email_sent=%s WHERE learner_id=%s
            AND request_id=%s AND status='pending' ''', [sent, source.id, state['request_id']])
    return sent


@csrf_exempt
@require_http_methods(['GET', 'POST'])
@learner_self_or_admin(kwarg='learner_id')
def attendance_mode(request, kind, learner_id):
    model = SOURCE_MODELS.get(kind)
    source = model.all_learners.filter(pk=learner_id).first() if model else None
    if source is None:
        return JsonResponse({'error': 'Learner not found.'}, status=404)
    if request.method == 'GET':
        return JsonResponse(read_mode(source, kind))
    if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return JsonResponse({'error': 'Missing X-Requested-With header.'}, status=403)
    try:
        body = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Invalid request.'}, status=400)
    mode = body.get('mode') if isinstance(body, dict) else None
    if mode not in {'live', 'lazy'}:
        return JsonResponse({'error': 'Choose Live Sessions or Lazy Mode.'}, status=400)
    # Administrators (User Management bulk actions) set the mode outright: no
    # manager approval, no email. A learner's own request still needs approval.
    direct = isinstance(body, dict) and body.get('direct') is True
    if direct and getattr(getattr(request, 'login_account', None), 'role', None) != 'admin':
        return JsonResponse({'error': 'Only an administrator can set the attendance mode directly.'}, status=403)
    approval = mode == 'lazy' and not direct
    manager = _manager(source) if approval else ''
    if approval and not manager:
        return JsonResponse({'error': 'A manager email must be linked to your learner profile before requesting Lazy Mode.'}, status=409)
    if approval and not is_configured():
        return JsonResponse({'error': 'Approval email is unavailable. Please contact support.'}, status=503)
    try:
        with transaction.atomic(using='enrolment'), connections['enrolment'].cursor() as cur:
            if not _ready(cur):
                return JsonResponse({'error': 'Attendance mode is not available yet. Please contact support.'}, status=503)
            cur.execute(f'''INSERT INTO {TABLE} (learner_id) VALUES (%s)
                ON CONFLICT (learner_id) DO NOTHING''', [learner_id])
            state = _state(cur, learner_id, lock=True)
            if mode == 'lazy' and state['mode'] == 'lazy':
                return JsonResponse(_payload(state, manager_available=True))
            if approval and _payload(state)['status'] == 'pending' and state['email_sent']:
                return JsonResponse(_payload(state, manager_available=True))
            if not approval:
                # Also retires any pending request, so its emailed link stops working.
                cur.execute(f'''UPDATE {TABLE} SET mode=%s,requested_mode=NULL,status='active',
                    request_id=NULL,manager_email='',email_sent=false,updated_at=now() WHERE learner_id=%s''', [mode, learner_id])
            else:
                cur.execute(f'''UPDATE {TABLE} SET requested_mode='lazy',status='pending',request_id=%s,
                    manager_email=%s,email_sent=false,updated_at=now() WHERE learner_id=%s''', [uuid4(), manager, learner_id])
            state = _state(cur, learner_id)
        if approval:
            state['email_sent'] = _send_request(source, kind, state)
        return JsonResponse(_payload(state, manager_available=bool(manager)))
    except Exception:
        log.exception('Attendance mode update failed for learner %s', learner_id)
        return JsonResponse({'error': 'Could not save attendance mode. Please retry.'}, status=502)


@require_http_methods(['GET'])
@require_role('admin', 'staff')
def attendance_modes(request):
    """Every saved attendance mode, for the User Management directory column
    and bulk window. Read-only, so it is open to the same staff and admin roles
    as the directory; setting a mode directly stays admin-only.

    A learner with no saved row is on Live Sessions, so only saved rows are
    returned, keyed by learner id.
    """
    with connections['enrolment'].cursor() as cur:
        if not _ready(cur):
            return JsonResponse({'available': False, 'modes': {}})
        cur.execute(f'SELECT * FROM {TABLE}')
        saved = rows(cur)
    modes = {}
    for state in saved:
        payload = _payload(state)
        modes[str(state['learner_id'])] = {'mode': payload['mode'], 'requestedMode': payload['requestedMode']}
    return JsonResponse({'available': True, 'modes': modes})


@csrf_protect
@require_http_methods(['GET', 'POST'])
def review_attendance_mode(request):
    token = request.POST.get('token') if request.method == 'POST' else request.GET.get('token')
    context = {'token': token, 'error': '', 'done': False}
    status = 200
    try:
        data = signing.loads(token or '', salt=SALT, max_age=MAX_AGE)
        model = SOURCE_MODELS.get(data['kind'])
        source = model.all_learners.filter(pk=data['learner']).first() if model else None
        if source is None:
            raise ValueError('Unknown learner')
        # GET only previews. Link scanners cannot approve a request.
        with transaction.atomic(using='enrolment'), connections['enrolment'].cursor() as cur:
            state = _state(cur, source.id, lock=request.method == 'POST')
            if (not state or state['status'] != 'pending' or str(state['request_id']) != data['request']
                    or state['manager_email'].casefold() != _manager(source).casefold()):
                raise ValueError('Request is no longer active')
            context['learner_name'] = source.username or 'Learner'
            if request.method == 'POST':
                decision = request.POST.get('decision')
                if decision not in {'approve', 'decline'}:
                    raise ValueError('Choose a decision')
                approved = decision == 'approve'
                cur.execute(f'''UPDATE {TABLE} SET mode=%s,requested_mode=NULL,status=%s,
                    request_id=NULL,updated_at=now() WHERE learner_id=%s''',
                            ['lazy' if approved else 'live', 'approved' if approved else 'declined', source.id])
                context.update(done=True, approved=approved)
    except (signing.BadSignature, ValueError, KeyError, TypeError):
        context['error'] = 'This request has expired, was already reviewed, or is no longer available.'
        status = 400
    except Exception:
        log.exception('Attendance mode approval failed')
        context['error'] = 'Could not load the request. Please try again.'
        status = 502
    response = render(request, 'learner_api/attendance_mode_review.html', context, status=status)
    response['Cache-Control'] = 'private, no-store'
    response['Referrer-Policy'] = 'no-referrer'
    response['X-Frame-Options'] = 'DENY'
    return response
