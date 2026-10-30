"""Role-scoped adapters for the existing audit endpoints."""
from functools import wraps
import json
from urllib.parse import urlencode

from django.conf import settings
from django.db import DatabaseError
from django.http import FileResponse, HttpResponse, JsonResponse, StreamingHttpResponse
from django.middleware.csrf import get_token
from django.utils.http import content_disposition_header
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie

from audit_api.db_source import is_clone
from learner_api import evidence_storage
from learner_api.journal_sources import learner_journal_view
from login.permissions import login_required
from login.security import client_ip, user_agent
from login.sessions import authenticate_request

from . import repository as repo, service, storage


def failure(error):
    return JsonResponse({'error': str(error), 'code': error.code}, status=error.status)


def endpoint(*methods):
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            try:
                if is_clone():
                    raise service.ServiceError('Not found.', 'not_found', 404)
                if not service.enabled():
                    raise service.ServiceError('Previous record review is not enabled yet.', 'not_enabled', 503)
                if request.method not in methods:
                    raise service.ServiceError('Method not allowed.', 'method_not_allowed', 405)
                if request.method not in {'GET', 'HEAD', 'OPTIONS'} and service.is_monitor(request.login_account):
                    raise service.ServiceError('This account can view records only.', 'read_only', 403)
                response = learner_journal_view(view)(request, *args, **kwargs)
            except service.ServiceError as error:
                response = failure(error)
            except DatabaseError:
                response = failure(service.ServiceError('Previous learning records are temporarily unavailable. Please try again or contact support.', 'records_unavailable', 503))
            response['Cache-Control'] = 'private, no-store'
            response['Vary'] = 'Cookie'
            response['X-Content-Type-Options'] = 'nosniff'
            return response
        return login_required(csrf_protect(wrapped))
    return decorate


def payload(request):
    if request.content_type == 'multipart/form-data':
        return request.POST.dict()
    try:
        data = json.loads(request.body or b'{}')
    except (ValueError, UnicodeDecodeError):
        raise service.ServiceError('Invalid request body.')
    if not isinstance(data, dict):
        raise service.ServiceError('Invalid request body.')
    return data


def scope(request, data=None, learner_id=None):
    data = data or {}
    account = request.login_account
    # These assertions never select a student's identity. The stored
    # Created_users link is resolved before any browser value is considered.
    assertions = [v for v in [learner_id, *request.GET.getlist('aptem_id'), data.get('aptem_id')] if v is not None]
    if account.role == 'learner':
        learner = service.resolve_authenticated_learner(account)
        role = 'learner'
        if any(str(v).strip() != str(learner['aptem_id']) for v in assertions):
            raise service.ServiceError('Record not found.', 'not_found', 404)
        if any(k in request.GET or k in data for k in ('learner_id', 'email', 'actor', 'signer_role', 'roles')):
            raise service.ServiceError('Identity and signing role are determined by your account.', 'forbidden', 403)
        return learner, role
    selected = assertions[0] if assertions else None
    if any(str(v) != str(selected) for v in assertions):
        raise service.ServiceError('Record not found.', 'not_found', 404)
    return service.coach_learner(account, selected)


def safe_signature(signature):
    if not signature:
        return None
    return {'signed_at': signature['signed_at'], 'signer_name': signature['signer_name'],
            'url': f"/audit_api/old-otjh/signatures/{signature['file_id']}/"}


def public_state(data):
    # Strip private file references and historical signatures from every API.
    result = dict(data)
    for key in ('student_signature', 'coach_signature'):
        if key in result:
            result[key] = safe_signature(result[key])
    if 'months' in result:
        result['months'] = [public_state(m) for m in result['months']]
    return result


