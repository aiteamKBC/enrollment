from django.http import JsonResponse
from django.urls import include, path
from .handoff import handoff
from coach_api.csrf import coach_csrf_token


def health(request):
    return JsonResponse({"service": "enrollment", "status": "ok"})


urlpatterns = [
    path("health/", health),
    path("learner_api/lms-handoff/", handoff, name='enrollment-lms-handoff'),
    path("learner_api/enrollment-csrf/", coach_csrf_token, name='enrollment-csrf'),
    path("enrolment_api/", include("enrolment_api.urls")),
    path("login_api/", include("enrollment_service.login_urls")),
    path("learner_api/", include("enrollment_service.learner_api_urls")),
    path("curriculum_api/", include("enrollment_service.curriculum_api_urls")),
    path("coach_api/", include("enrollment_service.coach_api_urls")),
]
