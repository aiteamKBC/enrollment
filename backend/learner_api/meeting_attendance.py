"""Attendance actions for the learner's own scheduled coaching/review records."""
import json
import logging
import math
from datetime import date, datetime, time, timedelta

from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from login.permissions import learner_self_or_admin, learner_self_or_staff
from .attendance_confirmation import read_confirmations, save_confirmation
from .calendar import _calendar_profile, coaching_events_for_learner
from .learner_detail import SOURCE_MODELS

log = logging.getLogger(__name__)
MEETING_TYPES = {'mcr', 'progress-review'}


def meeting_records(source, kind):
    """Consume the same occurrences as both calendars, retaining ledger IDs."""
    mirror = _calendar_profile(source, source.pk)
    calendar = [item for item in coaching_events_for_learner(source, mirror) if item['source'] in MEETING_TYPES]
    result = []
    for item in calendar:
        if item.get('reviewSource') != 'aptem':
            result.append(item)
            continue
        booking_key = item.get('calendarEventKey')
        result.append({**item, 'id': f"imported-review:{item['reviewId']}",
                       'eventKey': booking_key, 'actualBooking': bool(booking_key),
                       'monthlyLogRef': f"meeting:{booking_key}" if booking_key else f"review-attendance:{item['reviewId']}"})
    return result


def ledger_id(source, meeting):
    return f"meeting-attendance:{source.pk}:{meeting['id']}"


def report_key(source, meeting):
    # An absence belongs to this appointment. Moving the meeting must allow
    # attendance at its replacement while retaining the original absence.
    return f"meeting:{source.pk}:{meeting['id']}:{meeting.get('scheduledDate')}:{meeting.get('scheduledTime')}"


def is_booked(meeting):
    return (meeting.get('actualBooking') is not False
        and meeting.get('status') in {'scheduled', 'in-progress'}
        and meeting.get('bookingStatus') not in {'cancelled', 'deleted', 'superseded', 'failed'}
        and bool(meeting.get('scheduledDate') and meeting.get('scheduledTime')))


def attendance_context(source, meeting, confirmations, reports):
    from .absence_reports import _kbc_attendance_report_id
    scheduled_date = meeting.get('scheduledDate')
    minutes = meeting.get('durationMinutes')
    valid_duration = isinstance(minutes, (int, float)) and math.isfinite(minutes) and minutes > 0
    saved = confirmations.get(ledger_id(source, meeting))
    report_id = str(_kbc_attendance_report_id(report_key(source, meeting)))
    reported = report_id in reports
    booked = is_booked(meeting)
    return {'id': meeting['id'], 'title': meeting['title'], 'date': scheduled_date,
        'status': meeting.get('bookingStatus') if meeting.get('bookingStatus') in {'cancelled', 'deleted', 'superseded', 'failed'} else meeting['status'],
        'startTime': meeting.get('scheduledTime'), 'durationMinutes': minutes,
        'meetingLink': meeting.get('meetingLink', ''), 'meetingProvider': meeting.get('meetingProvider', ''),
        'syncWarning': meeting.get('syncWarning', ''), 'invited': meeting.get('invited'),
        'calendarEventKey': meeting.get('eventKey'),
        'attendanceConfirmed': bool(saved), 'creditedMinutes': saved['seconds'] / 60 if saved else None,
        'canAttend': booked and valid_duration and scheduled_date == timezone.localdate().isoformat() and not saved and not reported,
        'canReportAbsence': booked and not saved and not reported,
        'absenceReported': reported,
        'absenceSessionId': f"{report_key(source, meeting)}-{scheduled_date}" if scheduled_date else None,
        'missed': booked and scheduled_date < timezone.localdate().isoformat() and not saved,
    }


