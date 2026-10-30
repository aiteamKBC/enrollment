"""A signed record of one schedule change: what the calendar held before, and after.

A calendar write overwrites its occurrence rows, so the dates a learner was
told about are gone the moment Microsoft confirms the new ones. The endpoint
that made the change captures both sides and hands them back as a token the
server signed. Sending the change email later reads only that token and the
stored calendar, so the page that asks for the email can choose whether to send
it, but cannot put dates or recipients of its own into it.
"""
import uuid

from django.core import signing

from .teams_calendar_checks import utc_datetime

SALT = 'curriculum.teams.schedule-notice.v1'
# Long enough to send after a slow Microsoft round trip or a retry the next
# morning; short enough that an old token cannot describe a calendar that has
# since moved again (the send also refuses a token whose "after" is stale).
MAX_AGE_SECONDS = 7 * 24 * 60 * 60


def schedule_snapshot(rows):
    """The live sessions of a calendar as ``[{n, start, end}]``, in session order."""
    held = []
    for row in rows or []:
        if str(row.get('status') or '') in ('cancelled', 'superseded'):
            continue
        start, end = row.get('scheduled_start'), row.get('scheduled_end')
        if not start or not end:
            continue
        held.append({'n': int(row['session_number']), 'start': utc_datetime(start).isoformat(),
                     'end': utc_datetime(end).isoformat()})
    return sorted(held, key=lambda item: (item['start'], item['n']))


def same_schedule(left, right):
    key = lambda items: [(item['n'], utc_datetime(item['start']), utc_datetime(item['end'])) for item in items]
    return key(sorted(left, key=lambda item: item['n'])) == key(sorted(right, key=lambda item: item['n']))


def issue_change_notice(live_id, before, after, notice_id=None):
    """A token naming this update, for the email the author asked for.

    ``notice_id`` names the change for the delivery ledger. A caller that can be
    asked about the same change more than once (a calendar action whose status
    is checked again) passes its own stable id, so every token it hands out
    shares one ledger key and nobody is emailed twice about one change.

    This used to answer '' when no session date had moved, which left the
    browser with nothing to send: an author who had ticked "email attendees and
    organisers about this change" was told afterwards that no email went out
    because no date changed. The tick is an instruction, not a hint, so the
    token is always issued and the message is chosen from the change itself --
    what moved, or, when nothing moved, the schedule as it now stands.
    """
    if not live_id:
        return ''
    return signing.dumps({'id': notice_id or uuid.uuid4().hex, 'liveId': live_id, 'before': before, 'after': after},
                         salt=SALT, compress=True)


def read_change_notice(token, live_id):
    try:
        notice = signing.loads(token, salt=SALT, max_age=MAX_AGE_SECONDS)
    except signing.SignatureExpired as exc:
        raise ValueError('This change is too old to email about. Review the calendar and send a fresh update.') from exc
    except signing.BadSignature as exc:
        raise ValueError('This change notice could not be verified.') from exc
    if not isinstance(notice, dict) or notice.get('liveId') != live_id or not notice.get('id'):
        raise ValueError('This change notice belongs to another calendar.')
    return notice


def as_occurrences(snapshot, links=None):
    """Snapshot entries in the occurrence shape the email renderer reads."""
    links = links or {}
    return [{'session_number': item['n'], 'scheduled_start': item['start'], 'scheduled_end': item['end'],
             'join_url': links.get(item['n'], '')} for item in snapshot]
