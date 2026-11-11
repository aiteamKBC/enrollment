"""Immutable catalogue snapshots carried by the existing wizard publications."""
import copy
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

KEY = 'skillsRadarCatalogues'
APPROVALS = 'skillsRadarImportApprovals'
PROGRAMMES = ('PCP_L6', 'MM_L6', 'ME_L4', 'APM_L4')
KINDS = ('Knowledge', 'Skill', 'Behaviour')
CODE = re.compile(r'^[KSB][1-9][0-9]{0,4}$')
MAX_RECORDS = 2000
PCP_OWNERS = {
    'operational': {'pmp': 'PMP', 'ai-project-control': 'AI', 'risk-apm': 'RISK', 'planning-control': 'PPC', 'earned-value': 'FINANCE', 'pmi-sp': 'SCHEDULE'},
    'strategic': {'pmp': 'PMP', 'ai-project-control': 'AI', 'risk-apm': 'RISK', 'pmi-pmo': 'PPC', 'portfolio': 'FINANCE', 'successful-programmes': 'SCHEDULE'},
    'chartered': {'pmo-planning-control': 'CH_PLAN', 'pmo-stakeholders': 'CH_STAKE', 'pmo-risk-quality': 'CH_RISK', 'pmo-office': 'CH_PMO', 'ai-project-management': 'AI', 'portfolio': 'FINANCE', 'earned-value': 'FINANCE'},
}


class CatalogueError(ValueError):
    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors or []


def order(record):
    return ('KSB'.index(record['code'][0]), int(record['code'][1:]))


