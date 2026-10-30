"""Working-day rules shared by every learner calendar session type."""

import inspect
import json
from datetime import date, datetime, time
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from django.test import RequestFactory, SimpleTestCase
from django.db import DatabaseError

from .booking_calendar import booking_calendar_payload, booking_date_restriction


class BookingDateRestrictionTests(SimpleTestCase):
    def restriction_on_2026_09_01(self, day):
        return booking_date_restriction(day, today=date(2026, 9, 1))

    def test_a_date_before_today_is_closed(self):
        restriction = self.restriction_on_2026_09_01(date(2026, 8, 28))

        self.assertEqual(restriction.code, "past-date")
        self.assertIn("already passed", restriction.message)

    def test_saturday_and_sunday_are_closed(self):
        self.assertEqual(self.restriction_on_2026_09_01(date(2026, 9, 5)).code, "weekend")
        self.assertEqual(self.restriction_on_2026_09_01(date(2026, 9, 6)).code, "weekend")

    def test_england_and_wales_bank_holiday_is_closed(self):
        restriction = self.restriction_on_2026_09_01(date(2026, 12, 28))

        self.assertEqual(restriction.code, "bank-holiday")
        self.assertIn("Boxing Day", restriction.message)

    def test_ordinary_weekday_is_open(self):
        self.assertIsNone(self.restriction_on_2026_09_01(date(2026, 9, 7)))

    def test_unpublished_year_fails_closed(self):
        self.assertEqual(
            self.restriction_on_2026_09_01(date(2029, 1, 2)).code,
            "calendar-unavailable",
        )

    def test_payload_exposes_the_same_holidays_to_the_calendar_ui(self):
        with patch(
            "learner_api.booking_calendar.timezone.localdate",
            return_value=date(2026, 9, 7),
        ):
            payload = booking_calendar_payload()

        holiday = next(row for row in payload["bankHolidays"] if row["date"] == "2026-12-28")
        self.assertIn("Boxing Day", holiday["title"])
        self.assertIn(2026, payload["coveredYears"])
        self.assertEqual(payload["today"], "2026-09-07")


class ImportedReviewBookingTests(SimpleTestCase):
    def test_booking_validates_ownership_without_rewriting_aptem_source_evidence(self):
        from . import calendar as module

        cursor = Mock()
        cursor.rowcount = 1
        connection = Mock()
        cursor_context = MagicMock()
        cursor_context.__enter__.return_value = cursor
        connection.cursor.return_value = cursor_context
        with patch.object(module, "connection", connection):
            module._mark_imported_review_scheduled("62", 272, date(2026, 11, 26), time(9, 0))

        params = cursor.execute.call_args.args[1]
        self.assertEqual(params[:2], [62, 272])
        self.assertTrue(cursor.execute.call_args.args[0].strip().startswith('SELECT'))

    def test_sync_fails_when_the_review_is_not_owned_by_the_learner(self):
        from . import calendar as module

        cursor = Mock()
        cursor.rowcount = 0
        cursor.fetchone.return_value = None
        connection = Mock()
        cursor_context = MagicMock()
        cursor_context.__enter__.return_value = cursor
        connection.cursor.return_value = cursor_context
        with patch.object(module, "connection", connection):
            with self.assertRaises(DatabaseError):
                module._mark_imported_review_scheduled("62", 999, date(2026, 11, 26), time(9, 0))


