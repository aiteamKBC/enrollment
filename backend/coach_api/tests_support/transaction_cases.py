"""Real transactions and HTTP middleware with scoped managed-table cleanup."""
from django.apps import apps
from django.test import TransactionTestCase


class CoachTransactionTestCase(TransactionTestCase):
    databases = {"default", "enrolment"}

    def _fixture_teardown(self):
        # Keep the full registry during requests (Django auth middleware needs
        # auth.User even though LMS login uses its own unmanaged tables).
        # Restrict only flush: legacy chat FKs make an unrelated auth_user
        # TRUNCATE invalid. Subclasses explicitly clean their unmanaged rows.
        self.available_apps = ["coach_api"]
        apps.set_available_apps(self.available_apps)
        try:
            super()._fixture_teardown()
        finally:
            apps.unset_available_apps()
            self.available_apps = None
