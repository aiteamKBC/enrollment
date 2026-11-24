import os
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.db import DatabaseError, IntegrityError
from django.test import Client, SimpleTestCase, TestCase

from learner_api.models import StaffUser
from login.models import LoginAccount
from login.sessions import COOKIE_NAME, issue_session
from .catalog_models import CatalogCohort, CatalogDelivery, CatalogKsb, CatalogModule, CatalogModuleRevision, CatalogProgramme, CatalogWeeklyTopic
from .catalog_pricing import price_programme, written_agreement_costs
from .catalog_services import calculate_cohort_schedule


BASE = '/enrolment_api/programme-structure/catalog/'
PROGRAMMES = BASE + 'programmes/'
PROGRAMME = {
    'name': 'Catalogue fixture', 'code': 'test-prog', 'level': 4,
    'total_cost': '11000.00', 'epa_cost': '1000.00', 'funding_band_maximum': '12000.00',
}
COHORT = {
    'name': 'Test cohort', 'start_date': '2027-01-01', 'training_duration_months': 1, 'epa_period': 1,
}
GROUP = {'name': 'Test group', 'weekday': 1, 'start_time': '09:00', 'end_time': '11:00'}


@patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1', 'API_REQUIRE_AUTH': '1'})
class CatalogApiTests(TestCase):
    def setUp(self):
        self.coach = StaffUser.objects.create(id=120, username='Fixture Coach', email='coach@example.test', access='coach')
        self.tutor = StaffUser.objects.create(id=121, username='Fixture Tutor', email='tutor@example.test', access='tutor')
        self.staff_client = self.session(122, 'enrolment')
        self.csrf = self.staff_client.get('/learner_api/enrollment-csrf/').json()['csrfToken']

    def session(self, staff_id, access):
        StaffUser.objects.create(id=staff_id, username='Fixture User', access=access)
        account = LoginAccount.objects.create(
            id=staff_id, subject_type='staff', subject_id=staff_id, role='staff',
            email=f'catalog-{staff_id}@example.test', is_active=True,
        )
        client = Client(enforce_csrf_checks=True)
        client.cookies[COOKIE_NAME] = issue_session(account)[0]
        return client

    def request(self, method, url, data=None, status=200, *, revision='latest'):
        headers = {'HTTP_X_CSRFTOKEN': self.csrf}
        if method in ('patch', 'delete') and revision is not None:
            if revision == 'latest':
                current = self.staff_client.get(url).json()
                revision = current.get('programme', current)['revision']
            headers['HTTP_IF_MATCH'] = f'"{revision}"'
        response = getattr(self.staff_client, method)(
            url, data=data if data is not None else {}, content_type='application/json', **headers,
        )
        self.assertEqual(response.status_code, status, response.content[:700])
        return response.json() if response.content else None

    def create(self, url, data):
        return self.request('post', url, data, 201)

    def test_bulk_import_and_mapping_patch_preserve_pinned_coverage(self):
        programme = self.create(PROGRAMMES, PROGRAMME)
        path = f"{PROGRAMMES}{programme['id']}/"
        library = path + 'ksbs/'
        self.request('post', library, {'ksbs': [
            {'code': 'K1', 'description': 'Initial'}, {'code': 'k1', 'description': 'Duplicate'},
        ]}, 400)
        self.assertFalse(CatalogKsb.objects.exists())
        rows = [{'code': 'K1', 'description': 'Planning'}, {'code': 'S1', 'description': 'Delivery'}]
        imported = self.create(library, {'ksbs': rows})['ksbs']
        self.request('post', library, {'ksbs': [{'code': 'B1', 'description': 'Review'}, rows[0]]}, 400)
        self.assertEqual(CatalogKsb.objects.count(), 2)
        knowledge, skill = [row['id'] for row in imported]
        cohort = self.create(path + 'cohorts/', COHORT)
        group = self.create(path + 'groups/', GROUP | {'cohort_id': cohort['id']})
        module = self.create(path + 'modules/', {
            'name': 'Planning module', 'weeks': 2, 'lectures': 3, 'reviews': 2,
            'weekly_topics': [{'week': 1, 'content': 'Original topic'}], 'ksb_ids': [knowledge],
        })
        second = self.create(path + 'modules/', {'name': 'Delivery module', 'weeks': 1, 'ksb_ids': [skill]})
        delivery = self.create(path + 'deliveries/', {
            'group_id': group['id'], 'module_id': module['id'], 'start_date': '2027-01-04', 'tutor_id': str(self.tutor.uuid),
        })
        other = self.create(PROGRAMMES, PROGRAMME | {'code': 'FOREIGN'})
        foreign = self.create(f"{PROGRAMMES}{other['id']}/ksbs/", {'code': 'K1', 'description': 'Other programme'})
        url = path + f"modules/{module['id']}/"
        for ids in ([skill, skill], [foreign['id']]):
            self.request('patch', url, {'ksb_ids': ids}, 400)
        covered = self.request('patch', url, {'ksb_ids': [knowledge, skill]}, revision=module['revision'])
        self.assertEqual(covered['current_revision']['number'], 2)
        self.assertEqual(set(covered['ksb_ids']), {knowledge, skill})
        self.assertEqual((covered['lectures'], covered['reviews'], covered['weekly_topics']), (3, 2, module['weekly_topics']))
        self.request('patch', url, {'ksb_ids': []}, 409, revision=module['revision'])
        self.assertEqual(self.request('get', path + f"modules/{second['id']}/")['ksb_ids'], [skill])
        self.assertEqual(self.request('get', library)['ksbs'], imported)
        pinned = self.request('get', path + f"deliveries/{delivery['id']}/")
        self.assertEqual(pinned['curriculum'], delivery['curriculum'])

    def test_empty_list_and_existing_access_controls(self):
        self.assertEqual(self.staff_client.get(BASE + 'readiness/').json(), {'state': 'ready'})
        self.assertEqual(Client().get(PROGRAMMES).status_code, 401)
        self.assertEqual(self.staff_client.get(PROGRAMMES).json(), {'programmes': []})
        self.assertEqual(self.staff_client.post(PROGRAMMES, PROGRAMME, content_type='application/json').status_code, 403)
        self.assertEqual(self.staff_client.get(BASE + 'staff/').json(), {
            'coaches': [{'id': str(self.coach.uuid), 'name': self.coach.username, 'email': self.coach.email}],
            'tutors': [{'id': str(self.tutor.uuid), 'name': self.tutor.username, 'email': self.tutor.email}],
        })
        self.assertEqual(self.session(123, 'coach').get(PROGRAMMES).status_code, 404)

    def test_unapplied_schema_has_distinct_response_from_database_failure(self):
        with patch('enrolment_api.programme_catalog.catalogue_schema_ready', return_value=False):
            self.assertEqual(self.staff_client.get(BASE + 'readiness/').json(), {'state': 'schema_not_ready'})
            self.assertEqual(self.request('get', PROGRAMMES, status=503)['code'], 'catalog_schema_not_ready')
            self.assertEqual(self.request('post', PROGRAMMES, PROGRAMME, 503)['code'], 'catalog_schema_not_ready')
        with patch('enrolment_api.programme_catalog.catalogue_schema_ready', side_effect=DatabaseError('fixture')):
            with self.assertLogs('enrolment_api.programme_catalog', level='ERROR'):
                error = self.request('get', BASE + 'readiness/', status=503)
            self.assertEqual(error['code'], 'catalog_database_error')
        self.assertFalse(CatalogProgramme.objects.exists())

    def test_manual_hierarchy_and_rates_are_per_group(self):
        programme = self.create(PROGRAMMES, PROGRAMME)
        self.assertEqual((programme['code'], programme['status'], programme['source_system']), ('TEST-PROG', 'draft', 'manual'))
        path = f"{PROGRAMMES}{programme['id']}/"
        cohort = self.create(path + 'cohorts/', COHORT)
        self.assertEqual((cohort['duration_days'], cohort['apprenticeship_end_date']), (31, '2027-02-28'))
        self.assertEqual((cohort['training_duration_months'], cohort['apprenticeship_duration_months'], cohort['epa_unit']), (1, 2, 'months'))
        group = self.create(path + 'groups/', GROUP | {'cohort_id': cohort['id'], 'coach_id': str(self.coach.uuid)})
        second = self.create(path + 'groups/', GROUP | {'cohort_id': cohort['id'], 'name': 'Parallel group'})
        known = self.create(path + 'ksbs/', {'code': 'k1', 'description': 'Knowledge'})
        skill = self.create(path + 'ksbs/', {'code': 's1', 'description': 'Skill'})
        module = self.create(path + 'modules/', {
            'name': 'Test module', 'weeks': 2, 'lectures': 8, 'reviews': 2, 'books': 1,
            'weekly_topics': [{'week': 1, 'content': 'First topic'}], 'ksb_ids': [known['id'], skill['id']],
        })
        delivery = {'module_id': module['id'], 'start_date': '2027-01-04', 'tutor_id': str(self.tutor.uuid)}
        first_schedule = self.create(path + 'deliveries/', delivery | {'group_id': group['id']})
        self.create(path + 'deliveries/', delivery | {'group_id': second['id']})
        self.assertEqual(first_schedule['end_date'], '2027-01-17')
        self.assertIsNone(first_schedule['end_date_override'])
        self.assertEqual(len(self.staff_client.get(path).json()['deliveries']), 2)
        listed = self.staff_client.get(PROGRAMMES).json()['programmes'][0]
        self.assertEqual(listed['counts'], {'cohorts': 1, 'groups': 2, 'scheduled_modules': 2})
        self.assertEqual(self.staff_client.get(path + 'pricing/').json()['pricing']['rates'], {
            'training_hour': None, 'review': None, 'book': None,
        })
        quote = self.staff_client.get(path + f"pricing/?group_id={group['id']}").json()['pricing']
        self.assertEqual(quote['counts'], {'lectures': 8, 'reviews': 2, 'books': 1})
        self.assertEqual(quote['rates'], {'training_hour': '400.00', 'review': '750.00', 'book': '500.00'})
        updated = self.request('patch', path + f"modules/{module['id']}/", {'weekly_topics': [
            {'week': 1, 'content': 'Edited'}, {'week': 2, 'content': 'Another topic'},
        ]})
        self.assertEqual(updated['weekly_topics'][0]['id'], module['weekly_topics'][0]['id'])
        self.assertEqual(updated['weekly_topics'][0]['content'], 'Edited')
        self.request('patch', path + f"modules/{module['id']}/", {'weekly_topics': [{'week': 2, 'content': 'Another topic'}]})
        self.assertIsNotNone(CatalogWeeklyTopic.objects.get(pk=module['weekly_topics'][0]['id']).archived_at)
        self.request('delete', path + f"modules/{module['id']}/", status=409)
        self.request('delete', path + f"groups/{group['id']}/", status=204)
        listed = self.staff_client.get(PROGRAMMES).json()['programmes'][0]
        self.assertEqual(listed['counts'], {'cohorts': 1, 'groups': 1, 'scheduled_modules': 1})
        self.assertEqual(self.staff_client.get(path + f"pricing/?group_id={group['id']}").status_code, 404)
        self.request('patch', path + f"deliveries/{first_schedule['id']}/", {'start_date': '2027-01-05'}, 409)
        self.request('delete', path, status=204)
        archived = self.staff_client.get(path).json()
        self.assertEqual(archived['programme']['status'], 'archived')
        self.assertEqual(len(archived['deliveries']), 2)
        listed = self.staff_client.get(PROGRAMMES).json()['programmes'][0]
        self.assertEqual(listed['counts'], {'cohorts': 0, 'groups': 0, 'scheduled_modules': 0})

    def test_cross_programme_invalid_input_and_scheduling_conflicts(self):
        programme = self.create(PROGRAMMES, PROGRAMME)
        foreign = self.create(PROGRAMMES, PROGRAMME | {'code': 'OTHER'})
        path, foreign_path = [f"{PROGRAMMES}{row['id']}/" for row in (programme, foreign)]
        cohort = self.create(path + 'cohorts/', COHORT)
        foreign_cohort = self.create(foreign_path + 'cohorts/', COHORT)
        group_payload = GROUP | {'cohort_id': cohort['id']}
        self.request('post', path + 'groups/', group_payload | {'cohort_id': foreign_cohort['id']}, 400)
        self.request('post', path + 'groups/', group_payload | {'coach_id': str(self.tutor.uuid)}, 400)
        self.request('post', path + 'groups/', group_payload | {'start_time': '09:00+01:00'}, 400)
        self.request('post', path + 'groups/', group_payload | {'end_time': '08:00'}, 400)
        group = self.create(path + 'groups/', group_payload)
        self.request('patch', path, {'epa_cost': '12000.00'}, 400)
        self.request('patch', path, {'total_cost': '1.001'}, 400)
        self.request('patch', path, {'source_system': 'lms', 'external_id': 'untrusted'}, 400)
        self.request('post', path + 'cohorts/', COHORT | {'training_end_date': '2026-12-31'}, 400)
        self.request('post', path + 'cohorts/', COHORT | {'epa_unit': 'weeks'}, 400)
        cohort_path = path + f"cohorts/{cohort['id']}/"
        self.request('post', path + 'modules/', {'name': 'Bad', 'weeks': 1, 'ksb_ids': [str(uuid4())]}, 400)
        self.request('post', path + 'modules/', {
            'name': 'Bad', 'weeks': 1, 'weekly_topics': [{'week': 2, 'content': 'Too late'}],
        }, 400)
        module = self.create(path + 'modules/', {'name': 'Missing counts', 'weeks': 2, 'lectures': 0})
        delivery = {'group_id': group['id'], 'module_id': module['id'], 'start_date': '2027-01-22', 'tutor_id': str(self.tutor.uuid)}
        self.request('post', path + 'deliveries/', delivery, 400)
        self.request('post', foreign_path + 'deliveries/', delivery | {'start_date': '2027-01-04'}, 400)
        other_cohort = self.create(path + 'cohorts/', COHORT | {'name': 'Other cohort'})
        invalid = self.request('post', path + 'deliveries/', delivery | {
            'start_date': '2027-01-04', 'cohort_id': other_cohort['id'],
        }, 400)
        self.assertIn('selected cohort', str(invalid['fields']['group_id']))
        scheduled = self.create(path + 'deliveries/', delivery | {'start_date': '2027-01-04', 'cohort_id': cohort['id']})
        self.assertNotIn('cohort_id', scheduled)
        self.request('patch', cohort_path, {'start_date': '2027-01-15'}, 400)
        updated = self.request('patch', path + f"modules/{module['id']}/", {'weeks': 10})
        with patch('programme_suitability.catalog_services.timezone.localdate', return_value=date(2027, 1, 1)):
            self.request('patch', path + f"deliveries/{scheduled['id']}/", {
                'module_revision_id': updated['current_revision']['id'],
            }, 400)
        price = self.staff_client.get(path + f"pricing/?group_id={group['id']}").json()['pricing']
        self.assertEqual(price['counts'], {'lectures': 0, 'reviews': None, 'books': None})
        self.assertTrue(all(rate is None for rate in price['rates'].values()))

    def test_cohorts_own_independent_durations_and_programme_edits_keep_historical_values(self):
        programme = self.create(PROGRAMMES, PROGRAMME)
        path = f"{PROGRAMMES}{programme['id']}/"
        self.assertNotIn('duration_months', programme)
        self.assertIsNone(CatalogProgramme.objects.get(pk=programme['id']).duration_months)
        CatalogProgramme.objects.filter(pk=programme['id']).update(duration_months=12)
        cohort = self.create(path + 'cohorts/', COHORT | {'start_date': '2027-01-31'})
        cohort_path = path + f"cohorts/{cohort['id']}/"
        self.assertEqual((cohort['training_end_date'], cohort['apprenticeship_end_date']), ('2027-02-27', '2027-03-30'))
        self.request('patch', path, {'name': 'Renamed programme', 'epao': 'Fixture EPAO'})
        self.assertEqual(CatalogProgramme.objects.get(pk=programme['id']).duration_months, 12)
        renamed = self.request('patch', cohort_path, {'name': 'Renamed cohort', 'start_date': cohort['start_date'], 'epa_period': 1})
        for field in ('training_end_date', 'apprenticeship_end_date', 'training_duration_months', 'apprenticeship_duration_months'):
            self.assertEqual(renamed[field], cohort[field])
        next_cohort = self.create(path + 'cohorts/', COHORT | {'training_duration_months': 2})
        self.assertEqual((next_cohort['training_duration_months'], next_cohort['training_end_date']), (2, '2027-02-28'))
        moved = self.request('patch', cohort_path, {'start_date': '2027-02-01'})
        self.assertEqual((moved['training_end_date'], moved['apprenticeship_end_date']), ('2027-02-28', '2027-03-31'))
        recalculated = self.request('patch', cohort_path, {'training_duration_months': 3})
        self.assertEqual((recalculated['training_end_date'], recalculated['apprenticeship_end_date']), ('2027-04-30', '2027-05-31'))
        self.assertEqual(recalculated['apprenticeship_duration_months'], 4)
        self.assertEqual(self.request('get', path + f"cohorts/{next_cohort['id']}/"), next_cohort)

    def test_cohort_duration_is_required_and_bounded(self):
        programme = self.create(PROGRAMMES, PROGRAMME)
        path = f"{PROGRAMMES}{programme['id']}/cohorts/"
        missing = {key: value for key, value in COHORT.items() if key != 'training_duration_months'}
        self.assertIn('training_duration_months', self.request('post', path, missing, 400)['fields'])
        for value in (None, 0, 121, 1.5):
            with self.subTest(value=value):
                error = self.request('post', path, COHORT | {'training_duration_months': value}, 400)
                self.assertIn('training_duration_months', error['fields'])

    def test_legacy_cohort_dates_and_units_are_preserved_until_explicit_recalculation(self):
        programme = self.create(PROGRAMMES, PROGRAMME)
        cohort = CatalogCohort.objects.create(
            programme_id=programme['id'], name='Legacy cohort', start_date=date(2027, 1, 5),
            training_end_date=date(2027, 3, 10), epa_period=2, epa_unit='weeks',
        )
        path = f"{PROGRAMMES}{programme['id']}/"
        cohort_path = path + f'cohorts/{cohort.pk}/'
        self.request('patch', path, {'name': 'Renamed programme'})
        renamed = self.request('patch', cohort_path, {'name': 'Legacy rename'})
        self.assertEqual((renamed['training_end_date'], renamed['apprenticeship_end_date'], renamed['epa_unit']), ('2027-03-10', '2027-03-24', 'weeks'))
        self.assertIsNone(renamed['training_duration_months'])
        manual = self.request('patch', cohort_path, {'training_end_override': '2027-03-12'})
        self.assertEqual((manual['automatic_training_end_date'], manual['apprenticeship_end_date'], manual['epa_unit']),
                         ('2027-03-10', '2027-03-26', 'weeks'))
        reset = self.request('patch', cohort_path, {'training_end_override': None})
        self.assertEqual((reset['training_end_date'], reset['apprenticeship_end_date']), ('2027-03-10', '2027-03-24'))
        self.request('patch', cohort_path, {'start_date': '2027-01-06'}, 400)
        self.request('patch', cohort_path, {'training_duration_months': 2}, 400)
        recalculated = self.request('patch', cohort_path, {'training_duration_months': 2, 'epa_period': 2})
        self.assertEqual((recalculated['training_end_date'], recalculated['apprenticeship_end_date'], recalculated['epa_unit']), ('2027-03-04', '2027-05-04', 'months'))
        self.assertEqual(recalculated['apprenticeship_duration_months'], 4)

    def test_overrides_persist_independently_and_resets_use_saved_duration(self):
        programme = self.create(PROGRAMMES, PROGRAMME)
        path = f"{PROGRAMMES}{programme['id']}/"
        cohort = self.create(path + 'cohorts/', COHORT | {'start_date': '2028-01-01'})
        cohort_path = path + f"cohorts/{cohort['id']}/"
        self.assertIsNone(cohort['training_end_override'])
        self.request('patch', cohort_path, {'training_end_override': '2027-12-31'}, 400)
        manual = self.request('patch', cohort_path, {'training_end_override': '2028-01-30'})
        self.assertEqual((manual['training_end_date'], manual['apprenticeship_end_date']), ('2028-01-30', '2028-02-28'))
        self.assertEqual((manual['automatic_training_end_date'], manual['duration_days']), ('2028-01-31', 30))
        self.request('patch', cohort_path, {'apprenticeship_end_override': '2028-03-15'}, 409, revision=cohort['revision'])
        self.request('patch', cohort_path, {'apprenticeship_end_override': '2028-01-29'}, 400)
        manual = self.request('patch', cohort_path, {'apprenticeship_end_override': '2028-03-15'})
        self.request('patch', cohort_path, {'training_end_override': '2028-03-16'}, 400)
        duration_changed = self.request('patch', cohort_path, {'training_duration_months': 2})
        self.assertEqual(duration_changed['automatic_training_end_date'], '2028-02-29')
        self.assertEqual((duration_changed['training_end_date'], duration_changed['apprenticeship_end_date']), ('2028-01-30', '2028-03-15'))
        moved = self.request('patch', cohort_path, {'training_end_override': '2028-02-01'})
        self.assertEqual(moved['apprenticeship_end_date'], '2028-03-15')
        persisted = self.staff_client.get(cohort_path).json()
        self.assertEqual((persisted['training_end_override'], persisted['apprenticeship_end_override']), ('2028-02-01', '2028-03-15'))
        self.assertEqual(persisted['training_duration_months'], 2)
        reset_epa = self.request('patch', cohort_path, {'apprenticeship_end_override': None})
        self.assertEqual(reset_epa['apprenticeship_end_date'], '2028-03-01')
        reset_training = self.request('patch', cohort_path, {'training_end_override': None})
        self.assertEqual((reset_training['training_end_date'], reset_training['apprenticeship_end_date']), ('2028-02-29', '2028-03-31'))
        self.assertIsNone(reset_training['training_end_override'])
        self.assertIsNone(reset_training['apprenticeship_end_override'])

    def test_manual_training_bounds_control_existing_and_new_deliveries(self):
        programme = self.create(PROGRAMMES, PROGRAMME)
        path = f"{PROGRAMMES}{programme['id']}/"
        cohort = self.create(path + 'cohorts/', COHORT | {'training_end_override': '2027-02-07'})
        group = self.create(path + 'groups/', GROUP | {'cohort_id': cohort['id']})
        module = self.create(path + 'modules/', {'name': 'One week', 'weeks': 1})
        self.create(path + 'deliveries/', {
            'start_date': '2027-02-01', 'group_id': group['id'], 'module_id': module['id'], 'tutor_id': str(self.tutor.uuid),
        })
        self.request('patch', path + f"cohorts/{cohort['id']}/", {'training_end_override': None}, 400)

    def test_create_template_and_schedule_in_one_transaction_with_editable_dates(self):
        programme = self.create(PROGRAMMES, PROGRAMME)
        path = f"{PROGRAMMES}{programme['id']}/"
        cohort = self.create(path + 'cohorts/', COHORT | {'training_duration_months': 2})
        other_cohort = self.create(path + 'cohorts/', COHORT | {'name': 'Other cohort'})
        group = self.create(path + 'groups/', GROUP | {'cohort_id': cohort['id']})
        ksb = self.create(path + 'ksbs/', {'code': 'K1', 'description': 'Planning'})
        schedule = {
            'group_id': group['id'], 'cohort_id': cohort['id'], 'tutor_id': str(self.tutor.uuid),
            'start_date': '2027-01-04', 'end_date': '2027-02-10',
            'new_template': {'name': 'Inline template', 'weeks': 2, 'lectures': 2, 'reviews': 1, 'books': 2,
                             'ksb_ids': [ksb['id']], 'weekly_topics': [{'week': 1, 'content': 'Week one'}]},
        }
        deliveries = path + 'deliveries/'
        for invalid, field in [
            ({'end_date': '2027-01-03'}, 'end_date'), ({'end_date': '2027-03-01'}, 'end_date'),
            ({'start_date': '2026-12-31'}, 'start_date'), ({'cohort_id': other_cohort['id']}, 'group_id'),
            ({'tutor_id': None}, 'tutor_id'), ({'new_template': 'invalid'}, 'new_template'),
        ]:
            with self.subTest(field=field, invalid=invalid):
                self.assertIn(field, self.request('post', deliveries, schedule | invalid, 400)['fields'])
        # The second write fails after the template and revision have been inserted.
        with patch.object(CatalogDelivery.objects, 'create', side_effect=IntegrityError('fixture')):
            self.request('post', deliveries, schedule, 409)
        self.assertFalse(CatalogModule.objects.exists())
        self.assertFalse(CatalogModuleRevision.objects.exists())
        self.assertFalse(CatalogWeeklyTopic.objects.exists())
        delivery = self.create(deliveries, schedule)
        module = self.request('get', path + f"modules/{delivery['module_id']}/")
        self.assertEqual(delivery['end_date'], '2027-02-10')
        self.assertEqual(delivery['end_date_override'], '2027-02-10')
        self.assertEqual(delivery['curriculum']['id'], module['current_revision']['id'])
        self.assertEqual(delivery['curriculum']['ksbs'][0]['id'], ksb['id'])
        self.assertEqual(delivery['curriculum']['weekly_topics'][0]['content'], 'Week one')
        cohort_url = path + f"cohorts/{cohort['id']}/"
        self.request('patch', cohort_url, {'training_duration_months': 1}, 400)
        url = deliveries + f"{delivery['id']}/"
        self.request('patch', url, {'start_date': '2027-02-11'}, 400)
        self.request('patch', url, {'new_template': schedule['new_template']}, 400)
        edited = self.request('patch', url, {'start_date': '2027-01-08', 'end_date': '2027-01-08'})
        self.assertEqual(edited['end_date'], '2027-01-08')
        self.assertEqual(edited['curriculum'], delivery['curriculum'])
        self.assertEqual(self.request('get', url)['tutor_id'], str(self.tutor.uuid))
        self.request('patch', cohort_url, {'training_duration_months': 1})
        self.request('patch', url, {'start_date': '2027-01-25', 'end_date': '2027-01-28'})
        self.assertIn('end_date', self.request('patch', url, {'end_date': None}, 400)['fields'])
        self.assertEqual(self.request('get', url)['end_date_override'], '2027-01-28')
        reset = self.request('patch', url, {'start_date': '2027-01-08', 'end_date': None})
        self.assertEqual(reset['end_date'], '2027-01-21')
        self.assertIsNone(reset['end_date_override'])
        moved = self.request('patch', url, {'start_date': '2027-01-09'})
        self.assertEqual(moved['end_date'], '2027-01-22')
        self.assertEqual(moved['curriculum'], delivery['curriculum'])

    def test_staff_options_and_writes_share_active_role_rules_and_keep_existing_assignments(self):
        multi = StaffUser.objects.create(id=124, username='Multi role', access='coach', access_extra='tutor')
        inactive = StaffUser.objects.create(id=125, username='Inactive Coach', access='coach', status=' InActive ')
        disabled = StaffUser.objects.create(id=126, username='Disabled Tutor', access='tutor', status='disabled')
        StaffUser.objects.create(id=127, username='Archived Tutor', access='tutor', status='Archived')
        options = self.staff_client.get(BASE + 'staff/').json()
        self.assertEqual({row['id'] for row in options['coaches']}, {str(self.coach.uuid), str(multi.uuid)})
        self.assertEqual({row['id'] for row in options['tutors']}, {str(self.tutor.uuid), str(multi.uuid)})
        programme = self.create(PROGRAMMES, PROGRAMME)
        path = f"{PROGRAMMES}{programme['id']}/"
        cohort = self.create(path + 'cohorts/', COHORT)
        group_values = GROUP | {'cohort_id': cohort['id'], 'start_time': '09:00:25', 'end_time': '09:00:45'}
        for person in (self.tutor, inactive):
            self.request('post', path + 'groups/', group_values | {'coach_id': str(person.uuid)}, 400)
        group = self.create(path + 'groups/', group_values | {'coach_id': str(self.coach.uuid)})
        module = self.create(path + 'modules/', {'name': 'One week', 'weeks': 1})
        delivery_values = {'start_date': '2027-01-04', 'group_id': group['id'], 'module_id': module['id']}
        for person in (self.coach, disabled):
            self.request('post', path + 'deliveries/', delivery_values | {'tutor_id': str(person.uuid)}, 400)
        delivery = self.create(path + 'deliveries/', delivery_values | {'tutor_id': str(multi.uuid)})
        StaffUser.objects.filter(pk__in=[self.coach.pk, multi.pk]).update(status='archived')
        renamed = self.request('patch', path + f"groups/{group['id']}/", {'name': 'Renamed'})
        self.assertEqual((renamed['coach_id'], renamed['start_time'], renamed['end_time']),
                         (str(self.coach.uuid), '09:00:25', '09:00:45'))
        moved = self.request('patch', path + f"deliveries/{delivery['id']}/", {'start_date': '2027-01-05'})
        self.assertEqual(moved['tutor_id'], str(multi.uuid))

    def test_source_owned_records_are_read_only(self):
        programme = self.create(PROGRAMMES, PROGRAMME)
        path = f"{PROGRAMMES}{programme['id']}/"
        CatalogProgramme.objects.filter(pk=programme['id']).update(
            source_system='fixture-source', external_id='example-reference',
        )
        self.assertEqual(self.staff_client.get(path).json()['programme']['external_id'], 'example-reference')
        self.request('patch', path, {'name': 'Manual override'}, 409)
        self.request('delete', path, status=409)
        self.request('post', path + 'cohorts/', COHORT, 409)

    def test_compare_and_swap_covers_each_editable_record_and_archive(self):
        programme = self.create(PROGRAMMES, PROGRAMME)
        path = f"{PROGRAMMES}{programme['id']}/"
        cohort = self.create(path + 'cohorts/', COHORT)
        group = self.create(path + 'groups/', GROUP | {'cohort_id': cohort['id']})
        ksb = self.create(path + 'ksbs/', {'code': 'k1', 'description': 'Initial criteria'})
        module = self.create(path + 'modules/', {'name': 'Initial module', 'weeks': 2})
        delivery = self.create(path + 'deliveries/', {
            'group_id': group['id'], 'module_id': module['id'], 'start_date': '2027-01-04', 'tutor_id': str(self.tutor.uuid),
        })
        for resource, record, changes in (
            (None, programme, {'name': 'Changed programme'}),
            ('cohorts', cohort, {'name': 'Changed cohort'}),
            ('groups', group, {'name': 'Changed group'}),
            ('ksbs', ksb, {'description': 'Changed criteria'}),
            ('modules', module, {'name': 'Changed module'}),
            ('deliveries', delivery, {'start_date': '2027-01-07'}),
        ):
            with self.subTest(resource=resource):
                url = f"{path}{resource}/{record['id']}/" if resource else path
                self.request('patch', url, changes, 428, revision=None)
                saved = self.request('patch', url, changes, revision=1)
                self.assertEqual(saved['revision'], 2)
                for method in ('patch', 'delete'):
                    error = self.request(method, url, changes, 409, revision=1)
                    self.assertEqual((error['code'], error['current_revision']), ('catalog_revision_conflict', 2))
                current = self.request('get', url)
                current = current.get('programme', current)
                self.assertIsNone(current['archived_at'])
                self.assertEqual(current['revision'], 2)

    def test_deliveries_pin_weeks_counts_topics_and_ksb_text_until_explicitly_repinned(self):
        programme = self.create(PROGRAMMES, PROGRAMME)
        path = f"{PROGRAMMES}{programme['id']}/"
        cohort = self.create(path + 'cohorts/', COHORT)
        groups = [self.create(path + 'groups/', GROUP | {'cohort_id': cohort['id'], 'name': name}) for name in ('One', 'Two')]
        ksb = self.create(path + 'ksbs/', {'code': 'K1', 'description': 'Original criteria'})
        module = self.create(path + 'modules/', {
            'name': 'Original module', 'weeks': 2, 'lectures': 8, 'reviews': 2, 'books': 1,
            'ksb_ids': [ksb['id']], 'weekly_topics': [{'week': 1, 'content': 'Original topic'}],
        })
        deliveries = [self.create(path + 'deliveries/', {
            'group_id': group['id'], 'module_id': module['id'], 'start_date': '2027-01-04', 'tutor_id': str(self.tutor.uuid),
        }) for group in groups]
        module_path = path + f"modules/{module['id']}/"
        self.request('patch', path + f"ksbs/{ksb['id']}/", {'description': 'New criteria'})
        self.request('patch', module_path, {'name': 'Stale template'}, 409, revision=1)
        latest = self.request('patch', module_path, {
            'name': 'New module', 'weeks': 3, 'lectures': 16,
            'weekly_topics': [{'week': 1, 'content': 'New topic'}],
        })
        self.assertEqual(latest['current_revision']['number'], 3)
        self.assertEqual(latest['weekly_topics'][0]['id'], module['weekly_topics'][0]['id'])
        first_delivery = path + f"deliveries/{deliveries[0]['id']}/"
        before = self.request('patch', first_delivery, {'start_date': '2027-01-04'})
        self.assertEqual(before['end_date'], '2027-01-17')
        self.assertEqual(before['curriculum']['weekly_topics'][0]['content'], 'Original topic')
        self.assertEqual(before['curriculum']['ksbs'][0]['description'], 'Original criteria')
        with patch('programme_suitability.catalog_services.timezone.localdate', return_value=date(2027, 1, 5)):
            self.request('patch', first_delivery, {'module_revision_id': latest['current_revision']['id']}, 400)
        with patch('programme_suitability.catalog_services.timezone.localdate', return_value=date(2027, 1, 1)):
            changed = self.request('patch', first_delivery, {'module_revision_id': latest['current_revision']['id']})
        self.assertEqual((changed['curriculum']['name'], changed['end_date']), ('New module', '2027-01-24'))
        hours = [self.staff_client.get(path + f"pricing/?group_id={group['id']}").json()['pricing'] for group in groups]
        self.assertEqual([quote['rates']['training_hour'] for quote in hours], ['200.00', '400.00'])
        self.assertEqual(hours[0]['module_revision_ids'], [latest['current_revision']['id']])


