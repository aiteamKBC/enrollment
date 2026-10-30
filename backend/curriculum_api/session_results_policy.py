"""Pure session evidence rules, shared by reporting, exports and learner reads."""
import csv
import hashlib
import io
import json
import re
import unicodedata
from datetime import datetime, timezone

PRESENT_AFTER_SECONDS = 180


def attendance_name_key(value):
    """Return a conservative exact-name key for an unverified Teams row."""

    text = unicodedata.normalize('NFKC', str(value or '')).strip().casefold()
    text = re.sub(r'\s+\((?:unverified|external|guest)\)\s*$', '', text)
    return ' '.join(text.split())


def attendance_identity(record):
    """Read both documented and legacy Graph attendance identity shapes."""

    if not isinstance(record, dict):
        return '', ''
    raw = record.get('raw_data')
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = {}
    source = raw if isinstance(raw, dict) else record
    identity = source.get('identity') if isinstance(source.get('identity'), dict) else {}
    display_name = str(identity.get('displayName') or identity.get('name') or '').strip()
    identity_id = str(identity.get('id') or '').strip()
    if display_name or identity_id:
        return display_name, identity_id
    for key in ('user', 'guest', 'phone', 'encrypted'):
        value = identity.get(key)
        if not isinstance(value, dict):
            continue
        display_name = str(value.get('displayName') or value.get('name') or '').strip()
        identity_id = str(value.get('id') or '').strip()
        if display_name or identity_id:
            return display_name, identity_id
    return '', ''


def attendance_display_name(record):
    """Recover a display name without changing the stored raw Teams evidence."""

    if not isinstance(record, dict):
        return ''
    existing = str(record.get('display_name') or '').strip()
    if existing:
        return existing
    display_name, _identity_id = attendance_identity(record)
    if display_name:
        return display_name
    raw = record.get('raw_data')
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = {}
    source = raw if isinstance(raw, dict) else record
    for key in ('displayName', 'participantDisplayName', 'name'):
        display_name = str(source.get(key) or '').strip()
        if display_name:
            return display_name
    return ''


def instant(value):
    try:
        value = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def attendance_seconds(intervals):
    """Union actual visits: reconnects add time; simultaneous devices do not."""
    visits = []
    for item in intervals or []:
        if not isinstance(item, dict):
            continue
        start, end = instant(item.get('joinDateTime')), instant(item.get('leaveDateTime'))
        if start and end and end >= start:
            visits.append((start, end))
    total, previous_start, previous_end = 0, None, None
    for start, end in sorted(visits):
        if previous_end is not None and start <= previous_end:
            previous_end = max(previous_end, end)
        else:
            if previous_end is not None:
                total += (previous_end - previous_start).total_seconds()
            previous_start, previous_end = start, end
    if previous_end is not None:
        total += (previous_end - previous_start).total_seconds()
    return max(0, int(total))


def evidence_seconds(records):
    intervals, duration_only = [], 0
    for record in records:
        visits = []
        for visit in record.get('intervals') or []:
            if not isinstance(visit, dict):
                continue
            start, end = instant(visit.get('joinDateTime')), instant(visit.get('leaveDateTime'))
            if start and end and end >= start:
                visits.append(visit)
        if visits:
            intervals.extend(visits)
        else:
            duration_only = max(duration_only, int(record.get('total_attendance_seconds') or 0))
    # A duration-only record can prove presence even when another device has
    # short visits. Unknown overlaps cannot safely be added to those visits.
    return max(attendance_seconds(intervals), duration_only)


