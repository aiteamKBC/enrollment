"""Item-level three-way merge for Module Builder partial saves.

A partial save used to be accepted only when the module was still exactly the
version the editor had read. Two people working on different components of one
module therefore refused each other on every save: the second one re-read the
whole module (up to 5 MB), merged it in the browser and tried again, queueing
behind the first save's lock each time.

This module answers the question the browser was answering, on the server and
under the module's lock, one item at a time:

* an item only one side changed takes that side's version, whole;
* an item both sides changed is merged field by field, the way
  ``lib/collaborativeMerge.ts`` merges, and a field both changed to different
  values is a **conflict** -- refused, never silently decided (D2);
* an item one side deleted and the other edited is a conflict as well, and a
  deleted row is never brought back unless the caller names it in
  ``restoreIds`` (D3).

"Did the other side change this item?" is answered by the fingerprint the
server issued with the copy the editor started from (``itemFingerprints``), not
by comparing the editor's copy with the stored one. The editor's copy has been
through the browser's normalisation, so a field-by-field comparison would read
every default the browser filled in as a change somebody made. The fingerprint
is computed here, from the same payload shape, both times, so it is exact.

Pure Python: no Django, no database. The view reads the stored module and the
soft-deleted ids and passes them in.
"""
from __future__ import annotations

import hashlib
import json

# Module fields the server derives on every read or save. Taken from the
# editor's copy (they are recomputed anyway) and never reported as a conflict.
DERIVED_MODULE_FIELDS = frozenset({
    'structureRevision', 'lastUpdated', 'createdAt', 'weeks', 'weeksNumber',
    'totalOtjh', 'declaredTotalOtjh', 'ksbCount', 'lessonCount', 'quizCount',
    'qualityScore', 'qualityChecklist', 'assignedLearnerCount', 'sourceModule',
    'deliveryUsages', 'deliveryWeeks', 'deliveryStatus', 'deliveryMetadata',
    'itemFingerprints', 'id', 'moduleId', 'moduleCatalogueId', 'structureId',
    'catalogueId',
    # Re-derived from the live-session components (the Builder's
    # recalculateModule and the schedule both rewrite it); only a fallback.
    'sessionsNumber',
})

# Week fields stamped from the session plan on every read. Not stored, so
# whichever copy is newer is right; never a conflict.
DERIVED_WEEK_FIELDS = frozenset({
    'weekNumber', 'moduleId', 'sessionDate', 'sessionDay', 'sessionStartTime',
    'sessionEndTime', 'sessionDurationMinutes', 'sessionRescheduled',
})

# Component settings the session plan or the Teams attachment path writes.
# The stored copy wins when both sides moved them: it is what the planner and
# Microsoft last said, and the editor never authored it.
PLANNED_SETTING_KEYS = frozenset({
    'sessionDate', 'sessionDay', 'sessionTime', 'durationMinutes',
    'sessionDateTimeUtc', 'teamsStartDateTimeUtc',
})

# Settings that name a Microsoft meeting. Mirrors TEAMS_MEETING_SETTING_KEYS in
# module-builder/moduleAuthoringData.ts: the stored value always wins, so a tab
# opened before a meeting was booked or moved can never re-point a join link.
TEAMS_OWNED_SETTING_KEYS = frozenset({
    'teamsLiveSessionId', 'teamsSessionNumber', 'teamsEventId', 'teamsOnlineMeetingId',
    'teamsCalendarSeries', 'teamsMeetingOptionsUrl', 'teamsMeetingUrl', 'liveSessionUrl',
    'teamsProvider', 'teamsOccurrenceId', 'teamsWebLink', 'teamsDurationMinutes',
    'sessionRescheduled', 'liveSessionLinkOverride', 'teamsCalendarBinding',
    'teamsMainLinkInherited', 'liveSessionMeetingScope',
    'extraTeamsMeetingUrl', 'extraTeamsLiveSessionId', 'extraTeamsEventId',
    'extraTeamsOnlineMeetingId', 'extraTeamsWebLink', 'extraTeamsMeetingOptionsUrl',
    'extraTeamsOrganizerEmail', 'extraTeamsAttendees', 'extraTeamsPresenters',
    'extraTeamsCoOrganizers', 'extraTeamsStartDateTimeUtc', 'extraTeamsDurationMinutes',
    'extraTeamsSubject',
})

