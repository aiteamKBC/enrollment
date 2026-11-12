import json
import logging
from functools import wraps
from django.core import signing
from django.db import DatabaseError, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect
from enrolment_api import auth
from programme_suitability.permissions import is_staff
from . import revisions, service
from .models import LearnerDocumentRevision as Revision
from .registry import TemplateError, seed
from .views import body, response, error
from .storage import setup_details

SALT = 'learner-document-revision-preview-v1'
logger = logging.getLogger(__name__)


def gate(view):
    @csrf_protect
    @wraps(view)
    def wrapped(request, pk, *args, **kwargs):
        if not auth.is_authenticated(request): return error('Authentication required.', 401)
        account = getattr(request, 'login_account', None)
        employer = bool(account and account.role == 'employer')
        if employer:
            if not auth._employs(account, pk): return error('Not found.', 404)
        elif not auth._may_access(request, view.__name__, {'pk': pk}): return error('Not found.', 404)
        elif account is None and not is_staff(request): return error('Not found.', 404)
        if request.method != 'GET' and view.__name__ != 'sign_revision' and not is_staff(request): return error('Administrator access required.', 403)
        try:
            # An explicit Edit retry must see newly deployed storage immediately,
            # without waiting for a worker's negative readiness cache to expire.
            if not revisions.ready(force=view.__name__ == 'start_revision'):
                payload = {'setupRequired': True, 'results': [], 'error': 'Learner document revision storage requires setup.'}
                if is_staff(request):
                    details = setup_details(service.database()); payload.update(setup=details, error=details['message'])
                    logger.warning('Document revision storage unavailable: code=%s missing_tables=%s disabled_settings=%s', details['code'], details['missingTables'], details['disabledSettings'])
                return response(payload, 200 if request.method == 'GET' and view.__name__ == 'document_history' else 503)
            return view(request, pk, *args, **kwargs)
        except TemplateError as exc: return error(exc, 409 if 'changed elsewhere' in str(exc) or 'read-only' in str(exc) or 'locked' in str(exc) or 'already finalized' in str(exc) else 400)
        except revisions.RevisionLocked as exc: return error(exc, 409)
        except DatabaseError:
            logger.exception('Learner document revision database operation failed: view=%s', view.__name__)
            return error('Could not read or save this learner document revision. Check the database connection and permissions, then retry. Technical details are recorded in the backend log.', 503)
    return wrapped


def employer_allowed(request, key):
    account = getattr(request, 'login_account', None)
    return not account or account.role != 'employer' or key in {'apprenticeship-agreement', 'training-plan', 'written-agreement', 'review-eligibility', 'review-rpl', 'review-health-safety', 'review-uln'}


def revision_visible(request, revision):
    key = revision.issue.document_key
    if not employer_allowed(request, key): return False
    account = getattr(request, 'login_account', None)
    return not (account and account.role == 'employer' and key.startswith('review-')) or 'employer' in revisions.required_roles(key, revisions.payload(revision))


def document_revision(request, pk, identifier, lock=False):
    revision = revisions.get_revision(identifier, pk, lock=lock)
    if revision is None or revision.state == 'discarded' or not revision_visible(request, revision) or (revision.state == 'draft' and not is_staff(request)):
        return None
    return revision


@gate
def document_history(request, pk, key):
    if request.method != 'GET': return error('Method not allowed.', 405)
    seed(key)
    if not employer_allowed(request, key): return error('Not found.', 404)
    rows = Revision.objects.select_related('issue', 'base').filter(issue__learner_id=pk, issue__document_key=key).exclude(state='discarded').order_by('issue__issued_at', 'number')
    if not is_staff(request): rows = rows.exclude(state='draft')
    # Metadata only; signature images and content are loaded by View/Download.
    return response({'results': [revisions.summary(r) for r in rows if revision_visible(request, r)], 'setupRequired': False})


@gate
def start_revision(request, pk, key):
    if request.method != 'POST': return error('Method not allowed.', 405)
    from learner_api.models import EnrolmentUser
    learner = EnrolmentUser.all_learners.filter(pk=pk).first()
    if learner is None: return error('Not found.', 404)
    data = body(request)
    if not isinstance(data.get('sourceId'), (str, int)): raise TemplateError('Choose an existing issued document.')
    revision = revisions.create_draft(key, data['sourceId'], learner, service.actor(request), data.get('baseRevisionId'),
                                     mode=data.get('mode', 'edit'), request_id=data.get('requestId'))
    return response({'revision': revisions.summary(revision), 'preview': revisions.preview_response(revision)}, 201)


@gate
def revision_detail(request, pk, identifier):
    if request.method not in {'GET', 'PUT'}: return error('Method not allowed.', 405)
    with transaction.atomic(using=service.database()):
        revision = document_revision(request, pk, identifier, lock=request.method == 'PUT')
        if revision is None: return error('Not found.', 404)
        if request.method == 'PUT': revisions.save(revision, body(request), service.actor(request))
        return response({'revision': revisions.summary(revision), 'preview': revisions.preview_response(revision)})