def public_detail(learner, data):
    for row in data.get('rows', []):
        for doc in row['documents']:
            if doc.get('source_evidence_id'):
                version = urlencode({'aptem_id': learner['aptem_id'], 'v': str(doc.get('source_updated_at') or '')})
                doc['url'] = f"/audit_api/old-otjh/source-documents/{row['id']}/{doc['source_evidence_id']}/{doc['source_kind']}/?{version}"
            else:
                doc['url'] = f"/audit_api/old-otjh/documents/{doc['id']}/?aptem_id={learner['aptem_id']}"
    return public_state(data)


@endpoint('GET')
@ensure_csrf_cookie
def csrf(request):
    return JsonResponse({'csrfToken': get_token(request)})


@endpoint('GET')
def summary(request):
    learner, _ = scope(request)
    return JsonResponse(public_state(service.summary(learner)))


@endpoint('POST')
def start(request):
    data = payload(request)
    if set(data) - {'aptem_id'}:
        raise service.ServiceError('Unexpected request fields.')
    learner, role = scope(request, data)
    return JsonResponse(public_state(service.start(learner, request.login_account, role)))


@endpoint('GET')
def workspace_link(request):
    """Resolve a workspace shortcut without ever returning a learner directory."""
    account = request.login_account
    if account.role == 'learner':
        # A learner cannot select a record through URL parameters or storage.
        learner, _ = scope(request)
        href = '/old-otjh/months'
    else:
        service.coach_actor(account)
        try:
            learner_id = int(request.GET.get('learner_id', ''))
        except (TypeError, ValueError):
            raise service.ServiceError('Choose a learner record.', 'not_found', 404)
        if not 0 < learner_id <= 9223372036854775807:
            raise service.ServiceError('Learner not found.', 'not_found', 404)
        learner = service.resolve_record(learner_id)
        if learner['aptem_id']:
            learner, _ = service.coach_learner(account, learner['aptem_id'])
        href = f"/old-otjh/coach/{learner['aptem_id']}/months?workspace=learner"
    return JsonResponse({'href': href if learner['aptem_id'] else None})


@endpoint('GET')
def cohort(request):
    if request.login_account.role == 'learner':
        learner, _ = scope(request)
        if not learner['aptem_id']:
            return JsonResponse({'learners': [], 'total': 0, 'page': 1})
        return JsonResponse({'learners': [{'id': learner['aptem_id'], 'name': learner['name'],
                                          'programme': learner['programme']}], 'total': 1, 'page': 1})
    return _coach_cohort(request)


def _coach_cohort(request):
    actor = service.coach_actor(request.login_account)
    if actor['role'] == 'monitor':
        return monitor_dashboard(request)
    try:
        page = max(1, min(100000, int(request.GET.get('page', 1))))
    except (ValueError, TypeError):
        raise service.ServiceError('Invalid page.')
    # Filtered in the query rather than in the page the client already has:
    # these lists run to hundreds of records, so a client-side filter would
    # search only the 25 rows on screen and appear to find nothing.
    search = (request.GET.get('search') or '').strip()[:100]

    # Reused rather than re-parsed: coach_api.auth already normalises the
    # parameter and knows both spellings the console sends.
    from coach_api.auth import _requested_view_as_email

    email, is_admin = actor['email'], actor['role'] == 'admin'
    # An admin with a coach's workspace open sees THAT coach's records, not the
    # whole cohort. Without this the page read as "Viewing Radwa Samir's
    # workspace" above a list of all 368 learners, which is both wrong and
    # impossible to tell apart from a coach who really does hold everybody.
    #
    # The named coach only narrows what an admin may already see, so it cannot
    # widen anyone's access: a coach stays pinned to their own email regardless
    # of what the parameter says.
    if is_admin:
        view_as = _requested_view_as_email(request)
        if view_as:
            email, is_admin = view_as, False

    return JsonResponse(
        repo.coach_learners(email, is_admin, page, search=search)
    )


@endpoint('GET')
def coach_learners(request):
    return _coach_cohort(request)


