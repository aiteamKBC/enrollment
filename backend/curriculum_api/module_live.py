"""Module Builder live sync: who wrote which module, and what changed.

Two pieces, both answerable without rebuilding anything for a reader who has
nothing to learn:

1. **A counter per module.** Every write that reaches a module's rows -- the
   module itself, its weeks, components, KSB mappings, completion and details
   rows, its Teams share and its live-session series -- notes ``module:<id>``
   (and ``group:<id>`` for the group rows that feed its schedule). When the
   request finishes the noted counters move by one in
   ``curriculum.cache_scope_epoch``, the table programme scoping already uses,
   so no schema change is needed. An open Module Builder polls only its own
   module's counter; the global epoch, which every write in the estate moves,
   is used only as a slow backstop for writers this hook cannot see.

2. **A changes feed.** When the counter moves the editor asks what changed,
   sending the fingerprints of the copy it holds. The server builds the module
   once, fingerprints every week and component the same way, and returns only
   those that differ -- kilobytes instead of the whole structure.

Live sync is **off unless ``CURRICULUM_LIVE_SYNC=true``**. Off, the watch
endpoint answers ``{"enabled": false}`` and the editor does not poll; the feed
still works for the focus-return refresh, which only replaces a full read.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time

from django.db import connection, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from .module_merge import structure_fingerprints, week_fields

logger = logging.getLogger(__name__)


def live_sync_enabled():
    return os.environ.get('CURRICULUM_LIVE_SYNC', 'false').strip().lower() == 'true'


def live_poll_ms():
    try:
        return max(1000, int(os.environ.get('CURRICULUM_LIVE_POLL_MS', '2000')))
    except ValueError:
        return 2000


# One read per scope per worker per this many seconds, however many tabs ask.
WATCH_MEMO_SECONDS = 1.0
MAX_WATCHED_SCOPES = 8
_SCOPE_NAME = re.compile(r'^(module|group):[A-Za-z0-9_.:-]{1,120}$')

_PENDING = threading.local()
_MEMO = {}
_MEMO_LOCK = threading.Lock()
_REFRESH_LOCK = threading.Lock()


def _views():
    from . import views
    return views


# --- write side -------------------------------------------------------------

def _module_tables():
    views = _views()
    return {
        views.AUTHORING_MODULES_TABLE,
        views.AUTHORING_WEEKS_TABLE,
        views.AUTHORING_COMPONENTS_TABLE,
        views.AUTHORING_KSB_MAPPINGS_TABLE,
        views.AUTHORING_COMPLETION_TABLE,
        views.AUTHORING_ADVANCED_TABLE,
        views.MODULE_TEAMS_SHARES_TABLE,
        views.LIVE_SESSIONS_TABLE,
    }


def scopes_for_rows(table, rows):
    """The ``module:``/``group:`` scopes a write to these rows can change."""
    names = set()
    bare = str(table or '').split('.')[-1].strip('"')
    try:
        module_tables = _module_tables()
        groups_table = _views().GROUPS_TABLE
    except Exception:
        return names
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if bare in module_tables:
            module_id = str(row.get('module_catalogue_id') or '').strip()
            if module_id:
                names.add(f'module:{module_id}')
            if bare == _views().AUTHORING_MODULES_TABLE:
                group_id = str(row.get('group_id') or '').strip()
                if group_id:
                    names.add(f'group:{group_id}')
        elif bare == groups_table:
            group_id = str(row.get('group_id') or '').strip()
            if group_id:
                names.add(f'group:{group_id}')
    return names


def begin_request():
    _PENDING.names = set()
    _PENDING.active = True


def end_request():
    names = getattr(_PENDING, 'names', None) or set()
    _PENDING.names = set()
    _PENDING.active = False
    if names:
        bump_scopes(names)


def note_rows(table, rows):
    """Record that these rows were written. Never fails the write."""
    try:
        names = scopes_for_rows(table, rows)
        if not names:
            return
        if getattr(_PENDING, 'active', False):
            _PENDING.names.update(names)
            return
        # Outside a request (a management command, a worker thread): there is
        # no end-of-request flush, so move the counters once the data commits.
        transaction.on_commit(lambda: bump_scopes(names))
    except Exception:
        logger.debug('Unable to note a module write for live sync.', exc_info=True)


def bump_scopes(names):
    """Move each named counter by one. Never the programme rows or ``#bumps``."""
    names = sorted({name for name in names if _SCOPE_NAME.match(name)})
    if not names or connection.vendor != 'postgresql':
        return
    from .cache_scope import scope_table_available, _scoped_table
    if not scope_table_available():
        return
    try:
        with transaction.atomic():
            rows = _views().fetch_all(
                f'insert into {_scoped_table()} as t (scope, epoch) '
                f'select unnest(%s::text[]), 1 '
                f'on conflict (scope) do update set epoch = t.epoch + 1 '
                f'returning scope, epoch',
                [names],
            )
    except Exception:
        logger.warning('Unable to bump module live-sync counters.', exc_info=True)
        return
    now = time.monotonic()
    with _MEMO_LOCK:
        for row in rows or []:
            _MEMO[str(row['scope'])] = (int(row['epoch'] or 0), now)


# --- read side --------------------------------------------------------------

