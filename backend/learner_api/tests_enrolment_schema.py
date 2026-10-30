"""Schema/model agreement tests for the enrolment tables.

These exist because of P0-1 and P0-2 in ENROLMENT_GAP_ANALYSIS.md: a table's
shape is declared in an apply_* management command while the model that reads it
lives in models.py, and nothing checked the two agreed. Both defects were a
column the model selected on every query but no DDL ever created, so a fresh
database failed with UndefinedColumn on the very first read.

They are SimpleTestCase (no database) on purpose — they parse the DDL as text
and compare it against the model's own field list, so they run under
DJANGO_USE_SQLITE and in CI without a Neon connection. That is the whole point:
the check has to be cheap enough to run everywhere, or it will not run at all.
"""
import re

from django.test import SimpleTestCase

from .management.commands.apply_enrolment_reviews_table import (
    ADD_COLUMNS,
    CREATE_SQL,
    INDEXES_SQL,
)
from .models import EnrolmentReview


def _quoted_columns(sql):
    """Every "Quoted" identifier at the start of a DDL column definition."""
    return {m.group(1) for m in re.finditer(r'"([A-Za-z_][A-Za-z0-9_]*)"\s+\w', sql)}


def _model_db_columns(model):
    """The db_column of every concrete, non-auto field on the model."""
    return {
        f.db_column or f.column
        for f in model._meta.concrete_fields
        if not f.auto_created
    }


class EnrolmentReviewsDdlTests(SimpleTestCase):
    """enrolment."Enrolment_Reviews" must cover every EnrolmentReview field."""

    def test_create_sql_covers_every_model_column(self):
        created = _quoted_columns(CREATE_SQL)
        missing = sorted(_model_db_columns(EnrolmentReview) - created - {"id"})
        self.assertEqual(
            missing,
            [],
            "EnrolmentReview selects these columns but CREATE_SQL never creates "
            f"them, so a fresh database raises UndefinedColumn: {missing}",
        )

    # The original-release columns are NOT NULL with no default. ADD COLUMN
    # NOT NULL without a default fails on a non-empty table, so they are
    # deliberately absent from ADD_COLUMNS and can only come from CREATE_SQL.
    _CREATE_ONLY = frozenset(
        {"Event_key", "Review_type", "Learner_kind", "Learner_id", "id"}
    )

    def test_add_columns_covers_every_patchable_model_column(self):
        # ADD_COLUMNS is what patches an already-deployed table, so every column
        # that *can* be added later has to be here — otherwise existing
        # databases silently stay broken, which is exactly what caused P0-2.
        patched = {name.strip('"') for name, _ in ADD_COLUMNS}
        missing = sorted(
            _model_db_columns(EnrolmentReview) - patched - self._CREATE_ONLY
        )
        self.assertEqual(
            missing,
            [],
            "These columns are in CREATE_SQL but not ADD_COLUMNS, so an existing "
            f"database is never brought up to date: {missing}",
        )

    def test_create_only_columns_really_are_unpatchable(self):
        # Guards the exemption above: if one of these loses its NOT NULL, or
        # gains a default, it becomes patchable and belongs in ADD_COLUMNS.
        for column in self._CREATE_ONLY - {"id"}:
            match = re.search(rf'"{column}"\s+([^,\n]+)', CREATE_SQL)
            self.assertIsNotNone(match, f'"{column}" is no longer in CREATE_SQL')
            definition = match.group(1).upper()
            self.assertIn(
                "NOT NULL",
                definition,
                f'"{column}" is nullable now, so it can be added to an existing '
                "table — move it into ADD_COLUMNS and drop the exemption.",
            )
            self.assertNotIn(
                "DEFAULT",
                definition,
                f'"{column}" has a default now, so ADD COLUMN would succeed — '
                "move it into ADD_COLUMNS and drop the exemption.",
            )

    def test_employer_signing_columns_are_present(self):
        # P0-2 specifically: these four lived only in apply_employer_signing.
        expected = {
            "Employer_signature",
            "Employer_signed_name",
            "Employer_signed_at",
            "Employer_signature_required",
        }
        self.assertTrue(expected <= _quoted_columns(CREATE_SQL))
        self.assertTrue(expected <= {name.strip('"') for name, _ in ADD_COLUMNS})

    def test_every_statement_is_idempotent(self):
        # Re-running the command must never fail on an already-patched database.
        self.assertIn("CREATE TABLE IF NOT EXISTS", CREATE_SQL)
        for statement in INDEXES_SQL:
            self.assertIn("IF NOT EXISTS", statement)

    def test_employer_signed_index_exists(self):
        joined = " ".join(INDEXES_SQL)
        self.assertIn("enrolment_reviews_employer_signed_idx", joined)


