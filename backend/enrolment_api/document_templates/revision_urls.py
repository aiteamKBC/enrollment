from django.urls import path
from . import revision_views as views

urlpatterns = [
    path('learners/<int:pk>/<str:key>/history/', views.document_history),
    path('learners/<int:pk>/<str:key>/drafts/', views.start_revision),
    path('learners/<int:pk>/<str:key>/revisions/<uuid:identifier>/', views.discard_revision),
    path('learners/<int:pk>/revisions/<uuid:identifier>/', views.revision_detail),
    path('learners/<int:pk>/revisions/<uuid:identifier>/preview/', views.preview_revision),
    path('learners/<int:pk>/revisions/<uuid:identifier>/finalize/', views.finalize_revision),
    path('learners/<int:pk>/revisions/<uuid:identifier>/sign/', views.sign_revision),
]
