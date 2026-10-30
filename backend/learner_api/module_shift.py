"""Modules in the same programme that a learner can be shifted onto.

Alternatives span all cohorts and groups in the source module's programme.
Programme identity comes from the module, with cohort/group links as fallbacks
for older catalogue rows. A module without a resolvable programme returns an
empty list and an explanation.

The shift itself writes here rather than through the learning-plan endpoint, for
two reasons. The programme rule is the point of the feature, so it belongs to the
server and not to whichever screen happens to be calling; and a plan may hold
entries whose module has since left the curriculum, which the plan endpoint
rejects wholesale. A shift touches one entry and leaves every other exactly as
it was stored, so a learner part-way through delivery can still be moved.

A learner part-way through a module has progress on its components, and that
progress is the reason the shift is not just a swap of ids: it has to land on
the equivalent components of the module they are moving to. Weeks are matched
by stable id or one exact cleaned title before falling back to delivery order;
components are matched automatically by stable component id first and, for
cloned group modules with new ids, by one exact cleaned title and type. Staff
can manually choose another same-type component in the paired target week.
Progress left unmatched is soft-archived when the learner leaves the source
module. It remains available as historical evidence, but no longer contributes
to the learner's active progress, hours or KSB totals.

A completed component often carries a reflection awaiting a tutor or coach, or
one they have already marked with feedback. That review belongs to the work, not
to the component, so it travels with it: the submission is repointed and its
status, feedback and reviewer are left untouched.

    GET   /learner_api/module-shift/options/?moduleId=<id>
          -> {programme, cohort, groups, modules: [...], reason}
    GET   /learner_api/module-shift/<pk>/progress/?from=<id>&to=<id>
          -> {weeks: [{order, from, to}], suggested: [...], reason}
    PATCH /learner_api/module-shift/<pk>/  {fromModuleId, toModuleId,
                                            progressMappings: [...]}
          -> the learner's plan, as the learning-plan endpoint returns it
"""
import html
import json
import logging
import re
import unicodedata

from django.db import DatabaseError, connections, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from curriculum_api.week_visibility import visible_child_sql, visible_week_sql
from login.permissions import learner_self_or_staff

from .active_users import (
    _component_learning_context,
    _decimal,
    _normalise_weight_class,
    hydrate_training_plan,
    recompute_completed_hours,
    refresh_learner_ksb_snapshot,
    sync_active_user,
)
from .identity import learner_profile_for_source
from .learner_progression import advance_learner
from .learning_plan import (
    _group_module_ids,
    _module_payload,
    _programme_modules,
    _rows,
    _saved_modules,
    sync_learning_plan_mirror,
)
from .mappers import _s, training_plan_field
from .placement_links import placement_ids
from .models import (
    EnrolmentUser,
    LearnerProfile,
    LearnerProgressEntry,
    LearnerProgressKsb,
    LearnerTrainingPlanComponent,
    LearnerTrainingPlanModule,
    LearnerTrainingPlanWeek,
)
from .submission_audit import SUBMISSION_AUDIT_SQL, record_submission_row

logger = logging.getLogger(__name__)

MODULE_COLUMNS = """
    module_catalogue_id, title, group_name, programme_id, programme_name, total_otjh,
    start_date, end_date
"""


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


