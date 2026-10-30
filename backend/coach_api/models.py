from django.conf import settings
from django.db import models
from django.db.models.functions import Lower
import uuid


COACH_TEST_MODE = getattr(settings, "COACH_TEST_MODE", False)


def _table_name(test_name, production_name):
    return test_name if COACH_TEST_MODE else production_name


class CoachCalendarEvent(models.Model):
    SYNC_PENDING = "pending"
    SYNC_SYNCING = "syncing"
    SYNC_SYNCED = "synced"
    SYNC_FAILED = "failed"
    SYNC_RECONCILIATION = "reconciliation"
    SYNC_CANCELLED = "cancelled"
    SYNC_STATE_CHOICES = [
        (SYNC_PENDING, "Pending"),
        (SYNC_SYNCING, "Syncing"),
        (SYNC_SYNCED, "Synced"),
        (SYNC_FAILED, "Failed"),
        (SYNC_RECONCILIATION, "Reconciliation Required"),
        (SYNC_CANCELLED, "Cancelled"),
    ]

    STATUS_NOT_SCHEDULED = "not-scheduled"
    STATUS_SCHEDULED = "scheduled"
    STATUS_IN_PROGRESS = "in-progress"
    STATUS_AWAITING_SIGNATURE = "awaiting-signature"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_NOT_SCHEDULED, "Not Scheduled"),
        (STATUS_SCHEDULED, "Scheduled"),
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_AWAITING_SIGNATURE, "Awaiting Signature"),
        (STATUS_COMPLETED, "Completed"),
        # Legacy value retained for reading old deployments. Application
        # actions normalise it to Not Scheduled and never create new rows.
        (STATUS_CANCELLED, "Cancelled"),
    ]

    event_key = models.CharField(max_length=255, unique=True)
    operation_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    idempotency_key = models.CharField(max_length=255, blank=True)
    owner_email = models.EmailField(max_length=255, db_index=True)
    owner_name = models.CharField(max_length=255, blank=True)
    learner_id = models.IntegerField(db_index=True)
    learner_name = models.CharField(max_length=255, blank=True)
    learner_email = models.EmailField(max_length=255, blank=True)
    event_type = models.CharField(max_length=32, db_index=True)
    sequence = models.PositiveIntegerField(default=1)
    target_date = models.DateField(db_index=True)
    scheduled_date = models.DateField(null=True, blank=True)
    scheduled_time = models.TimeField(null=True, blank=True)
    duration_minutes = models.PositiveIntegerField(default=60)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_NOT_SCHEDULED, db_index=True)
    meeting_provider = models.CharField(max_length=64, blank=True)
    meeting_link = models.URLField(max_length=1000, blank=True)
    graph_event_id = models.CharField(max_length=255, blank=True)
    graph_web_link = models.URLField(max_length=1000, blank=True)
    # Mailbox the Graph event was created on, i.e. its organizer. Learner-booked
    # sessions organize from the learner's mailbox so the owner gets emailed, so
    # this is not always owner_email -- reads/deletes must target the right one.
    graph_organizer_email = models.EmailField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    review_responses = models.JSONField(default=dict, blank=True)
    review_completed_at = models.DateTimeField(null=True, blank=True)
    # Links this row back to the Curriculum Review that produced it. Set once
    # the row is generated from a Curriculum review_templates occurrence
    # (event_type "mcr"/"progress-review"); left blank for booked session
    # types (catch-up, student-support, ...), which have no Curriculum Review
    # behind them. See curriculum_api.review_instances for the engine that
    # resolves recurrence/eligibility/sections/fields from these ids.
    review_template_id = models.CharField(max_length=128, blank=True)
    review_instance_id = models.CharField(max_length=128, blank=True)
    occurrence_number = models.PositiveIntegerField(null=True, blank=True)
    manager_signed_at = models.DateTimeField(null=True, blank=True)
    manager_signed_by = models.CharField(max_length=255, blank=True)
    last_graph_sync_error = models.TextField(blank=True)
    sync_state = models.CharField(
        max_length=24,
        choices=SYNC_STATE_CHOICES,
        default=SYNC_PENDING,
        db_index=True,
    )
    sync_attempt_count = models.PositiveIntegerField(default=0)
    last_sync_attempt_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = _table_name('coach_test_calendar_events', 'Coach"."coach_calendar_event')
        ordering = ["target_date", "learner_name", "event_type", "sequence"]
        indexes = [
            models.Index(fields=["owner_email", "target_date", "status"], name="coach_owner_date_status_idx"),
            models.Index(fields=["learner_id", "event_type", "target_date"], name="coach_learner_type_date_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                # Literal values rather than the STATUS_* class attributes above:
                # a nested `class Meta` does not see its enclosing class's
                # namespace (Python class bodies aren't closures the way
                # function bodies are), so referencing them by name here raises
                # NameError at import time. Kept in sync with STATUS_CHOICES.
                condition=models.Q(
                    status__in=[
                        "not-scheduled",
                        "scheduled",
                        "in-progress",
                        "awaiting-signature",
                        "completed",
                        "cancelled",
                    ]
                ),
                name="coach_calendar_event_status_valid",
            ),
            models.UniqueConstraint(
                fields=["learner_id", "event_type", "sequence"],
                condition=models.Q(
                    event_type__in=[
                        "catch-up",
                        "student-support",
                        "eligibility-review",
                        "workspace",
                        "training-plan",
                        "uln-privacy",
                    ]
                ),
                name="coach_calendar_booking_seq_uniq",
            ),
            models.UniqueConstraint(
                fields=["owner_email", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="coach_calendar_owner_idempotency_uniq",
            ),
        ]

    def __str__(self):
        return f"{self.event_type} #{self.sequence} for {self.learner_name or self.learner_id}"


class MigratedReviewTemplate(models.Model):
    """Approved Aptem continuation form, separate from Curriculum templates."""

    FAMILY_MCM = "MCM"
    FAMILY_PR = "PR"
    FAMILY_PR_SKILLS_RADAR = "PR_SKILLS_RADAR"
    FAMILY_PSP = "PSP"
    FAMILY_CHOICES = [(FAMILY_MCM, "Monthly Coaching Meeting"), (FAMILY_PR, "Progress Review"),
                      (FAMILY_PR_SKILLS_RADAR, "Progress Review + Skills Radar"),
                      (FAMILY_PSP, "Personal Support Plan")]
    SCOPE_GLOBAL = "GLOBAL"
    SCOPE_PROGRAMME = "PROGRAMME"

    scope = models.CharField(max_length=9, choices=[("GLOBAL", "Global"), ("PROGRAMME", "Programme")], default="PROGRAMME")
    programme_key = models.CharField(max_length=255, blank=True, default="")
    review_family = models.CharField(max_length=15, choices=FAMILY_CHOICES)
    name = models.CharField(max_length=255)
    definition_json = models.JSONField(default=dict)
    source_metadata = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = _table_name('coach_test_migrated_review_templates', 'Coach"."coach_migrated_review_template')
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "programme_key", "review_family"],
                condition=models.Q(is_active=True, scope="PROGRAMME"),
                name="coach_migrated_programme_active",
            ),
            models.UniqueConstraint(
                fields=["scope", "review_family"], condition=models.Q(is_active=True, scope="GLOBAL"),
                name="coach_migrated_global_active",
            ),
            models.CheckConstraint(
                condition=models.Q(review_family__in=["MCM", "PR", "PR_SKILLS_RADAR", "PSP"]),
                name="coach_migrated_template_family_valid",
            ),
            models.CheckConstraint(
                condition=(models.Q(scope="GLOBAL", programme_key="")
                           | (models.Q(scope="PROGRAMME") & ~models.Q(programme_key=""))),
                name="coach_migrated_template_scope_valid",
            ),
        ]

    def __str__(self):
        return f"{self.programme_key or self.scope} / {self.review_family}: {self.name}"


