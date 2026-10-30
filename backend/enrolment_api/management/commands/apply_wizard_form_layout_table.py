"""Create enrolment."Wizard_Form_Layouts", where the wizard builder publishes to.

Idempotent: CREATE TABLE IF NOT EXISTS only. The publish endpoint also ensures
the table on first use, so running this is optional — it exists so an
environment can be provisioned (and checked) ahead of the first publish, like
every other enrolment table.

Custom-field columns are NOT created here: each publish adds the columns its
layout needs (see enrolment_api/wizard_layout.py).

    python manage.py apply_wizard_form_layout_table            # apply
    python manage.py apply_wizard_form_layout_table --dry-run  # show plan only
"""
from django.core.management.base import BaseCommand
from django.db import connections, transaction

from enrolment_api.wizard_layout import CONN, LAYOUT_TABLE, ensure_layout_table


class Command(BaseCommand):
    help = 'Create enrolment."Wizard_Form_Layouts" for the enrolment wizard builder.'

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Roll back instead of committing.")

    def handle(self, *args, **options):
        with transaction.atomic(using=CONN):
            with connections[CONN].cursor() as cursor:
                ensure_layout_table(cursor)
                cursor.execute(f'SELECT count(*) FROM enrolment."{LAYOUT_TABLE}"')
                self.stdout.write(f'  enrolment."{LAYOUT_TABLE}" — {cursor.fetchone()[0]} published layout(s)')
            if options["dry_run"]:
                transaction.set_rollback(True, using=CONN)
                self.stdout.write(self.style.WARNING("--dry-run: rolled back, nothing committed."))
            else:
                self.stdout.write(self.style.SUCCESS("Committed."))
