"""LMS templates are mocked; production recurrence/eligibility/OTJ engine is real."""
import copy
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from learner_api import learning_plan, programme_meetings as meetings
from learner_api.models import LearnerProfile
from curriculum_api import review_instances, review_otjh
from . import test_group_capacity as fixtures
from .programme_identity import CURRICULUM_PROGRAMME_IDS


class ProgrammeMeetingsTests(TestCase):
    learner = fixtures.GroupCapacityTests.learner

    def setUp(self):
        fixtures.GroupCapacityTests.setUp(self)
        self.source.start_date, self.source.end_date = date(2027, 1, 31), date(2028, 1, 31)
        self.source.programme_status = 'Active'
        self.source.group = 'Group 1'
        self.source.learning_plan = [{'moduleId': 'manual', 'moduleTitle': 'Custom saved content', 'hours': 123}]
        self.source.save()
        self.profile = LearnerProfile.objects.create(enrolment_id=self.source.pk, full_name='Isolated learner',
            programme_id='PROG-ME-L4', cohort_id='home', cohort='Feb 2027', group_id='g1', group_name='Group 1')
        self.templates = [self.template('coaching'), self.template('progress', unit='weeks', interval=12, hours=3),
            self.template('support', counted=False), self.template('gateway', interval=13)]
        self.raw = {'review_templates': self.templates, 'review_occurrence_overrides': [], 'blockers': []}
        self.read = patch.object(meetings, 'read_programme_reviews', return_value=self.raw).start()
        self.addCleanup(patch.stopall)

    def template(self, identifier, unit='months', interval=1, hours=2.5, counted=True):
        return dict(id=identifier, programme_id='PROG-ME-L4', name=identifier, enabled=True,
            recurrence_interval=interval, recurrence_unit=unit, occurrence_count=None,
            applicable_statuses=['Active'], applicability={'scope': 'programme'},
            expected_otjh=Decimal(str(hours)), counts_towards_otjh=counted, deleted_at=None)

    def test_real_recurrence_and_otj_engine_agree_and_keep_individual_dates(self):
        rows, reason = meetings.confirmed_programme_meetings(self.source)
        self.assertEqual(reason, '')
        by_id = {r['reviewTemplateId']: r for r in rows}
        self.assertEqual(set(by_id), {'coaching', 'progress', 'support'})
        coaching = by_id['coaching']
        self.assertEqual(coaching['plannedDates'][:2], ['2027-02-28', '2027-03-28'])
        self.assertEqual(coaching['hours'], 30)
        self.assertEqual(by_id['support']['hours'], 0)
        expected = review_otjh.planned_meetings('PROG-ME-L4', self.profile.pk, 'Active', self.source.start_date,
            self.source.start_date, self.source.end_date,
            template_cache={'otjh:PROG-ME-L4': [t for t in self.templates if review_otjh.template_otjh_hours(t)]},
            occurrence_resolver=lambda t, *args: review_instances.resolve_learner_occurrences(t, *args, overrides_by_date={}))
        self.assertEqual(sum(r['hours'] for r in rows), sum(r['hours'] for r in expected))
        self.assertTrue(all(r['scheduleKind'] == 'projected' and not r['bookedAppointmentsLoaded'] for r in rows))
        self.assertEqual(learning_plan._totals(self.source.learning_plan, rows)['totalHours'],
            123 + sum(r['hours'] for r in expected))

    def test_confirmed_programme_only_for_me_mm_apm_pcp(self):
        for code, name in [('ME_L4', 'Marketing Executive Level 4'), ('MM_L6', 'Marketing Manager Level 6'),
                ('APM_L4', 'Associate Project Manager Level 4'), ('PCP_L6', 'Project Controls Professional Level 6')]:
            self.row.selected_programme = code
            self.row.result = {'recommendedProgramme': 'MM_L6' if code != 'MM_L6' else 'ME_L4'}
            self.row.save()
            self.source.programme = name
            pid = CURRICULUM_PROGRAMME_IDS[code]
            self.profile.programme_id = pid
            self.profile.save()
            self.raw['review_templates'] = [{**self.template(code), 'programme_id': pid}]
            result = meetings.confirmed_programme_meetings(self.source)
            self.assertEqual([r['name'] for r in result[0]], [code])
            self.read.assert_called_with(pid, self.profile.pk, self.source.start_date, self.source.end_date)

    def test_only_applicable_status_group_and_cohort(self):
        self.templates.extend([
            {**self.template('other-status'), 'applicable_statuses': ['Delivery']},
            {**self.template('other-group'), 'applicability': {'scope': 'group', 'ids': ['g2']}},
            {**self.template('home-group'), 'applicability': {'scope': 'group', 'ids': ['g1']}},
            {**self.template('other-cohort'), 'applicability': {'scope': 'cohort', 'ids': ['elsewhere']}}])
        names = {r['name'] for r in meetings.confirmed_programme_meetings(self.source)[0]}
        self.assertIn('home-group', names)
        self.assertFalse(names & {'other-status', 'other-group', 'other-cohort'})

    def test_real_start_date_and_exact_profile_override_identity(self):
        self.raw['review_occurrence_overrides'] = [dict(review_id='coaching', programme_id='PROG-ME-L4',
            learner_id=self.profile.pk, occurrence_date=date(2027, 2, 28), action='skip', deleted_at=None)]
        rows = meetings.confirmed_programme_meetings(self.source)[0]
        self.assertNotIn('2027-02-28', next(r for r in rows if r['name'] == 'coaching')['plannedDates'])
        self.source.start_date = date(2027, 3, 3)
        rows = meetings.confirmed_programme_meetings(self.source)[0]
        self.assertEqual(next(r for r in rows if r['name'] == 'coaching')['startDate'], '2027-04-03')

    def test_missing_dates_do_not_fall_back_to_cohort_and_missing_permissions_are_explicit(self):
        for key in ('start_date', 'end_date'):
            original = getattr(self.source, key)
            setattr(self.source, key, None)
            self.assertEqual(meetings.confirmed_programme_meetings(self.source)[1], 'no-dates')
            setattr(self.source, key, original)
        self.read.assert_not_called()
        self.raw['blockers'] = [{'code': 'select_denied', 'table': 'review_templates', 'columns': ['name']}]
        result = meetings.confirmed_programme_meetings(self.source)
        self.assertEqual(result[1], 'unavailable')
        self.assertEqual(result.blockers, self.raw['blockers'])

    def test_refresh_is_idempotent_read_only_and_preserves_custom_plan(self):
        before = copy.deepcopy(self.source.learning_plan)
        with CaptureQueriesContext(connection) as queries:
            first = learning_plan._meetings(self.source)
            second = learning_plan._meetings(self.source)
        self.assertEqual(first, second)
        self.assertEqual(len({r['reviewTemplateId'] for r in first[0]}), len(first[0]))
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for q in queries))
        self.source.refresh_from_db()
        self.assertEqual(self.source.learning_plan, before)

    def test_foreign_programme_or_override_never_substituted(self):
        self.templates[0]['programme_id'] = 'PROG-PCP-L6'
        self.assertEqual(meetings.confirmed_programme_meetings(self.source).blockers[0]['code'], 'review_template_programme_conflict')
        self.templates[0]['programme_id'] = 'PROG-ME-L4'
        self.raw['review_occurrence_overrides'] = [dict(programme_id='PROG-ME-L4', learner_id=987654)]
        self.assertEqual(meetings.confirmed_programme_meetings(self.source).blockers[0]['code'], 'review_override_identity_unverified')

    def test_empty_disabled_and_duplicate_template_sources(self):
        self.raw['review_templates'] = []
        self.assertEqual(meetings.confirmed_programme_meetings(self.source)[1], 'no-reviews')
        self.raw['review_templates'] = [self.template('duplicate'), self.template('duplicate')]
        self.assertEqual(meetings.confirmed_programme_meetings(self.source).blockers[0]['code'], 'duplicate_review_template_identity')

    def test_invalid_recurrence_and_missing_placement_are_explicit_not_invented(self):
        self.raw['review_templates'] = [{**self.template('invalid'), 'recurrence_interval': 0},
            {**self.template('unknown-scope'), 'applicability': {'scope': 'unknown'}}]
        result = meetings.confirmed_programme_meetings(self.source)
        self.assertFalse(result[0])
        self.assertEqual({b['code'] for b in result.blockers}, {'review_recurrence_unavailable', 'review_placement_scope_unavailable'})
        self.raw['review_templates'] = [{**self.template('group'), 'applicability': {'scope': 'group', 'ids': ['g1']}}]
        self.profile.group_id = None
        self.profile.save()
        self.assertEqual(meetings.confirmed_programme_meetings(self.source).blockers[0]['code'], 'review_placement_unverified')

    def test_existing_default_otj_only_callers_keep_their_behaviour(self):
        with patch.object(review_otjh, 'otjh_template_rows', return_value=[self.templates[0]]) as read:
            result = review_otjh.planned_meetings('PROG-ME-L4', self.profile.pk, 'Active', self.source.start_date,
                self.source.start_date, self.source.end_date,
                occurrence_resolver=lambda t, *args: review_instances.resolve_learner_occurrences(t, *args, overrides_by_date={}))
        read.assert_called_once_with('PROG-ME-L4')
        self.assertEqual([r['name'] for r in result], ['coaching'])
        self.assertEqual(result[0]['hours'], 30)

    def test_onboarding_forecast_groups_each_review_and_keeps_execution_rules_unchanged(self):
        originals = copy.deepcopy(self.templates)
        active = meetings.confirmed_programme_meetings(self.source)[0]
        self.source.programme_status = 'Onboarding'
        self.source.save(update_fields=['programme_status'])
        with CaptureQueriesContext(connection) as queries:
            rows, reason = meetings.confirmed_programme_meetings(self.source)
            again = meetings.confirmed_programme_meetings(self.source)[0]
        self.assertEqual(reason, '')
        self.assertEqual(rows, again)
        self.assertEqual(self.templates, originals)
        self.assertEqual(len(rows), len({r['reviewTemplateId'] for r in rows}))
        self.assertEqual([(r['name'], r['plannedDates'], r['occurrences'], r['hours']) for r in rows],
            [(r['name'], r['plannedDates'], r['occurrences'], r['hours']) for r in active])
        self.assertTrue(all(r['planningDuringOnboarding'] and not r['executionEligibleNow'] for r in rows))
        for template in self.templates:
            self.assertEqual(review_instances.resolve_learner_occurrences(template, self.profile.pk, 'Onboarding',
                self.source.start_date, self.source.start_date, self.source.end_date, overrides_by_date={}), [])
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for q in queries))

    def test_onboarding_forecast_still_respects_placement_skips_and_occurrence_limits(self):
        self.source.programme_status = 'Onboarding'
        self.templates[0]['occurrence_count'] = 2
        self.templates.append({**self.template('wrong-group'), 'applicability': {'scope': 'group', 'ids': ['g2']}})
        self.raw['review_occurrence_overrides'] = [dict(review_id='coaching', programme_id='PROG-ME-L4',
            learner_id=self.profile.pk, occurrence_date=date(2027, 2, 28), action='skip', deleted_at=None)]
        rows = meetings.confirmed_programme_meetings(self.source)[0]
        self.assertNotIn('wrong-group', [r['name'] for r in rows])
        coaching = next(r for r in rows if r['name'] == 'coaching')
        self.assertEqual(coaching['plannedDates'], ['2027-03-28'])
        self.assertEqual(coaching['occurrences'], 1)
        self.assertEqual(coaching['hours'], 2.5)
