"""Focused regression tests for independent learner-team assignments."""
import json
from contextlib import nullcontext
from datetime import date, time
from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from django.test import RequestFactory, SimpleTestCase

from . import coach_assignment, learner_dates, mappers, views
from .mappers import ValidationError, _employment_details, _learner_team, _verified_employer_representative


class LearnerTeamMappingTests(SimpleTestCase):
    def test_case_owner_and_skills_coach_are_returned_independently(self):
        source = SimpleNamespace(
            case_owner="Administrative Owner",
            coach_name="Training Coach",
            coach_email="coach@example.test",
        )
        with patch.object(
            coach_assignment,
            "case_owner_coach",
            return_value={"coach_name": "Administrative Owner", "coach_email": "owner@example.test"},
        ):
            team = _learner_team(source)

        self.assertEqual(team["caseOwner"], {"name": "Administrative Owner", "email": "owner@example.test"})
        self.assertEqual(team["skillsCoach"], {"name": "Training Coach", "email": "coach@example.test"})

    def test_missing_assignments_have_explicit_empty_values(self):
        source = SimpleNamespace(case_owner="", coach_name="", coach_email="")
        self.assertEqual(_learner_team(source), {"caseOwner": None, "skillsCoach": None})

    def test_legacy_profile_coach_remains_readable_without_case_owner_fallback(self):
        source = SimpleNamespace(pk=7, email="learner@example.test", case_owner="Owner", coach_name=None, coach_email=None)
        profile = SimpleNamespace(coach_name="Legacy Coach", coach_email="legacy@example.test")
        with patch("learner_api.mappers.learner_profile_for_source", create=True), \
             patch("learner_api.identity.learner_profile_for_source", return_value=profile), \
             patch("learner_api.coach_assignment.case_owner_coach", return_value={"coach_email": "owner@example.test"}):
            team = _learner_team(source)
        self.assertEqual(team["skillsCoach"]["name"], "Legacy Coach")


