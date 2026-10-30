"""Which records are audited, and which of their fields may be shown.

Every entry here is two decisions made deliberately and written down:

1. **What is collected.** The tuple is an allowlist. A column not named cannot
   reach the audit log, whatever a write path does later, so adding a column to
   a table does not quietly add it to the history of that table.

2. **What may be read back.** ``redact`` names the fields that record *that*
   they changed without recording *what to*. Those fields are stored as a digest
   of their own value, so an edit to a learner's date of birth still shows up as
   an edit to a learner's date of birth, by a named person, at a known time --
   and the date of birth itself is nowhere in the log.

The rule applied throughout: personal detail about a human being is redacted;
the operational facts of their enrolment are not. Somebody's programme, cohort,
status, dates and access level are what an audit is for. Their address, phone
number and national insurance number are not, and an audit log that copied them
would be a second, less-guarded copy of the very data it exists to protect.

Free-text notes and answers about learners are redacted too. The trail records
that they changed, not their sensitive contents. Operational status, ownership,
dates, scheduling and scores remain available as before/after values.

Left out of the allowlists entirely, rather than redacted, because they are a
capability rather than a fact: meeting join links and evidence image URLs. A
digest of a link is useless, and the link itself is a way in.
"""

from __future__ import annotations

import logging

from .writes import attach_bulk_capture, register, register_model

logger = logging.getLogger(__name__)

#: The model attributes that say when a row was made and last saved. Passed as
#: ``timestamps=`` rather than listed in ``columns``: they decide whether a save
#: is reported as a create or as "first recorded", and must not show up as a
#: changed field on every edit. Named only for models that genuinely store both
#: -- a record type without the pair stays "first recorded" on its first sight.
ROW_TIMESTAMPS = ('created_at', 'updated_at')


def register_enrolment_records():
    """Learner, staff, employer and organisation records on the enrolment DB."""
    try:
        from learner_api.models import Employer, EnrolmentUser, Organisation, StaffUser
    except Exception:
        logger.warning('Enrolment records could not be registered for audit.', exc_info=True)
        return

    register_model(
        EnrolmentUser,
        workspace='admin',
        entity_type='learner_record',
        label='Learner record',
        href='/users',
        # The learner's own page, which is what an auditor reading "who changed
        # their end date" is actually after.
        record_href='/users/{id}',
        key='id',
        title='username',
        # A record imported before anyone set a username still has to be
        # findable by the person it is about.
        title_fallback='email',
        # Where they sit in delivery, which is the line the trail reads under
        # the name -- and a save that touches only these is a move, not an edit.
        context=('programme', 'cohort', 'group'),
        parents=('programme', 'cohort', 'group'),
        parent='programme',
        status='status',
        using='enrolment',
        columns=(
            # identity
            'id', 'uuid', 'aptem_id', 'username', 'email', 'title', 'preferred_name',
            # where they sit in delivery
            'learner_type', 'status', 'type', 'programme_status',
            'programme', 'cohort', 'group', 'target_programme', 'sub_programme',
            # who looks after them
            'coach_name', 'coach_email', 'mentor', 'case_owner', 'learning_provider',
            # who they work for
            'employer', 'employer_id', 'organization', 'line_manager',
            # the dates and hours the funding rests on
            'start_date', 'end_date', 'learner_start_date', 'learner_end_date',
            'practical_period_end_date', 'apprenticeship_end_date',
            'minimum_required_hours', 'planned_hours', 'rpl_hours',
            'extended_break', 'reference_number',
            # onboarding and access
            'onboarding_status', 'onboarding_completed', 'invite_to_platform',
            'allow_access_to_checkpoint', 'allow_access_to_console', 'allow_access_to_classic',
            # personal detail -- collected so a change to it is visible, redacted
            # below so the value is not
            'date_of_birth', 'phone_number', 'address', 'current_postcode',
            'national_insurance_number', 'legal_sex', 'gender', 'age',
            'referrer', 'referrer_address', 'referrer_contact',
            'employer_address', 'contacts',
        ),
        redact=(
            'date_of_birth', 'phone_number', 'address', 'current_postcode',
            'national_insurance_number', 'legal_sex', 'gender', 'age',
            'referrer', 'referrer_address', 'referrer_contact',
            'employer_address', 'contacts',
        ),
    )

    register_model(
        StaffUser,
        workspace='admin',
        entity_type='staff_record',
        label='Staff record',
        # No `record_href`: a staff account has no page of its own -- it is
        # edited in a dialog on the directory -- so the directory is the honest
        # destination rather than a /users/<id> that would read a learner with a
        # colliding id.
        href='/users',
        key='id',
        title='username',
        title_fallback='email',
        # Their role and their access level: the two facts that make a staff
        # change worth reading at all.
        context=('position', 'access', 'organization'),
        parent='organization',
        status='status',
        json_columns=('access_extra',),
        using='enrolment',
        columns=(
            'id', 'uuid', 'username', 'email', 'title', 'preferred_name',
            'type', 'status', 'position',
            # The permission level. The single most audit-worthy field in the
            # LMS: it is the answer to "who gave them that access, and when?".
            'access', 'access_extra',
            'organization', 'case_owner', 'learning_provider', 'reference_number',
            'invite_to_platform', 'allow_access_to_checkpoint',
            'allow_access_to_console', 'allow_access_to_classic',
            'created_at', 'updated_at',
            'phone_number', 'date_of_birth', 'gender',
        ),
        redact=('phone_number', 'date_of_birth', 'gender'),
    )

    register_model(
        Employer,
        workspace='employer',
        entity_type='employer_contact',
        label='Employer contact',
        # `/employers` alone is not a route -- the employer directory is the
        # Users list, and an employer's own side page is `/employers/<id>`.
        href='/users',
        record_href='/employers/{id}',
        key='id',
        # Both halves of the name: neither identifies a contact on its own.
        title=('first_name', 'surname'),
        title_fallback='email',
        context=('email', 'town_city', 'country'),
        using='enrolment',
        columns=(
            'id', 'uuid', 'first_name', 'surname', 'email',
            'town_city', 'county', 'country',
            'signature_name', 'signature_date',
            'created_at', 'updated_at',
            'mobile', 'post_code', 'gender', 'signature',
        ),
        redact=('mobile', 'post_code', 'gender', 'signature'),
    )

    register_model(
        Organisation,
        workspace='employer',
        entity_type='organisation',
        label='Organisation',
        href='/users',
        key='id',
        title='name',
        context=('parent_name', 'city_town', 'country'),
        parent='parent_name',
        status='status',
        using='enrolment',
        columns=(
            'id', 'status', 'name', 'owner', 'category', 'group_type', 'parent_name',
            'edrs_ern_number', 'apprenticeship_agreement_id',
            'city_town', 'county', 'country', 'website', 'reference_number',
            'levy_payer', 'approx_no_of_employees', 'health_and_safety',
            'send_hours_verification_emails', 'logo_url',
            'contact_name', 'contact_role',
            'created_at', 'updated_at',
            'post_code', 'contact_email', 'contact_telephone',
        ),
        redact=('post_code', 'contact_email', 'contact_telephone'),
    )

    # Bulk writes, which no signal sees. `EnrolmentUser.all_learners` is the
    # manager every bulk site in the codebase uses -- including the programme
    # change in `learning_plan.py`, which moves a learner between programmes
    # without a single `post_save` -- and it is a plain manager, so it is
    # swapped. Its sibling `objects` is an `ApprenticeshipManager` carrying a
    # queryset class of its own; that one is reported and left alone, because
    # re-basing a hand-written queryset to win a log line would be trading a
    # working feature for the trail. See `attach_bulk_capture`.
    for model, entity in (
        (EnrolmentUser, 'learner_record'),
        (StaffUser, 'staff_record'),
        (Employer, 'employer_contact'),
        (Organisation, 'organisation'),
    ):
        attach_bulk_capture(model, entity)


