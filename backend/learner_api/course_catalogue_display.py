"""Read-only course placement and exact links to retained source materials.

These dates belong to My Learning, never to the reporting ledger or meetings.
"""
from collections import defaultdict
from datetime import datetime
from html import unescape
import json
import re
from zoneinfo import ZoneInfo

from .subject_dates import MONTH, MONTHS, activity_schedule, apply_section_placement


def original_upload_day(value):
    """Use source upload metadata only; never an import or reporting timestamp."""
    if not value:
        return None
    try:
        at = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    if not 2020 <= at.year <= 2035:
        return None
    if at.tzinfo:
        at = at.astimezone(ZoneInfo('Europe/London'))
    return at.date().isoformat()


def metadata(value):
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


def section_schedule(title):
    title = unescape(str(title or '')).replace('\\', '/')
    schedule = activity_schedule(title, section_title=title)
    if schedule['date_source'] == 'undated':
        # A month heading supplies a month, not an invented delivery day.
        matches = re.findall(r'\b(' + MONTH + r')[\s,./-]+(20\d{2})\b', title, re.I)
        months = {f'{year}-{MONTHS[month.lower()]:02}' for month, year in matches}
        if len(months) == 1:
            schedule.update(month=months.pop(), date_source='section_month')
        elif months:
            schedule.update(date_source='section_needs_review', date_needs_review=True)
    return schedule


def read_export_placements(query, courses):
    if not courses:
        return []
    return query('''WITH exports AS (
        SELECT course_id, CASE WHEN jsonb_typeof(curriculum)='string'
            THEN (curriculum #>> '{}')::jsonb ELSE curriculum END AS payload
        FROM "MBA".course_curriculum WHERE course_id=ANY(%s))
        SELECT course_id,section->>'source_section_id' AS section_id,
            section->>'section_order' AS section_order,section->>'section_title' AS section_title,
            material->>'source_component_id' AS component_id,
            material->>'component_kind' AS component_kind,material->>'post_type' AS post_type,
            material->>'component_order' AS component_order,
            material->>'created_at_utc' AS original_created_at
        FROM exports CROSS JOIN LATERAL jsonb_array_elements(
            CASE WHEN jsonb_typeof(payload->'sections')='array'
                THEN payload->'sections' ELSE '[]'::jsonb END) section
        LEFT JOIN LATERAL jsonb_array_elements(
            CASE WHEN jsonb_typeof(section->'materials')='array'
                THEN section->'materials' ELSE '[]'::jsonb END) material ON true''',
        [[int(course['source_course_ref']) for course in courses]])


