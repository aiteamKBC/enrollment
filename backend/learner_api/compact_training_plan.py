"""Display-only Week summaries for newly captured Training Plan snapshots.

The Learning Plan/module/meeting totals are never calculated or changed here.
Source Components and assignment topics remain in the immutable snapshot.
"""
from decimal import Decimal, InvalidOperation
import re


def title(value):
    return re.sub(r'^\s*L\s*\d+(?:\s*[:.\-–—]\s*|\s+)', '', str(value or ''), flags=re.I).strip()


def summarise(module):
    summaries, diagnostics, seen = [], [], set()
    for index, week in enumerate(module.get('weeks') or []):
        wid = week['sourceId']
        components = [a for a in module['activities'] if a.get('weekId') == wid
                      and a.get('source') == 'LMS component' and not a.get('parentComponentId')]
        ids, missing, assignments, dates = [], [], [], set()
        total = Decimal(0)
        for component in components:
            cid = component['id']
            if cid in seen:
                diagnostics.append({'code': 'duplicate_week_component', 'sourceId': cid, 'weekId': wid,
                                    'message': 'A repeated Component ID was counted once only.'})
                continue
            seen.add(cid)
            ids.append(cid)
            try:
                if component.get('hours') is None or isinstance(component['hours'], bool):
                    raise InvalidOperation
                value = Decimal(str(component['hours']))
                if not value.is_finite() or value < 0:
                    raise InvalidOperation
                total += value
            except (InvalidOperation, ValueError):
                missing.append(cid)
            if str(component.get('kind', '')).casefold() == 'assignment':
                assignments.append(cid)
                if component.get('date'):
                    dates.add(component['date'])
        # Assignment is supported by an actual Component FK, never by ordinal.
        summary = {'id': wid, 'title': title(week.get('title')), 'kind': 'assignment' if assignments else 'topic',
                   'hours': None if missing or week.get('componentsAvailable') is False else float(total),
                   'knownHours': float(total), 'componentIds': ids, 'missingHoursComponentIds': missing,
                   'assignmentComponentIds': assignments, 'dueDates': sorted(dates),
                   'startDate': week.get('startDate'), 'endDate': week.get('endDate'),
                   'titleSource': 'curriculum.weeks.title', 'hoursSource': 'curriculum.components.expected_otjh'}
        summaries.append(summary)
        if missing or week.get('componentsAvailable') is False:
            diagnostics.append({'code': 'week_hours_unavailable', 'weekId': wid, 'componentIds': missing,
                                'message': 'Week OTJ is unavailable; missing Component hours have not been assumed zero.'})
        if (index + 1) % 4 == 0 and not assignments:
            diagnostics.append({'code': 'fourth_week_has_no_assignment', 'weekId': wid,
                                'message': 'The fourth Week has no configured Assignment and remains a Topic.'})
        elif (index + 1) % 4 != 0 and assignments:
            diagnostics.append({'code': 'assignment_outside_fourth_week', 'weekId': wid, 'componentIds': assignments,
                                'message': 'A real Assignment is configured outside the fourth Week; its source position is retained.'})
        if not summary['title']:
            diagnostics.append({'code': 'week_title_unavailable', 'weekId': wid,
                                'message': 'The source Week title is blank; no title has been invented.'})
    unknown = [a['id'] for a in module['activities'] if a.get('source') == 'LMS component' and a['id'] not in seen]
    if unknown:
        diagnostics.append({'code': 'component_week_unverified', 'componentIds': unknown,
                            'message': 'Components without a verified Week were not assigned to an invented Week.'})
    if summaries and all(w['hours'] is not None for w in summaries) and module.get('hours') is not None:
        weekly = sum((Decimal(str(w['knownHours'])) for w in summaries), Decimal(0))
        authoritative = Decimal(str(module['hours']))
        if abs(weekly - authoritative) > Decimal('0.005'):
            diagnostics.append({'code': 'weekly_module_otj_discrepancy', 'moduleHours': float(authoritative),
                                'weekHours': float(weekly), 'weekIds': [w['id'] for w in summaries],
                                'message': f'Week Components total {weekly}h; authoritative module total {authoritative}h. Both retained unchanged.'})
    for issue in diagnostics:
        issue.update(lmsOfferingId=module['lmsOfferingId'], academicModuleId=module['academicModuleId'])
    return summaries, diagnostics
