"""Coach Dashboard read model.

This module is the ownership boundary for ``GET /coach_api/coach/dashboard``.
It deliberately consumes the coach's learner rows once and passes that same
context to every bulk loader.  Detailed caseload, timetable and marking views
must not be called from here.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

from django.conf import settings
from django.utils import timezone

from coach_api.selectors.dashboard.marking import dashboard_marking_projection
from .context import CoachDashboardContext
from .completed_reviews import apply_completed_review_dates, saved_completed_review_dates


class CoachDashboardService:
    """Build the dashboard DTO without invoking any detailed endpoint loader."""

    def __init__(self, owner_email: str, *, today: date | None = None):
        self.context = CoachDashboardContext(owner_email, today or timezone.localdate())

    # v13 uses the learner Overview metrics for the embedded caseload table.
    # v14 carries the Aptem-backed programme plan/window into the learner DTO.
    # v15 adds the verified training-plan contract as the first schedule source.
    # v16 emits the API-owned OTJH target-to-date/RAG contract.
    # v17 carries the Case File start date separately for table display.
    # v18 makes startDate itself use the Profile resolver.
    # v19 keeps imported Review continuations on their source occurrence.
    # v20 separates other imported reviews from PR dates and dashboard counts.
    # Older snapshots may legitimately contain ``--`` dates, so they must not
    # be served as if they were current after the serializer is corrected.
    # v21 uses completion timestamps for both PR/MCM, including legacy aliases.
    SCHEMA_VERSION = 21

    def build(self) -> dict:
        """Read the persistent projection; build once only if it is absent."""
        if getattr(settings, "COACH_DASHBOARD_SHARED_READ_MODEL_ENABLED", False):
            from coach_api.read_model import MODEL_KEY, SCOPE_TYPE
            from read_models.repository import get_read_model

            shared = get_read_model(
                MODEL_KEY,
                SCOPE_TYPE,
                self.context.owner_email,
                schema_version=self.SCHEMA_VERSION,
                cache_ttl=max(int(getattr(settings, "COACH_DASHBOARD_CACHE_TTL", 30)), 1),
            )
            if shared is not None:
                payload = dict(shared.payload)
                payload["readModel"] = {
                    "version": shared.schema_version,
                    "refreshedAt": shared.refreshed_at.isoformat(),
                }
                return self.normalize_start_dates(payload, refresh_otjh=True)

        # Resolve through the compatibility module so existing patch points and
        # operational tooling remain valid during the module relocation.
        from coach_api import dashboard_service as compatibility
        snapshot = compatibility.CoachDashboardSnapshot.objects.filter(
            owner_email=self.context.owner_email,
            schema_version=self.SCHEMA_VERSION,
        ).only("payload", "refreshed_at").first()
        if snapshot is not None:
            return self.normalize_start_dates(self._snapshot_payload(snapshot, version=self.SCHEMA_VERSION), refresh_otjh=True)
        previous = compatibility.CoachDashboardSnapshot.objects.filter(
            owner_email=self.context.owner_email,
        ).order_by("-refreshed_at").only(
            "payload", "refreshed_at", "schema_version",
        ).first()
        if previous is not None:
            # A schema mismatch should not make the first page load wait for a
            # full caseload rebuild.  Serve the newest durable projection and
            # let dashboard_view enqueue the schema refresh after responding.
            return self.normalize_start_dates(self._snapshot_payload(previous, version=previous.schema_version), refresh_otjh=True)
        return self.refresh()

    def normalize_start_dates(self, previous_payload: dict, *, refresh_otjh=False) -> dict:
        """Correct persisted/cached dates with read-only, coach-scoped source reads.

        The optional OTJH overlay reads current Profile facts and window. Older
        snapshots must not expose their outdated startDate while refresh queues.
        Learners who have since been withdrawn, completed or entered EPA are
        dropped the same way, so an hourly snapshot never shows them.
        """
        from coach_api import views as domain

        payload = deepcopy(previous_payload)
        from .profile_dates import fetch_dashboard_profile_dates

        if not payload.get("learners"):
            return payload
        profile_ids = [domain.to_int(item.get("id")) for item in payload.get("learners") or []]
        profile_ids = [identity for identity in profile_ids if identity > 0]
        rows = fetch_dashboard_profile_dates(self.context.owner_email, profile_ids) if profile_ids else []
        rows_by_id = {str(row.id): row for row in rows}
        snapshot_version = (payload.get("readModel") or {}).get("version")
        review_dates = {}
        if rows and snapshot_version is not None and snapshot_version < self.SCHEMA_VERSION:
            # Correct old scheduled-date metrics while the normal refresh queues.
            review_dates = saved_completed_review_dates(
                rows, owner_email=self.context.owner_email,
                owner_name=(payload.get("owner") or {}).get("name") or "Coach",
            )
        learners = []
        for learner in payload.get("learners") or []:
            row = rows_by_id.get(str(learner.get("id")))
            if row is not None and domain.is_hidden_caseload_programme_status(
                domain.get_lms_row_program_status(row),
            ):
                continue
            learner.setdefault("otjhProgrammeStartDate", learner.get("startDate", "--"))
            learner["startDate"] = domain.caseload_profile_start_date(row)
            learner["displayStartDate"] = learner["startDate"]
            learner["displayEndDate"] = domain.format_date(getattr(getattr(row, "_caseload_source", None), "learner_end_date", None))
            dates = review_dates.get(domain.to_int(learner.get("id")))
            if dates is not None:
                apply_completed_review_dates(learner, dates)
            learners.append(learner)
        if "learners" in payload:
            payload["learners"] = learners
        if refresh_otjh:
            from .otjh import refresh_otjh_rows
            refresh_otjh_rows(payload, rows, today=self.context.today)
        return payload

    @staticmethod
    def _snapshot_payload(snapshot, *, version: int) -> dict:
        payload = dict(snapshot.payload)
        payload["readModel"] = {
            "version": version,
            "refreshedAt": snapshot.refreshed_at.isoformat(),
        }
        return payload

    def refresh_metric_projection(self, previous_payload: dict) -> dict:
        """Upgrade a prior Dashboard snapshot without rebuilding unrelated domains."""
        from coach_api import dashboard_service as compatibility
        from coach_api import views as domain

        payload = deepcopy(previous_payload)
        learners = payload.get("learners") or []
        rows = domain.fetch_caseload_learner_profiles(self.context.owner_email)
        rows_by_id = {int(row.id): row for row in rows}
        review_dates = saved_completed_review_dates(
            rows, owner_email=self.context.owner_email,
            owner_name=(payload.get("owner") or {}).get("name") or "Coach",
        ) if any("lastPr" in learner or "lastMcm" in learner or "lastProgressReview" in learner or "lastReview" in learner for learner in learners) else {}
        canonical_metrics = domain.caseload_canonical_metrics(rows, learner_workspace=True)
        aptem_by_profile = domain.caseload_aptem_ids(rows)
        attendance_rows = domain.dashboard_attendance_rows(
            rows, learners, aptem_by_profile=aptem_by_profile,
        )
        attendance_by_id = {
            domain.to_int(item.get("id")): item for item in attendance_rows
            if domain.to_int(item.get("id")) is not None
        }
        for learner in learners:
            profile_id = domain.to_int(learner.get("id"))
            if profile_id is None or profile_id not in rows_by_id:
                continue
            if profile_id in review_dates:
                apply_completed_review_dates(learner, review_dates[profile_id])
            schedule_planned, schedule_start, schedule_end = domain.caseload_schedule_values(
                rows_by_id[profile_id],
            )
            learner["startDate"] = domain.caseload_profile_start_date(rows_by_id[profile_id])
            learner["displayStartDate"] = learner["startDate"]
            learner["displayEndDate"] = domain.format_date(getattr(getattr(rows_by_id[profile_id], "_caseload_source", None), "learner_end_date", None))
            learner["otjhProgrammeStartDate"] = domain.format_date(schedule_start)
            if schedule_planned not in (None, ""):
                learner["otjhPlanned"] = domain.to_number(schedule_planned)
            if schedule_end not in (None, ""):
                learner["plannedEndDate"] = domain.format_date(schedule_end)
            domain.apply_canonical_learner_metrics(learner, canonical_metrics.get(profile_id), learner_workspace=True)
            domain.apply_aptem_variance_status(learner, aptem_by_profile.get(profile_id))
            domain.apply_attendance_summary(learner, attendance_by_id.get(profile_id))
            domain.apply_otjh_to_date_metrics(learner, today=self.context.today)
            learner["attendanceAvailable"] = bool(learner.get("attendanceRateAvailable"))
        from .otjh import refresh_otjh_rows
        refresh_otjh_rows(payload, rows, today=self.context.today)
        compatibility.CoachDashboardSnapshot.objects.update_or_create(
            owner_email=self.context.owner_email,
            defaults={"payload": payload, "schema_version": self.SCHEMA_VERSION},
        )
        return payload

    def refresh(self) -> dict:
        from coach_api import dashboard_service as compatibility
        payload = self.build_live()
        snapshot, _created = compatibility.CoachDashboardSnapshot.objects.update_or_create(
            owner_email=self.context.owner_email,
            defaults={"payload": payload, "schema_version": self.SCHEMA_VERSION},
        )
        shared = None
        if getattr(settings, "READ_MODEL_DUAL_WRITE_ENABLED", False):
            from coach_api.read_model import MODEL_KEY, SCOPE_TYPE
            from read_models.repository import put_read_model
            shared = put_read_model(
                MODEL_KEY,
                SCOPE_TYPE,
                self.context.owner_email,
                payload,
                schema_version=self.SCHEMA_VERSION,
                ttl_seconds=max(int(getattr(settings, "COACH_DASHBOARD_SNAPSHOT_MAX_AGE", 30)), 1),
            )
        response_payload = dict(payload)
        response_payload["readModel"] = {
            "version": self.SCHEMA_VERSION,
            "refreshedAt": (shared.refreshed_at if shared else snapshot.refreshed_at).isoformat(),
        }
        return response_payload

    def build_live(self) -> dict:
        # Imported lazily to avoid moving the established domain functions in
        # the same change.  The dependency direction remains one-way: the view
        # calls this service; this service never calls a view/HTTP endpoint.
        from coach_api import views as domain

        context = self.context
        context.rows = domain.fetch_caseload_learner_profiles(context.owner_email)
        context.learners = [
            domain.serialize_caseload_learner(row, refresh_live_snapshots=False)
            for row in context.rows
        ]
        canonical_metrics = domain.caseload_canonical_metrics(context.rows, learner_workspace=True)
        ksb_fallback_rows = [
            row for row in context.rows
            if (
                (canonical_metrics.get(int(row.id)) or {}).get("ksb") or {}
            ).get("status") != "ready"
        ]
        progress_projections = domain.caseload_dashboard_progress_projections(
            context.rows,
            ksb_rows=ksb_fallback_rows,
        )
        audit_totals = domain.caseload_audit_hour_totals(context.rows)
        ksb_counts = domain.caseload_evidenced_ksb_counts(context.rows)
        aptem_by_profile = domain.caseload_aptem_ids(context.rows)

        # Dashboard monthly risk uses the already-prefetched learner plan.  It
        # must never hydrate curriculum once per learner.
        plans = {int(row.id): getattr(row, "training_plan", None) or [] for row in context.rows}
        component_ids = [
            component_id
            for plan in plans.values()
            for week in domain.curriculum_monthly_target_hours_weeks(plan)
            for component_id in week
        ]
        expected = domain.curriculum_expected_otjh_by_component_id(component_ids)
        progress_by_learner = domain.caseload_progress_history(context.rows)
        monthly_risk = domain.build_monthly_risk_history(
            context.rows,
            progress_by_learner,
            expected,
            today=context.today,
            hydrated_plans_by_learner=plans,
        )

        owner_name = domain.coach_staff_display_name(context.owner_email) or next(
            (getattr(row, "coach_name", "") for row in context.rows if getattr(row, "coach_name", "")),
            "Coach",
        )
        # The caseload loader already attached these full source rows. A live
        # snapshot build must not fetch the same 84-column source query again
        # merely to generate its review events.
        source_schedule_rows = None
        if all(hasattr(row, "_caseload_source") for row in context.rows):
            commercial_rows, enrolment_rows = {}, {}
            for row in context.rows:
                source = row._caseload_source
                if source is not None:
                    target = commercial_rows if domain.clean_text(getattr(source, "learner_type", "")).casefold() == "commercial" else enrolment_rows
                    target[int(row.id)] = source
            source_schedule_rows = commercial_rows, enrolment_rows
        resolved = domain.resolve_coach_review_events(
            context.owner_email,
            owner_name,
            context.rows,
            source_schedule_rows=source_schedule_rows,
        )
        all_review_events = resolved.get("events", [])
        generated_keys = [event.get("eventKey") for event in all_review_events if event.get("eventKey")]
        stored_by_key = domain.fetch_calendar_event_records(context.owner_email, generated_keys) if generated_keys else {}
        all_review_events = [
            domain.overlay_calendar_record(event, stored_by_key.get(event.get("eventKey")))
            for event in all_review_events
        ]
        from coach_api.review_sources import enrich_imported_events, standalone_review_records
        imported = enrich_imported_events([event for event in resolved.get("events", []) if event.get("reviewSource") == "aptem"])
        imported_by_key = {event["eventKey"]: event for event in imported}
        all_review_events = [imported_by_key.get(event["eventKey"], event) for event in all_review_events]
        linked_keys = set(stored_by_key) | {event.get("calendarEventKey") for event in imported}
        standalone_records = standalone_review_records(
            domain.fetch_standalone_event_records(context.owner_email), context.rows,
            resolved.get("aptemProfileIds", set()), linked_keys=linked_keys)
        standalone_type_fields = domain.review_type_fields_by_template(
            getattr(record, "review_template_id", "") for record in standalone_records
        )
        learners_by_id = {int(row.id): row for row in context.rows}
        standalone_events = [
            domain.build_catchup_calendar_event(
                record,
                owner_name=owner_name,
                learner=learners_by_id.get(record.learner_id),
                review_type_fields=standalone_type_fields,
            )
            for record in standalone_records
        ]
        preview_events = [
            event for event in all_review_events
            if (parsed := domain.parse_schedule_date(event.get("date")))
            and context.today - timedelta(days=31) <= parsed <= context.today + timedelta(days=90)
        ]
        preview_events.extend(
            event for event in standalone_events
            if (parsed := domain.parse_schedule_date(event.get("date")))
            and context.today - timedelta(days=31) <= parsed <= context.today + timedelta(days=90)
        )
        # Live sessions are already tracked rows.  This is a bounded projection,
        # not timetable recurrence generation.
        preview_events.extend(domain.collect_tracked_live_session_events(
            context.owner_email,
            owner_name,
            start_date=context.today - timedelta(days=31),
            end_date=context.today + timedelta(days=90),
        ))
        preview_events.sort(key=lambda event: (event.get("date") or "", event.get("startHour") or 0))

        last_dates = domain.latest_completed_review_dates_from_events(context.rows, all_review_events)

        attendance_rows = domain.dashboard_attendance_rows(
            context.rows, context.learners, aptem_by_profile=aptem_by_profile,
        )
        attendance_by_id = {
            domain.to_int(item.get("id")): item for item in attendance_rows
            if domain.to_int(item.get("id")) is not None
        }
        for row, learner in zip(context.rows, context.learners):
            learner.update(progress_projections.get(int(row.id), {}))
            apply_completed_review_dates(learner, last_dates.get(int(row.id), {}))
            domain.apply_audit_hour_totals(learner, audit_totals.get(int(row.id)))
            domain.apply_evidenced_ksb_count(learner, ksb_counts.get(int(row.id)))
            domain.apply_canonical_learner_metrics(learner, canonical_metrics.get(int(row.id)), learner_workspace=True)
            domain.apply_aptem_variance_status(learner, aptem_by_profile.get(int(row.id)))
            domain.apply_attendance_summary(learner, attendance_by_id.get(int(row.id)))
            domain.apply_otjh_to_date_metrics(learner, today=context.today)
            learner["attendanceAvailable"] = bool(learner.get("attendanceRateAvailable"))

        marking = dashboard_marking_projection(context)
        from .otjh import refresh_otjh_rows
        refresh_otjh_rows({"learners": context.learners}, context.rows, today=context.today)
        return {
            "owner": {"name": owner_name, "email": context.owner_email},
            "learners": context.learners,
            "monthlyRisk": monthly_risk,
            "assignedGroups": domain.fetch_official_assigned_groups(context.owner_email, owner_name),
            "meetings": {
                "events": preview_events,
                "summary": resolved.get("sourceCounts", {}),
                "reviewGenerationIssues": resolved.get("reviewGenerationIssues", []),
            },
            "marking": marking,
            "errors": {},
        }
