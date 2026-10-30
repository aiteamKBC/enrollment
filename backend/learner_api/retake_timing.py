"""Read-only selection of persisted attempt timing; never changes audit evidence."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo


def choice(value):
    if value is not None and value not in ('keep', 'new'):
        raise ValueError('Choose whether to keep the current time or use the new attempt time.')
    return value


def metadata(record):
    value = record.get('source_payload') or {}
    value = json.loads(value) if isinstance(value, str) else value
    return value if isinstance(value, dict) else {}


def stamp(value):
    if not value:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    uk = ZoneInfo('Europe/London')
    return (value if value.tzinfo else value.replace(tzinfo=uk)).astimezone(uk)


def attempt_order(submitted_at, ident):
    """Order saved attempts by the real submission instant, then their stable ID."""
    ident = str(ident)
    submitted = stamp(submitted_at)
    return (submitted.astimezone(ZoneInfo('UTC')) if submitted else datetime.min.replace(tzinfo=ZoneInfo('UTC')),
            (0, int(ident)) if ident.isdigit() else (1, ident))


def native_key(record):
    # Match the existing native quiz identity; never cross learner ownership.
    return (record.get('learner_id'), str(record['quiz_ref'])) if record.get('quiz_ref') else None


def native_choices(baseline, candidates):
    groups = {}
    for record in candidates:
        key = native_key(record)
        if key:
            groups.setdefault(key, []).append(record)
    replacements = {}
    for key, rows in groups.items():
        if not any(metadata(row).get('retakeTimeChoice') for row in rows):
            continue
        selected = latest = None
        explicit = False
        for row in sorted(rows, key=lambda item: attempt_order(item.get('submitted_at'), item['id'])):
            if row.get('accepted') is not True or row.get('passed') is False or row.get('actual_seconds') is None:
                continue
            mode = metadata(row).get('retakeTimeChoice')
            explicit = explicit or mode in ('keep', 'new')
            latest = row
            if mode != 'keep' or selected is None:
                selected = row
        if not explicit or selected is None:
            continue
        end = stamp(selected.get('declared_completed_at') or selected.get('reporting_ended_at')
                    or selected.get('submitted_at'))
        start = stamp(selected.get('started_at') or selected.get('reporting_started_at'))
        label = selected.get('reporting_timestamp_label') or ''
        if end:
            label = end.strftime('%H:%M:%S')
            if start and start.date() == end.date() and start.timestamp() < end.timestamp():
                label = f'{start:%H:%M:%S} - {end:%H:%M:%S}'
            else:
                start = None
        replacements[key] = {**latest, 'actual_seconds': selected['actual_seconds'],
            'reporting_month': end.strftime('%Y-%m') if end else selected.get('reporting_month'),
            'reporting_started_at': start, 'reporting_ended_at': end,
            'reporting_timestamp_label': label, 'retake_timing_applied': True,
            'completion_reporting_date': end.date().isoformat() if end else None,
            'retake_superseded_ids': [str(row['id']) for row in rows if row['id'] != latest['id']]}
    return [row for row in baseline if native_key(row) not in replacements] + list(replacements.values())


def measured_choice(candidates, has_accepted_history=False):
    """Keep the currently selected successful local attempt across later retakes."""
    selected = None
    replace_history = False
    for row in sorted(candidates, key=lambda item: attempt_order(item['timing'].get('submittedAt'), item['id'])):
        mode = row['timing'].get('retakeTimeChoice')
        if mode in ('keep', 'new'):
            if mode == 'new':
                selected, replace_history = row, True
            elif selected is None and not has_accepted_history:
                selected = row
        else:
            selected = row
    return selected, replace_history
