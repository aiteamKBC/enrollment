"""Study-progress mutations belong to LMS, outside the enrolment database."""
from django.conf import settings
from django.http import JsonResponse
from login.permissions import learner_self_or_staff, require_role


def _response():
    return JsonResponse({
        'error': 'Module shifts that move study progress are managed in LMS. Use the enrolment learning-plan editor to prepare the initial plan.',
        'code': 'LMS_STUDY_REQUIRED',
        'url': settings.LMS_URL,
    }, status=409)


@require_role('learner', 'staff', 'admin')
def module_shift_options(request):
    return _response()


@learner_self_or_staff(kwarg='pk')
def module_shift(request, pk):
    return _response()


module_shift_progress = module_shift