def register_coach_records():
    """Coaching meetings and absence reports."""
    try:
        from coach_api.models import CoachAbsenceReport, CoachCalendarEvent
    except Exception:
        logger.warning('Coach records could not be registered for audit.', exc_info=True)
        return

    register_model(
        CoachCalendarEvent,
        workspace='coach',
        entity_type='coach_meeting',
        label='Coaching meeting',
        href='/coach/meetings',
        record_href='/coach/meetings/{id}',
        key='event_key',
        # Whose meeting, and which kind. "progress-review" alone names a
        # category, not the meeting somebody moved.
        title=('learner_name', 'event_type'),
        title_join=' — ',
        title_fallback='event_type',
        # Who runs it, when it is, and where it has got to.
        context=('owner_name', 'scheduled_date', 'status'),
        parent='learner_id',
        status='status',
        columns=(
            'event_key', 'owner_email', 'owner_name',
            'learner_id', 'learner_name', 'learner_email',
            'event_type', 'sequence', 'occurrence_number',
            'target_date', 'scheduled_date', 'scheduled_time', 'duration_minutes',
            'status', 'meeting_provider',
            # The meeting's identity in Graph, which is traceable on purpose.
            # `meeting_link` and `graph_web_link` are deliberately absent: those
            # are the way into the meeting, not a fact about it.
            'graph_event_id', 'graph_organizer_email',
            'review_template_id', 'review_instance_id', 'review_completed_at',
            'manager_signed_at', 'manager_signed_by',
            'sync_state', 'created_at', 'updated_at',
            'notes', 'review_responses',
        ),
        json_columns={'review_responses'},
        redact=('notes', 'review_responses'),
    )

    register_model(
        CoachAbsenceReport,
        workspace='coach',
        entity_type='absence_report',
        label='Absence report',
        # No `record_href`: a report is read in place on the list, which has no
        # per-report URL to link to.
        href='/coach/absence-reports',
        key='id',
        # Whose absence, from which session. The session title alone repeats
        # across every learner who missed it.
        title=('learner_name', 'session_title'),
        title_join=' — ',
        title_fallback='session_title',
        context=('session_date', 'reason_category', 'status'),
        parent='learner_id',
        status='status',
        columns=(
            'id', 'attendance_id', 'owner_email', 'owner_name',
            'learner_id', 'learner_name', 'learner_email',
            'session_title', 'session_date', 'session_time',
            'reason_category', 'reported_by', 'status',
            'evidence_provided', 'evidence_kind',
            'previous_absences', 'attendance_rate',
            'recovery_method', 'catchup_event_key',
            'created_at', 'updated_at',
            'reason', 'evidence_text', 'coach_note',
        ),
        redact=('reason', 'evidence_text', 'coach_note'),
    )

    # Bulk writes, which no signal sees. A coach meeting reaching
    # `awaiting-signature` is written as `filter(...).update(status=...)`, so
    # without this the status changes most worth tracing were the ones missing
    # from the trail. Switched on for these two models only -- see
    # `attach_bulk_capture`.
    attach_bulk_capture(CoachCalendarEvent, 'coach_meeting')
    attach_bulk_capture(CoachAbsenceReport, 'absence_report')


def register_coach_review_records():
    """Progress reviews and marking: the coach writing on a learner's record.

    These are raw tables rather than models, and they need no new capture
    mechanism. ``curriculum_api``'s ``insert_row`` / ``update_rows`` /
    ``delete_rows`` already hand every row they write to
    ``versioning.record_rows``, and ``review_instances.py`` writes through
    exactly those helpers -- so a review save has been reaching the recorder all
    along and being dropped, because the recorder had never been told these
    tables existed. Registering them is the whole change.

    Marking is the exception: it is one raw ``UPDATE`` on the ``enrolment``
    connection, so ``coach_api.views`` records it explicitly at the statement.

    Two columns are deliberately left out of the allowlists, on the rule
    ``versioning`` already applies to a meeting join link -- a stored blob that
    is an artefact or a capability rather than a fact, and that no reader of a
    diff can use:

    * ``review_instances.definition_snapshot`` -- the frozen question set. It is
      copied once when the instance is created and never edited, so carrying it
      would add a large blob to every revision to say nothing that changed.
    * ``review_instance_signatures.signature`` -- the drawn signature, a base64
      image. Who signed, under which role and when are all recorded; the image
      is not, and its absence is why a signature revision stays small enough to
      read.
    """
    register(
        'review_instances',
        workspace='coach',
        entity_type='review_instance',
        label='Progress review',
        href='/coach/progress-reviews',
        record_href='/coach/reviews/{id}',
        key='id',
        # A review instance carries no name of its own -- the title shown on
        # screen is read from the live template, which is not in this row. So it
        # is placed rather than named: whose it is, when it is due, and where it
        # has got to.
        context=('coach_email', 'target_date', 'status'),
        parent='learner_id',
        status='status',
        json_columns={'progress_snapshot'},
        columns=(
            'id', 'review_template_id', 'learner_id', 'learner_kind', 'programme_id',
            'occurrence_number', 'occurrence_source', 'occurrence_ref',
            'target_date', 'coach_email', 'calendar_event_id',
            'status', 'started_at', 'completed_at',
            'progress_snapshot',
            'created_by', 'updated_by',
        ),
    )

    register(
        'review_instance_answers',
        workspace='coach',
        entity_type='review_answer',
        label='Review answer',
        href='/coach/progress-reviews',
        key='id',
        # Named by the question, not by the answer. A trail that listed answers
        # by their own text would reproduce the review in the list; the answer
        # belongs in the diff, where a reader has asked to see it.
        title='field_id',
        parent='review_instance_id',
        # Parsed before diffing, so editing one value inside the blob reads as
        # that value moving rather than as "the answer changed".
        json_columns={'answer'},
        redact=('answer',),
        columns=(
            'id', 'review_instance_id', 'field_id', 'answer',
            'answered_by', 'answered_at',
        ),
    )

    register(
        'review_instance_signatures',
        workspace='coach',
        entity_type='review_signature',
        label='Review signature',
        href='/coach/progress-reviews',
        key='id',
        title=('role', 'signed_name'),
        title_join=' — ',
        title_fallback='role',
        parent='review_instance_id',
        columns=(
            'id', 'review_instance_id', 'role',
            'signed_by', 'signed_name', 'signed_at',
        ),
    )

    register(
        'review_instance_reopens',
        workspace='coach',
        entity_type='review_reopen',
        label='Review reopened',
        href='/coach/progress-reviews',
        key='id',
        # The move itself is the record's name: "completed to in-progress" is
        # what somebody is looking for when they ask why a signed review is open
        # again.
        title=('previous_status', 'new_status'),
        title_join=' → ',
        title_fallback='reason_code',
        context=('reason_code', 'changed_by', 'changed_at'),
        parent='review_instance_id',
        status='new_status',
        columns=(
            'id', 'review_instance_id', 'calendar_event_id',
            'previous_status', 'new_status', 'reason_code', 'note',
            'changed_by', 'changed_at',
        ),
        redact=('note',),
    )

    register(
        'review_instance_manual_overrides',
        workspace='coach',
        entity_type='review_manual_override',
        label='Review status override',
        href='/coach/progress-reviews',
        key='id',
        title=('previous_status', 'new_status'),
        title_join=' → ',
        title_fallback='reason_code',
        context=('reason_code', 'changed_by', 'changed_at'),
        parent='review_instance_id',
        status='new_status',
        columns=(
            'id', 'review_instance_id', 'calendar_event_id',
            'previous_status', 'new_status', 'reason_code', 'note',
            'changed_by', 'changed_at', 'manual_started_at',
        ),
        redact=('note',),
    )

    register(
        'learner_review_additions',
        workspace='coach',
        entity_type='learner_review_addition',
        label='Extra review for one learner',
        href='/coach/progress-reviews',
        key='id',
        title='reason_code',
        title_fallback='reason_code',
        context=('target_date', 'reason_code', 'created_by'),
        parent='learner_id',
        columns=(
            'id', 'review_template_id', 'programme_id', 'learner_id',
            'target_date', 'reason_code', 'reason',
            'created_by', 'updated_by', 'deleted_at', 'deleted_by',
        ),
        redact=('reason',),
    )

    # One row, two authors: the learner creates it by handing work in and the
    # coach edits it by deciding on that work. So it is `assignment_submission`
    # rather than `marking_decision` -- a creation recorded under the latter
    # name would have read "Marking decision created" the moment a learner
    # pressed submit, which is not what happened. It stays in the Coach
    # workspace because the decision is the audited action; the learner's side
    # shows as the record being created.
    #
    # The column list is not written here. `learner_api.submission_audit` owns
    # it, because all three write paths have to return the same columns for
    # their snapshots to be comparable, and one of them lives in another app.
    register(
        'learning_reflection_submissions',
        workspace='coach',
        entity_type='assignment_submission',
        label='Assignment submission',
        href='/coach/marking-queue',
        record_href='/coach/marking-queue/{id}',
        key='id',
        # Whose work, and which piece of it. The activity title repeats across
        # every learner who submitted the same assignment, so it cannot name the
        # record on its own.
        title=('learner_name', 'activity_title'),
        title_join=' — ',
        title_fallback='activity_title',
        context=('module_title', 'reviewed_by', 'status'),
        parent='learner_id',
        status='status',
        using='enrolment',
        columns=submission_audit_columns(),
        redact=('coach_feedback',),
    )


