from django.urls import include, path

urlpatterns = [
    path('', include('programme_suitability.urls')),
    path('enrolment_api/', include('enrolment_api.urls')),
]