# Values chosen as a set, compared whole. Half of one is a value neither
# editor chose. Paths with list ids replaced by `*`.
ATOMIC_PATTERNS = frozenset({
    'weeklySchedule', 'sessionHolidays', 'epaRequirements', 'qualificationOutcomes',
    'moduleKsbMappings',
    'weekStructure[*].learningOutcomes',
    'weekStructure[*].ksbMappings',
    'weekStructure[*].components[*].ksbMappings',
})

CONTROL_KEYS = frozenset({
    'saveMode', 'structureDelta', 'structureBase', 'expectedRevision', 'expected_revision',
    'weekStructure', 'weeks', 'restoreIds',
})


class ModuleMergeConflicts(Exception):
    """Both sides changed the same thing. Nothing may be written."""

    def __init__(self, conflicts):
        super().__init__(f'{len(conflicts)} conflicting change(s)')
        self.conflicts = conflicts


class ModuleMergeUnavailable(Exception):
    """The save does not carry what an item merge needs (an older client)."""


# --- fingerprints -----------------------------------------------------------

def fingerprint(value):
    """Short, stable digest of one payload item. Same input, same answer."""
    encoded = json.dumps(value, sort_keys=True, default=str, separators=(',', ':'))
    return hashlib.sha1(encoded.encode('utf-8')).hexdigest()[:16]


def week_fields(week):
    return {key: value for key, value in (week or {}).items() if key != 'components'}


def structure_fingerprints(payload):
    """``{'weeks': {id: fp}, 'components': {id: fp}}`` for a structure payload."""
    weeks, components = {}, {}
    for week in (payload or {}).get('weekStructure') or []:
        week_id = _id(week)
        if not week_id:
            continue
        weeks[week_id] = fingerprint(week_fields(week))
        for component in week.get('components') or []:
            component_id = _id(component)
            if component_id:
                components[component_id] = fingerprint(component)
    return {'weeks': weeks, 'components': components}


# --- equality ---------------------------------------------------------------

def _id(item):
    return str((item or {}).get('id') or '').strip()


def _normalised(value):
    """A value as a save sees it: empty is empty, a number is a number.

    The editor's copy and the stored copy come from different serialisers --
    JSON in a browser, Python dicts here -- so ``1`` and ``1.0``, ``''`` and
    ``None``, a missing key and an empty list must compare equal, or every
    item would look changed.
    """
    if value is None or value == '' or value == [] or value == {}:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            normalised = _normalised(item)
            if normalised is not None:
                out[str(key)] = normalised
        return out or None
    if isinstance(value, (list, tuple)):
        return [_normalised(item) for item in value]
    if isinstance(value, str):
        return value
    return str(value)


def same(a, b):
    return _normalised(a) == _normalised(b)


def _pattern(path):
    out, depth = [], 0
    for char in path:
        if char == '[':
            depth += 1
            if depth == 1:
                out.append('[*]')
            continue
        if char == ']':
            depth -= 1
            continue
        if depth == 0:
            out.append(char)
    return ''.join(out)


def _child(path, key):
    return f'{path}.{key}' if path else key


def _keyed(value):
    return (
        isinstance(value, list) and value
        and all(isinstance(entry, dict) and _id(entry) for entry in value)
    )


# --- the field merge ----------------------------------------------------------

class _Context:
    def __init__(self, *, ignore=None, prefer_stored=None, atomic=None):
        self.conflicts = []
        self.ignore = ignore or (lambda pattern: False)
        self.prefer_stored = prefer_stored or (lambda pattern: False)
        self.atomic = atomic or (lambda pattern: pattern in ATOMIC_PATTERNS)

    def conflict(self, path, local, stored, kind='value'):
        self.conflicts.append({'path': path, 'kind': kind, 'local': local, 'remote': stored})


_ABSENT = object()


_SAME_BASE = object()