def submission_audit_columns():
    """The submissions allowlist, from the module that the write paths share.

    Falls back to the decision-side columns alone if `learner_api` cannot be
    imported: a narrower trail is survivable, a start-up that dies because the
    audit registry could not resolve an import is not.
    """
    try:
        from learner_api.submission_audit import SUBMISSION_AUDIT_COLUMNS

        return SUBMISSION_AUDIT_COLUMNS
    except Exception:
        logger.warning('Submission audit columns unavailable; recording the decision only.', exc_info=True)
        return (
            'id', 'learner_kind', 'learner_id', 'learner_name',
            'status', 'coach_feedback', 'reviewed_by', 'reviewed_at',
        )


def register_enrolment_journey_records():
    """The enrolment itself: reviews, their detail sheets, and the signed documents.

    ``register_enrolment_records`` above covers the *records* enrolment
    maintains -- a learner, a staff account, an employer, an organisation. This
    covers the *process*: booking an eligibility review, answering it, signing
    an apprenticeship agreement, completing a training plan. Those are the
    actions somebody asks an audit trail about after the fact -- "who decided
    this learner was eligible", "when was that agreement signed and by whom" --
    and until now none of them left a trace.

    One rule decides every allowlist here, and it is the one the module docstring
    states: the operational facts of an enrolment are recorded in full, and
    personal detail about a human being is redacted to a digest. A funding
    decision, a signature date, a programme status and a compliance answer are
    what an audit is for. A date of birth, an address and a unique learner
    number are not.

    Every ``*_signature`` column is left out of its allowlist entirely rather
    than redacted. They hold a PNG data URL drawn in the browser, so a digest of
    one says nothing a reader could use and the value itself would put a
    person's handwritten signature into a second table. Who signed, under which
    name, and at what moment are all recorded -- which is what a reader of a
    signing trail actually needs.
    """
    try:
        from learner_api.models import (
            ApprenticeshipAgreement,
            EligibilityReviewDetail,
            EnrolmentReview,
            HealthSafetyReviewDetail,
            IlrDocument,
            LearnerProfile,
            RplReviewDetail,
            TrainingPlanDocument,
            WrittenAgreement,
        )
    except Exception:
        logger.warning('Enrolment journey records could not be registered for audit.', exc_info=True)
        return

    register_model(
        EnrolmentReview,
        workspace='admin',
        entity_type='enrolment_review',
        timestamps=ROW_TIMESTAMPS,
        label='Enrolment review',
        href='/users',
        key='id',
        # Which review, for whom. The label alone repeats across every learner
        # who was booked for the same one.
        title=('learner_name', 'review_label'),
        title_join=' — ',
        title_fallback='review_type',
        context=('coach_name', 'scheduled_date', 'status'),
        parent='learner_id',
        status='status',
        using='enrolment',
        # The form and its per-section progress are parsed before diffing, so
        # answering one question reads as that question being answered rather
        # than as "the form changed".
        json_columns={'form_answers', 'section_status'},
        columns=(
            'id', 'event_key', 'review_type', 'review_label',
            'learner_kind', 'learner_id', 'learner_name', 'learner_email',
            'coach_id', 'coach_name', 'coach_email',
            'scheduled_date', 'scheduled_time', 'duration_minutes',
            'status', 'notes',
            # The meeting's identity in Graph, traceable on purpose --
            # requirement 1 of the Teams invariants. `meeting_link` is absent
            # for the same reason it is absent from `coach_meeting`: it is the
            # way into the meeting, not a fact about it.
            'meeting_provider', 'graph_event_id', 'invite_sent', 'sync_error',
            'form_answers', 'section_status', 'form_completed', 'form_completed_at',
            'reviewed_by', 'started_at',
            # The fact of each signature, never the drawing.
            'learner_signed_name', 'learner_signed_at',
            'admin_signed_name', 'admin_signed_at',
            'employer_signed_name', 'employer_signed_at', 'employer_signature_required',
            'booked_at', 'cancelled_at',
        ),
        redact=('notes', 'form_answers'),
    )

    # The three detail sheets. Each is one row per review, projected out of
    # `Form_answers` on every save, so they are registered for the same reason
    # the form itself is: the sheet is what a compliance reader opens, and a
    # diff against the sheet names the question that moved.
    review_detail_common = (
        'id', 'review_id', 'event_key', 'learner_id', 'learner_name', 'completed',
    )

    register_model(
        EligibilityReviewDetail,
        workspace='admin',
        entity_type='eligibility_review',
        timestamps=ROW_TIMESTAMPS,
        label='Eligibility review sheet',
        href='/users',
        key='id',
        title='learner_name',
        title_fallback='event_key',
        context=('programme_status', 'completed'),
        parent='learner_id',
        status='programme_status',
        using='enrolment',
        json_columns={'initial_assessments', 'diagnostic_assessments', 'fs_results'},
        columns=review_detail_common + (
            # Funding eligibility. Every one of these is a decision somebody
            # made about public money, which is the clearest case in the LMS
            # for recording a value rather than a digest of one.
            'over16', 'within_contract_time', 'paye_scheme',
            'eligible_residency', 'identity_documents_seen', 'eligibility_evidence',
            'right_to_work_england', 'fifty_percent_england', 'minimum_wage',
            # Functional skills.
            'initial_assessments', 'diagnostic_assessments',
            'exemption_english', 'exemption_maths', 'exemption_ict', 'fs_results',
            # Job role discussion.
            'holds_level2', 'level_matches_role', 'productive_purpose',
            'ksb_exposure', 'release_for_otj', 'embed_otj', 'warning_areas',
            'comments', 'programme_status',
        ),
    )

    register_model(
        RplReviewDetail,
        workspace='admin',
        entity_type='rpl_review',
        timestamps=ROW_TIMESTAMPS,
        label='RPL review sheet',
        href='/users',
        key='id',
        title='learner_name',
        title_fallback='event_key',
        context=('reported_attainment', 'completed'),
        parent='learner_id',
        using='enrolment',
        json_columns={'prior_learning_items'},
        columns=review_detail_common + (
            'prior_learning_items',
            'apprenticeship_appropriate', 'plan_aligns_standard',
            'prior_education', 'prior_work_experience', 'plan_needs_adjusting',
            'reported_attainment',
            'attainment_english', 'attainment_maths', 'attainment_ict',
            'skills_radar_notes', 'comments',
            'uln',
        ),
        # The Unique Learner Number identifies a person across every provider
        # in the country. Recorded as changed; not recorded as a value.
        redact=('uln',),
    )

    register_model(
        HealthSafetyReviewDetail,
        workspace='admin',
        entity_type='health_safety_review',
        timestamps=ROW_TIMESTAMPS,
        label='Health & safety declaration',
        href='/users',
        key='id',
        title='learner_name',
        title_fallback='event_key',
        context=('completed',),
        parent='learner_id',
        using='enrolment',
        columns=review_detail_common + (
            # Not personal health data: these are declarations about the
            # employer's workplace arrangements, and they are recorded in full
            # because the declaration is the point of the record.
            'basic_arrangements', 'day_one_induction', 'fire_safety', 'first_aid',
            'supervision', 'ppe', 'accident_recording', 'inform_changes',
            'hs_policy', 'liability_insurance',
        ),
    )

    # The signed documents. Same shape, same rule: who signed, under what name,
    # when, and what state the document reached. Never the signature image, and
    # never the storage pointer -- `container` / `blob_name` / `doc_path` locate
    # the file in Azure, which is a way to fetch it rather than a fact about it.
    register_model(
        ApprenticeshipAgreement,
        workspace='admin',
        entity_type='apprenticeship_agreement',
        timestamps=ROW_TIMESTAMPS,
        label='Apprenticeship agreement',
        href='/users',
        key='id',
        title=('apprentice_name', 'standard'),
        title_join=' — ',
        title_fallback='employer_name',
        context=('employer_name', 'start_date', 'status'),
        parent='learner_id',
        status='status',
        using='enrolment',
        columns=(
            'id', 'learner_kind', 'learner_id', 'apprentice_name',
            'employer_name', 'standard',
            'start_date', 'end_date', 'practical_start', 'practical_end',
            'duration_weeks', 'planned_otjh',
            'apprentice_signed_name', 'apprentice_signed_at',
            'employer_signed_name', 'employer_signed_at',
            'fully_signed', 'status', 'size_bytes',
            'employer_address',
        ),
        # Consistent with `learner_record` above, which redacts the same field.
        redact=('employer_address',),
    )

    register_model(
        IlrDocument,
        workspace='admin',
        entity_type='ilr_document',
        timestamps=ROW_TIMESTAMPS,
        label='ILR document',
        href='/users',
        key='id',
        title='learner_signed_name',
        title_fallback='status',
        context=('provider_signed_name', 'status'),
        parent='learner_id',
        status='status',
        using='enrolment',
        columns=(
            'id', 'learner_kind', 'learner_id',
            'learner_signed_name', 'learner_signed_at',
            'provider_signed_name', 'provider_signed_at',
            'fully_signed', 'status',
        ),
    )

    register_model(
        TrainingPlanDocument,
        workspace='admin',
        entity_type='training_plan_document',
        timestamps=ROW_TIMESTAMPS,
        label='Training plan document',
        href='/users',
        key='id',
        title='apprentice_signed_name',
        title_fallback='status',
        context=('employer_signed_name', 'provider_signed_name', 'status'),
        parent='learner_id',
        status='status',
        using='enrolment',
        columns=(
            'id', 'learner_kind', 'learner_id',
            'apprentice_signed_name', 'apprentice_position', 'apprentice_signed_at',
            'employer_signed_name', 'employer_position', 'employer_signed_at',
            'provider_signed_name', 'provider_position', 'provider_signed_at',
            'fully_signed', 'status',
        ),
    )

    register_model(
        WrittenAgreement,
        workspace='admin',
        entity_type='written_agreement',
        timestamps=ROW_TIMESTAMPS,
        label='Written agreement',
        href='/users',
        key='id',
        title='learner_signed_name',
        title_fallback='status',
        context=('employer_signed_name', 'provider_signed_name', 'status'),
        parent='learner_id',
        status='status',
        using='enrolment',
        columns=(
            'id', 'learner_kind', 'learner_id',
            'learner_signed_name', 'learner_position', 'learner_signed_at',
            'employer_signed_name', 'employer_position', 'employer_signed_at',
            'provider_signed_name', 'provider_position', 'provider_signed_at',
            'fully_signed', 'status',
        ),
    )

    register_model(
        LearnerProfile,
        workspace='admin',
        entity_type='learner_profile',
        timestamps=ROW_TIMESTAMPS,
        label='Learner delivery profile',
        href='/users',
        record_href='/users/{id}',
        key='id',
        title='full_name',
        title_fallback='email',
        context=('programme', 'cohort', 'group_name'),
        parents=('programme', 'cohort', 'group_name'),
        parent='programme_id',
        status='lifecycle_status',
        using='enrolment',
        columns=(
            'id', 'uuid', 'full_name', 'email', 'enrolment_id',
            'lifecycle_status', 'learner_type',
            'programme', 'programme_id', 'programme_status',
            'cohort', 'cohort_id', 'group_name', 'group_id',
            # The hours the funding rests on.
            'completed_hours', 'target_hours', 'minimum_hours', 'maximum_hours',
            'planned_hours', 'progress_hours', 'progress_variance', 'otjh_status',
            # Who looks after them, and the RAG rating their coach sets -- the
            # one coach action on a learner that lives on this table rather than
            # on a coach one.
            'coach_name', 'coach_email', 'coach_rag',
            'accepted_assignments', 'rejected_assignments',
            'accepted_reflections', 'rejected_reflections',
            'learner_start_date', 'learner_end_date', 'start_date', 'end_date',
            'gateway_review_date', 'alert_notify_for_epa', 'enter_epa',
            'phone_number',
        ),
        # `learning_plan` is left out rather than redacted: it is a derived copy
        # of the curriculum this learner is on, rewritten wholesale whenever the
        # programme changes, and Curriculum Studio already records the authoring
        # side of every one of those changes.
        redact=('phone_number',),
    )

    # Bulk writes, which no signal sees. All of these declare a plain manager,
    # so every one is swapped; `register_enrolment_records` above explains the
    # one case that is not.
    for model, entity in (
        (EnrolmentReview, 'enrolment_review'),
        (EligibilityReviewDetail, 'eligibility_review'),
        (RplReviewDetail, 'rpl_review'),
        (HealthSafetyReviewDetail, 'health_safety_review'),
        (ApprenticeshipAgreement, 'apprenticeship_agreement'),
        (IlrDocument, 'ilr_document'),
        (TrainingPlanDocument, 'training_plan_document'),
        (WrittenAgreement, 'written_agreement'),
        (LearnerProfile, 'learner_profile'),
    ):
        attach_bulk_capture(model, entity)


