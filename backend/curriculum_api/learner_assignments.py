"""Cohort and module learner assignment using the existing learner plan."""
import json
import logging

from django.db import DatabaseError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from login.permissions import staff_only
from learner_api import learning_plan as plans
from learner_api.active_users import hydrate_training_plan
from learner_api.mappers import get_training_plan, stored_training_plan, training_plan_field
from learner_api.models import EnrolmentUser, LearnerProfile

logger = logging.getLogger(__name__)


def _target(scope, identifier):
    from . import views

    if scope == 'cohort':
        row = views.resolve_cohort_row(identifier)
        if not row or views.curriculum_row_effectively_deleted(row):
            return None
        cohort_id = views.clean_str(row.get('cohort_id'))
        groups = views.authoring_fetch_all(views.GROUPS_TABLE, 'cohort_id = %s', [cohort_id])
        group_ids = {views.clean_str(group.get('group_id')) for group in groups}
        modules = [
            module for module in views.authoring_fetch_all(views.AUTHORING_MODULES_TABLE)
            if not views.curriculum_row_effectively_deleted(module)
            and (views.clean_str(module.get('cohort_id')) == cohort_id
                 or (views.clean_str(module.get('group_id')) in group_ids
                     and views.clean_str(module.get('group_id'))))
        ]
        return {
            'id': cohort_id, 'name': views.clean_str(row.get('cohort_name')), 'scope': scope,
            'programmeName': views.clean_str(row.get('programme_name')),
            'programmeId': views.clean_str(row.get('programme_id')),
            'moduleIds': list(dict.fromkeys(views.clean_str(m.get('module_catalogue_id')) for m in modules)),
            'groupNames': {views.clean_str(group.get('group_name')) for group in groups},
        }
    # Assignment requests normally carry the stored MOD-* id. Resolve that
    # form with the indexed existence check first; legacy aliases still fall
    # through to the broad resolver.
    module_id = views.resolve_stored_module_catalogue_id(identifier) or identifier
    rows = views.authoring_fetch_all(views.AUTHORING_MODULES_TABLE, 'module_catalogue_id = %s', [module_id])
    if not rows or views.curriculum_row_effectively_deleted(rows[0]):
        return None
    row = rows[0]
    return {
        'id': module_id, 'name': views.clean_str(row.get('title')), 'scope': scope,
        'programmeName': views.clean_str(row.get('programme_name')),
        'programmeId': views.clean_str(row.get('programme_id')), 'moduleIds': [module_id],
    }


def _same_cohort(learner, target):
    return (plans._s(learner.cohort) == target['name']
            and plans._s(learner.programme) in {target['programmeName'], target['programmeId']})


def _assigned(learner, target, cache):
    ids = set(plans._effective_plan_ids(learner, cache))
    return set(target['moduleIds']).issubset(ids) and (target['scope'] == 'module' or _same_cohort(learner, target))


