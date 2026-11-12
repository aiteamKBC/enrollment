"""Learner-specific immutable copies over the existing document/signature models."""
import copy
import json
import time
import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import DatabaseError, connections, transaction
from django.db.models import Max
from django.utils import timezone
from . import service
from .models import IssuedTemplate, LearnerDocumentRevision as Revision
from .registry import REVIEW_KEYS, TemplateError, digest, seed, validate
from .question_fields import preserve_system_fields
from . import blank_documents

_AVAILABLE = False
_CHECKED = 0.0


class RevisionLocked(DatabaseError):
    pass


def ready(force=False):
    global _AVAILABLE, _CHECKED
    db = connections[service.database()]
    if not getattr(settings, 'DOCUMENT_REVISIONS_ENABLED', db.vendor == 'postgresql'): return False
    if not service.ready(force=force): return False
    if db.vendor == 'sqlite': return True
    if force or (not _AVAILABLE and time.monotonic() - _CHECKED > 10):
        with db.cursor() as cursor:
            cursor.execute('SELECT to_regclass(%s)', ['enrolment."Learner_Document_Revisions"'])
            _AVAILABLE = bool(cursor.fetchone()[0])
        _CHECKED = time.monotonic()
    return _AVAILABLE


def source_api(key):
    from learner_api import apprenticeship_agreement as aa, ilr_document as ilr, training_plan_document as tp, written_agreement as wa, review_form
    from learner_api.models import ApprenticeshipAgreement, IlrDocument, TrainingPlanDocument, WrittenAgreement, EnrolmentReview
    return {
        'apprenticeship-agreement': (ApprenticeshipAgreement, aa._agreement_json, aa.sign_agreement),
        'ilr': (IlrDocument, ilr._document_json, ilr.sign_ilr),
        'training-plan': (TrainingPlanDocument, tp._document_json, tp.sign_training_plan),
        'written-agreement': (WrittenAgreement, wa._document_json, wa.sign_written_agreement),
    }.get(key, (EnrolmentReview, review_form._serialize_form, None))


def source_record(key, learner_id, source_id=None, lock=False):
    original = seed(key)
    model, _, _ = source_api(key)
    query = model.objects.filter(learner_id=learner_id)
    if key.startswith('review-'): query = query.filter(review_type=original['reviewType'])
    if source_id is not None:
        try:
            query = query.filter(event_key=source_id) if key.startswith('review-') and not str(source_id).isdigit() else query.filter(pk=source_id)
        except (ValueError, TypeError, ValidationError) as exc: raise TemplateError('Invalid source document identifier.') from exc
    else:
        query = query.exclude(status='cancelled') if key.startswith('review-') else query.filter(status='active')
    return (query.select_for_update() if lock else query).order_by('-id' if key.startswith('review-') else '-created_at').first()


def serialize_source(key, source, learner):
    _, serializer, _ = source_api(key)
    return serializer(source, learner, None) if key.startswith('review-') else serializer(source)


def required_roles(key, data):
    roles = list(seed(key)['signatureRoles'])
    if key.startswith('review-') and not data.get('signatures', {}).get('employer', {}).get('required'): roles.remove('employer')
    return roles


def independently_signed(key, data):
    return all(bool(data.get('signatures', {}).get(role, {}).get('signed')) for role in required_roles(key, data))


def current_revision(key, document):
    """Latest issued copy of this active root; drafts never supersede it."""
    if document is None or not ready(): return None
    return Revision.objects.select_related('issue').filter(issue__document_key=key,
        issue__source_id=str(document.pk), issue__learner_id=document.learner_id,
        issue__learner_kind=document.learner_kind, state='finalized').order_by('-number').first()


def current_signoff(key, document):
    revision = current_revision(key, document)
    if revision is None: return None
    if revision.signature_method == 'sign-again' and (not revision.signing_source_id
            or source_record(key, document.learner_id, revision.signing_source_id) is None):
        return {'fullySigned': False, 'signatures': {}}
    return payload(revision)