class ImportedReviewInstance(models.Model):
    """Editable coach-owned state layered over an immutable Aptem review."""

    STATUS_IN_PROGRESS = "in-progress"
    STATUS_NOT_SCHEDULED = "not-scheduled"
    STATUS_SCHEDULED = "scheduled"
    STATUS_AWAITING_SIGNATURE = "awaiting-signature"
    STATUS_COMPLETED = "completed"
    STATUS_CHOICES = [
        (STATUS_NOT_SCHEDULED, "Not Scheduled"),
        (STATUS_SCHEDULED, "Scheduled"),
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_AWAITING_SIGNATURE, "Awaiting Signature"),
        (STATUS_COMPLETED, "Completed"),
    ]

    event_key = models.CharField(max_length=255)
    owner_email = models.EmailField(max_length=255, db_index=True)
    learner_id = models.IntegerField(db_index=True)
    source_review_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    migrated_template = models.ForeignKey(
        MigratedReviewTemplate, null=True, blank=True, on_delete=models.PROTECT,
        related_name="review_overlays",
    )
    template_snapshot = models.JSONField(default=dict, blank=True)
    signature_requirements = models.JSONField(default=dict, blank=True)
    answers = models.JSONField(default=dict, blank=True)
    # Explicit coach calculation only; existing and uncalculated reviews stay NULL.
    progress_snapshot = models.JSONField(null=True, blank=True)
    # Phase E metadata only. Full transcripts and attendance remain in the
    # existing Teams snapshot tables keyed by this overlay's calendar event.
    meeting_intelligence = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default=STATUS_IN_PROGRESS,
        db_index=True,
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = _table_name(
            'coach_test_imported_review_instances',
            'Coach"."coach_imported_review_instance',
        )
        constraints = [
            models.UniqueConstraint(
                fields=["owner_email", "event_key"],
                name="coach_imported_review_owner_event_unique",
            ),
            models.UniqueConstraint(
                Lower("owner_email"), models.F("event_key"),
                name="coach_imported_review_owner_event_ci_unique",
            ),
            models.UniqueConstraint(
                fields=["source_review_id"], condition=models.Q(source_review_id__isnull=False),
                name="coach_imported_review_source_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=[
                    "not-scheduled", "scheduled", "in-progress", "awaiting-signature", "completed",
                ]),
                name="coach_imported_review_status_valid",
            ),
        ]


