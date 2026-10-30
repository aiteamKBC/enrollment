"""Meeting-local, explicit Coach identity links; never edit Teams or Review answers."""
import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime

from django.db import DatabaseError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from .auth import coach_access_required, authenticated_coach_email
from .models import CoachCalendarEvent, ReviewAttendanceLink
from .bound_actual_session import BoundSessionConflict
from .review_sources import ReviewIdentityConflict


class LinkConflict(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def session_key(record, attendance):
    # Reused Teams URLs and a replacement booking must never inherit a correction.
    return digest({
        'booking': [str(getattr(record, key, '') or '') for key in (
            'pk', 'event_key', 'learner_id', 'graph_event_id', 'graph_organizer_email',
            'scheduled_date', 'scheduled_time', 'duration_minutes')],
        'reports': sorted((str(r.get('id', '')), str(r.get('meetingStartDateTime', '')),
                           str(r.get('meetingEndDateTime', '')),
                           str(r.get('_lms_verified_online_meeting_id', '')))
                          for r in attendance.get('reports', [])),
    })


def participant_evidence(attendance, row):
    ids, reports = set(row.get('actualRecordIds') or []), set(row.get('reportIds') or [])
    if not ids or len(reports) != 1:
        raise LinkConflict('This participant has no unambiguous saved Teams evidence.')
    found = {}
    for report in attendance.get('reports', []):
        if report.get('id') not in reports:
            continue
        for person in report.get('records', []):
            if person.get('id') not in ids:
                continue
            key = (str(report['id']), str(person['id']))
            if key in found and found[key] != person:
                raise LinkConflict('Teams participant evidence is inconsistent. Refresh the report.')
            found[key] = person
    if {key[1] for key in found} != ids or {key[0] for key in found} != reports:
        raise LinkConflict('Teams participant evidence changed. Reload attendance.')
    people = [{'reportId': key[0], **deepcopy(person)} for key, person in sorted(found.items())]
    seconds = attendance_seconds(people)
    if seconds <= 0:
        raise LinkConflict('A positive recorded attendance duration is required.')
    return {'records': people, 'seconds': seconds}


def attendance_seconds(people):
    """Merge reconnect/device intervals instead of summing overlapping attendance."""
    intervals = []
    for person in people:
        if not person.get('attended'):
            raise LinkConflict('This is not a confirmed Teams attendee.')
        for interval in person.get('intervals') or []:
            try:
                start = datetime.fromisoformat(interval['joinDateTime'].replace('Z', '+00:00'))
                end = datetime.fromisoformat(interval['leaveDateTime'].replace('Z', '+00:00'))
                if start.utcoffset() is None or end.utcoffset() is None or end <= start:
                    raise ValueError()
            except (KeyError, TypeError, ValueError, AttributeError):
                raise LinkConflict('Attendance intervals are incomplete. Refresh the report.') from None
            intervals.append((start.timestamp(), end.timestamp()))
        if not person.get('intervals'):
            # A single provider duration is usable. Several unbounded records
            # cannot be added safely because their time might overlap.
            if len(people) != 1:
                raise LinkConflict('Multiple attendance records need complete join/leave times.')
            seconds = person.get('totalAttendanceSeconds')
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds):
                raise LinkConflict('The saved attendance duration is invalid.')
            return max(0, int(seconds))
    total, last = 0, None
    for start, end in sorted(intervals):
        total += max(0, end - max(start, last if last is not None else start))
        last = max(last or end, end)
    return int(total)


def public_link(link):
    return {'id': link.pk, 'confirmedBy': link.confirmed_by,
            'targetRole': getattr(link, 'target_role', 'learner'),
            'targetName': getattr(link, 'target_name', ''),
            'confirmedAt': link.confirmed_at.isoformat(), 'reason': link.reason,
            'revokedBy': link.revoked_by or None,
            'undoReason': link.revoke_reason or None,
            'revokedAt': link.revoked_at.isoformat() if link.revoked_at else None}


def expected_targets(attendance):
    """Only unambiguous expected identities from this booking's trusted roster."""
    targets = []
    for row in attendance.get('tracker') or []:
        if not row.get('expected') or row.get('role') not in {'learner', 'employer'}:
            continue
        role = row['role']
        email = str(row.get('email') or '').strip().casefold()
        if role == 'employer' and not email:
            continue
        key = 'learner' if role == 'learner' else digest(['employer', email])
        targets.append((key, row))
    return {key: row for key, row in targets if sum(k == key for k, _ in targets) == 1}


