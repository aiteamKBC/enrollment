"""Booking the first session while the learner is being enrolled.

These are the rules the feature exists for, rather than the mechanics of the
calendar it books into:

* an Aptem learner already has a start date and is never booked for;
* the booked date becomes the learner's ``Learner_start_date``, because that is
  when their programme starts and it is what review scheduling anchors on;
* a Microsoft failure leaves a real, recoverable booking and says so, instead of
  reporting a meeting nobody was invited to.

``SimpleTestCase`` throughout: everything that would touch the database or Graph
is patched, so the suite needs neither.
"""
from datetime import date, datetime, time, timedelta
from datetime import timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.db import DatabaseError
from django.test import SimpleTestCase
from django.utils import timezone

from . import first_session
from .booking_calendar import booking_date_restriction


def bookable_day():
    """The next working day the booking calendar accepts, in UK time."""
    day = first_session.uk_today() + timedelta(days=1)
    while booking_date_restriction(day, today=first_session.uk_today()) is not None:
        day += timedelta(days=1)
    return day


def learner(**overrides):
    fields = {
        "pk": 7,
        "aptem_id": None,
        "username": "Test Learner",
        "email": "learner@example.test",
        "learner_start_date": None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class AptemLearnersAreSkippedTests(SimpleTestCase):
    def test_a_learner_imported_from_aptem_is_not_booked(self):
        subject = learner(aptem_id="123456")

        with patch.object(first_session, "save_enrolment_fields") as save:
            result = first_session.book_first_session(
                subject, scheduled_date=bookable_day(), scheduled_time=time(10, 0),
                owner_email="coach@example.test",
            )

        self.assertFalse(result["booked"])
        self.assertEqual(result["skipped"], "aptem")
        # Their existing start date is theirs; nothing is rewritten.
        save.assert_not_called()

    def test_a_blank_or_non_numeric_aptem_id_is_not_an_aptem_learner(self):
        for value in ("", "   ", None, "not-a-number", "0"):
            with self.subTest(aptem_id=value):
                self.assertFalse(first_session.imported_from_aptem(learner(aptem_id=value)))

    def test_a_real_aptem_id_is_recognised(self):
        self.assertTrue(first_session.imported_from_aptem(learner(aptem_id=" 4321 ")))


class SlotValidationTests(SimpleTestCase):
    def test_a_weekend_is_refused_with_the_shared_message(self):
        saturday = bookable_day()
        while saturday.weekday() != 5:
            saturday += timedelta(days=1)

        self.assertIn("Saturdays", first_session.validate_slot(saturday, time(10, 0)))

    def test_a_past_date_is_refused(self):
        yesterday = timezone.localdate() - timedelta(days=1)

        self.assertIn("already passed", first_session.validate_slot(yesterday, time(10, 0)))

    def test_a_missing_half_of_the_slot_is_reported(self):
        self.assertIn("required", first_session.validate_slot(None, time(10, 0)))

    def test_parse_slot_accepts_the_browser_format(self):
        day = bookable_day()

        parsed_date, parsed_time, error = first_session.parse_slot(
            {"firstSessionDate": day.isoformat(), "firstSessionTime": "09:30"}
        )

        self.assertEqual((parsed_date, parsed_time), (day, time(9, 30)))
        self.assertEqual(error, "")

    def test_parse_slot_reports_a_half_filled_slot_rather_than_dropping_it(self):
        _, _, error = first_session.parse_slot({"firstSessionDate": "2026-10-01"})

        self.assertIn("both a date and a time", error)

    def test_parse_slot_is_silent_when_no_session_was_supplied(self):
        self.assertEqual(first_session.parse_slot({}), (None, None, ""))

    def test_parse_slot_rejects_a_malformed_date(self):
        _, _, error = first_session.parse_slot(
            {"firstSessionDate": "01/10/2026", "firstSessionTime": "09:30"}
        )

        self.assertIn("YYYY-MM-DD", error)


class BookingTests(SimpleTestCase):
    def _book(self, subject, *, warning="", reserve=None, save=None):
        record = SimpleNamespace(pk=99, event_key="evt-1")
        reserve = reserve or (lambda **kwargs: (record, True))
        with patch.object(first_session, "save_enrolment_fields", save or (lambda *a, **k: None)), \
                patch("coach_api.views.reserve_coach_calendar_booking", side_effect=reserve), \
                patch("coach_api.views.build_booked_calendar_event", return_value={}), \
                patch("coach_api.views.synchronize_reserved_calendar_event",
                      return_value=(record, warning, True)):
            return first_session.book_first_session(
                subject,
                scheduled_date=bookable_day(),
                scheduled_time=time(10, 0),
                owner_name="Test Coach",
                owner_email="coach@example.test",
            )

    def test_the_booked_date_becomes_the_learner_start_date(self):
        subject = learner()
        saved = {}

        def save(user, fields):
            saved["fields"] = list(fields)
            saved["start"] = user.learner_start_date

        with patch.object(first_session, "_write_booking_columns") as flag:
            result = self._book(subject, save=save)

        self.assertTrue(result["booked"])
        self.assertEqual(saved["start"], bookable_day().isoformat())
        self.assertEqual(saved["fields"], ["learner_start_date"])
        # The flag lives in two unmapped columns, written separately.
        flag.assert_called_once()
        self.assertIs(flag.call_args.kwargs["booked"], True)

    def test_a_graph_failure_still_reports_the_booking_and_its_warning(self):
        result = self._book(learner(), warning="Microsoft rejected the request.")

        # The reservation is durable, so the booking is real...
        self.assertTrue(result["booked"])
        # ...but nobody has been invited, and the caller is told so.
        self.assertEqual(result["warning"], "Microsoft rejected the request.")

    def test_a_case_owner_without_an_email_cannot_be_booked_with(self):
        result = first_session.book_first_session(
            learner(), scheduled_date=bookable_day(), scheduled_time=time(10, 0),
            owner_name="Ambiguous Name", owner_email="",
        )

        self.assertFalse(result["booked"])
        self.assertIn("no email address", result["error"])

    def test_a_failed_reservation_is_not_recorded_as_booked(self):
        subject = learner()

        def reserve(**kwargs):
            raise DatabaseError("gone")

        with patch.object(first_session, "save_enrolment_fields") as save, \
                patch("coach_api.views.reserve_coach_calendar_booking", side_effect=reserve), \
                patch("coach_api.views.build_booked_calendar_event", return_value={}), \
                patch("coach_api.views.synchronize_reserved_calendar_event"):
            result = first_session.book_first_session(
                subject, scheduled_date=bookable_day(), scheduled_time=time(10, 0),
                owner_email="coach@example.test",
            )

        self.assertFalse(result["booked"])
        self.assertTrue(result["error"])
        # Nothing is stamped on a learner whose session does not exist.
        save.assert_not_called()

    def test_the_same_slot_produces_the_same_idempotency_key(self):
        day, slot = bookable_day(), time(10, 0)

        first = first_session._idempotency_key(7, day, slot)
        again = first_session._idempotency_key(7, day, slot)
        other_slot = first_session._idempotency_key(7, day, time(11, 0))
        other_learner = first_session._idempotency_key(8, day, slot)

        self.assertEqual(first, again)
        self.assertNotEqual(first, other_slot)
        self.assertNotEqual(first, other_learner)


class UkTimeZoneTests(SimpleTestCase):
    """Times and dates on this path are the college's, not the server's."""

    def test_today_follows_uk_not_the_server_time_zone(self):
        # 23:30 UTC on a summer evening is already tomorrow in London (BST).
        summer_evening = datetime(2026, 6, 15, 23, 30, tzinfo=dt_timezone.utc)

        with patch.object(first_session.timezone, "now", return_value=summer_evening):
            self.assertEqual(first_session.uk_today(), date(2026, 6, 16))

    def test_a_session_later_today_is_not_rejected_as_past(self):
        # The bug this guards: a server on UTC rolling past midnight while it is
        # still the same working day in Kent would refuse a valid booking.
        uk_evening = datetime(2026, 6, 15, 22, 0, tzinfo=dt_timezone.utc)

        with patch.object(first_session.timezone, "now", return_value=uk_evening):
            today_in_uk = first_session.uk_today()
            problem = first_session.validate_slot(today_in_uk, time(23, 0))

        # 16 June 2026 is a Tuesday, so nothing else refuses it.
        self.assertIsNone(problem)


class StartDateWithoutABookingTests(SimpleTestCase):
    """An imported row that knows the day but not the hour."""

    def test_the_start_date_is_recorded_and_nothing_is_booked(self):
        subject = learner()
        saved = {}

        def save(user, fields):
            saved["fields"] = list(fields)
            saved["start"] = user.learner_start_date

        day = bookable_day()
        with patch.object(first_session, "save_enrolment_fields", save),                 patch.object(first_session, "_write_booking_columns") as flag:
            result = first_session.record_start_date_only(subject, day)

        self.assertFalse(result["booked"])
        self.assertTrue(result["startDateRecorded"])
        self.assertEqual(saved["start"], day.isoformat())
        # False, not None: "known to be unbooked" is not "nobody has said".
        self.assertIs(flag.call_args.kwargs["booked"], False)

    def test_an_aptem_learner_keeps_their_own_start_date(self):
        subject = learner(aptem_id="123456")

        with patch.object(first_session, "save_enrolment_fields") as save:
            result = first_session.record_start_date_only(subject, bookable_day())

        self.assertEqual(result["skipped"], "aptem")
        save.assert_not_called()


class MissingBookingColumnsTests(SimpleTestCase):
    """A database that has not had the SQL file run against it yet.

    This is the failure that took login down once: the columns were mapped on
    the model before they existed, so Django selected them in every query
    against Created_users -- including the one login runs per request. They are
    unmapped now, and the booking flag degrades to "not recorded" instead.
    """

    def test_the_booking_still_succeeds_without_the_columns(self):
        subject = learner()

        with patch.object(first_session, "booking_columns_exist", return_value=False),                 patch.object(first_session, "save_enrolment_fields") as save:
            first_session._stamp(subject, bookable_day(), booked=True)

        # The start date is the part that matters, and it still lands.
        save.assert_called_once()
        self.assertEqual(subject.learner_start_date, bookable_day().isoformat())

    def test_the_flag_is_written_when_the_columns_are_there(self):
        subject = learner()
        executed = {}

        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def execute(self, sql, params=None):
                executed["sql"] = sql
                executed["params"] = params

        class Connection:
            def cursor(self): return Cursor()

        with patch.object(first_session, "booking_columns_exist", return_value=True),                 patch.object(first_session, "connections", {"enrolment": Connection()}):
            first_session._write_booking_columns(subject, booked=True)

        self.assertIn("First_session_booked", executed["sql"])
        self.assertIs(executed["params"][0], True)
        self.assertIsNotNone(executed["params"][1])
        self.assertEqual(executed["params"][2], subject.pk)
