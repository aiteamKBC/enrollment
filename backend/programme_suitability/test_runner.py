"""Apply only the feature migration to in-memory SQLite, with networking blocked."""
import socket
from django.db import connection
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
        # SQLite cannot qualify RETURNING columns with an attached schema name.
        connection.features.can_return_columns_from_insert = False
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes('programme_suitability'))
        # Exercise the real unmanaged relationship on the SAME isolated connection.
        from learner_api.models import EnrolmentUser, LearnerProfile
        from enrolment_api.models import ExtendedIlr
        with connection.cursor() as cursor:
            for schema in ('enrolment', 'Learner', 'curriculum'):
                cursor.execute(f"ATTACH DATABASE ':memory:' AS {schema}")
            cursor.execute('CREATE TABLE curriculum.programmes (programme_id TEXT PRIMARY KEY, name TEXT, level TEXT, is_archived BOOLEAN DEFAULT false, is_active BOOLEAN DEFAULT true, deleted_at TEXT, status TEXT DEFAULT \'active\')')
            cursor.execute('CREATE TABLE curriculum.cohorts (cohort_id TEXT PRIMARY KEY, cohort_name TEXT, programme_id TEXT, programme_name TEXT)')
            cursor.execute('CREATE TABLE curriculum.groups (group_id TEXT PRIMARY KEY, group_name TEXT, cohort_id TEXT, cohort_name TEXT, programme_id TEXT, programme_name TEXT)')
        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(EnrolmentUser)
            schema_editor.create_model(LearnerProfile)
            schema_editor.create_model(ExtendedIlr)
        return None

    def teardown_databases(self, old_config, **kwargs):
        connection.close()
