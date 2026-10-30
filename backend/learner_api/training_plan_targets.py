"""Authoritative target calculations for one learner's resolved training plan.

This module deliberately owns only *targets*.  Actual component completion and
actual OTJ hours keep their existing rules and callers. A frozen Progress
Review passes its resolved assigned plan through this calculator at one
authoritative ``as_of`` cutoff.

Authored weeks follow Curriculum's existing Monday-to-Sunday model.  The first
week starts on the module's own start date (so a mid-week module has a partial
first week); later weeks start on the corresponding Monday.  Missing schedule
metadata is an unresolved calculation, never a partial or zero target.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone as datetime_timezone
from zoneinfo import ZoneInfo

from django.db import DatabaseError, connections

from .progress_rules import progress_record_counts_as_achieved
from curriculum_api.week_visibility import visible_week_sql


UK_TIME_ZONE = ZoneInfo("Europe/London")
TARGET_STATUS_RESOLVED = "resolved"
TARGET_STATUS_UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class WeekScheduleResolution:
    starts: dict[str, date]
    reasons: list[dict]


def _text(value):
    return "" if value is None else str(value).strip()


def _number(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def as_of_date(as_of):
    if isinstance(as_of, datetime):
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("Target calculation requires a timezone-aware as_of timestamp.")
        return as_of.astimezone(UK_TIME_ZONE).date()
    if isinstance(as_of, date):
        return as_of
    raise ValueError("Target calculation requires an as_of date or timezone-aware timestamp.")


def _utc_iso(as_of):
    if not isinstance(as_of, datetime):
        return ""
    return as_of.astimezone(datetime_timezone.utc).isoformat().replace("+00:00", "Z")


def _reason(code, *, module_id="", week_id="", identity=""):
    return {
        "code": code,
        **({"moduleId": module_id} if module_id else {}),
        **({"weekId": week_id} if week_id else {}),
        **({"componentIdentity": identity} if identity else {}),
    }


def derive_week_starts(module_starts, ordered_week_ids_by_module):
    """Pure form of Curriculum's partial-first-week calendar rule."""
    starts = {}
    reasons = []
    for module_id, module_week_ids in ordered_week_ids_by_module.items():
        module_start = module_starts.get(module_id)
        if not module_start:
            for week_id in module_week_ids or [""]:
                reasons.append(_reason("module_has_no_start_date", module_id=module_id, week_id=week_id))
            continue
        anchor_monday = module_start - timedelta(days=module_start.weekday())
        for zero_based_index, week_id in enumerate(module_week_ids):
            calendar_week_start = anchor_monday + timedelta(days=zero_based_index * 7)
            starts[week_id] = max(module_start, calendar_week_start)
    return WeekScheduleResolution(starts, reasons)


def resolve_training_plan_week_starts(detail):
    """Resolve every id-bearing plan week through Curriculum's canonical order.

    ``curriculum.modules.start_date`` is group-delivery-specific because each
    assigned module row belongs to one group.  ``curriculum.weeks`` supplies the
    ordered authored weeks.  We enumerate that order instead of treating the
    stored zero-based ``display_order`` as a one-based week number.
    """
    plan_weeks = [row for row in (detail.get("week") or []) if isinstance(row, dict)]
    module_ids = list(dict.fromkeys(
        _text(row.get("moduleId")) for row in plan_weeks if _text(row.get("moduleId"))
    ))
    starts = {}
    reasons = []

    for row in plan_weeks:
        module_id, week_id = _text(row.get("moduleId")), _text(row.get("weekId"))
        if not module_id or not week_id:
            reasons.append(_reason("week_has_no_authoritative_curriculum_identity", module_id=module_id, week_id=week_id))

    if not module_ids:
        return WeekScheduleResolution(starts, reasons)

    placeholders = ", ".join(["%s"] * len(module_ids))
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute(
                "SELECT module_catalogue_id, start_date FROM curriculum.modules "
                f"WHERE module_catalogue_id IN ({placeholders})",
                module_ids,
            )
            module_starts = {str(module_id): start for module_id, start in cursor.fetchall()}
            cursor.execute(
                "SELECT id, module_catalogue_id FROM curriculum.weeks "
                f"WHERE module_catalogue_id IN ({placeholders}) "
                f"AND {visible_week_sql('weeks')} "
                "ORDER BY module_catalogue_id, display_order, week_number, id",
                module_ids,
            )
            ordered_weeks = cursor.fetchall()
    except DatabaseError:
        return WeekScheduleResolution(
            starts,
            reasons + [_reason("authoritative_week_schedule_lookup_failed")],
        )

    week_ids_by_module = {}
    for week_id, module_id in ordered_weeks:
        week_ids_by_module.setdefault(str(module_id), []).append(str(week_id))

    derived = derive_week_starts(
        module_starts,
        {module_id: week_ids_by_module.get(module_id, []) for module_id in module_ids},
    )
    starts.update(derived.starts)
    reasons.extend(derived.reasons)

    known_week_ids = set(starts)
    reason_week_ids = {item.get("weekId") for item in reasons}
    for row in plan_weeks:
        module_id, week_id = _text(row.get("moduleId")), _text(row.get("weekId"))
        if module_id and week_id and week_id not in known_week_ids and week_id not in reason_week_ids:
            reasons.append(_reason("week_not_found_in_authoritative_module_schedule", module_id=module_id, week_id=week_id))
    return WeekScheduleResolution(starts, reasons)


