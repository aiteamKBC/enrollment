"""Read-only, learner-scoped projection of pinned local catalogue curriculum.

This module deliberately never imports the LMS reader or resolves a live LMS
home cohort. Missing assignment or curriculum is an explicit unavailable state.
"""
from collections import Counter
from uuid import UUID

from learner_api.models import LearnerProfile
from programme_suitability.catalog_models import (
    CatalogCohort, CatalogDelivery, CatalogGroup, CatalogKsb, CatalogProgramme, CatalogSourceLink,
)
from programme_suitability.models import Assessment
from programme_suitability.programme_identity import CURRICULUM_PROGRAMME_IDS
from enrollment_service.lms_catalogue_source import SOURCE_BRANCH_ID, SOURCE_PROJECT_ID


def _leaves(items):
    for item in items:
        if not item.get('active'):
            continue
        if item.get('itemType') == 'GROUP' or isinstance(item.get('items'), list):
            yield from _leaves(item.get('items') or [])
        else:
            yield item


def selected_modules(assessment):
    """Use the learner's pinned Suitability configuration and saved choice."""
    configuration = assessment.modules_configuration or {}
    programme = next((p for p in configuration.get('programmes', [])
                      if p.get('id') == assessment.selected_programme), None)
    if not programme:
        return []
    if assessment.selected_programme == 'PCP_L6':
        choice = assessment.modules_selection or {}
        if choice.get('programme') != 'PCP_L6':
            return []
        pathway = next((p for p in programme.get('pathways', [])
                        if p.get('id') == choice.get('pathway') and p.get('active')), None)
        if not pathway:
            return []
        selected = set((choice.get('modulesByPathway') or {}).get(pathway['id']) or [])
        items = [m for m in _leaves(pathway.get('modules') or [])
                 if m.get('isMandatory') or m.get('id') in selected]
    else:
        items = list(_leaves(programme.get('modules') or []))
    return [{'id': m['id'], 'name': m['title']} for m in items]


def _unavailable(items, reason, **scope):
    return {'status': 'unavailable', 'reason': reason, **scope,
            'modules': [{'id': item['id'], 'name': item['name'], 'status': 'unavailable',
                         'reason': reason, 'ksbs': []} for item in items]}


