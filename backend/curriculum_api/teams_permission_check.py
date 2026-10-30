"""Read-only check of whether this app may manage one Teams meeting, and why not.

Answers the question a refused onlineMeeting write leaves open: is the app's
permission missing, is the organiser not covered by a Teams application access
policy, does the meeting belong to somebody else, or did Microsoft refuse the
write for another reason? It only reads: every Graph call goes through
``read_only`` and a non-GET request is refused before it is made. No meeting,
calendar, permission or LMS row is changed, and nobody is emailed. The access
token is decoded for its granted roles and never returned or logged.

Microsoft facts this relies on (Microsoft Learn, "Update onlineMeeting" and
"Configure an application access policy"):
- ``PATCH users/{userId}/onlineMeetings/{id}`` with application permission needs
  ``OnlineMeetings.ReadWrite.All`` and an application access policy granted to
  ``userId`` (the organiser's Entra object ID) containing this app's client ID.
- The same policy governs reading that user's online meetings, and Microsoft's
  documented error without it is ``Forbidden: No application access policy
  found for this app``.
- Policy changes can take up to 30 minutes to reach Graph.
"""
import base64
import json
import logging
from datetime import datetime, timezone
from urllib.parse import quote

from django.http import JsonResponse
from django.views.decorators.http import require_GET
from login.permissions import require_role

from .teams_meeting_options_policy import NOT_APPLIED, graph_error_details

logger = logging.getLogger(__name__)

READ_WRITE = 'OnlineMeetings.ReadWrite.All'
USER_READ = 'User.Read.All'
SESSION_RESULT_ROLES = (
    'OnlineMeetingArtifact.Read.All',
    'OnlineMeetingRecording.Read.All',
    'OnlineMeetingTranscript.Read.All',
)


def _text(value):
    return str(value or '').strip()


