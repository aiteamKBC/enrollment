"""Learner-facing absence report API backed by Coach.coach_absence_report."""
import hashlib
import logging
import re
import threading
import traceback
from datetime import date, datetime, time, timezone as dt_timezone
from html import escape
from uuid import uuid4

from django.conf import settings
from django.db import DatabaseError, IntegrityError, connections, router, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone

from coach_api.models import CoachAbsenceReport, CoachCalendarEvent
from curriculum_api.live_session_absences import record_reported_absence
from curriculum_api.models import LiveSessionAbsence
from login import email_azure
from login.permissions import learner_self_or_admin

from .attendance_lectures import can_report_absence_status, lecture_register, session_key, report_id
from .evidence_storage import (
    azure_configured,
    blob_url,
    delete_blob,
    resolve_read_url,
    upload_to_quarantine,
)
from .identity import learner_profile_for_source
from .models import CommercialUser, EnrolmentUser
from .alternative_recovery import (
    ALTERNATIVE_METHOD,
    alternative_event_key,
    alternative_target_details,
    eligible_alternative_occurrences,
    eligible_alternatives_by_occurrence,
    validate_alternative_occurrence,
)


logger = logging.getLogger(__name__)

ALLOWED_REASONS = {"illness", "work", "emergency", "travel", "technical", "other"}
REASON_LABELS = {
    "illness": "Illness or medical appointment",
    "work": "Work commitment",
    "emergency": "Family or personal emergency",
    "travel": "Travel disruption",
    "technical": "Technical issue",
}
ALLOWED_UPLOAD_TYPES = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
MAX_UPLOAD_SIZE = 10 * 1024 * 1024
UPLOAD_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}
DEFAULT_COACH_NAME = "Med Maher"
DEFAULT_COACH_EMAIL = "med.maher@kbc.ac.uk"
KBC_ATTENDANCE_ID_BASE = 8_000_000_000_000_000_000
KBC_ATTENDANCE_ID_RANGE = 1_000_000_000_000_000_000


def _error(message, status=400):
    return JsonResponse({"error": message}, status=status)


def _save_failure(stage, exc, *, evidence_uploaded=False):
    """Correlate a failed save without exposing SQL parameters or learner details."""
    cause = exc.__cause__ or exc
    sqlstate = str(getattr(cause, "sqlstate", "") or "")
    sqlstate = sqlstate if re.fullmatch(r"[A-Z0-9]{5}", sqlstate) else "APP"
    reference = f"ABS-{stage}-{sqlstate}-{uuid4().hex[:12].upper()}"
    diagnostic = getattr(cause, "diag", None)

    def identifier(name):
        value = getattr(diagnostic, name, None)
        return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", value) else "-"

    # A PostgreSQL exception's text can contain the entire rejected row, including
    # the absence reason and evidence. Log locations and safe metadata instead.
    frames = " > ".join(f"{frame.name}:{frame.lineno}" for frame in traceback.extract_tb(exc.__traceback__)[-12:])
    logger.error(
        "absence_save_failed reference=%s stage=%s exception=%s sqlstate=%s "
        "table=%s constraint=%s column=%s frames=%s",
        reference, stage, type(exc).__name__, sqlstate, identifier("table_name"),
        identifier("constraint_name"), identifier("column_name"), frames,
    )
    message = (
        "The evidence was uploaded, but the absence report could not be saved. Please retry."
        if evidence_uploaded else "The absence report could not be saved. Please retry."
    )
    return JsonResponse({"error": message, "code": "absence_report_save_failed", "reference": reference}, status=502)


def _source_learner(kind, learner_id):
    model = {"commercial": CommercialUser, "apprenticeship": EnrolmentUser}.get(kind)
    if model is None:
        return None
    return model.all_learners.filter(pk=learner_id).first()


def _attendance_rows_for_learner(learner, learner_id, *, meetings=False, kind=None):
    if meetings:
        from .meeting_attendance import absence_rows
        return absence_rows(learner, kind or getattr(learner, 'learner_type', None) or 'apprenticeship')
    return lecture_register(learner)