class CatalogDateTests(SimpleTestCase):
    def test_inclusive_end_dates_use_the_original_start_for_each_offset(self):
        cases = [
            (date(2027, 1, 1), 1, 0, date(2027, 1, 31), date(2027, 1, 31)),
            (date(2027, 1, 31), 1, 1, date(2027, 2, 27), date(2027, 3, 30)),
            (date(2028, 1, 31), 1, 1, date(2028, 2, 28), date(2028, 3, 30)),
            (date(2028, 2, 29), 12, 1, date(2029, 2, 27), date(2029, 3, 28)),
            (date(2027, 12, 1), 2, 1, date(2028, 1, 31), date(2028, 2, 29)),
            (date(9999, 12, 1), 1, 0, date(9999, 12, 31), date(9999, 12, 31)),
        ]
        for start, training, epa, training_end, apprenticeship_end in cases:
            with self.subTest(start=start, training=training, epa=epa):
                fields = calculate_cohort_schedule(start, training, epa)
                cohort = CatalogCohort(start_date=start, epa_period=epa, **fields)
                self.assertEqual(cohort.training_end_date, training_end)
                self.assertEqual(cohort.apprenticeship_end_date, apprenticeship_end)
                self.assertEqual(cohort.apprenticeship_duration_months, training + epa)


