from django.urls import path
from . import views
from . import modules_views
from . import rpl_report
from .module_schedules import learner_module_schedule

urlpatterns = [
    path('<str:kind>/<int:learner_id>/module-schedules/', learner_module_schedule, name='learner-module-schedules'),
    path('modules/defaults/', modules_views.modules_defaults, name='modules-defaults'),
    path('modules/draft/', modules_views.modules_draft, name='modules-draft'),
    path('<str:kind>/<int:learner_id>/modules/', modules_views.learner_modules, name='learner-modules'),
    path("defaults/", views.configuration_defaults, name="suitability-defaults"),
    path("preview/", views.preview, name="suitability-preview"),
    path('<str:kind>/<int:learner_id>/suitability-rpl/', rpl_report.suitability_rpl_report, name='suitability-rpl-report'),
    path('<str:kind>/<int:learner_id>/suitability-rpl/preview-pdf/', rpl_report.preview_suitability_rpl_report, name='suitability-rpl-preview-pdf'),
    path('<str:kind>/<int:learner_id>/suitability-rpl/issue/', rpl_report.issue_suitability_rpl_report, name='suitability-rpl-issue'),
    path('<str:kind>/<int:learner_id>/suitability-rpl/issued/', rpl_report.suitability_rpl_issued_documents, name='suitability-rpl-issued-documents'),
    path('<str:kind>/<int:learner_id>/suitability-rpl/issued/<uuid:issue_id>/signatures/', rpl_report.suitability_rpl_signatures, name='suitability-rpl-signatures'),
    path('<str:kind>/<int:learner_id>/suitability-rpl/ai/rpl-analysis/', rpl_report.suitability_rpl_analysis, name='suitability-rpl-analysis'),
    path('<str:kind>/<int:learner_id>/suitability-rpl/ai/<str:draft_type>/', rpl_report.suitability_rpl_ai, name='suitability-rpl-ai'),
    path("<str:kind>/<int:learner_id>/", views.assessment, name="suitability-assessment"),
    path("<str:kind>/<int:learner_id>/review/", views.review, name="suitability-review"),
]
