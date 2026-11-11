"""Retry-safe first Issue, without changing explicit historical/reissue workflows."""
import json
from functools import wraps
from django.db import DatabaseError, transaction
from django.http import JsonResponse
from . import service
from .registry import REVIEW_KEYS


def issued_response(key, source, learner, body):
    from .models import RenderHistory
    from .revisions import serialize_source, required_roles
    from .registry import digest
    binding = service.binding_for(key, source)
    if binding and binding.template_snapshot.get('originalPdfVersion') in (1, 2):
        row = RenderHistory.objects.filter(issue=binding, payload__kind='original-issued-snapshot-v1').first()
        snapshot = row.payload['data'] if row else serialize_source(key, source, learner)
        if row or not any(snapshot.get('signatures', {}).get(role, {}).get('signed') for role in required_roles(key, snapshot)):
            if row is None:
                RenderHistory.objects.create(issue=binding, fingerprint=digest({'kind': 'original-issued-snapshot-v1', 'data': snapshot}),
                    payload={'kind': 'original-issued-snapshot-v1', 'data': snapshot}, recorded_by='Initial issue')
            body = {**body, 'issuedDocumentSnapshot': snapshot}
    return JsonResponse(body, status=201)


def initial_issue(key):
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method != 'POST' or request.headers.get('X-Document-Issue-Mode') != 'initial':
                return view(request, *args, **kwargs)
            from learner_api.models import EnrolmentUser
            from .revisions import source_record, serialize_source
            try:
                actual_key = key
                if key == 'review':
                    if kwargs.get('kind') != 'apprenticeship': return view(request, *args, **kwargs)
                    try: actual_key = REVIEW_KEYS.get(json.loads(request.body or b'{}').get('type'))
                    except (ValueError, AttributeError): actual_key = None
                    if not actual_key: return view(request, *args, **kwargs)
                with transaction.atomic(using=service.database()):
                    learner = EnrolmentUser.all_learners.select_for_update().filter(pk=kwargs['pk']).first()
                    if learner is None: return view(request, *args, **kwargs)
                    existing = source_record(actual_key, learner.pk)
                    if existing is not None:
                        if actual_key.startswith('review-'):
                            from learner_api.review_form import _slots_payload
                            return issued_response(actual_key, existing, learner, _slots_payload(learner, kwargs['kind'], learner.pk))
                        return issued_response(actual_key, existing, learner, {'agreement' if actual_key == 'apprenticeship-agreement' else 'document': serialize_source(actual_key, existing, learner)})
                    if not service.ready(force=True):
                        return JsonResponse({'error': 'Document template storage must be available before issuing a new purple document.'}, status=503)
                    selected = service.issuance_reference(actual_key, learner)
                    if selected['content'].get('designTheme') != 'purple-reference-v2':
                        return JsonResponse({'error': 'The applicable published template uses an older design. In Compliance Documents, select Purple — supplied reference design, save, preview and publish a new template version before issuing.', 'templateId': selected['content']['templateId'], 'publicationRequired': True}, status=409)
                    result = view(request, *args, **kwargs)
                    if result.status_code == 201:
                        source = source_record(actual_key, learner.pk)
                        return issued_response(actual_key, source, learner, json.loads(result.content))
                    return result
            except service.TemplateError as exc:
                return JsonResponse({'error': str(exc)}, status=409)
            except DatabaseError:
                return JsonResponse({'error': 'Could not issue this document. Check the database connection and retry.'}, status=503)
        return wrapped
    return decorate