def _payload(target, learners):
    # `learners` deliberately does not select learning_plan/training_plan --
    # shipping those for every learner (~11 MB across ~460 rows) is what made
    # this directory time out over a slow link. assignment_directory_facts
    # gets the same assigned/moduleCount answer from Postgres directly, and
    # only the rare ambiguous case (a plan stored as a JSON string) needs the
    # plan columns read back in Python, for just those learners.
    from .learner_assignment_counts_sql import assignment_directory_facts

    target_ids = set(target['moduleIds'])
    facts, ambiguous_ids = assignment_directory_facts(target['moduleIds'])
    plan_backfill = {}
    if ambiguous_ids:
        plan_backfill = dict(EnrolmentUser.all_learners.filter(pk__in=ambiguous_ids)
                              .values_list('pk', 'learning_plan', 'training_plan'))
    cache = {}
    rows = []
    for learner in learners:
        if learner.pk in plan_backfill:
            learner.learning_plan, learner.training_plan = plan_backfill[learner.pk]
            assigned = _assigned(learner, target, cache)
            module_count = len(plans._effective_plan_ids(learner, cache))
        else:
            fact = facts.get(learner.pk)
            assigned = bool(fact) and target_ids.issubset(fact['effective']) and (
                target['scope'] == 'module' or _same_cohort(learner, target))
            module_count = fact['moduleCount'] if fact else 0
        rows.append({
            'id': str(learner.pk), 'name': plans._s(learner.username) or plans._s(learner.email),
            'email': plans._s(learner.email), 'programme': plans._s(learner.programme),
            'company': plans._s(learner.employer) or plans._s(learner.organization),
            'cohort': plans._s(learner.cohort), 'group': plans._s(learner.group),
            'programmeStatus': plans._s(learner.programme_status),
            'learnerType': plans._s(learner.learner_type) or 'apprenticeship',
            'assigned': assigned, 'moduleCount': module_count,
        })
    return {
        'target': {**{key: target[key] for key in ('id', 'name', 'scope', 'programmeName')},
                   'moduleCount': len(target['moduleIds'])},
        'learners': rows,
        'totals': {'learnerCount': len(rows), 'assignedCount': sum(row['assigned'] for row in rows)},
    }


def _assign(learner, target, catalogue, cache):
    if _assigned(learner, target, cache):
        return False
    # Preserve detailed and retired entries, as well as the modules this learner
    # already inherits. Only the target contributes new modules.
    saved = stored_training_plan(learner)
    existing = list(saved if saved is not None else get_training_plan(learner) or [])
    existing_ids = {plans._s(entry.get('moduleId')) for entry in existing}
    for module_id in [*plans._effective_plan_ids(learner, cache), *target['moduleIds']]:
        if module_id and module_id not in existing_ids:
            existing.append(dict(catalogue.get(module_id) or {'moduleId': module_id}))
            existing_ids.add(module_id)
    # Freeze this explicitly selected set. A later plan read must not turn one
    # module added to an empty plan into the learner's entire group preset.
    existing = [dict(entry, assignmentMode='explicit') for entry in existing]
    # Expand the target's content once for the entire bulk operation.
    if '_assignmentModules' not in cache:
        additions = hydrate_training_plan([catalogue[module_id] for module_id in target['moduleIds']], strict=True)
        cache['_assignmentModules'] = {entry['moduleId']: entry for entry in additions}
    hydrated = cache['_assignmentModules']
    plan = [{**entry, **hydrated.get(entry.get('moduleId'), {})} for entry in existing]
    field = training_plan_field(learner)
    setattr(learner, field, plan)
    fields = [field]
    if target['scope'] == 'cohort':
        keep_group = _same_cohort(learner, target) and plans._s(learner.group) in target['groupNames']
        learner.programme = target['programmeName'] or target['programmeId']
        learner.cohort = target['name']
        if not keep_group:
            learner.group = ''
        fields += ['programme', 'cohort', 'group']
        # Update an existing profile without changing activation, company,
        # review schedules or the learner's dates as a side effect of assignment.
        profile_updates = {
            'programme': learner.programme, 'programme_id': target['programmeId'] or None,
            'cohort': learner.cohort, 'cohort_id': target['id'], 'group_name': learner.group,
        }
        if not keep_group:
            profile_updates['group_id'] = None
        LearnerProfile.objects.filter(enrolment_id=learner.pk).update(**profile_updates)
    learner.save(update_fields=fields)
    # Strict here: all selected learners and their delivery plans commit together.
    plans.sync_learning_plan_mirror(learner, strict=True)
    return True


