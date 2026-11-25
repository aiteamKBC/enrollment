from django.urls import path
from . import views

urlpatterns = [
    path('screening-setting/', views.screening_setting, name='skills-radar-screening-setting'),
    path('catalogues/', views.catalogue_admin, name='skills-radar-catalogues'),
    path('catalogues/<str:programme>/', views.catalogue_admin, name='skills-radar-csv'),
    path('draft/', views.catalogue_draft, name='skills-radar-draft'),
    path('<str:kind>/<int:learner_id>/module-impact/', views.module_impact, name='skills-radar-module-impact'),
    path('<str:kind>/<int:learner_id>/', views.learner_catalogue, name='skills-radar-learner'),
]
