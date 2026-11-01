"""Repair missing initial PCP rule fields with an append-only publication."""
import copy
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction

from enrolment_api import wizard_layout as wizard
from programme_suitability.modules_configuration import KEY, defaults, validate
from programme_suitability.modules_rules import available_modules


def repaired_configuration(source, restore_mandatory_groups=False):
    config = copy.deepcopy(source)
    initial = {p['id']: p for p in next(p for p in defaults()['programmes'] if p['id'] == 'PCP_L6')['pathways']}
    paths = next(p for p in config['programmes'] if p['id'] == 'PCP_L6')['pathways']
    for path in paths:
        if not path.get('active', True):
            continue
        for field in ('requiredOptionalModulesCount', 'targetCredits'):
            if path.get(field) is None:
                if path['id'] not in initial:
                    raise CommandError('Configure the custom pathway requirements in the wizard builder first.')
                path[field] = initial[path['id']][field]
        if restore_mandatory_groups:
            def restore(items):
                for item in items:
                    children = item.get('items', [])
                    restore(children)
                    if children and not item.get('selectable') and not item.get('active', True) and any(m.get('isMandatory') for m in available_modules(children)):
                        item['active'] = True
            restore(path['modules'])
    config['schemaVersion'] = 2
    validate(config, publishing=True)
    return config


class Command(BaseCommand):
    help = 'Preview a targeted repair of missing PCP counts/targets; --apply appends a new wizard version.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--base-version', type=int)
        parser.add_argument('--restore-mandatory-groups', action='store_true')

    def handle(self, *args, **options):
        with transaction.atomic(using=wizard.CONN), connections[wizard.CONN].cursor() as cursor:
            if options['apply']:
                cursor.execute(f'LOCK TABLE enrolment."{wizard.LAYOUT_TABLE}" IN SHARE ROW EXCLUSIVE MODE')
            current = wizard.read_current(cursor)
            if not current or not current[1].get(KEY):
                raise CommandError('No programme modules configuration is published.')
            version, source = current[:2]
            if options['apply'] and options['base_version'] != version:
                raise CommandError('Published version changed; rerun the preview before applying.')
            fixed = repaired_configuration(source[KEY], options['restore_mandatory_groups'])
            changes = []
            before_paths = next(p for p in source[KEY]['programmes'] if p['id'] == 'PCP_L6')['pathways']
            for path in next(p for p in fixed['programmes'] if p['id'] == 'PCP_L6')['pathways']:
                before = next(p for p in before_paths if p['id'] == path['id'])
                for field in ('requiredOptionalModulesCount', 'targetCredits'):
                    if before.get(field) != path.get(field):
                        changes.append({'pathway': path['id'], 'field': field, 'from': before.get(field), 'to': path[field]})
                def groups(items, prior):
                    for item in items:
                        old = next(m for m in prior if m['id'] == item['id'])
                        if item.get('active') != old.get('active'):
                            changes.append({'pathway': path['id'], 'module': item['id'], 'field': 'active', 'from': old.get('active'), 'to': item.get('active')})
                        groups(item.get('items', []), old.get('items', []))
                groups(path['modules'], before['modules'])
            self.stdout.write(json.dumps({'baseVersion': version, 'changes': changes, 'schemaVersion': fixed['schemaVersion']}))
            if not options['apply'] or fixed == source[KEY]:
                return
            layout, plan = wizard.normalize_layout({**source, KEY: fixed}, source)
            wizard.apply_plan(cursor, plan)
            cursor.execute(f'INSERT INTO enrolment."{wizard.LAYOUT_TABLE}" ("Layout", "Created_by") VALUES (%s::jsonb, %s) RETURNING id',
                           [json.dumps(layout), 'PCP missing-rule repair'])
            self.stdout.write(f'Published repaired wizard version {cursor.fetchone()[0]}; previous versions and learner selections retained.')
