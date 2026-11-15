"""Staff learner writes keep company and manager references consistent."""
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from learner_api import views
from learner_api.mappers import ValidationError


class OrganisationLinkTests(SimpleTestCase):
    def test_manager_must_belong_to_selected_company(self):
        fields = {"organisation_id": 3, "employer_id": 8}
        with patch.object(views, "Organisation") as organisations, patch.object(views, "Employer") as employers:
            organisations.objects.filter.return_value.first.return_value = SimpleNamespace(name="Company")
            employers.objects.filter.return_value.first.return_value = SimpleNamespace(employer_group_ids=[4])
            with self.assertRaisesRegex(ValidationError, "not linked"):
                views._check_employer_id(fields)

    def test_company_name_comes_from_selected_profile(self):
        fields = {"organisation_id": 3, "employer_id": None, "organization": "Client supplied"}
        with patch.object(views, "Organisation") as organisations:
            organisations.objects.filter.return_value.first.return_value = SimpleNamespace(name="Authoritative Company")
            views._check_employer_id(fields)
        self.assertEqual(fields["organization"], "Authoritative Company")
        self.assertEqual(fields["employer"], "Authoritative Company")