def token_claims(token):
    """Granted roles of an app token, read from its payload. The token itself never leaves here."""

    try:
        payload = _text(token).split('.')[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    except (IndexError, ValueError, TypeError):
        return {'roles': [], 'appId': '', 'tenantId': '', 'readable': False}
    roles = claims.get('roles') if isinstance(claims.get('roles'), list) else []
    return {'roles': sorted(str(role) for role in roles), 'appId': _text(claims.get('appid') or claims.get('azp')),
            'tenantId': _text(claims.get('tid')), 'readable': True}


def read_only(graph_request):
    def read(method, path, **kwargs):
        if method.upper() != 'GET' or kwargs.get('payload') is not None:
            raise PermissionError('The permission check only reads from Microsoft.')
        return graph_request('GET', path)
    return read


def _attempt(read, path, at):
    try:
        return read('GET', path), None
    except RuntimeError as exc:
        return None, graph_error_details(str(exc), at=at)


def last_option_failure(series):
    """The most recent refused options write saved on this series, if any."""

    warnings = series.get('warnings')
    if isinstance(warnings, str):
        try:
            warnings = json.loads(warnings)
        except ValueError:
            warnings = []
    for item in reversed(warnings if isinstance(warnings, list) else []):
        if isinstance(item, dict) and item.get('code') == NOT_APPLIED:
            error = item.get('graphError') if isinstance(item.get('graphError'), dict) else graph_error_details(item.get('detail'))
            return {'groups': item.get('groups') or [], 'message': _text(item.get('message')), 'graphError': error}
    return None


def admin_commands(client_id, object_id):
    client_id = client_id or '<application client ID>'
    object_id = object_id or '<organiser object ID>'
    return [
        '# Teams PowerShell (MicrosoftTeams module), as a Teams administrator. Read-only first:',
        'Connect-MicrosoftTeams',
        f'Get-CsOnlineUser -Identity "{object_id}" | Select-Object UserPrincipalName, ApplicationAccessPolicy, TeamsMeetingPolicy',
        'Get-CsApplicationAccessPolicy | Select-Object Identity, AppIds',
        f'# A policy is in effect only if its AppIds contains {client_id} and it is granted to this user or -Global.',
        '# If it is missing, an administrator decides whether to grant it (not run by the LMS):',
        f'# Grant-CsApplicationAccessPolicy -PolicyName "<policy containing {client_id}>" -Identity "{object_id}"',
        '# Then wait up to 30 minutes before trying the save again.',
    ]


def permission_report(series, *, graph_request, graph_settings, token, owner_id, organizer, online_meeting_id, join_url,
                      now=None):
    """Build the report. ``owner_id`` is the user ID the LMS writes under (from the join link or config)."""

    at = (now or datetime.now(timezone.utc)).isoformat()
    read = read_only(graph_request)
    claims = token_claims(token) if token else {'roles': [], 'appId': '', 'tenantId': '', 'readable': False}
    client_id = _text(graph_settings.get('client_id'))
    checks, actions = [], []

    def check(key, status, summary, **extra):
        checks.append({'key': key, 'status': status, 'summary': summary, **extra})

    # 1. The app and what Microsoft granted it.
    has_read_write = READ_WRITE in claims['roles']
    check('app_permission', 'pass' if has_read_write else 'fail' if claims['readable'] else 'unknown',
          (f'The app token carries {READ_WRITE}.' if has_read_write else
           f'The app token does not carry {READ_WRITE}.' if claims['readable'] else
           'The app token could not be read, so its permissions are unknown.'),
          roles=[role for role in claims['roles'] if role.startswith(('OnlineMeeting', 'Calendars', 'User.'))])
    if claims['readable'] and not has_read_write:
        actions.append(f'In Entra ID > App registrations > {client_id} > API permissions, add the application permission '
                       f'{READ_WRITE} and grant admin consent. The LMS never grants tenant permissions itself.')
    missing_results_roles = [role for role in SESSION_RESULT_ROLES if role not in claims['roles']]
    results_status = 'unknown' if not claims['readable'] else 'fail' if missing_results_roles else 'pass'
    results_summary = (
        'The app token could not be read, so session result permissions are unknown.' if not claims['readable'] else
        'The app token is missing session result permissions: ' + ', '.join(missing_results_roles) + '.' if missing_results_roles else
        'The app token carries the attendance, recording and transcript permissions.'
    )
    check('session_results_permissions', results_status, results_summary,
          roles=[role for role in claims['roles'] if role in SESSION_RESULT_ROLES])
    if missing_results_roles and claims['readable']:
        actions.append(f'In Entra ID > App registrations > {client_id} > API permissions, add the application permissions '
                       f'{", ".join(missing_results_roles)} and grant admin consent. Restart the LMS worker after '
                       'updating its Microsoft Graph app settings.')

    # 2. Who the organiser is, by email in the directory and by the ID the LMS writes under.
    directory_id = ''
    if organizer:
        user, error = _attempt(read, f"users/{quote(organizer, safe='')}?$select=id,userPrincipalName,accountEnabled", at)
        if user:
            directory_id = _text(user.get('id'))
            matches = not owner_id or directory_id.casefold() == _text(owner_id).casefold()
            check('organizer_identity', 'pass' if matches else 'fail',
                  ('The saved organiser email belongs to the user ID the LMS writes under.' if matches else
                   'The saved organiser email belongs to a different user than the one in the meeting link.'),
                  organizerEmail=organizer, directoryObjectId=directory_id, accountEnabled=user.get('accountEnabled'))
            if not matches:
                actions.append('The meeting link names a different organiser than the one saved in the LMS. Confirm in '
                               'Outlook who owns this meeting before granting anything.')
        else:
            check('organizer_identity', 'unknown', 'The organiser could not be looked up in the directory.',
                  organizerEmail=organizer, graphError=error)

    # 3. Can the app read this meeting as its organiser? The access policy governs reads too.
    meeting_path = (f"users/{quote(owner_id, safe='')}/onlineMeetings/{quote(online_meeting_id, safe='')}"
                    if owner_id and online_meeting_id else '')
    meeting, error = (_attempt(read, meeting_path, at) if meeting_path else (None, None))
    if meeting:
        identity = (((meeting.get('participants') or {}).get('organizer') or {}).get('identity') or {})
        graph_organizer = _text((identity.get('user') or {}).get('id'))
        owned = not graph_organizer or graph_organizer.casefold() == _text(owner_id).casefold()
        check('meeting_access', 'pass' if owned else 'fail',
              ('The app can read this meeting as its organiser, so an application access policy covers this organiser.'
               if owned else 'Microsoft reports a different organiser for this meeting than the one the LMS writes under.'),
              current={'lobby': (meeting.get('lobbyBypassSettings') or {}).get('scope'),
                       'allowRecording': meeting.get('allowRecording'),
                       'recordAutomatically': meeting.get('recordAutomatically'),
                       'allowTranscription': meeting.get('allowTranscription'),
                       'allowedPresenters': meeting.get('allowedPresenters')})
    elif error:
        policy_missing = error['status'] == 403
        check('meeting_access', 'fail', (
            'Microsoft refused to let the app read this meeting as its organiser. This is what a missing Teams '
            'application access policy for this organiser looks like.' if policy_missing else
            'Microsoft could not find this meeting under the organiser the LMS writes under.' if error['status'] == 404 else
            'Microsoft did not answer the meeting read.'), graphError=error)
        if policy_missing:
            actions.append('Ask a Teams administrator to confirm, and if appropriate grant, an application access policy '
                           f'containing app {client_id} for organiser {owner_id} (commands below).')
        elif error['status'] == 404:
            actions.append('The saved online meeting ID or organiser is wrong for this meeting. Do not grant permissions '
                           'for this; review the meeting in the Teams Meetings page.')
    else:
        check('meeting_access', 'unknown', 'This meeting has no online meeting ID saved yet, so it could not be read.')

    # 4. The last refused write, as Microsoft reported it.
    failure = last_option_failure(series)
    if failure:
        check('last_options_write', 'fail', 'The last attempt to apply this meeting’s settings was refused.', **failure)
        if meeting and failure['graphError'].get('status') == 403:
            actions.append(
                'Reading works but the settings write was refused, so the access policy is in place and something else '
                'blocked the write. Check, read-only, the organiser’s Teams meeting policy (recording, transcription, '
                'presenter and lobby settings), any meeting template or sensitivity label that locks meeting options, '
                'and the Graph request ID with Microsoft support. Saving one setting group at a time shows which one '
                'Microsoft refuses.')
    else:
        check('last_options_write', 'pass', 'No refused settings write is saved for this meeting.')

    verdicts = {item['status'] for item in checks}
    return {
        'checkedAt': at,
        'readOnly': True,
        'verdict': 'fail' if 'fail' in verdicts else 'unknown' if 'unknown' in verdicts else 'pass',
        'app': {'clientId': client_id, 'tenantId': _text(graph_settings.get('tenant_id')),
                'tokenAppId': claims['appId'], 'graphBaseUrl': _text(graph_settings.get('base_url')),
                'permissionContext': 'application (client credentials)'},
        'organizer': {'email': organizer, 'objectId': owner_id, 'directoryObjectId': directory_id},
        'meeting': {'onlineMeetingId': online_meeting_id, 'graphPath': meeting_path,
                    'hasJoinUrl': bool(join_url)},
        'checks': checks,
        'actions': actions,
        'adminCommands': admin_commands(client_id, owner_id),
    }


def check_live_session(live_session_id):
    """Run the report for one saved series (module calendar or additional week meeting)."""
    from coach_api.views import get_graph_settings, microsoft_graph_request, microsoft_graph_token
    from . import views as v

    rows = v.authoring_fetch_all(v.LIVE_SESSIONS_TABLE, 'id = %s', [live_session_id])
    if not rows:
        raise LookupError('Live session series not found.')
    series = rows[0]
    organizer = v.clean_str(series.get('organizer_email')) or v.teams_meeting_default_organizer()
    join_url = v.clean_str(series.get('join_url'))
    try:
        token = microsoft_graph_token()
    except RuntimeError:
        token = ''
    return permission_report(
        series, graph_request=microsoft_graph_request, graph_settings=get_graph_settings(), token=token,
        owner_id=v.teams_online_meeting_owner_id(organizer, join_url), organizer=organizer,
        online_meeting_id=v.clean_str(series.get('online_meeting_id')), join_url=join_url,
    )


@require_role('admin', 'staff')
@require_GET
def teams_meeting_permission_check(request, live_session_id):
    """Staff-only, read-only Microsoft permission check for one meeting."""
    from coach_api.views import has_graph_credentials

    if not has_graph_credentials():
        return JsonResponse({'error': 'Microsoft Graph credentials are not configured.',
                             'code': 'graph_not_configured'}, status=503)
    try:
        report = check_live_session(live_session_id)
    except LookupError:
        return JsonResponse({'error': 'Live session series not found.', 'code': 'not_found'}, status=404)
    except Exception:
        logger.exception('Teams permission check failed for %s', live_session_id)
        return JsonResponse({'error': 'The permission check could not finish. Nothing was changed.',
                             'code': 'check_failed'}, status=502)
    response = JsonResponse(report)
    response['Cache-Control'] = 'no-store, private'
    return response
