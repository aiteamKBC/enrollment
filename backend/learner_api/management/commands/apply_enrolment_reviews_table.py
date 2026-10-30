"""Create enrolment."Enrolment_Reviews" — the enrolment record of booked reviews.

Idempotent: safe to re-run. Creates the table if absent and adds any missing
column, so extending the model later means adding an ADD COLUMN IF NOT EXISTS
line here rather than a hand-run ALTER.

This file is the single source of truth for the table's shape: every column
EnrolmentReview declares must appear in both CREATE_SQL and ADD_COLUMNS. The
employer sign-off columns once lived only in apply_employer_signing, so a fresh
database got a table the model could not query at all (UndefinedColumn on every
read). apply_employer_signing still adds them — both use IF NOT EXISTS, so the
two commands agree in either order — but the definition belongs here.

One row per booked review, keyed by Event_key — the same key as the
"Coach".coach_calendar_event row the booking creates, so the enrolment record and
the live calendar can always be reconciled. The unique index on it makes the
upsert in learner_api.calendar race-safe.

Run it once:

    python manage.py apply_enrolment_reviews_table            # apply
    python manage.py apply_enrolment_reviews_table --dry-run  # show plan only
"""
from django.core.management.base import BaseCommand
from django.db import connections, transaction

CONN = "enrolment"
TABLE = "Enrolment_Reviews"

CREATE_SQL = f'''
CREATE TABLE IF NOT EXISTS enrolment."{TABLE}" (
    id                  bigserial PRIMARY KEY,
    "Event_key"         text NOT NULL,
    "Review_type"       text NOT NULL,
    "Review_label"      text,
    "Learner_kind"      text NOT NULL,
    "Learner_id"        bigint NOT NULL,
    "Learner_name"      text,
    "Learner_email"     text,
    "Coach_id"          bigint,
    "Coach_name"        text,
    "Coach_email"       text,
    "Scheduled_date"    date,
    "Scheduled_time"    time,
    "Duration_minutes"  integer NOT NULL DEFAULT 60,
    "Status"            text NOT NULL DEFAULT 'booked',
    "Notes"             text,
    "Meeting_provider"  text,
    "Meeting_link"      text,
    "Graph_event_id"    text,
    "Invite_sent"       boolean NOT NULL DEFAULT false,
    "Sync_error"        text,
    "Form_answers"      jsonb,
    "Section_status"    jsonb,
    "Form_completed"    boolean NOT NULL DEFAULT false,
    "Form_completed_at" timestamptz,
    "Reviewed_by"       text,
    "Started_at"        timestamptz,
    "Learner_signature" text,
    "Learner_signed_name" text,
    "Learner_signed_at" timestamptz,
    "Admin_signature"   text,
    "Admin_signed_name" text,
    "Admin_signed_at"   timestamptz,
    -- Third signing party: the learner's employer. Employer_signature_required is
    -- nullable so the API decides the default per review type rather than the DDL
    -- freezing it (see EnrolmentReview.employer_signature_required).
    "Employer_signature"          text,
    "Employer_signed_name"        text,
    "Employer_signed_at"          timestamptz,
    "Employer_signature_required" boolean,
    "Booked_at"         timestamptz,
    "Cancelled_at"      timestamptz,
    "Created_at"        timestamptz NOT NULL DEFAULT now(),
    "Updated_at"        timestamptz NOT NULL DEFAULT now()
)
'''

# Columns added after the initial release go here; each is IF NOT EXISTS so a
# re-run on an older table brings it up to date.
ADD_COLUMNS = [
    ('"Review_label"', "text"),
    ('"Learner_name"', "text"),
    ('"Learner_email"', "text"),
    ('"Coach_id"', "bigint"),
    ('"Coach_name"', "text"),
    ('"Coach_email"', "text"),
    ('"Scheduled_date"', "date"),
    ('"Scheduled_time"', "time"),
    ('"Duration_minutes"', "integer NOT NULL DEFAULT 60"),
    ('"Status"', "text NOT NULL DEFAULT 'booked'"),
    ('"Notes"', "text"),
    ('"Meeting_provider"', "text"),
    ('"Meeting_link"', "text"),
    ('"Graph_event_id"', "text"),
    ('"Invite_sent"', "boolean NOT NULL DEFAULT false"),
    ('"Sync_error"', "text"),
    # The review form (see EnrolmentReview.form_answers).
    ('"Form_answers"', "jsonb"),
    ('"Section_status"', "jsonb"),
    ('"Form_completed"', "boolean NOT NULL DEFAULT false"),
    ('"Form_completed_at"', "timestamptz"),
    ('"Reviewed_by"', "text"),
    ('"Started_at"', "timestamptz"),
    # Sign-off, once the form is completed (see EnrolmentReview.learner_signature).
    ('"Learner_signature"', "text"),
    ('"Learner_signed_name"', "text"),
    ('"Learner_signed_at"', "timestamptz"),
    ('"Admin_signature"', "text"),
    ('"Admin_signed_name"', "text"),
    ('"Admin_signed_at"', "timestamptz"),
    # Third signing party: the learner's employer (see
    # EnrolmentReview.employer_signature). Previously added only by
    # apply_employer_signing, which left a fresh database without them and made
    # every EnrolmentReview query fail with UndefinedColumn.
    ('"Employer_signature"', "text"),
    ('"Employer_signed_name"', "text"),
    ('"Employer_signed_at"', "timestamptz"),
    ('"Employer_signature_required"', "boolean"),
    ('"Booked_at"', "timestamptz"),
    ('"Cancelled_at"', "timestamptz"),
    ('"Created_at"', "timestamptz NOT NULL DEFAULT now()"),
    ('"Updated_at"', "timestamptz NOT NULL DEFAULT now()"),
]

# One enrolment row per calendar event — also the conflict target for the upsert
# in learner_api.calendar.
INDEXES_SQL = [
    f'''CREATE UNIQUE INDEX IF NOT EXISTS enrolment_reviews_event_key_uniq
            ON enrolment."{TABLE}" ("Event_key")''',
    # The reviews panel reads every review for one learner.
    f'''CREATE INDEX IF NOT EXISTS enrolment_reviews_learner_idx
            ON enrolment."{TABLE}" ("Learner_kind", "Learner_id")''',
    # Enrolment officers review their own caseload's bookings.
    f'''CREATE INDEX IF NOT EXISTS enrolment_reviews_coach_idx
            ON enrolment."{TABLE}" ("Coach_email")''',
    # Employers pull their own "what still needs signing?" list. Also created by
    # apply_employer_signing; both are IF NOT EXISTS so either order is safe.
    f'''CREATE INDEX IF NOT EXISTS enrolment_reviews_employer_signed_idx
            ON enrolment."{TABLE}" ("Employer_signed_at")''',
]


class Command(BaseCommand):
    help = 'Create/patch enrolment."Enrolment_Reviews" for booked enrolment reviews.'

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
                for index_sql in INDEXES_SQL:
                    cur.execute(index_sql)

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
