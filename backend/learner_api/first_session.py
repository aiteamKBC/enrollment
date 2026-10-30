"""Book a new learner's first session with their case owner, at enrolment.

The first session used to be something the learner requested from their own
calendar once they were Active. It is now arranged *for* them while they are
being created, which is both earlier and simpler: the person enrolling them
picks a date and time, and the meeting exists before the learner ever signs in.

Booking here rather than from the learner calendar also sidesteps a constraint
that made the old route unusable for this: ``learner_calendar_book`` requires an
Active mirror with a coach (calendar.py, "Only Active learners can book coach
sessions"), and a learner being created is neither Active nor mirrored yet. The
case owner is already resolved to a name and email during creation, and
``reserve_coach_calendar_booking`` takes plain values, so nothing here needs the
mirror.

Two rules from the wider platform apply:

* Learners imported from Aptem (non-empty ``aptem_id``) already have a start
  date and a history behind them, so they are skipped entirely -- the same
  discriminator ``old_otjh.gate`` uses to decide who is a native LMS learner.
* A Microsoft outage must not fail the enrolment. The reservation is durable and
  the Graph sync is a separate step, so a failed sync leaves a recoverable
  booking and an honest report, never a fabricated success.

Times are UK wall clock throughout -- see ``UK`` below.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import date, time
from zoneinfo import ZoneInfo

from django.db import DatabaseError, connections
from django.utils import timezone

from .booking_calendar import booking_date_restriction
from .learner_dates import save_enrolment_fields

logger = logging.getLogger(__name__)

#: The college's time zone. Every session time on this path is UK wall clock --
#: the time staff typed is the time the meeting happens in Kent, and the Graph
#: payload carries "GMT Standard Time" (coach_api.views), which is the same zone
#: including BST. Nothing here converts between zones; naming it keeps the
#: convention explicit rather than inherited from whatever the server runs on.
UK = ZoneInfo("Europe/London")

SESSION_TYPE = "first-session"

#: Same default the learner booking modal offers, and what Graph is handed when
#: nobody says otherwise.
DEFAULT_DURATION_MINUTES = 60

#: Field separator for the idempotency digest. A control character, so it cannot
#: occur inside a date, a time or the session type and let two different slots
#: collide onto one key.
_DIGEST_SEPARATOR = "\x1f"


def _text(value):
    return str(value or "").strip()


def imported_from_aptem(learner) -> bool:
    """Whether this learner came from Aptem and should be skipped.

    Mirrors ``student_activity_access.student_activity_available``: the column is
    text against a bigint source, so blank, non-numeric and zero all mean "not an
    Aptem learner" rather than raising.
    """
    try:
        return int(_text(getattr(learner, "aptem_id", ""))) > 0
    except (TypeError, ValueError):
        return False


def _idempotency_key(learner_id, scheduled_date, scheduled_time) -> str:
    """Stable identity for one learner's first session.

    Derived from the learner and the slot, so a double-submitted form or a
    retried import cannot produce two meetings. Same shape as the learner
    calendar's own deterministic key.
    """
    digest = hashlib.sha256(
        _DIGEST_SEPARATOR.join(
            [
                str(learner_id),
                SESSION_TYPE,
                scheduled_date.isoformat(),
                scheduled_time.isoformat(),
            ]
        ).encode("utf-8")
    ).hexdigest()
    return f"enrolment-first-session:{digest}"


def parse_slot(payload):
    """Pull the first-session date and time out of a create payload.

    Deliberately *not* routed through ``WRITABLE_FIELDS``: these two are not
    columns on the learner row, and letting them through the normal field mapper
    would hand unknown keyword arguments to ``EnrolmentUser.objects.create``.

    Returns ``(date, time, error)``. Anything unparseable is reported rather than
    silently dropped, so a mistyped slot cannot enrol somebody with no session.
    """
    raw_date = _text(payload.get("firstSessionDate"))
    raw_time = _text(payload.get("firstSessionTime"))
    if not raw_date and not raw_time:
        return None, None, ""
    if not raw_date or not raw_time:
        return None, None, "A first session needs both a date and a time."
    try:
        scheduled_date = date.fromisoformat(raw_date)
    except ValueError:
        return None, None, "First session date must be a valid date in YYYY-MM-DD format."
    try:
        # Browsers send HH:MM; accept HH:MM:SS too rather than rejecting a value
        # that is merely more precise than the input produced.
        scheduled_time = time.fromisoformat(raw_time)
    except ValueError:
        return None, None, "First session time must be a valid time in HH:MM format."
    return scheduled_date, scheduled_time, ""


def uk_today():
    """Today in the college's own time zone.

    Not ``timezone.localdate()``: that follows ``settings.TIME_ZONE``, which is
    environment-overridable (``SYSTEM_TIME_ZONE``). A server running on UTC in
    late-evening BST is already on tomorrow's date, which would reject a session
    booked for this afternoon as being in the past. The college's working day is
    the thing being asked about, so it is named outright.
    """
    return timezone.now().astimezone(UK).date()


def validate_slot(scheduled_date, scheduled_time):
    """The reason this slot cannot be booked, or None.

    Reuses the booking calendar every other learner booking is checked against,
    so Saturdays, bank holidays and past dates are refused with the same wording
    staff and learners already see elsewhere -- against the UK working day, not
    whatever day it happens to be where the server runs. Sundays are open for a
    first session only.
    """
    if scheduled_date is None or scheduled_time is None:
        return "A first session date and time are required."
    restriction = booking_date_restriction(scheduled_date, today=uk_today(), allow_sunday=True)
    return restriction.message if restriction is not None else None


def book_first_session(learner, *, scheduled_date, scheduled_time,
                       owner_name="", owner_email="",
                       duration_minutes=DEFAULT_DURATION_MINUTES, notes=""):
    """Reserve and sync the first session, and record it on the learner.

    Returns a result dict describing what actually happened -- never raises for a
    Microsoft failure, because the learner has already been created by the time
    this runs and losing them to a calendar outage would be the worse error.

    ``booked`` is true only when the reservation itself succeeded. ``warning``
    carries a Graph sync failure, which means the row exists and is recoverable
    but nobody has been invited yet: a local save is not proof Microsoft accepted
    the meeting.
    """
    if imported_from_aptem(learner):
        return {"booked": False, "skipped": "aptem", "warning": "", "error": ""}

    problem = validate_slot(scheduled_date, scheduled_time)
    if problem:
        return {"booked": False, "skipped": "", "warning": "", "error": problem}

    owner_email = _text(owner_email)
    if not owner_email:
        return {
            "booked": False,
            "skipped": "",
            "warning": "",
            "error": (
                "The case owner has no email address on their staff record, so the "
                "first session could not be booked."
            ),
        }

    # Imported here rather than at module scope: coach_api.views imports from
    # learner_api, and a top-level import would close that circle at startup.
    from coach_api.models import CoachCalendarEvent
    from coach_api.views import (
        build_booked_calendar_event,
        reserve_coach_calendar_booking,
        synchronize_reserved_calendar_event,
    )

    learner_name = _text(getattr(learner, "username", ""))
    learner_email = _text(getattr(learner, "email", ""))

    try:
        record, _created = reserve_coach_calendar_booking(
            owner_email=owner_email,
            owner_name=_text(owner_name) or "Case owner",
            learner_id=int(learner.pk),
            learner_name=learner_name,
            learner_email=learner_email,
            session_type=SESSION_TYPE,
            scheduled_date=scheduled_date,
            scheduled_time=scheduled_time,
            duration_minutes=duration_minutes,
            notes=_text(notes)[:500],
            idempotency_key=_idempotency_key(learner.pk, scheduled_date, scheduled_time),
            initial_status=CoachCalendarEvent.STATUS_SCHEDULED,
        )
    except ValueError as exc:
        # A reused idempotency key for a different slot, or an unsupported
        # status. The learner exists; the session does not.
        logger.warning("first_session: could not reserve for learner %s: %s", learner.pk, exc)
        return {"booked": False, "skipped": "", "warning": "", "error": str(exc)}
    except DatabaseError:
        logger.exception("first_session: reservation failed for learner %s", learner.pk)
        return {
            "booked": False,
            "skipped": "",
            "warning": "",
            "error": "The first session could not be booked. Please book it from the learner's calendar.",
        }

    warning = ""
    try:
        record, warning, _attempted = synchronize_reserved_calendar_event(
            record.pk, build_booked_calendar_event(record)
        )
    except Exception:  # noqa: BLE001 - a sync failure must not undo the enrolment
        logger.exception("first_session: Graph sync failed for learner %s", learner.pk)
        warning = "The session was booked but the Teams meeting has not been created yet."

    if warning:
        # Loud in the log: the booking exists and nobody has been invited to it.
        logger.error(
            "first_session: Graph sync incomplete for learner %s (owner=%s): %s",
            learner.pk, owner_email, warning,
        )

    _stamp(learner, scheduled_date, booked=True)

    return {
        "booked": True,
        "skipped": "",
        "warning": warning,
        "error": "",
        "eventKey": getattr(record, "event_key", ""),
        "scheduledDate": scheduled_date.isoformat(),
        "scheduledTime": scheduled_time.isoformat(),
    }


def record_start_date_only(learner, scheduled_date):
    """Record the programme start without booking anything.

    For an imported row that names the day but not the hour. The learner starts
    on that date either way, so it is written; the session itself is left to be
    booked from the learner's calendar once a time is agreed.

    ``first_session_booked`` is set to False rather than left null: "we know it
    is not booked" is a different fact from "nobody has said", and it is what
    lets a report find the learners still waiting for a slot.
    """
    if imported_from_aptem(learner):
        return {"booked": False, "skipped": "aptem", "warning": "", "error": ""}
    _stamp(learner, scheduled_date, booked=False)
    return {
        "booked": False,
        "skipped": "",
        "warning": "",
        "error": "",
        "scheduledDate": scheduled_date.isoformat(),
        "startDateRecorded": True,
    }


def _stamp(learner, scheduled_date, *, booked):
    """Write the start date and the booking state onto the learner row.

    ``learner_start_date`` is the date the programme starts, and the first
    session is that start -- so the date is written there. It is not a spare
    field: it is the strict anchor review scheduling reads, so this moves the
    learner's review dates deliberately rather than as a side effect.

    Written through ``save_enrolment_fields`` so the ``Learner`` profile mirror
    stays in step, the same path the enrolment screens use.
    """
    learner.learner_start_date = scheduled_date.isoformat()
    try:
        save_enrolment_fields(learner, ["learner_start_date"])
    except DatabaseError:
        # The meeting is real either way; losing the stamp must not lose it.
        logger.exception("first_session: could not record start date on learner %s", learner.pk)
    _write_booking_columns(learner, booked=booked)


#: The two columns added by
#: sql/2026-09-17_first_session_booking_on_created_users.sql. Unmapped on the
#: model on purpose -- see the note in models.EnrolmentUser.
_BOOKED_COLUMN = "First_session_booked"
_BOOKED_AT_COLUMN = "First_session_booked_at"


def booking_columns_exist():
    """Whether this database has the first-session booking columns yet.

    Asked rather than assumed: the columns arrive by a hand-run SQL file, so a
    checkout can be ahead of the database it is pointed at. Answering False
    there costs the booking flag and nothing else.
    """
    try:
        with connections["enrolment"].cursor() as cursor:
            cursor.execute(
                """
                select count(*) from information_schema.columns
                 where table_schema = 'enrolment'
                   and table_name   = 'Created_users'
                   and column_name in (%s, %s)
                """,
                [_BOOKED_COLUMN, _BOOKED_AT_COLUMN],
            )
            return cursor.fetchone()[0] == 2
    except DatabaseError:
        logger.exception("first_session: could not check for the booking columns")
        return False


def _write_booking_columns(learner, *, booked):
    """Record the booking flag, if the database has somewhere to put it.

    Raw SQL against the two unmapped columns. Only a real booking carries a
    booking time: a recorded start date with no session has nothing to stamp,
    and inventing one would read as a meeting that happened.

    A database without the columns simply does not get the flag. The booking and
    the start date are already saved by then, so the feature works; what is lost
    is the "was it booked" marker, which the SQL file restores when it is run.
    """
    if not booking_columns_exist():
        logger.warning(
            "first_session: %s/%s are missing, so the booking flag for learner %s "
            "was not recorded. Run sql/2026-09-17_first_session_booking_on_created_users.sql.",
            _BOOKED_COLUMN, _BOOKED_AT_COLUMN, learner.pk,
        )
        return
    booked_at = timezone.now() if booked else None
    try:
        with connections["enrolment"].cursor() as cursor:
            cursor.execute(
                f'''update enrolment."Created_users"
                       set "{_BOOKED_COLUMN}" = %s, "{_BOOKED_AT_COLUMN}" = %s
                     where id = %s''',
                [booked, booked_at, learner.pk],
            )
    except DatabaseError:
        logger.exception("first_session: could not record booking flag on learner %s", learner.pk)
