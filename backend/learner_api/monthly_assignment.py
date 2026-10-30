"""Authoritative monthly assignment checks and real PPTX export.

Versioned answers live in learning_reflection_submissions.full_submission.
No schema creation and no import/write-back of historical assignments here.
"""
import calendar
import hashlib
import io
import json
import math
from datetime import date, timedelta
from urllib.parse import urlparse

from django.core import signing
from django.db import connections, DatabaseError, transaction
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from login.permissions import learner_self_or_admin
from .booking_calendar import booking_date_restriction


def text(value):
    return value.strip() if isinstance(value, str) else ""


def words(value):
    return len(text(value).split())


def mapping(value):
    return value if isinstance(value, dict) else {}


def items(value):
    return value if isinstance(value, list) else []


def month_bounds(month):
    try:
        start = date.fromisoformat(text(month) + "-01")
        end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
        return start, end
    except ValueError:
        return None, None


def _completion_date(record):
    """The date this assignment counts on.

    The declared working instant when the learner's Finish click fell outside
    the working rules and had to be corrected, otherwise today. The real click
    stays on the progress record's ``submittedAt`` either way.
    """
    from django.utils import timezone
    from django.utils.dateparse import parse_datetime

    declared = str((record or {}).get("declaredCompletedAt") or "").strip()
    if declared:
        parsed = parse_datetime(declared)
        if parsed is not None:
            return parsed.date().isoformat()
    return timezone.localdate().isoformat()


def valid_time_entries(monthly, hours, *, daily_limit=False):
    # Older submissions retain their original aggregate time.
    if "timeEntries" not in monthly:
        return True
    entries = monthly.get("timeEntries")
    start, end = month_bounds(monthly.get("month"))
    if not start or not isinstance(entries, list) or not entries:
        return False
    if daily_limit:
        from .assignment_hours import daily_limit_error
        if daily_limit_error(entries):
            return False
    total = 0
    for entry in entries:
        if not isinstance(entry, dict) or not text(entry.get("topic")):
            return False
        try:
            duration = float(entry.get("hours"))
            day = date.fromisoformat(text(entry.get("date")))
        except (ValueError, TypeError):
            return False
        if not math.isfinite(duration) or not 0 < duration <= 8 or not start <= day <= end or booking_date_restriction(day, today=day):
            return False
        total += duration
    return math.isfinite(total) and math.isclose(total, hours, rel_tol=0, abs_tol=1 / 3600)


def coaching_booking_bounds(month):
    start, end = month_bounds(month)
    if not start:
        return None, None
    return start.replace(day=20), end + timedelta(days=5)


def coaching_booking_windows(month):
    first = coaching_booking_bounds(month)
    if not first[0]:
        return []
    following_month = first[1].strftime('%Y-%m')
    return [first, coaching_booking_bounds(following_month)]


