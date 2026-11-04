"""The ILR step (wizard slug 'ilr-details'): kept apart from the Extended ILR.

Its answers are stored only in enrolment."Wizard_Ilr_Learner_Details" — never in
enrolment."Extended_ILR" — and handed back to the wizard from there.

SimpleTestCase with the database layer mocked: Django refuses any real query
from these tests, so they are safe to run against any configured database.
"""
import json
import os
from contextlib import ExitStack, nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, SimpleTestCase

from enrolment_api import extended_ilr, ilr_learner_record, wizard_steps

ILR_DETAILS = {
    "yearsAtAddress": 3,
    "sinceBirth": False,
    "postcodePriorToEnrolment": "CT1 1AA",
    "niNumber": "AB 12 34 56 C",
    "niApplied": None,
    "legalSex": "Female",
    "pronouns": "she/her",
    "ethnicity": "Irish",
    "longTermDisability": False,
    "highestQualification": "Full level 3",
    "employmentStatus": "In paid employment",
    "employmentStartDate": "2024-01-15",
    "jobTitle": "Marketing Assistant",
    "selfEmployed": False,
    "fullTimeEducation": False,
    "expectedLeavingDate": "",
    "lengthOfUnemployment": "",
    "volunteers": None,
    "stateBenefits": "None",
    "benefitClaimBasis": "",
    "signature": "data:image/png;base64,AAAA",
    "signatureDate": "2026-09-27",
}
PERSONAL = {"firstName": "Test", "lastName": "Learner"}


