"""Transactional PLR operations on the existing wizard tables."""
import hashlib
import json
import re
import unicodedata
import uuid

from django.db import connections, transaction
from .models import WizardPlr, WizardPlrRecord
from .plr_pdf import PlrError, date_value

ULN_MISMATCH = 'The ULN in the uploaded PDF does not match the ULN associated with this student. Please upload the correct Personal Learning Record.'
FIELDS = {'placeOfStudy': 'place_of_study', 'qualificationType': 'qualification_type', 'subject': 'subject',
          'level': 'level', 'awardDate': 'award_date', 'credits': 'credits', 'grade': 'grade',
          'startDate': 'start_date', 'endDate': 'end_date'}


def can_manage(request):
    from .auth import _accesses_of, STAFF_ROLES
    from learner_api.constants import ACCESS_ENROLMENT, ACCESS_SUPER_ADMIN
    account = getattr(request, 'login_account', None)
    if account is not None:
        return bool(account.is_active and account.role in STAFF_ROLES and
                    _accesses_of(account) & {ACCESS_ENROLMENT, ACCESS_SUPER_ADMIN})
    user = getattr(request, 'user', None)
    return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))


def scope(kind, learner_id):
    return {'learner_kind': kind, 'learner_id': int(learner_id)}


def lock(kind, learner_id):
    # All save paths (including the legacy draft) take the same learner lock.
    from .extended_ilr import KINDS
    KINDS[kind].objects.select_for_update().get(pk=learner_id)
    row, _ = WizardPlr.objects.get_or_create(**scope(kind, learner_id))
    return WizardPlr.objects.select_for_update().get(pk=row.pk)


def state(kind, learner_id):
    row = WizardPlr.objects.filter(**scope(kind, learner_id)).first()
    records = WizardPlrRecord.objects.filter(**scope(kind, learner_id), deleted=False).order_by('id')
    return {'uln': row.uln or '' if row else '', 'revision': row.revision if row else 0,
            'records': [public(r) for r in records],
            'deletedRefs': list(WizardPlrRecord.objects.filter(**scope(kind, learner_id), deleted=True).values_list('record_ref', flat=True))}


def public(row):
    return {'id': row.record_ref, 'recordType': row.record_type or '', **{
        key: (str(value) if key.endswith('Date') and value else value if key == 'credits' else value or '')
        for key, field in FIELDS.items() for value in [getattr(row, field)]}}


def valid_uln(value):
    if not isinstance(value, str) or (value and not re.fullmatch(r'[0-9]{10}', value)):
        raise PlrError('ULN must contain exactly 10 digits, or be left blank.', 'invalid_uln')
    return value


def assign_uln(row, value):
    valid_uln(value)
    if value:
        connection = connections['enrolment']
        if connection.vendor == 'postgresql':
            # Global assignment lock plus unique index prevents cross-student races.
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(hashtext('plr-uln-assignment'))")
        if WizardPlr.objects.filter(uln=value).exclude(learner_id=row.learner_id).exists():
            raise PlrError('This ULN is already associated with another student.', 'duplicate_uln', 409)
    row.uln = value or None


def check_revision(row, revision):
    if type(revision) is not int or revision != row.revision:
        raise PlrError('The PLR has changed. Refresh this step before saving again.', 'stale_plr', 409)


def touch(row):
    row.revision += 1
    row.save(update_fields=['uln', 'revision', 'updated_at'])


def values(record):
    if not isinstance(record, dict):
        raise PlrError('Provide a valid learning record.')
    result = {}
    for key, field in FIELDS.items():
        value = record.get(key)
        if key.endswith('Date'):
            value = date_value(value)
        elif key == 'credits':
            if value is not None and value != '' and (isinstance(value, bool) or not re.fullmatch(r'\d{1,6}', str(value))):
                raise PlrError('Credits must be a non-negative whole number, or blank.')
            value = int(value) if value is not None and value != '' else None
        else:
            if value is not None and not isinstance(value, str):
                raise PlrError('Learning record fields must contain text.')
            value = (value or '').strip() or None
            if value and len(value) > 4000:
                raise PlrError('A learning record field exceeds the allowed length.')
        result[field] = value
    return result