class BookingEndpointRestrictionTests(SimpleTestCase):
    def test_new_booking_uses_current_assignment_instead_of_stale_profile(self):
        from . import calendar as module

        learner = SimpleNamespace(pk=101, username='Test Learner', email='learner@example.com',
                                  case_owner='Test curriculum', coach_name='Test curriculum',
                                  coach_email='curriculum@example.com')
        mirror = SimpleNamespace(id=248, coach_email='old@example.com', coach_name='Old coach',
                                 full_name='Test Learner', email='learner@example.com')
        source_model = Mock()
        source_model.all_learners.filter.return_value.first.return_value = learner
        record = SimpleNamespace(pk=1, event_key='catch-up:248:1:2026-10-15', event_type='catch-up')
        with patch.object(module, 'SOURCE_MODELS', {'commercial': source_model}), \
                patch.object(module, 'learner_profile_for_source', return_value=mirror), \
                patch('learner_api.booking_calendar.timezone.localdate', return_value=date(2026, 9, 14)), \
                patch.object(module.CoachCalendarEvent.objects, 'filter') as events, \
                patch('learner_api.calendar_connections.booking_conflicts', return_value=False), \
                patch('learner_api.coach_availability.catchup_slot_is_free', return_value=True) as coach_free, \
                patch('coach_api.views.reserve_coach_calendar_booking', return_value=(record, True)) as reserve, \
                patch('coach_api.views.build_booked_calendar_event', return_value={}), \
                patch('coach_api.views.synchronize_reserved_calendar_event', return_value=(record, '', True)) as sync, \
                patch.object(module, '_serialize_event', return_value={'eventKey': record.event_key}):
            events.return_value.first.return_value = None
            request = RequestFactory().post('/book/', data=json.dumps({
                'sessionType': 'catch-up', 'scheduledDate': '2026-10-15',
                'scheduledTime': '11:00', 'durationMinutes': 60,
            }), content_type='application/json')
            response = inspect.unwrap(module.learner_calendar_book)(request, 'commercial', 101)

        self.assertEqual(response.status_code, 201)
        # The coach's availability is checked on the same mailbox the booking uses.
        self.assertEqual(coach_free.call_args.args[0], 'curriculum@example.com')
        self.assertEqual(reserve.call_args.kwargs['owner_name'], 'Test curriculum')
        self.assertEqual(reserve.call_args.kwargs['owner_email'], 'curriculum@example.com')
        self.assertNotIn('approvalRequired', json.loads(response.content))
        sync.assert_called_once()

    def test_new_booking_rejects_explicitly_cleared_assignment(self):
        from . import calendar as module

        learner = SimpleNamespace(username='Test Learner', email='learner@example.com', case_owner='', coach_name='', coach_email='')
        mirror = SimpleNamespace(coach_email='old@example.com', coach_name='Old coach')
        source_model = Mock()
        source_model.all_learners.filter.return_value.first.return_value = learner
        with patch.object(module, 'SOURCE_MODELS', {'commercial': source_model}), \
                patch.object(module, 'learner_profile_for_source', return_value=mirror), \
                patch('coach_api.views.reserve_coach_calendar_booking') as reserve:
            request = RequestFactory().post('/book/', data=json.dumps({
                'sessionType': 'catch-up', 'scheduledDate': '2026-10-15', 'scheduledTime': '11:00',
            }), content_type='application/json')
            response = inspect.unwrap(module.learner_calendar_book)(request, 'commercial', 101)
        self.assertEqual(response.status_code, 400)
        self.assertIn('No coach has been assigned', json.loads(response.content)['error'])
        reserve.assert_not_called()

    def test_every_coach_session_type_is_rejected_on_a_weekend(self):
        from . import calendar as module

        learner = SimpleNamespace(username="Test Learner", email="learner@example.com")
        mirror = SimpleNamespace(
            coach_email="coach@example.com",
            coach_name="Coach",
            full_name="Test Learner",
            email="learner@example.com",
        )
        source_model = Mock()
        source_model.all_learners.filter.return_value.first.return_value = learner
        # Bypass only the authentication wrapper; this test is about the view's
        # own booking rule and must never reach a database or Microsoft Graph.
        view = inspect.unwrap(module.learner_calendar_book)

        for session_type in module.BOOKABLE_TYPES:
            with self.subTest(session_type=session_type), \
                    patch.object(module, "SOURCE_MODELS", {"commercial": source_model}), \
                    patch.object(module, "learner_profile_for_source", return_value=mirror), \
                    patch("learner_api.booking_calendar.timezone.localdate", return_value=date(2026, 9, 1)):
                request = RequestFactory().post(
                    "/learner_api/calendar/commercial/101/book/",
                    data=json.dumps({
                        "sessionType": session_type,
                        "scheduledDate": "2026-09-05",
                        "scheduledTime": "10:00",
                        "durationMinutes": 60,
                    }),
                    content_type="application/json",
                )
                response = view(request, "commercial", 101)

            self.assertEqual(response.status_code, 400)
            self.assertIn("Saturdays or Sundays", json.loads(response.content)["error"])

    def test_catch_up_booking_is_rejected_on_a_bank_holiday_before_graph_sync(self):
        from . import calendar as module

        learner = SimpleNamespace(username="Test Learner", email="learner@example.com")
        mirror = SimpleNamespace(
            coach_email="coach@example.com", coach_name="Coach",
            full_name="Test Learner", email="learner@example.com",
        )
        source_model = Mock()
        source_model.all_learners.filter.return_value.first.return_value = learner
        view = inspect.unwrap(module.learner_calendar_book)

        with patch.object(module, "SOURCE_MODELS", {"commercial": source_model}), \
                patch.object(module, "learner_profile_for_source", return_value=mirror), \
                patch("learner_api.booking_calendar.timezone.localdate", return_value=date(2026, 9, 1)), \
                patch("coach_api.views.reserve_coach_calendar_booking") as reserve:
            request = RequestFactory().post(
                "/learner_api/calendar/commercial/101/book/",
                data=json.dumps({
                    "sessionType": "catch-up",
                    "scheduledDate": "2026-12-28",
                    "scheduledTime": "10:00",
                    "durationMinutes": 60,
                }),
                content_type="application/json",
            )
            response = view(request, "commercial", 101)

        self.assertEqual(response.status_code, 400)
        self.assertIn("UK bank holidays", json.loads(response.content)["error"])
        reserve.assert_not_called()

    def test_a_direct_booking_request_for_a_past_date_is_rejected(self):
        from . import calendar as module

        learner = SimpleNamespace(username="Test Learner", email="learner@example.com")
        mirror = SimpleNamespace(
            coach_email="coach@example.com",
            coach_name="Coach",
            full_name="Test Learner",
            email="learner@example.com",
        )
        source_model = Mock()
        source_model.all_learners.filter.return_value.first.return_value = learner
        view = inspect.unwrap(module.learner_calendar_book)

        with patch.object(module, "SOURCE_MODELS", {"commercial": source_model}), \
                patch.object(module, "learner_profile_for_source", return_value=mirror), \
                patch("learner_api.booking_calendar.timezone.localdate", return_value=date(2026, 9, 7)):
            request = RequestFactory().post(
                "/learner_api/calendar/commercial/101/book/",
                data=json.dumps({
                    "sessionType": module.BOOKABLE_TYPES[0],
                    "scheduledDate": "2026-09-04",
                    "scheduledTime": "10:00",
                    "durationMinutes": 60,
                }),
                content_type="application/json",
            )
            response = view(request, "commercial", 101)

        self.assertEqual(response.status_code, 400)
        self.assertIn("already passed", json.loads(response.content)["error"])