def catalogue_display_context(definitions, exports):
    """Use typed source IDs inside the owned course, never title matching."""
    from .source_sections import section_metadata
    original_sections = section_metadata(definitions, exports)
    owned = {str(item['source_course_ref']) for item in definitions}
    sections, placements, section_refs = defaultdict(dict), defaultdict(list), defaultdict(list)
    uploaded = defaultdict(set)
    for row in exports:
        course = str(row['course_id'])
        if course not in owned:
            continue
        section = (str(row.get('section_id') or ''), str(row.get('section_order') or ''),
                   unescape(str(row.get('section_title') or '')))
        if section not in sections[course]:
            sections[course][section] = section_schedule(section[2])
        if section[0] and section not in section_refs[(course, section[0])]:
            section_refs[(course, section[0])].append(section)
        kind = str(row.get('component_kind') or '').lower()
        post_type = str(row.get('post_type') or '').lower()
        kind = 'quiz' if kind == 'quiz' or post_type == 'stm-quizzes' else 'material' if kind == 'lesson' or post_type == 'stm-lessons' else None
        ident = str(row.get('component_id') or '')
        if kind and ident.isdigit():
            if section not in placements[(course, f'{kind}:{ident}')]:
                placements[(course, f'{kind}:{ident}')].append(section)
            day = original_upload_day(row.get('original_created_at'))
            if day:
                uploaded[(course, f'{kind}:{ident}')].add(day)

    introductions = set()
    for course, values in sections.items():
        # Duplicated/missing order cannot establish what preceded teaching.
        orders = [int(s[1]) for s in values if s[1].isdigit()]
        if len(orders) != len(values) or len(set(orders)) != len(orders):
            continue
        dated = [int(s[1]) for s, schedule in values.items() if schedule['month'] != 'undated']
        if dated:
            first_dated = min(dated)
            introductions.update((course, s) for s in values if int(s[1]) < first_dated
                                 and values[s]['date_source'] in {'undated', 'introduction'})

    enriched = []
    for definition in definitions:
        item = dict(definition)
        course, ref = str(item['source_course_ref']), item['source_activity_id']
        if (course, ref) in original_sections:
            item['source_section'] = original_sections[(course, ref)]
        matches = list(placements.get((course, ref), []))
        # The retained catalogue also records section membership directly.
        # Attached quizzes need not appear as standalone export materials.
        section_ref = str(item.get('source_section_ref') or '').strip()
        for section in section_refs.get((course, section_ref), []):
            if section not in matches:
                matches.append(section)
        payload = metadata(item.get('source_payload'))
        title = item.get('source_section_title') or payload.get('section_title') or ''
        if len(matches) == 1:
            section = matches[0]
            title = section[2]
            schedule = dict(sections[course][section])
            if (course, section) in introductions:
                schedule.update(date_source='introduction')
            apply_section_placement(schedule, int(course), section[0])
        elif len(matches) > 1:
            schedule = activity_schedule('')
            schedule.update(date_source='section_needs_review', date_needs_review=True)
        else:
            schedule = section_schedule(title)
        if schedule['date_source'] in {'undated', 'partial_title_needs_review'}:
            activity = activity_schedule(item.get('source_activity_title'))
            if schedule['date_source'] == 'undated' or activity['date_source'] == 'title':
                schedule = activity
        if schedule['date_source'] == 'undated':
            schedule['date_needs_review'] = True
        item['_display_schedule'] = schedule
        days = set(uploaded.get((course, ref), ()))
        # These values came from the original LMS post, not source_activities'
        # first_seen_at or a learner's submitted/reporting dates.
        for field in ('created_at_utc', 'created_at'):
            day = original_upload_day(payload.get(field))
            if day:
                days.add(day)
                break
        item['_original_upload_days'] = days
        item['source_section_title'] = title
        enriched.append(item)

    by_id = {(str(d['source_course_ref']), d['source_activity_id']): d for d in enriched}
    for item in enriched:
        course, ref = str(item['source_course_ref']), item['source_activity_id']
        if ref.startswith('quiz:'):
            quiz_id = ref.partition(':')[2]
            # Imported quizzes identify their parent by activity_id. Confirm
            # the inverse quiz_id link as well, in this exact owned course.
            payload = metadata(item.get('source_payload'))
            parent_id = str(payload.get('activity_id') or payload.get('parent_activity_id') or '')
            parent = by_id.get((course, f'material:{parent_id}'))
            if (parent_id.isdigit() and parent
                    and str(metadata(parent.get('source_payload')).get('quiz_id') or '') == quiz_id):
                parent_schedule = parent['_display_schedule']
                if (item['_display_schedule']['date_source'] == 'undated'
                        and parent_schedule['date_source'] != 'undated'
                        and not parent_schedule['date_needs_review']):
                    item['_display_schedule'] = dict(parent_schedule)
                    item['source_section_title'] = item['source_section_title'] or parent['source_section_title']
                if parent.get('material_available'):
                    item['source_material_activity_id'] = f'catalogue:{course}:material:{parent_id}'
                if not item.get('source_section') and not str(item.get('source_section_ref') or '').strip('0') and parent.get('source_section'):
                    item['source_section'] = dict(parent['source_section'])
                elif not parent.get('source_section') and not str(parent.get('source_section_ref') or '').strip('0') and item.get('source_section'):
                    parent['source_section'] = dict(item['source_section'])
    # Resolve companion quizzes before upload fallback, so a parent's authored
    # lecture date takes precedence over the quiz's original upload date.
    for item in enriched:
        days = item.pop('_original_upload_days')
        if (item['_display_schedule']['month'] == 'undated'
                and item['_display_schedule']['date_source'] not in {'introduction', 'extra_activity'}):
            if len(days) == 1:
                item['_display_schedule'] = activity_schedule('', original_created_at=next(iter(days)))
            elif len(days) > 1:
                item['_display_schedule'].update(date_source='source_date_needs_review', date_needs_review=True)
    return enriched


def retain_learner_schedule(definition, record, *, completed):
    """Keep execution evidence on this learner's projection, never the catalogue.

    A saved submission/start is more specific than a reporting allocation. A
    month-only record stays month-only; import/sync timestamps are not evidence.
    """
    day = next((value for field in ('submitted_at', 'started_at', 'reporting_started_at', 'reporting_ended_at')
                if (value := original_upload_day(record.get(field)))), None)
    month = day[:7] if day else str(record.get('reporting_month') or '')
    if not re.fullmatch(r'20\d{2}-(?:0[1-9]|1[0-2])', month):
        return
    placement = activity_schedule('', stored_date=day)
    placement.update(month=month, date_source='learner_activity_record', date_needs_review=False)
    # Passing/completed evidence wins over a later failed attempt, while every
    # record still contributes its original hours in the canonical aggregator.
    rank = (bool(completed), day or month)
    previous = definition.get('_learner_schedule')
    if previous is None or rank > previous['rank']:
        definition['_learner_schedule'] = {'rank': rank, 'placement': placement}


def display_schedule(definition):
    schedule = definition.get('_display_schedule')
    if (schedule and schedule.get('month') == 'undated'
            and schedule.get('date_source') not in {'introduction', 'extra_activity'}
            and definition.get('_learner_schedule')):
        return dict(definition['_learner_schedule']['placement'])
    return schedule


def apply_catalogue_display(items, definitions):
    by_id = {f"catalogue:{d['source_course_ref']}:{d['source_activity_id']}": d for d in definitions}
    for item in items:
        definition = by_id[item['activity_id']]
        schedule = display_schedule(definition)
        # Lecture/upload dates stay shared. Only unresolved activities use this
        # learner's execution month; the reporting ledger itself is untouched.
        if schedule:
            item.update(schedule)
        if definition.get('source_material_activity_id'):
            item['source_material_activity_id'] = definition['source_material_activity_id']
        if definition.get('source_section'):
            item['source_section'] = dict(definition['source_section'])
    return items