class CatalogPricingTests(SimpleTestCase):
    def test_rounding_reconciles_every_cent_and_preserves_the_agreement_contract(self):
        programme = CatalogProgramme(code='PRECISION', epa_cost=Decimal('20.00'), lecture_hours=Decimal('2.50'))
        for pennies in range(200):
            programme.total_cost = Decimal('20.00') + Decimal(pennies) / 100
            quote = price_programme(programme)
            self.assertEqual(sum(map(Decimal, quote['allocations'].values())), programme.total_cost)
            self.assertEqual(Decimal(quote['total_training']), (Decimal(pennies) * Decimal('0.008')).quantize(
                Decimal('0.01'),
            ))
        adjusted_quote = price_programme(programme, adjusted_total=Decimal('21.00'))
        snapshot = written_agreement_costs(adjusted_quote)
        self.assertEqual(snapshot['total'], 21.00)
        self.assertIsNone(snapshot['balanceDue'])
        self.assertIsNone(snapshot['items'][2]['price'])
        self.assertEqual(snapshot['items'][11]['price'], 20.00)
        original = programme.total_cost
        self.assertEqual(snapshot['pricingSnapshot']['master_total'], str(original))
        programme.total_cost = Decimal('100.00')
        adjusted_quote['master_total'] = '100.00'
        self.assertEqual(snapshot['pricingSnapshot']['master_total'], str(original))
