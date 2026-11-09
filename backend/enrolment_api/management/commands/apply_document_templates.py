"""Reviewed, additive setup matching the enrolment schema's existing commands."""
from pathlib import Path
import copy
import hashlib
import os
from urllib.parse import parse_qs, unquote, urlsplit
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction


class Command(BaseCommand):
    help = 'Create document template/version/draft/issuance-history tables without changing existing documents.'
    plan_summary = 'Adds feature indexes and immutable-version/history triggers. No existing learner, answer, source-document, signature or PDF records are rewritten.'
    success_message = 'Document template schema is ready. Existing documents were not modified.'

    def add_arguments(self, parser):
        parser.add_argument('--check', action='store_true')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--with-revisions', action='store_true', help='Also install the additive learner-document revision table and immutable-finalization trigger.')
        parser.add_argument('--plan', action='store_true', help='Print the additive deployment plan without connecting to or changing a database.')
        parser.add_argument('--database-url-env', default='DOCUMENT_TEMPLATE_DATABASE_URL',
                            help='Environment variable containing a direct connection to the reviewed target branch (never pass a password on the command line).')

    def schema_plan(self, options):
        names = ('Document_Template_Versions', 'Document_Template_Drafts', 'Document_Template_Issues', 'Document_Template_Render_History')
        sql = (Path(__file__).parents[2] / 'document_templates' / 'schema.sql').read_text(encoding='utf-8')
        if options['with_revisions']:
            names += ('Learner_Document_Revisions',)
            sql += '\n' + (Path(__file__).parents[2] / 'document_templates' / 'revision_schema.sql').read_text(encoding='utf-8')
        return names, sql

    def check_storage(self, connection):
        pass

    def handle(self, *args, **options):
        names, sql = self.schema_plan(options)
        if options['plan']:
            if options['check'] or options['dry_run']: raise CommandError('Use --plan separately from --check or --dry-run.')
            self.stdout.write('Additive document storage setup; tables in dependency order:')
            for name in names: self.stdout.write(f'  enrolment."{name}"')
            self.stdout.write(self.plan_summary)
            self.stdout.write('Test on an isolated database branch first. Shared/production deployment requires explicit approval and a direct (non-pooled) connection.')
            self.stdout.write('SQL SHA256: ' + hashlib.sha256(sql.encode()).hexdigest())
            return
        alias = 'enrolment'
        supplied = os.environ.get(options['database_url_env'])
        if supplied:
            url = urlsplit(supplied)
            if url.scheme not in ('postgres', 'postgresql') or not url.hostname:
                raise CommandError('The migration connection environment variable must contain a PostgreSQL URL.')
            from django.db.backends.postgresql.base import DatabaseWrapper
            configuration = copy.deepcopy(connections['enrolment'].settings_dict)
            configuration.update(NAME=unquote(url.path.lstrip('/')), USER=unquote(url.username or ''), PASSWORD=unquote(url.password or ''),
                                 HOST=url.hostname, PORT=str(url.port or 5432), CONN_MAX_AGE=0)
            configuration['OPTIONS'] = {k: v[-1] for k, v in parse_qs(url.query).items() if k in {'sslmode', 'channel_binding', 'connect_timeout', 'sslrootcert'}}
            alias = 'document_template_migration'
            connections[alias] = DatabaseWrapper(configuration, alias)
        connection = connections[alias]
        if connection.vendor != 'postgresql':
            raise CommandError('This deployment command requires the existing enrolment PostgreSQL database.')
        if '-pooler' in str(connection.settings_dict.get('HOST', '')) and not options['check']:
            raise CommandError('Schema changes require a direct connection to the reviewed database branch. Set DOCUMENT_TEMPLATE_DATABASE_URL (or --database-url-env). The application .env is left unchanged.')
        missing = []
        with connection.cursor() as cursor:
            for name in names:
                cursor.execute('SELECT to_regclass(%s)', [f'enrolment."{name}"'])
                found = bool(cursor.fetchone()[0])
                if not found: missing.append(name)
                self.stdout.write(f'{name}: {"present" if found else "missing"}')
        if options['check']:
            if not missing: self.check_storage(connection)
            if supplied: connection.close()
            if missing: raise CommandError('Required document storage is missing. Review --with-revisions --plan, then deploy to the approved direct database connection. No schema changes were made by --check.')
            return
        try:
            with transaction.atomic(using=alias):
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '5s'")
                    cursor.execute(sql)
                if options['dry_run']:
                    transaction.set_rollback(True, using=alias)
        finally:
            if supplied: connection.close()
        self.stdout.write(self.style.SUCCESS('Dry run rolled back.' if options['dry_run'] else self.success_message))
