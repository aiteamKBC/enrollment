"""Enrolment-only API surface. Study/delivery endpoints remain in LMS."""
from django.urls import path
from coach_api.csrf import coach_csrf_token
from coach_api.views import coach_directory, coach_directory_calendar

urlpatterns = [
    path('csrf', coach_csrf_token, name='coach-csrf'),
    path('coaches', coach_directory, name='coach-directory'),
    path('coaches/calendar', coach_directory_calendar, name='coach-directory-calendar'),
]