class ImportedMeetingOwnership(models.Model):
    """Verified provider meeting reserved for one source-qualified APTEM Review.

    The calendar ID points to the physical Learner table in PostgreSQL. It is
    deliberately an integer here because CoachCalendarEvent maps to a view.
    Provider identity is never released for reuse after cancellation.
    """

    STATE_ACTIVE = "active"
    STATE_CANCELLED = "cancelled"

    source_review_id = models.BigIntegerField()
    learner_id = models.IntegerField()
    review_event_key = models.CharField(max_length=255)
    calendar_event_id = models.BigIntegerField()
    overlay = models.ForeignKey(ImportedReviewInstance, null=True, blank=True,
                                on_delete=models.PROTECT, related_name="meeting_ownerships")
    owner_email = models.EmailField(max_length=255)
    tenant_id = models.UUIDField()
    organizer_id = models.UUIDField()
    online_meeting_id = models.CharField(max_length=240)
    graph_event_id = models.CharField(max_length=255)
    booking_fingerprint = models.CharField(max_length=64)
    provenance = models.CharField(max_length=32, default="graph-calendar-verified")
    state = models.CharField(max_length=16, default=STATE_ACTIVE)
    verified_at = models.DateTimeField()

    class Meta:
        db_table = _table_name('coach_test_imported_meeting_ownership',
                               'Coach"."coach_imported_meeting_ownership')
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "organizer_id", "online_meeting_id"],
                                    name="aptem_owner_provider_uniq"),
            models.UniqueConstraint(fields=["calendar_event_id"], name="aptem_owner_calendar_uniq"),
            models.UniqueConstraint(fields=["source_review_id"],
                                    condition=models.Q(state="active"),
                                    name="aptem_owner_active_source_uniq"),
            models.CheckConstraint(condition=models.Q(state__in=["active", "cancelled"]),
                                   name="aptem_owner_state_valid"),
            models.CheckConstraint(condition=~models.Q(online_meeting_id=""),
                                   name="aptem_owner_meeting_nonempty"),
        ]

