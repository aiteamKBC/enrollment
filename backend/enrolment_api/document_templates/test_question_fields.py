import copy
import json
from pathlib import Path
from django.test import SimpleTestCase
from .registry import TemplateError, seed, validate, legacy_equivalent
from .question_fields import legacy_fields, preserve_system_fields


def question(identifier='custom', **overrides):
    return {'id': identifier, 'label': 'Configured question', 'type': 'single_line', 'helpText': '', 'required': False,
            'condition': None, 'options': [], 'value': '', 'defaultOptionIds': [], 'systemLinked': False, 'binding': None, **overrides}


class QuestionConfigurationTests(SimpleTestCase):
    def native(self):
        content = seed('review-eligibility')
        block = next(b for b in content['blocks'] if b.get('source') == 'review:extendedIlr')
        return content, block

    def test_existing_question_types_wording_bindings_and_order_are_adapted_without_mutation(self):
        content, block = self.native(); before = copy.deepcopy(content)
        fields = legacy_fields(block, content['documentKey'])
        self.assertEqual([f['binding']['key'] for f in fields], list(block['labels']))
        self.assertEqual([f['label'] for f in fields], list(block['labels'].values()))
        self.assertEqual(fields[0]['type'], 'radio'); self.assertEqual(fields[2]['type'], 'multi_line')
        self.assertTrue(all(f['systemLinked'] for f in fields))
        self.assertEqual(content, before); self.assertTrue(legacy_equivalent(validate(content)))

    def test_reordered_fields_keep_their_saved_answer_ids_and_support_help_and_tokens(self):
        content, block = self.native(); block['fields'] = legacy_fields(block, content['documentKey'])
        original = copy.deepcopy(block['fields']); block['fields'].reverse()
        block['fields'][0]['label'] = 'Question for {{learner.fullName}}'; block['fields'][0]['helpText'] = 'Programme: {{programme.name}}'
        result = validate(content, publishing=True)
        after = next(b['fields'] for b in result['blocks'] if b['id'] == block['id'])
        self.assertEqual([f['id'] for f in after], [f['id'] for f in reversed(original)])
        self.assertEqual([f['binding'] for f in after], [f['binding'] for f in reversed(original)])
        preserve_system_fields(result, seed(content['documentKey']))

    def test_scalar_sections_can_be_grouped_without_changing_ids_or_data_paths(self):
        content = seed('ilr'); before = copy.deepcopy(content)
        index = next(i for i, b in enumerate(content['blocks']) if b['type'] == 'field')
        old = content['blocks'][index:index + 2]
        fields = [f for b in old for f in legacy_fields(b, 'ilr')]
        content['blocks'][index:index + 2] = [{'id': old[0]['id'], 'type': 'questions', 'fields': list(reversed(fields))}]
        result = validate(content, publishing=True); preserve_system_fields(result, before)
        self.assertEqual(result['blocks'][index]['fields'][0]['id'], old[1]['id'])

    def test_system_type_source_flags_choices_and_workflow_settings_cannot_be_forged(self):
        for name, value in [('type', 'number'), ('binding', {'kind': 'value', 'value': '{{learner.fullName}}'}),
                            ('systemLinked', False), ('required', True), ('condition', {'field': 'x', 'values': ['No']}),
                            ('options', [{'id': 'Yes', 'label': 'Changed stored choice'}]), ('defaultOptionIds', ['Yes'])]:
            content, block = self.native(); block['fields'] = legacy_fields(block, content['documentKey'])
            block['fields'][0][name] = value
            with self.subTest(setting=name), self.assertRaises(TemplateError): validate(content)

    def test_legacy_scalar_binding_edits_survive_card_upgrade_but_new_rebinding_is_rejected(self):
        previous = seed('ilr'); index = next(i for i, b in enumerate(previous['blocks']) if b['type'] == 'field')
        previous['blocks'][index]['value'] = '{{learner.fullName}}'
        field = legacy_fields(previous['blocks'][index], 'ilr')[0]
        upgraded = copy.deepcopy(previous); upgraded['blocks'][index] = {'id': previous['blocks'][index]['id'], 'type': 'questions', 'fields': [field]}
        valid = validate(upgraded); preserve_system_fields(valid, previous)
        field['binding']['value'] = '{{programme.name}}'
        valid = validate(upgraded)
        with self.assertRaises(TemplateError): preserve_system_fields(valid, previous)

    def test_legacy_presentation_repeats_are_not_corrupted_or_given_duplicate_card_ids(self):
        content = seed('apprenticeship-agreement'); block = next(b for b in content['blocks'] if b['type'] == 'table')
        block['rows'].append(copy.deepcopy(block['rows'][0])); before = copy.deepcopy(content)
        validate(content)
        fields = legacy_fields(block, content['documentKey'])
        self.assertEqual(len({f['id'] for f in fields}), len(fields))
        self.assertEqual(fields[-1]['binding'], fields[0]['binding'])
        self.assertEqual(content, before)
        block['fields'] = fields; validate(content)

    def test_system_deletion_and_duplication_are_rejected(self):
        content, block = self.native(); block['fields'] = legacy_fields(block, content['documentKey'])
        original = copy.deepcopy(block['fields']); block['fields'].pop()
        with self.assertRaises(TemplateError): validate(content)
        block['fields'] = original + [copy.deepcopy(original[0])]
        with self.assertRaises(TemplateError): validate(content)
        content, block = self.native(); content['blocks'].remove(block)
        with self.assertRaises(TemplateError): preserve_system_fields(content, seed(content['documentKey']))

    def test_every_supported_custom_type_can_be_saved_and_published(self):
        for kind in ('single_line', 'multi_line', 'number', 'heading', 'description', 'divider', 'dropdown', 'radio', 'checkbox'):
            content = seed('ilr'); field = question(type=kind, value='42' if kind == 'number' else '')
            if kind in ('dropdown', 'radio', 'checkbox'): field['options'] = [{'id': 'option-a', 'label': 'First option'}]
            content['blocks'].append({'id': 'custom-section', 'type': 'questions', 'fields': [field]})
            with self.subTest(kind=kind): self.assertEqual(validate(content, publishing=True)['blocks'][-1]['fields'][0]['type'], kind)

    def test_invalid_options_defaults_and_unsupported_upload_cannot_bypass_ui(self):
        fields = [question(type='upload'), question(type='radio', options=[]),
                  question(type='radio', options=[{'id': 'a', 'label': ''}]),
                  question(type='radio', options=[{'id': 'a', 'label': 'Same'}, {'id': 'b', 'label': 'same'}]),
                  question(type='radio', options=[{'id': 'a', 'label': 'One'}], defaultOptionIds=['missing']),
                  question(type='number', value='forty'), question(type='number', value='42', max=10),
                  question(type='number', value='{{learner.fullName}}'), question(type='heading', required=True)]
        for field in fields:
            content = seed('ilr'); content['blocks'].append({'id': 'custom-section', 'type': 'questions', 'fields': [field]})
            with self.subTest(field=field), self.assertRaises(TemplateError): validate(content)

    def test_conditions_use_stable_preceding_field_and_option_ids(self):
        first = question('source', type='radio', options=[{'id': 'yes', 'label': 'Yes'}, {'id': 'no', 'label': 'No'}], defaultOptionIds=['no'])
        second = question('follow', condition={'field': 'source', 'values': ['no']}, value='Supporting text')
        content = seed('ilr'); content['blocks'].append({'id': 'custom-section', 'type': 'questions', 'fields': [first, second]})
        before = validate(content)['blocks'][-1]['fields'][1]['condition']
        first['options'][1]['label'] = 'Updated label'
        self.assertEqual(validate(content)['blocks'][-1]['fields'][1]['condition'], before)
        content['blocks'][-1]['fields'].reverse()
        with self.assertRaises(TemplateError): validate(content)

    def test_deleted_controllers_or_options_require_correction(self):
        content = seed('ilr')
        first = question('source', type='dropdown', options=[{'id': 'a', 'label': 'A'}])
        second = question('follow', condition={'field': 'source', 'values': ['a']})
        content['blocks'].append({'id': 'custom-section', 'type': 'questions', 'fields': [first, second]})
        first['options'] = [{'id': 'b', 'label': 'B'}]
        with self.assertRaises(TemplateError): validate(content)
        content['blocks'][-1]['fields'] = [second]
        with self.assertRaises(TemplateError): validate(content)

    def test_required_configured_values_are_enforced_at_publication(self):
        content = seed('ilr'); field = question(required=True)
        content['blocks'].append({'id': 'custom-section', 'type': 'questions', 'fields': [field]})
        validate(content)
        with self.assertRaises(TemplateError): validate(content, publishing=True)
        field['value'] = 'Approved value'; validate(content, publishing=True)

    def test_catalogues_match_and_reject_unknown_dynamic_references_in_help(self):
        root = Path(__file__).resolve().parents[3]
        self.assertEqual(json.loads(Path(__file__).with_name('question_types.json').read_text()),
            json.loads((root / 'frontend/src/features/compliance-documents/question_types.json').read_text()))
        content = seed('ilr'); content['blocks'].append({'id': 'custom-section', 'type': 'questions', 'fields': [question(helpText='{{unknown.secret}}')]})
        with self.assertRaises(TemplateError): validate(content)
