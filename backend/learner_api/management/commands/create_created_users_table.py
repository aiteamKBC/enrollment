"""Cut the learner data over to a single new table: enrolment."Created_users".

This replaces BOTH old learner tables with one. The user-creation form's own
fields come first in the column order, so the table reads as "the create form,
persisted"; the operational columns the rest of the app needs follow.

This is BOTH the fresh-install path for the table and the one-time cutover from
the two legacy tables. Step 1 always runs; steps 2-5 are skipped when there is
nothing to migrate, so it is equally safe on a clean database and on one that
still holds legacy data.

Ordered steps, all inside one transaction:

  1. CREATE enrolment."Created_users" — form fields first, then operational
     columns. Unconditional: this is the only path that creates the core learner
     table, so a clean install must get it. (It used to run only after the
     legacy-table check below, which meant a fresh database was told "nothing to
     do" and never got the table at all.)
  2. Stop here if enrolment."Enrolment_Users" is gone — the cutover is done.
  3. Copy every learner across from enrolment."Enrolment_Users" PRESERVING ids.
     Ids matter: the satellite tables (Extended_ILR, Enrolment_Documents, the
     Wizard_* set) reference a learner by (Learner_kind, Learner_id), so a
     renumbering here would orphan real wizard and document data. The id column is
     GENERATED ALWAYS AS IDENTITY, so the insert uses OVERRIDING SYSTEM VALUE and
     the identity sequence is then advanced past the highest copied id.
  4. Verify the copy row-for-row — same count, same id set, same emails. The DROP
     in step 5 is only reached if this passes, which is what makes it safe: the
     data is provably already in the new table.
  5. DROP enrolment."Enrolment_Users", enrolment."Commercial_users" and the two
     *_premerge_bak copies of them.

Run:

    python manage.py create_created_users_table --dry-run   # rehearse, roll back
    python manage.py create_created_users_table             # ensure + copy, KEEP old tables
    python manage.py create_created_users_table --drop-old  # ... and drop the old tables

`apply_created_users_table` is a thin alias for the install case, so a fresh
deployment reads as an install rather than a migration.
"""
from django.core.management.base import BaseCommand
from django.db import connections, transaction

CONN = "enrolment"
SRC = 'enrolment."Enrolment_Users"'
DST = 'enrolment."Created_users"'

# --------------------------------------------------------------------------- #
# 1. the user-creation form's own fields, in the order the form asks for them  #
# --------------------------------------------------------------------------- #
# (form field -> DB column). Kept first in the table so the schema mirrors the
# form. See frontend/src/pages/users/components/CreateUserModal.tsx.
FORM_COLUMNS = [
    # -- learner type switch (foot of the form; decides the kind of learner) --
    ("Learner_type", "text"),          # 'apprenticeship' | 'commercial'
    # -- invitation --
    ("Invite_to_platform", "boolean"),
    # -- identity --
    ("Username", "text"),              # firstName + lastName
    ("Preferred_name", "text"),
    ("Title", "text"),
    ("Gender", "text"),
    ("Email", "text"),
    ("Phone_number", "text"),
    ("Date_of_birth", "text"),
    ("National_insurance_number", "text"),
    # -- referral --
    ("Referrer", "text"),
    ("Referrer_address", "text"),
    ("Referrer_contact", "text"),
    # -- programme (live curriculum cascade) --
    ("Programme", "text"),
    ("Cohort", "text"),
    ("Group", "text"),
    # -- address --
    ("Current_postcode", "text"),
    ("Current_address_line_1", "text"),
    ("Current_address_line_2", "text"),
    ("Current_address_line_3", "text"),   # townCity
    ("Current_address_line_4", "text"),   # county
    ("Country", "text"),
    # -- delivery & employer --
    ("Case_owner", "text"),
    ("Learning_provider", "text"),
    ("Employer", "text"),
    ("Employer_address", "text"),
    ("Line_manager", "text"),
    ("Mentor", "text"),
    ("Orgnization", "text"),             # source spelling, kept for continuity
    ("Reference_number", "text"),
    ("Extended_break", "text"),
    # -- stamped by the form on submit rather than shown as a field --
    ("Type", "text"),                    # always 'User' from this form
    (" Status", "text"),                 # NB leading space; always 'FullUser'
]

