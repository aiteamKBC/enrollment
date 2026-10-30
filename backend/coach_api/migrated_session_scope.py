"""Select verified imported meeting evidence and retain legacy booked-call reads."""
from datetime import datetime, timedelta, timezone as utc_timezone
from dataclasses import dataclass
import hashlib
import json
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime


def instant(value):
    try:
        value = parse_datetime(value) if isinstance(value, str) else value
        return value.astimezone(utc_timezone.utc) if isinstance(value, datetime) and timezone.is_aware(value) else None
    except (TypeError, ValueError):
        return None


def booking_window(record):
    if not record.scheduled_date or not record.scheduled_time:
        return None, None
    start = datetime.combine(record.scheduled_date, record.scheduled_time, ZoneInfo(settings.TIME_ZONE))
    start = start.astimezone(utc_timezone.utc)
    return start, start + timedelta(minutes=record.duration_minutes or 60)


def report_window(report):
    return instant(report.get('meetingStartDateTime')), instant(report.get('meetingEndDateTime'))


@dataclass(frozen=True)
class SessionContext:
    """Parsed claim scope; callers must separately prove database exclusivity."""
    call_id: str
    report_id: str
    online_meeting_id: str
    selection_version: int


def booking_fingerprint(record):
    return hashlib.sha256(json.dumps(session_provenance(record), sort_keys=True).encode()).hexdigest()


def session_context_from_claim(record, claim):
    """Reject stale or malformed JSON; this does not establish claim uniqueness."""
    if not isinstance(claim, dict) or claim.get('schemaVersion') != 1 or claim.get('selectionVersion') != 1:
        return None
    expected = {'eventKey': record.event_key, 'calendarId': record.pk,
        'profileId': record.learner_id, 'ownerEmail': record.owner_email,
        'graphEventId': record.graph_event_id, 'bookingFingerprint': booking_fingerprint(record)}
    if any(claim.get(key) != value for key, value in expected.items()):
        return None
    ids = [claim.get(key) for key in ('onlineMeetingId', 'callId', 'reportId')]
    if any(not isinstance(value, str) or not value or not value.isascii()
           or value != value.strip() or len(value) > 240 for value in ids):
        return None
    return SessionContext(ids[1], ids[2], ids[0], 1)


def current_reports(record, snapshot, *, session=None):
    if session is not None:
        matches = [row for row in snapshot.get('attendanceReports', [])
                   if isinstance(row, dict) and row.get('id') == session.report_id]
        if len(matches) != 1:
            return []
        start, end = report_window(matches[0])
        return matches if start and end and start < end else []
    start, end = booking_window(record)
    if not start:
        return []
    return [report for report in snapshot.get('attendanceReports', [])
            if (bounds := report_window(report))[0] and bounds[1]
            and bounds[0] < bounds[1] and bounds[0] < end and bounds[1] > start]


def meeting_reports(snapshot, *, meeting_id=None, graph_event_id=None, require_verified=False):
    """Reports fetched under a verified onlineMeeting, without a call claim.

    A report is meeting-level evidence. Its dates do not identify a media call.
    """
    seen = {}
    for report in snapshot.get('attendanceReports') or []:
        if not isinstance(report, dict) or not isinstance(report.get('id'), str) or not report['id']:
            return [], 'ATTENDANCE_REPORT_AMBIGUOUS'
        saved_meeting = report.get('_lms_verified_online_meeting_id')
        saved_event = report.get('_lms_graph_event_id')
        if require_verified and (not saved_meeting or not saved_event):
            continue  # Retain legacy rows in SQL without attributing them anew.
        if (meeting_id and saved_meeting not in (None, meeting_id)
                or graph_event_id and saved_event not in (None, graph_event_id)):
            return [], 'ATTENDANCE_REPORT_AMBIGUOUS'
        if meeting_id and report.get('meetingId') not in (None, meeting_id):
            return [], 'ATTENDANCE_REPORT_AMBIGUOUS'
        start, end = report_window(report)
        if not start or not end or start >= end:
            return [], 'ATTENDANCE_REPORT_AMBIGUOUS'
        if report['id'] in seen and seen[report['id']] != report:
            return [], 'ATTENDANCE_REPORT_AMBIGUOUS'
        seen[report['id']] = report
    return list(seen.values()), None