class ReviewSourceReconciliation(models.Model):
    """Explicit current authority; the calendar's Native fields remain provenance."""

    learner_id = models.BigIntegerField()
    # The Coach calendar model addresses a PostgreSQL VIEW. The migration's
    # physical FK therefore targets Learner.coach_calendar_event, not that view.
    calendar_event_id = models.BigIntegerField()
    native_review_instance_id = models.CharField(max_length=128)
    native_template_id = models.CharField(max_length=128)
    native_event_key = models.CharField(max_length=255)
    source_review_id = models.BigIntegerField()
    aptem_review_id = models.CharField(max_length=128)
    family = models.CharField(max_length=16, default="MCM")
    overlay = models.ForeignKey(ImportedReviewInstance, on_delete=models.PROTECT)
    status = models.CharField(max_length=16, default="APPLIED")
    approved_by = models.BigIntegerField()
    approved_at = models.DateTimeField()
    reason = models.TextField()
    reversed_by = models.BigIntegerField(null=True)
    reversed_at = models.DateTimeField(null=True)
    reversal_reason = models.TextField(blank=True)
    # Hashes only: do not duplicate private meeting URLs or learner answers.
    calendar_fingerprint = models.CharField(max_length=64)
    overlay_fingerprint = models.CharField(max_length=64)
    overlay_created = models.BooleanField(default=False)
    overlay_previous_status = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = _table_name('coach_test_review_reconciliations', 'Coach"."review_source_reconciliation')
        constraints = [
            models.UniqueConstraint(fields=[field], condition=models.Q(status="APPLIED"), name=name)
            for field, name in (
                ("calendar_event_id", "review_recon_active_calendar"),
                ("native_review_instance_id", "review_recon_active_native"),
                ("source_review_id", "review_recon_active_source"),
                ("overlay", "review_recon_active_overlay"),
            )
        ] + [
            models.CheckConstraint(condition=models.Q(status__in=["APPLIED", "REVERSED"]), name="review_recon_valid_status"),
            models.CheckConstraint(condition=models.Q(family="MCM"), name="review_recon_mcm_only"),
        ]


class MigratedReviewSignature(models.Model):
    """One immutable LMS sign-off for an imported review and participant role."""

    overlay = models.ForeignKey(ImportedReviewInstance, on_delete=models.PROTECT, related_name="migrated_signatures")
    role = models.CharField(max_length=16)
    signer_account_id = models.BigIntegerField()
    signer_name = models.CharField(max_length=255)
    signer_email = models.EmailField(max_length=255, blank=True)
    signature = models.TextField()
    signed_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = _table_name('coach_test_migrated_review_signatures', 'Coach"."coach_migrated_review_signature')
        constraints = [models.UniqueConstraint(fields=["overlay", "role"], name="coach_migrated_signature_role_unique")]


class MigratedReviewDocument(models.Model):
    """Authoritative final LMS PDF; never stored among original Aptem documents."""

    overlay = models.OneToOneField(ImportedReviewInstance, on_delete=models.PROTECT, related_name="migrated_document")
    pdf_bytes = models.BinaryField()
    sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = _table_name('coach_test_migrated_review_documents', 'Coach"."coach_migrated_review_document')