def version(programme, records):
    payload = json.dumps([programme, sorted(records, key=order)], sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


@lru_cache(maxsize=1)
def _initial():
    data = json.loads(Path(__file__).with_name('defaults.json').read_text(encoding='utf-8'))
    for programme, catalogue in data.items():
        catalogue.update(version=version(programme, catalogue['records']), publishedAt=None)
    return data


def initial(programme):
    return copy.deepcopy(_initial()[programme])


def current_catalogues(layout):
    stored = (layout or {}).get(KEY) or {}
    return {pid: copy.deepcopy(stored.get(pid) or _initial()[pid]) for pid in PROGRAMMES}


def with_catalogues(layout):
    return {**layout, KEY: current_catalogues(layout)} if layout is not None else None


def leaves(items):
    for item in items:
        if not item.get('active'):
            continue
        if item.get('itemType') == 'GROUP' or ('items' in item and item.get('itemType') != 'MODULE'):
            yield from leaves(item.get('items') or [])
        else:
            yield item


def allowed_keys(programme, modules):
    definition = next((p for p in modules.get('programmes', []) if p['id'] == programme), None)
    if not definition:
        raise CatalogueError(f'{programme}: published programme modules are missing.')
    if programme != 'PCP_L6':
        return {'moduleKey': {m['id'] for m in leaves(definition.get('modules', []))}}
    result = {}
    for path, owners in PCP_OWNERS.items():
        branch = next((p for p in definition['pathways'] if p['id'] == path and p.get('active')), None)
        if not branch:
            raise CatalogueError(f'PCP pathway {path} is unavailable.')
        result[path] = {owners[m['id']] for m in leaves(branch['modules']) if m.get('selectable') and m['id'] in owners}
    return result


def validate(programme, raw, modules, prior):
    if programme not in PROGRAMMES or not isinstance(raw, dict) or raw.get('programmeId') != programme:
        raise CatalogueError('The catalogue does not belong to the selected programme.')
    records = raw.get('records')
    if not isinstance(records, list) or not 1 <= len(records) <= MAX_RECORDS:
        raise CatalogueError(f'Provide a complete catalogue containing 1–{MAX_RECORDS} KSBs.')
    allowed = allowed_keys(programme, modules)
    identities = {r['code']: r['id'] for r in _initial()[programme]['records']}
    identities.update({r['code']: r['id'] for r in prior.get('records', [])})
    seen, errors, cleaned = set(), [], []
    for index, record in enumerate(records, 2):
        row = record.get('_row', index) if isinstance(record, dict) else index
        code = record.get('code') if isinstance(record, dict) else None
        problems = []
        if not isinstance(code, str) or not CODE.fullmatch(code):
            problems.append('Use a KSB code such as K1, S1 or B1.')
        elif code in seen:
            problems.append(f'Duplicate KSB code {code}.')
        seen.add(code if isinstance(code, str) else '')
        kind = record.get('kind') if isinstance(record, dict) else None
        description = record.get('description') if isinstance(record, dict) else None
        if kind not in KINDS:
            problems.append('Category must be Knowledge, Skill or Behaviour.')
        if not isinstance(description, str) or not description.strip() or len(description) > 12000 or '\x00' in description:
            problems.append('Provide a non-empty description of at most 12,000 characters without null bytes.')
        mapping = record.get('owners', {}) if programme == 'PCP_L6' and isinstance(record, dict) else {}
        if programme == 'PCP_L6':
            if not isinstance(mapping, dict):
                mapping = {}
            for path in PCP_OWNERS:
                if not isinstance(mapping.get(path), str) or mapping[path] not in allowed[path]:
                    problems.append(f'Invalid {path} owner key. Available: {", ".join(sorted(allowed[path]))}.')
        elif not isinstance(record, dict):
            problems.append(f'Invalid active module key. Available: {", ".join(sorted(allowed["moduleKey"]))}. Parent groups are not assessable.')
        else:
            keys = record.get('moduleKeys') if isinstance(record.get('moduleKeys'), list) else [record.get('moduleKey')]
            keys = [key for key in keys if isinstance(key, str)]
            if not keys or any(key not in allowed['moduleKey'] for key in keys) or len(set(keys)) != len(keys):
                problems.append(f'Invalid active module keys. Available: {", ".join(sorted(allowed["moduleKey"]))}. Parent groups are not assessable.')
        if problems:
            errors.extend({'row': row, 'code': code, 'message': text} for text in problems)
            continue
        item = {'id': identities.get(code) or f'radar:{programme}:{code}', 'code': code, 'kind': kind, 'description': description}
        item.update({'owners': {path: mapping[path] for path in PCP_OWNERS}} if programme == 'PCP_L6' else ({'moduleKey': keys[0], **({'moduleKeys': keys} if len(keys) > 1 else {})}))
        cleaned.append(item)
    if errors:
        raise CatalogueError('CSV/catalogue validation failed. Nothing was imported.', errors)
    cleaned.sort(key=order)
    return {'programmeId': programme, 'records': cleaned, 'version': version(programme, cleaned), 'publishedAt': prior.get('publishedAt')}


def compare(before, after):
    old, new = ({r['code']: r for r in value['records']} for value in (before, after))
    changes = []
    counts = {name: 0 for name in ('unchanged', 'added', 'removed', 'descriptions', 'categories', 'mappings')}
    for code in sorted(old.keys() | new.keys(), key=lambda c: order({'code': c})):
        a, b = old.get(code), new.get(code)
        flags = (['added'] if a is None else ['removed'] if b is None else
                 [label for label, different in [('descriptions', a['description'] != b['description']), ('categories', a['kind'] != b['kind']), ('mappings', a.get('owners', a.get('moduleKey')) != b.get('owners', b.get('moduleKey')))] if different])
        flags = flags or ['unchanged']
        for flag in flags:
            counts[flag] += 1
        changes.append({'code': code, 'changes': flags, 'before': a, 'after': b})
    return {'currentTotal': len(old), 'newTotal': len(new), **counts, 'changes': changes,
            'removedCodes': [c['code'] for c in changes if 'removed' in c['changes']],
            'categoryCodes': [c['code'] for c in changes if 'categories' in c['changes']],
            'largeRemoval': counts['removed'] / max(1, len(old)) >= 0.25}


def check_approval(before, after, approval):
    diff = compare(before, after)
    if before['version'] == after['version']:
        return diff
    if not isinstance(approval, dict) or approval.get('againstVersion') != before['version'] or approval.get('completeSnapshot') is not True:
        raise CatalogueError('Review and confirm the complete replacement snapshot before saving or publishing.')
    for field, codes in [('removedCodes', diff['removedCodes']), ('categoryCodes', diff['categoryCodes'])]:
        selected = approval.get(field)
        if not isinstance(selected, list) or not all(isinstance(code, str) for code in selected) or sorted(selected) != sorted(codes):
            raise CatalogueError('Explicitly confirm every removal and review every category change before saving or publishing.')
    return diff


def integrate_layout(raw, previous, layout):
    if KEY not in raw and KEY not in (previous or {}):
        return
    old = current_catalogues(previous)
    incoming = raw.get(KEY, old)
    if not isinstance(incoming, dict) or set(incoming) != set(PROGRAMMES):
        raise CatalogueError('Keep all four programme catalogues; upload a complete CSV to change one programme.')
    approvals = raw.get(APPROVALS) or {}
    if not isinstance(approvals, dict):
        raise CatalogueError('Invalid import confirmations.')
    from django.utils import timezone
    from programme_suitability.modules_configuration import defaults
    result = {}
    for pid in PROGRAMMES:
        value = validate(pid, incoming[pid], layout.get('programmeModules') or defaults(), old[pid])
        check_approval(old[pid], value, approvals.get(pid))
        if value['version'] == old[pid]['version']:
            result[pid] = old[pid]
        else:
            result[pid] = {**value, 'publishedAt': timezone.now().isoformat()}
    layout[KEY] = result
