"""Aptem Personal Support Plan bookings, independent of form templates.

The calendar owns the appointment; source reviews and form answers are never
written here. Keep the existing imported event key and Graph update/retry path.
"""
from django.db import IntegrityError, transaction

from .models import CoachCalendarEvent, ImportedReviewInstance
from .migrated_reviews import initial_local_status
from .review_sources import booking_for_imported_review, ReviewIdentityConflict


def is_personal_support_plan(review_type):
    return str(review_type or "").strip().casefold() == "personal support plan"


def booking_state(owner, learner, review, overlay=None, *, preview_only=False, lock=False):
    from . import views as v

    conflict = False
    try:
        record = booking_for_imported_review(learner, review, overlay, lock=lock)
    except ReviewIdentityConflict:
        record, conflict = None, True
    valid = bool(record and record.owner_email.casefold() == owner.casefold()
                 and record.learner_id == learner.id and record.event_type == "progress-review"
                 and not record.review_instance_id and not record.review_template_id)
    conflict = conflict or bool(record and not valid)
    active = bool(is_personal_support_plan(review.get("type"))
                  and initial_local_status(review.get("status")) and not review.get("completedDate")
                  and (not overlay or (initial_local_status(overlay.status) and not overlay.completed_at)))
    verified = bool(valid and record.graph_event_id and v.calendar_record_has_launch_url(record)
                    and record.sync_state == CoachCalendarEvent.SYNC_SYNCED)
    mutable = bool(active and not preview_only and not conflict
                   and (not record or (record.status == CoachCalendarEvent.STATUS_SCHEDULED
                        and record.sync_state in {CoachCalendarEvent.SYNC_PENDING,
                                                  CoachCalendarEvent.SYNC_SYNCED,
                                                  CoachCalendarEvent.SYNC_FAILED})))
    existing_meeting = bool(valid and record.graph_event_id)
    status = (overlay.status if overlay else review.get("status")) or "not-scheduled"
    if verified and active:
        status = record.status
    from .booking_reservations import review_occupies_slot
    return record, {
        "reserved": bool(valid and review_occupies_slot(record)),
        "formRequired": False,
        "status": status,
        "booked": bool(verified and record.status in {"scheduled", "in-progress"}),
        "conflict": conflict,
        "canBook": bool(mutable and not existing_meeting),
        "canReschedule": bool(mutable and existing_meeting),
        "canAttach": False,
        "eventKey": record.event_key if valid else None,
        "scheduledDate": record.scheduled_date.isoformat() if valid and record.scheduled_date else None,
        "scheduledTime": v.format_time_value(record.scheduled_time) if valid and record.scheduled_time else None,
        "durationMinutes": record.duration_minutes if valid else None,
        "meetingLink": (record.meeting_link or record.graph_web_link) if verified else None,
        "syncState": record.sync_state if valid else None,
    }


def can_refresh_meeting(owner, record):
    """Authorize the existing PSP meeting panel through its imported source.

    PSP appointments do not require an initialized form and have no MCM/PR
    Check Session adapter. A calendar key alone cannot grant this exception.
    """
    from . import views as v

    if (record.owner_email.casefold() != owner.casefold()
            or record.event_type != "progress-review"
            or record.review_instance_id or record.review_template_id
            or not record.graph_event_id or record.sync_state != CoachCalendarEvent.SYNC_SYNCED):
        return False
    definition = v._imported_review_definition(owner, record.event_key)
    if not definition or definition.get("previewOnly"):
        return False
    instance = definition.get("instance") or {}
    source = definition.get("historicalReview") or {}
    booking = definition.get("booking") or {}
    return bool(
        (definition.get("template") or {}).get("reviewTypeCode") == "aptem_psp"
        and is_personal_support_plan(source.get("type"))
        and source.get("status") in v.MIGRATED_EDITABLE_STATUSES
        and not v.migrated_source_is_completed(source)
        and (definition.get("localStatus") or instance.get("status")) in v.MIGRATED_EDITABLE_STATUSES
        and not instance.get("completedAt")
        and instance.get("id") == record.event_key
        and instance.get("learnerId") == record.learner_id
        and booking.get("eventKey") == record.event_key
        and not booking.get("conflict")
        and booking.get("syncState") == CoachCalendarEvent.SYNC_SYNCED
        and booking.get("meetingLink")
        and booking["meetingLink"] == (record.meeting_link or record.graph_web_link)
    )


