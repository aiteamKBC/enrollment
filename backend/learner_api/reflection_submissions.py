"""Persist the complete seven-step learner reflection for tutor review."""

import json
import math
import logging
import uuid
from datetime import date

from django.db import DatabaseError, connections, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from login.permissions import learner_self_or_admin, learner_self_or_staff
from .assignment_attempts import HISTORY_KEY, preserve_attempts, submission_attempts
from .submission_audit import SUBMISSION_AUDIT_SQL, record_submission_row

logger = logging.getLogger(__name__)

VALID_KINDS = {"commercial", "apprenticeship"}


def _error(message, status=400):
    return JsonResponse({"error": message}, status=status)


def _text(value):
    return str(value or "").strip()


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _activity_title(payload, assignment_topic):
    """The coach's queue title: "Title - Topic 2: Name", without an empty name."""
    title = _text(payload.get("activityTitle"))
    if not assignment_topic:
        return title
    name = _text(payload.get("assignmentTopicName"))
    return f"{title} - Topic {assignment_topic}" + (f": {name}" if name else "")


def _component_ksb_weights(component_ref):
    component_ref = _text(component_ref)
    if not component_ref:
        return {}
    try:
        with connections["enrolment"].cursor() as cur:
            cur.execute("select ksb_mappings from curriculum.components where id = %s limit 1", [component_ref])
            row = cur.fetchone()
    except DatabaseError:
        return {}
    mappings = row[0] if row else []
    if isinstance(mappings, str):
        try:
            mappings = json.loads(mappings) if mappings else []
        except (TypeError, ValueError):
            mappings = []
    weights = {}
    if isinstance(mappings, list):
        for item in mappings:
            if not isinstance(item, dict):
                continue
            code = _text(item.get("code") or item.get("ksbCode") or item.get("ksb_code")).upper()
            if not code:
                continue
            try:
                weights[code] = float(item.get("weight") or 0)
            except (TypeError, ValueError):
                weights[code] = 0
    return weights


def _learner_profile_ids_for_source(cur, learner_id):
    learner_id = _text(learner_id)
    if not learner_id:
        return []

    cur.execute('select id::text from "Learner".learners where enrolment_id::text = %s', [learner_id])
    linked = [_text(row[0]) for row in cur.fetchall() if row and _text(row[0])]
    if linked:
        return sorted(set(linked))
    email = ""
    cur.execute('select "Email" from enrolment."Created_users" where id::text = %s limit 1', [learner_id])
    row = cur.fetchone()
    if row:
        email = _text(row[0])

    if email:
        cur.execute(
            """
            select id::text
              from "Learner".learners
             where enrolment_id is null
               and lower(email) = lower(%s)
               and not exists (
                   select 1 from enrolment."Created_users" other
                    where other.id::text <> %s and lower(other."Email") = lower(%s)
               )
            """,
            [email, learner_id, email],
        )
    else:
        cur.execute('select id::text from "Learner".learners where id::text = %s and enrolment_id is null', [learner_id])
    ids = {_text(row[0]) for row in cur.fetchall() if row and _text(row[0])}
    return sorted(ids) if len(ids) == 1 else []


def _reflection_lineage(learner_id, activity_id):
    component_ref = _text(activity_id)
    lineage = {
        "progress_entry_id": None,
        "component_ref": "",
        "programme_ref": "",
        "cohort_ref": "",
        "group_ref": "",
        "module_ref": "",
        "week_ref": "",
    }
    if not component_ref:
        return lineage
    try:
        with connections["enrolment"].cursor() as cur:
            cur.execute("select id from curriculum.components where id = %s limit 1", [component_ref])
            if cur.fetchone():
                lineage["component_ref"] = component_ref
            learner_ids = _learner_profile_ids_for_source(cur, learner_id)
            if not learner_ids:
                return lineage
            cur.execute(
                """
                select id, component_ref, programme_ref, cohort_ref, group_ref, module_ref, week_ref
                  from "Learner"."learner_progress_entries"
                 where learner_id::text = any(%s)
                   and component_ref = %s
                 order by submitted_at desc nulls last, id desc
                 limit 1
                """,
                [learner_ids, component_ref],
            )
            row = cur.fetchone()
    except DatabaseError:
        return lineage
    if row:
        lineage.update({
            "progress_entry_id": row[0],
            "component_ref": _text(row[1]) or lineage["component_ref"],
            "programme_ref": _text(row[2]),
            "cohort_ref": _text(row[3]),
            "group_ref": _text(row[4]),
            "module_ref": _text(row[5]),
            "week_ref": _text(row[6]),
        })
    return lineage