def register_enrolment_wizard_records():
    """The enrolment wizard, step by step.

    Eight tables the learner and the enrolment officer fill in together. They
    had no audit coverage of any kind: ``enrolment_api`` never registered
    anything, so nine ``update_or_create`` calls in ``wizard_steps.py`` wrote a
    learner's personal details, self-assessment, prior learning and policy
    acknowledgements with nothing recording that they had.

    ``ExtendedIlr.wizard_draft`` is deliberately not collected. It is a resume
    snapshot holding a copy of every step below, so recording it would write a
    second revision of the same edit on every save -- the exact duplication
    ``record_rows`` exists to avoid. ``answers`` IS collected: that is the ILR
    questionnaire itself, and no per-step table holds it.
    """
    try:
        from enrolment_api.models import (
            ExtendedIlr,
            WizardCvJob,
            WizardKsbAssessment,
            WizardPersonalDetails,
            WizardPlr,
            WizardPlrRecord,
            WizardPolicyAck,
            WizardSkillsRadar,
        )
    except Exception:
        logger.warning('Enrolment wizard records could not be registered for audit.', exc_info=True)
        return

    register_model(
        ExtendedIlr,
        workspace='admin',
        entity_type='extended_ilr',
        timestamps=ROW_TIMESTAMPS,
        label='Extended ILR',
        href='/users',
        key='id',
        title='learner_name',
        title_fallback='learner_kind',
        context=('learner_kind', 'completed'),
        parent='learner_id',
        using='enrolment',
        json_columns={'answers'},
        columns=(
            'id', 'learner_kind', 'learner_id', 'learner_name',
            'answers',
            'learner_signed', 'learner_signed_date',
            'provider_signed', 'provider_signed_date',
            'completed',
        ),
    )

    register_model(
        WizardPersonalDetails,
        workspace='admin',
        entity_type='wizard_personal_details',
        timestamps=ROW_TIMESTAMPS,
        label='Enrolment: personal details',
        href='/users',
        key='id',
        title=('first_name', 'last_name'),
        title_fallback='email',
        context=('email', 'signature_date'),
        parent='learner_id',
        using='enrolment',
        columns=(
            'id', 'learner_kind', 'learner_id',
            'first_name', 'last_name', 'email', 'signature_date',
            'phone', 'address', 'date_of_birth', 'age', 'sex',
        ),
        # The same set `learner_record` redacts, for the same reason: this table
        # is where a learner types them in the first place, so recording the
        # values here would defeat redacting them there.
        redact=('phone', 'address', 'date_of_birth', 'age', 'sex'),
    )

    register_model(
        WizardSkillsRadar,
        workspace='admin',
        entity_type='wizard_skills_radar',
        timestamps=ROW_TIMESTAMPS,
        label='Enrolment: skills radar',
        href='/users',
        key='id',
        title='standard_id',
        parent='learner_id',
        using='enrolment',
        columns=('id', 'learner_kind', 'learner_id', 'standard_id'),
    )

    register_model(
        WizardKsbAssessment,
        workspace='admin',
        entity_type='wizard_ksb_assessment',
        timestamps=ROW_TIMESTAMPS,
        label='Enrolment: KSB self-assessment',
        href='/users',
        key='id',
        title='ksb_id',
        context=('level', 'due_date'),
        parent='learner_id',
        status='level',
        using='enrolment',
        columns=(
            'id', 'learner_kind', 'learner_id', 'ksb_id',
            # The rating and the plan agreed off it. Recorded in full: this is
            # the learner's starting point, and a later dispute about how far
            # they have come is answered by what it said at the start.
            'level', 'score', 'note', 'action_text', 'action', 'goal', 'due_date',
        ),
        # `evidence_files` is left out on the module rule for evidence links: a
        # digest of a file URL is useless and the URL itself is a way in.
    )

    register_model(
        WizardPlr,
        workspace='admin',
        entity_type='wizard_plr',
        timestamps=ROW_TIMESTAMPS,
        label='Enrolment: personal learning record',
        href='/users',
        key='id',
        parent='learner_id',
        using='enrolment',
        columns=('id', 'learner_kind', 'learner_id', 'uln'),
        # As on the RPL sheet: the ULN identifies a person nationally.
        redact=('uln',),
    )

    register_model(
        WizardPlrRecord,
        workspace='admin',
        entity_type='wizard_plr_record',
        timestamps=ROW_TIMESTAMPS,
        label='Enrolment: prior qualification',
        href='/users',
        key='id',
        title=('qualification_type', 'subject'),
        title_join=' — ',
        title_fallback='record_ref',
        context=('place_of_study', 'award_date', 'grade'),
        parent='learner_id',
        using='enrolment',
        # Prior attainment in full. It is the evidence an RPL reduction rests
        # on, so a change to it is a change to how much programme a learner is
        # funded for.
        columns=(
            'id', 'learner_kind', 'learner_id', 'record_ref',
            'place_of_study', 'qualification_type', 'subject', 'level',
            'award_date', 'credits', 'grade', 'record_type',
            # Not `evidence`: its blob paths locate files rather than describe them.
            'start_date', 'end_date',
        ),
    )

    register_model(
        WizardCvJob,
        workspace='admin',
        entity_type='wizard_cv_job',
        timestamps=ROW_TIMESTAMPS,
        label='Enrolment: CV and job role',
        href='/users',
        key='id',
        context=('functional_skills_enrol',),
        parent='learner_id',
        using='enrolment',
        columns=(
            'id', 'learner_kind', 'learner_id',
            'pm_qualifications', 'functional_skills_enrol',
            'cv_file', 'experience_text',
            # Not `documents`: its blob paths locate files rather than describe them.
            'highest_qualification', 'highest_qualification_field',
            'has_field_qualification', 'highest_field_qualification',
            'gcse_english', 'gcse_maths',
        ),
        # A CV and a free-text work history are a person's own account of
        # themselves, held here only to be read during enrolment. That they were
        # changed is auditable; their contents are not.
        redact=('cv_file', 'experience_text'),
    )

    register_model(
        WizardPolicyAck,
        workspace='admin',
        entity_type='wizard_policy_ack',
        timestamps=ROW_TIMESTAMPS,
        label='Enrolment: policy acknowledgement',
        href='/users',
        key='id',
        title='policy_id',
        context=('acknowledged', 'acknowledged_at'),
        parent='learner_id',
        using='enrolment',
        columns=(
            'id', 'learner_kind', 'learner_id',
            'policy_id', 'acknowledged', 'acknowledged_at',
        ),
    )

    # Uploaded and signed enrolment documents. A raw table rather than a model:
    # `enrolment_api.documents` writes it with four raw statements and records
    # each one itself through `record_table_rows`, so there is no signal to
    # connect and nothing here but the declaration of what may be kept.
    register(
        'Enrolment_Documents',
        workspace='admin',
        entity_type='enrolment_document',
        label='Enrolment document',
        href='/users',
        key='id',
        title=('learner_name', 'doc_name'),
        title_join=' — ',
        title_fallback='doc_type',
        context=('doc_type', 'generated_at', 'signed'),
        parent='learner_id',
        status='signed',
        using='enrolment',
        columns=(
            'id', 'learner_kind', 'learner_id', 'learner_name',
            'doc_type', 'doc_name', 'size_bytes', 'signed', 'generated_at',
            # Whether each party has signed, never the drawing itself. The
            # write path reduces the signature to this boolean before it ever
            # reaches here, so the image cannot arrive even by accident.
            'learner_signed_name', 'learner_signed_at', 'learner_has_signature',
            'employer_signed_name', 'employer_signed_at', 'employer_has_signature',
        ),
    )

    for model, entity in (
        (ExtendedIlr, 'extended_ilr'),
        (WizardPersonalDetails, 'wizard_personal_details'),
        (WizardSkillsRadar, 'wizard_skills_radar'),
        (WizardKsbAssessment, 'wizard_ksb_assessment'),
        (WizardPlr, 'wizard_plr'),
        (WizardPlrRecord, 'wizard_plr_record'),
        (WizardCvJob, 'wizard_cv_job'),
        (WizardPolicyAck, 'wizard_policy_ack'),
    ):
        attach_bulk_capture(model, entity)


