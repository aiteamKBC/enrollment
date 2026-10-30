"""Operator-selected association only. Never writes calendar, Native or Aptem evidence."""
import hashlib
import json
import os

from django.db import connection, connections, transaction, IntegrityError
from django.utils import timezone
from config.management_safety import read_only_databases

from .models import (CoachCalendarEvent, ImportedReviewInstance, MigratedReviewSignature,
                     MigratedReviewDocument, ReviewSourceReconciliation)
from . import reconciliation_links as links
from .reconciliation_schema import require_reconciliation_schema


class ReconciliationConflict(ValueError):
    def __init__(self, message, *, code="OTHER_BLOCKED"):
        super().__init__(message)
        self.code = code


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def calendar_fingerprint(record):
    return fingerprint({field.attname: getattr(record, field.attname) for field in record._meta.fields})


def overlay_fingerprint(overlay):
    return fingerprint({field.attname: getattr(overlay, field.attname) for field in overlay._meta.fields})


def require_operator(account_id):
    from login.models import LoginAccount, ROLE_ADMIN
    actor = LoginAccount.objects.filter(pk=account_id, is_active=True, role=ROLE_ADMIN).first()
    if actor is None:
        raise PermissionError("An active KBC admin account is required.")
    return actor


def require_same_database():
    # No cross-alias distributed transaction: source/profile locks and all writes
    # use default, which must address the same DB as the imported read paths.
    if connection.vendor == "postgresql":
        other = connections["enrolment"].settings_dict
        # An omitted PostgreSQL port uses libpq's PGPORT/default. Compare
        # effective ports without treating a different target as equivalent.
        def target(config):
            port = config.get("PORT") or os.environ.get("PGPORT") or "5432"
            return tuple(config.get(k) for k in ("ENGINE", "HOST", "NAME", "USER")) + (str(port),)
        if target(connection.settings_dict) != target(other):
            raise ReconciliationConflict("Default and enrolment must address the same database for reconciliation.")


def read_source(source_review_id, *, lock=False):
    # Deliberately does not run schema provisioning or an import/ensure helper.
    with connection.cursor() as cursor:
        cursor.execute('SELECT id, learner_id, aptem_review_id, review_type, status, completed_date, review_data '
                       'FROM "Learner".reviews WHERE id = %s' +
                       (' FOR UPDATE' if lock and connection.vendor == "postgresql" else ''), [source_review_id])
        row = cursor.fetchone()
        if row is None:
            raise ReconciliationConflict("The exact source review does not exist.")
        result = dict(zip([column[0] for column in cursor.description], row))
    if isinstance(result["review_data"], str):
        result["review_data"] = json.loads(result["review_data"])
    return result


def read_profile(profile_id, *, lock=False):
    from learner_api.models import LearnerProfile, EnrolmentUser
    from .review_sources import source_for_learner
    query = LearnerProfile.objects.using("default")
    if lock:
        query = query.select_for_update()
    profile = query.filter(pk=profile_id).first()
    if profile is None:
        raise ReconciliationConflict("The learner profile is unavailable.")
    source = None
    if profile.enrolment_id:
        query = EnrolmentUser.all_learners.using("default")
        if lock:
            query = query.select_for_update()
        source = query.filter(pk=profile.enrolment_id).first()
        if source is None:
            raise ReconciliationConflict("The linked enrolment identity is unavailable.")
    decision = source_for_learner(source, profile)
    if decision.kind != "aptem":
        raise ReconciliationConflict("An unambiguous positive Aptem identity is required.")
    return profile, decision


def dependency_counts(record):
    result = {}
    # Dependencies are reported, not retargeted. IDs/URLs/answers are not output.
    with connection.cursor() as cursor:
        for key, table in (("attendance", "coach_meeting_attendance"),
                           ("attendanceReports", "coach_meeting_attendance_reports"),
                           ("artifacts", "coach_meeting_artifacts"), ("summaries", "coach_meeting_summaries")):
            cursor.execute(f'SELECT count(*) FROM "Coach".{table} WHERE calendar_event_id=%s OR event_key=%s',
                           [record.pk, record.event_key])
            result[key] = cursor.fetchone()[0]
        cursor.execute('SELECT count(*) FROM "Learner".learning_reflection_submissions '
                       "WHERE full_submission #>> '{monthlyAssignment,meetingKey}' = %s", [record.event_key])
        result["assignmentReferences"] = cursor.fetchone()[0]
    return result