def has_verified_meeting_evidence(snapshot):
    """Choose the meeting reader when a saved media or report has its scope marker."""
    return (any((row.get('metadata') or {}).get('_lms_verified_online_meeting_id')
                for row in snapshot.get('artifacts') or [] if isinstance(row, dict))
            or any(row.get('_lms_verified_online_meeting_id')
                   for row in snapshot.get('attendanceReports') or [] if isinstance(row, dict)))


def reconcile_meeting_reports(previous, incoming):
    """Keep verified report rows when a later read has less evidence."""
    def count(value):
        try:
            return max(int(value or 0), 0)
        except (TypeError, ValueError):
            return 0

    retained = {row['id']: row for row in previous}
    reduced = False
    for row in incoming:
        old = retained.get(row['id'])
        if old is None:
            retained[row['id']] = row
            continue
        old_records = {item.get('id'): item for item in old.get('records') or []
                       if isinstance(item, dict) and item.get('id')}
        new_records = {item.get('id'): item for item in row.get('records') or []
                       if isinstance(item, dict) and item.get('id')}
        reduced_here = any(
            old.get(key) and row.get(key) != old[key]
            for key in ('meetingStartDateTime', 'meetingEndDateTime'))
        reduced_here = (reduced_here
            or len(old.get('records') or []) > len(row.get('records') or [])
            or any(count(old.get(key)) > count(row.get(key))
                   for key in ('totalParticipantCount', 'attendedCount')))
        reduced_here = reduced_here or any(
            key not in new_records
            or count(new_records[key].get('totalAttendanceSeconds'))
               < count(item.get('totalAttendanceSeconds'))
            or (item.get('email') and new_records[key].get('email') != item['email'])
            for key, item in old_records.items())
        if reduced_here:
            reduced = True
        else:
            retained[row['id']] = row
    return list(retained.values()), reduced


def meeting_artifacts(record, snapshot, meeting_id, *, trusted_fetch=False):
    """Select independently verified media from one onlineMeeting.

    Fresh rows are trusted only when the caller fetched them under the strictly
    resolved Graph target. Stored rows require that target's persisted marker.
    A contradictory row is excluded without hiding other verified resources.
    """
    if not meeting_id or not record.graph_event_id:
        return [], 'MEETING_TARGET_UNVERIFIED'
    selected, seen, conflicted, conflict = [], {}, set(), False
    for row in snapshot.get('artifacts') or []:
        if not isinstance(row, dict):
            conflict = True
            continue
        metadata = row.get('metadata') or {}
        if not isinstance(metadata, dict):
            conflict = True
            continue
        kind, artifact_id = row.get('artifact_type'), row.get('graph_artifact_id')
        if kind not in {'transcript', 'recording'} or not isinstance(artifact_id, str) or not artifact_id:
            conflict = True
            continue
        provider_meeting = metadata.get('meetingId')
        saved_meeting = metadata.get('_lms_verified_online_meeting_id')
        saved_event = metadata.get('_lms_graph_event_id')
        if (provider_meeting not in (None, meeting_id)
                or saved_meeting not in (None, meeting_id)
                or saved_event not in (None, record.graph_event_id)
                or (not trusted_fetch and (saved_meeting != meeting_id
                                          or saved_event != record.graph_event_id))):
            conflict = True
            continue
        call = row.get('call_id') or metadata.get('callId')
        if row.get('call_id') and metadata.get('callId') and row['call_id'] != metadata['callId']:
            conflict = True
            continue
        key = kind, artifact_id
        identity = call, row.get('created_datetime'), row.get('end_datetime')
        if key in seen:
            if seen[key] != identity:
                conflict = True
                conflicted.add(key)
            continue
        seen[key] = identity
        selected.append(row)
    return [row for row in selected if (row['artifact_type'], row['graph_artifact_id']) not in conflicted], (
        'MEETING_ARTIFACT_CONFLICT' if conflict else None)


def validate_attendance_response(response, *, report=None):
    """Imported refresh must not turn missing/partial records into absence.

    Keep the generic/Native serializer unchanged. Unsupported continuation is
    an explicit resource failure, so a previously verified report can survive.
    """
    if report is None:
        if (not isinstance(response, dict) or not isinstance(response.get('value'), list)
                or response.get('@odata.nextLink')
                or any(not isinstance(row, dict) or not isinstance(row.get('id'), str)
                       or not row['id'].strip() for row in response['value'])):
            raise RuntimeError('Teams returned an incomplete attendance report collection.')
        return
    if (not isinstance(response, dict) or response.get('id') != report.get('id')
            or not isinstance(response.get('attendanceRecords'), list)
            or any(not isinstance(row, dict) for row in response['attendanceRecords'])
            or response.get('attendanceRecords@odata.nextLink') or response.get('@odata.nextLink')):
        raise RuntimeError('Teams returned incomplete or mismatched attendance report details.')


