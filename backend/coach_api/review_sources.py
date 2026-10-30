"""Review ownership and read-only Aptem continuation linkage shared by both roles.

An imported row owns the occurrence. Calendar rows and form overlays supply
local state; neither dates nor email addresses establish that relationship.
"""
from dataclasses import dataclass

from django.db import DatabaseError
from django.db.models import Q

from learner_api.student_activity_access import student_activity_available


class ReviewIdentityConflict(DatabaseError):
    pass


@dataclass(frozen=True)
class ReviewSource:
    kind: str
    aptem_id: int | None = None


def resolve_review_source(source_aptem_id=None, profile_aptem_id=None):
    """The existing coach rule: enrolment first, profile fallback, fail closed."""
    source_id = int(str(source_aptem_id).strip()) if student_activity_available(source_aptem_id) else None
    profile_id = int(str(profile_aptem_id).strip()) if student_activity_available(profile_aptem_id) else None
    if source_id and profile_id and source_id != profile_id:
        return ReviewSource("conflict")
    effective = source_id or profile_id
    return ReviewSource("aptem", effective) if effective else ReviewSource("curriculum")


def source_for_learner(source, profile):
    return resolve_review_source(getattr(source, "aptem_id", None), getattr(profile, "aptem_id", None))


class ReviewCreationBlocked(Exception):
    """A source boundary failure, before any Native Review write or Graph call."""

    def __init__(self, policy):
        super().__init__(policy["detail"])
        self.policy = policy
        self.code = policy["code"]


def native_review_creation_policy(profile):
    """Re-read the stable enrolment link; missing imports never decide identity."""
    from learner_api.models import EnrolmentUser

    try:
        if profile is None:
            raise DatabaseError("Review profile unavailable")
        source = None
        enrolment_id = getattr(profile, "enrolment_id", None)
        if enrolment_id:
            source = EnrolmentUser.all_learners.only("id", "aptem_id").filter(pk=enrolment_id).first()
            if source is None:
                raise DatabaseError("Review enrolment unavailable")
        identity = source_for_learner(source, profile)
    except DatabaseError:
        return {"reviewSource": "unavailable", "canAddReview": False,
                "code": "REVIEW_SOURCE_UNAVAILABLE",
                "detail": "The learner's Review source could not be verified. Please try again."}
    if identity.kind == "conflict":
        return {"reviewSource": "conflict", "canAddReview": False,
                "code": "REVIEW_IDENTITY_CONFLICT",
                "detail": "The learner has conflicting Aptem identities. Review creation is unavailable."}
    if identity.kind == "aptem":
        return {"reviewSource": "aptem", "canAddReview": False,
                "code": "APTEM_REVIEW_SOURCE_ONLY",
                "detail": "This learner uses Aptem Reviews. Choose an imported Review; if none are available, refresh the Aptem source data."}
    return {"reviewSource": "curriculum", "canAddReview": True}


def require_native_review_creation(learner_id):
    """Protect new instances/additions even when called without a calendar UI."""
    from learner_api.models import LearnerProfile

    try:
        profile = LearnerProfile.objects.only("id", "enrolment_id", "aptem_id").filter(pk=learner_id).first()
    except DatabaseError:
        profile = None
    policy = native_review_creation_policy(profile)
    if not policy["canAddReview"]:
        raise ReviewCreationBlocked(policy)


def review_profile_for_source(source, profile=None):
    """Resolve without the general identity helper's opportunistic DB writes.

    Stable enrolment/Aptem links precede the unique legacy email fallback.
    A conflicting or multiply-owned link is never reclassified as Native.
    """
    from learner_api.models import EnrolmentUser, LearnerProfile

    source_pk = getattr(source, "pk", None)
    if profile is None:
        matches = list(LearnerProfile.objects.filter(enrolment_id=source_pk)[:2]) if source_pk else []
        if not matches and student_activity_available(getattr(source, "aptem_id", None)):
            matches = list(LearnerProfile.objects.filter(aptem_id=int(str(source.aptem_id).strip()))[:2])
            if any(item.enrolment_id not in (None, source_pk) for item in matches):
                raise ReviewIdentityConflict("The Aptem profile belongs to another enrolment.")
        if not matches and not student_activity_available(getattr(source, "aptem_id", None)):
            email = str(getattr(source, "email", "") or "").strip()
            if email:
                matches = list(LearnerProfile.objects.filter(email__iexact=email, enrolment_id__isnull=True)[:2])
                if matches and EnrolmentUser.all_learners.filter(email__iexact=email).exclude(pk=source_pk).exists():
                    raise ReviewIdentityConflict("The legacy Review profile is ambiguous.")
        if len(matches) > 1:
            raise ReviewIdentityConflict("More than one Review profile matches this learner.")
        profile = matches[0] if matches else None
    if source_for_learner(source, profile).kind == "conflict":
        raise ReviewIdentityConflict("Conflicting Aptem learner identities.")
    return profile