class RescheduleEndpointTests(SimpleTestCase):
    @staticmethod
    def scheduled_record():
        return SimpleNamespace(
            pk=55,
            event_key="progress-review:101:3:2026-09-08",
            event_type="progress-review",
            sequence=3,
            status="scheduled",
            target_date=date(2026, 9, 8),
            scheduled_date=date(2026, 9, 8),
            scheduled_time=time(10, 0),
            duration_minutes=60,
            owner_name="Coach Example",
            owner_email="coach@example.com",
            learner_id=101,
            learner_name="Test Learner",
            learner_email="learner@example.com",
            meeting_provider="Microsoft Teams",
            meeting_link="https://teams.microsoft.com/l/meetup-join/test",
            graph_web_link="https://outlook.office.com/calendar/item/test",
            graph_event_id="graph-event-1",
            sync_state="synced",
            notes="",
            review_responses={},
            review_completed_at=None,
            last_graph_sync_error="",
        )

    def request(self):
        return RequestFactory().patch(
            "/learner_api/calendar/commercial/101/reschedule/",
            data=json.dumps({
                "eventKey": "progress-review:101:3:2026-09-08",
                "scheduledDate": "2026-09-09",
                "scheduledTime": "11:30",
                "durationMinutes": 45,
                "timezoneOffsetMinutes": 0,
            }),
            content_type="application/json",
        )

    def test_reschedule_updates_the_same_booking_record(self):
        from . import calendar as module

        record = self.scheduled_record()
        view = inspect.unwrap(module.learner_calendar_reschedule)
        with patch.object(module, "SOURCE_MODELS", {"commercial": Mock()}), \
                patch.object(module, "_learner_booking_record", return_value=record), \
                patch("learner_api.booking_calendar.timezone.localdate", return_value=date(2026, 9, 7)), \
                patch("learner_api.calendar_connections.booking_conflicts", return_value=False), \
                patch("coach_api.views.build_booked_calendar_event", return_value={}) as build_event, \
                patch("coach_api.views.persist_calendar_sync_reservation", return_value=record) as persist, \
                patch("coach_api.views.synchronize_reserved_calendar_event", return_value=(record, "", True)) as sync:
            response = view(self.request(), "commercial", 101)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(record.scheduled_date, date(2026, 9, 9))
        self.assertEqual(record.scheduled_time.strftime("%H:%M"), "11:30")
        self.assertEqual(record.duration_minutes, 45)
        self.assertEqual(record.owner_name, 'Coach Example')
        self.assertEqual(record.owner_email, 'coach@example.com')
        self.assertEqual(record.meeting_link, 'https://teams.microsoft.com/l/meetup-join/test')
        persist.assert_called_once_with(record)
        build_event.assert_called_once_with(record)
        sync.assert_called_once_with(record.pk, {})

    def test_reschedule_returns_conflict_for_any_other_overlapping_session(self):
        from coach_api.views import LearnerCalendarConflict
        from . import calendar as module

        record = self.scheduled_record()
        view = inspect.unwrap(module.learner_calendar_reschedule)
        with patch.object(module, "SOURCE_MODELS", {"commercial": Mock()}), \
                patch.object(module, "_learner_booking_record", return_value=record), \
                patch("learner_api.booking_calendar.timezone.localdate", return_value=date(2026, 9, 7)), \
                patch("learner_api.calendar_connections.booking_conflicts", return_value=False), \
                patch("coach_api.views.persist_calendar_sync_reservation", side_effect=LearnerCalendarConflict("This learner already has another session at that time.")), \
                patch("coach_api.views.synchronize_reserved_calendar_event") as sync:
            response = view(self.request(), "commercial", 101)

        self.assertEqual(response.status_code, 409)
        self.assertIn("already has another session", json.loads(response.content)["error"])
        sync.assert_not_called()

    def test_retry_same_time_after_failed_sync_retries_the_existing_event(self):
        from . import calendar as module

        record = self.scheduled_record()
        record.scheduled_date = date(2026, 9, 9)
        record.scheduled_time = time(11, 30)
        record.duration_minutes = 45
        record.sync_state = "failed"
        view = inspect.unwrap(module.learner_calendar_reschedule)
        with patch.object(module, "SOURCE_MODELS", {"commercial": Mock()}), \
                patch.object(module, "_learner_booking_record", return_value=record), \
                patch("learner_api.booking_calendar.timezone.localdate", return_value=date(2026, 9, 7)), \
                patch("learner_api.calendar_connections.booking_conflicts", return_value=False), \
                patch("coach_api.views.build_booked_calendar_event", return_value={}), \
                patch("coach_api.views.persist_calendar_sync_reservation", return_value=record) as persist, \
                patch("coach_api.views.synchronize_reserved_calendar_event", return_value=(record, "", True)) as sync:
            response = view(self.request(), "commercial", 101)
        self.assertEqual(response.status_code, 200)
        persist.assert_called_once_with(record)
        sync.assert_called_once_with(record.pk, {})
        self.assertEqual(record.graph_event_id, "graph-event-1")

    def test_booking_lookup_accepts_current_active_users_mirror_id(self):
        from . import calendar as module

        learner = SimpleNamespace(email="learner@example.com")
        mirror = SimpleNamespace(id=248, email="learner@example.com")
        record = self.scheduled_record()
        record.event_key = "progress-review:248:3:2026-09-08"
        record.learner_id = 248

        source_model = Mock()
        source_model.all_learners.filter.return_value.first.return_value = learner
        with patch.object(module, "SOURCE_MODELS", {"commercial": source_model}), \
                patch.object(module, "learner_profile_for_source", return_value=mirror), \
                patch.object(module.CoachCalendarEvent.objects, "filter") as event_filter:
            event_filter.return_value.first.return_value = record

            found = module._learner_booking_record("commercial", 101, record.event_key)

        self.assertIs(found, record)


