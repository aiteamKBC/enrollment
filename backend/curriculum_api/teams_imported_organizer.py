"""Identify an imported meeting organiser from scoped, original Microsoft evidence.

Recording metadata proves the meeting organiser's object identity. Original
attendance data may name that same identity; attendance alone never establishes
meeting ownership. Calendar mailboxes and LMS-resolved email columns are unused.
"""
from datetime import datetime, timezone
import re


def text(value):
    return str(value or '').strip()


def timestamp(value):
    if not value:
        return ''
    try:
        instant = value if isinstance(value, datetime) else datetime.fromisoformat(text(value).replace('Z', '+00:00'))
        return instant.replace(tzinfo=instant.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    except (TypeError, ValueError):
        return ''


def saved_organizer_report(series, context, evidence):
    owner, tenant = text(context.get('Oid')), text(context.get('Tid'))
    meeting_id = text(series.get('online_meeting_id'))
    unavailable = {'status': 'unavailable', 'message': 'The meeting organiser could not be verified from Microsoft or sufficient saved Microsoft evidence.'}
    if not owner or not tenant or not meeting_id:
        return unavailable
    recordings = evidence.get('recordings') or []
    attendance = evidence.get('attendance') or []
    if len(recordings) > 500 or len(attendance) > 100:
        return unavailable
    matching = [row for row in recordings if text(row.get('meeting_id')) == meeting_id]
    if not matching:
        return unavailable
    if any((text(row.get('organizer_id')) and text(row.get('organizer_id')).casefold() != owner.casefold())
           or (text(row.get('organizer_tenant_id')) and text(row.get('organizer_tenant_id')).casefold() != tenant.casefold())
           for row in matching):
        return {'status': 'ambiguous', 'message': 'Saved Microsoft recording identities disagree with this meeting link. The organiser was not inferred.'}
    proven = [row for row in matching if text(row.get('organizer_id')).casefold() == owner.casefold()
              and text(row.get('organizer_tenant_id')).casefold() == tenant.casefold()]
    if not proven:
        return unavailable
    same_identity = [row for row in attendance if text(row.get('identity_id')).casefold() == owner.casefold()
                     and text(row.get('raw_role')).casefold() == 'organizer']
    if any(text(row.get('identity_tenant_id')) and text(row.get('identity_tenant_id')).casefold() != tenant.casefold()
           for row in same_identity):
        return {'status': 'ambiguous', 'message': 'Saved Microsoft attendance and recording tenant identities disagree. The organiser was not inferred.'}
    emails = {text(row.get('raw_email')).casefold() for row in same_identity
              if re.fullmatch(r'[^\s@]+@[^\s@]+', text(row.get('raw_email')))}
    if len(emails) > 1:
        return {'status': 'ambiguous', 'message': 'The same saved Microsoft organiser identity has conflicting original email addresses. Review is required.'}
    address = next(iter(emails), '')
    named = next((row for row in same_identity if text(row.get('raw_email')).casefold() == address and address), None)
    name = text((named or {}).get('raw_name')) or next((text(row.get('organizer_name')) for row in proven if text(row.get('organizer_name'))), '')
    result = {'status': 'available' if address else 'partial', 'source': 'saved_microsoft_records',
              'objectId': owner,
              'message': ('Organiser identified from saved Microsoft recording metadata and the same organiser identity in original attendance records. This is saved evidence; a fresh Microsoft organiser lookup was not available.' if address else
                          'Saved Microsoft recording metadata verifies the organiser object identity, but a name and email could not be fully confirmed. This is saved evidence, not a fresh Microsoft lookup.')}
    if address or name:
        result['person'] = {'name': name, 'email': address}
    dates = [timestamp(row.get('evidence_at')) for row in proven]
    if named:
        dates.append(timestamp(named.get('evidence_at')))
    if any(dates):
        result['evidenceAt'] = max(dates)
    return result


def load_saved_organizer_evidence(series, context):
    """Read narrow identity fields only; no schema helpers, content or LMS aliases."""
    from django.db import connection

    owner = text(context.get('Oid'))
    series_id, meeting_id, join_url = (text(series.get(key)) for key in ('id', 'online_meeting_id', 'join_url'))
    if not owner or not text(context.get('Tid')) or not series_id or not meeting_id or not join_url:
        return {'recordings': [], 'attendance': []}
    # This evidence lives in the existing production PostgreSQL schema. Do not
    # provision it or pretend a different database returned an empty roster.
    if connection.vendor != 'postgresql':
        return {'recordings': [], 'attendance': []}
    params = [series_id, meeting_id, meeting_id, join_url, join_url]
    with connection.cursor() as cursor:
        cursor.execute('''SELECT a.metadata->>'meetingId' AS meeting_id,
                a.metadata#>>'{meetingOrganizer,user,id}' AS organizer_id,
                a.metadata#>>'{meetingOrganizer,user,tenantId}' AS organizer_tenant_id,
                a.metadata#>>'{meetingOrganizer,user,displayName}' AS organizer_name,
                a.created_datetime AS evidence_at
            FROM curriculum.live_session_artifacts a
            JOIN curriculum.live_session_occurrences o ON o.id=a.occurrence_id
            WHERE o.live_session_id=%s AND a.artifact_type='recording'
              AND coalesce(nullif(o.online_meeting_id,''),%s)=%s
              AND coalesce(nullif(o.join_url,''),%s)=%s
            ORDER BY a.created_datetime DESC NULLS LAST LIMIT 501''', params)
        fields = [column[0] for column in cursor.description]
        recordings = [dict(zip(fields, row)) for row in cursor.fetchall()]
        # Attendance is only useful after recording evidence identifies this
        # same meeting owner. Avoid reading unrelated participant identities.
        preliminary = saved_organizer_report(series, context, {'recordings': recordings, 'attendance': []})
        if preliminary.get('source') != 'saved_microsoft_records':
            return {'recordings': recordings, 'attendance': []}
        cursor.execute('''SELECT
                coalesce(a.raw_data#>>'{identity,id}',a.raw_data#>>'{identity,user,id}') AS identity_id,
                coalesce(a.raw_data#>>'{identity,tenantId}',a.raw_data#>>'{identity,user,tenantId}') AS identity_tenant_id,
                a.raw_data->>'role' AS raw_role,
                coalesce(nullif(a.raw_data->>'emailAddress',''),a.raw_data->>'email') AS raw_email,
                coalesce(nullif(a.raw_data#>>'{identity,displayName}',''),
                         nullif(a.raw_data#>>'{identity,user,displayName}',''),
                         nullif(a.raw_data->>'name',''),a.raw_data->>'displayName') AS raw_name,
                coalesce(o.actual_start,o.scheduled_start) AS evidence_at
            FROM curriculum.live_session_attendance a
            JOIN curriculum.live_session_occurrences o ON o.id=a.occurrence_id
            WHERE o.live_session_id=%s
              AND coalesce(nullif(o.online_meeting_id,''),%s)=%s
              AND coalesce(nullif(o.join_url,''),%s)=%s
              AND lower(coalesce(a.raw_data#>>'{identity,id}',a.raw_data#>>'{identity,user,id}'))=lower(%s)
              AND lower(a.raw_data->>'role')='organizer'
            ORDER BY coalesce(o.actual_start,o.scheduled_start) DESC LIMIT 101''', [*params, owner])
        fields = [column[0] for column in cursor.description]
        attendance = [dict(zip(fields, row)) for row in cursor.fetchall()]
    return {'recordings': recordings, 'attendance': attendance}
