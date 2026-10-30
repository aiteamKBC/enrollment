"""A jsonb-side reimplementation of ``bulk_assigned_learner_counts``.

The Python original in :mod:`curriculum_api.learner_assignments` is still the
source of truth and is not touched by this module. It is correct; it is just
expensive in the one way that matters over a slow link. To produce a handful of
integers -- the "Learners (N)" figure on every module card -- it reads every
learner row whole, and ``enrolment."Created_users"."Learning_plan"`` alone is
about 11 MB across ~460 learners. On a developer machine roughly 1 Mbps from the
Neon region that single read was measured at 83.9 s, longer than the statement
timeout, so the curriculum payload build never completed and the cache it would
have populated stayed empty.

Nothing about the *rule* is expensive; only the transfer is. So the same rule is
expressed in SQL and only the decisions come back:

    learner_id | programme | group | has_plan | has_explicit | id_count | matched

``matched`` is already intersected with the caller's module ids, so the payload
is a few hundred short rows instead of the whole plan corpus.

The rule being reproduced is ``learner_api.learning_plan._effective_plan_ids``
together with ``learner_api.mappers.stored_training_plan``:

* ``Training_plan`` holds the plan when it is a JSON array, otherwise
  ``Learning_plan`` does; when neither is an array there is no saved plan and
  the learner is taught their group's preset.
* Only object entries count, and only a non-blank ``moduleId``.
* An empty id list stays empty -- it is a cleared assignment, not an absent one.
* Any entry with ``assignmentMode == 'explicit'`` freezes the plan to exactly
  what it lists.
* Otherwise the group preset is appended for modules the plan does not name.

Two things are deliberately *not* moved into SQL.

The group preset still goes through ``learning_plan._group_module_ids``, one
query per distinct (programme, group) pair, cached exactly as the original
caches it. That query ends in ``LIMIT 1`` with no ``ORDER BY`` and
``curriculum.groups`` does contain duplicate (group_name, programme) rows, so
which row wins is already arbitrary. Batching those lookups would make a
*different* arbitrary choice and surface as a spurious mismatch, so the lookup
is left alone and shares the original's cache.

A plan column holding a JSON *string* is also left to Python. ``_maybe_json``
re-parses such a value, so it may or may not decode to a list, and Postgres has
no safe way to try. Those learners are flagged ``ambiguous`` and counted by the
original code path. No row in the current data is in that state.
"""
from __future__ import annotations

import logging
import time

from django.db import connections

from learner_api import learning_plan as plans
from learner_api.models import EnrolmentUser

logger = logging.getLogger(__name__)

#: The relation the production query reads. Parameterised only so the tests can
#: substitute a literal VALUES list and exercise the jsonb expressions below
#: against a real Postgres without needing the table.
LEARNER_RELATION = 'enrolment."Created_users" u'