class CatchupBookAndLinkTests(SimpleTestCase):
    """A catch-up booked for a reported absence is linked in the same request."""

    def book(self, *, precheck=None, link_error=None):
        from . import calendar as module
        learner = SimpleNamespace(pk=101, username='Test Learner', email='learner@example.com',
                                  case_owner='Coach', coach_name='Coach', coach_email='coach@example.com')
        mirror = SimpleNamespace(id=248, coach_email='coach@example.com', coach_name='Coach',
                                 full_name='Test Learner', email='learner@example.com')
        source_model = Mock()
        source_model.all_learners.filter.return_value.first.return_value = learner
        record = SimpleNamespace(pk=1, event_key='catch-up:248:12:2026-10-15', event_type='catch-up')
        report = SimpleNamespace(session_date=date(2026, 9, 11))
        with patch.object(module, 'SOURCE_MODELS', {'commercial': source_model}), \
                patch.object(module, 'learner_profile_for_source', return_value=mirror), \
                patch('learner_api.booking_calendar.timezone.localdate', return_value=date(2026, 9, 14)), \
                patch.object(module.CoachCalendarEvent.objects, 'filter') as events, \
                patch('learner_api.calendar_connections.booking_conflicts', return_value=False), \
                patch('learner_api.coach_availability.catchup_slot_is_free', return_value=True), \
                patch('learner_api.session_recovery.check_report_can_take_catchup',
                      side_effect=precheck, return_value=report) as check, \
                patch('learner_api.session_recovery.link_report_to_catchup', side_effect=link_error) as link, \
                patch('coach_api.views.cancel_reserved_calendar_event', return_value=(record, '')) as cancel, \
                patch('coach_api.views.reserve_coach_calendar_booking', return_value=(record, True)) as reserve, \
                patch('coach_api.views.build_booked_calendar_event', return_value={}), \
                patch('coach_api.views.synchronize_reserved_calendar_event', return_value=(record, '', True)), \
                patch.object(module, '_serialize_event', return_value={'eventKey': record.event_key}):
            events.return_value.first.return_value = None
            request = RequestFactory().post('/book/', data=json.dumps({
                'sessionType': 'catch-up', 'scheduledDate': '2026-10-15', 'scheduledTime': '11:00',
                'durationMinutes': 30, 'absenceReportId': 34,
            }), content_type='application/json')
            response = inspect.unwrap(module.learner_calendar_book)(request, 'commercial', 101)
        return response, check, link, cancel, reserve

    def test_the_new_booking_is_linked_to_the_absence_in_the_same_request(self):
        response, check, link, cancel, _reserve = self.book()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(json.loads(response.content)['linkedReportId'], 34)
        # A learner's own booking keeps the 12-hour rule for replacing a booked catch-up.
        check.assert_called_once_with(101, 34, enforce_change_cutoff=True)
        self.assertEqual(link.call_args.args[2:], (101, 34, 'catch-up:248:12:2026-10-15'))
        self.assertEqual(link.call_args.kwargs, {'enforce_change_cutoff': True})
        cancel.assert_not_called()

    def test_a_link_that_cannot_happen_is_refused_before_anything_is_booked(self):
        from .session_recovery import ReportNotFound
        response, _check, link, _cancel, reserve = self.book(precheck=ReportNotFound())
        self.assertEqual(response.status_code, 404)
        reserve.assert_not_called()
        link.assert_not_called()

    def test_a_failed_link_cancels_the_booking_instead_of_leaving_it_behind(self):
        from .absence_reports import RecoveryPlanError
        response, _check, _link, cancel, _reserve = self.book(
            link_error=RecoveryPlanError('This catch-up is already linked to another lecture.'))
        self.assertEqual(response.status_code, 409)
        self.assertIn('booking was cancelled', json.loads(response.content)['error'])
        cancel.assert_called_once()