def watched_epochs(names):
    """``{scope: epoch}`` for the named scopes; 0 for a scope never written."""
    now = time.monotonic()
    with _MEMO_LOCK:
        fresh = {name: _MEMO[name][0] for name in names if name in _MEMO and now - _MEMO[name][1] < WATCH_MEMO_SECONDS}
    stale = [name for name in names if name not in fresh]
    if not stale:
        return fresh
    with _REFRESH_LOCK:
        with _MEMO_LOCK:
            for name in list(stale):
                entry = _MEMO.get(name)
                if entry and time.monotonic() - entry[1] < WATCH_MEMO_SECONDS:
                    fresh[name] = entry[0]
                    stale.remove(name)
        if stale:
            from .cache_scope import _scoped_table
            needs_savepoint = connection.in_atomic_block or not connection.get_autocommit()
            guard = transaction.atomic() if needs_savepoint else _Null()
            with guard:
                rows = _views().fetch_all(
                    f'select scope, epoch from {_scoped_table()} where scope = any(%s)', [stale],
                )
            read = {str(row['scope']): int(row['epoch'] or 0) for row in rows}
            stamp = time.monotonic()
            with _MEMO_LOCK:
                for name in stale:
                    value = read.get(name, 0)
                    _MEMO[name] = (value, stamp)
                    fresh[name] = value
    return fresh


class _Null:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def reset_state():
    """For tests."""
    with _MEMO_LOCK:
        _MEMO.clear()
    _PENDING.names = set()
    _PENDING.active = False


@require_GET
def curriculum_live(request):
    """Counters for the scopes an open editor is watching.

    ``?watch=module:MOD-1&watch=group:GROUP-2`` answers
    ``{"enabled": true, "epochs": {...}, "epoch": <global>, "pollMs": 2000}``.
    Staff-only through the API gate like the rest of the curriculum console.
    """
    if not live_sync_enabled():
        return JsonResponse({'enabled': False})
    from .cache_scope import scope_table_available
    if connection.vendor != 'postgresql' or not scope_table_available():
        return JsonResponse({'enabled': False, 'reason': 'unavailable'})
    names = []
    for value in request.GET.getlist('watch'):
        value = str(value or '').strip()
        if _SCOPE_NAME.match(value) and value not in names:
            names.append(value)
    if not names or len(names) > MAX_WATCHED_SCOPES:
        return JsonResponse({'error': 'Name between one and eight module: or group: scopes to watch.'}, status=400)
    try:
        epochs = watched_epochs(names)
        epoch = _views().shared_curriculum_epoch()
    except Exception:
        logger.warning('Unable to read module live-sync counters.', exc_info=True)
        return JsonResponse({'enabled': True, 'error': 'unavailable'}, status=503)
    response = JsonResponse({'enabled': True, 'epochs': epochs, 'epoch': epoch, 'pollMs': live_poll_ms()})
    response['Cache-Control'] = 'no-store'
    return response


def changed_items(payload, client_prints):
    """What of ``payload`` differs from the copy whose fingerprints are given."""
    prints = structure_fingerprints(payload)
    client_prints = client_prints if isinstance(client_prints, dict) else {}
    client_weeks = client_prints.get('weeks') if isinstance(client_prints.get('weeks'), dict) else {}
    client_components = client_prints.get('components') if isinstance(client_prints.get('components'), dict) else {}
    weeks, components = {}, {}
    week_order, component_order = [], {}
    for week in payload.get('weekStructure') or []:
        week_id = str(week.get('id') or '')
        week_order.append(week_id)
        component_order[week_id] = []
        if client_weeks.get(week_id) != prints['weeks'].get(week_id):
            weeks[week_id] = week_fields(week)
        for component in week.get('components') or []:
            component_id = str(component.get('id') or '')
            component_order[week_id].append(component_id)
            if client_components.get(component_id) != prints['components'].get(component_id):
                components[component_id] = component
    module = {key: value for key, value in payload.items() if key != 'weekStructure'}
    return {
        'module': module,
        'weekOrder': week_order,
        'componentOrder': component_order,
        'weeks': weeks,
        'components': components,
        'itemFingerprints': prints,
    }


@csrf_exempt
def curriculum_module_changes(request, module_catalogue_id):
    """What changed in one module since the copy the caller holds.

    POST, because the caller's fingerprints do not fit a query string; it
    writes nothing. Body: ``{"revision": "...", "fingerprints": {"weeks": {},
    "components": {}}}``. A caller already at the stored revision is answered
    ``{"unchanged": true}`` without the module being built.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    views = _views()
    body = views.json_body(request)
    if not isinstance(body, dict):
        return JsonResponse({'error': 'Invalid JSON body.'}, status=400)
    catalogue_id = views.resolve_stored_module_catalogue_id(views.clean_str(module_catalogue_id))
    if not catalogue_id:
        return JsonResponse({'error': 'Module not found.'}, status=404)
    known_revision = views.clean_str(body.get('revision'))
    try:
        with views.curriculum_read_scope(targeted_child_reads=True):
            source_rows = views.module_structure_source_rows(catalogue_id)
            revision = views.module_structure_revision(catalogue_id, source_rows=source_rows) \
                or views.STRUCTURE_REVISION_UNAVAILABLE
            if known_revision and revision == known_revision:
                return JsonResponse({'revision': revision, 'unchanged': True})
            payload = views.get_authoring_structure_payload(catalogue_id, source_rows=source_rows)
    except Exception:
        logger.exception('Unable to read the changes for module %s.', catalogue_id)
        return JsonResponse({'error': 'Unable to read the saved module.'}, status=500)
    if not payload:
        return JsonResponse({'error': 'Module not found.'}, status=404)
    payload = {**payload, 'structureRevision': revision}
    return JsonResponse({'revision': revision, **changed_items(payload, body.get('fingerprints'))})
