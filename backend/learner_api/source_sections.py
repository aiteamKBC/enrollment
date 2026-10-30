"""Original curriculum sections, independent of upload and reporting dates."""
from collections import defaultdict
from html import unescape
import json


def number(value):
    return int(value) if not isinstance(value, bool) and str(value).isdigit() else None


def section_metadata(definitions, exports):
    """Retain exact course/typed-component placement; do not infer it from titles."""
    owned = {str(row['source_course_ref']) for row in definitions}
    sections, placements, positions = defaultdict(list), defaultdict(set), defaultdict(set)
    for row in exports:
        course, section = str(row['course_id']), str(row.get('section_id') or '')
        if course not in owned or not number(section):
            continue
        sections[(course, section)].append((unescape(str(row.get('section_title') or '')), number(row.get('section_order'))))
        kind = 'quiz' if row.get('component_kind') == 'quiz' or row.get('post_type') == 'stm-quizzes' else (
            'material' if row.get('component_kind') == 'lesson' or row.get('post_type') == 'stm-lessons' else None)
        if kind and number(row.get('component_id')):
            key = (course, f"{kind}:{int(row['component_id'])}")
            placements[key].add(section)
            if number(row.get('component_order')) is not None:
                positions[key].add(number(row['component_order']))
    for row in definitions:
        course, section = str(row['source_course_ref']), str(row.get('source_section_ref') or '')
        if not number(section):
            continue
        payload = row.get('source_payload') or {}
        if isinstance(payload, str):
            payload = json.loads(payload)
        payload = payload if isinstance(payload, dict) else {}
        sections[(course, section)].append((unescape(str(row.get('source_section_title') or payload.get('section_title') or '')),
                                            number(payload.get('section_order'))))
        key = (course, row['source_activity_id'])
        placements[key].add(section)
        position = number(payload.get('component_order'))
        if position is not None:
            positions[key].add(position)
    result = {}
    for key, refs in placements.items():
        if len(refs) != 1:
            continue
        section = next(iter(refs))
        values = sections[(key[0], section)]
        titles = {title for title, _ in values if title}
        orders = {order for _, order in values if order is not None}
        if len(titles) != 1:
            continue
        result[key] = {'id': section, 'title': next(iter(titles)),
                       'order': next(iter(orders)) if len(orders) == 1 else None,
                       'component_order': next(iter(positions[key])) if len(positions[key]) == 1 else None}
    return result
