"""Prepare append-only wizard versions and their learner assignments.

Idempotent: creates missing tables and adds missing version metadata. The publish endpoint also ensures
the table on first use, so running this is optional — it exists so an
environment can be provisioned (and checked) ahead of the first publish, like
every other enrolment table.

This adds scope/name/parent metadata to the existing layout table and creates
the sparse assignment table. Custom-field columns are NOT created here: each
publish adds the columns its layout needs (see enrolment_api/wizard_layout.py).

    python manage.py apply_wizard_form_layout_table            # apply
    python manage.py apply_wizard_form_layout_table --dry-run  # show plan only
"""
from django.core.management.base import BaseCommand
from django.db import connections, transaction

from enrolment_api.wizard_layout import ASSIGNMENT_TABLE, CONN, LAYOUT_TABLE, ensure_layout_table


class Command(BaseCommand):
    help = 'Prepare published wizard versions and per-learner layout assignments.'

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Roll back instead of committing.")

    def handle(self, *args, **options):
        with transaction.atomic(using=CONN):
            with connections[CONN].cursor() as cursor:
                ensure_layout_table(cursor)
                cursor.execute(
                    f'''INSERT INTO enrolment."{ASSIGNMENT_TABLE}" AS saved
                        ("Learner_kind", "Learner_id", "Submitted_version", "Submitted_at", "Updated_at")
                        SELECT CASE WHEN lower(COALESCE(u."Learner_type", '')) = 'commercial' THEN 'commercial' ELSE 'apprenticeship' END,
                               u.id, v.id, now(), now()
                        FROM enrolment."Created_users" u
                        CROSS JOIN (
                            SELECT id FROM enrolment."{LAYOUT_TABLE}"
                            WHERE "Scope" = 'default' AND NOT "Archived"
                            ORDER BY id DESC LIMIT 1
                        ) v
                        WHERE lower(COALESCE(u."Onboarding_status", '')) IN ('submitted', 'completed')
                        ON CONFLICT ("Learner_kind", "Learner_id") DO UPDATE SET
                            "Submitted_version" = COALESCE(saved."Submitted_version", EXCLUDED."Submitted_version"),
                            "Submitted_at" = COALESCE(saved."Submitted_at", EXCLUDED."Submitted_at"),
                            "Updated_at" = now()'''
                )
                self.stdout.write(f'  prepared submitted layout references for {cursor.rowcount} existing learner(s)')
                cursor.execute(f'SELECT count(*) FROM enrolment."{ASSIGNMENT_TABLE}"')
                self.stdout.write(f'  enrolment."{ASSIGNMENT_TABLE}" — {cursor.fetchone()[0]} learner assignment(s)')
                cursor.execute(f'SELECT count(*) FROM enrolment."{LAYOUT_TABLE}"')
                self.stdout.write(f'  enrolment."{LAYOUT_TABLE}" — {cursor.fetchone()[0]} published layout(s)')
            if options["dry_run"]:
                transaction.set_rollback(True, using=CONN)
                self.stdout.write(self.style.WARNING("--dry-run: rolled back, nothing committed."))
            else:
                self.stdout.write(self.style.SUCCESS("Committed."))