@endpoint('GET')
def monitor_dashboard(request):
    from .monitoring import dashboard
    actor = service.coach_actor(request.login_account)
    if actor['role'] not in {'admin', 'monitor'}:
        raise service.ServiceError('Monitoring access is required.', 'forbidden', 403)
    if getattr(settings, 'RECORD_MONITOR_SHARED_READ_MODEL_ENABLED', False):
        from .read_model import enqueue_record_monitor_refresh, get_record_monitor
        shared = get_record_monitor()
        records = (shared.payload or {}).get('records') if shared is not None else None
        if isinstance(records, list):
            response = JsonResponse(dashboard(
                request.GET,
                records=records,
                updated_at=(shared.payload or {}).get('updated_at'),
            ))
            response['X-LMS-Cache'] = 'READ-MODEL-STALE' if shared.stale else 'READ-MODEL-HIT'
            if shared.stale:
                enqueue_record_monitor_refresh(reason='stale-read')
            return response

    response = JsonResponse(dashboard(request.GET))
    if getattr(settings, 'RECORD_MONITOR_SHARED_READ_MODEL_ENABLED', False):
        from .read_model import enqueue_record_monitor_refresh
        enqueue_record_monitor_refresh(reason='missing-read-model')
        response['X-LMS-Cache'] = 'READ-MODEL-MISS'
    return response


@endpoint('GET')
def rows(request):
    learner, _ = scope(request)
    if 'activity_id' in request.GET:
        return JsonResponse(service.activity_content(learner, request.GET.get('month'), request.GET['activity_id']))
    data = service.month_detail(learner, request.GET.get('month'), demo=request.GET.get('demo') == '1')
    return JsonResponse(public_detail(learner, data))


@endpoint('GET', 'POST')
def signoff(request, learner_id):
    data = payload(request) if request.method == 'POST' else {}
    learner, role = scope(request, data, learner_id)
    month = request.GET.get('month') or data.get('month')
    if request.method == 'GET':
        detail = service.signoff_state(learner, month)
        return JsonResponse({'month': month, 'signoffs': {
            'learner': safe_signature(detail['student_signature']),
            'coach': safe_signature(detail['coach_signature'])}})
    if set(data) - {'aptem_id', 'month', 'confirmed', 'snapshot_digest', 'capture_method'} or set(request.FILES) != {'signature'}:
        raise service.ServiceError('Only your signature and review confirmation can be submitted.')
    if data.get('confirmed') not in (True, 'true') or data.get('capture_method') not in {'draw', 'upload', 'import'}:
        raise service.ServiceError('Confirm your signature before saving.')
    image = storage.sanitize(request.FILES.get('signature'))
    result = service.sign(learner, month, request.login_account, role, image,
                          data.get('snapshot_digest'), {'ip': client_ip(request),
                          'user_agent': user_agent(request), 'capture_method': data['capture_method']})
    return JsonResponse(public_detail(learner, result))


@endpoint('GET', 'POST')
def bulk_signoff(request):
    data = payload(request) if request.method == 'POST' else {}
    learner, role = scope(request, data)
    if request.method == 'GET':
        return JsonResponse(service.signing_review(learner, request.GET.get('month'), role))
    if set(data) - {'aptem_id', 'months', 'confirmed', 'capture_method'} or set(request.FILES) != {'signature'}:
        raise service.ServiceError('Only your signature and reviewed months can be submitted.')
    if data.get('confirmed') not in (True, 'true') or data.get('capture_method') not in {'draw', 'upload', 'import'}:
        raise service.ServiceError('Confirm your signature before saving.')
    try:
        months = json.loads(data['months']) if isinstance(data.get('months'), str) else data.get('months')
    except ValueError:
        raise service.ServiceError('Invalid reviewed months.')
    image = storage.sanitize(request.FILES.get('signature'))
    result = service.sign_months(learner, months, request.login_account, role, image,
                                {'ip': client_ip(request), 'user_agent': user_agent(request), 'capture_method': data['capture_method']})
    return JsonResponse({**result, 'summary': public_state(result['summary'])})


