"""Django-owned generation/cache using the existing shared jsPDF renderer."""
import base64
import copy
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import DatabaseError
from django.http import HttpResponse
from .models import RenderHistory
from .registry import digest

BRAND = 'kbc-dfe-purple-v4'


def enabled():
    return getattr(settings, 'DOCUMENT_BRANDED_PDF_ENABLED', True)


def new_presentation(template):
    value = copy.deepcopy(template)
    if enabled(): value.update(presentationTheme=BRAND, originalPdfVersion=2)
    return value


def branded(data):
    return data.get('template', {}).get('presentationTheme') in ('kbc-dfe-purple-v3', BRAND)


def render(value):
    node = getattr(settings, 'DOCUMENT_PDF_NODE', None) or shutil.which('node')
    runtime = Path(__file__).with_name('pdf_runtime') / 'renderer.cjs'
    if not node or not runtime.is_file():
        raise DatabaseError('Document PDF runtime is unavailable. Deploy Node and run npm run build:document-pdf in frontend before issuing documents.')
    try:
        result = subprocess.run([str(node), str(runtime)], input=json.dumps(value, cls=DjangoJSONEncoder).encode(),
                                capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DatabaseError('Document PDF generation could not finish. No historical PDF was replaced; please retry.') from exc
    if result.returncode or not result.stdout.startswith(b'%PDF-') or b'%%EOF' not in result.stdout[-2048:]:
        raise DatabaseError('Document PDF rendering failed. Check the deployed document renderer; existing PDFs remain unchanged.')
    return result.stdout


def render_preview(value):
    if not enabled(): return value
    value = copy.deepcopy(value)
    value['template'] = new_presentation(value['template'])
    value['data']['template'] = value['template']
    value['pdfBase64'] = base64.b64encode(render(value)).decode()
    return value


def fingerprint(data, revision_id=None):
    # Operational timestamps/status are not printed. All text, answers, binding
    # values, signatures, provenance and template identity remain in the hash.
    content = {key: value for key, value in data.items() if key not in ('createdAt', 'updatedAt', 'startedAt', 'status', 'pdfUrl')}
    return digest({'kind': 'branded-issued-pdf-v3', 'revision': str(revision_id or ''), 'data': content})


def artifact(issue, data, revision_id=None):
    return RenderHistory.objects.filter(issue=issue, fingerprint=fingerprint(data, revision_id)).first()


def store(issue, data, *, revision_id=None, actor='Document PDF renderer'):
    if not branded(data): return None
    saved = artifact(issue, data, revision_id)
    if saved: return saved
    pdf = render({'template': data['template'], 'data': data})
    encoded = base64.b64encode(pdf).decode()
    saved, _ = RenderHistory.objects.get_or_create(issue=issue, fingerprint=fingerprint(data, revision_id),
        defaults={'recorded_by': actor, 'payload': {'kind': 'branded-issued-pdf-v3', 'pdf': encoded,
            'sha256': hashlib.sha256(pdf).hexdigest(), 'snapshot': copy.deepcopy(data), 'revisionId': str(revision_id or '')}})
    return saved


def store_source(issue, source, learner, *, original=False, actor='Document PDF renderer'):
    from .revisions import serialize_source, required_roles
    data = serialize_source(issue.document_key, source, learner)
    saved = store(issue, data, actor=actor)
    if saved and original and not any(data.get('signatures', {}).get(role, {}).get('signed') for role in required_roles(issue.document_key, data)):
        RenderHistory.objects.get_or_create(issue=issue, fingerprint=digest({'kind': 'original-issued-snapshot-v1', 'data': data}),
            defaults={'payload': {'kind': 'original-issued-snapshot-v1', 'data': data}, 'recorded_by': actor})
        # Backwards-compatible original endpoint, now generated only by Django.
        from .original_pdf import artifact_fingerprint
        RenderHistory.objects.get_or_create(issue=issue, fingerprint=artifact_fingerprint(data), defaults={
            'payload': {**saved.payload, 'kind': 'original-issued-pdf-v1'}, 'recorded_by': actor})
    return saved


def store_revision(revision):
    from .revisions import payload
    return store(revision.issue, payload(revision), revision_id=revision.pk, actor=revision.updated_by)


def issued_url(pk, issue_id, render_id=None):
    return f'/enrolment_api/document-templates/learners/{pk}/issued/{issue_id}/pdf/' + (f'?render={render_id}' if render_id else '')


def revision_url(pk, revision_id, render_id=None):
    return f'/enrolment_api/document-templates/learners/{pk}/revisions/{revision_id}/pdf/' + (f'?render={render_id}' if render_id else '')


def http_pdf(row, filename):
    if row is None: return None
    result = HttpResponse(base64.b64decode(row.payload['pdf']), content_type='application/pdf')
    result['Content-Disposition'] = f'inline; filename="{filename}.pdf"'
    result['Cache-Control'] = 'private, no-store'
    result['X-Content-Type-Options'] = 'nosniff'
    return result