@csrf_exempt
# READ and WRITE need different authorization, so this single-URL view splits by
# method. A learner may only WRITE their own reflection (POST, self-only, learner
# id in the body); the owner OR staff may READ one (GET, id in the query — staff
# review a learner's reflections and coach feedback). Before M2 the GET relied on
# the gate's open-safe-methods bypass; that is now closed (A5), so the read is
# gated explicitly on get_reflection_submission and the write on _submit_reflection.
def create_reflection_submission(request):
    if request.method == "GET":
        return get_reflection_submission(request)
    return _submit_reflection(request)


@learner_self_or_admin(body_field="learnerId")
def _submit_reflection(request):
    if request.method != "POST":
        return _error("Method not allowed.", 405)

    try:
        payload = json.loads(request.body or b"{}")
    except (TypeError, ValueError):
        return _error("Request body must be valid JSON.")

    if not isinstance(payload, dict):
        return _error("Request body must be a JSON object.")

    learner_kind = _text(payload.get("learnerKind"))
    learner_id = _text(payload.get("learnerId"))
    activity_type = _text(payload.get("activityType"))
    activity_id = _text(payload.get("activityId"))
    learning_reflection = _text(payload.get("learningReflection"))
    submission_mode = "draft" if _text(payload.get("submissionMode")).lower() == "draft" else "submit"
    if submission_mode == "submit":
        # Drafts may be saved before the cohort starts; submitting waits for it.
        # The API gate lets this path through for drafts, so the submit half is
        # refused here, before anything is written.
        from .programme_access import submission_refusal
        refused = submission_refusal(getattr(request, "login_account", None))
        if refused is not None:
            return refused
    is_extra_activity = activity_type == "extra_activity"
    is_assignment_form = activity_type.lower() in ("assignment", "extra_activity")
    if is_extra_activity:
        from .extra_activities import prepare_extra_activity
        try:
            payload = prepare_extra_activity(payload)
        except ValueError as exc:
            return _error(str(exc))
    assignment_answer = _text(payload.get("assignmentAnswer"))
    what_you_learned = _text(payload.get("whatYouLearned"))
    business_impact = _text(payload.get("businessImpact"))

    if learner_kind not in VALID_KINDS:
        return _error("A valid learnerKind is required.")
    if not learner_id:
        return _error("learnerId is required.")
    if not activity_type or not activity_id:
        return _error("activityType and activityId are required.")
    if activity_type == 'assignment' and activity_id.startswith('aptem:'):
        return _error('Classified historical assignments are read-only.', 409)
    if submission_mode != "draft":
        if is_assignment_form:
            missing = []
            if not assignment_answer:
                missing.append("assignmentAnswer")
            if not what_you_learned:
                missing.append("whatYouLearned")
            if not business_impact and not is_extra_activity:
                missing.append("businessImpact")
            if missing:
                return _error("Complete all assignment sections before submitting: " + ", ".join(missing) + ".")
        elif not learning_reflection:
            return _error("learningReflection is required.")

    # The existing column is non-null and remains the searchable summary for
    # every reflection-like submission. Assignment forms keep their complete
    # versioned monthly payload in full_submission and use the learning answer here.
    if is_assignment_form:
        learning_reflection = what_you_learned or assignment_answer
        if is_extra_activity:
            monthly = _dict(payload.get('monthlyAssignment'))
            learning_reflection = "\n\n".join([
                "Activity answer:\n" + assignment_answer, "What I learned:\n" + what_you_learned,
                "What I understood:\n" + _text(monthly.get('understood')),
                "Skills I gained:\n" + _text(monthly.get('gainedSkills')),
            ])
    submission_status = "draft" if submission_mode == "draft" else "submitted_for_tutor_review"

    raw_date = _text(payload.get("dateCompleted"))
    try:
        date_completed = date.fromisoformat(raw_date) if raw_date else None
    except ValueError:
        return _error("dateCompleted must use YYYY-MM-DD format.")

    quality_score = payload.get("qualityScore", 0)
    try:
        quality_score = max(0, min(100, int(quality_score)))
    except (TypeError, ValueError):
        quality_score = 0

    submission_id = uuid.uuid4()
    from .assignment_topics import topic_id
    try:
        assignment_topic = topic_id(payload.get("assignmentTopicId"))
    except ValueError as exc:
        return _error(str(exc))
    if "assignmentElapsedSeconds" in payload:
        try:
            elapsed = float(payload["assignmentElapsedSeconds"])
            if not math.isfinite(elapsed) or elapsed < 0:
                raise ValueError()
            payload["assignmentElapsedSeconds"] = int(elapsed)
        except (TypeError, ValueError, OverflowError):
            return _error("assignmentElapsedSeconds must be a non-negative number.")
    full_submission = dict(payload)
    full_submission.pop("selectAssignmentTopic", None)
    lineage = _reflection_lineage(learner_id, activity_id)
    if assignment_topic and submission_mode == "draft":
        # An optional draft must never claim the previous topic\'s progress entry.
        lineage["progress_entry_id"] = None
    ksb_weights = _dict(payload.get("ksbWeights"))
    if not ksb_weights and lineage.get("component_ref"):
        ksb_weights = _component_ksb_weights(lineage["component_ref"])
    full_submission["ksbWeights"] = ksb_weights

    try:
        with transaction.atomic(using="enrolment"):
            with connections["enrolment"].cursor() as cur:
                if activity_type == "assignment":
                    from .assignment_hours import lock_assignment_hours
                    lock_assignment_hours(cur, learner_kind, learner_id)
                if assignment_topic:
                    from .assignment_topics import prepare_topic_save
                    prepare_topic_save(cur, payload)
                    full_submission.update({key: payload[key] for key in ("assignmentTopicId", "assignmentTopicName", "assignmentQuestion", "monthlyAssignment") if key in payload})
                cur.execute(
                    """
                    select status, full_submission, coach_feedback, reviewed_by, reviewed_at, submitted_at
                    from "Learner"."learning_reflection_submissions"
                    where learner_kind = %s
                      and learner_id = %s
                      and activity_type = %s
                      and activity_id = %s
                      and assignment_topic_id = %s
                    for update
                    """,
                    [learner_kind, learner_id, activity_type, activity_id, assignment_topic],
                )
                existing = cur.fetchone()
                # Provenance is server-owned. A learner cannot bypass the new
                # checks by posting an import flag or by omitting version 2.
                stored = existing[1] if existing else {}
                if isinstance(stored, str):
                    try:
                        stored = json.loads(stored)
                    except (TypeError, ValueError):
                        stored = {}
                stored = _dict(stored)
                imported = stored.get("submissionOrigin") == "imported_legacy"
                full_submission["submissionOrigin"] = "imported_legacy" if imported else "learner"
                if is_assignment_form and imported:
                    return _error("Imported assignments are historical records and cannot be overwritten here.", 409)
                if existing and existing[0] == "accepted":
                    return _error(
                        "This reflection has been accepted by the coach and can no longer be changed.",
                        409,
                    )
                if (
                    existing
                    and existing[0] == "submitted_for_tutor_review"
                    and is_assignment_form
                ):
                    return _error(
                        "This assignment has already been submitted for tutor review.",
                        409,
                    )

                if assignment_topic and existing and existing[0] == "partial":
                    return _error("This topic has already been reviewed and is locked.", 409)
                if assignment_topic and existing and payload.get("selectAssignmentTopic") is True:
                    return JsonResponse({"status": existing[0], "assignmentTopicId": assignment_topic})

                if is_assignment_form:
                    preserve_attempts(
                        full_submission, stored,
                        status=existing[0] if existing else None,
                        feedback=existing[2] if existing else None,
                        reviewer=existing[3] if existing else None,
                        reviewed_at=existing[4] if existing else None,
                        submitted_at=existing[5] if existing else None,
                    )

                if activity_type == "assignment":
                    from .assignment_hours import validate_saved_daily_hours
                    monthly = _dict(payload.get("monthlyAssignment"))
                    previous_monthly = _dict(stored.get("monthlyAssignment"))
                    # An omitted field must not release a saved daily claim.
                    # New forms require dated entries before final submission;
                    # existing aggregate-only legacy forms retain their data.
                    if "timeEntries" not in monthly and ("timeEntries" in previous_monthly or not existing or assignment_topic):
                        monthly = {**monthly, "timeEntries": previous_monthly.get("timeEntries", [])}
                        payload["monthlyAssignment"] = monthly
                        full_submission["monthlyAssignment"] = monthly
                    validate_saved_daily_hours(cur, payload)

                if is_assignment_form and submission_mode == "submit":
                    from .monthly_assignment import assignment_checks
                    if is_extra_activity:
                        from .extra_activities import extra_activity_checks
                        checks = extra_activity_checks(payload)
                    else:
                        checks = assignment_checks(payload)
                    missing_checks = [check["label"] for check in checks if not check["passed"]]
                    if missing_checks:
                        return JsonResponse({"error": "Complete the outstanding submission requirements. Your draft is saved.", "checks": checks}, status=400)
                    quality_score = 100
                    full_submission["qualityScore"] = quality_score
                    full_submission["qualityChecks"] = checks

                cur.execute(
                """
                insert into "Learner"."learning_reflection_submissions" (
                    id, learner_kind, learner_id, learner_name, programme_name,
                    activity_type, activity_id, activity_title, module_title,
                    week_title, planned_otjh, status, learning_reflection, ksb_codes,
                    ksb_weights, ksb_explanations, confidence_before, confidence_after,
                    application_type, application_text, evidence_files,
                    evidence_consent_confirmed, selected_benefits,
                    benefit_explanation, actual_time_hours,
                    completed_during_paid_hours, date_completed, otjh_confirmed,
                    signed_declaration, quality_score, full_submission,
                    progress_entry_id, component_ref, programme_ref, cohort_ref,
                    group_ref, module_ref, week_ref, assignment_topic_id
                ) values (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s::jsonb,
                    %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                    %s, %s, %s::jsonb,
                    %s, %s::jsonb,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s, %s::jsonb,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                on conflict (learner_kind, learner_id, activity_type, activity_id, assignment_topic_id)
                do update set
                    learner_name = excluded.learner_name,
                    programme_name = excluded.programme_name,
                    activity_title = excluded.activity_title,
                    module_title = excluded.module_title,
                    week_title = excluded.week_title,
                    planned_otjh = excluded.planned_otjh,
                    status = excluded.status,
                    learning_reflection = excluded.learning_reflection,
                    ksb_codes = excluded.ksb_codes,
                    ksb_weights = excluded.ksb_weights,
                    ksb_explanations = excluded.ksb_explanations,
                    confidence_before = excluded.confidence_before,
                    confidence_after = excluded.confidence_after,
                    application_type = excluded.application_type,
                    application_text = excluded.application_text,
                    evidence_files = excluded.evidence_files,
                    evidence_consent_confirmed = excluded.evidence_consent_confirmed,
                    selected_benefits = excluded.selected_benefits,
                    benefit_explanation = excluded.benefit_explanation,
                    actual_time_hours = excluded.actual_time_hours,
                    completed_during_paid_hours = excluded.completed_during_paid_hours,
                    date_completed = excluded.date_completed,
                    otjh_confirmed = excluded.otjh_confirmed,
                    signed_declaration = excluded.signed_declaration,
                    quality_score = excluded.quality_score,
                    full_submission = excluded.full_submission,
                    progress_entry_id = excluded.progress_entry_id,
                    component_ref = excluded.component_ref,
                    programme_ref = excluded.programme_ref,
                    cohort_ref = excluded.cohort_ref,
                    group_ref = excluded.group_ref,
                    module_ref = excluded.module_ref,
                    week_ref = excluded.week_ref,
                    coach_feedback = CASE WHEN excluded.activity_type IN ('assignment', 'extra_activity') THEN NULL ELSE learning_reflection_submissions.coach_feedback END,
                    reviewed_by = CASE WHEN excluded.activity_type IN ('assignment', 'extra_activity') THEN NULL ELSE learning_reflection_submissions.reviewed_by END,
                    reviewed_at = CASE WHEN excluded.activity_type IN ('assignment', 'extra_activity') THEN NULL ELSE learning_reflection_submissions.reviewed_at END,
                    submitted_at = now()
                returning """ + SUBMISSION_AUDIT_SQL + """
                """,
                    [
                    str(submission_id),
                    learner_kind,
                    learner_id,
                    _text(payload.get("learnerName")),
                    _text(payload.get("programmeName")),
                    activity_type,
                    activity_id,
                    _activity_title(payload, assignment_topic),
                    _text(payload.get("moduleTitle")),
                    _text(payload.get("weekTitle")),
                    _text(payload.get("plannedOtjh")),
                    submission_status,
                    learning_reflection,
                    json.dumps(_list(payload.get("ksbCodes"))),
                    json.dumps(ksb_weights),
                    json.dumps(_dict(payload.get("ksbExplanations"))),
                    json.dumps(_dict(payload.get("confidenceBefore"))),
                    json.dumps(_dict(payload.get("confidenceAfter"))),
                    _text(payload.get("applicationType")),
                    _text(payload.get("applicationText")),
                    json.dumps(_list(payload.get("evidenceFiles"))),
                    bool(payload.get("evidenceConsentConfirmed")),
                    json.dumps(_list(payload.get("selectedBenefits"))),
                    _text(payload.get("benefitExplanation")),
                    _text(payload.get("actualTimeHours")),
                    _text(payload.get("completedDuringPaidHours")),
                    date_completed,
                    bool(payload.get("otjhConfirmed")),
                    bool(payload.get("signedDeclaration")),
                    quality_score,
                    json.dumps(full_submission),
                    lineage.get("progress_entry_id"),
                    lineage.get("component_ref") or None,
                    lineage.get("programme_ref") or None,
                    lineage.get("cohort_ref") or None,
                    lineage.get("group_ref") or None,
                    lineage.get("module_ref") or None,
                    lineage.get("week_ref") or None,
                    assignment_topic,
                    ],
                )
                stored = cur.fetchone()
                # `id` is the first audited column, so this read is unchanged
                # by widening the clause.
                stored_id = stored[0]
                record_submission_row(stored)
    except ValueError as exc:
        return _error(str(exc), 409)
    except DatabaseError:
        logger.exception("Could not save learner reflection submission.")
        return _error("Could not save the reflection for tutor review.", 502)

    return JsonResponse(
        {
            "id": str(stored_id),
            "status": submission_status,
            "submissionAttempts": submission_attempts(full_submission, submission_status) if is_assignment_form else [],
        },
        status=201,
    )