def register_learner_records():
    """What a learner submits: monthly reports and evidence files.

    Raw tables, written by raw SQL that records itself -- see
    ``learner_api.monthly_reports`` and ``learner_api.evidence``. Nothing is
    connected to a signal here; this is only the declaration of what those
    writes may keep.

    These belong to the ``learner`` workspace rather than to ``admin``, because
    the person who performs the action is the learner. The Coach workspace
    already covers what happens to the work afterwards.

    Not registered, deliberately, and each for a stated reason:

    * ``learner_attendance_details`` -- five raw statements in
      ``teams_attendance``, every one of them either a schema backfill or a
      write of what Microsoft Graph reported. ``versioning`` states the rule
      this follows: attendance polled from Graph is machine traffic, and
      recording it would bury the edits people actually made.
    * The reusable-signature write in ``monthly_reports`` -- it touches only the
      saved-signature columns on the learner record, and those are not
      collected, so the revision it produced would be empty by construction.
    * ``"Learner".reviews`` in ``calendar`` -- a mirror kept in step with a
      booking that is already recorded on ``coach_meeting``. Recording it too
      would report one booking as two events.
    """
    register(
        # Not a table: the attempt lives inside the learner's progress, with
        # every answer they gave. The trail records the outcome of the attempt
        # and none of the answers -- see learner_api.quizzes.record_quiz_attempt.
        'learner_quiz_attempts',
        workspace='learner',
        entity_type='quiz_attempt',
        label='Quiz attempt',
        href='/learner',
        key='id',
        title=('learner_name', 'quiz_title'),
        title_join=' — ',
        title_fallback='quiz_id',
        context=('module_title', 'week_title', 'attempt', 'result'),
        parent='learner_id',
        status='result',
        using='enrolment',
        columns=(
            'id', 'learner_kind', 'learner_id', 'learner_name', 'quiz_id', 'quiz_title',
            'module_title', 'week_title', 'attempt', 'result', 'score_percent',
            'correct_answers', 'total_questions', 'time_taken', 'submitted_at',
            'created_at', 'updated_at',
        ),
    )

    register(
        'learner_monthly_reports',
        workspace='learner',
        entity_type='monthly_report',
        label='Monthly report',
        href='/learner/monthly-reports',
        key='id',
        # Whose month. The label alone repeats across every learner that month.
        title=('learner_name', 'month_label'),
        title_join=' — ',
        title_fallback='month_key',
        context=('programme_name', 'month_key', 'status'),
        parent='learner_id',
        status='status',
        using='enrolment',
        json_columns={'summary_metrics', 'selected_ksbs'},
        columns=(
            'id', 'learner_kind', 'learner_id', 'learner_name', 'programme_name',
            'month_key', 'month_label', 'status',
            # Changes remain visible without copying private learner narratives.
            'learned_summary',
            'summary_metrics', 'selected_ksbs', 'submitted_at',
            'signed_name', 'signed_at',
        ),
        redact=('learned_summary',),
    )

    register(
        'evidence_files',
        workspace='learner',
        entity_type='evidence_file',
        label='Evidence file',
        href='/learner/evidence',
        key='id',
        title='original_filename',
        title_fallback='section_ref',
        context=('section_ref', 'status', 'uploaded_at'),
        parent='learner_id',
        status='status',
        using='enrolment',
        columns=(
            'id', 'learner_kind', 'learner_id', 'section_ref', 'component_ref',
            'progress_entry_id', 'original_filename', 'content_type', 'size_bytes',
            'status', 'scan_result', 'uploaded_by', 'uploaded_at', 'reviewed_at',
        ),
    )


