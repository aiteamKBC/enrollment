"""Read-only, Admin-only PDF preview. No document storage or PLR persistence."""
import re
import unicodedata
from django.core.exceptions import RequestDataTooBig, TooManyFieldsSent, TooManyFilesSent
from django.core.files.uploadhandler import MemoryFileUploadHandler, StopUpload
from django.db import DatabaseError
from django.http import JsonResponse
from django.http.multipartparser import MultiPartParserError
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from .auth import enrolment_login_required
from .models import WizardPersonalDetails
from .plr_uln_storage import read_uln
from .plr_pdf import MAX_BYTES, PlrError, date_value, parse_bounded
from .plr_uln import can_edit_uln, valid_uln
from .wizard_steps import log_plr_error


def response(data, status=200):
    result = JsonResponse(data, status=status)
    result['Cache-Control'] = 'no-store, private'
    result['X-Content-Type-Options'] = 'nosniff'
    return result


class PreviewMemoryUploadHandler(MemoryFileUploadHandler):
    """Never fall back to a temporary disk upload, even under deployment overrides."""
    def handle_raw_input(self, *args, **kwargs):
        self.activated = True

    def receive_data_chunk(self, raw_data, start):
        if start + len(raw_data) > MAX_BYTES:
            raise StopUpload(connection_reset=True)
        return super().receive_data_chunk(raw_data, start)


def _normal(value):
    return ' '.join(re.findall(r'\w+', unicodedata.normalize('NFKC', value or '').casefold()))


def build_preview(report, learner, kind, entered_uln=''):
    scope = {'learner_kind': kind, 'learner_id': learner.pk}
    saved_uln = read_uln(kind, learner.pk)
    if not valid_uln(entered_uln):
        raise PlrError('The entered ULN must be empty or contain exactly 10 digits.', 'invalid_uln')
    if (saved_uln and saved_uln != report['uln']) or (entered_uln and entered_uln != report['uln']):
        raise PlrError('The PDF ULN does not match the selected student. No data has been saved.', 'uln_mismatch', 409)
    personal = WizardPersonalDetails.objects.filter(**scope).first()
    name = ' '.join(filter(None, [personal.first_name, personal.last_name])) if personal and personal.first_name and personal.last_name else ''
    name = name or getattr(learner, 'username', '') or ''
    dob = (personal.date_of_birth if personal else None) or getattr(learner, 'date_of_birth', None)
    try:
        dob = date_value(dob) if dob else None
    except PlrError:
        dob = None
    if name and report.get('name') and _normal(name) != _normal(report['name']):
        raise PlrError('The learner name in the PDF does not match the selected student.', 'identity_mismatch', 409)
    if dob and report.get('dob') and dob != report['dob']:
        raise PlrError('The date of birth in the PDF does not match the selected student.', 'identity_mismatch', 409)
    warnings = []
    verified = bool(name and report.get('name') and dob and report.get('dob'))
    if not verified:
        warnings.append('Learner identity could not be fully verified from the available name and date of birth. Review is required before any future import.')
    if not saved_uln:
        warnings.append('The selected student has no saved ULN. The extracted ULN has not been saved.')
    records = []
    for raw in report['records']:
        records.append({
            'placeOfStudy': raw.get('provider', ''), 'qualificationType': raw.get('qualificationType', ''),
            'subject': raw.get('subject', ''), 'level': raw.get('level', ''),
            'awardDate': raw.get('awardDate') or '', 'credits': raw.get('credits', ''),
            'grade': raw.get('grade', ''), 'recordType': 'Imported',
            'sourceSection': raw['sourceSection'], 'awardingBody': raw.get('awardingBody', ''),
            'referenceNumber': raw.get('referenceNumber', ''), 'startDate': raw.get('startDate') or '',
            'endDate': raw.get('endDate') or '', 'completionStatus': raw.get('completionStatus', ''),
            'outcomeStatus': raw.get('outcomeStatus', ''), 'language': raw.get('language', ''),
        })
    if any(not r['placeOfStudy'] or not r['subject'] for r in records):
        warnings.append('Some report rows have blank providers or qualification titles. These fields have been left blank.')
    counts = {key: sum(r['sourceSection'] == section for r in records)
              for key, section in [('learningEvents', 'Learning Events'), ('achievements', 'Achievements')]}
    if any(value is None for value in report.get('reportCounts', {}).values()):
        warnings.append('Some sections have no report count; their extracted totals could not be checked against a printed count.')
    return {'previewOnly': True, 'uln': report['uln'], 'learnerName': report.get('name', ''),
            'dateOfBirth': report.get('dob') or '', 'identityVerified': verified,
            'records': records, 'counts': counts, 'warnings': warnings}


@csrf_protect
def _post_preview(request, kind, learner_id):
    from .extended_ilr import KINDS
    try:
        learner = KINDS[kind].objects.filter(pk=learner_id).first()
        if learner is None:
            return response({'error': 'Learner not found.'}, 404)
        report = read_uploaded_report(request)
        return response(build_preview(report, learner, kind, request.POST.get('enteredUln', '')))
    except PlrError as exc:
        return response({'error': str(exc), 'code': exc.code}, exc.status)
    except DatabaseError as exc:
        log_plr_error(exc, 'PDF preview validation')
        return response({'error': 'The selected student could not be verified. Please retry the PDF preview.'}, 503)


def read_uploaded_report(request):
    """Reparse original PDF bytes for preview or confirmation, never client records."""
    if len(request.FILES.getlist('file')) != 1 or len(request.FILES) != 1:
        raise PlrError('Select one PDF file no larger than 8 MB.')
    file = request.FILES['file']
    try:
        if (not file.name.lower().endswith('.pdf') or file.content_type not in ('application/pdf', 'application/octet-stream')
                or not 0 < file.size <= MAX_BYTES):
            raise PlrError('Select one PDF file no larger than 8 MB.')
        data = file.read(MAX_BYTES + 1)
    finally:
        file.close()
    return parse_bounded(data)


@enrolment_login_required
@csrf_exempt
def plr_pdf_preview(request, kind, learner_id):
    from .extended_ilr import KINDS
    if not can_edit_uln(request):
        return response({'error': 'Only an authorized enrolment Admin can preview PLR PDFs.'}, 403)
    if kind not in KINDS:
        return response({'error': 'Learner not found.'}, 404)
    if request.method == 'GET':
        return response({'csrfToken': get_token(request)})
    if request.method != 'POST':
        return response({'error': 'Method not allowed.'}, 405)
    try:
        if int(request.META.get('CONTENT_LENGTH') or 0) > MAX_BYTES + 65536:
            return response({'error': 'Select one PDF file no larger than 8 MB.'}, 413)
        # Configure memory-only handling before the inner CSRF check parses POST.
        request.upload_handlers = [PreviewMemoryUploadHandler(request)]
        return _post_preview(request, kind, learner_id)
    except (RequestDataTooBig, TooManyFieldsSent, TooManyFilesSent, MultiPartParserError, ValueError):
        return response({'error': 'The PDF upload could not be read. Select one PDF no larger than 8 MB.'}, 400)
