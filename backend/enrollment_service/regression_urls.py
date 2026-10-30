from django.urls import path
from .urls import urlpatterns as service_routes
from coach_api.views import coach_timetable_event_action, coach_timetable_book_event, coach_timetable_schedule_event, coach_timetable
urlpatterns = [path('coach_api/coach/timetable', coach_timetable, name='coach-timetable'),
path('coach_api/coach/timetable/events/book', coach_timetable_book_event, name='coach-timetable-event-book'),
path('coach_api/coach/timetable/events/schedule', coach_timetable_schedule_event, name='coach-timetable-event-schedule'),
path('coach_api/coach/timetable/events/action', coach_timetable_event_action, name='coach-timetable-event-action')]
from curriculum_api import views
urlpatterns += [path('curriculum_api/curriculum/modules/<str:module_catalogue_id>/session-plan/', views.curriculum_module_session_plan),
                path('curriculum_api/curriculum/preview/module-session-plan/', views.curriculum_preview_module_session_plan)]
from curriculum_api.teams_attendee_compare import teams_meeting_attendee_comparison
from curriculum_api.teams_permission_check import teams_meeting_permission_check
from curriculum_api.teams_imported_participants import imported_meeting_participants
from curriculum_api.teams_imported_calendar_update import imported_calendar_update
from curriculum_api.teams_imported_calendar_auth import imported_calendar_connect, imported_calendar_connection
from curriculum_api.teams_dial_in import teams_meeting_dial_in
from curriculum_api.teams_options_retry import teams_meeting_options_retry
from curriculum_api.teams_endpoint_guard import teams_staff_required
from curriculum_api.teams_create_guard import teams_create_status, teams_meeting_collection
from curriculum_api.teams_create_drafts import teams_create_draft
from curriculum_api.teams_schedule_delivery import forward_schedule_email, schedule_email
from curriculum_api.teams_calendar_state import sync_calendar_state
from curriculum_api.teams_directory import search_teams_directory
from curriculum_api.teams_calendar_actions import calendar_action
from curriculum_api.teams_week_meeting import curriculum_week_teams_meeting, curriculum_week_teams_meeting_detail
urlpatterns = urlpatterns + [path('curriculum_api/curriculum/teams-directory/', search_teams_directory, name='curriculum-teams-directory'),
path('curriculum_api/curriculum/modules/<str:module_catalogue_id>/teams-meetings/restore/', teams_staff_required(views.curriculum_module_teams_meeting_restore), name='curriculum-module-teams-meeting-restore'),
path('curriculum_api/curriculum/modules/<str:module_catalogue_id>/teams-links/', teams_staff_required(views.curriculum_module_teams_links_replace), name='curriculum-module-teams-links-replace'),
path('curriculum_api/curriculum/modules/<str:module_catalogue_id>/week-teams-meetings/', teams_staff_required(curriculum_week_teams_meeting), name='curriculum-week-teams-meeting'),
path('curriculum_api/curriculum/modules/<str:module_catalogue_id>/week-teams-meetings/<str:live_session_id>/', teams_staff_required(curriculum_week_teams_meeting_detail), name='curriculum-week-teams-meeting-detail'),
path('curriculum_api/curriculum/teams-meetings/', teams_staff_required(teams_meeting_collection), name='curriculum-teams-meeting'),
path('curriculum_api/curriculum/teams-meetings/create-status/', teams_create_status, name='curriculum-teams-create-status'),
path('curriculum_api/curriculum/teams-meetings/create-draft/', teams_staff_required(teams_create_draft), name='curriculum-teams-create-draft'),
path('curriculum_api/curriculum/teams-meetings/summary/', views.curriculum_teams_meeting_summary, name='curriculum-teams-meeting-summary'),
path('curriculum_api/curriculum/teams-meetings/<str:live_session_id>/schedule/', teams_staff_required(views.curriculum_teams_meeting_schedule), name='curriculum-teams-meeting-schedule'),
path('curriculum_api/curriculum/teams-meetings/<str:live_session_id>/off-schedule-runs/', teams_staff_required(views.curriculum_teams_meeting_off_schedule_runs), name='curriculum-teams-meeting-off-schedule-runs'),
path('curriculum_api/curriculum/teams-meetings/<str:live_session_id>/schedule-email/', schedule_email, name='curriculum-teams-schedule-email'),
path('curriculum_api/curriculum/teams-meetings/<str:live_session_id>/forward-email/', forward_schedule_email, name='curriculum-teams-forward-email'),
path('curriculum_api/curriculum/teams-meetings/<str:live_session_id>/calendar-state/', sync_calendar_state, name='curriculum-teams-calendar-state'),
path('curriculum_api/curriculum/teams-meetings/<str:live_session_id>/actions/', calendar_action, name='curriculum-teams-calendar-action'),
path('curriculum_api/curriculum/teams-meetings/<str:live_session_id>/compare-attendees/', teams_meeting_attendee_comparison, name='curriculum-teams-compare-attendees'),
path('curriculum_api/curriculum/teams-meetings/<str:live_session_id>/permission-check/', teams_meeting_permission_check, name='curriculum-teams-permission-check'),
path('curriculum_api/curriculum/teams-meetings/<str:live_session_id>/imported-participants/', imported_meeting_participants, name='curriculum-teams-imported-participants'),
path('curriculum_api/curriculum/teams-meetings/<str:live_session_id>/imported-calendar/connection/', imported_calendar_connection, name='curriculum-teams-imported-calendar-connection'),
path('curriculum_api/curriculum/teams-meetings/<str:live_session_id>/imported-calendar/connect/', imported_calendar_connect, name='curriculum-teams-imported-calendar-connect'),
path('curriculum_api/curriculum/teams-meetings/<str:live_session_id>/imported-calendar/preview/', imported_calendar_update, {'stage': 'preview'}, name='curriculum-teams-imported-calendar-preview'),
path('curriculum_api/curriculum/teams-meetings/<str:live_session_id>/imported-calendar/confirm/', imported_calendar_update, {'stage': 'confirm'}, name='curriculum-teams-imported-calendar-confirm'),
path('curriculum_api/curriculum/teams-meetings/<str:live_session_id>/imported-calendar/status/', imported_calendar_update, {'stage': 'status'}, name='curriculum-teams-imported-calendar-status'),
path('curriculum_api/curriculum/teams-meetings/<str:live_session_id>/dial-in/', teams_staff_required(teams_meeting_dial_in), name='curriculum-teams-dial-in'),
path('curriculum_api/curriculum/teams-meetings/<str:live_session_id>/retry-options/', teams_meeting_options_retry, name='curriculum-teams-retry-options'),
path('curriculum_api/curriculum/teams-meetings/<str:live_session_id>/occurrences/<int:session_number>/schedule/', teams_staff_required(views.curriculum_teams_meeting_occurrence_schedule), name='curriculum-teams-meeting-occurrence-schedule'),
path('curriculum_api/curriculum/teams-meetings/<str:live_session_id>/occurrences/<str:occurrence_id>/join/', views.curriculum_teams_meeting_join, name='curriculum-teams-meeting-join'),
path('curriculum_api/curriculum/teams-meetings/<str:live_session_id>/artifacts/', views.curriculum_teams_meeting_artifacts, name='curriculum-teams-meeting-artifacts'),
path('curriculum_api/curriculum/teams-meetings/<str:live_session_id>/artifacts/<str:artifact_id>/content/', views.curriculum_teams_meeting_artifact_content, name='curriculum-teams-meeting-artifact-content'),
path('curriculum_api/curriculum/teams-meetings/<str:live_session_id>/artifacts/<str:artifact_id>/recording-events/', views.curriculum_teams_recording_events, name='curriculum-teams-recording-events')]
urlpatterns += service_routes
