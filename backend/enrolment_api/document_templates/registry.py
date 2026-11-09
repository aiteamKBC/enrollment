"""Controlled document identities, safe field references, and structured blocks.

Seed wording is extracted from the original jsPDF generators, never invented.
The retained legacy generators reproduce an unchanged seed exactly.
"""
import copy
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

PROGRAMMES = {'PCP_L6': 'Project Controls Professional Level 6', 'MM_L6': 'Marketing Manager Level 6',
              'ME_L4': 'Marketing Executive Level 4', 'APM_L4': 'Associate Project Manager Level 4'}
REVIEW_KEYS = {'eligibility-review': 'review-eligibility', 'workspace': 'review-rpl',
               'training-plan': 'review-health-safety', 'uln-privacy': 'review-uln'}
TOKEN = re.compile(r'\{\{([A-Za-z0-9_.-]+)\}\}')
IDENTIFIER = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$')
MAX_BYTES = 1024 * 1024
STYLES = {'bold', 'italic', 'underline', 'align', 'fontSize', 'spaceAfter'}


class TemplateError(ValueError):
    pass


@lru_cache(maxsize=1)
def seeds():
    return json.loads(Path(__file__).with_name('seed.json').read_text(encoding='utf-8'))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def seed(key):
    if not isinstance(key, str) or key not in seeds()['templates']:
        raise TemplateError('Unknown document type.')
    return copy.deepcopy(seeds()['templates'][key])


def text(value, label, *, empty=False, maximum=16000):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value or (not empty and not value.strip()):
        raise TemplateError(f'{label}: provide {"text" if empty else "non-empty text"} of at most {maximum} characters without null bytes.')
    return value


def check_tokens(value, key):
    allowed = seeds()['fields'][key]
    for ref in TOKEN.findall(value):
        if ref not in allowed:
            raise TemplateError(f'Unsupported dynamic field: {ref}. Use the field picker.')
    if '{{' in TOKEN.sub('', value) or '}}' in TOKEN.sub('', value):
        raise TemplateError('Invalid dynamic field syntax. Use the field picker; expressions and template code are not supported.')


