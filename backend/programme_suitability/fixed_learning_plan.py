"""Seed the existing Enrolment Learning Plan on fixed-programme confirmation.

Never allocates groups or writes to LMS, profiles, documents or learner status.
Existing assignments (including an explicitly cleared list) always win.
"""
import json
import math
import hashlib
from datetime import date
from pathlib import Path

from django.db import DatabaseError, connections, transaction
from learner_api.models import EnrolmentUser, LearnerProfile
from enrollment_service import lms_readonly
from .lms_placement import curriculum_programme_id, group_key, label_key
from .models import Assessment, CohortGroupAllocation
from .module_items import is_group
from .modules_service import _pin_configuration
from .programme_assignment import assignment_database
from .programme_identity import CURRICULUM_PROGRAMME_IDS
from .learning_plan_storage import protected_plan, write_plan

FIXED_PROGRAMMES = {'ME_L4', 'MM_L6', 'APM_L4'}
SOURCE = 'programme_suitability_fixed'


class FixedPlanSaveConflict(Exception):
    """The displayed preview is stale or an existing plan must be preserved."""


def fixed_plan_preview_token(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def save_previewed_fixed_plan(learner_id, expected_token):
    """Staff explicitly saves an unchanged prepared plan, without PATCH side effects."""
    from learner_api.mappers import stored_training_plan
    with transaction.atomic(using='default'):
        row = Assessment.objects.using('default').select_for_update().filter(learner_id=learner_id,
            learner_kind='apprenticeship', selected_programme__in=FIXED_PROGRAMMES,
            confirmed_at__isnull=False, selection_confirmed_at__isnull=False).first()
        if not row or not row.modules_configuration:
            raise FixedPlanSaveConflict('A confirmed fixed programme and its saved module configuration are required. PCP uses Confirm My Cohort Modules.')
        using = assignment_database(row)
        source = EnrolmentUser.all_learners.using(using).select_for_update().get(pk=learner_id)
        profiles = list(LearnerProfile.objects.using(using).select_for_update().filter(enrolment_id=learner_id)[:2])
        previous = (row.review or {}).get('fixedLearningPlan') or {}
        saved = stored_training_plan(source)
        # A network retry may return success only for this identical saved snapshot.
        if (previous.get('previewToken') == expected_token and saved is not None
                and fixed_plan_preview_token(saved) == expected_token
                and not any(p.learning_plan and p.learning_plan != saved for p in profiles)):
            return
        if previous or protected_plan(source, profiles):
            raise FixedPlanSaveConflict('An existing Learning Plan has been preserved. Close and reopen this window to see the current plan.')
        if curriculum_programme_id('', source.programme) != CURRICULUM_PROGRAMME_IDS[row.selected_programme]:
            raise FixedPlanSaveConflict('The saved programme needs admissions review; the Learning Plan was not changed.')
        plan = _build_plan(row, source, profiles, using)
        if fixed_plan_preview_token(plan) != expected_token:
            raise FixedPlanSaveConflict('The prepared modules changed. Close and reopen the Learning Plan before saving.')
        write_plan(source, plan, using)
        row.review = {**(row.review or {}), 'fixedLearningPlan': {
            'status': 'CREATED', 'source': 'admin-saved-preview', 'previewToken': expected_token,
            'programme': row.selected_programme, 'configurationVersion': row.modules_configuration_version,
            'moduleCount': len(plan), 'pendingScheduleCount': sum(e['schedulingStatus'] == 'pending' for e in plan)}}
        row.save(using=using, update_fields=['review'])


def _module_hours(offer):
    """Only the matched LMS module's total OTJ hours; never weeks/sessions."""
    try:
        hours = float(offer['total_otjh'])
        if math.isfinite(hours) and hours >= 0:
            return hours, False
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    return 0, True


def _modules(items):
    for item in sorted(items, key=lambda m: m.get('order', 0)):
        if not item.get('active', True):
            continue
        if is_group(item):
            yield from _modules(item.get('items', []))
        else:
            yield item


def _schedule(row, source, profile, using):
    """Resolve saved placement only. A failed lookup leaves academic rows pending."""
    pid = CURRICULUM_PROGRAMME_IDS[row.selected_programme]
    assignment = (row.review or {}).get('programmeAssignment') or {}
    cid = profile.cohort_id if profile and profile.cohort_id else (
        assignment.get('cohortId') if label_key(assignment.get('cohortName')) == label_key(source.cohort) else None)
    gid = profile.group_id if profile and profile.group_id else None
    names = [name for name in (source.group, profile.group_name if profile else '') if name]
    if len({group_key(name) for name in names}) > 1:
        return None, None, []
    group_name = names[0] if names else ''
    if not source.cohort or (profile and profile.programme_id and profile.programme_id != pid):
        return None, None, []
    if profile and profile.cohort and label_key(profile.cohort) != label_key(source.cohort):
        return None, None, []
    cohorts = lms_readonly.get_programme_cohorts(pid)
    cohorts = [c for c in cohorts if c['programme_id'] == pid and
               (c['cohort_id'] == cid if cid else label_key(c['cohort_name']) == label_key(source.cohort))]
    if len(cohorts) != 1 or label_key(cohorts[0]['cohort_name']) != label_key(source.cohort):
        return None, None, []
    cid = cohorts[0]['cohort_id']
    # Read an existing allocation only; do not call the capacity chooser/writer.
    try:
        with transaction.atomic(using=using):
            allocated = CohortGroupAllocation.objects.using(using).select_related('pool').filter(learner_id=row.learner_id).first()
    except DatabaseError:
        allocated = None
    if allocated:
        if allocated.pool.programme_id != pid or allocated.pool.cohort_id != cid or (gid and gid != allocated.group_id):
            return cid, None, []
        gid = allocated.group_id
    groups = lms_readonly.get_cohort_groups(pid, cid)
    groups = [g for g in groups if g['programme_id'] == pid and g['cohort_id'] == cid and
              (g['group_id'] == gid if gid else bool(group_name) and group_key(g['group_name']) == group_key(group_name))]
    if len(groups) != 1 or (group_name and group_key(groups[0]['group_name']) != group_key(group_name)):
        return cid, None, []
    group = groups[0]
    from .module_schedules import read_all, eligible
    offerings = [o for o in read_all(pid, cid) if o['cohort_id'] == cid and o.get('group_id') == group['group_id']
                 and eligible(o, pid, date.min)]
    return cid, group, offerings


def _build_plan(row, source, profiles, using):
    """One resolver for confirmation writes and read-only historical previews."""
    programme = next((p for p in row.modules_configuration['programmes'] if p['id'] == row.selected_programme), None)
    modules = list(_modules(programme.get('modules', []))) if programme else []
    if not modules or len({m['id'] for m in modules}) != len(modules):
        from .configuration import ConfigurationError
        raise ConfigurationError('The saved fixed academic modules need admissions review before programme confirmation.')
    try:
        with transaction.atomic(using=using):
            cid, group, offerings = _schedule(row, source, profiles[0] if profiles else None, using) if len(profiles) <= 1 else (None, None, [])
    except (lms_readonly.LmsReadUnavailable, DatabaseError, ValueError):
        cid, group, offerings = None, None, []
    from .module_schedules import matching_confidence
    evidence = json.loads(Path(__file__).with_name('schedule_review_candidates.json').read_text(encoding='utf-8'))
    matches_by_module = {}
    for module in modules:
        aliases = next((e.get('confirmed_titles', []) for e in evidence['modules']
            if e['programme'] == row.selected_programme and e['academic_id'] == module['id'] and e['academic_title'] == module['title']), [])
        matches_by_module[module['id']] = [o for o in offerings if matching_confidence(module, o['title'], (), aliases)[0]]
    plan = []
    for module in modules:
        matches = matches_by_module[module['id']]
        offer = matches[0] if len(matches) == 1 and sum(
            any(o['module_catalogue_id'] == matches[0]['module_catalogue_id'] for o in candidates)
            for candidates in matches_by_module.values()) == 1 else None
        hours, hours_pending = _module_hours(offer)
        plan.append({'moduleId': offer['module_catalogue_id'] if offer else f'academic:{row.selected_programme}:{module["id"]}',
            'moduleTitle': offer['title'] if offer else module['title'], 'academicModuleId': module['id'],
            'programmeId': CURRICULUM_PROGRAMME_IDS[row.selected_programme], 'programmeName': source.programme,
            'cohortId': cid or '', 'cohortName': source.cohort or '',
            'groupId': group['group_id'] if group else '', 'groupName': group['group_name'] if group else '',
            'hours': hours, 'hoursPending': hours_pending,
            'startDate': str(offer['start_date']) if offer else '', 'endDate': str(offer['end_date']) if offer else '',
            'lmsOfferingId': offer['module_catalogue_id'] if offer else None,
            'schedulingStatus': 'scheduled' if offer else 'pending',
            'assignmentMode': 'explicit', 'assignmentSource': SOURCE,
            'configurationVersion': row.modules_configuration_version, 'weeks': [],
            **({'sessions': offer.get('sessions_number'), 'durationWeeks': offer.get('weeks_number')} if offer else {})})
    return plan


def preview_uninitialised_fixed_plan(source):
    """Existing confirmed learners need the same academic source, not legacy presets.

    This never pins configuration, writes a plan or repairs a live learner on GET.
    Saved/cleared/manual plans remain governed by the existing serializer.
    """
    from .enrolment_catalogue import enabled
    if enabled():
        return None
    from learner_api.mappers import stored_training_plan
    if (stored_training_plan(source) is not None or curriculum_programme_id('', source.programme)
            not in {CURRICULUM_PROGRAMME_IDS[code] for code in FIXED_PROGRAMMES}):
        return None
    using = source._state.db or 'default'
    row = Assessment.objects.using(using).filter(learner_id=source.pk, selected_programme__in=FIXED_PROGRAMMES,
        confirmed_at__isnull=False, selection_confirmed_at__isnull=False).first()
    if (not row or not row.modules_configuration or (row.review or {}).get('fixedLearningPlan')
            or curriculum_programme_id('', source.programme) != CURRICULUM_PROGRAMME_IDS[row.selected_programme]):
        return None
    profiles = list(LearnerProfile.objects.using(using).filter(enrolment_id=source.pk)[:2])
    if any(p.learning_plan for p in profiles):
        return None
    return _build_plan(row, source, profiles, using)


def populate_fixed_learning_plan(row):
    """Caller holds the confirmation transaction/assessment lock on this alias."""
    from .enrolment_catalogue import enabled
    if enabled():
        # Programme confirmation stops at the cohort. Existing plans are kept.
        return None
    if row.selected_programme not in FIXED_PROGRAMMES or not row.confirmed_at or not row.selection_confirmed_at:
        return None
    using = assignment_database(row)
    if not connections[using].in_atomic_block:
        raise RuntimeError('Learning Plan population requires the programme confirmation transaction.')
    source = EnrolmentUser.all_learners.using(using).select_for_update().get(pk=row.learner_id)
    previous = (row.review or {}).get('fixedLearningPlan')
    # Never replenish a removed module or overwrite edits, even on a retry.
    if previous:
        return previous
    profiles = list(LearnerProfile.objects.using(using).select_for_update().filter(enrolment_id=row.learner_id)[:2])
    protected = protected_plan(source, profiles)
    if protected:
        outcome = {'status': 'EXISTING_PLAN_PRESERVED'}
    elif curriculum_programme_id('', source.programme) != CURRICULUM_PROGRAMME_IDS[row.selected_programme]:
        outcome = {'status': 'PLACEMENT_CONFLICT', 'message': 'The saved programme needs admissions review; the Learning Plan was not changed.'}
    else:
        changed = _pin_configuration(row)
        if changed:
            row.save(using=using, update_fields=changed)
        plan = _build_plan(row, source, profiles, using)
        # No full learner save, status advancement, profile sync or document hook.
        write_plan(source, plan, using)
        outcome = {'status': 'CREATED', 'programme': row.selected_programme,
                   'configurationVersion': row.modules_configuration_version, 'moduleCount': len(plan),
                   'pendingScheduleCount': sum(e['schedulingStatus'] == 'pending' for e in plan)}
    row.review = {**row.review, 'fixedLearningPlan': outcome}
    row.save(using=using, update_fields=['review'])
    return outcome
