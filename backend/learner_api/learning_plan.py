"""The learner's learning plan — the modules they will actually be taught.

A learner's group already carries a module set (curriculum.groups.module_ids),
so the plan starts as that preset rather than blank. Staff then tune it: drop a
module the learner doesn't need, or add any module in the catalogue — including
one belonging to another programme, chosen from the programme picker.

Cross-programme modules used to be refused here, because a module from another
programme maps to different KSBs and sits under different funding. That is still
true, and it is why every module carries its programme through to the plan and
the picker: the combination is now allowed but never hidden, so whoever assembles
the plan can see when they have crossed a programme boundary.

Hours come from curriculum.modules.total_otjh (off-the-job hours), summed for
the plan total so staff can see the commitment while editing.

Each row also shows the module's delivery window (curriculum.modules
start_date/end_date), set where the module is scheduled in the curriculum tree.
Read-only here, and re-derived on every read like the hours — the plan decides
which modules are taught, not when. Empty for a module with no window yet.

The saved plan lives on enrolment."Created_users"."Learning_plan" (jsonb), which
also feeds the delivery-side training plan.

    GET   /learner_api/learning-plan/<pk>/            -> {plan, preset, available, programmes, totals}
    PATCH /learner_api/learning-plan/<pk>/            -> save {modules:[...]}

The module builder assigns the same record from the other end -- one module,
every learner -- and that route is documented beside its view below:

    GET   /learner_api/module-learners/<module_id>/   -> {module, learners, totals}
    PATCH /learner_api/module-learners/<module_id>/   -> save {learnerIds:[...]}
"""
import json
import logging
from datetime import date, datetime

from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from audit_api.last_audit_ledger_views import _connection as _audit_connection
from login.permissions import learner_self_or_staff, staff_only

from .constants import DELIVERY_PROGRAMME_STATUS
from .learner_progression import advance_learner
from .mappers import _normalize_free_courses, _s, get_training_plan, stored_training_plan, training_plan_field
from .models import EnrolmentUser
from .placement_links import placement_ids
from .projection_performance import projection_call, projection_operation

logger = logging.getLogger(__name__)


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


def _rows(sql, params):
    # Deliberately the `default` alias: every caller reads curriculum.*, which is
    # owned by curriculum_api and migrated on `default`. Not an oversight — see
    # the note in apprenticeship_agreement._group_dates.
    with projection_operation('assignment_query'), connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _hours(value):
    """total_otjh is numeric in Postgres; JSON needs a float (0.0 when unset)."""
    return float(value) if value is not None else 0.0


def _iso_day(value):
    """A date column as YYYY-MM-DD; "" when the module has no window set.

    The column is a real `date`, but psycopg hands back a datetime on some
    drivers and the older rows were written as text — so anything date-like is
    reduced to its first ten characters rather than assumed.
    """
    if value in (None, ""):
        return ""
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    return _s(value)[:10]


