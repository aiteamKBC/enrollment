"""Propagate current Curriculum KSB mappings to every linked learner activity.

The progress row remains the immutable activity identity.  Its KSB child rows
are the current snapshot used by Learner/Coach/Curriculum reads; every material
edit records the replaced snapshot in learner_activity_revisions first.
"""
import json
import uuid
from decimal import Decimal, InvalidOperation

from django.db import connection


def _text(value):
    return str(value or '').strip()


def _weight(value):
    if value in (None, ''):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def normalise_mappings(mappings):
    """Return the exact learner snapshot shape, in deterministic order."""
    result = []
    seen = set()
    for mapping in mappings or []:
        if not isinstance(mapping, dict):
            continue
        code = _text(mapping.get('ksb_code') or mapping.get('code') or mapping.get('ksbCode')).upper()
        if not code:
            continue
        source_type = _text(mapping.get('source_type') or mapping.get('sourceType'))
        source_id = _text(mapping.get('source_id') or mapping.get('sourceId'))
        identity = (code, source_type, source_id)
        if identity in seen:
            continue
        seen.add(identity)
        result.append({
            'position': len(result),
            'ksb_code': code,
            'ksb_description': _text(
                mapping.get('ksb_description') or mapping.get('description') or mapping.get('ksbDescription')
            ),
            'source_type': source_type,
            'source_id': source_id,
            'classification': _text(mapping.get('classification') or mapping.get('type')).lower(),
            'weight': _weight(mapping.get('weight')),
            'weight_class': _text(mapping.get('weight_class') or mapping.get('weightClass')).lower(),
        })
    return result


def _snapshot(rows):
    return [{
        **row,
        'weight': float(row['weight']) if row.get('weight') is not None else None,
    } for row in rows]


def sync_progress_ksbs(mappings_by_component, *, actor='curriculum-ksb-sync'):
    """Replace KSB snapshots for all linked progress, including completed rows.

    Callers run this inside the same ``transaction.atomic`` block as the
    Curriculum update.  A failure therefore rolls back both sides of the link.
    """
    desired = {
        _text(component_id): normalise_mappings(mappings)
        for component_id, mappings in (mappings_by_component or {}).items()
        if _text(component_id)
    }
    if not desired or connection.vendor != 'postgresql':
        return {'components': len(desired), 'progress_rows': 0, 'revisions': 0}

    with connection.cursor() as cursor:
        cursor.execute('''
            SELECT p.id,p.learner_id,p.reporting_month,
                   coalesce(nullif(p.curriculum_component_ref,''),nullif(p.component_ref,'')) AS component_id,
                   k.position,k.ksb_code,k.ksb_description,k.source_type,k.source_id,
                   k.classification,k.weight,k.weight_class
            FROM "Learner".learner_progress_entries p
            LEFT JOIN "Learner".learner_progress_ksbs k ON k.progress_id=p.id
            WHERE p.deleted_at IS NULL
              AND coalesce(nullif(p.curriculum_component_ref,''),nullif(p.component_ref,''))=ANY(%s)
            ORDER BY p.id,k.position
        ''', [list(desired)])
        columns = [column[0] for column in cursor.description]
        records = [dict(zip(columns, row)) for row in cursor.fetchall()]

        progress = {}
        for record in records:
            item = progress.setdefault(record['id'], {
                'learner_id': record['learner_id'],
                'reporting_month': record['reporting_month'],
                'component_id': _text(record['component_id']),
                'ksbs': [],
            })
            if record['position'] is not None:
                item['ksbs'].append({key: record.get(key) for key in (
                    'position', 'ksb_code', 'ksb_description', 'source_type', 'source_id',
                    'classification', 'weight', 'weight_class',
                )})

        changed = []
        for progress_id, item in progress.items():
            next_rows = desired.get(item['component_id'], [])
            current_rows = normalise_mappings(item['ksbs'])
            if _snapshot(current_rows) != _snapshot(next_rows):
                changed.append((progress_id, item, next_rows, current_rows))

        if not changed:
            return {'components': len(desired), 'progress_rows': 0, 'revisions': 0}

        for progress_id, item, next_rows, current_rows in changed:
            cursor.execute('''
                INSERT INTO "Learner".learner_activity_revisions
                    (learner_id,progress_id,report_month,source_system,source_ref,
                     change_kind,before_snapshot,after_snapshot,actor)
                VALUES (%s,%s,%s,'curriculum',%s,'ksb_mapping_sync',%s::jsonb,%s::jsonb,%s)
            ''', [
                item['learner_id'], progress_id, item['reporting_month'],
                f"component:{item['component_id']}:progress:{progress_id}:event:{uuid.uuid4().hex}",
                json.dumps({'ksbs': _snapshot(current_rows)}),
                json.dumps({'ksbs': _snapshot(next_rows)}),
                _text(actor) or 'curriculum-ksb-sync',
            ])
            cursor.execute(
                'DELETE FROM "Learner".learner_progress_ksbs WHERE progress_id=%s',
                [progress_id],
            )
            if next_rows:
                cursor.executemany('''
                    INSERT INTO "Learner".learner_progress_ksbs
                        (progress_id,position,ksb_code,ksb_description,source_type,source_id,
                         classification,weight,weight_class)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ''', [[
                    progress_id, row['position'], row['ksb_code'], row['ksb_description'],
                    row['source_type'], row['source_id'], row['classification'],
                    row['weight'], row['weight_class'],
                ] for row in next_rows])

    return {'components': len(desired), 'progress_rows': len(changed), 'revisions': len(changed)}
