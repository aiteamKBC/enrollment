"""Read-only reconciliation of one meeting's invitation list against Microsoft.

The calendar sync already running on this page reconciles *dates*: it learns
that a session was cancelled in Outlook. Nothing reconciles *people*. A save
only writes when the browser's form differs from the roster the LMS stored, so
somebody added to the meeting in Outlook -- by the organizer, by a co-organizer,
or by whoever a learner forwarded the invitation to -- is invisible here: the
form matches the stored roster, Save says nothing has changed, and the extra
person stays on every session.

This module answers the question that gap leaves open, and only that question.
It reads Microsoft and reports the difference. It writes nothing on either
side: no PATCH, no invitation, no roster row, no occurrence, no schedule. The
correction, when somebody decides to make one, is the existing Save.

The baseline is the *published* roster -- the addresses stored on
``curriculum.live_sessions`` after a save Microsoft confirmed -- and never the
form the author currently has open. Somebody typed into the box a moment ago is
not somebody Microsoft was ever asked to invite, and reporting them as "missing
from Teams" would turn every unsaved edit into an alarm.

On aliases: Microsoft stores an internal alias under its mailbox's primary
address, so one published address can come back spelled differently. The
publish gate in ``teams_calendar_checks.attendee_differences`` pairs those off
and drops them, which is right when the question is "may this save proceed" and
wrong here -- pairing an alias also pairs a real removal against a real
gate-crasher, and hides exactly the person this report exists to find. So both
sides are reported in full and the ambiguity is named instead
(``aliasPossible``), leaving the reader to tell one case from the other.
"""
import logging
from datetime import datetime, timezone
from urllib.parse import quote

import httpx
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from login.permissions import require_role

from .teams_calendar_checks import attendee_addresses, event_organizer_address

logger = logging.getLogger(__name__)

#: Everything the comparison needs from an event, and nothing else: no body, no
#: join link, no attendee response status, no extended properties.
EVENT_SELECT = 'id,attendees,organizer,isCancelled'


class AttendeeCompareError(RuntimeError):
    """The comparison could not be made. Carries the message shown to the user."""

    def __init__(self, message, code=''):
        super().__init__(message)
        self.code = code


def comparable_addresses(items):
    """Invitation addresses that can be compared, however each entry is written.

    ``attendee_addresses`` already lower-cases, trims and de-duplicates. What it
    keeps and this drops is a value that is not an address at all: Graph has
    returned an entry with an empty or malformed ``address`` before, and such an
    entry is not evidence that anybody was added -- reporting it as an extra
    attendee would be a difference nobody can act on.
    """
    return {address for address in attendee_addresses(items)
            if '@' in address and ' ' not in address and not address.startswith('@') and not address.endswith('@')}


def attendee_names(items):
    """Display names by address, for the addresses Microsoft named.

    First spelling wins, so a duplicated attendee does not change the name shown.
    """
    names = {}
    for item in items or []:
        entry = (item.get('emailAddress') or {}) if isinstance(item, dict) else {}
        address = str(entry.get('address') or '').strip().lower()
        name = str(entry.get('name') or '').strip()
        if address and name and address not in names:
            names.setdefault(address, name)
    return names


def person(address, names):
    """One row of the report: the mailbox, and the name if Microsoft gave one."""
    return {'email': address, 'name': names.get(address, '')}