class DatabaseAliasCheckTests(SimpleTestCase):
    """P0-4 / 7.2: the two-database assumption must fail loudly, not silently.

    Six apply_* commands created enrolment-schema tables through the `default`
    alias while EnrolmentRouter reads them through `enrolment`. That worked only
    because .env sets one Database_url both chains fall back to. These tests
    cover the check that now flags a split.
    """

    _PG = {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": "a.neon.tech",
        "PORT": "5432",
        "NAME": "kbc",
    }

    def _run(self, **overrides):
        from django.test import override_settings

        from .checks import check_enrolment_alias_matches_default

        with override_settings(**overrides):
            return [w.id for w in check_enrolment_alias_matches_default(None)]

    def test_silent_when_both_aliases_match(self):
        self.assertEqual(
            self._run(
                USE_SQLITE_FOR_TESTS=False,
                DATABASES={"default": self._PG, "enrolment": dict(self._PG)},
            ),
            [],
        )

    def test_warns_when_aliases_diverge(self):
        self.assertEqual(
            self._run(
                USE_SQLITE_FOR_TESTS=False,
                DATABASES={
                    "default": self._PG,
                    "enrolment": {**self._PG, "HOST": "b.neon.tech"},
                },
            ),
            ["learner_api.W002"],
        )

    def test_silent_when_only_the_port_representation_differs(self):
        # The two aliases are built by different code paths: `default` normalises
        # a missing port to "5432" while database_from_url leaves it empty. Same
        # endpoint, different repr -- this warned on every normal startup until
        # the port was normalised, which is how a check gets ignored.
        self.assertEqual(
            self._run(
                USE_SQLITE_FOR_TESTS=False,
                DATABASES={
                    "default": {**self._PG, "PORT": "5432"},
                    "enrolment": {**self._PG, "PORT": ""},
                },
            ),
            [],
        )

    def test_silent_when_host_case_or_whitespace_differs(self):
        self.assertEqual(
            self._run(
                USE_SQLITE_FOR_TESTS=False,
                DATABASES={
                    "default": {**self._PG, "HOST": "A.Neon.Tech "},
                    "enrolment": {**self._PG, "HOST": "a.neon.tech"},
                },
            ),
            [],
        )

    def test_still_warns_when_the_port_genuinely_differs(self):
        # Normalising must not swallow a real difference.
        self.assertEqual(
            self._run(
                USE_SQLITE_FOR_TESTS=False,
                DATABASES={
                    "default": {**self._PG, "PORT": "5432"},
                    "enrolment": {**self._PG, "PORT": "6543"},
                },
            ),
            ["learner_api.W002"],
        )

    def test_warns_when_enrolment_alias_is_missing(self):
        self.assertEqual(
            self._run(USE_SQLITE_FOR_TESTS=False, DATABASES={"default": self._PG}),
            ["learner_api.W001"],
        )

    def test_skipped_under_sqlite_test_mode(self):
        # Warning on every test run would train people to ignore it.
        self.assertEqual(
            self._run(
                USE_SQLITE_FOR_TESTS=True,
                DATABASES={
                    "default": self._PG,
                    "enrolment": {**self._PG, "HOST": "b.neon.tech"},
                },
            ),
            [],
        )

    def test_divergence_hint_names_the_cross_schema_readers(self):
        # The warning is only actionable if it says which call sites are affected.
        from django.test import override_settings

        from .checks import check_enrolment_alias_matches_default

        with override_settings(
            USE_SQLITE_FOR_TESTS=False,
            DATABASES={
                "default": self._PG,
                "enrolment": {**self._PG, "HOST": "b.neon.tech"},
            },
        ):
            hint = check_enrolment_alias_matches_default(None)[0].hint

        for fragment in ("curriculum.groups", "curriculum.weeks", "Learner"):
            self.assertIn(fragment, hint)


