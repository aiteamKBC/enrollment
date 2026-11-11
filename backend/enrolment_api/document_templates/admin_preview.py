"""Read-only row previews and version-checked delegation to existing signers."""
from django.core import signing
from django.db import transaction
from learner_api.models import EnrolmentUser
from . import revisions, service
from .models import RenderHistory
from .preview import render_payload
from .registry import TemplateError, digest, seed
from .views import body, error, gate, response

SALT = 'admin-issued-document-review-v1'


def resolve(key, learner, source_id, *, lock=False):
    seed(key)
    if not isinstance(source_id, (str, int)) or not str(source_id):
        raise TemplateError('Choose the exact issued document to preview.')
    source = revisions.source_record(key, learner.pk, source_id, lock=lock)
    active = revisions.source_record(key, learner.pk)
    if source is None or active is None or source.pk != active.pk:
        raise TemplateError('This issued document was replaced or is unavailable. Reload Documents before previewing or signing.')
    binding = service.binding_for(key, source)
    # The top-level row targets its original active source, never an R2/draft.
    # Signed reviews without a frozen snapshot cannot safely use live profile data.
    if key.startswith('review-') and any((source.learner_signature, source.admin_signature, source.employer_signature)):
        if binding is None or not binding.review_snapshot:
            raise TemplateError('This signed review has no frozen historical snapshot. Its exact content cannot safely be previewed here.')
    data = revisions.serialize_source(key, source, learner)
    template = data.get('template') or {**service.reference(seed(key)), 'legacy': True, 'fields': {}}
    data['template'] = template
    return source, binding, data


def identity(pk, key, source, data):
    return [pk, key, str(source.pk), digest(data)]


def original_url(pk, binding, data):
    if binding is None: return None
    original = RenderHistory.objects.filter(issue=binding, payload__kind='original-issued-pdf-v1').first()
    if original is None: return None
    # Never show the unsigned original in place of a partly/fully signed copy.
    # Metadata such as updatedAt is not rendered, but all content/signatures must match.
    def content(value):
        return {k: v for k, v in value.items() if k not in ('createdAt', 'updatedAt', 'startedAt', 'status')}
    if content(original.payload.get('snapshot', {})) != content(data): return None
    return f'/enrolment_api/document-templates/learners/{pk}/issued/{binding.pk}/original-pdf/'


@gate
def document_preview(request, pk, key):
    if request.method != 'GET': return error('Method not allowed.', 405)
    learner = EnrolmentUser.all_learners.filter(pk=pk).first()
    if learner is None: return error('Not found.', 404)
    seed(key)
    if request.GET.get('phase') == 'pre-issue':
        if revisions.source_record(key, pk) is not None:
            return error('This document has already been issued. Reload Documents to preview the issued version.', 409)
        selected = service.issuance_reference(key, learner)
        value = render_payload(selected['content'], learner)
        # The version and design are the same as the actual initial Issue resolver.
        value['template'].update(selected)
        value['data']['template'] = value['template']
        from .pdf_artifacts import render_preview
        value = render_preview(value)
        return response({**value, 'previewToken': None, 'pdfUrl': None,
            'notice': 'PRE-ISSUE PREVIEW — current published template and available learner data. Unsigned; no document or signature request has been created.'})
    try: source, binding, data = resolve(key, learner, request.GET.get('sourceId'))
    except TemplateError as exc: return error(exc, 409)
    token = signing.dumps(identity(pk, key, source, data), salt=SALT, compress=True)
    from . import pdf_artifacts as pdf
    saved_url = original_url(pk, binding, data)
    if pdf.branded(data):
        artifact = pdf.artifact(binding, data)
        if artifact is None:
            return error('This exact saved PDF is unavailable. It will not be regenerated when previewed. Please retry the operation that saved this document.', 409)
        saved_url = pdf.issued_url(pk, binding.pk, artifact.fingerprint)
    from .legacy_snapshot import can_render_original_snapshot, SNAPSHOT_NOTICE
    snapshot_preview = pdf.enabled() and not saved_url and can_render_original_snapshot(key, data)
    if pdf.enabled() and not pdf.branded(data) and not saved_url and not snapshot_preview:
        return error('The stored historical PDF is unavailable. It will not be regenerated or changed to the new design.', 409)
    version = data['template']['number']
    return response({'template': data['template'], 'data': data, 'sample': False, 'missingFields': [],
        'previewToken': token, 'pdfUrl': saved_url,
        'notice': SNAPSHOT_NOTICE if snapshot_preview else 'Issued original R1 · ' + (f'Template V{version}' if version else 'Original application template') +
                  '. Saved issued content and recorded signatures. Other versions are available in History.'})


@gate
def sign_reviewed_document(request, pk, key):
    if request.method != 'POST': return error('Method not allowed.', 405)
    data = body(request)
    party = 'admin' if key.startswith('review-') else 'provider'
    if party not in seed(key)['signatureRoles'] or data.get('party') != party:
        return error('You cannot sign as this document party.', 403)
    learner = EnrolmentUser.all_learners.filter(pk=pk).first()
    if learner is None: return error('Not found.', 404)
    try: reviewed = signing.loads(data.get('reviewToken', ''), salt=SALT, max_age=3600)
    except (signing.BadSignature, TypeError): return error('Preview this issued document again before signing.', 409)
    with transaction.atomic(using=service.database()):
        try: source, _, current = resolve(key, learner, data.get('sourceId'), lock=True)
        except TemplateError as exc: return error(exc, 409)
        if reviewed != identity(pk, key, source, current):
            return error('The document changed after preview. Preview the current issued document again before signing.', 409)
        if key.startswith('review-'):
            from learner_api.review_form import enrolment_review_sign
            # Same request, permissions, completion checks and signature writer.
            return enrolment_review_sign(request, kind='apprenticeship', pk=pk, event_key=source.event_key)
        _, _, signer = revisions.source_api(key)
        return signer(request, pk=pk, _revision_source=source)
