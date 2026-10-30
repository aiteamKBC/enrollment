"""Enrolment-only API surface. Study/delivery endpoints remain in LMS."""
from django.urls import path
from login import safeguarding_sso
from login import inclusion_sso
from login import coach_directory
from login import lms_introduction
from login import access_requests, microsoft_sso, saved_signature, views

urlpatterns = [
    path('inclusion/authorize/', inclusion_sso.authorize, name='inclusion-authorize'),
    path('public/coaches/<slug:slug>/', coach_directory.public_coach, name='public-coach-booking'),
    path('public/lms-introduction/', lms_introduction.public_request, name='public-lms-introduction'),
    path('admin/coach-directory/', coach_directory.directory, name='admin-coach-directory'),
    path('admin/coach-directory/<int:pk>/', coach_directory.directory, name='admin-coach-directory-item'),
    path('safeguarding/authorize/', safeguarding_sso.authorize, name='safeguarding-authorize'),
    path('health/', views.health, name='login-health'),
    path('login/', views.login, name='login'),
    path('logout/', views.logout, name='logout'),
    path('me/', views.me, name='login-me'),
    path('me/signature/', saved_signature.my_signature, name='login-my-signature'),
    path('microsoft/start/', microsoft_sso.start, name='login-microsoft-start'),
    path('microsoft/callback/', microsoft_sso.callback, name='login-microsoft-callback'),
    path('change-password/', views.change_password, name='login-change-password'),
    path('forgot-password/', views.forgot_password, name='login-forgot-password'),
    path('reset/', views.reset_info, name='login-reset-info'),
    path('reset-password/', views.reset_password, name='login-reset-password'),
    path('invitation/', views.invitation_info, name='login-invitation-info'),
    path('accept-invitation/', views.accept_invitation_view, name='login-accept-invitation'),
    path('accounts/invite/', views.invite_account, name='login-invite-account'),
    path('accounts/invitation-link/', views.invitation_link_view, name='login-invitation-link'),
    path('request-access/', access_requests.request_access, name='login-request-access'),
]
