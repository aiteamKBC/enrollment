"""Atomic PDF confirmation using the existing PLR rows and protected JSON."""
from collections import Counter
import hashlib
import json
from django.core.exceptions import RequestDataTooBig, TooManyFieldsSent, TooManyFilesSent
from django.db import DatabaseError, IntegrityError, transaction
from django.http.multipartparser import MultiPartParserError
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from .auth import enrolment_login_required
from .models import ExtendedIlr, WizardPlrRecord
from .plr_uln_storage import write_uln
from .plr_pdf import MAX_BYTES, PlrError
from .plr_pdf_preview import PreviewMemoryUploadHandler, build_preview, read_uploaded_report, response
from .plr_record_changes import DELETED_KEY, IMPORT_KEY, FIELDS, RecordChangeError, normalized
from .plr_uln import can_edit_uln, reserve_uln, UlnAssignmentError, valid_uln
from .wizard_steps import read_plr, log_plr_error


@transaction.atomic(using='enrolment')
def import_report(report, kind, learner_id, entered_uln=''):
    from .extended_ilr import KINDS
    if not valid_uln(report.get('uln')) or not report.get('uln'):
        raise PlrError('The PDF contains no valid ULN.', 'invalid_uln')
    # Always lock ULN assignment before the learner; manual ULN updates use
    # the same order. PostgreSQL holds this advisory lock until commit/rollback.
    reserve_uln(report['uln'], learner_id)
    learner = KINDS[kind].objects.select_for_update().filter(pk=learner_id).first()
    if learner is None:
        raise PlrError('Learner not found.', 'not_found', 404)
    preview = build_preview(report, learner, kind, entered_uln)
    if not preview['identityVerified']:
        raise PlrError('The learner name and date of birth could not be fully verified. No data was saved.', 'identity_unverified', 409)
    scope = {'learner_kind': kind, 'learner_id': learner_id}
    current = read_plr(kind, learner_id)
    if current.get('loadError'):
        raise PlrError(current['loadError'], 'plr_unavailable', 503)
    # Validate every mapped row before changing ULN or qualification data.
    mapped = []
    for record in preview['records']:
        try:
            values = normalized(record)
        except RecordChangeError as exc:
            raise PlrError(f'{exc} No data was saved.', 'unsupported_record') from None
        mapped.append({field: values[key] if key == 'credits' else values[key] or None for key, field in FIELDS.items()})
    stored, _ = ExtendedIlr.objects.get_or_create(**scope, defaults={'learner_name': learner.username})
    draft = dict(stored.wizard_draft or {})
    deleted = draft.get(DELETED_KEY) or {}
    sources = dict(draft.get(IMPORT_KEY) or {})
    occurrences = Counter()
    added = skipped = 0
    planned = []
    for raw, values in zip(report['records'], mapped):
        digest = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        occurrence = occurrences[digest]; occurrences[digest] += 1
        ref = f'import-{learner_id}-{digest}-{occurrence}'
        planned.append((ref, raw, values))
    existing = set(WizardPlrRecord.objects.filter(**scope, record_ref__in=[ref for ref, _, _ in planned]).values_list('record_ref', flat=True))
    inserts = []
    for ref, raw, values in planned:
        if ref in sources or ref in deleted or ref in existing:
            skipped += 1
            continue
        inserts.append(WizardPlrRecord(**scope, record_ref=ref, record_type='Imported', **values))
        # Immutable source values retain completion/outcome and stable identity
        # after Admin edits. No PDF, learner name or DOB is retained here.
        sources[ref] = raw
        added += 1
    WizardPlrRecord.objects.bulk_create(inserts, batch_size=100)
    write_uln(kind, learner_id, report['uln'])
    draft[IMPORT_KEY] = sources
    stored.wizard_draft = draft
    stored.save(update_fields=['wizard_draft', 'updated_at'])
    canonical = read_plr(kind, learner_id)
    if canonical.get('loadError'):
        raise PlrError(canonical['loadError'], 'plr_unavailable', 503)
    stored.wizard_draft = {**draft, 'plr': canonical}
    stored.save(update_fields=['wizard_draft', 'updated_at'])
    return {'imported': True, 'plr': canonical, 'added': added, 'skipped': skipped,
            'message': f'PLR import complete. {added} records imported and {skipped} records skipped.'}


@csrf_protect
def _post_import(request, kind, learner_id):
    try:
        report = read_uploaded_report(request)
        return response(import_report(report, kind, learner_id, request.POST.get('enteredUln', '')))
    except PlrError as exc:
        return response({'error': str(exc), 'code': exc.code}, exc.status)
    except UlnAssignmentError as exc:
        return response({'error': str(exc), 'code': 'duplicate_uln'}, 409)
    except IntegrityError:
        return response({'error': 'The PLR changed during import. No data was saved. Please reload and retry.'}, 409)
    except DatabaseError as exc:
        log_plr_error(exc, 'PDF import')
        return response({'error': 'PLR import failed. No ULN or records were saved. Please retry.'}, 503)


@enrolment_login_required
@csrf_exempt
def plr_pdf_import(request, kind, learner_id):
    from .extended_ilr import KINDS
    if not can_edit_uln(request):
        return response({'error': 'Only an authorized enrolment Admin can import PLR PDFs.'}, 403)
    if kind not in KINDS:
        return response({'error': 'Learner not found.'}, 404)
    if request.method != 'POST':
        return response({'error': 'Method not allowed.'}, 405)
    try:
        if int(request.META.get('CONTENT_LENGTH') or 0) > MAX_BYTES + 65536:
            return response({'error': 'Select one PDF file no larger than 8 MB.'}, 413)
        request.upload_handlers = [PreviewMemoryUploadHandler(request)]
        return _post_import(request, kind, learner_id)
    except (RequestDataTooBig, TooManyFieldsSent, TooManyFilesSent, MultiPartParserError, ValueError):
        return response({'error': 'The PDF upload could not be read. No data was saved.'}, 400)