def get_revision(identifier, learner_id, lock=False):
    query = Revision.objects.filter(pk=identifier, issue_id__in=IssuedTemplate.objects.filter(learner_id=learner_id).values('pk'))
    # PostgreSQL FOR UPDATE OF rejects this legacy schema-qualified db_table
    # name. Lock the revision alone without joined nullable base rows or OF.
    return query.select_for_update().first() if lock else query.select_related('issue', 'base').first()


def frozen_fields(key, learner, payload):
    fields = service.field_values(key, learner, payload)
    # For pre-builder records, do not invent historical common values from a
    # learner's current placement. Original document-specific snapshots win.
    info = payload.get('learnerInformation') or {}; particulars = payload.get('particulars') or {}; programme = payload.get('programme') or {}
    details = payload.get('learnerDetails') or {}; employment = payload.get('employment') or {}
    fields.update({'learner.fullName': info.get('name') or particulars.get('apprenticeName') or programme.get('apprenticeName') or ' '.join(filter(None, [details.get('givenNames'), details.get('familyName')])),
        'programme.name': info.get('programmeName') or particulars.get('standard') or particulars.get('apprenticeshipTitle') or programme.get('programme') or '',
        'programme.cohort': '', 'employer.name': info.get('employer') or particulars.get('employerName') or particulars.get('employer') or employment.get('employerName') or '',
        'employer.manager': info.get('manager') or employment.get('lineManager') or particulars.get('managerName') or '',
        'enrolment.startDate': particulars.get('startDate') or programme.get('startDate') or info.get('programmeStartDate') or '',
        'enrolment.endDate': particulars.get('endDate') or particulars.get('plannedEndDate') or programme.get('endDate') or info.get('plannedEndDate') or ''})
    return fields


def original_binding(key, source, learner):
    binding = service.binding_for(key, source)
    if key.startswith('review-') and (binding is None or not binding.review_snapshot):
        raise TemplateError('This review has no frozen historical render snapshot. Its earlier signed content cannot safely be reconstructed for editing.')
    if binding is not None: return binding
    payload = serialize_source(key, source, learner)
    return IssuedTemplate.objects.create(document_key=key, source_id=str(source.pk), learner_id=learner.pk,
        learner_kind=source.learner_kind, template_snapshot=service.reference(seed(key)),
        field_values=frozen_fields(key, learner, payload))