def _id_list(value):
    """A group's `module_ids`, which is jsonb on newer rows and text on older."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return [_s(i) for i in value if _s(i)] if isinstance(value, list) else []


def _module_row(module_id):
    rows = _rows(
        f"""
        SELECT {MODULE_COLUMNS}, cohort_id, cohort_name, group_id
        FROM curriculum.modules
        WHERE module_catalogue_id = %s
        LIMIT 1
        """,
        [module_id],
    )
    return rows[0] if rows else None


def _all_groups():
    return _rows(
        """
        SELECT group_id, group_name, cohort_id, cohort_name, programme_id, programme_name, module_ids
        FROM curriculum.groups
        """,
        [],
    )


def _cohort_of(module, groups):
    """(cohort_id, cohort_name) for a module, or (None, name) when unlinked.

    Tried in order of how directly the module states it: its own cohort column,
    then the cohort of the group it names, then the cohort of any group whose
    module list includes it.
    """
    module_id = _s(module.get("module_catalogue_id"))
    cohort_id = _s(module.get("cohort_id"))
    if cohort_id:
        return cohort_id, _s(module.get("cohort_name"))

    group_id = _s(module.get("group_id"))
    if group_id:
        group = next((g for g in groups if _s(g.get("group_id")) == group_id), None)
        if group and _s(group.get("cohort_id")):
            return _s(group.get("cohort_id")), _s(group.get("cohort_name"))

    for group in groups:
        if module_id in _id_list(group.get("module_ids")) and _s(group.get("cohort_id")):
            return _s(group.get("cohort_id")), _s(group.get("cohort_name"))

    return None, ""


def _all_cohorts():
    return _rows(
        "SELECT cohort_id, programme_id, programme_name FROM curriculum.cohorts", []
    )


def _programme_of(row, groups, cohorts):
    """Prefer explicit programme identity; retain legacy cohort/group links."""
    programme = (_s(row.get("programme_id")), _s(row.get("programme_name")))
    if any(programme):
        return programme
    cohort_id = _s(row.get("cohort_id"))
    cohort = next(
        (c for c in cohorts if cohort_id and _s(c.get("cohort_id")) == cohort_id),
        None,
    )
    if cohort and (cohort.get("programme_id") or cohort.get("programme_name")):
        return _s(cohort.get("programme_id")), _s(cohort.get("programme_name"))
    group_id = _s(row.get("group_id"))
    module_id = _s(row.get("module_catalogue_id"))
    linked = [g for g in groups if group_id and _s(g.get("group_id")) == group_id]
    linked += [g for g in groups if module_id and module_id in _id_list(g.get("module_ids"))]
    for group in linked:
        programme = _programme_of(group, [], cohorts)
        if any(programme):
            return programme
    return "", ""


def _same_programme(left, right):
    # Stable ids win when both exist; names support legacy name-only rows.
    if left[0] and right[0]:
        return left[0] == right[0]
    return bool(left[1] and right[1] and left[1] == right[1])


def _programme_shift_modules(programme, groups, cohorts):
    """Collect alternatives across cohorts, preserving legacy module links."""
    programme_groups = [
        g for g in groups if _same_programme(programme, _programme_of(g, [], cohorts))
    ]
    cohort_ids = [
        _s(c.get("cohort_id")) for c in cohorts
        if _s(c.get("cohort_id")) and _same_programme(
            programme, (_s(c.get("programme_id")), _s(c.get("programme_name")))
        )
    ]
    group_ids = [_s(g.get("group_id")) for g in programme_groups if _s(g.get("group_id"))]
    listed_ids = sorted({i for g in programme_groups for i in _id_list(g.get("module_ids"))})
    rows = _rows(
        f"""
        SELECT {MODULE_COLUMNS}, cohort_id, cohort_name, group_id
        FROM curriculum.modules
        WHERE deleted_at IS NULL
          AND COALESCE(is_programme_deleted, FALSE) = FALSE
          AND (
               (programme_id = %s AND programme_id <> '')
            OR (programme_name = %s AND programme_name <> '')
            OR cohort_id = ANY(%s::text[])
            OR group_id = ANY(%s::text[])
            OR module_catalogue_id = ANY(%s::text[])
          )
        ORDER BY start_date NULLS LAST, group_name NULLS LAST, title
        """,
        [*programme, cohort_ids, group_ids, listed_ids],
    )
    # An old group list must not override an explicit different programme id.
    return [
        _module_payload(r) for r in rows
        if _same_programme(programme, _programme_of(r, groups, cohorts))
    ], programme_groups


def module_shift_options(request):
    """The modules taught alongside one module, for the shift picker."""
    if request.method != "GET":
        return _error("Method not allowed.", 405)

    module_id = _s(request.GET.get("moduleId"))
    if not module_id:
        return _error("moduleId is required.", 400)

    try:
        module = _module_row(module_id)
        if module is None:
            # A plan can hold a module retired from the catalogue; there is
            # nothing left to walk outwards from.
            return JsonResponse({
                "cohort": {"id": "", "name": ""},
                "programme": {"id": "", "name": ""},
                "groups": [],
                "modules": [],
                "reason": "This module is no longer in the curriculum, so its programme cannot be found.",
            })

        groups = _all_groups()
        cohorts = _all_cohorts()
        programme = _programme_of(module, groups, cohorts)
        if not any(programme):
            return JsonResponse({
                "cohort": {"id": "", "name": ""},
                "programme": {"id": "", "name": ""},
                "groups": [],
                "modules": [],
                "reason": (
                    f"“{_s(module.get('title')) or module_id}” is not linked to a programme yet, "
                    "so there are no programme modules to shift to. Link it in the curriculum first."
                ),
            })

        modules, programme_groups = _programme_shift_modules(programme, groups, cohorts)
        cohort_id, cohort_name = _cohort_of(module, groups)
    except DatabaseError as exc:
        logger.exception("module_shift_options: lookup failed")
        return _error(f"Database error: {exc}", 502)

    return JsonResponse({
        "cohort": {"id": cohort_id or "", "name": cohort_name},
        "programme": {"id": programme[0], "name": programme[1]},
        # Named so the picker can say which groups the alternatives came from.
        "groups": [_s(g.get("group_name")) for g in programme_groups if _s(g.get("group_name"))],
        "modules": modules,
        "reason": "",
    })


# ---------------------------------------------------------------------------
# Progress: pairing a module's weeks with the weeks of the module joined
# ---------------------------------------------------------------------------
def _weeks_with_components(module_id):
    """A module's weeks in delivery order, each with its components in order."""
    rows = _rows(
        """
        SELECT w.id AS week_id, w.week_number, w.title AS week_title,
               c.id AS component_id, c.type, c.title AS component_title,
               c.expected_otjh
        FROM curriculum.weeks w
        LEFT JOIN curriculum.components c ON c.week_id = w.id
          AND """ + visible_child_sql("c") + """
        WHERE w.module_catalogue_id = %s
          AND """ + visible_week_sql("w") + """
        ORDER BY w.week_number, w.display_order, c.display_order, c.title
        """,
        [module_id],
    )
    weeks, by_id = [], {}
    for row in rows:
        week_id = _s(row.get("week_id"))
        week = by_id.get(week_id)
        if week is None:
            number = row.get("week_number")
            week = {
                "weekId": week_id,
                "weekNumber": number,
                "title": _s(row.get("week_title")) or (f"Week {number}" if number else "Week"),
                "components": [],
            }
            by_id[week_id] = week
            weeks.append(week)
        # LEFT JOIN: an empty week still pairs, it just offers nothing.
        if _s(row.get("component_id")):
            week["components"].append({
                "componentId": _s(row.get("component_id")),
                "title": _s(row.get("component_title")) or _s(row.get("type")),
                "type": _s(row.get("type")),
                "otjHours": float(row["expected_otjh"]) if row.get("expected_otjh") is not None else 0.0,
            })
    return weeks


def _quiz_component_slots(profile, component_ids):
    """Include other assigned slots so a shared quiz is never guessed twice."""
    return _rows(
        """
        SELECT c.id AS component_id, c.module_catalogue_id AS module_id,
               m.title AS module_title, c.week_id, w.title AS week_title,
               c.title AS component_title,
               coalesce(
                   (SELECT link.quiz_id::text
                    FROM curriculum.quiz_component_links link
                    WHERE link.component_id = c.id
                    ORDER BY link.id
                    LIMIT 1),
                   c.settings_json->>'linkedQuizId'
               ) AS quiz_id
        FROM curriculum.components c
        JOIN curriculum.modules m ON m.module_catalogue_id = c.module_catalogue_id
        LEFT JOIN curriculum.weeks w ON w.id = c.week_id
          AND w.module_catalogue_id = c.module_catalogue_id
        WHERE c.type = 'quiz'
          AND nullif(
              coalesce(
                  (SELECT link.quiz_id::text
                   FROM curriculum.quiz_component_links link
                   WHERE link.component_id = c.id
                   ORDER BY link.id
                   LIMIT 1),
                  c.settings_json->>'linkedQuizId'
              ),
              ''
          ) IS NOT NULL
          AND """ + visible_child_sql("c") + """
          AND c.module_catalogue_id IN (
              SELECT module_ref FROM "Learner".learner_training_plan_modules
              WHERE learner_id = %s
              UNION
              SELECT module_catalogue_id FROM curriculum.components
              WHERE id = ANY(%s::text[])
          )
        """,
        [profile.pk, list(component_ids)],
    )


