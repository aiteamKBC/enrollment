import os
from copy import deepcopy
from datetime import date
from unittest.mock import patch

from django.test import Client, SimpleTestCase, TestCase

from enrollment_service.lms_readonly import LmsReadUnavailable
from enrollment_service.lms_catalogue_source import canonical_fingerprint, catalogue_query_sql
from learner_api.models import StaffUser
from login.models import LoginAccount
from login.sessions import COOKIE_NAME, issue_session
from .catalog_lms import CatalogLmsError, apply_updates, approve_import, check_updates, normalize_snapshot
from .catalog_models import (
    CatalogDelivery, CatalogModule, CatalogModuleRevision, CatalogProgramme, CatalogSourceLink,
)


def raw_snapshot():
    return {
        'programme': {
            'programme_id': 'PROG-ME-L4', 'name': 'Marketing Executive Level 4',
            'level': 'Level 4', 'description': 'Marketing', 'status': 'active',
            'standard_ref': 'ST0596', 'standard_version': '1.0',
            'updated_at': '2026-11-24T10:00:00+00:00',
        },
        'hierarchy': [{
            'cohort_id': 'c1', 'cohort_name': 'Feb 2027', 'start_date': '2027-01-01',
            'end_date': '2028-01-31', 'duration_months': 13, 'epa_months': 5,
            'apprenticeship_end_date': '2028-06-30', 'cohort_status': 'planned',
            'cohort_updated_at': '2026-11-24T10:00:00',
            'group_id': 'g1', 'group_name': 'Group 1', 'session_week_day': 'Friday',
            'session_start_time': '09:00', 'session_end_time': '11:00', 'group_status': 'active',
            'group_updated_at': '2026-11-24T10:00:00',
            'module_catalogue_id': 'm1', 'module_title': 'Social Media',
            'module_description': 'Module description', 'sessions_number': 2,
            'module_start_date': '2027-06-21', 'module_end_date': '2027-07-04',
            'module_status': 'active', 'module_updated_at': '2026-11-24T10:00:00',
        }],
        'weeks': [
            {'id': 'w1', 'module_catalogue_id': 'm1', 'week_number': 1, 'title': 'Introduction', 'display_order': 0, 'status': 'active', 'updated_at': '2026-11-24T10:00:00'},
            {'id': 'w2', 'module_catalogue_id': 'm1', 'week_number': 2, 'title': 'Reading Week', 'display_order': 1, 'status': 'active', 'updated_at': '2026-11-24T10:00:00'},
        ],
        'ksbs': [{
            'id': '130', 'ksb_code': 'B1', 'ksb_type': 'Behaviour',
            'ksb_description': 'See projects through', 'standard_ref': 'ST0596',
            'standard_version': '1.0', 'updated_at': '2026-11-24T10:00:00',
            'module_ids': ['m1'],
            'module_weights': [{
                'module_id': 'm1', 'total_weight': '1060.00', 'mapping_count': 27,
                'missing_weight_count': 0, 'hard_count': 26, 'soft_count': 1, 'possible_count': 0,
                'hard_weight': '1040.00', 'soft_weight': '20.00', 'possible_weight': '0',
                'main_count': 26, 'secondary_count': 1,
                'main_weight': '1040.00', 'secondary_weight': '20.00',
            }],
        }],
    }


def snapshot():
    return normalize_snapshot(
        raw_snapshot(), programme_id='PROG-ME-L4', cohort_start_date=date(2027, 1, 1),
        standard_ref='ST0596', standard_version='1.0',
    )


def preview(value=None):
    value = value or snapshot()
    return {
        'snapshot': value, 'preview_fingerprint': 'a' * 64, 'can_import': True,
        'conflicts': [], 'missing_fields': [], 'metrics': {'query_count': 6},
        'performance_warnings': [],
    }


PRICING = {'total_cost': '6000', 'epa_cost': '1000', 'funding_band_maximum': '7000', 'epao': 'EPA Org'}
MODULE_COUNTS = {'m1': {'reviews': 4, 'books': 2}}


