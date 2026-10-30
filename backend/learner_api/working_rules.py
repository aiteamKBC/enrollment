"""The one working-rules check a completion timestamp has to pass.

Learning itself is unrestricted: a learner opens what they like, when they
like, from wherever they like. This module is consulted at exactly one moment
-- when a learner presses Finish -- to decide whether the instant they are
claiming credit for is a legitimate working instant.

Two instants exist for every completion and they are deliberately different
things:

``submitted_at``   the real moment the learner pressed Finish. Immutable audit
                   evidence, never validated away, never overwritten.
``declared_at``    the instant the learner states the work was actually done at,
                   supplied only after the first instant failed. It is not
                   required to be a working instant: the dialog warns, but a
                   declared time outside the rules is still accepted.

The holidays are the learner's OWN holidays. They are resolved through the
component's module to its cohort and handed to
``cohort_selected_holidays_by_cohort`` -- the same resolver the Module Builder,
the Teams series and the learner timeline already use, with the cohort's
``excluded_holiday_ids`` deny-list honoured upstream. A closure authored
against somebody else's programme therefore cannot close this learner's day,
and there is no second holiday store to keep in step.
"""
import os
from datetime import time
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connections
from django.utils import timezone
from django.utils.dateparse import parse_datetime

UK = ZoneInfo("Europe/London")

# The official working window. Monday-Friday, 07:00-19:00 UK time; 19:00 itself
# is already outside. GMT/BST is handled by the zone, not by arithmetic.
WORKING_DAY_START = time(7, 0)
WORKING_DAY_END = time(19, 0)
WORKING_WEEKDAYS = frozenset({0, 1, 2, 3, 4})

REASON_WEEKEND = "weekend"
REASON_OUTSIDE_HOURS = "outside_working_hours"
REASON_HOLIDAY = "holiday"


class DeclaredCompletionError(ValueError):
    """The declared completion instant was unusable or is not a working instant."""

    def __init__(self, message, reason=None, holiday_name=""):
        super().__init__(message)
        self.reason = reason
        self.holiday_name = holiday_name


def _module_cohort_for_component(component_id):
    """The cohort that owns a component, via its module. '' when unknown."""
    if not str(component_id or "").strip():
        return ""
    with connections["enrolment"].cursor() as cursor:
        cursor.execute(
            """SELECT m.cohort_id
                 FROM curriculum.components c
                 JOIN curriculum.modules m
                   ON m.module_catalogue_id = c.module_catalogue_id
                WHERE c.id = %s
                LIMIT 1""",
            [str(component_id)],
        )
        row = cursor.fetchone()
    return str(row[0]) if row and row[0] else ""


def _module_cohort_for_quiz(quiz_id):
    """The cohort that owns a quiz, via its week's module. '' when unknown.

    A quiz hangs off a week rather than a component, so it reaches its module
    through ``curriculum.weeks``; from there the cohort is read exactly as a
    component's is (``curriculum.modules`` is keyed by module_catalogue_id, so
    one module row carries one cohort).
    """
    if not str(quiz_id or "").strip():
        return ""
    with connections["enrolment"].cursor() as cursor:
        cursor.execute(
            """SELECT m.cohort_id
                 FROM curriculum.quizzes q
                 JOIN curriculum.weeks w
                   ON w.id = q.week_id
                 JOIN curriculum.modules m
                   ON m.module_catalogue_id = w.module_catalogue_id
                WHERE q.id = %s
                LIMIT 1""",
            [quiz_id],
        )
        row = cursor.fetchone()
    return str(row[0]) if row and row[0] else ""


def learner_holiday_details(component_id=None, *, quiz_id=None):
    """Closed dates -> the bank holidays closing them, for this activity's cohort.

    Only GOV.UK bank holidays count for the working rules, each on its own
    date. Authored closure periods (a workshop week, a training day) still
    shape session plans and Teams series, but they are not holidays for a
    completion: a workshop week must not flag every day of that week.

    Returns ``{}`` when the activity has no cohort: work outside a cohort's
    delivery (personal learning, a free course) has no cohort calendar to sit
    on, so nothing is a holiday for it.
    """
    from curriculum_api.views import (
        HOLIDAY_SOURCE_GOVUK,
        cohort_selected_holidays_by_cohort,
        holiday_details_by_date,
    )

    cohort_id = (
        _module_cohort_for_quiz(quiz_id) if quiz_id
        else _module_cohort_for_component(component_id)
    )
    if not cohort_id:
        return {}
    holidays = cohort_selected_holidays_by_cohort([cohort_id]).get(cohort_id) or []
    bank_holidays = [
        item for item in holidays
        if (item or {}).get("source") == HOLIDAY_SOURCE_GOVUK
    ]
    return holiday_details_by_date(bank_holidays)


def _as_uk(instant):
    if timezone.is_naive(instant):
        instant = timezone.make_aware(instant, UK)
    return instant.astimezone(UK)


# The env var a tester sets to pretend it is some other moment, e.g.
# TEST_COMPLETION_NOW=2026-10-04T22:30:00+01:00
SIMULATED_NOW_ENV = "TEST_COMPLETION_NOW"


