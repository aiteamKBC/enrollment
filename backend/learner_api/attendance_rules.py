"""Canonical attendance outcomes and eligibility, independent of storage."""
from datetime import time, timezone as utc
from django.utils import timezone

INVALID = {'cancelled', 'canceled', 'deleted', 'failed', 'superseded'}

def canonical_status(value):
    value = str(value or '').strip().lower()
    if value in {'present', 'late', 'attended', 'completed', 'made_up'}:
        return 'present'
    if value in {'absent', 'missed'}:
        return 'absent'
    return value if value in {'upcoming', 'in_progress', 'awaiting_recording'} else 'unmarked'

def attendance_outcome(row, now=None):
    """Return canonical status and counted flag; unmarked never implies absence."""
    now = now or timezone.now()
    local = timezone.localtime(now)
    raw = row.get('attendance_status')
    effective = row.get('effective_attendance_status') or raw
    status = canonical_status(effective)
    credit = row.get('effective_attendance')
    if credit == 1:
        status = 'present'
    elif credit == 0 and status in {'present', 'absent'}:
        status = 'absent'
    def aware(value):
        return timezone.make_aware(value, utc.utc) if value and timezone.is_naive(value) else value
    start, end = aware(row.get('scheduled_start')), aware(row.get('scheduled_end'))
    day = row.get('session_date')
    future = (start > now) if start else bool(day and (day, row.get('session_start_time') or time.min) > (local.date(), local.time().replace(tzinfo=None)))
    active = bool(start and end and start <= now < end)
    if future:
        status = 'upcoming'
    elif active:
        status = 'in_progress'
    invalid = any(str(row.get(key) or '').strip().lower() in INVALID for key in ('status', 'session_status', 'occurrence_status', 'attendance_status'))
    counted = bool(day and not future and not active and not invalid and status in {'present', 'absent'})
    return status, counted
