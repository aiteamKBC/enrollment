import copy
from decimal import Decimal
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from .configuration import ConfigurationError
from .module_items import corrected_pmo_group
from .modules_configuration import KEY, defaults, integrate_layout, validate
from .modules_rules import available_modules, credits
from .modules_service import validate_snapshot_choices


class ModuleGroupTests(SimpleTestCase):
    def setUp(self):
        self.source = defaults()
        self.config = validate(self.source)
        self.path = self.config['programmes'][3]['pathways'][2]
        self.group = self.path['modules'][0]

    def choice(self, ids):
        return {'programme': 'PCP_L6', 'pathway': 'chartered', 'configurationVersion': 10,
                'modulesByPathway': {'chartered': ids}}

    def test_parent_is_only_metadata_children_remain_mandatory_and_total_six(self):
        self.assertEqual(self.group['itemType'], 'GROUP')
        self.assertFalse({'sessions', 'credits', 'selectable', 'isMandatory'} & self.group.keys())
        self.assertEqual(len(self.group['items']), 4)
        self.assertTrue(all(m['isMandatory'] and m['credits'] == 1 for m in self.group['items']))
        self.assertEqual(credits(self.group['items']), Decimal(4))
        self.assertEqual(credits([self.group]), Decimal(0))
        self.assertTrue(self.path['modules'][1]['isMandatory'])
        modules = available_modules(self.path['modules'])
        for optional in [m for m in modules if not m['isMandatory']]:
            saved = validate_snapshot_choices(self.choice([optional['id']]), 10, self.config, complete=True)
            ids = saved['modulesByPathway']['chartered']
            self.assertEqual(len(ids), 6)
            self.assertEqual(credits([m for m in modules if m['id'] in ids]), Decimal(6))
        for ids in [[], [m['id'] for m in modules if not m['isMandatory']], ['certified-pmo']]:
            with self.assertRaises(ConfigurationError):
                validate_snapshot_choices(self.choice(ids), 10, self.config, complete=True)

    def test_explicit_group_rejects_module_fields_and_module_rejects_children(self):
        for field, value in [('sessions', 0), ('credits', 0), ('selectable', True), ('isMandatory', True)]:
            config = copy.deepcopy(self.source)
            config['programmes'][3]['pathways'][2]['modules'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ConfigurationError):
                validate(config)
        config = copy.deepcopy(self.source)
        config['programmes'][3]['pathways'][2]['modules'][0]['itemType'] = 'MODULE'
        with self.assertRaises(ConfigurationError): validate(config)

    def test_legacy_group_is_never_selectable_or_counted_even_if_old_flags_are_wrong(self):
        self.group.pop('itemType')
        self.group.update(selectable=True, isMandatory=True, credits=99, sessions=99)
        self.assertNotIn('certified-pmo', [m['id'] for m in available_modules(self.path['modules'])])
        self.assertEqual(credits([self.group]), Decimal(0))
        with self.assertRaises(ConfigurationError):
            validate_snapshot_choices(self.choice(['certified-pmo']), 10, self.config, complete=True)

    def test_correction_and_publication_preserve_ids_children_other_paths_and_history(self):
        old = copy.deepcopy(self.source)
        group = old['programmes'][3]['pathways'][2]['modules'][0]
        group.pop('itemType'); group.update(selectable=True, credits=99, sessions=99, isMandatory=True)
        original = copy.deepcopy(old)
        fixed = corrected_pmo_group(old)
        self.assertEqual(old, original)
        self.assertEqual(fixed['programmes'][:3], old['programmes'][:3])
        self.assertEqual(fixed['programmes'][3]['pathways'][:2], old['programmes'][3]['pathways'][:2])
        parent = fixed['programmes'][3]['pathways'][2]['modules'][0]
        self.assertEqual(parent['items'], group['items'])
        self.assertEqual(parent['id'], group['id'])
        self.assertEqual(corrected_pmo_group(fixed), fixed)
        layout = {}; integrate_layout({KEY: fixed}, {KEY: old}, layout)
        self.assertEqual(layout[KEY], validate(self.source))
        self.assertEqual(old, original)

    def test_group_name_order_active_and_children_are_independently_editable(self):
        config = copy.deepcopy(self.source)
        group = config['programmes'][3]['pathways'][2]['modules'][0]
        group.update(title='Renamed group', order=3, description='Description')
        group['items'][0].update(title='Renamed child', sessions=8)
        saved = validate(config)['programmes'][3]['pathways'][2]['modules'][0]
        self.assertEqual(saved['title'], 'Renamed group')
        self.assertEqual(saved['items'][0]['sessions'], 8)
        self.assertTrue(saved['items'][0]['isMandatory'])

    def test_initial_hierarchy_and_saved_selection_survive_configuration_round_trip(self):
        group = next(m for m in self.path['modules'] if m['id'] == 'certified-pmo')
        self.assertEqual({m['id'] for m in group['items']},
                         {'pmo-planning-control', 'pmo-stakeholders', 'pmo-risk-quality', 'pmo-office'})
        roots = {m['id']: m for m in self.path['modules']}
        self.assertEqual(set(roots), {'certified-pmo', 'ai-project-management', 'portfolio', 'earned-value'})
        self.assertTrue(roots['ai-project-management']['isMandatory'])
        self.assertFalse(roots['portfolio']['isMandatory'])
        self.assertFalse(roots['earned-value']['isMandatory'])
        ids = [m['id'] for m in available_modules(self.path['modules']) if m['isMandatory']] + ['portfolio']
        stored = self.choice(ids); original = copy.deepcopy(stored)
        layout = {}; integrate_layout({KEY: self.source}, {KEY: self.source}, layout)
        self.assertEqual(validate_snapshot_choices(stored, 10, layout[KEY], complete=True), stored)
        self.assertEqual(stored, original)

    def test_publication_allows_reparenting_without_retaining_duplicate_module_ids(self):
        current = copy.deepcopy(self.source)
        path = current['programmes'][3]['pathways'][2]
        group = next(m for m in path['modules'] if m['id'] == 'certified-pmo')
        child = group['items'].pop(0)
        path['modules'].append(child)
        portfolio = next(m for m in path['modules'] if m['id'] == 'portfolio')
        path['modules'].remove(portfolio); group['items'].append(portfolio)
        previous = copy.deepcopy(self.source)
        layout = {}; integrate_layout({KEY: current}, {KEY: previous}, layout)
        self.assertEqual(layout[KEY], validate(current))
        self.assertEqual(previous, self.source)
        restored = {}; integrate_layout({KEY: self.source}, layout, restored)
        self.assertEqual(restored[KEY], self.config)
        self.assertEqual(restored[KEY]['programmes'][3]['pathways'][:2], self.config['programmes'][3]['pathways'][:2])

    def test_removed_group_does_not_duplicate_children_moved_to_pathway_level(self):
        current = copy.deepcopy(self.source)
        path = current['programmes'][3]['pathways'][2]
        group = path['modules'].pop(0)
        path['modules'].extend(group['items'])
        layout = {}; integrate_layout({KEY: current}, {KEY: self.source}, layout)
        path = layout[KEY]['programmes'][3]['pathways'][2]
        retained = next(m for m in path['modules'] if m['id'] == 'certified-pmo')
        self.assertFalse(retained['active'])
        self.assertEqual(retained['items'], [])
        self.assertEqual(len(available_modules(path['modules'])), 7)

    def test_correction_command_is_append_only_version_guarded_and_idempotent(self):
        from .management.commands import correct_pmo_group as command
        source = {'steps': [{'slug': 'cv-job', 'items': []}], KEY: copy.deepcopy(self.source)}
        source[KEY]['programmes'][3]['pathways'][2]['modules'][0].pop('itemType')
        cursor = MagicMock(); cursor.fetchone.return_value = (11,)
        connection = MagicMock(); connection.cursor.return_value.__enter__.return_value = cursor
        with patch.object(command, 'connections', {command.wizard.CONN: connection}), \
             patch.object(command.transaction, 'atomic'), \
             patch.object(command.wizard, 'read_current', return_value=(10, source)):
            call_command('correct_pmo_group', stdout=StringIO())
            cursor.execute.assert_not_called()
            with self.assertRaises(CommandError):
                call_command('correct_pmo_group', apply=True, base_version=9, stdout=StringIO())
            cursor.reset_mock()
            call_command('correct_pmo_group', apply=True, base_version=10, stdout=StringIO())
            inserts = [c for c in cursor.execute.call_args_list if 'INSERT INTO' in c.args[0]]
            self.assertEqual(len(inserts), 1)
            import json
            appended = json.loads(inserts[0].args[1][0])
            self.assertEqual(appended, {**source, KEY: corrected_pmo_group(source[KEY])})
            with patch.object(command.wizard, 'read_current', return_value=(11, appended)):
                cursor.reset_mock()
                call_command('correct_pmo_group', apply=True, base_version=11, stdout=StringIO())
                self.assertFalse(any('INSERT INTO' in c.args[0] for c in cursor.execute.call_args_list))
