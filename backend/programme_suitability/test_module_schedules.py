import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, RequestFactory
from . import module_schedules as schedules
from .modules_rules import available_modules


class ModuleScheduleTests(SimpleTestCase):
    def setUp(self):
        self.config = json.loads(Path(__file__).with_name('modules_defaults.json').read_text(encoding='utf-8'))
        path = next(p for p in self.config['programmes'] if p['id'] == 'PCP_L6')['pathways'][2]
        self.modules = [m for m in available_modules(path['modules']) if m.get('isMandatory') or m['id'] == 'portfolio']
        self.home = {'status': 'resolved', 'cohortId': 'home-feb', 'name': 'February 2027'}
        self.row = SimpleNamespace(selected_programme='PCP_L6', selection_confirmed_at=True, modules_configuration=self.config,
            modules_configuration_version=24, modules_selection={'pathway':'chartered', 'modulesByPathway':{'chartered':[m['id'] for m in self.modules]}}, save=Mock())

    def offering(self, oid, title, **changes):
        return dict(module_catalogue_id=oid, title=title, programme_id='PROG-PCP-L6', cohort_id='delivery-oct',
                    cohort_name='October 2026', group_id=oid+'-group', group_name=oid, cohort_status='planned',
                    cohort_source_type='curriculum', start_date='2028-06-07', end_date='2028-09-20', **changes)

    @patch.object(schedules, 'read_all')
    def test_review_exact_titles_separate_groups_and_missing_matches(self, read):
        read.return_value = [self.offering('g1','Portfolio Management'), self.offering('g2','Portfolio Management'),
                             self.offering('ppc1','PPC'), self.offering('ppc2','PPC'), self.offering('unrelated','EVM')]
        before = json.dumps(self.home)
        result = schedules.project('PCP_L6','chartered',self.modules,self.home,review=True,today=date(2026,11,14))
        self.assertEqual(result['offeringCount'], 4)
        self.assertEqual(len(result['modules']), 6)
        self.assertTrue(all(not o['approved'] and o['reviewOnly'] for m in result['modules'] for o in m['offerings']))
        self.assertEqual(json.dumps(self.home), before)
        read.assert_called_once_with('PROG-PCP-L6', None)
        self.assertFalse(next(m for m in result['modules'] if m['academicModuleId']=='ai-project-management')['offerings'])

    @patch.object(schedules, 'read_all')
    def test_normal_learners_get_automatic_provisional_candidates(self, read):
        read.return_value=[self.offering('g1','Portfolio Management'),self.offering('g2','PPC')]
        result=schedules.project('PCP_L6','chartered',self.modules,self.home,today=date(2026,11,14))
        self.assertEqual(result['offeringCount'],2)
        offers=[o for m in result['modules'] for o in m['offerings']]
        self.assertTrue(all(o['matchConfidence']=='tentative' and o['provisional'] and not o['allowLocalSelection'] and not o['reviewOnly'] for o in offers))
        read.assert_called_once_with('PROG-PCP-L6',None)

    def test_future_uk_day_and_test_records(self):
        row=self.offering('g1','PPC')
        self.assertFalse(schedules.eligible(row,'PROG-PCP-L6',date(2028,6,7)))
        self.assertTrue(schedules.eligible(row,'PROG-PCP-L6',date(2028,6,6)))
        for update in [{'title':'PPC Test'}, {'cohort_status':'archived'}, {'programme_id':'OTHER'},
                       {'end_date':'2027-01-01'}, {'cohort_id':''}, {'start_date':None}]:
            self.assertFalse(schedules.eligible({**row,**update},'PROG-PCP-L6',date(2026,11,14)))

    @patch.object(schedules.lms_readonly, 'get_module_offerings')
    def test_all_pages_read_without_silent_total_limit(self, read):
        read.side_effect=[[{'module_catalogue_id':str(i)} for i in range(200)],
                          [{'module_catalogue_id':str(i)} for i in range(200,400)], [{'module_catalogue_id':'400'}]]
        self.assertEqual(len(schedules.read_all('PROG-PCP-L6')),401)
        self.assertEqual([c.kwargs['offset'] for c in read.call_args_list],[0,200,400])

    @patch.object(schedules.lms_readonly, 'get_module_offerings')
    def test_concurrent_duplicate_pages_fail_closed(self, read):
        read.return_value=[{'module_catalogue_id':'duplicate'}]*200
        with self.assertRaises(schedules.lms_readonly.LmsReadUnavailable): schedules.read_all('PROG-PCP-L6')

    @patch.object(schedules, 'read_all')
    def test_exact_normalization_and_new_offering_need_no_code_change(self, read):
        row=self.offering('new1','  PORTFOLIO  MANAGEMENT (APMG) ')
        read.side_effect=[[row],[row,{**row,'module_catalogue_id':'new2','group_id':'new-group','start_date':'2028-07-01'}]]
        first=schedules.project('PCP_L6','chartered',self.modules,self.home,today=date(2026,11,14))
        second=schedules.project('PCP_L6','chartered',self.modules,self.home,today=date(2026,11,14))
        self.assertEqual(first['offeringCount'],1)
        self.assertEqual(second['offeringCount'],2)
        offer=next(m for m in second['modules'] if m['academicModuleId']=='portfolio')['offerings'][0]
        self.assertEqual(offer['matchConfidence'],'exact_title')
        self.assertFalse(offer['provisional'])

    def test_normalization_preserves_qualification_words_and_no_fuzzy_substitution(self):
        self.assertEqual(schedules.normalized_title('Marketing Strategy&Planning'),schedules.normalized_title('Marketing Strategy and Planning'))
        self.assertNotEqual(schedules.normalized_title('Portfolio Management (APMG)'),schedules.normalized_title('Portfolio Management'))
        self.assertEqual(schedules.matching_confidence({'title':'AI in Project Management'},'AI in Project Control',())[0],None)
        self.assertEqual(schedules.matching_confidence({'title':'Risk, Quality and Issue Management'},'Risk Management',())[0],None)

    @patch.object(schedules, 'read_all')
    def test_admin_confirmed_martech_alias_is_scoped_to_its_programme_and_module(self, read):
        read.return_value = [{**self.offering('confirmed', 'MarTech'), 'programme_id': 'PROG-ME-L4', 'cohort_id': 'home-feb', 'group_id': 'g1', 'weeks_number': 20, 'sessions_number': 99},
                             {**self.offering('possible', 'Martech - Fri'), 'programme_id': 'PROG-ME-L4', 'cohort_id': 'home-feb', 'group_id': 'g1'}]
        module = {'id': 'martech', 'title': 'Marketing Technology (MarTech)'}
        result = schedules.project('ME_L4', None, [module], {**self.home, 'groupId': 'g1'}, today=date(2026, 11, 17))
        offers = result['modules'][0]['offerings']
        self.assertEqual([o['matchConfidence'] for o in offers], ['confirmed_alias', 'tentative'])
        self.assertFalse(offers[0]['provisional'])
        self.assertFalse(offers[0]['approved'])  # Matching is separate from booking.
        self.assertEqual(offers[0]['lmsTitle'], 'MarTech')
        self.assertEqual(offers[0]['weeks'], 20)
        unrelated = schedules.project('ME_L4', None, [{**module, 'id': 'another-module'}], self.home, today=date(2026, 11, 17))
        self.assertEqual(unrelated['offeringCount'], 0)

    @patch.object(schedules, 'read_all')
    def test_me_mm_apm_only_query_and_return_home_cohort_and_allocated_group(self, read):
        for programme_id in ('ME_L4', 'MM_L6', 'APM_L4'):
            for review in (False, True):
                with self.subTest(programme=programme_id, review=review):
                    programme = next(p for p in self.config['programmes'] if p['id'] == programme_id)
                    module = programme['modules'][0]
                    pid = schedules.PROGRAMMES[programme_id]
                    correct = {**self.offering('saved', module['title']), 'programme_id': pid,
                               'cohort_id': 'home-feb', 'group_id': 'g1'}
                    # Deliberately make the mocked reader return an out-of-scope
                    # row despite its cohort parameter: response filtering must
                    # independently enforce programme-specific restrictions.
                    read.return_value = [correct,
                        {**correct, 'module_catalogue_id': 'other-cohort', 'cohort_id': 'delivery-oct'},
                        {**correct, 'module_catalogue_id': 'other-group', 'group_id': 'g2'}]
                    read.reset_mock()
                    result = schedules.project(programme_id, None, [module], {**self.home, 'groupId': 'g1'},
                        today=date(2026, 11, 17), review=review, home_group={'status': 'assigned', 'groupId': 'g1'})
                    self.assertEqual([o['id'] for o in result['modules'][0]['offerings']], ['saved'])
                    self.assertFalse(result['modules'][0]['offerings'][0]['informationOnly'])
                    self.assertFalse(result['modules'][0]['offerings'][0]['allowLocalSelection'])
                    read.assert_called_once_with(pid, 'home-feb')

    @patch.object(schedules, 'read_all')
    def test_me_mm_apm_never_fall_back_to_other_cohorts_or_an_unallocated_eligible_group(self, read):
        for programme_id in ('ME_L4', 'MM_L6', 'APM_L4'):
            module = next(p for p in self.config['programmes'] if p['id'] == programme_id)['modules'][0]
            for home, group in (
                ({'status': 'pending', 'cohortId': None}, {'status': 'assigned', 'groupId': 'g1'}),
                (self.home, {'status': 'unavailable', 'groupId': None}),
                (self.home, {'status': 'eligible', 'groupId': 'g1'}),
            ):
                with self.subTest(programme=programme_id, home=home, group=group):
                    read.reset_mock()
                    result = schedules.project(programme_id, None, [module], home,
                        today=date(2026, 11, 17), home_group=group)
                    self.assertEqual(result['offeringCount'], 0)
                    self.assertNotEqual((result['homeGroup'] or {}).get('status'), 'eligible')
                    read.assert_not_called()

    @patch.object(schedules, 'read_all')
    def test_lms_home_schedule_never_borrows_another_groups_delivery(self, read):
        first = {**self.offering('fixed', 'Social Media'), 'programme_id': 'PROG-ME-L4', 'cohort_id': 'home-feb', 'group_id': 'g1'}
        read.return_value = [first, {**first, 'module_catalogue_id': 'other', 'group_id': 'g2'}]
        home = {**self.home, 'source': 'lms', 'groupId': 'g1'}
        result = schedules.project('ME_L4', None, [{'id': 'social-media', 'title': 'Social Media'}], home, today=date(2026, 11, 14))
        self.assertEqual(result['offeringCount'], 1)
        self.assertEqual(result['modules'][0]['offerings'][0]['groupId'], 'g1')

    @patch.object(schedules, 'read_all')
    def test_fixed_home_schedule_is_exact_and_not_selectable(self, read):
        read.return_value=[{**self.offering('home-offer','Social Media'), 'programme_id':'PROG-ME-L4','cohort_id':'home-feb'}]
        result=schedules.project('ME_L4',None,[{'id':'social-media','title':'Social Media'}],{**self.home, 'groupId': 'home-offer-group'},today=date(2026,11,14))
        offer=result['modules'][0]['offerings'][0]
        self.assertEqual(offer['matchConfidence'],'exact_title')
        self.assertFalse(offer['allowLocalSelection'])

    @patch.object(schedules, 'read_all')
    def test_staff_read_is_get_only_and_does_not_pin_or_save(self, read):
        request=RequestFactory().get('/schedule/?review=1')
        request.user=SimpleNamespace(is_authenticated=True,is_staff=True)
        with patch.object(schedules, 'learner_record', return_value=object()), patch.object(schedules.Assessment.objects,'filter') as manager, patch.object(schedules,'read_home_cohort',return_value=self.home), patch.object(schedules,'preview_home_group',return_value={'status': 'unavailable', 'groupId': None}):
            manager.return_value.first.return_value=self.row
            read.return_value=[]
            response=schedules.learner_module_schedule(request,kind='apprenticeship',learner_id=1)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response['Cache-Control'],'private, no-store')
        self.row.save.assert_not_called()
        request=RequestFactory().put('/schedule/')
        request.user=SimpleNamespace(is_authenticated=True,is_staff=True)
        self.assertEqual(schedules.learner_module_schedule(request,kind='apprenticeship',learner_id=1).status_code,405)

    @patch.object(schedules, 'read_all')
    def test_nonstaff_review_denied_even_with_development_auth_bypass(self, read):
        request=RequestFactory().get('/schedule/?review=1')
        request.user=SimpleNamespace(is_authenticated=True,is_staff=False,is_superuser=False)
        with patch('enrolment_api.auth.auth_required',return_value=False):
            self.assertEqual(schedules.learner_module_schedule(request,kind='apprenticeship',learner_id=1).status_code,403)
        read.assert_not_called()

    def test_other_learner_cannot_read_schedule(self):
        request=RequestFactory().get('/schedule/')
        request.login_account=SimpleNamespace(is_active=True,role='learner',subject_id=2,subject_type='learner')
        request.user=SimpleNamespace(is_authenticated=False)
        with patch('enrolment_api.auth.auth_required',return_value=True):
            self.assertEqual(schedules.learner_module_schedule(request,kind='apprenticeship',learner_id=1).status_code,404)

    @patch.object(schedules, 'read_all')
    def test_home_uses_one_saved_or_eligible_group_while_other_cohorts_keep_both(self, read):
        read.return_value = [
            {**self.offering('home1', 'Portfolio Management (APMG)'), 'cohort_id': 'home-feb', 'group_id': 'g1'},
            {**self.offering('home2', 'Portfolio Management (APMG)'), 'cohort_id': 'home-feb', 'group_id': 'g2'},
            self.offering('other1', 'Portfolio Management (APMG)'), self.offering('other2', 'Portfolio Management (APMG)'),
        ]
        for status in ('assigned', 'allocated', 'eligible'):
            result = schedules.project('PCP_L6', 'chartered', self.modules, self.home,
                today=date(2026, 11, 17), home_group={'status': status, 'groupId': 'g1'})
            offers = next(m for m in result['modules'] if m['academicModuleId'] == 'portfolio')['offerings']
            self.assertEqual({o['id'] for o in offers}, {'home1', 'other1', 'other2'})
            self.assertTrue(all(not o['allowLocalSelection'] for o in offers))
        blocked = schedules.project('PCP_L6', 'chartered', self.modules, self.home,
            today=date(2026, 11, 17), home_group={'status': 'unavailable', 'groupId': None})
        offers = next(m for m in blocked['modules'] if m['academicModuleId'] == 'portfolio')['offerings']
        self.assertEqual({o['id'] for o in offers}, {'other1', 'other2'})

    @patch.object(schedules, 'read_all')
    def test_saved_marketing_group_keeps_marketing_impact_delivery_visible(self, read):
        read.return_value = [{**self.offering('impact', 'Marketing Impact and Planning'),
            'programme_id': 'PROG-ME-L4', 'cohort_id': 'home-feb', 'group_id': 'g1'}]
        result = schedules.project('ME_L4', None, [{'id': 'marketing-impact', 'title': 'Marketing Impact and Planning'}],
            {**self.home, 'source': 'lms', 'groupId': 'g1'}, today=date(2026, 11, 17),
            home_group={'status': 'assigned', 'groupId': 'g1'})
        self.assertEqual(result['modules'][0]['offerings'][0]['id'], 'impact')