def _parse_day(value):
    """A learner date column as a real ``date``, or None.

    ``Created_users`` stores these as free text, so they arrive in whatever
    shape was written -- ISO from the newer forms, DD/MM/YYYY from the older
    ones. Anything unparseable is None rather than a guess: the caller's
    fallback (show no projected meetings) is safer than a wrong window.
    """
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _s(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def _module_payload(row):
    return {
        "moduleId": _s(row.get("module_catalogue_id")),
        "moduleTitle": _s(row.get("title")) or _s(row.get("module_catalogue_id")),
        "groupName": _s(row.get("group_name")),
        "programmeId": _s(row.get("programme_id")),
        "programmeName": _s(row.get("programme_name")),
        "hours": _hours(row.get("total_otjh")),
        # When this module is delivered, per the curriculum tree. Not editable
        # from the plan — see the module header.
        "startDate": _iso_day(row.get("start_date")),
        "endDate": _iso_day(row.get("end_date")),
        "learnerRosterMode": _s(row.get("learner_roster_mode")) or "inherited",
    }


#: Modules of a deleted programme, and soft-deleted modules, are not offerable.
_LIVE_MODULE_SQL = """
    SELECT module_catalogue_id, title, group_name, programme_id, programme_name,
           total_otjh, start_date, end_date, learner_roster_mode
    FROM curriculum.modules
    WHERE deleted_at IS NULL AND NOT is_programme_deleted
"""


def _programme_modules(programme, programme_id=""):
    """Every module on this programme, whichever group teaches it.

    ``programme_id``, when the learner's placement holds one, decides alone: a
    renamed programme keeps its id, and the old name may by then be another
    programme's.
    """
    programme_id = _s(programme_id)
    if programme_id:
        where, params = "AND programme_id = %s", [programme_id]
    elif programme:
        where, params = "AND (programme_name = %s OR programme_id = %s)", [programme, programme]
    else:
        return []
    return [
        _module_payload(r)
        for r in _rows(
            _LIVE_MODULE_SQL + f"""
              {where}
            ORDER BY group_name, title
            """,
            params,
        )
    ]


def _all_modules():
    """The whole catalogue, every programme.

    The picker offers all of it, so the plan is validated against all of it too
    — a module the client could choose has to be a module the server accepts.
    """
    return [
        _module_payload(r)
        for r in _rows(_LIVE_MODULE_SQL + " ORDER BY programme_name, group_name, title", [])
    ]


def _modules_by_id(module_ids):
    """Live catalogue rows for specific module ids, whatever programme they sit on.

    ``_programme_modules`` is scoped to the learner's own programme, so a module
    authored without a programme (or borrowed from another one) finds no match
    there and would fall back to the plan's saved snapshot -- showing whatever
    hours were stored when the plan was agreed, typically 0h, instead of the
    module's real ``total_otjh``. Looking the ids up directly keeps those rows
    live like every other row on the plan.
    """
    ids = [i for i in dict.fromkeys(_s(i) for i in module_ids) if i]
    if not ids:
        return {}
    rows = _rows(_LIVE_MODULE_SQL + " AND module_catalogue_id = ANY(%s)", [ids])
    return {m["moduleId"]: m for m in (_module_payload(r) for r in rows) if m["moduleId"]}


def _aptem_subject_modules(learner):
    """Live curriculum modules for the subjects an imported learner is enrolled
    in via Aptem, keyed by module id.

    Learners imported from the Aptem snapshot (``enrolment."Created_users"`` rows
    carrying an ``aptem_id``) had their subjects injected through
    ``"Last_audit".group_learners`` and never written to the plan column, so the
    learning-plan modal showed one module -- or none -- while the learner's own
    My Learning page listed every subject they are actually taught.

    Their subjects are group memberships in the audit mirror, matched to the
    authoring catalogue on title: the two systems share no module ids, and the
    import reused the subject name verbatim. Matching is case/whitespace
    insensitive, and a title with no live module is skipped rather than guessed
    at. Programme is deliberately NOT filtered on -- these modules are routinely
    filed under a different programme name ("Marketing Manager" vs "Marketing
    Manager Level 6") than the learner sits on, which is precisely why the
    programme-scoped picker never offered them.

    Never raises: the audit database is a separate, optional connection, and a
    plan must still open when it is unreachable.
    """
    aptem_id = _s(getattr(learner, "aptem_id", ""))
    if not aptem_id:
        return {}
    try:
        with _audit_connection().cursor() as cursor:
            cursor.execute(
                'SELECT g.group_name FROM "Last_audit".group_learners gl '
                'JOIN "Last_audit".groups g ON g.group_id = gl.group_id '
                'JOIN "Last_audit".learners l ON l.learner_id = gl.learner_id '
                'WHERE l.aptem_id = %s',
                [aptem_id],
            )
            titles = [_s(row[0]) for row in cursor.fetchall() if _s(row[0])]
    except Exception:
        logger.warning("Could not read Last_audit subjects for learner %s.", getattr(learner, "pk", "?"))
        return {}
    if not titles:
        return {}
    rows = _rows(
        _LIVE_MODULE_SQL + " AND lower(btrim(title)) = ANY(%s)",
        [[t.strip().lower() for t in dict.fromkeys(titles)]],
    )
    return {m["moduleId"]: m for m in (_module_payload(r) for r in rows) if m["moduleId"]}


def _programmes(modules):
    """The programmes to choose between, each with how many modules it offers.

    Derived from the catalogue rather than queried separately, so the picker can
    never list a programme whose modules it would then fail to show.
    """
    counts, names = {}, {}
    for module in modules:
        key = module["programmeId"] or module["programmeName"]
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
        names.setdefault(key, module["programmeName"] or key)
    return [
        {"programmeId": key, "programmeName": names[key], "moduleCount": counts[key]}
        for key in sorted(counts, key=lambda k: names[k].lower())
    ]


#: The relation the preset lookup reads. Parameterised only so its tiebreak can
#: be tested against a literal VALUES list -- the ordering below is the whole
#: point of the query, and it is not observable from a mocked call.
GROUP_PRESET_SOURCE = 'curriculum.groups'

GROUP_PRESET_SQL = """
SELECT module_ids
FROM {source}
WHERE group_name = %s AND (programme_name = %s OR programme_id = %s)
  AND deleted_at IS NULL
  AND COALESCE(is_programme_deleted, FALSE) = FALSE
ORDER BY
    -- jsonb_array_length() errors on a scalar, and this column is not
    -- guaranteed to hold an array -- the parsing in _group_module_ids allows
    -- for a string too. Anything that is not an array sorts as empty.
    jsonb_array_length(
        CASE WHEN jsonb_typeof(module_ids) = 'array'
             THEN module_ids ELSE '[]'::jsonb END
    ) DESC,
    updated_at DESC NULLS LAST,
    created_at DESC NULLS LAST,
    group_id
LIMIT 1
"""


#: The same choice, made for many (programme, group) pairs in one round trip.
#: DISTINCT ON keeps the first row per pair under an ORDER BY that repeats
#: GROUP_PRESET_SQL's tiebreak exactly, so a pair resolves here to the row it
#: would resolve to on its own. Only worth writing because that tiebreak is now
#: defined: against the old bare LIMIT 1 a batched query would have made a
#: different arbitrary choice from the per-pair one.
GROUP_PRESET_BULK_SQL = """
SELECT DISTINCT ON (p.programme, p.grp)
       p.programme, p.grp, g.module_ids
FROM unnest(%s::text[], %s::text[]) AS p(programme, grp)
JOIN {source} g
  ON g.group_name = p.grp
 AND (g.programme_name = p.programme OR g.programme_id = p.programme)
 AND g.deleted_at IS NULL
 AND COALESCE(g.is_programme_deleted, FALSE) = FALSE
ORDER BY
    p.programme, p.grp,
    jsonb_array_length(
        CASE WHEN jsonb_typeof(g.module_ids) = 'array'
             THEN g.module_ids ELSE '[]'::jsonb END
    ) DESC,
    g.updated_at DESC NULLS LAST,
    g.created_at DESC NULLS LAST,
    g.group_id
"""


def _group_preset_rows(programme, group, source=None):
    """The one group row the preset comes from, or nothing."""
    return _rows(
        GROUP_PRESET_SQL.format(source=source or GROUP_PRESET_SOURCE),
        [group, programme, programme],
    )


def _module_ids_from_column(ids):
    """The module_ids column as a list of trimmed strings."""
    if isinstance(ids, str):
        try:
            ids = json.loads(ids)
        except ValueError:
            return []
    return [_s(i) for i in ids] if isinstance(ids, list) else []


def _manual_roster_module_ids(module_ids):
    """Return module ids that stay attached to a group but never inherit learners.

    ``groups.module_ids`` is also the curriculum tree's structural list, so a
    manual-roster duplicate must remain in it.  Learner plan resolution filters
    the ids here instead of deleting the module from its parent.  The lookup is
    deliberately best-effort for older test/install schemas; an unavailable
    column means the pre-feature inherited behaviour, never a blank plan.
    """
    wanted = [item for item in dict.fromkeys(_s(value) for value in module_ids) if item]
    if not wanted:
        return set()
    table = 'curriculum.modules' if connection.vendor == 'postgresql' else 'modules'
    placeholders = ', '.join(['%s'] * len(wanted))
    try:
        rows = _rows(
            f"SELECT module_catalogue_id FROM {table} "
            f"WHERE module_catalogue_id IN ({placeholders}) "
            "AND COALESCE(learner_roster_mode, 'inherited') = 'manual'",
            wanted,
        )
    except Exception:
        return set()
    return {_s(row.get('module_catalogue_id')) for row in rows if _s(row.get('module_catalogue_id'))}


def group_module_ids_bulk(pairs, source=None):
    """`_group_module_ids` for many (programme, group) pairs, in one query.

    Every pair asked for is present in the result, mapping to [] when no live
    group matches -- the caller can seed a cache with it and never ask again.

    One round trip instead of one per pair. On the curriculum payload build that
    was 46 sequential lookups of a 155-row table, ~3.2 s of latency for ~70 ms of
    work, because the learner counts need a preset per distinct group.
    """
    wanted = [(_s(programme), _s(group)) for programme, group in pairs]
    askable = sorted({pair for pair in wanted if pair[1]})
    found = {}
    if askable:
        rows = _rows(
            GROUP_PRESET_BULK_SQL.format(source=source or GROUP_PRESET_SOURCE),
            [[pair[0] for pair in askable], [pair[1] for pair in askable]],
        )
        found = {
            (_s(row.get('programme')), _s(row.get('grp'))): _module_ids_from_column(row.get('module_ids'))
            for row in rows
        }
        manual_ids = _manual_roster_module_ids(
            [module_id for values in found.values() for module_id in values]
        )
        found = {
            key: [module_id for module_id in values if module_id not in manual_ids]
            for key, values in found.items()
        }
    return {pair: found.get(pair, []) for pair in wanted}


GROUP_PRESET_BY_ID_SQL = """
SELECT module_ids
FROM {source}
WHERE group_id = %s
  AND deleted_at IS NULL
  AND COALESCE(is_programme_deleted, FALSE) = FALSE
"""


def _group_module_ids(programme, group, source=None, group_id=""):
    """The module ids preset on the learner's group.

    ``group_id``, when the learner's placement holds one, names the group
    exactly: no rename can detach it and no namesake in another cohort can
    stand in for it. Everything below describes the name lookup a learner
    without one still falls back to.

    A group is identified here by name and programme, but that pair is not
    unique: the same group name recurs in every cohort of a programme, so
    "G1" on Project Controls L6 matches three rows teaching 9, 1 and 0 modules.
    The learner's cohort would settle it, and this function is never given one —
    all five call sites pass (programme, group) only.

    That left a bare LIMIT 1 with no ORDER BY, which is not a tiebreak but the
    absence of one: Postgres returns whichever row it reaches first, and the
    same learner could inherit nine modules on one request and none on the next.

    So the choice is made explicitly instead:

    * Soft-deleted groups are excluded outright. They were matching, and a
      deleted group has no curriculum to lend anybody.
    * A group that carries modules wins over one that carries none. Neither is
      more "correct" without a cohort, but they fail very differently — an empty
      row reads as "this group teaches nothing" and silently strips every module
      the learner inherits, while a populated one at worst offers a sibling
      cohort's list, which the learner's own saved plan overrides anyway.
    * Then the most recently updated row, then group_id, so the answer is
      stable across requests and across replicas.

    Cohort-blindness itself is unfixed; this only stops it being random.
    """
    group_id = _s(group_id)
    if group_id:
        rows = _rows(GROUP_PRESET_BY_ID_SQL.format(source=source or GROUP_PRESET_SOURCE), [group_id])
    elif group:
        rows = _group_preset_rows(programme, group, source)
    else:
        return []
    if not rows:
        return []
    # Shared with group_module_ids_bulk so the two cannot read the same column
    # differently.
    module_ids = _module_ids_from_column(rows[0].get("module_ids"))
    manual_ids = _manual_roster_module_ids(module_ids)
    return [module_id for module_id in module_ids if module_id not in manual_ids]


def learner_programme_modules(learner):
    """`_programme_modules` for one learner, by their placement's programme id."""
    return _programme_modules(_s(learner.programme), placement_ids(learner)["programme_id"])


def learner_group_module_ids(learner):
    """`_group_module_ids` for one learner, by their placement's group id."""
    return _group_module_ids(
        _s(learner.programme), _s(getattr(learner, "group", "")),
        group_id=placement_ids(learner)["group_id"],
    )


def _saved_modules(learner):
    """The plan already stored on the learner, if any."""
    return stored_training_plan(learner)


def _orphan_module(entry):
    """A saved module with no catalogue match, in the standard shape.

    Covers two cases: a module retired from the programme since the plan was
    saved, and plans written by the earlier wizard, whose entries carry
    weeks/components and no hours. Flagged so the UI can mark it.
    """
    return {
        "moduleId": _s(entry.get("moduleId")),
        "moduleTitle": _s(entry.get("moduleTitle")) or _s(entry.get("moduleId")),
        "groupName": _s(entry.get("groupName")),
        "programmeName": _s(entry.get("programmeName")),
        "hours": float(entry.get("hours") or 0),
        # The saved snapshot is the only window left for a module that is gone.
        "startDate": _iso_day(entry.get("startDate")),
        "endDate": _iso_day(entry.get("endDate")),
        "orphaned": True,
    }


def _meetings(learner):
    """The learner's recurring Reviews, as plan rows with their own OTJ hours.

    A Review that Curriculum has flagged ``counts_towards_otjh`` is off-the-job
    training the same way a module is, so the plan has to show it or its total
    understates the commitment. They are a SEPARATE list rather than extra
    module rows: a module is taught content chosen per learner and editable
    here, while these are generated by the Review Engine from the programme's
    own templates and the learner's start date -- nothing on this screen can
    add or remove one. See curriculum_api.review_otjh.

    Reviews belong to the PROGRAMME, so the programme id is resolved from the
    learner's own ``programme`` and never from the modules on the plan. Those
    are two different things: a plan may hold modules borrowed from another
    programme (the picker allows it deliberately), and a group's modules may
    carry a different programme id again -- resolving from either would give a
    learner whichever programme's Reviews their module list happened to name.

    Returns ``(rows, reason)``. ``reason`` is "" when rows were produced, and
    otherwise says WHY there are none -- the table renders either way, because
    an empty table that explains itself is readable and a vanished one is
    indistinguishable from a broken feature. Configuring a Review's hours is a
    setup step someone is part-way through, so "no Review on this programme
    counts towards OTJH yet" is the single most useful thing this screen can
    say; hiding the table leaves them with nothing to act on.

    Never fatal: a plan that cannot reach Curriculum still shows its modules.
    """
    from coach_api.views import resolve_curriculum_programme_id
    from curriculum_api import review_otjh

    from coach_api.review_sources import source_for_learner, review_profile_for_source
    if source_for_learner(learner, None).kind == "aptem":
        from coach_api.migrated_otjh import planned_meetings
        try:
            profile = review_profile_for_source(learner)
            if profile is None:
                return [], "unavailable"
            start, end = _parse_day(learner.start_date), _parse_day(learner.end_date)
            if not start or not end:
                return [], "no-dates"
            rows = planned_meetings(profile, start, end)
            return rows, "" if rows else "no-otjh-reviews"
        except DatabaseError:
            logger.exception("learning_plan: could not resolve migrated review hours")
            return [], "unavailable"

    try:
        programme_id = resolve_curriculum_programme_id(_s(learner.programme))
    except Exception:
        logger.exception(
            "learning_plan: could not resolve the Curriculum programme for learner %s", learner.pk,
        )
        return [], "unavailable"
    # The learner's programme is not configured in Curriculum, so it has no
    # Review templates to project. Not an error: not every programme runs
    # Reviews, and inventing a programme id here would show another one's.
    if not programme_id:
        return [], "no-programme"

    start_date = _parse_day(learner.start_date)
    # The plan is a lifetime commitment, so the projection runs to the end of
    # the learner's programme. With no end date there is no window to project
    # into, and an arbitrary horizon would invent hours -- so show none.
    end_date = _parse_day(learner.end_date)
    if not start_date or not end_date:
        return [], "no-dates"

    try:
        rows = review_otjh.planned_meetings(
            programme_id,
            learner.pk,
            _s(learner.programme_status),
            start_date,
            start_date,
            end_date,
        )
    except Exception:
        logger.exception("learning_plan: could not resolve review meetings for learner %s", learner.pk)
        return [], "unavailable"

    if rows:
        return rows, ""
    # The programme resolved and the learner has a window, so the only reasons
    # left are that no Review is flagged countsTowardsOtjh with hours on it, or
    # that none is eligible for this learner's status. Distinguishing the two
    # costs a second pass over the same templates and tells the reader which
    # thing to go and fix.
    try:
        has_otjh_template = bool(review_otjh.otjh_template_rows(programme_id))
    except Exception:
        logger.exception(
            "learning_plan: could not inspect review templates for learner %s", learner.pk,
        )
        return [], "unavailable"
    return [], "not-eligible" if has_otjh_template else "no-otjh-reviews"


def _totals(modules, meetings=None):
    """Plan totals. Module hours and meeting hours are reported separately as
    well as combined, so the modal can show each table its own subtotal and
    still print one figure for the whole commitment."""
    module_hours = round(sum(float(m.get("hours") or 0) for m in modules), 2)
    meeting_hours = round(sum(float(m.get("hours") or 0) for m in (meetings or [])), 2)
    return {
        "moduleCount": len(modules),
        "moduleHours": module_hours,
        "meetingCount": len(meetings or []),
        "meetingHours": meeting_hours,
        "totalHours": round(module_hours + meeting_hours, 2),
    }


def _plan_titles(plan):
    """The legacy comma-joined title columns, rebuilt from a structured plan.

    ``Created_users`` carries Modules/Weeks/Components as free text alongside
    the structured jsonb, and ``mappers._legacy_plan_from_csv`` still reads them
    for a learner whose plan predates the structured format. They hold *titles*
    joined with ", ", with "module · week · component" hierarchy -- so they are
    rebuilt in that shape here rather than as ids, which would silently make the
    legacy reader reconstruct a plan of id-shaped module names.
    """
    modules, weeks, components = [], [], []
    for module in plan or []:
        if not isinstance(module, dict):
            continue
        module_title = _s(module.get("moduleTitle"))
        if module_title:
            modules.append(module_title)
        for week in module.get("weeks") or []:
            if not isinstance(week, dict):
                continue
            week_title = _s(week.get("weekTitle"))
            if week_title:
                weeks.append(f"{module_title} · {week_title}" if module_title else week_title)
            for component in week.get("components") or []:
                if not isinstance(component, dict):
                    continue
                component_title = _s(component.get("componentTitle"))
                if not component_title:
                    continue
                parts = [p for p in (module_title, week_title, component_title) if p]
                components.append(" · ".join(parts))
    return ", ".join(modules), ", ".join(weeks), ", ".join(components)


def sync_learning_plan_mirror(source, *, strict=False):
    """Write the learner's assigned plan to the two places that report on it.

    The plan staff edit lives on ``enrolment."Created_users"."Learning_plan"``.
    That is still the record of truth; this copies it outwards so the surfaces
    that already read those tables can see what a learner is assigned:

    * ``"Learner".learners.learning_plan`` -- the mirror every coach, marking
      and reporting surface reads. Previously they had no way to see a learner's
      plan without joining back to enrolment.
    * ``Created_users`` Modules/Weeks/Components -- the legacy title columns,
      kept in step so they cannot describe a plan the learner no longer has.

    Resolved through ``get_training_plan``, which is the same resolver the
    learner's own "My learning" page uses, so the mirror shows what the learner
    sees rather than a second opinion assembled here.

    An unassigned plan writes an empty mirror rather than being skipped: a
    learner whose plan was cleared must not keep a stale copy of it.

    Returns the number of modules mirrored, or 0 if the mirror could not be
    written. Never raises, for the same reason ``advance_learner`` does not: the
    plan itself is already saved by the time this runs, and a failed mirror must
    not turn a successful staff edit into an error. A lagging mirror is
    recoverable -- ``manage.py sync_learning_plans`` rebuilds it from the plan.
    """
    from .active_users import replace_free_courses, replace_training_plan
    from .mappers import get_training_plan
    from .models import LearnerProfile

    if source is None or not getattr(source, "pk", None):
        return 0

    try:
        plan = get_training_plan(source) or []
        modules_csv, weeks_csv, components_csv = _plan_titles(plan)

        # Matched on enrolment_id, never on a LearnerProfile pk held from before
        # a sync_active_user call: that reference goes stale, and updating by it
        # silently missed 367 rows when the coach assignment did the same thing.
        mirrors = LearnerProfile.objects.filter(enrolment_id=source.pk)
        mirrors.update(learning_plan=plan or None)
        # The jsonb above is what reports read; the learner's own "My learning"
        # page reads LearnerProfile.training_plan, which is assembled from the
        # plan_modules/weeks/components child rows. Both have to be written, or
        # the plan is stored and the learner is still shown an empty page.
        profile = mirrors.first()
        if profile is not None:
            replace_training_plan(profile, plan)
            # Free courses ride the same mirror: the enrolment source holds them
            # on Created_users."Free_courses", and the coach/learner side reads
            # the relational learner_free_courses table (no hours/KSBs).
            replace_free_courses(profile, getattr(source, "free_courses", None) or [])
        EnrolmentUser.all_learners.filter(pk=source.pk).update(
            modules=modules_csv,
            weeks=weeks_csv,
            components=components_csv,
        )
    except DatabaseError:
        if strict:
            raise
        logger.exception(
            "sync_learning_plan_mirror: could not mirror the plan for learner %s",
            getattr(source, "pk", None),
        )
        return 0
    return len(plan)


def _serialize(learner):
    programme = _s(learner.programme)
    group = _s(learner.group)

    catalogue = learner_programme_modules(learner)
    by_id = {m["moduleId"]: m for m in catalogue}

    preset_ids = learner_group_module_ids(learner)
    preset = [by_id[i] for i in preset_ids if i in by_id]

    # A saved plan wins; an unsaved learner starts from their group's preset so
    # staff are editing something real rather than an empty list.
    saved = _saved_modules(learner)
    if saved is not None:
        # Re-read hours/titles from the catalogue so an edited module shows its
        # current values. Entries with no catalogue match are kept (the module
        # may have been retired) but normalised to the same shape — plans saved
        # by the older wizard carry weeks/components and no hours at all.
        # Modules on the plan that this programme's catalogue does not list --
        # authored without a programme, or borrowed from another one. They are
        # still live modules, so their hours and title come from master rather
        # than from the plan's saved snapshot.
        off_programme = _modules_by_id(
            _s(m.get("moduleId")) for m in saved if _s(m.get("moduleId")) not in by_id
        )
        plan = [
            {**(by_id.get(_s(m.get("moduleId")))
                or off_programme.get(_s(m.get("moduleId")))
                or _orphan_module(m)),
             **({'assignmentMode': 'explicit'} if m.get('assignmentMode') == 'explicit' else {})}
            for m in saved
        ]
        # A module added to the group after this plan was agreed is still a
        # module the learner is now taught, so it joins their plan rather than
        # waiting for someone to notice and add it by hand. Appended, so the
        # order staff chose is untouched and the new arrival is the last row.
        #
        # Additive only: a module *dropped* from the group is left on the plan.
        # The plan records what was agreed and may already carry progress, so
        # taking a module away stays the deliberate per-learner act that the
        # removal confirmation in the modal guards.
        #
        # An empty plan is left empty. Emptying one is how staff say this
        # learner is taught none of their group's modules, and that reading has
        # to survive: re-filling it from the group would overturn the decision
        # and there would be no way to express it. The two cases are
        # indistinguishable in the stored plan, so the deliberate one wins.
        # Curriculum assignments also mark an explicit set, which must stay
        # limited to those modules even if the group later acquires others.
        planned = {_s(m.get("moduleId")) for m in plan}
        inherited = (
            [i for i in preset_ids if i in by_id and i not in planned]
            if plan and not any(m.get('assignmentMode') == 'explicit' for m in saved) else []
        )
        plan = plan + [dict(by_id[i], inherited=True) for i in inherited]
        # Subjects an Aptem-imported learner is taught that never reached their
        # plan column. Same additive contract as the group preset above: shown
        # so staff can see what the learner actually studies, flagged, and only
        # persisted if someone saves. An explicit curriculum assignment still
        # wins -- that marks a deliberately limited set.
        #
        # Unlike the group preset this also applies to an EMPTY plan: for these
        # learners an empty column means "nothing was ever imported here",
        # not "staff decided this learner is taught nothing".
        if not any(m.get('assignmentMode') == 'explicit' for m in saved):
            planned = {_s(m.get("moduleId")) for m in plan}
            plan = plan + [
                dict(module, fromAptem=True)
                for module_id, module in _aptem_subject_modules(learner).items()
                if module_id not in planned
            ]
    else:
        inherited = []
        # A learner with no saved plan at all still gets their imported
        # subjects, for the same reason -- their group preset is empty because
        # the Aptem import populated the audit mirror, not the curriculum group.
        aptem = _aptem_subject_modules(learner)
        preset_ids_set = {_s(m.get("moduleId")) for m in preset}
        plan = preset + [
            dict(module, fromAptem=True)
            for module_id, module in aptem.items() if module_id not in preset_ids_set
        ]

    chosen = {_s(m.get("moduleId")) for m in plan}
    everything = _all_modules()
    learner_programme_id = next(
        (m["programmeId"] for m in catalogue if m["programmeId"]), "",
    )
    # Reviews follow the learner's programme, not the plan's modules -- so this
    # deliberately does not reuse learner_programme_id above, which is derived
    # from the module catalogue for the picker's benefit.
    meetings, meetings_reason = _meetings(learner)
    return {
        "learner": {
            "id": str(learner.id),
            "name": _s(learner.username),
            "programme": programme,
            "programmeId": learner_programme_id,
            "cohort": _s(learner.cohort),
            "group": group,
            "programmeStatus": _s(learner.programme_status),
        },
        "plan": plan,
        # Free courses assigned to this learner. A pure list of assignment
        # records ([{freeCourseId, courseName, addedAt}]) held in its own column,
        # deliberately outside `plan`/`preset`/`totals` so they never touch hours
        # or KSBs — see EnrolmentUser.free_courses.
        "freeCourses": learner.free_courses if isinstance(learner.free_courses, list) else [],
        "preset": preset,
        # Anything in the catalogue not already on the plan, whichever programme
        # it belongs to. The picker defaults to the learner's own programme and
        # the rest are a dropdown away.
        "available": [m for m in everything if m["moduleId"] not in chosen],
        "programmes": _programmes(everything),
        "saved": saved is not None,
        # How many of the rows above arrived from the group since this plan was
        # last agreed. Non-zero means the plan on screen is ahead of the stored
        # one, so the modal can say so and offer to save it.
        "inheritedCount": len(inherited),
        # Generated by the Review Engine, not editable here -- see _meetings.
        "meetings": meetings,
        # Why `meetings` is empty, so the table can say so rather than vanish.
        "meetingsReason": meetings_reason,
        "totals": _totals(plan, meetings),
    }


@csrf_exempt
# Saving the plan rewrites the learner's modules and can advance their
# programme; only the learner themselves or staff may write it. Reads stay open.
@learner_self_or_staff(kwarg="pk")
def learning_plan(request, pk):
    """GET the learner's plan (with preset + pickable modules), or PATCH to save."""
    try:
        learner = EnrolmentUser.all_learners.get(pk=pk)
    except EnrolmentUser.DoesNotExist:
        return _error("Learner not found.", 404)
    except DatabaseError as exc:
        logger.exception("learning_plan: lookup failed")
        return _error(f"Database error: {exc}", 502)

    if request.method == "GET":
        try:
            return JsonResponse(_serialize(learner))
        except DatabaseError as exc:
            logger.exception("learning_plan: read failed")
            return _error(f"Database error: {exc}", 502)

    if request.method not in ("PATCH", "PUT"):
        return _error("Method not allowed.", 405)

    # ---- save ----
    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        return _error("Request body must be valid JSON.", 400)
    if not isinstance(payload, dict):
        return _error("Request body must be a JSON object.", 400)

    modules = payload.get("modules")
    if not isinstance(modules, list):
        return _error("modules must be a list.", 400)

    # Free courses are optional and independent of the module list: a pure
    # assignment record with no hours/KSBs, stored in their own column. Absent
    # key = leave them untouched; present must be a list.
    has_free_courses = "freeCourses" in payload
    if has_free_courses and not isinstance(payload.get("freeCourses"), list):
        return _error("freeCourses must be a list.", 400)

    programme = _s(learner.programme)
    try:
        catalogue = {m["moduleId"]: m for m in _all_modules()}
    except DatabaseError as exc:
        logger.exception("learning_plan: catalogue lookup failed")
        return _error(f"Database error: {exc}", 502)

    # A module already on this learner's plan that the catalogue no longer
    # offers. `_serialize` serves those rows as orphans so the plan still says
    # what was agreed; refusing to save them would mean the server rejecting its
    # own response -- one retired module anywhere on the plan and the modal
    # could not be saved at all, with nothing to do about it but remove a row
    # recording real teaching.
    retained = {
        _s(m.get("moduleId")): m
        for m in _plan_entries(learner)
        if _s(m.get("moduleId"))
    }

    # Rebuild each entry from the catalogue rather than trusting the client's
    # titles/hours/dates/programme, and reject anything that is not a live
    # module and was not already on the plan. A module from another programme is
    # allowed — it is recorded with its own programme, so the plan says plainly
    # where each module came from.
    resolved, seen = [], set()
    for entry in modules:
        module_id = _s(entry.get("moduleId") if isinstance(entry, dict) else entry)
        if not module_id:
            return _error("Every module needs a moduleId.", 400)
        if module_id in seen:
            continue
        if module_id not in catalogue and module_id not in retained:
            return _error(f"Module '{module_id}' is not in the module catalogue.", 400)
        seen.add(module_id)
        resolved.append(dict(catalogue.get(module_id) or retained[module_id]))

    # Is this save agreeing to the learner's group, or overriding it?
    #
    # `_serialize` re-offers any group module missing from the stored plan as an
    # inherited row, because a module added to the group after the plan was
    # agreed is one the learner is now taught. The stored plan alone cannot tell
    # that case apart from "staff took this module off this learner" -- so a
    # removal saved, and then came straight back on the next read wearing a "New
    # from group" badge, with nothing the officer could do to make it stick.
    #
    # The omission itself is the record. A save that drops a module the group
    # teaches is a decision about this learner, and freezes the set the way a
    # curriculum assignment does; a save that keeps every one of them is an
    # agreement to the group, and lets later additions flow in again. That makes
    # "Reset to group default" the way back: it puts the preset on screen, and
    # saving it clears the freeze.
    try:
        programme_catalogue = {m["moduleId"] for m in learner_programme_modules(learner)}
    except DatabaseError as exc:
        logger.exception("learning_plan: programme catalogue lookup failed")
        return _error(f"Database error: {exc}", 502)
    # Only the preset modules `_serialize` could actually inherit count: an id
    # left in groups.module_ids for a module that no longer exists would
    # otherwise pin every plan to explicit forever.
    inheritable = [
        i for i in learner_group_module_ids(learner)
        if i in programme_catalogue
    ]
    overrides_group = any(i not in seen for i in inheritable)
    if overrides_group:
        resolved = [dict(entry, assignmentMode='explicit') for entry in resolved]

    field = training_plan_field(learner)
    setattr(learner, field, resolved)
    update_fields = [field]
    if has_free_courses:
        learner.free_courses = _normalize_free_courses(payload.get("freeCourses"))
        update_fields.append("free_courses")
    try:
        learner.save(update_fields=update_fields)
    except DatabaseError as exc:
        logger.exception("learning_plan: save failed")
        return _error(f"Database error: {exc}", 502)

    # Mirror outwards to the tables that report on the plan: "Learner".learners
    # for every coach and marking surface, and the legacy Modules/Weeks/
    # Components title columns. Never raises -- the plan is already saved above,
    # and refusing here would lose the staff member's edit over a stale mirror.
    sync_learning_plan_mirror(learner)

    # For a commercial learner the plan is the last thing progression waits for:
    # with a start date already passed, agreeing the plan is what makes them
    # Active. Nothing else on this path would notice, so a learner who was ready
    # the moment their plan was saved sat in Delivery until some unrelated edit
    # happened to run the check. Never raises — see advance_learner.
    advance_learner(learner)

    return JsonResponse(_serialize(learner))


# ---------------------------------------------------------------------------
# The same assignment read from the module's side
# ---------------------------------------------------------------------------
# One module, every learner — the inverse of the plan above, and what the Module
# builder's "Assign learners" picker writes. It is deliberately the *same*
# record: a tick here appends this module to that learner's plan on
# enrolment."Created_users", and an untick removes it. There is no separate
# module-roster table to drift out of step with the plans.
#
#     GET   /learner_api/module-learners/<module_id>/   -> {module, learners, totals}
#     PATCH /learner_api/module-learners/<module_id>/   -> save {learnerIds:[...]}
# ---------------------------------------------------------------------------

#: The columns the picker reports. Only these reach the client -- see
#: _learner_picker_row -- but the rows themselves are read whole; see
#: _picker_learners for why.
_PICKER_FIELDS = (
    "id", "username", "email", "learner_type",
    "programme", "cohort", "group", "programme_status",
    "learning_plan", "training_plan",
)


def _plan_field(learner):
    """Which column holds this learner's plan.

    Apprenticeships use "Learning_plan"; commercial learners use
    "Training_plan". The same rule as
    ``active_users.hydrate_source_training_plan``, so a plan written here lands
    in the column the delivery-side sync reads back.
    """
    return training_plan_field(learner)


def _plan_entries(learner):
    """The plan stored on this learner, from whichever column holds it."""
    return stored_training_plan(learner) or []


def _preset_ids_for(learner, cache):
    """The learner's group preset, cached per group id, else per (programme, group).

    A learner with no saved plan is *shown* their group's preset — see
    ``_serialize`` — so that preset is their effective plan. Assigning one
    module has to keep it: writing only the ticked module would silently drop
    every other module their group teaches.
    """
    group_id = projection_call('assignment_placement', placement_ids, learner)["group_id"]
    key = ("group_id", group_id) if group_id else (_s(learner.programme), _s(learner.group))
    if key not in cache:
        cache[key] = (
            _group_module_ids("", "", group_id=group_id) if group_id else _group_module_ids(*key)
        )
    return cache[key]


def request_preset_cache(learner):
    """The group-preset cache a reader shares across one request, else a fresh one.

    A Dashboard tab resolves the effective plan in several shared helpers; each
    passing ``{}`` re-reads the same (programme, group) preset. ``read_tab``
    installs one dict on its request-scoped source so the preset is read once.
    """
    cache = getattr(learner, '_plan_preset_cache', None)
    return cache if cache is not None else {}


def _effective_plan_ids(learner, preset_cache, *, saved_plan=...):
    """The module ids this learner is currently taught: saved plan, else preset.

    A saved plan also picks up modules added to the learner's group since it was
    agreed, exactly as ``_serialize`` shows them. Both sides read the plan the
    same way, so a module inherited from the group reads as assigned here too
    and the picker cannot contradict the modal.
    """
    saved = stored_training_plan(learner) if saved_plan is ... else saved_plan
    if saved is None:
        return list(_preset_ids_for(learner, preset_cache))
    ids = [_s(entry.get("moduleId")) for entry in saved if _s(entry.get("moduleId"))]
    # An emptied plan stays empty, and is not re-filled from the group. Same
    # rule as _serialize. Explicit curriculum selections also keep their scope.
    if not ids or any(entry.get('assignmentMode') == 'explicit' for entry in saved):
        return ids
    planned = set(ids)
    return ids + [i for i in _preset_ids_for(learner, preset_cache) if i not in planned]


def effective_training_plan(learner, preset_cache=None):
    """Read-only plan snapshot including modules newly inherited from a group.

    The stored plan remains the agreement record and is never changed here.
    Learner-facing reads still need the effective assignment shown by the
    enrolment modal, so append minimal id-bearing entries for newly inherited
    modules. Module Builder remains authoritative for their live titles, weeks
    and components.
    """
    saved = stored_training_plan(learner)
    plan = list(saved if saved is not None else (get_training_plan(learner) or []))
    effective_ids = _effective_plan_ids(
        learner, preset_cache if preset_cache is not None else {}, saved_plan=saved,
    )
    represented = {
        _s(entry.get('moduleId')) for entry in plan
        if isinstance(entry, dict) and _s(entry.get('moduleId'))
    }
    return plan + [
        {'moduleId': module_id}
        for module_id in effective_ids if module_id not in represented
    ]


def _learner_picker_row(learner, module_id, preset_cache):
    saved = _plan_entries(learner)
    plan_ids = _effective_plan_ids(learner, preset_cache)
    assigned = module_id in plan_ids
    return {
        "id": str(learner.id),
        "name": _s(learner.username) or _s(learner.email) or f"Learner {learner.id}",
        "email": _s(learner.email),
        "learnerType": _s(learner.learner_type) or "apprenticeship",
        "programme": _s(learner.programme),
        "cohort": _s(learner.cohort),
        "group": _s(learner.group),
        "programmeStatus": _s(learner.programme_status),
        "assigned": assigned,
        # True when the tick comes from the group preset rather than a saved
        # plan: nobody has agreed this learner's plan yet, so the module is
        # inherited rather than chosen. Named so the picker can say which it is.
        "fromPreset": assigned and not saved,
        "moduleCount": len(plan_ids),
    }


def _module_facts(module):
    """The module being assigned to, for the picker's own header."""
    return {
        "moduleId": module["moduleId"],
        "moduleTitle": module["moduleTitle"],
        "programmeId": module["programmeId"],
        "programmeName": module["programmeName"],
        "groupName": module["groupName"],
        "hours": module["hours"],
        "startDate": module["startDate"],
        "endDate": module["endDate"],
    }


def _module_learners_payload(module, learners, preset_cache):
    rows = [_learner_picker_row(learner, module["moduleId"], preset_cache) for learner in learners]
    return {
        "module": _module_facts(module),
        "learners": rows,
        "totals": {
            "learnerCount": len(rows),
            "assignedCount": sum(1 for row in rows if row["assigned"]),
        },
    }


def _picker_learners():
    """Every learner in enrolment."Created_users", both kinds, by name.

    Whole rows, not ``.only(*_PICKER_FIELDS)``. A save runs advance_learner
    on each learner it touches, and that reads columns the picker itself
    never shows -- the delivery dates, the apprenticeship window. On a
    deferred instance each of those reads is its own query, so narrowing the
    select traded one wide read for dozens of narrow ones, and left the write
    depending on which columns a caller happened to list.
    """
    return list(EnrolmentUser.all_learners.order_by("username", "id"))


@csrf_exempt
# The whole learner directory with names and email addresses, and a write that
# changes what other people are taught. Staff/admin only, reads included.
@staff_only()
def module_learners(request, module_id):
    """GET who is assigned to this module, or PATCH to set the assignment."""
    module_id = _s(module_id)
    if not module_id:
        return _error("A module id is required.", 400)

    try:
        catalogue = {m["moduleId"]: m for m in _all_modules()}
    except DatabaseError as exc:
        logger.exception("module_learners: catalogue lookup failed")
        return _error(f"Database error: {exc}", 502)

    module = catalogue.get(module_id)
    if module is None:
        return _error(f"Module {module_id} is not in the module catalogue.", 404)

    if request.method == "GET":
        try:
            return JsonResponse(_module_learners_payload(module, _picker_learners(), {}))
        except DatabaseError as exc:
            logger.exception("module_learners: read failed")
            return _error(f"Database error: {exc}", 502)

    if request.method not in ("PATCH", "PUT"):
        return _error("Method not allowed.", 405)

    # ---- save ----
    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        return _error("Request body must be valid JSON.", 400)
    if not isinstance(payload, dict):
        return _error("Request body must be a JSON object.", 400)

    learner_ids = payload.get("learnerIds")
    if not isinstance(learner_ids, list):
        return _error("learnerIds must be a list.", 400)
    assigned_ids = {_s(value) for value in learner_ids if _s(value)}

    try:
        learners = _picker_learners()
    except DatabaseError as exc:
        logger.exception("module_learners: read failed")
        return _error(f"Database error: {exc}", 502)

    known_ids = {str(learner.id) for learner in learners}
    unknown = sorted(assigned_ids - known_ids)
    if unknown:
        return _error(f"Learner {unknown[0]} is not in the learner directory.", 400)

    preset_cache = {}
    changed = 0
    for learner in learners:
        should_have = str(learner.id) in assigned_ids
        saved = _plan_entries(learner)
        plan_ids = _effective_plan_ids(learner, preset_cache)
        # A learner whose effective plan already says the right thing is left
        # alone — including one inheriting the module from their group preset.
        # So opening this picker and saving it unchanged writes nothing, and
        # never converts a preset into an agreed plan behind staff's back.
        if should_have == (module_id in plan_ids):
            continue
        if should_have:
            plan_ids = [*plan_ids, module_id]
        else:
            plan_ids = [value for value in plan_ids if value != module_id]

        # Rebuilt from the catalogue, exactly as the learner's own plan save
        # does it, so titles/hours/dates are current rather than whatever was
        # stored last. A module retired since the plan was agreed has no
        # catalogue row left, and keeps its saved snapshot instead of vanishing.
        saved_by_id = {_s(entry.get("moduleId")): entry for entry in saved}
        resolved, seen = [], set()
        for plan_id in plan_ids:
            if not plan_id or plan_id in seen:
                continue
            seen.add(plan_id)
            resolved.append(dict(
                catalogue.get(plan_id)
                or _orphan_module(saved_by_id.get(plan_id) or {"moduleId": plan_id}),
                **({'assignmentMode': 'explicit'} if any(entry.get('assignmentMode') == 'explicit' for entry in saved) else {}),
            ))

        field = _plan_field(learner)
        setattr(learner, field, resolved)
        try:
            learner.save(update_fields=[field])
        except DatabaseError as exc:
            logger.exception("module_learners: save failed for learner %s", learner.id)
            return _error(f"Database error: {exc}", 502)
        # Same mirror as the learner's own plan save: assigning a module from
        # this side changes the same record, so the reporting tables have to
        # follow it here too or they would only track edits made from one of
        # the two directions.
        sync_learning_plan_mirror(learner)
        # Agreeing a plan is the last gate before Active for a commercial
        # learner, the same as on the learner's own plan save. Never raises.
        advance_learner(learner)
        changed += 1

    try:
        return JsonResponse({
            **_module_learners_payload(module, _picker_learners(), {}),
            "changedCount": changed,
        })
    except DatabaseError as exc:
        logger.exception("module_learners: read-back failed")
        return _error(f"Database error: {exc}", 502)


# ---------------------------------------------------------------------------
# Onboarding completion -> Delivery
# ---------------------------------------------------------------------------
# The three onboarding reviews are the gate into delivery. Once every one of them
# is signed by each party it needs, the learner has nothing left to complete, so
# the status moves itself on rather than waiting for someone to remember.
ONBOARDING_REVIEW_TYPES = ("eligibility-review", "workspace", "training-plan", "uln-privacy")

# Statuses a learner can be promoted *out of*. Anything else (Active, Completed,
# Withdrawn, On break) is a deliberate later state, so a late signature must not
# drag the learner backwards into Delivery.
PRE_DELIVERY_STATUSES = {"Fresh user", "Onboarding", "Ready to enrol", ""}


def _fully_signed(review):
    """Whether every party this review needs has signed it."""
    from .review_form import employer_signature_required

    if not review.form_completed:
        return False
    if not _s(review.learner_signature) or not _s(review.admin_signature):
        return False
    if employer_signature_required(review) and not _s(review.employer_signature):
        return False
    return True


def onboarding_complete(learner_kind, learner_id):
    """True when all three onboarding reviews are signed off by every party."""
    from .models import EnrolmentReview

    reviews = EnrolmentReview.objects.filter(
        learner_kind=learner_kind,
        learner_id=learner_id,
        review_type__in=ONBOARDING_REVIEW_TYPES,
    ).exclude(status="cancelled")

    signed = {
        _s(r.review_type) for r in reviews if _fully_signed(r)
    }
    return all(t in signed for t in ONBOARDING_REVIEW_TYPES)


def promote_learner_if_ready(learner_kind, learner_id):
    """Move one learner to Delivery if their onboarding reviews are all signed.

    The signature endpoint calls promote_to_delivery_if_ready() the moment the
    last signature lands, which covers the normal path. This is the safety net
    for everything else: reviews signed before that hook existed, or completed
    through another route. Reading a learner's record re-checks them, so a
    qualifying learner cannot stay stuck at Onboarding.

    Returns the new status when it changed, else None. Never raises.
    """
    try:
        # Commercial learners deliberately have no onboarding-review gate.
        if _s(learner_kind).casefold() == "commercial":
            return None
        if not onboarding_complete(learner_kind, learner_id):
            return None

        learner = EnrolmentUser.all_learners.filter(pk=learner_id).first()
        if learner is None:
            return None
        if _s(learner.programme_status) not in PRE_DELIVERY_STATUSES:
            return None

        learner.programme_status = DELIVERY_PROGRAMME_STATUS
        learner.save(update_fields=["programme_status"])
        return DELIVERY_PROGRAMME_STATUS
    except DatabaseError:
        logger.exception("promote_learner_if_ready: failed for %s/%s", learner_kind, learner_id)
        return None


def promote_to_delivery_if_ready(review):
    """Move a learner to Delivery once their onboarding reviews are all signed.

    Called after a signature is saved. Only ever moves a learner *forward* out of
    the pre-delivery statuses — a learner already Active, Completed or Withdrawn
    is left alone, so a late signature cannot drag them backwards.

    Returns the new status when it changed, else None. Never raises: a failure
    here must not fail the signature that triggered it.
    """
    try:
        # Commercial learners deliberately have no onboarding-review gate.
        if _s(getattr(review, "learner_kind", "")).casefold() == "commercial":
            return None
        if _s(review.review_type) not in ONBOARDING_REVIEW_TYPES:
            return None
        if not onboarding_complete(review.learner_kind, review.learner_id):
            return None

        learner = EnrolmentUser.all_learners.filter(pk=review.learner_id).first()
        if learner is None:
            return None
        if _s(learner.programme_status) not in PRE_DELIVERY_STATUSES:
            return None

        learner.programme_status = DELIVERY_PROGRAMME_STATUS
        learner.save(update_fields=["programme_status"])
        return DELIVERY_PROGRAMME_STATUS
    except DatabaseError:
        logger.exception("promote_to_delivery_if_ready: failed for review %s", review.pk)
        return None