def session_roster(expected, records, *, complete):
    people = {}
    for email in expected:
        email = str(email or '').strip().casefold()
        if email:
            people[email] = {'email': email, 'name': email, 'expected': True, 'records': []}
    unidentified = False
    for record in records:
        email = str(record.get('email') or '').strip().casefold()
        unidentified |= not bool(email)
        source_id = str(record.get('id') or record.get('graph_record_id') or '').strip()
        display_name = attendance_display_name(record)
        name_key = attendance_name_key(display_name)
        key = email or ('unmatched-name:' + name_key if name_key else 'unmatched:' + source_id)
        person = people.setdefault(key, {'email': email, 'name': display_name or 'Unmatched participant', 'expected': False, 'records': []})
        person['name'] = display_name or person['name']
        person['records'].append(record)
    result = []
    for person in people.values():
        source_records = person.pop('records')
        seconds = evidence_seconds(source_records)
        visits = set()
        for record in source_records:
            for visit in record.get('intervals') or []:
                if not isinstance(visit, dict):
                    continue
                joined, left = instant(visit.get('joinDateTime')), instant(visit.get('leaveDateTime'))
                if joined and left and left >= joined:
                    visits.add((joined, left))
        status = 'pending' if not complete else 'present' if seconds > PRESENT_AFTER_SECONDS else 'absent'
        if not person['email'] or (unidentified and not seconds):
            status = 'review'
        attendance = 1 if status == 'present' else 0 if status == 'absent' else None
        source_record_ids = sorted({
            str(record.get('id') or record.get('graph_record_id') or '').strip()
            for record in source_records
            if str(record.get('id') or record.get('graph_record_id') or '').strip()
        })
        result.append({**person, 'seconds': seconds, 'status': status,
                       'attendance': attendance,
                       # These are the immutable Teams result. Recovery is an
                       # LMS decision layered on top, never a rewrite of what
                       # happened in the original meeting.
                       'rawStatus': status, 'rawAttendance': attendance,
                       'excuseStatus': 'none', 'recoveryStatus': 'none',
                       'effectiveStatus': status, 'effectiveAttendance': attendance,
                       'finalOutcome': status, 'sourceRecordIds': source_record_ids,
                       'excused': False, 'catchupCompleted': False,
                       'intervals': [{'joinedAt': joined.isoformat(), 'leftAt': left.isoformat()} for joined, left in sorted(visits)]})
    return sorted(result, key=lambda person: (not person['expected'], person['name'].casefold()))


def session_runs(occurrence, records):
    runs = set()
    for record in records:
        raw = record.get('raw_data') or {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = {}
        if not isinstance(raw, dict):
            continue
        start, end = instant(raw.get('attendanceReportStart')), instant(raw.get('attendanceReportEnd'))
        if start and end and end >= start:
            runs.add((start, end))
    if not runs:
        start, end = instant(occurrence.get('actual_start')), instant(occurrence.get('actual_end'))
        if start and end and end >= start:
            runs.add((start, end))
    return [{'startsAt': start.isoformat(), 'endsAt': end.isoformat()} for start, end in sorted(runs)]


def safe_segment(label, identifier):
    label = re.sub(r'[^\w-]+', '-', str(label or ''), flags=re.UNICODE).strip('-')[:65] or 'untitled'
    key = re.sub(r'[^a-zA-Z0-9_-]', '-', str(identifier or ''))[:80]
    # Hash prevents different unsafe/truncated identifiers sharing a directory.
    suffix = hashlib.sha256(str(identifier).encode()).hexdigest()[:10]
    return f'{label}_{key}-{suffix}'


def archive_prefix(series, occurrence, module=None):
    module = module or {}
    return '/'.join((safe_segment(series.get('module_title'), series.get('module_catalogue_id') or series['id']),
                     safe_segment(module.get('group_name') or 'group', module.get('group_id') or series['id']),
                     safe_segment(f"session-{int(occurrence.get('session_number') or 0):02d}", occurrence['id'])))


def transcript_text(vtt):
    lines = []
    for line in vtt.replace('\r', '').split('\n'):
        if not line.strip() or line.strip() == 'WEBVTT' or '-->' in line or line.strip().isdigit():
            continue
        line = re.sub(r'<v\s+([^>]+)>', r'\1: ', line)
        lines.append(re.sub(r'<[^>]+>', '', line))
    return '\n'.join(lines).strip()


def attendance_csv(rows):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['Name', 'Email', 'Raw attendance', 'Raw status', 'Effective attendance',
                     'Final outcome', 'Seconds', 'Excused', 'Catch-up completed',
                     'Absence reported', 'Recovery status', 'Recovery method'])
    def cell(value):
        value = str(value)
        return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value
    for row in rows:
        writer.writerow([cell(row.get('name', '')), cell(row.get('email', '')),
                         '' if row.get('rawAttendance', row.get('attendance')) is None else row.get('rawAttendance', row.get('attendance')),
                         row.get('rawStatus', row.get('status', '')),
                         '' if row.get('effectiveAttendance', row.get('attendance')) is None else row.get('effectiveAttendance', row.get('attendance')),
                         row.get('finalOutcome', row.get('status', '')), row['seconds'],
                         'Yes' if row.get('excused') else 'No', 'Yes' if row.get('catchupCompleted') else 'No',
                         'Yes' if row.get('absenceReported') else 'No',
                         row.get('recoveryStatus', 'none'), row.get('recoveryType', 'none')])
    return '\ufeff' + output.getvalue()