def create_draft(key, source_id, learner, actor, base_id=None, *, mode='edit', request_id=None):
    if not ready(): raise TemplateError('Learner document revision storage must be deployed before editing.')
    if mode not in ('edit', 'blank', 'template', 'learner'): raise TemplateError('Choose a supported document creation mode.')
    creating = mode in ('blank', 'template', 'learner')
    if creating:
        if base_id is not None: raise TemplateError('A blank document cannot copy a source revision.')
        try: request_id = str(uuid.UUID(str(request_id)))
        except (ValueError, TypeError, AttributeError) as exc: raise TemplateError('Provide a unique blank document request identifier.') from exc
    with transaction.atomic(using=service.database()):
        source = source_record(key, learner.pk, source_id, lock=True)
        if source is None: raise TemplateError('Issued document not found for this learner.')
        issue = original_binding(key, source, learner)
        issue = IssuedTemplate.objects.select_for_update().get(pk=issue.pk)
        existing = Revision.objects.filter(issue=issue, state='draft').first()
        if creating:
            retried = Revision.objects.filter(issue=issue, changes__creationRequestId=request_id).first()
            if retried:
                if retried.state == 'draft': return retried
                raise RevisionLocked('This Create request has already been completed. Reload before creating another document.')
            old_empty = existing and existing.changes.get('creationMode') == 'blank' and existing.content['blocks'] == [{'id': 'blank-page', 'type': 'paragraph', 'text': ''}]
            old_questions = (existing and existing.changes.get('creationMode') == 'template'
                             and existing.data.get('documentTemplateSnapshot')
                             and existing.content == blank_documents.initial_content(existing))
            reusable = (mode in ('template', 'learner') and existing and existing.edit_version == 1
                        and not existing.reason and not existing.provenance and can_discard(existing)
                        and (old_empty or mode == 'learner' and old_questions))
            if existing and not reusable: raise RevisionLocked('Another draft is open for this document. Use Continue editing, or finalise or delete that draft before creating a new document.')
        elif existing and base_id is None and not blank_documents.is_blank(existing):
            return existing  # Legacy callers resume their existing editing workflow.
        original = Revision.objects.filter(issue=issue, number=1).first()
        if original is None:
            data = serialize_source(key, source, learner)
            original = Revision.objects.create(issue=issue, number=1, state='original', signature_method='original',
                content=issue.template_snapshot['content'], data=data, created_by=actor, updated_by=actor,
                finalized_at=timezone.now(), reason='Original issued document snapshot', checksum=digest(data))
        if creating: base = original
        elif base_id:
            try: base_id = uuid.UUID(str(base_id))
            except (ValueError, TypeError, AttributeError) as exc: raise TemplateError('Invalid source revision identifier.') from exc
            base = Revision.objects.filter(pk=base_id, issue=issue, state__in=('original', 'finalized')).first()
            if base is None: raise TemplateError('Source revision not found for this document.')
        else: base = Revision.objects.filter(issue=issue, state__in=('original', 'finalized')).order_by('-number').first()
        if existing and not creating:
            if existing.base_id == base.pk and not blank_documents.is_blank(existing): return existing
            raise RevisionLocked('A draft from another version is open. Use Continue editing, or finalise or delete that draft before editing this exact version.')
        data = payload(base)
        from .pdf_artifacts import enabled, BRAND
        if enabled(): data['documentPresentationTheme'] = BRAND
        number = (Revision.objects.filter(issue=issue).aggregate(n=Max('number'))['n'] or 1) + 1
        if creating:
            if mode == 'learner':
                from .learner_documents import snapshot_data
                snapshot = service.applicable(key, learner)
                content = copy.deepcopy(snapshot['content'])
                data = snapshot_data(learner, content, snapshot)
            elif mode == 'template':
                snapshot = service.applicable(key, learner)
                content = blank_documents.unanswered_content(snapshot['content'])
                data = blank_documents.template_data(learner, required_roles(key, data), content, snapshot)
            else:
                content = blank_documents.content_for(issue.template_snapshot['content'])
                data = blank_documents.data_for(learner, required_roles(key, data))
                data['documentFieldValues'] = service.field_values(key, learner, data)
            changes = {'creationMode': mode, 'creationRequestId': request_id}
            if existing:
                changes['initializedFromEmptyDraft'] = {'requestId': existing.changes.get('creationRequestId'), 'by': actor, 'at': timezone.now().isoformat()}
                existing.content = content; existing.data = data; existing.changes = changes
                existing.signature_method = 'sign-again'; existing.updated_by = actor; existing.edit_version += 1
                existing.checksum = digest({'content': content, 'data': data, 'changes': changes})
                existing.save()
                return existing
            return Revision.objects.create(issue=issue, base=base, number=number, content=content, data=data,
                signature_method='sign-again', changes=changes, checksum=digest({'content': content, 'data': data, 'changes': changes}),
                created_by=actor, updated_by=actor)
        provenance = {}
        for role in required_roles(key, data):
            state = data.get('signatures', {}).get(role, {})
            if state.get('signed'):
                inherited = data.get('signatureProvenance', {}).get(role)
                provenance[role] = inherited or {'sourceRevisionId': str(base.pk), 'sourceRevision': base.number,
                    'name': state.get('name', ''), 'signedAt': state.get('signedAt'), 'approvesRevisedContent': False}
        return Revision.objects.create(issue=issue, base=base, number=number, content=copy.deepcopy(base.content),
            data=data, provenance=provenance, created_by=actor, updated_by=actor)