def attendance_reports_for_refresh(record, snapshot, previous, *, preserve_existing=False, session=None):
    """Preserve current-target stored evidence on an incomplete imported fetch.

    The stored snapshot reader already qualifies reports by Graph event. Both
    inputs still need booking-window qualification. Fresh complete copies win
    by report ID; one report must never contribute its duration twice.
    """
    fresh = current_reports(record, snapshot, session=session)
    failed = ((snapshot.get('resourceErrors') or {}).get('attendance')
              or any('attendance' in message.lower() for message in snapshot.get('errors', [])))
    cached = current_reports(record, previous, session=session) if preserve_existing or failed or not fresh else []
    return list({row['id']: row for row in [*cached, *fresh] if row.get('id')}.values())


def snapshot_version(snapshot):
    """Optimistic resource version, without relying on clocks across workers."""
    from django.core.serializers.json import DjangoJSONEncoder
    state = {key: snapshot.get(key) for key in ('artifacts', 'attendanceReports', 'attendanceTracker', 'storage')}
    return hashlib.sha256(json.dumps(state, cls=DjangoJSONEncoder, sort_keys=True).encode()).hexdigest()


def persist_imported_snapshot(record, snapshot, *, expected_version, revalidate=None, preserve_existing=False, context=None, start_review=None, session=None, meeting_id=None):
    """Serialize imported writers; reject responses overtaken during Graph I/O.

    Providers run before this function. The current calendar, stored evidence,
    and unchanged snapshot SQL use the same write connection/transaction.
    Native callers retain their original persistence path.
    """
    from django.db import router, transaction
    from django.db.models import Q
    from .models import CoachCalendarEvent, ImportedReviewInstance
    from .views import persist_coach_meeting_snapshots, stored_coach_meeting_snapshot

    database = router.db_for_write(CoachCalendarEvent) or 'default'
    with transaction.atomic(using=database):
        current = CoachCalendarEvent.objects.using(database).select_for_update().get(pk=record.pk)
        # Same calendar -> overlay order as imported manual/reopen writers.
        # No ensure/get_or_create helper: background writes resources only.
        overlay = None
        if context and context.get('overlayId'):
            overlay = ImportedReviewInstance.objects.using(database).select_for_update().get(pk=context['overlayId'])
        if session is not None:
            from .bound_actual_session import BoundSessionConflict, bound_session
            try:
                bound = bound_session(current, overlay=overlay, lock=True)
            except BoundSessionConflict:
                return {'stored': False, 'reason': 'snapshot_context_changed'}
            if bound is None or bound.context != session:
                return {'stored': False, 'reason': 'snapshot_context_changed'}
        if (refresh_basis(current) != refresh_basis(record)
                or (revalidate is not None and not revalidate(current))):
            return {'stored': False, 'reason': 'snapshot_context_changed'}
        shared_meeting = Q(graph_event_id=current.graph_event_id)
        if current.meeting_link:
            shared_meeting |= Q(meeting_link=current.meeting_link)
        if meeting_id and CoachCalendarEvent.objects.using(database).filter(
                shared_meeting).exclude(pk=current.pk).exists():
            # A second local Review may have adopted this provider meeting
            # while Graph was being queried. Never publish to an ambiguous one.
            return {'stored': False, 'reason': 'snapshot_context_changed'}
        previous = stored_coach_meeting_snapshot(current, database=database, safe_diagnostics=True)
        storage = previous.get('storage') or {}
        if not storage.get('stored') or previous.get('partial'):
            return {'stored': False, 'reason': 'snapshot_read_unavailable'}
        if snapshot_version(previous) != expected_version:
            return {'stored': False, 'reason': 'snapshot_superseded'}
        from .imported_review_start import has_start_evidence, start_locked
        # Evaluate the unfiltered fresh collection: discarding a failed segment
        # before this check would make an incomplete transcript appear complete.
        start_evidence = has_start_evidence(current, snapshot, session=session, meeting_id=meeting_id)
        if meeting_id:
            fresh_reports, report_error = meeting_reports(snapshot, meeting_id=meeting_id,
                graph_event_id=current.graph_event_id, require_verified=True)
            old_reports, old_error = meeting_reports(previous, meeting_id=meeting_id,
                graph_event_id=current.graph_event_id, require_verified=True)
            if report_error or old_error:
                return {'stored': False, 'reason': 'snapshot_context_changed'}
            reports, reduced_reports = reconcile_meeting_reports(old_reports, fresh_reports)
        else:
            fresh_reports = current_reports(current, snapshot, session=session)
            reports = attendance_reports_for_refresh(current, snapshot, previous, preserve_existing=preserve_existing, session=session)
        resources = snapshot['artifacts']
        if session and (snapshot.get('resourceErrors') or {}).get('recordings'):
            resources = [row for row in resources if row.get('artifact_type') != 'recording']
        scoped = scope_snapshot(current, {**snapshot, 'artifacts': resources,
                                         'attendanceReports': reports}, session=session,
                                meeting_scoped=bool(meeting_id), meeting_id=meeting_id)
        incomplete_content = bool(meeting_id and any(
            row.get('artifact_type') == 'transcript'
            and (not row.get('transcript_text') or not row.get('transcript_vtt'))
            for row in scoped['artifacts']))
        if meeting_id and not reports and previous.get('attendanceReports'):
            # Earlier snapshots have no meeting marker. Keep their tracker
            # current in SQL until verified meeting reports can replace it;
            # the meeting-scoped response still withholds unverified rows.
            scoped['attendanceTracker'] = previous.get('attendanceTracker') or scoped['attendanceTracker']
        for key in ('attendance', 'attendanceReports', 'attendanceTracker'):
            snapshot[key] = scoped[key]
        if context is not None:
            # Background resource writes must not erase a stored transcript
            # after a failed download, or attach a different call's content.
            snapshot['artifacts'] = ([] if scoped['selectionError'] and not meeting_id else
                [row for row in scoped['artifacts'] if not row.get('transcript_fetch_error')
                 and not (meeting_id and row.get('artifact_type') == 'transcript'
                          and (not row.get('transcript_text') or not row.get('transcript_vtt')))])
        result = persist_coach_meeting_snapshots(current, artifacts=snapshot['artifacts'],
            attendance_reports=snapshot['attendanceReports'], attendance_tracker=snapshot['attendanceTracker'],
            safe_diagnostics=True)
        # Manual Check Session can defer Start until it evaluates the original,
        # unfiltered collection under its already-held parent/overlay locks.
        if result.get('stored') and start_evidence and start_review is not False:
            if start_review is not None:
                owned, family, completed = start_review
            else:
                from .background_meeting_sync import imported_context
                qualified = context if context is not None else imported_context(current)
                owned = overlay
                if qualified and qualified.get('overlayId') and owned is None:
                    owned = ImportedReviewInstance.objects.using(database).select_for_update().get(pk=qualified['overlayId'])
                    # Revalidate after acquiring the overlay lock (e.g. reopen).
                    if imported_context(current) != qualified:
                        return {**result, 'reviewStarted': False}
                family = qualified.get('family') if qualified else None
                completed = qualified.get('completed', False) if qualified else False
            if owned and start_locked(owned, current, family, source_completed=completed):
                record.status = current.status
                result['reviewStarted'] = True
        if context is not None:
            result['resourceRefresh'] = ('partial' if snapshot.get('partial') or scoped['selectionError']
                or incomplete_content or (meeting_id and reduced_reports)
                or any(row.get('transcript_fetch_error') for row in scoped['artifacts']) else
                'updated' if fresh_reports or snapshot['artifacts'] else 'pending')
        return result