def _can_report_absence(row):
    return can_report_absence_status(row.get('attendance_status'), row.get('session_date'))


def _native_absence_occurrence_id(row):
    """Resolve a shared display row using the learner's server-side register."""
    if row.get("source") != "microsoft-teams":
        return ""
    occurrence_id = str(row.get("occurrence_id") or row.get("session_id") or "")
    source_id = str(row.get("attendance_source_occurrence_id") or "")
    module_id = str(row.get("module_catalogue_id") or "")
    if (row.get("attendance_source_module_id") and source_id and module_id
            and occurrence_id == f"{source_id}:{module_id}"):
        return source_id
    # Replaced meetings may carry historical evidence from a different occurrence.
    # A real, current occurrence must retain its own absence identity.
    return occurrence_id


def _fetch_missed_sessions(learner, learner_id, *, meetings=False, kind=None):
    """Both registers, including real upcoming occurrences, scoped to this learner."""
    rows = [
        row for row in (_attendance_rows_for_learner(learner, learner_id, meetings=True, kind=kind) if meetings else _attendance_rows_for_learner(learner, learner_id))
        if _can_report_absence(row)
    ]

    # Alternatives for every Teams lecture in one batch, not several queries per lecture.
    alternatives = eligible_alternatives_by_occurrence([
        _native_absence_occurrence_id(row)
        for row in rows if row.get("source") == "microsoft-teams"
    ])
    result = []
    for row in rows:
        item = {
            "id": f"{session_key(row)}-{row['session_date'].isoformat()}",
            "sessionId": session_key(row),
            "reportId": str(report_id(row)),
            "status": row['attendance_status'],
            "title": row.get("session_title", "") or "",
            "sessionType": row.get("session_type", "") or "",
            "dateIso": row["session_date"].isoformat(),
            "startTime": row["session_start_time"].strftime("%H:%M") if row.get("session_start_time") else "",
            "endTime": row["session_end_time"].strftime("%H:%M") if row.get("session_end_time") else "",
            "coach": row.get("coach_name", "") or "",
            "module": row.get("module_title", "") or "",
        }
        if row.get("source") == "microsoft-teams":
            item["alternativeSessions"] = alternatives.get(
                _native_absence_occurrence_id(row), []
            )
        result.append(item)
    return result


def _kbc_attendance_report_id(session_id):
    """Map KBC's text key into CoachAbsenceReport's signed bigint column."""
    digest = hashlib.sha256(str(session_id).encode("utf-8")).digest()
    return KBC_ATTENDANCE_ID_BASE + (
        int.from_bytes(digest[:8], "big") % KBC_ATTENDANCE_ID_RANGE
    )


def _resolve_absent_attendance(
    learner,
    learner_id,
    session_id,
    session_title,
    session_date,
    session_time,
    kind=None,
    *,
    include_occurrence=False,
):
    del session_time  # Source identity/date/title, not a client-supplied time, authorises the report.
    expected_id = str(session_id or "").strip()
    expected_title = str(session_title or "").strip().casefold()
    matches = [
        row for row in (_attendance_rows_for_learner(learner, learner_id, meetings=True, kind=kind) if expected_id.startswith('meeting:') else _attendance_rows_for_learner(learner, learner_id))
        if _can_report_absence(row)
        and row.get("session_date") == session_date
        and str(row.get("session_title") or "").strip().casefold() == expected_title
        and (not expected_id or session_key(row) == expected_id)
    ]
    if len(matches) != 1:
        return None
    row = matches[0]
    attendance_id = report_id(row)
    if include_occurrence:
        return attendance_id, _native_absence_occurrence_id(row)
    return attendance_id


