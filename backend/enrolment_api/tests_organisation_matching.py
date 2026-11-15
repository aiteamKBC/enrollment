"""Company matching must preserve existing profiles and refuse ambiguous links."""
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
    def test_reuses_only_compatible_organisation_without_replacing_contact(self):
        org = SimpleNamespace(pk=8, name="Example Ltd", post_code="CT11AA",
            address_1="1 Office Park", address_2="Unit 4", city_town="Canterbury",
            county="Kent", contact_name="Existing Contact", contact_email="existing@example.test")
        with patch.object(matching, "connections"), patch.object(matching, "Organisation") as model:
            model.objects.filter.return_value = [org]
            self.assertIs(matching.resolve_application_organisation(ROW), org)
            model.objects.create.assert_not_called()
            model.objects.filter.assert_called_once_with(name__iexact="Example Ltd")

    def test_conflicting_address_requires_review(self):
        org = SimpleNamespace(name="Example Ltd", post_code="CT1 1AA",
            address_1="Different street", address_2="", city_town="Canterbury", county="Kent")
        with patch.object(matching, "connections"), patch.object(matching, "Organisation") as model:
            model.objects.filter.return_value = [org]
            with self.assertRaisesRegex(ValueError, "staff review"):
                matching.resolve_application_organisation(ROW)
            model.objects.create.assert_not_called()

    def test_creates_company_with_representative_as_contact(self):
        with patch.object(matching, "connections"), patch.object(matching, "Organisation") as model:
            model.objects.filter.return_value = []
            matching.resolve_application_organisation(ROW)
            self.assertEqual(model.objects.create.call_args.kwargs["contact_name"], "A Contact")
            self.assertEqual(model.objects.create.call_args.kwargs["contact_email"], "contact@example.test")