def _unassign(learner, target, catalogue, cache):
    # `catalogue` is unused here, but _handle dispatches to this and _assign
    # through the same call, so the two signatures have to match.
    if not _assigned(learner, target, cache):
        return False
    saved = stored_training_plan(learner)
    existing = list(saved if saved is not None else get_training_plan(learner) or [])
    remove_ids = set(target['moduleIds'])
    remaining = [entry for entry in existing if plans._s(entry.get('moduleId')) not in remove_ids]
    # Freeze what is left as explicit, the same as an assignment does, so a later
    # plan read does not put the removed module straight back from a group/cohort
    # preset the learner still inherits from.
    remaining = [dict(entry, assignmentMode='explicit') for entry in remaining]
    field = training_plan_field(learner)
    setattr(learner, field, remaining)
    learner.save(update_fields=[field])
    plans.sync_learning_plan_mirror(learner, strict=True)
    return True


def _handle(request, scope, identifier):
    if request.method not in ('GET', 'POST', 'DELETE'):
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    try:
        target = _target(scope, identifier)
        if target is None:
            return JsonResponse({'error': f'Active {scope} not found.'}, status=404)
        if request.method == 'GET':
            learners = EnrolmentUser.all_learners.only(
                'id', 'username', 'email', 'programme', 'cohort', 'group',
                'employer', 'organization', 'programme_status', 'learner_type',
            ).order_by('username', 'id')
            return JsonResponse(_payload(target, learners))
        try:
            body = json.loads(request.body)
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({'error': 'Request body must be valid JSON.'}, status=400)
        values = body.get('learnerIds') if isinstance(body, dict) else None
        if (not isinstance(values, list) or not values or len(values) > 5000
                or any(isinstance(value, bool) or not isinstance(value, (str, int))
                       or not str(value).isascii() or not str(value).isdecimal()
                       or len(str(value)) > 10 or not 0 < int(value) <= 2147483647 for value in values)):
            return JsonResponse({'error': 'Select between 1 and 5000 valid learners.'}, status=400)
        ids = sorted({int(value) for value in values})
        catalogue = {module['moduleId']: module for module in plans._all_modules()}
        if set(target['moduleIds']) - catalogue.keys():
            return JsonResponse({'error': 'The module list changed. Reload and try again.'}, status=409)
        alias = EnrolmentUser.all_learners.db
        if EnrolmentUser.all_learners.filter(pk__in=ids).count() != len(ids):
            return JsonResponse({'error': 'One or more selected learners no longer exist.'}, status=400)
        changed = 0
        failed = []
        failure_message = ''
        cache = {}
        # One committed transaction per learner. Holding every learner's plan
        # write open in a single transaction is what made a fifteen-learner save
        # run past the gateway timeout and come back as a 500/503 with nothing
        # saved; per learner, the ones that succeed stay saved and the caller is
        # told exactly which ones to retry.
        for learner_id in ids:
            try:
                with transaction.atomic(using=alias):
                    learner = (EnrolmentUser.all_learners.select_for_update()
                               .filter(pk=learner_id).first())
                    if learner is None:
                        failed.append(str(learner_id))
                        failure_message = failure_message or 'One or more selected learners no longer exist.'
                        continue
                    if request.method == 'DELETE':
                        # Four arguments, matching _assign: passing three put
                        # `cache` in the catalogue slot and left `cache`
                        # unfilled, so every removal raised TypeError and the
                        # drawer showed a bare 500.
                        changed += int(_unassign(learner, target, catalogue, cache))
                    else:
                        changed += int(_assign(learner, target, catalogue, cache))
            except Exception:  # noqa: BLE001 - one learner must not sink the batch
                logger.exception('Curriculum learner assignment failed for learner %s', learner_id)
                failed.append(str(learner_id))
                failure_message = failure_message or 'Unable to save learner assignments. Please try again.'
        if changed:
            from .views import invalidate_curriculum_cache
            invalidate_curriculum_cache()
        if len(failed) == len(ids):
            return JsonResponse({'error': failure_message or 'Unable to save learner assignments. Please try again.'}, status=503)
        return JsonResponse({
            'assignedCount': len(ids) - len(failed), 'changedCount': changed,
            'moduleCount': len(target['moduleIds']),
            'failedIds': failed, 'failureMessage': failure_message,
        })
    except DatabaseError:
        logger.exception('Curriculum learner assignment failed')
        return JsonResponse({'error': 'Unable to save learner assignments. Please try again.'}, status=503)
    except Exception:  # noqa: BLE001 - a bare 500 gives the drawer nothing to show
        logger.exception('Curriculum learner assignment failed')
        return JsonResponse({'error': 'Unable to save learner assignments. Please try again.'}, status=500)


