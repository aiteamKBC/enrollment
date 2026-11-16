"""Apply feature migrations to isolated in-memory SQLite, with networking blocked."""
import socket
from django.db import connection, connections
from django.db.migrations.executor import MigrationExecutor
from django.test.runner import DiscoverRunner

def refused(*args, **kwargs):
    raise AssertionError('External network calls are forbidden in suitability tests.')

class SuitabilityRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        if connection.vendor != 'sqlite' or connection.settings_dict['NAME'] != ':memory:':
            raise RuntimeError('Suitability tests require in-memory SQLite.')
        socket.create_connection = refused
        socket.socket.connect = refused
        socket.socket.connect_ex = refused
        connection.features.can_return_columns_from_insert = False
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes('programme_suitability'))
        from learner_api.models import EnrolmentUser, LearnerProfile, StaffUser
        from enrolment_api.models import ExtendedIlr, WizardCvJob, WizardIlrLearnerDetails, WizardKsbAssessment, WizardPersonalDetails, WizardPlr, WizardPlrRecord, WizardPolicyAck, WizardSkillsRadar
        from login.models import LoginAccount, LoginSession
        LoginAccount._meta.db_table = 'Login_accounts'
        LoginSession._meta.db_table = 'Login_sessions'
        with connection.cursor() as cursor:
            for schema in ('enrolment', 'Learner', 'curriculum', 'login'):
                cursor.execute(f"ATTACH DATABASE ':memory:' AS {schema}")
            cursor.execute("CREATE TABLE curriculum.programmes (programme_id TEXT PRIMARY KEY, name TEXT, level TEXT, is_archived BOOLEAN DEFAULT false, is_active BOOLEAN DEFAULT true, deleted_at TEXT, status TEXT DEFAULT 'active')")
            cursor.execute("CREATE TABLE curriculum.cohorts (cohort_id TEXT PRIMARY KEY, cohort_name TEXT, programme_id TEXT, programme_name TEXT, status TEXT DEFAULT 'active', is_active BOOLEAN DEFAULT true, is_archived BOOLEAN DEFAULT false)")
            cursor.execute('CREATE TABLE curriculum.groups (group_id TEXT PRIMARY KEY, group_name TEXT, cohort_id TEXT, cohort_name TEXT, programme_id TEXT, programme_name TEXT)')
            cursor.execute('CREATE TABLE curriculum.modules (module_catalogue_id TEXT PRIMARY KEY, title TEXT, group_name TEXT, programme_id TEXT, programme_name TEXT, total_otjh NUMERIC, start_date TEXT, end_date TEXT, learner_roster_mode TEXT, deleted_at TEXT, is_programme_deleted BOOLEAN DEFAULT 0)')
            cursor.execute("CREATE TABLE enrolment.\"Wizard_Form_Layouts\" (id INTEGER PRIMARY KEY, \"Layout\" TEXT NOT NULL, \"Created_by\" TEXT, \"Created_at\" TEXT, \"Scope\" TEXT DEFAULT 'default', \"Variant_name\" TEXT, \"Parent_version\" INTEGER, \"Archived\" BOOLEAN DEFAULT false)")
            cursor.execute('CREATE TABLE enrolment."Wizard_Layout_Assignments" ("Learner_kind" TEXT NOT NULL, "Learner_id" INTEGER NOT NULL, "Override_version" INTEGER, "Submitted_version" INTEGER, "Pinned_version" INTEGER, PRIMARY KEY ("Learner_kind", "Learner_id"))')
            cursor.execute("CREATE TABLE login.Login_accounts (id INTEGER PRIMARY KEY, Subject_type TEXT, Subject_id INTEGER, Email TEXT, Display_name TEXT, Role TEXT, Password_hash TEXT DEFAULT '', Password_set_at TEXT, Is_active INTEGER DEFAULT 1, Failed_attempts INTEGER DEFAULT 0, Locked_until TEXT, Last_login_at TEXT, Last_login_ip TEXT, Created_at TEXT, Updated_at TEXT)")
            cursor.execute('CREATE TABLE login.Login_sessions (id INTEGER PRIMARY KEY, Account_id INTEGER, Token_hash TEXT UNIQUE, Expires_at TEXT, Revoked_at TEXT, Ip_address TEXT, User_agent TEXT, Created_at TEXT, Last_seen_at TEXT, Remember INTEGER DEFAULT 0)')
        with connection.schema_editor() as schema_editor:
            for model in (EnrolmentUser, LearnerProfile, StaffUser, ExtendedIlr, WizardSkillsRadar, WizardKsbAssessment, WizardPersonalDetails, WizardIlrLearnerDetails, WizardPlr, WizardPlrRecord, WizardCvJob, WizardPolicyAck):
                schema_editor.create_model(model)
        enrolment_connection = connections['enrolment']
        with enrolment_connection.cursor() as cursor:
            for schema in ('enrolment', 'login', 'curriculum'):
                cursor.execute(f"ATTACH DATABASE ':memory:' AS {schema}")
            cursor.execute('CREATE TABLE IF NOT EXISTS curriculum.programmes (programme_id TEXT PRIMARY KEY, name TEXT, level TEXT, is_archived BOOLEAN DEFAULT false, is_active BOOLEAN DEFAULT true, deleted_at TEXT, status TEXT DEFAULT \'active\')')
            cursor.execute('CREATE TABLE IF NOT EXISTS "Login_accounts" (id INTEGER PRIMARY KEY AUTOINCREMENT, "Subject_type" TEXT, "Subject_id" INTEGER, "Email" TEXT, "Display_name" TEXT, "Role" TEXT, "Password_hash" TEXT DEFAULT \'\', "Password_set_at" TEXT, "Is_active" BOOLEAN DEFAULT true, "Failed_attempts" INTEGER DEFAULT 0, "Locked_until" TEXT, "Last_login_at" TEXT, "Last_login_ip" TEXT, "Created_at" TEXT, "Updated_at" TEXT)')
            cursor.execute('CREATE TABLE IF NOT EXISTS "Login_sessions" (id INTEGER PRIMARY KEY AUTOINCREMENT, "Account_id" INTEGER, "Token_hash" TEXT UNIQUE, "Expires_at" TEXT, "Revoked_at" TEXT, "Ip_address" TEXT, "User_agent" TEXT, "Created_at" TEXT, "Last_seen_at" TEXT, "Remember" BOOLEAN DEFAULT false)')
            cursor.execute("CREATE TABLE IF NOT EXISTS curriculum.cohorts (cohort_id TEXT PRIMARY KEY, cohort_name TEXT, programme_id TEXT, programme_name TEXT, status TEXT DEFAULT 'active')")
            cursor.execute('CREATE TABLE IF NOT EXISTS enrolment."Wizard_Form_Layouts" (id INTEGER PRIMARY KEY, "Layout" TEXT NOT NULL, "Created_by" TEXT, "Created_at" TEXT, "Scope" TEXT DEFAULT \'default\', "Variant_name" TEXT, "Parent_version" INTEGER, "Archived" BOOLEAN DEFAULT false)')
            cursor.execute('CREATE TABLE IF NOT EXISTS enrolment."Wizard_Layout_Assignments" ("Learner_kind" TEXT NOT NULL, "Learner_id" INTEGER NOT NULL, "Override_version" INTEGER, "Submitted_version" INTEGER, "Pinned_version" INTEGER, PRIMARY KEY ("Learner_kind", "Learner_id"))')
            cursor.execute('DROP TABLE IF EXISTS enrolment.Wizard_Cv_Job')
            cursor.execute('CREATE TABLE enrolment.Wizard_Cv_Job (id INTEGER PRIMARY KEY AUTOINCREMENT, Learner_kind TEXT, Learner_id INTEGER, Cv_file TEXT, Experience_text TEXT, Pm_qualifications TEXT, Functional_skills_enrol TEXT, Highest_qualification TEXT, Highest_qualification_field TEXT, Has_field_qualification INTEGER, Highest_field_qualification TEXT, Gcse_english INTEGER, Gcse_maths INTEGER, Documents TEXT, Created_at TEXT, Updated_at TEXT)')
            cursor.execute('CREATE TABLE login."Login_accounts" (id INTEGER PRIMARY KEY, "Subject_type" TEXT, "Subject_id" INTEGER, "Email" TEXT, "Display_name" TEXT, "Role" TEXT, "Password_hash" TEXT DEFAULT \'\', "Password_set_at" TEXT, "Is_active" BOOLEAN DEFAULT true, "Failed_attempts" INTEGER DEFAULT 0, "Locked_until" TEXT, "Last_login_at" TEXT, "Last_login_ip" TEXT, "Created_at" TEXT, "Updated_at" TEXT)')
            cursor.execute('CREATE TABLE login."Login_sessions" (id INTEGER PRIMARY KEY, "Account_id" INTEGER, "Token_hash" TEXT UNIQUE, "Expires_at" TEXT, "Revoked_at" TEXT, "Ip_address" TEXT, "User_agent" TEXT, "Created_at" TEXT, "Last_seen_at" TEXT, "Remember" BOOLEAN DEFAULT false)')
        # Match PostgreSQL's schema lookup and keep the real CV manifest table
        # available on the production-routed enrolment alias.
        enrolment_connection.connection.create_function(
            'to_regclass', 1, lambda name: name if enrolment_connection.connection.execute(
                "SELECT name FROM enrolment.sqlite_master WHERE type='table' AND name='Wizard_Form_Layouts'"
            ).fetchone() else None,
        )
        with enrolment_connection.cursor() as cursor:
            cursor.execute("CREATE VIEW pg_attribute AS SELECT 'enrolment.\"Wizard_Form_Layouts\"' AS attrelid, name AS attname, 0 AS attisdropped FROM pragma_table_info('Wizard_Form_Layouts', 'enrolment')")
        return None
    def teardown_databases(self, old_config, **kwargs):
        connection.close()
