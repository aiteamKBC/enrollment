"""Read shared delivery attendance using the builder's reviewed identity context."""
from collections import defaultdict

from django.utils import timezone


def apply_shared_attendance(rows, learner):
    groups = defaultdict(list)
    now = timezone.now()
    for row in rows:
        owner = row.get('attendance_source_module_id')
        if (row.get('source') == 'microsoft-teams' and owner
                and row.get('scheduled_end') and row['scheduled_end'] <= now
                and row.get('attendance_report_id')):
            groups[(row['module_catalogue_id'], owner, row['live_session_id'])].append(row)
    if not groups:
        return rows
    from curriculum_api.session_results import result_rows
    email = str(learner.email or '').strip().casefold()
    for (module_id, owner, series_id), scheduled in groups.items():
        # Once per series/delivery, not once per lecture. Resolve against the
        # full delivery roster before filtering this learner (name ambiguity).
        sessions = result_rows({'id': series_id, 'module_catalogue_id': owner},
                               email=email, module_catalogue_id=module_id, attendance_only=True,
                               occurrence_ids={str(row.get('attendance_source_occurrence_id') or row['session_id'])
                                               for row in scheduled})
        by_id = {str(session['id']): session for session in sessions}
        for row in scheduled:
            session = by_id.get(str(row.get('attendance_source_occurrence_id') or ''))
            if not session or not session.get('reportReady'):
                continue
            person = next((person for person in session.get('attendance', [])
                           if person.get('email') == email), None)
            if person is None:
                continue
            status = person.get('rawStatus') or person.get('status')
            if status not in {'present', 'absent'}:
                continue
            row.update(attendance_status=status, attended=status == 'present',
                       attended_seconds=max(0, int(person.get('seconds') or 0)))
    return rows
