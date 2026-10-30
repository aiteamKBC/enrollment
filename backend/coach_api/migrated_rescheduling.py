"""Explicit rescheduling of an existing imported booking; never creates a meeting."""
from django.db import transaction
from django.http import JsonResponse

from . import imported_meeting_ownership as meeting_ownership
from .models import CoachCalendarEvent, ImportedReviewInstance


def can_reschedule(overlay, calendar):
    return bool(
        overlay and overlay.status == ImportedReviewInstance.STATUS_SCHEDULED
        and calendar and calendar.status == CoachCalendarEvent.STATUS_SCHEDULED
        and calendar.graph_event_id and (calendar.meeting_link or calendar.graph_web_link)
        and calendar.scheduled_date and calendar.scheduled_time
        and calendar.sync_state in {CoachCalendarEvent.SYNC_SYNCED, CoachCalendarEvent.SYNC_FAILED}
    )


def reschedule(request, instance_id):
    from . import views as v
    from .review_sources import booking_for_overlay, ReviewIdentityConflict
    from .review_reconciliation import read_source, ReconciliationConflict
    from .reconciliation_links import require_schedulable

    def conflict(message):
        return v.coach_error(request, code="BOOKING_CONFLICT", message=message, status=409)

    owner = v.authenticated_coach_email(request).strip().casefold()
    definition = v._imported_review_definition(owner, instance_id)
    if not definition:
        return v.coach_error(request, code="UNAUTHORIZED", message="Imported review not found for this coach.", status=404)
    try:
        payload = v.parse_json_body(request)
        validator = v.ObjectValidator(payload)
        scheduled_date = validator.iso_date("scheduledDate", required=True)
        scheduled_time = validator.clock_time("scheduledTime", required=True)
        duration = validator.integer("durationMinutes", required=True, minimum=15, maximum=480)
        offset = validator.integer("timezoneOffsetMinutes", default=0, minimum=-840, maximum=840)
        if scheduled_date and scheduled_date < v.date.today():
            validator.error("scheduledDate", "Choose today or a future date.")
        if not isinstance(payload.get("expectedBooking"), dict):
            validator.error("expectedBooking", "Reload the existing booking before rescheduling.")
        from .booking_hours import validate_review_time_precision
        try:
            validate_review_time_precision(payload.get("scheduledTime"))
        except ValueError as exc:
            validator.error("scheduledTime", str(exc))
        validator.check()
        expected = v.ObjectValidator(payload["expectedBooking"])
        previous_slot = (
            expected.iso_date("scheduledDate", required=True),
            expected.clock_time("scheduledTime", required=True),
            expected.integer("durationMinutes", required=True, minimum=15, maximum=480),
        )
        expected.check()
    except v.ValidationError as exc:
        return v.validation_error_response(exc)

    try:
        # Personal calendars may involve network I/O. Check outside DB locks,
        # excluding this appointment, then revalidate identity/slot under locks.
        preview_overlay = v._owned_migrated_overlay(owner, definition)
        preview_record = booking_for_overlay(preview_overlay) if preview_overlay else None
        if not can_reschedule(preview_overlay, preview_record) or definition.get("readOnly"):
            return conflict("Only an existing Scheduled review booking can be rescheduled.")
        learner = next((row for row in v.fetch_caseload_dashboard_profiles(owner)
                        if int(getattr(row, "id", 0) or 0) == preview_overlay.learner_id), None)
        if learner is None:
            return conflict("The learner is no longer assigned to this coach.")
        desired_slot = (scheduled_date, scheduled_time, duration)
        current_slot = (preview_record.scheduled_date, preview_record.scheduled_time,
                        preview_record.duration_minutes)
        same_persisted_slot = current_slot == desired_slot
        if not same_persisted_slot:
            from .booking_hours import validate_review_interval
            try:
                validate_review_interval(scheduled_date, scheduled_time, duration)
            except ValueError as exc:
                return conflict(str(exc))
            if v.coach_learner_personal_calendar_conflicts(
                    learner, scheduled_date, scheduled_time, duration, offset,
                    exclude_record=preview_record, business_time_zone="Europe/London"):
                return conflict("This learner is busy at that time.")
            from .booking_availability import ensure_coach_review_slot_available
            ensure_coach_review_slot_available(owner, scheduled_date, scheduled_time, duration,
                                               record=preview_record)
        with transaction.atomic():
            v.lock_learner_calendar(definition["instance"]["learnerId"])
            # Ownership, source lifecycle and local state may have changed since GET.
            definition = v._imported_review_definition(owner, instance_id)
            if not definition or not definition.get("migratedForm") or definition.get("readOnly"):
                return conflict("This imported review is no longer editable.")
            overlay = v._owned_migrated_overlay(owner, definition, lock=True, lock_calendar=True)
            if not overlay:
                return conflict("The imported review association changed. Reload it.")
            record = booking_for_overlay(overlay, lock=True)
            family = v.migrated_review_family((definition.get("historicalReview") or {}).get("type"))
            if (not can_reschedule(overlay, record)
                    or record.owner_email.casefold() != owner
                    or record.learner_id != overlay.learner_id
                    or record.event_type != v.migrated_booking_event_type(family)
                    or definition.get("booking", {}).get("conflict")
                    or not v.snapshot_assignment_valid(overlay, family, definition.get("migratedProgrammeKey"))):
                return conflict("Only an existing Scheduled review booking can be rescheduled. Reload to check its status.")
            source = read_source(overlay.source_review_id)
            if (source.get("learner_id") != overlay.learner_id
                    or f"imported-review:{source.get('aptem_review_id')}" != overlay.event_key
                    or v.migrated_review_family(source.get("review_type")) != family
                    or source.get("completed_date") or not v.initial_local_status(source.get("status"))):
                return conflict("The Aptem review is historical or its source association changed.")
            require_schedulable(record)
            desired_slot = (scheduled_date, scheduled_time, duration)
            current_slot = (record.scheduled_date, record.scheduled_time, record.duration_minutes)
            if current_slot != desired_slot and current_slot != previous_slot:
                return conflict("This booking was changed by another request. Reload before rescheduling.")
            if current_slot == desired_slot and record.sync_state == CoachCalendarEvent.SYNC_SYNCED:
                if getattr(v.settings, 'APTEM_MEETING_OWNERSHIP_ENABLED', False):
                    meeting_ownership.require_activation_ready()
                    meeting_ownership.require_synced_owner(record, {'reviewId': source['id']})
                return JsonResponse(definition)
            base_event, _ = v.find_generated_timetable_event(owner, overlay.event_key)
            if (not base_event or base_event.get("reviewSource") != "aptem"
                    or int(base_event.get("learnerId") or 0) != overlay.learner_id
                    or base_event.get("source") != record.event_type):
                return conflict("The imported calendar event could not be verified.")
            record.scheduled_date, record.scheduled_time, record.duration_minutes = desired_slot
            # Reuse normal availability/weekly rules and the existing reservation.
            # No overlay, source, answers, lifecycle or meeting identity is written.
            record = v.persist_calendar_sync_reservation(record, review_event=base_event)
    except (v.LearnerCalendarConflict, ReviewIdentityConflict, v.CalendarSyncInProgress,
            ReconciliationConflict, meeting_ownership.MeetingOwnershipError) as exc:
        return conflict(str(exc))
    except Exception as exc:
        from .booking_availability import CoachAvailabilityUnavailable
        if isinstance(exc, CoachAvailabilityUnavailable):
            return v.coach_error(request, code="AVAILABILITY_UNAVAILABLE", message=str(exc), status=503)
        raise

    try:
        record, warning, _ = v.synchronize_reserved_calendar_event(record.pk, base_event)
    except Exception:
        v.logger.exception("migrated_review_reschedule_sync_failed")
        return v.coach_error(request, code="GRAPH_UPDATE_FAILED", message="Teams could not confirm the new time. Retry with the same details.", status=502)
    if warning or record.sync_state != CoachCalendarEvent.SYNC_SYNCED:
        return v.coach_error(request, code="GRAPH_UPDATE_FAILED", message="Teams could not confirm the new time. Retry with the same details.", status=502)
    return JsonResponse(v._imported_review_definition(owner, instance_id))
