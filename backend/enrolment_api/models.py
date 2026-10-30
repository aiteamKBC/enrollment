"""Unmanaged mapping of enrolment."Extended_ILR".

One row per learner, holding the answers captured on the wizard's Extended ILR
step. The learner is identified by (learner_kind, learner_id) rather than a
foreign key: the pairing dates from when apprenticeship and commercial learners
lived in two separate tables. They now share enrolment."Created_users" (told
apart by its "Learner_type" column), so learner_id alone is unique and
learner_kind is retained only to keep existing rows resolvable.

The answers themselves are stored as a single jsonb document rather than ~45
columns: the form is a compliance questionnaire that gets reworded whenever the
ESFA revises it, and a document column absorbs added or renamed questions
without DDL. The columns broken out flat are the ones other features need to
query or report on (signature state and completion).

`managed = False` — the table is created by the apply_extended_ilr_table
management command, matching how every other enrolment table is handled here.
"""
from django.db import models

from learner_api.models import SafeJSONField


class ExtendedIlr(models.Model):
    id = models.AutoField(primary_key=True, db_column="id")

    # --- learner identity (see module docstring: two source tables) ---
    learner_kind = models.TextField(db_column="Learner_kind")  # 'apprenticeship' | 'commercial'
    learner_id = models.BigIntegerField(db_column="Learner_id")
    learner_name = models.TextField(db_column="Learner_name", null=True, blank=True)

    # --- the questionnaire itself ---
    answers = SafeJSONField(db_column="Answers", null=True, blank=True)

    # The wizard's other steps (personal details, skills radar, PLR, CV/job,
    # policies). Same reasoning as Answers: one document rather than columns, so
    # a step gaining a field needs no DDL.
    wizard_draft = SafeJSONField(db_column="Wizard_draft", null=True, blank=True)

    # --- flat columns other features report on ---
    learner_signed = models.BooleanField(db_column="Learner_signed", default=False)
    learner_signed_date = models.TextField(db_column="Learner_signed_date", null=True, blank=True)
    provider_signed = models.BooleanField(db_column="Provider_signed", default=False)
    provider_signed_date = models.TextField(db_column="Provider_signed_date", null=True, blank=True)
    completed = models.BooleanField(db_column="Completed", default=False)
    # Next of kin's own address — only answered when it is not the learner's.
    next_of_kin_postcode = models.TextField(db_column="Next_of_kin_postcode", null=True, blank=True)
    next_of_kin_address = models.TextField(db_column="Next_of_kin_address", null=True, blank=True)
    # Eligibility proof of identification/residency: one object per file in
    # Azure, each with its approved-container blob `path`. Written only by
    # ilr_eligibility_evidence, never by the answers save.
    eligibility_evidence = SafeJSONField(db_column="Eligibility_evidence", default=list, blank=True)

    created_at = models.DateTimeField(db_column="Created_at", auto_now_add=True)
    updated_at = models.DateTimeField(db_column="Updated_at", auto_now=True)

    class Meta:
        managed = False
        # Emitted by Django as "enrolment"."Extended_ILR".
        db_table = 'enrolment"."Extended_ILR'

    def __str__(self):
        return f"Extended ILR {self.learner_kind}:{self.learner_id}"


# ---------------------------------------------------------------------------
# Per-step wizard tables.
#
# These hold the same data as ExtendedIlr.wizard_draft, but as real columns and
# rows so it can be queried and reported on ("who hasn't acknowledged policy X",
# "every learner rating themselves 'rarely' on K3"). The draft column remains the
# resume/audit snapshot. Created by apply_enrolment_wizard_tables, hence
# managed = False like every other table in this schema.
# ---------------------------------------------------------------------------