def refresh_basis(record):
    return session_provenance(record) | {key: getattr(record, key, None) for key in (
        'event_type', 'sync_state', 'status', 'graph_web_link', 'idempotency_key',
    )}


def select_artifacts(record, snapshot, *, session=None):
    """Use report timing when available, otherwise the booked interval.

    Multiple call IDs/windows or undated transcripts are ambiguous. Never infer
    that an arbitrary first/last transcript belongs to this learner's session.
    """
    if session is not None:
        selected, seen = [], {}
        for row in snapshot.get('artifacts') or []:
            if not isinstance(row, dict):
                return [], 'TRANSCRIPT_SESSION_AMBIGUOUS'
            metadata = row.get('metadata') or {}
            if not isinstance(metadata, dict):
                return [], 'TRANSCRIPT_SESSION_AMBIGUOUS'
            call = row.get('call_id') or metadata.get('callId')
            if call != session.call_id:
                continue
            if (metadata.get('callId') not in (None, session.call_id)
                    or metadata.get('meetingId') not in (None, session.online_meeting_id)
                    or metadata.get('_lms_graph_event_id') not in (None, record.graph_event_id)):
                return [], 'TRANSCRIPT_SESSION_AMBIGUOUS'
            key = (row.get('artifact_type'), row.get('graph_artifact_id'))
            if key[0] not in {'transcript', 'recording'} or not key[1]:
                return [], 'TRANSCRIPT_SESSION_AMBIGUOUS'
            start = instant(row.get('created_datetime') or metadata.get('createdDateTime'))
            end = instant(row.get('end_datetime') or metadata.get('endDateTime'))
            if not start or not end or end < start:
                return [], 'TRANSCRIPT_SESSION_AMBIGUOUS'
            basis = (call, row.get('created_datetime'), row.get('end_datetime'))
            if key in seen and seen[key] != basis:
                return [], 'TRANSCRIPT_SESSION_AMBIGUOUS'
            seen[key] = basis
            selected.append(row)
        return selected, None
    start, end = booking_window(record)
    windows = set(report_window(report) for report in current_reports(record, snapshot))
    if len(windows) > 1:
        return [], 'TRANSCRIPT_SESSION_AMBIGUOUS'
    if windows:
        start, end = next(iter(windows))
    if not start:
        return [], 'TRANSCRIPT_SESSION_AMBIGUOUS'
    rows = snapshot.get('artifacts') or []
    anchored, undated = [], []
    for row in rows:
        metadata = row.get('metadata') or {}
        first = instant(row.get('created_datetime') or metadata.get('createdDateTime'))
        last = instant(row.get('end_datetime') or metadata.get('endDateTime'))
        first, last = first or last, last or first
        if not first or last < first:
            undated.append(row)
        elif (start <= first < end if first == last else first < end and last > start):
            anchored.append(row)
    calls = {row.get('call_id') or (row.get('metadata') or {}).get('callId') for row in anchored}
    calls.discard(None)
    calls.discard('')
    if len(calls) > 1:
        return [], 'TRANSCRIPT_SESSION_AMBIGUOUS'
    transcripts = [row for row in anchored if row.get('artifact_type') == 'transcript']
    if len(transcripts) > 1 and any(not (row.get('call_id') or (row.get('metadata') or {}).get('callId')) for row in transcripts):
        return [], 'TRANSCRIPT_SESSION_AMBIGUOUS'
    selected = list(anchored)
    ambiguous = False
    for row in rows:
        if row in anchored:
            continue
        call = row.get('call_id') or (row.get('metadata') or {}).get('callId')
        if call and call in calls:
            selected.append(row)  # Later transcript segments of the same call.
        elif row in undated and row.get('artifact_type') == 'transcript':
            ambiguous = True
    return selected, 'TRANSCRIPT_SESSION_AMBIGUOUS' if ambiguous else None


