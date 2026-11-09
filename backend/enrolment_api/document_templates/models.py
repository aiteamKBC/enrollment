import uuid
from django.db import models


class TemplateVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template_id = models.CharField(max_length=120)
    document_key = models.CharField(max_length=64)
    number = models.PositiveIntegerField()
    content = models.JSONField()
    content_hash = models.CharField(max_length=64)
    published_at = models.DateTimeField(auto_now_add=True)
    published_by = models.TextField()
    approval_note = models.TextField(blank=True)

    class Meta:
        app_label = 'enrolment_api'
        managed = False
        db_table = 'enrolment"."Document_Template_Versions'
        unique_together = [('template_id', 'number')]


class TemplateDraft(models.Model):
    template_id = models.CharField(max_length=120, primary_key=True)
    document_key = models.CharField(max_length=64)
    base_version = models.CharField(max_length=100)
    revision = models.PositiveIntegerField(default=1)
    content = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.TextField()

    class Meta:
        app_label = 'enrolment_api'
        managed = False
        db_table = 'enrolment"."Document_Template_Drafts'


class IssuedTemplate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document_key = models.CharField(max_length=64)
    source_id = models.CharField(max_length=160)
    learner_kind = models.CharField(max_length=32)
    learner_id = models.BigIntegerField()
    version = models.ForeignKey(TemplateVersion, null=True, on_delete=models.PROTECT)
    template_snapshot = models.JSONField()
    field_values = models.JSONField()
    review_snapshot = models.JSONField(null=True)
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'enrolment_api'
        managed = False
        db_table = 'enrolment"."Document_Template_Issues'
        unique_together = [('document_key', 'source_id')]


class RenderHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    issue = models.ForeignKey(IssuedTemplate, on_delete=models.PROTECT)
    payload = models.JSONField()
    fingerprint = models.CharField(max_length=64)
    recorded_at = models.DateTimeField(auto_now_add=True)
    recorded_by = models.TextField(blank=True)

    class Meta:
        app_label = 'enrolment_api'
        managed = False
        db_table = 'enrolment"."Document_Template_Render_History'
        unique_together = [('issue', 'fingerprint')]


class LearnerDocumentRevision(models.Model):
    """A frozen learner copy, independently of its global template version.

    Fresh marks stay on the existing document/review models, not this table.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    issue = models.ForeignKey(IssuedTemplate, on_delete=models.PROTECT)
    number = models.PositiveIntegerField()
    base = models.ForeignKey('self', null=True, on_delete=models.PROTECT)
    state = models.CharField(max_length=16, default='draft')
    edit_version = models.PositiveIntegerField(default=1)
    content = models.JSONField()
    data = models.JSONField()
    signature_method = models.CharField(max_length=16, blank=True)
    provenance = models.JSONField(default=dict)
    signing_source_id = models.CharField(max_length=160, blank=True)
    reason = models.TextField(blank=True)
    changes = models.JSONField(default=dict)
    checksum = models.CharField(max_length=64, blank=True)
    created_by = models.TextField()
    updated_by = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    finalized_at = models.DateTimeField(null=True)

    class Meta:
        app_label = 'enrolment_api'
        managed = False
        db_table = 'enrolment"."Learner_Document_Revisions'
        unique_together = [('issue', 'number')]