def payload(revision):
    data = copy.deepcopy(revision.data)
    key = revision.issue.document_key
    if revision.signature_method == 'sign-again' and revision.signing_source_id:
        from learner_api.models import EnrolmentUser
        learner = EnrolmentUser.all_learners.get(pk=revision.issue.learner_id)
        source = source_record(key, learner.pk, revision.signing_source_id)
        if source is not None:
            signed = serialize_source(key, source, learner)
            if blank_documents.is_blank(revision):
                # Only the new signature evidence comes from the signing row.
                data.update({k: signed[k] for k in ('signatures', 'marks', 'fullySigned') if k in signed})
            else: data = signed
    template = copy.deepcopy(revision.data.get('documentTemplateSnapshot', revision.issue.template_snapshot))
    template['content'] = copy.deepcopy(revision.content)
    if revision.data.get('documentPresentationTheme'):
        template['presentationTheme'] = revision.data['documentPresentationTheme']
        template['originalPdfVersion'] = 2
    template['fields'] = copy.deepcopy(revision.data.get('documentFieldValues', revision.issue.field_values))
    template['issueId'] = str(revision.issue_id)
    if revision.number > 1:
        template['legacyEquivalent'] = False
        template['contentHash'] = digest(revision.content)
        template['sourceContentHash'] = revision.data.get('documentTemplateSnapshot', revision.issue.template_snapshot).get('contentHash')
    data['template'] = template
    data['documentRevision'] = {'id': str(revision.pk), 'number': revision.number,
        'templateVersionId': template['id'], 'templateVersionNumber': template['number'], 'signatureMethod': revision.signature_method}
    if revision.number > 1 and revision.signature_method != 'sign-again':
        data['signatureProvenance'] = copy.deepcopy(revision.provenance)
        data['fullySigned'] = False
        data['independentlySigned'] = False
    elif revision.signature_method == 'sign-again':
        if not revision.signing_source_id:
            for role in seed(key)['signatureRoles']:
                previous = data.get('signatures', {}).get(role, {})
                data.setdefault('signatures', {})[role] = {**previous, 'signed': False, 'name': '', 'signedAt': None, 'signature': ''}
                data.setdefault('marks', {})[role] = ''
            data['fullySigned'] = False
        data.pop('signatureProvenance', None)
        data['independentlySigned'] = bool(revision.signing_source_id and independently_signed(key, data))
    for role in seed(key)['signatureRoles']:
        state = data.get('signatures', {}).get(role, {})
        template['fields'][f'signature.{role}.name'] = state.get('name') or ''
        template['fields'][f'signature.{role}.date'] = state.get('signedAt') or ''
    return data


def preview_response(revision):
    data = payload(revision)
    notice = (f'Learner-specific document Revision R{revision.number}. No historical signatures were copied into this new document.'
              if blank_documents.is_blank(revision) else f'Learner-specific Revision R{revision.number}. Historical signature references are identified in the document.')
    from .registry import TOKEN, seeds
    fields = data['template']['fields']
    missing = [{'field': ref, 'label': seeds()['fields'][revision.issue.document_key][ref]['label']}
               for ref in sorted(set(TOKEN.findall(str(revision.content['blocks'])))) if fields.get(ref) in (None, '')]
    result = {'template': data['template'], 'data': data, 'missingFields': missing, 'sample': False,
        'bindingWarnings': data.get('bindingWarnings', []), 'notice': notice}
    from . import pdf_artifacts as pdf
    stored = pdf.enabled() or pdf.branded(data)
    row = pdf.artifact(revision.issue, data, revision.pk) if stored else None
    if row is None and stored and revision.state == 'original': row = pdf.artifact(revision.issue, revision.data)
    if row: result['pdfUrl'] = pdf.revision_url(revision.issue.learner_id, revision.pk, row.fingerprint)
    elif stored and revision.state in ('original', 'finalized'):
        if revision.state == 'original':
            from .admin_preview import original_url
            legacy = original_url(revision.issue.learner_id, revision.issue, revision.data)
            if legacy:
                result['pdfUrl'] = legacy
                return result
        from .legacy_snapshot import can_render_revision_snapshot, SNAPSHOT_NOTICE
        if can_render_revision_snapshot(revision, data):
            # Retain the pinned content/design and this revision's signing
            # envelope. Viewing must not issue a document or store a substitute.
            result['notice'] = SNAPSHOT_NOTICE
            return result
        result['pdfUnavailable'] = 'This historical PDF was not stored by the branded renderer. It will not be regenerated or restyled when viewed.'
    return result


