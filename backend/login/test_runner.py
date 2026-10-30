"""Test runner that provisions the unmanaged Neon tables into the test database.

The `login` models — like every other Neon-backed model in this project — are
``managed = False``, and ``EnrolmentRouter.allow_migrate`` returns False for the
enrolment connection. So Django's test runner creates ``test_neondb`` with none
of the tables the tests need, and every query fails with "relation does not
exist".

This runner closes that gap by running the same ``apply_*`` DDL commands that
provision a real environment, against the test database, immediately after it is
created. The tests then exercise the actual table shapes rather than a Django
approximation of them — which is the point, given the DDL is hand-written.

Enable it for a run:

    python manage.py test login --testrunner=login.test_runner.EnrolmentTestRunner

or make it the default by setting in config/settings.py:

    TEST_RUNNER = 'login.test_runner.EnrolmentTestRunner'
"""
from pathlib import Path

import psycopg

from django.core.management import call_command
from django.db import connections
from django.db.backends.signals import connection_created
from django.test.runner import DiscoverRunner

#: chat's migrations are skipped in the test build (see settings.MIGRATION_MODULES
#: / SECURITY_AUDIT.md A17), but its one ``managed = True`` model —
#: ``MessageDeletion`` (``chat"."message_deletions``) — is still created by
#: run-syncdb during test-database setup and needs the ``chat`` schema to exist
#: first. coach_api keeps its migrations, which create their own schema, so it is
#: not listed here.
_SYNCDB_SCHEMAS = ("chat",)


def _ensure_syncdb_schemas(sender, connection, **kwargs):
    """Create the schemas run-syncdb needs — but only ever on a test database.

    Guarded twice: this handler is connected only by the test runner (below),
    and it refuses any connection whose database name is not ``test_``-prefixed,
    so it can never run ``CREATE SCHEMA`` against a real database even if it were
    left connected by accident.
    """
    if connection.vendor != "postgresql":
        return
    if not (connection.settings_dict.get("NAME") or "").startswith(TEST_DATABASE_PREFIX):
        return
    with connection.cursor() as cursor:
        for schema in _SYNCDB_SCHEMAS:
            cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')

#: DDL commands to run against the freshly created test database, in order.
#: Staff_users and Employers come first: the login tables reference the people
#: in them, and the tests create staff rows to hang accounts off.
SETUP_COMMANDS = (
    "apply_staff_users_table",
    "apply_employer_tables",
    # The learner table, for the tests that post to the learner creation form.
    # Its Employer_id column was added later, by its own command — a fresh test
    # database needs both, in this order.
    "apply_created_users_table",
    "apply_created_users_employer_id",
    # Created_users."Attendance_type" — likewise added after the base table.
    "apply_created_users_attendance_type",
    # Current StaffUser/Employer/learner models include the additive public
    # uuid column. Fresh test databases must match that production shape.
    "apply_user_uuid",
    # Staff_users."Access" — the staff access grant. Added after the base table
    # command, which does not know about it.
    "apply_staff_access_column",
    "apply_staff_access_extra_column",
    # Staff_users/Employers/Created_users."uuid" — the permanent public user
    # identifier. The models declare it, so every INSERT names the column and a
    # test database without it fails on the first row created.
    "apply_user_uuid",
    "apply_login_tables",
)

#: Django's test databases are always named with this prefix. Used as the
#: safety check that we are not about to run DDL against production.
TEST_DATABASE_PREFIX = "test_"


def _verify_security_test_branch(settings):
    """Prove the exact Neon branch before Django can run setup or migrations.

    A different Neon branch has a different host, but host inequality from
    production only proves "not production". It does not prove that the branch
    is the disposable one approved for this run. Query Neon's branch id through
    a read-only direct connection and compare it with the explicit expected id.
    """
    config = settings.DATABASES.get("enrolment") or {}
    host = (config.get("HOST") or "").lower()
    allowed_host = (getattr(settings, "SECURITY_TEST_BRANCH_HOST", "") or "").lower()
    expected_branch_id = (getattr(settings, "SECURITY_TEST_BRANCH_ID", "") or "").strip()
    if not allowed_host or host != allowed_host:
        raise RuntimeError(
            "Branch-mode preflight: the 'enrolment' connection host does not match "
            "SECURITY_TEST_BRANCH_HOST; refusing to run database setup."
        )
    if not expected_branch_id:
        raise RuntimeError(
            "Branch-mode preflight: SECURITY_TEST_BRANCH_ID is missing; refusing "
            "to run database setup without an exact Neon branch identity."
        )

    options = config.get("OPTIONS") or {}
    connect_kwargs = {
        "dbname": config.get("NAME"),
        "user": config.get("USER"),
        "password": config.get("PASSWORD"),
        "host": config.get("HOST"),
        "port": config.get("PORT") or 5432,
        "connect_timeout": options.get("connect_timeout", 10),
        "options": "-c default_transaction_read_only=on",
    }
    for key in ("sslmode", "channel_binding"):
        value = options.get(key)
        if value:
            connect_kwargs[key] = value

    connection = psycopg.connect(**connect_kwargs)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "select current_database(), current_setting(%s), "
                "current_setting(%s, true)",
                ["transaction_read_only", "neon.branch_id"],
            )
            database_name, read_only, actual_branch_id = cursor.fetchone()
    finally:
        connection.rollback()
        connection.close()

    if read_only != "on":
        raise RuntimeError(
            "Branch-mode preflight was not read-only; refusing to run database setup."
        )
    if database_name != config.get("NAME"):
        raise RuntimeError(
            "Branch-mode preflight reached an unexpected database; refusing to run setup."
        )
    if actual_branch_id != expected_branch_id:
        raise RuntimeError(
            "Branch-mode preflight reached Neon branch "
            f"{actual_branch_id!r}, expected {expected_branch_id!r}; refusing to run setup."
        )


class EnrolmentTestRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        """Force the mail kill switch off for the whole run.

        The integration tests exercise the real invitation and reset flows
        against ``qa-…@kbc.invalid`` addresses, and ``email_azure.send_mail``
        reads ``os.environ`` at call time — so once this deployment's mail app is
        configured, an unguarded run would fire a live Graph ``sendMail`` from
        the real ``lms@`` mailbox for every one of those tests.

        Setting the documented kill switch here is better than mocking each call
        site: it cannot be forgotten by a test added later, and the tests that
        deliberately assert transport behaviour set their own environment with
        ``mock.patch.dict``, which overrides this.
        """
        import os

        os.environ["AZURE_MAIL_ENABLED"] = "false"
        # run-syncdb creates the managed chat/coach tables during database setup
        # and needs their schemas to exist first. Connected here (test runs only)
        # and self-guarded to test_ databases.
        connection_created.connect(_ensure_syncdb_schemas)
        super().setup_test_environment(**kwargs)

    def setup_databases(self, **kwargs):
        from django.conf import settings

        # Branch mode (A17): run against the dedicated Neon test branch, which is
        # a schema clone of production where every managed=False table and the
        # full migration history already exist. None of the apply_* provisioning
        # below is needed or wanted, and the test_-prefix guard does not apply
        # (isolation is by host, asserted at settings load and reconfirmed here).
        # The non-branch path below is unchanged.
        branch_mode = getattr(settings, "USE_SECURITY_TEST_BRANCH", False)
        if branch_mode and not self.keepdb:
            # BARRIER 4: without --keepdb Django would DROP and recreate the branch
            # database. Refuse rather than wipe it.
            raise RuntimeError(
                "Branch mode requires --keepdb: without it Django would drop and "
                "recreate the Neon test branch database."
            )

        if branch_mode:
            # Run before DiscoverRunner.setup_databases: that method may apply
            # migrations even with --keepdb, so branch identity must already be
            # proven before it is allowed to start.
            _verify_security_test_branch(settings)

        old_config = super().setup_databases(**kwargs)

        if branch_mode:
            # CONDITION 3: pass only when branch mode is active, and take the
            # allowed host from configuration — not a hardcoded string.
            connection = connections["enrolment"]
            host = (connection.settings_dict.get("HOST") or "").lower()
            allowed = (getattr(settings, "SECURITY_TEST_BRANCH_HOST", "") or "").lower()
            if not allowed or host != allowed:
                raise RuntimeError(
                    "Branch-mode setup: the 'enrolment' connection host "
                    f"({host!r}) does not match the approved SECURITY_TEST_BRANCH_HOST; "
                    "refusing to provision or run."
                )
            self.log(
                "Branch mode: Neon test branch is pre-provisioned; skipping apply_* DDL."
            )
            return old_config

        connection = connections["enrolment"]

        # The apply_* commands address connections["enrolment"] directly. By
        # this point Django has pointed that alias's NAME at the test database,
        # but a connection to the *production* database may already be open and
        # cached from an earlier query. Close it so the next cursor is opened
        # against the test database — otherwise these commands would silently
        # re-run their DDL against production.
        connection.close()
        test_name = connection.settings_dict["NAME"]

        # Refuse to run the DDL unless the alias really was redirected to a test
        # database. Django leaves an alias pointing at the original database
        # when it considers it a mirror/duplicate of another entry — and both
        # `default` and `enrolment` are configured from the same Neon URL here.
        # Without this guard the "test" setup would create tables in production.
        if not test_name.startswith(TEST_DATABASE_PREFIX):
            raise RuntimeError(
                f"Refusing to provision test tables: the 'enrolment' connection "
                f"still points at {test_name!r}, not a test database. Give the "
                f"enrolment alias its own TEST['NAME'] in settings, or run these "
                f"tests against a separate database."
            )

        self.log(f"Provisioning unmanaged enrolment/login tables into '{test_name}'…")

        # The apply_* commands create tables *in* the enrolment schema but do
        # not create the schema itself — on a real deployment it predates them.
        # A fresh test database has neither.
        with connection.cursor() as cursor:
            cursor.execute('CREATE SCHEMA IF NOT EXISTS "enrolment"')

        # Some "Learner" schema tables are deployed from reviewed SQL rather
        # than runtime request code. Apply those same deployment artifacts to
        # the test DB.
        sql_dir = Path(__file__).resolve().parents[1] / "learner_api" / "sql"
        for artifact in ("learning_reflection_submissions.sql", "learner_monthly_reports.sql"):
            statements = (sql_dir / artifact).read_text(encoding="utf-8")
            with connection.cursor() as cursor:
                cursor.execute(statements)

        for command in SETUP_COMMANDS:
            try:
                call_command(command, verbosity=0)
            except Exception as exc:  # noqa: BLE001 - report and continue
                # Surfaced rather than raised: one missing optional table should
                # not stop the whole suite, and the failing tests will say
                # exactly which relation is absent.
                self.log(f"WARNING: {command} failed against the test database: {exc}")

        return old_config
