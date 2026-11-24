"""Staff role grants on creation; all database writes are mocked."""
import json
from contextlib import nullcontext
from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase

from .views import staff_users


class StaffAccessCreationTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def create(self, role, grants):
        request = self.factory.post(
            "/learner_api/staff-users/",
            data=json.dumps({
                "username": "New colleague",
                "email": "colleague@example.invalid",
                "position": "Admin",
                "accesses": grants,
            }),
            content_type="application/json",
        )
        request.login_account = SimpleNamespace(role=role)
        with patch("learner_api.views.StaffUser.objects.create") as save, \
                patch("learner_api.views.transaction.atomic", return_value=nullcontext()), \
                patch("learner_api.views.assert_available_for_new_profile"), \
                patch("learner_api.views.to_staff_row", return_value={}), \
                patch("learner_api.views._send_platform_invitation", return_value={}):
            response = unwrap(staff_users)(request)
        return response, save

    def test_staff_cannot_assign_any_role_on_creation(self):
        for grants in (["coach"], ["super-admin"], ["coach", "tutor"]):
            with self.subTest(grants=grants):
                response, save = self.create("staff", grants)
                self.assertEqual(response.status_code, 403)
                save.assert_not_called()

    def test_admin_can_assign_coach_and_tutor_together(self):
        response, save = self.create("admin", ["coach", "tutor"])
        self.assertEqual(response.status_code, 201)
        self.assertEqual(save.call_args.kwargs["access"], "coach")
        self.assertEqual(save.call_args.kwargs["access_extra"], "tutor")

    def test_super_admin_is_saved_without_extra_grants(self):
        response, save = self.create("admin", ["coach", "super-admin"])
        self.assertEqual(response.status_code, 201)
        self.assertEqual(save.call_args.kwargs["access"], "super-admin")
        self.assertIsNone(save.call_args.kwargs["access_extra"])
