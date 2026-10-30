"""Cancelling a Teams calendar: one mutation per meeting, then proof it happened.

Cancelling is not the mirror image of creating or moving a meeting, and this is
why it lives apart from both.

A move can be verified by reading the new time back: the event is still there
and either says the reviewed instant or it does not. A cancellation has to be
proved by an *absence*, and Microsoft expresses that absence in more than one
way depending on what was cancelled and how far its own processing has got:

* a cancelled single meeting or series master leaves the organizer's calendar
  for Deleted Items, so reading it back answers ``ErrorItemNotFound``;
* a cancelled occurrence of a series disappears from the master's instances and
  its ``occurrenceId`` joins the master's ``cancelledOccurrences``;
* while ``/cancel``'s 202 is still being processed, the event can briefly read
  back with ``isCancelled`` already true.

The path this replaced asked only one of those questions. It cancelled the
occurrence, then handed verification to the whole-calendar reconcile sweep,
which can only recognise a cancelled occurrence through ``cancelledOccurrences``
and, failing that, reports "could not be matched" and deliberately preserves the
local status. Microsoft had genuinely cancelled the meeting, the LMS never
agreed that it had, the operation stopped at ``uncertain`` for ever, its
cancellation email -- which only goes out on ``done`` -- never went, and because
an unfinished operation blocks the next one, every later cancellation was
refused with "A previous action needs a status check".

So verification here asks Microsoft directly about the one meeting the operation
cancelled, accepts any of the answers above as proof, and saves the local status
itself. It is still read-only and still conservative: no answer short of
positive evidence is treated as a cancellation, because the reconcile sweep's
caution is right -- it simply must not be the only voice, when we are the ones
who asked for the cancellation and Microsoft accepted it.
"""
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

from django.db import connection

# Same package, and deliberately the same reader: a cancellation must be judged
# against exactly the evidence the status sweep judges an outside cancellation
# against, or the two screens would disagree about the same calendar.
from .teams_cancellation_checks import FIELDS, CalendarStateError

# Graph rejects `POST /events/{id}/cancel` for the occurrence of a recurring
# series in some tenants. The rejection is definitive -- nothing was sent, no
# invitee was mailed -- so falling through to the organizer delete, which is
# how an occurrence is cancelled, is not a retry of an accepted mutation.
OCCURRENCE_CANCEL_REJECTIONS = {
    'ErrorInvalidRequest', 'ErrorCannotCancelOccurrence', 'ErrorInvalidRecurrenceObject',
    'ErrorCalendarCannotCancelOccurrence', 'ErrorInvalidOperationForMeetingOccurrence',
}
# 202 is the documented answer to /cancel; 200 and 204 are what a delete and
# some tenants' cancel answer. Treating those two as "not confirmed" was its own
# way of stranding a cancellation Microsoft had already made.
CANCEL_ACCEPTED = (200, 202, 204)


def _error_code(response):
    try:
        return str((response.json().get('error') or {}).get('code') or '')
    except (ValueError, AttributeError):
        return ''


def send_cancellation(client, path, comment, *, occurrence, not_sent):
    """Exactly one accepted mutation for one meeting. Never an automatic retry.

    ``not_sent`` raises the caller's definitive-preflight-failure error, which
    means "nothing reached Microsoft, it is safe to review again". Only a
    rejection may raise it; an accepted request never does.
    """
    response = client.post(path + '/cancel', json={'comment': comment} if comment else {})
    if response.status_code in CANCEL_ACCEPTED:
        return True
    if not (400 <= response.status_code < 500) or response.status_code in (408, 409):
        # Ambiguous. The caller stops and verifies rather than sending again.
        return False
    code = _error_code(response)
    if not occurrence or code not in OCCURRENCE_CANCEL_REJECTIONS:
        raise not_sent('Microsoft rejected the cancellation. No cancellation was confirmed.')
    # One session of a series: deleting the occurrence as its organizer is the
    # cancellation, and Microsoft mails the invitees exactly as /cancel would.
    # The author's message has no place to travel on a delete; the LMS
    # cancellation email carries it instead.
    deleted = client.delete(path)
    if deleted.status_code in CANCEL_ACCEPTED:
        return True
    if 400 <= deleted.status_code < 500 and deleted.status_code not in (408, 409):
        raise not_sent('Microsoft rejected the cancellation of this session. No cancellation was confirmed.')
    return False


