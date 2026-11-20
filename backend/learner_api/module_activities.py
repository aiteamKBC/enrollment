"""Read-only activities for one learner's EXISTING confirmed academic plan.

Never calls plan previews, hydration, allocation, save handlers or OTJ totals.
"""
import copy
import json
import math
from collections import Counter

from django.db import DatabaseError
from django.http import JsonResponse
from enrolment_api.auth import enrolment_login_required
from enrollment_service.lms_activity_readonly import get_module_activities
from .assignment_topics import definitions as assignment_topics
from .mappers import stored_training_plan
from .models import EnrolmentUser, LearnerProfile
from programme_suitability.models import Assessment, CohortGroupAllocation
from programme_suitability.programme_identity import CURRICULUM_PROGRAMME_IDS
from programme_suitability.lms_placement import curriculum_programme_id, label_key, group_key
from programme_suitability.modules_rules import available_modules
from programme_suitability.modules_service import selection_complete


def issue(code, table='Learning Plan', **details):
    return {'database': 'Enrolment' if table == 'Learning Plan' else 'LMS',
            'schema': 'enrolment' if table == 'Learning Plan' else 'curriculum',
            'table': table, 'code': code, 'message': f"{table}: {code.replace('_', ' ')}.", **details}


def _json(value, expected, fallback):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return fallback
    return value if isinstance(value, expected) else fallback


def _number(value):
    # Zero is a recorded value; NULL stays unavailable. Never derive hours.
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _order(row):
    return (row.get('display_order') or 0, row.get('week_number') or 0, str(row.get('id', '')))


def _unique_coverage(items):
    result = {}
    for item in items:
        key = item['code'].casefold()
        if key not in result:
            result[key] = {**item, 'sources': list(item['sources'])}
        else:
            result[key]['sources'].extend(s for s in item['sources'] if s not in result[key]['sources'])
    return list(result.values())