def _empty_native(record, instance):
    from curriculum_api import review_instances as engine
    from curriculum_api import views as curriculum
    def has_history(table):
        return bool(curriculum.fetch_all(
            f'SELECT id FROM {curriculum.table_name(table)} WHERE review_instance_id=%s LIMIT 1', [instance["id"]]))
    empty = (None, "", {}, [], "{}", "[]", "null")
    if (instance.get("status") not in {"scheduled", "not-scheduled"}
            or any(instance.get(key) not in empty for key in ("completed_at", "started_at", "progress_snapshot"))
            or record.review_responses not in empty or record.review_completed_at or record.manager_signed_at
            or record.manager_signed_by
            or engine.get_review_instance_answers(instance["id"])
            or engine.get_review_instance_signatures(instance["id"])
            or has_history(engine.REVIEW_INSTANCE_MANUAL_OVERRIDES_TABLE)
            or has_history(engine.REVIEW_INSTANCE_REOPENS_TABLE)):
        raise ReconciliationConflict("Native review contains work or lifecycle evidence; specialist review is required.", code="INVALID_NATIVE_STATE")


def _source_event(profile, source):
    from .review_sources import imported_learner_identity
    return {"eventKey": f"imported-review:{source['aptem_review_id']}", "reviewId": str(source["id"]),
            "aptemReviewId": str(source["aptem_review_id"]), "learnerId": str(profile.id), "source": "mcr",
            **imported_learner_identity(profile)}


def _empty_overlay(overlay):
    if (overlay.status not in {"not-scheduled", "scheduled"} or overlay.answers or overlay.progress_snapshot
            or overlay.completed_at or overlay.meeting_intelligence):
        raise ReconciliationConflict("Continuation already has work; specialist review is required.", code="OVERLAY_CONFLICT")
    if MigratedReviewSignature.objects.filter(overlay=overlay).exists() or MigratedReviewDocument.objects.filter(overlay=overlay).exists():
        raise ReconciliationConflict("Continuation already has signed or published evidence.", code="OVERLAY_CONFLICT")