def cancellation_confirmed(series, command, read):
    """Has Microsoft cancelled this exact meeting? Read-only; evidence only.

    True only on positive evidence. An event that reads back alive, or a read
    that cannot settle the question, answers False, and the caller asks again
    rather than recording a cancellation Microsoft has not made.
    """
    owner = quote(str(series.get('organizer_email') or ''), safe='')
    if not owner:
        raise CalendarStateError('The saved calendar identity is incomplete.')
    prefix = f'users/{owner}'
    select = urlencode({'$select': FIELDS})

    def event(event_id):
        return read(f'{prefix}/events/{quote(event_id, safe="")}?{select}')

    # Gone from the organizer's calendar: the plainest proof there is, and the
    # one the reconcile sweep never asks for on the occurrence it just cancelled.
    target = event(command['eventId'])
    if target is None:
        return True
    if target.get('isCancelled') is True:
        return True
    root_id = str(command.get('rootId') or '')
    if not root_id or root_id == command['eventId']:
        # A whole meeting that still reads back live was not cancelled.
        return False
    master = event(root_id)
    if master is None or master.get('isCancelled') is True:
        # The series went; this occurrence went with it.
        return True
    occurrence_id = str(target.get('occurrenceId') or command.get('occurrenceGraphId') or '')
    return bool(occurrence_id and occurrence_id in set(master.get('cancelledOccurrences') or []))


def persist_cancellation(live_id, command, scope, *, complete):
    """Record what Microsoft cancelled, and nothing else.

    A single session marks its own occurrence and leaves the rest of the
    calendar -- its weeks, its module dates, its other meetings -- exactly as
    they were. A series marks the calendar and every session still waiting to
    run, once every one of its meetings is confirmed.

    A session that already ran is never rewritten: its attendance, its evidence
    and its history are facts about a session that happened, and a calendar
    entry being withdrawn afterwards does not unmake them.
    """
    from . import views as v
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # The same session this calendar's review calls eligible: still scheduled,
    # never started, no attendance report and nobody recorded as having
    # attended. `attendance_report_id` and `participant_count` carry '' and 0
    # rather than NULL, so they are read as the review reads them.
    kept = """status = 'scheduled' AND actual_start IS NULL
              AND COALESCE(attendance_report_id, '') = ''
              AND COALESCE(participant_count, 0) = 0"""
    changed = 0
    if scope == 'leftover':
        # A slot on Teams that no LMS session is held on: Microsoft's own record
        # of it is the only thing the cancellation changes.
        return 0
    with connection.cursor() as cursor:
        if scope == 'occurrence':
            cursor.execute(f"""UPDATE curriculum.live_session_occurrences
                               SET status = 'cancelled', updated_at = %s
                               WHERE id = %s AND live_session_id = %s AND {kept}""",
                           [now, command.get('occurrenceId'), live_id])
            changed = cursor.rowcount
        elif complete:
            cursor.execute(f"""UPDATE curriculum.live_session_occurrences
                               SET status = 'cancelled', updated_at = %s
                               WHERE live_session_id = %s AND {kept}""", [now, live_id])
            changed = cursor.rowcount
            cursor.execute("""UPDATE curriculum.live_sessions SET status = 'cancelled', updated_at = %s
                              WHERE id = %s AND status = 'active'""", [now, live_id])
            changed += cursor.rowcount
    if changed:
        v.invalidate_curriculum_cache()
    return changed