def _progress_entries_by_component(profile, component_ids):
    """Resolve unplaced quiz attempts as well as directly linked progress.

    Quiz submissions historically store quiz_ref without component_ref. Only
    one matching slot in the learner's plan may claim such an attempt. Explicit
    module/week references narrow that choice first; older attempts retain the
    module, week and quiz titles shown when submitted, so those conservatively
    disambiguate cloned/shared quiz slots when stable references are absent.
    An existing component reference always wins, including progress already
    moved by an earlier shift.
    """
    if profile is None or not component_ids:
        return {}
    component_ids = set(component_ids)
    grouped = {}
    for entry in LearnerProgressEntry.objects.filter(
        learner=profile, deleted_at__isnull=True,
        component_ref__in=list(component_ids),
    ).exclude(kind="activity_event"):
        grouped.setdefault(_s(entry.component_ref), []).append(entry)

    slots = _quiz_component_slots(profile, component_ids)
    quiz_ids = {_s(slot["quiz_id"]) for slot in slots if slot["component_id"] in component_ids}
    if not quiz_ids:
        return grouped
    attempts = LearnerProgressEntry.objects.filter(
        Q(component_ref__isnull=True) | Q(component_ref=""),
        learner=profile, deleted_at__isnull=True,
        kind="quiz", quiz_ref__in=list(quiz_ids),
    )
    for entry in attempts:
        candidates = [
            slot for slot in slots
            if _s(slot["quiz_id"]) == _s(entry.quiz_ref)
            and (not _s(entry.module_ref) or _s(slot["module_id"]) == _s(entry.module_ref))
            and (not _s(entry.week_ref) or _s(slot["week_id"]) == _s(entry.week_ref))
        ]
        # Before component_ref was written, quiz attempts still retained the
        # visible module/week/quiz titles from the page that submitted them.
        # Use each title only when it actually narrows an ambiguous set; a
        # later curriculum rename must not invalidate an otherwise unique slot.
        for entry_field, slot_field, stable_ref in (
            ("module_title", "module_title", entry.module_ref),
            ("week_title", "week_title", entry.week_ref),
            ("component_title", "component_title", entry.component_ref),
        ):
            value = _component_title_key(getattr(entry, entry_field, ""))
            if len(candidates) <= 1 or stable_ref or not value:
                continue
            narrowed = [
                slot for slot in candidates
                if _component_title_key(slot.get(slot_field)) == value
            ]
            if narrowed:
                candidates = narrowed
        if len(candidates) == 1:
            component_id = _s(candidates[0]["component_id"])
            if component_id in component_ids:
                grouped.setdefault(component_id, []).append(entry)
    return grouped


def _progress_by_component(profile, component_ids):
    """What the learner has recorded against each of these components.

    Keyed on component_ref rather than module_ref: the component is what a
    mapping moves, and older rows can name a component with no module recorded.
    """
    if profile is None or not component_ids:
        return {}
    progress = {}
    grouped = _progress_entries_by_component(profile, component_ids)
    for component_id, entries in grouped.items():
        item = progress.setdefault(component_id, {
            "entries": 0, "kinds": [], "lastAt": "", "otjHours": 0.0, "points": 0,
        })
        for entry in entries:
            item["entries"] += 1
            kind = _s(entry.kind)
            if kind and kind not in item["kinds"]:
                item["kinds"].append(kind)
            at = entry.submitted_at or entry.started_at
            if at and at.isoformat() > item["lastAt"]:
                item["lastAt"] = at.isoformat()
            item["otjHours"] += float(entry.expected_otjh or 0)
            item["points"] += int(entry.points or 0)
    return progress


# The reflection a learner writes when they complete a component, and the
# tutor/coach decision on it. No model declares this table; the reflection and
# coach endpoints both reach it with SQL, so this follows suit.
REFLECTION_TABLE = '"Learner"."learning_reflection_submissions"'


def _reviews_by_component(learner, component_ids):
    """The review state attached to each of these components, if any.

    Matched on the learner's enrolment id, which the reflection rows carry as
    text in `learner_id` and (for newer rows) as `enrolment_id`, and on either
    reference to the component — `activity_id` is what the reflection form
    writes, `component_ref` what the curriculum link resolved to.
    """
    if not component_ids:
        return {}
    try:
        with connections["enrolment"].cursor() as cursor:
            cursor.execute(
                f"""
                SELECT coalesce(nullif(component_ref, ''), activity_id) AS component,
                       status, coach_feedback, reviewed_by, reviewed_at
                FROM {REFLECTION_TABLE}
                WHERE (learner_id = %s OR enrolment_id = %s)
                  AND (activity_id = ANY(%s::text[]) OR component_ref = ANY(%s::text[]))
                """,
                [str(learner.pk), learner.pk, list(component_ids), list(component_ids)],
            )
            rows = cursor.fetchall()
    except DatabaseError:
        # The pairing is still useful without it, and the shift reads the rows
        # again for itself before moving anything.
        logger.warning("Could not read reflection reviews for the shift picker", exc_info=True)
        return {}

    reviews = {}
    for component, status, feedback, reviewed_by, reviewed_at in rows:
        reviews[_s(component)] = {
            "status": _s(status),
            "feedback": _s(feedback),
            "reviewedBy": _s(reviewed_by),
            "reviewedAt": reviewed_at.isoformat() if reviewed_at else "",
        }
    return reviews


def _component_title_key(value):
    """Normalise display-only differences without weakening title identity."""
    value = unicodedata.normalize("NFKC", html.unescape(_s(value)))
    return re.sub(r"\s+", " ", value).strip().casefold()


