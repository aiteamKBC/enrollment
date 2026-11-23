"""Explicit PCP confirmation and canonical Learning Plan; LMS remains read-only."""
from django.db import DatabaseError, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect

from enrolment_api.auth import enrolment_login_required
from enrollment_service import lms_readonly
from learner_api.models import EnrolmentUser
from . import group_capacity as capacity
from .configuration import ConfigurationError
from .models import Assessment, CohortGroupAllocation
from .permissions import is_staff
from .pcp_learning_plan import confirm_with_learning_plan
from .programme_assignment import ProgrammeAssignmentError
from .views import body, learner_record


@enrolment_login_required
@csrf_protect
def cohort_module_confirmation(request, kind, learner_id):
    account = getattr(request, 'login_account', None)
    # Keep this mutation scoped even when the development auth bypass is on.
    owner = account is not None and account.role == 'learner' and account.subject_id == learner_id and account.is_active
    if not owner and not (request.method == 'GET' and is_staff(request)):
        return JsonResponse({'error': 'Only the signed-in learner can confirm their cohort modules.'}, status=403)
    if request.method not in {'GET', 'PUT'}:
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    try:
        if learner_record(kind, learner_id) is None:
            return JsonResponse({'error': 'Not found.'}, status=404)
        row = Assessment.objects.filter(learner_kind=kind, learner_id=learner_id).first()
        if not row:
            return JsonResponse({'error': 'Confirm your programme and save your academic module choices first.'}, status=409)
        if row.selected_programme != 'PCP_L6':
            return JsonResponse({'error': 'Cohort module confirmation is available only for PCP Level 6.', 'code': 'pcp_only'}, status=409)
        if request.method == 'PUT':
            data = body(request)
            if not isinstance(data, dict) or set(data) != {'expectedRevision'} or type(data['expectedRevision']) is not int:
                raise ConfigurationError('Provide only the saved module selection revision; group and offering choices are automatic.')
            result = confirm_with_learning_plan(kind, learner_id, data['expectedRevision'])
        else:
            # An already persisted confirmation survives refresh and LMS outages.
            # GET never pins configuration, reserves seats or writes records.
            with transaction.atomic(using='default'):
                existing = CohortGroupAllocation.objects.select_related('pool').filter(learner_id=learner_id, learner_kind=kind).first()
                if existing:
                    result = capacity.result_for(existing)
                else:
                    context = capacity.context_for(row)
                    existing, _ = capacity.choose(context)
                    result = capacity.result_for(existing) if existing else {
                        'status': 'ready', 'selectionRevision': row.modules_revision,
                        'savedSelection': row.modules_selection}
        response = JsonResponse(result)
    except capacity.CapacityUnavailable as error:
        response = JsonResponse({'status': 'unavailable', 'code': error.code, 'error': str(error)}, status=409)
    except ConfigurationError as error:
        response = JsonResponse({'error': str(error)}, status=400)
    except ProgrammeAssignmentError as error:
        response = JsonResponse({'status': 'unavailable', 'error': str(error)}, status=409)
    except (Assessment.DoesNotExist, EnrolmentUser.DoesNotExist):
        response = JsonResponse({'error': 'The saved learner or programme is unavailable.'}, status=409)
    except lms_readonly.LmsReadUnavailable:
        response = JsonResponse({'status': 'unavailable', 'error': 'LMS home-cohort groups and complete schedules could not be verified. Please retry later.'}, status=503)
    except DatabaseError:
        response = JsonResponse({'status': 'unavailable', 'code': 'allocation_storage_unavailable',
            'error': 'Cohort module confirmation is not available yet. Please contact admissions before trying again.'}, status=503)
    response['Cache-Control'] = 'private, no-store'
    return response
