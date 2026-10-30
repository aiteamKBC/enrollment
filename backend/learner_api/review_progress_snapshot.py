"""Build the progress snapshot frozen when a coach presses Calculate.

Target Components and Target OTJ Hours come from one target calculator. It
resolves the learner's effective training plan and includes a component/hour
when its authoritative curriculum week has started.
Qualifying Review hours use the existing recurrence projection's targetDate;
the one server timestamp supplied by the Calculate endpoint is the cutoff for
both activity types. The current Review's own instance/booking date is never a
second input and its hours are not separately added.

``calculatedFrom`` and legacy ``weeksElapsed`` remain as explanatory metadata
for compatible readers, but they do not drive the versioned target formula.
Actual OTJ is read directly from the resolved learner profile's persisted
``completed_hours`` value. Progress Review is only a consumer of that value;
it does not recalculate or persist learner hours.
Snapshots are persisted by ``curriculum_api.review_instances`` and are never
recalculated on read, completion, or PDF rendering.
"""
from __future__ import annotations

from datetime import date, datetime, timezone as datetime_timezone
from decimal import Decimal, InvalidOperation

from .ksb_progress import calculate_ksb_progress
from .learner_detail import build_otjh_detail
from .review_progress_inputs import review_planned_target_hours
from .training_plan_targets import (
    TARGET_STATUS_RESOLVED,
    calculate_training_plan_targets,
)

#: Existing strategy selector retained for backward compatibility. Formula
#: evolution is recorded independently in ``formulaVersion``.
CALCULATION_METHOD_PLANNED_HOURS = 'planned_hours'
SNAPSHOT_SCHEMA_VERSION = 4
TARGET_FORMULA_VERSION = 'stored_completed_hours_and_component_ksb_v4'
ACTUAL_HOURS_SOURCE = 'learner.completed_hours'

DIRECTION_ABOVE = 'above'
DIRECTION_BELOW = 'below'


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def _percent(part, whole):
    try:
        part = float(part)
        whole = float(whole)
    except (TypeError, ValueError):
        return None
    if not whole:
        return None
    return round(part / whole * 100, 2)


def _variance(actual_percent, expected_percent):
    """(variance, direction) -- None when either side is unknown, so a missing
    figure reads as "not calculated" rather than a confident 0% on track."""
    if actual_percent is None or expected_percent is None:
        return None, ''
    variance = round(actual_percent - expected_percent, 2)
    return variance, DIRECTION_ABOVE if variance >= 0 else DIRECTION_BELOW


def _metric(*, actual, expected, planned, actual_percent, expected_percent):
    variance_percent, direction = _variance(actual_percent, expected_percent)
    return {
        'actual': actual,
        'expected': expected,
        'planned': planned,
        'actualPercent': actual_percent,
        'expectedPercent': expected_percent,
        'variancePercent': variance_percent,
        'varianceDirection': direction,
    }


def _progress_as_of(progress, calculated_at):
    """Rows visible at the captured instant; undated legacy rows stay visible."""
    visible = []
    for record in progress if isinstance(progress, list) else []:
        if not isinstance(record, dict):
            continue
        submitted = record.get('submittedAt') or record.get('submitted_at')
        if submitted:
            try:
                submitted_at = datetime.fromisoformat(str(submitted).replace('Z', '+00:00'))
                if submitted_at.tzinfo is None:
                    submitted_at = submitted_at.replace(tzinfo=datetime_timezone.utc)
                if submitted_at.astimezone(datetime_timezone.utc) > calculated_at:
                    continue
            except (TypeError, ValueError):
                # Malformed legacy timestamps were historically included. Keep
                # that behavior instead of silently removing earned progress.
                pass
        visible.append(record)
    return visible


class UnresolvedTrainingPlanTarget(ValueError):
    def __init__(self, calculation):
        self.calculation = calculation
        super().__init__('The learner training plan has no complete authoritative week schedule.')


class UnavailableCompletedHours(ValueError):
    """The resolved learner has no usable persisted completed-hours value."""


def _stored_completed_hours(learner_profile):
    raw_value = getattr(learner_profile, 'completed_hours', None)
    if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
        raise UnavailableCompletedHours(
            'This learner has no stored completed OTJ hours, so progress cannot be calculated.'
        )
    try:
        value = Decimal(str(raw_value).strip())
    except (InvalidOperation, TypeError, ValueError):
        raise UnavailableCompletedHours(
            'This learner\'s stored completed OTJ hours are invalid, so progress cannot be calculated.'
        ) from None
    if not value.is_finite() or value < 0:
        raise UnavailableCompletedHours(
            'This learner\'s stored completed OTJ hours are invalid, so progress cannot be calculated.'
        )
    return round(float(value), 2)