def project_module(entry, raw):
    """Pure projection: parent/component hours occur once; child topic hours NULL."""
    problems = copy.deepcopy(raw.get('blockers') or [])
    module_id = entry['lmsOfferingId']
    result = {'academicModuleId': entry['academicModuleId'], 'lmsModuleId': module_id,
        'title': entry.get('moduleTitle'), 'programmeId': entry['programmeId'],
        'cohortId': entry['cohortId'], 'cohortName': entry.get('cohortName'),
        'groupId': entry['groupId'], 'groupName': entry.get('groupName'),
        'moduleTotalOtjHours': entry.get('hours'), 'moduleHoursPending': entry.get('hoursPending', False),
        'moduleTotalSource': 'existing Learning Plan; unchanged',
        'startDate': entry.get('startDate'), 'endDate': entry.get('endDate'),
        'weeks': None, 'coveredKsbs': None, 'description': None}
    if entry.get('replacementStatus'):
        result.update(replacementStatus=entry['replacementStatus'], replacementNotice=entry.get('replacementNotice'),
                      replacesLmsOfferingId=entry.get('replacesLmsOfferingId'))
    modules = raw.get('modules')
    if (not modules or len(modules) != 1 or any(modules[0].get(field) != entry[key]
        for field, key in (('module_catalogue_id', 'lmsOfferingId'), ('programme_id', 'programmeId'),
                           ('cohort_id', 'cohortId'), ('group_id', 'groupId')))):
        if not problems:
            problems.append(issue('module_identity_unverified', 'modules'))
        return {**result, 'availability': {'status': 'unavailable', 'blockers': problems}}
    result.update(lmsTitle=modules[0]['title'], description=modules[0].get('description'))
    if not result['description']:
        problems.append(issue('module_description_not_recorded', 'modules', columns=['description']))
    weeks, components, mappings = (raw.get(name) for name in ('weeks', 'components', 'ksb_mappings'))
    valid_weeks = {w['id']: w for w in weeks or [] if w.get('module_catalogue_id') == module_id}
    valid_components = {c['id']: c for c in components or [] if c.get('module_catalogue_id') == module_id
                        and c.get('week_id') in valid_weeks}
    if weeks is not None and (len(valid_weeks) != len(weeks) or not weeks):
        problems.append(issue('week_relationship_missing_or_ambiguous', 'weeks'))
    if components is not None and (len(valid_components) != len(components) or not components):
        problems.append(issue('component_relationship_missing_or_ambiguous', 'components'))

    # Definition descriptions may enrich mapped codes only. No profile-wide KSBs.
    descriptions = {}
    profile_id = modules[0].get('ksb_profile_source_id')
    versions = raw.get('ksb_profile_versions') or []
    version_id = (versions[0]['id'] if len(versions) == 1
                  and versions[0].get('source_profile_id') == profile_id else None)
    for definition in raw.get('ksb_definitions') or []:
        if version_id is None or definition.get('profile_version_id') != version_id:
            problems.append(issue('ksb_definition_relationship_unverified', 'ksb_definitions', sourceId=definition.get('id')))
            continue
        descriptions.setdefault(str(definition['code']).casefold(), set()).add(definition.get('description') or '')
    profiles = raw.get('ksb_profiles')
    if (profiles and len(profiles) == 1
            and profile_id in (profiles[0].get('id'), profiles[0].get('ksb_profile_id'))):
        for item in _json(profiles[0].get('ksb_items'), list, []):
            if isinstance(item, dict) and isinstance(item.get('code'), str):
                descriptions.setdefault(item['code'].casefold(), set()).add(item.get('description') or '')
    elif profiles is not None:
        problems.append(issue('ksb_profile_missing_or_ambiguous', 'ksb_profiles'))
    coverage = []

    def add_ksb(code, description, source, week_id=None, component_id=None, **extra):
        if not isinstance(code, str) or not code.strip():
            problems.append(issue('ksb_code_missing', 'ksb_mappings'))
            return
        code = code.strip()
        choices = {d for d in descriptions.get(code.casefold(), set()) if d}
        if not description and len(choices) == 1:
            description = next(iter(choices))
        if not description:
            problems.append(issue('ksb_description_unavailable', 'ksb_definitions', ksbCode=code))
        coverage.append({'code': code, 'description': description or None, 'weekId': week_id,
                         'componentId': component_id, 'sources': [{**source, 'weekId': week_id, 'componentId': component_id}], **extra})

    for mapping in mappings or []:
        wid, cid = mapping.get('week_id') or None, mapping.get('component_id') or None
        if (mapping.get('module_catalogue_id') != module_id or (wid and wid not in valid_weeks)
                or (cid and cid not in valid_components)
                or (cid and wid and valid_components[cid]['week_id'] != wid)):
            problems.append(issue('ksb_relationship_unverified', 'ksb_mappings', sourceId=mapping.get('id')))
            continue
        if cid and not wid:
            wid = valid_components[cid]['week_id']  # Explicit component FK, never by title.
        add_ksb(mapping.get('ksb_code'), mapping.get('ksb_description'),
            {'table': 'curriculum.ksb_mappings', 'id': mapping['id']}, wid, cid,
            classification=mapping.get('classification'), weight=_number(mapping.get('weight')),
            weightClass=mapping.get('weight_class'))
    for component in valid_components.values():
        for position, mapping in enumerate(_json(component.get('ksb_mappings'), list, [])):
            if isinstance(mapping, dict):
                add_ksb(mapping.get('code') or mapping.get('ksb_code'), mapping.get('description') or mapping.get('ksb_description'),
                    {'table': 'curriculum.components.ksb_mappings', 'id': component['id'], 'position': position},
                    component['week_id'], component['id'])
    if mappings is not None or components is not None:
        result['coveredKsbs'] = _unique_coverage(coverage)
        if not coverage:
            problems.append(issue('mapped_ksbs_unavailable', 'ksb_mappings'))

    if weeks is not None:
        result['weeks'] = []
        for week in sorted(valid_weeks.values(), key=_order):
            children = None if components is None else []
            if children is not None:
                for component in sorted((c for c in valid_components.values() if c['week_id'] == week['id']), key=_order):
                    settings = _json(component.get('settings_json'), dict, {})
                    if component.get('settings_json') is not None and not isinstance(_json(component['settings_json'], dict, None), dict):
                        problems.append(issue('component_settings_invalid', 'components', sourceId=component['id']))
                    hours = _number(component.get('expected_otjh'))
                    if hours is None:
                        problems.append(issue('activity_hours_unavailable', 'components', sourceId=component['id'], columns=['expected_otjh']))
                    assignment = component['type'].casefold() == 'assignment'
                    topics = assignment_topics(settings.get('assignmentTopics')) if assignment else []
                    timing = {key: settings[key] for key in ('dueTiming', 'recordingDate')
                              if key in settings and settings[key] not in (None, '')}
                    children.append({'sourceId': component['id'], 'moduleId': module_id, 'weekId': week['id'],
                        'order': component.get('display_order'), 'type': component['type'], 'title': component['title'],
                        'description': component.get('description'), 'activityOtjHours': hours,
                        'hoursSource': 'curriculum.components.expected_otjh',
                        'timing': timing or None, 'timingAvailability': 'recorded' if timing else 'not_recorded',
                        'topics': [{**topic, 'parentComponentId': component['id'], 'activityOtjHours': None,
                                    'hoursAvailability': 'no_independent_recorded_hours'} for topic in topics],
                        'assignment': {key: settings[key] for key in ('assignmentBrief', 'assignmentContent',
                            'submissionInstructions', 'learnerTitle', 'dueTiming') if key in settings} if assignment else None,
                        'activityDetails': {key: settings[key] for key in ('readingContent', 'learnerGuidance',
                            'reflectionPrompt', 'submissionInstructions') if key in settings},
                        'coveredKsbs': _unique_coverage([k for k in coverage if k['componentId'] == component['id']])})
            result['weeks'].append({'sourceId': week['id'], 'moduleId': module_id, 'order': week.get('display_order'),
                'weekNumber': week.get('week_number'), 'title': week['title'], 'summary': week.get('summary'),
                'learningOutcomes': week.get('learning_outcomes'), 'components': children,
                'coveredKsbs': _unique_coverage([k for k in coverage if k['weekId'] == week['id']])})
    return {**result, 'availability': {'status': 'partial' if problems else 'available', 'blockers': problems}}


