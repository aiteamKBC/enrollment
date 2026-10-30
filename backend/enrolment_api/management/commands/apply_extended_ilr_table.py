"""Create enrolment."Extended_ILR" — storage for the wizard's Extended ILR step.

Idempotent: safe to re-run. Creates the table if absent and adds any missing
column, so extending the model later means adding an ADD COLUMN IF NOT EXISTS
line here rather than a hand-run ALTER.

One row per learner, keyed by (Learner_kind, Learner_id) — the console's
directory spans enrolment."Enrolment_Users" and enrolment."Commercial_users", so
a single foreign key cannot address both. A unique index on that pair makes the
upsert in views.extended_ilr race-safe.

Run it once:

    python manage.py apply_extended_ilr_table            # apply
    python manage.py apply_extended_ilr_table --dry-run  # show plan only
"""
from django.core.management.base import BaseCommand
from django.db import connections, transaction

CONN = "enrolment"
TABLE = "Extended_ILR"

CREATE_SQL = f'''
CREATE TABLE IF NOT EXISTS enrolment."{TABLE}" (
    id                    bigserial PRIMARY KEY,
    "Learner_kind"        text NOT NULL,
    "Learner_id"          bigint NOT NULL,
    "Learner_name"        text,
    "Answers"             jsonb,
    "Wizard_draft"        jsonb,
    "Learner_signed"      boolean NOT NULL DEFAULT false,
    "Learner_signed_date" text,
    "Provider_signed"     boolean NOT NULL DEFAULT false,
    "Provider_signed_date" text,
    "Completed"           boolean NOT NULL DEFAULT false,
    "Created_at"          timestamptz NOT NULL DEFAULT now(),
    "Updated_at"          timestamptz NOT NULL DEFAULT now()
)
'''

# Columns added after the initial release go here; each is IF NOT EXISTS so a
# re-run on an older table brings it up to date.
ADD_COLUMNS = [
    ('"Learner_name"', "text"),
    ('"Answers"', "jsonb"),
    # The wizard's non-ILR steps (skills radar, PLR, CV/job, policies, personal
    # details) so a part-finished enrolment can be reopened and reviewed.
    ('"Wizard_draft"', "jsonb"),
    ('"Learner_signed"', "boolean NOT NULL DEFAULT false"),
    ('"Learner_signed_date"', "text"),
    ('"Provider_signed"', "boolean NOT NULL DEFAULT false"),
    ('"Provider_signed_date"', "text"),
    ('"Completed"', "boolean NOT NULL DEFAULT false"),
    ('"Created_at"', "timestamptz NOT NULL DEFAULT now()"),
    ('"Updated_at"', "timestamptz NOT NULL DEFAULT now()"),
    # Next of kin's own address, asked when it is not the learner's.
    ('"Next_of_kin_postcode"', "text"),
    ('"Next_of_kin_address"', "text"),
    # Eligibility evidence files held in Azure, each with its blob path.
    ('"Eligibility_evidence"', "jsonb NOT NULL DEFAULT '[]'::jsonb"),
]

# One ILR per learner — also the conflict target for the upsert in the view.
INDEX_SQL = f'''
CREATE UNIQUE INDEX IF NOT EXISTS extended_ilr_learner_uniq
    ON enrolment."{TABLE}" ("Learner_kind", "Learner_id")
'''


class Command(BaseCommand):
    help = 'Create/patch enrolment."Extended_ILR" for Extended ILR storage.'

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show the before/after column list without committing (rolls back).",
        )

    def _columns(self, cur):
        cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema='enrolment' AND table_name=%s "
            "ORDER BY ordinal_position",
            [TABLE],
        )
        return cur.fetchall()

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        conn = connections[CONN]

        try:
            with transaction.atomic(using=CONN):
                cur = conn.cursor()

                self.stdout.write("\n===== BEFORE =====")
                before = self._columns(cur)
                if not before:
                    self.stdout.write("  (table does not exist)")
                for col in before:
                    self.stdout.write(f"  {col}")

                cur.execute(CREATE_SQL)
                for name, ddl in ADD_COLUMNS:
                    cur.execute(
                        f'ALTER TABLE enrolment."{TABLE}" ADD COLUMN IF NOT EXISTS {name} {ddl}'
                    )
                cur.execute(INDEX_SQL)

                self.stdout.write("\n===== AFTER =====")
                for col in self._columns(cur):
                    self.stdout.write(f"  {col}")

                if dry_run:
                    self.stdout.write(self.style.WARNING("\n--dry-run: rolling back, nothing committed."))
                    transaction.set_rollback(True, using=CONN)
                else:
                    self.stdout.write(self.style.SUCCESS("\nCommitted."))
        except Exception as exc:  # noqa: BLE001 - surface any DB/DDL failure clearly
            self.stderr.write(self.style.ERROR(f"Migration failed (rolled back): {exc}"))
            raise
