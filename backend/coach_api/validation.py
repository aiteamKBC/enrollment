"""Small, dependency-free validation primitives for Coach HTTP inputs."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta

from django.http import JsonResponse


class ValidationError(ValueError):
    def __init__(self, fields: dict[str, list[str]]):
        super().__init__("Coach request validation failed")
        self.fields = fields


def validation_error_response(error: ValidationError) -> JsonResponse:
    return JsonResponse(
        {"error": "validation_error", "fields": error.fields},
        status=400,
    )


def parse_json_object(request) -> dict:
    if not request.body:
        return {}
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationError({"body": ["Request body must be valid JSON."]}) from exc
    if not isinstance(payload, dict):
        raise ValidationError({"body": ["Request body must be a JSON object."]})
    return payload


class ObjectValidator:
    def __init__(self, payload: dict):
        self.payload = payload
        self.fields: dict[str, list[str]] = {}

    def error(self, field: str, message: str):
        self.fields.setdefault(field, []).append(message)

    def text(
        self,
        field: str,
        *,
        required: bool = False,
        max_length: int | None = None,
        choices: set[str] | None = None,
        default: str = "",
        lower: bool = False,
    ) -> str:
        value = self.payload.get(field)
        if value is None or value == "":
            if required:
                self.error(field, "This field is required.")
            return default
        if not isinstance(value, str):
            self.error(field, "Must be a string.")
            return default
        value = value.strip()
        if lower:
            value = value.lower()
        if required and not value:
            self.error(field, "This field is required.")
        if max_length is not None and len(value) > max_length:
            self.error(field, f"Must be at most {max_length} characters.")
        if choices is not None and value not in choices:
            self.error(field, f"Must be one of: {', '.join(sorted(choices))}.")
        return value

    def integer(
        self,
        field: str,
        *,
        required: bool = False,
        default: int | None = None,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int | None:
        value = self.payload.get(field)
        if value is None or value == "":
            if required:
                self.error(field, "This field is required.")
            return default
        if isinstance(value, bool):
            self.error(field, "Must be a whole number.")
            return default
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            self.error(field, "Must be a whole number.")
            return default
        if isinstance(value, float) and not value.is_integer():
            self.error(field, "Must be a whole number.")
        if minimum is not None and parsed < minimum:
            self.error(field, f"Must be at least {minimum}.")
        if maximum is not None and parsed > maximum:
            self.error(field, f"Must be at most {maximum}.")
        return parsed

    def iso_date(self, field: str, *, required: bool = False) -> date | None:
        value = self.payload.get(field)
        if value is None or value == "":
            if required:
                self.error(field, "This field is required.")
            return None
        if not isinstance(value, str):
            self.error(field, "Must be a date in YYYY-MM-DD format.")
            return None
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            self.error(field, "Must be a valid date in YYYY-MM-DD format.")
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            self.error(field, "Must be a valid date in YYYY-MM-DD format.")
            return None

    def clock_time(self, field: str, *, required: bool = False) -> time | None:
        value = self.payload.get(field)
        if value is None or value == "":
            if required:
                self.error(field, "This field is required.")
            return None
        if not isinstance(value, str):
            self.error(field, "Must be a valid 24-hour clock time.")
            return None
        patterns = ((r"\d{2}:\d{2}", "%H:%M"), (r"\d{2}:\d{2}:\d{2}", "%H:%M:%S"))
        for expected, pattern in patterns:
            if not re.fullmatch(expected, value):
                continue
            try:
                return datetime.strptime(value, pattern).time().replace(second=0, microsecond=0)
            except ValueError:
                continue
        self.error(field, "Must be a valid 24-hour clock time.")
        return None

    def object(self, field: str, *, required: bool = False) -> dict | None:
        value = self.payload.get(field)
        if value is None:
            if required:
                self.error(field, "This field is required.")
            return None
        if not isinstance(value, dict):
            self.error(field, "Must be a JSON object.")
            return None
        return value

    def check(self):
        if self.fields:
            raise ValidationError(self.fields)


def validate_month(value: str | None) -> tuple[date, date, str, str]:
    if not value or not isinstance(value, str):
        raise ValidationError({"month": ["Month is required in YYYY-MM format."]})
    if not re.fullmatch(r"\d{4}-\d{2}", value):
        raise ValidationError({"month": ["Must be a valid month in YYYY-MM format."]})
    try:
        start = datetime.strptime(value, "%Y-%m").date().replace(day=1)
    except ValueError as exc:
        raise ValidationError({"month": ["Must be a valid month in YYYY-MM format."]}) from exc
    next_month = date(start.year + (1 if start.month == 12 else 0), 1 if start.month == 12 else start.month + 1, 1)
    end = next_month - timedelta(days=1)
    return start, end, start.strftime("%B %Y"), start.strftime("%Y-%m")
