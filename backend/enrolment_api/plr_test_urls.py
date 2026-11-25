"""Only supported PLR/enrolment routes for the isolated regression suite."""
from django.urls import path
from .extended_ilr import extended_ilr
from .wizard_bootstrap import wizard_bootstrap
from .plr_pdf_preview import plr_pdf_preview
from .plr_pdf_import import plr_pdf_import

urlpatterns = [
    path('enrolment_api/extended-ilr/<str:kind>/<int:learner_id>/', extended_ilr),
    path('enrolment_api/wizard-bootstrap/<str:kind>/<int:learner_id>/', wizard_bootstrap),
    path('enrolment_api/wizard/<str:kind>/<int:learner_id>/plr-pdf-preview/', plr_pdf_preview),
    path('enrolment_api/wizard/<str:kind>/<int:learner_id>/plr-pdf-import/', plr_pdf_import),
]
