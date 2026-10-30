"""Merged completion totals, retaining the old percentage and hours as history."""
from collections import Counter
from copy import deepcopy
import json


def eligible_pairs(imports, replacements, owned, assigned, transferred=()):
    links = {**imports, **replacements}
    uses = Counter(links.values())
    taken = {item['courseRef'] for item in transferred}
    taken_modules = {item['moduleId'] for item in transferred}
    return {current: old for current, old in links.items()
            if current.removeprefix('current:') in assigned and old in owned
            and uses[old] == 1 and old not in taken and current.removeprefix('current:') not in taken_modules}


def is_old_record(record):
    # Locally saved legacy attempts have a synthetic subject id, not a ledger
    # source_system. They must be frozen too, without freezing native saves.
    return record.get('source_system') == 'old_lms' or (
        not record.get('source_system') and str(record.get('id', '')).startswith('subject:'))


def record_courses(record):
    refs = {str(item.get('source_course_ref') or '')
            for bucket in ('sources', 'historical_components')
            for item in record.get(bucket, [])
            if item.get('source_system') == 'old_lms'}
    refs.update(str(item.get('group_id') or '') for item in record.get('journal_routes', [])
                if item.get('source_system') == 'old_lms')
    payload = record.get('source_payload') or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = {}
    original = payload.get('original_source_ref') if isinstance(payload, dict) else None
    if isinstance(original, str):
        parts = original.split(':')
        if len(parts) == 3 and parts[0] == 'la':
            refs.add(parts[1])
        elif len(parts) == 4 and parts[:2] == ['subject', 'quiz']:
            refs.add(parts[2])
    return refs - {''}


def frozen_records(records, state, *, entry_ids=None):
    """Keep the request's old facts even when a later import changes the source.

    Current-LMS rows and other courses are untouched. Snapshots retain their
    original record identity, reporting dates and seconds; no credit row is added.
    """
    courses = {item['courseRef'].removeprefix('legacy:') for item in state.get('items', [])}
    snapshots = {str(record['id']): record for record in state.get('records', [])}
    allowed = None if entry_ids is None else {str(value) for value in entry_ids}
    result, seen = [], set()
    for record in records:
        key = str(record['id'])
        if key in snapshots:
            result.append(deepcopy(snapshots[key]))
            seen.add(key)
        elif is_old_record(record) and record_courses(record) & courses:
            continue
        else:
            result.append(record)
    result.extend(deepcopy(record) for key, record in snapshots.items()
                  if key not in seen and (allowed is None or key in allowed))
    return result


def public_item(item):
    result = {key: item[key] for key in ('courseRef', 'moduleId', 'oldTitle', 'newTitle', 'percent', 'hours')}
    if item.get('demoProgress'):
        result['demoProgress'] = True
    if 'componentMatches' in item:
        result['componentMatches'] = item['componentMatches']
    if 'componentCounts' in item:
        result['componentCounts'] = item['componentCounts']
    if 'mergedPercent' in item:
        result['mergedPercent'] = item['mergedPercent']
    return result


def merged_percent(completed_ids, total):
    """Use verified native + projected matches in the current module, once.

    Callers supply scoped current completions, after validating saved matches.
    Old percentages and baseline snapshots are historical facts, not extra credit.
    """
    return round(min(100, 100 * len(set(completed_ids)) / total), 2) if total else 0
