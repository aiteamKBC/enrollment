"""Programme-scoped cache generations.

Every curriculum write moves the one shared epoch, and every cached payload is
keyed on it, so a save to one module retired every programme's page in every
worker. The next look at any programme -- however unrelated -- paid the whole
rebuild (10s+ on the remote database) only to answer 304 with the body the
client already had.

A programme's own payloads (its detail document, the shell/modules sections,
ksb-stats) are keyed here instead, on two counters held in
``curriculum.cache_scope_epoch``:

* ``'*'``, moved by every write this module cannot attribute, and
* the programme's own row, moved by a write it can.

A write is attributed only when its request is on a short allowlist of
module/group/cohort saves, and only when every id it names resolves to rows
whose programmes are all known. The programmes are taken before and after the
view, so a module moved between programmes retires both, and from the whole
closure of modules linked to it by ``source_id`` or
``teams_shared_source_module_id`` in either direction, because a programme's
page reads those modules too. Anything else -- a programme, KSB source,
holiday, Teams or tree save, a write outside a request, a resolution that
fails or finds an unknown programme -- moves ``'*'``, which is exactly the old
global behaviour.

The table is created by the standalone script in
``backend/sql/2026-10-18_curriculum_cache_scope_epoch/``. Until it exists every
key falls back to the global epoch, so the code ships inert.
"""
from __future__ import annotations

import logging
import re
import threading
import time

from django.db import connection, transaction

logger = logging.getLogger(__name__)

SCOPE_TABLE = 'cache_scope_epoch'
UNSCOPED = '*'
# How many global bumps were made by code that also moves these counters. A
# writer that does not know this table -- an older deployment, a management
# command on another checkout -- moves the global epoch alone, which changes
# ``global - BUMPS`` and so retires every programme key at once, exactly as the
# global epoch always did. The two are moved in one transaction and read in
# one statement, so the difference never moves for a write that was scoped.
BUMPS = '#bumps'
# Not a row of the table: the global epoch, carried alongside in the same read.
GLOBAL = '#global'
# The same window the global epoch is polled on: bounds how long another
# worker may serve a programme's pre-write payload.
SCOPE_POLL_SECONDS = 3.0
# Absent a table the answer is re-asked this often, so applying the SQL takes
# effect without a restart.
TABLE_RECHECK_SECONDS = 60.0

_STATE = {'epochs': None, 'checked_at': 0.0, 'table': None, 'table_checked_at': 0.0}
_STATE_LOCK = threading.Lock()
_REFRESH_LOCK = threading.Lock()
_INDEX = {'epoch': None, 'ids': frozenset()}
_INDEX_LOCK = threading.Lock()

# Saves whose effect is confined to the module, group or cohort the path names
# (and the modules linked to it). Teams routes are deliberately absent: they
# keep the global invalidation they have always had.
_MODULE_WRITE = re.compile(
    r'^/curriculum/modules/(?P<id>[^/]+)/(?:structure/|settings/|components/[^/]+/|weeks/[^/]+/)?$'
)
_GROUP_WRITE = re.compile(r'^/curriculum/groups/(?P<id>[^/]+)/$')
_COHORT_WRITE = re.compile(r'^/curriculum/cohorts/(?P<id>[^/]+)/$')
_SCOPED_METHODS = frozenset({'PUT', 'PATCH', 'POST'})
_RESERVED_SEGMENTS = frozenset({'tutor-email', 'reorder', 'bulk', 'restore'})


def _views():
    from . import views
    return views