def meeting_profile(record):
    """Resolve review attendance identity without mixing calendar ID namespaces.

    The owner has already authorized the calendar record. A legacy learner-book
    key qualifies its Enrolment identity; only the existing stable resolver may
    translate that identity. Stored participant names/emails remain historical.
    """
    from learner_api.models import EnrolmentUser, LearnerProfile

    owner = str(record.owner_email or '').strip().casefold()
    if not owner or not record.learner_id:
        return None
    if record.review_instance_id:
        from curriculum_api.review_instances import get_review_instance
        instance = get_review_instance(record.review_instance_id)
        if (not instance or instance.get('calendar_event_id') != record.pk
                or instance.get('learner_id') != record.learner_id
                or instance.get('review_template_id') != record.review_template_id
                or str(instance.get('coach_email') or '').strip().casefold() != owner):
            raise ReviewIdentityConflict('The Native Review meeting identity is inconsistent.')
        return LearnerProfile.objects.filter(pk=instance['learner_id']).first()

    parts = (record.idempotency_key or '').split(':')
    if parts[0] == 'learner-book' and record.event_type in {'mcr', 'progress-review'}:
        expected_family = 'mcm' if record.event_type == 'mcr' else 'progress-review'
        if (len(parts) not in {5, 6} or parts[1] != expected_family
                or parts[2] not in {'commercial', 'apprenticeship'} or not parts[3].isdigit()):
            raise ReviewIdentityConflict('The legacy meeting identity cannot be verified.')
        source = EnrolmentUser.all_learners.filter(pk=int(parts[3])).first()
        if not source or source.learner_type != parts[2]:
            raise ReviewIdentityConflict('The legacy meeting learner source is inconsistent.')
        profile = review_profile_for_source(source)
        if profile and record.learner_id not in {source.pk, profile.pk}:
            raise ReviewIdentityConflict('The legacy meeting learner link is inconsistent.')
    else:
        profile = LearnerProfile.objects.filter(pk=record.learner_id).first()
    if profile and str(profile.coach_email or '').strip().casefold() != owner:
        raise ReviewIdentityConflict('The meeting fallback profile belongs to another coach.')
    return profile


def imported_identity(review, profile_id):
    """Keep both source IDs; the Aptem event key remains the existing form key."""
    return {
        "reviewSource": "aptem", "reviewId": str(review["id"]),
        "aptemReviewId": review["aptemReviewId"], "importedReviewType": review["type"],
        "learnerId": str(profile_id),
        "sourceStatus": review["status"], "rawStatus": review.get("rawStatus", review["status"]),
        "sourcePlannedDate": review.get("plannedDate"),
        "sourceCompletedDate": review.get("completedDate"),
    }


def enrolment_for_review_profile(profile):
    """Reverse the same stable bridge for participant authorization, without writes."""
    from learner_api.models import EnrolmentUser

    if profile is None:
        return None
    if profile.enrolment_id:
        source = EnrolmentUser.all_learners.filter(pk=profile.enrolment_id).first()
    elif student_activity_available(profile.aptem_id):
        matches = list(EnrolmentUser.all_learners.filter(aptem_id=str(profile.aptem_id))[:2])
        source = matches[0] if len(matches) == 1 else None
    else:
        source = None
    if source is None or source_for_learner(source, profile).kind != "aptem":
        return None
    return source


def imported_learner_identity(profile):
    enrolment_id = getattr(profile, "enrolment_id", None)
    kind = "commercial" if getattr(profile, "learner_type", "") == "commercial" else "apprenticeship"
    if not enrolment_id:
        source = enrolment_for_review_profile(profile)
        if source:
            enrolment_id = source.pk
            kind = getattr(source, "learner_type", "") or kind
    return {"enrolmentId": str(enrolment_id) if enrolment_id else None, "learnerType": kind}