class SeparationTests(SimpleTestCase):
    def setUp(self):
        boundary = patch('enrolment_api.eligibility_prefill.verified_application', return_value=None)
        boundary.start()
        self.addCleanup(boundary.stop)

    def test_combined_signature_confirmation_round_trips_in_existing_json_without_reinterpreting_old_certification(self):
        personal = {**PERSONAL, "signature": "data:image/png;base64,PAPERWORK", "signatureDate": "2020-04-03",
                    "signatureDeclaration": {"text": "I certify the ILR.\nEnrolment paperwork.",
                                             "confirmedAt": "2026-11-03T10:00:00Z"}}
        draft = {"personalDetails": personal, "ilrDetails": ILR_DETAILS}
        request = RequestFactory().post("/enrolment_api/extended-ilr/apprenticeship/41/",
                                        data=json.dumps({"draft": draft}), content_type="application/json")
        learner_model = MagicMock()
        learner_model.objects.filter.return_value.first.return_value = SimpleNamespace(username="Test Learner")
        row = SimpleNamespace(wizard_draft=None, answers=None)
        def save_row(**kwargs):
            row.wizard_draft = kwargs["defaults"]["wizard_draft"]
            return row, True
        with patch.dict(os.environ, {"ENROLMENT_API_REQUIRE_AUTH": "0"}), \
                patch.dict(extended_ilr.KINDS, {"apprenticeship": learner_model}), \
                patch.object(extended_ilr.transaction, "atomic", lambda **_: nullcontext()), \
                patch.object(extended_ilr.ExtendedIlr.objects, "update_or_create", side_effect=save_row), \
                patch.object(extended_ilr, "project_draft") as project, \
                patch.object(extended_ilr, "project_custom_fields"), \
                patch.object(extended_ilr, "_payload", side_effect=lambda saved: {"draft": saved.wizard_draft}):
            response = extended_ilr.extended_ilr(request, kind="apprenticeship", learner_id=41)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["draft"]["personalDetails"], personal)
        project.assert_called_once_with("apprenticeship", 41, draft)
        self.assertEqual(draft["ilrDetails"]["signature"], ILR_DETAILS["signature"])
        self.assertEqual(draft["ilrDetails"]["signatureDate"], ILR_DETAILS["signatureDate"])

    def test_the_extended_ilr_copy_never_carries_the_ilr_step(self):
        stored = wizard_steps.without_ilr_details({"personalDetails": PERSONAL, "ilrDetails": ILR_DETAILS})

        self.assertEqual(stored, {"personalDetails": PERSONAL})

    def test_saving_keeps_the_ilr_step_out_of_the_extended_ilr_row(self):
        factory = RequestFactory()
        request = factory.post(
            "/enrolment_api/extended-ilr/apprenticeship/41/",
            data=json.dumps({"draft": {"personalDetails": PERSONAL, "ilrDetails": ILR_DETAILS}}),
            content_type="application/json",
        )
        learner_model = MagicMock()
        learner_model.objects.filter.return_value.first.return_value = SimpleNamespace(username="Test Learner")
        row = MagicMock()
        with patch.dict(os.environ, {"ENROLMENT_API_REQUIRE_AUTH": "0"}), \
                patch.dict(extended_ilr.KINDS, {"apprenticeship": learner_model}), \
                patch.object(extended_ilr.transaction, "atomic", lambda **_: nullcontext()), \
                patch.object(extended_ilr.ExtendedIlr.objects, "update_or_create", return_value=(row, True)) as upsert, \
                patch.object(extended_ilr, "project_draft") as project, \
                patch.object(extended_ilr, "_payload", return_value={"ok": True}):
            response = extended_ilr.extended_ilr(request, kind="apprenticeship", learner_id=41)

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(upsert.call_args.kwargs["defaults"]["wizard_draft"], {"personalDetails": PERSONAL})
        # The step still reaches its own table.
        project.assert_called_once_with("apprenticeship", 41, {"personalDetails": PERSONAL, "ilrDetails": ILR_DETAILS})

    def test_reading_hands_the_ilr_step_back_from_its_own_table(self):
        with patch.object(extended_ilr, "_read_extended_ilr_row", return_value={"draft": {"personalDetails": PERSONAL}}), \
                patch.object(extended_ilr, "read_ilr_details", return_value=ILR_DETAILS):
            payload = extended_ilr.read_extended_ilr("apprenticeship", 41, "Test Learner")

        self.assertEqual(payload["draft"], {"personalDetails": PERSONAL, "ilrDetails": ILR_DETAILS})

    def test_reading_without_a_saved_ilr_step_leaves_the_draft_alone(self):
        with patch.object(extended_ilr, "_read_extended_ilr_row", return_value={"draft": {"personalDetails": PERSONAL}}), \
                patch.object(extended_ilr, "read_ilr_details", return_value=None):
            payload = extended_ilr.read_extended_ilr("apprenticeship", 41, "Test Learner")

        self.assertEqual(payload["draft"], {"personalDetails": PERSONAL})