@endpoint('GET', 'POST')
def finalization(request):
    data = payload(request) if request.method == 'POST' else {}
    learner, role = scope(request, data)
    month = request.GET.get('month') or data.get('month')
    if request.method == 'GET':
        detail = service.month_detail(learner, month)
        detail.pop('rows')
        return JsonResponse(public_state(detail))
    if set(data) - {'aptem_id', 'month', 'action', 'reason'}:
        raise service.ServiceError('Unexpected request fields.')
    action = data.get('action', 'complete')
    if action == 'reopen':
        result = service.reopen(learner, month, request.login_account, role, data.get('reason'))
    elif action == 'complete':
        result = service.complete(learner, month, request.login_account, role)
    else:
        raise service.ServiceError('Unknown action.')
    return JsonResponse(public_detail(learner, result))


@endpoint('POST')
def refresh_months(request):
    data = payload(request)
    if set(data) - {'aptem_id', 'reason'}:
        raise service.ServiceError('Unexpected request fields.')
    learner, role = scope(request, data)
    return JsonResponse(public_state(service.refresh_months(learner, request.login_account, role, data.get('reason'))))


@endpoint('GET')
def content_review(request):
    learner, _ = scope(request)
    return JsonResponse(service.content_review(learner, request.GET.get('month')))


def require_document_month(learner, month):
    """Later Audit evidence is readable; transition signing stays capped."""
    from django.utils import timezone
    import re
    if (isinstance(month, str) and re.fullmatch(r'[0-9]{4}-(0[1-9]|1[0-2])', month)
            and repo.CUTOFF < month <= timezone.localdate().strftime('%Y-%m')):
        found = repo.query(f'SELECT id FROM {repo.ROWS} WHERE aptem_id=%s AND month=%s '
                           'AND deleted_at IS NULL LIMIT 1', [learner['aptem_id'], month])
        if found:
            return
    service.require_month(service.readable_transition(learner), month)


@endpoint('GET')
def material_document(request, row_id, material_id):
    from .content import source_ids, catalogue, backup_document
    learner, _ = scope(request)
    month = request.GET.get('month')
    require_document_month(learner, month)
    row = repo.activity_row(learner, month, row_id)
    if not row or material_id not in source_ids(row)[1]:
        raise service.ServiceError('Document not found.', 'not_found', 404)
    backup = catalogue({material_id}).get(material_id, {}).get('backup')
    if not backup:
        raise service.ServiceError('Document not found.', 'not_found', 404)
    doc = backup_document(learner, row, backup)
    if request.GET.get('preview') == 'office':
        return office_preview({'container': backup['blob_container'], 'blob_name': backup['blob_name']})
    try:
        download = evidence_storage._service_client().get_blob_client(
            container=backup['blob_container'], blob=backup['blob_name']).download_blob(
                connection_timeout=10, read_timeout=30, retry_total=0)
    except Exception:
        raise service.ServiceError('The saved source file could not be loaded. Please try again.', 'storage_unavailable', 503)
    response = StreamingHttpResponse(download.chunks(), content_type=doc['content_type'] or 'application/octet-stream')
    response['Content-Length'] = str(download.size)
    response['Content-Disposition'] = content_disposition_header(True, doc['display_name'])
    response['Content-Security-Policy'] = "sandbox; default-src 'none'"
    return response


