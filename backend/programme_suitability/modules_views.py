import logging
from time import perf_counter

from django.db import DatabaseError
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect

from enrolment_api.auth import enrolment_login_required
from .configuration import ConfigurationError
from .models import Assessment, ModuleConfigurationDraft
from .modules_configuration import defaults, validate
from .modules_service import pinned_configuration, published_modules, saved_choices, selection_complete, save_selection, ModuleSelectionConflict
from .permissions import is_staff
from .views import body, learner_record
from learner_api.models import LearnerProfile
from .programme_structure import resolve_published, resolve_assigned_cohort, learner_published_layout
from .programme_identity import find_structure_programme
from .home_cohort import read_home_cohort, pending_home_cohort

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
        from . import enrolment_catalogue
        if enrolment_catalogue.enabled():
            if request.method != 'GET':
                return JsonResponse({'error': 'Catalogue modules are read-only. Group and module allocation is paused.'}, status=409)
            row = Assessment.objects.filter(learner_id=learner_id, learner_kind=kind).first()
            if not row or not row.confirmed_at or not row.selection_confirmed_at or not row.selected_programme:
                return JsonResponse({'error': 'Confirm your programme before loading its modules.'}, status=409)
            response = JsonResponse(enrolment_catalogue.programme_modules(row))
            response['Cache-Control'] = 'private, no-store'
            return response
        if request.method == 'PUT':
            result = save_selection(kind, learner_id, body(request))
            logger.info('Module choices saved learner_id=%s revision=%s duration_ms=%.1f', learner_id, result['selectionRevision'], (perf_counter() - started) * 1000)
            return JsonResponse(result)
        row, version, config = pinned_configuration(kind, learner_id)
        saved = saved_choices(row.learner_kind, learner_id, row)
        try:
            home_cohort = read_home_cohort(row, kind, learner_id, version)
        except Exception:
            # Display metadata must not break existing assessment/module flows.
            # No raw database exception or guessed name is returned to clients.
            home_cohort = pending_home_cohort(row.selected_programme, 'lookup_unavailable')
        assigned_cohort = None
        learner_configuration = config
        profile = None
        try:
            if getattr(settings, 'PROGRAMME_STRUCTURE_COHORT_LEARNER_ENABLED', False):
                profile = LearnerProfile.objects.filter(enrolment_id=learner_id).only('programme_id', 'cohort_id').first()
            if profile and profile.programme_id and profile.cohort_id and home_cohort.get('source') != 'lms':
                _, layout, _ = learner_published_layout(kind, learner_id, module_version=version)
                if layout:
                    resolved = resolve_published(layout, compare_legacy=False)
                    assigned_cohort = resolve_assigned_cohort(resolved['snapshot'], profile.programme_id, profile.cohort_id)
                    if resolved['source'] == 'canonical' and assigned_cohort is None:
                        return JsonResponse({'error': 'The assigned Cohort is unavailable in this learner\'s saved published layout. Admissions must review the assignment.'}, status=409)
                    canonical = find_structure_programme(resolved['snapshot'], profile.programme_id)
                    if assigned_cohort and canonical and canonical['id'] in {'ME_L4', 'MM_L6', 'APM_L4'}:
                        import copy
                        learner_configuration = copy.deepcopy(config)
                        target = next((p for p in learner_configuration.get('programmes', []) if p.get('id') == canonical['id']), None)
                        if target:
                            target['modules'] = copy.deepcopy(assigned_cohort.get('modules', []))
        except LookupError as exc:
            return JsonResponse({'error': str(exc)}, status=409)
        except Exception:
            logger.warning('Could not resolve assigned cohort in read-only learner modules lookup', exc_info=True)
        if getattr(settings, 'PROGRAMME_STRUCTURE_SHADOW_ENABLED', False):
            from .shadow_runtime import compare
            selected = ((saved or {}).get('modulesByPathway', {}).get((saved or {}).get('pathway'), []) if isinstance(saved, dict) and (saved or {}).get('modulesByPathway') else (saved or {}).get('modules')) if isinstance(saved, dict) else None
            compare(kind, learner_id, row.selected_programme, selected, module_version=version)
        return JsonResponse({'configuration': learner_configuration, 'configurationVersion': version, 'selectedProgramme': row.selected_programme,
                             'assignedProgrammeId': getattr(profile, 'programme_id', None) if 'profile' in locals() and profile else None,
                             'assignedCohortId': getattr(profile, 'cohort_id', None) if 'profile' in locals() and profile else None,
                             'cohortResolution': assigned_cohort,
                             'homeCohort': home_cohort,
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