def scope_table_available():
    """Whether curriculum.cache_scope_epoch exists. Cached; re-asked when absent."""
    now = time.monotonic()
    with _STATE_LOCK:
        known = _STATE['table']
        checked_at = _STATE['table_checked_at']
    if known or (known is False and now - checked_at < TABLE_RECHECK_SECONDS):
        return bool(known)
    if connection.vendor != 'postgresql':
        found = False
    else:
        try:
            views = _views()
            rows = views.fetch_all(
                'select to_regclass(%s) is not null as present',
                [f'{views.CURRICULUM_SCHEMA}.{SCOPE_TABLE}'],
            )
            found = bool(rows and rows[0].get('present'))
        except Exception:
            logger.warning('Unable to check for the cache scope table.', exc_info=True)
            found = False
    with _STATE_LOCK:
        _STATE['table'] = found
        _STATE['table_checked_at'] = time.monotonic()
    return found


def _scoped_table():
    views = _views()
    return f'{views.quote_ident(views.CURRICULUM_SCHEMA)}.{views.quote_ident(SCOPE_TABLE)}'


def scope_epochs():
    """``{scope: epoch}`` as last polled, or None when scoping is unavailable."""
    if not scope_table_available():
        return None
    now = time.monotonic()
    with _STATE_LOCK:
        epochs, checked_at = _STATE['epochs'], _STATE['checked_at']
    if epochs is not None and now - checked_at < SCOPE_POLL_SECONDS:
        return epochs
    with _REFRESH_LOCK:
        with _STATE_LOCK:
            epochs, checked_at = _STATE['epochs'], _STATE['checked_at']
        if epochs is not None and time.monotonic() - checked_at < SCOPE_POLL_SECONDS:
            return epochs
        try:
            needs_savepoint = connection.in_atomic_block or not connection.get_autocommit()
            with (transaction.atomic() if needs_savepoint else _null()):
                views = _views()
                # One statement, one snapshot: the global epoch and BUMPS are
                # moved together, so they must be read together too.
                rows = views.fetch_all(
                    f'select scope, epoch from {_scoped_table()} '
                    f'union all select %s, epoch from {views.table_name(views.CURRICULUM_EPOCH_TABLE)} where id = 1',
                    [GLOBAL],
                )
            fresh = {str(row['scope']): int(row['epoch'] or 0) for row in rows}
            if GLOBAL not in fresh:
                return None
        except Exception:
            logger.warning('Unable to read the cache scope epochs.', exc_info=True)
            _forget_table()
            return None
        with _STATE_LOCK:
            # A local bump during the read already published newer values.
            if _STATE['checked_at'] != checked_at:
                return _STATE['epochs']
            _STATE['epochs'] = fresh
            _STATE['checked_at'] = time.monotonic()
        # Read in the same statement: this is the global poll too.
        _views().note_read_shared_curriculum_epoch(fresh[GLOBAL])
        return fresh


def _forget_table():
    """Ask again whether the table exists (it may have been rolled back)."""
    with _STATE_LOCK:
        _STATE.update({'epochs': None, 'checked_at': 0.0, 'table': None, 'table_checked_at': 0.0})


class _null:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def programme_ids():
    """Every programme id, refreshed whenever the global epoch moves."""
    views = _views()
    epoch = views.shared_curriculum_epoch()
    with _INDEX_LOCK:
        if _INDEX['epoch'] == epoch:
            return _INDEX['ids']
    rows = views.fetch_all(f'select programme_id from {views.table_name("programmes")}')
    ids = frozenset(views.clean_str(row.get('programme_id')) for row in rows if views.clean_str(row.get('programme_id')))
    with _INDEX_LOCK:
        _INDEX['epoch'] = epoch
        _INDEX['ids'] = ids
    return ids


def programme_generation(identifier):
    """The cache generation for a programme-keyed payload, or None for global.

    Only an identifier that is exactly a programme's id is scoped. A name,
    slug or legacy id can resolve to a different programme as the data moves,
    so it keeps the global epoch.
    """
    programme_id = _views().clean_str(identifier)
    if not programme_id:
        return None
    try:
        epochs = scope_epochs()
        if epochs is None or programme_id not in programme_ids():
            return None
    except Exception:
        logger.warning('Unable to resolve a programme cache scope.', exc_info=True)
        return None
    if GLOBAL not in epochs:
        return None
    foreign_bumps = epochs[GLOBAL] - epochs.get(BUMPS, 0)
    return ('scope', foreign_bumps, epochs.get(UNSCOPED, 0), programme_id, epochs.get(programme_id, 0))