def component_identity(component):
    quiz_meta = component.get("quizMeta") if isinstance(component.get("quizMeta"), dict) else {}
    quiz_id = _text(quiz_meta.get("quizId"))
    if (component.get("isQuiz") or _text(component.get("type")).casefold() == "quiz") and quiz_id:
        # Quiz submissions persist quizId even when launched through an authored
        # component.  This identity therefore reconciles both linked and
        # standalone quizzes and deduplicates one logical quiz linked twice.
        return f"quiz:{quiz_id}"
    component_id = _text(component.get("componentId") or component.get("id"))
    return f"component:{component_id}" if component_id else ""


def achieved_component_identities(progress):
    achieved = set()
    for record in progress if isinstance(progress, list) else []:
        if not isinstance(record, dict) or not progress_record_counts_as_achieved(record):
            continue
        component_id = _text(record.get("componentId") or record.get("component_id"))
        quiz_id = _text(record.get("quizId") or record.get("quiz_id"))
        if component_id:
            achieved.add(f"component:{component_id}")
        if quiz_id:
            achieved.add(f"quiz:{quiz_id}")
    return achieved


def calculate_training_plan_targets(
    detail, *, progress, as_of, week_schedule=None,
    planned_review_otj_hours=0, target_review_otj_hours=0,
):
    """Calculate component and OTJ targets from one resolved plan and cutoff.

    ``week_schedule`` is injectable for deterministic DB-free tests.  Runtime
    callers omit it and use ``resolve_training_plan_week_starts``.
    """
    cutoff_date = as_of_date(as_of)
    components = [row for row in (detail.get("components") or []) if isinstance(row, dict)]
    resolution = week_schedule or (
        resolve_training_plan_week_starts(detail) if components else WeekScheduleResolution({}, [])
    )
    week_starts = dict(resolution.starts)
    relevant_week_ids = {_text(row.get("weekId")) for row in components if _text(row.get("weekId"))}
    reasons = [
        reason for reason in resolution.reasons
        if not reason.get("weekId") or reason.get("weekId") in relevant_week_ids
    ]

    units = {}
    for component in components:
        identity = component_identity(component)
        module_id = _text(component.get("moduleId"))
        week_id = _text(component.get("weekId"))
        if not identity:
            reasons.append(_reason("planned_component_has_no_reconcilable_identity", module_id=module_id, week_id=week_id))
            continue
        if not week_id:
            reasons.append(_reason("planned_component_has_no_authoritative_week", module_id=module_id, identity=identity))
            continue
        is_quiz = identity.startswith("quiz:")
        candidate = {
            "identity": identity,
            "moduleId": module_id,
            "weekId": week_id,
            "weekStart": week_starts.get(week_id),
            "isQuiz": is_quiz,
            # Quiz duration/expected fields remain excluded from OTJ exactly as
            # before; this phase changes component identity, not OTJ eligibility.
            "plannedOtjHours": 0.0 if is_quiz else _number(component.get("expectedOtjh")),
        }
        existing = units.get(identity)
        if existing:
            if existing["weekId"] != week_id or existing["weekStart"] != candidate["weekStart"]:
                reasons.append(_reason(
                    "logical_component_has_multiple_target_weeks",
                    module_id=module_id, week_id=week_id, identity=identity,
                ))
            continue
        units[identity] = candidate

    for unit in units.values():
        if unit["weekStart"] is None:
            reasons.append(_reason(
                "planned_component_week_date_unresolved",
                module_id=unit["moduleId"], week_id=unit["weekId"], identity=unit["identity"],
            ))

    # Stable de-duplication keeps one explainable reason per exact issue.
    unique_reasons = list({tuple(sorted(reason.items())): reason for reason in reasons}.values())
    achieved = achieved_component_identities(progress)
    total_components = len(units)
    completed_components = sum(identity in achieved for identity in units)
    component_planned_otj = round(sum(unit["plannedOtjHours"] for unit in units.values()), 2)
    review_planned_otj = round(max(_number(planned_review_otj_hours), 0), 2)
    review_target_otj = round(max(_number(target_review_otj_hours), 0), 2)
    total_planned_otj = round(component_planned_otj + review_planned_otj, 2)
    resolved = not unique_reasons
    eligible = [unit for unit in units.values() if unit["weekStart"] and unit["weekStart"] <= cutoff_date]
    current_week = max(
        (unit for unit in eligible), key=lambda unit: (unit["weekStart"], unit["weekId"]), default=None,
    ) if resolved else None
    if current_week:
        current_week = {
            "moduleId": current_week["moduleId"],
            "weekId": current_week["weekId"],
            "weekStart": current_week["weekStart"].isoformat(),
        }

    return {
        "status": TARGET_STATUS_RESOLVED if resolved else TARGET_STATUS_UNRESOLVED,
        "asOf": _utc_iso(as_of),
        "asOfDate": cutoff_date.isoformat(),
        "timeZone": str(UK_TIME_ZONE),
        "targetComponents": len(eligible) if resolved else None,
        "targetOtjHours": (
            round(sum(unit["plannedOtjHours"] for unit in eligible) + review_target_otj, 2)
            if resolved else None
        ),
        "totalComponents": total_components,
        "totalPlannedOtjHours": total_planned_otj,
        "componentPlannedOtjHours": component_planned_otj,
        "reviewPlannedOtjHours": review_planned_otj,
        "reviewTargetOtjHours": review_target_otj,
        "completedComponents": completed_components,
        "qualifyingWeeks": len({unit["weekId"] for unit in eligible}) if resolved else None,
        "totalWeeks": len({_text(row.get("weekId")) for row in detail.get("week") or [] if _text(row.get("weekId"))}),
        "unresolvedReasons": unique_reasons,
        "currentWeek": current_week,
    }