def _planned_hours_strategy(source, learner_profile, *, learner_start_date, calculated_at):
    detail = build_otjh_detail(source, learner_profile)
    progress = _progress_as_of(
        getattr(learner_profile, 'training_plan_progress', None), calculated_at,
    )
    review_planned, review_target = review_planned_target_hours(
        learner_profile,
        as_of=calculated_at,
        learner_start_date=learner_start_date,
        learner_end_date=getattr(source, 'learner_end_date', None),
    )
    targets = calculate_training_plan_targets(
        detail,
        progress=progress,
        as_of=calculated_at,
        planned_review_otj_hours=review_planned,
        target_review_otj_hours=review_target,
    )
    if targets['status'] != TARGET_STATUS_RESOLVED:
        raise UnresolvedTrainingPlanTarget(targets)

    planned_hours = targets['totalPlannedOtjHours']
    expected_hours = targets['targetOtjHours']
    actual_hours = _stored_completed_hours(learner_profile)

    planned_components = targets['totalComponents']
    completed_components = targets['completedComponents']
    expected_components = targets['targetComponents']

    return {
        'offTheJobHours': _metric(
            actual=actual_hours,
            expected=expected_hours,
            planned=planned_hours,
            actual_percent=_percent(actual_hours, planned_hours),
            expected_percent=_percent(expected_hours, planned_hours) if expected_hours is not None else None,
        ),
        'programmeProgress': _metric(
            actual=completed_components,
            expected=expected_components,
            planned=planned_components,
            actual_percent=_percent(completed_components, planned_components),
            expected_percent=(
                _percent(expected_components, planned_components) if expected_components is not None else None
            ),
        ),
        'ksbProgress': calculate_ksb_progress(learner_profile, detail, progress),
        'actualHoursSource': ACTUAL_HOURS_SOURCE,
        'targetCalculation': targets,
    }


CALCULATION_STRATEGIES = {
    CALCULATION_METHOD_PLANNED_HOURS: _planned_hours_strategy,
}


def build_progress_snapshot(
    source, learner_profile, *, learner_start_date, calculated_at,
    method=CALCULATION_METHOD_PLANNED_HOURS, calculated_by='',
):
    """One learner's cumulative progress from their own programme start date up
    to ``calculated_at``, ready to be frozen against a Review Instance.

    ``learner_start_date`` must already be the learner's own start date (see
    this module's docstring); this function does not resolve it, so it cannot
    silently substitute a cohort date when the real one is missing -- the
    caller decides what to do about that instead.
    """
    strategy = CALCULATION_STRATEGIES.get(method)
    if strategy is None:
        raise ValueError(f'Unknown progress calculation method "{method}".')
    start_date = _as_date(learner_start_date)
    if start_date is None:
        raise ValueError('This learner has no individual programme start date, so progress cannot be calculated.')

    if not isinstance(calculated_at, datetime) or calculated_at.tzinfo is None or calculated_at.utcoffset() is None:
        raise ValueError('Progress calculation requires a timezone-aware snapshot timestamp.')
    calculated_at_utc = calculated_at.astimezone(datetime_timezone.utc)
    metrics = strategy(
        source, learner_profile,
        learner_start_date=start_date, calculated_at=calculated_at_utc,
    )
    targets = metrics.pop('targetCalculation')
    as_of_date = date.fromisoformat(targets['asOfDate'])
    return {
        'calculationMethod': method,
        'schemaVersion': SNAPSHOT_SCHEMA_VERSION,
        'formulaVersion': TARGET_FORMULA_VERSION,
        'calculatedFrom': start_date.isoformat(),
        'calculatedAt': calculated_at_utc.isoformat().replace('+00:00', 'Z'),
        'calculatedBy': calculated_by or '',
        # Legacy explanatory field retained for old readers.  It no longer
        # drives either target; targetCalculation.qualifyingWeeks states the
        # authoritative schedule-based count.
        'weeksElapsed': max(0, (as_of_date - start_date).days // 7),
        'targetCalculation': targets,
        **metrics,
    }