def _simulated_now():
    """The instant the working rules should judge, when a tester has forced one.

    Returns ``None`` -- meaning "use the learner's real click" -- everywhere
    except a process running in test-branch mode with the variable set. Two
    independent locks keep this off in production:

    1. ``settings.RUN_APP_ON_TEST_BRANCH`` is read FIRST, so with the flag off
       the variable is never even looked at and behaviour is unchanged.
    2. A process carrying that flag cannot boot against production at all:
       ``config.settings`` fails closed unless every database URL resolves to
       the sanitised Neon branch and the audit/attendance aliases are blank.

    This moves only the moment the rules are applied to. It is deliberately not
    returned to the caller, so the stored ``submitted_at`` -- the audit evidence
    of the real click -- is untouched, as are engagement timestamps and every
    other clock in the system.
    """
    if not getattr(settings, "RUN_APP_ON_TEST_BRANCH", False):
        return None
    raw = str(os.environ.get(SIMULATED_NOW_ENV) or "").strip()
    if not raw:
        return None
    parsed = parse_datetime(raw)
    if parsed is None:
        # Fail loud rather than silently judging against the real clock, which
        # would quietly invalidate whatever scenario the tester meant to run.
        raise ImproperlyConfigured(
            f"{SIMULATED_NOW_ENV}={raw!r} is not an ISO 8601 datetime, e.g. "
            "2026-10-04T22:30:00+01:00."
        )
    return _as_uk(parsed)


def working_rule_failure(instant, *, component_id=None, quiz_id=None, holiday_details=None):
    """Why ``instant`` is not a working instant, or ``None`` when it is.

    ``holiday_details`` lets a caller validating several instants (the dialog
    round-trip) resolve the learner's holidays once instead of per check.
    """
    local = _as_uk(instant)

    if holiday_details is None:
        holiday_details = learner_holiday_details(component_id, quiz_id=quiz_id)
    closures = holiday_details.get(local.date()) or []
    if closures:
        name = ""
        for closure in closures:
            name = str((closure or {}).get("label") or "").strip()
            if name:
                break
        return {
            "reason": REASON_HOLIDAY,
            "holidayName": name,
            "message": (
                f"Bank holiday: {name}." if name else "This date is a bank holiday."
            ),
        }

    if local.weekday() not in WORKING_WEEKDAYS:
        return {
            "reason": REASON_WEEKEND,
            "holidayName": "",
            "message": f"{local.strftime('%A')} is not a working day.",
        }

    if not (WORKING_DAY_START <= local.time() < WORKING_DAY_END):
        return {
            "reason": REASON_OUTSIDE_HOURS,
            "holidayName": "",
            "message": "Outside official working hours (Monday to Friday, 07:00-19:00 UK time).",
        }

    return None


def parse_declared_completion(value):
    """The learner's declared instant as an aware datetime, or ``None``.

    A bare ``2026-09-29T14:30`` from a date+time picker carries no offset and
    means UK local time, which is what the learner typed and what the rules are
    written in.
    """
    text = str(value or "").strip()
    if not text:
        return None
    parsed = parse_datetime(text)
    if parsed is None:
        raise DeclaredCompletionError(
            "The completion date and time could not be read. Please select them again."
        )
    return _as_uk(parsed)


def resolve_completion_instants(payload, submitted_at, *, component_id=None, quiz_id=None):
    """Decide the pair of instants a completion is written with.

    Returns ``(declared_at, validation_reason)``. ``declared_at`` is ``None``
    when the learner's real Finish click was already valid -- there is nothing
    to declare and nothing to explain. Raises ``DeclaredCompletionError`` when
    the completion must not be written, which is the caller's signal to answer
    409 and open the correction dialog.

    The caller must invoke this BEFORE writing any progress, because a refusal
    here means no completion, no percentage change and no history entry.
    """
    holiday_details = learner_holiday_details(component_id, quiz_id=quiz_id)
    # The instant the RULES judge. Identical to the real click everywhere except
    # a test-branch process running a forced clock; ``submitted_at`` itself is
    # never reassigned, so what the caller stores as audit evidence is the real
    # click either way.
    judged_at = _simulated_now() or submitted_at
    actual_failure = working_rule_failure(
        judged_at, holiday_details=holiday_details,
    )

    declared_at = parse_declared_completion(payload.get("declaredCompletedAt"))

    if declared_at is None:
        if actual_failure is None:
            return None, ""
        raise DeclaredCompletionError(
            actual_failure["message"],
            reason=actual_failure["reason"],
            holiday_name=actual_failure["holidayName"],
        )

    # A declared instant outside the working rules is accepted: the dialog warns
    # the learner, but the time they actually did the work is theirs to state.
    # Only an impossible (future) instant is refused.
    if declared_at > judged_at:
        raise DeclaredCompletionError(
            "The completion date and time cannot be in the future.",
            reason=REASON_OUTSIDE_HOURS,
        )

    # The stored reason explains why the REAL click needed correcting. When the
    # click was already valid a declared instant is accepted but explains
    # nothing, so no reason is recorded.
    return declared_at, (actual_failure or {}).get("reason") or ""


def validation_response_payload(error):
    """The structured body the correction dialog is opened from."""
    return {
        "error": str(error),
        "validation": {
            "reason": error.reason or "",
            "holidayName": error.holiday_name or "",
        },
    }


def reporting_instant(record):
    """The instant OTJH and reporting count a progress record at.

    The declared working instant when the learner had to correct their Finish
    click, otherwise the click itself. The click always remains readable at
    ``submittedAt`` for audit.
    """
    if not isinstance(record, dict):
        return ""
    for field in ("declaredCompletedAt", "submittedAt"):
        value = str(record.get(field) or "").strip()
        if value:
            return value
    return ""