def linked_overlay(event, overlays):
    matches = [row for row in overlays
               if str(row.learner_id) == str(event.get("learnerId"))
               and ((row.source_review_id is not None and str(row.source_review_id) == event.get("reviewId"))
                    or (row.source_review_id is None and row.event_key == event["eventKey"]))]
    if len(matches) > 1:
        raise ReviewIdentityConflict("More than one continuation matches the imported Review.")
    return matches[0] if matches else None


def linked_booking(event, records, overlay=None, *, reconciliations=None):
    """Use durable keys, checking the ID space of legacy learner bookings."""
    from . import reconciliation_links
    records = list(records)
    if reconciliations is None:
        reconciliations = list(reconciliation_links.active_links(
            calendar_event_id__in=[row.pk for row in records if row.review_instance_id],
        ).select_related("overlay")) if any(row.review_instance_id for row in records) else []
    explicit = {link.calendar_event_id: link for link in reconciliations
                if reconciliation_links.event_matches(link, event)}
    matches = []
    for row in records:
        if row.pk in explicit:
            reconciliation_links.validate_record(explicit[row.pk], row)
            if overlay and overlay.pk != explicit[row.pk].overlay_id:
                raise ReviewIdentityConflict("The reconciled Review has a conflicting continuation.")
            row._review_source_reconciliation_id = explicit[row.pk].pk
            matches.append(row)
            continue
        exact_key = row.event_key == event["eventKey"] or (overlay is not None and row.event_key == overlay.event_key)
        profile_owned = str(row.learner_id) == str(event.get("learnerId"))
        if exact_key and (not profile_owned or row.event_type != event["source"]
                          or row.review_instance_id or row.review_template_id):
            raise ReviewIdentityConflict("The imported Review calendar association is inconsistent.")
        if row.event_type != event["source"]:
            continue
        parts = (row.idempotency_key or "").split(":")
        legacy = (len(parts) == 6 and parts[0] == "learner-book"
                  and parts[1] == ("mcm" if event["source"] == "mcr" else "progress-review")
                  and parts[2] == event.get("learnerType")
                  and parts[3] == str(event.get("enrolmentId"))
                  and parts[5] == event.get("reviewId"))
        legacy_owned = legacy and str(row.learner_id) in {str(event.get("learnerId")), str(event.get("enrolmentId"))}
        if (profile_owned and exact_key) or legacy_owned:
            if overlay and row.owner_email.casefold() != overlay.owner_email.casefold():
                raise ReviewIdentityConflict("The imported Review booking has a conflicting owner.")
            matches.append(row)
    # Never silently choose between two real appointments for one source row.
    if len(matches) > 1:
        raise ReviewIdentityConflict("More than one booking matches the imported Review.")
    return matches[0] if matches else None


def load_imported_links(events, *, records=None, compact=False):
    from .models import CoachCalendarEvent, ImportedReviewInstance

    if not events:
        return {}, {}
    profile_ids = {int(event["learnerId"]) for event in events}
    review_ids = {int(event["reviewId"]) for event in events if event.get("reviewId")}
    keys = {event["eventKey"] for event in events}
    overlay_query = ImportedReviewInstance.objects.filter(learner_id__in=profile_ids).filter(
        Q(source_review_id__in=review_ids) | Q(event_key__in=keys))
    if compact:
        overlay_query = overlay_query.only('id', 'learner_id', 'source_review_id', 'event_key', 'owner_email', 'status', 'completed_at')
    overlays = list(overlay_query)
    if records is None:
        enrolment_ids = {int(event["enrolmentId"]) for event in events if str(event.get("enrolmentId", "")).isdigit()}
        records = list(CoachCalendarEvent.objects.filter(
            Q(event_key__in=keys) | Q(learner_id__in=profile_ids | enrolment_ids,
                                     event_type__in={event["source"] for event in events})))
    from .reconciliation_links import active_links
    reconciliations = list(active_links(source_review_id__in=review_ids).select_related("overlay"))
    booking_map, overlay_map = {}, {}
    for event in events:
        overlay = linked_overlay(event, overlays)
        booking_map[event["eventKey"]] = linked_booking(event, records, overlay, reconciliations=reconciliations)
        overlay_map[event["eventKey"]] = overlay
    return booking_map, overlay_map