def merge_value(base, local, stored, path, ctx, mine_base=_SAME_BASE):
    """Three-way merge of one value; records a conflict and keeps local on a real clash.

    ``base`` is the server's own copy of what the editor started from, so
    "did someone else change this?" is ``stored`` against it. ``mine_base``,
    when the editor sends it, is that same starting point as the editor holds
    it -- normalised by the browser -- so "did this author change it?" is
    ``local`` against it. With one base for both questions, every default the
    browser filled in would read as the author's change, and a colleague's
    edit to that field as a conflict nobody made.
    """
    if mine_base is _SAME_BASE:
        mine_base = base
    if same(local, stored):
        return local
    pattern = _pattern(path)
    if ctx.ignore(pattern):
        return local
    if ctx.prefer_stored(pattern):
        return stored
    if same(mine_base, local):
        return stored
    if same(base, stored):
        return local
    if not ctx.atomic(pattern):
        if isinstance(local, list) and isinstance(stored, list) and (_keyed(local) or _keyed(stored)):
            return _merge_keyed_list(
                base if isinstance(base, list) else [], local, stored, path, ctx,
                mine_base=mine_base if isinstance(mine_base, list) else [],
            )
        if isinstance(local, dict) and isinstance(stored, dict):
            return merge_object(
                base if isinstance(base, dict) else {}, local, stored, path, ctx,
                mine_base=mine_base if isinstance(mine_base, dict) else {},
            )
    ctx.conflict(path, local, stored)
    return local


def merge_object(base, local, stored, path, ctx, mine_base=None):
    mine_base = base if mine_base is None else mine_base
    merged = {}
    ordered = list(local.keys()) + [key for key in stored.keys() if key not in local]
    for key in ordered:
        value = merge_value(
            base.get(key), local.get(key, None), stored.get(key, None), _child(path, key), ctx,
            mine_base=mine_base.get(key),
        )
        if key in local or value is not None:
            merged[key] = value
        # A key the editor removed and nobody else touched stays removed.
    return merged


def _merge_keyed_list(base, local, stored, path, ctx, mine_base=None):
    base_by = {_id(row): row for row in base if isinstance(row, dict)}
    mine_by = {_id(row): row for row in (base if mine_base is None else mine_base) if isinstance(row, dict)}
    local_by = {_id(row): row for row in local}
    stored_by = {_id(row): row for row in stored}
    merged = []
    for row in local:
        row_id = _id(row)
        if row_id in stored_by:
            merged.append(merge_value(
                base_by.get(row_id), row, stored_by[row_id], f'{path}[{row_id}]', ctx,
                mine_base=mine_by.get(row_id, base_by.get(row_id)),
            ))
        elif row_id in base_by and same(mine_by.get(row_id, base_by[row_id]), row):
            continue  # they deleted it, the editor never touched it
        elif row_id in base_by:
            ctx.conflict(f'{path}[{row_id}]', row, None, 'remote-deleted')
            merged.append(row)
        else:
            merged.append(row)  # the editor added it
    for index, row in enumerate(stored):
        row_id = _id(row)
        if row_id in local_by:
            continue
        if row_id in base_by:
            if not same(base_by[row_id], row):
                ctx.conflict(f'{path}[{row_id}]', None, row, 'local-deleted')
            continue
        anchor = _id(stored[index - 1]) if index > 0 else ''
        position = next((at for at, item in enumerate(merged) if _id(item) == anchor), -1) if anchor else -1
        merged.insert(position + 1 if anchor else 0, row)
    return merged


# --- orders -------------------------------------------------------------------

def merge_order(base_ids, local_ids, stored_ids, members, path, ctx):
    """One list's order, three ways, holding exactly ``members``.

    Whoever moved the shared entries decides their order; an entry only one
    side has stays behind the entry it followed in that side's list. Two
    different rearrangements of the same entries are a conflict.
    """
    base_ids = [value for value in base_ids or []]
    local_ids = [value for value in local_ids or []]
    stored_ids = [value for value in stored_ids or []]
    shared = set(base_ids) & set(local_ids) & set(stored_ids) & set(members)

    def order(ids):
        return [value for value in ids if value in shared]

    local_moved = order(local_ids) != order(base_ids)
    stored_moved = order(stored_ids) != order(base_ids)
    if local_moved and stored_moved and order(local_ids) != order(stored_ids):
        ctx.conflict(path, order(local_ids), order(stored_ids), 'reorder')
    primary, secondary = (stored_ids, local_ids) if stored_moved and not local_moved else (local_ids, stored_ids)
    result = []
    for value in primary:
        if value in members and value not in result:
            result.append(value)
    for source in (secondary, base_ids):
        for index, value in enumerate(source):
            if value not in members or value in result:
                continue
            anchor = next((source[at] for at in range(index - 1, -1, -1) if source[at] in result), '')
            result.insert(result.index(anchor) + 1 if anchor else 0, value)
    for value in members:
        if value not in result:
            result.append(value)
    return result


# --- the save -------------------------------------------------------------------

def _week_path(week_id):
    return f'weekStructure[{week_id}]'


def _component_path(week_id, component_id):
    return f'weekStructure[{week_id}].components[{component_id}]'