class LmsCatalogueQueryTests(SimpleTestCase):
    def test_queries_are_bounded_and_never_select_raw_components(self):
        queries = catalogue_query_sql()
        self.assertEqual(len(queries), 4)
        self.assertTrue(all('SELECT *' not in query.upper() for query in queries))
        self.assertTrue(all('LIMIT ' in query.upper() for query in queries))
        self.assertNotIn('c.title', queries[3])
        self.assertIn('GROUP BY c.module_catalogue_id, km.standard_ksb_id', queries[3])
        self.assertIn('SUM(km.weight)::text AS total_weight', queries[3])
        self.assertIn("w.status = 'active'", queries[3])
        self.assertIn('w.id = c.week_id', queries[3])
        self.assertIn("g.status = 'active'", queries[3])

    def test_normalization_preserves_reading_week_and_distinct_module_coverage(self):
        result = snapshot()
        self.assertEqual(result['counts'], {
            'programmes': 1, 'cohorts': 1, 'groups': 1, 'modules': 1,
            'weekly_topics': 2, 'ksbs': 1,
        })
        self.assertEqual(result['modules'][0]['weekly_topics'][1]['content'], 'Reading Week')
        self.assertEqual(result['modules'][0]['ksb_ids'], ['130'])
        self.assertEqual(result['modules'][0]['ksb_weights']['130']['total_weight'], '1060.00')
        self.assertEqual((result['modules'][0]['ksb_weights']['130']['hard_count'],
                          result['modules'][0]['ksb_weights']['130']['soft_count']), (26, 1))

    def test_filter_uses_actual_start_date(self):
        value = raw_snapshot()
        value['hierarchy'][0]['cohort_name'] = 'December 2026 label'
        result = normalize_snapshot(
            value, programme_id='PROG-ME-L4', cohort_start_date=date(2027, 1, 1),
            standard_ref='ST0596', standard_version='1.0',
        )
        self.assertEqual(result['cohorts'][0]['start_date'], '2027-01-01')

    def test_zero_weight_is_distinct_from_missing_historical_weight(self):
        source = raw_snapshot()
        values = source['ksbs'][0]['module_weights'][0]
        for key in ('total_weight', 'hard_weight', 'main_weight'):
            values[key] = '0.00'
        values['soft_weight'] = values['secondary_weight'] = '0.00'
        result = normalize_snapshot(source, programme_id='PROG-ME-L4', cohort_start_date=date(2027, 1, 1),
                                    standard_ref='ST0596', standard_version='1.0')
        self.assertEqual(result['modules'][0]['ksb_weights']['130']['total_weight'], '0.00')
        del source['ksbs'][0]['module_weights']
        with self.assertRaises(CatalogLmsError):
            normalize_snapshot(source, programme_id='PROG-ME-L4', cohort_start_date=date(2027, 1, 1),
                               standard_ref='ST0596', standard_version='1.0')