def can_discard(revision):
    if (revision.state != 'draft' or revision.number <= 1 or revision.finalized_at is not None
            or revision.signing_source_id or revision.signature_method not in ('', 'keep', 'sign-again')
            or revision.base_id is None): return False
    base = revision.base
    if base.issue_id != revision.issue_id or base.state not in ('original', 'finalized'): return False
    # Drafts legitimately contain HISTORICAL signature images. Only the exact
    # inherited evidence is allowed; unexpected/new marks cannot be discarded.
    if blank_documents.is_blank(revision):
        if revision.provenance or any(revision.data.get('marks', {}).values()): return False
        if any(v.get('signed') or v.get('signature') or v.get('signedAt') for v in revision.data.get('signatures', {}).values()): return False
        return not Revision.objects.filter(base_id=revision.pk).exists()
    historical = payload(base)
    if any(revision.data.get(key, {}) != historical.get(key, {}) for key in ('signatures', 'marks')): return False
    return not Revision.objects.filter(base_id=revision.pk).exists()


def discard(revision, actor, edit_version):
    if revision.state == 'discarded': return False  # Idempotent retry, audit unchanged.
    if not can_discard(revision): raise RevisionLocked('Only an unissued draft without new signatures can be deleted.')
    if type(edit_version) is not int or edit_version != revision.edit_version:
        raise RevisionLocked('This revision changed elsewhere. Reload it before deleting the draft.')
    at = timezone.now()
    revision.state = 'discarded'
    revision.updated_by = actor
    revision.edit_version += 1
    revision.changes = {**revision.changes, 'discardedDraft': {'by': actor, 'at': at.isoformat()}}
    revision.save(update_fields=['state', 'updated_by', 'updated_at', 'edit_version', 'changes'])
    return True


def summary(revision):
    data = payload(revision)
    signed = independently_signed(revision.issue.document_key, data) if revision.number == 1 or revision.signature_method == 'sign-again' else False
    return {'id': str(revision.pk), 'issueId': str(revision.issue_id), 'sourceId': revision.issue.source_id,
        'documentKey': revision.issue.document_key, 'title': revision.content['title'], 'number': revision.number,
        'baseId': str(revision.base_id) if revision.base_id else None, 'state': revision.state,
        'creationMode': revision.changes.get('creationMode', 'edit'),
        'canDiscard': can_discard(revision),
        'editVersion': revision.edit_version, 'signatureMethod': revision.signature_method,
        'status': 'Draft' if revision.state == 'draft' else 'Signed' if signed else 'Original' if revision.number == 1 else 'Updated Copy' if revision.signature_method == 'keep' else 'Available',
        'fullySigned': signed, 'templateVersionId': data['template']['id'],
        'templateVersionNumber': data['template']['number'], 'createdBy': revision.created_by,
        'createdAt': revision.created_at.isoformat(), 'finalizedAt': revision.finalized_at.isoformat() if revision.finalized_at else None,
        'reason': revision.reason, 'changes': revision.changes, 'checksum': revision.checksum,
        'provenance': revision.provenance, 'signatures': {role: {'signed': bool(data.get('signatures', {}).get(role, {}).get('signed')),
            'name': data.get('signatures', {}).get(role, {}).get('name', ''), 'signedAt': data.get('signatures', {}).get(role, {}).get('signedAt')}
            for role in required_roles(revision.issue.document_key, data)}}