class MigratedReviewRevision(models.Model):
    """Append-only application archive of the state replaced by a coach reopen."""

    overlay = models.ForeignKey(
        ImportedReviewInstance, on_delete=models.PROTECT, related_name="archived_revisions",
    )
    number = models.PositiveIntegerField()
    snapshot = models.JSONField()
    pdf_bytes = models.BinaryField(null=True)
    pdf_sha256 = models.CharField(max_length=64, blank=True)
    reason_code = models.CharField(max_length=32)
    note = models.TextField(blank=True)
    actor_account_id = models.BigIntegerField()
    actor_email = models.EmailField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = _table_name('coach_test_migrated_review_revisions', 'Coach"."coach_migrated_review_revision')
        constraints = [
            models.UniqueConstraint(fields=["overlay", "number"], name="coach_migrated_revision_unique"),
            models.CheckConstraint(condition=models.Q(number__gte=1), name="coach_migrated_revision_positive"),
        ]


class CoachCalendarSequence(models.Model):
    """Cross-process sequence allocator for a learner/session-type scope."""

    learner_id = models.IntegerField()
    event_type = models.CharField(max_length=32)
    last_sequence = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = _table_name(
            "coach_test_calendar_sequences",
            'Coach"."coach_calendar_sequence',
        )
        constraints = [
            models.UniqueConstraint(
                fields=["learner_id", "event_type"],
                name="coach_calendar_sequence_scope_uniq",
            ),
        ]


class CoachDashboardSnapshot(models.Model):
    """Persistent read model for the Coach Dashboard summary only.

    Source tables remain authoritative.  A controlled refresh rebuilds this
    projection; HTTP reads never recompute cross-schema learner aggregates.
    """

    owner_email = models.EmailField(max_length=255, unique=True)
    payload = models.JSONField(default=dict)
    schema_version = models.PositiveSmallIntegerField(default=1)
    refreshed_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = _table_name(
            "coach_test_dashboard_snapshots",
            'Coach"."coach_dashboard_snapshot',
        )
        indexes = [
            models.Index(fields=["-refreshed_at"], name="coach_dash_snapshot_fresh_idx"),
        ]


class CoachAbsenceReport(models.Model):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_DECLINED = "declined"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_DECLINED, "Declined"),
    ]

    attendance_id = models.BigIntegerField(unique=True)
    owner_email = models.EmailField(max_length=255, db_index=True)
    owner_name = models.CharField(max_length=255, blank=True)
    learner_id = models.IntegerField(db_index=True)
    learner_name = models.CharField(max_length=255)
    learner_email = models.EmailField(max_length=255, blank=True)
    session_title = models.CharField(max_length=255)
    session_date = models.DateField(db_index=True)
    session_time = models.TimeField(null=True, blank=True)
    reason_category = models.CharField(max_length=64, blank=True)
    reason = models.TextField()
    reported_by = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    evidence_provided = models.BooleanField(default=False)
    evidence_kind = models.CharField(max_length=20, default="none")
    evidence_text = models.TextField(blank=True)
    evidence_image_url = models.URLField(max_length=1000, blank=True)
    previous_absences = models.PositiveIntegerField(default=0)
    attendance_rate = models.PositiveSmallIntegerField(null=True, blank=True)
    coach_note = models.TextField(blank=True)
    # Added by owner-run SQL in backend/sql/attendance_absence_recovery.sql.
    recovery_method = models.CharField(max_length=16, blank=True, default="")
    catchup_event_key = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = _table_name('coach_test_absence_reports', 'Coach"."coach_absence_report')
        ordering = ["-session_date", "learner_name"]
        indexes = [
            models.Index(fields=["owner_email", "status", "-session_date"], name="coach_abs_owner_status_idx"),
            models.Index(fields=["learner_id", "-session_date"], name="coach_abs_learner_date_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                # Literal values, not the STATUS_* class attributes: see the
                # matching comment on CoachCalendarEvent's constraint above.
                condition=models.Q(status__in=["pending", "approved", "declined"]),
                name="coach_absence_report_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(attendance_rate__isnull=True)
                | (models.Q(attendance_rate__gte=0) & models.Q(attendance_rate__lte=100)),
                name="coach_absence_attendance_0_100",
            ),
        ]

    def __str__(self):
        return f"{self.learner_name}: {self.session_title} ({self.status})"


