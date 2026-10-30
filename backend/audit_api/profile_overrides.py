"""Auditor corrections for learner profile and employer fields."""

import datetime
import json
import re

from django.db import DatabaseError, connections
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .audit_trail import PROFILE_OVERRIDE_COLUMNS, PROFILE_OVERRIDE_SQL, record_audit_rows
from .db_source import resolve
from .learner_exclusions import is_excluded_learner
from .views import _has_audit_permission


CONN = "enrolment"
PROFILE_FIELDS = {
    "employer_name",
    "job_title",
    "employment_start_date",
    "contracted_hours_per_week",
    "line_manager_name",
    "workplace_address",
    "employer_postcode",
    "levy_status",
    "start_date",
    "planned_end_date",
    "last_learning_date",
    "expected_return_date",
    "return_to_learning_date",
    "revised_learning_planned_end_date",
}
DATE_FIELDS = {
    "start_date",
    "planned_end_date",
    "last_learning_date",
    "expected_return_date",
    "return_to_learning_date",
    "revised_learning_planned_end_date",
}
TEXT_LIMITS = {
    "employer_name": 250,
    "job_title": 250,
    "employment_start_date": 50,
    "line_manager_name": 250,
    "workplace_address": 1000,
    "employer_postcode": 30,
    "levy_status": 20,
}


def ensure_profile_override_table(cursor):
    cursor.execute(
        '''
        select column_name
        from information_schema.columns
        where table_schema = 'Audit' and table_name = 'learner_profile_overrides'
        '''
    )
    existing_columns = {row[0] for row in cursor.fetchall()}
    if {"learner_id", "values", "updated_by", "updated_at"}.issubset(existing_columns):
        return
    cursor.execute(
        '''
        create table if not exists "Audit".learner_profile_overrides (
            learner_id bigint primary key,
            "values" jsonb not null default '{}'::jsonb,
            updated_by text,
            updated_at timestamptz not null default now()
        )
        '''
    )


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


def _clean_profile_fields(value):
    if not isinstance(value, dict) or not value:
        raise ValueError("fields must be a non-empty object.")
    unknown = set(value) - PROFILE_FIELDS
    if unknown:
        raise ValueError(f"Unsupported profile field: {sorted(unknown)[0]}.")

    cleaned = {}
    for field, raw in value.items():
        if field == "levy_status":
            text = str(raw or "").strip()
            if text not in {"", "Levy", "Non-Levy"}:
                raise ValueError("levy_status must be Levy or Non-Levy.")
            cleaned[field] = text or None
            continue

        if field == "contracted_hours_per_week":
            if raw in (None, ""):
                cleaned[field] = None
                continue
            try:
                hours = float(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError("contracted_hours_per_week must be a number.") from exc
            if hours < 0 or hours > 168:
                raise ValueError("contracted_hours_per_week must be between 0 and 168.")
            cleaned[field] = hours
            continue

        if field in DATE_FIELDS:
            text = str(raw or "").strip()
            if text:
                try:
                    datetime.date.fromisoformat(text)
                except ValueError as exc:
                    raise ValueError(f"{field} must use YYYY-MM-DD.") from exc
            cleaned[field] = text or None
            continue

        text = re.sub(r"[\x00]+", "", str(raw or "")).strip()
        if len(text) > TEXT_LIMITS[field]:
            raise ValueError(f"{field} must be {TEXT_LIMITS[field]} characters or fewer.")
        cleaned[field] = text or None
    return cleaned


def get_profile_overrides(learner_id):
    with connections[resolve(CONN)].cursor() as cursor:
        ensure_profile_override_table(cursor)
        cursor.execute(
            '''
            select "values"
            from "Audit".learner_profile_overrides
            where learner_id = %s
            limit 1
            ''',
            [learner_id],
        )
        row = cursor.fetchone()
    value = row[0] if row else {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def apply_profile_overrides(employment, learning_delivery, overrides):
    if not isinstance(overrides, dict) or not overrides:
        return employment, learning_delivery

    employer_fields = {
        "employer_name", "job_title", "employment_start_date",
        "contracted_hours_per_week", "workplace_address", "levy_status",
    }
    if any(field in overrides for field in employer_fields | {"line_manager_name"}):
        employment = dict(employment or {})
        for field in employer_fields:
            if field in overrides:
                employment[field] = overrides[field]
        manager = dict(employment.get("line_manager") or {})
        if "line_manager_name" in overrides:
            manager["name"] = overrides["line_manager_name"]
        employment["line_manager"] = manager

    learning_delivery = dict(learning_delivery or {})
    if "employer_postcode" in overrides:
        learning_delivery["employer_postcode"] = overrides["employer_postcode"]
    if "start_date" in overrides:
        learning_delivery["start_date"] = overrides["start_date"]
    if "planned_end_date" in overrides:
        learning_delivery["planned_end_date"] = overrides["planned_end_date"]
    return employment, learning_delivery


def apply_break_overrides(break_in_learning, overrides):
    result = dict(break_in_learning or {})
    if not isinstance(overrides, dict):
        return result
    for field in {
        "last_learning_date",
        "expected_return_date",
        "return_to_learning_date",
        "revised_learning_planned_end_date",
    }:
        if field in overrides:
            result[field] = overrides[field]
    if "return_to_learning_date" in overrides:
        result["has_return_to_learning"] = bool(overrides["return_to_learning_date"])
    return result


@csrf_exempt
def update_profile_overrides(request):
    if request.method != "PATCH":
        return _error("Method not allowed.", 405)
    if not _has_audit_permission(request, write=True):
        return _error("Authentication or audit permission is required.", 403)
    try:
        body = json.loads(request.body or b"{}")
        learner_id = int(body.get("learner_id"))
        fields = _clean_profile_fields(body.get("fields"))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        return _error(str(error), 400)
    updated_by = str(body.get("updated_by") or "").strip()[:200] or None

    try:
        with connections[resolve(CONN)].cursor() as cursor:
            cursor.execute(
                '''select learner_name from "Last_audit".learners where aptem_id = %s limit 1''',
                [learner_id],
            )
            learner = cursor.fetchone()
            if not learner or is_excluded_learner(learner_id, learner[0]):
                return _error("Learner not found.", 404)
            ensure_profile_override_table(cursor)
            cursor.execute(
                '''
                insert into "Audit".learner_profile_overrides (
                    learner_id, "values", updated_by, updated_at
                ) values (%s, %s::jsonb, %s, now())
                on conflict (learner_id) do update set
                    "values" = "Audit".learner_profile_overrides."values" || excluded."values",
                    updated_by = excluded.updated_by,
                    updated_at = now()
                returning ''' + PROFILE_OVERRIDE_SQL + '''
                ''',
                [learner_id, json.dumps(fields), updated_by],
            )
            row = cursor.fetchone()
            # The upsert merges the new fields into the stored document, so the
            # values it replaced exist nowhere else once this returns.
            record_audit_rows('learner_profile_overrides', PROFILE_OVERRIDE_COLUMNS, row)
            saved = dict(zip(PROFILE_OVERRIDE_COLUMNS, row))['values']
    except (KeyError, DatabaseError):
        return _error("Could not update the learner profile.", 503)

    return JsonResponse({"ok": True, "learner_id": str(learner_id), "fields": saved})