def _components_match(source, target):
    """Match by component id, else by one exact cleaned title and type."""
    if _s(source.get("componentId")) == _s(target.get("componentId")):
        return True
    source_title = _component_title_key(source.get("title"))
    return (
        _s(source.get("type")) == _s(target.get("type"))
        and bool(source_title)
        and source_title == _component_title_key(target.get("title"))
    )


def _suggested_pair(component, target_week, claimed=None):
    """Return one unambiguous id/title match, never a positional guess."""
    targets = (target_week or {}).get("components") or []
    claimed = claimed or set()
    source_id = _s(component.get("componentId"))
    same_id = [
        target for target in targets
        if _s(target.get("componentId")) == source_id
        and _s(target.get("componentId")) not in claimed
    ]
    if len(same_id) == 1:
        return same_id[0]["componentId"]

    title_matches = [
        target for target in targets
        if _s(target.get("componentId")) not in claimed
        and _components_match(component, target)
    ]
    return title_matches[0]["componentId"] if len(title_matches) == 1 else ""


def _paired_target_week(source_week, target_weeks, position):
    """Find the equivalent destination week without trusting calendar position.

    Cohort copies can schedule the same lesson under different week numbers
    (for example a Friday copy in week 5 and a Thursday copy in week 4). A
    stable id wins, then one exact cleaned lesson title. Week number and list
    position remain conservative fallbacks for older, generically titled plans.
    """
    source_id = _s(source_week.get("weekId"))
    same_id = [
        week for week in target_weeks
        if source_id and _s(week.get("weekId")) == source_id
    ]
    if len(same_id) == 1:
        return same_id[0]

    source_title = _component_title_key(source_week.get("title"))
    same_title = [
        week for week in target_weeks
        if source_title and _component_title_key(week.get("title")) == source_title
    ]
    if len(same_title) == 1:
        return same_title[0]

    source_number = source_week.get("weekNumber")
    same_number = [
        week for week in target_weeks
        if source_number is not None and week.get("weekNumber") == source_number
    ]
    if len(same_number) == 1:
        return same_number[0]

    return target_weeks[position] if position < len(target_weeks) else None


def _progress_pairing(learner, from_id, to_id):
    """The progressed weeks of `from_id`, paired with equivalent `to_id` lessons."""
    source_weeks = _weeks_with_components(from_id)
    target_weeks = _weeks_with_components(to_id)
    if not source_weeks:
        return [], [], "This module has no weeks, so there is no progress to move."

    profile = learner_profile_for_source(learner)
    if profile is None:
        return [], [], "This learner has no delivery record yet, so no progress is recorded."

    component_ids = [c["componentId"] for w in source_weeks for c in w["components"]]
    progress = _progress_by_component(profile, component_ids)
    if not progress:
        return [], [], "This learner has no recorded progress on this module."
    # Shown against each component so it is plain that a pending or marked
    # review moves with the work rather than being left behind.
    reviews = _reviews_by_component(learner, component_ids)

    pairs, suggested, claimed_targets = [], [], set()
    for index, week in enumerate(source_weeks):
        # Only the weeks the learner has actually worked in.
        if not any(c["componentId"] in progress for c in week["components"]):
            continue
        # Cohort copies can move a lesson to another numbered week. Prefer the
        # same stable week/title before using the historical position fallback.
        target = _paired_target_week(week, target_weeks, index)
        pairs.append({
            "order": index + 1,
            "from": {
                **week,
                "components": [
                    {
                        **c,
                        "progress": progress.get(c["componentId"]),
                        "review": reviews.get(c["componentId"]),
                    }
                    for c in week["components"]
                ],
            },
            "to": target,
        })
        for component in week["components"]:
            if component["componentId"] not in progress:
                continue
            match = _suggested_pair(component, target, claimed_targets)
            if match:
                suggested.append({
                    "fromComponentId": component["componentId"],
                    "toComponentId": match,
                })
                claimed_targets.add(match)
    return pairs, suggested, ""


@learner_self_or_staff(kwarg="pk")
def module_shift_progress(request, pk):
    """The week-by-week pairing behind the progress step of a shift."""
    if request.method != "GET":
        return _error("Method not allowed.", 405)

    from_id = _s(request.GET.get("from"))
    to_id = _s(request.GET.get("to"))
    if not from_id or not to_id:
        return _error("from and to module ids are both required.", 400)

    try:
        learner = EnrolmentUser.all_learners.get(pk=pk)
    except EnrolmentUser.DoesNotExist:
        return _error("Learner not found.", 404)
    except DatabaseError as exc:
        logger.exception("module_shift_progress: lookup failed")
        return _error(f"Database error: {exc}", 502)

    try:
        weeks, suggested, reason = _progress_pairing(learner, from_id, to_id)
    except DatabaseError as exc:
        logger.exception("module_shift_progress: pairing failed")
        return _error(f"Database error: {exc}", 502)

    return JsonResponse({
        "weeks": weeks,
        "suggested": suggested,
        "reason": reason,
        "progressedComponents": sum(
            1 for w in weeks for c in w["from"]["components"] if c.get("progress")
        ),
    })


