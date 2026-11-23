from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from learner_api.mappers import ValidationError, write_fields, write_organisation_fields
from learner_api.views import _check_learner_reference

from .employment_status_history import (
    append_record,
    apply_effective,
    effective_record,
    strip_staff_fields,
    staff_record_for_write,
    validate_staff_record,
)


class EmploymentStatusHistoryTests(SimpleTestCase):
    def test_learner_cannot_round_trip_staff_owned_values(self):
        self.assertEqual(
            strip_staff_fields({
                "employmentStatus": "In paid employment",
                "dateStatusApplies": "2026-08-01",
                "agreementIdentifier": "ABC123",
            }),
            {"employmentStatus": "In paid employment"},
        )

    def test_effective_date_selects_latest_non_future_record(self):
        history = [
            {"dateStatusApplies": "2026-01-01", "agreementIdentifier": "OLD"},
            {"dateStatusApplies": "2026-09-01", "agreementIdentifier": "NOW"},
            {"dateStatusApplies": "2027-01-01", "agreementIdentifier": "FUTURE"},
        ]
        self.assertEqual(effective_record(history, date(2026, 11, 22))["agreementIdentifier"], "NOW")
        self.assertEqual(apply_effective({}, history, date(2025, 12, 31)), {})

    def test_history_is_idempotent_and_same_date_cannot_be_rewritten(self):
        record = {"dateStatusApplies": "2026-09-01", "agreementIdentifier": "ABC"}
        self.assertEqual(append_record([record], dict(record)), [record])
        with self.assertRaisesMessage(ValueError, "preserve history"):
            append_record([record], {**record, "agreementIdentifier": "XYZ"})

    @patch("enrolment_api.employment_status_history.validate_staff_record")
    def test_unchanged_record_does_not_re_resolve_current_employer(self, validate):
        record = {
            "employmentStatus": "In paid employment",
            "dateStatusApplies": "2026-09-01",
            "agreementIdentifier": "ABC",
            "organisationId": 7,
            "employerIdentifier": "123456789",
        }
        self.assertIs(staff_record_for_write(record, SimpleNamespace(), [record]), record)
        validate.assert_not_called()

    @patch("enrolment_api.employment_status_history.organisation_for")
    def test_staff_record_snapshots_valid_organisation_identifier(self, organisation_for):
        organisation_for.return_value = SimpleNamespace(pk=7, edrs_ern_number="123456789", apprenticeship_agreement_id="ORG123")
        record = validate_staff_record({
            "employmentStatus": "In paid employment",
            "dateStatusApplies": "2026-09-01",
            "agreementIdentifier": "AB12",
        }, SimpleNamespace())
        self.assertEqual(record["organisationId"], 7)
        self.assertEqual(record["employerIdentifier"], "123456789")

    @patch("enrolment_api.employment_status_history.organisation_for")
    def test_employed_record_can_use_organisation_agreement_default(self, organisation_for):
        organisation_for.return_value = SimpleNamespace(
            pk=7, edrs_ern_number="123456789", apprenticeship_agreement_id="ORG123"
        )
        record = validate_staff_record({
            "employmentStatus": "In paid employment",
            "dateStatusApplies": "2026-09-01",
            "agreementIdentifier": "",
        }, SimpleNamespace())
        self.assertEqual(record["agreementIdentifier"], "ORG123")

    @patch("enrolment_api.employment_status_history.organisation_for")
    def test_invalid_values_are_rejected(self, organisation_for):
        organisation_for.return_value = SimpleNamespace(pk=7, edrs_ern_number="invalid", apprenticeship_agreement_id="AB12")
        with self.assertRaisesMessage(ValueError, "exactly 9 digits"):
            validate_staff_record({"dateStatusApplies": "2026-09-01", "agreementIdentifier": "AB12"}, SimpleNamespace())
        organisation_for.return_value = None
        with self.assertRaisesMessage(ValueError, "1-7 alphanumeric"):
            validate_staff_record({"dateStatusApplies": "2026-09-01", "agreementIdentifier": "TOO-LONG"}, SimpleNamespace())


class IlrIdentifierValidationTests(SimpleTestCase):
    def test_learner_reference_rules(self):
        self.assertEqual(write_fields({"learnerReferenceNumber": "ABC 123"})["reference_number"], "ABC 123")
        self.assertNotIn("reference_number", write_fields({}))
        for invalid in ("", "ABC-123", "1234567890123"):
            if invalid == "":
                self.assertEqual(write_fields({"learnerReferenceNumber": invalid})["reference_number"], "")
            else:
                with self.assertRaises(ValidationError):
                    write_fields({"learnerReferenceNumber": invalid})

    @patch("learner_api.views.EnrolmentUser.all_learners")
    def test_learner_reference_is_unique_case_insensitively_and_stable_on_update(self, manager):
        matches = manager.using.return_value.filter.return_value
        matches.exists.return_value = True
        with self.assertRaisesMessage(ValidationError, "already assigned"):
            _check_learner_reference({"reference_number": "ABC123"})
        existing = SimpleNamespace(pk=42)
        matches.exclude.return_value.exists.return_value = False
        _check_learner_reference({"reference_number": "ABC123"}, existing)
        matches.exclude.assert_called_once_with(pk=42)

    def test_organisation_official_identifier_rules(self):
        self.assertEqual(write_organisation_fields({"edrsErnNumber": "999999999"})["edrs_ern_number"], "999999999")
        self.assertEqual(write_organisation_fields({"apprenticeshipAgreementId": "A12B"})["apprenticeship_agreement_id"], "A12B")
        with self.assertRaises(ValidationError):
            write_organisation_fields({"edrsErnNumber": "123"})
        with self.assertRaises(ValidationError):
            write_organisation_fields({"apprenticeshipAgreementId": "ABC-1"})