class _LearnerScoped(models.Model):
    """Shared identity for the wizard tables: (learner_kind, learner_id).

    Not a foreign key: the pairing dates from when apprenticeship and commercial
    learners lived in two separate tables. Both now share
    enrolment."Created_users", so learner_id alone identifies a learner and
    learner_kind is kept only so existing rows keep resolving.
    """

    id = models.AutoField(primary_key=True, db_column="id")
    learner_kind = models.TextField(db_column="Learner_kind")
    learner_id = models.BigIntegerField(db_column="Learner_id")
    created_at = models.DateTimeField(db_column="Created_at", auto_now_add=True)
    updated_at = models.DateTimeField(db_column="Updated_at", auto_now=True)

    class Meta:
        abstract = True


class WizardPersonalDetails(_LearnerScoped):
    first_name = models.TextField(db_column="First_name", null=True, blank=True)
    last_name = models.TextField(db_column="Last_name", null=True, blank=True)
    email = models.TextField(db_column="Email", null=True, blank=True)
    phone = models.TextField(db_column="Phone", null=True, blank=True)
    address = models.TextField(db_column="Address", null=True, blank=True)
    date_of_birth = models.DateField(db_column="Date_of_birth", null=True, blank=True)
    age = models.IntegerField(db_column="Age", null=True, blank=True)
    sex = models.TextField(db_column="Sex", null=True, blank=True)
    # PNG data URL — drawn in the browser or uploaded (see SignaturePad.tsx).
    signature = models.TextField(db_column="Signature", null=True, blank=True)
    signature_date = models.DateField(db_column="Signature_date", null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'enrolment"."Wizard_Personal_Details'


class WizardIlrLearnerDetails(_LearnerScoped):
    """The ILR Learner Details step: what the ILR asks that the learner record
    does not already hold. Name, DOB, current address, phone and email stay on
    enrolment."Created_users" and are shown read-only on the step."""

    years_at_address = models.IntegerField(db_column="Years_at_address", null=True, blank=True)
    at_address_since_birth = models.BooleanField(db_column="At_address_since_birth", null=True, blank=True)
    postcode_prior_to_enrolment = models.TextField(db_column="Postcode_prior_to_enrolment", null=True, blank=True)
    national_insurance_number = models.TextField(db_column="National_insurance_number", null=True, blank=True)
    ni_number_applied = models.BooleanField(db_column="Ni_number_applied", null=True, blank=True)
    legal_sex = models.TextField(db_column="Legal_sex", null=True, blank=True)
    pronouns = models.TextField(db_column="Pronouns", null=True, blank=True)
    ethnicity = models.TextField(db_column="Ethnicity", null=True, blank=True)
    long_term_disability = models.BooleanField(db_column="Long_term_disability", null=True, blank=True)
    highest_qualification = models.TextField(db_column="Highest_qualification", null=True, blank=True)
    employment_status = models.TextField(db_column="Employment_status", null=True, blank=True)
    # Asked only of learners in paid employment.
    employment_start_date = models.DateField(db_column="Employment_start_date", null=True, blank=True)
    job_title = models.TextField(db_column="Job_title", null=True, blank=True)
    self_employed = models.BooleanField(db_column="Self_employed", null=True, blank=True)
    full_time_education = models.BooleanField(db_column="Full_time_education", null=True, blank=True)
    # Asked only when in full-time education or training.
    expected_leaving_date = models.DateField(db_column="Expected_leaving_date", null=True, blank=True)
    # Asked only of learners not in paid employment.
    length_of_unemployment = models.TextField(db_column="Length_of_unemployment", null=True, blank=True)
    volunteers = models.BooleanField(db_column="Volunteers", null=True, blank=True)
    state_benefits = models.TextField(db_column="State_benefits", null=True, blank=True)
    # Asked when a benefit is claimed: in the learner's own right or a joint claim.
    benefit_claim_basis = models.TextField(db_column="Benefit_claim_basis", null=True, blank=True)
    # PNG data URL — see SignaturePad.tsx.
    declaration_signature = models.TextField(db_column="Declaration_signature", null=True, blank=True)
    declaration_signed_date = models.DateField(db_column="Declaration_signed_date", null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'enrolment"."Wizard_Ilr_Learner_Details'


class WizardSkillsRadar(_LearnerScoped):
    standard_id = models.TextField(db_column="Standard_id", null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'enrolment"."Wizard_Skills_Radar'


class WizardKsbAssessment(_LearnerScoped):
    ksb_id = models.TextField(db_column="Ksb_id")
    level = models.TextField(db_column="Level", null=True, blank=True)
    # 9..1 for `level`, denormalised for reporting (see LEVEL_SCORES).
    score = models.IntegerField(db_column="Score", null=True, blank=True)
    note = models.TextField(db_column="Note", null=True, blank=True)
    action_text = models.TextField(db_column="Action_text", null=True, blank=True)
    action = models.TextField(db_column="Action", null=True, blank=True)
    goal = models.TextField(db_column="Goal", null=True, blank=True)
    due_date = models.DateField(db_column="Due_date", null=True, blank=True)
    evidence_files = SafeJSONField(db_column="Evidence_files", null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'enrolment"."Wizard_Ksb_Assessments'


class WizardPlr(_LearnerScoped):
    uln = models.TextField(db_column="ULN", null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'enrolment"."Wizard_Plr'


class WizardPlrRecord(_LearnerScoped):
    record_ref = models.TextField(db_column="Record_ref")
    place_of_study = models.TextField(db_column="Place_of_study", null=True, blank=True)
    qualification_type = models.TextField(db_column="Qualification_type", null=True, blank=True)
    subject = models.TextField(db_column="Subject", null=True, blank=True)
    level = models.TextField(db_column="Level", null=True, blank=True)
    award_date = models.DateField(db_column="Award_date", null=True, blank=True)
    credits = models.IntegerField(db_column="Credits", null=True, blank=True)
    grade = models.TextField(db_column="Grade", null=True, blank=True)
    record_type = models.TextField(db_column="Record_type", null=True, blank=True)
    start_date = models.DateField(db_column="Start_date", null=True, blank=True)
    end_date = models.DateField(db_column="End_date", null=True, blank=True)
    # Certificate/evidence files held in Azure: one object per file, with its
    # approved-container blob `path`. Written only by plr_evidence, never by
    # the draft save.
    evidence = SafeJSONField(db_column="Evidence", default=list, blank=True)

    class Meta:
        managed = False
        db_table = 'enrolment"."Wizard_Plr_Records'


class WizardCvJob(_LearnerScoped):
    cv_file = models.TextField(db_column="Cv_file", null=True, blank=True)
    experience_text = models.TextField(db_column="Experience_text", null=True, blank=True)
    pm_qualifications = models.TextField(db_column="PM_qualifications", null=True, blank=True)
    functional_skills_enrol = models.TextField(db_column="Functional_skills_enrol", null=True, blank=True)
    # Qualifications. "Field" questions are worded for the learner's programme
    # (marketing, project management, ...); the answers are stored the same way.
    highest_qualification = models.TextField(db_column="Highest_qualification", null=True, blank=True)
    highest_qualification_field = models.TextField(db_column="Highest_qualification_field", null=True, blank=True)
    has_field_qualification = models.BooleanField(db_column="Has_field_qualification", null=True, blank=True)
    highest_field_qualification = models.TextField(db_column="Highest_field_qualification", null=True, blank=True)
    gcse_english = models.BooleanField(db_column="Gcse_english", null=True, blank=True)
    gcse_maths = models.BooleanField(db_column="Gcse_maths", null=True, blank=True)
    # CV, transcript and GCSE evidence held in Azure: one object per file, with
    # its `docKind` and approved-container blob `path`. Written only by
    # cv_job_documents, never by the draft save.
    documents = SafeJSONField(db_column="Documents", default=list, blank=True)

    class Meta:
        managed = False
        db_table = 'enrolment"."Wizard_Cv_Job'


class WizardPolicyAck(_LearnerScoped):
    policy_id = models.TextField(db_column="Policy_id")
    acknowledged = models.BooleanField(db_column="Acknowledged", default=False)
    acknowledged_at = models.DateTimeField(db_column="Acknowledged_at", null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'enrolment"."Wizard_Policy_Acks'
