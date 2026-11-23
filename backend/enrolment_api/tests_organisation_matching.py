"""Company matching reuses exact identity and flags possible matches."""
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from enrolment_api import organisation_matching as matching


ROW = {
    "employer_name": " Example Ltd ", "employer_postcode": "CT1 1AA",
    "employer_address_line1": "1 Office Park", "employer_address_line2": "Unit 4",
    "employer_town_city": "Canterbury", "employer_county": "Kent",
    "representative_name": "A Contact", "representative_email": "contact@example.test",
}


class OrganisationMatchingTests(SimpleTestCase):
    def test_reuses_exact_name_and_postcode_even_if_address_differs(self):
        org = SimpleNamespace(pk=8, name="Example Ltd", post_code="CT11AA",
            address_1="Different street", address_2="Unit 4", city_town="Canterbury",
            county="Kent", contact_name="Existing Contact", contact_email="existing@example.test")
        with patch.object(matching, "connections"), patch.object(matching, "Organisation") as model:
            model.objects.filter.return_value = [org]
            self.assertIs(matching.resolve_application_organisation(ROW), org)
            model.objects.create.assert_not_called()
            model.objects.filter.assert_called_once_with(merged_into_organisation_id__isnull=True)

    def test_similar_company_is_created_and_flagged_for_staff(self):
        org = SimpleNamespace(pk=8, name="Example Limited", post_code="CT1 1AB")
        with patch.object(matching, "connections"), patch.object(matching, "Organisation") as model:
            model.objects.filter.return_value = [org]
            matching.resolve_application_organisation(ROW)
            self.assertEqual(model.objects.create.call_args.kwargs["potential_match_ids"], [8])

    def test_creates_company_with_representative_as_contact(self):
        with patch.object(matching, "connections"), patch.object(matching, "Organisation") as model:
            model.objects.filter.return_value = []
            matching.resolve_application_organisation(ROW)
            self.assertEqual(model.objects.create.call_args.kwargs["contact_name"], "A Contact")
            self.assertEqual(model.objects.create.call_args.kwargs["contact_email"], "contact@example.test")
            self.assertEqual(model.objects.create.call_args.kwargs["potential_match_ids"], [])