# --------------------------------------------------------------------------- #
# 2. operational columns — read/written by the app, never set by the form      #
# --------------------------------------------------------------------------- #
# Dropping any of these turns a page into a 500, so they come across verbatim.
OPERATIONAL_COLUMNS = [
    # programme lifecycle
    ("Programme_status", "text"),
    ("Start_date", "text"),
    ("End_date", "text"),
    ("Practical_period_end_date", "text"),
    ("Apprenticeship_End_date", "text"),
    ("Enrolled_time_and_user", "text"),
    ("Onboarding_status", "text"),
    ("Onboarding_completed", "text"),
    # hours
    ("Minimum_required_hours", "text"),
    ("Planned_hours", "text"),
    ("RPL_Hours", "text"),
    # training plans (apprenticeship uses Learning_plan, commercial Training_plan)
    ("Learning_plan", "text"),
    ("Training_plan", "jsonb"),
    ("Modules", "text"),
    ("Weeks", "text"),
    ("Components", "text"),
    # wizard personal details / statutory ILR capture
    ("Address", "text"),
    ("Age", "text"),
    ("Legal_Sex", "text"),
    ("How_long_have_you_been_at_this_address_(years)?", "text"),
    ("Postcode_prior_to_enrolment", "text"),
    ("What_pronouns_do_you_use?", "text"),
    ("Ethnicity", "text"),
    ("Target_programme", "text"),
    # access flags (columns retained; the create form no longer asks)
    ("Allow_access_to_checkpoint", "boolean"),
    ("Allow_access_to_console", "boolean"),
    ("Allow_access_to_classic", "boolean"),
    # board / compliance JSON blocks
    ("Sub-programme", "json"),
    ("Aims/Qualifications", "json"),
    (" English_Assessments", "json"),      # NB leading space
    ("Maths_Assessments", "json"),
    ("ICT_Assessments", "json"),
    ("English_Exemption_from_Functional_Skills", "json"),
    ("Maths_Exemption_from_Functional_Skills", "json"),
    ("ICT_Exemption_from_Functional_Skills", "json"),
    ("Managed_jobs_and_placements/workshops", "text"),
    ("Reviews", "json"),
    ("Tracker", "json"),
    ("Milestones", "json"),
    ("Contacts", "json"),
    ("Activity", "json"),
    (" Compliance_documents", "json"),     # NB leading space
    (" Review_documents", "json"),         # NB leading space
    ("Documents", "json"),
    ("Competencies", "text"),
    ("Subscription_details", "json"),
    # extended-ILR answer blocks
    ("Do_you_consider_yourself_to_have_a_long_term_disability_,_healt", "json"),
    ("Contact_Preferences", "json"),
    ("Emergency_contact_details ", "json"),  # NB trailing space
    ("Eligibility", "json"),
    ("Other_training", "text"),
    ("Personal_Circumstances", "json"),
    ("Additional_information", "json"),
    ("Media_Consent", "text"),
    ("Declarations_/_consents", "text"),
]

ALL_COLUMNS = FORM_COLUMNS + OPERATIONAL_COLUMNS

# Dropped once the copy is verified. The premerge backups are copies of the two
# originals, so they go with them.
DROP_TABLES = [
    'enrolment."Enrolment_Users"',
    'enrolment."Commercial_users"',
    'enrolment."Enrolment_Users_premerge_bak"',
    'enrolment."Commercial_users_premerge_bak"',
]