def compare_attendee_lists(published, held, organizer='', checked_at=None):
    """What the LMS published, what Microsoft holds, and the difference.

    ``published`` is the stored invitation list, ``held`` is Microsoft's
    ``attendees`` array. The organizer is excluded from both sides: Microsoft
    never lists the owner of an event among its attendees, so an organizer who
    also appears on the LMS list would otherwise be reported missing forever.
    """
    owner = {str(organizer or '').strip().lower()} - {''}
    wanted = comparable_addresses(published) - owner
    have = comparable_addresses(held) - owner
    names = attendee_names(held)
    matching = sorted(wanted & have)
    missing = sorted(wanted - have)
    extra = sorted(have - wanted)
    return {
        'status': 'match' if not missing and not extra else 'different',
        'lmsCount': len(wanted),
        'teamsCount': len(have),
        'matchingCount': len(matching),
        'extraCount': len(extra),
        'missingCount': len(missing),
        'matching': [person(address, names) for address in matching],
        'extraOnTeams': [person(address, names) for address in extra],
        'missingFromTeams': [person(address, names) for address in missing],
        # Microsoft answers an internal alias with its mailbox's primary
        # address, which arrives as one missing and one extra. So can a person
        # genuinely swapped for another. Only both lists being occupied makes
        # the two indistinguishable, and only then is it worth saying so.
        'aliasPossible': bool(missing and extra),
        'checkedAt': (checked_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'),
    }


def published_invitation_list(series):
    """Everyone the LMS last published to this meeting, in any role.

    The same union the save builds before it writes: co-organizers, presenters
    and attendees are all invitees of the calendar event, and Microsoft holds
    them in one list. Comparing only the ``attendees`` column would report every
    presenter as a gate-crasher.
    """
    from . import views as v

    return list(dict.fromkeys([
        *v.teams_series_email_list(series.get('co_organizers')),
        *v.teams_series_email_list(series.get('presenters')),
        *v.teams_series_email_list(series.get('attendees')),
    ]))


def calendar_event_ids(series):
    """Every Graph master this one calendar is made of.

    A module delivered on more than one weekday is several Graph series behind
    one live-session row, and each carries the same invitation list. Reading
    only the main event would miss a person added to the Thursday series alone.
    """
    from . import views as v
    from .teams_weekly_calendar import stored_calendar_series

    ids = [v.clean_str(entry.get('eventId')) for entry in stored_calendar_series(series) if isinstance(entry, dict)]
    ids.append(v.clean_str(series.get('graph_event_id')))
    return list(dict.fromkeys(identifier for identifier in ids if identifier))


def read_event_attendees(read, owner, event_ids):
    """The union of the attendees Microsoft holds right now, and the organizer.

    One GET per master and nothing else: no instance expansion, no online
    meeting read, no write. An attendee list is a property of the series, so its
    occurrences cannot hold anybody the master does not.
    """
    held, organizer = [], ''
    for event_id in event_ids:
        event = read(f'users/{quote(owner, safe="@.")}/events/{quote(event_id, safe="")}?$select={EVENT_SELECT}')
        if event is None:
            raise AttendeeCompareError('The Microsoft calendar event could not be found. It may have been deleted in Outlook.',
                                       'event_not_found')
        if not isinstance(event, dict):
            raise AttendeeCompareError('Microsoft returned an attendee list that could not be read.', 'malformed_response')
        attendees = event.get('attendees')
        if attendees is not None and not isinstance(attendees, list):
            raise AttendeeCompareError('Microsoft returned an attendee list that could not be read.', 'malformed_response')
        held.extend(attendees or [])
        organizer = organizer or event_organizer_address(event)
    return held, organizer


def compare_meeting_attendees(live_session_id):
    """Read the stored roster, read Microsoft, and report the difference."""
    from . import views as v
    from .teams_cancellation_checks import CalendarStateError
    from .teams_calendar_state import calendar_reader

    series_rows = v.authoring_fetch_all(v.LIVE_SESSIONS_TABLE, 'id = %s', [live_session_id])
    if not series_rows:
        raise LookupError('Live session series not found.')
    series = series_rows[0]
    organizer = v.clean_str(series.get('organizer_email')) or v.teams_meeting_default_organizer()
    event_ids = calendar_event_ids(series)
    if not organizer or not event_ids:
        raise AttendeeCompareError('This meeting has no Microsoft calendar event to compare against yet.',
                                   'no_calendar_event')
    published = published_invitation_list(series)
    if not published:
        raise AttendeeCompareError('The LMS has no published invitation list for this meeting yet. Save invitations first.',
                                   'no_published_roster')
    try:
        with calendar_reader() as read:
            held, graph_organizer = read_event_attendees(read, organizer, event_ids)
    except CalendarStateError as exc:
        status = getattr(exc, 'status_code', 0)
        logger.warning('Attendee comparison could not read Microsoft for %s (HTTP %s)', live_session_id, status or 'unknown')
        if status in (401, 403):
            raise AttendeeCompareError(
                'Microsoft refused the request for this meeting. Check the calendar permissions for the organizer account.',
                'graph_forbidden') from exc
        raise AttendeeCompareError('Unable to read the current attendee list from Microsoft. Nothing was changed; try again.',
                                   'graph_unavailable') from exc
    except httpx.HTTPError as exc:
        # The address, the status and the token stay in the server log; the
        # browser is told what it can act on and nothing about the transport.
        logger.warning('Attendee comparison transport failure for %s: %s', live_session_id, type(exc).__name__)
        raise AttendeeCompareError('Unable to read the current attendee list from Microsoft. Nothing was changed; try again.',
                                   'graph_unavailable') from exc
    except ValueError as exc:
        logger.warning('Attendee comparison read an unreadable Microsoft response for %s', live_session_id)
        raise AttendeeCompareError('Microsoft returned an attendee list that could not be read.', 'malformed_response') from exc
    return compare_attendee_lists(published, held, graph_organizer or organizer)


@require_role('admin', 'staff')
@require_GET
def teams_meeting_attendee_comparison(request, live_session_id):
    """Compare this meeting's published invitation list with Microsoft's.

    A read on both sides, behind the same staff gate as every other Teams
    management endpoint on this page, so the URL cannot be typed by somebody who
    could not open the drawer it belongs to.
    """
    from coach_api.views import has_graph_credentials

    if not has_graph_credentials():
        return JsonResponse({'error': 'Microsoft Graph credentials are not configured.',
                             'code': 'graph_not_configured'}, status=503)
    try:
        result = compare_meeting_attendees(live_session_id)
    except LookupError:
        return JsonResponse({'error': 'Live session series not found.', 'code': 'not_found'}, status=404)
    except AttendeeCompareError as exc:
        status = 502 if exc.code in ('graph_unavailable', 'graph_forbidden', 'malformed_response') else 409
        return JsonResponse({'error': str(exc), 'code': exc.code}, status=status)
    except RuntimeError:
        logger.exception('Attendee comparison failed for %s', live_session_id)
        return JsonResponse({'error': 'Unable to read the current attendee list from Microsoft. Nothing was changed; try again.',
                             'code': 'graph_unavailable'}, status=502)
    response = JsonResponse(result)
    # Every press is a fresh reading of Microsoft; a cached one would answer the
    # question the button was pressed to ask with the answer it already had.
    response['Cache-Control'] = 'no-store, private'
    return response