@csrf_exempt
@staff_only()
def cohort_learner_assignments(request, identifier):
    return _handle(request, 'cohort', identifier)


@csrf_exempt
@staff_only()
def module_learner_assignments(request, identifier):
    return _handle(request, 'module', identifier)


def module_assignment_roster(module_id, placement_learners, lifecycle_status=''):
    """Include direct module membership without broadening programme/cohort rosters."""
    placed = {str(row['id']): row for row in placement_learners}
    profiles = LearnerProfile.objects.filter(
        Q(pk__in=list(placed)) | Q(learning_plan__contains=[{'moduleId': module_id}])
    )
    if lifecycle_status:
        profiles = profiles.filter(lifecycle_status__iexact=lifecycle_status)
    result = dict(placed)
    for profile in profiles:
        plan = profile.learning_plan
        if isinstance(plan, str):
            try:
                plan = json.loads(plan)
            except ValueError:
                plan = None
        entries = [entry for entry in plan if isinstance(entry, dict)] if isinstance(plan, list) else []
        explicit = any(entry.get('assignmentMode') == 'explicit' for entry in entries)
        assigned = any(str(entry.get('moduleId')) == module_id for entry in entries)
        key = str(profile.pk)
        if explicit and not assigned:
            result.pop(key, None)
            continue
        if key in result or not assigned:
            continue
        result[key] = {
            'id': profile.pk, 'sourceId': profile.pk, 'sourceKind': 'learner',
            'enrolmentId': profile.enrolment_id,
            'assignmentBasis': 'module',
            'name': profile.full_name or '', 'email': profile.email or '',
            'programme': profile.programme or '', 'programmeStatus': profile.programme_status or '',
            'cohort': profile.cohort or '', 'cohortId': profile.cohort_id or '',
            'group': profile.group_name or '', 'groupId': profile.group_id or '',
            'lifecycleStatus': profile.lifecycle_status or '',
            'coachName': profile.coach_name or '', 'coachEmail': profile.coach_email or '',
            **{out: float(getattr(profile, field, 0) or 0) for out, field in (
                ('completedHours', 'completed_hours'), ('plannedHours', 'planned_hours'),
                ('targetHours', 'target_hours'), ('progressHours', 'progress_hours'),
            )},
            'progressVariance': profile.progress_variance, 'otjhStatus': profile.otjh_status or '',
        }
    return sorted(result.values(), key=lambda row: (row.get('name', '').casefold(), str(row['id'])))


def bulk_assigned_learner_counts(module_ids):
    """Assigned-learner counts for many modules in one pass over all learners.

    The module list shows a "Learners (N)" figure on every card, and computing
    it the way `_assigned()` does -- one query and one effective-plan
    computation per module -- would mean one query per card. This runs that
    same computation (saved plan, or the learner's group preset when nothing is
    saved) once per learner instead, with the group-preset lookup cached and
    shared across learners exactly as `_payload` already shares it across a
    single module's request.
    """
    wanted = {plans._s(module_id) for module_id in module_ids if plans._s(module_id)}
    if not wanted:
        return {}
    counts = {module_id: 0 for module_id in wanted}
    cache = {}
    learners = EnrolmentUser.all_learners.only('id', 'programme', 'group', 'learning_plan', 'training_plan')
    for learner in learners:
        for module_id in set(plans._effective_plan_ids(learner, cache)):
            if module_id in wanted:
                counts[module_id] += 1
    return counts