def project_attendance(record, attendance, links):
    """Pure overlay. Raw reports, records, and other expected people are retained."""
    result = deepcopy(attendance)
    rows = result.get('tracker') or []
    current_key = session_key(record, attendance)
    targets = expected_targets(result)
    active = [link for link in links if not link.revoked_at and link.session_key == current_key]
    for link in active:
        target = targets.get(getattr(link, 'target_key', 'learner'))
        if link.learner_id != record.learner_id:
            continue
        if target is None or target['role'] != getattr(link, 'target_role', 'learner'):
            result['linkWarning'] = 'The expected participant changed. Review or undo the previous attendance link.'
            continue
        if target.get('attended'):
            # Provider evidence now matches directly; do not count a second row
            # as this person without reviewing both sets of intervals.
            result['linkWarning'] = 'Teams evidence changed. Review or undo the previous attendance link.'
            continue
        candidates = []
        for row in rows:
            if row.get('expected') or not row.get('attended'):
                continue
            try:
                evidence = participant_evidence(attendance, row)
            except LinkConflict:
                continue
            if digest(evidence) == link.participant_key and evidence == link.evidence:
                candidates.append((row, evidence))
        if len(candidates) != 1:
            result['linkWarning'] = 'Linked Teams evidence changed. Review or undo the previous attendance link.'
            continue
        actual, evidence = candidates[0]
        target.update(attended=True, status='attended', totalAttendanceSeconds=evidence['seconds'],
                      actualDisplayName=actual.get('displayName'), actualRecordIds=actual.get('actualRecordIds'),
                      reportIds=actual.get('reportIds'), coachVerification=public_link(link),
                      linkedEmail=actual.get('email'))
        rows.remove(actual)
    expected = [r for r in rows if r.get('expected')]
    result.update(tracker=rows, extraAttendees=[r for r in rows if not r.get('expected')],
                  expectedCount=len(expected), expectedAttendedCount=sum(bool(r.get('attended')) for r in expected),
                  expectedAbsentCount=sum(r.get('status') == 'absent' for r in expected),
                  expectedPendingCount=sum(r.get('status') == 'pending' for r in expected),
                  extraCount=sum(not r.get('expected') for r in rows))
    return result


def saved_links(record):
    return list(ReviewAttendanceLink.objects.using(record._state.db or 'default')
                .filter(calendar_event_id=record.pk, learner_id=record.learner_id).order_by('pk'))


def attendance_for_display(record, attendance):
    """Shared read-only presentation; never write corrected rows to Teams snapshots."""
    if record.event_type not in {'mcr', 'progress-review'}:
        return attendance
    try:
        links = saved_links(record)
    except DatabaseError:
        return {**attendance, 'linkWarning': 'Attendance corrections could not be loaded.'}
    return project_attendance(record, attendance, links) if links else attendance


def scoped_snapshot(record, overlay=None):
    from .views import stored_coach_meeting_snapshot
    snapshot = stored_coach_meeting_snapshot(record)
    if overlay is not None:
        from .bound_actual_session import bound_session
        from .migrated_session_scope import has_verified_meeting_evidence, scope_snapshot
        meeting_scoped = has_verified_meeting_evidence(snapshot)
        session = None
        if not meeting_scoped and (overlay.meeting_intelligence or {}).get('actualSession'):
            bound = bound_session(record, overlay=overlay)
            if not bound:
                raise LinkConflict('The historical meeting association could not be confirmed.')
            session = bound.context
        snapshot = scope_snapshot(record, snapshot, session=session, meeting_scoped=meeting_scoped)
    if snapshot.get('partial') or snapshot.get('selectionError') or not (snapshot.get('storage') or {}).get('stored'):
        raise LinkConflict('Saved meeting evidence is unavailable or ambiguous. Reload the Review.')
    return snapshot


def owned_context(request, event_key, *, lock=False):
    from .views import coach_meeting_artifact_record
    owner = authenticated_coach_email(request)
    overlay = None
    if event_key.startswith('imported-review:'):
        from .migrated_intelligence_views import _association
        overlay, record, _ = _association(request, event_key, lock=lock)
        if not overlay or not record:
            return None, None
    else:
        record = coach_meeting_artifact_record(owner, event_key)
        if record:
            from .reconciliation_links import for_calendar
            linked = for_calendar(record)
            if linked:
                from .migrated_intelligence_views import _association
                overlay, resolved, _ = _association(request, f'imported-review:{linked.aptem_review_id}', lock=lock)
                if not overlay or not resolved or resolved.pk != record.pk:
                    return None, None
                record = resolved
        if record and lock:
            record = CoachCalendarEvent.objects.using(record._state.db or 'default').select_for_update().filter(
                pk=record.pk, owner_email__iexact=owner).first()
    if not record or record.owner_email.casefold() != owner.casefold() or record.event_type not in {'mcr', 'progress-review'}:
        return None, None
    return record, overlay


