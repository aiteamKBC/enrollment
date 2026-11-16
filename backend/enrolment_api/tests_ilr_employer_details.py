"""Extended ILR Employer Details prefill: organisation address, employer as line manager.

SimpleTestCase with the ORM mocked, so no query ever reaches a database.
"""
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, SimpleTestCase

from enrolment_api import ilr_employer_details as view
from enrolment_api.document_templates.learner_sources import employer_postcode
from learner_api.apprenticeship_agreement import _employer_address


def _learner(employer_id=7, organization="", employer=""):
    return SimpleNamespace(employer_id=employer_id, organisation_id=None, organization=organization, employer=employer)


def _employer(group_ids=(3,)):
    return SimpleNamespace(
        full_name="Pat Manager", email="pat@example.com", mobile="07123 456789",
        employer_group_ids=list(group_ids),
    )


ORG = SimpleNamespace(name="Example Ltd", post_code="CT1 1AA", address_1="1 Office Park", address_2="Unit 4", city_town="Canterbury")


class EmployerDetailsTests(SimpleTestCase):
    def test_direct_organisation_link_works_without_manager(self):
        learner = _learner(employer_id=None)
        learner.organisation_id = 3
        details, employers, organisations = self._resolve(learner, organisation=ORG)
        organisations.objects.filter.assert_called_once_with(pk=3)
        employers.objects.filter.assert_not_called()
        self.assertEqual(details["organisationName"], "Example Ltd")
        self.assertEqual(details["postcode"], "CT1 1AA")

    def _resolve(self, learner, employer=None, organisation=None):
        with patch.object(view, "Employer") as employers, patch.object(view, "Organisation") as organisations:
            employers.objects.filter.return_value.first.return_value = employer
            organisations.objects.filter.return_value.first.return_value = organisation
            return view.employer_details_for(learner), employers, organisations

    def test_organisation_address_and_employer_as_line_manager(self):
        details, _, organisations = self._resolve(_learner(), _employer(), ORG)

        organisations.objects.filter.assert_called_once_with(pk=3)
        self.assertEqual(details, {
            "organisationName": "Example Ltd", "postcode": "CT1 1AA", "address": "1 Office Park, Unit 4",
            "city": "Canterbury", "lineManagerName": "Pat Manager", "lineManagerEmail": "pat@example.com",
            "lineManagerPhone": "07123 456789",
        })

    def test_without_a_linked_organisation_only_the_learners_own_name_text_is_offered(self):
        details, _, _ = self._resolve(_learner(organization="Typed Org Ltd"), _employer(group_ids=()))

        self.assertEqual(details["organisationName"], "Typed Org Ltd")
        self.assertEqual((details["postcode"], details["address"], details["city"]), ("", "", ""))
        self.assertEqual(details["lineManagerName"], "Pat Manager")

    def test_several_organisations_are_not_guessed_between(self):
        details, _, organisations = self._resolve(_learner(employer="Employer Text"), _employer(group_ids=(3, 4)), ORG)

        organisations.objects.filter.assert_not_called()
        self.assertEqual(details["organisationName"], "Employer Text")
        self.assertEqual(details["address"], "")

    def test_a_learner_with_no_employer_gets_blanks(self):
        details, employers, _ = self._resolve(_learner(employer_id=None))

        employers.objects.filter.assert_not_called()
        self.assertEqual(set(details.values()), {""})

    def test_documents_use_the_organisation_not_the_manager_address(self):
        learner = _learner()
        manager = _employer()
        manager.address_1 = "Manager's personal address"
        manager.post_code = "PRIVATE"
        organisation = SimpleNamespace(**vars(ORG), county="Kent")
        with patch.object(view, "Employer") as employers, patch.object(view, "Organisation") as organisations:
            employers.objects.filter.return_value.first.return_value = manager
            organisations.objects.filter.return_value.first.return_value = organisation
            self.assertEqual(_employer_address(learner), "1 Office Park\nUnit 4\nCanterbury\nKent\nCT1 1AA")
            self.assertEqual(employer_postcode(learner, {"postcode": "OLD"}), "CT1 1AA")
            organisation.post_code = ""
            self.assertEqual(employer_postcode(learner, {"postcode": "OLD"}), "")

    def test_documents_do_not_guess_between_multiple_organisations(self):
        learner = _learner()
        learner.employer_address = "Recorded workplace address"
        manager = _employer(group_ids=(3, 4))
        manager.address_1 = "Manager's personal address"
        manager.post_code = "PRIVATE"
        with patch.object(view, "Employer") as employers, patch.object(view, "Organisation") as organisations:
            employers.objects.filter.return_value.first.return_value = manager
            self.assertEqual(_employer_address(learner), "Recorded workplace address")
            self.assertEqual(employer_postcode(learner, {"postcode": "SAVED"}), "SAVED")
            organisations.objects.filter.assert_not_called()

    def test_another_learner_gets_not_found(self):
        request = RequestFactory().get("/enrolment_api/extended-ilr/apprenticeship/41/employer-details/")
        request.login_account = SimpleNamespace(role="learner", subject_id=99, is_active=True)
        learner_model = MagicMock()
        with patch.dict(os.environ, {"ENROLMENT_API_REQUIRE_AUTH": "1"}), \
                patch.dict(view.KINDS, {"apprenticeship": learner_model}):
            response = view.ilr_employer_details(request, kind="apprenticeship", learner_id=41)

        self.assertEqual(response.status_code, 404)
        learner_model.objects.filter.assert_not_called()
