"""Same-day attendance credit, saved as an event in the existing learner ledger."""
import json
import logging
import math
from datetime import datetime

from django.db import connections, transaction
from django.db.models import Max
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_protect

from login.permissions import learner_self_or_admin
from .learner_detail import SOURCE_MODELS
from .models import LearnerProfile, LearnerProgressEntry
from .training_plan_dashboard import rows as dict_rows

EVENT = 'attendance_confirmation'
log = logging.getLogger(__name__)


def read_confirmations(learner_id):
    with connections['enrolment'].cursor() as cur:
        cur.execute('''SELECT p.time_tracking_session_ref AS lecture_id,
            p.claimed_seconds AS seconds,p.submitted_at,p.time_tracking_calculation AS details
            FROM "Learner".learner_progress_entries p
            JOIN "Learner".learners l ON l.id=p.learner_id
            WHERE l.enrolment_id=%s AND p.kind='activity_event' AND p.feed_kind=%s
            ORDER BY p.id''', [learner_id, EVENT])
        return {r['lecture_id']: r for r in dict_rows(cur)}


def apply_confirmations(register, confirmations):
    from .attendance_lectures import _aware, session_key
    result = []
    for row in register:
        saved = confirmations.get(f"{session_key(row)}-{row['session_date'].isoformat()}")
        if not saved:
            result.append(row)
            continue
        details = saved.get('details') or {}
        if isinstance(details, str):
            details = json.loads(details)
        extra = {'raw_attendance_status': row.get('raw_attendance_status') or row['attendance_status'],
                 'effective_attendance_status': 'present', 'effective_attendance': 1,
                 'final_outcome': 'present'} if details.get('staffConfirmed') else {}
        result.append({**row, **extra, 'attendance_status': 'present', 'minutes_late': 0,
                       'attendance_confirmed': True, 'credited_minutes': saved['seconds'] / 60,
                       'updated_at': _aware(saved['submitted_at'])})
    return result


def confirmation_payload(lecture_id, seconds, *, already_recorded=False):
    return {'lectureId': lecture_id, 'status': 'completed', 'creditedMinutes': seconds / 60,
            'creditedHours': round(seconds / 3600, 2), 'alreadyRecorded': already_recorded}


def save_confirmation(source, lecture, *, validate=None):
    # Serialize this learner's confirmations so concurrent tabs cannot credit
    # the same occurrence twice. No new table or schema change is needed.
    with transaction.atomic(using='enrolment'):
        profiles = list(LearnerProfile.objects.using('enrolment').select_for_update()
                        .filter(enrolment_id=source.id).only('id')[:2])
        if len(profiles) != 1:
            raise ValueError('Your learner record is not available. Please contact support.')
        entries = LearnerProgressEntry.objects.using('enrolment').filter(learner=profiles[0])
        saved = entries.filter(kind='activity_event', feed_kind=EVENT,
                               time_tracking_session_ref=lecture['id']).first()
        if saved:
            return confirmation_payload(lecture['id'], saved.claimed_seconds, already_recorded=True)
        if validate:
            lecture = validate()
        # Check again after acquiring the lock in case the request crossed midnight.
        if lecture['date'] != timezone.localdate().isoformat():
            raise ValueError('Attendance can only be recorded on the lecture date.')
        seconds = round(lecture['durationMinutes'] * 60)
        now = timezone.now()
        start = lecture.get('startsAt') or f"{lecture['date']}T{lecture['startTime'] or '00:00'}:00"
        started_at = datetime.fromisoformat(start)
        if timezone.is_naive(started_at):
            started_at = timezone.make_aware(started_at)
        detail = {'sourceRef': lecture['monthlyLog']['sourceRef'], 'date': lecture['date'],
                  'startsAt': start, 'title': lecture['title'], 'module': lecture['module'], 'ksbs': lecture['ksbs']}
        if lecture.get('category'):
            detail['category'] = lecture['category']
            detail['componentType'] = lecture.get('componentType')
            detail['meetingId'] = lecture.get('meetingId')
        entries.create(learner=profiles[0], entry_order=(entries.aggregate(last=Max('entry_order'))['last'] or 0) + 1,
            kind='activity_event', component_title=lecture['title'], component_type=lecture.get('componentType', 'live_session'),
            module_title=lecture['module'], expected_otjh=round(seconds / 3600, 2),
            started_at=started_at, submitted_at=now, claimed_seconds=seconds,
            time_tracking_source=EVENT, time_tracking_session_ref=lecture['id'],
            # Keep the source snapshot with the calculation, and readable copy
            # in feed_detail, which is displayed in the learner's activity feed.
            time_tracking_calculation=json.dumps(detail),
            feed_kind=EVENT, feed_action='attended', feed_title=lecture['title'],
            feed_detail=f"{seconds / 3600:g} hours credited for the full {'meeting' if lecture.get('category') == 'Meeting' else 'lecture'}.", feed_occurred_at=now)
        return confirmation_payload(lecture['id'], seconds)


@require_POST
@csrf_protect
@learner_self_or_admin(kwarg='learner_id')
def confirm_attendance(request, kind, learner_id):
    from .attendance_lectures import ATTENDANCE_SOURCE_FIELDS, read_workspace
    model = SOURCE_MODELS.get(kind)
    if model is None:
        return JsonResponse({'error': 'Unknown learner kind.'}, status=404)
    try:
        body = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'error': 'Choose a valid lecture.'}, status=400)
    if not isinstance(body, dict) or not isinstance(body.get('lectureId'), str):
        return JsonResponse({'error': 'Choose a valid lecture.'}, status=400)
    try:
        source = model.all_learners.filter(pk=learner_id).only(*ATTENDANCE_SOURCE_FIELDS).first()
        if source is None:
            return JsonResponse({'error': 'Learner not found.'}, status=404)
        # Resolve ownership, cancellation, date and duration from the live source;
        # the browser cannot choose its own hours or attendance status.
        matches = [r for r in read_workspace(source, kind)['lectures'] if r['id'] == body['lectureId']]
        if len(matches) != 1:
            return JsonResponse({'error': 'Lecture not found.'}, status=404)
        lecture = matches[0]
        if lecture.get('source') == 'microsoft-teams':
            return JsonResponse({'error': 'Teams attendance is verified from the meeting report. Use catch-up for a missed session.'}, status=409)
        if lecture['date'] != timezone.localdate().isoformat():
            return JsonResponse({'error': 'Attendance can only be recorded on the lecture date.'}, status=409)
        minutes = lecture.get('durationMinutes')
        if minutes is None or not math.isfinite(minutes) or minutes <= 0:
            return JsonResponse({'error': 'The lecture duration is not available. Please contact support.'}, status=409)
        if lecture.get('attendanceConfirmed'):
            return JsonResponse(confirmation_payload(lecture['id'], round(lecture['creditedMinutes'] * 60), already_recorded=True))
        if lecture['status'] in {'present', 'completed', 'late'}:
            return JsonResponse({'error': 'Attendance has already been recorded for this lecture.'}, status=409)
        return JsonResponse(save_confirmation(source, lecture))
    except ValueError as error:
        return JsonResponse({'error': str(error)}, status=409)
    except Exception:
        log.exception('Could not confirm attendance for learner %s', learner_id)
        return JsonResponse({'error': 'Could not save attendance. Please try again.'}, status=503)
