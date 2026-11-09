from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase

from learner_api import account_settings as view
from learner_api import profile_photo


class AccountSettingsValidationTests(SimpleTestCase):
    def test_profile_rejects_protected_identity_fields(self):
        with self.assertRaisesMessage(ValueError, "cannot be changed"):
            view._profile_values({"title": "Ms", "pronouns": "she/her", "fullName": "Changed"})

    def test_contact_accepts_mobile_but_not_email(self):
        self.assertEqual(view._contact_values({"mobileNumber": "+44 7700 900123"}), "+44 7700 900123")
        with self.assertRaisesMessage(ValueError, "cannot be changed"):
            view._contact_values({"emailAddress": "other@example.test"})

    def test_address_formats_uk_postcode_and_keeps_residential_fields(self):
        values = view._address_values({
            "addressLine1": "10 High Street", "addressLine2": "", "addressLine3": "Canterbury",
            "addressLine4": "Kent", "residentialPostcode": "ct11aa", "country": "United Kingdom",
        })
        self.assertEqual(values["current_postcode"], "CT1 1AA")
        self.assertEqual(values["address_line_1"], "10 High Street")


class AccountSettingsOwnershipTests(SimpleTestCase):
    def test_learner_cannot_upload_about_you_photo(self):
        request = RequestFactory().post('/learner_api/profile-photo/apprenticeship/41/')
        account = SimpleNamespace(role='learner', subject_id=41)
        request.login_account = account
        model = SimpleNamespace(all_learners=SimpleNamespace(filter=lambda **_: SimpleNamespace(exists=lambda: True)))
        with patch('login.permissions.authenticate_request', return_value=account), \
                patch.dict(profile_photo.SOURCE_MODELS, {'apprenticeship': model}):
            response = profile_photo.learner_profile_photo(request, kind='apprenticeship', pk=41)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response['Content-Type'], 'application/json')

    def test_owner_cannot_edit_about_you_even_before_ilr_finalisation(self):
        request = RequestFactory().put('/learner_api/account-settings/41/',
                                       data='{"section":"contact","values":{"mobileNumber":"07123456789"}}',
                                       content_type='application/json')
        request._dont_enforce_csrf_checks = True
        account = SimpleNamespace(role='learner', subject_id=41)
        learner = SimpleNamespace(pk=41)
        with patch('login.permissions.authenticate_request', return_value=account), \
                patch.object(view.EnrolmentUser.all_learners, 'filter') as rows:
            rows.return_value.first.return_value = learner
            response = view.learner_account_settings(request, pk=41)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response['Content-Type'], 'application/json')
        rows.return_value.first.assert_called_once()

    def test_a_learner_cannot_read_another_learners_settings(self):
        request = RequestFactory().get("/learner_api/account-settings/41/")
        account = SimpleNamespace(role="learner", subject_id=42)

        def authenticate(req):
            req.login_account = account
            return account

        with patch("login.permissions.authenticate_request", side_effect=authenticate):
            response = view.learner_account_settings(request, pk=41)

        self.assertEqual(response.status_code, 404)

    def test_unexpected_read_failure_is_json_without_a_stack_trace(self):
        request = RequestFactory().get("/learner_api/account-settings/41/")
        account = SimpleNamespace(role="learner", subject_id=41)

        with patch("login.permissions.authenticate_request", return_value=account), \
             patch.object(view.EnrolmentUser.all_learners, "filter", side_effect=RuntimeError("database details")), \
             patch.object(view.logger, "exception"):
            response = view.learner_account_settings(request, pk=41)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertNotIn(b"database details", response.content)