@gate
def discard_revision(request, pk, key, identifier):
    if request.method != 'DELETE': return error('Method not allowed.', 405)
    seed(key)
    data = body(request)
    if not isinstance(data.get('sourceId'), (str, int)): return error('Choose the exact issued source document.', 400)
    with transaction.atomic(using=service.database()):
        # Match the create-draft lock order: issue before revision. A concurrent
        # Edit either resumes this draft first or creates the next revision after.
        issue = revisions.IssuedTemplate.objects.select_for_update().filter(learner_id=pk, document_key=key, source_id=str(data['sourceId'])).first()
        if issue is None: return error('Not found.', 404)
        revision = revisions.get_revision(identifier, pk, lock=True)
        if revision is None or revision.issue_id != issue.pk: return error('Not found.', 404)
        changed = revisions.discard(revision, service.actor(request), data.get('editVersion'))
        return response({'discarded': True, 'alreadyDiscarded': not changed, 'id': str(revision.pk)})


@gate
def preview_revision(request, pk, identifier):
    if request.method != 'POST': return error('Method not allowed.', 405)
    revision = document_revision(request, pk, identifier)
    if revision is None: return error('Not found.', 404)
    data = body(request)
    if revision.state != 'draft' or data.get('editVersion') != revision.edit_version: raise TemplateError('Save the current draft before previewing it.')
    token = signing.dumps([str(revision.pk), revision.edit_version, revision.checksum], salt=SALT)
    from .pdf_artifacts import store_revision
    store_revision(revision)
    return response({**revisions.preview_response(revision), 'previewToken': token})


@gate
def finalize_revision(request, pk, identifier):
    if request.method != 'POST': return error('Method not allowed.', 405)
    data = body(request)
    with transaction.atomic(using=service.database()):
        revision = document_revision(request, pk, identifier, lock=True)
        if revision is None: return error('Not found.', 404)
        try: token = signing.loads(data.get('previewToken', ''), salt=SALT, max_age=86400)
        except (signing.BadSignature, TypeError): raise TemplateError('Preview the saved revision before finalizing it.')
        if token != [str(revision.pk), revision.edit_version, revision.checksum]: raise TemplateError('This revision changed elsewhere. Save and preview it again.')
        revisions.finalize(revision, service.actor(request))
        from .pdf_artifacts import store_revision
        store_revision(revision)
        return response({'revision': revisions.summary(revision), 'preview': revisions.preview_response(revision)})


@gate
def sign_revision(request, pk, identifier):
    if request.method != 'POST': return error('Method not allowed.', 405)
    data = body(request)
    with transaction.atomic(using=service.database()):
        revision = document_revision(request, pk, identifier, lock=True)
        if revision is None: return error('Not found.', 404)
        if revision.state != 'finalized' or revision.signature_method != 'sign-again': raise TemplateError('This copy does not request new signatures.')
        if Revision.objects.filter(issue_id=revision.issue_id, state='finalized', number__gt=revision.number).exists():
            raise TemplateError('This historical document is locked. Sign the latest issued revision instead.')
        current = revisions.payload(revision); key = revision.issue.document_key
        if revisions.independently_signed(key, current): raise TemplateError('This fully signed revision is locked.')
        account = getattr(request, 'login_account', None)
        role = getattr(account, 'role', None) or ('staff' if is_staff(request) else '')
        party = data.get('party')
        allowed = {'staff': {'provider', 'admin'}, 'admin': {'provider', 'admin'}, 'learner': {'learner', 'apprentice'}, 'employer': {'employer'}}.get(role, set())
        if party not in allowed or party not in revisions.required_roles(key, current): return error('You cannot sign as this document party.', 403)
        signature, name = data.get('signature', ''), data.get('name', '')
        if not isinstance(signature, str) or not isinstance(name, str) or len(signature) > 400000 or (signature and (not signature.startswith('data:image/') or not name.strip())):
            raise TemplateError('Provide the signatory name and a supported signature image.')
        source = revisions.source_record(key, pk, revision.signing_source_id, lock=True)
        if source is None: raise TemplateError('Revision signing document not found.')
        if key.startswith('review-'):
            from learner_api.review_form import record_signature
            record_signature(source, party, signature, name)
        else:
            _, _, signer = revisions.source_api(key)
            # Same request identity, same existing authorisation/signature writer;
            # only the already-authorised source row is selected internally.
            result = signer(request, pk=pk, _revision_source=source)
            if result.status_code != 200: return result
        revision.refresh_from_db()
        from .pdf_artifacts import store_revision
        store_revision(revision)
        return response({'revision': revisions.summary(revision), 'preview': revisions.preview_response(revision)})
