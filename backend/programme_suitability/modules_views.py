import logging
from time import perf_counter

from django.db import DatabaseError
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect

from enrolment_api.auth import enrolment_login_required
from .configuration import ConfigurationError
from .models import ModuleConfigurationDraft
from .modules_configuration import defaults, validate
from .modules_service import pinned_configuration, published_modules, saved_choices, selection_complete, save_selection, ModuleSelectionConflict
from .permissions import is_staff
from .views import body, learner_record

logger = logging.getLogger(__name__)


@enrolment_login_required
def modules_defaults(request):
    if not is_staff(request):
        return JsonResponse({'error': 'Admissions staff access required.'}, status=403)
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    return JsonResponse({'configuration': validate(defaults())})


@enrolment_login_required
@csrf_protect
def learner_modules(request, kind, learner_id):
    started = perf_counter()
    if request.method not in {'GET', 'PUT'}:
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    try:
        if learner_record(kind, learner_id) is None:
            return JsonResponse({'error': 'Not found.'}, status=404)
        if request.method == 'PUT':
            result = save_selection(kind, learner_id, body(request))
            logger.info('Module choices saved learner_id=%s revision=%s duration_ms=%.1f', learner_id, result['selectionRevision'], (perf_counter() - started) * 1000)
            return JsonResponse(result)
        row, version, config = pinned_configuration(kind, learner_id)
        saved = saved_choices(row.learner_kind, learner_id, row) if row.selected_programme == 'PCP_L6' else None
        return JsonResponse({'configuration': config, 'configurationVersion': version, 'selectedProgramme': row.selected_programme,
                             'selectionComplete': selection_complete(row, saved), 'savedSelection': saved,
                             'selectionRevision': row.modules_revision})
    except ModuleSelectionConflict as exc:
        return JsonResponse({'error': str(exc)}, status=409)
    except ConfigurationError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    except DatabaseError as exc:
        cause = exc.__cause__
        logger.warning('Module persistence failed learner_id=%s sqlstate=%s duration_ms=%.1f', learner_id,
                       getattr(cause, 'sqlstate', None) or getattr(cause, 'pgcode', None), (perf_counter() - started) * 1000)
        return JsonResponse({'error': 'Unable to save module changes. Please retry.' if request.method == 'PUT' else 'Could not load programme modules. Please retry.'}, status=503)


@enrolment_login_required
@csrf_protect
def modules_draft(request):
    if not is_staff(request):
        return JsonResponse({'error': 'Admissions staff access required.'}, status=403)
    if request.method not in {'GET', 'PUT', 'DELETE'}:
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    account = getattr(request, 'login_account', None)
    actor = f'platform:{account.pk}' if account else f'django:{request.user.pk}'
    try:
        if request.method == 'GET':
            draft = ModuleConfigurationDraft.objects.filter(pk=actor).first()
            return JsonResponse({'configuration': draft.configuration if draft else None,
                                 'baseVersion': draft.base_version if draft else None})
        if request.method == 'DELETE':
            ModuleConfigurationDraft.objects.filter(pk=actor).delete()
            return JsonResponse({'deleted': True})
        data = body(request)
        base_version = data.get('baseVersion')
        if type(base_version) not in (int, type(None)) or (base_version is not None and base_version < 1):
            raise ConfigurationError('Invalid wizard configuration version.')
        current_version, _ = published_modules(validate_rules=False)
        if base_version != current_version:
            return JsonResponse({'error': 'The wizard was published elsewhere. Reload it before saving this draft.'}, status=409)
        config = validate(data.get('configuration'), publishing=True)
        ModuleConfigurationDraft.objects.update_or_create(pk=actor, defaults={'base_version': base_version, 'configuration': config})
        return JsonResponse({'configuration': config, 'baseVersion': base_version})
    except ConfigurationError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    except DatabaseError:
        return JsonResponse({'error': 'Could not save or load the modules draft. Please retry.'}, status=503)
