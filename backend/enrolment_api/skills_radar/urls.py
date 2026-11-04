from django.urls import path
from . import views

urlpatterns = [
    path('catalogues/', views.catalogue_admin, name='skills-radar-catalogues'),
    path('catalogues/<str:programme>/', views.catalogue_admin, name='skills-radar-csv'),
    path('draft/', views.catalogue_draft, name='skills-radar-draft'),
    path('<str:kind>/<int:learner_id>/', views.learner_catalogue, name='skills-radar-learner'),
]
