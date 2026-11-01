"""Modules share the existing wizard publication/version; defaults are seed data."""
import copy
import json
import math
import re
from pathlib import Path

from .configuration import ConfigurationError, PROGRAMMES

KEY = 'programmeModules'
IDENTIFIER = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$')


def defaults():
    return json.loads(Path(__file__).with_name('modules_defaults.json').read_text(encoding='utf-8'))


def legacy_defaults():
    return json.loads(Path(__file__).with_name('modules_legacy_defaults.json').read_text(encoding='utf-8'))


def _text(value, label, required=False):
    if not isinstance(value, str) or len(value) > 12000 or (required and not value.strip()):
        raise ConfigurationError(f'{label} must be valid text' + (' and cannot be empty.' if required else '.'))
    return value.strip()


def _id(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ConfigurationError('Provide a stable alphanumeric module/pathway identifier.')
    return value


def _number(value, label, integer=False, nullable=True):
    if value is None and nullable:
        return None
    try:
        finite = isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        finite = False
    if isinstance(value, bool) or not finite or value < 0 or (integer and type(value) is not int):
        raise ConfigurationError(f'{label} must be a non-negative ' + ('integer.' if integer else 'number.'))
    return value


def _flag(raw, name, default):
    value = raw.get(name, default)
    if type(value) is not bool:
        raise ConfigurationError(f'{name} must be true or false.')
    return value


def _modules(raw, seen=None, ancestors=None, depth=0):
    if not isinstance(raw, list) or depth > 32:
        raise ConfigurationError('Modules must be a valid nested list without circular references.')
    seen = set() if seen is None else seen
    ancestors = set() if ancestors is None else ancestors
    out = []
    for item in raw:
        if not isinstance(item, dict) or id(item) in ancestors:
            raise ConfigurationError('Invalid or circular module hierarchy.')
        mid = _id(item.get('id'))
        if mid in seen:
            raise ConfigurationError(f'Duplicate module identifier: {mid}.')
        seen.add(mid)
        if any(key in item for key in ('parentId', 'programmeId', 'pathwayId')):
            raise ConfigurationError('Module associations must use their containing programme/pathway and nested items.')
        value = {'id': mid, 'title': _text(item.get('title'), 'Module name', True),
                 'description': _text(item.get('description', ''), 'Module description'),
                 'sessions': _number(item.get('sessions'), 'Sessions', integer=True),
                 'order': _number(item.get('order', len(out)), 'Order', integer=True, nullable=False),
                 'active': _flag(item, 'active', True), 'selectable': _flag(item, 'selectable', False),
                 'isMandatory': _flag(item, 'isMandatory', False)}
        if value['isMandatory'] and (not value['selectable'] or item.get('items')):
            raise ConfigurationError('Only selectable actual modules can be mandatory; group headings cannot be mandatory.')
        if item.get('credits') is not None:
            value['credits'] = _number(item['credits'], 'Credits', nullable=False)
        if 'items' in item:
            value['items'] = _modules(item['items'], seen, ancestors | {id(item)}, depth + 1)
        out.append(value)
    return out


def validate(raw, publishing=False):
    if not isinstance(raw, dict) or type(raw.get('schemaVersion')) is not int or raw['schemaVersion'] not in (1, 2) or not isinstance(raw.get('programmes'), list):
        raise ConfigurationError('Provide a valid version 1 or 2 programme modules configuration.')
    programmes, placed = [], set()
    for programme in raw['programmes']:
        if not isinstance(programme, dict) or not isinstance(programme.get('id'), str) or programme['id'] not in PROGRAMMES or programme['id'] in placed:
            raise ConfigurationError('Modules must reference each supported programme exactly once.')
        pid = programme['id']
        placed.add(pid)
        paths, path_ids = [], set()
        raw_paths = programme.get('pathways', [])
        if not isinstance(raw_paths, list) or (pid != 'PCP_L6' and raw_paths):
            raise ConfigurationError('Only PCP supports pathway configuration.')
        for pathway in raw_paths:
            if not isinstance(pathway, dict):
                raise ConfigurationError('Invalid pathway.')
            path_id = _id(pathway.get('id'))
            if path_id in path_ids:
                raise ConfigurationError(f'Duplicate pathway identifier: {path_id}.')
            path_ids.add(path_id)
            paths.append({'id': path_id, 'title': _text(pathway.get('title'), 'Pathway name', True),
                          **{key: _text(pathway.get(key, ''), f'Pathway {key}') for key in ('subtitle', 'description', 'recognition')},
                          'targetCredits': _number(pathway.get('targetCredits'), 'Target credits'),
                          'requiredOptionalModulesCount': _number(pathway.get('requiredOptionalModulesCount'), 'Required optional module count', integer=True),
                          'order': _number(pathway.get('order', len(paths)), 'Pathway order', integer=True, nullable=False),
                          'active': _flag(pathway, 'active', True),
                          'allowModuleSelection': _flag(pathway, 'allowModuleSelection', True),
                          'modules': _modules(pathway.get('modules', []))})
            if raw['schemaVersion'] == 2 and paths[-1]['active'] and paths[-1]['requiredOptionalModulesCount'] is None:
                raise ConfigurationError('Set the required optional module count for every active PCP pathway.')
            if publishing and paths[-1]['active'] and paths[-1]['requiredOptionalModulesCount'] is None:
                from .modules_rules import available_modules
                if any(m.get('isMandatory') for m in available_modules(paths[-1]['modules'])):
                    raise ConfigurationError('Set the required optional module count before publishing mandatory modules.')
            if paths[-1]['active'] and paths[-1]['requiredOptionalModulesCount'] is not None:
                def check_groups(items):
                    for item in items:
                        if item.get('items') and (item['selectable'] or item.get('credits', 0) != 0):
                            raise ConfigurationError('Parent group headings must not be selectable or credited modules.')
                        check_groups(item.get('items', []))
                check_groups(paths[-1]['modules'])
                from .modules_rules import validate_pathway_rules
                validate_pathway_rules(paths[-1])
        programmes.append({'id': pid, 'title': _text(programme.get('title'), 'Programme title', True),
                           'modules': _modules(programme.get('modules', [])), 'pathways': paths})
    if placed != set(PROGRAMMES):
        raise ConfigurationError('Configure ME L4, MM L6, APM L4 and PCP L6.')
    return {'schemaVersion': raw['schemaVersion'], 'programmes': programmes}


def _retain_modules(previous, current):
    by_id = {item['id']: item for item in current}
    for old in previous:
        if old['id'] not in by_id:
            current.append({**copy.deepcopy(old), 'active': False})
        elif old.get('items'):
            item = by_id[old['id']]
            item.setdefault('items', [])
            _retain_modules(old['items'], item['items'])


def integrate_layout(raw, previous, layout):
    if KEY in raw and raw[KEY] is None:
        raise ConfigurationError('Modules configuration cannot be cleared; deactivate its content instead.')
    value = raw.get(KEY, (previous or {}).get(KEY))
    if value is None:
        return
    config = validate(value, publishing=True)
    old = validate((previous or {}).get(KEY) or defaults())
    for programme in config['programmes']:
        prior = next(p for p in old['programmes'] if p['id'] == programme['id'])
        _retain_modules(prior['modules'], programme['modules'])
        by_id = {p['id']: p for p in programme['pathways']}
        for path in prior['pathways']:
            if path['id'] not in by_id:
                programme['pathways'].append({**copy.deepcopy(path), 'active': False})
            else:
                _retain_modules(path['modules'], by_id[path['id']]['modules'])
    layout[KEY] = config


def with_modules(layout):
    if layout is None:
        return None
    return {**layout, KEY: copy.deepcopy(layout.get(KEY) or defaults())}