def _normal(value):
    return ' '.join(re.findall(r'\w+', unicodedata.normalize('NFKC', value or '').casefold()))


def validate_identity(report, learner, reviewed=False):
    from .models import WizardPersonalDetails
    personal = WizardPersonalDetails.objects.filter(learner_id=learner.pk).first()
    name = ' '.join(filter(None, [personal.first_name, personal.last_name])) if personal else ''
    name = name or getattr(learner, 'username', '') or ''
    dob = getattr(personal, 'date_of_birth', None) if personal else None
    dob = dob or getattr(learner, 'date_of_birth', None)
    try:
        dob = date_value(str(dob)) if dob else None
    except PlrError:
        dob = None
    if name and report.get('name') and _normal(name) != _normal(report['name']):
        raise PlrError('The learner name in the PDF does not match this student. No records were imported.', 'identity_mismatch')
    if dob and report.get('dob') and dob != report['dob']:
        raise PlrError('The date of birth in the PDF does not match this student. No records were imported.', 'identity_mismatch')
    if not (name and report.get('name') and dob and report.get('dob')) and not reviewed:
        raise PlrError('The learner identity could not be fully verified. An authorized Admin must review the report and selected student before importing.', 'identity_review_required', 409)


@transaction.atomic(using='enrolment')
def import_report(kind, learner, report, entered_uln='', reviewed=False):
    row = lock(kind, learner.pk)
    pdf_uln = valid_uln(report['uln'])
    if (row.uln and row.uln != pdf_uln) or (entered_uln and entered_uln != pdf_uln):
        raise PlrError(ULN_MISMATCH, 'uln_mismatch', 409)
    validate_identity(report, learner, reviewed)
    assign_uln(row, pdf_uln)
    added, skipped = 0, 0
    for source in report['records']:
        fingerprint = hashlib.sha256(json.dumps(source, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        # Deleted imports remain tombstones, and edits never change source identity.
        if WizardPlrRecord.objects.filter(**scope(kind, learner.pk), source_fingerprint=fingerprint).exists():
            skipped += 1
            continue
        mapped = {key: source.get('provider' if key == 'placeOfStudy' else key) for key in FIELDS}
        WizardPlrRecord.objects.create(**scope(kind, learner.pk), record_ref=str(uuid.uuid4()),
            record_type='Imported', source_fingerprint=fingerprint, source_metadata=source, **values(mapped))
        added += 1
    touch(row)
    return {**state(kind, learner.pk), 'added': added, 'skipped': skipped}


@transaction.atomic(using='enrolment')
def save_uln(kind, learner_id, value, revision):
    row = lock(kind, learner_id)
    check_revision(row, revision)
    assign_uln(row, value)
    touch(row)
    return state(kind, learner_id)


@transaction.atomic(using='enrolment')
def change_record(kind, learner_id, ref, record, revision, delete=False):
    row = lock(kind, learner_id)
    check_revision(row, revision)
    item = WizardPlrRecord.objects.filter(**scope(kind, learner_id), record_ref=ref, deleted=False).first()
    if item is None:
        raise PlrError('Learning record not found.', 'not_found', 404)
    if delete:
        item.deleted = True
    else:
        for key, value in values(record).items():
            setattr(item, key, value)
    # Evidence and original source metadata/fingerprint are deliberately preserved.
    item.save()
    touch(row)
    return state(kind, learner_id)


def append_draft_records(kind, learner_id, plr):
    """Compatibility path: Save Progress may ADD manual entries, never replace PLR.

    Updates/deletions/ULNs use authorized revision-checked endpoints. An old or
    forged draft cannot edit an imported record or resurrect a deleted record.
    """
    row = lock(kind, learner_id)
    added = False
    for record in plr.get('records', []):
        ref = record.get('id', '')
        if not isinstance(ref, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', ref):
            raise PlrError('Invalid learning record identifier.')
        if WizardPlrRecord.objects.filter(**scope(kind, learner_id), record_ref=ref).exists():
            continue
        if record.get('recordType') != 'Manual':
            raise PlrError('Imported records can only be created by the authorized PDF import.', 'forbidden', 403)
        WizardPlrRecord.objects.create(**scope(kind, learner_id), record_ref=ref, record_type='Manual', **values(record))
        added = True
    if added:
        touch(row)
