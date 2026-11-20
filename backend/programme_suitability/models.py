import uuid

from django.db import models
from django.db.models import Q


class CohortGroupPool(models.Model):
    """Enrolment-owned, owner-verified capacity baseline; never created on GET."""
    programme_id = models.CharField(max_length=100)
    cohort_id = models.CharField(max_length=100)
    verified_members = models.JSONField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    verification_evidence = models.TextField(blank=True)

    class Meta:
        db_table = 'cohort_group_pools'
        constraints = [models.UniqueConstraint(fields=('programme_id', 'cohort_id'), name='unique_group_capacity_pool')]


class CohortGroupAllocation(models.Model):
    learner_id = models.BigIntegerField(unique=True)
    learner_kind = models.CharField(max_length=20)
    pool = models.ForeignKey(CohortGroupPool, on_delete=models.PROTECT)
    group_id = models.CharField(max_length=100)
    group_name = models.CharField(max_length=200)
    seat = models.PositiveSmallIntegerField()
    pathway = models.CharField(max_length=100, blank=True)
    academic_ids = models.JSONField()
    offerings = models.JSONField()
    configuration_version = models.BigIntegerField()
    selection_revision = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'cohort_group_allocations'
        constraints = [
            models.UniqueConstraint(fields=('pool', 'group_id', 'seat'), name='unique_group_capacity_seat'),
            models.CheckConstraint(condition=Q(seat__gte=1, seat__lte=20), name='group_capacity_seat_1_to_20'),
        ]


class Assessment(models.Model):
    # Learner ids are unique across the enrolment service's Created_users table.
    learner_id = models.BigIntegerField(unique=True)
    learner_kind = models.CharField(max_length=20)
    configuration_version = models.BigIntegerField()
    configuration = models.JSONField()
    pathway = models.CharField(max_length=30)
    responses = models.JSONField(default=dict)
    result = models.JSONField(default=dict)
    selected_programme = models.CharField(max_length=20, blank=True)
    alternative_selection_reason = models.TextField(max_length=1000, null=True, blank=True)
    programme_selection_source = models.CharField(max_length=40, default="LEGACY")
    original_preference = models.CharField(max_length=20, blank=True)
    review_status = models.CharField(max_length=40, default="PRELIMINARY_RECOMMENDATION")
    review = models.JSONField(default=dict)
    history = models.JSONField(default=list)
    revision = models.PositiveIntegerField(default=1)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    selection_confirmed_at = models.DateTimeField(null=True, blank=True)
    modules_configuration = models.JSONField(null=True, blank=True)
    modules_configuration_version = models.BigIntegerField(null=True, blank=True)
    modules_selection = models.JSONField(null=True, blank=True)
    modules_revision = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "programme_suitability_assessments"


class ModuleConfigurationDraft(models.Model):
    actor_key = models.CharField(max_length=200, primary_key=True)
    base_version = models.BigIntegerField(null=True, blank=True)
    configuration = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'programme_modules_admin_drafts'


class SuitabilityRplReport(models.Model):
    """Admin-owned suitability/RPL assessment for one enrolment context.

    ``context_key`` identifies the learner's programme/enrolment combination,
    rather than the learner alone.  The JSON snapshot is deliberately frozen
    when a report is first saved so later wizard or programme publications do
    not rewrite what the assessor reviewed.
    """

    STATUS_DRAFT = 'draft'
    STATUS_FINALISED = 'finalised'
    STATUS_CHOICES = ((STATUS_DRAFT, 'Draft'), (STATUS_FINALISED, 'Finalised'))

    SUITABILITY_NOT_ASSESSED = 'not_assessed'
    SUITABILITY_SUITABLE = 'suitable'
    SUITABILITY_FURTHER_ASSESSMENT = 'further_assessment'
    SUITABILITY_CHOICES = (
        (SUITABILITY_NOT_ASSESSED, 'Not assessed'),
        (SUITABILITY_SUITABLE, 'Suitable'),
        (SUITABILITY_FURTHER_ASSESSMENT, 'Further assessment required'),
    )

    learner_kind = models.CharField(max_length=20)
    learner_id = models.BigIntegerField()
    context_key = models.CharField(max_length=64)
    programme_id = models.CharField(max_length=100, blank=True)
    programme_name = models.TextField(blank=True)
    enrolment_reference = models.CharField(max_length=200, blank=True)
    practical_start_date = models.CharField(max_length=32, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    report_reference = models.CharField(max_length=100, unique=True)
    report_version = models.PositiveIntegerField(default=1)
    revision = models.PositiveIntegerField(default=1)
    parent_report = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='amended_versions')
    amendment_reason = models.TextField(blank=True)

    relevant_prior_learning = models.BooleanField(null=True, blank=True)
    reduction_justified = models.BooleanField(null=True, blank=True)
    assessor_evidence_review = models.TextField(blank=True)
    no_reduction_reason = models.TextField(blank=True)
    suitability_outcome = models.CharField(
        max_length=32, choices=SUITABILITY_CHOICES, default=SUITABILITY_NOT_ASSESSED,
    )
    final_summary = models.TextField(blank=True)

    suitability_justification = models.TextField(blank=True)
    suitability_ai_metadata = models.JSONField(default=dict, blank=True)
    suitability_reviewed_by = models.BigIntegerField(null=True, blank=True)
    suitability_reviewed_by_name = models.CharField(max_length=200, blank=True)
    suitability_reviewed_at = models.DateTimeField(null=True, blank=True)

    rpl_justification = models.TextField(blank=True)
    rpl_ai_metadata = models.JSONField(default=dict, blank=True)
    rpl_reviewed_by = models.BigIntegerField(null=True, blank=True)
    rpl_reviewed_by_name = models.CharField(max_length=200, blank=True)
    rpl_reviewed_at = models.DateTimeField(null=True, blank=True)
    cv_extraction_acknowledged = models.BooleanField(default=False)
    cv_extraction_acknowledged_by = models.BigIntegerField(null=True, blank=True)
    cv_extraction_acknowledged_at = models.DateTimeField(null=True, blank=True)
    cv_extraction_acknowledgement_reason = models.TextField(blank=True)
    cv_source_document_id = models.CharField(max_length=200, blank=True)
    cv_source_container = models.CharField(max_length=100, blank=True)
    cv_source_blob_name = models.TextField(blank=True)
    cv_source_blob_hash = models.CharField(max_length=64, blank=True)
    cv_extractor_version = models.CharField(max_length=40, blank=True)

    source_snapshot = models.JSONField(default=dict)
    source_versions = models.JSONField(default=dict)
    source_snapshot_hash = models.CharField(max_length=64)

    created_by = models.BigIntegerField(null=True, blank=True)
    updated_by = models.BigIntegerField(null=True, blank=True)
    finalised_by = models.BigIntegerField(null=True, blank=True)
    finalised_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'suitability_rpl_reports'
        constraints = [
            models.UniqueConstraint(
                fields=('learner_kind', 'learner_id', 'context_key'),
                condition=Q(status='draft'),
                name='unique_draft_suitability_rpl_context',
            ),
        ]
        indexes = [
            models.Index(
                fields=('learner_kind', 'learner_id', 'context_key'),
                name='suit_rpl_context_idx',
            ),
        ]


