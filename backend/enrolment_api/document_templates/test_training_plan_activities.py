"""Official revisions + shared Node PDF renderer, isolated learner/LMS fixtures."""
import base64
import copy
import json
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import patch
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from learner_api import programme_meetings, training_plan_activities as activities
from learner_api.models import TrainingPlanDocument
from programme_suitability import test_module_activities as fixtures
from programme_suitability.models import CohortGroupAllocation
from programme_suitability.modules_rules import available_modules
from . import revisions, service, pdf_artifacts as pdf
from .models import LearnerDocumentRevision as Revision, IssuedTemplate, RenderHistory
from .registry import seed, TemplateError, digest
from .learner_sources import lms_apprenticeship_end as resolve_lms_apprenticeship_end
from . import test_templates as helpers, revision_views, pdf_views


@override_settings(DOCUMENT_REVISIONS_ENABLED=True, DOCUMENT_BRANDED_PDF_ENABLED=True)
class TrainingActivitiesWorkflowTests(TestCase):
    databases = {'default', 'enrolment'}
    learner = fixtures.ModuleActivitiesTests.learner
    configure = fixtures.ModuleActivitiesTests.configure
    request = helpers.TemplateWorkflowTests.request
    result = helpers.TemplateWorkflowTests.payload

    def setUp(self):
        fixtures.ModuleActivitiesTests.setUp(self)
        from django.test import RequestFactory
        self.factory = RequestFactory()
        self.source.username, self.source.email = 'ISOLATED Activities Learner', 'isolated@example.test'
        self.source.start_date, self.source.end_date = '2027-01-01', '2028-02-01'
        self.source.learner_start_date, self.source.learner_end_date = '2027-01-01', '2028-02-01'
        self.source.save()
        from learner_api.models import LearnerProfile
        self.profile = LearnerProfile.objects.create(enrolment_id=self.source.pk, full_name=self.source.username,
            programme_id='PROG-ME-L4', cohort_id='home', cohort='Feb 2027', group_id='g1', group_name='Group 1')
        self.source.programme_status = 'Onboarding'
        self.source.save()
        patch('learner_api.training_plan_document._employer_address', return_value='Recorded fixture workplace').start()
        patch('enrolment_api.document_templates.learner_sources.employment_details', return_value=None).start()
        self.apprenticeship_end_read = patch('enrolment_api.document_templates.learner_sources.lms_apprenticeship_end',
            return_value=(date(2028, 2, 1), '')).start()
        self.review_read = patch.object(programme_meetings, 'read_programme_reviews', side_effect=self.reviews).start()
        self.definition_read = patch.object(activities, 'read_document_review_definitions', side_effect=self.definitions).start()
        self.calendar_read = patch.object(activities, 'get_document_week_dates', return_value={'weeks': {}, 'blockers': []}).start()
        self.read.side_effect = self.content
        self.addCleanup(patch.stopall)

    def content(self, mid, pid, cid, gid):
        raw = fixtures.raw_content(mid, pid, cid, gid)
        entry = next(e for e in self.plan if e['lmsOfferingId'] == mid)
        raw['modules'][0]['title'] = entry['moduleTitle']
        return raw

    def reviews(self, pid, learner, start, end):
        return {'review_templates': [dict(id=pid+'-coaching', programme_id=pid, name='Monthly Coaching Meeting',
            enabled=True, recurrence_interval=1, recurrence_unit='months', occurrence_count=12,
            applicable_statuses=['Active'], applicability=None, expected_otjh=1, counts_towards_otjh=True,
            deleted_at=None), dict(id=pid+'-progress', programme_id=pid, name='Renamed development checkpoint',
            enabled=True, recurrence_interval=12, recurrence_unit='weeks', occurrence_count=2,
            applicable_statuses=['Active'], applicability=None, expected_otjh=0, counts_towards_otjh=False,
            deleted_at=None)], 'review_occurrence_overrides': [], 'blockers': []}

    def definitions(self, pid, ids):
        return {'definitions': {i: {'typeCode': 'progress_review' if i.endswith('-progress') else 'mcm',
            'signatures': {'advisor': True, 'employer': True, 'participant': True, 'referrer': False}} for i in ids}, 'blockers': []}

    def original(self):
        source = TrainingPlanDocument.objects.create(learner_id=self.source.pk, learner_kind='apprenticeship',
            programme={'programme': 'Historical original wording'}, employment={}, learning_plan=[],
            otjh={'plannedTotal': 17}, epa={}, contacts={}, fully_signed=True,
            apprentice_signature='historical-mark')
        IssuedTemplate.objects.create(document_key='training-plan', source_id=str(source.pk), learner_id=self.source.pk,
            learner_kind='apprenticeship', template_snapshot=pdf.new_presentation(service.reference(seed('training-plan'))), field_values={})
        return source

    def create(self, original):
        return revisions.create_draft('training-plan', original.pk, self.source, 'Isolated Admin',
            mode='learner', request_id=str(uuid.uuid4()))

    def test_new_training_plan_uses_learner_practical_dates_and_ilr_working_hours(self):
        from decimal import Decimal
        from types import SimpleNamespace
        self.source.learner_start_date = '2027-02-01'
        self.source.learner_end_date = '2027-06-21'
        self.source.save()
        with patch('enrolment_api.document_templates.learner_sources.employment_details',
                   return_value=SimpleNamespace(working_hours_per_week=Decimal('55.00'))):
            draft = self.create(self.original())
        programme = draft.data['programme']
        self.assertEqual(programme['startDate'], '2027-02-01')
        self.assertEqual(programme['practicalStartDate'], '2027-02-01')
        self.assertEqual(programme['practicalEndDate'], '2027-06-21')
        self.assertEqual(programme['endDate'], '2028-02-01')
        self.assertEqual(programme['durationWeeks'], 20)
        self.assertEqual(draft.data['employment']['workingHoursPerWeek'], '55.00')
        self.assertEqual(draft.data['activitiesOverview']['startDate'], '2027-01-01')
        self.assertEqual(draft.data['documentFieldValues']['enrolment.startDate'], '2027-02-01')

    def test_new_agreement_uses_distinct_dates_current_plan_total_and_linked_employer(self):
        from learner_api.models import ApprenticeshipAgreement
        self.source.learner_start_date = '2027-02-01'
        self.source.learner_end_date = '2028-01-31'
        self.source.save()
        previous_plan = copy.deepcopy(self.source.learning_plan)
        original = ApprenticeshipAgreement.objects.create(learner_id=self.source.pk, learner_kind='apprenticeship',
            apprentice_name='Frozen earlier name', employer_name='Frozen employer', planned_otjh=346.75,
            end_date=date(2028, 1, 31), fully_signed=True, apprentice_signature='frozen-mark')
        IssuedTemplate.objects.create(document_key='apprenticeship-agreement', source_id=str(original.pk),
            learner_id=self.source.pk, learner_kind='apprenticeship',
            template_snapshot=service.reference(seed('apprenticeship-agreement')), field_values={})
        previous = revisions.serialize_source('apprenticeship-agreement', original, self.source)
        current = [dict(entry, hours=value) for entry, value in zip(self.plan, [138, 110, 110])]
        self.apprenticeship_end_read.return_value = (date(2028, 6, 30), '')
        with patch('programme_suitability.module_replacements.resolve_saved_learning_plan', return_value=current), \
             patch('learner_api.learning_plan._meetings', return_value=([{'hours': 12}], '')), \
             patch('enrolment_api.ilr_employer_details.employer_details_for', return_value={'organisationName': 'Current linked company'}), \
             patch('learner_api.apprenticeship_agreement._employer_address', return_value='Current linked workplace'):
            draft = revisions.create_draft('apprenticeship-agreement', original.pk, self.source, 'Isolated Admin',
                mode='learner', request_id=str(uuid.uuid4()))
        values = draft.data['particulars']
        self.assertEqual(values['startDate'], '2027-02-01')
        self.assertEqual(values['practicalStartDate'], '2027-02-01')
        self.assertEqual(values['practicalEndDate'], '2028-01-31')
        self.assertEqual(values['endDate'], '2028-06-30')
        self.assertEqual(float(values['durationWeeks']), 52)
        self.assertEqual(values['plannedOtjHours'], 370)
        self.assertEqual(values['employerName'], 'Current linked company')
        self.assertEqual(values['employerAddress'], 'Current linked workplace')
        self.assertEqual(draft.data['documentFieldValues']['enrolment.endDate'], '2028-06-30')
        original.refresh_from_db(); self.source.refresh_from_db()
        self.assertEqual(revisions.serialize_source('apprenticeship-agreement', original, self.source), previous)
        self.assertEqual(self.source.learning_plan, previous_plan)

    def test_agreement_missing_sources_are_blank_not_replaced_with_local_dates(self):
        from learner_api.apprenticeship_agreement import derive_particulars
        self.source.learner_start_date = None
        self.source.learner_end_date = None
        self.source.save()
        self.apprenticeship_end_read.return_value = (None, 'not_recorded')
        with patch('learner_api.apprenticeship_agreement._plan_modules', return_value=[]), \
             patch('learner_api.apprenticeship_agreement._saved_modules', return_value=[]), \
             patch('learner_api.learning_plan._meetings', return_value=([], 'no-dates')), \
             patch('enrolment_api.ilr_employer_details.employer_details_for', return_value={}), \
             patch('learner_api.apprenticeship_agreement._employer_address', return_value=''):
            values = derive_particulars(self.source)
        for field in ('startDate', 'endDate', 'practicalStart', 'practicalEnd', 'durationWeeks'):
            self.assertIsNone(values[field])
        self.assertEqual(values['plannedOtjh'], 0)

    def test_new_training_plan_uses_current_lms_cohort_end_not_local_dates(self):
        self.source.apprenticeship_end_date = '2028-03-31'
        self.source.end_date = '2027-10-31'
        self.source.save()
        self.apprenticeship_end_read.side_effect = resolve_lms_apprenticeship_end
        with patch('programme_suitability.lms_placement.saved_cohort', return_value=({'cohort_id': 'verified-home'}, '')) as cohort, \
             patch('enrollment_service.lms_readonly.get_cohort_apprenticeship_end', return_value=date(2028, 6, 30)) as read:
            draft = self.create(self.original())
        self.assertEqual(draft.data['programme']['endDate'], '2028-06-30')
        self.assertEqual(draft.data['documentFieldValues']['enrolment.endDate'], '2028-06-30')
        self.assertEqual(draft.data['activitiesOverview']['endDate'], '2027-10-31')
        cohort.assert_called_once_with('PROG-ME-L4', None, self.source.cohort)
        read.assert_called_once_with('PROG-ME-L4', 'verified-home')
        self.source.refresh_from_db()
        self.assertEqual(self.source.apprenticeship_end_date, '2028-03-31')
        self.assertEqual(self.source.end_date, '2027-10-31')

    def test_missing_lms_apprenticeship_end_does_not_substitute_a_local_date(self):
        self.apprenticeship_end_read.return_value = (None, 'lms_apprenticeship_end_not_recorded')
        draft = self.create(self.original())
        self.assertEqual(draft.data['programme']['endDate'], '')
        self.assertIsNone(draft.data['documentFieldValues']['enrolment.endDate'])
        self.assertTrue(any(w['code'] == 'lms_apprenticeship_end_not_recorded' for w in draft.data['bindingWarnings']))

    def test_lms_apprenticeship_end_scopes_all_supported_programmes_and_fails_closed(self):
        from types import SimpleNamespace
        from programme_suitability.programme_assignment import CATALOGUE_NAMES
        from programme_suitability.programme_identity import CURRICULUM_PROGRAMME_IDS
        from enrollment_service.lms_readonly import LmsReadUnavailable
        for key, title in CATALOGUE_NAMES.items():
            with self.subTest(programme=key), \
                 patch('programme_suitability.lms_placement.saved_cohort', return_value=({'cohort_id': 'own-cohort'}, '')), \
                 patch('enrollment_service.lms_readonly.get_cohort_apprenticeship_end', return_value=date(2029, 5, 1)) as read:
                learner = SimpleNamespace(programme=title, cohort='Own cohort')
                self.assertEqual(resolve_lms_apprenticeship_end(learner), (date(2029, 5, 1), ''))
                read.assert_called_once_with(CURRICULUM_PROGRAMME_IDS[key], 'own-cohort')
        learner = SimpleNamespace(programme=CATALOGUE_NAMES['ME_L4'], cohort='Own cohort')
        with patch('programme_suitability.lms_placement.saved_cohort', return_value=(None, 'ambiguous')), \
             patch('enrollment_service.lms_readonly.get_cohort_apprenticeship_end') as read:
            self.assertEqual(resolve_lms_apprenticeship_end(learner), (None, 'ambiguous'))
            read.assert_not_called()
        with patch('programme_suitability.lms_placement.saved_cohort', side_effect=LmsReadUnavailable('denied')):
            self.assertEqual(resolve_lms_apprenticeship_end(learner), (None, 'denied'))

    def test_missing_learner_dates_and_working_hours_are_not_filled_from_other_dates(self):
        self.source.learner_start_date = None
        self.source.learner_end_date = None
        self.source.save()
        draft = self.create(self.original())
        programme = draft.data['programme']
        for field in ('startDate', 'practicalStartDate', 'practicalEndDate'):
            self.assertEqual(programme[field], '')
        self.assertIsNone(programme['durationWeeks'])
        self.assertEqual(programme['endDate'], '2028-02-01')
        self.assertEqual(draft.data['employment']['workingHoursPerWeek'], '')
        self.assertIsNone(draft.data['documentFieldValues']['enrolment.startDate'])

    def test_fixed_official_create_preview_download_finalize_and_history_are_frozen(self):
        source = self.original()
        before = revisions.serialize_source('training-plan', source, self.source)
        original_pdf = pdf.store(service.binding_for('training-plan', source), before)
        historical_bytes = base64.b64decode(original_pdf.payload['pdf'])
        plan_before = copy.deepcopy(self.source.learning_plan)
        draft = self.create(source)
        overview = draft.data['activitiesOverview']
        self.assertEqual(overview['programme'], 'Marketing Executive Level 4')
        self.assertTrue(overview['finalisationAllowed'], overview['blockers'])
        self.assertEqual(len(overview['modules']), len(self.plan))
        self.assertEqual(overview['totals']['moduleHours'], 99.75 * len(self.plan))
        self.assertEqual(overview['totals']['meetingHours'], 12)
        self.assertTrue(all(m['coveredKsbs'] for m in overview['modules']))
        self.assertTrue(all(r['hours'] is None for m in overview['modules'] for r in m['activities'] if r.get('parentComponentId')))
        self.assertEqual(len(overview['reviews']), 2)
        self.assertEqual(len({r['id'] for r in overview['reviews']}), 2)
        self.assertEqual(sum(r['reviewTypeCode'] == 'progress_review' for r in overview['reviews']), 2)
        # Saved draft preview uses the same immutable artifact as download.
        saved = pdf.store_revision(draft)
        preview = revisions.preview_response(draft)
        self.assertIn(str(draft.pk), preview['pdfUrl'])
        self.assertEqual(pdf.http_pdf(saved, 'preview').content, base64.b64decode(saved.payload['pdf']))
        self.read.side_effect = AssertionError('A saved draft must not reread current LMS content')
        self.review_read.side_effect = AssertionError('Frozen review schedule must not be recalculated')
        revisions.finalize(draft, 'Isolated Admin')
        finalized = pdf.store_revision(draft)
        self.assertEqual(revisions.payload(draft)['activitiesOverview'], overview)
        self.assertEqual(draft.state, 'finalized')
        self.assertFalse(draft.data['fullySigned'])
        source.refresh_from_db()
        self.assertEqual(revisions.serialize_source('training-plan', source, self.source), before)
        original_pdf.refresh_from_db()
        self.assertEqual(base64.b64decode(original_pdf.payload['pdf']), historical_bytes)
        self.source.refresh_from_db()
        self.assertEqual(self.source.learning_plan, plan_before)
        self.export('fixed', draft, finalized)

    def export(self, name, draft, artifact):
        root = Path(__file__).resolve().parents[3] / '.cache' / 'step7'
        root.mkdir(parents=True, exist_ok=True)
        (root / (name + '.pdf')).write_bytes(base64.b64decode(artifact.payload['pdf']))
        (root / (name + '.json')).write_text(json.dumps({'template': revisions.payload(draft)['template'],
            'data': revisions.payload(draft)}, default=str), encoding='utf-8')

    def test_pcp_active_confirmed_pathway_only_in_real_revision(self):
        programme = next(p for p in self.config['programmes'] if p['id'] == 'PCP_L6')
        path = next(p for p in programme['pathways'] if p['id'] == 'chartered')
        modules = [m for m in available_modules(path['modules']) if m.get('isMandatory') or m['id'] == 'portfolio']
        self.configure('PCP_L6', modules)
        self.row.modules_selection = {'programme': 'PCP_L6', 'pathway': 'chartered', 'configurationVersion': 24,
            'modulesByPathway': {'chartered': [m['id'] for m in modules], 'operational': ['pmp']}}
        self.row.save()
        self.profile.programme_id = 'PROG-PCP-L6'
        self.profile.save()
        self.pool.programme_id = 'PROG-PCP-L6'
        self.pool.save()
        allocation = CohortGroupAllocation.objects.create(learner_id=self.source.pk, learner_kind='apprenticeship',
            pool=self.pool, group_id='g1', group_name='Group 1', seat=1, pathway='chartered',
            academic_ids=[m['id'] for m in modules], configuration_version=24, selection_revision=0,
            offerings=[dict(academicModuleId=e['academicModuleId'], offeringId=e['lmsOfferingId'], groupId='g1',
                cohortId='home', start=e['startDate'], end=e['endDate']) for e in self.plan])
        before = copy.deepcopy(allocation.offerings)
        draft = self.create(self.original())
        overview = draft.data['activitiesOverview']
        self.assertTrue(overview['finalisationAllowed'], overview['blockers'])
        self.assertEqual({m['academicModuleId'] for m in overview['modules']}, {m['id'] for m in modules})
        self.assertNotIn('pmp', {m['academicModuleId'] for m in overview['modules']})
        self.assertEqual(overview['totals']['totalHours'], 99.75 * len(modules) + 12)
        revisions.finalize(draft, 'Isolated Admin')
        self.export('pcp', draft, pdf.store_revision(draft))
        allocation.refresh_from_db()
        self.assertEqual(allocation.offerings, before)
        self.assertEqual(allocation.seat, 1)

    def test_missing_source_blocks_finalization_and_custom_draft_is_never_replaced(self):
        self.read.side_effect = lambda *args: {'modules': None, 'weeks': None, 'components': None,
            'ksb_mappings': None, 'blockers': [{'code': 'select_denied', 'message': 'LMS curriculum.components: select denied'}]}
        source = self.original()
        draft = self.create(source)
        before = TrainingPlanDocument.objects.count()
        with self.assertRaisesRegex(TemplateError, 'select denied'):
            revisions.finalize(draft, 'Isolated Admin')
        self.assertEqual(TrainingPlanDocument.objects.count(), before)
        self.assertEqual(Revision.objects.get(pk=draft.pk).state, 'draft')
        draft.content['blocks'][0]['text'] = 'Administrator custom draft'
        draft.edit_version = 2
        draft.save()
        with self.assertRaises(revisions.RevisionLocked):
            self.create(source)
        draft.refresh_from_db()
        self.assertEqual(draft.content['blocks'][0]['text'], 'Administrator custom draft')

    def test_module_overlap_keeps_meetings_separate_and_no_duplicate_activity_hours(self):
        result = activities.build(self.source)
        self.assertEqual(len(result['reviews']), 2)
        # Fixture module ranges overlap: never arbitrarily assign a coaching date.
        self.assertTrue(result['unassignedMeetings'])
        self.assertEqual(result['totals']['moduleHours'], 99.75 * len(self.plan))
        self.assertTrue(all(r['hours'] is None for m in result['modules'] for r in m['activities'] if r.get('parentComponentId')))

    def test_existing_preview_token_finalize_and_download_routes_use_new_snapshot(self):
        source = self.original()
        detail = self.result(self.request(revision_views.start_revision, 'POST', {'sourceId': str(source.pk),
            'mode': 'learner', 'requestId': str(uuid.uuid4())}, pk=self.source.pk, key='training-plan'), 201)
        identifier = detail['revision']['id']
        preview = self.result(self.request(revision_views.preview_revision, 'POST',
            {'editVersion': detail['revision']['editVersion']}, pk=self.source.pk, identifier=identifier))
        final = self.result(self.request(revision_views.finalize_revision, 'POST',
            {'previewToken': preview['previewToken']}, pk=self.source.pk, identifier=identifier))
        self.assertEqual(final['revision']['state'], 'finalized')
        self.assertEqual(final['preview']['data']['activitiesOverview'], detail['preview']['data']['activitiesOverview'])
        download = self.request(pdf_views.revision_pdf, pk=self.source.pk, identifier=identifier)
        revision = Revision.objects.get(pk=identifier)
        artifact = pdf.artifact(revision.issue, revisions.payload(revision), revision.pk)
        self.assertEqual(download.content, base64.b64decode(artifact.payload['pdf']))
        from .test_revisions import MARK
        frozen = copy.deepcopy(revision.data['activitiesOverview'])
        with patch('learner_api.training_plan_document.advance_learner', return_value=False):
            signed = self.result(self.request(revision_views.sign_revision, 'POST',
                {'party': 'provider', 'name': 'Isolated Provider', 'signature': MARK},
                pk=self.source.pk, identifier=identifier))
        self.assertEqual(signed['preview']['data']['activitiesOverview'], frozen)
        self.assertEqual(signed['preview']['data']['marks']['provider'], MARK)
        source.refresh_from_db()
        self.assertEqual(source.apprentice_signature, 'historical-mark')

    def test_long_module_repeats_activity_headers_and_contains_last_recorded_activity(self):
        def long_content(mid, pid, cid, gid):
            raw = self.content(mid, pid, cid, gid)
            if mid == self.plan[0]['moduleId']:
                topic = raw['components'][1]
                raw['components'].extend([{**topic, 'id': 'long-' + str(i), 'title': f'Isolated long module activity {i}',
                    'display_order': i + 5} for i in range(160)])
            return raw
        self.read.side_effect = long_content
        draft = self.create(self.original())
        artifact = pdf.store_revision(draft)
        self.export('long-module', draft, artifact)
        import fitz
        document = fitz.open(stream=base64.b64decode(artifact.payload['pdf']), filetype='pdf')
        texts = [p.get_text() for p in document]
        self.assertTrue(any('Isolated long module activity 159' in t for t in texts))
        self.assertGreater(sum('Learning Activity' in t for t in texts), 3)

    def test_new_edit_revision_upgrades_design_without_refreshing_existing_custom_draft(self):
        source = self.original()
        before = revisions.serialize_source('training-plan', source, self.source)
        draft = revisions.create_draft('training-plan', source.pk, self.source, 'Isolated Admin')
        self.assertEqual(draft.data['activitiesOverview']['version'], activities.VERSION)
        self.assertIn('Activities Overview', str(draft.content))
        draft.content['blocks'][0]['text'] = 'Custom draft title'
        draft.save()
        self.read.side_effect = AssertionError('Resuming a custom draft must not refresh its data')
        again = revisions.create_draft('training-plan', source.pk, self.source, 'Isolated Admin')
        self.assertEqual(again.pk, draft.pk)
        self.assertEqual(again.content['blocks'][0]['text'], 'Custom draft title')
        again.reason = 'Updated learner Activities Overview'
        again.signature_method = 'sign-again'
        revisions.finalize(again, 'Isolated Admin')
        signing = revisions.source_record('training-plan', self.source.pk, again.signing_source_id)
        self.assertEqual(signing.otjh['activitiesOverview'], again.data['activitiesOverview'])
        self.assertEqual(revisions.payload(again)['activitiesOverview'], again.data['activitiesOverview'])
        self.assertIsNotNone(pdf.store_revision(again))
        source.refresh_from_db()
        self.assertEqual(revisions.serialize_source('training-plan', source, self.source), before)

    def test_verified_meeting_dates_are_placed_once_in_unique_module_ranges(self):
        ranges = [('2027-02-01', '2027-05-31'), ('2027-06-01', '2027-09-30'), ('2027-10-01', '2028-01-31')]
        for entry, (start, end) in zip(self.plan, ranges):
            entry.update(startDate=start, endDate=end)
        self.source.learning_plan = self.plan
        self.source.save()
        def dated_weeks(mid, pid, cid, gid):
            raw = self.content(mid, pid, cid, gid)
            from datetime import timedelta
            entry = next(e for e in self.plan if e['lmsOfferingId'] == mid)
            start = date.fromisoformat(entry['startDate'])
            template = raw['weeks'][1]
            raw['weeks'] = [{**template, 'id': 'earlier' if i == 0 else 'later' if i == 1 else template['id'] + str(i),
                'title': (start + timedelta(days=i * 7)).isoformat(), 'display_order': i} for i in range(19)]
            return raw
        self.read.side_effect = dated_weeks
        overview = activities.build(self.source)
        placed = [r for m in overview['modules'] for r in m['activities'] if r['kind'] == 'meeting']
        self.assertEqual(len(placed), 12)
        self.assertEqual(len({r['id'] for r in placed}), 12)
        self.assertFalse(overview['unassignedMeetings'])
        self.assertEqual(overview['totals']['meetingHours'], 12)
        self.assertEqual(overview['totals']['totalHours'], overview['totals']['moduleHours'] + 12)
        self.assertTrue(overview['finalisationAllowed'], overview['blockers'])
        draft = self.create(self.original())
        revisions.finalize(draft, 'Isolated Admin')
        self.export('corrected', draft, pdf.store_revision(draft))

    def test_explicit_initial_issue_uses_the_new_frozen_design_and_missing_data_cannot_supersede_it(self):
        from learner_api import training_plan_document as tp
        issued = self.result(self.request(tp.issue_training_plan, 'POST', pk=self.source.pk), 201)['document']
        self.assertEqual(issued['activitiesOverview']['version'], activities.VERSION)
        self.assertFalse(issued['fullySigned'])
        self.assertIn('Activities Overview', str(issued['template']['content']))
        source = TrainingPlanDocument.objects.get(pk=issued['id'])
        artifact = pdf.artifact(service.binding_for('training-plan', source), issued)
        self.assertIsNotNone(artifact)
        self.read.side_effect = lambda *args: {'modules': None, 'blockers': [{'code': 'select_denied'}]}
        failed = self.request(tp.issue_training_plan, 'POST', pk=self.source.pk)
        self.assertEqual(failed.status_code, 409)
        source.refresh_from_db()
        self.assertEqual(source.status, source.STATUS_ACTIVE)
        self.assertEqual(TrainingPlanDocument.objects.count(), 1)

    def test_module_period_outside_actual_saved_dates_blocks_issuance_without_changing_dates(self):
        self.source.end_date = '2027-03-01'
        result = activities.build(self.source)
        self.assertFalse(result['finalisationAllowed'])
        self.assertIn('module_outside_saved_programme_period', [b['code'] for b in result['blockers']])
        self.assertEqual(self.source.end_date, '2027-03-01')

    def test_activity_zero_null_and_only_verified_assignment_deadlines(self):
        def content(mid, pid, cid, gid):
            raw = self.content(mid, pid, cid, gid)
            assignment = next(c for c in raw['components'] if c['type'] == 'assignment')
            assignment['settings_json']['dueTiming'] = '2027-03-14'
            for index, c in enumerate(raw['components']):
                if c is not assignment:
                    c['expected_otjh'] = None if index == 1 else 0
                    c['settings_json']['recordingDate'] = '2027-03-11'
                    c['settings_json']['durationMinutes'] = 90
            return raw
        self.read.side_effect = content
        overview = activities.build(self.source)
        rows = [r for m in overview['modules'] for r in m['activities'] if r['source'] == 'LMS component']
        self.assertTrue(any(r['hours'] is None for r in rows))
        self.assertTrue(any(r['hours'] == 0 for r in rows))
        self.assertTrue(all(r['date'] == '2027-03-14' for r in rows if r['kind'] == 'assignment'))
        self.assertTrue(all(r['date'] is None for r in rows if r['kind'] != 'assignment'))
        self.assertEqual(overview['totals']['moduleHours'], 99.75 * len(self.plan))

    def test_existing_end_of_week_rule_and_missing_calendar_fail_without_inventing_dates(self):
        component = {'timing': {'dueTiming': 'End of week', 'recordingDate': '2027-12-31'}}
        self.assertEqual(activities.assignment_due(component, {'title': '2027-03-09'})[0], '2027-03-14')
        self.assertEqual(activities.assignment_due(component, {'title': 'Week 1'}), (None, None))
        window = {'start': '2027-03-08', 'end': '2027-03-14', 'source': 'existing Builder delivery/holiday rule'}
        self.assertEqual(activities.assignment_due(component, {'title': 'Week 1'}, window)[0], '2027-03-14')
        component['timing']['dueTiming'] = 'Not a deadline'
        self.assertEqual(activities.assignment_due(component, {'title': 'Week 1'}, window), (None, None))

    def test_only_live_session_titles_use_their_own_linked_week_and_keep_identity_hours(self):
        def content(mid, pid, cid, gid):
            raw = self.content(mid, pid, cid, gid)
            for i, week in enumerate(raw['weeks']):
                week['title'] = f'L{i + 1}: Authored lesson {mid}'
                raw['components'].append(dict(id=f'live-{i}', module_catalogue_id=mid, week_id=week['id'],
                    display_order=3, type='live_session' if i == 0 else 'live-session', title='Live Teams Session 1',
                    expected_otjh=2.5, settings_json={}, ksb_mappings=[]))
            return raw
        self.read.side_effect = content
        result = activities.build(self.source)
        for module in result['modules']:
            live = [r for r in module['activities'] if r['id'].startswith('live-')]
            self.assertEqual(len(live), 2)
            self.assertNotEqual(live[0]['title'], live[1]['title'])
            for row in live:
                self.assertEqual(row['title'], row['weekTitle'])
                self.assertEqual(row['componentTitle'], 'Live Teams Session 1')
                self.assertEqual(row['titleSource'], 'curriculum.weeks.title')
                self.assertEqual(row['hours'], 2.5)
                self.assertIsNone(row['date'])
            self.assertTrue(any(r['title'] == 'Synthetic recorded topic' for r in module['activities']))
            self.assertTrue(any(r['title'] == 'Synthetic evidence exercise' for r in module['activities']))
        self.assertEqual(result['totals']['moduleHours'], 99.75 * len(self.plan))

    def test_unissued_title_presentation_uses_captured_week_without_source_reads(self):
        original = {'activitiesOverview': {'version': activities.VERSION, 'modules': [{'activities': [
            {'kind':'live_session', 'id':'live', 'title':'Live Teams Session 1', 'weekId':'w1', 'weekTitle':'L1: Definitions', 'hours':2.5},
            {'kind':'reading', 'id':'reading', 'title':'Recorded textbook', 'weekId':'w1', 'weekTitle':'L1: Definitions', 'hours':.5},
            {'kind':'live_session', 'id':'undated', 'title':'Original session', 'weekTitle':'', 'hours':0}]}]}}
        original['otjh'] = {'activitiesOverview': copy.deepcopy(original['activitiesOverview'])}
        before = copy.deepcopy(original)
        cleaned = activities.clean_document_presentation(original)
        updated = cleaned['activitiesOverview']['modules'][0]['activities']
        self.assertEqual(cleaned['otjh']['activitiesOverview'], cleaned['activitiesOverview'])
        self.assertEqual(updated[0]['title'], 'L1: Definitions')
        self.assertEqual(updated[0]['id'], 'live')
        self.assertEqual(updated[0]['hours'], 2.5)
        self.assertEqual(updated[1]['title'], 'Recorded textbook')
        self.assertEqual(updated[2]['title'], 'Original session')
        self.assertEqual(original, before)

    def test_weekly_schedule_contains_only_progress_with_programme_specific_roles(self):
        result = activities.build(self.source)
        self.assertTrue(result['reviews'])
        progress = [r for r in result['reviews'] if r['reviewTypeCode'] == 'progress_review']
        self.assertEqual(len(progress), 2)
        self.assertEqual(sum(r['reviewTypeCode'] == 'mcm' for r in result['reviews']), 0)
        self.assertTrue(all(r['requiredRoles'] == ['advisor', 'employer', 'participant'] for r in result['reviews']))
        self.assertTrue(all(r['title'] == 'Renamed development checkpoint' for r in progress))
        self.assertFalse(any(r.get('reviewTypeCode') == 'progress_review' for m in result['modules'] for r in m['activities']))
        self.assertEqual(result['totals']['meetingHours'], 12)
        self.definition_read.assert_called_once_with('PROG-ME-L4', ['PROG-ME-L4-coaching', 'PROG-ME-L4-progress'])

    def test_missing_classification_or_signatures_is_explicit_and_never_guessed_from_name(self):
        self.definition_read.return_value = {'definitions': {}, 'blockers': [{'code': 'select_denied', 'table': 'review_templates', 'columns': ['review_type_id']}]}
        self.definition_read.side_effect = None
        result = activities.build(self.source)
        self.assertFalse(result['finalisationAllowed'])
        self.assertFalse(result['reviews'])
        self.assertEqual(len(result['unclassifiedMeetings']), 14)
        self.assertFalse(any(r['kind'] == 'meeting' for m in result['modules'] for r in m['activities']))
        self.assertTrue(any(b['code'] == 'review_type_unavailable' for b in result['blockers']))
        definitions = self.definitions('PROG-ME-L4', ['PROG-ME-L4-coaching', 'PROG-ME-L4-progress'])
        definitions['definitions']['PROG-ME-L4-progress']['signatures'] = None
        self.definition_read.return_value = definitions
        result = activities.build(self.source)
        self.assertTrue(all(r['participants'] is None for r in result['reviews'] if r['reviewTypeCode'] == 'progress_review'))
        self.assertTrue(any(b['code'] == 'review_signatories_unavailable' for b in result['warnings']))

    def test_unknown_review_types_preserved_separately_without_changing_totals(self):
        raw = self.reviews('PROG-ME-L4', None, None, None)
        for name in ('Gateway Review', 'Personal Support Plan'):
            raw['review_templates'].append({**raw['review_templates'][1], 'id': name,
                'name': name, 'occurrence_count': 1})
        self.review_read.side_effect = None
        self.review_read.return_value = raw
        self.definition_read.side_effect = None
        self.definition_read.return_value = {'definitions': {}, 'blockers': []}
        result = activities.build(self.source)
        self.assertFalse(result['reviews'])
        self.assertEqual(len(result['unclassifiedMeetings']), 16)
        self.assertTrue({'Gateway Review', 'Personal Support Plan'}.issubset({r['title'] for r in result['unclassifiedMeetings']}))
        self.assertEqual(result['totals']['meetingHours'], 12)
        self.assertEqual(len({r['id'] for r in result['unclassifiedMeetings']}), 16)

    def test_known_gateway_support_templates_stay_in_meetings_but_not_weekly_pdf_schedule(self):
        raw = self.reviews('PROG-ME-L4', None, None, None)
        for name in ('Gateway Review', 'Personal Support Plan'):
            raw['review_templates'].append({**raw['review_templates'][1], 'id': name, 'name': name, 'occurrence_count': 1})
        original = copy.deepcopy(raw)
        self.review_read.side_effect = None; self.review_read.return_value = raw
        def definitions(pid, ids):
            result = self.definitions(pid, ids)
            result['definitions']['Gateway Review']['typeCode'] = 'gateway_review'
            result['definitions']['Personal Support Plan']['typeCode'] = 'personal_support_plan'
            return result
        self.definition_read.side_effect = definitions
        result = activities.build(self.source)
        self.assertEqual(len(result['reviews']), 2)
        self.assertTrue(all(r['reviewTypeCode'] == 'progress_review' for r in result['reviews']))
        self.assertEqual(result['totals']['meetingHours'], 12)
        self.assertFalse(result['unclassifiedMeetings'])
        self.assertEqual(raw, original)

    def test_existing_unissued_draft_preview_and_final_copy_hide_diagnostics_but_keep_admin_data(self):
        draft = self.create(self.original())
        draft.data['activitiesOverview']['version'] = 'activities-overview-v3'
        warning = {'code': 'select_denied', 'message': 'PRIVATE_DIAGNOSTIC LMS curriculum.groups: select denied'}
        draft.data['activitiesOverview']['warnings'].append(warning)
        draft.content['blocks'][0]['text'] = 'Preserve administrator wording'
        draft.save()
        captured = copy.deepcopy(draft.data)
        preview = revisions.payload(draft)
        self.assertEqual(preview['activitiesOverview']['version'], 'activities-overview-v4')
        self.assertIn(warning, preview['activitiesOverview']['warnings'])
        self.assertEqual(draft.data, captured)
        artifact = pdf.store_revision(draft)
        import fitz
        def pdf_text(artifact):
            doc = fitz.open(stream=base64.b64decode(artifact.payload['pdf']), filetype='pdf')
            return '\n'.join(page.get_text() for page in doc)
        self.assertNotIn('PRIVATE_DIAGNOSTIC', pdf_text(artifact))
        revisions.finalize(draft, 'Isolated Admin')
        self.assertIn(warning, draft.data['activitiesOverview']['warnings'])
        self.assertEqual(draft.data['activitiesOverview']['version'], 'activities-overview-v4')
        self.assertEqual(draft.content['blocks'][0]['text'], 'Preserve administrator wording')
        self.assertNotIn('PRIVATE_DIAGNOSTIC', pdf_text(pdf.store_revision(draft)))

    def test_hidden_critical_diagnostics_still_block_issuance(self):
        draft = self.create(self.original())
        draft.data['activitiesOverview'].update(version='activities-overview-v3', finalisationAllowed=False,
            blockers=[{'code':'missing_source', 'message':'Critical source unavailable'}])
        with self.assertRaisesRegex(TemplateError, 'Critical source unavailable'):
            revisions.finalize(draft, 'Isolated Admin')
        self.assertEqual(draft.state, 'draft')

    def test_coaching_enters_verified_week_before_later_assignment_and_progress_stays_separate(self):
        self.review_read.side_effect = None
        self.review_read.return_value = {'review_templates': [dict(id='one-coaching', programme_id='PROG-ME-L4',
            name='Renamed monthly conversation', enabled=True, recurrence_interval=1, recurrence_unit='months',
            occurrence_count=1, applicable_statuses=[], applicability=None, expected_otjh=1,
            counts_towards_otjh=True, deleted_at=None)], 'review_occurrence_overrides': [], 'blockers': []}
        # Other module ranges overlap: keep them outside this one verified date.
        for entry in self.plan[1:]:
            entry.update(startDate='2027-06-01', endDate='2028-01-31')
        self.source.learning_plan = self.plan; self.source.save()
        def calendar(mid, pid, cid, gid, weeks):
            return {'weeks': {weeks[0]['sourceId']: {'start': '2027-02-01', 'end': '2027-02-07',
                'source': 'existing Builder delivery/holiday rule'}}, 'blockers': []}
        self.calendar_read.side_effect = calendar
        result = activities.build(self.source)
        placed = [r for m in result['modules'] for r in m['activities'] if r['kind'] == 'meeting']
        self.assertEqual(len(placed), 1)
        self.assertEqual(placed[0]['date'], '2027-02-01')
        self.assertEqual(placed[0]['reviewTypeCode'], 'mcm')
        rows = result['modules'][0]['activities']
        self.assertLess(rows.index(placed[0]), next(i for i, r in enumerate(rows) if r['kind'] == 'assignment'))
        self.assertFalse(result['reviews'])
        self.assertFalse(result['unassignedMeetings'])

    def test_weekly_large_pdf_preserves_every_week_component_and_duplicate_named_resource(self):
        from datetime import timedelta
        from collections import Counter
        import fitz
        root = Path(__file__).resolve().parents[3] / '.cache' / 'weekly-training-plan'
        root.mkdir(parents=True, exist_ok=True)
        ranges = [('2027-02-01', '2027-06-20'), ('2027-06-21', '2027-09-26'), ('2027-09-28', '2028-01-04')]
        for entry, (start, end) in zip(self.plan, ranges):
            entry.update(startDate=start, endDate=end)
        self.source.learning_plan = self.plan; self.source.save()
        sources = {}
        def content(mid, pid, cid, gid):
            raw = self.content(mid, pid, cid, gid)
            entry = next(e for e in self.plan if e['lmsOfferingId'] == mid)
            start = date.fromisoformat(entry['startDate'])
            count = 20 if mid == self.plan[0]['lmsOfferingId'] else 3
            raw['weeks'], raw['components'], raw['ksb_mappings'] = [], [], []
            for i in range(count):
                wid = f'{mid}:week:{i}'
                raw['weeks'].append(dict(id=wid, module_catalogue_id=mid, display_order=i,
                    week_number=i+1, title=f'Synthetic lesson {i+1}: Marketing concepts and practice'))
                # The final real week is empty, and must still appear.
                if i == count - 1:
                    continue
                types = ['live_session', 'reading', 'quiz', 'assignment', 'video'] + ['powerpoint', 'video', 'podcast'] * 5
                for j, typ in enumerate(types):
                    cid = f'{wid}:component:{j}'
                    title = f'{typ} full source name {i+1}.{j}: recorded marketing learning resource'
                    if j in (6, 9):
                        title = f'Identically named video for week {i+1}'
                    expected = 2.5 if typ == 'live_session' else .5 if typ in ('reading', 'quiz') else 8 if typ == 'assignment' else .75 if j == 4 else None if i == 1 and j == 7 else 0
                    settings = {'dueTiming': (start + timedelta(days=i*7+6)).isoformat()} if typ == 'assignment' else {}
                    raw['components'].append(dict(id=cid, module_catalogue_id=mid, week_id=wid, display_order=j,
                        type=typ, title=title, expected_otjh=expected, settings_json=settings, ksb_mappings=[]))
            raw['ksb_mappings'] = [dict(id=mid+':ksb', module_catalogue_id=mid, week_id=None,
                component_id=None, ksb_code='K1', ksb_description='Fixture verified knowledge', weight=0)]
            sources[mid] = copy.deepcopy(raw)
            return raw
        def calendar(mid, pid, cid, gid, weeks):
            start = date.fromisoformat(next(e['startDate'] for e in self.plan if e['lmsOfferingId'] == mid))
            return {'weeks': {w['sourceId']: {'start': (start+timedelta(days=i*7)).isoformat(),
                'end': (start+timedelta(days=i*7+6)).isoformat(), 'source':'isolated verified calendar fixture'}
                for i, w in enumerate(weeks)}, 'blockers': []}
        self.read.side_effect, self.calendar_read.side_effect = content, calendar
        draft = self.create(self.original())
        overview = draft.data['activitiesOverview']
        self.assertTrue(overview['finalisationAllowed'], overview['blockers'])
        reconciliation = []
        for module in overview['modules']:
            source = sources[module['lmsOfferingId']]
            actual = [r for r in module['activities'] if r['source'] == 'LMS component']
            self.assertEqual(Counter(c['id'] for c in source['components']), Counter(r['id'] for r in actual))
            self.assertEqual([w['id'] for w in source['weeks']], [w['sourceId'] for w in module['weeks']])
            for row in actual:
                self.assertIn(row['weekId'], {w['sourceId'] for w in module['weeks']})
            reconciliation.append({'moduleId':module['lmsOfferingId'], 'weeks':len(module['weeks']),
                'sourceComponentIds':[c['id'] for c in source['components']], 'snapshotComponentIds':[r['id'] for r in actual]})
        frozen = copy.deepcopy(revisions.payload(draft))
        before = copy.deepcopy(frozen); before['activitiesOverview']['version'] = 'activities-overview-v4'
        before_pdf = pdf.render({'template':before['template'], 'data':before})
        # Exercise the real frozen revision finalization/download renderer.
        revisions.finalize(draft, 'Isolated Admin')
        after_pdf = base64.b64decode(pdf.store_revision(draft).payload['pdf'])
        before_doc, after_doc = fitz.open(stream=before_pdf, filetype='pdf'), fitz.open(stream=after_pdf, filetype='pdf')
        texts = [p.get_text() for p in after_doc]
        self.assertLess(len(after_doc), len(before_doc))
        import re
        extracted = re.sub(r'\s+', ' ', ' '.join(texts))
        expected_titles = Counter(r['title'] for m in overview['modules'] for r in m['activities'] if r['source'] == 'LMS component')
        for title, count in expected_titles.items():
            self.assertGreaterEqual(extracted.count(re.sub(r'\s+', ' ', title)), count, title)
        self.assertTrue(any('Week 20:' in t for t in texts))
        self.assertTrue(any('Supporting Resources' in t for t in texts))
        self.assertTrue(any('OTJ unavailable' in t for t in texts))
        self.assertEqual(overview['totals'], draft.data['activitiesOverview']['totals'])
        (root/'before-flat.pdf').write_bytes(before_pdf); (root/'after-weekly.pdf').write_bytes(after_pdf)
        (root/'snapshot.json').write_text(json.dumps({'template':frozen['template'], 'data':frozen}, default=str), encoding='utf-8')
        (root/'reconciliation.json').write_text(json.dumps({'beforePages':len(before_doc), 'afterPages':len(after_doc),
            'totalsUnchanged':overview['totals'], 'modules':reconciliation}, indent=2), encoding='utf-8')
        selected = sorted({i for i, t in enumerate(texts) if 'Activities Overview' in t or 'Reviews Schedule' in t} |
            {next(i for i, t in enumerate(texts) if 'Supporting Resources' in t)})
        for i in selected:
            after_doc[i].get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(root/f'weekly-page-{i+1}.png')
