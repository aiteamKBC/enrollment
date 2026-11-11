import copy
import json
import logging
import uuid
from functools import wraps

from django.core import signing
from django.db import DatabaseError, connections, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect

from enrolment_api.auth import enrolment_login_required
from programme_suitability.permissions import is_staff
from . import service
from .models import IssuedTemplate, RenderHistory, TemplateDraft, TemplateVersion
from .preview import render_payload
from .registry import MAX_BYTES, TemplateError, digest, seed, seeds, validate
from .question_fields import preserve_system_fields

logger = logging.getLogger(__name__)
SALT = 'compliance-template-preview-v1'


def response(payload, status=200):
    result = JsonResponse(payload, status=status)
    result['Cache-Control'] = 'private, no-store'
    return result


def error(message, status=400): return response({'error': str(message)}, status)


def body(request):
    if len(request.body) > MAX_BYTES + 16000: raise TemplateError('Template request is too large.')
    try: data = json.loads(request.body)
    except ValueError as exc: raise TemplateError('Provide valid JSON.') from exc
    if not isinstance(data, dict): raise TemplateError('Provide a JSON object.')
    return data


def published(identifier):
    try: return service.latest(identifier)[0]
    except TemplateError: return None


def lock_table(table):
    db = service.database()
    if connections[db].vendor == 'postgresql':
        # Serialise first draft saves / overlapping programme publications.
        with connections[db].cursor() as cursor:
            cursor.execute(f'LOCK TABLE enrolment."Document_Template_{table}" IN SHARE ROW EXCLUSIVE MODE')


def gate(view):
    @enrolment_login_required
    @csrf_protect
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not is_staff(request): return error('Authorised enrolment administrator access required.', 403)
        try: return view(request, *args, **kwargs)
        except TemplateError as exc: return error(exc)
        except DatabaseError:
            logger.exception('Document template request failed')
            return error('Could not read or save document templates. Please retry; existing documents have not been changed.', 503)
    return wrapped


@gate
def templates(request):
    if request.method != 'GET': return error('Method not allowed.', 405)
    return response(service.index())


@gate
def fields(request, key):
    if request.method != 'GET': return error('Method not allowed.', 405)
    seed(key)
    return response({'results': list(seeds()['fields'][key].values())})


@gate
def template(request, identifier):
    if request.method not in {'GET', 'PUT', 'DELETE'}: return error('Method not allowed.', 405)
    current = published(identifier)
    draft = TemplateDraft.objects.filter(pk=identifier).first() if service.ready() else None
    if request.method == 'GET':
        if current is None and draft is None: return error('Template not found.', 404)
        return response({'published': current, 'draft': draft.content if draft else None,
                         'revision': draft.revision if draft else 0, 'baseVersion': draft.base_version if draft else current['id'],
                         'setupRequired': not service.ready()})
    if not service.ready(): return error('Run the reviewed apply_document_templates deployment command before saving or publishing templates.', 503)
    data = body(request)
    with transaction.atomic(using=service.database()):
        lock_table('Drafts')
        draft = TemplateDraft.objects.select_for_update().filter(pk=identifier).first()
        current = published(identifier)
        base = current['id'] if current else 'new'
        if data.get('baseVersion') != base or type(data.get('revision')) is not int or data['revision'] != (draft.revision if draft else 0):
            return error('This template or its draft changed elsewhere. Reload it before saving.', 409)
        if request.method == 'DELETE':
            if draft: draft.delete()
            return response({'deleted': True})
        content = validate(data.get('content'))
        preserve_system_fields(content, current['content'] if current else seed(content['documentKey']))
        if content['templateId'] != identifier: raise TemplateError('Template identity does not match the requested document.')
        if draft and draft.content == content and draft.base_version == base:
            return response({'content': draft.content, 'revision': draft.revision, 'baseVersion': base})
        next_revision = (draft.revision if draft else 0) + 1
        TemplateDraft.objects.update_or_create(pk=identifier, defaults={'document_key': content['documentKey'], 'base_version': base,
            'revision': next_revision, 'content': content, 'updated_by': service.actor(request)})
        logger.info('Document template draft saved template=%s revision=%s actor=%s', identifier, next_revision, service.actor(request))
    return response({'content': content, 'revision': next_revision, 'baseVersion': base})