class ProjectionTests(SimpleTestCase):
    def test_old_personal_details_projection_still_returns_signature_and_original_date(self):
        from datetime import date
        original = SimpleNamespace(first_name="Test", last_name="Learner", email="test@example.invalid", phone="123",
                                   address="Historical address", date_of_birth=date(1991, 6, 12), age=35, sex="Female",
                                   signature="data:image/png;base64,PAPERWORK", signature_date=date(2020, 4, 3))
        with ExitStack() as stack:
            for model in (wizard_steps.WizardPersonalDetails, wizard_steps.WizardIlrLearnerDetails, wizard_steps.WizardSkillsRadar,
                          wizard_steps.WizardKsbAssessment, wizard_steps.WizardPlr, wizard_steps.WizardPlrRecord,
                          wizard_steps.WizardCvJob, wizard_steps.WizardPolicyAck):
                query = stack.enter_context(patch.object(model.objects, "filter"))
                query.return_value.first.return_value = original if model is wizard_steps.WizardPersonalDetails else None
            draft = wizard_steps.read_projection("apprenticeship", 41)
        self.assertEqual(draft["personalDetails"]["signature"], original.signature)
        self.assertEqual(draft["personalDetails"]["signatureDate"], "2020-04-03")
        self.assertEqual(draft["personalDetails"]["address"], original.address)

    def test_relocated_paperwork_signature_keeps_its_original_storage_separate_from_certification(self):
        from copy import deepcopy
        personal = {**PERSONAL, "email": "historic@example.invalid", "address": "Historical address",
                    "signature": "data:image/png;base64,PAPERWORK", "signatureDate": "2020-04-03"}
        draft = {"personalDetails": personal, "ilrDetails": ILR_DETAILS}
        before = deepcopy(draft)
        with patch.object(wizard_steps.WizardPersonalDetails.objects, "update_or_create") as pd, \
                patch.object(wizard_steps.WizardIlrLearnerDetails.objects, "update_or_create") as ilr:
            wizard_steps.project_draft("apprenticeship", 41, draft)
        self.assertEqual(draft, before)
        self.assertEqual(pd.call_args.kwargs["defaults"]["signature"], personal["signature"])
        self.assertEqual(str(pd.call_args.kwargs["defaults"]["signature_date"]), "2020-04-03")
        self.assertEqual(pd.call_args.kwargs["defaults"]["address"], "Historical address")
        self.assertEqual(ilr.call_args.kwargs["defaults"]["declaration_signature"], ILR_DETAILS["signature"])
        self.assertEqual(wizard_steps.without_ilr_details(draft), {"personalDetails": personal})

    def test_ilr_only_save_cannot_remove_old_personal_details_or_signatures(self):
        with patch.object(wizard_steps.WizardPersonalDetails.objects, "update_or_create") as personal, \
                patch.object(wizard_steps.WizardIlrLearnerDetails.objects, "update_or_create"):
            wizard_steps.project_draft("apprenticeship", 41, {"ilrDetails": ILR_DETAILS})
        personal.assert_not_called()

    def test_explicit_signature_clear_uses_the_existing_columns_without_wiping_identity(self):
        with patch.object(wizard_steps.WizardPersonalDetails.objects, "update_or_create") as personal:
            wizard_steps.project_draft("apprenticeship", 41, {"personalDetails": {
                **PERSONAL, "email": "historic@example.invalid", "signature": "", "signatureDate": "",
            }})
        defaults = personal.call_args.kwargs["defaults"]
        self.assertIsNone(defaults["signature"])
        self.assertIsNone(defaults["signature_date"])
        self.assertEqual(defaults["first_name"], PERSONAL["firstName"])
        self.assertEqual(defaults["email"], "historic@example.invalid")

    def test_writes_every_answer_to_its_own_table(self):
        with patch.object(wizard_steps.WizardIlrLearnerDetails.objects, "update_or_create") as upsert:
            wizard_steps.project_draft("apprenticeship", 41, {"ilrDetails": ILR_DETAILS})

        kwargs = upsert.call_args.kwargs
        self.assertEqual((kwargs["learner_kind"], kwargs["learner_id"]), ("apprenticeship", 41))
        self.assertEqual(kwargs["defaults"], {
            "years_at_address": 3,
            "at_address_since_birth": False,
            "postcode_prior_to_enrolment": "CT1 1AA",
            "national_insurance_number": "AB 12 34 56 C",
            "ni_number_applied": None,
            "legal_sex": "Female",
            "pronouns": "she/her",
            "ethnicity": "Irish",
            "long_term_disability": False,
            "highest_qualification": "Full level 3",
            "employment_status": "In paid employment",
            "employment_start_date": "2024-01-15",
            "job_title": "Marketing Assistant",
            "self_employed": False,
            "full_time_education": False,
            "expected_leaving_date": None,
            "length_of_unemployment": None,
            "volunteers": None,
            "state_benefits": "None",
            "benefit_claim_basis": None,
            "declaration_signature": "data:image/png;base64,AAAA",
            "declaration_signed_date": "2026-09-27",
        })

    def test_a_save_without_the_ilr_step_does_not_touch_its_table(self):
        with patch.object(wizard_steps.WizardIlrLearnerDetails.objects, "update_or_create") as upsert, \
                patch.object(wizard_steps.WizardPersonalDetails.objects, "update_or_create"):
            wizard_steps.project_draft("apprenticeship", 41, {"personalDetails": PERSONAL})

        upsert.assert_not_called()

    def test_reads_back_in_the_draft_shape_the_wizard_saved(self):
        from datetime import date

        row = SimpleNamespace(
            years_at_address=3, at_address_since_birth=False, postcode_prior_to_enrolment="CT1 1AA",
            national_insurance_number="AB 12 34 56 C", ni_number_applied=None, legal_sex="Female",
            pronouns="she/her", ethnicity="Irish", long_term_disability=False,
            health_condition_codes=None, primary_health_condition_code=None, additional_support_details=None,
            highest_qualification="Full level 3", employment_status="In paid employment",
            employment_start_date=date(2024, 1, 15), job_title="Marketing Assistant", self_employed=False,
            full_time_education=False, expected_leaving_date=None, length_of_unemployment=None, volunteers=None,
            working_hours_per_week=None,
            state_benefits="None", benefit_claim_basis=None,
            declaration_signature="data:image/png;base64,AAAA", declaration_signed_date=date(2026, 9, 27),
        )
        self.assertEqual(wizard_steps.ilr_details_draft(row), {
            **ILR_DETAILS, "healthConditionCodes": [], "primaryHealthConditionCode": "",
            "additionalSupportDetails": "", "workingHoursPerWeek": "",
        })


