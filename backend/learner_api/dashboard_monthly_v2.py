"""UI projection of canonical Monthly facts; no database or mutation helpers."""
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

UK = ZoneInfo('Europe/London')
MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December')
SHORT_MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sept', 'Oct', 'Nov', 'Dec')


def hours(value, *, signed=False):
    if value is None:
        return 'Not available'
    # Match the established two-decimal display without changing source totals.
    number = format(Decimal.from_float(float(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP), 'f').rstrip('0').rstrip('.')
    if number == '-0':
        number = '0'
    return ('+' if signed and value > 0 else '') + number + ' hrs'


def date_label(value, today):
    if not value:
        return 'Dates coming soon'
    day = date.fromisoformat(str(value)[:10])
    return f'{day.day} {SHORT_MONTHS[day.month - 1]}' + (f' {day.year}' if day.year != today.year else '')


def instant(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return parsed.replace(tzinfo=ZoneInfo('UTC')) if parsed.tzinfo is None else parsed


def review_row(review, kind, learner_id, coach_name, now):
    day = review.get('scheduledDate') or review.get('targetDate') or review.get('date') or ''
    status = review.get('status')
    display_status = {'completed': 'Attended', 'scheduled': 'Booking pending' if review.get('invited') is False else 'Booked',
        'not-scheduled': 'Overdue' if day and day < now.date().isoformat() else 'Not booked',
        'awaiting-signature': 'Awaiting signatures', 'in-progress': 'In progress'}.get(status, 'Not booked')
    clock = review.get('scheduledTime')
    duration = review.get('durationMinutes')
    time_label = f'{str(clock)[:5]} · UK time' if clock else f'{duration} min' if duration else '—'
    form = None
    if review.get('reviewTemplateId') or review.get('reviewInstanceId'):
        base = {'mcr': '/learner/monthly-coaching', 'progress-review': '/learner/progress-reviews'}.get(review.get('source'))
        if base:
            form = f"{base}/{quote(str(review.get('eventKey') or review['id']), safe='')}?{urlencode({'kind': kind, 'learner': learner_id})}"
    booking = None
    if status == 'not-scheduled':
        # Only fields consumed by MeetingBookingDialog before it resolves the
        # authoritative calendar event. No duplicated target/scheduled dates.
        booking = {key: review.get(key) for key in ('id', 'eventKey', 'title', 'source', 'status', 'durationMinutes')}
        booking['date'] = day or None
        if clock:
            booking['scheduledTime'] = clock
    return {'id': str(review.get('eventKey') or review['id']), 'title': review['title'],
        'date': date_label(day, now.date()), 'time': time_label, 'status': display_status,
        'tooltip': review.get('coachName') or coach_name or '', 'formHref': form,
        'attendHref': review.get('meetingLink') if status in ('scheduled', 'in-progress') and safe_link(review.get('meetingLink')) else None,
        'booking': booking}


def safe_link(value):
    return isinstance(value, str) and value.lower().startswith(('http://', 'https://'))


def assignment_rows(learning, subjects, month, kind, learner_id):
    """TrainingPlanDetails' dated assignment selection, using reconciled identities.

    Reuse the already read learning facts. Builder dates include empty/holiday
    weeks; ambiguous historical dates remain excluded. Do not read a second
    weekly/plan response or construct cards beyond the visible two.
    """
    from .progress_rules import progress_counts_as_achieved
    native = learning.get('detail') or {}
    components = {c.get('componentId'): c for c in native.get('components', []) if c.get('componentId')}
    legacy = {(f"legacy:{a['group_id']}", a['activity_id']): a for a in (learning.get('activity') or {}).get('activities', [])}
    completed = {str(r['componentId']) for bucket in ('videoProgress', 'componentProgress') for r in native.get(bucket, [])
                 if r.get('componentId') and progress_counts_as_achieved(r.get('kind'), r.get('passed'))}
    from heapq import nsmallest
    candidates = ((s, a) for s in subjects for a in s['activities']
                  if 'assignment' in str(a.get('category') or '').lower()
                  and str(a.get('date') or '').startswith(month) and not a.get('needsReview'))
    # nsmallest preserves source order for tied keys, like the previous stable sort.
    candidates = nsmallest(2, candidates, key=lambda row: (row[1]['date'], str(row[1].get('title') or '').casefold()))
    result = []
    for subject, activity in candidates:
        historical = legacy.get((subject['id'], activity['id'])) or {}
        refs = activity.get('nativeRefs') or [activity.get('nativeRef') or activity['id']]
        component = next((components[ref] for ref in refs if ref in components), {})
        component_id = component.get('componentId')
        done = bool(historical.get('completed')) or any(str(ref) in completed for ref in refs)
        if component.get('isQuiz'):
            quiz = (component.get('quizMeta') or {}).get('quizId')
            done |= any(a.get('passed') is True and (a.get('componentId') == component_id if a.get('componentId')
                else quiz is not None and str(a.get('quizId')) == str(quiz)) for a in native.get('quizAttempts', []))
        expected = component.get('expectedOtjh')
        if expected is None:
            expected = historical.get('planned')
        week = component.get('week') or historical.get('section_title') or ''
        href = (f"/learner/component/{kind}/{learner_id}/{quote(str(component_id), safe='')}?{urlencode({'week': week}, quote_via=quote)}"
                if component_id else f"/learner/modules/{kind}/{learner_id}?subject={quote(subject['id'], safe='')}")
        expected_label = hours(expected) if expected is not None else '— hrs'
        result.append({'id': f"{subject['id']}:{activity['id']}", 'title': activity.get('title') or 'Assignment',
            'subtitle': week or subject['title'], 'requiredHours': expected_label,
            'achievedHours': expected_label if done else '0 hrs', 'progress': '100%' if done else '0%',
            'progressLabel': '100% complete' if done else '0% complete', 'href': href, 'actionLabel': 'View' if done else 'Start'})
    return result


def lecture_row(session, lecture, title, tutor, now):
    start, end = instant(session['start']), instant(session.get('end'))
    available = session.get('status') not in ('cancelled', 'deleted', 'completed') and (end or start) > now
    catchup = None
    absence = None
    if lecture:
        status = lecture.get('status')
        if status == 'present':
            raw = lecture.get('rawStatus') or lecture.get('rawAttendanceStatus')
            status = 'absent' if raw == 'absent' and lecture.get('effectiveAttendance') == 1 else 'late' if raw == 'late' else 'completed'
        recovery = lecture.get('recovery') or {}
        catchup_status = lecture.get('catchupStatus')
        if status == 'absent' and lecture.get('effectiveAttendance') != 1:
            plan_open = recovery.get('calendarKey') and (not recovery.get('ended') or recovery.get('method') == 'recorded') and catchup_status != 'missed'
            if plan_open:
                if recovery.get('method') != 'alternative':
                    catchup = 'Change catch-up' if recovery.get('method') == 'catch-up' else 'Book catch-up'
            elif catchup_status != 'completed':
                catchup = 'Change catch-up' if recovery.get('method') == 'catch-up' and catchup_status == 'pending' else 'Book Catchup Session'
        report_absence = not catchup and status != 'absent' and bool(lecture.get('canReportAbsence'))
        if catchup or report_absence:
            absence = {'id': lecture['id'], 'date': lecture['date'], 'title': lecture['title'],
                'reportId': (lecture.get('absenceReport') or {}).get('id')}
    return {'id': str(session['id']), 'date': date_label(start.astimezone(UK).date(), now.date()),
        'time': start.astimezone(UK).strftime('%H:%M'), 'title': title, 'tutor': tutor,
        'duration': f"{session.get('minutes')} min", 'attendHref': session.get('joinUrl') if available and safe_link(session.get('joinUrl')) else None,
        'status': 'Link pending' if available and not safe_link(session.get('joinUrl')) else '',
        'absenceLabel': catchup or ('Report absence' if absence else None),
        'absenceAction': 'catchup' if catchup else 'absence' if absence else None, 'absence': absence}


def response(month, navigation, required, achieved, codes, selected, reviews, lectures, kind, learner_id, coach_name, now):
    difference = achieved - required if achieved is not None and required is not None else None
    tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=UK)
    return {'version': 2, 'month': month, 'title': f'{MONTHS[int(month[5:]) - 1]} {month[:4]}', 'navigation': navigation,
        'summary': {'requiredHours': hours(required), 'achievedHours': hours(achieved), 'difference': hours(difference, signed=True),
            'differenceTone': 'positive' if difference is not None and difference >= 0 else None,
            'differenceDirection': 'down' if difference is not None and difference < 0 else 'up' if difference is not None and difference > 0 else 'equal',
            'ksbs': codes},
        'activitiesHref': f"/learner/modules/{kind}/{learner_id}?subject={quote(selected['id'], safe='')}" if selected else None,
        # Refresh at the earliest business boundary; the browser only schedules
        # this deadline and never recalculates attendance/meeting eligibility.
        'refreshAt': tomorrow.isoformat(),
        'reviews': [review_row(r, kind, learner_id, coach_name, now) for r in reviews],
        'assignments': [], 'lectures': lectures}