def register_audit_records():
    """What an auditor corrected: overrides, annotations and evidence overlays.

    Raw tables, written by ``audit_api`` and recorded at each statement through
    ``audit_api.audit_trail`` -- which also refuses to record anything written in
    the HOURS-TEST clone, so a sandbox edit can never appear in the live trail.

    Every one of these is an upsert. The row carries ``updated_by`` and
    ``updated_at``, so it says who touched it last and nothing whatever about
    what it said before; on the evidence overlay it does not even say who. These
    are the corrections that decide which activity counts and which month it
    counts in, so "what did this say before, and who changed it" is the whole
    question.

    ``actual_hours`` is deliberately absent. It already has a stronger history of
    its own -- previous and proposed values, proposer and decider each with their
    source, a source-state snapshot, a base fingerprint that makes a stale
    approval impossible, and the rule version. See ``audit_api.audit_trail``.
    """
    register(
        'activity_annotations',
        workspace='audit',
        entity_type='activity_annotation',
        label='Activity annotation',
        href='/workspace/auditor-copy',
        key='component_id',
        title='component_id',
        context=('planned_hours', 'updated_by', 'updated_at'),
        using='audit',
        json_columns={'mapped_ksbs'},
        columns=('component_id', 'planned_hours', 'mapped_ksbs', 'updated_by', 'updated_at'),
    )

    register(
        'activity_overrides',
        workspace='audit',
        entity_type='activity_override',
        label='Activity correction',
        href='/workspace/auditor-copy',
        # Synthetic, joining the table's real composite key. See
        # `audit_api.audit_trail` for why one column is not enough.
        key='override_key',
        title='activity_id',
        # What was done to the activity -- edited, deleted -- is its state.
        context=('aptem_id', 'operation', 'updated_by'),
        parent='aptem_id',
        status='operation',
        using='audit',
        # Both parsed before diffing, so a correction to one field reads as that
        # field moving. `source_payload` is what the import said and `payload` is
        # what the auditor made of it; keeping both is what lets a reader see
        # whether a correction drifted from its source.
        json_columns={'payload', 'source_payload'},
        columns=(
            'override_key', 'aptem_id', 'activity_id', 'operation', 'payload',
            'source_payload', 'updated_by', 'updated_at',
        ),
    )

    register(
        'learner_profile_overrides',
        workspace='audit',
        entity_type='profile_override',
        label='Learner profile correction',
        href='/workspace/auditor',
        key='learner_id',
        context=('updated_by', 'updated_at'),
        parent='learner_id',
        using='audit',
        # The corrections are one merged document, so it is parsed before
        # diffing: correcting an employer name should read as the employer name
        # changing, not as the whole override being replaced.
        json_columns={'values'},
        columns=('learner_id', 'values', 'updated_by', 'updated_at'),
    )

    register(
        'learner_evidence_overrides',
        workspace='audit',
        entity_type='evidence_override',
        label='Evidence correction',
        href='/workspace/auditor',
        key='evidence_id',
        title='document_name',
        title_fallback='evidence_id',
        # The date first: moving it is what moves the hours between months.
        context=('evidence_date', 'evidence_status', 'uploaded_by'),
        parent='learner_id',
        status='evidence_status',
        using='audit',
        columns=(
            'evidence_id', 'learner_id', 'is_uploaded', 'document_name', 'component_name',
            'evidence_kind', 'evidence_status', 'evidence_date',
            'source_evidence_id', 'source_activity_id', 'source_activity_month',
            'source_activity_category', 'uploaded_by', 'updated_at',
        ),
    )


