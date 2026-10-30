"""Enrolment-only API surface. Study/delivery endpoints remain in LMS."""
from django.urls import path
from curriculum_api import views

urlpatterns = [
    path('curriculum/free-programmes/<str:programme_id>/modules/', views.curriculum_free_programme_modules, name='curriculum-free-programme-modules'),
]