def save(revision, data, actor):
    if revision.state != 'draft': raise TemplateError('Finalized revisions are read-only. Create the next revision to make changes.')
    if type(data.get('editVersion')) is not int or data['editVersion'] != revision.edit_version: raise TemplateError('This revision changed elsewhere. Reload it before saving.')
    blank = blank_documents.is_blank(revision)
    raw = data.get('content')
    if blank and isinstance(raw, dict) and raw.get('blocks') == []:
        raw = {**raw, 'blocks': [{'id': 'blank-page', 'type': 'paragraph', 'text': ''}]}
    content = validate(raw)
    source_template = revision.data.get('documentTemplateSnapshot')
    baseline = blank_documents.initial_content(revision) if blank else revision.base.content
    if any(content[k] != baseline[k] for k in ('templateId', 'documentKey', 'programmes', 'signatureRoles')): raise TemplateError('A learner revision must retain its original template identity and applicability.')
    if not blank or source_template: preserve_system_fields(content, baseline)
    reason = data.get('reason', '')
    if not isinstance(reason, str) or len(reason) > 2000: raise TemplateError('The reason must be text of up to 2,000 characters.')
    if not blank and not reason.strip(): raise TemplateError('Provide a reason for changing this learner document (up to 2,000 characters).')
    method = data.get('signatureMethod', '')
    if method not in ('', 'keep', 'sign-again'): raise TemplateError('Choose Keep Existing Signatures or Sign Again.')
    if blank and method != 'sign-again': raise TemplateError('New documents require fresh signatures; historical signatures cannot be kept.')
    previous = {b['id']: b for b in baseline['blocks']}; current = {b['id']: b for b in content['blocks']}
    creation = {k: revision.changes[k] for k in ('creationMode', 'creationRequestId', 'initializedFromEmptyDraft') if k in revision.changes}
    revision.changes = {**creation, 'addedBlocks': [k for k in current if k not in previous], 'removedBlocks': [k for k in previous if k not in current],
        'changedBlocks': [k for k in current if k in previous and current[k] != previous[k]], 'sourceChecksum': digest(baseline), 'contentChecksum': digest(content)}
    revision.content = content; revision.reason = reason.strip(); revision.signature_method = method
    revision.edit_version += 1; revision.updated_by = actor
    revision.checksum = digest({'content': content, 'reason': revision.reason, 'method': method, 'provenance': revision.provenance})
    revision.save()
    return revision


def clone_for_signing(revision):
    key = revision.issue.document_key
    source = source_record(key, revision.issue.learner_id, revision.issue.source_id)
    if source is None: raise TemplateError('Original issued document not found.')
    model = type(source)
    values = {field.attname: copy.deepcopy(getattr(source, field.attname)) for field in model._meta.concrete_fields
        if not field.primary_key and not getattr(field, 'auto_now', False) and not getattr(field, 'auto_now_add', False)}
    if blank_documents.is_blank(revision):
        # A fresh signing envelope, not a clone of old answers, dates or body.
        values = {'learner_id': source.learner_id, 'learner_kind': source.learner_kind}
        if key.startswith('review-'):
            values.update(review_type=source.review_type, review_label=revision.content['title'],
                          employer_signature_required='employer' in required_roles(key, revision.data))
    if revision.data.get('learnerDataSnapshot'):
        from .learner_documents import signing_values
        values.update(signing_values(key, revision.data))
    for name in list(values):
        if name.endswith(('_signature', '_signed_name', '_position')): values[name] = ''
        elif name.endswith('_signed_at'): values[name] = None
    for name in ('doc_path', 'blob_name', 'container'):
        if name in values: values[name] = ''
    if 'size_bytes' in values: values['size_bytes'] = None
    if 'fully_signed' in values: values['fully_signed'] = False
    # Non-active source rows are deliberately excluded from existing document /
    # calendar lists. The revision viewer targets this row through the same signer.
    values['status'] = 'cancelled' if key.startswith('review-') else model.STATUS_SUPERSEDED
    if key.startswith('review-'):
        values.update(event_key=f'document-revision:{revision.pk}', form_answers=copy.deepcopy(revision.data.get('answers', {})), form_completed=True)
    clone = model.objects.create(**values)
    template = copy.deepcopy(revision.data.get('documentTemplateSnapshot', revision.issue.template_snapshot)); template['content'] = copy.deepcopy(revision.content)
    template.update(legacyEquivalent=False, contentHash=digest(revision.content), sourceContentHash=template.get('contentHash'))
    IssuedTemplate.objects.create(document_key=key, source_id=str(clone.pk), learner_id=revision.issue.learner_id,
        learner_kind=revision.issue.learner_kind, version_id=template['id'] if template['number'] > 0 else None, template_snapshot=template,
        field_values=copy.deepcopy(revision.data.get('documentFieldValues', revision.issue.field_values)), review_snapshot={k: copy.deepcopy(revision.data[k]) for k in service.REVIEW_SNAPSHOT_KEYS if k in revision.data} if key.startswith('review-') else None)
    return clone