# ---------------------------------------------------------------------------
# Moving the progress
# ---------------------------------------------------------------------------
def _validate_mappings(mappings, from_id, to_id):
    """(pairs, error): the mapping as (from, to) tuples, or why it is refused."""
    if not isinstance(mappings, list):
        return None, "progressMappings must be a list."
    if not mappings:
        return [], ""

    source_weeks = _weeks_with_components(from_id)
    target_weeks = _weeks_with_components(to_id)
    source = {
        c["componentId"]: c for w in source_weeks for c in w["components"]
    }
    target = {
        c["componentId"]: c for w in target_weeks for c in w["components"]
    }
    allowed_targets = {}
    for position, source_week in enumerate(source_weeks):
        paired_week = _paired_target_week(source_week, target_weeks, position)
        paired_ids = {
            component["componentId"]
            for component in (paired_week or {}).get("components", [])
        }
        for component in source_week["components"]:
            allowed_targets.setdefault(component["componentId"], set()).update(paired_ids)

    pairs, claimed = [], {}
    for item in mappings:
        if not isinstance(item, dict):
            return None, "Every progress mapping must be an object."
        source_id = _s(item.get("fromComponentId"))
        target_id = _s(item.get("toComponentId"))
        # A pair with no target is deliberately unmatched. It is archived when
        # the learner leaves the source module rather than remaining active.
        if not source_id or not target_id:
            continue
        if source_id not in source:
            return None, f"Component '{source_id}' is not part of the module being left."
        if target_id not in target:
            return None, f"Component '{target_id}' is not part of the module being joined."
        # Manual choices remain inside the paired week and component type. The
        # server enforces this as this is what the stored progress ends up claiming.
        source_type = source[source_id]["type"]
        target_type = target[target_id]["type"]
        if source_type != target_type:
            return None, (
                f"'{source[source_id]['title']}' is a {source_type or 'component'} and cannot be "
                f"matched to '{target[target_id]['title']}', which is a {target_type or 'component'}."
            )
        # Two components' progress landing on one would credit it twice.
        if claimed.get(target_id, source_id) != source_id:
            return None, "Two components cannot be moved onto the same component."
        if target_id not in allowed_targets.get(source_id, set()):
            return None, (
                f"'{target[target_id]['title']}' is not in the target week paired with "
                f"'{source[source_id]['title']}'."
            )
        claimed[target_id] = source_id
        pairs.append((source_id, target_id))
    return pairs, ""


def _rewrite_ksbs(entry, context):
    """Re-map an entry's KSBs to the ones the component it moved to carries.

    A submission takes its KSBs from the component it was made against, so a
    moved one has to as well, or the learner keeps credit for what the module
    they left mapped to. A target carrying no mappings is the exception: the
    existing links stay, since nothing better is known and losing evidence is
    worse than a stale label.
    """
    mappings = [
        item for item in (context.get("ksbMappings") or [])
        if isinstance(item, dict) and _s(item.get("code") or item.get("ksbCode"))
    ]
    if not mappings:
        return
    LearnerProgressKsb.objects.filter(progress=entry).delete()
    LearnerProgressKsb.objects.bulk_create([
        LearnerProgressKsb(
            progress=entry,
            position=position,
            ksb_code=_s(item.get("code") or item.get("ksbCode")).upper(),
            ksb_description=_s(item.get("description") or item.get("ksbDescription")),
            source_type=_s(item.get("sourceType") or item.get("source_type")),
            source_id=_s(item.get("sourceId") or item.get("source_id")),
            classification=_s(item.get("classification") or item.get("type")),
            weight=_decimal(item.get("weight")),
            weight_class=_normalise_weight_class(
                item.get("weightClass") if "weightClass" in item else item.get("weight_class"),
                item.get("classification") or item.get("type"),
            ),
        )
        for position, item in enumerate(mappings, 1)
    ])


REPOINTED_FIELDS = [
    "legacy_component_ref", "programme_ref", "programme_title",
    "module_ref", "module_title", "week_ref", "week_title",
    "group_ref", "group_title", "cohort_ref", "cohort_title",
    "component_ref", "component_title", "component_type", "points",
    "component_link_status", "component_link_source",
]