def book(request, instance_id, definition):
    from . import views as v

    def fail(message, code="BOOKING_CONFLICT", status=409):
        return v.coach_error(request, code=code, message=message, status=status)

    owner = v.authenticated_coach_email(request).strip().casefold()
    learner_id = definition["instance"]["learnerId"]
    learner = next((item for item in v.fetch_caseload_dashboard_profiles(owner)
                    if item.id == learner_id), None)
    if learner is None:
        return fail("The learner is no longer assigned to this coach.", status=404)
    rescheduling = request.method == "PATCH"
    permission = "canReschedule" if rescheduling else "canBook"
    booking = definition.get("booking") or {}
    if not booking.get(permission) and not (not rescheduling and booking.get("canReschedule") and booking.get("booked")):
        return fail("Reload this Personal Support Plan to check its current booking.")
    try:
        payload = v.parse_json_body(request)
        validator = v.ObjectValidator(payload)
        scheduled_date = validator.iso_date("scheduledDate", required=True)
        scheduled_time = validator.clock_time("scheduledTime", required=True)
        duration = validator.integer("durationMinutes", required=True, minimum=15, maximum=480)
        offset = validator.integer("timezoneOffsetMinutes", default=0, minimum=-840, maximum=840)
        if scheduled_date and scheduled_date < v.date.today():
            validator.error("scheduledDate", "Choose today or a future date.")
        if rescheduling and not isinstance(payload.get("expectedBooking"), dict):
            validator.error("expectedBooking", "Reload the existing booking before rescheduling.")
        from .booking_hours import validate_review_time_precision
        try:
            validate_review_time_precision(payload.get("scheduledTime"))
        except ValueError as exc:
            validator.error("scheduledTime", str(exc))
        validator.check()
        previous = None
        if rescheduling:
            expected = v.ObjectValidator(payload["expectedBooking"])
            previous = (expected.iso_date("scheduledDate", required=True),
                        expected.clock_time("scheduledTime", required=True),
                        expected.integer("durationMinutes", required=True, minimum=15, maximum=480))
            expected.check()
    except v.ValidationError as exc:
        return v.validation_error_response(exc)

    desired = (scheduled_date, scheduled_time, duration)
    review = definition["historicalReview"]
    try:
        record = booking_for_imported_review(learner, review)
        same_persisted_slot = bool(record and record.status == CoachCalendarEvent.STATUS_SCHEDULED
                                   and (record.scheduled_date, record.scheduled_time, record.duration_minutes) == desired)
        if not same_persisted_slot:
            from .booking_hours import validate_review_interval
            try:
                validate_review_interval(scheduled_date, scheduled_time, duration)
            except ValueError as exc:
                return fail(str(exc), code="INVALID_REVIEW_TIME", status=400)
        if not same_persisted_slot and v.coach_learner_personal_calendar_conflicts(
            learner, scheduled_date, scheduled_time, duration, offset,
            exclude_record=record, business_time_zone="Europe/London",
        ):
            return fail("This learner is busy at that time.")
        if not same_persisted_slot:
            from .booking_availability import ensure_coach_review_slot_available
            ensure_coach_review_slot_available(owner, scheduled_date, scheduled_time, duration, record=record)
        with transaction.atomic():
            v.lock_learner_calendar(learner_id)
            # Calendar then overlay: the same lock order as other Review writers.
            record = booking_for_imported_review(learner, review, lock=True)
            overlay = ImportedReviewInstance.objects.select_for_update().filter(
                learner_id=learner_id, event_key=instance_id,
            ).first()
            current = v._imported_review_definition(owner, instance_id)
            if (not current or current["instance"]["learnerId"] != learner_id
                    or current["historicalReview"]["id"] != review["id"]
                    or not is_personal_support_plan(current["historicalReview"].get("type"))):
                return fail("The imported review association changed. Reload it.")
            record, state = booking_state(owner, learner, current["historicalReview"], overlay, lock=True)
            slot = (record.scheduled_date, record.scheduled_time, record.duration_minutes) if record else None
            # A successful same-slot retry is safe even when POST is no longer
            # offered: it must return the existing meeting, never create another.
            if not state[permission]:
                if not rescheduling and state["canReschedule"] and state["booked"] and slot == desired:
                    return v.JsonResponse(current)
                return fail("Only an unstarted Personal Support Plan can be booked or rescheduled.")
            if record and ((rescheduling and slot != previous and slot != desired)
                           or (not rescheduling and slot != desired)):
                return fail("This booking was changed by another request. Reload before scheduling.")
            if record and slot == desired and state["booked"]:
                return v.JsonResponse(current)
            base_event, owner_name = v.find_generated_timetable_event(owner, instance_id)
            if (not base_event or base_event.get("reviewSource") != "aptem"
                    or int(base_event.get("learnerId") or 0) != learner_id
                    or base_event.get("source") != "progress-review"
                    or not is_personal_support_plan(base_event.get("importedReviewType"))):
                return fail("The imported calendar event could not be verified.")
            if not record:
                target_date = v.parse_schedule_date(current["instance"]["targetDate"])
                if not target_date:
                    return fail("This review has no valid target date.")
                record = CoachCalendarEvent.objects.create(
                    event_key=instance_id, idempotency_key=instance_id,
                    owner_email=owner, owner_name=owner_name,
                    learner_id=learner_id, learner_name=v.clean_text(base_event.get("learner")),
                    learner_email=v.clean_text(base_event.get("email")), event_type="progress-review",
                    sequence=int(base_event.get("sequence") or 1), target_date=target_date,
                    scheduled_date=scheduled_date, scheduled_time=scheduled_time,
                    duration_minutes=duration, status=CoachCalendarEvent.STATUS_SCHEDULED,
                )
            record.scheduled_date, record.scheduled_time, record.duration_minutes = desired
            record = v.persist_calendar_sync_reservation(record, review_event=base_event)
    except IntegrityError:
        return fail("A concurrent booking won. Reload this review.")
    except (v.LearnerCalendarConflict, ReviewIdentityConflict, v.CalendarSyncInProgress) as exc:
        return fail(str(exc))
    except Exception as exc:
        from .booking_availability import CoachAvailabilityUnavailable
        if isinstance(exc, CoachAvailabilityUnavailable):
            return fail(str(exc), code="AVAILABILITY_UNAVAILABLE", status=503)
        raise

    try:
        record, warning, _attempted = v.synchronize_reserved_calendar_event(record.pk, base_event)
    except Exception:
        v.logger.exception("personal_support_plan_graph_sync_failed")
        return fail("Teams could not confirm the booking. Reload and retry with the same details.",
                    code="GRAPH_UPDATE_FAILED" if rescheduling else "GRAPH_CREATE_FAILED", status=502)
    if warning or record.sync_state != CoachCalendarEvent.SYNC_SYNCED or not record.graph_event_id or not v.calendar_record_has_launch_url(record):
        return fail("Teams could not confirm the booking. Reload and retry with the same details.",
                    code="GRAPH_UPDATE_FAILED" if rescheduling else "GRAPH_CREATE_FAILED", status=502)
    return v.JsonResponse(v._imported_review_definition(owner, instance_id))