def _inspect(calendar_event_id, source_review_id, *, lock=False, learners=None):
    from curriculum_api import review_instances as engine, reviews, review_types
    from .migrated_reviews import review_family, initial_local_status
    from .review_sources import linked_booking
    from .views import _imported_review_definition
    require_same_database()
    query = CoachCalendarEvent.objects.select_for_update() if lock else CoachCalendarEvent.objects
    record = query.filter(pk=calendar_event_id).first()
    if (not record or not record.review_instance_id or not record.review_template_id
            or record.event_type != "mcr" or not record.scheduled_date or not record.scheduled_time
            or record.status != "scheduled" or record.sync_state != "synced"
            or not record.graph_event_id or not record.meeting_link or not record.graph_organizer_email):
        raise ReconciliationConflict("A verified, unstarted Native MCM booking with preserved meeting identity is required.", code="INVALID_NATIVE_STATE")
    instance = engine.get_review_instance(record.review_instance_id, for_update=lock)
    if (not instance or instance.get("learner_id") != record.learner_id
            or instance.get("calendar_event_id") != record.pk
            or instance.get("review_template_id") != record.review_template_id
            or str(instance.get("coach_email", "")).casefold() != record.owner_email.casefold()):
        raise ReconciliationConflict("Native booking ownership/provenance is inconsistent.", code="INVALID_OWNERSHIP")
    template = reviews.get_review_template_row(record.review_template_id, include_deleted=True)
    type_row = review_types.get_review_type(template.get("review_type_id")) if template else None
    if not type_row or type_row.get("code") != "mcm":
        raise ReconciliationConflict("The Native template family must be MCM.", code="INVALID_NATIVE_STATE")
    _empty_native(record, instance)
    profile, decision = read_profile(record.learner_id, lock=lock)
    source = read_source(source_review_id, lock=lock)
    data = source.get("review_data") or {}
    source_aptem_id = str(data.get("aptem_learner_id") or (data.get("live_odata") or {}).get("LearnerId") or "").strip()
    if (source["learner_id"] != profile.id or not str(source.get("aptem_review_id") or "").strip()
            or (source_aptem_id and source_aptem_id != str(decision.aptem_id))
            or (data.get("aptem_review_id") and str(data["aptem_review_id"]) != str(source["aptem_review_id"]))
            or ((data.get("live_odata") or {}).get("Id") and str(data["live_odata"]["Id"]) != str(source["aptem_review_id"]))):
        raise ReconciliationConflict("Source review learner/Aptem identity does not match.", code="INVALID_OWNERSHIP")
    if review_family(source["review_type"]) != "MCM":
        raise ReconciliationConflict("Only the exact MCM source family is supported.")
    if source.get("completed_date") or not initial_local_status(source.get("status")):
        raise ReconciliationConflict("The Aptem source is historical or already started.", code="LIFECYCLE_CONFLICT")
    event = _source_event(profile, source)
    overlays = list(ImportedReviewInstance.objects.filter(source_review_id=source["id"]))
    overlay = overlays[0] if overlays else None
    if overlay and (overlay.learner_id != profile.id or overlay.owner_email.casefold() != record.owner_email.casefold()
                    or overlay.event_key != event["eventKey"] or not overlay.template_snapshot):
        raise ReconciliationConflict("The existing overlay has an incompatible identity.", code="OVERLAY_CONFLICT")
    if overlay:
        _empty_overlay(overlay)
    # Include every owner: another coach's booking is still a conflict.
    records = list(CoachCalendarEvent.objects.filter(event_type="mcr"))
    existing = linked_booking(event, records, overlay, reconciliations=[])
    if existing and existing.pk != record.pk:
        raise ReconciliationConflict(f"Source already has incompatible calendar booking {existing.pk}.", code="IMPORTED_CALENDAR_CONFLICT")
    if links.active_links(source_review_id=source["id"]).exclude(calendar_event_id=record.pk).exists():
        raise ReconciliationConflict("Another applied booking already claims this source review.", code="SOURCE_REUSE_CONFLICT")
    current = links.active_links(calendar_event_id=record.pk).first()
    if current and current.source_review_id != source["id"]:
        raise ReconciliationConflict("Booking is already reconciled to another source; reverse it first.", code="NATIVE_ALREADY_MAPPED")
    definition = _imported_review_definition(record.owner_email, event["eventKey"],
                                            **({"learners": learners} if learners is not None else {}))
    if (not definition or str((definition.get("historicalReview") or {}).get("id")) != str(source["id"])
            or definition.get("instance", {}).get("learnerId") != profile.id
            or (not overlay and not definition.get("canInitialize"))
            or (overlay and not definition.get("migratedForm"))):
        raise ReconciliationConflict("The canonical imported continuation cannot be initialized or reused.")
    preview = {"calendarEventId": record.pk, "learnerProfileId": profile.id,
               "nativeInstanceId": record.review_instance_id, "nativeTemplateId": record.review_template_id,
               "nativeEventKey": record.event_key, "sourceReviewId": source["id"],
               "aptemReviewId": source["aptem_review_id"], "family": "MCM",
               "overlayId": overlay.pk if overlay else None, "existingImportedBookingId": existing.pk if existing else None,
               "dependencies": dependency_counts(record), "status": "APPLIED" if current else "PREVIEW",
               "warnings": ["Exact IDs require human approval; dates and candidate confidence are not identity."],
               "changes": ["Create explicit association", "Reuse or canonically initialize imported continuation"],
               "preserve": ["calendar", "Graph/Teams", "Native origin", "attendance", "assignment history", "Aptem evidence"]}
    return record, source, overlay, definition, preview


def preview(*, calendar_event_id, source_review_id, actor_id):
    with read_only_databases():
        require_operator(actor_id)
        require_reconciliation_schema()
        try:
            return _inspect(calendar_event_id, source_review_id)[4]
        except ValueError as exc:
            record = CoachCalendarEvent.objects.filter(pk=calendar_event_id).first()
            return {"status": "CONFLICT", "calendarEventId": calendar_event_id, "sourceReviewId": source_review_id,
                    "detail": str(exc), "nativeInstanceId": record.review_instance_id if record else None,
                    "learnerProfileId": record.learner_id if record else None,
                    "dependencies": dependency_counts(record) if record else {}, "changes": []}


