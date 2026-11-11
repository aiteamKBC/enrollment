from django.urls import path
from . import views, original_pdf, admin_preview, pdf_views

urlpatterns = [
    path('', views.templates),
    path('fields/<str:key>/', views.fields),
    path('issued/<uuid:issue_id>/history/', views.issued_history),
    path('learners/<int:pk>/issued/<uuid:issue_id>/original-pdf/', original_pdf.original_pdf),
    path('learners/<int:pk>/issued/<uuid:issue_id>/pdf/', pdf_views.issued_pdf),
    path('learners/<int:pk>/revisions/<uuid:identifier>/pdf/', pdf_views.revision_pdf),
    path('learners/<int:pk>/documents/<str:key>/preview/', admin_preview.document_preview),
    path('learners/<int:pk>/documents/<str:key>/sign-reviewed/', admin_preview.sign_reviewed_document),
    path('<str:identifier>/', views.template),
    path('<str:identifier>/preview/', views.preview),
    path('<str:identifier>/publish/', views.publish),
    path('<str:identifier>/history/', views.history),
]
