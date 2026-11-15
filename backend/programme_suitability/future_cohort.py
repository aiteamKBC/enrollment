"""Fail-closed consumer of existing published cohort/assignment contracts.

No profile creation, new intake search, publication changes, or database writes.
The caller is the existing first-confirmation transaction, behind its unchanged
automatic-assignment flag. Existing APPLIED synchronizations never reach here.
"""
from datetime import date
from zoneinfo import ZoneInfo

from django.db import connections
from enrolment_api.wizard_layout import CONN, read_current

from .intake_calendar import cohort_assignment_window, validate_calendar
from .programme_identity import find_structure_programme
from .programme_structure import learner_published_layout, resolve_assigned_cohort


def confirmation_local_date(selection_confirmed_at, confirmed_at=None):
    timestamp = selection_confirmed_at or confirmed_at
    if timestamp is None or timestamp.utcoffset() is None:
        raise ValueError('An aware programme confirmation timestamp is required.')
    return timestamp.astimezone(ZoneInfo('Europe/London')).date()


def resolve_future_cohort(row, database, programme_id, completed_on, *, include_publication=False):
    """Validate prerequisites, then reuse the existing resolver unchanged.

    Both current and relevant pinned publications must actually contain the
    programme/cohort. Compatibility-generated cohorts and name matching cannot
    satisfy these prerequisites. Incompatible published cutoff periods block
    assignment; this consumer does not repair or override them.
    """
    from .programme_assignment import automatic_cohort_window, resolve_automatic_cohort

    try:
        pinned_version, pinned, _ = learner_published_layout(
            row.learner_kind, row.learner_id, module_version=row.modules_configuration_version)
        with connections[CONN].cursor() as cursor:
            current = read_current(cursor, row.learner_kind)
        pinned_snapshot = (pinned or {}).get('programmeStructure')
        latest_snapshot = ((current[1] if current else None) or {}).get('programmeStructure')
        if not isinstance(pinned_snapshot, dict) or not isinstance(latest_snapshot, dict):
            return None
        pinned_programme = find_structure_programme(pinned_snapshot, programme_id)
        latest_programme = find_structure_programme(latest_snapshot, programme_id)
        if (not pinned_programme or not latest_programme
                or pinned_programme['id'] != row.selected_programme
                or latest_programme['id'] != row.selected_programme
                or not any(c.get('assignmentPeriod') for c in latest_programme.get('cohorts', []))):
            return None
        calendar = (pinned or {}).get('intakeCalendar')
        if calendar is not None:
            validate_calendar(calendar)
        # Conflicting published allocation rules require a separate review.
        # Never rewrite that calendar in memory or storage.
        intended = automatic_cohort_window(completed_on)
        year = intended[1]
        expected_period = {
            'Feb': (date(year - 1, 10, 16), date(year, 2, 15)),
            'Jun': (date(year, 2, 16), date(year, 6, 15)),
            'Oct': (date(year, 6, 16), date(year, 10, 15)),
        }[intended[0]]
        if calendar and automatic_cohort_window(completed_on, calendar) != intended:
            return None
        result = resolve_automatic_cohort(database, programme_id, completed_on, calendar)
        if not result:
            return None
        for snapshot in (pinned_snapshot, latest_snapshot):
            cohort = resolve_assigned_cohort(snapshot, programme_id, result[0])
            if not cohort or cohort.get('status', 'active') not in {'active', 'planned'}:
                return None
            period = cohort_assignment_window(cohort)
            if period is None:
                return None
            if not period[0] <= completed_on <= period[1]:
                return None
            if tuple(period) != expected_period:
                return None
        if include_publication:
            cohort = resolve_assigned_cohort(pinned_snapshot, programme_id, result[0])
            if (type(pinned_version) is not int or pinned_version < 1
                    or not isinstance(cohort.get('name'), str) or not cohort['name'].strip()):
                return None
            return {'cohortId': result[0], 'name': cohort['name'],
                    'publicationVersion': pinned_version, 'fallback': bool(result[2])}
        return result
    except (LookupError, ValueError, TypeError, KeyError):
        return None