def apply(*, calendar_event_id, source_review_id, actor_id, reason, validate_locked=None):
    actor = require_operator(actor_id)
    if not str(reason or "").strip():
        raise ReconciliationConflict("An explicit approval reason is required.")
    try:
        with transaction.atomic():
            require_reconciliation_schema()
            from .views import lock_learner_calendar
            owner_record = CoachCalendarEvent.objects.filter(pk=calendar_event_id).first()
            if owner_record:
                lock_learner_calendar(owner_record.learner_id)
            # Repeat approvals remain a no-op even after imported work starts.
            record = CoachCalendarEvent.objects.select_for_update().filter(pk=calendar_event_id).first()
            if record:
                current = links.for_calendar(record)
                if current:
                    if current.source_review_id != source_review_id:
                        raise ReconciliationConflict("Booking already has another applied source.")
                    return {"status": "APPLIED", "id": current.pk, "idempotent": True}
            record, source, overlay, definition, _ = _inspect(calendar_event_id, source_review_id, lock=True)
            # Explicit batch policies recheck their candidate set only after
            # the canonical parent/Native/source locks, before any pair write.
            if validate_locked is not None:
                validate_locked(record, source)
            before = calendar_fingerprint(record)
            created = False
            if overlay is None:
                from .migrated_initialization import initialize_imported_review
                overlay, created = initialize_imported_review(record.owner_email, definition)
            # Initialization may race with the normal initializer and return an
            # existing row. Re-read it under the same lock as imported writers.
            overlay = ImportedReviewInstance.objects.select_for_update().get(pk=overlay.pk)
            _empty_overlay(overlay)
            previous_status = overlay.status
            if overlay.status != "scheduled":
                overlay.status = "scheduled"
                overlay.save(update_fields=["status", "updated_at"])
            link = ReviewSourceReconciliation.objects.create(
                learner_id=record.learner_id, calendar_event_id=record.pk,
                native_review_instance_id=record.review_instance_id, native_template_id=record.review_template_id,
                native_event_key=record.event_key, source_review_id=source["id"], aptem_review_id=source["aptem_review_id"],
                overlay=overlay, approved_by=actor.pk, approved_at=timezone.now(), reason=reason.strip(),
                calendar_fingerprint=before, overlay_fingerprint=overlay_fingerprint(overlay), overlay_created=created,
                overlay_previous_status=previous_status,
            )
            record.refresh_from_db()
            links.validate_record(link, record)
            if calendar_fingerprint(record) != before:
                raise ReconciliationConflict("The calendar changed during reconciliation.")
            return {"status": "APPLIED", "id": link.pk, "idempotent": False}
    except IntegrityError as exc:
        raise ReconciliationConflict("A competing reconciliation/overlay exists. Reload the exact pair.") from exc


def reverse(*, reconciliation_id, actor_id, reason):
    actor = require_operator(actor_id)
    if not str(reason or "").strip():
        raise ReconciliationConflict("A reversal reason is required.")
    with transaction.atomic():
        require_reconciliation_schema()
        prior = ReviewSourceReconciliation.objects.get(pk=reconciliation_id)
        from .views import lock_learner_calendar
        lock_learner_calendar(prior.learner_id)
        record = CoachCalendarEvent.objects.select_for_update().get(pk=prior.calendar_event_id)
        link = ReviewSourceReconciliation.objects.select_for_update().get(pk=prior.pk)
        if link.status == "REVERSED":
            return {"status": "REVERSED", "id": link.pk, "idempotent": True}
        overlay = ImportedReviewInstance.objects.select_for_update().get(pk=link.overlay_id)
        links.validate_record(link, record)
        # Once continuation content/signatures or lifecycle have changed, a
        # simple reversal could orphan work. Retain the association for review.
        if (overlay_fingerprint(overlay) != link.overlay_fingerprint
                or MigratedReviewSignature.objects.filter(overlay=overlay).exists()
                or MigratedReviewDocument.objects.filter(overlay=overlay).exists()
                or record.status not in {"scheduled", "not-scheduled"}):
            raise ReconciliationConflict("Continuation or lifecycle changed; specialist reversal is required.")
        link.status, link.reversed_by, link.reversed_at = "REVERSED", actor.pk, timezone.now()
        link.reversal_reason = reason.strip()
        link.save(update_fields=["status", "reversed_by", "reversed_at", "reversal_reason"])
        if overlay.status != link.overlay_previous_status:
            overlay.status = link.overlay_previous_status
            overlay.save(update_fields=["status", "updated_at"])
        # The overlay and all evidence remain. No historical row is deleted.
        return {"status": "REVERSED", "id": link.pk, "idempotent": False}


@read_only_databases()
def candidates(*, calendar_event_id, actor_id):
    require_operator(actor_id)
    require_reconciliation_schema()
    record = CoachCalendarEvent.objects.get(pk=calendar_event_id)
    read_profile(record.learner_id)
    with connection.cursor() as cursor:
        cursor.execute('SELECT id, aptem_review_id, review_type, status, planned_scheduled_date '
                       'FROM "Learner".reviews WHERE learner_id=%s ORDER BY id', [record.learner_id])
        rows = [dict(zip([col[0] for col in cursor.description], row)) for row in cursor.fetchall()]
    from .migrated_reviews import review_family
    return {"calendarEventId": record.pk, "learnerProfileId": record.learner_id,
            "nativeInstanceId": record.review_instance_id, "dependencies": dependency_counts(record),
            "candidates": [row for row in rows if review_family(row["review_type"]) == "MCM"],
            "warning": "Candidates are not approved. Select an exact source review ID and run preview."}