def scope_snapshot(record, snapshot, *, session=None, meeting_scoped=False, meeting_id=None):
    from .views import build_coach_meeting_attendance_tracker

    if meeting_scoped:
        if meeting_id is None:
            saved_ids = {(row.get('metadata') or {}).get('_lms_verified_online_meeting_id')
                         for row in snapshot.get('artifacts') or []
                         if isinstance(row, dict) and isinstance(row.get('metadata'), dict)
                         and (row.get('metadata') or {}).get('_lms_graph_event_id') == record.graph_event_id
                         and (row.get('metadata') or {}).get('_lms_verified_online_meeting_id')}
            saved_ids.update(row.get('_lms_verified_online_meeting_id')
                             for row in snapshot.get('attendanceReports') or []
                             if isinstance(row, dict)
                             and row.get('_lms_graph_event_id') == record.graph_event_id
                             and row.get('_lms_verified_online_meeting_id'))
            meeting_id = next(iter(saved_ids)) if len(saved_ids) == 1 else ''
        reports, report_error = (meeting_reports(snapshot, meeting_id=meeting_id,
            graph_event_id=record.graph_event_id, require_verified=True)
            if meeting_id else ([], 'ATTENDANCE_REPORT_AMBIGUOUS'
                                if snapshot.get('attendanceReports') else None))
        artifacts, artifact_error = (meeting_artifacts(record, snapshot, meeting_id)
                                     if meeting_id else ([], 'MEETING_ARTIFACT_CONFLICT'
                                                         if snapshot.get('artifacts') else None))
        selection_error = report_error or artifact_error
    else:
        reports = current_reports(record, snapshot, session=session)
        artifacts, selection_error = select_artifacts(record, snapshot, session=session)
    expected = (snapshot.get('attendance') or {}).get('expectedAttendees') or []
    records = [row for report in reports for row in report.get('records', [])]
    tracker = build_coach_meeting_attendance_tracker(expected, records, has_report=bool(reports))
    attendance = {
        'reports': reports, 'records': records, 'expectedAttendees': expected,
        'reportCount': len(reports),
        'participantCount': sum(report.get('totalParticipantCount', 0) for report in reports),
        'attendedCount': sum(report.get('attendedCount', 0) for report in reports),
        'absentCount': sum(report.get('absentCount', 0) for report in reports), **tracker,
    }
    return {**snapshot, 'artifacts': artifacts, 'attendanceReports': reports,
            'attendance': attendance, 'attendanceTracker': tracker, 'selectionError': selection_error}