class CoachManualAttendance(models.Model):
    STATUS_PRESENT = "present"
    STATUS_ABSENT = "absent"
    STATUS_CHOICES = [(STATUS_PRESENT, "Present"), (STATUS_ABSENT, "Absent")]

    owner_email = models.EmailField(max_length=255, db_index=True)
    learner_id = models.IntegerField(db_index=True)
    # Stable cross-schema links already present on Coach.coach_manual_attendance.
    # learner_id/enrolment_id remain historical snapshots used by the existing
    # attendance contract; these relations are the canonical joins for new code.
    learner_profile_link = models.ForeignKey(
        "learner_api.LearnerProfile",
        db_column="learner_profile_link_id",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="coach_manual_attendance_links",
        db_index=False,
    )
    enrolment_id = models.IntegerField(null=True, blank=True)
    enrolment_link = models.ForeignKey(
        "learner_api.EnrolmentUser",
        db_column="enrolment_link_id",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="coach_manual_attendance_enrolment_links",
        db_index=False,
    )
    learner_name = models.CharField(max_length=255)
    learner_email = models.EmailField(max_length=255, blank=True)
    session_date = models.DateField(db_index=True)
    module_name = models.CharField(max_length=255)
    session_title = models.CharField(max_length=255)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES)
    created_by = models.EmailField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = _table_name("coach_test_manual_attendance", 'Coach"."coach_manual_attendance')
        ordering = ["-session_date", "-id"]
        indexes = [
            models.Index(fields=["owner_email", "learner_id", "-session_date"], name="coach_manual_att_owner_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=["present", "absent"]),
                name="coach_manual_att_status_valid",
            ),
        ]


class CoachAttendanceSourceAdjustment(models.Model):
    """Coach correction for one learner's source attendance row.

    The scheduled occurrence and its Teams evidence remain intact for every
    other learner; this row is the authoritative per-learner correction read by
    both the learner and coach registers.
    """

    owner_email = models.EmailField(max_length=255)
    learner_id = models.IntegerField(db_index=True)
    # Existing FK column; keep learner_id as the legacy snapshot until all
    # readers migrate to the stable LearnerProfile relation.
    learner_profile_link = models.ForeignKey(
        "learner_api.LearnerProfile",
        db_column="learner_profile_link_id",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="coach_attendance_adjustment_links",
        db_index=False,
    )
    source = models.CharField(max_length=40)
    source_id = models.CharField(max_length=255)
    session_date = models.DateField(null=True, blank=True)
    module_name = models.CharField(max_length=255, blank=True)
    session_title = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=CoachManualAttendance.STATUS_CHOICES, blank=True)
    is_deleted = models.BooleanField(default=False)
    updated_by = models.EmailField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = _table_name("coach_test_attendance_adjustment", 'Coach"."coach_attendance_adjustment')
        constraints = [
            models.UniqueConstraint(
                fields=["learner_id", "source", "source_id"],
                name="coach_attendance_adjustment_source_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["", "present", "absent"]),
                name="coach_attendance_adjustment_status_valid",
            ),
        ]