def context_payload(record, snapshot, links):
    attendance = snapshot['attendance']
    learner_rows = [r for r in attendance.get('tracker', []) if r.get('expected') and r.get('role') == 'learner']
    key = session_key(record, attendance)
    active = [link for link in links if link.session_key == key and not link.revoked_at]
    linked_targets = {getattr(link, 'target_key', 'learner') for link in active}
    linked_people = {link.participant_key for link in active}
    targets = [{'key': target_key, 'role': row['role'],
                'name': row.get('displayName') or row.get('name') or row.get('email')}
               for target_key, row in expected_targets(attendance).items()
               if not row.get('attended') and target_key not in linked_targets]
    candidates = []
    if targets:
        for row in attendance.get('tracker', []):
            if row.get('expected') or not row.get('attended'):
                continue
            if sum(r.get('id') == row.get('id') for r in attendance.get('tracker', [])) != 1:
                continue
            try:
                evidence = participant_evidence(attendance, row)
            except LinkConflict:
                continue
            if digest(evidence) not in linked_people:
                candidates.append({'rowId': row['id'], 'key': digest(evidence)})
    return {
        'attendance': project_attendance(record, attendance, links),
        'version': digest([key, attendance, [(l.pk, l.revoked_at) for l in links]]),
        'learner': {'id': record.learner_id, 'name': learner_rows[0].get('displayName') or learner_rows[0].get('name')} if len(learner_rows) == 1 else None,
        'targets': targets,
        'candidates': candidates,
        'history': [public_link(link) for link in links],
        'activeLinks': [public_link(link) for link in links if not link.revoked_at],
    }


@coach_access_required
@csrf_protect
@require_http_methods(['GET', 'POST'])
def review_attendance_links(request, event_key):
    try:
        record, overlay = owned_context(request, event_key)
        if record is None:
            return JsonResponse({'detail': 'Review meeting not found for this coach.'}, status=404)
        db = record._state.db or 'default'
        if request.method == 'GET':
            result = context_payload(record, scoped_snapshot(record, overlay), saved_links(record))
        else:
            try:
                payload = json.loads(request.body)
            except (ValueError, UnicodeDecodeError):
                return JsonResponse({'detail': 'Invalid JSON.'}, status=400)
            if not isinstance(payload, dict) or payload.get('action') not in {'link', 'undo'}:
                return JsonResponse({'detail': 'Choose link or undo.'}, status=400)
            reason = payload.get('reason')
            if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 500:
                return JsonResponse({'detail': 'Enter a reason between 3 and 500 characters.'}, status=400)
            with transaction.atomic(using=db):
                record, overlay = owned_context(request, event_key, lock=True)
                if record is None:
                    raise LinkConflict('Review ownership changed. Reload the Review.')
                snapshot, links = scoped_snapshot(record, overlay), saved_links(record)
                current = context_payload(record, snapshot, links)
                if payload.get('version') != current['version']:
                    raise LinkConflict('Attendance changed. Reload it before confirming.')
                if payload['action'] == 'undo':
                    link = next((l for l in links if l.pk == payload.get('linkId') and not l.revoked_at), None)
                    if not link:
                        raise LinkConflict('This attendance link is no longer active.')
                    link.revoked_by = authenticated_coach_email(request)
                    link.revoked_at = timezone.now()
                    link.revoke_reason = reason.strip()
                    link.save(using=db, update_fields=['revoked_by', 'revoked_at', 'revoke_reason'])
                else:
                    if 'learnerId' in payload and (type(payload['learnerId']) is not int or payload['learnerId'] != record.learner_id):
                        return JsonResponse({'detail': 'Only this Review learner can be selected.'}, status=400)
                    # Existing learner-only clients remain supported; never infer an employer.
                    target_key = payload.get('targetKey', 'learner' if payload.get('learnerId') == record.learner_id else None)
                    target = next((t for t in current['targets'] if t['key'] == target_key), None)
                    if target is None:
                        raise LinkConflict('Select an available expected participant from this Review.')
                    candidate = next((c for c in current['candidates'] if c['key'] == payload.get('participantKey')), None)
                    if candidate is None:
                        raise LinkConflict('This participant cannot be linked. Reload attendance.')
                    row = next(r for r in snapshot['attendance']['tracker'] if r['id'] == candidate['rowId'] and not r.get('expected'))
                    evidence = participant_evidence(snapshot['attendance'], row)
                    ReviewAttendanceLink.objects.using(db).create(calendar_event_id=record.pk,
                        learner_id=record.learner_id, session_key=session_key(record, snapshot['attendance']),
                        target_key=target['key'], target_role=target['role'], target_name=str(target['name'] or '')[:255],
                        participant_key=candidate['key'], evidence=evidence, reason=reason.strip(),
                        confirmed_by=authenticated_coach_email(request))
                result = context_payload(record, snapshot, saved_links(record))
        response = JsonResponse(result)
        response['Cache-Control'] = 'private, no-store'
        return response
    except (LinkConflict, BoundSessionConflict, ReviewIdentityConflict) as exc:
        return JsonResponse({'detail': str(exc)}, status=409)
    except DatabaseError:
        return JsonResponse({'detail': 'Attendance correction storage is unavailable. No change was saved.'}, status=503)