class SuitabilityRplReduction(models.Model):
    report = models.ForeignKey(
        SuitabilityRplReport, on_delete=models.CASCADE, related_name='reductions',
    )
    module_id = models.CharField(max_length=200)
    module_title = models.TextField()
    planned_hours = models.DecimalField(max_digits=9, decimal_places=2)
    reduced_hours = models.DecimalField(max_digits=9, decimal_places=2)
    ksb_codes = models.JSONField(default=list, blank=True)
    evidence = models.TextField()
    assessor_comments = models.TextField(blank=True)
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'suitability_rpl_reductions'
        ordering = ('display_order', 'id')
        constraints = [
            models.UniqueConstraint(
                fields=('report', 'module_id'), name='unique_suitability_rpl_module',
            ),
        ]


class SuitabilityCvExtraction(models.Model):
    """Private, versioned text extracted from one approved CV blob."""
    STATUS_PENDING = 'pending'
    STATUS_READY = 'ready'
    STATUS_UNSUPPORTED = 'unsupported'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = tuple((value, value.replace('_', ' ').title()) for value in (STATUS_PENDING, STATUS_READY, STATUS_UNSUPPORTED, STATUS_FAILED))

    learner_kind = models.CharField(max_length=20)
    learner_id = models.BigIntegerField()
    source_document_id = models.CharField(max_length=200)
    source_container = models.CharField(max_length=100)
    source_blob_name = models.TextField()
    source_blob_hash = models.CharField(max_length=64)
    source_filename = models.CharField(max_length=255, blank=True)
    source_content_type = models.CharField(max_length=160, blank=True)
    extractor_version = models.CharField(max_length=40, default='cv-text-v1')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    extracted_text = models.TextField(blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    extracted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'suitability_cv_extractions'
        constraints = [models.UniqueConstraint(fields=('learner_kind', 'learner_id', 'source_document_id', 'source_blob_hash'), name='unique_suitability_cv_source')]
        indexes = [models.Index(fields=('learner_kind', 'learner_id', 'source_document_id'), name='suit_cv_learner_doc_idx')]


class SuitabilityRplDocumentSignature(models.Model):
    """One immutable signer record for one issued Suitability/RPL PDF.

    ``issued_document_id`` is the UUID of the existing immutable
    ``Document_Template_Issues`` row. It is intentionally stored as a value,
    rather than a Django FK, because that table is managed outside Django
    migrations and lives in the ``enrolment`` schema.
    """

    ROLE_ADMIN = 'admin'
    ROLE_LEARNER = 'learner'
    ROLE_EMPLOYER = 'employer'
    ROLE_CHOICES = (
        (ROLE_ADMIN, 'Admin / assessor'),
        (ROLE_LEARNER, 'Learner'),
        (ROLE_EMPLOYER, 'Employer / line manager'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    issued_document_id = models.UUIDField()
    document_key = models.CharField(max_length=64, default='suitability-rpl-report')
    learner_kind = models.CharField(max_length=20)
    learner_id = models.BigIntegerField()
    report_id = models.BigIntegerField()
    report_version = models.PositiveIntegerField()
    report_revision = models.PositiveIntegerField()
    pdf_sha256 = models.CharField(max_length=64)
    signer_role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    login_account_id = models.BigIntegerField()
    signer_subject_type = models.CharField(max_length=20)
    signer_subject_id = models.BigIntegerField()
    signer_name = models.CharField(max_length=200)
    signature_method = models.CharField(max_length=32, default='drawn')
    signature = models.TextField()
    consent_version = models.CharField(max_length=40, default='suitability-rpl-sign-v1')
    consent_text = models.TextField()
    consented_at = models.DateTimeField()
    signed_at = models.DateTimeField()
    ip_address = models.TextField(blank=True)
    user_agent = models.TextField(blank=True)
    audit_metadata = models.JSONField(default=dict)

    class Meta:
        db_table = 'suitability_rpl_document_signatures'
        constraints = [
            models.UniqueConstraint(
                fields=('issued_document_id', 'signer_role'),
                name='unique_suit_rpl_issue_signature_role',
            ),
        ]
        indexes = [
            models.Index(fields=('learner_kind', 'learner_id', 'issued_document_id'), name='suit_rpl_sig_issue_idx'),
        ]