class EmploymentDetailsMappingTests(SimpleTestCase):
    def _learner(self, **overrides):
        values = {
            "pk": 27,
            "organisation_id": None,
            "employer_id": None,
            "organization": "",
            "employer": "",
            "employer_address": "",
            "line_manager": "",
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_linked_organisation_verified_representative_and_assigned_manager_are_mapped(self):
        learner = self._learner(organisation_id=41, employer_id=9)
        organisation = SimpleNamespace(
            name="Bright Ideas Ltd",
            address_1="71 Queen Street",
            address_2="Floor 2",
            city_town="London",
            county="Greater London",
            post_code="EC2A 4BX",
            country="United Kingdom",
        )
        manager = SimpleNamespace(
            full_name="Kerry Katsapaos",
            email="kerry@example.test",
            mobile="020 7946 0123",
            employer_group_ids=[41],
        )
        with patch("learner_api.models.Organisation.objects.filter") as organisations, \
             patch("learner_api.models.Employer.objects.filter") as employers, \
             patch.object(mappers, "_extended_employment", return_value={}), \
             patch.object(mappers, "_verified_employer_representative", return_value={
                 "name": "John Smith", "email": "john@example.test",
             }):
            organisations.return_value.first.return_value = organisation
            employers.return_value.first.return_value = manager
            details = _employment_details(learner)

        self.assertEqual(details["organisation"], {
            "name": "Bright Ideas Ltd",
            "address": "71 Queen Street, Floor 2, London, Greater London, EC2A 4BX, United Kingdom",
        })
        self.assertEqual(details["employerRepresentative"], {
            "name": "John Smith", "email": "john@example.test",
        })
        self.assertEqual(details["lineManager"], {
            "name": "Kerry Katsapaos", "email": "kerry@example.test", "phone": "020 7946 0123",
        })

    def test_manager_from_another_organisation_uses_the_saved_extended_ilr_person(self):
        learner = self._learner(organisation_id=41, employer_id=9)
        organisation = SimpleNamespace(
            name="Bright Ideas Ltd", address_1="71 Queen Street", address_2="",
            city_town="London", county="", post_code="EC2A 4BX", country="",
        )
        unrelated_manager = SimpleNamespace(
            full_name="Wrong Person", email="wrong@example.test", mobile="111", employer_group_ids=[99],
        )
        extended = {
            "lineManagerName": "Saved Manager",
            "lineManagerEmail": "saved.manager@example.test",
            "lineManagerPhone": "020 7000 0000",
        }
        with patch("learner_api.models.Organisation.objects.filter") as organisations, \
             patch("learner_api.models.Employer.objects.filter") as employers, \
             patch.object(mappers, "_extended_employment", return_value=extended), \
             patch.object(mappers, "_verified_employer_representative", return_value=None):
            organisations.return_value.first.return_value = organisation
            employers.return_value.first.return_value = unrelated_manager
            details = _employment_details(learner)

        self.assertEqual(details["lineManager"], {
            "name": "Saved Manager",
            "email": "saved.manager@example.test",
            "phone": "020 7000 0000",
        })

    def test_extended_ilr_fallback_keeps_organisation_and_manager_fields_coherent(self):
        learner = self._learner()
        extended = {
            "organisationName": "Saved Company",
            "address": "10 Saved Road",
            "city": "York",
            "postcode": "YO1 1AA",
            "lineManagerName": "Saved Manager",
            "lineManagerEmail": "saved@example.test",
            "lineManagerPhone": "01904 000000",
        }
        with patch.object(mappers, "_extended_employment", return_value=extended), \
             patch.object(mappers, "_verified_employer_representative", return_value=None):
            details = _employment_details(learner)

        self.assertEqual(details["organisation"], {
            "name": "Saved Company", "address": "10 Saved Road, York, YO1 1AA",
        })
        self.assertEqual(details["lineManager"], {
            "name": "Saved Manager", "email": "saved@example.test", "phone": "01904 000000",
        })

    def test_legacy_employer_link_resolves_an_organisation_only_when_unambiguous(self):
        learner = self._learner(employer_id=9)
        organisation = SimpleNamespace(
            name="Legacy Linked Company", address_1="8 Link Road", address_2="",
            city_town="Leeds", county="", post_code="LS1 1AA", country="",
        )
        manager = SimpleNamespace(
            full_name="Assigned Manager", email="assigned@example.test", mobile="0113 000 0000",
            employer_group_ids=[51],
        )
        with patch("learner_api.models.Organisation.objects.filter") as organisations, \
             patch("learner_api.models.Employer.objects.filter") as employers, \
             patch.object(mappers, "_extended_employment", return_value={}), \
             patch.object(mappers, "_verified_employer_representative", return_value=None):
            organisations.return_value.first.return_value = organisation
            employers.return_value.first.return_value = manager
            details = _employment_details(learner)

        organisations.assert_called_once_with(pk=51)
        self.assertEqual(details["organisation"], {
            "name": "Legacy Linked Company", "address": "8 Link Road, Leeds, LS1 1AA",
        })

    def test_legacy_employer_with_multiple_organisations_does_not_guess_the_company(self):
        learner = self._learner(employer_id=9)
        manager = SimpleNamespace(
            full_name="Assigned Manager", email="assigned@example.test", mobile="0113 000 0000",
            employer_group_ids=[51, 52],
        )
        with patch("learner_api.models.Organisation.objects.filter") as organisations, \
             patch("learner_api.models.Employer.objects.filter") as employers, \
             patch.object(mappers, "_extended_employment", return_value={}), \
             patch.object(mappers, "_verified_employer_representative", return_value=None):
            employers.return_value.first.return_value = manager
            details = _employment_details(learner)

        organisations.assert_not_called()
        self.assertIsNone(details["organisation"])

    def test_legacy_fallback_and_missing_values_do_not_invent_contact_details(self):
        legacy = self._learner(
            organization="Legacy Company", employer_address="Legacy address", line_manager="Legacy Manager",
        )
        with patch.object(mappers, "_extended_employment", return_value={}), \
             patch.object(mappers, "_verified_employer_representative", return_value=None):
            details = _employment_details(legacy)
            empty = _employment_details(self._learner())

        self.assertEqual(details["organisation"], {"name": "Legacy Company", "address": "Legacy address"})
        self.assertEqual(details["lineManager"], {"name": "Legacy Manager", "email": "", "phone": ""})
        self.assertIsNone(details["employerRepresentative"])
        self.assertEqual(empty, {"organisation": None, "employerRepresentative": None, "lineManager": None})

    def test_representative_is_read_only_from_the_exact_verified_application(self):
        learner = self._learner()
        application = {
            "representative_name": "Verified Representative",
            "representative_email": "verified@example.test",
        }
        with patch("enrolment_api.eligibility_link.verified_application", return_value=application) as verified:
            representative = _verified_employer_representative(learner)

        verified.assert_called_once_with(27)
        self.assertEqual(representative, {
            "name": "Verified Representative", "email": "verified@example.test",
        })


class CoachAssignmentValidationTests(SimpleTestCase):
    def _staff_query(self, staff):
        queryset = MagicMock()
        queryset.filter.return_value = queryset
        queryset.only.return_value = queryset
        queryset.__getitem__.return_value = staff
        return queryset

    def test_only_staff_with_actual_coach_access_can_be_assigned(self):
        allowed = SimpleNamespace(
            username="Skills Coach", email="coach@example.test", access="enrolment", access_extra="coach"
        )
        with patch.object(coach_assignment.StaffUser.objects, "all", return_value=self._staff_query([allowed])):
            result = coach_assignment.resolve_coach_assignment("Skills Coach", "coach@example.test")
        self.assertEqual(result, {"coach_name": "Skills Coach", "coach_email": "coach@example.test"})

    def test_job_title_without_coach_access_is_rejected(self):
        denied = SimpleNamespace(
            username="Case Owner", email="owner@example.test", access="enrolment", access_extra=""
        )
        with patch.object(coach_assignment.StaffUser.objects, "all", return_value=self._staff_query([denied])):
            with self.assertRaisesRegex(ValidationError, "does not have Coach access"):
                coach_assignment.resolve_coach_assignment("Case Owner", "owner@example.test")

    def test_reading_a_saved_coach_never_falls_back_to_case_owner(self):
        source = SimpleNamespace(case_owner="Owner", coach_name="Coach", coach_email="coach@example.test")
        self.assertEqual(
            coach_assignment.source_coach(source),
            {"coach_name": "Coach", "coach_email": "coach@example.test"},
        )

    def test_active_learner_caseload_still_uses_authoritative_coach_email(self):
        from login.permissions import coach_owns_learner

        account = SimpleNamespace(role="staff", subject_type="staff", subject_id=4)
        staff = SimpleNamespace(email="coach@example.test", access="coach", access_extra="")
        staff_query = MagicMock()
        staff_query.only.return_value.first.return_value = staff
        profile_query = MagicMock()
        profile_query.filter.return_value.exists.return_value = True
        with patch("learner_api.models.StaffUser.objects.filter", return_value=staff_query), \
             patch("learner_api.models.LearnerProfile.objects.annotate", return_value=profile_query):
            self.assertTrue(coach_owns_learner(account, 27))
        profile_query.filter.assert_called_once()
        self.assertEqual(profile_query.filter.call_args.kwargs["enrolment_id"], 27)


class IndependentWriteTests(SimpleTestCase):
    def test_updating_case_owner_does_not_change_coach_or_trigger_handover(self):
        user = SimpleNamespace(
            case_owner="New Owner", coach_name="Existing Coach", coach_email="coach@example.test", save=Mock()
        )
        with patch("coach_api.review_handover.save_assignment") as handover:
            learner_dates.save_enrolment_fields(user, {"case_owner": "New Owner"})
        user.save.assert_called_once_with(update_fields=["case_owner"])
        handover.assert_not_called()
        self.assertEqual(user.coach_name, "Existing Coach")

    def test_updating_skills_coach_mirrors_profile_and_triggers_handover(self):
        user = SimpleNamespace(coach_name="New Coach", coach_email="new@example.test", save=Mock())
        profile = SimpleNamespace(coach_name="Old Coach", coach_email="old@example.test", save=Mock())
        result = {"transferred": ["review:1"], "blocked": []}
        with patch.object(learner_dates, "learner_profile_for_source", side_effect=[profile, profile]), \
             patch("coach_api.review_handover.save_assignment", return_value=(False, result)) as handover, \
             patch.object(learner_dates.transaction, "atomic", return_value=nullcontext()):
            response = learner_dates.save_enrolment_fields(
                user, {"coach_name": "New Coach", "coach_email": "new@example.test"}
            )
        handover.assert_called_once()
        self.assertEqual(profile.coach_name, "New Coach")
        self.assertEqual(profile.coach_email, "new@example.test")
        self.assertEqual(response, {"reviewHandover": result})

    def test_saving_the_same_skills_coach_does_not_trigger_handover(self):
        user = SimpleNamespace(coach_name="Same Coach", coach_email="same@example.test", save=Mock())
        profile = SimpleNamespace(coach_name="Same Coach", coach_email="same@example.test", save=Mock())
        with patch.object(learner_dates, "learner_profile_for_source", side_effect=[profile, profile]), \
             patch("coach_api.review_handover.save_assignment") as handover, \
             patch.object(learner_dates.transaction, "atomic", return_value=nullcontext()):
            learner_dates.save_enrolment_fields(
                user, {"coach_name": "Same Coach", "coach_email": "same@example.test"}
            )
        handover.assert_not_called()

    def test_coach_endpoint_never_overwrites_case_owner(self):
        source = SimpleNamespace(
            pk=3, case_owner="Administrative Owner", coach_name="Old Coach", coach_email="old@example.test"
        )
        request = RequestFactory().patch(
            "/learner_api/learners/3/coach/",
            json.dumps({"coachName": "New Coach", "coachEmail": "new@example.test"}),
            content_type="application/json",
        )
        request.login_account = SimpleNamespace(role="staff")
        manager = MagicMock()
        manager.filter.return_value.first.return_value = source
        fake_model = SimpleNamespace(all_learners=manager)
        with patch.object(views, "EnrolmentUser", fake_model), \
             patch.object(views, "learner_profile_for_source", return_value=SimpleNamespace()), \
             patch.object(views, "resolve_coach_assignment", return_value={
                 "coach_name": "New Coach", "coach_email": "new@example.test"
             }), \
             patch.object(views, "save_enrolment_fields", return_value=None) as save:
            response = unwrap(views.learner_coach)(request, 3)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(source.case_owner, "Administrative Owner")
        self.assertNotIn("case_owner", save.call_args.args[1])


class LearnerCreationAssignmentTests(SimpleTestCase):
    def test_creation_keeps_case_owner_and_skills_coach_independent_and_books_owner(self):
        manager = MagicMock()
        manager.annotate.return_value.filter.return_value.only.return_value.first.return_value = None
        created = SimpleNamespace(pk=11, id=11)
        manager.create.return_value = created
        fake_model = SimpleNamespace(all_learners=manager)
        request = SimpleNamespace()
        with patch.object(views, "EnrolmentUser", fake_model), \
             patch.object(views, "cohort_delivery_window", return_value=(None, None, None)), \
             patch.object(views, "case_owner_coach", return_value={
                 "coach_name": "Case Owner", "coach_email": "owner@example.test"
             }), \
             patch.object(views, "advance_learner"), \
             patch.object(views, "to_list_row", return_value={"id": "11"}), \
             patch.object(views, "book_first_session", return_value={"booked": True}) as booking, \
             patch.object(views, "_send_platform_invitation", return_value={}):
            views._create_enrolment_user(
                request,
                {"username": "Learner", "email": "learner@example.test", "case_owner": "Case Owner"},
                session_date=date(2026, 11, 18), session_time=time(10, 0),
            )
        saved = manager.create.call_args.kwargs
        self.assertEqual(saved["case_owner"], "Case Owner")
        self.assertNotIn("coach_name", saved)
        self.assertNotIn("coach_email", saved)
        booking.assert_called_once()
        self.assertEqual(booking.call_args.kwargs["owner_email"], "owner@example.test")
