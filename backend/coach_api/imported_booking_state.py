"""Effective booking state for imported Aptem reviews.

Three states stay separate and must not be conflated:

* the Aptem row is immutable source evidence (status and planned date),
* the linked calendar record owns the *booking*,
* the overlay owns the *review lifecycle* that Start/Submit read.

Both booking paths -- the Coach's `coach_review_instance_book` and the
learner calendar -- plus the overlay initializer ask the same questions here,
so one Review can never be booked on the calendar and unbooked in the form.

Callers must resolve the record through the durable source/overlay linkage
(`booking_for_imported_review` / `booking_for_overlay`) first: a matching
`event_key` is not an association, and nothing in this module re-derives one.
"""
from .models import CoachCalendarEvent


def verified_meeting(record, *, owner_email, event_type):
    """True when `record` is this coach's confirmed, synchronized appointment.

    This is the predicate `coach_review_instance_adopt_booking` already applies
    before it advances an overlay, kept in one place so the Coach booking, the
    learner booking and the initializer cannot drift apart.
    """
    from .views import calendar_record_has_launch_url

    return bool(
        record
        and record.owner_email.strip().casefold() == (owner_email or "").strip().casefold()
        and record.event_type == event_type
        and record.status in {CoachCalendarEvent.STATUS_SCHEDULED,
                              CoachCalendarEvent.STATUS_IN_PROGRESS}
        and record.sync_state == CoachCalendarEvent.SYNC_SYNCED
        and str(record.graph_event_id or "").strip()
        and calendar_record_has_launch_url(record)
        and record.scheduled_date is not None
        and record.scheduled_time is not None
    )


def unreserved_placeholder(record):
    """True for a row a booking attempt created but never reserved a slot on.

    `persist_calendar_sync_reservation` writes the schedule, the scheduled
    status and the pending sync state together, so a row still holding none of
    them has never carried a booking. Everything that does carry history is
    excluded: a real or cancelled booking (status), an in-flight or failed
    synchronization lease (sync_state), an existing Teams meeting
    (graph_event_id / launch URL) and any Native review linkage.
    """
    return bool(
        record
        and record.status == CoachCalendarEvent.STATUS_NOT_SCHEDULED
        and record.scheduled_date is None
        and record.scheduled_time is None
        and record.sync_state == CoachCalendarEvent.SYNC_PENDING
        and not str(record.graph_event_id or "").strip()
        and not str(record.meeting_link or "").strip()
        and not str(record.graph_web_link or "").strip()
        and not str(record.review_instance_id or "").strip()
        and not str(record.review_template_id or "").strip()
    )


def schedule_for_verified_meeting(overlay, record, *, owner_email, event_type):
    """Advance a never-scheduled overlay onto its verified meeting.

    `coach_review_instance_book` already performs exactly this transition once
    Graph confirms the appointment. Booking is the same business action
    whichever side starts it, and Start/Submit read the lifecycle rather than
    the calendar, so the learner path and the initializer need it too.

    Only not-scheduled -> scheduled happens here. A booking is not evidence
    that the meeting took place, so nothing is started, submitted, signed or
    completed, and an overlay that already moved on is left untouched. The
    caller holds the learner -> calendar -> overlay locks.
    """
    from .models import ImportedReviewInstance

    if (not overlay or overlay.completed_at
            or overlay.status != ImportedReviewInstance.STATUS_NOT_SCHEDULED
            or overlay.owner_email.strip().casefold() != (owner_email or "").strip().casefold()
            or not verified_meeting(record, owner_email=owner_email, event_type=event_type)):
        return False
    overlay.status = ImportedReviewInstance.STATUS_SCHEDULED
    overlay.save(update_fields=["status", "updated_at"])
    return True
