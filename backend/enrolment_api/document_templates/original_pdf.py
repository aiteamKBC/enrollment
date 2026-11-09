"""Original PDF artifacts in existing immutable render history, never old blobs."""
import base64
import binascii
import copy
import json
import logging
from functools import wraps
from django.db import DatabaseError
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from enrolment_api import auth
from django.views.decorators.csrf import csrf_protect
from programme_suitability.permissions import is_staff
from . import service
from .models import IssuedTemplate, RenderHistory
from .registry import digest

logger = logging.getLogger(__name__)


def access(view):
    @csrf_protect
    @wraps(view)
    def wrapped(request, pk, *args, **kwargs):
        if not auth.is_authenticated(request): return JsonResponse({'error': 'Authentication required.'}, status=401)
        account = getattr(request, 'login_account', None)
        if account and account.role == 'employer':
            if not auth._employs(account, pk): return JsonResponse({'error': 'Not found.'}, status=404)
        elif not auth._may_access(request, view.__name__, {'pk': pk}): return JsonResponse({'error': 'Not found.'}, status=404)
        elif account is None and not is_staff(request): return JsonResponse({'error': 'Not found.'}, status=404)
        try: return view(request, pk, *args, **kwargs)
        except DatabaseError:
            logger.exception('Original issued PDF storage operation failed')
            return JsonResponse({'error': 'Could not store or read the original issued PDF. Retry the Issue action.'}, status=503)
    return wrapped


def artifact_fingerprint(payload):
    return digest({'kind': 'original-issued-pdf-v1', 'snapshot': payload})


@access
def original_pdf(request, pk, issue_id):
    if request.method not in ('GET', 'POST'): return JsonResponse({'error': 'Method not allowed.'}, status=405)
    issue = IssuedTemplate.objects.filter(pk=issue_id, learner_id=pk).first()
    if issue is None or issue.template_snapshot.get('originalPdfVersion') not in (1, 2):
        return JsonResponse({'error': 'Original issued PDF not found.'}, status=404)
    account = getattr(request, 'login_account', None)
    if account and account.role == 'employer' and issue.document_key == 'ilr':
        return JsonResponse({'error': 'Not found.'}, status=404)
    if request.method == 'GET':
        row = RenderHistory.objects.filter(issue=issue, payload__kind='original-issued-pdf-v1').order_by('recorded_at').first()
        if row is None: return JsonResponse({'error': 'The original PDF has not yet been stored.'}, status=404)
        result = HttpResponse(base64.b64decode(row.payload['pdf']), content_type='application/pdf')
        result['Content-Disposition'] = f'attachment; filename="{issue.document_key}-R1-original.pdf"'
        result['Cache-Control'] = 'private, no-store'
        return result
    if not is_staff(request): return JsonResponse({'error': 'Administrator access required.'}, status=403)
    if issue.template_snapshot.get('originalPdfVersion') == 2:
        return JsonResponse({'error': 'This original PDF is generated and stored by the server; client PDF replacement is not allowed.'}, status=409)
    try:
        data = json.loads(request.body)
        encoded = data.get('pdf')
        if not isinstance(encoded, str) or len(encoded) > 16 * 1024 * 1024: raise ValueError()
        pdf = base64.b64decode(encoded, validate=True)
        if not pdf.startswith(b'%PDF-') or b'%%EOF' not in pdf[-2048:]: raise ValueError()
    except (ValueError, TypeError, AttributeError, binascii.Error):
        return JsonResponse({'error': 'Provide a valid original PDF of at most 12 MB.'}, status=400)
    from .revisions import source_record, serialize_source, required_roles
    from learner_api.models import EnrolmentUser
    with transaction.atomic(using=service.database()):
        issue = IssuedTemplate.objects.select_for_update().get(pk=issue.pk)
        stored = RenderHistory.objects.filter(issue=issue, payload__kind='original-issued-pdf-v1').first()
        if stored:
            return JsonResponse({'stored': True, 'sha256': stored.payload['sha256']})
        learner = EnrolmentUser.all_learners.get(pk=pk)
        source = source_record(issue.document_key, pk, issue.source_id, lock=True)
        if source is None: return JsonResponse({'error': 'Issued source not found.'}, status=404)
        original = RenderHistory.objects.filter(issue=issue, payload__kind='original-issued-snapshot-v1').first()
        snapshot = original.payload['data'] if original else serialize_source(issue.document_key, source, learner)
        # Only first issuance can add this artifact. Never replace an old/signed PDF.
        if any(snapshot.get('signatures', {}).get(role, {}).get('signed') for role in required_roles(issue.document_key, snapshot)):
            return JsonResponse({'error': 'This document already has signatures; its historical original cannot be replaced.'}, status=409)
        if data.get('snapshot') != snapshot:
            return JsonResponse({'error': 'The document changed before its PDF was stored. Reload the issued document and retry.'}, status=409)
        import hashlib
        sha = hashlib.sha256(pdf).hexdigest()
        RenderHistory.objects.create(issue=issue, fingerprint=artifact_fingerprint(snapshot),
            payload={'kind': 'original-issued-pdf-v1', 'pdf': encoded, 'sha256': sha, 'snapshot': copy.deepcopy(snapshot)}, recorded_by=service.actor(request))
    return JsonResponse({'stored': True, 'sha256': sha}, status=201)
