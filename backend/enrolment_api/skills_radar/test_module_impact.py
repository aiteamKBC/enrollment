from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from programme_suitability.catalog_models import (
    CatalogCohort, CatalogDelivery, CatalogGroup, CatalogKsb, CatalogModule,
    CatalogModuleRevision, CatalogProgramme, CatalogSourceLink,
)
from programme_suitability.models import Assessment
from enrollment_service.lms_catalogue_source import SOURCE_BRANCH_ID, SOURCE_PROJECT_ID

from .module_impact import learner_module_impact, selected_modules


class ModuleImpactTests(TestCase):
    def setUp(self):
        self.assessment = Assessment.objects.create(
            learner_id=917, learner_kind='apprenticeship', configuration_version=1,
            configuration={}, pathway='', selected_programme='ME_L4',
            selection_confirmed_at=timezone.now(),
            modules_configuration={'programmes': [{'id': 'ME_L4', 'modules': [
                {'id': 'social-media', 'title': 'Social Media', 'active': True},
                {'id': 'martech', 'title': 'Marketing Technology (MarTech)', 'active': True},
                {'id': 'marketing-impact', 'title': 'Marketing Impact and Planning', 'active': True},
            ]}]},
        )
        self.programme = CatalogProgramme.objects.create(
            name='Marketing Executive', code='PROG-ME-L4', level=4,
            total_cost=6000, epa_cost=1000, source_system='lms', external_id='PROG-ME-L4')
        self.cohort = CatalogCohort.objects.create(
            programme=self.programme, name='2027', start_date=date(2027, 1, 1),
            training_end_date=date(2028, 1, 1), training_duration_months=12,
            epa_period=3, epa_unit='months', source_system='lms', external_id='cohort-2027')
        self.group = CatalogGroup.objects.create(
            cohort=self.cohort, name='Group A', weekday=1,
            start_time='09:00', end_time='17:00', source_system='lms', external_id='group-a')
        self.ksb = CatalogKsb.objects.create(
            programme=self.programme, code='K5', description='Knowledge five',
            kind='Knowledge', standard='ST0596', standard_version='1.0')
        self._profile = patch('enrolment_api.skills_radar.module_impact.LearnerProfile.objects.filter')
        profile_query = self._profile.start()
        profile_query.return_value.values.return_value.__getitem__.return_value = [
            {'programme_id': 'PROG-ME-L4', 'cohort_id': 'cohort-2027', 'group_id': 'group-a'}]
        self.addCleanup(self._profile.stop)

    def delivery(self, name, weight=None):
        module = CatalogModule.objects.create(
            programme=self.programme, name=name, weeks=1, source_system='lms',
            external_id=name.lower().replace(' ', '-'))
        CatalogSourceLink.objects.create(
            source_system='lms', source_project_id=SOURCE_PROJECT_ID, source_branch_id=SOURCE_BRANCH_ID,
            source_entity_type='module_template', source_entity_id=module.external_id,
            local_entity_type='module_template', local_record_id=module.id,
            accepted_fingerprint='test', last_synced_at=timezone.now())
        entry = {'id': str(self.ksb.id), 'code': 'K5', 'description': 'Knowledge five'}
        if weight is not None:
            entry['mapping_weight'] = {'total_weight': weight, 'mapping_count': 1}
        revision = CatalogModuleRevision.objects.create(
            module=module, number=1, name=name, weeks=1, ksbs=[entry])
        CatalogDelivery.objects.create(group=self.group, module=module, module_revision=revision,
                                       start_date=date(2027, 2, 1))
        return module, revision

    def test_shared_ksb_is_returned_once_per_module_with_independent_weight(self):
        self.delivery('Social Media', '1060.00')
        self.delivery('Marketing Technology (MarTech)', '40.00')
        result = learner_module_impact('apprenticeship', 917)
        self.assertEqual([m['status'] for m in result['modules']], ['ready', 'ready', 'unavailable'])
        self.assertEqual([result['modules'][i]['ksbs'][0]['weight']['total_weight'] for i in (0, 1)],
                         ['1060.00', '40.00'])
        self.assertEqual(result['modules'][2]['reason'], 'module_mapping_unavailable')

    def test_registered_http_route_returns_module_impact(self):
        self.delivery('Social Media', '1060.00')
        path = '/enrolment_api/skills-radar/apprenticeship/917/module-impact/'
        with override_settings(ROOT_URLCONF='enrollment_service.urls'):
            match = resolve(path)
        self.assertEqual(match.url_name, 'skills-radar-module-impact')
        request = RequestFactory().get(path)
        request.user = SimpleNamespace(is_authenticated=True)
        request.login_account = None
        with patch('enrolment_api.extended_ilr.KINDS') as kinds:
            kinds.get.return_value.objects.filter.return_value.exists.return_value = True
            response = match.func(request, **match.kwargs)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'"social-media"', response.content)

    def test_historical_pinned_revision_keeps_missing_weight(self):
        module, first = self.delivery('Social Media')
        CatalogModuleRevision.objects.create(
            module=module, number=2, name='Social Media', weeks=1,
            ksbs=[{'id': str(self.ksb.id), 'code': 'K5', 'description': 'Knowledge five',
                   'mapping_weight': {'total_weight': '1060.00', 'mapping_count': 27}}])
        result = learner_module_impact('apprenticeship', 917)
        self.assertEqual(result['modules'][0]['revisionId'], str(first.id))
        self.assertNotIn('weight', result['modules'][0]['ksbs'][0])

    def test_cohort_or_programme_mismatch_fails_closed(self):
        self.delivery('Social Media', '40.00')
        with patch('enrolment_api.skills_radar.module_impact.LearnerProfile.objects.filter') as query:
            query.return_value.values.return_value.__getitem__.return_value = [
                {'programme_id': 'PROG-OTHER', 'cohort_id': 'cohort-2027', 'group_id': 'group-a'}]
            result = learner_module_impact('apprenticeship', 917)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['reason'], 'cohort_assignment_unavailable')

    def test_same_title_without_verified_source_link_is_unavailable(self):
        module, _ = self.delivery('Social Media', '40.00')
        CatalogSourceLink.objects.filter(local_record_id=module.id).delete()
        result = learner_module_impact('apprenticeship', 917)
        self.assertEqual(result['modules'][0]['reason'], 'module_mapping_unavailable')

    def test_reads_saved_ratings_without_changing_suitability_or_rpl(self):
        self.delivery('Social Media', '40.00')
        with patch('enrolment_api.models.ExtendedIlr.objects.filter') as query, \
                patch('enrollment_service.lms_readonly.get_assigned_cohort') as lms_read, \
                CaptureQueriesContext(connection) as queries:
            query.return_value.values_list.return_value.first.return_value = {
                'assessments': {'profile-K5': {'level': 'mastery', 'note': 'Evidence retained'}}}
            result = learner_module_impact('apprenticeship', 917)
        self.assertEqual(result['savedLevels'], {'profile-K5': 'mastery'})
        self.assertLessEqual(len(queries), 9)
        lms_read.assert_not_called()
        self.assessment.refresh_from_db()
        self.assertEqual(self.assessment.modules_selection, None)
        self.assertEqual(self.assessment.review_status, 'PRELIMINARY_RECOMMENDATION')

    def test_pcp_uses_only_the_saved_active_pathway_selection(self):
        self.assessment.selected_programme = 'PCP_L6'
        self.assessment.modules_configuration = {'programmes': [{'id': 'PCP_L6', 'pathways': [
            {'id': 'operational', 'active': True, 'modules': [
                {'id': 'required', 'title': 'Required', 'active': True, 'isMandatory': True},
                {'id': 'chosen', 'title': 'Chosen', 'active': True},
                {'id': 'other', 'title': 'Other', 'active': True}]},
            {'id': 'strategic', 'active': True, 'modules': [
                {'id': 'wrong-path', 'title': 'Wrong path', 'active': True}]},
        ]}]}
        self.assessment.modules_selection = {'programme': 'PCP_L6', 'pathway': 'operational',
                                             'modulesByPathway': {'operational': ['chosen'],
                                                                  'strategic': ['wrong-path']}}
        self.assertEqual([item['id'] for item in selected_modules(self.assessment)], ['required', 'chosen'])
