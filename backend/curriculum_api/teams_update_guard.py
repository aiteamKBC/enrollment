"""One logical calendar change is announced by Microsoft at most once.

An update can time out in the browser -- or one Graph call can time out on the
server -- after Microsoft has already applied it. The author then presses
Update again with exactly the same request. Before this guard, that retry
re-sent the announced attendee write (another "Updated:" to every invitee) and
the LMS change email under a brand new ledger key (another "was / now" to
every learner).

The guard names the change by what it asks for, not by when it was asked:

* ``logical_change_id`` hashes the calendar's saved LMS schedule, the
  requested schedule, the invitation list and the subject. A retry of the same
  request produces the same id until the LMS has persisted the result; once it
  has, the retry's "before" equals its "after" and there is nothing left to
  announce at all.
* ``claim_announcement`` records, in the existing durable delivery ledger and
  BEFORE Microsoft is asked, that this change is about to be announced. A later
  attempt that finds the claim taken does not announce again: whether the first
  write reached anyone cannot be read back from Graph, so it is reported as
  already attempted rather than repeated blindly.

The ledger stores this as one row per change, under its own key, with a
``microsoft:<part>`` pseudo-recipient. It is never read by any email batch,
because every email batch reads its own key.
"""
import hashlib
import json
import logging
import re

from .teams_calendar_checks import utc_datetime

logger = logging.getLogger(__name__)

# Microsoft refused the request outright: nothing was applied, so nothing was
# announced, and the same change may be announced again. A timeout, a 5xx and
# a 408 are not this -- the write may well have landed.
_DEFINITE_REFUSAL = re.compile(r'HTTP (4\d\d)')


def schedule_entries(snapshot):
    """``[{n, start, end}]`` as sortable, zone-free tuples."""
    entries = []
    for item in snapshot or []:
        start, end = utc_datetime(item.get('start')), utc_datetime(item.get('end'))
        if start is None or end is None:
            continue
        entries.append((int(item.get('n') or 0), start.replace(second=0, microsecond=0).isoformat(),
                        end.replace(second=0, microsecond=0).isoformat()))
    return sorted(entries)


def targets_snapshot(targets):
    """Calendar targets in the snapshot shape the change notice uses."""
    return [{'n': int(item['session_number']), 'start': utc_datetime(item['start']).isoformat(),
             'end': utc_datetime(item['end']).isoformat()} for item in targets or []]


def logical_change_id(live_id, before, after, people=(), title='', extra=None):
    """A stable name for "move this calendar from ``before`` to ``after``, for these people"."""
    material = json.dumps({
        'live': str(live_id or ''),
        'before': schedule_entries(before),
        'after': schedule_entries(after),
        'people': sorted({str(value or '').strip().lower() for value in people or () if str(value or '').strip()}),
        'title': ' '.join(str(title or '').split()),
        'extra': extra,
    }, sort_keys=True, default=str)
    return hashlib.sha256(material.encode('utf-8')).hexdigest()[:32]


def announcement_key(live_id, change_id):
    return f'{live_id}#ms#{change_id}'


def _recipient(part):
    return f"microsoft:{re.sub(r'[^a-z0-9-]', '', str(part or 'series').strip().lower()) or 'series'}"


def _ledger(ledger):
    if ledger is not None:
        return ledger
    from django.db import connection
    from .teams_schedule_delivery import DeliveryLedger
    return DeliveryLedger(connection)


def claim_announcement(live_id, change_id, part='series', ledger=None):
    """Claim the right to announce one change, durably, before Microsoft is asked.

    ``'claimed'``    nobody has announced it: this request may.
    ``'attempted'``  an earlier request already asked Microsoft to announce this
                     exact change. Do not ask again; the outcome of that write
                     cannot be proved from Graph, only reported.
    ``'unguarded'``  the ledger is not available (not provisioned, not
                     PostgreSQL). The caller announces as it always did and
                     says the retry guard was not available.
    """
    try:
        ledger = _ledger(ledger)
        ledger.check()
        key, recipient = announcement_key(live_id, change_id), _recipient(part)
        ledger.enqueue(key, [recipient])
        return 'claimed' if ledger.claim(key, recipient, True) else 'attempted'
    except Exception:
        logger.warning('The Teams announcement retry guard is not available; the update is announced unguarded.')
        return 'unguarded'


def finish_announcement(live_id, change_id, part='series', outcome='accepted', ledger=None):
    """Record what became of a claimed announcement.

    ``'failed'`` means Microsoft was never asked or refused outright, so a retry
    may claim the change again. ``'accepted'`` and ``'unknown'`` keep it claimed.
    """
    try:
        ledger = _ledger(ledger)
        ledger.finish(announcement_key(live_id, change_id), _recipient(part), outcome,
                      '' if outcome == 'accepted' else f'announcement_{outcome}')
    except Exception:
        # Left as 'sending': still claimed, so still never repeated.
        logger.warning('The Teams announcement outcome could not be recorded; the claim stays in place.')


def graph_failure_outcome(exc):
    """'failed' only when Microsoft definitely refused the write; otherwise 'unknown'."""
    match = _DEFINITE_REFUSAL.search(str(exc or ''))
    if match and match.group(1) != '408':
        return 'failed'
    return 'unknown'
