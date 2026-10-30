"""Read-only holiday source for activity completion declarations.

This does not synchronize feeds, alter curriculum schedules or restrict access.

With ``?componentId=`` the answer is scoped to the holidays that actually apply
to that component's cohort, resolved through ``working_rules`` -- the same
curriculum resolver the Module Builder and the learner timeline use. A closure
authored against another programme therefore never reaches this learner. The
unscoped answer is kept for callers that have no component in hand.
"""
from django.db import DatabaseError, connection
from django.http import JsonResponse

from login.permissions import login_required

from .working_rules import learner_holiday_details


def working_hours_holiday_ranges():
    with connection.cursor() as cursor:
        cursor.execute('''
            SELECT holiday_date, holiday_date
              FROM curriculum.england_holidays
             WHERE division = %s
            UNION
            SELECT start_date, COALESCE(end_date, start_date)
              FROM curriculum.holidays
             WHERE is_archived IS NOT TRUE
               AND start_date IS NOT NULL
               AND COALESCE(end_date, start_date) >= start_date
            ORDER BY 1, 2
        ''', ['england-and-wales'])
        return [{'start': str(start), 'end': str(end)} for start, end in cursor.fetchall()]


def component_holiday_ranges(component_id, quiz_id=None):
    """One closed day per range, each named, for this activity's cohort.

    A day is emitted per closed date rather than per holiday period so the
    completion dialog can name the holiday on whichever day the learner picks
    without re-deriving the period arithmetic in the browser.
    """
    details = learner_holiday_details(component_id, quiz_id=quiz_id)
    return [
        {
            'start': day.isoformat(),
            'end': day.isoformat(),
            'label': next(
                (str((item or {}).get('label') or '').strip()
                 for item in rows if str((item or {}).get('label') or '').strip()),
                '',
            ),
        }
        for day, rows in sorted(details.items())
    ]


def is_working_hours_holiday(day):
    key = day.isoformat()
    return any(row['start'] <= key <= row['end'] for row in working_hours_holiday_ranges())


@login_required
def working_hours_holidays(request):
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    component_id = (request.GET.get('componentId') or '').strip()
    quiz_id = (request.GET.get('quizId') or '').strip()
    try:
        ranges = (
            component_holiday_ranges(component_id, quiz_id or None)
            if (component_id or quiz_id)
            else working_hours_holiday_ranges()
        )
    except DatabaseError:
        return JsonResponse({'error': 'Could not load the working-hours holiday calendar. Please try again.'}, status=503)
    return JsonResponse({'holidays': ranges})
