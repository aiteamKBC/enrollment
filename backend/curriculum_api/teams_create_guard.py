"""One "Create Teams calendar" per module at a time, across every worker.

The create endpoint refuses a module that already has an active calendar, but
it looks when the request starts and saves the calendar only after Microsoft
has built the event. A browser that stopped waiting re-enabled Create inside
that gap, and the second request passed the same check: Microsoft deduplicated
the event by its transaction id, the LMS saved it twice and marked the first
row superseded. The check was never the problem; the gap was.

A claim closes the gap. It is one committed conditional INSERT ... ON CONFLICT
taken before the create endpoint runs, so it holds across processes and through
the connection pooler, and it records how the request ended. A claim still
'creating' after its lease (a worker that crashed or was killed mid-create) is
reported as uncertain and is never taken over automatically: whether Microsoft
made the meeting is unknown, so only a person who has checked may create again.

The owner provisions the table using backend/sql/teams_calendar_create_claims.sql.
"""
import json
import logging
import uuid

from django.db import connection, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)
TABLE = 'curriculum.teams_calendar_create_claims'
# Well past the slowest create (Graph, holiday shifts, verification, invitations
# and component links run in about a minute), so a live request never loses its
# claim; short enough that a crashed one is reported within minutes.
LEASE_SECONDS = 10 * 60


class CreateClaims:
    def __init__(self, db):
        self.db = db

    def check(self):
        if self.db.vendor != 'postgresql' or self.db.in_atomic_block or not self.db.get_autocommit():
            raise RuntimeError('The Teams create guard needs the provisioned PostgreSQL table and autocommit.')
        with self.db.cursor() as cursor:
            cursor.execute('SELECT to_regclass(%s)', [TABLE])
            if not cursor.fetchone()[0]:
                raise RuntimeError('Creating Teams calendars is paused until the owner applies '
                                   'backend/sql/teams_calendar_create_claims.sql.')

    def claim(self, key, token, take_over_uncertain=False):
        """True when this request now owns the module's create.

        A finished claim is always free. An uncertain one (explicitly uncertain,
        or 'creating' past its lease) is free only when the caller says a person
        has checked. A live 'creating' claim is never free.
        """
        with self.db.cursor() as cursor:
            cursor.execute(f'''INSERT INTO {TABLE} (module_key, token, status, claimed_at, lease_until)
                VALUES (%s, %s, 'creating', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP + make_interval(secs => %s))
                ON CONFLICT (module_key) DO UPDATE SET token = EXCLUDED.token, status = 'creating',
                    outcome_status = NULL, outcome_code = '', live_session_id = '',
                    claimed_at = EXCLUDED.claimed_at, lease_until = EXCLUDED.lease_until, finished_at = NULL
                WHERE {TABLE}.status = 'done'
                   OR (%s AND ({TABLE}.status = 'uncertain' OR {TABLE}.lease_until < CURRENT_TIMESTAMP))
                RETURNING token''', [key, token, LEASE_SECONDS, bool(take_over_uncertain)])
            row = cursor.fetchone()
            return bool(row and row[0] == token)

    def finish(self, key, token, status, outcome_status=None, code='', live_session_id=''):
        # Only the owner's own claim, and only while it is still in flight.
        with self.db.cursor() as cursor:
            cursor.execute(f'''UPDATE {TABLE} SET status = %s, outcome_status = %s, outcome_code = %s,
                live_session_id = %s, finished_at = CURRENT_TIMESTAMP
                WHERE module_key = %s AND token = %s AND status = 'creating' ''',
                           [status, outcome_status, str(code or '')[:80], str(live_session_id or '')[:128], key, token])

    def read(self, key):
        with self.db.cursor() as cursor:
            cursor.execute(f'''SELECT status, outcome_status, outcome_code, live_session_id, claimed_at, lease_until,
                lease_until < CURRENT_TIMESTAMP FROM {TABLE} WHERE module_key = %s''', [key])
            row = cursor.fetchone()
        if not row:
            return None
        return {'status': row[0], 'outcomeStatus': row[1], 'outcomeCode': row[2], 'liveSessionId': row[3],
                'claimedAt': row[4].isoformat() if row[4] else '', 'leaseUntil': row[5].isoformat() if row[5] else '',
                'expired': bool(row[6])}