def absence_rows(source, kind):
    """Adapt meetings to the existing absence form without adding lecture rows."""
    confirmations = read_confirmations(source.pk)
    result = []
    for meeting in meeting_records(source, kind):
        if not is_booked(meeting) or ledger_id(source, meeting) in confirmations:
            continue
        day = date.fromisoformat(meeting['scheduledDate'])
        start = time.fromisoformat(meeting['scheduledTime'])
        minutes = meeting.get('durationMinutes')
        end = (datetime.combine(day, start) + timedelta(minutes=minutes)).time() if minutes else None
        result.append({'source': 'coaching-meeting', 'session_id': report_key(source, meeting),
            'learner_id': source.pk, 'session_title': meeting['title'], 'session_type': meeting['source'],
            'session_date': day, 'session_start_time': start, 'session_end_time': end,
            'attendance_status': 'absent' if day < timezone.localdate() else 'upcoming',
            'coach_name': meeting.get('coachName', ''), 'module_title': ''})
    return result


def _source(kind, pk):
    model = SOURCE_MODELS.get(kind)
    return model.all_learners.filter(pk=pk).first() if model else None


@require_GET
@learner_self_or_staff(kwarg='learner_id')
def meeting_attendance(request, kind, learner_id):
    try:
        source = _source(kind, learner_id)
        if source is None:
            return JsonResponse({'error': 'Learner not found.'}, status=404)
        from coach_api.models import CoachAbsenceReport
        reports = {str(value) for value in CoachAbsenceReport.objects.filter(learner_id=learner_id).values_list('attendance_id', flat=True)}
        confirmations = read_confirmations(learner_id)
        return JsonResponse({'sessions': [attendance_context(source, item, confirmations, reports) for item in meeting_records(source, kind)],
            'today': timezone.localdate().isoformat(), 'timeZone': timezone.get_current_timezone_name(), 'csrfToken': get_token(request)})
    except Exception:
        log.exception('Could not load meeting attendance for learner %s', learner_id)
        return JsonResponse({'error': 'Could not load meeting attendance. Please try again.'}, status=503)


def resolve_confirmation(source, kind, meeting_id):
    from coach_api.models import CoachAbsenceReport
    from .absence_reports import _kbc_attendance_report_id
    matches = [item for item in meeting_records(source, kind) if item['id'] == meeting_id]
    if len(matches) != 1:
        raise ValueError('This meeting is no longer available for this learner.')
    meeting = matches[0]
    if not is_booked(meeting):
        raise ValueError('Only a scheduled meeting can be attended.')
    if meeting['scheduledDate'] != timezone.localdate().isoformat():
        raise ValueError('Attendance can only be recorded on the meeting date.')
    minutes = meeting.get('durationMinutes')
    if not isinstance(minutes, (int, float)) or not math.isfinite(minutes) or minutes <= 0:
        raise ValueError('The meeting duration is not recorded. Please contact your coach.')
    if CoachAbsenceReport.objects.filter(attendance_id=_kbc_attendance_report_id(report_key(source, meeting))).exists():
        raise ValueError('An absence has been reported for this meeting. Please contact your coach.')
    return {'id': ledger_id(source, meeting), 'date': meeting['scheduledDate'],
        'startTime': meeting['scheduledTime'], 'durationMinutes': minutes, 'title': meeting['title'],
        'module': '', 'ksbs': [], 'componentType': meeting['source'], 'category': 'Meeting', 'meetingId': meeting['id'],
        'monthlyLog': {'sourceRef': meeting.get('monthlyLogRef') or f"meeting:{meeting['id']}"}}


@require_POST
@csrf_protect
@learner_self_or_admin(kwarg='learner_id')
def confirm_meeting_attendance(request, kind, learner_id):
    try:
        payload = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'error': 'Choose a valid meeting.'}, status=400)
    if not isinstance(payload, dict) or not isinstance(payload.get('meetingId'), str):
        return JsonResponse({'error': 'Choose a valid meeting.'}, status=400)
    try:
        source = _source(kind, learner_id)
        if source is None:
            return JsonResponse({'error': 'Learner not found.'}, status=404)
        validate = lambda: resolve_confirmation(source, kind, payload['meetingId'])
        saved = save_confirmation(source, validate(), validate=validate)
        return JsonResponse({**saved, 'meetingId': payload['meetingId']})
    except ValueError as error:
        return JsonResponse({'error': str(error)}, status=409)
    except Exception:
        log.exception('Could not confirm meeting attendance for learner %s', learner_id)
        return JsonResponse({'error': 'Could not save attendance. Please try again.'}, status=503)
