"""Create, or bring up to date, the tables of the public apprenticeship
application (/apprenticeship_application):

  * enrolment.eligibility_form, where each submission is saved: created if
    missing; otherwise the postcode-location and layout_version columns are
    added and every answer the eligibility form builder can remove is allowed
    to be NULL (first name, surname and work email stay NOT NULL);
  * enrolment."Eligibility_Form_Layouts", where the builder publishes the form.

Idempotent, and additive only: no column, table or row is dropped or changed.
Run once per environment before this release serves the form -- the public
endpoint never runs DDL itself, and its insert writes the new columns.

    python manage.py apply_eligibility_form_table            # apply
    python manage.py apply_eligibility_form_table --dry-run  # show plan only
"""
from django.core.management.base import BaseCommand
from django.db import connections, transaction

from enrolment_api.eligibility_form import CONN, TABLE, ensure_table, upgrade_table
from enrolment_api.eligibility_layout import LAYOUT_TABLE, ensure_layout_table


class Command(BaseCommand):
    help = "Create or update enrolment.eligibility_form and its builder's layout table."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Roll back instead of committing.")

    def handle(self, *args, **options):
        with transaction.atomic(using=CONN):
            with connections[CONN].cursor() as cursor:
                ensure_table(cursor)
                upgrade_table(cursor)
                ensure_layout_table(cursor)
                cursor.execute(f"SELECT count(*) FROM enrolment.{TABLE}")
                self.stdout.write(f"  enrolment.{TABLE} — {cursor.fetchone()[0]} application(s)")
                cursor.execute(f'SELECT count(*) FROM enrolment."{LAYOUT_TABLE}"')
                self.stdout.write(f'  enrolment."{LAYOUT_TABLE}" — {cursor.fetchone()[0]} published layout(s)')
            if options["dry_run"]:
                transaction.set_rollback(True, using=CONN)
                self.stdout.write(self.style.WARNING("--dry-run: rolled back, nothing committed."))
            else:
                self.stdout.write(self.style.SUCCESS("Committed."))
