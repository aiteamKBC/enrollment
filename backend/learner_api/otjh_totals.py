"""Shared OTJ hour rules used by learner home and dashboard views."""

from math import isfinite

from .active_users import completed_hours_value_from_progress, otjh_progress_dedupe_key
from .progress_rules import progress_record_counts_as_achieved

ACCEPTED_ASSIGNMENT_STATUSES = {'accepted', 'partial'}


def completed_actual_otjh(native, progress, submissions, historical_hours, historical_refs=()):
    """Retained Audit hours plus timestamped LMS completions, without planned time.

    Exact source references identify progress already retained in the Audit.
    Imported progress is excluded by the direct-progress reader upstream.
    """
    if historical_hours is None or submissions is None:
        return None
    historical_refs = {ref for ref in historical_refs or () if ref}
    assignment_ids = {str(row['id']) for row in native
                      if row.get('id') is not None and row.get('type') == 'assignment'}
    assignment_ids.update(str(row['componentId']) for row in progress
                          if row.get('componentId') and row.get('componentType') == 'assignment')
    # Once one attempt is in the retained ledger, do not credit another copy
    # of the same activity from the current-platform progress collection.
    retained_keys = {otjh_progress_dedupe_key(row, index)
                     for index, row in enumerate(progress)
                     if row.get('sourceRef') in historical_refs}
    ordinary = []
    eligible = []
    for index, row in enumerate(progress):
        if (not row.get('submittedAt') or row.get('kind') == 'activity_event'
                or not progress_record_counts_as_achieved(row)
                or otjh_progress_dedupe_key(row, index) in retained_keys):
            continue
        actual = {key: value for key, value in row.items()
                  if key not in {'expectedOtjh', 'expected_otjh'}}
        eligible.append(actual)
        if str(row.get('componentId') or '') not in assignment_ids:
            ordinary.append(actual)
    total = float(historical_hours) + completed_hours_value_from_progress(ordinary)
    marking = {}
    retained_assignments = set()
    for row in submissions:
        component = str(row.get('component_ref') or row.get('activity_id') or '')
        if component in assignment_ids:
            marking[component] = row
            if {f"reflection:{row.get('id')}", f"progress:{row.get('progress_entry_id')}"} & historical_refs:
                retained_assignments.add(component)
    for component, row in marking.items():
        if (row.get('imported') or not row.get('submitted_at')
                or row.get('status') not in ACCEPTED_ASSIGNMENT_STATUSES):
            continue
        if component in retained_assignments:
            continue
        component_progress = [item for item in progress if str(item.get('componentId') or '') == component]
        if any(item.get('sourceRef') in historical_refs for item in component_progress):
            continue
        value = _hours(row.get('actual_time_hours'))
        if value is None:
            value = completed_hours_value_from_progress([
                item for item in eligible if str(item.get('componentId') or '') == component])
        total += value
    return round(total, 4) if isfinite(total) else None


def _hours(value):
    try:
        value = float(value)
        return value if isfinite(value) and value >= 0 else None
    except (TypeError, ValueError):
        return None


def completed_otjh(native, progress, submissions, historical_hours=0):
    """Return recorded OTJ time, counting tutor accepted assignments once.

    Assignment progress is excluded from the ordinary progress total because
    its time is governed by the latest reflection marking decision.
    """
    assignment_ids = {str(row.get('id')) for row in native
                      if row.get('id') is not None and row.get('type') == 'assignment'}
    assignment_ids.update(str(row.get('componentId')) for row in progress
                          if row.get('componentId') and row.get('componentType') == 'assignment')
    ordinary = [row for row in progress if str(row.get('componentId') or '') not in assignment_ids]
    total = float(historical_hours or 0) + completed_hours_value_from_progress(ordinary)

    marking = {}
    for row in submissions:
        component = str(row.get('component_ref') or row.get('activity_id') or '')
        if component in assignment_ids:
            marking[component] = row
    missing = False
    for component, row in marking.items():
        if row.get('imported') or row.get('status') not in ACCEPTED_ASSIGNMENT_STATUSES:
            continue
        value = _hours(row.get('actual_time_hours'))
        if value is None:
            matching = [item for item in progress
                        if str(item.get('componentId') or '') == component
                        and (item.get('reportedTime') or item.get('claimedSeconds') is not None
                             or item.get('verifiedSeconds') is not None)]
            matching = [{key: val for key, val in item.items()
                         if key not in {'expectedOtjh', 'expected_otjh'}} for item in matching]
            value = completed_hours_value_from_progress(matching) if matching else None
        if value is None:
            missing = True
        else:
            total += value
    return None if missing else round(total, 4)