def booking_for_imported_review(profile, review, overlay=None, *, lock=False):
    """Resolve the same durable appointment from a form or its occurrence."""
    from .models import CoachCalendarEvent
    from .migrated_reviews import booking_event_type, review_family
    from .personal_support_booking import is_personal_support_plan

    event = {**imported_identity(review, profile.id),
             "eventKey": f"imported-review:{review['aptemReviewId']}",
             "source": ("progress-review" if is_personal_support_plan(review["type"])
                        else booking_event_type(review_family(review["type"]))),
             **imported_learner_identity(profile)}
    if not event["source"]:
        return None
    ids = {profile.id}
    if str(event["enrolmentId"] or "").isdigit():
        ids.add(int(event["enrolmentId"]))
    query = CoachCalendarEvent.objects
    if lock:
        query = query.select_for_update()
    keys = {event["eventKey"]}
    if overlay is not None:
        keys.add(overlay.event_key)
    records = query.filter(Q(event_key__in=keys) | Q(learner_id__in=ids, event_type=event["source"]))
    return linked_booking(event, records, overlay)


def booking_for_overlay(overlay, *, lock=False):
    """Keep legacy imported bookings available to completion/PDF/summary reads."""
    from .models import CoachCalendarEvent
    from learner_api.models import LearnerProfile

    query = CoachCalendarEvent.objects.filter(event_key=overlay.event_key)
    if lock:
        query = query.select_for_update()
    current = list(query[:2])
    from .reconciliation_links import active_links, validate_record
    link = active_links(overlay_id=overlay.pk).select_related("overlay").first()
    if link:
        query = CoachCalendarEvent.objects.select_for_update() if lock else CoachCalendarEvent.objects
        record = query.get(pk=link.calendar_event_id)
        validate_record(link, record)
        if any(row.pk != record.pk for row in current):
            raise ReviewIdentityConflict("More than one booking matches the imported Review.")
        return record
    if len(current) > 1:
        raise ReviewIdentityConflict("More than one booking matches the imported Review.")
    if current:
        record = current[0]
        if (record.owner_email.casefold() != overlay.owner_email.casefold()
                or record.learner_id != overlay.learner_id
                or record.review_instance_id or record.review_template_id
                or record.event_type not in {"mcr", "progress-review"}):
            raise ReviewIdentityConflict("The imported Review calendar association is inconsistent.")
        return record
    profile = LearnerProfile.objects.filter(pk=overlay.learner_id).first()
    if not profile or not overlay.source_review_id:
        return None
    # Family is only a filter. Internal review ID in the durable key is the link.
    matches = []
    for review_type in ("Monthly Coaching Meeting", "Progress Review"):
        review = {"id": overlay.source_review_id, "aptemReviewId": overlay.event_key.removeprefix("imported-review:"),
                  "type": review_type, "status": "not-scheduled"}
        record = booking_for_imported_review(profile, review, overlay, lock=lock)
        if record:
            matches.append(record)
    if len(matches) > 1:
        raise ReviewIdentityConflict("More than one booking matches the imported Review.")
    return matches[0] if matches else None


def enrich_imported_events(events):
    from .views import overlay_calendar_record

    bookings, overlays = load_imported_links(events)
    return [overlay_calendar_record(event, bookings.get(event["eventKey"]), overlays.get(event["eventKey"]))
            for event in events]


def standalone_review_records(records, profiles, aptem_profile_ids, *, linked_keys=()):
    """Suppress parallel Aptem reviews, respecting legacy enrolment ID space."""
    identities = {row.id: imported_learner_identity(row) for row in profiles if row.id in aptem_profile_ids}
    enrolments = {(item["learnerType"], item["enrolmentId"]) for item in identities.values() if item["enrolmentId"]}
    result = []
    for record in records:
        if record.event_key in linked_keys:
            continue
        if record.event_type in {"mcr", "progress-review", "review"}:
            parts = (record.idempotency_key or "").split(":")
            if len(parts) >= 4 and parts[0] == "learner-book":
                if (parts[2], parts[3]) in enrolments:
                    continue
            elif record.learner_id in aptem_profile_ids:
                continue
        result.append(record)
    return result