def claim_state(claim):
    """'none', 'creating', 'uncertain' or 'done', as a person should read it."""
    if not claim:
        return 'none'
    if claim['status'] == 'creating':
        return 'uncertain' if claim['expired'] else 'creating'
    return claim['status']


def module_key(v, payload):
    """The same module identity the create endpoint's own duplicate check uses.

    The cheap resolver: a stored MOD-* id is its own answer in one indexed read.
    The general one summarises every module (every component row included), and
    right after a create has invalidated the curriculum cache that ran past the
    browser's 15s budget on every status check -- a create that had finished
    read as "still creating" until the dialog gave up.
    """
    requested = v.clean_str(payload.get('moduleCatalogueId'))
    resolved = (v.resolve_stored_module_catalogue_id(requested) or requested) if requested else ''
    if resolved and v.authoring_module_exists(resolved):
        return f'module:{resolved}'
    draft = v.clean_str(payload.get('moduleDraftId'))
    return f'draft:{draft}' if draft else ''


def refusal(claim):
    state = claim_state(claim)
    if state == 'creating':
        return JsonResponse({
            'error': "This module's Teams calendar is already being created. Wait for it to finish; do not create it again.",
            'code': 'teams_calendar_create_in_progress', 'createStatus': {**(claim or {}), 'state': state},
        }, status=409)
    return JsonResponse({
        'error': ('An earlier Create for this module did not report back, so Microsoft may already hold its meeting. '
                  "Check the organizer's Outlook calendar and this module's saved calendar before creating again."),
        'code': 'teams_calendar_create_uncertain', 'createStatus': {**(claim or {}), 'state': state},
    }, status=409)


#: Who may have the create send the LMS schedule emails: exactly the roles the
#: schedule-email endpoint admits, so creating a calendar never widens who can mail.
EMAIL_ROLES = frozenset({'admin', 'staff'})


def with_creation_emails(request, response):
    """A successful create, with its schedule emails already sent by the server.

    Only a 201 is a calendar Microsoft built, verified and invited everyone to;
    every other outcome returns untouched and emails nobody. The result rides
    on the create's own response as ``scheduleEmail``, so the browser shows what
    was sent instead of sending it. When the caller may not send, nothing is
    attempted and the browser's own request meets the endpoint's refusal.
    """
    if response.status_code != 201:
        return response
    try:
        body = json.loads(response.content)
    except (ValueError, TypeError, AttributeError):
        return response
    meeting = body.get('meeting') if isinstance(body, dict) else None
    live_session_id = str((meeting or {}).get('liveSessionId') or '') if isinstance(meeting, dict) else ''
    if not live_session_id or body.get('warnings'):
        return response
    try:
        from login.sessions import authenticate_request
        account = authenticate_request(request)
    except Exception:
        account = None
    if account is None or getattr(account, 'role', None) not in EMAIL_ROLES:
        return response
    from .teams_schedule_delivery import send_creation_emails
    return JsonResponse({**body, 'scheduleEmail': send_creation_emails(live_session_id)}, status=response.status_code)


def response_outcome(response):
    try:
        body = json.loads(response.content)
    except (ValueError, TypeError, AttributeError):
        body = {}
    body = body if isinstance(body, dict) else {}
    meeting = body.get('meeting') if isinstance(body.get('meeting'), dict) else {}
    return str(body.get('code') or ''), str(meeting.get('liveSessionId') or body.get('liveSessionId') or '')