class ReviewAttendanceLink(models.Model):
    """Audited, reversible identity correction; original Teams evidence is immutable."""

    calendar_event_id = models.BigIntegerField(db_index=True)
    learner_id = models.IntegerField()
    target_role = models.CharField(max_length=16, default='learner')
    target_key = models.CharField(max_length=64, default='learner')
    target_name = models.CharField(max_length=255, blank=True, default='')
    session_key = models.CharField(max_length=64)
    participant_key = models.CharField(max_length=64)
    evidence = models.JSONField()
    reason = models.CharField(max_length=500)
    confirmed_by = models.EmailField(max_length=255)
    confirmed_at = models.DateTimeField(auto_now_add=True)
    revoked_by = models.EmailField(max_length=255, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoke_reason = models.CharField(max_length=500, blank=True)

    class Meta:
        db_table = _table_name('coach_test_review_attendance_link', 'Coach"."review_attendance_link')
        constraints = [
            models.UniqueConstraint(fields=['calendar_event_id', 'session_key', 'target_key'],
                condition=models.Q(revoked_at__isnull=True), name='review_att_link_active_target'),
            models.UniqueConstraint(fields=['calendar_event_id', 'session_key', 'participant_key'],
                condition=models.Q(revoked_at__isnull=True), name='review_att_link_active_person'),
        ]


class CoachCalendarColorPreference(models.Model):
    """Per-coach timetable colours: one row per category default or per
    single-event override. Never read by the timetable event builders in
    views.py -- the frontend merges these into eventConfig() output itself,
    so booking/Teams code paths are untouched by this feature."""

    SCOPE_CATEGORY = "category"
    SCOPE_EVENT = "event"
    SCOPE_CHOICES = [(SCOPE_CATEGORY, "Category"), (SCOPE_EVENT, "Event")]

    owner_email = models.EmailField(max_length=255, db_index=True)
    scope = models.CharField(max_length=16, choices=SCOPE_CHOICES)
    scope_key = models.CharField(max_length=255)
    color = models.CharField(max_length=7)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = _table_name(
            "coach_test_calendar_color_preferences",
            'Coach"."coach_calendar_color_preference',
        )
        indexes = [
            models.Index(fields=["owner_email", "scope"], name="coach_color_owner_scope_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["owner_email", "scope", "scope_key"],
                name="coach_color_pref_owner_scope_key_uniq",
            ),
            models.CheckConstraint(
                # Literal values, not the SCOPE_* class attributes: see the
                # matching comment on CoachCalendarEvent's constraint above.
                condition=models.Q(scope__in=["category", "event"]),
                name="coach_color_pref_scope_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(color__regex=r"^#[0-9A-Fa-f]{6}$"),
                name="coach_color_pref_color_format_valid",
            ),
        ]

    def __str__(self):
        return f"{self.scope}:{self.scope_key} for {self.owner_email}"


class CatchupReminder(models.Model):
    """One reminder email sent before a catch-up, for one start time.

    The row is written before the email is sent, so two servers never send the
    same reminder. A rescheduled catch-up has a new start time and is reminded again.
    """

    KIND_DAY = "24h"
    KIND_HOUR = "1h"
    KIND_CHOICES = [(KIND_DAY, "24 hours before"), (KIND_HOUR, "1 hour before")]

    event_key = models.CharField(max_length=255)
    kind = models.CharField(max_length=8, choices=KIND_CHOICES)
    starts_at = models.DateTimeField()
    recipient = models.EmailField(max_length=255)
    sent = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = _table_name("coach_test_catchup_reminder", 'Coach"."catchup_reminder')
        constraints = [
            models.UniqueConstraint(fields=["event_key", "kind", "starts_at"], name="catchup_reminder_once"),
        ]


class CoachingQualitySuggestion(models.Model):
    """Separate, review-only AI cache. Never changes reviews or sanctions."""

    event_key = models.CharField(max_length=255)
    fingerprint = models.CharField(max_length=64)
    state = models.CharField(max_length=16, default='running')
    claim = models.UUIDField(default=uuid.uuid4)
    lease_until = models.DateTimeField()
    result = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = _table_name('coach_test_quality_suggestion', 'Coach"."coaching_quality_suggestion')
        constraints = [models.UniqueConstraint(fields=['event_key', 'fingerprint'], name='coach_quality_input_unique')]


class CoachingRecordingJob(models.Model):
    """Durable, leased calendar/discovery/archive work and last successful snapshot."""

    key = models.CharField(max_length=96, primary_key=True)
    kind = models.CharField(max_length=16)
    owner_email = models.EmailField(max_length=320)
    context = models.JSONField(default=dict)
    result = models.JSONField(default=dict)
    state = models.CharField(max_length=16, default='queued')
    claim = models.UUIDField(null=True)
    lease_until = models.DateTimeField(null=True)
    next_run_at = models.DateTimeField(null=True)
    last_success_at = models.DateTimeField(null=True)
    error_code = models.CharField(max_length=64, default='')
    retryable = models.BooleanField(default=True)
    attempts = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = _table_name('coach_test_recording_job', 'Coach"."coaching_recording_job')
        indexes = [models.Index(fields=['kind', 'next_run_at'], name='coach_recording_job_due')]


class CoachingCalendarIndex(models.Model):
    """Complete, versioned calendar projection; source jobs remain authoritative."""

    key = models.CharField(max_length=96, primary_key=True)
    owner_email = models.EmailField(max_length=320)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    source_success_at = models.DateTimeField()
    event_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = _table_name('coach_test_calendar_index', 'Coach"."coaching_calendar_index')
        indexes = [models.Index(fields=['owner_email', 'starts_at', 'ends_at'], name='coach_cal_index_owner_window')]


class CoachingCalendarMeeting(models.Model):
    """Thin calendar metadata only; never video bytes, tokens or review evidence."""

    key = models.CharField(max_length=64, primary_key=True)
    snapshot = models.ForeignKey(CoachingCalendarIndex, on_delete=models.CASCADE, related_name='meetings')
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    event = models.JSONField(default=dict)

    class Meta:
        db_table = _table_name('coach_test_calendar_meeting', 'Coach"."coaching_calendar_meeting')
        indexes = [models.Index(fields=['snapshot', 'starts_at', 'ends_at'], name='coach_cal_meeting_window')]


class CoachingCompletionSession(models.Model):
    """Read-only Aptem projection; never marks a source review complete."""
    key = models.CharField(max_length=64, primary_key=True)
    component_id = models.BigIntegerField(db_index=True)
    source_meeting_id = models.CharField(max_length=64)
    owner_email = models.EmailField(max_length=320, db_index=True)
    learner_name = models.CharField(max_length=255)
    learner_email = models.EmailField(max_length=320)
    family = models.CharField(max_length=3)
    event_id = models.CharField(max_length=1024)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    state = models.CharField(max_length=32)
    source_status = models.CharField(max_length=64)
    source_updated_at = models.DateTimeField()
    checked_at = models.DateTimeField()
    class Meta:
        db_table = _table_name('coach_test_completion_session', 'Coach"."coaching_completion_session')
        indexes = [models.Index(fields=['state', 'ends_at'], name='coach_completion_due'),
            models.Index(fields=['owner_email', 'starts_at'], name='coach_completion_occurrence')]


class CoachingCompletionDigest(models.Model):
    owner_email = models.EmailField(max_length=320)
    day = models.DateField()
    state = models.CharField(max_length=16, default='sending')
    created_at = models.DateTimeField()
    sent_at = models.DateTimeField(null=True)
    class Meta:
        db_table = _table_name('coach_test_completion_digest', 'Coach"."coaching_completion_digest')
        constraints = [models.UniqueConstraint(fields=['owner_email', 'day'], name='coach_completion_digest_once')]


class CoachingCompletionReminder(models.Model):
    session = models.ForeignKey(CoachingCompletionSession, on_delete=models.PROTECT, related_name='reminders')
    stage = models.PositiveSmallIntegerField()
    due_at = models.DateTimeField()
    read_at = models.DateTimeField(null=True)
    dismissed_at = models.DateTimeField(null=True)
    cancelled_at = models.DateTimeField(null=True)
    digest = models.ForeignKey(CoachingCompletionDigest, null=True, on_delete=models.PROTECT, related_name='reminders')
    class Meta:
        db_table = _table_name('coach_test_completion_reminder', 'Coach"."coaching_completion_reminder')
        constraints = [models.UniqueConstraint(fields=['session', 'stage'], name='coach_completion_stage_once')]
        indexes = [models.Index(fields=['cancelled_at', 'due_at'], name='coach_completion_inbox')]