#: One row per learner: enough to apply `_effective_plan_ids` without ever
#: shipping a plan. `%s` is the caller's module ids as a text[].
PLAN_FACTS_SQL = """
WITH wanted(module_id) AS (
    SELECT DISTINCT unnest(%s::text[])
),
src AS (
    SELECT
        u.id              AS learner_id,
        u."Programme"     AS programme,
        u."Group"         AS grp,
        u."Training_plan" AS tp,
        u."Learning_plan" AS lp
    FROM {source}
),
resolved AS (
    SELECT
        learner_id,
        programme,
        grp,
        -- mappers.stored_training_plan: the first of the two columns holding a
        -- list. Anything that is not an array is not a saved plan.
        CASE
            WHEN jsonb_typeof(tp) = 'array' THEN tp
            WHEN jsonb_typeof(tp) IS DISTINCT FROM 'string'
                 AND jsonb_typeof(lp) = 'array' THEN lp
        END AS plan,
        -- A jsonb string could still decode to a list in Python; SQL cannot
        -- decide that safely, so hand the learner back to the original.
        -- IS NOT DISTINCT FROM, not `=`: jsonb_typeof(NULL) is NULL, and a NULL
        -- here would make `NOT ambiguous` below drop the learner's entries
        -- entirely -- which is every learner, since Training_plan is normally
        -- null.
        (jsonb_typeof(tp) IS NOT DISTINCT FROM 'string'
         OR (jsonb_typeof(tp) IS DISTINCT FROM 'array'
             AND jsonb_typeof(lp) IS NOT DISTINCT FROM 'string')) AS ambiguous
    FROM src
),
entry AS (
    SELECT r.learner_id, e.value AS entry
    FROM resolved r
    CROSS JOIN LATERAL jsonb_array_elements(r.plan) AS e(value)
    WHERE r.plan IS NOT NULL AND NOT r.ambiguous
),
obj AS (
    -- `[entry for entry in plan if isinstance(entry, dict)]`, then
    -- `_s(entry.get("moduleId"))` with '' meaning absent.
    SELECT
        learner_id,
        NULLIF(btrim(COALESCE(entry ->> 'moduleId', '')), '') AS module_id,
        (entry ->> 'assignmentMode') IS NOT DISTINCT FROM 'explicit' AS explicit
    FROM entry
    WHERE jsonb_typeof(entry) = 'object'
),
agg AS (
    SELECT
        learner_id,
        count(module_id) AS id_count,
        bool_or(explicit) AS has_explicit
    FROM obj
    GROUP BY learner_id
),
hit AS (
    SELECT DISTINCT o.learner_id, o.module_id
    FROM obj o
    JOIN wanted w ON w.module_id = o.module_id
),
matched AS (
    SELECT learner_id, array_agg(module_id) AS module_ids
    FROM hit
    GROUP BY learner_id
)
SELECT
    r.learner_id,
    r.programme,
    r.grp,
    r.ambiguous,
    (r.plan IS NOT NULL)                    AS has_plan,
    COALESCE(a.id_count, 0)                 AS id_count,
    COALESCE(a.has_explicit, false)         AS has_explicit,
    COALESCE(m.module_ids, ARRAY[]::text[]) AS matched
FROM resolved r
LEFT JOIN agg a ON a.learner_id = r.learner_id
LEFT JOIN matched m ON m.learner_id = r.learner_id
"""


def _preset_ids(programme, group, cache):
    """`learning_plan._preset_ids_for`, keyed off plain values not a learner."""
    key = (plans._s(programme), plans._s(group))
    if key not in cache:
        cache[key] = plans._group_module_ids(*key)
    return cache[key]


def fetch_plan_facts(wanted, using=None, source=None):
    """One row per learner describing their plan, without shipping the plan."""
    alias = using or EnrolmentUser.all_learners.db
    sql = PLAN_FACTS_SQL.format(source=source or LEARNER_RELATION)
    with connections[alias].cursor() as cursor:
        cursor.execute(sql, [sorted(wanted)])
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _effective_ids_from_fact(fact, wanted, cache):
    """`_effective_plan_ids`, restricted to ids in `wanted` -- SQL cannot report
    an id it was never asked about, so the caller must pass a `wanted` wide
    enough to cover every id it needs back (the whole catalogue, to get a
    learner's true plan size rather than just their overlap with one target)."""
    matched = {module_id for module_id in (fact['matched'] or []) if module_id in wanted}
    if not fact['has_plan']:
        # No saved plan: the group preset *is* the effective plan.
        return {i for i in _preset_ids(fact['programme'], fact['grp'], cache) if i in wanted}
    if not fact['id_count'] or fact['has_explicit']:
        # A cleared plan stays cleared, and an explicit one is the whole of it.
        return matched
    return matched | {i for i in _preset_ids(fact['programme'], fact['grp'], cache) if i in wanted}


def counts_from_plan_facts(facts, wanted, preset_cache=None):
    """Apply `_effective_plan_ids` to the fetched decisions.

    Pure apart from the group-preset lookup, which is the one thing still worth
    a query. Returns the counts and the learner ids SQL could not decide.
    """
    wanted = {plans._s(module_id) for module_id in wanted if plans._s(module_id)}
    counts = {module_id: 0 for module_id in wanted}
    cache = preset_cache if preset_cache is not None else {}
    ambiguous = []
    for fact in facts:
        if fact['ambiguous']:
            ambiguous.append(fact['learner_id'])
            continue
        for module_id in _effective_ids_from_fact(fact, wanted, cache):
            counts[module_id] += 1
    return counts, ambiguous


