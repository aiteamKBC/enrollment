"""Retired learner fields cannot be written or imported."""

from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from openpyxl import Workbook

from .learner_import import COLUMNS, read_students
from .mappers import write_fields, write_staff_fields
from .models import EnrolmentUser, StaffUser


class RetiredLearnerFieldsTests(SimpleTestCase):
    def test_login_subject_models_do_not_select_retired_columns(self):
        learner_columns = {field.column for field in EnrolmentUser._meta.concrete_fields}
        staff_columns = {field.column for field in StaffUser._meta.concrete_fields}
        self.assertFalse({"Referrer", "Referrer_address", "Referrer_contact", "Learning_provider"} & learner_columns)
        self.assertNotIn("Learning_provider", staff_columns)

    def test_old_payload_fields_are_ignored(self):
        old_fields = {
            "referrer": "Old source",
            "referrerAddress": "Old address",
            "referrerContact": "Old contact",
            "learningProvider": "Another provider",
            "referenceNumber": "OLD-123",
            "extendedBreak": "Legacy value",
        }
        self.assertEqual(write_fields(old_fields), {})
        self.assertEqual(write_staff_fields({"learningProvider": "Another provider"}), {})

    def test_retired_import_headings_are_rejected(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Students"
        sheet.append(["First name", "Surname", "Email", "Case owner", "First session date", "Learning provider", "Referrer", "Referrer address", "Referrer contact", "Reference number", "Extended break"])
        sheet.append(["New", "Learner", "new@example.invalid", "Coach", "2030-01-01", "Another provider", "Old source", "Old address", "Old contact", "OLD-123", "Legacy value"])
        data = BytesIO()
        workbook.save(data)
        upload = SimpleUploadedFile("learners.xlsx", data.getvalue())

        students, errors = read_students(upload)

        self.assertEqual(students, [])
        self.assertEqual(len(errors), 4)
        self.assertTrue(all("Unknown column" in issue["message"] for issue in errors))
        self.assertNotIn(("Learning provider", "learningProvider"), COLUMNS)
        self.assertNotIn(("Referrer", "referrer"), COLUMNS)
        self.assertNotIn(("Reference number", "referenceNumber"), COLUMNS)
        self.assertNotIn(("Extended break", "extendedBreak"), COLUMNS)
