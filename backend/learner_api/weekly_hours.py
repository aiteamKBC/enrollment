"""Accepted activity hours for Weekly Learning, using Overview's ledger rules."""
from collections import defaultdict

from . import canonical_learning
from .subject_content import clean_text


def module_activity_hours(records, modules):
    """Keep module and activity identities; never infer time from completion."""
    result = {str(module['id']): {'components': {}, 'quizzes': {}} for module in modules}
    ids_by_title = defaultdict(list)
    for module in modules:
        title = clean_text(module.get('title')).casefold()
        if title:
            ids_by_title[title].append(str(module['id']))
    for record in records:
        if not canonical_learning.counts_as_actual(record):
            continue
        module_id = str(record.get('module_ref') or '').strip()
        if module_id not in result:
            matches = ids_by_title.get(clean_text(record.get('module_title')).casefold(), [])
            module_id = matches[0] if len(matches) == 1 else None
        if module_id is None:
            continue
        component_id = str(record.get('component_ref') or '').strip()
        quiz_id = str(record.get('quiz_ref') or '').strip()
        # A quiz row with a component belongs only to that component. The quiz
        # fallback is for older rows that carry no component identity.
        key = component_id or quiz_id
        if not key:
            continue
        bucket = result[module_id]['components' if component_id else 'quizzes']
        hours = sum(canonical_learning.number(part.get('actual_seconds')) / 3600
                    for part in canonical_learning.allocations(record))
        bucket[key] = bucket.get(key, 0) + hours
    return result


def activity_hours_index(activity_hours, module_components):
    """Resolve quiz-only attribution once, rather than once per selected row."""
    placements = defaultdict(set)
    for component in module_components:
        if component.get('quiz_id') is not None:
            placements[str(component['quiz_id'])].add(str(component['id']))
    return {str(component['id']): activity_hours['components'].get(str(component['id']), 0)
            + (activity_hours['quizzes'].get(str(component['quiz_id']), 0)
               if component.get('quiz_id') is not None and len(placements[str(component['quiz_id'])]) == 1 else 0)
            for component in module_components}


def selected_activity_hours(activity_hours, components, module_components=None):
    """Sum a selected week's activities once, including legacy quiz references."""
    component_ids = {str(component['id']) for component in components}
    quiz_ids = {str(component['quiz_id']) for component in components
                if component.get('quiz_id') is not None}
    quiz_placements = defaultdict(set)
    for component in module_components if module_components is not None else components:
        if component.get('quiz_id') is not None:
            quiz_placements[str(component['quiz_id'])].add(str(component['id']))
    # A quiz-only record cannot be assigned to one week if that quiz appears
    # in multiple components. Component-linked records remain unambiguous.
    quiz_ids = {key for key in quiz_ids if len(quiz_placements[key]) == 1}
    return round(sum(activity_hours['components'].get(key, 0) for key in component_ids)
                 + sum(activity_hours['quizzes'].get(key, 0) for key in quiz_ids), 4)
