"""Create/patch enrolment."Enrolment_Documents" at deploy time.

The table has always been created lazily by
enrolment_api.document_tables.ensure_enrolment_documents_table on the first
request that needs it. That is kept as a safety net, but running DDL during a
user request is how the table shipped incomplete: its CREATE lacked the six
signature columns that documents.SELECT_COLS reads on every query, and the two
commands that added them (apply_employer_signing,
apply_document_learner_signature) both bail out when the table is absent. A
fresh deployment therefore auto-created a table that every subsequent read
failed against with UndefinedColumn -- and the employer portal swallows that
error, so its whole document queue disappeared silently.

This command gives deployment a real, ordered step. It shares its DDL with
document_tables so the two can never drift.

Idempotent -- CREATE TABLE IF NOT EXISTS plus ADD COLUMN IF NOT EXISTS.

    python manage.py apply_enrolment_documents_table --check
    python manage.py apply_enrolment_documents_table
"""
from django.core.management.base import BaseCommand
from django.db import connections, transaction

from ...document_tables import _PATCH_COLUMNS, ensure_enrolment_documents_table

CONN = "enrolment"
TABLE = 'enrolment."Enrolment_Documents"'


def _columns(cursor):
    cursor.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'enrolment' AND table_name = 'Enrolment_Documents'
        """
    )
    return {r[0] for r in cursor.fetchall()}


class Command(BaseCommand):
    help = 'Create/patch enrolment."Enrolment_Documents" for generated compliance documents.'

    def add_arguments(self, parser):
        parser.add_argument(
            "--check", action="store_true", help="Report what is missing, then exit."
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Apply and roll back, showing the before/after column list.",
        )

    def handle(self, *args, **options):
        conn = connections[CONN]

        with conn.cursor() as cursor:
            before = _columns(cursor)

        if not before:
            self.stdout.write(f"{TABLE} does not exist — it will be created.")
        else:
            missing = sorted({c for c, _ in _PATCH_COLUMNS} - before)
            if missing:
                for column in missing:
                    self.stdout.write(f"  missing: {column}")
            else:
                self.stdout.write("All expected columns present.")

        if options["check"]:
            return

        if options["dry_run"]:
            with transaction.atomic(using=CONN):
                self._apply(conn)
                with conn.cursor() as cursor:
                    after = _columns(cursor)
                self.stdout.write(f"\nwould add: {sorted(after - before)}")
                self.stdout.write(
                    self.style.WARNING("--dry-run: rolling back, nothing committed.")
                )
                transaction.set_rollback(True, using=CONN)
            return

        self._apply(conn)
        with conn.cursor() as cursor:
            after = _columns(cursor)
        added = sorted(after - before)
        self.stdout.write(
            self.style.SUCCESS(
                f"{TABLE} is ready." + (f" Added: {added}." if added else "")
            )
        )

    def _apply(self, conn):
        # Reuse the single DDL definition rather than restating it here, so the
        # deploy path and the request-path safety net can never disagree. The
        # _READY flag is process-global and would skip the work on a second
        # call, so clear it first.
        from ... import document_tables

        document_tables._READY = False
        ensure_enrolment_documents_table()
        document_tables._READY = False