def _serialize(report):
    allowed_containers = {
        settings.AZURE_QUARANTINE_CONTAINER,
        settings.AZURE_APPROVED_CONTAINER,
    }
    # Signing the evidence link and reading the alternative session are extras:
    # a saved report must still be returned when either lookup fails.
    evidence_url = ""
    if report.status != CoachAbsenceReport.STATUS_DECLINED:
        try:
            evidence_url = resolve_read_url(report.evidence_image_url, allowed_containers)
        except Exception:
            logger.exception("Could not sign evidence for absence report %s", report.id)
    alternative = None
    if report.recovery_method == ALTERNATIVE_METHOD:
        try:
            alternative = alternative_target_details(
                report.catchup_event_key,
                include_join_url=report.status == CoachAbsenceReport.STATUS_APPROVED,
            )
        except Exception:
            logger.exception("Could not read the alternative session for absence report %s", report.id)
    return {
        "id": report.id,
        "attendanceId": str(report.attendance_id),
        "reference": f"AR-{report.id:04d}",
        "sessionTitle": report.session_title,
        "sessionDate": report.session_date.isoformat(),
        "sessionTime": report.session_time.strftime("%H:%M") if report.session_time else "",
        "reasonCategory": report.reason_category,
        "reason": report.reason,
        "status": report.status,
        "evidenceProvided": report.evidence_provided,
        "evidenceKind": report.evidence_kind,
        "evidenceUrl": evidence_url,
        "evidenceText": report.evidence_text,
        "coachNote": report.coach_note,
        "recoveryMethod": report.recovery_method,
        "catchupEventKey": report.catchup_event_key,
        "alternativeSession": alternative,
        "attendanceRate": report.attendance_rate,
        "previousAbsences": report.previous_absences,
        "createdAt": report.created_at.isoformat(),
        "updatedAt": report.updated_at.isoformat(),
    }


def _existing_report_response(existing, learner_id):
    """A retried submission gets the learner's saved report back instead of an error.

    The first attempt may have been saved after the proxy had already given up
    on it. Another learner's report for the same key remains a conflict.
    """
    if str(existing.learner_id) != str(learner_id):
        return _error("An absence report already exists for this session.", 409)
    return JsonResponse(_serialize(existing), status=200)


class RecoveryPlanError(ValueError):
    pass


def _recording_recovery_event(report, learner, mirror, recording_date, recording_time, duration_minutes):
    """Save the learner-selected recording slot as a local calendar task."""
    calendar_learner_id = int(getattr(mirror, "id", 0) or getattr(learner, "id", 0))
    return CoachCalendarEvent.objects.create(
        event_key=f"absence-recording:{report.id}",
        idempotency_key=f"absence-recovery:{report.id}",
        owner_email=report.owner_email,
        owner_name=report.owner_name,
        learner_id=calendar_learner_id,
        learner_name=report.learner_name,
        learner_email=report.learner_email,
        event_type="recorded-recovery",
        sequence=1,
        target_date=recording_date,
        scheduled_date=recording_date,
        scheduled_time=recording_time,
        duration_minutes=duration_minutes,
        status=CoachCalendarEvent.STATUS_SCHEDULED,
        notes=f"Watch the recording for: {report.session_title}",
        sync_state=CoachCalendarEvent.SYNC_SYNCED,
    )


def _email_coach_recovery(report, *, recovery_details=""):
    """Best-effort coach notification containing only the selected recovery plan."""
    labels = {
        "recorded": "Watch the recording",
        "catch-up": "Catch-up session",
        ALTERNATIVE_METHOD: "Alternative group session",
    }
    label = labels.get(report.recovery_method)
    if not label or not report.owner_email:
        return
    detail_line = f"\nScheduled: {recovery_details}" if recovery_details else ""
    text_body = (
        f"{report.learner_name} reported an absence for {report.session_title}.\n"
        f"Recovery method: {label}.{detail_line}"
    )
    html_detail = f"<p><strong>Scheduled:</strong> {escape(recovery_details)}</p>" if recovery_details else ""
    html_body = (
        f"<p>{escape(report.learner_name)} reported an absence for "
        f"{escape(report.session_title)}.</p>"
        f"<p><strong>Recovery method:</strong> {escape(label)}</p>{html_detail}"
    )
    try:
        sent, detail = email_azure.send_mail(
            to=report.owner_email,
            subject=f"Learner recovery plan: {report.session_title}",
            html_body=html_body,
            text_body=text_body,
            sender_name="Kent Business College LMS",
        )
        if not sent:
            logger.warning("Coach recovery email was not sent for absence report %s: %s", report.id, detail)
    except Exception:
        logger.exception("Coach recovery email failed for absence report %s", report.id)


