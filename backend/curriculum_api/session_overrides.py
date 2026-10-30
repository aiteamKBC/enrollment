"""Explicit session exceptions shared by curriculum and learner plan readers."""
import copy
import json
from datetime import date, datetime, timedelta


def session_overrides(module):
    value = (module or {}).get('session_overrides') or {}
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError('The saved session exceptions could not be read.')
    return value


def override_clock(module, session_date):
    day = str(session_date or '')[:10]
    for item in session_overrides(module).values():
        if item.get('date') == day:
            return item['startTime'], item['endTime'], item['durationMinutes']
    return None


def apply_session_overrides(plan, module):
    overrides = session_overrides(module)
    if not overrides:
        return plan
    result = copy.deepcopy(plan)
    for session in result.get('sessions') or []:
        item = overrides.get(str(session['sessionNumber']))
        if not item:
            continue
        previous = session['date']
        session.update({key: item[key] for key in ('date', 'startTime', 'endTime', 'durationMinutes')})
        session.update(day=date.fromisoformat(item['date']).strftime('%A'), rescheduled=True, originalDate=previous)
        for slot in result.get('slots') or []:
            if slot.get('sessionNumber') == session['sessionNumber'] and slot.get('type') == 'live-session':
                slot.update(date=session['date'], day=session['day'])
    if result.get('sessions'):
        result['finalEndDate'] = max(item['date'] for item in result['sessions'])
    return result


def schedule_override(start, duration, zone):
    local = start.astimezone(zone)
    end = (start + timedelta(minutes=duration)).astimezone(zone)
    if start.second or start.microsecond:
        raise ValueError('Choose a start time in whole minutes.')
    if local.replace(fold=0).utcoffset() != local.replace(fold=1).utcoffset():
        raise ValueError('This clock time occurs twice when daylight saving ends. Choose an unambiguous time.')
    if local.utcoffset() != end.utcoffset() or end.replace(fold=0).utcoffset() != end.replace(fold=1).utcoffset():
        raise ValueError('Choose a session outside the daylight-saving clock change.')
    if local.date() != end.date() or end.replace(tzinfo=None) <= local.replace(tzinfo=None):
        raise ValueError('Choose a session that starts and ends on the same local day.')
    return {'date': local.date().isoformat(), 'startTime': local.strftime('%H:%M'),
            'endTime': end.strftime('%H:%M'), 'durationMinutes': duration,
            'startDateTimeUtc': start.isoformat()}


def validate_exception_plan(plan):
    """Keep session numbering and prevent a move overlapping its neighbour."""
    previous_end = None
    previous_date = None
    for session in plan.get('sessions') or []:
        start = datetime.fromisoformat(session['date'] + 'T' + session['startTime'])
        end = start + timedelta(minutes=int(session['durationMinutes']))
        if previous_date == session['date']:
            raise ValueError('Two sessions cannot share a delivery date. Choose a separate date.')
        if previous_end is not None and start < previous_end:
            raise ValueError('This move overlaps or changes the order of the sessions. Choose a time between the neighbouring sessions.')
        previous_end = end
        previous_date = session['date']
