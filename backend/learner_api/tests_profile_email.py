"""Cross-role email ownership checks; all database access is mocked."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from . import profile_email


class ProfileEmailTests(SimpleTestCase):
    def test_existing_email_in_any_profile_blocks_a_new_profile(self):
        for owner in (
            profile_email.LoginAccount,
            profile_email.EnrolmentUser,
            profile_email.StaffUser,
            profile_email.Employer,
        ):
            with self.subTest(owner=owner.__name__):
                managers = [MagicMock() for _ in range(4)]
                for manager in managers:
                    manager.annotate.return_value.filter.return_value.exists.return_value = False
                managers[(profile_email.LoginAccount, profile_email.EnrolmentUser,
                          profile_email.StaffUser, profile_email.Employer).index(owner)] \
                    .annotate.return_value.filter.return_value.exists.return_value = True
                with patch.object(profile_email.LoginAccount, "objects", managers[0]), \
                     patch.object(profile_email.EnrolmentUser, "all_learners", managers[1]), \
                     patch.object(profile_email.StaffUser, "objects", managers[2]), \
                     patch.object(profile_email.Employer, "objects", managers[3]):
                    self.assertTrue(profile_email.profile_email_in_use("  PERSON@Example.test "))
                for manager in managers:
                    if manager.annotate.called:
                        manager.annotate.return_value.filter.assert_called_once_with(
                            _email_key="person@example.test"
                        )

    def test_lock_and_check_run_before_creation(self):
        cursor = MagicMock()
        connection = SimpleNamespace(in_atomic_block=True)
        connection.cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        with patch.object(profile_email, "connections", {"enrolment": connection}), \
             patch.object(profile_email, "profile_email_in_use", return_value=True):
            with self.assertRaises(profile_email.ProfileEmailInUse):
                profile_email.assert_available_for_new_profile(" Person@Example.test ")
        cursor.execute.assert_called_once_with(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            ["person@example.test"],
        )

    def test_unchanged_email_does_not_block_legacy_profile_edits(self):
        with patch.object(profile_email, "assert_available_for_new_profile") as guard:
            profile_email.assert_available_for_email_change(
                "Person@Example.test", " person@example.test "
            )
        guard.assert_not_called()
