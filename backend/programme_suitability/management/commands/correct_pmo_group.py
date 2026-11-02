"""Append a narrowly scoped GROUP correction; never rewrite historical records."""
import copy
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction

from enrolment_api import wizard_layout as wizard
from programme_suitability.module_items import corrected_pmo_group
from programme_suitability.modules_configuration import KEY, validate


class Command(BaseCommand):
    help = 'Preview the certified-pmo GROUP correction; --apply requires the current --base-version.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--base-version', type=int)

    def handle(self, *args, **options):
        with transaction.atomic(using=wizard.CONN), connections[wizard.CONN].cursor() as cursor:
            if options['apply']:
                cursor.execute(f'LOCK TABLE enrolment."{wizard.LAYOUT_TABLE}" IN SHARE ROW EXCLUSIVE MODE')
            current = wizard.read_current(cursor)
            if not current or not current[1].get(KEY):
                raise CommandError('No programme modules configuration is published.')
            version, source = current[:2]
            if options['apply'] and options['base_version'] != version:
                raise CommandError('Published version changed; preview the correction again.')
            fixed = corrected_pmo_group(source[KEY])
            validate(fixed, publishing=True)
            changed = fixed != source[KEY]
            self.stdout.write(json.dumps({'baseVersion': version, 'changed': changed,
                'groupId': 'certified-pmo', 'itemType': 'GROUP'}))
            if not options['apply'] or not changed:
                return
            layout = copy.deepcopy(source)
            layout[KEY] = fixed
            cursor.execute(f'INSERT INTO enrolment."{wizard.LAYOUT_TABLE}" ("Layout", "Created_by") VALUES (%s::jsonb, %s) RETURNING id',
                           [json.dumps(layout), 'Certified PMO group correction'])
            self.stdout.write(f'Published wizard version {cursor.fetchone()[0]}; historical versions and learner data retained.')
