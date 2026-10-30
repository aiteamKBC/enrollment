"""Compact, paged Case File register. Never settle catch-up outcomes."""
from django.utils import timezone


class AttendancePaginationError(ValueError):
    pass


def pagination_params(params):
    try:
        page, size = int(params.get('page', 1)), int(params.get('pageSize', 20))
    except (ValueError, TypeError) as error:
        raise AttendancePaginationError('Invalid attendance page or pageSize.') from error
    if page < 1 or not 1 <= size <= 100:
        raise AttendancePaginationError('Invalid attendance page or pageSize.')
    return page, size


def project_register(rows, params, *, now=None):
    from learner_api.attendance_rules import attendance_outcome
    now = now or timezone.now()
    counted = [(row, attendance_outcome(row, now)[0]) for row in rows
               if attendance_outcome(row, now)[1]]
    absent = sum(status == 'absent' for _, status in counted)
    total = len(counted)
    summary = {'sessions': total, 'present': total - absent, 'absent': absent,
               'attendanceRate': round((total - absent) / total * 100) if total else 0,
               'outstandingAbsences': absent}
    sessions = [{'id': f"{row.get('session_id', '')}-{row['session_date'].isoformat()}",
                 'title': row.get('session_title') or '', 'date': row['session_date'].isoformat(),
                 'status': status, 'reason': row.get('absence_reason') or None}
                for row, status in counted]
    sessions.sort(key=lambda row: (row['date'], row['id']), reverse=True)
    months = sorted({row['date'][:7] for row in sessions}, reverse=True)
    search = params.get('search', '').strip().casefold()
    status, month = params.get('status', 'all'), params.get('month', 'all')
    sessions = [row for row in sessions if (status == 'all' or row['status'] == status)
                and (month == 'all' or row['date'].startswith(month))
                and (not search or any(search in str(row[key] or '').casefold() for key in ('title', 'reason')))]
    page, size = pagination_params(params)
    start = (page - 1) * size
    return {'summary': summary, 'sessions': sessions[start:start + size], 'months': months,
            'pagination': {'page': page, 'pageSize': size, 'total': len(sessions),
                           'hasMore': start + size < len(sessions)}}


def read_attendance(context, params):
    from learner_api.attendance_lectures import lecture_register
    if context.source is None:
        raise LookupError('Learner enrolment is unavailable.')
    pagination_params(params)
    return project_register(lecture_register(context.source, learner_profile_id=context.profile.id), params)
