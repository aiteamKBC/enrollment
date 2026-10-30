"""Keeping England's bank holidays current, and saying what moved.

``curriculum.england_holidays`` is a mirror of the feed GOV.UK publishes at
https://www.gov.uk/bank-holidays.json. A mirror is only worth having if it is
current: every cohort takes the bank holidays inside its own dates, and every
module session plan steps over them, so a holiday GOV.UK moves and we do not is
a session planned onto a day the country is closed.

This module is the one place that check lives. Three callers share it:

``run_sync(source='command')``
    ``python manage.py fetch_england_holidays``, run by hand or from cron.
``run_sync(source='manual')``
    The "Check GOV.UK now" button on the England Holidays page.
``auto_sync_if_due()``
    The background refresh. Any read of the holiday calendar asks whether a
    check is due, and if it is, one runs on a thread behind the response. That
    is what makes the mirror self-maintaining on a deployment with no cron and
    no worker: the first page load after the interval expires does the check.

Every check is recorded in ``curriculum.england_holiday_syncs``, including the
ones that found nothing and the ones that could not reach GOV.UK. That ledger is
the answer to "which holidays changed on the website?" -- the question the page
puts in front of a curriculum designer, who otherwise has no way to know that a
date under their cohorts moved.

What counts as a change
-----------------------
The feed is keyed by date within a division, and so is the table. So:

* a date the feed carries and we do not is **added**;
* a date we both carry whose title, note or bunting flag differs is **changed**;
* a date we carry that the feed no longer lists, *inside the span the feed
  covers*, is **withdrawn** -- GOV.UK took it back, and it is deleted here too,
  because a holiday that is not a holiday any more must stop closing sessions;
* a date we carry from *before* the feed's span has simply aged out of GOV.UK's
  rolling ten-year window. It is kept: it is still the date a past cohort was
  scheduled around, and forgetting it would silently rewrite that history.

A holiday that *moves* -- Boxing Day sliding to the 28th because the 26th fell on
a Saturday -- arrives as a withdrawal and an addition, since the date is the key.
``pair_moves`` puts those two back together so the report can say "Boxing Day:
26 Dec -> 28 Dec" instead of listing an unexplained removal next to an
unexplained arrival.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta

import requests
from django.db import connection, connections, transaction

from . import views

logger = logging.getLogger(__name__)

#: The feed behind https://www.gov.uk/bank-holidays.
BANK_HOLIDAYS_URL = 'https://www.gov.uk/bank-holidays.json'
#: England and Wales share a single set of bank holidays; this is their key in
#: the feed, and the value stored in the table's ``division`` column.
DIVISION = 'england-and-wales'

HOLIDAYS_TABLE = 'england_holidays'
SYNCS_TABLE = 'england_holiday_syncs'

FEED_TIMEOUT_SECONDS = float(os.environ.get('ENGLAND_HOLIDAY_FEED_TIMEOUT', '30'))

#: The background refresh, off only where a background thread reading the
#: database is a problem -- the test runner sets this to 0, exactly as it does
#: for cache warming.
AUTO_SYNC_ENABLED = os.environ.get('ENGLAND_HOLIDAY_AUTO_SYNC', 'true').lower() not in {'false', '0', 'no'}
#: How long a successful check stays good for. GOV.UK changes this feed a
#: handful of times a decade, so a day is already far more often than the data
#: moves; the point of the interval is to bound how long a change can sit
#: unnoticed, not to keep up with a fast source.
AUTO_SYNC_INTERVAL_HOURS = float(os.environ.get('ENGLAND_HOLIDAY_SYNC_INTERVAL_HOURS', '24'))
#: After a failed check, how long before trying again. Short enough that a
#: half-hour GOV.UK outage does not cost a day of currency, long enough that a
#: sustained one is not retried on every page load.
AUTO_SYNC_RETRY_MINUTES = float(os.environ.get('ENGLAND_HOLIDAY_SYNC_RETRY_MINUTES', '30'))

_AUTO_SYNC_LOCK = threading.Lock()
_AUTO_SYNC_RUNNING = False
#: The earliest moment the next check is worth *asking the database* about. An
#: in-process short circuit so the common case -- a page load minutes after the
#: last check -- costs nothing at all, not even the one-row read below.
_AUTO_SYNC_NEXT_PROBE = None


class FeedUnavailable(RuntimeError):
    """GOV.UK could not be read, or answered with something unusable."""


# ---------------------------------------------------------------------------
# Reading the feed
# ---------------------------------------------------------------------------

def read_feed(url=BANK_HOLIDAYS_URL, timeout=FEED_TIMEOUT_SECONDS):
    """The England and Wales bank holidays GOV.UK is publishing right now.

    Returns ``{'YYYY-MM-DD': {...}}`` -- keyed by date, because that is the key
    the feed itself is unique on and the key the table is unique on.

    Raises ``FeedUnavailable`` rather than returning an empty result for any
    failure. An empty answer here would look exactly like "GOV.UK has abolished
    every bank holiday", and the withdrawal rule below would act on it.
    """
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise FeedUnavailable(f'Could not read {url}: {exc}') from exc

    events = (payload.get(DIVISION) or {}).get('events') or []
    if not events:
        raise FeedUnavailable(f'{url} returned no {DIVISION} holidays.')

    incoming = {}
    for event in events:
        holiday_date = views.format_date(event.get('date'))
        title = views.clean_str(event.get('title'))
        if not holiday_date or not title:
            continue
        incoming[holiday_date] = {
            'id': f'{DIVISION}:{holiday_date}',
            'title': title,
            'date': holiday_date,
            'notes': views.clean_str(event.get('notes')),
            'bunting': bool(event.get('bunting')),
        }

    if not incoming:
        raise FeedUnavailable(
            f'{url} returned {len(events)} {DIVISION} events, none of them with '
            'both a date and a title.'
        )
    return incoming


def stored_holidays():
    """What the mirror currently holds, keyed by date like the feed."""
    rows = views.fetch_all(
        f'select * from {views.table_name(HOLIDAYS_TABLE)} where division = %s',
        [DIVISION],
    )
    return {views.format_date(row.get('holiday_date')): row for row in rows}


# ---------------------------------------------------------------------------
# Working out what changed
# ---------------------------------------------------------------------------

def plan_changes(incoming, stored):
    """The four buckets: added, changed, withdrawn, aged out.

    ``stored`` is keyed by date, as ``stored_holidays`` returns it. Nothing is
    written here -- this is the part the dry run also runs.
    """
    added = []
    changed = []
    for holiday_date, item in sorted(incoming.items()):
        row = stored.get(holiday_date)
        if not row:
            added.append(item)
            continue
        was = {
            'title': views.clean_str(row.get('title')),
            'notes': views.clean_str(row.get('notes')),
            'bunting': bool(row.get('bunting')),
        }
        if (was['title'], was['notes'], was['bunting']) != (item['title'], item['notes'], item['bunting']):
            changed.append({**item, 'previous': was})

    # The span GOV.UK is currently publishing. A date we hold inside it that the
    # feed does not list has been withdrawn; one before it has merely aged out of
    # the rolling window. There is no "after": the feed always reaches further
    # forward than anything we could already be holding from it.
    feed_start = min(incoming)
    withdrawn = []
    aged_out = []
    for holiday_date, row in sorted(stored.items()):
        if not holiday_date or holiday_date in incoming:
            continue
        item = {
            'id': views.clean_str(row.get('id')),
            'title': views.clean_str(row.get('title')),
            'date': holiday_date,
            'notes': views.clean_str(row.get('notes')),
            'bunting': bool(row.get('bunting')),
        }
        (aged_out if holiday_date < feed_start else withdrawn).append(item)

    return added, changed, withdrawn, aged_out


def pair_moves(added, withdrawn):
    """Rejoin a holiday that changed date into a single reported move.

    The feed is keyed by date, so a holiday GOV.UK reschedules leaves as one
    withdrawal and arrives as one addition. Reporting them apart is accurate and
    useless: "Boxing Day removed, Boxing Day added" is one fact, and the fact is
    the new date.

    Matched on the same title in the same year, which is as far as the data
    allows -- the feed carries no identifier beyond the date. Anything unmatched
    stays in its own bucket, so the totals never change.
    """
    remaining = list(withdrawn)
    moves = []
    still_added = []
    for item in added:
        match = next(
            (
                gone for gone in remaining
                if gone['title'].casefold() == item['title'].casefold()
                and gone['date'][:4] == item['date'][:4]
            ),
            None,
        )
        if match is None:
            still_added.append(item)
            continue
        remaining.remove(match)
        moves.append({**item, 'previousDate': match['date'], 'previousId': match['id']})
    return moves, still_added, remaining


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def apply_changes(incoming, added, changed, removed, now=None):
    """Write the plan. One transaction, so the mirror is never half-updated.

    ``removed`` is every date that must no longer be in the mirror: the holidays
    GOV.UK withdrew, *and* the old date of every holiday it moved -- a move is an
    insert of the new date and a delete of the old one, because the date is the
    key. Leaving the old one behind would close a delivery day twice over.
    """
    now = now or datetime.utcnow()
    table = views.table_name(HOLIDAYS_TABLE)
    with transaction.atomic():
        with connection.cursor() as cursor:
            for item in added:
                cursor.execute(
                    f'''insert into {table}
                            (id, division, title, holiday_date, notes, bunting,
                             fetched_at, created_at, updated_at)
                        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)''',
                    [
                        item['id'], DIVISION, item['title'], item['date'],
                        item['notes'], item['bunting'], now, now, now,
                    ],
                )
            for item in changed:
                cursor.execute(
                    f'''update {table}
                           set title = %s, notes = %s, bunting = %s,
                               fetched_at = %s, updated_at = %s
                         where division = %s and holiday_date = %s''',
                    [
                        item['title'], item['notes'], item['bunting'], now, now,
                        DIVISION, item['date'],
                    ],
                )
            for item in removed:
                # GOV.UK has taken this date back. Keeping it would go on closing
                # a delivery day the country is open on, which is the one failure
                # this whole mechanism exists to prevent.
                cursor.execute(
                    f'delete from {table} where division = %s and holiday_date = %s',
                    [DIVISION, item['date']],
                )
            # Every date the feed still carries was confirmed today, whether or
            # not anything about it moved -- that is what fetched_at records, and
            # it is what lets the page say when the mirror was last known good.
            dates = sorted(incoming)
            placeholders = ', '.join(['%s'] * len(dates))
            cursor.execute(
                f'''update {table} set fetched_at = %s
                     where division = %s and holiday_date in ({placeholders})''',
                [now, DIVISION, *dates],
            )


# ---------------------------------------------------------------------------
# The sync itself
# ---------------------------------------------------------------------------

def run_sync(source='auto', apply=True, url=BANK_HOLIDAYS_URL):
    """Check GOV.UK, optionally write what moved, and record the check.

    Returns the summary the API and the command both report from. It never
    raises for an unreachable feed: that is a result -- ``status: 'error'`` --
    and it is recorded, because "we have not reached GOV.UK since Tuesday" is
    precisely the thing the page has to be able to say.
    """
    checked_at = datetime.utcnow()
    summary = {
        'status': 'ok',
        'source': views.clean_str(source) or 'auto',
        'checkedAt': checked_at.isoformat(),
        'applied': False,
        'feedCount': 0,
        'added': [],
        'changed': [],
        'moved': [],
        'withdrawn': [],
        'agedOut': [],
        'unchanged': 0,
        'message': '',
    }

    if not views.table_exists(HOLIDAYS_TABLE):
        summary['status'] = 'error'
        summary['message'] = (
            f'The {HOLIDAYS_TABLE} table does not exist. Apply '
            'sql/2026-09-13_curriculum_england_holidays.sql first.'
        )
        record_sync(summary)
        return summary

    try:
        incoming = read_feed(url)
    except FeedUnavailable as exc:
        summary['status'] = 'error'
        summary['message'] = str(exc)
        record_sync(summary)
        return summary

    try:
        stored = stored_holidays()
    except Exception as exc:  # pragma: no cover - a broken table is not a code path
        summary['status'] = 'error'
        summary['message'] = f'Could not read the {HOLIDAYS_TABLE} table: {exc}'
        record_sync(summary)
        return summary

    added, changed, withdrawn, aged_out = plan_changes(incoming, stored)
    moved, added, withdrawn = pair_moves(added, withdrawn)

    summary.update({
        'feedCount': len(incoming),
        'added': added,
        'changed': changed,
        'moved': moved,
        'withdrawn': withdrawn,
        'agedOut': aged_out,
        'unchanged': len(incoming) - len(added) - len(changed) - len(moved),
        'feedStart': min(incoming),
        'feedEnd': max(incoming),
    })

    if not apply:
        summary['message'] = 'Dry run: nothing was written.'
        return summary

    apply_changes(
        incoming,
        added=[*added, *moved],
        changed=changed,
        # A moved holiday leaves its old date behind unless it is removed here.
        removed=[*withdrawn, *({'date': item['previousDate']} for item in moved)],
        now=checked_at,
    )
    summary['applied'] = True

    if change_count(summary):
        # These are the dates every cohort and module schedule is built from, and
        # the curriculum payload holding them is cached. Without this a feed that
        # moved a holiday does not reach a single screen until the TTL expires.
        views.invalidate_curriculum_cache()

    record_sync(summary)
    return summary


def change_count(summary):
    """How many holidays this check actually moved, in any direction."""
    return sum(len(summary.get(key) or []) for key in ('added', 'changed', 'moved', 'withdrawn'))


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------

def record_sync(summary):
    """Record the check. Best effort: a sync is not lost for want of a log.

    The ledger table is provisioned by
    sql/2026-09-14_curriculum_england_holiday_syncs.sql. An environment that has
    not applied it yet still syncs -- it just cannot yet say what changed.
    """
    if not views.table_exists(SYNCS_TABLE):
        return False
    changes = {
        'added': summary.get('added') or [],
        'changed': summary.get('changed') or [],
        'moved': summary.get('moved') or [],
        'withdrawn': summary.get('withdrawn') or [],
        'agedOut': summary.get('agedOut') or [],
    }
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f'''insert into {views.table_name(SYNCS_TABLE)}
                        (id, checked_at, source, status, feed_count,
                         added_count, changed_count, added, changed, message)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''',
                [
                    uuid.uuid4().hex,
                    summary.get('checkedAt') or datetime.utcnow().isoformat(),
                    summary.get('source') or 'auto',
                    summary.get('status') or 'ok',
                    summary.get('feedCount') or 0,
                    len(changes['added']),
                    # One "changed" count covering everything that was not a
                    # plain addition, so a reader of the table alone still sees
                    # that something moved.
                    len(changes['changed']) + len(changes['moved']) + len(changes['withdrawn']),
                    json.dumps(changes['added']),
                    json.dumps({
                        'changed': changes['changed'],
                        'moved': changes['moved'],
                        'withdrawn': changes['withdrawn'],
                        'agedOut': changes['agedOut'],
                    }),
                    views.clean_str(summary.get('message')),
                ],
            )
        return True
    except Exception:
        logger.warning('Could not record the GOV.UK bank holiday check.', exc_info=True)
        return False


def serialize_sync_row(row):
    """One recorded check, in the shape the England Holidays page reads."""
    detail = views.parse_json_value(row.get('changed'), {}) or {}
    if isinstance(detail, list):
        # Rows written before the detail column carried the four buckets.
        detail = {'changed': detail}
    return {
        'id': views.clean_str(row.get('id')),
        'checkedAt': views.format_created_at(row.get('checked_at')),
        'source': views.clean_str(row.get('source')),
        'status': views.clean_str(row.get('status')) or 'ok',
        'feedCount': views.parse_int(row.get('feed_count')),
        'added': views.parse_json_value(row.get('added'), []) or [],
        'changed': detail.get('changed') or [],
        'moved': detail.get('moved') or [],
        'withdrawn': detail.get('withdrawn') or [],
        'agedOut': detail.get('agedOut') or [],
        'message': views.clean_str(row.get('message')),
    }


def recent_syncs(limit=20):
    """The most recent checks, newest first."""
    if not views.table_exists(SYNCS_TABLE):
        return []
    try:
        rows = views.fetch_all(
            f'''select * from {views.table_name(SYNCS_TABLE)}
                 order by checked_at desc
                 limit %s''',
            [max(1, int(limit))],
        )
    except Exception:
        logger.warning('Could not read the GOV.UK bank holiday check log.', exc_info=True)
        return []
    return [serialize_sync_row(row) for row in rows]


def last_successful_check():
    """When GOV.UK was last read successfully, or ``None``.

    Falls back to the newest ``fetched_at`` on the holidays themselves, so an
    environment that has the mirror but not the ledger yet -- or one seeded
    straight from the SQL file -- is not treated as never checked.
    """
    if views.table_exists(SYNCS_TABLE):
        rows = views.fetch_all(
            f'''select max(checked_at) as checked_at
                  from {views.table_name(SYNCS_TABLE)}
                 where status = %s''',
            ['ok'],
        )
        stamp = as_datetime(rows[0].get('checked_at') if rows else None)
        if stamp:
            return stamp
    if not views.table_exists(HOLIDAYS_TABLE):
        return None
    rows = views.fetch_all(
        f'select max(fetched_at) as fetched_at from {views.table_name(HOLIDAYS_TABLE)}'
    )
    return as_datetime(rows[0].get('fetched_at') if rows else None)


def last_check():
    """When GOV.UK was last *attempted*, successfully or not."""
    if not views.table_exists(SYNCS_TABLE):
        return last_successful_check()
    rows = views.fetch_all(
        f'select max(checked_at) as checked_at from {views.table_name(SYNCS_TABLE)}'
    )
    return as_datetime(rows[0].get('checked_at') if rows else None) or last_successful_check()


def as_datetime(value):
    """A stored timestamp as a naive UTC datetime, or ``None``.

    SQLite hands back text and PostgreSQL a tz-aware datetime; the interval
    arithmetic here needs one kind, and everything written by this module is UTC.
    """
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value
        return value.astimezone(tz=None).replace(tzinfo=None)
    text = views.clean_str(value).replace('Z', '').replace('T', ' ')
    for pattern in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(text[:26], pattern)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# The background refresh
# ---------------------------------------------------------------------------

def sync_status():
    """What the page needs to describe how current the mirror is."""
    last_ok = last_successful_check()
    attempted = last_check()
    due_at = last_ok + timedelta(hours=AUTO_SYNC_INTERVAL_HOURS) if last_ok else None
    return {
        'autoSync': AUTO_SYNC_ENABLED,
        'intervalHours': AUTO_SYNC_INTERVAL_HOURS,
        'lastCheckedAt': attempted.isoformat() if attempted else '',
        'lastSuccessAt': last_ok.isoformat() if last_ok else '',
        'nextCheckDueAt': due_at.isoformat() if due_at else '',
        'source': BANK_HOLIDAYS_URL,
    }


def auto_sync_if_due():
    """Run the check on a background thread if one is due. Never blocks a read.

    Called from the holiday read paths, which is what makes the mirror keep
    itself current without cron or a worker: whoever opens a curriculum page
    first after the interval expires pays nothing (the check runs behind their
    response) and everyone after them sees the refreshed dates.

    Returns whether a check was started, so tests and callers can tell the three
    cases -- disabled, not due, started -- apart.
    """
    global _AUTO_SYNC_RUNNING, _AUTO_SYNC_NEXT_PROBE

    if not AUTO_SYNC_ENABLED:
        return False

    now = datetime.utcnow()
    with _AUTO_SYNC_LOCK:
        if _AUTO_SYNC_RUNNING:
            return False
        if _AUTO_SYNC_NEXT_PROBE and now < _AUTO_SYNC_NEXT_PROBE:
            return False
        # Hold the probe off for a minute regardless of what the database says
        # below, so a burst of concurrent reads asks once rather than once each.
        _AUTO_SYNC_NEXT_PROBE = now + timedelta(minutes=1)

    try:
        last_ok = last_successful_check()
    except Exception:
        logger.debug('Could not tell when the bank holidays were last checked.', exc_info=True)
        return False

    if last_ok and now - last_ok < timedelta(hours=AUTO_SYNC_INTERVAL_HOURS):
        with _AUTO_SYNC_LOCK:
            # Nothing to do until the interval is up; stop asking until then.
            _AUTO_SYNC_NEXT_PROBE = last_ok + timedelta(hours=AUTO_SYNC_INTERVAL_HOURS)
        return False

    with _AUTO_SYNC_LOCK:
        if _AUTO_SYNC_RUNNING:
            return False
        _AUTO_SYNC_RUNNING = True

    thread = threading.Thread(target=_auto_sync_worker, name='england-holiday-sync', daemon=True)
    thread.start()
    return True


def _auto_sync_worker():
    global _AUTO_SYNC_RUNNING, _AUTO_SYNC_NEXT_PROBE
    try:
        summary = run_sync(source='auto')
        if summary.get('status') == 'ok':
            logger.info(
                'Bank holidays checked against GOV.UK: %s added, %s changed, %s moved, %s withdrawn.',
                len(summary.get('added') or []), len(summary.get('changed') or []),
                len(summary.get('moved') or []), len(summary.get('withdrawn') or []),
            )
            wait = timedelta(hours=AUTO_SYNC_INTERVAL_HOURS)
        else:
            logger.warning('Bank holiday check failed: %s', summary.get('message'))
            wait = timedelta(minutes=AUTO_SYNC_RETRY_MINUTES)
    except Exception:
        # A refresh that fails must never take the process with it. The dates
        # already stored stay exactly as they were.
        logger.warning('Bank holiday check failed.', exc_info=True)
        wait = timedelta(minutes=AUTO_SYNC_RETRY_MINUTES)
    finally:
        # This thread opened its own connection; a check runs once a day, so
        # holding one between runs is pure cost on a pooled database.
        connections.close_all()
        with _AUTO_SYNC_LOCK:
            _AUTO_SYNC_RUNNING = False
            _AUTO_SYNC_NEXT_PROBE = datetime.utcnow() + wait


def reset_auto_sync_state():
    """Forget the in-process schedule. For tests, and after a manual check.

    A manual check has just made the mirror current, so the next automatic one
    is a full interval away; clearing the latch lets the next read recompute
    that from the ledger rather than from a stale in-memory timestamp.
    """
    global _AUTO_SYNC_RUNNING, _AUTO_SYNC_NEXT_PROBE
    with _AUTO_SYNC_LOCK:
        _AUTO_SYNC_RUNNING = False
        _AUTO_SYNC_NEXT_PROBE = None


def provision_sync_table():
    """Mirror of sql/2026-09-14_curriculum_england_holiday_syncs.sql for SQLite.

    Schema is migration-owned in production; this exists so the test runner and
    local development have the ledger without running the SQL file by hand.
    """
    with connection.cursor() as cursor:
        if connection.vendor == 'postgresql':
            cursor.execute(f'create schema if not exists {views.quote_ident(views.CURRICULUM_SCHEMA)}')
        cursor.execute(f'''
            create table if not exists {views.table_name(SYNCS_TABLE)} (
                id varchar(64) primary key,
                checked_at timestamp not null default current_timestamp,
                source varchar(32) not null default 'auto',
                status varchar(32) not null default 'ok',
                feed_count integer not null default 0,
                added_count integer not null default 0,
                changed_count integer not null default 0,
                added text not null default '[]',
                changed text not null default '[]',
                message text not null default ''
            )
        ''')
