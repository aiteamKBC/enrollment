"""Populate the existing Learning Plan from a persisted PCP allocation only."""
import hashlib
import json

from django.db import connections, transaction
from learner_api.mappers import stored_training_plan
from learner_api.models import EnrolmentUser, LearnerProfile
from enrollment_service import lms_readonly
from . import group_capacity as capacity
from .fixed_learning_plan import _module_hours
from .learning_plan_storage import protected_plan, write_plan
from .models import Assessment, CohortGroupAllocation
from .modules_rules import available_modules
from .programme_assignment import assignment_database

SOURCE = 'programme_suitability_pcp'


def fingerprint(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def conflict():
    capacity.unavailable('learning_plan_conflict',
        'Your existing Learning Plan has been preserved. Admissions must review it before cohort modules can be confirmed.')


def confirm_with_learning_plan(kind, learner_id, revision):
    """One transaction/connection for the existing allocator and canonical plan."""
    using = 'default'
    with transaction.atomic(using=using):
        row = Assessment.objects.using(using).select_for_update().get(learner_kind=kind, learner_id=learner_id)
        # Same physical Enrolment database guard used by Step 4.
        if assignment_database(row) != using or not connections[using].in_atomic_block:
            raise RuntimeError('PCP confirmation requires one Enrolment transaction.')
        source = EnrolmentUser.all_learners.using(using).select_for_update().get(pk=learner_id)
        profiles = list(LearnerProfile.objects.using(using).select_for_update().filter(enrolment_id=learner_id)[:2])
        previous = (row.review or {}).get('pcpLearningPlan')
        if previous:
            saved = stored_training_plan(source)
            if (saved is None or fingerprint(saved) != previous.get('fingerprint')
                    or any(p.learning_plan and p.learning_plan != saved for p in profiles)):
                conflict()
        elif protected_plan(source, profiles):
            conflict()
        result = capacity.allocate(kind, learner_id, revision, using=using, pcp_only=True)
        allocation = CohortGroupAllocation.objects.using(using).select_related('pool').get(
            learner_id=learner_id, learner_kind=kind)
        if previous:
            if previous.get('allocationId') != allocation.pk:
                conflict()
            return result

        programme = next(p for p in row.modules_configuration['programmes'] if p['id'] == 'PCP_L6')
        path = next(p for p in programme['pathways'] if p['id'] == row.modules_selection['pathway'])
        selected = set(row.modules_selection['modulesByPathway'][path['id']])
        modules = [m for m in available_modules(path['modules']) if m['id'] in selected]
        offers = allocation.offerings
        if (allocation.pathway != path['id'] or set(allocation.academic_ids) != {m['id'] for m in modules}
                or len(offers) != len(modules) or len({o['academicModuleId'] for o in offers}) != len(modules)
                or len({o['offeringId'] for o in offers}) != len(modules)
                or any(o['cohortId'] != allocation.pool.cohort_id or o['groupId'] != allocation.group_id for o in offers)):
            capacity.unavailable('schedule_incomplete', 'The confirmed home-group module bundle requires admissions review.')

        # Enrich hours only by the exact confirmed offering ID and placement.
        # Dates and identities always come from the immutable allocation snapshot.
        from .module_schedules import read_all
        try:
            details = read_all(allocation.pool.programme_id, allocation.pool.cohort_id)
        except lms_readonly.LmsReadUnavailable:
            details = []
        plan = []
        for module in modules:
            offer = next(o for o in offers if o['academicModuleId'] == module['id'])
            matches = [o for o in details if o['module_catalogue_id'] == offer['offeringId']
                and o['programme_id'] == allocation.pool.programme_id and o['cohort_id'] == offer['cohortId']
                and o['group_id'] == offer['groupId'] and str(o['start_date']) == offer['start']
                and str(o['end_date']) == offer['end']]
            hours, pending = _module_hours(matches[0] if len(matches) == 1 else {})
            plan.append({'moduleId': offer['offeringId'], 'academicModuleId': module['id'],
                'lmsOfferingId': offer['offeringId'], 'moduleTitle': module['title'], 'lmsTitle': offer['title'],
                'programmeId': allocation.pool.programme_id, 'programmeName': source.programme,
                'cohortId': offer['cohortId'], 'cohortName': source.cohort,
                'groupId': allocation.group_id, 'groupName': allocation.group_name,
                'startDate': offer['start'], 'endDate': offer['end'], 'hours': hours, 'hoursPending': pending,
                'schedulingStatus': 'scheduled', 'assignmentMode': 'explicit', 'assignmentSource': SOURCE,
                'pathway': path['id'], 'configurationVersion': allocation.configuration_version, 'weeks': []})
        write_plan(source, plan, using)
        row.review = {**(row.review or {}), 'pcpLearningPlan': {
            'allocationId': allocation.pk, 'moduleCount': len(plan), 'fingerprint': fingerprint(plan)}}
        row.save(using=using, update_fields=['review'])
        return result