def register_engagement_records():
    """Rewards, recognition, events, clubs, points and flash cards.

    The cleanest workspace in the LMS to wire up: sixteen models, not one raw
    SQL write between them, so ORM signals plus ``attach_bulk_capture`` see
    everything without a single write path being touched.

    ``FlashCardView`` is the one model deliberately left out. It is a row per
    learner per card viewed -- the highest-volume table here by far, and machine
    traffic in everything but name. Recording it would bury the awards, claims
    and interventions people actually need to trace, for the same reason
    ``activity_trail`` excludes the learner content runner from the reading half.

    Two kinds of column are left out of the allowlists rather than redacted, on
    the rule this module applies throughout: ``Reward.image`` and
    ``Recognition.avatar_img`` are stored images, and a digest of one says
    nothing a reader of a diff could use.
    """
    try:
        from engagement_api.models import (
            AttendanceIntervention,
            Club,
            ClubMeeting,
            ClubMeetingAttendance,
            ClubMembership,
            Event,
            EventAttendance,
            EventBooking,
            FlashCard,
            FlashCardDeck,
            PointsGrant,
            PointsRule,
            Recognition,
            Reward,
            VoucherClaim,
        )
    except Exception:
        logger.warning('Engagement records could not be registered for audit.', exc_info=True)
        return

    register_model(
        Reward,
        workspace='engagement', entity_type='reward', label='Reward',
        timestamps=ROW_TIMESTAMPS,
        href='/engagement/rewards', key='id',
        title='name', context=('category', 'points', 'active'), status='category',
        columns=(
            'id', 'name', 'description', 'points', 'category', 'delivery_type',
            'stock', 'total_claimed', 'popular', 'active',
        ),
    )

    register_model(
        VoucherClaim,
        workspace='engagement', entity_type='voucher_claim', label='Voucher claim',
        href='/engagement/rewards', key='id',
        # Whose claim, for what. Neither names it alone.
        title=('learner_name', 'reward_id'), title_join=' — ', title_fallback='learner_name',
        context=('points', 'reviewed_by', 'status'),
        parent='learner_id', status='status',
        columns=(
            'id', 'learner_id', 'learner_name', 'reward_id', 'points', 'status',
            'requested_at', 'reviewed_by', 'reviewed_at',
            'delivery_type', 'delivery_method',
            'delivery_detail', 'delivery_instructions',
        ),
        # Where a voucher is being sent is a home address or a personal email.
        # That it changed is auditable; where to is not.
        redact=('delivery_detail', 'delivery_instructions'),
    )

    register_model(
        Recognition,
        workspace='engagement', entity_type='recognition', label='Recognition',
        href='/engagement/recognition', key='id',
        title='title', title_fallback='learner_name',
        context=('learner_name', 'awarded_by', 'awarded_at'),
        parent='learner_id', status='type',
        columns=(
            'id', 'learner_id', 'learner_name', 'programme_code', 'programme', 'cohort',
            'type', 'title', 'description', 'awarded_by', 'awarded_at',
            'category', 'points', 'is_public',
        ),
    )

    register_model(
        Event,
        workspace='engagement', entity_type='engagement_event', label='Event',
        timestamps=ROW_TIMESTAMPS,
        href='/engagement/events', key='id',
        title='title', context=('date', 'location', 'status'), status='status',
        columns=(
            'id', 'title', 'description', 'date', 'time', 'location', 'type',
            'event_date', 'start_time', 'end_time',
            'attendees', 'status', 'organizer',
        ),
    )

    register_model(
        EventBooking,
        workspace='engagement', entity_type='event_booking', label='Event booking',
        href='/engagement/events', key='id',
        title='learner_name', title_fallback='learner_email',
        context=('event_id', 'status', 'booked_at'),
        parent='learner_id', status='status',
        columns=(
            'id', 'event_id', 'learner_id', 'learner_name', 'learner_email',
            'status', 'booked_at', 'cancelled_at',
        ),
    )

    register_model(
        EventAttendance,
        workspace='engagement', entity_type='event_attendance', label='Event attendance',
        href='/engagement/events', key='id',
        title='learner_name',
        context=('event_id', 'status', 'marked_by'),
        parent='learner_id', status='status',
        columns=(
            'id', 'event_id', 'learner_id', 'learner_name',
            'attendee_email', 'attendee_type', 'attendance_source',
            'status', 'marked_by', 'marked_at',
        ),
        redact=('attendee_email',),
    )

    register_model(
        Club,
        workspace='engagement', entity_type='club', label='Club',
        href='/engagement/clubs', key='id',
        title='name', context=('location', 'ambassador', 'members'),
        json_columns={'sample_members'},
        columns=(
            'id', 'name', 'location', 'description',
            'ambassador', 'ambassador_role', 'members', 'sample_members', 'active',
        ),
    )

    register_model(
        ClubMeeting,
        workspace='engagement', entity_type='club_meeting', label='Club meeting',
        href='/engagement/clubs', key='id',
        title='title', context=('date', 'venue', 'scheduled'),
        parent='club_id',
        columns=('id', 'club_id', 'title', 'scheduled', 'date', 'time', 'venue', 'attendees'),
    )

    register_model(
        ClubMembership,
        workspace='engagement', entity_type='club_membership', label='Club membership',
        href='/engagement/clubs', key='id',
        title='learner_name',
        context=('club_id', 'status', 'assigned_by'),
        parent='learner_id', status='status',
        columns=(
            'id', 'club_id', 'learner_id', 'learner_name',
            'status', 'assigned_by', 'assigned_at',
        ),
    )

    register_model(
        ClubMeetingAttendance,
        workspace='engagement', entity_type='club_meeting_attendance',
        label='Club meeting attendance',
        href='/engagement/clubs', key='id',
        title='learner_name',
        context=('meeting_id', 'status', 'marked_by'),
        parent='learner_id', status='status',
        columns=(
            'id', 'meeting_id', 'learner_id', 'learner_name',
            'status', 'marked_by', 'marked_at',
        ),
    )

    register_model(
        AttendanceIntervention,
        workspace='engagement', entity_type='attendance_intervention',
        label='Attendance intervention',
        href='/engagement/attendance', key='id',
        title='learner_name', title_fallback='action',
        # Whether the employer was told is the fact most often asked for after
        # the event, so it is on the line rather than buried in the diff.
        context=('intervention_date', 'employer_notified', 'resolved'),
        parent='learner_id',
        columns=(
            'id', 'learner_id', 'learner_name', 'action',
            'employer_notified', 'intervention_date',
            'created_by', 'created_at', 'resolved', 'resolved_at',
        ),
    )

    register_model(
        PointsRule,
        workspace='engagement', entity_type='points_rule', label='Points rule',
        timestamps=ROW_TIMESTAMPS,
        href='/engagement/points', key='id',
        title='name', title_fallback='key',
        context=('category', 'points', 'active'), status='category',
        columns=(
            'id', 'name', 'description', 'points', 'category',
            'frequency', 'trigger', 'active', 'key',
        ),
    )

    register_model(
        PointsGrant,
        workspace='engagement', entity_type='points_grant', label='Points award',
        href='/engagement/points', key='id',
        title='learner_name', title_fallback='reason',
        context=('points', 'awarded_by', 'awarded_at'),
        parent='learner_id', status='source_type',
        columns=(
            'id', 'rule_id', 'learner_id', 'learner_name', 'points', 'awarded_at',
            'event_reference', 'awarded_by', 'source_type', 'source_id', 'reason',
        ),
    )

    register_model(
        FlashCardDeck,
        workspace='engagement', entity_type='flash_card_deck', label='Flash card deck',
        timestamps=ROW_TIMESTAMPS,
        href='/engagement/flash-cards', key='id',
        title='title',
        context=('programme', 'module', 'status'),
        parent='programme_id', status='status',
        columns=(
            'id', 'title', 'programme_id', 'programme', 'module', 'week_id',
            'status', 'author', 'card_count', 'ai_generated',
        ),
    )

    register_model(
        FlashCard,
        workspace='engagement', entity_type='flash_card', label='Flash card',
        timestamps=ROW_TIMESTAMPS,
        href='/engagement/flash-cards', key='id',
        title='question',
        context=('category', 'difficulty'),
        parent='deck_id',
        columns=(
            'id', 'deck_id', 'question', 'answer',
            'category', 'difficulty', 'sort_order',
        ),
    )

    for model, entity in (
        (Reward, 'reward'),
        (VoucherClaim, 'voucher_claim'),
        (Recognition, 'recognition'),
        (Event, 'engagement_event'),
        (EventBooking, 'event_booking'),
        (EventAttendance, 'event_attendance'),
        (Club, 'club'),
        (ClubMeeting, 'club_meeting'),
        (ClubMembership, 'club_membership'),
        (ClubMeetingAttendance, 'club_meeting_attendance'),
        (AttendanceIntervention, 'attendance_intervention'),
        (PointsRule, 'points_rule'),
        (PointsGrant, 'points_grant'),
        (FlashCardDeck, 'flash_card_deck'),
        (FlashCard, 'flash_card'),
    ):
        attach_bulk_capture(model, entity)


def register_manual_audit_records():
    """The manual audit: its plan's own event log, and the auditor's overrides.

    Two different things, and the difference is the point.

    ``plan_events`` is registered because the plan already logs itself
    properly -- ``plan_tables.log_plan_event`` writes the entity, the action,
    the value before, the value after and the actor, from ten call sites
    covering group creation and archiving, membership, months, activities and
    exemptions. Surfacing that log is strictly better than re-deriving the same
    events from the six plan tables it describes, which would produce a second
    and vaguer account of each one.

    The overrides are registered because they log nothing. Each is an upsert
    carrying only ``updated_by`` and ``updated_at``, and between them they set a
    learner's hours, the dates that decide which month their evidence counts in,
    and whether a month is signed off at all.

    Both hours tables keep their real composite key and gain a synthetic one for
    the trail to index by; ``manual_audit_api.audit_trail`` explains why, and
    what would have gone wrong without it.
    """
    register(
        'plan_events',
        workspace='audit',
        entity_type='manual_plan_event',
        label='Audit plan change',
        href='/workspace/auditor-manual',
        key='id',
        # "group - membership", "activity - excluded": the two together are what
        # the event actually was.
        title=('entity_type', 'action'),
        title_join=' — ',
        title_fallback='action',
        context=('entity_id', 'actor', 'at'),
        parent='entity_id',
        status='action',
        using='audit',
        # Parsed before diffing, so the trail can show which field of the plan
        # moved rather than "the event has a payload".
        json_columns={'old_value', 'new_value'},
        columns=(
            'id', 'entity_type', 'entity_id', 'action',
            'old_value', 'new_value', 'actor', 'at',
        ),
    )

    register(
        'manual_learner_hours_overrides',
        workspace='audit',
        entity_type='manual_hours_override',
        label='Manual hours override',
        href='/workspace/auditor-manual',
        key='override_key',
        title='aptem_id',
        # The period first: an override belongs to one month, and which month is
        # the first thing a reader needs to place it.
        context=('period', 'updated_by', 'updated_at'),
        parent='aptem_id',
        using='audit',
        columns=(
            'override_key', 'aptem_id', 'period',
            'planned_hours', 'actual_hours', 'not_accepted_hours',
            'updated_by', 'updated_at',
        ),
    )

    register(
        'manual_learner_profile_date_overrides',
        workspace='audit',
        entity_type='manual_date_override',
        label='Manual date override',
        href='/workspace/auditor-manual',
        key='aptem_id',
        title='aptem_id',
        context=('start_date', 'planned_end_date', 'updated_by'),
        parent='aptem_id',
        using='audit',
        columns=(
            'aptem_id', 'start_date', 'first_evidence_date', 'planned_end_date',
            'updated_by', 'updated_at',
        ),
    )

    register(
        'monthly_audit_signoffs',
        workspace='audit',
        entity_type='manual_signoff',
        label='Monthly audit sign-off',
        href='/workspace/auditor-manual',
        key='signoff_key',
        # Who signed and in what capacity. The month is in the context line.
        title=('signer_role', 'signer_name'),
        title_join=' — ',
        title_fallback='signer_role',
        context=('report_month', 'signer_name', 'signed_at'),
        parent='learner_id',
        status='signer_role',
        using='audit',
        # `signature_data` is not collected: it is the drawn signature, and who
        # signed, whether they confirmed the review and when are all here
        # already. `snapshot_hash` and `audit_version` are, because they say
        # *what* was signed off -- a sign-off whose subject cannot be identified
        # is not evidence of anything.
        columns=(
            'signoff_key', 'learner_id', 'programme_key', 'report_month', 'signer_role',
            'signer_name', 'review_confirmed', 'signed_at', 'snapshot_hash',
            'audit_version', 'updated_at',
        ),
    )