# --- write side --------------------------------------------------------------

def write_seeds(method, path):
    """``{'modules': [...], 'groups': [...], 'cohorts': [...]}`` or None."""
    if method not in _SCOPED_METHODS:
        return None
    for pattern, kind in ((_MODULE_WRITE, 'modules'), (_GROUP_WRITE, 'groups'), (_COHORT_WRITE, 'cohorts')):
        match = pattern.match(path)
        if match:
            value = match.group('id')
            if not value or value in _RESERVED_SEGMENTS:
                return None
            return {'modules': [], 'groups': [], 'cohorts': [], kind: [value]}
    return None


_AFFECTED_SQL = '''
with recursive
seed_modules as (
    select module_catalogue_id from {modules}
    where module_catalogue_id = any(%(m)s) or group_id = any(%(g)s) or cohort_id = any(%(c)s)
),
linked(id) as (
    select module_catalogue_id from seed_modules
    union
    select ref.id
    from linked
    join {modules} m on m.module_catalogue_id = linked.id
        or m.source_id = linked.id
        or m.teams_shared_source_module_id = linked.id
    cross join lateral (values (m.module_catalogue_id), (m.source_id), (m.teams_shared_source_module_id)) as ref(id)
    where coalesce(ref.id, '') <> ''
),
module_rows as (
    select programme_id, programme_name, group_id, cohort_id from {modules}
    where module_catalogue_id in (select id from linked)
),
group_rows as (
    select programme_id, programme_name, cohort_id from {groups}
    where group_id = any(%(g)s) or cohort_id = any(%(c)s)
        or group_id in (select group_id from module_rows)
),
cohort_rows as (
    select programme_id, programme_name from {cohorts}
    where cohort_id = any(%(c)s)
        or cohort_id in (select cohort_id from module_rows)
        or cohort_id in (select cohort_id from group_rows)
)
select
    (select count(*) from {modules} where module_catalogue_id = any(%(m)s)) as found_modules,
    (select count(*) from {groups} where group_id = any(%(g)s)) as found_groups,
    (select count(*) from {cohorts} where cohort_id = any(%(c)s)) as found_cohorts,
    (select count(*) from linked) as linked_modules,
    coalesce((select jsonb_agg(jsonb_build_array(programme_id, programme_name)) from (
        select programme_id, programme_name from module_rows
        union select programme_id, programme_name from group_rows
        union select programme_id, programme_name from cohort_rows
    ) refs), '[]'::jsonb) as refs,
    coalesce((select jsonb_agg(jsonb_build_array(programme_id, name)) from {programmes}), '[]'::jsonb) as programmes
'''

# A closure this wide is no longer "this programme's change"; treat it as global.
MAX_LINKED_MODULES = 500