def assignment_directory_facts(target_module_ids):
    """Per-learner effective module ids and plan size for the "Assign learners"
    directory (`learner_assignments._payload`), without shipping every
    learner's plan to Python -- see this module's docstring for why that
    transfer alone was slow enough to blow the statement timeout.

    `wanted` is the whole catalogue, not just `target_module_ids`: the
    directory shows every learner's total assigned-module count, not only
    whether they hold the modules being viewed, and SQL can only report ids it
    was asked about.

    Returns `(facts_by_learner_id, ambiguous_learner_ids)`, where each fact is
    `{'effective': {module_id, ...}, 'moduleCount': int}`. A learner id absent
    from the ambiguous list and missing a plan-string edge case is not
    possible to omit from `facts_by_learner_id` -- every learner row read by
    `fetch_plan_facts` produces one or the other.
    """
    from . import views

    catalogue_rows = views.authoring_fetch_all(views.AUTHORING_MODULES_TABLE)
    wanted = {
        views.clean_str(row.get('module_catalogue_id')) for row in catalogue_rows
        if views.clean_str(row.get('module_catalogue_id'))
    }
    wanted |= {plans._s(module_id) for module_id in target_module_ids if plans._s(module_id)}
    if not wanted:
        return {}, []
    facts = fetch_plan_facts(wanted)
    # One round trip for every distinct (programme, group) pair in the result,
    # same as bulk_assigned_learner_counts_sql -- see its own comment for why
    # that matters on a slow link.
    cache = dict(plans.group_module_ids_bulk(
        {(fact['programme'], fact['grp']) for fact in facts}
    ))
    rows = {}
    ambiguous = []
    for fact in facts:
        if fact['ambiguous']:
            ambiguous.append(fact['learner_id'])
            continue
        effective = _effective_ids_from_fact(fact, wanted, cache)
        rows[fact['learner_id']] = {'effective': effective, 'moduleCount': len(effective)}
    return rows, ambiguous


def bulk_assigned_learner_counts_sql(module_ids):
    """Counts identical to `learner_assignments.bulk_assigned_learner_counts`.

    Not yet wired into the payload build -- see `compare_assignment_counts` for
    the check that has to pass first.
    """
    wanted = {plans._s(module_id) for module_id in module_ids if plans._s(module_id)}
    if not wanted:
        return {}
    facts = fetch_plan_facts(wanted)
    # Seed the preset cache in one round trip. `counts_from_plan_facts` would
    # otherwise fill it a pair at a time, and on the live data that is ~46
    # sequential lookups of a 155-row table -- more latency than the learner read
    # this module exists to remove. The keys are exactly what `_preset_ids`
    # builds, so anything the bulk read misses still falls through to it.
    cache = dict(plans.group_module_ids_bulk(
        {(fact['programme'], fact['grp']) for fact in facts}
    ))
    counts, ambiguous = counts_from_plan_facts(facts, wanted, cache)
    if ambiguous:
        # A plan stored as a JSON string. Rare enough that reading those few rows
        # whole costs nothing, and it keeps the answer exactly the original's.
        logger.info('assignment counts: %d learner(s) fell back to Python plan parsing', len(ambiguous))
        learners = EnrolmentUser.all_learners.only(
            'id', 'programme', 'group', 'learning_plan', 'training_plan',
        ).filter(pk__in=ambiguous)
        for learner in learners:
            for module_id in set(plans._effective_plan_ids(learner, cache)):
                if module_id in wanted:
                    counts[module_id] += 1
    return counts


def compare_assignment_counts(module_ids):
    """Run both implementations over the same live data and diff every module.

    Returns a report; the caller decides what to do with it. Nothing here
    changes what the application serves.
    """
    from .learner_assignments import bulk_assigned_learner_counts

    started = time.monotonic()
    old = bulk_assigned_learner_counts(module_ids)
    old_seconds = time.monotonic() - started

    started = time.monotonic()
    new = bulk_assigned_learner_counts_sql(module_ids)
    new_seconds = time.monotonic() - started

    every = sorted(set(old) | set(new))
    mismatches = [
        {
            'module_id': module_id,
            'old_count': old.get(module_id),
            'new_count': new.get(module_id),
            'difference': (new.get(module_id) or 0) - (old.get(module_id) or 0),
        }
        for module_id in every
        if old.get(module_id) != new.get(module_id)
    ]
    for row in mismatches:
        logger.error(
            'assignment count mismatch: module_id=%s old_count=%s new_count=%s difference=%+d',
            row['module_id'], row['old_count'], row['new_count'], row['difference'],
        )
    return {
        'modules': len(every),
        'matched': len(every) - len(mismatches),
        'mismatches': mismatches,
        'old_seconds': round(old_seconds, 2),
        'new_seconds': round(new_seconds, 2),
        'old_counts': old,
        'new_counts': new,
    }
