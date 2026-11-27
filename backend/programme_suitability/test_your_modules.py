"""Synthetic local-only publication and read tests. The runner blocks networking."""
import copy
from datetime import date, datetime, timezone as utc
from decimal import Decimal
from unittest.mock import patch
from types import SimpleNamespace

from django.db import DatabaseError
from django.test import RequestFactory, TestCase
from django.utils import timezone

from learner_api.models import EnrolmentUser, LearnerProfile
from .catalog_models import CatalogProgramme, CatalogCohort, CatalogGroup, CatalogModule, CatalogModuleRevision, CatalogDelivery
from .configuration import defaults
from .models import Assessment
from .your_modules import KEY, capture_catalogue, learner_data, published_layout, your_modules


class YourModulesTests(TestCase):
    databases = {'default', 'enrolment'}

    def setUp(self):
        self.programme = CatalogProgramme.objects.create(name='Synthetic confirmed programme', code='PROG-ME-L4',
            level=4, description='', total_cost=Decimal('6000'), epa_cost=Decimal('500'), status='draft')
        self.module = CatalogModule.objects.create(programme=self.programme, name='Synthetic academic module', weeks=4)
        self.revision = CatalogModuleRevision.objects.create(module=self.module, number=1, name=self.module.name,
            weeks=4, ksbs=[], weekly_topics=[])
        self.home = self.cohort('February 2027', '2027-01-05', '2028-01-20')
        self.early = self.cohort('October 2026', '2026-10-01', '2028-12-31')
        self.later = self.cohort('June 2027', '2027-06-01', '2028-12-31')
        self.home_delivery = self.delivery(self.home, '2027-02-01')
        self.early_delivery = self.delivery(self.early, '2027-01-05')
        self.late_delivery = self.delivery(self.later, '2028-01-20')
        self.snapshot = capture_catalogue()
        self.layout = {'steps': [{'slug': 'your-modules', 'hidden': False,
            'items': [{'key': 'block.yourModules', 'hidden': False}]}], KEY: self.snapshot}
        self.learner = EnrolmentUser(id=41, username='Synthetic learner', email='synthetic@example.test',
            programme=self.programme.name, cohort=self.home.name, group='', learning_plan=[],
            learner_type='apprenticeship', status='FullUser', programme_status='Onboarding')
        EnrolmentUser.all_learners.bulk_create([self.learner])
        self.assessment = Assessment.objects.create(learner_id=41, learner_kind='apprenticeship',
            configuration=defaults(), configuration_version=1, pathway='marketing',
            selected_programme='ME_L4', result={'recommendedProgramme': 'MM_L6'},
            confirmed_at=timezone.now(), selection_confirmed_at=timezone.now())
        self.pin = patch('programme_suitability.your_modules.published_layout', return_value=(7, self.layout))
        self.pin.start(); self.addCleanup(self.pin.stop)

    def cohort(self, name, start, end):
        return CatalogCohort.objects.create(programme=self.programme, name=name, start_date=start,
            training_end_date=end, epa_period=3, epa_unit='months')

    def delivery(self, cohort, start):
        group = CatalogGroup.objects.create(cohort=cohort, name=f'Synthetic group {start}',
            weekday=2, start_time='09:00', end_time='11:00')
        return CatalogDelivery.objects.create(group=group, module=self.module,
            module_revision=self.revision, start_date=start)

    def data(self, today=date(2026, 11, 27)):
        return learner_data('apprenticeship', 41, today)

    def test_confirmed_programme_published_modules_and_actual_cohort_dates(self):
        result = self.data()
        self.assertEqual(result['programmeId'], str(self.programme.pk))
        self.assertEqual(result['selectedProgramme'], 'ME_L4')
        self.assertEqual(result['modules'][0]['id'], str(self.module.pk))
        self.assertEqual(result['homeCohort']['start'], '2027-01-05')
        self.assertEqual(result['homeCohort']['end'], '2028-01-20')
        self.assertEqual(result['version'], 7)
        self.assertEqual(result['source'], 'published-programme-structure')

    def test_inclusive_first_last_days_earlier_later_cohorts_and_unclipped_end(self):
        result = self.data()
        alternatives = [o for o in result['offerings'] if not o['home']]
        self.assertEqual({o['start'] for o in alternatives}, {'2027-01-05', '2028-01-20'})
        self.assertEqual({o['cohortName'] for o in alternatives}, {self.early.name, self.later.name})
        self.assertEqual({o['cohortStart'] for o in alternatives}, {'2026-10-01', '2027-06-01'})
        last = next(o for o in alternatives if o['start'] == '2028-01-20')
        self.assertEqual(last['end'], '2028-02-16')
        self.assertTrue(last['extendsWindow'])

    def test_before_after_window_and_already_started_excluded(self):
        self.delivery(self.early, '2027-01-04')
        self.delivery(self.later, '2028-01-21')
        self.layout[KEY] = capture_catalogue()
        result = self.data(today=date(2027, 1, 6))
        self.assertEqual([o['start'] for o in result['offerings'] if not o['home']], ['2028-01-20'])
        # Home teaching is still shown even when it has already started.
        self.assertTrue(any(o['home'] for o in self.data(today=date(2028, 1, 20))['offerings']))

    def test_no_group_assignment_still_shows_genuine_group_deliveries_without_writes(self):
        before = Assessment.objects.filter(pk=self.assessment.pk).values().get()
        with patch('enrollment_service.lms_readonly.get_assigned_cohort', side_effect=AssertionError('No LMS')):
            result = self.data()
        self.assertEqual(len(result['offerings']), 3)
        self.assertTrue(all(o['groupName'].startswith('Synthetic group') for o in result['offerings']))
        self.learner.refresh_from_db(); self.assessment.refresh_from_db()
        self.assertEqual((self.learner.programme, self.learner.cohort, self.learner.group), (self.programme.name, self.home.name, ''))
        self.assertEqual(self.learner.learning_plan, [])
        self.assertFalse(LearnerProfile.objects.filter(enrolment_id=41).exists())
        self.assertEqual(Assessment.objects.filter(pk=self.assessment.pk).values().get(), before)

    def test_live_edits_never_change_an_existing_publication(self):
        original = copy.deepcopy(self.layout)
        CatalogModule.objects.filter(pk=self.module.pk).update(name='Unpublished edit', weeks=99)
        CatalogDelivery.objects.filter(pk=self.home_delivery.pk).update(start_date='2027-03-03')
        result = self.data()
        self.assertEqual(result['modules'][0]['name'], 'Synthetic academic module')
        self.assertEqual(result['modules'][0]['weeks'], 4)
        self.assertEqual(next(o for o in result['offerings'] if o['home'])['start'], '2027-02-01')
        self.assertEqual(self.layout, original)

    def test_archived_records_not_published_and_missing_schedules_are_empty(self):
        CatalogDelivery.objects.update(archived_at=timezone.now())
        self.layout[KEY] = capture_catalogue()
        result = self.data()
        self.assertEqual(len(result['modules']), 1)
        self.assertEqual(result['offerings'], [])
        CatalogModule.objects.update(archived_at=timezone.now())
        self.assertEqual(capture_catalogue()['programmes'][0]['modules'], [])

    def test_other_programme_and_cross_module_revision_cannot_publish_as_an_offering(self):
        other = CatalogProgramme.objects.create(name='Other', code='OTHER', level=4,
            total_cost=100, epa_cost=0, status='active')
        CatalogModule.objects.filter(pk=self.module.pk).update(programme=other)
        with self.assertRaisesRegex(ValueError, 'inconsistent module delivery'):
            capture_catalogue()

    def test_missing_old_pin_snapshot_does_not_upgrade_or_fallback(self):
        self.layout.pop(KEY)
        self.assertEqual(self.data()['status'], 'unavailable')
        self.assertIn('saved wizard version', self.data()['message'])

    def test_withdrawn_delivery_is_not_offered_even_in_an_older_immutable_publication(self):
        original = copy.deepcopy(self.layout)
        CatalogDelivery.objects.filter(pk=self.early_delivery.pk).update(archived_at=timezone.now())
        self.assertNotIn(str(self.early_delivery.pk), [o['id'] for o in self.data()['offerings']])
        self.assertEqual(self.layout, original)

    def test_cleared_cohort_is_not_restored_from_historical_audit(self):
        EnrolmentUser.all_learners.filter(pk=41).update(cohort='')
        Assessment.objects.filter(pk=self.assessment.pk).update(review={'programmeAssignment': {'cohortId': str(self.home.pk)}})
        self.assertIn('has not been saved', self.data()['message'])
        self.learner.refresh_from_db(); self.assertEqual(self.learner.cohort, '')

    def test_errors_and_methods_are_isolated_to_this_endpoint(self):
        request = RequestFactory().get('/your-modules/')
        request.user = SimpleNamespace(is_authenticated=True, is_staff=True)
        with patch('programme_suitability.your_modules.learner_data', side_effect=DatabaseError('synthetic failure')):
            self.assertEqual(your_modules(request, kind='apprenticeship', learner_id=41).status_code, 503)
        request = RequestFactory().put('/your-modules/')
        request.user = SimpleNamespace(is_authenticated=True, is_staff=True)
        self.assertEqual(your_modules(request, kind='apprenticeship', learner_id=41).status_code, 405)

    def test_admin_and_student_get_the_same_read_only_data(self):
        request = RequestFactory().get('/your-modules/')
        request.login_account = SimpleNamespace(role='learner', subject_id=41, is_active=True)
        first = your_modules(request, kind='apprenticeship', learner_id=41)
        del request.login_account
        request.user = SimpleNamespace(is_authenticated=True, is_staff=True)
        second = your_modules(request, kind='apprenticeship', learner_id=41)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.content, second.content)
        self.assertEqual(second['Cache-Control'], 'private, no-store')

    def test_saved_profile_ids_are_verified_and_conflicting_placement_is_not_reassigned(self):
        profile = LearnerProfile(id=51, enrolment_id=41, full_name='Synthetic learner', email='synthetic@example.test',
            programme=self.programme.name, programme_id=str(self.programme.pk), cohort=self.home.name,
            cohort_id=str(self.home.pk), learning_plan=[])
        LearnerProfile.objects.bulk_create([profile])
        self.assertEqual(self.data()['status'], 'ready')
        LearnerProfile.objects.filter(pk=51).update(programme_id='different-programme')
        self.assertEqual(self.data()['status'], 'unavailable')
        profile.refresh_from_db(); self.assertEqual(profile.programme_id, 'different-programme')
        self.learner.refresh_from_db(); self.assertEqual(self.learner.cohort, self.home.name)

    def test_multiple_real_groups_remain_separate_offerings(self):
        second = self.delivery(self.home, '2027-04-01')
        self.layout[KEY] = capture_catalogue()
        home = [o for o in self.data()['offerings'] if o['home']]
        self.assertEqual({o['id'] for o in home}, {str(self.home_delivery.pk), str(second.pk)})
        self.assertEqual(len({o['groupId'] for o in home}), 2)

    def test_current_date_is_evaluated_in_london_including_summer_time(self):
        with patch('programme_suitability.your_modules.timezone.now', return_value=datetime(2027, 6, 30, 23, 30, tzinfo=utc.utc)):
            result = learner_data('apprenticeship', 41)
        self.assertEqual(result['evaluatedOn'], '2027-07-01')

    def test_a_learner_cannot_read_another_learners_schedule(self):
        request = RequestFactory().get('/your-modules/')
        request.login_account = SimpleNamespace(role='learner', subject_id=42, is_active=True)
        self.assertEqual(your_modules(request, kind='apprenticeship', learner_id=41).status_code, 404)

    def test_missing_submitted_publication_never_falls_back_to_latest(self):
        from enrolment_api.tests_wizard_layout import FakeConnections, FakeCursor
        from enrolment_api import wizard_layout
        cursor = FakeCursor([(None, 99, 7)])
        with patch('programme_suitability.your_modules.connections', FakeConnections(cursor)), \
                patch.object(wizard_layout, 'read_version', return_value=None) as saved, \
                patch.object(wizard_layout, 'read_current') as latest:
            self.assertEqual(published_layout('apprenticeship', 41), (None, None))
        saved.assert_called_once_with(cursor, 99)
        latest.assert_not_called()
        self.assertTrue(all(sql.startswith('SELECT') for sql, _ in cursor.executed))