def _move_review(learner, source_id, target_id, context):
    """Move the reflection and its tutor/coach decision onto the new component.

    (moved, kept). The decision itself — status, feedback, who marked it and
    when — is deliberately not touched: a tutor has read this learner's work and
    said something about it, and that stands whichever component now carries it.
    Only where the submission points is rewritten.

    A learner who already has a submission of the same kind on the target keeps
    it: the table holds one per learner and activity, and overwriting either
    review would throw away a real one.
    """
    moved = kept = 0
    with connections["enrolment"].cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, learner_kind, learner_id, activity_type
            FROM {REFLECTION_TABLE}
            WHERE (learner_id = %s OR enrolment_id = %s)
              AND (activity_id = %s OR component_ref = %s)
            """,
            [str(learner.pk), learner.pk, source_id, source_id],
        )
        rows = cursor.fetchall()

        for row_id, learner_kind, learner_id, activity_type in rows:
            cursor.execute(
                f"""
                SELECT 1 FROM {REFLECTION_TABLE}
                WHERE learner_kind = %s AND learner_id = %s
                  AND activity_type = %s AND activity_id = %s
                LIMIT 1
                """,
                [learner_kind, learner_id, activity_type, target_id],
            )
            if cursor.fetchone():
                kept += 1
                continue

            cursor.execute(
                f"""
                UPDATE {REFLECTION_TABLE}
                SET activity_id = %s,
                    component_ref = %s,
                    activity_title = coalesce(nullif(%s, ''), activity_title),
                    module_ref = coalesce(nullif(%s, ''), module_ref),
                    module_title = coalesce(nullif(%s, ''), module_title),
                    week_ref = coalesce(nullif(%s, ''), week_ref),
                    week_title = coalesce(nullif(%s, ''), week_title),
                    group_ref = coalesce(nullif(%s, ''), group_ref),
                    cohort_ref = coalesce(nullif(%s, ''), cohort_ref),
                    programme_ref = coalesce(nullif(%s, ''), programme_ref)
                WHERE id = %s
                RETURNING {SUBMISSION_AUDIT_SQL}
                """,
                [
                    target_id, target_id,
                    _s(context.get("componentTitle")),
                    _s(context.get("moduleId")), _s(context.get("moduleTitle")),
                    _s(context.get("weekId")), _s(context.get("weekTitle")),
                    _s(context.get("groupId")), _s(context.get("cohortId")),
                    _s(context.get("programmeId")),
                    row_id,
                ],
            )
            # A shift re-points a learner's submitted work at a different
            # component. The work itself is untouched, but where it sits is
            # not, and "why does this submission belong to another module now"
            # is precisely a question for the trail.
            record_submission_row(cursor.fetchone())
            moved += 1
    return moved, kept


def _repoint_progress(learner, profile, pairs, entries_by_component=None):
    """Move the learner's progress from each source component to its target.

    Only where the activity sits is rewritten — module, week, component, and the
    target component's points. What the learner did is left exactly as it was:
    recorded/fallback hours, grades, attempts, scores, timestamps and quiz
    answers describe an attempt that really happened, and rewriting those to
    match another module's quiz would be inventing a record. The reflection and
    its tutor decision move with it, for the same reason.
    """
    counts = {"entries": 0, "entryIds": [], "reviews": 0, "reviewsKept": 0}
    if entries_by_component is None:
        entries_by_component = _progress_entries_by_component(profile, [source for source, _ in pairs])
    for source_id, target_id in pairs:
        context = _component_learning_context(target_id)
        if not context:
            continue
        entries = entries_by_component.get(source_id, [])
        for entry in entries:
            # What it first pointed at, kept once: the column exists so a
            # repointed row never loses its original reference.
            if not _s(entry.legacy_component_ref):
                entry.legacy_component_ref = entry.component_ref or source_id
            entry.programme_ref = _s(context.get("programmeId")) or entry.programme_ref
            entry.programme_title = _s(context.get("programme")) or entry.programme_title
            entry.module_ref = _s(context.get("moduleId")) or entry.module_ref
            entry.module_title = _s(context.get("moduleTitle")) or entry.module_title
            entry.week_ref = _s(context.get("weekId")) or entry.week_ref
            entry.week_title = _s(context.get("weekTitle")) or entry.week_title
            entry.group_ref = _s(context.get("groupId")) or entry.group_ref
            entry.group_title = _s(context.get("group")) or entry.group_title
            entry.cohort_ref = _s(context.get("cohortId")) or entry.cohort_ref
            entry.cohort_title = _s(context.get("cohort")) or entry.cohort_title
            entry.component_ref = target_id
            entry.component_title = _s(context.get("componentTitle")) or entry.component_title
            entry.component_type = _s(context.get("componentType")) or entry.component_type
            if context.get("points") not in (None, ""):
                entry.points = context.get("points")
            entry.component_link_status = "resolved_to_current_component"
            entry.component_link_source = "direct"
            entry.save(update_fields=REPOINTED_FIELDS)
            _rewrite_ksbs(entry, context)
            counts["entries"] += 1
            counts["entryIds"].append(getattr(entry, "pk", getattr(entry, "id", None)))
        moved_reviews, kept_reviews = _move_review(learner, source_id, target_id, context)
        counts["reviews"] += moved_reviews
        counts["reviewsKept"] += kept_reviews
    return counts


def _archive_unmatched_progress(
    profile, from_id, source_component_ids, resolved_legacy_entry_ids=(), moved_entry_ids=(),
):
    """Soft-archive active source progress that was not moved to the target.

    ``module_ref`` is authoritative for normal rows. Older rows can lack it, so
    a component reference is used only when the module reference is blank. The
    moved ids are excluded because cloned modules can deliberately share a
    stable component id.
    """
    if profile is None:
        return 0

    source_scope = Q(module_ref=from_id)
    if source_component_ids:
        missing_module = Q(module_ref__isnull=True) | Q(module_ref="")
        source_scope |= missing_module & Q(component_ref__in=list(source_component_ids))
    if resolved_legacy_entry_ids:
        # Some old quiz attempts carry only quiz_ref. The pre-shift resolver can
        # still prove which unique source slot owns them, so archive those ids
        # explicitly rather than leaving their hours active.
        source_scope |= Q(pk__in=list(resolved_legacy_entry_ids))

    entries = (
        LearnerProgressEntry.objects
        .filter(learner=profile, deleted_at__isnull=True)
        .filter(source_scope)
    )
    moved_entry_ids = [entry_id for entry_id in moved_entry_ids if entry_id is not None]
    if moved_entry_ids:
        entries = entries.exclude(pk__in=moved_entry_ids)
    return entries.update(deleted_at=timezone.now())


def _base_plan(learner):
    """The plan a shift starts from: what is saved, else the group's preset.

    An unsaved learner is shown their group's preset everywhere else, so a shift
    has to move them within that same list — otherwise the first shift for a
    learner whose plan was never saved would be rejected as "not on the plan".
    Materialising it here means that shift saves the preset along with the move,
    which is exactly what agreeing to a plan does.
    """
    saved = _saved_modules(learner)
    if saved:
        return saved

    # By the ids the learner's placement holds; see placement_links.
    ids = placement_ids(learner)
    programme = _s(learner.programme)
    catalogue = {m["moduleId"]: m for m in _programme_modules(programme, ids["programme_id"])}
    preset_ids = _group_module_ids(programme, _s(learner.group), group_id=ids["group_id"])
    return [catalogue[i] for i in preset_ids if i in catalogue]


def _shifted_plan(learner, plan, target, from_id):
    """Replace one module and keep the old group from inheriting it again."""
    shifted = [
        target if _s(module.get("moduleId")) == from_id else module
        for module in plan
    ]
    preset_ids = set(_group_module_ids(
        _s(learner.programme), _s(learner.group), group_id=placement_ids(learner)["group_id"],
    ))
    if from_id in preset_ids:
        # The omission is deliberate: without an explicit assignment the
        # effective-plan reader immediately adds the source module back from
        # the learner's unchanged group preset.
        shifted = [dict(module, assignmentMode="explicit") for module in shifted]
    return shifted


def _replace_mirrored_module(profile, from_id, target):
    """Replace one module in the relational learner-plan mirror.

    Rebuilding every module, week and component made a one-module shift take
    more than a minute for a large learner plan.  The source JSON remains the
    record of truth; this updates only the mirror branch whose id changed.
    ``False`` asks the caller to use the full repair path when an old/incomplete
    mirror cannot be updated safely.
    """
    matches = list(LearnerTrainingPlanModule.objects.filter(
        learner=profile, module_ref=from_id,
    )[:2])
    if len(matches) != 1:
        return False

    module_row = matches[0]
    module_row.module_ref = _s(target.get("moduleId")) or None
    module_row.module_title = _s(target.get("moduleTitle"))
    module_row.save(update_fields=["module_ref", "module_title"])
    module_row.weeks.all().delete()

    weeks = [week for week in target.get("weeks") or [] if isinstance(week, dict)]
    week_rows = LearnerTrainingPlanWeek.objects.bulk_create([
        LearnerTrainingPlanWeek(
            plan_module=module_row,
            position=position,
            week_ref=_s(week.get("weekId")) or None,
            week_title=_s(week.get("weekTitle")),
        )
        for position, week in enumerate(weeks, 1)
    ])
    if any(row.pk is None for row in week_rows):
        week_rows = list(module_row.weeks.all().order_by("position", "id"))

    LearnerTrainingPlanComponent.objects.bulk_create([
        LearnerTrainingPlanComponent(
            plan_week=week_row,
            position=position,
            component_ref=_s(component.get("componentId")) or None,
            component_title=_s(component.get("componentTitle")),
        )
        for week_row, week in zip(week_rows, weeks)
        for position, component in enumerate(week.get("components") or [], 1)
        if isinstance(component, dict)
    ], batch_size=1000)
    return True


def _replace_json_plan_module(model, pk, field_name, existing_plan, from_id, target, explicit):
    """Replace one item in a stored jsonb plan without uploading the whole plan."""
    if not isinstance(existing_plan, list) or not any(
        isinstance(module, dict) and _s(module.get("moduleId")) == from_id
        for module in existing_plan
    ):
        return False

    connection = connections["enrolment"]
    table = connection.ops.quote_name(model._meta.db_table)
    column = connection.ops.quote_name(model._meta.get_field(field_name).column)
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE {table} AS learner
            SET {column} = rebuilt.plan
            FROM (
                SELECT coalesce(jsonb_agg(
                    CASE
                        WHEN item.module ->> 'moduleId' = %s THEN %s::jsonb
                        WHEN %s THEN item.module || jsonb_build_object('assignmentMode', 'explicit')
                        ELSE item.module
                    END
                    ORDER BY item.position
                ), '[]'::jsonb) AS plan
                FROM jsonb_array_elements(coalesce(
                    (SELECT source.{column} FROM {table} AS source WHERE source.id = %s),
                    '[]'::jsonb
                )) WITH ORDINALITY AS item(module, position)
            ) AS rebuilt
            WHERE learner.id = %s
            """,
            [from_id, json.dumps(target), explicit, pk, pk],
        )
        return cursor.rowcount == 1