def learner_module_impact(kind, learner_id):
    """At most one query per relevant table; no catalogue-wide reads or writes."""
    assessment = Assessment.objects.filter(learner_id=learner_id, learner_kind=kind).only(
        'selected_programme', 'selection_confirmed_at', 'modules_configuration', 'modules_selection').first()
    if not assessment or not assessment.selection_confirmed_at:
        return _unavailable([], 'programme_not_confirmed')
    selected = selected_modules(assessment)
    if not selected:
        return _unavailable([], 'module_selection_unavailable', programmeId=assessment.selected_programme)
    external_programme = CURRICULUM_PROGRAMME_IDS.get(assessment.selected_programme)
    if not external_programme:
        return _unavailable(selected, 'programme_mapping_unavailable', programmeId=assessment.selected_programme)
    profiles = list(LearnerProfile.objects.filter(enrolment_id=learner_id).values(
        'programme_id', 'cohort_id', 'group_id')[:2])
    if len(profiles) != 1 or profiles[0]['programme_id'] != external_programme or not profiles[0]['cohort_id']:
        return _unavailable(selected, 'cohort_assignment_unavailable', programmeId=assessment.selected_programme)
    profile = profiles[0]
    programme = CatalogProgramme.objects.filter(source_system='lms', external_id=external_programme,
                                                  archived_at__isnull=True).only('id').first()
    if not programme:
        return _unavailable(selected, 'catalogue_programme_unavailable', programmeId=assessment.selected_programme)
    cohort = CatalogCohort.objects.filter(programme=programme, source_system='lms',
                                           external_id=profile['cohort_id'], archived_at__isnull=True).only('id').first()
    if not cohort:
        return _unavailable(selected, 'catalogue_cohort_unavailable', programmeId=assessment.selected_programme)
    if not profile['group_id']:
        return _unavailable(selected, 'group_assignment_unavailable', programmeId=assessment.selected_programme)
    group = CatalogGroup.objects.filter(cohort=cohort, source_system='lms',
                                         external_id=profile['group_id'], archived_at__isnull=True).only('id').first()
    if not group:
        return _unavailable(selected, 'catalogue_group_unavailable', programmeId=assessment.selected_programme)
    deliveries = list(CatalogDelivery.objects.filter(group=group, archived_at__isnull=True)
                      .select_related('module', 'module_revision').only(
                          'module__name', 'module__programme_id', 'module__source_system', 'module__external_id',
                          'module_revision__id', 'module_revision__number', 'module_revision__ksbs',
                          'module_revision__module_id'))
    # A title inside the right group is insufficient evidence of an LMS identity.
    # Require the persisted source link for this exact imported module too.
    source_links = list(CatalogSourceLink.objects.filter(
        local_entity_type='module_template', local_record_id__in=[d.module_id for d in deliveries],
        source_system='lms', source_project_id=SOURCE_PROJECT_ID, source_branch_id=SOURCE_BRANCH_ID,
        source_entity_type='module_template').values('local_record_id', 'source_entity_id'))
    linked_ids = {row['local_record_id']: row['source_entity_id'] for row in source_links}
    by_name = {}
    for delivery in deliveries:
        module = delivery.module
        if (module.programme_id != programme.id or module.source_system != 'lms'
                or linked_ids.get(module.id) != module.external_id
                or delivery.module_revision.module_id != module.id):
            continue
        by_name.setdefault(module.name.strip().casefold(), []).append(delivery)
    chosen = {}
    for item in selected:
        matches = by_name.get(item['name'].strip().casefold(), [])
        if len(matches) == 1:
            chosen[item['id']] = matches[0]
    ksb_ids = {entry.get('id') for delivery in chosen.values()
               for entry in delivery.module_revision.ksbs if isinstance(entry, dict) and entry.get('id')}
    try:
        for identifier in ksb_ids:
            UUID(str(identifier))
    except (ValueError, TypeError, AttributeError):
        return _unavailable(selected, 'revision_ksb_invalid', programmeId=assessment.selected_programme)
    definitions = {str(k.id): k for k in CatalogKsb.objects.filter(id__in=ksb_ids, programme=programme,
                                                                     archived_at__isnull=True).only(
        'id', 'code', 'description', 'kind', 'standard', 'standard_version')}
    identities = {(item.standard, item.standard_version) for item in definitions.values()}
    if len(identities) > 1 or (definitions and any(not standard or not version for standard, version in identities)):
        return _unavailable(selected, 'standard_version_conflict', programmeId=assessment.selected_programme)
    result = []
    for item in selected:
        delivery = chosen.get(item['id'])
        if not delivery:
            result.append({**item, 'status': 'unavailable', 'reason': 'module_mapping_unavailable', 'ksbs': []})
            continue
        entries = delivery.module_revision.ksbs
        if any(not isinstance(e, dict) or e.get('id') not in definitions for e in entries):
            result.append({**item, 'status': 'unavailable', 'reason': 'revision_ksb_unavailable', 'ksbs': []})
            continue
        codes = Counter(definitions[e['id']].code for e in entries)
        if any(count != 1 for count in codes.values()):
            result.append({**item, 'status': 'unavailable', 'reason': 'revision_ksb_ambiguous', 'ksbs': []})
            continue
        result.append({**item, 'status': 'ready', 'revisionId': str(delivery.module_revision_id),
                       'revisionNumber': delivery.module_revision.number,
                       'ksbs': [{'id': e['id'], 'code': definitions[e['id']].code,
                                 'description': e.get('description') or definitions[e['id']].description,
                                 'kind': definitions[e['id']].kind,
                                 'standard': definitions[e['id']].standard,
                                 'standardVersion': definitions[e['id']].standard_version,
                                 **({'weight': e['mapping_weight']} if 'mapping_weight' in e else {})}
                                for e in entries]})
    from enrolment_api.models import ExtendedIlr
    radar = ExtendedIlr.objects.filter(learner_kind=kind, learner_id=learner_id).values_list(
        'wizard_draft__skillsRadar', flat=True).first()
    if not isinstance(radar, dict):
        from .service import read_legacy_radar
        radar = read_legacy_radar(kind, learner_id) or {}
    answers = radar.get('assessments') or {}
    saved_levels = {key: value.get('level') for key, value in answers.items()
                    if isinstance(key, str) and isinstance(value, dict) and isinstance(value.get('level'), str)}
    return {'status': 'ready', 'programmeId': assessment.selected_programme,
            'cohortId': str(cohort.id), 'groupId': str(group.id), 'modules': result,
            'savedLevels': saved_levels}