def stored_transcript_selection(record, snapshot, *, session=None, meeting_scoped=False, meeting_id=None):
    """One contract for stored Native/imported/history/download consumers.

    Metadata without a proven Graph event is not evidence for the current link.
    Keep rejected historical rows intact, but never read their content here.
    """
    graph_id = record.graph_event_id
    artifacts = [row for row in snapshot.get('artifacts', []) if graph_id and
                 (row.get('metadata') or {}).get('_lms_graph_event_id') == graph_id]
    if not artifacts:
        return {'artifactIds': [], 'callId': None, 'provenance': session_provenance(record),
                'status': 'unavailable', 'code': 'TRANSCRIPT_UNAVAILABLE'}
    if meeting_scoped:
        if meeting_id is None:
            saved_ids = {(row.get('metadata') or {}).get('_lms_verified_online_meeting_id')
                         for row in artifacts if (row.get('metadata') or {}).get('_lms_verified_online_meeting_id')}
            meeting_id = next(iter(saved_ids)) if len(saved_ids) == 1 else ''
        selected, error = meeting_artifacts(record, {**snapshot, 'artifacts': artifacts}, meeting_id)
    else:
        selected, error = select_artifacts(record, {**snapshot, 'artifacts': artifacts}, session=session)
    ids = sorted({row['graph_artifact_id'] for row in selected
                  if row.get('artifact_type') == 'transcript' and row.get('graph_artifact_id')}) if not error else []
    calls = {row.get('call_id') or (row.get('metadata') or {}).get('callId') for row in selected} - {None, ''}
    return {'artifactIds': ids, 'callId': next(iter(calls)) if ids and len(calls) == 1 else None,
            'provenance': session_provenance(record), 'status': 'ambiguous' if error else 'ready' if ids else 'unavailable',
            'code': error or (None if ids else 'TRANSCRIPT_UNAVAILABLE')}


def session_provenance(record):
    """Target and booked occurrence; stored inside existing summary JSON only."""
    start, end = booking_window(record)
    return {key: str(getattr(record, key, '') or '') for key in (
        'pk', 'event_key', 'owner_email', 'learner_id', 'graph_event_id',
        'graph_organizer_email', 'meeting_link', 'review_instance_id', 'review_template_id',
    )} | {'start': start.isoformat() if start else None, 'end': end.isoformat() if end else None}


def summary_matches_session(state, record, transcript):
    """Validate Teams suggestion provenance, independently of formal answers."""
    return bool(transcript and state.get('eventKey') == record.event_key
                and state.get('graphEventId') == record.graph_event_id
                and state.get('transcriptArtifactId') == transcript['artifactId']
                and state.get('transcriptHash') == hashlib.sha256(transcript['text'].encode('utf-8')).hexdigest()
                and (not state.get('sessionProvenance') or state['sessionProvenance'] == session_provenance(record)))
