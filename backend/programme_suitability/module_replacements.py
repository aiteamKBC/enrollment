"""Resolve deleted LMS deliveries for a saved academic plan, without writes.

Saved confirmations and issued snapshots remain historical evidence. A current
read may use one verified replacement in the very same programme/home group.
"""
import copy
import json
from datetime import date
from pathlib import Path

from django.db import DatabaseError
from enrollment_service import lms_readonly
from learner_api.models import LearnerProfile
from .models import Assessment, CohortGroupAllocation
from .learning_plan_storage import ACADEMIC_SOURCES
from .programme_identity import CURRICULUM_PROGRAMME_IDS
from .lms_placement import curriculum_programme_id, label_key, group_key
from .modules_rules import available_modules
from .modules_service import selection_complete


def _notice(entry, status, message):
    return {**entry, 'replacementStatus': status, 'replacementNotice': message}


def resolve_entries(plan, programme, modules, pathway=None):
    """Caller verifies saved learner context; no module sets or group seats change."""
    from .fixed_learning_plan import _module_hours
    from .module_schedules import eligible, matching_confidence
    result = copy.deepcopy(plan)
    allowed = {m['id']: m for m in modules}
    indexes = [i for i, e in enumerate(plan) if e.get('assignmentSource') in ACADEMIC_SOURCES
               and e.get('academicModuleId') in allowed and isinstance(e.get('lmsOfferingId'), str)
               and e.get('moduleId') == e['lmsOfferingId'] and all(isinstance(e.get(k), str) and e[k]
                   for k in ('programmeId', 'cohortId', 'groupId'))]
    if not indexes:
        return result
    try:
        references = lms_readonly.get_saved_module_references([plan[i]['lmsOfferingId'] for i in indexes])
    except (lms_readonly.LmsReadUnavailable, ValueError):
        # An outage is not evidence that a module was deleted.
        return result
    by_id = {r['module_catalogue_id']: r for r in references}
    evidence = json.loads(Path(__file__).with_name('schedule_review_candidates.json').read_text(encoding='utf-8'))

    def matches(module, offering):
        aliases = next((e.get('confirmed_titles', []) for e in evidence['modules'] if e['programme'] == programme
            and e['pathway'] == pathway and e['academic_id'] == module['id'] and e['academic_title'] == module['title']), [])
        return bool(matching_confidence(module, offering['title'], (), aliases)[0])

    candidates_by_scope = {}
    occupied = {e.get('lmsOfferingId') for e in plan}
    for index in indexes:
        entry = plan[index]
        old_id = entry['lmsOfferingId']
        reference = by_id.get(old_id)
        scope = tuple(entry[k] for k in ('programmeId', 'cohortId', 'groupId'))
        if reference and tuple(reference.get(k) for k in ('programme_id', 'cohort_id', 'group_id')) != scope:
            result[index] = _notice(entry, 'conflict', 'The saved LMS module moved to a different placement; admissions review is required.')
            continue
        deleted = (not reference or reference.get('deleted_at') is not None or reference.get('is_programme_deleted')
                   or str(reference.get('deleted_by') or '').casefold() == 'module-delete' or reference.get('deleted_via_parent'))
        if not deleted:
            continue
        if entry.get('weeks'):
            result[index] = _notice(entry, 'conflict', 'This deleted module has customised learning content; its replacement needs review.')
            continue
        try:
            if scope not in candidates_by_scope:
                candidates, seen = [], set()
                for offset in range(0, 100001, 200):
                    page = lms_readonly.get_replacement_offerings(*scope, limit=200, offset=offset)
                    if any(o['module_catalogue_id'] in seen for o in page):
                        raise lms_readonly.LmsReadUnavailable('replacement_result_incomplete')
                    seen.update(o['module_catalogue_id'] for o in page)
                    candidates.extend(o for o in page if tuple(o.get(k) for k in ('programme_id', 'cohort_id', 'group_id')) == scope
                        and eligible(o, scope[0], date.min))
                    if len(page) < 200:
                        break
                else:
                    raise lms_readonly.LmsReadUnavailable('replacement_result_incomplete')
                candidates_by_scope[scope] = candidates
            candidates = [o for o in candidates_by_scope[scope] if matches(allowed[entry['academicModuleId']], o)]
        except lms_readonly.LmsReadUnavailable:
            result[index] = _notice(entry, 'unavailable', 'The deleted module replacement could not be verified. Retry later.')
            continue
        if len(candidates) != 1:
            result[index] = _notice(entry, 'ambiguous' if candidates else 'unavailable',
                'Multiple matching replacement modules need review.' if candidates else 'Waiting for a replacement module in the same programme, cohort and group.')
            continue
        offer = candidates[0]
        if offer['module_catalogue_id'] in occupied or sum(matches(m, offer) for m in modules) != 1:
            result[index] = _notice(entry, 'conflict', 'The replacement module has an ambiguous academic identity and needs review.')
            continue
        occupied.add(offer['module_catalogue_id'])
        hours, pending = _module_hours(offer)
        result[index] = _notice({**entry, 'moduleId': offer['module_catalogue_id'],
            'lmsOfferingId': offer['module_catalogue_id'], 'replacesLmsOfferingId': old_id,
            'moduleTitle': offer['title'], 'startDate': str(offer['start_date']), 'endDate': str(offer['end_date']),
            'hours': hours, 'hoursPending': pending, 'sessions': offer.get('sessions_number'),
            'durationWeeks': offer.get('weeks_number'), 'schedulingStatus': 'scheduled'},
            'resolved', 'Using the replacement LMS module in the same home cohort and group.')
    return result