@gate
def preview(request, identifier):
    if request.method != 'POST': return error('Method not allowed.', 405)
    data = body(request)
    preview_token = None
    historical_version = None
    if data.get('version'):
        if identifier in seeds()['templates'] and data['version'] == service.reference(seed(identifier))['id']:
            content = seed(identifier)
        else:
            try: version_id = uuid.UUID(str(data['version']))
            except (ValueError, TypeError) as exc: raise TemplateError('Invalid historical version identifier.') from exc
            version = TemplateVersion.objects.filter(pk=version_id, template_id=identifier).first() if service.ready() else None
            if version:
                content = version.content
                historical_version = version
            else: return error('Historical template version not found.', 404)
    else:
        if not service.ready(): return error('Save a draft after the document template schema has been deployed.', 503)
        draft = TemplateDraft.objects.filter(pk=identifier).first()
        if not draft or draft.revision != data.get('revision'): return error('Save the current draft before previewing it.', 409)
        current = published(identifier)
        if draft.base_version != (current['id'] if current else 'new'): return error('A newer template was published. Reload before previewing.', 409)
        content = draft.content
        preview_token = signing.dumps([identifier, draft.revision, digest(content), draft.base_version], salt=SALT, compress=True)
    content = validate(content)
    learner = None
    if data.get('learnerId') is not None:
        from learner_api.models import EnrolmentUser
        if type(data['learnerId']) is not int or data['learnerId'] < 1: raise TemplateError('Choose a valid existing learner identifier.')
        learner = EnrolmentUser.all_learners.filter(pk=data['learnerId']).first()
        if learner is None: return error('Learner not found.', 404)
    result = render_payload(content, learner)
    if historical_version:
        result['template'].update(service.reference(content, historical_version))
    if not data.get('version'):
        from .pdf_artifacts import render_preview
        result = render_preview(result)
    return response({**result, 'previewToken': preview_token})


@gate
def publish(request, identifier):
    if request.method != 'POST': return error('Method not allowed.', 405)
    if not service.ready(): return error('Document template schema must be deployed before publication.', 503)
    data = body(request)
    if data.get('reviewConfirmed') is not True:
        raise TemplateError('Confirm that the wording has received the appropriate organisational review before publishing.')
    with transaction.atomic(using=service.database()):
        lock_table('Versions')
        draft = TemplateDraft.objects.select_for_update().filter(pk=identifier).first()
        if not draft or draft.revision != data.get('revision'): return error('Save and preview the current draft before publishing.', 409)
        current, previous = service.latest(identifier) if published(identifier) else (None, None)
        base = current['id'] if current else 'new'
        if draft.base_version != base: return error('A newer template was published. Reload before publishing.', 409)
        if not isinstance(data.get('previewToken'), str): raise TemplateError('Preview the saved draft before publishing it.')
        try: token = signing.loads(data['previewToken'], salt=SALT, max_age=24 * 3600)
        except (signing.BadSignature, TypeError) as exc: raise TemplateError('Preview the saved draft before publishing it.') from exc
        if token != [identifier, draft.revision, digest(draft.content), base]: raise TemplateError('The preview belongs to a different draft. Preview the saved changes again.')
        content = validate(draft.content, publishing=True)
        preserve_system_fields(content, current['content'] if current else seed(content['documentKey']))
        for other_id, other in service.current_versions().items():
            if other_id != identifier and other['content']['documentKey'] == content['documentKey'] and set(other['content']['programmes']) & set(content['programmes']):
                raise TemplateError('Another published variant already applies to one of these programmes. Edit that variant or choose different programmes.')
        note = data.get('approvalNote', '')
        if not isinstance(note, str) or len(note) > 2000: raise TemplateError('Approval note must be at most 2,000 characters.')
        version = TemplateVersion.objects.create(template_id=identifier, document_key=content['documentKey'],
            number=(previous.number if previous else 0) + 1, content=content, content_hash=digest(content),
            published_by=service.actor(request), approval_note=note)
        draft.delete()
        logger.info('Document template published template=%s version=%s actor=%s', identifier, version.number, service.actor(request))
    return response({'published': service.reference(content, version)}, 201)


@gate
def history(request, identifier):
    if request.method != 'GET': return error('Method not allowed.', 405)
    versions = list(TemplateVersion.objects.filter(template_id=identifier).order_by('-number')) if service.ready() else []
    result = [service.reference(v.content, v) for v in versions]
    if identifier in seeds()['templates']: result.append(service.reference(seed(identifier)))
    return response({'results': result})


@gate
def issued_history(request, issue_id):
    if request.method != 'GET': return error('Method not allowed.', 405)
    if not service.ready(): return error('Template history is not available.', 503)
    binding = IssuedTemplate.objects.filter(pk=issue_id).first()
    if binding is None: return error('Issued document not found.', 404)
    rows = RenderHistory.objects.filter(issue=binding).order_by('-recorded_at')
    return response({'documentKey': binding.document_key, 'learnerId': binding.learner_id, 'template': binding.template_snapshot,
                     'results': [{'id': str(r.pk), 'recordedAt': r.recorded_at.isoformat(), 'recordedBy': r.recorded_by, 'data': r.payload} for r in rows]})