def register_quiz_records():
    """Quizzes, their questions and their answers.

    Registered under ``curriculum`` rather than a workspace of their own,
    because that is what they are: the tables live in the ``curriculum`` schema
    and a quiz is authored beside the module it belongs to. The Curriculum
    trail was already answering "who changed this component"; it can now answer
    "who changed the answer that made this question right".

    ``uploaded_file`` is not collected. It is the storage path of the imported
    package, which is a way to fetch the file rather than a fact about the quiz,
    and ``file_name`` and ``file_size`` already say which import a version came
    from.
    """
    try:
        from quiz_api.models import QuizAnswer, QuizPackage, QuizQuestion
    except Exception:
        logger.warning('Quiz records could not be registered for audit.', exc_info=True)
        return

    register_model(
        QuizPackage,
        workspace='curriculum', entity_type='quiz', label='Quiz',
        timestamps=ROW_TIMESTAMPS,
        href='/curriculum/quizzes', key='id',
        title='title',
        context=('programme', 'module', 'status'),
        parent='programme_id', status='status',
        columns=(
            'id', 'title', 'programme_id', 'programme', 'module', 'week_id', 'version',
            'questions', 'default_question_type', 'assessment_type',
            'status', 'package_type', 'file_name', 'file_size',
            'schema_valid', 'validation_message', 'mapped_components',
            'author', 'linked_courses', 'short_description', 'lesson_content',
            'duration', 'time_unit',
            # How the quiz behaves for a learner. Every one of these changes what
            # a score means, so a grade dispute is answered by what they said at
            # the time rather than by what they say now.
            'quiz_style', 'randomize_questions', 'randomize_answers',
            'show_correct_answer', 'attempt_history', 'retake_after_pass',
            'limit_attempts', 'passing_grade', 'retake_points_cut',
            'published_at',
        ),
    )

    register_model(
        QuizQuestion,
        workspace='curriculum', entity_type='quiz_question', label='Quiz question',
        timestamps=ROW_TIMESTAMPS,
        href='/curriculum/quizzes', key='id',
        title='question_text',
        context=('question_type', 'points', 'is_archived'),
        parent='quiz_id', status='question_type',
        columns=(
            'id', 'quiz_id', 'question_text', 'question_type', 'points',
            'sort_order', 'explanation', 'is_archived',
        ),
    )

    register_model(
        QuizAnswer,
        workspace='curriculum', entity_type='quiz_answer', label='Quiz answer',
        timestamps=ROW_TIMESTAMPS,
        href='/curriculum/quizzes', key='id',
        title='answer_text',
        # Whether this is the right answer is the whole point of the record, so
        # it is on the line rather than only in the diff.
        context=('is_correct', 'sort_order'),
        parent='question_id',
        columns=('id', 'question_id', 'answer_text', 'is_correct', 'sort_order'),
    )

    for model, entity in (
        (QuizPackage, 'quiz'),
        (QuizQuestion, 'quiz_question'),
        (QuizAnswer, 'quiz_answer'),
    ):
        attach_bulk_capture(model, entity)


def register_chat_records():
    """Coach-learner private messages.

    ``ChatCoach`` and ``ChatLearner`` are read-only views over
    ``enrolment."Staff_users"`` and ``"Learner"."learners"`` -- the same rows
    ``learner_record`` and ``staff_record`` already audit under their own
    identity. Registering them again here would file the same save under two
    entity types, so only the conversation and its messages are registered.

    ``body`` is redacted. It is a private message between one coach and one
    learner, which is a stronger case for redaction than anything else in this
    module: that a message was sent, edited or deleted is auditable; its
    content is a private conversation and is not copied into a second,
    less-guarded table to make that point.

    ``MessageReceipt`` (delivered/read state) is deliberately not registered --
    it is written on every read receipt a client reports, which is delivery
    telemetry rather than an action, on the same rule that excludes Teams
    attendance polling.
    """
    try:
        from chat.models import Conversation, Message, MessageDeletion
    except Exception:
        logger.warning('Chat records could not be registered for audit.', exc_info=True)
        return

    register_model(
        Conversation,
        workspace='platform', entity_type='chat_conversation', label='Conversation',
        timestamps=ROW_TIMESTAMPS,
        href='/messages', key='id',
        parents=('coach_id', 'learner_id'),
        columns=('id', 'coach_id', 'learner_id'),
    )

    register_model(
        Message,
        workspace='platform', entity_type='chat_message', label='Chat message',
        href='/messages', key='id',
        context=('sender_type', 'is_deleted'),
        parent='conversation_id', status='sender_type',
        columns=(
            'id', 'conversation_id', 'sender_type', 'sender_coach_id', 'sender_learner_id',
            'body', 'created_at', 'edited_at', 'is_deleted',
        ),
        redact=('body',),
    )

    register_model(
        MessageDeletion,
        workspace='platform', entity_type='chat_message_deletion', label='Message hidden',
        href='/messages', key='id',
        context=('participant_type', 'participant_id'),
        parent='message_id',
        columns=('id', 'message_id', 'participant_type', 'participant_id', 'deleted_at'),
    )

    for model, entity in (
        (Conversation, 'chat_conversation'),
        (Message, 'chat_message'),
        (MessageDeletion, 'chat_message_deletion'),
    ):
        attach_bulk_capture(model, entity)


def register_progress_review_records():
    """Progress review pack generation runs.

    ``progress_reviews_api`` generates a 12-week progress-review PPTX pack for
    a learner, tracked as one row per attempt in
    ``"Learner"."progress_review_runs"`` -- ``generation_status`` moves
    pending/running to completed/failed, and ``generated_by`` is the coach or
    admin who triggered it. Recorded here for the same reason a coach meeting
    is: who generated a review pack, when, and whether it succeeded is
    something a reader of the trail asks about a learner's record.

    Two sibling tables are deliberately not registered:

    * ``progress_review_source_snapshots`` -- the full input pack frozen at
      generation time, one row per run. It is never edited after being written,
      so recording it would add one large, unchanging blob to history per run
      for no diff it could ever show.
    * ``progress_review_pptx_files`` -- the generated file's Azure location and
      size. A storage pointer, on the rule this module applies to every one:
      a fact about where to fetch a thing, not a fact about the thing.
    """
    register(
        'progress_review_runs',
        workspace='coach',
        entity_type='progress_review_run',
        label='Progress review pack',
        href='/coach/progress-reviews',
        key='id',
        title='review_number',
        title_fallback='review_date',
        context=('review_date', 'generated_by', 'generation_status'),
        parent='learner_id',
        status='generation_status',
        using='enrolment',
        json_columns={'errors', 'source_warnings'},
        columns=(
            'id', 'learner_kind', 'learner_id', 'review_number', 'review_date',
            'review_period_start', 'review_period_end',
            'action_period_start', 'action_period_end',
            'generation_status', 'errors', 'source_warnings',
            'generated_by', 'generated_at',
        ),
    )
