"""Create the additive local projection used by the BKSB wizard step.

The enrolment database is managed outside Django migrations.  This command is
idempotent and intentionally contains no provider call; run it against a
reviewed database branch before enabling BKSB in an environment.
"""
from django.core.management.base import BaseCommand
from django.db import connections, transaction


CONN = "enrolment"
TABLE = "Bksb_Initial_Assessments"

CREATE_SQL = f'''
CREATE TABLE IF NOT EXISTS enrolment."{TABLE}" (
    id                    bigserial PRIMARY KEY,
    "Learner_kind"       varchar(32) NOT NULL,
    "Learner_id"         bigint NOT NULL,
    "Subject"            varchar(16) NOT NULL,
    "Bksb_user_id"       bigint,
    "Bksb_username"      text,
    "Course_subject"     text,
    "Course_name"        text,
    "Course_is_reforms"  boolean,
    "Status"             varchar(32) NOT NULL DEFAULT 'not_started',
    "Provider_session_id" text,
    "Result"             text,
    "Level"              text,
    "Assessment_date"    timestamptz,
    "Last_synced_at"     timestamptz,
    "Last_error"         text,
    "Attempts"           jsonb NOT NULL DEFAULT '[]'::jsonb,
    "Created_at"         timestamptz NOT NULL DEFAULT now(),
    "Updated_at"         timestamptz NOT NULL DEFAULT now()
)
'''

ADD_COLUMNS = [
    ('"Bksb_user_id"', "bigint"),
    ('"Bksb_username"', "text"),
    ('"Course_subject"', "text"),
    ('"Course_name"', "text"),
    ('"Course_is_reforms"', "boolean"),
    ('"Status"', "varchar(32) NOT NULL DEFAULT 'not_started'"),
    ('"Provider_session_id"', "text"),
    ('"Result"', "text"),
    ('"Level"', "text"),
    ('"Assessment_date"', "timestamptz"),
    ('"Last_synced_at"', "timestamptz"),
    ('"Last_error"', "text"),
    ('"Attempts"', "jsonb NOT NULL DEFAULT '[]'::jsonb"),
    ('"Created_at"', "timestamptz NOT NULL DEFAULT now()"),
    ('"Updated_at"', "timestamptz NOT NULL DEFAULT now()"),
]

INDEXES = [
    'CREATE UNIQUE INDEX IF NOT EXISTS bksb_initial_assessment_learner_subject_uniq '
    'ON enrolment."Bksb_Initial_Assessments" ("Learner_kind", "Learner_id", "Subject")',
    'CREATE INDEX IF NOT EXISTS idx_bksb_initial_assessment_provider_user '
    'ON enrolment."Bksb_Initial_Assessments" ("Bksb_user_id")',
]


class Command(BaseCommand):
    help = 'Create/patch the additive BKSB initial assessment projection table.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Roll back the DDL after showing the plan.')

    def handle(self, *args, **options):
        conn = connections[CONN]
        with transaction.atomic(using=CONN):
            cur = conn.cursor()
            cur.execute('CREATE SCHEMA IF NOT EXISTS enrolment')
            cur.execute(CREATE_SQL)
            for column, ddl in ADD_COLUMNS:
                cur.execute(f'ALTER TABLE enrolment."{TABLE}" ADD COLUMN IF NOT EXISTS {column} {ddl}')
            for statement in INDEXES:
                cur.execute(statement)
            if options['dry_run']:
                transaction.set_rollback(True, using=CONN)
                self.stdout.write(self.style.WARNING('Dry run: rolled back.'))
            else:
                self.stdout.write(self.style.SUCCESS(f'Ensured enrolment."{TABLE}" and its indexes.'))