class LearnerRecordTests(SimpleTestCase):
    def test_returns_the_record_fields_the_step_shows_read_only(self):
        learner = SimpleNamespace(
            username="Test Middle Learner", date_of_birth="1991-06-12", current_postcode="BR8 7EU",
            address_line_1="1 Waterton", address="", address_line_2="", address_line_3="Swanley",
            address_line_4="Kent", phone_number="01002331985", email="learner@example.test",
            national_insurance_number="", legal_sex="",
        )
        self.assertEqual(ilr_learner_record.learner_record(learner), {
            "title": "",
        "country": "",
            "familyName": "Learner",
            "givenNames": "Test Middle",
            "dateOfBirth": "1991-06-12",
            "currentPostcode": "BR8 7EU",
            "addressLine1": "1 Waterton",
            "addressLine2": "",
            "addressLine3": "Swanley",
            "addressLine4": "Kent",
            "telephone": "01002331985",
            "email": "learner@example.test",
            "nationalInsuranceNumber": "",
            "legalSex": "",
        })


class IlrDocumentTests(SimpleTestCase):
    def test_the_ilr_document_fills_its_blanks_from_the_ilr_step(self):
        from learner_api import ilr_document

        details = SimpleNamespace(
            years_at_address=None, at_address_since_birth=True, postcode_prior_to_enrolment="CT1 1AA",
            national_insurance_number="AB 12 34 56 C", legal_sex="Female", ethnicity="Irish",
            long_term_disability=False, highest_qualification="Full level 3",
            employment_status="In paid employment", full_time_education=False,
            employment_start_date=None, job_title="Marketing Assistant", self_employed=True,
        )
        learner = SimpleNamespace(
            pk=41, username="Test Learner", date_of_birth="", address_line_1="", address="", address_line_2="",
            address_line_3="", address_line_4="", phone_number="", email="", current_postcode="",
            national_insurance_number="", legal_sex="",
        )
        with patch.object(ilr_document, "_wizard_personal", return_value=None), \
                patch.object(ilr_document, "_rpl_detail", return_value=None), \
                patch.object(ilr_document, "_wizard_ilr_details", return_value=details):
            out = ilr_document.derive_learner_details(learner)

        self.assertEqual(out["yearsAtAddress"], "Since birth")
        self.assertEqual(out["postcodePriorToEnrolment"], "CT1 1AA")
        self.assertEqual(out["nationalInsuranceNumber"], "AB 12 34 56 C")
        self.assertEqual(out["sex"], "Female")
        self.assertEqual(out["ethnicity"], "Irish")
        self.assertIs(out["longTermDisability"], False)
        self.assertEqual(out["priorAttainment"], "Full level 3")
        self.assertEqual(out["employmentStatus"], "In paid employment")
        self.assertIs(out["fullTimeEducationPrior"], False)
        self.assertEqual(out["jobTitle"], "Marketing Assistant")
        self.assertIs(out["selfEmployed"], True)

    def test_the_learner_record_still_wins_where_it_holds_a_value(self):
        from learner_api import ilr_document

        details = SimpleNamespace(
            years_at_address=2, at_address_since_birth=False, postcode_prior_to_enrolment="",
            national_insurance_number="AB 12 34 56 C", legal_sex="Female", ethnicity="",
            long_term_disability=None, highest_qualification="", employment_status="", full_time_education=None,
        )
        learner = SimpleNamespace(
            pk=41, username="Test Learner", date_of_birth="", address_line_1="", address="", address_line_2="",
            address_line_3="", address_line_4="", phone_number="", email="", current_postcode="",
            national_insurance_number="CD 98 76 54 A", legal_sex="Male",
        )
        with patch.object(ilr_document, "_wizard_personal", return_value=None), \
                patch.object(ilr_document, "_rpl_detail", return_value=None), \
                patch.object(ilr_document, "_wizard_ilr_details", return_value=details):
            out = ilr_document.derive_learner_details(learner)

        self.assertEqual(out["nationalInsuranceNumber"], "CD 98 76 54 A")
        self.assertEqual(out["sex"], "Male")
        self.assertEqual(out["yearsAtAddress"], "2")


