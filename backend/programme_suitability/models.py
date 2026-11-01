from django.db import models


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