def alternative_invite_ics(report, occurrence, title, join_url):
    """A one-event calendar file for the learner's own calendar (METHOD:PUBLISH).

    It never touches the other group's Teams meeting, so nobody else is emailed.
    """
    def stamp(value):
        instant = value if timezone.is_aware(value) else timezone.make_aware(value, dt_timezone.utc)
        return instant.astimezone(dt_timezone.utc).strftime('%Y%m%dT%H%M%SZ')

    def text(value):
        # RFC 5545 TEXT escaping.
        return (str(value or '').replace('\\', '\\\\').replace(';', '\\;')
                .replace(',', '\\,').replace('\n', '\\n'))

    end = occurrence.scheduled_end or occurrence.scheduled_start
    lines = [
        'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Kent Business College//LMS//EN', 'METHOD:PUBLISH',
        'BEGIN:VEVENT',
        f'UID:absence-alternative-{report.id}@kbc-lms',
        f'DTSTAMP:{stamp(timezone.now())}',
        f'DTSTART:{stamp(occurrence.scheduled_start)}',
        f'DTEND:{stamp(end)}',
        f'SUMMARY:{text("Alternative session: " + title)}',
        f'DESCRIPTION:{text(f"Make-up session for {report.session_title}. Join on Microsoft Teams: {join_url}")}',
        'LOCATION:Microsoft Teams',
        *([f'URL:{join_url}'] if join_url else []),
        'END:VEVENT', 'END:VCALENDAR',
    ]
    return ('\r\n'.join(lines) + '\r\n').encode('utf-8')


def _email_learner_alternative_invite(report, target_occurrence_id):
    """Email the learner a calendar invite for the alternative session they chose."""
    from curriculum_api.models import LiveSession, LiveSessionOccurrence
    try:
        occurrence = LiveSessionOccurrence.objects.using('enrolment').filter(pk=target_occurrence_id).first()
        session = LiveSession.objects.using('enrolment').filter(pk=occurrence.live_session_id).first() if occurrence else None
        if not occurrence or not session or not report.learner_email:
            return
        details = alternative_target_details(report.catchup_event_key, include_join_url=True) or {}
        title = details.get('title') or session.module_title or 'Live session'
        join_url = details.get('joinUrl') or occurrence.join_url or session.join_url or ''
        when = f"{details.get('dateIso', '')} {details.get('startTime', '')}–{details.get('endTime', '')} (UK time)".strip()
        html_body = (
            f"<p>Your alternative session for <strong>{escape(report.session_title)}</strong> is booked.</p>"
            f"<p><strong>{escape(title)}</strong><br>{escape(when)}<br>{escape(details.get('group') or '')}</p>"
            "<p>Open the attached invite to add it to your Outlook or Teams calendar.</p>"
            + (f'<p><a href="{escape(join_url)}">Join on Microsoft Teams</a></p>' if join_url else '')
        )
        sent, detail = email_azure.send_mail(
            to=report.learner_email,
            subject=f"Alternative session booked: {title}",
            html_body=html_body,
            text_body=f"Your alternative session for {report.session_title} is booked: {title}, {when}. {join_url}",
            sender_name="Kent Business College LMS",
            attachments=[{'name': 'alternative-session.ics', 'content_type': 'text/calendar',
                          'content': alternative_invite_ics(report, occurrence, title, join_url)}],
        )
        if not sent:
            logger.warning("Alternative session invite was not sent for absence report %s: %s", report.id, detail)
    except Exception:
        logger.exception("Alternative session invite failed for absence report %s", report.id)