def apply_imported_state(event, record=None, overlay=None, *, compact=False):
    """Pure read adapter. Never changes source evidence or a meeting identity."""
    event = dict(event)
    from learner_api.review_history import _normalise_status
    from .booking_reservations import review_occupies_slot

    event["bookingReserved"] = review_occupies_slot(record)
    source_status = _normalise_status(event.get("sourceStatus") or event.get("status"))
    historical = source_status == "completed" or bool(event.get("sourceCompletedDate"))
    effective = "completed" if historical else source_status
    migrated = bool(not compact and not historical and overlay and overlay.source_review_id
                    and isinstance(overlay.template_snapshot, dict) and overlay.template_snapshot.get("sections"))
    if record:
        # Pending/failed sync must not claim a successful external booking.
        verified = bool(record.sync_state == "synced" and record.graph_event_id
                        and (record.meeting_link or record.graph_web_link))
        if not historical and (verified or record.status in {"completed", "awaiting-signature", "cancelled", "failed"}):
            effective = record.status
        event.update({
            "calendarEventId": str(record.pk), "calendarEventKey": record.event_key,
            "reconciledReview": bool(getattr(record, "_review_source_reconciliation_id", None) or event.get("reconciledReview")),
            "bookingStatus": record.status, "syncState": record.sync_state,
            "scheduledDate": record.scheduled_date.isoformat() if record.scheduled_date else None,
            "scheduledTime": record.scheduled_time.strftime("%H:%M") if record.scheduled_time else None,
            "durationMinutes": record.duration_minutes,
            "meetingLink": (record.meeting_link or record.graph_web_link or "") if verified else "",
            "graphWebLink": (record.graph_web_link or "") if verified else "", "meetingProvider": record.meeting_provider or "",
            "coachName": record.owner_name or "", "coachEmail": record.owner_email or "",
            "invited": verified, "reviewResponses": {} if compact else record.review_responses or {},
            # Keep editable learner notes separate from generated calendar copy.
            "bookingNotes": "" if compact else record.notes or "",
        })
        if record.scheduled_date:
            event["date"] = record.scheduled_date.isoformat()
    else:
        event["calendarEventKey"] = None
    if not historical and overlay:
        local = _normalise_status(overlay.status)
        # An unstarted form must not demote an existing, verified appointment.
        if local != "not-scheduled" or effective == "not-scheduled":
            effective = local
        event["localStatus"] = local
        if overlay.completed_at:
            event["reviewCompletedAt"] = overlay.completed_at.isoformat()
    event.update({"status": effective, "effectiveLmsStatus": effective, "migratedForm": migrated})
    if migrated:
        event["hasReviewForm"] = True
        event["formEventKey"] = overlay.event_key
    if not historical:
        # An unbooked source occurrence follows the current assignment. An
        # initialized form follows its stored owner, including blocked handovers.
        current_owner = str(event.get('ownerEmail') or '').strip().casefold()
        responsible = str(overlay.owner_email if overlay else
                          record.owner_email if record else current_owner).strip().casefold()
        if responsible and responsible == current_owner:
            event['coachName'] = event.get('ownerName') or ''
            event['coachEmail'] = responsible
        elif overlay and (not record or record.owner_email.strip().casefold() != responsible):
            # A source reviewer is not proof of the current overlay's owner.
            event['coachName'] = responsible
            event['coachEmail'] = responsible
    # A legacy booking may carry old native linkage, which is not the form for
    # this occurrence. Keep the stored row intact; expose the imported form.
    event["reviewInstanceId"] = None
    event["reviewTemplateId"] = None
    return event


def number_imported_events(events):
    """Display sequence only; equal dates never collapse distinct source rows."""
    counters = {}
    ordered = sorted(events, key=lambda item: (
        item.get("sourcePlannedDate") or item.get("sourceCompletedDate") or "9999-12-31",
        int(item["reviewId"]), item.get("aptemReviewId", "")))
    for event in ordered:
        family = (event["learnerId"], event["source"])
        counters[family] = counters.get(family, 0) + 1
        event["sequence"] = counters[family]
        event["occurrenceNumber"] = None
    return ordered