# Owner or staff may read a reflection (A5); the learner id is in the query here.
@learner_self_or_staff(query_param="learnerId")
def get_reflection_submission(request):
    learner_kind = _text(request.GET.get("learnerKind"))
    learner_id = _text(request.GET.get("learnerId"))
    activity_type = _text(request.GET.get("activityType"))
    activity_id = _text(request.GET.get("activityId"))

    if learner_kind not in VALID_KINDS:
        return _error("A valid learnerKind is required.")
    if not learner_id:
        return _error("learnerId is required.")
    if bool(activity_type) != bool(activity_id):
        return _error("activityType and activityId must be provided together.")

    from .assignment_topics import topic_id, aggregate_statuses
    from .submission_visibility import VISIBLE_SUBMISSION_SQL
    try:
        selected_topic = topic_id(request.GET.get("assignmentTopicId"))
    except ValueError as exc:
        return _error(str(exc))

    try:
        if ((not activity_type and request.GET.get('view') == 'assignments')
                or (activity_type == 'assignment' and activity_id.startswith('aptem:'))):
            from .legacy_assignments import classified_response
            return classified_response(learner_kind, learner_id, activity_id)
        with connections["enrolment"].cursor() as cur:
            if activity_type == "assignment" and request.GET.get("view") == "topics":
                cur.execute(f'''SELECT assignment_topic_id, status, full_submission, submitted_at
                    FROM "Learner".learning_reflection_submissions AS submission
                    WHERE learner_kind=%s AND learner_id=%s AND activity_type=\'assignment\' AND activity_id=%s
                      AND {VISIBLE_SUBMISSION_SQL}
                    ORDER BY assignment_topic_id''', [learner_kind, learner_id, activity_id])
                states = []
                for identity, status, content, submitted_at in cur.fetchall():
                    content = json.loads(content) if isinstance(content, str) else content or {}
                    states.append({"topicId": identity, "status": status, "elapsedSeconds": content.get("assignmentElapsedSeconds", 0),
                                   "month": _dict(content.get("monthlyAssignment")).get("month"),
                                   "meetingKey": _dict(content.get("monthlyAssignment")).get("meetingKey") or "",
                                   "submittedAt": submitted_at.isoformat() if submitted_at else None})
                return JsonResponse({"topics": states})
            if not activity_type:
                cur.execute(
                    f"""
                    select activity_type, activity_id, status,
                           full_submission -> 'assignmentAttemptHistory'
                    from "Learner"."learning_reflection_submissions" AS submission
                    where learner_kind = %s and learner_id = %s
                      and {VISIBLE_SUBMISSION_SQL}
                    """,
                    [learner_kind, learner_id],
                )
                return JsonResponse(
                    {
                        "statuses": aggregate_statuses(cur.fetchall())
                    }
                )

            cur.execute(
                f"""
                select id, status, full_submission, coach_feedback,
                       reviewed_by, reviewed_at, submitted_at
                from "Learner"."learning_reflection_submissions" AS submission
                where learner_kind = %s
                  and learner_id = %s
                  and activity_type = %s
                  and activity_id = %s
                  and (%s::text IS NULL OR assignment_topic_id = %s)
                  and {VISIBLE_SUBMISSION_SQL}
                order by case status when \'accepted\' then 0 when \'submitted_for_tutor_review\' then 1 else 2 end, submitted_at desc
                limit 1
                """,
                [learner_kind, learner_id, activity_type, activity_id,
                 selected_topic if "assignmentTopicId" in request.GET else None, selected_topic],
            )
            row = cur.fetchone()
    except DatabaseError:
        logger.exception("Could not load learner reflection submission.")
        return _error("Could not load the saved reflection.", 502)

    if not row:
        return JsonResponse({"submission": None})

    full_submission = row[2]
    if isinstance(full_submission, str):
        try:
            full_submission = json.loads(full_submission)
        except (TypeError, ValueError):
            full_submission = {}
    if not isinstance(full_submission, dict):
        full_submission = {}

    if request.GET.get("attempt") is not None:
        from .assignment_attempts import attempt_document
        try:
            if activity_type != "assignment":
                raise ValueError("An assignment is required.")
            attempt = attempt_document(full_submission, int(request.GET["attempt"]), row[1], row[6], row[3], row[4], row[5])
        except (ValueError, TypeError):
            return _error("Submission attempt not found.", 404)
        return JsonResponse({"submission": {**attempt, "id": str(row[0])}})

    return JsonResponse(
        {
            "submission": {
                **{key: value for key, value in full_submission.items() if key != HISTORY_KEY},
                "submissionAttempts": submission_attempts(full_submission, row[1], row[6], row[3], row[4], row[5]) if activity_type in ("assignment", "extra_activity") else [],
                "id": str(row[0]),
                "status": row[1],
                "coachFeedback": row[3],
                "reviewedBy": row[4],
                "reviewedAt": row[5].isoformat() if row[5] else None,
                "submittedAt": row[6].isoformat() if row[6] else None,
                "locked": row[1] == "accepted",
            }
        }
    )