def _send_after_commit(task, *args, **kwargs):
    """Send a notification once the report is committed, off the request path.

    A slow Graph call used to hold the response until the proxy gave up with a
    502, so the learner retried a report that had in fact been saved. The email
    is best-effort: it never fails or delays the saved report.
    """
    name = getattr(task, "__name__", "notification")

    def run():
        try:
            task(*args, **kwargs)
        except Exception:
            logger.exception("Absence report notification %s failed", name)
        finally:
            # This thread's own connections (the invite reads the occurrence).
            connections.close_all()

    def start():
        try:
            threading.Thread(target=run, name="absence-report-email", daemon=True).start()
        except RuntimeError:
            logger.exception("Could not start absence report notification %s", name)

    try:
        transaction.on_commit(start, using=router.db_for_write(CoachAbsenceReport))
    except Exception:
        logger.exception("Could not schedule absence report notification %s", name)


def _catchup_booking(learner, mirror, event_key, session_date, *, lock=False, for_report_id=None):
    """Recheck the saved appointment; matching IDs never override another email.

    One catch-up makes up one lecture: a booking already linked to another
    (not declined) absence report is refused. ``for_report_id`` is the report
    being (re)linked, which may keep its own booking.
    """
    query = CoachCalendarEvent.objects
    if lock:
        query = query.select_for_update()
    booking = query.filter(event_key=event_key).first()
    emails = {str(getattr(person, 'email', '') or '').strip().casefold()
              for person in (learner, mirror)} - {''}
    ids = {str(getattr(person, 'id', '')) for person in (learner, mirror) if person is not None}
    email = str(getattr(booking, 'learner_email', '') or '').strip().casefold()
    belongs = booking is not None and (email in emails if email else str(booking.learner_id) in ids)
    if not belongs or booking.event_type != 'catch-up':
        raise RecoveryPlanError('Choose one of your own catch-up bookings.')
    if (booking.status not in {'not-scheduled', 'scheduled', 'in-progress'}
            or not booking.scheduled_date or not booking.scheduled_time):
        raise RecoveryPlanError('This catch-up booking is no longer available. Book or select another session.')
    start = datetime.combine(booking.scheduled_date, booking.scheduled_time)
    if booking.scheduled_date < session_date or start <= timezone.localtime().replace(tzinfo=None):
        raise RecoveryPlanError('Choose a future catch-up session on or after the lecture date.')
    linked = CoachAbsenceReport.objects.filter(catchup_event_key=event_key).exclude(status='declined')
    if for_report_id is not None:
        linked = linked.exclude(pk=for_report_id)
    if linked.exists():
        raise RecoveryPlanError('This catch-up is already linked to another lecture. Book or select another session.')
    return booking


