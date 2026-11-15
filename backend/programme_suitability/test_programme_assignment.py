import copy
import io
import json
import os
from contextlib import nullcontext
from datetime import date, datetime, timezone as datetime_timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.db import DatabaseError, connection, connections, transaction
from django.test import RequestFactory, TestCase, override_settings

from learner_api.models import EnrolmentUser, LearnerProfile
from . import views
from .configuration import defaults
from .models import Assessment
from .programme_assignment import CATALOGUE_NAMES, automatic_cohort_window
from .intake_calendar import resolve_intake, validate_calendar
from .tests import answers


class ProgrammeAssignmentIntegrationTests(TestCase):
    databases = {'default', 'enrolment'}

    @override_settings(PROGRAMME_STRUCTURE_AUTOMATIC_COHORT_ASSIGNMENT_ENABLED=True)
    def test_historical_reconciliation_cannot_enter_future_cohort_assignment(self):
        self.submit()
        row = Assessment.objects.get()
        row.selection_confirmed_at = row.confirmed_at
        row.save(update_fields=['selection_confirmed_at'])
        from .programme_assignment import sync_confirmed_programme_to_learner
        with patch('programme_suitability.future_cohort.resolve_future_cohort') as resolve:
            with transaction.atomic():
                result = sync_confirmed_programme_to_learner(row, reconcile=True, dry_run=True)
        resolve.assert_not_called()
        self.assertIsNone(result['automaticCohort'])
        self.profile.refresh_from_db(); self.source.refresh_from_db()
        self.assertIsNone(self.profile.cohort_id)
        self.assertEqual(self.source.programme, '')

    @override_settings(PROGRAMME_STRUCTURE_AUTOMATIC_COHORT_ASSIGNMENT_ENABLED=True,
                       PROGRAMME_STRUCTURE_COHORT_LEARNER_ENABLED=True)
    def test_future_first_confirmation_persists_confirmed_programme_cohort_and_shared_display(self):
        self.assert_future_confirmation_assignment(with_profile=True)

    @override_settings(PROGRAMME_STRUCTURE_AUTOMATIC_COHORT_ASSIGNMENT_ENABLED=True,
                       PROGRAMME_STRUCTURE_COHORT_LEARNER_ENABLED=True)
    def test_future_confirmation_without_profile_saves_stable_identity_and_shared_display(self):
        self.assert_future_confirmation_assignment(with_profile=False)

    @override_settings(PROGRAMME_STRUCTURE_AUTOMATIC_COHORT_ASSIGNMENT_ENABLED=True,
                       PROGRAMME_STRUCTURE_COHORT_LEARNER_ENABLED=True)
    def test_account_write_failure_rolls_back_cohort_programme_and_confirmation_together(self):
        self.assert_future_confirmation_assignment(with_profile=False, fail_first=True)

    def assert_future_confirmation_assignment(self, *, with_profile, fail_first=False):
        # All records below exist only in the isolated in-memory test databases.
        # No Programme Structure implementation/defaults/publications are edited.
        cohort = {'id': 'real-future-feb', 'programmeId': 'ME_L4', 'name': 'February 2027 - Real group',
                  'status': 'planned', 'assignmentPeriod': {'year': 2027, 'startMonth': 10,
                  'startDay': 16, 'endMonth': 2, 'endDay': 15},
                  'modules': [{'id': 'fixed-module', 'title': 'Fixed module', 'active': True,
                               'selection': 'required', 'sessions': 12}], 'mappings': []}
        layout = {'programmeStructure': {'schemaVersion': 2, 'programmes': [{
            'id': 'ME_L4', 'curriculumProgrammeId': 'PROG-ME-L4', 'title': 'Marketing Executive',
            'modules': [], 'ksbs': [], 'mappings': [], 'cohorts': [cohort]}]}}
        with connection.cursor() as cursor:
            cursor.execute('UPDATE curriculum.programmes SET programme_id=%s WHERE programme_id=%s',
                           ['PROG-ME-L4', 'CAT-ME_L4'])
            cursor.execute('INSERT INTO curriculum.cohorts (cohort_id,cohort_name,programme_id,programme_name) VALUES (%s,%s,%s,%s)',
                           [cohort['id'], cohort['name'], 'PROG-ME-L4', CATALOGUE_NAMES['ME_L4']])
        with connections['enrolment'].cursor() as cursor:
            cursor.execute('INSERT INTO enrolment."Wizard_Form_Layouts" (id,"Layout") VALUES (%s,%s)',
                           [50, json.dumps(layout)])
        if not with_profile:
            with connection.cursor() as cursor:
                cursor.execute('DELETE FROM "Learner".learners WHERE id = %s', [self.profile.pk])
        self.submit()  # The assessment recommends MM_L6.
        timestamp = datetime(2026, 12, 1, 10, 30, tzinfo=datetime_timezone.utc)
        if fail_first:
            with patch.object(EnrolmentUser, 'save', side_effect=DatabaseError('Synthetic account failure')):
                failed = self.request({'revision': 1, 'action': 'select', 'selectedProgramme': 'ME_L4',
                                       'alternativeSelectionReason': 'My confirmed delivery programme.'})
            self.assertEqual(failed.status_code, 503, failed.content)
            self.source.refresh_from_db()
            self.assertEqual((self.source.programme, self.source.cohort), ('', ''))
            failed_row = Assessment.objects.get()
            self.assertIsNone(failed_row.selection_confirmed_at)
            self.assertNotIn('programmeAssignment', failed_row.review)
        with patch('programme_suitability.views.timezone.now', return_value=timestamp):
            response = self.request({'revision': 1, 'action': 'select', 'selectedProgramme': 'ME_L4',
                                     'alternativeSelectionReason': 'My confirmed delivery programme.'})
        self.assertEqual(response.status_code, 200, response.content)
        self.source.refresh_from_db()
        row = Assessment.objects.get()
        self.assertEqual(row.selected_programme, 'ME_L4')
        self.assertEqual(row.selection_confirmed_at, timestamp)
        if with_profile:
            self.profile.refresh_from_db()
            self.assertEqual(self.profile.programme_id, 'PROG-ME-L4')
            self.assertEqual(self.profile.cohort_id, cohort['id'])
            self.assertEqual(self.profile.cohort, cohort['name'])
        self.assertEqual(self.source.programme, CATALOGUE_NAMES['ME_L4'])
        self.assertEqual(self.source.cohort, cohort['name'])
        assignment = row.review['programmeAssignment']
        self.assertEqual(assignment['cohortAssignmentVersion'], 1)
        self.assertEqual(assignment['programmeId'], 'PROG-ME-L4')
        self.assertEqual(assignment['cohortId'], cohort['id'])
        self.assertEqual(assignment['cohortPublicationVersion'], 50)
        self.assertEqual(assignment['profileId'], self.profile.pk if with_profile else None)
        from .home_cohort import read_home_cohort, cohort_label_for_board
        home = read_home_cohort(row, 'apprenticeship', self.source.pk, row.modules_configuration_version)
        self.assertEqual(home['status'], 'resolved')
        self.assertEqual(home['name'], cohort_label_for_board(self.source, self.source.cohort))
        self.assertEqual(home['name'], cohort['name'])
        from learner_api.mappers import to_board
        self.assertEqual(to_board(self.source)['programme']['cohort'], home['name'])
        before = Assessment.objects.values().get()
        self.assertEqual(self.request({'revision': row.revision, 'action': 'continue'}).status_code, 200)
        self.assertEqual(Assessment.objects.values().get(), before)
        self.assertEqual(LearnerProfile.objects.count(), int(with_profile))
        if with_profile:
            self.profile.refresh_from_db(); self.assertEqual(self.profile.cohort_id, cohort['id'])
        self.source.refresh_from_db()
        self.assertEqual(self.source.cohort, cohort['name'])

    @override_settings(PROGRAMME_STRUCTURE_AUTOMATIC_COHORT_ASSIGNMENT_ENABLED=True)
    def test_future_confirmation_without_canonical_publication_preserves_pending(self):
        self.submit()
        with patch('programme_suitability.programme_assignment.resolve_automatic_cohort') as resolve:
            response = self.request({'revision': 1, 'action': 'continue'})
        self.assertEqual(response.status_code, 200, response.content)
        resolve.assert_not_called()
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.cohort_id)
        self.assertEqual(self.profile.cohort, '')
        self.assertTrue(Assessment.objects.get().review['programmeAssignment']['automaticCohort']['pending'])

    def assert_legacy_cohort_is_not_reassigned(self, *, source_cohort, profile_cohort):
        with connection.cursor() as cursor:
            cursor.execute('INSERT INTO curriculum.cohorts (cohort_id, cohort_name, programme_id, programme_name) VALUES (%s,%s,%s,%s)',
                           ['existing', 'Existing cohort', 'CAT-MM_L6', CATALOGUE_NAMES['MM_L6']])
        EnrolmentUser.all_learners.filter(pk=self.source.pk).update(cohort=source_cohort)
        LearnerProfile.objects.filter(pk=self.profile.pk).update(cohort=profile_cohort, cohort_id=None)
        self.submit()
        with patch('programme_suitability.programme_assignment.resolve_automatic_cohort') as resolve:
            response = self.request({'revision': 1, 'action': 'continue'})
        self.assertEqual(response.status_code, 200, response.content)
        resolve.assert_not_called()
        self.source.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual(self.source.cohort, source_cohort)
        self.assertEqual(self.profile.cohort, profile_cohort)
        self.assertIsNone(self.profile.cohort_id)

    @override_settings(PROGRAMME_STRUCTURE_AUTOMATIC_COHORT_ASSIGNMENT_ENABLED=True)
    def test_automatic_assignment_preserves_legacy_profile_cohort_without_an_id(self):
        self.assert_legacy_cohort_is_not_reassigned(source_cohort='', profile_cohort='Existing cohort')

    @override_settings(PROGRAMME_STRUCTURE_AUTOMATIC_COHORT_ASSIGNMENT_ENABLED=True)
    def test_automatic_assignment_preserves_legacy_account_cohort_without_an_id(self):
        self.assert_legacy_cohort_is_not_reassigned(source_cohort='Existing cohort', profile_cohort='')

    @override_settings(PROGRAMME_STRUCTURE_AUTOMATIC_COHORT_ASSIGNMENT_ENABLED=True)
    def test_confirmation_without_an_existing_profile_does_not_create_or_invent_one(self):
        # Remove only this in-memory fixture; unrelated unmanaged FK tables
        # intentionally do not exist in the isolated assignment-test database.
        with connection.cursor() as cursor:
            cursor.execute('DELETE FROM "Learner".learners WHERE id = %s', [self.profile.pk])
        self.submit()
        with patch('programme_suitability.programme_assignment.resolve_automatic_cohort') as resolve:
            response = self.request({'revision': 1, 'action': 'continue'})
        self.assertEqual(response.status_code, 200, response.content)
        resolve.assert_not_called()
        self.assertFalse(LearnerProfile.objects.filter(enrolment_id=self.source.pk).exists())
        self.assertIsNone(Assessment.objects.get().review['programmeAssignment']['profileId'])

    def test_automatic_cohort_boundaries(self):
        self.assertEqual(automatic_cohort_window(date(2026, 11, 20)), ('Feb', 2027))
        self.assertEqual(automatic_cohort_window(date(2027, 2, 15)), ('Feb', 2027))
        self.assertEqual(automatic_cohort_window(date(2027, 2, 16)), ('Jun', 2027))
        self.assertEqual(automatic_cohort_window(date(2027, 6, 15)), ('Jun', 2027))
        self.assertEqual(automatic_cohort_window(date(2027, 6, 16)), ('Oct', 2027))
        self.assertEqual(automatic_cohort_window(date(2027, 10, 14)), ('Oct', 2027))
        self.assertEqual(automatic_cohort_window(date(2027, 10, 15)), ('Oct', 2027))
        self.assertEqual(automatic_cohort_window(date(2027, 10, 16)), ('Feb', 2028))

    def test_configurable_calendar_supports_fourth_intake_and_rejects_invalid_dates(self):
        calendar = [
            {'code': 'Feb', 'name': 'February', 'startMonth': 10, 'startDay': 15, 'endMonth': 2, 'endDay': 15, 'active': False},
            {'code': 'Jun', 'name': 'June', 'startMonth': 2, 'startDay': 16, 'endMonth': 6, 'endDay': 15, 'active': True},
            {'code': 'Oct', 'name': 'October', 'startMonth': 6, 'startDay': 16, 'endMonth': 10, 'endDay': 14, 'active': True},
            {'code': 'DEC', 'name': 'December', 'startMonth': 11, 'startDay': 1, 'endMonth': 11, 'endDay': 14, 'active': True},
        ]
        self.assertEqual(resolve_intake(date(2029, 11, 10), calendar), ('DEC', 2029))
        with self.assertRaises(ValueError): validate_calendar([*calendar, dict(calendar[0])])
    def setUp(self):
        self.config = defaults()
        self.factory = RequestFactory()
        self.source = EnrolmentUser(id=4101, username="Synthetic assignment learner", email="assignment@example.test",
            learner_type="apprenticeship", status="FullUser", programme_status="Onboarding", onboarding_status="Not started",
            programme="", cohort="", group="", learning_plan=[], start_date="2027-01-01", employer="Existing employer")
        EnrolmentUser.all_learners.bulk_create([self.source])
        self.profile = LearnerProfile(id=5101, enrolment_id=self.source.pk, email=self.source.email, full_name=self.source.username,
            lifecycle_status="inactive", learner_type="apprenticeship", programme="", programme_status="Onboarding",
            cohort="", group_name="", learning_plan=[], start_date=date(2027, 1, 2))
        LearnerProfile.objects.bulk_create([self.profile])
        with connection.cursor() as cursor:
            for code, name in CATALOGUE_NAMES.items():
                cursor.execute('INSERT INTO curriculum.programmes (programme_id, name, level) VALUES (%s, %s, %s)',
                               ["CAT-" + code, name, "Level " + code[-1]])
        for mock in (patch.dict(os.environ, {"ENROLMENT_API_REQUIRE_AUTH": "1"}),
                     patch.object(views, "published", side_effect=lambda: (3, copy.deepcopy(self.config))),
                     patch("enrolment_api.auth._accesses_of", return_value={"enrolment"})):
            mock.start(); self.addCleanup(mock.stop)

    def request(self, data=None, method="PUT", owner=4101, role="learner", endpoint=views.assessment):
        request = self.factory.get('/') if method == "GET" else self.factory.put('/', json.dumps(data), content_type="application/json")
        request.login_account = SimpleNamespace(is_active=True, role=role, subject_id=owner,
            subject_type="staff" if role == "staff" else "learner", email="synthetic@example.test")
        request._dont_enforce_csrf_checks = True
        return endpoint(request, kind="apprenticeship", learner_id=self.source.pk)

    def submit(self, pathway="marketing", index=3):
        response = self.request({"revision": 0, "configurationVersion": 3, "pathway": pathway,
                                 "responses": answers(self.config, pathway, (index,)), "action": "submit"})
        self.assertEqual(response.status_code, 200, response.content)
        return Assessment.objects.get()

    def assert_account_apis(self, expected):
        from learner_api import views as user_views
        from learner_api import learner_detail
        request = self.factory.get('/')
        request.login_account = SimpleNamespace(is_active=True, role="staff", subject_type="staff", subject_id=99)
        with patch.object(user_views, "_annotate_account_state"), patch("learner_api.directory.with_aptem_status", side_effect=lambda q: q):
            listing = user_views.enrolment_users(request)
        self.assertEqual(listing.status_code, 200, listing.content)
        self.assertEqual(json.loads(listing.content)["results"][0]["programme"], expected)
        fields = user_views.enrolment_user_fields(request, pk=self.source.pk)
        self.assertEqual(fields.status_code, 200, fields.content)
        self.assertEqual(json.loads(fields.content)["programme"], expected)
        # Exercise the existing learner heading API, without unrelated document/date lookups.
        request.login_account = SimpleNamespace(is_active=True, role="learner", subject_type="learner", subject_id=self.source.pk)
        with patch("learner_api.apprenticeship_agreement._group_dates", return_value=(None, None, "")), \
             patch.object(learner_detail, "access_gate", return_value={}), \
             patch.object(learner_detail, "learning_access", return_value={}):
            summary = learner_detail.learner_summary(request, kind="apprenticeship", pk=self.source.pk)
        self.assertEqual(summary.status_code, 200, summary.content)
        self.assertEqual(json.loads(summary.content)["programme"], expected)

    def test_all_recommendations_and_alternatives_update_the_real_relationship_and_api_projections(self):
        cases = [("marketing", 3, None, "MM_L6"), ("marketing", 3, "ME_L4", "ME_L4"), ("marketing", 1, None, "ME_L4"),
                 ("project-controls", 3, None, "PCP_L6"), ("project-controls", 3, "APM_L4", "APM_L4"), ("project-controls", 1, None, "APM_L4")]
        for pathway, index, alternative, expected in cases:
            with self.subTest(expected=expected, alternative=alternative):
                Assessment.objects.all().delete()
                EnrolmentUser.all_learners.filter(pk=self.source.pk).update(programme="")
                LearnerProfile.objects.filter(pk=self.profile.pk).update(programme="", programme_id=None)
                original = self.submit(pathway, index)
                self.source.refresh_from_db(); self.assertEqual(self.source.programme, "")
                data = {"revision": original.revision, "action": "select" if alternative else "continue"}
                if alternative:
                    data.update(selectedProgramme=alternative, alternativeSelectionReason="I prefer practical delivery.")
                response = self.request(data)
                self.assertEqual(response.status_code, 200, response.content)
                self.source.refresh_from_db(); self.profile.refresh_from_db()
                row = Assessment.objects.get()
                self.assertEqual(self.source.programme, CATALOGUE_NAMES[expected])
                self.assertEqual(self.profile.programme, CATALOGUE_NAMES[expected])
                self.assertEqual(self.profile.programme_id, "CAT-" + expected)
                self.assertEqual(self.source.start_date, "2027-01-01")
                self.assertEqual(self.profile.start_date, date(2027, 1, 2))
                self.assertEqual(self.source.employer, "Existing employer")
                self.assertEqual(self.source.programme_status, "Onboarding")
                self.assertEqual(row.result, original.result)
                self.assertEqual(json.loads(response.content)["programmeAssignment"]["programmeId"], "CAT-" + expected)
                self.assert_account_apis(CATALOGUE_NAMES[expected])
                revision = row.revision
                self.assertEqual(self.request(data).status_code, 200)
                self.assertEqual(Assessment.objects.get().revision, revision)
                self.assertEqual(LearnerProfile.objects.count(), 1)

    def test_provisional_programme_is_replaced_only_at_confirmation(self):
        EnrolmentUser.all_learners.filter(pk=self.source.pk).update(programme="Provisional programme")
        self.submit()
        self.source.refresh_from_db(); self.assertEqual(self.source.programme, "Provisional programme")
        self.assertEqual(self.request({"revision": 1, "action": "continue"}).status_code, 200)
        self.source.refresh_from_db(); self.assertEqual(self.source.programme, CATALOGUE_NAMES["MM_L6"])

    def test_an_unrelated_authorised_edit_keeps_the_confirmed_programme(self):
        from learner_api import views as user_views
        self.submit()
        self.assertEqual(self.request({"revision": 1, "action": "continue"}).status_code, 200)
        request = self.factory.patch('/', json.dumps({"phone": "07123456789"}), content_type="application/json")
        request.login_account = SimpleNamespace(is_active=True, role="staff", subject_type="staff", subject_id=99)
        with patch.object(user_views, "advance_learner"), \
             patch.object(user_views.transaction, "atomic", lambda **_: nullcontext()), \
             patch("learner_api.board_wizard.saved_signature", return_value=(None, None)), \
             patch("learner_api.board_wizard.merge_wizard_sections", side_effect=lambda board, *args, **kwargs: board):
            response = user_views.enrolment_user_detail(request, pk=self.source.pk)
        self.assertEqual(response.status_code, 200, response.content)
        self.source.refresh_from_db()
        self.assertEqual(self.source.programme, CATALOGUE_NAMES["MM_L6"])
        self.assertEqual(self.source.phone_number, "07123456789")

    def test_missing_or_ambiguous_catalogue_mapping_rolls_back_confirmation(self):
        for ambiguous in (False, True):
            Assessment.objects.all().delete()
            with connection.cursor() as cursor:
                cursor.execute('DELETE FROM curriculum.programmes WHERE name = %s', [CATALOGUE_NAMES["MM_L6"]])
                if ambiguous:
                    for identifier in ("duplicate-1", "duplicate-2"):
                        cursor.execute('INSERT INTO curriculum.programmes (programme_id, name, level) VALUES (%s,%s,%s)', [identifier, CATALOGUE_NAMES["MM_L6"], "6"])
            original = self.submit()
            response = self.request({"revision": 1, "action": "continue"})
            self.assertEqual(response.status_code, 409, response.content)
            row = Assessment.objects.get()
            self.assertIsNone(row.selection_confirmed_at); self.assertEqual(row.revision, original.revision)
            self.source.refresh_from_db(); self.assertEqual(self.source.programme, "")

    def test_profile_write_failure_rolls_back_source_confirmation_and_alternative_reason(self):
        original = self.submit()
        with patch.object(LearnerProfile, "save", side_effect=DatabaseError("Synthetic profile failure")):
            response = self.request({"revision": 1, "action": "select", "selectedProgramme": "ME_L4", "alternativeSelectionReason": "Delivery first."})
        self.assertEqual(response.status_code, 503, response.content)
        self.source.refresh_from_db(); self.assertEqual(self.source.programme, "")
        row = Assessment.objects.get()
        self.assertIsNone(row.selection_confirmed_at); self.assertIsNone(row.alternative_selection_reason)
        self.assertEqual(row.selected_programme, original.selected_programme)
        retried = self.request({"revision": 1, "action": "select", "selectedProgramme": "ME_L4", "alternativeSelectionReason": "Delivery first."})
        self.assertEqual(retried.status_code, 200, retried.content)
        self.source.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual(self.source.programme, CATALOGUE_NAMES["ME_L4"])
        self.assertEqual(self.profile.programme_id, "CAT-ME_L4")

    def test_legacy_null_learner_type_uses_existing_apprenticeship_compatibility(self):
        EnrolmentUser.all_learners.filter(pk=self.source.pk).update(learner_type=None)
        self.submit()
        self.assertEqual(self.request({"revision": 1, "action": "continue"}).status_code, 200)

    def test_incompatible_cohort_or_group_and_finalised_assignments_need_staff_review(self):
        for extra in ({"cohort": "Old cohort"}, {"group": "Old group"},
                      {"programme": "Approved programme", "onboarding_status": "Completed"},
                      {"programme": "Approved programme", "programme_status": "Active"}):
            Assessment.objects.all().delete()
            EnrolmentUser.all_learners.filter(pk=self.source.pk).update(programme="", cohort="", group="", onboarding_status="Not started", programme_status="Onboarding")
            EnrolmentUser.all_learners.filter(pk=self.source.pk).update(**extra)
            self.submit()
            response = self.request({"revision": 1, "action": "continue"})
            self.assertEqual(response.status_code, 409, response.content)
            self.source.refresh_from_db()
            for field, value in extra.items(): self.assertEqual(getattr(self.source, field), value)
            self.assertIsNone(Assessment.objects.get().selection_confirmed_at)

    def test_compatible_cohort_and_group_are_preserved_without_touching_dates(self):
        with connection.cursor() as cursor:
            cursor.execute('INSERT INTO curriculum.cohorts (cohort_id, cohort_name, programme_id, programme_name) VALUES (%s,%s,%s,%s)', ["cohort", "Existing cohort", "CAT-MM_L6", CATALOGUE_NAMES["MM_L6"]])
            cursor.execute('INSERT INTO curriculum.groups VALUES (%s,%s,%s,%s,%s,%s)', ["group", "Existing group", "cohort", "Existing cohort", "CAT-MM_L6", CATALOGUE_NAMES["MM_L6"]])
        EnrolmentUser.all_learners.filter(pk=self.source.pk).update(cohort="Existing cohort", group="Existing group")
        LearnerProfile.objects.filter(pk=self.profile.pk).update(cohort="Existing cohort", cohort_id="cohort", group_name="Existing group", group_id="group")
        self.submit()
        response = self.request({"revision": 1, "action": "continue"})
        self.assertEqual(response.status_code, 200, response.content)
        self.profile.refresh_from_db(); self.source.refresh_from_db()
        self.assertEqual(self.profile.group_id, "group"); self.assertEqual(self.source.group, "Existing group")
        self.assertEqual(self.profile.start_date, date(2027, 1, 2))

    def test_reconciliation_defaults_to_dry_run_then_applies_only_safe_missing_assignments(self):
        self.submit()
        row = Assessment.objects.get(); row.selection_confirmed_at = row.confirmed_at; row.save()
        out = io.StringIO(); call_command("reconcile_suitability_programmes", stdout=out)
        self.assertIn("WOULD_APPLY=1", out.getvalue())
        self.source.refresh_from_db(); self.assertEqual(self.source.programme, "")
        call_command("reconcile_suitability_programmes", apply=True, stdout=io.StringIO())
        self.source.refresh_from_db(); self.assertEqual(self.source.programme, CATALOGUE_NAMES["MM_L6"])
        out = io.StringIO(); call_command("reconcile_suitability_programmes", apply=True, stdout=out)
        self.assertIn("ALREADY_SYNCED=1", out.getvalue())
        # An authorised later change must survive confirmation retries and reconciliation.
        EnrolmentUser.all_learners.filter(pk=self.source.pk).update(programme="Staff changed programme")
        self.assertEqual(self.request({"revision": 1, "action": "continue"}).status_code, 200)
        out = io.StringIO(); call_command("reconcile_suitability_programmes", apply=True, stdout=out)
        self.source.refresh_from_db(); self.assertEqual(self.source.programme, "Staff changed programme")

    def test_historical_different_staff_assignment_is_reported_not_overwritten(self):
        self.submit()
        row = Assessment.objects.get(); row.selection_confirmed_at = row.confirmed_at; row.save()
        EnrolmentUser.all_learners.filter(pk=self.source.pk).update(programme="Staff assigned programme")
        out = io.StringIO(); call_command("reconcile_suitability_programmes", apply=True, stdout=out)
        self.assertIn("REVIEW=1", out.getvalue())
        self.source.refresh_from_db(); self.assertEqual(self.source.programme, "Staff assigned programme")

    def test_another_learner_and_a_forged_mapping_cannot_update_assignment(self):
        self.submit()
        self.assertEqual(self.request({"revision": 1, "action": "continue"}, owner=999).status_code, 404)
        self.assertEqual(self.request({"revision": 1, "action": "select", "selectedProgramme": "PCP_L6", "alternativeSelectionReason": "Cross pathway"}).status_code, 400)
        self.source.refresh_from_db(); self.assertEqual(self.source.programme, "")

    def test_zero_evidence_does_not_assign_a_programme_or_create_a_profile(self):
        with connection.cursor() as cursor:
            cursor.execute('DELETE FROM "Learner"."learners"')
        response = self.request({"revision": 0, "configurationVersion": 3, "pathway": "marketing", "action": "submit",
                                 "responses": {qid: [] for qid in answers(self.config)}})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.request({"revision": 1, "action": "continue"}).status_code, 200)
        self.source.refresh_from_db(); self.assertEqual(self.source.programme, "")
        self.assertEqual(LearnerProfile.objects.count(), 0)

    def test_explicit_wrong_catalogue_reference_is_rejected(self):
        self.config["programmes"][1]["catalogueId"] = "CAT-PCP_L6"
        self.submit()
        self.assertEqual(self.request({"revision": 1, "action": "continue"}).status_code, 409)

    def test_legacy_profile_link_is_resolved_without_creating_another_account(self):
        LearnerProfile.objects.filter(pk=self.profile.pk).update(enrolment_id=None)
        self.submit()
        self.assertEqual(self.request({"revision": 1, "action": "continue"}).status_code, 200)
        self.profile.refresh_from_db(); self.assertEqual(self.profile.enrolment_id, self.source.pk)
        self.assertEqual(LearnerProfile.objects.count(), 1)

    def test_inactive_programme_and_mismatched_database_are_not_silently_accepted(self):
        self.submit()
        with connection.cursor() as cursor:
            cursor.execute('UPDATE curriculum.programmes SET is_active = false WHERE programme_id = %s', ["CAT-MM_L6"])
        self.assertEqual(self.request({"revision": 1, "action": "continue"}).status_code, 409)
        with connection.cursor() as cursor:
            cursor.execute('UPDATE curriculum.programmes SET is_active = true WHERE programme_id = %s', ["CAT-MM_L6"])
        with patch.dict(connections['enrolment'].settings_dict, {"HOST": "different-database.example.test"}), \
             patch("programme_suitability.programme_assignment.router.db_for_write", side_effect=lambda model, **kw: "enrolment" if model is EnrolmentUser else "default"):
            response = self.request({"revision": 1, "action": "continue"})
        self.assertEqual(response.status_code, 409, response.content)
        self.assertIsNone(Assessment.objects.get().selection_confirmed_at)

    def test_active_profile_is_not_silently_moved_even_if_source_status_is_stale(self):
        LearnerProfile.objects.filter(pk=self.profile.pk).update(programme="Staff approved programme", lifecycle_status="active")
        for current_source_name in ("", CATALOGUE_NAMES["MM_L6"]):
            Assessment.objects.all().delete()
            EnrolmentUser.all_learners.filter(pk=self.source.pk).update(programme=current_source_name)
            self.submit()
            response = self.request({"revision": 1, "action": "continue"})
            self.assertEqual(response.status_code, 409, response.content)
            self.profile.refresh_from_db(); self.assertEqual(self.profile.programme, "Staff approved programme")