def resolve_saved_learning_plan(source, plan, row=None):
    """Shared current-read resolver; never updates the stored plan or allocation."""
    if not plan or not any(e.get('assignmentSource') in ACADEMIC_SOURCES for e in plan):
        return plan
    using = source._state.db or 'default'
    try:
        row = row or Assessment.objects.using(using).filter(learner_id=source.pk, learner_kind='apprenticeship',
            selection_confirmed_at__isnull=False).first()
        if not row or not row.selection_confirmed_at or row.selected_programme not in CURRICULUM_PROGRAMME_IDS:
            return plan
        pid = CURRICULUM_PROGRAMME_IDS[row.selected_programme]
        if curriculum_programme_id('', source.programme) != pid:
            return plan
        programme = next((p for p in (row.modules_configuration or {}).get('programmes', []) if p['id'] == row.selected_programme), None)
        if not programme:
            return plan
        profiles = list(LearnerProfile.objects.using(using).filter(enrolment_id=source.pk)[:2])
        if len(profiles) > 1:
            return plan
        profile = profiles[0] if profiles else None
        assignment = (row.review or {}).get('programmeAssignment') or {}
        cid = (profile.cohort_id if profile else '') or assignment.get('cohortId')
        if not cid and source.cohort:
            # Older confirmations kept the home-cohort label only. Verify its
            # unique LMS identity; this reads the assignment, never assigns one.
            cohorts = [c for c in lms_readonly.get_programme_cohorts(pid)
                if c.get('programme_id') == pid and label_key(c.get('cohort_name')) == label_key(source.cohort)]
            if len(cohorts) == 1:
                cid = cohorts[0]['cohort_id']
        if (not cid or not source.cohort or (assignment.get('cohortName') and label_key(assignment['cohortName']) != label_key(source.cohort))
                or (profile and ((profile.programme_id and profile.programme_id != pid)
                    or (profile.cohort and label_key(profile.cohort) != label_key(source.cohort))
                    or (profile.cohort_id and assignment.get('cohortId') and profile.cohort_id != assignment['cohortId'])))):
            return plan
        pathway, allocated = None, None
        if row.selected_programme == 'PCP_L6':
            if not selection_complete(row, row.modules_selection):
                return plan
            pathway = row.modules_selection['pathway']
            path = next(p for p in programme['pathways'] if p['id'] == pathway and p.get('active'))
            ids = set(row.modules_selection['modulesByPathway'][pathway])
            modules = [m for m in available_modules(path['modules']) if m['id'] in ids]
            allocated = CohortGroupAllocation.objects.using(using).select_related('pool').filter(learner_id=source.pk, learner_kind='apprenticeship').first()
            if (not allocated or allocated.pathway != pathway or allocated.pool.programme_id != pid
                    or allocated.pool.cohort_id != cid or set(allocated.academic_ids) != ids):
                return plan
        else:
            from .fixed_learning_plan import _modules
            modules = list(_modules(programme.get('modules', [])))
        valid = []
        for entry in plan:
            same_group = (entry.get('groupId') == allocated.group_id if allocated else
                entry.get('groupId') == profile.group_id if profile and profile.group_id else
                bool(source.group) and group_key(source.group) == group_key(entry.get('groupName')))
            if entry.get('programmeId') != pid or entry.get('cohortId') != cid or not same_group:
                continue
            if profile and profile.group_id and entry.get('groupId') != profile.group_id:
                continue
            if source.group and group_key(source.group) != group_key(entry.get('groupName')):
                continue
            if allocated and not any(o.get('academicModuleId') == entry.get('academicModuleId')
                    and o.get('offeringId') == entry.get('lmsOfferingId') and o.get('cohortId') == cid
                    and o.get('groupId') == allocated.group_id for o in allocated.offerings):
                continue
            valid.append(entry)
        resolved = resolve_entries(valid, row.selected_programme, modules, pathway)
        replacements = {id(original): effective for original, effective in zip(valid, resolved)}
        return [replacements.get(id(e), e) for e in plan]
    except (DatabaseError, lms_readonly.LmsReadUnavailable, ValueError, TypeError, KeyError):
        return plan