@endpoint('GET')
def source_document(request, row_id, evidence_id, kind):
    learner, _ = scope(request)
    doc = next((d for d in repo.source_documents(learner, [row_id])
                if d['source_evidence_id'] == evidence_id and d['source_kind'] == kind), None)
    if not doc:
        raise service.ServiceError('Document not found.', 'not_found', 404)
    require_document_month(learner, doc['month'])
    if request.GET.get('preview') == 'office' and kind != 'note':
        return office_preview(doc)
    if kind == 'note':
        response = HttpResponse(doc['body'], content_type='text/plain; charset=utf-8')
    else:
        try:
            download = evidence_storage._service_client().get_blob_client(
                container=doc['container'], blob=doc['blob_name']).download_blob(connection_timeout=10, read_timeout=30)
        except Exception:
            raise service.ServiceError('The document store is temporarily unavailable.', 'storage_unavailable', 503)
        response = StreamingHttpResponse(download.chunks(), content_type=doc['content_type'] or 'application/octet-stream')
        response['Content-Length'] = str(download.size)
    response['Content-Disposition'] = content_disposition_header(True, doc['display_name'])
    response['Content-Security-Policy'] = "sandbox; default-src 'none'"
    return response


@endpoint('GET')
def document(request, doc_id):
    learner, _ = scope(request)
    doc = repo.document(learner, doc_id)
    if not doc:
        raise service.ServiceError('Document not found.', 'not_found', 404)
    require_document_month(learner, doc['month'])
    if request.GET.get('preview') == 'office':
        return office_preview(doc)
    try:
        download = evidence_storage._service_client().get_blob_client(
            container=doc['container'], blob=doc['blob_name']
        ).download_blob(connection_timeout=10, read_timeout=30)
    except Exception:
        raise service.ServiceError('The document store is temporarily unavailable.', 'storage_unavailable', 503)
    mime = doc['content_type'] or 'application/octet-stream'
    inline = request.GET.get('inline') == '1' and mime in {'application/pdf', 'image/png', 'image/jpeg'}
    response = StreamingHttpResponse(download.chunks(), content_type=mime)
    response['Content-Length'] = str(download.size)
    response['Content-Disposition'] = content_disposition_header(not inline, doc['display_name'])
    response['Content-Security-Policy'] = "sandbox; default-src 'none'"
    return response


def office_preview(doc):
    """The existing audit Office viewer, after learner/month authorization."""
    try:
        if not evidence_storage.azure_configured():
            raise ValueError('Document store unavailable')
        source = evidence_storage.get_read_sas(doc['container'], doc['blob_name'])
    except Exception:
        raise service.ServiceError('The document preview is temporarily unavailable.', 'storage_unavailable', 503)
    return JsonResponse({'url': 'https://view.officeapps.live.com/op/embed.aspx?' + urlencode({'src': source})})


@endpoint('GET')
def signature_file(request, file_id):
    owner = repo.signature_owner(file_id)
    if not owner:
        raise service.ServiceError('Signature not found.', 'not_found', 404)
    record = repo.student(owner['learner_id'])
    if not record:
        raise service.ServiceError('Signature not found.', 'not_found', 404)
    if request.login_account.role == 'learner':
        learner = service.resolve_authenticated_learner(request.login_account)
        if learner['id'] != owner['learner_id']:
            raise service.ServiceError('Signature not found.', 'not_found', 404)
    else:
        learner, _ = service.coach_learner(request.login_account, record['aptem_id'])
    service.require_month(service.readable_transition(learner), owner['report_month'])
    try:
        stream = storage.read(file_id)
    except Exception:
        raise service.ServiceError('The signature image is temporarily unavailable.', 'storage_unavailable', 503)
    return FileResponse(stream, content_type='image/png') if hasattr(stream, 'read') else StreamingHttpResponse(stream, content_type='image/png')


def dispatch(legacy_view, transition_view):
    """Keep the audit workspace contract; all student calls take the safe path.

    Staff opt into the transition contract with ?transition=1. A learner cannot
    opt out of it, nor use the old activity write handlers via the same URL.
    """
    @wraps(legacy_view)
    def wrapped(request, *args, **kwargs):
        account = authenticate_request(request)
        if request.GET.get('transition') == '1' or (account and (account.role == 'learner' or service.is_monitor(account))):
            return transition_view(request, *args, **kwargs)
        return legacy_view(request, *args, **kwargs)
    return wrapped