def _sync_legacy_titles_from_json(learner, plan_field):
    """Rebuild legacy title columns inside Postgres, without uploading them."""
    connection = connections["enrolment"]
    table = connection.ops.quote_name(EnrolmentUser._meta.db_table)
    plan_column = connection.ops.quote_name(
        EnrolmentUser._meta.get_field(plan_field).column,
    )
    modules_column = connection.ops.quote_name(EnrolmentUser._meta.get_field("modules").column)
    weeks_column = connection.ops.quote_name(EnrolmentUser._meta.get_field("weeks").column)
    components_column = connection.ops.quote_name(EnrolmentUser._meta.get_field("components").column)
    array_or_empty = (
        "CASE WHEN jsonb_typeof({value}) = 'array' THEN {value} ELSE '[]'::jsonb END"
    )
    weeks_json = array_or_empty.format(value="module.item -> 'weeks'")
    components_json = array_or_empty.format(value="week.item -> 'components'")
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE {table} AS learner
            SET {modules_column} = coalesce((
                    SELECT string_agg(module.item ->> 'moduleTitle', ', ' ORDER BY module.position)
                    FROM jsonb_array_elements(coalesce(learner.{plan_column}, '[]'::jsonb))
                         WITH ORDINALITY AS module(item, position)
                    WHERE nullif(module.item ->> 'moduleTitle', '') IS NOT NULL
                ), ''),
                {weeks_column} = coalesce((
                    SELECT string_agg(
                        concat_ws(' · ', module.item ->> 'moduleTitle', week.item ->> 'weekTitle'),
                        ', ' ORDER BY module.position, week.position
                    )
                    FROM jsonb_array_elements(coalesce(learner.{plan_column}, '[]'::jsonb))
                         WITH ORDINALITY AS module(item, position)
                    CROSS JOIN LATERAL jsonb_array_elements({weeks_json})
                         WITH ORDINALITY AS week(item, position)
                    WHERE nullif(week.item ->> 'weekTitle', '') IS NOT NULL
                ), ''),
                {components_column} = coalesce((
                    SELECT string_agg(
                        concat_ws(' · ', module.item ->> 'moduleTitle',
                                  week.item ->> 'weekTitle', component.item ->> 'componentTitle'),
                        ', ' ORDER BY module.position, week.position, component.position
                    )
                    FROM jsonb_array_elements(coalesce(learner.{plan_column}, '[]'::jsonb))
                         WITH ORDINALITY AS module(item, position)
                    CROSS JOIN LATERAL jsonb_array_elements({weeks_json})
                         WITH ORDINALITY AS week(item, position)
                    CROSS JOIN LATERAL jsonb_array_elements({components_json})
                         WITH ORDINALITY AS component(item, position)
                    WHERE nullif(component.item ->> 'componentTitle', '') IS NOT NULL
                ), '')
            WHERE learner.id = %s
            """,
            [learner.pk],
        )


def _save_shifted_plan(learner, shifted_plan, from_id="", to_id=""):
    """Store a shift and update only the changed learner-plan mirror branch."""
    plan_field = training_plan_field(learner)
    existing_plan = getattr(learner, plan_field, None)
    setattr(learner, plan_field, shifted_plan)
    target = next(
        (module for module in shifted_plan if _s(module.get("moduleId")) == to_id),
        None,
    )
    explicit = bool(shifted_plan) and all(
        not isinstance(module, dict) or module.get("assignmentMode") == "explicit"
        for module in shifted_plan
    )
    if target is None or not _replace_json_plan_module(
        EnrolmentUser, learner.pk, plan_field, existing_plan, from_id, target, explicit,
    ):
        learner.save(update_fields=[plan_field])

    profile = learner_profile_for_source(learner)

    mirrored = False
    if profile is not None and target is not None:
        if not _replace_json_plan_module(
            LearnerProfile, profile.pk, "learning_plan", profile.learning_plan,
            from_id, target, explicit,
        ):
            LearnerProfile.objects.filter(pk=profile.pk).update(
                learning_plan=shifted_plan or None,
            )
        profile.learning_plan = shifted_plan or None
        mirrored = _replace_mirrored_module(profile, from_id, target)

    _sync_legacy_titles_from_json(learner, plan_field)
    if profile is not None and not mirrored:
        # Legacy/incomplete mirrors still get the established full repair.
        sync_learning_plan_mirror(learner, strict=True)
        mirrored = True
    return plan_field, profile, mirrored


@csrf_exempt
# Shifting a module rewrites the learner's plan and repoints their progress
# entries; only the learner themselves or staff may write it.
@learner_self_or_staff(kwarg="pk")
def module_shift(request, pk):
    """Move a learner from one module to another taught to the same programme."""
    if request.method not in ("PATCH", "POST"):
        return _error("Method not allowed.", 405)

    try:
        learner = EnrolmentUser.all_learners.get(pk=pk)
    except EnrolmentUser.DoesNotExist:
        return _error("Learner not found.", 404)
    except DatabaseError as exc:
        logger.exception("module_shift: lookup failed")
        return _error(f"Database error: {exc}", 502)

    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        return _error("Request body must be valid JSON.", 400)
    if not isinstance(payload, dict):
        return _error("Request body must be a JSON object.", 400)

    from_id = _s(payload.get("fromModuleId"))
    to_id = _s(payload.get("toModuleId"))
    if not from_id or not to_id:
        return _error("fromModuleId and toModuleId are both required.", 400)
    if from_id == to_id:
        return _error("Pick a different module to shift to.", 400)

    try:
        plan = _base_plan(learner)
        if not any(_s(m.get("moduleId")) == from_id for m in plan):
            return _error(f"Module '{from_id}' is not on this learner's plan.", 400)
        if any(_s(m.get("moduleId")) == to_id for m in plan):
            return _error("That module is already on this learner's plan.", 400)

        # The same walk the picker offered, re-run here: the programme rule is the
        # rule, so it is checked server-side rather than trusted from the client.
        module = _module_row(from_id)
        if module is None:
            return _error(
                f"Module '{from_id}' is no longer in the curriculum, so its programme cannot be found.",
                400,
            )
        groups = _all_groups()
        cohorts = _all_cohorts()
        programme = _programme_of(module, groups, cohorts)
        if not any(programme):
            return _error(
                f"'{_s(module.get('title')) or from_id}' is not linked to a programme, "
                "so there is nothing to shift within.",
                400,
            )
        options, _programme_groups = _programme_shift_modules(programme, groups, cohorts)
        target = next((m for m in options if m["moduleId"] == to_id), None)
        if target is None:
            return _error(
                f"That module is not taught to {programme[1] or programme[0] or 'this programme'}.",
                400,
            )

        # Which progress moves with the learner, and onto what. Anything not
        # mapped is archived after the plan changes so it cannot remain in the
        # learner's active completion, hours or KSB totals.
        pairs, mapping_error = _validate_mappings(payload.get("progressMappings") or [], from_id, to_id)
        if mapping_error:
            return _error(mapping_error, 400)

        profile = learner_profile_for_source(learner)
        if pairs and profile is None:
            return _error(
                "This learner has no delivery record, so there is no progress to move.",
                400,
            )

        # The plan and the progress move together: a learner left on the new
        # module with their progress still on the old one, or the reverse, is
        # worse than the shift not happening.
        with transaction.atomic(using="enrolment"):
            source_component_ids = {
                component["componentId"]
                for week in _weeks_with_components(from_id)
                for component in week["components"]
            } if profile is not None else set()
            # Resolve against the old plan before its mirror is replaced. The
            # destination's quiz slots must not make an old attempt ambiguous.
            entries_by_component = _progress_entries_by_component(
                profile, source_component_ids,
            ) if profile is not None and source_component_ids else {}
            resolved_legacy_entry_ids = {
                getattr(entry, "pk", getattr(entry, "id", None))
                for entries in entries_by_component.values()
                for entry in entries
                if not _s(entry.module_ref)
            }
            resolved_legacy_entry_ids.discard(None)
            # One entry replaced, the rest kept exactly as stored — including any
            # whose module has left the curriculum, which is not this shift's
            # business to rewrite or drop.
            hydrated_target = hydrate_training_plan([target], strict=True)
            target = hydrated_target[0] if hydrated_target else target
            shifted_plan = _shifted_plan(learner, plan, target, from_id)
            _plan_field, mirrored_profile, mirror_updated = _save_shifted_plan(
                learner, shifted_plan, from_id, to_id,
            )
            counts = (
                _repoint_progress(learner, profile, pairs, entries_by_component) if pairs
                else {"entries": 0, "entryIds": [], "reviews": 0, "reviewsKept": 0}
            )
            archived = _archive_unmatched_progress(
                profile, from_id, source_component_ids,
                resolved_legacy_entry_ids, counts["entryIds"],
            )

        moved = counts["entries"]
        if moved or archived:
            # The component's hours may differ from the one left behind, and the
            # learner's own workspace reads the materialised plan rather than
            # this list — both are derived, so both are refreshed here.
            recompute_completed_hours(profile.id)
        # An already-active learner cannot advance again.  Running the full
        # progression workflow anyway dominated this endpoint for large plans
        # (about 70 seconds in the reported case), while changing nothing.
        # A pre-delivery learner can still become ready through their first
        # saved plan, so preserve the check for that state only.
        if _s(learner.programme_status).lower() != "active":
            advance_learner(learner)
        if _s(learner.programme_status).lower() == "active":
            if mirror_updated and mirrored_profile is not None:
                refresh_learner_ksb_snapshot(
                    mirrored_profile, learner, training_plan=shifted_plan,
                )
            else:
                sync_active_user(learner)

        # The modal only needs the write result; its parent refetches the learner
        # after closing.  Serialising the entire learning-plan catalogue here
        # repeated several expensive curriculum lookups and added roughly a
        # minute after the shift had already succeeded.
        return JsonResponse({
            "progressMoved": moved,
            "progressArchived": archived,
            "reviewsMoved": counts["reviews"],
            # A review left behind because the target already had one of its own.
            "reviewsKept": counts["reviewsKept"],
        })
    except DatabaseError as exc:
        logger.exception("module_shift: save failed")
        return _error(f"Database error: {exc}", 502)
