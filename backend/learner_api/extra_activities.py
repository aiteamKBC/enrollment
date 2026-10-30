"""Extra activities use the existing owned submission and coach-review records."""
import json
from uuid import UUID
from zoneinfo import ZoneInfo
from django.utils import timezone
from django.db import connections, DatabaseError
from django.http import JsonResponse
from login.permissions import learner_self_or_staff
from .monthly_assignment import assignment_checks, mapping, text
from .extra_activity_month import valid_activity_month

EXTRA_CHECK_KEYS = {'answer', 'learning', 'evidence', 'ksbs', 'planned', 'declarations', 'hours'}


def prepare_extra_activity(payload):
    identifier = text(payload.get('activityId'))
    if not identifier.startswith('extra:'):
        raise ValueError('A valid extra activity identifier is required.')
    try:
        UUID(identifier[6:])
    except ValueError:
        raise ValueError('A valid extra activity identifier is required.')
    current = timezone.now().astimezone(ZoneInfo('Europe/London'))
    monthly = mapping(payload.get('monthlyAssignment'))
    month = monthly.get('month', current.strftime('%Y-%m'))
    if not valid_activity_month(month):
        raise ValueError('Choose a valid activity month (YYYY-MM).')
    return {**payload, 'monthlyAssignment': {**monthly, 'month': month, 'timeEntries': monthly.get('timeEntries', [])},
            'dateCompleted': current.date().isoformat(), 'plannedOtjh': ''}


def extra_activity_checks(payload, **dependencies):
    prepared = prepare_extra_activity(payload)
    checks = assignment_checks(prepared, meeting_booked=True, **dependencies)
    checks = [check for check in checks if check['key'] in EXTRA_CHECK_KEYS]
    return [{'key': 'title', 'label': 'Give your extra activity a title', 'passed': bool(text(payload.get('activityTitle')))}, *checks]


@learner_self_or_staff(query_param='learnerId')
def list_extra_activities(request):
    kind, learner_id = request.GET.get('learnerKind'), request.GET.get('learnerId')
    if kind not in ('commercial', 'apprenticeship') or not str(learner_id or '').isdigit():
        return JsonResponse({'error': 'A valid learner is required.'}, status=400)
    try:
        with connections['enrolment'].cursor() as cur:
            cur.execute('''SELECT activity_id, activity_title, status, submitted_at, full_submission,
                coach_feedback, reviewed_by, reviewed_at FROM "Learner".learning_reflection_submissions
                WHERE learner_kind=%s AND learner_id=%s AND activity_type='extra_activity'
                ORDER BY submitted_at DESC, id''', [kind, learner_id])
            rows = cur.fetchall()
    except DatabaseError:
        return JsonResponse({'error': 'Could not load extra activities. Please retry.'}, status=503)
    result = []
    for identifier, title, status, submitted, raw, feedback, reviewer, reviewed in rows:
        data = json.loads(raw) if isinstance(raw, str) else mapping(raw)
        selected_month = mapping(data.get('monthlyAssignment')).get('month')
        month = (selected_month if valid_activity_month(selected_month) else
                 submitted.astimezone(ZoneInfo('Europe/London')).strftime('%Y-%m') if submitted else '')
        if request.GET.get('month') and (status == 'draft' or month != request.GET['month']):
            continue
        result.append({'activityId': identifier, 'title': title, 'status': status,
                       'submittedAt': submitted.isoformat() if submitted else None, 'month': month,
                       'answer': data.get('assignmentAnswer', ''), 'reflection': data.get('whatYouLearned', ''),
                       'ksbs': data.get('ksbCodes', []), 'hours': data.get('actualTimeHours', ''),
                       'coachFeedback': feedback, 'reviewedBy': reviewer,
                       'reviewedAt': reviewed.isoformat() if reviewed else None})
    return JsonResponse({'activities': result})