def _module_ctx():
    return _Context(ignore=lambda pattern: pattern in DERIVED_MODULE_FIELDS)


def _week_ctx():
    return _Context(ignore=lambda pattern: pattern.split('.')[-1] in DERIVED_WEEK_FIELDS)


def _component_ctx():
    def prefer_stored(pattern):
        parts = pattern.split('.')
        return (
            len(parts) >= 2 and parts[-2] == 'settings'
            and (parts[-1] in TEAMS_OWNED_SETTING_KEYS or parts[-1] in PLANNED_SETTING_KEYS)
        )

    def ignore(pattern):
        return pattern.split('.')[-1] in {'moduleId'}

    return _Context(ignore=ignore, prefer_stored=prefer_stored)


def merge_partial_save(stored, payload, *, deleted_ids=frozenset(), fingerprint_of=fingerprint):
    """The full module a partial save should write, given what is stored now.

    ``stored`` is the structure payload as the server reads it; ``payload`` is
    the editor's partial save carrying ``structureDelta`` and
    ``structureBase``. ``deleted_ids`` are week/component ids that exist as
    soft-deleted rows. Returns ``(module_payload, summary)`` or raises
    ModuleMergeConflicts / ModuleMergeUnavailable.
    """
    delta = payload.get('structureDelta')
    base = payload.get('structureBase')
    if not isinstance(delta, dict) or not isinstance(base, dict):
        raise ModuleMergeUnavailable('A merge needs the copy the changes were made against.')
    base_prints = base.get('fingerprints') if isinstance(base.get('fingerprints'), dict) else None
    if base_prints is None:
        raise ModuleMergeUnavailable('A merge needs the fingerprints of the copy it was made against.')
    base_week_prints = base_prints.get('weeks') or {}
    base_component_prints = base_prints.get('components') or {}
    base_week_copies = base.get('weeks') if isinstance(base.get('weeks'), dict) else {}
    base_component_copies = base.get('components') if isinstance(base.get('components'), dict) else {}
    # The same starting point as the editor holds it (normalised by the
    # browser), for "did this author change it?". Older clients send none.
    mine_module = base.get('localModule') if isinstance(base.get('localModule'), dict) else None
    mine_weeks = base.get('localWeeks') if isinstance(base.get('localWeeks'), dict) else {}
    mine_components = base.get('localComponents') if isinstance(base.get('localComponents'), dict) else {}
    base_week_order = [str(value) for value in base.get('weekOrder') or []]
    base_component_order = {
        str(week_id): [str(value) for value in ids or []]
        for week_id, ids in (base.get('componentOrder') or {}).items()
    }
    restore_ids = {str(value) for value in payload.get('restoreIds') or []}
    deleted_ids = {str(value) for value in deleted_ids}

    local_week_order = [str(value) for value in delta.get('weekOrder') or []]
    local_component_order = {
        str(week_id): [str(value) for value in ids or []]
        for week_id, ids in (delta.get('componentOrder') or {}).items()
    }
    local_weeks = delta.get('weeks') if isinstance(delta.get('weeks'), dict) else {}
    local_components = delta.get('components') if isinstance(delta.get('components'), dict) else {}

    stored_weeks = {}
    stored_week_order = []
    stored_components = {}
    stored_component_order = {}
    for week in stored.get('weekStructure') or []:
        week_id = _id(week)
        stored_week_order.append(week_id)
        stored_weeks[week_id] = week_fields(week)
        stored_component_order[week_id] = []
        for component in week.get('components') or []:
            component_id = _id(component)
            stored_components[component_id] = component
            stored_component_order[week_id].append(component_id)

    conflicts = []
    summary = {'taken_local': 0, 'taken_stored': 0, 'merged': 0, 'deleted': 0}

    # Module fields. Field by field: the module row is one record many fields
    # wide, and two editors changing two of its fields is the normal case.
    module_ctx = _module_ctx()
    base_module = base.get('module') if isinstance(base.get('module'), dict) else {}
    stored_module = {key: value for key, value in stored.items() if key != 'weekStructure'}
    local_module = {key: value for key, value in payload.items() if key not in CONTROL_KEYS}
    merged_module = {}
    for key, local_value in local_module.items():
        merged_module[key] = merge_value(
            base_module.get(key), local_value, stored_module.get(key), key, module_ctx,
            **({'mine_base': mine_module.get(key)} if mine_module is not None else {}),
        )
    conflicts.extend(module_ctx.conflicts)

    def resolve_item(kind, item_id, *, local_item, base_copy, base_print, stored_item, path, ctx, mine_copy=None):
        """One week or component: (merged item or None, conflict list)."""
        in_base = base_print is not None
        if stored_item is None:
            if item_id in deleted_ids and item_id not in restore_ids:
                return local_item, [{'path': path, 'kind': 'remote-deleted', 'local': local_item, 'remote': None}]
            if in_base and item_id not in restore_ids:
                # Gone entirely (hard-deleted) while the editor changed it.
                return local_item, [{'path': path, 'kind': 'remote-deleted', 'local': local_item, 'remote': None}]
            summary['taken_local'] += 1
            return local_item, []
        stored_print = fingerprint_of(stored_item)
        if in_base and stored_print == base_print:
            summary['taken_local'] += 1
            return local_item, []
        if same(local_item, stored_item):
            summary['taken_local'] += 1
            return local_item, []
        merged = merge_value(
            base_copy if in_base else None, local_item, stored_item, path, ctx,
            **({'mine_base': mine_copy} if in_base and mine_copy is not None else {}),
        )
        summary['merged'] += 1
        found, ctx.conflicts = ctx.conflicts, []
        return merged, found

    # Weeks.
    final_weeks = {}
    deleted_weeks = set()
    for week_id in _union(base_week_order, list(base_week_prints), local_week_order, stored_week_order):
        in_local = week_id in local_week_order
        in_base = week_id in base_week_prints
        stored_week = stored_weeks.get(week_id)
        if in_local and week_id in local_weeks:
            merged, found = resolve_item(
                'week', week_id,
                local_item=week_fields(local_weeks[week_id]),
                base_copy=week_fields(base_week_copies.get(week_id) or {}),
                base_print=base_week_prints.get(week_id),
                stored_item=stored_week,
                path=_week_path(week_id),
                ctx=_week_ctx(),
                mine_copy=week_fields(mine_weeks[week_id]) if isinstance(mine_weeks.get(week_id), dict) else None,
            )
            conflicts.extend(found)
            final_weeks[week_id] = merged
        elif in_local:
            if stored_week is not None:
                final_weeks[week_id] = stored_week
                summary['taken_stored'] += 1
            # else: they deleted a week this editor never changed.
        elif in_base:
            if stored_week is None:
                continue
            if fingerprint_of(stored_week) == base_week_prints.get(week_id):
                deleted_weeks.add(week_id)
                summary['deleted'] += 1
            else:
                conflicts.append({
                    'path': _week_path(week_id), 'kind': 'local-deleted',
                    'local': None, 'remote': stored_week,
                })
                final_weeks[week_id] = stored_week
        elif stored_week is not None:
            final_weeks[week_id] = stored_week  # added by someone else
            summary['taken_stored'] += 1

    # Components. Where a component lives is its `weekId`, merged like any
    # other field, so a move on one side and an edit on the other both land.
    local_location = {
        component_id: week_id
        for week_id, ids in local_component_order.items()
        for component_id in ids
    }
    base_location = {
        component_id: week_id
        for week_id, ids in base_component_order.items()
        for component_id in ids
    }
    final_components = {}
    for component_id in _union(
        [value for ids in base_component_order.values() for value in ids],
        list(base_component_prints),
        [value for ids in local_component_order.values() for value in ids],
        [value for ids in stored_component_order.values() for value in ids],
    ):
        in_local = component_id in local_location
        in_base = component_id in base_component_prints
        stored_component = stored_components.get(component_id)
        week_for_path = local_location.get(component_id) or base_location.get(component_id) \
            or (stored_component or {}).get('weekId') or ''
        path = _component_path(week_for_path, component_id)
        if in_local and component_id in local_components:
            local_component = {**local_components[component_id], 'weekId': local_location[component_id]}
            merged, found = resolve_item(
                'component', component_id,
                local_item=local_component,
                base_copy=base_component_copies.get(component_id) or {},
                base_print=base_component_prints.get(component_id),
                stored_item=stored_component,
                path=path,
                ctx=_component_ctx(),
                mine_copy=mine_components.get(component_id) if isinstance(mine_components.get(component_id), dict) else None,
            )
            conflicts.extend(found)
            final_components[component_id] = merged
        elif in_local:
            if stored_component is not None:
                final_components[component_id] = stored_component
                summary['taken_stored'] += 1
        elif in_base:
            if stored_component is None:
                continue
            if fingerprint_of(stored_component) == base_component_prints.get(component_id):
                summary['deleted'] += 1
            else:
                conflicts.append({'path': path, 'kind': 'local-deleted', 'local': None, 'remote': stored_component})
                final_components[component_id] = stored_component
        elif stored_component is not None:
            final_components[component_id] = stored_component
            summary['taken_stored'] += 1

    # A week deleted here that someone else has since put content into is not
    # the week this editor deleted.
    for component_id, component in final_components.items():
        week_id = str(component.get('weekId') or '')
        if week_id in deleted_weeks:
            conflicts.append({
                'path': _week_path(week_id), 'kind': 'local-deleted',
                'local': None, 'remote': stored_weeks.get(week_id),
            })
            deleted_weeks.discard(week_id)
            final_weeks[week_id] = stored_weeks[week_id]
        elif week_id not in final_weeks:
            conflicts.append({
                'path': _week_path(week_id), 'kind': 'remote-deleted',
                'local': local_weeks.get(week_id), 'remote': None,
            })

    if conflicts:
        raise ModuleMergeConflicts(_dedupe(conflicts))

    order_ctx = _Context()
    week_order = merge_order(
        base_week_order, local_week_order, stored_week_order, set(final_weeks), 'weekStructure', order_ctx,
    )
    members_by_week = {week_id: set() for week_id in week_order}
    for component_id, component in final_components.items():
        members_by_week.setdefault(str(component.get('weekId') or ''), set()).add(component_id)
    weeks = []
    for index, week_id in enumerate(week_order):
        component_ids = merge_order(
            base_component_order.get(week_id, []),
            local_component_order.get(week_id, []),
            stored_component_order.get(week_id, []),
            members_by_week.get(week_id, set()),
            f'{_week_path(week_id)}.components',
            order_ctx,
        )
        weeks.append({
            **final_weeks[week_id],
            'weekNumber': index + 1,
            'components': [final_components[component_id] for component_id in component_ids],
        })
    if order_ctx.conflicts:
        raise ModuleMergeConflicts(_dedupe(order_ctx.conflicts))
    merged_module['weekStructure'] = weeks
    merged_module['weeksNumber'] = len(weeks)
    return merged_module, summary


