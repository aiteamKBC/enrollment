"""Read-only KSB coverage from a learner's non-deleted progress rows."""
from .ksb_points import point_ratio
from .ksb_exclusions import and_not_excluded


def build_progress_breakdown(learner_id, entries):
    groups = {}
    for entry in entries:
        if entry.get('learner_id') != learner_id or entry.get('deleted_at') is not None:
            continue
        code = entry.get('ksb_code')
        # COUNT(DISTINCT ksb_code) ignores NULL; preserve stored code values.
        if code is None:
            continue
        group = groups.setdefault(code, dict(
            code=code, description=entry.get('ksb_description') or code,
            category={'K': 'Knowledge', 'S': 'Skills', 'B': 'Behaviours'}.get(code[:1], 'Other'),
            components={}))
        identity = entry['id']
        achieved = entry.get('activity_status') == 'completed' and entry.get('accepted') is True
        group['components'][identity] = dict(
            name=entry.get('component_title') or f'Learning activity {identity}',
            status=entry.get('activity_status') or 'Unknown', achieved=achieved,
            source=entry.get('source_system') or 'Progress',
            accepted=entry.get('accepted') is True,
            type=entry.get('component_type') or entry.get('kind') or 'Component',
            module=entry.get('module_title'),
            date=entry.get('submitted_at') or entry.get('reporting_ended_at'),
            completedAt=(entry.get('submitted_at') or entry.get('reporting_ended_at'))
                if entry.get('accepted') is True else None)
    rows = []
    for group in groups.values():
        group['components'] = list(group['components'].values())
        points = point_ratio(sum(item['accepted'] for item in group['components']), len(group['components']))
        group.update(pointsAchieved=points['completed'], totalPoints=points['total'], progressPercent=points['percent'])
        group['completed'] = sum(item['achieved'] for item in group['components'])
        group['status'] = 'Achieved' if group['completed'] else 'Not Achieved'
        rows.append(group)
    achieved = sum(row['status'] == 'Achieved' for row in rows)
    return dict(rows=rows, source='progress', totalKsbs=len(rows), achievedKsbs=achieved,
                remainingKsbs=len(rows) - achieved)


def read_breakdown(connection, learner_id, *, code=None):
    """The primary learner_id scopes coverage for every learner type."""
    with connection.cursor() as cursor:
        code_filter = ' AND k.ksb_code=%s' if code is not None else ''
        cursor.execute('''SELECT p.id,p.learner_id,p.component_title,p.activity_status,
                p.accepted,p.deleted_at,p.source_system,k.ksb_code,k.ksb_description,
                p.component_type,p.kind,p.module_title,p.submitted_at,p.reporting_ended_at
            FROM "Learner".learner_progress_entries p
            JOIN "Learner".learner_progress_ksbs k ON k.progress_id=p.id''' + and_not_excluded('k', connection) + '''
            WHERE p.learner_id=%s AND p.deleted_at IS NULL
            ''' + code_filter + ' ORDER BY p.id,k.position', [learner_id, code] if code is not None else [learner_id])
        fields = ('id', 'learner_id', 'component_title', 'activity_status', 'accepted',
                  'deleted_at', 'source_system', 'ksb_code', 'ksb_description',
                  'component_type', 'kind', 'module_title', 'submitted_at', 'reporting_ended_at')
        entries = [dict(zip(fields, row)) for row in cursor.fetchall()]
    return build_progress_breakdown(learner_id, entries)


def read_learner_breakdown(connection, enrolment_id, *, code=None):
    """Resolve the already-authorized enrolment to its primary learner_id."""
    with connection.cursor() as cursor:
        cursor.execute('SELECT id FROM "Learner".learners WHERE enrolment_id=%s', [enrolment_id])
        owners = cursor.fetchall()
    if len(owners) != 1:
        raise ValueError('Learner identity is unavailable or ambiguous.')
    return read_breakdown(connection, owners[0][0], code=code) if code is not None else read_breakdown(connection, owners[0][0])