class Command(BaseCommand):
    help = 'Create enrolment."Created_users", move every learner into it, and drop the old learner tables.'

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Rehearse everything (including the drops) and roll back.",
        )
        parser.add_argument(
            "--drop-old",
            action="store_true",
            help="Drop the old learner tables after the copy verifies. Without this they are kept.",
        )

    def _columns(self, cur, table):
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='enrolment' AND table_name=%s",
            (table,),
        )
        return {r[0] for r in cur.fetchall()}

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        drop_old = options["drop_old"] or dry_run
        conn = connections[CONN]

        try:
            with transaction.atomic(using=CONN):
                cur = conn.cursor()

                # --- 1. create ---
                # Unconditional, and ahead of the legacy-table check below: this
                # is the only path that creates the core learner table, so a
                # clean database must get it whether or not there is anything to
                # copy. It used to sit after that check, which meant a fresh
                # install returned "nothing to do" and left the table absent.
                cur.execute("CREATE SCHEMA IF NOT EXISTS enrolment")
                col_defs = ",\n                        ".join(
                    f'"{name}" {sql_type}' for name, sql_type in ALL_COLUMNS
                )
                cur.execute(f'''
                    CREATE TABLE IF NOT EXISTS {DST} (
                        id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                        {col_defs}
                    )
                ''')
                self.stdout.write(
                    f"ensured {DST}: {len(FORM_COLUMNS)} form column(s) "
                    f"+ {len(OPERATIONAL_COLUMNS)} operational = {len(ALL_COLUMNS)} (+ id)"
                )

                # --- 2. is there anything to copy? ---
                src_cols = self._columns(cur, "Enrolment_Users")
                if not src_cols:
                    self.stdout.write(self.style.SUCCESS(
                        f"{SRC} does not exist — cutover already done. "
                        "Table ensured; nothing to copy."
                    ))
                    return

                cur.execute(f"SELECT count(*) FROM {SRC}")
                src_count = cur.fetchone()[0]
                self.stdout.write(f"\nsource {SRC}: {src_count} learner(s), {len(src_cols)} columns")

                cur.execute(f"SELECT count(*) FROM {DST}")
                if cur.fetchone()[0]:
                    self.stdout.write(self.style.WARNING(
                        f"{DST} already holds rows — refusing to copy again."
                    ))
                    return

                # Any source column not carried over would be silent data loss, so
                # name them explicitly rather than discovering it later.
                carried = {name for name, _ in ALL_COLUMNS} & src_cols
                dropped = src_cols - carried - {"id"}
                if dropped:
                    self.stdout.write(self.style.WARNING(
                        f"  NOT carried over ({len(dropped)}): {sorted(dropped)}"
                    ))
                else:
                    self.stdout.write("  every source column is carried over")

                # --- 3. copy, preserving ids ---
                ordered = sorted(carried)
                col_list = ", ".join(f'"{c}"' for c in ordered)
                cur.execute(f'''
                    INSERT INTO {DST} (id, {col_list})
                    OVERRIDING SYSTEM VALUE
                    SELECT id, {col_list} FROM {SRC} ORDER BY id
                ''')
                copied = cur.rowcount
                self.stdout.write(f"copied {copied} learner(s) across {len(ordered)} column(s), ids preserved")

                # Advance the identity sequence past the highest copied id, or the
                # next insert would collide with an existing row.
                cur.execute(f'SELECT coalesce(max(id), 0) FROM {DST}')
                max_id = cur.fetchone()[0]
                cur.execute(f"""
                    SELECT pg_catalog.setval(
                        pg_get_serial_sequence('enrolment."Created_users"', 'id'), %s, true
                    )
                """, [max_id])
                self.stdout.write(f"identity sequence set past max id {max_id}")

                # --- 4. verify before anything is destroyed ---
                cur.execute(f"SELECT count(*) FROM {DST}")
                dst_count = cur.fetchone()[0]
                if dst_count != src_count:
                    raise RuntimeError(f"row count mismatch: {src_count} source vs {dst_count} copied")

                cur.execute(f'''
                    SELECT s.id, s."Email", s."Learner_type"
                    FROM {SRC} s
                    FULL OUTER JOIN {DST} d
                      ON d.id = s.id
                     AND coalesce(d."Email",'') = coalesce(s."Email",'')
                     AND coalesce(d."Learner_type",'') = coalesce(s."Learner_type",'')
                    WHERE s.id IS NULL OR d.id IS NULL
                ''')
                mismatched = cur.fetchall()
                if mismatched:
                    raise RuntimeError(f"row-for-row verification failed: {mismatched}")

                cur.execute(f'''SELECT "Learner_type", count(*) FROM {DST} GROUP BY 1 ORDER BY 1''')
                self.stdout.write("\nverified copy:")
                for kind, n in cur.fetchall():
                    self.stdout.write(f"  {kind or '(null)'}: {n}")
                self.stdout.write(self.style.SUCCESS(
                    f"  {dst_count}/{src_count} rows match on (id, Email, Learner_type)"
                ))

                # Satellites must still resolve against the copied ids.
                cur.execute(f'''
                    SELECT count(*) FROM enrolment."Extended_ILR" e
                    WHERE NOT EXISTS (SELECT 1 FROM {DST} d WHERE d.id = e."Learner_id")
                ''')
                orphans = cur.fetchone()[0]
                if orphans:
                    raise RuntimeError(f"{orphans} Extended_ILR row(s) would be orphaned")
                self.stdout.write("  no orphaned satellite rows")

                # --- 5. drop ---
                if drop_old:
                    self.stdout.write("\ndropping old tables:")
                    for table in DROP_TABLES:
                        cur.execute(f"DROP TABLE IF EXISTS {table}")
                        self.stdout.write(f"  dropped {table}")
                else:
                    self.stdout.write(self.style.WARNING(
                        "\nold tables KEPT (pass --drop-old to remove them)"
                    ))

                if dry_run:
                    self.stdout.write(self.style.WARNING("\n--dry-run: rolling back, nothing committed."))
                    transaction.set_rollback(True, using=CONN)
                else:
                    self.stdout.write(self.style.SUCCESS("\nCommitted."))
        except Exception as exc:  # noqa: BLE001 - surface any DB/DDL failure clearly
            self.stderr.write(self.style.ERROR(f"Cutover failed (rolled back): {exc}"))
            raise