class CatchupChangeCutoffTests(SimpleTestCase):
    """A learner moves or cancels their catch-up until 12 hours before it starts; staff at any time."""

    @staticmethod
    def catchup():
        record = RescheduleEndpointTests.scheduled_record()
        record.event_key = 'catch-up:248:12:2026-10-15'
        record.event_type = 'catch-up'
        record.scheduled_date = date(2026, 10, 15)
        record.scheduled_time = time(11, 0)
        record.save = Mock()
        return record

    @staticmethod
    def request(path, role=None, body=None):
        request = RequestFactory().post(path, data=json.dumps(body or {'eventKey': 'catch-up:248:12:2026-10-15'}),
                                        content_type='application/json')
        if role:
            request.account = SimpleNamespace(role=role)
        return request

    def reschedule(self, *, closed, role=None):
        from . import calendar as module
        record = self.catchup()
        body = {'eventKey': record.event_key, 'scheduledDate': '2026-10-16', 'scheduledTime': '10:00',
                'durationMinutes': 30, 'timezoneOffsetMinutes': -60}
        with patch.object(module, 'SOURCE_MODELS', {'commercial': Mock()}), \
                patch.object(module, '_learner_booking_record', return_value=record), \
                patch('learner_api.coach_availability.catchup_change_closed', return_value=closed), \
                patch('learner_api.booking_calendar.timezone.localdate', return_value=date(2026, 10, 14)), \
                patch.object(module, '_catchup_time_error', return_value=None), \
                patch('learner_api.calendar_connections.booking_conflicts', return_value=False), \
                patch('coach_api.views.build_booked_calendar_event', return_value={}), \
                patch('coach_api.views.persist_calendar_sync_reservation', return_value=record) as persist, \
                patch('coach_api.views.synchronize_reserved_calendar_event', return_value=(record, '', True)):
            response = inspect.unwrap(module.learner_calendar_reschedule)(
                self.request('/reschedule/', role, body), 'commercial', 101)
        return response, persist

    def cancel(self, *, closed, role=None, status='scheduled'):
        from . import calendar as module
        record = self.catchup()
        record.status = status
        with patch.object(module, 'SOURCE_MODELS', {'commercial': Mock()}), \
                patch.object(module, '_learner_booking_record', return_value=record), \
                patch('learner_api.coach_availability.catchup_change_closed', return_value=closed), \
                patch('coach_api.views.delete_calendar_event_from_graph', return_value='') as delete, \
                patch.object(module, '_cancel_enrolment_review'), \
                patch('curriculum_api.live_session_absences.release_cancelled_catchup', return_value=1) as release:
            response = inspect.unwrap(module.learner_calendar_cancel)(self.request('/cancel/', role), 'commercial', 101)
        return response, delete, release, record

    def test_learner_cannot_move_a_catchup_within_12_hours(self):
        response, persist = self.reschedule(closed=True)
        self.assertEqual(response.status_code, 409)
        self.assertIn('12 hours', json.loads(response.content)['error'])
        persist.assert_not_called()

    def test_learner_moves_a_catchup_earlier_than_12_hours(self):
        response, persist = self.reschedule(closed=False)
        self.assertEqual(response.status_code, 200)
        persist.assert_called_once()

    def test_staff_may_move_a_catchup_at_any_time(self):
        response, persist = self.reschedule(closed=True, role='staff')
        self.assertEqual(response.status_code, 200)
        persist.assert_called_once()

    def test_learner_cannot_cancel_a_catchup_within_12_hours(self):
        response, delete, release, record = self.cancel(closed=True)
        self.assertEqual(response.status_code, 409)
        delete.assert_not_called()
        release.assert_not_called()
        self.assertEqual(record.status, 'scheduled')

    def test_cancelled_catchup_returns_the_absence_to_needing_a_recovery(self):
        response, delete, release, record = self.cancel(closed=False)
        self.assertEqual(response.status_code, 200)
        delete.assert_called_once_with(record)
        self.assertEqual(record.status, 'cancelled')
        self.assertEqual(release.call_args.kwargs['event_key'], 'catch-up:248:12:2026-10-15')

    def test_a_finished_catchup_cannot_be_cancelled(self):
        response, delete, _release, _record = self.cancel(closed=False, status='completed')
        self.assertEqual(response.status_code, 409)
        delete.assert_not_called()

    def test_cutoff_is_counted_in_uk_time(self):
        from datetime import timezone as dt_timezone
        from .coach_availability import catchup_change_closed
        # 15 Oct 11:00 UK (BST) is 10:00 UTC.
        self.assertTrue(catchup_change_closed(date(2026, 10, 15), time(11, 0),
                                              datetime(2026, 10, 14, 22, 30, tzinfo=dt_timezone.utc)))
        self.assertFalse(catchup_change_closed(date(2026, 10, 15), time(11, 0),
                                               datetime(2026, 10, 14, 21, 30, tzinfo=dt_timezone.utc)))