def finalize(revision, actor):
    if revision.state != 'draft': raise TemplateError('This revision is already finalized and cannot be changed.')
    # Serialize issuance with first-issue signing, whose writer locks this row
    # before checking whether revised wording has already been issued.
    source_record(revision.issue.document_key, revision.issue.learner_id, revision.issue.source_id, lock=True)
    if revision.signature_method not in ('keep', 'sign-again'): raise TemplateError('Choose a signature option before finalizing.')
    if not blank_documents.is_blank(revision) and not revision.reason.strip():
        raise TemplateError('Provide a reason for changing this learner document before finalizing.')
    validate(revision.content, publishing=True)
    if blank_documents.is_blank(revision):
        if revision.signature_method != 'sign-again' or revision.provenance: raise TemplateError('New documents require fresh signatures.')
        blank_documents.validate_body(revision.content)
        if revision.data.get('documentTemplateSnapshot'):
            preserve_system_fields(revision.content, blank_documents.initial_content(revision))
    else: preserve_system_fields(revision.content, revision.base.content)
    if revision.signature_method == 'sign-again': revision.signing_source_id = str(clone_for_signing(revision).pk)
    revision.state = 'finalized'; revision.finalized_at = timezone.now(); revision.updated_by = actor
    revision.checksum = digest({'template': revision.data.get('documentTemplateSnapshot', revision.issue.template_snapshot)['id'], 'content': revision.content,
        'data': revision.data, 'signatureMethod': revision.signature_method, 'provenance': revision.provenance,
        'reason': revision.reason, 'base': str(revision.base_id)})
    revision.save()
    return revision


def source_locked(key, document):
    if not ready(): return False
    if Revision.objects.filter(issue__document_key=key, issue__source_id=str(document.pk), number=1).exists(): return True
    signed_revision = Revision.objects.filter(issue__document_key=key, signing_source_id=str(document.pk)).first()
    if signed_revision is None: return False
    if key.startswith('review-'):
        from learner_api.review_form import employer_signature_required
        return bool(document.learner_signature and document.admin_signature and (not employer_signature_required(document) or document.employer_signature))
    return bool(document.fully_signed)


def content_locked(key, document):
    return ready() and (source_locked(key, document) or Revision.objects.filter(issue__document_key=key, signing_source_id=str(document.pk), state='finalized').exists())


def signing_locked(key, document, party, signature):
    if not source_locked(key, document): return False
    # Capturing R1 for an edit/Create freezes content, not the outstanding
    # signing obligations on the active first issue. Permit only an absent
    # party's first signature; never clear or replace historical evidence.
    if (key.startswith('review-') or not signature or party not in seed(key)['signatureRoles']
            or document.status != document.STATUS_ACTIVE or document.fully_signed
            or str(getattr(document, f'{party}_signature', '') or '').strip()):
        return True
    # Once revised wording has actually been issued, the old source remains
    # historical. Its independent signing envelope is the place to sign.
    return Revision.objects.filter(issue__document_key=key, issue__source_id=str(document.pk),
                                   state='finalized', number__gt=1).exists()
