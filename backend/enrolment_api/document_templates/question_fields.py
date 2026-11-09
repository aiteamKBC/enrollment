"""Presentation configuration over existing document bindings; no answer store.

Old snapshots are adapted on read by the editor, never rewritten in storage.
"""
import copy
import json
import math
import re
from pathlib import Path
from enrolment_api.wizard_layout import FIELD_TYPES, CONTENT_FIELD_TYPES, CHOICE_FIELD_TYPES

TYPES = FIELD_TYPES - {'upload'}
CHOICES = {'dropdown', *CHOICE_FIELD_TYPES}
CONTENT = CONTENT_FIELD_TYPES
IDENTIFIER = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$')
QUESTION_TYPES = json.loads(Path(__file__).with_name('question_types.json').read_text(encoding='utf-8'))


def legacy_fields(block, key):
    from .registry import TOKEN, seeds
    if 'fields' in block: return copy.deepcopy(block['fields'])
    pairs = []
    if block.get('type') == 'native' and block.get('labels'):
        for name, label in block['labels'].items():
            source = block['source']
            kind = 'review' if source.startswith('review:') else 'cost'
            field_type = QUESTION_TYPES.get(source, {}).get(name, 'radio' if kind == 'review' else 'number')
            pairs.append((name, label, field_type, {'kind': kind, 'source': source, 'key': name}))
    elif block.get('type') == 'field':
        value = block.get('value', '')
        refs = TOKEN.findall(value)
        fmt = seeds()['fields'][key].get(refs[0], {}).get('format') if refs else None
        field_type = 'radio' if fmt == 'yesno' else 'number' if fmt in {'number', 'hours', 'weeks'} else 'single_line'
        pairs.append(('', block.get('label', ''), field_type, {'kind': 'value', 'value': value} if refs else None))
    elif block.get('type') == 'table' and len(block.get('columns', [])) == 2 and block.get('rows') and all(TOKEN.search(row[1]) for row in block['rows']):
        original = next((b for b in seeds()['templates'][key]['blocks'] if b['id'] == block['id']), {})
        used = set()
        for index, row in enumerate(block['rows']):
            source_index = next((i for i, old in enumerate(original.get('rows', [])) if old[1] == row[1]), index)
            suffix = f'row-{source_index}'
            if suffix in used: suffix += f'-repeat-{index}'
            used.add(suffix)
            refs = TOKEN.findall(row[1]); fmt = seeds()['fields'][key].get(refs[0], {}).get('format') if refs else None
            field_type = 'radio' if fmt == 'yesno' else 'number' if fmt in {'number', 'hours', 'weeks'} else 'single_line'
            pairs.append((suffix, row[0], field_type, {'kind': 'value', 'value': row[1]}))
    return [{'id': block['id'] + ('--' + name if name else ''), 'label': label, 'type': field_type,
             'helpText': '', 'required': False, 'condition': None, 'options': [{'id': v, 'label': v} for v in ('Yes', 'No')] if field_type == 'radio' else [],
             'value': block.get('value', '') if not binding else '', 'defaultOptionIds': [],
             'systemLinked': bool(binding), 'binding': binding}
            for name, label, field_type, binding in pairs]