@csrf_exempt
@transaction.non_atomic_requests
def teams_meeting_collection(request):
    """The create endpoint behind a per-module claim; every other method unchanged."""
    from . import views as v
    if request.method != 'POST':
        return v.curriculum_teams_meeting(request)
    payload = v.json_body(request)
    key = module_key(v, payload) if isinstance(payload, dict) else ''
    if not key:
        # No module to be a duplicate of (and nothing the endpoint would supersede).
        return with_creation_emails(request, v.curriculum_teams_meeting(request))
    claims = CreateClaims(connection)
    try:
        claims.check()
    except RuntimeError as exc:
        return JsonResponse({'error': str(exc), 'code': 'teams_create_guard_unavailable'}, status=503)
    token = uuid.uuid4().hex
    if not claims.claim(key, token, request.GET.get('confirmUncertain') == '1'):
        return refusal(claims.read(key))
    try:
        response = v.curriculum_teams_meeting(request)
    except Exception:
        # Microsoft may or may not hold the meeting: uncertain, never auto-retried.
        try:
            claims.finish(key, token, 'uncertain', None, 'create_raised')
        except Exception:
            logger.exception('The Teams create claim could not record an uncertain outcome; its lease will expire.')
        raise
    # Before the claim is marked done: a browser that lost this request reads
    # the claim to learn how the create ended, and by then its emails are sent.
    try:
        response = with_creation_emails(request, response)
    except Exception:
        # The calendar is saved either way; its claim must still be finished.
        logger.exception('The Teams create could not attach its schedule email result.')
    code, live_session_id = response_outcome(response)
    if response.status_code == 201:
        # The calendar exists, so the form it was drafted from is finished.
        from .teams_create_drafts import clear_draft
        clear_draft(key)
    try:
        claims.finish(key, token, 'done', response.status_code, code, live_session_id)
    except Exception:
        # The response still goes back. Left 'creating', the claim reads as
        # uncertain once its lease ends, which errs towards asking a person.
        logger.exception('The Teams create claim could not record its outcome; its lease will expire.')
    return response


def active_calendar(v, key):
    kind, _, ident = key.partition(':')
    column = 'module_catalogue_id' if kind == 'module' else 'module_draft_id'
    rows = v.authoring_fetch_all(v.LIVE_SESSIONS_TABLE, f"{column} = %s and status = 'active'", [ident],
                                 'updated_at desc, created_at desc', ensure_tables=False)
    if not rows:
        return None
    row = rows[0]
    warnings = [str(item) for item in v.parse_json_value(row.get('warnings'), []) or []]
    return {'liveSessionId': v.clean_str(row.get('id')), 'joinUrl': v.clean_str(row.get('join_url')),
            'organizerEmail': v.clean_str(row.get('organizer_email')), 'warnings': warnings,
            'settingsApplied': not warnings}


@transaction.non_atomic_requests
def teams_create_status(request):
    """Read-only: where this module's last Create stands, and its saved calendar."""
    from . import views as v
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    key = module_key(v, {'moduleCatalogueId': request.GET.get('moduleCatalogueId'),
                         'moduleDraftId': request.GET.get('moduleDraftId')})
    if not key:
        return JsonResponse({'error': 'Name the module whose Create to check.'}, status=400)
    claims = CreateClaims(connection)
    try:
        claims.check()
        claim = claims.read(key)
    except RuntimeError as exc:
        return JsonResponse({'error': str(exc), 'code': 'teams_create_guard_unavailable'}, status=503)
    calendar = active_calendar(v, key)
    return JsonResponse({'state': claim_state(claim), 'claim': claim, 'calendar': calendar,
                         'emails': email_progress(calendar['liveSessionId']) if calendar else None})


def email_progress(live_session_id):
    """How far this calendar's schedule emails have got: counts only, never addresses.

    Lets the create dialog show "2 of 4 emails sent" while the server is still
    sending, instead of one undifferentiated wait. ``None`` when the ledger
    cannot be read; the dialog then simply does not show the count.
    """
    try:
        from .teams_schedule_delivery import DeliveryLedger
        ledger = DeliveryLedger(connection)
        ledger.check()
        states = list(ledger.states(live_session_id).values())
    except Exception:
        return None
    counts = {status: states.count(status) for status in ('queued', 'accepted', 'failed', 'sending', 'unknown')}
    return {'total': len(states), 'accepted': counts['accepted'], 'queued': counts['queued'],
            'failed': counts['failed'], 'uncertain': counts['sending'] + counts['unknown']}