class EnrolmentDdlAliasTests(SimpleTestCase):
    """Every command that creates an enrolment table must use that alias."""

    COMMANDS = (
        "apply_enrolment_reviews_table",
        "apply_review_detail_tables",
        "apply_apprenticeship_agreements_table",
        "apply_ilr_documents_table",
        "apply_training_plans_table",
        "apply_written_agreements_table",
        "apply_document_learner_signature",
        "apply_learning_plan_jsonb",
        "apply_employer_signing",
        "apply_staff_users_table",
        "apply_employer_tables",
        "create_created_users_table",
    )

    def test_no_command_opens_a_default_alias_cursor(self):
        import importlib
        import inspect

        offenders = []
        for name in self.COMMANDS:
            module = importlib.import_module(
                f"learner_api.management.commands.{name}"
            )
            source = inspect.getsource(module)
            if "with connection.cursor()" in source:
                offenders.append(name)

        self.assertEqual(
            offenders,
            [],
            "These create/alter enrolment.* but open a cursor on the `default` "
            f"alias, so DDL and DML can land on different databases: {offenders}",
        )

    def test_no_command_opens_a_bare_atomic_block(self):
        # transaction.atomic() defaults to `default`; with an enrolment cursor the
        # DDL ends up outside the block it appears to be inside, so --dry-run
        # would not roll it back.
        import importlib
        import inspect

        offenders = []
        for name in self.COMMANDS:
            module = importlib.import_module(
                f"learner_api.management.commands.{name}"
            )
            if "transaction.atomic()" in inspect.getsource(module):
                offenders.append(name)

        self.assertEqual(
            offenders,
            [],
            f"bare transaction.atomic() must pass using=CONN: {offenders}",
        )


class CreatedUsersInstallPathTests(SimpleTestCase):
    """P0-3: enrolment."Created_users" must be creatable on a clean database.

    The CREATE used to sit *after* the check for the legacy Enrolment_Users
    table, so a fresh install hit "cutover already done — nothing to do" and
    returned without ever creating the core learner table of the whole system.
    """

    def _handle_source(self):
        import inspect

        from .management.commands.create_created_users_table import Command

        return inspect.getsource(Command.handle)

    def test_create_runs_before_the_legacy_table_check(self):
        source = self._handle_source()
        create_at = source.find("CREATE TABLE IF NOT EXISTS")
        guard_at = source.find('self._columns(cur, "Enrolment_Users")')

        self.assertNotEqual(create_at, -1, "the CREATE has gone missing")
        self.assertNotEqual(guard_at, -1, "the legacy-table guard has gone missing")
        self.assertLess(
            create_at,
            guard_at,
            "CREATE TABLE must run before the Enrolment_Users check, or a fresh "
            "database returns early and never gets Created_users at all (P0-3).",
        )

    def test_early_return_does_not_precede_the_create(self):
        # A bare `return` above the CREATE would reintroduce the bug by another
        # route, so assert the create is reached unconditionally. Match a return
        # *statement*, not the substring — prose in comments says "returned".
        source = self._handle_source()
        head = source[: source.find("CREATE TABLE IF NOT EXISTS")]
        code = [
            line for line in head.splitlines() if not line.lstrip().startswith("#")
        ]
        offenders = [line for line in code if re.match(r"\s*return\b", line)]
        self.assertEqual(
            offenders,
            [],
            "something returns before the CREATE — a fresh install would again "
            f"finish without creating the table: {offenders}",
        )

    def test_create_is_idempotent_and_makes_its_schema(self):
        source = self._handle_source()
        self.assertIn("CREATE SCHEMA IF NOT EXISTS enrolment", source)
        self.assertIn("CREATE TABLE IF NOT EXISTS", source)
