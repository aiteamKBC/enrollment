"""The cohort dates a learner's page needs to know when Gateway opens.

    python manage.py test learner_api.tests_cohort_schedule

Gateway is a scheduled point in the programme: the practical period runs to the
cohort's end date, and the EPA window (``epa_months``) follows it. The learner
page was declaring someone "Gateway ready" as soon as their modules were
complete, which for a cohort ending in 2027 happened a year early — so the
payload now carries the cohort's own dates for the page to gate on.

The lookup is by cohort *name*, because that is what the enrolment record
stores, so these pin the parts that go wrong with names: a learner with no
cohort, a name that matches nothing, and a database that is unavailable.
"""
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.db import DatabaseError
from django.test import SimpleTestCase

from .learner_detail import _apply_cohort_schedule, _cohort_schedule, _iso_date

ROW = (date(2026, 8, 3), date(2027, 8, 2), date(2028, 1, 2), None, 5)


def _cursor_returning(row):
    """A stand-in for connections['default'].cursor() as a context manager."""
    cursor = MagicMock()
    cursor.fetchone.return_value = row
    context = MagicMock()
    context.__enter__.return_value = cursor
    connections = {'default': MagicMock()}
    connections['default'].cursor.return_value = context
    return connections, cursor


class CohortScheduleTests(SimpleTestCase):
    def test_it_reports_the_dates_the_page_gates_on(self):
        connections, _cursor = _cursor_returning(ROW)
        with patch('learner_api.learner_detail.connections', connections):
            schedule = _cohort_schedule('Final Cohort', 'Final Test')

        self.assertEqual(schedule, {
            'cohortStartDate': '2026-08-03',
            # The practical period's last day: Gateway opens once it has passed.
            'gatewayStartDate': '2027-08-02',
            'epaEndDate': '2028-01-02',
            'epaMonths': 5,
        })

    def test_an_override_end_date_wins_over_the_calculated_one(self):
        row = (date(2026, 8, 3), date(2027, 8, 2), date(2028, 1, 2), date(2028, 3, 1), 5)
        connections, _cursor = _cursor_returning(row)
        with patch('learner_api.learner_detail.connections', connections):
            self.assertEqual(_cohort_schedule('C', '')['epaEndDate'], '2028-03-01')

    def test_a_learner_with_no_cohort_is_not_looked_up_at_all(self):
        connections, _cursor = _cursor_returning(ROW)
        with patch('learner_api.learner_detail.connections', connections):
            self.assertEqual(_cohort_schedule('', 'Final Test'), {})
        connections['default'].cursor.assert_not_called()

    def test_a_cohort_name_that_matches_nothing_returns_nothing(self):
        connections, _cursor = _cursor_returning(None)
        with patch('learner_api.learner_detail.connections', connections):
            self.assertEqual(_cohort_schedule('Ghost cohort', ''), {})

    def test_a_database_error_does_not_take_the_learner_page_down(self):
        connections = {'default': MagicMock()}
        connections['default'].cursor.side_effect = DatabaseError('gone')
        with patch('learner_api.learner_detail.connections', connections):
            self.assertEqual(_cohort_schedule('Final Cohort', ''), {})


class ApplyScheduleTests(SimpleTestCase):
    """The payload always carries the keys, so the page never sees undefined."""

    def test_the_keys_are_present_even_when_there_is_no_cohort(self):
        detail = {}
        source = SimpleNamespace(cohort='', programme='')
        _apply_cohort_schedule(detail, source)

        self.assertEqual(detail, {
            'cohortStartDate': '', 'gatewayStartDate': '', 'epaEndDate': '', 'epaMonths': None,
        })

    def test_a_found_cohort_fills_them_in(self):
        connections, _cursor = _cursor_returning(ROW)
        detail = {}
        with patch('learner_api.learner_detail.connections', connections):
            _apply_cohort_schedule(detail, SimpleNamespace(cohort='Final Cohort', programme='Final Test'))

        self.assertEqual(detail['gatewayStartDate'], '2027-08-02')
        self.assertEqual(detail['epaMonths'], 5)


class IsoDateTests(SimpleTestCase):
    def test_dates_and_strings_both_become_an_iso_day(self):
        self.assertEqual(_iso_date(date(2027, 8, 2)), '2027-08-02')
        self.assertEqual(_iso_date('2027-08-02T00:00:00Z'), '2027-08-02')
        self.assertEqual(_iso_date(None), '')
        self.assertEqual(_iso_date(''), '')