def restore_conflicts(delta, stored, deleted_ids, restore_ids):
    """Ids a save would bring back from a soft delete without being asked to.

    For the fast path, where the editor's base is the stored version: an item
    the delta sends that is not stored but exists deleted is either the
    editor's own undo (named in ``restoreIds``) or a stale copy resurrecting a
    colleague's delete, which must be refused.
    """
    stored_ids = set()
    for week in (stored or {}).get('weekStructure') or []:
        stored_ids.add(_id(week))
        for component in week.get('components') or []:
            stored_ids.add(_id(component))
    restore_ids = {str(value) for value in restore_ids or []}
    deleted_ids = {str(value) for value in deleted_ids or []}
    found = []
    for kind in ('weeks', 'components'):
        for item_id, item in ((delta or {}).get(kind) or {}).items():
            item_id = str(item_id)
            if item_id in stored_ids or item_id not in deleted_ids or item_id in restore_ids:
                continue
            week_id = str((item or {}).get('weekId') or '') if kind == 'components' else item_id
            path = _week_path(item_id) if kind == 'weeks' else _component_path(week_id, item_id)
            found.append({'path': path, 'kind': 'remote-deleted', 'local': item, 'remote': None})
    return found


def candidate_new_ids(delta, stored):
    """Ids the delta sends that the stored module does not hold (live)."""
    stored_ids = set()
    for week in (stored or {}).get('weekStructure') or []:
        stored_ids.add(_id(week))
        for component in week.get('components') or []:
            stored_ids.add(_id(component))
    found = {'weeks': [], 'components': []}
    for kind in ('weeks', 'components'):
        for item_id in ((delta or {}).get(kind) or {}):
            if str(item_id) not in stored_ids:
                found[kind].append(str(item_id))
    return found


def _union(*lists):
    seen, out = set(), []
    for values in lists:
        for value in values:
            value = str(value)
            if value and value not in seen:
                seen.add(value)
                out.append(value)
    return out


def _dedupe(conflicts):
    seen, out = set(), []
    for conflict in conflicts:
        key = (conflict['path'], conflict['kind'])
        if key in seen:
            continue
        seen.add(key)
        out.append(conflict)
    return out