def validate(raw, *, publishing=False):
    from .question_fields import legacy_fields, validate_fields
    from .page_content import runs, layout
    if not isinstance(raw, dict) or raw.get('schemaVersion') != 1:
        raise TemplateError('Unsupported document template format.')
    key = raw.get('documentKey')
    original = seed(key)
    identifier = raw.get('templateId')
    if not isinstance(identifier, str) or not (identifier == key or re.fullmatch(re.escape(key) + r'@[a-f0-9-]{36}', identifier)):
        raise TemplateError('Invalid template identity.')
    programmes = raw.get('programmes')
    if not isinstance(programmes, list) or any(not isinstance(p, str) or p not in PROGRAMMES for p in programmes) or len(programmes) != len(set(programmes)):
        raise TemplateError('Choose supported programme identities without duplicates.')
    if (identifier == key and programmes) or (identifier != key and not programmes):
        raise TemplateError('Keep the original template shared. Create a programme variant for one or more specific programmes.')
    blocks = raw.get('blocks')
    if not isinstance(blocks, list) or not 1 <= len(blocks) <= 500:
        raise TemplateError('Provide between 1 and 500 content blocks.')
    allowed_native = {b['source'] for b in original['blocks'] if b['type'] == 'native'}
    found, roles, cleaned = set(), [], []
    for i, block in enumerate(blocks, 1):
        if not isinstance(block, dict) or not isinstance(block.get('id'), str) or not IDENTIFIER.fullmatch(block['id']) or block['id'] in found:
            raise TemplateError(f'Block {i}: provide a unique stable identifier.')
        found.add(block['id']); kind = block.get('type'); item = {'id': block['id'], 'type': kind}
        if not isinstance(kind, str): raise TemplateError(f'Block {i}: unsupported block type.')
        if kind in {'heading', 'paragraph', 'declaration', 'date'}:
            item['text'] = text(block.get('text'), f'Block {i} text', empty=True)
            if 'runs' in block:
                item['runs'] = runs(block['runs'], key)
                if ''.join(r['text'] for r in item['runs']) != item['text']: raise TemplateError('Rich text and its plain-text representation must match.')
            if kind == 'heading':
                level = block.get('level', 2)
                if type(level) is not int or level not in (1, 2, 3):
                    raise TemplateError('Heading level must be 1, 2 or 3.')
                item['level'] = level
        elif kind == 'field':
            item.update(label=text(block.get('label'), f'Block {i} label', maximum=2000), value=text(block.get('value'), f'Block {i} value', empty=True))
        elif kind == 'questions':
            raw_fields = block.get('fields')
            incoming = {f.get('id') for f in raw_fields if isinstance(f, dict) and isinstance(f.get('id'), str)} if isinstance(raw_fields, list) else set()
            # A contiguous legacy scalar section may become one card collection.
            # Its field IDs/bindings remain the controlled original identities.
            expected = []
            for previous_block in original['blocks']:
                if previous_block['type'] != 'field' or previous_block['id'] not in incoming: continue
                candidate = next(f for f in raw_fields if isinstance(f, dict) and f.get('id') == previous_block['id'])
                binding = candidate.get('binding')
                # v1 allowed administrators to edit a scalar placeholder value.
                # Infer its display type from that existing allowlisted binding.
                # Save/Publish additionally compare all bindings to the actual
                # published base, so an API request cannot use this to rebind it.
                baseline_block = previous_block
                if isinstance(binding, dict) and binding.get('kind') == 'value' and isinstance(binding.get('value'), str):
                    baseline_block = {**previous_block, 'value': binding['value']}
                expected.extend(legacy_fields(baseline_block, key))
            item['fields'] = validate_fields(raw_fields, expected, key, publishing=publishing)
        elif kind == 'list':
            if type(block.get('ordered')) is not bool or not isinstance(block.get('items'), list) or not 1 <= len(block['items']) <= 200: raise TemplateError('Lists require an ordering setting and 1–200 items.')
            item.update(ordered=block['ordered'], items=[runs(v, key) for v in block['items']])
        elif kind == 'table':
            columns, rows = block.get('columns'), block.get('rows')
            if not isinstance(columns, list) or not 1 <= len(columns) <= 12 or not isinstance(rows, list) or len(rows) > 200:
                raise TemplateError(f'Block {i}: tables support 1–12 columns and at most 200 rows.')
            item['columns'] = [text(v, 'Column heading', empty=True, maximum=2000) for v in columns]
            if any(not isinstance(row, list) or len(row) != len(columns) for row in rows):
                raise TemplateError(f'Block {i}: every row must have the same number of cells as the columns.')
            item['rows'] = [[text(v, 'Table cell', empty=True) for v in row] for row in rows]
        elif kind == 'signature':
            role = block.get('role')
            if role not in original['signatureRoles'] or role in roles:
                raise TemplateError('Use each supported signature party once; signing roles cannot be invented or duplicated.')
            roles.append(role); item.update(role=role, label=text(block.get('label'), 'Signature label', maximum=2000))
            if original['category'] == 'Review' and role == 'employer':
                item['condition'] = 'employer-required'
        elif kind == 'native':
            source = block.get('source')
            if not isinstance(source, str) or source not in allowed_native:
                raise TemplateError('Unsupported document data table. Use the existing data block.')
            item.update(source=source, label=text(block.get('label'), 'Data block label', maximum=2000))
            if 'columns' in block:
                expected = next(b.get('columns', []) for b in original['blocks'] if b.get('source') == source)
                if not isinstance(block['columns'], list) or len(block['columns']) != len(expected):
                    raise TemplateError('Keep the existing data table columns; their labels can be edited without changing their data links.')
                item['columns'] = [text(v, 'Data column', empty=True, maximum=2000) for v in block['columns']]
            if 'labels' in block:
                previous = next(b.get('labels', {}) for b in original['blocks'] if b.get('source') == source)
                labels = block['labels']
                if not isinstance(labels, dict) or set(labels) != set(previous):
                    raise TemplateError('Existing review answer identifiers must be preserved. Edit their wording, not their database mapping.')
                item['labels'] = {k: text(v, f'Question {k}', maximum=4000) for k, v in labels.items()}
        elif kind != 'page-break':
            raise TemplateError(f'Block {i}: unsupported block type.')
        if 'fields' in block and kind != 'questions':
            if kind not in {'native', 'table', 'field'}: raise TemplateError('Field cards belong to existing question sections or document field blocks.')
            baseline = {k: v for k, v in item.items() if k != 'fields'}
            item['fields'] = validate_fields(block['fields'], legacy_fields(baseline, key), key, publishing=publishing)
        style = block.get('style')
        if style is not None:
            if not isinstance(style, dict) or set(style) - STYLES:
                raise TemplateError('Unsupported text style.')
            for flag in ('bold', 'italic', 'underline'):
                if flag in style and type(style[flag]) is not bool:
                    raise TemplateError('Text formatting flags must be boolean.')
            if style.get('align', 'left') not in ('left', 'center', 'right'):
                raise TemplateError('Invalid text alignment.')
            for name, low, high in [('fontSize', 7, 24), ('spaceAfter', 0, 20)]:
                if name in style and (type(style[name]) not in (int, float) or not low <= style[name] <= high):
                    raise TemplateError(f'{name} must be between {low} and {high}.')
            item['style'] = copy.deepcopy(style)
        # Check every user-controlled string, including table cells and labels.
        def strings(value):
            if isinstance(value, str): yield value
            elif isinstance(value, list):
                for v in value: yield from strings(v)
            elif isinstance(value, dict):
                for v in value.values(): yield from strings(v)
        for value in strings(item): check_tokens(value, key)
        cleaned.append(item)
    if publishing and set(roles) != set(original['signatureRoles']):
        missing = set(original['signatureRoles']) - set(roles)
        raise TemplateError('Restore required signature placeholders before publishing: ' + ', '.join(sorted(missing)))
    question_ids = [f['id'] for b in cleaned if 'fields' in b for f in b['fields']]
    all_ids = [f['id'] for b in cleaned for f in legacy_fields(b, key)]
    if len(question_ids) != len(set(question_ids)) or any(all_ids.count(identifier) > 1 for identifier in question_ids):
        raise TemplateError('Question identifiers must be unique across the document; system-linked sections cannot be duplicated.')
    result = {'schemaVersion': 1, 'templateId': identifier, 'documentKey': key,
              'title': text(raw.get('title'), 'Document title', maximum=200), 'category': original['category'],
              'programmes': sorted(programmes), 'blocks': cleaned, 'signatureRoles': original['signatureRoles']}
    if 'reviewType' in original: result['reviewType'] = original['reviewType']
    if 'designTheme' in raw:
        if raw['designTheme'] not in ('classic', 'lavender-v1', 'purple-reference-v2'):
            raise TemplateError('Unsupported document design theme.')
        result['designTheme'] = raw['designTheme']
    if 'pageLayout' in raw:
        result['pageLayout'] = layout(raw['pageLayout'])
        check_tokens(result['pageLayout']['header'], key); check_tokens(result['pageLayout']['footer'], key)
    check_tokens(result['title'], key)
    if len(json.dumps(result).encode()) > MAX_BYTES:
        raise TemplateError('Template exceeds the 1 MiB size limit.')
    return result


def legacy_equivalent(content):
    original = seed(content['documentKey'])
    return content.get('designTheme') not in ('lavender-v1', 'purple-reference-v2') and not content.get('pageLayout') and content['title'] == original['title'] and content['blocks'] == validate(original)['blocks']