def presentation_fingerprint(payload):
    monthly = mapping(payload.get("monthlyAssignment"))
    contents = {key: payload.get(key) for key in ("learnerKind", "learnerId", "activityId")}
    # Timer ticks and booking selection must not invalidate a deck; changes to
    # the learner's written/evidence content do require a new reviewed export.
    contents.update({key: payload.get(key) for key in ("assignmentAnswer", "whatYouLearned", "businessImpact")})
    contents["monthly"] = {key: value for key, value in monthly.items()
                           if key not in ("step", "meetingKey", "presentationReviewed", "presentationToken")}
    return hashlib.sha256(json.dumps(contents, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def valid_presentation(payload):
    monthly = mapping(payload.get("monthlyAssignment"))
    if monthly.get("presentationReviewed") is not True:
        return False
    if valid_uploaded_presentation(payload):
        return True
    slides = items(monthly.get("slides"))
    if not (1 <= len(slides) <= 200 and monthly.get("presentationReviewed") is True):
        return False
    try:
        digest = signing.loads(text(monthly.get("presentationToken")), salt="monthly-assignment-pptx")
        return digest == presentation_fingerprint(payload)
    except signing.BadSignature:
        return False


def valid_uploaded_presentation(payload):
    """Trust the stored, scanned evidence record, never client file metadata."""
    uploaded = mapping(mapping(payload.get("monthlyAssignment")).get("uploadedPresentation"))
    file_id = text(uploaded.get("id"))
    if not file_id:
        return False
    with connections["enrolment"].cursor() as cur:
        cur.execute(
            'SELECT original_filename FROM "Learner"."evidence_files" '
            "WHERE id::text = %s AND learner_kind = %s AND learner_id = %s "
            "AND section_ref = %s AND status = 'approved' AND size_bytes > 0 AND size_bytes <= %s",
            [file_id, payload.get("learnerKind"), str(payload.get("learnerId")),
             payload.get("activityId"), 50 * 1024 * 1024],
        )
        row = cur.fetchone()
    return bool(row and text(row[0]).lower().endswith((".ppt", ".pptx")))


def approved_evidence_ids(payload):
    with connections["enrolment"].cursor() as cur:
        cur.execute(
            'SELECT id::text FROM "Learner"."evidence_files" '
            "WHERE learner_kind = %s AND learner_id = %s AND status = 'approved'",
            [payload.get("learnerKind"), str(payload.get("learnerId"))],
        )
        return {str(row[0]) for row in cur.fetchall()}


def coaching_meeting_booked(kind, learner_id, meeting_key):
    """Verify the owned appointment for checks and topic inheritance, without writes."""
    from .calendar import _learner_calendar_record, coaching_events_for_learner, SOURCE_MODELS
    from coach_api.review_sources import review_profile_for_source, source_for_learner

    booked_statuses = ("scheduled", "in-progress", "completed", "awaiting-signature")
    record = _learner_calendar_record(kind, learner_id, meeting_key)
    if record is not None:
        return bool(record.event_type == "mcr" and record.scheduled_date and record.status in booked_statuses)

    # Aptem can own a scheduled appointment without an LMS calendar row. The
    # read-only calendar adapter already verifies its source/learner identity.
    # Do not create a local booking just to satisfy assignment validation.
    if not meeting_key.startswith("imported-review:"):
        return False
    model = SOURCE_MODELS.get(kind)
    learner = model.all_learners.filter(pk=learner_id).first() if model else None
    if learner is None:
        return False
    profile = review_profile_for_source(learner)
    if profile is None or source_for_learner(learner, profile).kind != "aptem":
        return False
    events = coaching_events_for_learner(learner, profile, only_sources={"mcr"}, dates_only=True)
    matches = [event for event in events if event.get("eventKey") == meeting_key]
    if len(matches) != 1:
        return False
    event = matches[0]
    return bool(
        event.get("reviewSource") == "aptem" and event.get("source") == "mcr"
        and str(event.get("learnerId")) == str(profile.id)
        and event.get("scheduledDate") and event.get("status") in booked_statuses
        and event.get("sourceStatus") in booked_statuses
        # An unresolved local booking must not fall back to an older source status.
        and not event.get("calendarEventKey")
    )


def booked_coaching(payload):
    if resubmission_booking_satisfied(payload):
        return True
    monthly = mapping(payload.get("monthlyAssignment"))
    # Any booked MCM counts, whatever its date; the month only has to be valid.
    if not month_bounds(monthly.get("month"))[0] or not text(monthly.get("meetingKey")):
        return False
    return coaching_meeting_booked(payload.get("learnerKind"), int(payload.get("learnerId")), monthly["meetingKey"])


def resubmission_booking_satisfied(payload):
    """Only stored rejected attempts can carry a previously verified MCM booking."""
    from .assignment_attempts import document, HISTORY_KEY
    with connections["enrolment"].cursor() as cur:
        cur.execute(
            'SELECT status, full_submission FROM "Learner"."learning_reflection_submissions" '
            "WHERE learner_kind = %s AND learner_id = %s AND activity_type = 'assignment' AND activity_id = %s AND assignment_topic_id = %s",
            [payload.get("learnerKind"), str(payload.get("learnerId")), payload.get("activityId"), payload.get("assignmentTopicId") or ""],
        )
        row = cur.fetchone()
    if not row or row[0] not in ("rejected", "draft"):
        return False
    stored = document(row[1])
    previous = stored
    if row[0] == "draft":
        history = stored.get(HISTORY_KEY)
        if not isinstance(history, list) or not history or mapping(history[-1]).get("status") != "rejected":
            return False
        previous = document(history[-1].get("content"))
    monthly = mapping(previous.get("monthlyAssignment"))
    return bool(
        previous.get("submissionOrigin") not in ("imported_legacy", "classified_legacy")
        and monthly.get("month") == mapping(payload.get("monthlyAssignment")).get("month")
        and text(monthly.get("meetingKey"))
        and any(mapping(check).get("key") == "meeting" and mapping(check).get("passed") is True
                for check in items(previous.get("qualityChecks")))
    )


def available_ksb_codes(payload):
    from .models import CommercialUser, EnrolmentUser
    from .identity import learner_profile_for_source
    from .components import component_ksb_codes
    model = CommercialUser if payload.get("learnerKind") == "commercial" else EnrolmentUser
    source = model.all_learners.filter(pk=payload.get("learnerId")).first()
    profile = learner_profile_for_source(source, payload.get("learnerId"), active_only=True) if source else None
    from .active_users import current_curriculum_ksb_items_for_learner
    programme_items = current_curriculum_ksb_items_for_learner(profile, source=source) if source else []
    return set(component_ksb_codes(payload.get("activityId"))) | {k["code"] for k in (programme_items or (profile.ksbs if profile else []))}


def assignment_checks(payload, *, evidence_ids=None, meeting_booked=None, allowed_ksbs=None, claimed_hours=None):
    """Dependencies are injectable for schema-free tests; production verifies DB ownership."""
    monthly = mapping(payload.get("monthlyAssignment"))
    evidence = [mapping(e) for e in items(monthly.get("evidence"))]
    owned = approved_evidence_ids(payload) if evidence_ids is None and evidence else (evidence_ids or set())
    answer_lines = [line.strip() for line in text(payload.get("assignmentAnswer")).splitlines() if line.strip()]
    linked_ids = set()
    evidence_valid = True
    for entry in evidence:
        entry_id = text(entry.get("id"))
        url = text(entry.get("url"))
        is_link = entry_id.startswith("link:") and urlparse(url).scheme in ("https", "http") and bool(urlparse(url).hostname)
        point_text = text(entry.get("points")).strip()
        try:
            points = [int(p.strip()) for p in point_text.split(",") if p.strip()]
        except ValueError:
            points = []
        valid_points = not point_text or (bool(points) and all(1 <= p <= len(answer_lines) for p in points))
        if entry_id and (is_link or entry_id in owned) and valid_points:
            linked_ids.add(entry_id)
        else:
            evidence_valid = False
    claims = [mapping(c) for c in items(monthly.get("claims"))]
    allowed = available_ksb_codes(payload) if allowed_ksbs is None and claims else (allowed_ksbs or set())
    claimed_codes = [text(c.get("code")) for c in claims]
    try:
        hours = float(payload.get("actualTimeHours") or 0)
    except (TypeError, ValueError):
        hours = 0
    booked = booked_coaching(payload) if meeting_booked is None else meeting_booked
    # Personal study has its own account-owned storage; extra activities retain
    # their existing rules. Neither participates in official assignment claims.
    daily_limit = payload.get("activityType") == "assignment" and str(payload.get("learnerId", "")).isdigit()
    hours_error = ""
    if daily_limit and "timeEntries" in monthly:
        from .assignment_hours import daily_totals, daily_limit_error, saved_daily_totals
        if claimed_hours is None and daily_totals(monthly["timeEntries"]):
            with connections["enrolment"].cursor() as cur:
                claimed_hours = saved_daily_totals(cur, payload["learnerKind"], payload["learnerId"],
                                                  payload["activityId"], payload.get("assignmentTopicId"))
        hours_error = daily_limit_error(monthly["timeEntries"], claimed_hours)
    hours_label = ("Record positive hours with a maximum of 8 per day across all assignments, including saved drafts"
                   if daily_limit else "Record each topic with positive hours (maximum 8 per topic)")
    hours_label += " and a working date in the assignment month (no weekends or bank holidays)"
    checks = [
        ("answer", "Assignment answer: at least 120 words", words(payload.get("assignmentAnswer")) >= 120),
        ("learning", "Learned, understood and gained skills: at least 20 words each", all(words(v) >= 20 for v in [payload.get("whatYouLearned"), monthly.get("understood"), monthly.get("gainedSkills")])),
        ("evidence", "Evidence files and links are optional; any provided items and answer point numbers must be valid", evidence_valid),
        ("ksbs", "Every claimed programme KSB has a 20-word explanation; evidence links are optional and must be valid if selected", bool(claims) and len(set(claimed_codes)) == len(claimed_codes) and set(claimed_codes) <= allowed and all(words(c.get("explanation")) >= 20 and set(str(e) for e in items(c.get("evidenceIds"))) <= linked_ids for c in claims)),
        ("planned", "Planned hours and KSBs reviewed", monthly.get("plannedReviewed") is True),
        ("declarations", "New learning, skills and employer evidence-sharing declarations confirmed", all(monthly.get(k) is True for k in ["newKnowledge", "newSkills", "sharingConsent"])),
        ("hours", hours_error or hours_label, math.isfinite(hours) and hours > 0 and valid_time_entries(monthly, hours, daily_limit=daily_limit) and not hours_error),
        ("reflection", "Monthly LMS reflection and integrated understanding: at least 20 words each", all(words(monthly.get(k)) >= 20 for k in ["lmsReflection", "integratedReflection"])),
        ("benefit", "Employer benefit confirmed and measurable outcomes described (20 words)", monthly.get("employerBenefit") is True and words(payload.get("businessImpact")) >= 20),
        ("impact", "Career, job and employer impacts: at least 20 words each", all(words(monthly.get(k)) >= 20 for k in ["careerImpact", "jobImpact", "employerImpact"])),
        ("action", "Action plan and EPA preparedness: at least 20 words each", all(words(monthly.get(k)) >= 20 for k in ["actionPlan", "epaPreparedness"])),
        ("meeting", "Coaching meeting booked", booked),
        ("presentation", "MCM PowerPoint uploaded or generated and exported; presentation reviewed", valid_presentation(payload)),
    ]
    return [{"key": key, "label": label, "passed": bool(passed)} for key, label, passed in checks]


def parse_request(request):
    if request.method != "POST":
        raise ValueError("POST is required.")
    payload = json.loads(request.body or b"{}")
    if not isinstance(payload, dict) or payload.get("learnerKind") not in ("commercial", "apprenticeship") or not str(payload.get("learnerId", "")).isdigit() or not text(payload.get("activityId")):
        raise ValueError("A valid learner and assignment are required.")
    return payload


@csrf_exempt
@learner_self_or_admin(body_field="learnerId")
def check_assignment(request):
    try:
        payload = parse_request(request)
        if payload.get("activityType") == "extra_activity":
            from .extra_activities import extra_activity_checks
            return JsonResponse({"checks": extra_activity_checks(payload)})
        return JsonResponse({"checks": assignment_checks(payload)})
    except (ValueError, TypeError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except DatabaseError:
        return JsonResponse({"error": "Could not verify evidence or coaching. Save your draft and retry."}, status=503)


@csrf_exempt
@learner_self_or_admin(body_field="learnerId")
def export_presentation(request):
    try:
        payload = parse_request(request)
        slides = items(mapping(payload.get("monthlyAssignment")).get("slides"))
        if not 1 <= len(slides) <= 200:
            raise ValueError("Add between 1 and 200 slides before exporting.")
        for index, slide in enumerate(slides, 1):
            if not text(mapping(slide).get("title")):
                raise ValueError(f"Slide {index}: add a title before exporting.")
            if not text(mapping(slide).get("body")):
                raise ValueError(f"Slide {index}: add content, regenerate from your completed answers, or remove this slide.")
            if len(text(mapping(slide).get("body"))) > 12000:
                raise ValueError(f"Slide {index}: split the content across more slides (maximum 12,000 characters per slide).")
        from .presentation_design import build_deck
        design = mapping(mapping(payload.get("monthlyAssignment")).get("presentationDesign"))
        if design and not design.get("evidenceId"):
            raise ValueError("This saved reference contains colours only. Upload the original PPTX again to use its full template, or choose the default KBC design.")
        if design.get("evidenceId"):
            from .presentation_template import load_owned_template, build_from_template
            from .presentation_design import extract_design
            try:
                _, reference = load_owned_template(payload["learnerKind"], payload["learnerId"], design["evidenceId"])
                extract_design(reference)
                content = build_from_template(slides, reference, design)
            except ValueError:
                raise
            except Exception:
                return JsonResponse({"error": "Could not load or apply the saved PowerPoint template. Try uploading it again."}, status=503)
        else:
            content = build_deck(slides)
        response = HttpResponse(content, content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")
        response["Content-Disposition"] = 'attachment; filename="monthly-assignment.pptx"'
        response["X-Presentation-Token"] = signing.dumps(presentation_fingerprint(payload), salt="monthly-assignment-pptx")
        response["Cache-Control"] = "no-store"
        return response
    except (ValueError, TypeError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)


def complete_saved_assignment(kind, learner_id, component_id, record, save_progress, assignment_topic=""):
    """Progress and final submission commit together; failed completion leaves a draft.

    The row lock also prevents a delayed autosave from overwriting submission.
    """
    from .reflection_submissions import _reflection_lineage
    from .assignment_attempts import preserve_attempts
    from django.utils import timezone
    with transaction.atomic(using="enrolment"):
        with connections["enrolment"].cursor() as cur:
            from .assignment_hours import lock_assignment_hours
            lock_assignment_hours(cur, kind, learner_id)
            cur.execute(
                'SELECT id, status, full_submission, coach_feedback, reviewed_by, reviewed_at, submitted_at FROM "Learner"."learning_reflection_submissions" '
                "WHERE learner_kind = %s AND learner_id = %s AND activity_type = 'assignment' AND activity_id = %s AND assignment_topic_id = %s FOR UPDATE",
                [kind, str(learner_id), component_id, assignment_topic],
            )
            row = cur.fetchone()
            if not row or row[1] in ("accepted", "submitted_for_tutor_review") or (assignment_topic and row[1] == "partial"):
                raise ValueError("This assignment is missing or has already been submitted. Reload the page to see its current status.")
            payload = mapping(json.loads(row[2]) if isinstance(row[2], str) else row[2])
            if payload.get("submissionOrigin") == "imported_legacy":
                raise ValueError("Historical imported assignments cannot be completed again.")
            if row[1] != "draft":
                preserve_attempts(payload, payload, status=row[1], feedback=row[3], reviewer=row[4], reviewed_at=row[5], submitted_at=row[6])
            payload.update(learnerKind=kind, learnerId=str(learner_id), activityType="assignment", activityId=component_id, assignmentTopicId=assignment_topic)
            checks = assignment_checks(payload)
            if not all(c["passed"] for c in checks):
                raise ValueError("Some monthly submission requirements changed. Your draft is safe; recheck before submitting.")
            # Use the reviewed claims, not all planned KSBs automatically.
            record["ksbs"] = [c["code"] for c in payload["monthlyAssignment"]["claims"]]
            # Completion must use the same validated hours as the saved form.
            # The page timer can be stale or cover only time spent online;
            # keep its verification evidence separate from the learner's input.
            seconds = round(float(payload["actualTimeHours"]) * 3600)
            minutes, remainder = divmod(seconds, 60)
            source = "timer" if ("timeEntries" not in payload["monthlyAssignment"]
                                 and payload.get("assignmentTimeSource") == "timer") else "input"
            tracking_source = record.get("timeTrackingSource", "assignment").rsplit(":", 1)[0]
            record.update(claimedSeconds=seconds, reportedTime=f"{seconds / 60} minutes",
                          timeTaken=f"{minutes:02d}:{remainder:02d}",
                          timeTrackingSource=f"{tracking_source}:{source}")
            save_progress()
            lineage = _reflection_lineage(learner_id, component_id)
            payload.update(submissionMode="submit", qualityScore=100, qualityChecks=checks,
                           otjhConfirmed=True, signedDeclaration=True,
                           dateCompleted=_completion_date(record),
                           outsideWorkingHours=record.get("outsideWorkingHours", False),
                           insideWorkingHoursConfirmed=record.get("insideWorkingHoursConfirmed", False),
                           insideWorkingHoursConfirmedAt=record.get("insideWorkingHoursConfirmedAt"),
                           outsideWorkingHoursConfirmed=record.get("outsideWorkingHoursConfirmed", False),
                           outsideWorkingHoursConfirmedAt=record.get("outsideWorkingHoursConfirmedAt"),
                           declaredCompletedAt=record.get("declaredCompletedAt"),
                           submissionValidationReason=record.get("submissionValidationReason", ""))
            cur.execute(
                'UPDATE "Learner"."learning_reflection_submissions" '
                "SET status = 'submitted_for_tutor_review', full_submission = %s::jsonb, quality_score = 100, "
                "submitted_at = now(), coach_feedback = NULL, reviewed_by = NULL, reviewed_at = NULL, date_completed = %s, otjh_confirmed = true, signed_declaration = true, "
                "progress_entry_id = %s WHERE id = %s",
                [json.dumps(payload), payload["dateCompleted"], lineage["progress_entry_id"], row[0]],
            )
