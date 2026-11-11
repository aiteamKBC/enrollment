"""GET returns stored bytes only: viewing never regenerates historical PDFs."""
from django.http import JsonResponse
from learner_api.models import EnrolmentUser
from . import pdf_artifacts as pdf, revisions
from .models import IssuedTemplate, RenderHistory
from .original_pdf import access
from . import revision_views


@access
def issued_pdf(request, pk, issue_id):
    if request.method != 'GET': return JsonResponse({'error': 'Method not allowed.'}, status=405)
    issue = IssuedTemplate.objects.filter(pk=issue_id, learner_id=pk).first()
    account = getattr(request, 'login_account', None)
    if issue is None or account and account.role == 'employer' and issue.document_key == 'ilr':
        return JsonResponse({'error': 'Not found.'}, status=404)
    learner = EnrolmentUser.all_learners.filter(pk=pk).first()
    source = revisions.source_record(issue.document_key, pk, issue.source_id)
    if learner is None or source is None: return JsonResponse({'error': 'Not found.'}, status=404)
    data = revisions.serialize_source(issue.document_key, source, learner)
    row = (RenderHistory.objects.filter(issue=issue, fingerprint=request.GET['render'], payload__kind='branded-issued-pdf-v3', payload__revisionId='').first()
           if request.GET.get('render') else pdf.artifact(issue, data))
    result = pdf.http_pdf(row, issue.document_key + '-issued')
    return result if result is not None else JsonResponse({'error': 'This exact saved PDF is unavailable. It has not been regenerated or replaced.'}, status=409)


@revision_views.gate
def revision_pdf(request, pk, identifier):
    if request.method != 'GET': return JsonResponse({'error': 'Method not allowed.'}, status=405)
    revision = revision_views.document_revision(request, pk, identifier)
    if revision is None: return JsonResponse({'error': 'Not found.'}, status=404)
    data = revisions.payload(revision)
    row = (RenderHistory.objects.filter(issue=revision.issue, fingerprint=request.GET['render'], payload__kind='branded-issued-pdf-v3', payload__revisionId=str(revision.pk)).first()
           if request.GET.get('render') else pdf.artifact(revision.issue, data, revision.pk))
    if row is None and revision.state == 'original':
        original = pdf.artifact(revision.issue, revision.data)
        if original and (not request.GET.get('render') or request.GET['render'] == original.fingerprint): row = original
    result = pdf.http_pdf(row, f'{revision.issue.document_key}-R{revision.number}')
    return result if result is not None else JsonResponse({'error': 'The selected historical PDF is unavailable; it has not been regenerated.'}, status=409)
