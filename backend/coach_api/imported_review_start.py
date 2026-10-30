"""Status-only start shared by explicit Coach Join and verified resource writes."""
from django.utils import timezone

from .migrated_session_scope import (booking_window, current_reports, instant,
                                     meeting_artifacts, meeting_reports, select_artifacts)


def start_locked(overlay, calendar, family, *, source_completed=False):
    """Caller has authorized the source/booking and locked calendar -> overlay.

    Never initialize, synchronize a template, sign, submit, or bind an answer.
    An archived revision is not the current Review's lifecycle state.
    """
    if (not overlay or family not in {'MCM', 'PR', 'PR_SKILLS_RADAR', 'PSP'}
            or not (overlay.template_snapshot or {}).get('sections')
            or overlay.status not in {'scheduled', 'in-progress'}
            or overlay.completed_at
            or source_completed
            or overlay.owner_email.casefold() != calendar.owner_email.casefold()
            or calendar.event_type != ('mcr' if family == 'MCM' else 'progress-review')
            or calendar.status not in {'scheduled', 'in-progress'}
            or calendar.sync_state != 'synced' or not calendar.graph_event_id
            or not (calendar.meeting_link or calendar.graph_web_link)
            or not booking_window(calendar)[0]
            or overlay.migrated_signatures.exists()):
        return False
    if overlay.status == 'in-progress':
        return True  # Retry must not write or repair a different lifecycle.
    overlay.status = 'in-progress'
    overlay.save(update_fields=['status', 'updated_at'])
    if calendar.status == 'scheduled':
        calendar.status = 'in-progress'
        calendar.save(update_fields=['status', 'updated_at'])
    return True


def has_start_evidence(record, snapshot, *, session=None, meeting_id=None):
    """Fresh verified evidence only; tracker labels and stored recaps are not proof."""
    from .views import coach_attendance_record_confirms_expected

    now = timezone.now()
    failures = snapshot.get('resourceErrors') or {}
    expected = [row for row in (snapshot.get('attendance') or {}).get('expectedAttendees', [])
                if row.get('role') in {'coach', 'learner'}]
    if not failures.get('attendance'):
        reports = (meeting_reports(snapshot)[0] if meeting_id else
                   current_reports(record, snapshot, session=session))
        for report in reports:
            if instant(report.get('meetingEndDateTime')) > now:
                continue
            for row in report.get('records', []):
                seconds = row.get('totalAttendanceSeconds')
                if (isinstance(seconds, (int, float)) and seconds > 0
                        and any(coach_attendance_record_confirms_expected(row, person) for person in expected)):
                    return True
    if not snapshot.get('transcriptCollectionComplete') or failures.get('transcripts'):
        return False
    selected, error = (meeting_artifacts(record, snapshot, meeting_id)
                       if meeting_id else select_artifacts(record, snapshot, session=session))
    transcripts = [row for row in selected if row.get('artifact_type') == 'transcript']
    return bool(not error and transcripts and all(
        row.get('graph_artifact_id') and not row.get('transcript_fetch_error')
        and str(row.get('transcript_text') or '').strip()
        and instant(row.get('created_datetime') or (row.get('metadata') or {}).get('createdDateTime'))
        and instant(row.get('created_datetime') or (row.get('metadata') or {}).get('createdDateTime')) <= now
        and (not (end := row.get('end_datetime') or (row.get('metadata') or {}).get('endDateTime'))
             or (instant(end) is not None and instant(end) <= now))
        for row in transcripts))
