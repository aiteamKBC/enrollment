"""Record-level PLR changes using existing rows and server-owned draft metadata."""
import datetime
import re
from .models import WizardPlrRecord

DELETED_KEY = '_plrDeletedRecords'
IMPORT_KEY = '_plrImportSources'
SERVER_PLR_KEYS = (DELETED_KEY, IMPORT_KEY)
FIELDS = {'placeOfStudy': 'place_of_study', 'qualificationType': 'qualification_type',
          'subject': 'subject', 'level': 'level', 'awardDate': 'award_date',
          'credits': 'credits', 'grade': 'grade', 'startDate': 'start_date', 'endDate': 'end_date'}


class RecordChangeError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def normalized(record):
    if not isinstance(record, dict):
        raise RecordChangeError('Provide a valid PLR record.')
    result = {}
    for key in FIELDS:
        value = record.get(key)
        if key == 'credits':
            if value in (None, ''):
                result[key] = None
                continue
            if isinstance(value, bool) or not re.fullmatch(r'[0-9]{1,6}', str(value)):
                raise RecordChangeError('Credits must be a non-negative whole number.')
            value = int(value)
        else:
            value = '' if value is None else value
            if not isinstance(value, str) or len(value) > 4000:
                raise RecordChangeError('PLR fields must contain valid text.')
            value = value.strip()
            if key.endswith('Date') and value:
                try:
                    value = datetime.date.fromisoformat(value).isoformat()
                except ValueError:
                    raise RecordChangeError('Provide valid PLR dates in YYYY-MM-DD format.') from None
        result[key] = value
    return result


def merge_records(snapshot, records, deleted):
    """Preserve legacy JSON-only entries; database rows override older snapshots."""
    combined = {r['id']: r for r in snapshot if isinstance(r, dict) and isinstance(r.get('id'), str)}
    for record in records:
        # Evidence uploads may create a placeholder before Add is saved.
        if record.get('recordType') or any(record.get(key) for key in FIELDS) or record['id'] not in combined:
            combined[record['id']] = record
    return [r for ref, r in combined.items() if ref not in deleted]


def apply_change(kind, learner_id, change, plr, deleted):
    """Caller holds the selected learner lock and the enrolment transaction."""
    ref = change.get('id')
    selected = [r for r in plr['records'] if r['id'] == ref]
    if len(selected) != 1 or ref in deleted:
        raise RecordChangeError('PLR record no longer exists. Reload this step.', 404)
    current = selected[0]
    expected = change.get('expected')
    if not isinstance(expected, dict) or expected.get('id') != ref or normalized(expected) != normalized(current):
        raise RecordChangeError('This PLR record has changed. Reload before editing or deleting it.', 409)
    scope = {'learner_kind': kind, 'learner_id': learner_id, 'record_ref': ref}
    rows = WizardPlrRecord.objects.filter(**scope)
    if rows.count() > 1:
        raise RecordChangeError('This PLR record has duplicate identifiers. No changes were saved.', 409)
    row = rows.first()
    if change['action'] == 'delete':
        # Keep evidence references in the existing JSON; never remove blobs here.
        deleted = {**deleted, ref: {'evidence': row.evidence or [] if row else []}}
        if row:
            row.delete()
        return {**plr, 'records': [r for r in plr['records'] if r['id'] != ref]}, deleted
    values = normalized(change.get('record'))
    defaults = {field: values[key] or None if key != 'credits' else values[key] for key, field in FIELDS.items()}
    if row:
        for field, value in defaults.items():
            setattr(row, field, value)
        row.save(update_fields=[*defaults, 'updated_at'])
    else:
        # Historical records may have lived only in Wizard_draft.
        WizardPlrRecord.objects.create(**scope, record_type=current.get('recordType') or 'Manual', **defaults)
    updated = {**current, **values}
    return {**plr, 'records': [updated if r['id'] == ref else r for r in plr['records']]}, deleted