def affected_programmes(seeds):
    """The programme ids a write to ``seeds`` can change, or None if unsure."""
    import json

    if not seeds or connection.vendor != 'postgresql' or not scope_table_available():
        return None
    views = _views()
    tables = {
        'modules': views.authoring_table_name(views.AUTHORING_MODULES_TABLE),
        'groups': views.authoring_table_name(views.GROUPS_TABLE),
        'cohorts': views.authoring_table_name(views.COHORT_AUTHORING_DETAILS_TABLE),
        'programmes': views.table_name('programmes'),
    }
    params = {'m': list(seeds['modules']), 'g': list(seeds['groups']), 'c': list(seeds['cohorts'])}
    try:
        needs_savepoint = connection.in_atomic_block or not connection.get_autocommit()
        with (transaction.atomic() if needs_savepoint else _null()), connection.cursor() as cursor:
            cursor.execute(_AFFECTED_SQL.format(**tables), params)
            columns = [column[0] for column in cursor.description]
            row = dict(zip(columns, cursor.fetchone()))
    except Exception:
        logger.warning('Unable to resolve the programmes a curriculum write affects.', exc_info=True)
        return None
    if (
        row['found_modules'] != len(set(params['m']))
        or row['found_groups'] != len(set(params['g']))
        or row['found_cohorts'] != len(set(params['c']))
        or row['linked_modules'] > MAX_LINKED_MODULES
    ):
        return None
    load = lambda value: json.loads(value) if isinstance(value, str) else (value or [])  # noqa: E731
    programmes = load(row['programmes'])
    known_ids = {views.clean_str(pid) for pid, _name in programmes if views.clean_str(pid)}
    ids_by_name = {}
    for pid, name in programmes:
        if views.clean_str(pid) and views.normalise(name or ''):
            ids_by_name.setdefault(views.normalise(name), set()).add(views.clean_str(pid))
    affected = set()
    for pid, name in load(row['refs']):
        pid = views.clean_str(pid)
        if pid:
            if pid not in known_ids:
                return None
            affected.add(pid)
        # Rows are attributed by name when they carry no id, and a stale name
        # can match another live programme: retire that one too.
        affected.update(ids_by_name.get(views.normalise(name or ''), ()))
    return frozenset(affected) or None


def bump_scopes(scopes):
    """Move each programme's counter, or the unscoped one.

    Runs inside the caller's global-bump transaction and returns the moved
    ``[{'scope', 'epoch'}]`` rows, or None when nothing was moved. It never
    publishes them: until that transaction commits they are not the database's
    values, and a worker that built under them would key a pre-write payload
    on a generation the next real save hands out again. The caller passes them
    to remember_scope_epochs() once the commit has succeeded.
    """
    if connection.vendor != 'postgresql' or not scope_table_available():
        return None
    names = [BUMPS, *(sorted(scopes) if scopes else [UNSCOPED])]
    try:
        with transaction.atomic():
            return _views().fetch_all(
                f'insert into {_scoped_table()} as t (scope, epoch) '
                f'select unnest(%s::text[]), 1 '
                f'on conflict (scope) do update set epoch = t.epoch + 1 '
                f'returning scope, epoch',
                [names],
            )
    except Exception:
        logger.warning('Unable to bump the cache scope epochs; moving the unscoped one.', exc_info=True)
        if scopes:
            return bump_scopes(None)
        # Nothing could be moved. Stop trusting the table until it is found
        # again, so every programme falls back to the global epoch that this
        # write did move (global - BUMPS shifts, which retires them all).
        _forget_table()
        return None


def remember_scope_epochs(rows, global_epoch):
    """Publish a committed bump to this worker, so its own save shows at once."""
    with _STATE_LOCK:
        if _STATE['epochs'] is None:
            return
        if not rows or global_epoch is None:
            # Without both values the difference cannot be updated here;
            # read them again on the next lookup.
            _STATE['epochs'] = None
            _STATE['checked_at'] = 0.0
            return
        merged = dict(_STATE['epochs'])
        merged.update({str(row['scope']): int(row['epoch']) for row in rows})
        merged[GLOBAL] = int(global_epoch)
        _STATE['epochs'] = merged
        _STATE['checked_at'] = time.monotonic()


def forget_epochs():
    """Drop the polled epochs so the next lookup reads the database again."""
    with _STATE_LOCK:
        _STATE['epochs'] = None
        _STATE['checked_at'] = 0.0


def reset_state():
    """For tests."""
    with _STATE_LOCK:
        _STATE.update({'epochs': None, 'checked_at': 0.0, 'table': None, 'table_checked_at': 0.0})
    with _INDEX_LOCK:
        _INDEX.update({'epoch': None, 'ids': frozenset()})