def validate_fields(raw, expected, key, *, publishing=False):
    from .registry import TemplateError, check_tokens, text
    if not isinstance(raw, list) or len(raw) > 250: raise TemplateError('A section supports at most 250 field cards.')
    protected = {f['id']: f for f in expected if f['systemLinked']}
    result, seen = [], {}
    for field in raw:
        if not isinstance(field, dict) or not isinstance(field.get('id'), str) or not IDENTIFIER.fullmatch(field['id']) or field['id'] in seen:
            raise TemplateError('Field cards require unique stable identifiers.')
        identifier = field['id']; original = protected.get(identifier)
        kind = field.get('type')
        if not isinstance(kind, str) or kind not in TYPES: raise TemplateError('Unsupported document field type. File upload uses the existing Uploaded Documents workflow.')
        if type(field.get('systemLinked')) is not bool or type(field.get('required')) is not bool:
            raise TemplateError('Field flags must be boolean.')
        if original:
            for name in ('binding', 'type', 'systemLinked', 'required', 'condition', 'options', 'defaultOptionIds', 'value'):
                if field.get(name) != original[name]: raise TemplateError('This field is system-linked. Its source, type, answer choices and workflow settings are fixed.')
        elif field['systemLinked'] or field.get('binding') is not None:
            raise TemplateError('Custom fields cannot invent or replace system bindings. Use permitted placeholders in the configured value.')
        label = text(field.get('label'), 'Question / Label', empty=kind == 'divider', maximum=4000)
        help_text = text(field.get('helpText', ''), 'Help text', empty=True, maximum=8000)
        value = text(field.get('value', ''), 'Configured document value', empty=True)
        options = field.get('options', [])
        if not isinstance(options, list) or len(options) > 100: raise TemplateError('A choice field supports up to 100 options.')
        clean_options, ids, labels = [], set(), set()
        for option in options:
            if not isinstance(option, dict) or not isinstance(option.get('id'), str) or not IDENTIFIER.fullmatch(option['id']) or option['id'] in ids:
                raise TemplateError('Options require unique stable identifiers.')
            option_label = text(option.get('label'), 'Option label', maximum=1000)
            if option_label.strip().casefold() in labels: raise TemplateError('Choice labels must not be duplicated.')
            ids.add(option['id']); labels.add(option_label.strip().casefold()); clean_options.append({'id': option['id'], 'label': option_label})
        defaults = field.get('defaultOptionIds', [])
        if not isinstance(defaults, list) or any(not isinstance(v, str) or v not in ids for v in defaults) or len(defaults) != len(set(defaults)) or (kind != 'checkbox' and len(defaults) > 1):
            raise TemplateError('Default selections must reference valid option identifiers.')
        if kind in CHOICES:
            if not options: raise TemplateError('Add at least one non-empty choice.')
        elif options or defaults: raise TemplateError('Only choice fields support options and default selections.')
        condition = field.get('condition')
        if condition is not None:
            if not isinstance(condition, dict) or not isinstance(condition.get('field'), str) or condition['field'] not in seen: raise TemplateError('Conditions must reference a preceding field in the same section; forward and circular references are not supported.')
            controller = seen[condition['field']]; values = condition.get('values')
            allowed = {o['id'] for o in controller['options']}
            if not allowed or not isinstance(values, list) or not values or any(not isinstance(v, str) or v not in allowed for v in values) or len(values) != len(set(values)):
                raise TemplateError('Conditional values must reference existing choice identifiers. Correct the rule after changing its controlling field.')
            condition = {'field': condition['field'], 'values': values}
        clean = {'id': identifier, 'label': label, 'type': kind, 'helpText': help_text, 'required': field['required'],
                 'condition': condition, 'options': clean_options, 'value': value, 'defaultOptionIds': defaults,
                 'systemLinked': bool(original), 'binding': copy.deepcopy(original['binding']) if original else None}
        if not original and kind in CONTENT and field['required']: raise TemplateError('Presentation content does not require an answer.')
        for bound in ('min', 'max'):
            if field.get(bound) is not None:
                n = field[bound]
                if original or kind != 'number' or type(n) not in (int, float) or not math.isfinite(n): raise TemplateError('Numeric limits require a custom number field and finite values.')
                clean[bound] = n
        if clean.get('min', -math.inf) > clean.get('max', math.inf): raise TemplateError('Minimum must not exceed maximum.')
        if kind == 'number' and not original and value and '{{' not in value:
            try: number = float(value)
            except ValueError as exc: raise TemplateError('Configured number must be numeric.') from exc
            if not math.isfinite(number) or not clean.get('min', -math.inf) <= number <= clean.get('max', math.inf): raise TemplateError('Configured number is outside its permitted range.')
        if kind == 'number' and not original and '{{' in value:
            from .registry import TOKEN, seeds
            match = TOKEN.fullmatch(value)
            if not match or seeds()['fields'][key].get(match.group(1), {}).get('format') not in {'number', 'hours', 'weeks'}:
                raise TemplateError('Number fields require a number or a single existing numeric document placeholder.')
            if clean.get('min') is not None or clean.get('max') is not None:
                raise TemplateError('Numeric limits validate configured constant values. Existing numeric source data is managed by its original workflow.')
        if publishing and not original and clean['required'] and not (defaults if kind in CHOICES else value.strip()):
            raise TemplateError('Provide a configured document value/default for mandatory custom fields before publication. The document builder does not collect new learner answers.')
        for v in (label, help_text, value, *(o['label'] for o in clean_options)): check_tokens(v, key)
        result.append(clean); seen[identifier] = clean
    if not set(protected) <= set(seen): raise TemplateError('Required system-linked fields cannot be deleted. Their source answers and calculations must remain intact.')
    return result


def system_bindings(content):
    return {f['id']: f['binding'] for b in content['blocks'] for f in legacy_fields(b, content['documentKey']) if f['systemLinked']}


def preserve_system_fields(content, previous):
    from .registry import TemplateError
    before, after = system_bindings(previous), system_bindings(content)
    if any(after.get(identifier) != binding for identifier, binding in before.items()):
        raise TemplateError('Required system-linked fields or their enclosing sections cannot be deleted or rebound. Edit their presentation instead.')
    if any(identifier not in before and binding in before.values() for identifier, binding in after.items()):
        raise TemplateError('System-linked questions cannot be duplicated by copying their enclosing section or table row.')