class LmsCataloguePersistenceTests(TestCase):
    @patch('programme_suitability.catalog_lms.preview_import')
    def test_stale_import_preview_is_rejected_before_writes(self, mocked_preview):
        mocked_preview.return_value = preview() | {'preview_fingerprint': 'b' * 64}
        with self.assertRaises(CatalogLmsError) as failure:
            approve_import('a' * 64, 'PROG-ME-L4', date(2027, 1, 1), 'ST0596', '1.0', PRICING, '41', module_counts=MODULE_COUNTS)
        self.assertEqual(failure.exception.code, 'lms_preview_stale')
        self.assertFalse(CatalogProgramme.objects.exists())

    @patch('programme_suitability.catalog_lms.preview_import')
    def test_import_is_persistent_linked_and_idempotent(self, mocked_preview):
        mocked_preview.return_value = preview()
        first = approve_import('a' * 64, 'PROG-ME-L4', date(2027, 1, 1), 'ST0596', '1.0', PRICING, '41', module_counts=MODULE_COUNTS)
        second = approve_import('a' * 64, 'PROG-ME-L4', date(2027, 1, 1), 'ST0596', '1.0', PRICING, '41', module_counts=MODULE_COUNTS)
        self.assertTrue(first['created'])
        self.assertFalse(second['created'])
        self.assertEqual(CatalogProgramme.objects.count(), 1)
        self.assertEqual(CatalogModule.objects.count(), 1)
        self.assertEqual(CatalogDelivery.objects.count(), 1)
        self.assertEqual(CatalogSourceLink.objects.filter(source_entity_type='weekly_topic').count(), 2)

    @patch('programme_suitability.catalog_lms.preview_import')
    def test_admin_owned_module_counts_are_saved_in_first_pinned_revision(self, mocked_preview):
        mocked_preview.return_value = preview()
        approve_import('a' * 64, 'PROG-ME-L4', date(2027, 1, 1), 'ST0596', '1.0', PRICING, '41', module_counts=MODULE_COUNTS)
        module = CatalogModule.objects.get()
        self.assertEqual((module.reviews, module.books), (4, 2))
        self.assertEqual((module.revisions.first().reviews, module.revisions.first().books), (4, 2))
        self.assertEqual(module.revisions.first().ksbs[0]['mapping_weight']['total_weight'], '1060.00')
        self.assertEqual(module.lectures, 2)

    @patch('programme_suitability.catalog_lms.preview_import')
    def test_missing_counts_are_rejected_but_explicit_zero_is_preserved(self, mocked_preview):
        mocked_preview.return_value = preview()
        scope = ('a' * 64, 'PROG-ME-L4', date(2027, 1, 1), 'ST0596', '1.0', PRICING, '41')
        for counts in (None, {}, {'m1': {'reviews': 0}}, {'m1': {'reviews': None, 'books': 0}}):
            with self.assertRaises(Exception):
                approve_import(*scope, module_counts=counts)
            self.assertFalse(CatalogProgramme.objects.exists())
        approve_import(*scope, module_counts={'m1': {'reviews': 0, 'books': 0}})
        module = CatalogModule.objects.get()
        self.assertEqual((module.reviews, module.books), (0, 0))
        self.assertEqual((module.revisions.first().reviews, module.revisions.first().books), (0, 0))

    @patch('programme_suitability.catalog_lms.preview_import')
    def test_each_previewed_module_requires_its_own_counts(self, mocked_preview):
        expanded = snapshot()
        second = deepcopy(expanded['modules'][0])
        second['id'], second['name'] = 'm2', 'Second LMS module'
        second['weekly_topics'] = [{**topic, 'id': f"{topic['id']}-second"} for topic in second['weekly_topics']]
        expanded['modules'].append(second)
        mocked_preview.return_value = preview(expanded)
        scope = ('a' * 64, 'PROG-ME-L4', date(2027, 1, 1), 'ST0596', '1.0', PRICING, '41')
        with self.assertRaises(Exception):
            approve_import(*scope, module_counts=MODULE_COUNTS)
        self.assertFalse(CatalogProgramme.objects.exists())
        approve_import(*scope, module_counts=MODULE_COUNTS | {'m2': {'reviews': 0, 'books': 1}})
        self.assertEqual({row.external_id: (row.reviews, row.books) for row in CatalogModule.objects.all()},
                         {'m1': (4, 2), 'm2': (0, 1)})

    @patch('programme_suitability.catalog_lms.preview_import')
    def test_atomic_import_rolls_back_invalid_group_schedule(self, mocked_preview):
        broken = snapshot()
        broken['groups'][0]['end_time'] = '08:00'
        mocked_preview.return_value = preview(broken)
        with self.assertRaises(Exception):
            approve_import('a' * 64, 'PROG-ME-L4', date(2027, 1, 1), 'ST0596', '1.0', PRICING, '41', module_counts=MODULE_COUNTS)
        self.assertFalse(CatalogProgramme.objects.exists())
        self.assertFalse(CatalogSourceLink.objects.exists())

    @patch('programme_suitability.catalog_lms.check_updates')
    @patch('programme_suitability.catalog_lms.preview_import')
    def test_module_sync_creates_revision_and_repins_future_delivery(self, mocked_preview, mocked_check):
        mocked_preview.return_value = preview()
        result = approve_import('a' * 64, 'PROG-ME-L4', date(2027, 1, 1), 'ST0596', '1.0', PRICING, '41', module_counts=MODULE_COUNTS)
        programme = CatalogProgramme.objects.get(pk=result['programme_id'])
        delivery = CatalogDelivery.objects.get()
        pinned_revision = delivery.module_revision_id
        CatalogModule.objects.update(books=3, reviews=4)
        changed = deepcopy(snapshot())
        changed['modules'][0]['name'] = 'Social Media updated'
        changed['modules'][0]['weekly_topics'][0]['content'] = 'Updated introduction'
        key = 'module_template:m1:module_template'
        mocked_check.return_value = {
            'preview_fingerprint': 'b' * 64, 'snapshot': changed,
            'changes': [{'key': key, 'state': 'modified', 'entity_type': 'module_template', 'source_id': 'm1', 'fields': [{'field': 'name', 'before': 'Social Media', 'after': 'Social Media updated'}]}],
        }
        apply_updates(programme, 'b' * 64, [key], '41', programme.revision)
        delivery.refresh_from_db()
        module = CatalogModule.objects.get()
        self.assertNotEqual(delivery.module_revision_id, pinned_revision)
        self.assertEqual(module.revisions.count(), 2)
        self.assertEqual(module.revisions.first().name, 'Social Media updated')
        self.assertEqual((module.books, module.reviews), (3, 4))

    @patch('programme_suitability.catalog_lms.normalize_snapshot')
    @patch('programme_suitability.catalog_lms.read_catalogue_snapshot')
    @patch('programme_suitability.catalog_lms.preview_import')
    def test_existing_import_backfills_weights_without_recreating_links_or_history(self, mocked_preview, mocked_read, mocked_normalize):
        mocked_preview.return_value = preview()
        imported = approve_import('a' * 64, 'PROG-ME-L4', date(2027, 1, 1), 'ST0596', '1.0', PRICING, '41',
                                  module_counts=MODULE_COUNTS)
        programme = CatalogProgramme.objects.get(pk=imported['programme_id'])
        module = CatalogModule.objects.get()
        delivery = CatalogDelivery.objects.get()
        first_revision = delivery.module_revision
        old_ksbs = deepcopy(first_revision.ksbs)
        old_ksbs[0].pop('mapping_weight')
        first_revision.ksbs = old_ksbs
        first_revision.save(update_fields=['ksbs'])
        link = CatalogSourceLink.objects.get(local_entity_type='module_template')
        old_state = deepcopy(link.accepted_state)
        old_state.pop('ksb_weights')
        link.accepted_state = old_state
        link.accepted_fingerprint = canonical_fingerprint(old_state)
        link.save(update_fields=['accepted_state', 'accepted_fingerprint', 'updated_at'])
        link_count = CatalogSourceLink.objects.count()
        mocked_read.return_value = {'metrics': {'query_count': 6}, 'performance_warnings': []}
        current = snapshot()
        mocked_normalize.return_value = current
        changes = check_updates(programme)['changes']
        module_changes = [item for item in changes if item['entity_type'] == 'module_template']
        self.assertEqual(len(module_changes), 1)
        self.assertEqual(module_changes[0]['state'], 'modified')
        self.assertEqual(module_changes[0]['fields'][0]['field'], 'KSB 130 mapping weight')
        self.assertIsNone(module_changes[0]['fields'][0]['before'])
        with patch('programme_suitability.catalog_lms.timezone.localdate', return_value=date(2028, 1, 1)):
            apply_updates(programme, current['fingerprint'], [module_changes[0]['key']], '41', programme.revision)
        delivery.refresh_from_db()
        module.refresh_from_db()
        self.assertEqual(delivery.module_revision_id, first_revision.pk)
        self.assertNotIn('mapping_weight', first_revision.ksbs[0])
        self.assertEqual(module.revisions.first().ksbs[0]['mapping_weight']['total_weight'], '1060.00')
        self.assertEqual((module.books, module.reviews), (2, 4))
        self.assertEqual((CatalogProgramme.objects.count(), CatalogModule.objects.count(), CatalogDelivery.objects.count()), (1, 1, 1))
        self.assertEqual(CatalogSourceLink.objects.count(), link_count)
        self.assertEqual(check_updates(programme)['changes'], [])

    @patch('programme_suitability.catalog_lms.normalize_snapshot')
    @patch('programme_suitability.catalog_lms.read_catalogue_snapshot')
    @patch('programme_suitability.catalog_lms.preview_import')
    def test_weight_change_sync_is_idempotent_and_repins_future_delivery(self, mocked_preview, mocked_read, mocked_normalize):
        mocked_preview.return_value = preview()
        imported = approve_import('a' * 64, 'PROG-ME-L4', date(2027, 1, 1), 'ST0596', '1.0', PRICING, '41',
                                  module_counts=MODULE_COUNTS)
        programme = CatalogProgramme.objects.get(pk=imported['programme_id'])
        delivery = CatalogDelivery.objects.get()
        prior_revision = delivery.module_revision_id
        changed = deepcopy(snapshot())
        weight = changed['modules'][0]['ksb_weights']['130']
        weight['total_weight'] = '1080.00'
        weight['hard_weight'] = weight['main_weight'] = '1060.00'
        changed['fingerprint'] = 'b' * 64
        mocked_read.return_value = {'metrics': {'query_count': 6}, 'performance_warnings': []}
        mocked_normalize.return_value = changed
        difference = check_updates(programme)['changes']
        self.assertEqual(len(difference), 1)
        self.assertEqual(difference[0]['fields'][0]['after']['total_weight'], '1080.00')
        apply_updates(programme, changed['fingerprint'], [difference[0]['key']], '41', programme.revision)
        delivery.refresh_from_db()
        self.assertNotEqual(delivery.module_revision_id, prior_revision)
        self.assertEqual(delivery.module_revision.ksbs[0]['mapping_weight']['total_weight'], '1080.00')
        self.assertEqual((delivery.module_revision.books, delivery.module_revision.reviews), (2, 4))
        self.assertEqual(check_updates(programme)['changes'], [])
        self.assertEqual(CatalogModule.objects.get().revisions.count(), 2)

    @patch('programme_suitability.catalog_lms.preview_import')
    def test_sync_rejects_stale_local_programme_revision(self, mocked_preview):
        mocked_preview.return_value = preview()
        result = approve_import('a' * 64, 'PROG-ME-L4', date(2027, 1, 1), 'ST0596', '1.0', PRICING, '41', module_counts=MODULE_COUNTS)
        programme = CatalogProgramme.objects.get(pk=result['programme_id'])
        expected = programme.revision
        CatalogProgramme.objects.filter(pk=programme.pk).update(revision=expected + 1)
        with self.assertRaises(CatalogLmsError) as failure:
            apply_updates(programme, 'b' * 64, ['module_template:m1:module_template'], '41', expected)
        self.assertEqual(failure.exception.code, 'catalog_revision_conflict')


@patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1', 'API_REQUIRE_AUTH': '1'})
class LmsCatalogueApiTests(TestCase):
    url = '/enrolment_api/programme-structure/catalog/lms/preview/'

    def setUp(self):
        staff = StaffUser.objects.create(id=880, username='Import Admin', access='enrolment')
        account = LoginAccount.objects.create(
            id=880, subject_type='staff', subject_id=staff.id, role='staff',
            email='import-admin@example.test', is_active=True,
        )
        self.client = Client(enforce_csrf_checks=True)
        self.client.cookies[COOKIE_NAME] = issue_session(account)[0]
        self.csrf = self.client.get('/learner_api/enrollment-csrf/').json()['csrfToken']
        self.payload = {
            'programme_id': 'PROG-ME-L4', 'cohort_start_date': '2027-01-01',
            'standard_ref': 'ST0596', 'standard_version': '1.0',
        }

    def post(self):
        return self.client.post(
            self.url, self.payload, content_type='application/json', HTTP_X_CSRFTOKEN=self.csrf,
        )

    def test_import_endpoints_require_authenticated_enrolment_staff(self):
        self.assertEqual(Client().post(self.url, self.payload, content_type='application/json').status_code, 401)

    @patch('enrolment_api.programme_catalog.preview_import')
    @patch('enrolment_api.programme_catalog.catalogue_lms_schema_ready', return_value=False)
    def test_preview_remains_read_only_when_link_migration_is_pending(self, _ready, mocked_preview):
        mocked_preview.return_value = preview()
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['can_import'])
        self.assertIn('migration 0019', response.json()['missing_fields'][-1])
        self.assertFalse(CatalogProgramme.objects.exists())

    @patch('enrolment_api.programme_catalog.preview_import', side_effect=LmsReadUnavailable('connection_timeout'))
    def test_source_failures_are_distinct_and_do_not_write(self, _preview):
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['code'], 'connection_timeout')
        self.assertFalse(CatalogProgramme.objects.exists())

    @patch('programme_suitability.catalog_lms.preview_import')
    def test_import_api_requires_counts_for_each_module(self, mocked_preview):
        mocked_preview.return_value = preview()
        payload = self.payload | {'preview_fingerprint': 'a' * 64, 'pricing': PRICING}
        url = '/enrolment_api/programme-structure/catalog/lms/import/'
        missing = self.client.post(url, payload, content_type='application/json', HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(missing.status_code, 400, missing.content[:700])
        self.assertIn('module_counts', missing.json()['fields'])
        self.assertFalse(CatalogProgramme.objects.exists())
        created = self.client.post(url, payload | {'module_counts': {'m1': {'reviews': 0, 'books': 0}}},
                                   content_type='application/json', HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(created.status_code, 201, created.content[:700])
        self.assertEqual((CatalogModule.objects.get().reviews, CatalogModule.objects.get().books), (0, 0))

    @patch('programme_suitability.catalog_lms.preview_import')
    def test_counts_patch_persists_and_reprices_future_delivery(self, mocked_preview):
        mocked_preview.return_value = preview()
        imported = approve_import('a' * 64, 'PROG-ME-L4', date(2027, 1, 1), 'ST0596', '1.0', PRICING, '41',
                                  module_counts={'m1': {'reviews': 0, 'books': 0}})
        programme_id = imported['programme_id']
        module = CatalogModule.objects.get()
        delivery = CatalogDelivery.objects.get()
        CatalogModule.objects.filter(pk=module.pk).update(reviews=None, books=None)
        CatalogModuleRevision.objects.filter(pk=delivery.module_revision_id).update(reviews=None, books=None)
        module.refresh_from_db()
        first_revision = delivery.module_revision_id
        link_count = CatalogSourceLink.objects.count()
        path = f'/enrolment_api/programme-structure/catalog/programmes/{programme_id}/'
        module_url = path + f'modules/{module.pk}/'
        pricing_url = path + f'pricing/?group_id={delivery.group_id}'
        self.assertIsNone(self.client.get(pricing_url).json()['pricing']['rates']['review'])

        response = self.client.patch(module_url, {'reviews': 4, 'books': 2}, content_type='application/json',
                                     HTTP_IF_MATCH=f'"{module.revision}"', HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(response.status_code, 200, response.content[:700])
        self.assertEqual((response.json()['reviews'], response.json()['books']), (4, 2))
        module.refresh_from_db()
        delivery.refresh_from_db()
        self.assertNotEqual(delivery.module_revision_id, first_revision)
        self.assertEqual((delivery.module_revision.reviews, delivery.module_revision.books), (4, 2))
        self.assertEqual((CatalogModule.objects.get(pk=module.pk).reviews,
                          CatalogModule.objects.get(pk=module.pk).books), (4, 2))
        self.assertEqual(CatalogSourceLink.objects.count(), link_count)
        refreshed = self.client.get(path).json()
        self.assertEqual((refreshed['modules'][0]['reviews'], refreshed['modules'][0]['books']), (4, 2))
        rates = self.client.get(pricing_url).json()['pricing']['rates']
        self.assertEqual((rates['review'], rates['book']), ('187.50', '125.00'))
        self.assertEqual(self.client.patch(module_url, {'reviews': 5, 'books': 2}, content_type='application/json',
                                           HTTP_IF_MATCH='"1"', HTTP_X_CSRFTOKEN=self.csrf).status_code, 409)
        self.assertEqual(self.client.patch(module_url, {'name': 'Changed by hand'}, content_type='application/json',
                                           HTTP_IF_MATCH=f'"{module.revision}"', HTTP_X_CSRFTOKEN=self.csrf).status_code, 400)
        self.assertEqual(self.client.patch(module_url, {'reviews': None, 'books': 0}, content_type='application/json',
                                           HTTP_IF_MATCH=f'"{module.revision}"', HTTP_X_CSRFTOKEN=self.csrf).status_code, 400)

    @patch('programme_suitability.catalog_lms.normalize_snapshot')
    @patch('programme_suitability.catalog_lms.read_catalogue_snapshot')
    @patch('programme_suitability.catalog_lms.preview_import')
    def test_admin_counts_do_not_create_sync_conflict_or_duplicates(self, mocked_preview, mocked_read, mocked_normalize):
        mocked_preview.return_value = preview()
        imported = approve_import('a' * 64, 'PROG-ME-L4', date(2027, 1, 1), 'ST0596', '1.0', PRICING, '41',
                                  module_counts=MODULE_COUNTS)
        programme = CatalogProgramme.objects.get(pk=imported['programme_id'])
        module = CatalogModule.objects.get()
        delivery = CatalogDelivery.objects.get()
        module_url = f'/enrolment_api/programme-structure/catalog/programmes/{programme.pk}/modules/{module.pk}/'
        response = self.client.patch(module_url, {'reviews': 6, 'books': 3}, content_type='application/json',
                                     HTTP_IF_MATCH=f'"{module.revision}"', HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(response.status_code, 200, response.content[:700])
        mocked_read.return_value = {'metrics': {'query_count': 6}, 'performance_warnings': []}
        mocked_normalize.return_value = snapshot()
        self.assertEqual(check_updates(programme)['changes'], [])
        self.assertEqual((CatalogModule.objects.count(), CatalogDelivery.objects.count()), (1, 1))
        self.assertEqual(CatalogSourceLink.objects.filter(local_entity_type='module_template').count(), 1)

        changed = deepcopy(snapshot())
        changed['modules'][0]['name'] = 'Updated LMS title'
        key = 'module_template:m1:module_template'
        with patch('programme_suitability.catalog_lms.check_updates', return_value={
            'preview_fingerprint': 'b' * 64, 'snapshot': changed,
            'changes': [{'key': key, 'state': 'modified', 'entity_type': 'module_template',
                         'source_id': 'm1', 'fields': [{'field': 'name', 'before': 'Social Media', 'after': 'Updated LMS title'}]}],
        }):
            apply_updates(programme, 'b' * 64, [key], '41', programme.revision)
        module.refresh_from_db()
        delivery.refresh_from_db()
        self.assertEqual((module.reviews, module.books), (6, 3))
        self.assertEqual((module.revisions.first().reviews, module.revisions.first().books), (6, 3))
        self.assertEqual((delivery.module_revision.reviews, delivery.module_revision.books), (6, 3))
        self.assertEqual((CatalogProgramme.objects.count(), CatalogModule.objects.count(), CatalogDelivery.objects.count()), (1, 1, 1))

    @patch('programme_suitability.catalog_lms.preview_import')
    def test_count_edit_keeps_started_delivery_revision(self, mocked_preview):
        mocked_preview.return_value = preview()
        imported = approve_import('a' * 64, 'PROG-ME-L4', date(2027, 1, 1), 'ST0596', '1.0', PRICING, '41',
                                  module_counts=MODULE_COUNTS)
        module = CatalogModule.objects.get()
        delivery = CatalogDelivery.objects.get()
        first_revision = delivery.module_revision_id
        url = (f'/enrolment_api/programme-structure/catalog/programmes/{imported["programme_id"]}/'
               f'modules/{module.pk}/')
        with patch('enrolment_api.programme_catalog.timezone.localdate', return_value=date(2028, 1, 1)):
            response = self.client.patch(url, {'reviews': 7, 'books': 5}, content_type='application/json',
                                         HTTP_IF_MATCH=f'"{module.revision}"', HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(response.status_code, 200, response.content[:700])
        delivery.refresh_from_db()
        module.refresh_from_db()
        self.assertEqual(delivery.module_revision_id, first_revision)
        self.assertEqual((delivery.module_revision.reviews, delivery.module_revision.books), (4, 2))
        self.assertEqual((module.revisions.first().reviews, module.revisions.first().books), (7, 5))
        self.assertEqual(module.revisions.first().ksbs[0]['mapping_weight']['total_weight'], '1060.00')