def learner_module_activities(learner_id, kind='apprenticeship'):
    """Public reusable service; existing saved plan is the only module source."""
    source = EnrolmentUser.all_learners.get(pk=learner_id)
    row = Assessment.objects.filter(learner_id=learner_id, learner_kind=kind).first()
    result = {'learnerId': learner_id, 'learnerKind': kind, 'modules': [], 'blockers': [], 'readOnly': True}

    def blocked(code):
        return {**result, 'status': 'unavailable', 'blockers': [issue(code)]}

    if kind != 'apprenticeship':
        return blocked('unsupported_learner_kind')
    if not row or not row.selection_confirmed_at or row.selected_programme not in CURRICULUM_PROGRAMME_IDS:
        return blocked('programme_not_confirmed')
    plan = stored_training_plan(source)
    if not plan:
        return blocked('persisted_learning_plan_required')
    for field in ('training_plan', 'learning_plan'):
        raw_plan = _json(getattr(source, field, None), list, None)
        if raw_plan is not None:
            if len(raw_plan) != len(plan):
                return blocked('saved_learning_plan_invalid')
            break
    if len(plan) > 200:
        return blocked('confirmed_plan_too_large')
    for entry in plan:
        for key in ('academicModuleId', 'lmsOfferingId', 'moduleId', 'programmeId', 'cohortId', 'groupId'):
            value = entry.get(key)
            if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 255 or '\x00' in value):
                return blocked('saved_module_identifier_invalid')
    pid = CURRICULUM_PROGRAMME_IDS[row.selected_programme]
    if curriculum_programme_id('', source.programme) != pid:
        return blocked('saved_programme_conflict')
    config = row.modules_configuration
    if not isinstance(config, dict) or not isinstance(config.get('programmes'), list) or not isinstance(row.review, dict):
        return blocked('saved_academic_configuration_invalid')
    programme = next((p for p in config['programmes'] if isinstance(p, dict) and p.get('id') == row.selected_programme), None)
    if not programme:
        return blocked('saved_academic_configuration_missing')
    profiles = list(LearnerProfile.objects.filter(enrolment_id=learner_id)[:2])
    if len(profiles) > 1:
        return blocked('saved_placement_ambiguous')
    profile = profiles[0] if profiles else None
    assignment = (row.review or {}).get('programmeAssignment') or {}
    cid = (profile.cohort_id if profile else '') or assignment.get('cohortId')
    if (not cid or not source.cohort or (assignment.get('cohortName') and label_key(assignment['cohortName']) != label_key(source.cohort))
            or (profile and ((profile.programme_id and profile.programme_id != pid)
                or (profile.cohort and label_key(profile.cohort) != label_key(source.cohort))
                or (profile.cohort_id and assignment.get('cohortId') and profile.cohort_id != assignment['cohortId'])))):
        return blocked('saved_home_cohort_unverified')
    allocation = None
    if row.selected_programme == 'PCP_L6':
        if not selection_complete(row, row.modules_selection):
            return blocked('active_pathway_not_confirmed')
        pathway = row.modules_selection['pathway']
        path = next(p for p in programme['pathways'] if p['id'] == pathway and p.get('active'))
        selected = set(row.modules_selection['modulesByPathway'][pathway])
        allowed = {m['id'] for m in available_modules(path['modules']) if m['id'] in selected}
        allocation = CohortGroupAllocation.objects.select_related('pool').filter(learner_id=learner_id, learner_kind=kind).first()
        if (not allocation or allocation.pathway != pathway or allocation.pool.programme_id != pid
                or allocation.pool.cohort_id != cid or set(allocation.academic_ids) != allowed):
            return blocked('persisted_pcp_home_allocation_required')
        if {e.get('academicModuleId') for e in plan} != allowed:
            return blocked('confirmed_active_pathway_plan_conflict')
    else:
        from programme_suitability.fixed_learning_plan import _modules
        allowed = {m['id'] for m in _modules(programme.get('modules', []))}
    counts = Counter(e.get('lmsOfferingId') for e in plan)
    academic_counts = Counter(e.get('academicModuleId') for e in plan)
    replacement_plan = None
    for entry in plan:
        aid, mid = entry.get('academicModuleId'), entry.get('lmsOfferingId')
        gid = entry.get('groupId')
        error = None
        if not aid or aid not in allowed:
            error = 'academic_module_not_confirmed'
        elif not mid or entry.get('moduleId') != mid:
            error = 'verified_lms_offering_required'
        elif counts[mid] != 1 or academic_counts[aid] != 1:
            error = 'module_identity_ambiguous'
        elif entry.get('programmeId') != pid or entry.get('cohortId') != cid or not gid:
            error = 'module_outside_saved_home_placement'
        elif not allocation and not (source.group or (profile and profile.group_id)):
            error = 'saved_group_unverified'
        elif ((profile and profile.group_id and profile.group_id != gid)
              or (source.group and group_key(source.group) != group_key(entry.get('groupName')))):
            error = 'saved_group_conflict'
        elif allocation and (gid != allocation.group_id or not any(
                o.get('academicModuleId') == aid and o.get('offeringId') == mid and o.get('cohortId') == cid
                and o.get('groupId') == gid and o.get('start') == entry.get('startDate')
                and o.get('end') == entry.get('endDate') for o in allocation.offerings)):
            error = 'pcp_offering_not_confirmed'
        if error:
            result['blockers'].append(issue(error, academicModuleId=aid))
            continue
        raw = get_module_activities(mid, pid, cid, gid)
        if raw.get('modules') == [] and any(b.get('code') == 'module_placement_not_found' for b in raw.get('blockers') or []):
            if replacement_plan is None:
                from programme_suitability.module_replacements import resolve_saved_learning_plan
                replacement_plan = resolve_saved_learning_plan(source, plan, row=row)
            effective = next((e for e in replacement_plan if e.get('academicModuleId') == aid), entry)
            if effective.get('replacementStatus') == 'resolved':
                entry = effective
                raw = get_module_activities(entry['lmsOfferingId'], pid, cid, gid)
            elif effective.get('replacementStatus'):
                entry = effective
                raw = {**raw, 'blockers': [*(raw.get('blockers') or []), issue(
                    'replacement_' + effective['replacementStatus'], 'modules', message=effective['replacementNotice'])]}
        module = project_module(entry, raw)
        result['modules'].append(module)
        result['blockers'].extend({**b, 'academicModuleId': aid} for b in module['availability']['blockers'])
    result['status'] = ('available' if not result['blockers'] else
                        'partial' if any(m['availability']['status'] != 'unavailable' for m in result['modules']) else 'unavailable')
    return result


@enrolment_login_required
def learner_module_activities_view(request, kind, learner_id):
    """GET only; no body/query parameters can broaden the learner's module set."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    try:
        result = learner_module_activities(learner_id, kind)
        response = JsonResponse(result, status=200 if result['status'] != 'unavailable' else 409)
    except EnrolmentUser.DoesNotExist:
        response = JsonResponse({'error': 'Learner not found.'}, status=404)
    except DatabaseError:
        response = JsonResponse({'status': 'unavailable', 'blockers': [issue('saved_learning_plan_storage_unavailable')]}, status=503)
    response['Cache-Control'] = 'private, no-store'
    return response
