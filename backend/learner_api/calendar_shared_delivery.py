"""Fill learner calendar links from the shared card's existing Teams occurrence."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone as utc_timezone

from django.utils import timezone

from .shared_live_delivery import read_shared_live_delivery


def read_shared_calendar_slots(module_ids, components):
    """Resolve only assigned, unambiguous live cards; never guess by session number."""
    assigned = set(module_ids)
    by_week = defaultdict(list)
    for component in components:
        if (component.get('module_catalogue_id') in assigned
                and component.get('id') and component.get('week_id')):
            by_week[component['week_id']].append(component['id'])
    single_cards = {week: ids[0] for week, ids in by_week.items() if len(ids) == 1}
    if not single_cards:
        return {}
    delivery = read_shared_live_delivery(module_ids, list(single_cards.values()))
    return {week: delivery[cid] for week, cid in single_cards.items() if cid in delivery}


def apply_shared_calendar_slot(event, delivery):
    """Keep the assigned event identity, using the booked occurrence's clock/link."""
    if event.get('meetingLink') or event.get('occurrenceId') or not delivery.get('liveSessionUrl'):
        return event
    start = datetime.fromisoformat(delivery['sessionDateTimeUtc'])
    if start.tzinfo is None:
        start = start.replace(tzinfo=utc_timezone.utc)
    end = start.astimezone(utc_timezone.utc) + timedelta(minutes=delivery['durationMinutes'])
    business_zone = timezone.get_default_timezone()
    start, end = start.astimezone(business_zone), end.astimezone(business_zone)
    return {
        **event,
        'occurrenceId': delivery['teamsOccurrenceId'],
        'date': start.date().isoformat(),
        'targetDate': start.date().isoformat(),
        'rawPlanned': start.date().isoformat(),
        'year': start.year, 'month': start.month - 1, 'dayOfMonth': start.day,
        'dayOfWeek': start.weekday(),
        'startHour': start.hour + start.minute / 60,
        'endHour': end.hour + end.minute / 60,
        'timeLabel': f'{start:%H:%M} - {end:%H:%M}',
        'durationMinutes': delivery['durationMinutes'],
        'isTimeEstimated': False,
        'meetingLink': delivery['liveSessionUrl'],
        'meetingProvider': 'Microsoft Teams',
        'platform': 'Microsoft Teams',
        'location': 'Microsoft Teams',
    }
