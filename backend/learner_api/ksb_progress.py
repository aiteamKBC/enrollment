"""KSB progress derived from the assigned profile and completed plan units."""
from __future__ import annotations

from .ksb_codes import extract_ksb_codes, normalize_ksb_parent_code
from .training_plan_targets import achieved_component_identities, component_identity


def _text(value):
    return "" if value is None else str(value).strip()


def _code(value):
    return normalize_ksb_parent_code(value)


def _mapping_codes(component):
    mappings = component.get('ksbMappings')
    if not isinstance(mappings, list):
        mappings = component.get('ksbs') if isinstance(component.get('ksbs'), list) else []
    return extract_ksb_codes(mappings)


def _assigned_source(learner_profile):
    try:
        assignment = learner_profile.ksb_assignment
    except (AttributeError, ObjectDoesNotExist):
        assignment = None
    if assignment is None:
        return '', None
    version = getattr(assignment, 'profile_version', None)
    return _text(getattr(version, 'source_profile_id', '')), version


# Importing django here keeps the pure calculation below usable in simple unit
# tests while still handling a missing reverse OneToOne relation in production.
from django.core.exceptions import ObjectDoesNotExist  # noqa: E402


def assigned_standard_title(source_id, profile_version=None):
    """Return only title metadata confirmed by the assigned source."""
    if source_id.lower().startswith('standard:'):
        from curriculum_api.views import find_skills_england_standard

        standard = find_skills_england_standard(source_id.split(':', 1)[1])
        if standard:
            name = _text(standard.get('name'))
            version = _text(standard.get('version'))
            level = _text(standard.get('level'))
            heading = (
                name if 'apprenticeship standard' in name.casefold()
                else f'{name} Apprenticeship Standard' if name
                else 'Apprenticeship Standard'
            )
            parts = [heading]
            if version:
                parts.append(f'(v{version})')
            if level:
                parts.append(f'({level if level.casefold().startswith("level") else f"Level {level}"})')
            return ' '.join(parts)
    # A pinned custom profile version records its programme label. It is
    # confirmed assignment metadata, unlike guessing from arbitrary mappings.
    programme = _text(getattr(profile_version, 'programme', ''))
    return programme or 'Apprenticeship Standard progress'


def calculate_ksb_progress(learner_profile, detail, progress):
    """Required unique KSBs achieved through completed effective-plan units."""
    source_id, profile_version = _assigned_source(learner_profile)
    required_items = getattr(learner_profile, 'ksbs', None)
    required_codes = {
        _code(item.get('code') if isinstance(item, dict) else item)
        for item in (required_items if isinstance(required_items, list) else [])
        if _code(item.get('code') if isinstance(item, dict) else item)
    }
    title = assigned_standard_title(source_id, profile_version)
    if not source_id or not required_codes:
        return {
            'available': False,
            'title': title,
            'reason': 'The learner has no authoritative assigned KSB profile.',
            'actualPercent': None,
            'expectedPercent': 100,
        }

    achieved_identities = achieved_component_identities(progress)
    completed_codes = set()
    for component in detail.get('components') or []:
        if not isinstance(component, dict):
            continue
        identity = component_identity(component)
        if identity and identity in achieved_identities:
            completed_codes.update(_mapping_codes(component))
    achieved_required = completed_codes & required_codes
    actual, total = len(achieved_required), len(required_codes)
    percent = round(actual / total * 100, 2)
    variance = round(percent - 100, 2)
    return {
        'available': True,
        'title': title,
        'actual': actual,
        'expected': total,
        'planned': total,
        'actualPercent': percent,
        'expectedPercent': 100,
        'variancePercent': variance,
        'varianceDirection': 'above' if variance >= 0 else 'below',
        'achievedCodes': sorted(achieved_required),
        'requiredCodes': sorted(required_codes),
        'sourceId': source_id,
    }

