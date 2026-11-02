from django.urls import path
from . import views
from . import modules_views

urlpatterns = [
    path('modules/defaults/', modules_views.modules_defaults, name='modules-defaults'),
    path('modules/draft/', modules_views.modules_draft, name='modules-draft'),
    path('<str:kind>/<int:learner_id>/modules/', modules_views.learner_modules, name='learner-modules'),
    path("defaults/", views.configuration_defaults, name="suitability-defaults"),
    path("preview/", views.preview, name="suitability-preview"),
    path("<str:kind>/<int:learner_id>/", views.assessment, name="suitability-assessment"),
    path("<str:kind>/<int:learner_id>/review/", views.review, name="suitability-review"),
]