class NextOfKinColumnTests(SimpleTestCase):
    """The Extended ILR next of kin's own address lands in its own columns."""

    def test_an_address_of_their_own_is_stored_in_columns(self):
        state = extended_ilr._next_of_kin_state({"nextOfKin": {
            "sameAddressAsLearner": False, "postcode": " CT1 1AA ", "address": "1 High Street",
        }})
        self.assertEqual(state, {"next_of_kin_postcode": "CT1 1AA", "next_of_kin_address": "1 High Street"})

    def test_the_same_address_as_the_learner_clears_the_columns(self):
        state = extended_ilr._next_of_kin_state({"nextOfKin": {
            "sameAddressAsLearner": True, "postcode": "CT1 1AA", "address": "1 High Street",
        }})
        self.assertEqual(state, {"next_of_kin_postcode": None, "next_of_kin_address": None})

    def test_saving_answers_writes_the_columns(self):
        factory = RequestFactory()
        answers = {"nextOfKin": {"sameAddressAsLearner": False, "postcode": "CT1 1AA", "address": "1 High Street"}}
        request = factory.post(
            "/enrolment_api/extended-ilr/apprenticeship/41/",
            data=json.dumps({"answers": answers}),
            content_type="application/json",
        )
        learner_model = MagicMock()
        learner_model.objects.filter.return_value.first.return_value = SimpleNamespace(username="Test Learner")
        with patch.dict(os.environ, {"ENROLMENT_API_REQUIRE_AUTH": "0"}), \
                patch.dict(extended_ilr.KINDS, {"apprenticeship": learner_model}), \
                patch.object(extended_ilr.transaction, "atomic", lambda **_: nullcontext()), \
                patch.object(extended_ilr.ExtendedIlr.objects, "update_or_create", return_value=(MagicMock(), True)) as upsert, \
                patch.object(extended_ilr, "_payload", return_value={"ok": True}):
            response = extended_ilr.extended_ilr(request, kind="apprenticeship", learner_id=41)

        self.assertEqual(response.status_code, 200, response.content)
        defaults = upsert.call_args.kwargs["defaults"]
        self.assertEqual(defaults["next_of_kin_postcode"], "CT1 1AA")
        self.assertEqual(defaults["next_of_kin_address"], "1 High Street")
