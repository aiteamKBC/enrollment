"""Explicit, recovery-backed three-way rebase of the existing shared draft."""
import copy
import json
from pathlib import Path

from django.core.serializers.json import DjangoJSONEncoder
from django.db import connections, router, transaction

from .models import ModuleConfigurationDraft
from .programme_structure import from_legacy, validate_structure, version

SHARED_KEY = 'programme-structure:shared'
_MISSING = object()


class RebaseConflict(ValueError):
    pass


def snapshot(layout):
    if isinstance(layout.get('programmeStructure'), dict):
        result = copy.deepcopy(layout['programmeStructure'])
    else:
        result = from_legacy(layout.get('programmeModules', {}), layout.get('skillsRadarCatalogues', {}))
        # A legacy projection's synthetic cohorts are not real managed records.
        for programme in result['programmes']:
            programme.pop('cohorts', None)
    if 'intakeCalendar' in layout:
        result.setdefault('intakeCalendar', copy.deepcopy(layout['intakeCalendar']))
    result.pop('version', None)
    result.pop('_draftRevision', None)
    return result


def merge(base, draft, current, path='$'):
    """Merge keyed entities independently; ambiguous edits fail closed."""
    if draft == base:
        return copy.deepcopy(current) if current is not _MISSING else _MISSING
    if current == base or draft == current:
        return copy.deepcopy(draft) if draft is not _MISSING else _MISSING
    if all(isinstance(x, dict) for x in (base, draft, current)):
        result = {}
        for key in sorted(base.keys() | draft.keys() | current.keys()):
            value = merge(base.get(key, _MISSING), draft.get(key, _MISSING), current.get(key, _MISSING), f'{path}.{key}')
            if value is not _MISSING:
                result[key] = value
        return result
    if all(isinstance(x, list) for x in (base, draft, current)):
        def key(item):
            if not isinstance(item, dict):
                raise RebaseConflict(f'Conflicting ordered values at {path}')
            if 'id' in item:
                return item['id']
            if 'ksbId' in item and 'moduleId' in item:
                return (item['ksbId'], item['moduleId'], item.get('pathwayId'))
            raise RebaseConflict(f'Conflicting unkeyed values at {path}')
        indexed = []
        for items in (base, draft, current):
            mapping = {key(item): item for item in items}
            if len(mapping) != len(items):
                raise RebaseConflict(f'Duplicate identities at {path}')
            indexed.append(mapping)
        b, d, c = indexed
        # Preserve ordering; simultaneous incompatible reordering is a conflict.
        common = b.keys() & d.keys() & c.keys()
        orders = [[key(item) for item in items if key(item) in common] for items in (base, draft, current)]
        if orders[1] != orders[0] and orders[2] != orders[0] and orders[1] != orders[2]:
            raise RebaseConflict(f'Conflicting ordering at {path}')
        first, second = (draft, current) if orders[1] != orders[0] else (current, draft)
        ordered_keys = list(dict.fromkeys([key(item) for item in first + second + base]))
        result = []
        for identity in ordered_keys:
            value = merge(b.get(identity, _MISSING), d.get(identity, _MISSING), c.get(identity, _MISSING), f'{path}[{identity}]')
            if value is not _MISSING:
                result.append(value)
        return result
    raise RebaseConflict(f'Conflicting edits at {path}')


def rebase_shared_draft(*, expected_base, expected_revision, expected_published, backup_dir, apply=False):
    from enrolment_api.wizard_layout import read_current, read_version
    database = router.db_for_write(ModuleConfigurationDraft) or 'default'
    # Both aliases point at the configured enrolment DB. Lock the published table
    # on the same connection as the draft so publication cannot race the rebase.
    with transaction.atomic(using=database):
        with connections[database].cursor() as cursor:
            if connections[database].vendor == 'postgresql':
                cursor.execute('LOCK TABLE enrolment."Wizard_Form_Layouts" IN SHARE MODE')
            current = read_current(cursor)
            row = ModuleConfigurationDraft.objects.using(database).select_for_update().get(pk=SHARED_KEY)
            if not current or current[0] != expected_published or row.base_version != expected_base or row.configuration.get('_draftRevision', 0) != expected_revision:
                raise RebaseConflict('The saved draft or published configuration changed. Review again before retrying.')
            original = read_version(cursor, row.base_version)
            if not original:
                raise RebaseConflict('The original published configuration is unavailable; no changes were made.')
            backup = {'draft': {'actor_key': row.pk, 'base_version': row.base_version, 'configuration': row.configuration, 'updated_at': row.updated_at}, 'base': original, 'current': current}
            encoded = json.dumps(backup, cls=DjangoJSONEncoder, ensure_ascii=False, indent=2)
            path = Path(backup_dir) / 'before-rebase.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.read_text(encoding='utf-8') != encoded:
                raise RebaseConflict('Recovery backup differs; retain it and use a new backup directory.')
            path.write_text(encoded, encoding='utf-8')
            if json.loads(path.read_text(encoding='utf-8')) != json.loads(encoded):
                raise RebaseConflict('Recovery backup verification failed.')
            draft = copy.deepcopy(row.configuration)
            draft.pop('version', None)
            draft.pop('_draftRevision', None)
            merged = merge(snapshot(original[1]), draft, snapshot(current[1]))
            validate_structure(merged)
            merged['_draftRevision'] = expected_revision + 1
            merged['version'] = version(merged)
            if apply:
                row.base_version = current[0]
                row.configuration = merged
                row.save(using=database, update_fields=['base_version', 'configuration', 'updated_at'])
                row.refresh_from_db(using=database)
                if row.configuration != merged:
                    raise RebaseConflict('Saved draft verification failed.')
            return {'baseVersion': current[0], 'revision': merged['_draftRevision'], 'structure': merged, 'applied': apply}