@csrf_exempt
# Learners and admins use the learner workspace; coaches use coach_api.
@learner_self_or_admin(kwarg="learner_id")
def learner_absence_reports(request, kind, learner_id):
    try:
        learner = _source_learner(kind, learner_id)
    except DatabaseError as exc:
        return _error(f"Could not load learner: {exc}", 502)
    if learner is None:
        return _error("Learner not found.", 404)

    learner_email = str(getattr(learner, "email", "") or "").strip()
    learner_name = str(getattr(learner, "username", "") or "").strip() or learner_email

    if request.method == "GET":
        try:
            reports = CoachAbsenceReport.objects.filter(learner_id=learner_id).order_by("-created_at")
            results = [_serialize(report) for report in reports]
            missed_sessions = _fetch_missed_sessions(learner, learner_id, meetings=True, kind=kind) if request.GET.get('scope') == 'meetings' else _fetch_missed_sessions(learner, learner_id)
        except Exception:
            logger.exception(
                "Could not load absence sessions for %s learner %s",
                kind,
                learner_id,
            )
            return _error("Could not load absence reports and sessions.", 502)
        return JsonResponse({
            "count": len(results),
            "results": results,
            "missedSessions": missed_sessions,
        })

    if request.method != "POST":
        return _error("Method not allowed.", 405)

    session_id = request.POST.get("sessionId", "").strip()
    session_title = request.POST.get("sessionTitle", "").strip()
    session_date_text = request.POST.get("sessionDate", "").strip()
    session_time_text = request.POST.get("sessionTime", "").strip()
    reason_category = request.POST.get("reasonCategory", "").strip().lower()
    other_reason = request.POST.get("otherReason", "").strip()
    evidence_text = request.POST.get("explanation", "").strip()
    recovery_method = request.POST.get("recoveryMethod", "").strip()
    catchup_event_key = request.POST.get("catchupEventKey", "").strip()
    # The form books a new catch-up after the report is saved, linking it in that
    # same booking request; until then the report has no catch-up to hold.
    catchup_pending = request.POST.get("catchupPending") == "1" and not catchup_event_key
    target_occurrence_id = request.POST.get("targetOccurrenceId", "").strip()
    recording_date_text = request.POST.get("recordingDate", "").strip()
    recording_time_text = request.POST.get("recordingTime", "").strip()
    recording_duration_text = request.POST.get("recordingDurationMinutes", "").strip()
    upload = request.FILES.get("evidence")

    if not session_title or not session_date_text:
        return _error("Session title and date are required.")
    if reason_category not in ALLOWED_REASONS:
        return _error("Choose a valid absence reason.")
    meeting_absence = session_id.startswith('meeting:')
    if meeting_absence and (recovery_method or catchup_event_key or target_occurrence_id):
        return _error('Report the meeting absence, then reschedule the meeting with your coach.')
    if not meeting_absence and recovery_method not in {'recorded', 'catch-up', ALTERNATIVE_METHOD}:
        return _error('Choose another cohort session, a coach catch-up, or the recording.')
    if recovery_method == 'catch-up' and not catchup_pending and (not catchup_event_key or len(catchup_event_key) > 255):
        return _error('Book or select a catch-up session before submitting your absence report.')
    if recovery_method == 'recorded' and catchup_event_key:
        return _error('A recording recovery plan cannot include a catch-up booking.')
    if recovery_method == 'recorded' and (not recording_date_text or not recording_time_text):
        return _error('Choose when you plan to watch the recording.')
    if recovery_method != 'recorded' and (recording_date_text or recording_time_text or recording_duration_text):
        return _error('A recording time can only be used with the recording recovery plan.')
    if recovery_method == ALTERNATIVE_METHOD and not target_occurrence_id:
        return _error('Choose an available alternative session.')
    if recovery_method != ALTERNATIVE_METHOD and target_occurrence_id:
        return _error('The alternative session does not match the selected recovery plan.')
    if reason_category == "other" and not other_reason:
        return _error("Please specify the other reason.")
    if len(evidence_text) > 600 or len(other_reason) > 120 or len(session_title) > 255:
        return _error("Absence details are too long.")

    try:
        parsed_date = date.fromisoformat(session_date_text)
        parsed_time = time.fromisoformat(session_time_text) if session_time_text else None
        recording_date = date.fromisoformat(recording_date_text) if recording_date_text else None
        recording_time = time.fromisoformat(recording_time_text) if recording_time_text else None
    except ValueError:
        return _error("Invalid session date or time.")
    try:
        recording_duration = int(recording_duration_text or 60)
        if not 15 <= recording_duration <= 480:
            raise ValueError
    except (TypeError, ValueError):
        return _error('Recording duration must be between 15 and 480 minutes.')
    if recovery_method == 'recorded':
        recording_start = datetime.combine(recording_date, recording_time)
        if recording_date < parsed_date or recording_start <= timezone.localtime().replace(tzinfo=None):
            return _error('Choose a future recording time on or after the lecture date.')

    try:
        resolved = _resolve_absent_attendance(
            learner,
            learner_id,
            session_id,
            session_title,
            parsed_date,
            parsed_time,
            kind=kind,
            include_occurrence=True,
        )
    except Exception:
        logger.exception(
            "Could not validate absence session for %s learner %s",
            kind,
            learner_id,
        )
        return _error("Could not validate the attendance session.", 502)
    if resolved is None:
        return _error("Choose a valid absent, unmarked, upcoming or in-progress session.")
    attendance_id, original_occurrence_id = resolved
    try:
        existing = CoachAbsenceReport.objects.filter(attendance_id=attendance_id)
        if existing.exists():
            return _existing_report_response(existing.first(), learner_id)
    except Exception:
        logger.exception("Could not check for an existing absence report for %s learner %s", kind, learner_id)
        return _error("Could not check your existing absence reports. Please retry.", 502)

    if upload is not None:
        if upload.content_type not in ALLOWED_UPLOAD_TYPES:
            return _error("Evidence must be a JPG, PNG, WEBP, or PDF file.")
        if upload.size > MAX_UPLOAD_SIZE:
            return _error("Evidence must be smaller than 10 MB.")
        if not azure_configured():
            return _error("Evidence storage is not configured.", 503)

    try:
        attendance_rate = int(request.POST.get("attendanceRate", ""))
        if not 0 <= attendance_rate <= 100:
            raise ValueError
    except (TypeError, ValueError):
        attendance_rate = None

    try:
        # A learner whose profile is not marked active (e.g. paused) still owns
        # their lectures: the active profile wins, but any linked one will do.
        active = (learner_profile_for_source(learner, learner_id, active_only=True)
                  or learner_profile_for_source(learner, learner_id))
    except Exception:
        logger.exception("Could not load the learner profile for %s learner %s", kind, learner_id)
        return _error("Could not load your learner profile. Please retry.", 502)
    if original_occurrence_id and active is None:
        return _error('This live-session absence could not be linked to your learner profile.', 409)
    if recovery_method == ALTERNATIVE_METHOD:
        try:
            alternative = validate_alternative_occurrence(
                original_occurrence_id,
                target_occurrence_id,
            )
        except DatabaseError:
            return _error('Could not verify the alternative session. Please retry.', 502)
        if alternative is None:
            return _error('That alternative session is no longer eligible. Choose another option.', 409)
        catchup_event_key = alternative_event_key(target_occurrence_id)
    catchup_booking = None
    if recovery_method == 'catch-up' and not catchup_pending:
        try:
            catchup_booking = _catchup_booking(learner, active, catchup_event_key, parsed_date)
        except RecoveryPlanError as exc:
            return _error(str(exc))
        except DatabaseError:
            return _error('Could not verify the catch-up booking. Please retry.', 502)
    owner_name = str(getattr(active, "coach_name", "") or "").strip() or DEFAULT_COACH_NAME
    owner_email = str(getattr(active, "coach_email", "") or "").strip() or DEFAULT_COACH_EMAIL
    reason = other_reason if reason_category == "other" else REASON_LABELS[reason_category]
    evidence_url = ""
    blob_name = ""
    quarantine_container = settings.AZURE_QUARANTINE_CONTAINER

    if upload is not None:
        extension = UPLOAD_EXTENSIONS[upload.content_type]
        blob_name = f"absence-reports/{kind}/{learner_id}/{uuid4().hex}{extension}"
        try:
            upload_to_quarantine(upload, blob_name, upload.content_type)
            evidence_url = blob_url(quarantine_container, blob_name)
        except Exception:
            logger.exception(
                "Could not upload absence evidence for %s learner %s attendance %s",
                kind,
                learner_id,
                attendance_id,
            )
            try:
                delete_blob(quarantine_container, blob_name)
            except Exception:
                pass
            return _error(
                "Could not upload the evidence to secure storage. Please retry.",
                502,
            )

    save_stage = "START"
    try:
        with transaction.atomic():
            if recovery_method == 'catch-up' and not catchup_pending:
                catchup_booking = _catchup_booking(
                    learner, active, catchup_event_key, parsed_date, lock=True,
                )
            elif recovery_method == ALTERNATIVE_METHOD:
                # Recheck eligibility inside the save transaction so an expired
                # or cancelled target cannot be submitted from a stale dialog.
                if validate_alternative_occurrence(original_occurrence_id, target_occurrence_id) is None:
                    raise RecoveryPlanError('That alternative session is no longer eligible. Choose another option.')
            save_stage = "RPT"
            previous_absences = CoachAbsenceReport.objects.filter(learner_id=learner_id).count()
            report = CoachAbsenceReport.objects.create(
                attendance_id=attendance_id,
                owner_email=owner_email,
                owner_name=owner_name,
                learner_id=learner_id,
                learner_name=learner_name,
                learner_email=learner_email,
                session_title=session_title,
                session_date=parsed_date,
                session_time=parsed_time,
                reason_category=reason_category,
                reason=reason,
                reported_by=learner_email or learner_name,
                status=(CoachAbsenceReport.STATUS_APPROVED
                        if recovery_method in {'recorded', 'catch-up', ALTERNATIVE_METHOD}
                        else CoachAbsenceReport.STATUS_PENDING),
                evidence_provided=bool(upload or evidence_text),
                coach_note="",
                recovery_method=recovery_method,
                catchup_event_key=catchup_event_key or None,
                attendance_rate=attendance_rate,
                evidence_image_url=evidence_url,
                evidence_kind="both" if upload and evidence_text else "file" if upload else "text" if evidence_text else "none",
                evidence_text=evidence_text,
                previous_absences=previous_absences,
            )
            if original_occurrence_id:
                save_stage = "LINK"
                record_reported_absence(
                    database=router.db_for_write(LiveSessionAbsence) or 'default',
                    occurrence_id=original_occurrence_id,
                    learner_profile_id=active.id,
                    source_kind=kind,
                    source_learner_id=learner_id,
                    learner_email=learner_email,
                    learner_name=learner_name,
                    # No catch-up yet: the booking that follows records it (link_report_to_catchup).
                    recovery_method='' if catchup_pending else recovery_method,
                    recovery_reference=catchup_event_key or '',
                )
            if recovery_method == 'recorded':
                save_stage = "PLAN"
                _recording_recovery_event(
                    report, learner, active, recording_date, recording_time, recording_duration,
                )
            save_stage = "COMMIT"
    except RecoveryPlanError as exc:
        if blob_name:
            try:
                delete_blob(quarantine_container, blob_name)
            except Exception:
                pass
        return _error(str(exc), 409)
    except IntegrityError as exc:
        # Another submission may have saved the same lecture after validation.
        # The unique attendance_id still guarantees a single report.
        if blob_name:
            try:
                delete_blob(quarantine_container, blob_name)
            except Exception:
                pass
        try:
            existing = CoachAbsenceReport.objects.filter(attendance_id=attendance_id).first()
        except Exception:
            existing = None
        if existing is not None:
            return _existing_report_response(existing, learner_id)
        return _save_failure(save_stage, exc)
    except Exception as exc:
        if blob_name:
            try:
                delete_blob(quarantine_container, blob_name)
            except Exception:
                pass
        return _save_failure(save_stage, exc, evidence_uploaded=bool(blob_name))

    recovery_details = ""
    if recovery_method == 'recorded':
        recovery_details = f"{recording_date.isoformat()} at {recording_time.strftime('%H:%M')}"
    elif recovery_method == 'catch-up' and catchup_pending:
        recovery_details = "Catch-up being booked by the learner"
    elif recovery_method == 'catch-up':
        recovery_details = (
            f"{catchup_booking.scheduled_date.isoformat()} at "
            f"{catchup_booking.scheduled_time.strftime('%H:%M')}"
        )
    elif recovery_method == ALTERNATIVE_METHOD:
        target = alternative or {}
        recovery_details = " ".join(filter(None, [target.get('dateIso'), target.get('startTime'), target.get('group')]))
    _send_after_commit(_email_coach_recovery, report, recovery_details=recovery_details)
    if recovery_method == ALTERNATIVE_METHOD:
        _send_after_commit(_email_learner_alternative_invite, report, target_occurrence_id)
    try:
        return JsonResponse(_serialize(report), status=201)
    except Exception:
        logger.exception("Could not return saved absence report %s", getattr(report, "id", None))
        return _error("Your absence report was saved. Refresh the page to see it.", 502)
