"""Module filters from the same complete history shown in Coach attendance View."""
from collections import defaultdict
from hashlib import sha256

from .attendance_detail import counted_history
from .bulk_attendance import delivery_occurrences


def module_key(name):
    # This is a display filter within one group, never a meeting/write identity.
    return sha256(name.encode('utf-8')).hexdigest()


def with_module_options(programme, profiles, placements):
    """Read only this programme's authorised groups; never truncate to recent/page rows."""
    by_group = defaultdict(list)
    for placement in placements:
        if placement.get('programmeId') == programme['id'] and placement.get('groupId'):
            by_group[placement['groupId']].append(profiles[placement['id']])
    groups = []
    for group in programme['groups']:
        selected = by_group[group['id']]
        modules = {}

        def facet(name):
            name = name or ''
            key = module_key(name)
            return modules.setdefault(key, {'key': key, 'name': name or 'Unspecified module',
                'learnerIds': set(), 'sessionIds': set(), 'recentByLearner': {}})

        for profile in selected:
            source = getattr(profile, '_caseload_source', None)
            if source is None:
                continue
            history, _, _ = counted_history(source, profile.id)
            for row, status in history:
                module = facet(row.get('module_title'))
                learner_id = str(profile.id)
                module['learnerIds'].add(learner_id)
                recent = module['recentByLearner'].setdefault(learner_id, [])
                if len(recent) < 4:
                    recent.append({'date': row['session_date'].isoformat(),
                                   'status': 'present' if status == 'present' else 'absent'})
                if row.get('source') == 'microsoft-teams':
                    module['sessionIds'].add(str(row['session_id']))

        # Include other editable occurrences only for modules present in View.
        for occurrence, authored_module in delivery_occurrences(
                programme['id'], group['id'], profiles=selected):
            module = modules.get(module_key(authored_module.title or ''))
            if module is not None:
                module['sessionIds'].add(str(occurrence.id))
        groups.append({**group, 'modules': [
            {**module, 'learnerIds': sorted(module['learnerIds']), 'sessionIds': sorted(module['sessionIds'])}
            for module in sorted(modules.values(), key=lambda item: (item['name'].casefold(), item['key']))]})
    return {**programme, 'groups': groups}
