"""Coach view of the Inclusion support tickets raised for their learners.

The tickets live in the Inclusion & Safeguarding app's own database
(``public.support_tickets``, reached through ``SafeGuarding_database_url``);
the LMS does not own that table. It reads it, and makes the two narrow writes
agreed for coaches:

- a note on any ticket of a learner on their caseload;
- a status change on a *wellbeing* ticket. Safeguarding tickets stay with the
  DSL in the Inclusion app, so their status is read-only here.

Both writes are recorded in ``notes`` exactly the way the Inclusion app records
its own: a plain note is ``{id, note, created_at, created_by}`` and a status
change adds an ``activity`` entry reading "Status changed from X to Y".

Ownership is the coach caseload, not the ticket's ``coach_email``: a ticket is
shown when its learner email belongs to a learner assigned to this coach, using
the same hidden-programme rule as the dashboard. A learner reassigned to
another coach takes their tickets with them.

Admin view-as reads the page; writes are refused by ``coach_access_required``
(``coach_view_as_read_only``), so a note is never attributed to the coach whose
workspace an admin happened to open.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import psycopg
from django.conf import settings
from django.db.models.functions import Lower, Trim
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_http_methods
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from coach_api.auth import authenticated_coach_email, coach_access_required, is_coach_view_as
from coach_api.errors import coach_error
from coach_api.validation import ObjectValidator, ValidationError, parse_json_object, validation_error_response
from learner_api.models import LearnerProfile

logger = logging.getLogger(__name__)

CONNECTION_ENV_KEYS = ("SafeGuarding_database_url", "SAFEGUARDING_DATABASE_URL")
CONNECT_TIMEOUT_SECONDS = 10

# The Inclusion app's lifecycle, in workflow order. Stored lower-case; the
# activity note uses the title-cased label ("Action In Progress").
TICKET_STATUSES = (
    "new",
    "under review",
    "assigned",
    "action in progress",
    "outcome recorded",
    "closed",
)
STATUS_EDITABLE_TICKET_TYPES = {"wellbeing"}
NOTE_MAX_LENGTH = 4000

# Evidence entries carry an inline base64 copy of each file (``data_url``,
# hundreds of KB apiece); it is stripped in SQL so it never leaves the database.
TICKET_COLUMNS = """
    id, ticket_type, full_name, email, subject, details, urgency,
    preferred_contact, status, created_at, updated_at, notes,
    case when jsonb_typeof(evidence) = 'array' then (
        select coalesce(jsonb_agg(case when jsonb_typeof(item) = 'object' then item - 'data_url' else item end), '[]'::jsonb)
        from jsonb_array_elements(evidence) as item
    ) end as evidence,
    days_to_close, assigned_owner, is_archived
"""


class SupportTicketsUnavailable(RuntimeError):
    """The Inclusion database is not configured or could not be reached."""


def _normalize_email(value) -> str:
    return str(value or "").strip().lower()


def status_label(status) -> str:
    return " ".join(part.capitalize() for part in str(status or "").split())


def safeguarding_connection_string() -> str:
    """The Inclusion database DSN, or '' when this process must not reach it.

    The test runner and both test-branch modes never get it: those runs are
    built so that nothing they do can touch a production database, and this
    connection is opened directly rather than through a Django alias that the
    branch guards would otherwise remove.
    """
    if (
        "test" in sys.argv
        or getattr(settings, "RUN_APP_ON_TEST_BRANCH", False)
        or getattr(settings, "USE_SECURITY_TEST_BRANCH", False)
    ):
        return ""
    for key in CONNECTION_ENV_KEYS:
        value = (os.environ.get(key) or "").strip()
        if value.lower().startswith("psql "):
            value = value[5:].strip()
        value = value.strip('"').strip("'").strip()
        if value:
            return value
    return ""


def _connect():
    dsn = safeguarding_connection_string()
    if not dsn:
        raise SupportTicketsUnavailable("SafeGuarding_database_url is not configured.")
    try:
        return psycopg.connect(dsn, connect_timeout=CONNECT_TIMEOUT_SECONDS, row_factory=dict_row)
    except psycopg.Error as exc:
        raise SupportTicketsUnavailable("Could not connect to the Inclusion database.") from exc


def caseload_learners(owner_email: str) -> list[LearnerProfile]:
    """The coach's caseload as the dashboard shows it."""
    from coach_api.views import is_hidden_caseload_programme_status

    rows = (
        LearnerProfile.objects.annotate(coach_email_key=Lower(Trim("coach_email")))
        .filter(coach_email_key=_normalize_email(owner_email))
        .only("id", "enrolment_id", "full_name", "email", "programme", "cohort", "group_name", "programme_status")
        .order_by("full_name", "id")
    )
    return [
        row
        for row in rows
        if (row.full_name or "").strip()
        and _normalize_email(row.email)
        and not is_hidden_caseload_programme_status(row.programme_status)
    ]


def _caseload_by_email(owner_email: str) -> dict[str, LearnerProfile]:
    learners: dict[str, LearnerProfile] = {}
    for row in caseload_learners(owner_email):
        learners.setdefault(_normalize_email(row.email), row)
    return learners


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _json_value(value):
    """jsonb as a Python value. Inside the Django process psycopg hands jsonb
    back as text (Django replaces the jsonb loader globally), so decode it here
    rather than trusting the driver default."""
    if isinstance(value, (str, bytes, bytearray)):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _https_url(*candidates) -> str:
    for candidate in candidates:
        value = str(candidate or "").strip()
        if urlparse(value).scheme == "https":
            return value
    return ""


def _serialize_notes(notes) -> list[dict]:
    notes = _json_value(notes)
    entries = [entry for entry in (notes if isinstance(notes, list) else []) if isinstance(entry, dict)]
    serialized = [
        {
            "id": str(entry.get("id") or ""),
            "type": "activity" if entry.get("type") == "activity" else "note",
            "note": str(entry.get("note") or ""),
            "createdAt": entry.get("created_at"),
            "createdBy": str(entry.get("created_by") or ""),
        }
        for entry in entries
    ]
    serialized.sort(key=lambda entry: str(entry["createdAt"] or ""), reverse=True)
    return serialized


def _serialize_evidence(evidence) -> list[dict]:
    """File references only. ``data_url`` is an inline base64 copy of the file
    (hundreds of KB each) and is never sent to the browser."""
    items = []
    evidence = _json_value(evidence)
    for entry in evidence if isinstance(evidence, list) else []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("file_name") or "").strip()
        url = _https_url(entry.get("file_url"), entry.get("url"))
        if not name and not url:
            continue
        items.append({
            "id": str(entry.get("id") or ""),
            "fileName": name or "Evidence file",
            "mimeType": str(entry.get("mime_type") or ""),
            "description": str(entry.get("description") or ""),
            "url": url,
            "createdAt": entry.get("created_at"),
            "createdBy": str(entry.get("created_by") or ""),
        })
    return items


def serialize_ticket(row: dict, *, read_only: bool) -> dict:
    ticket_type = str(row.get("ticket_type") or "").strip().lower()
    status = str(row.get("status") or "").strip().lower()
    return {
        "id": row["id"],
        "ticketType": ticket_type,
        "subject": row.get("subject") or "",
        "details": row.get("details") or "",
        "urgency": (row.get("urgency") or "").strip().lower(),
        "preferredContact": row.get("preferred_contact") or "",
        "status": status,
        "statusLabel": status_label(status),
        "assignedOwner": row.get("assigned_owner") or "",
        "daysToClose": row.get("days_to_close"),
        "createdAt": _iso(row.get("created_at")),
        "updatedAt": _iso(row.get("updated_at")),
        "notes": _serialize_notes(row.get("notes")),
        "evidence": _serialize_evidence(row.get("evidence")),
        "canChangeStatus": not read_only and ticket_type in STATUS_EDITABLE_TICKET_TYPES,
        "canAddNote": not read_only,
    }


def _learner_payload(learner: LearnerProfile, tickets: list[dict]) -> dict:
    open_count = sum(1 for ticket in tickets if ticket["status"] != "closed")
    return {
        "learnerId": learner.id,
        "name": (learner.full_name or "").strip(),
        "email": _normalize_email(learner.email),
        "programme": learner.programme or "",
        "cohort": learner.cohort or "",
        "group": learner.group_name or "",
        "openTickets": open_count,
        "tickets": tickets,
    }


def _unavailable(request, exc: Exception):
    logger.warning("coach_support_tickets_unavailable reason=%s", exc.__class__.__name__)
    return coach_error(
        request,
        code="support_tickets_unavailable",
        message="The Inclusion ticket system is unavailable right now. Try again shortly.",
        status=503,
    )


@require_GET
@coach_access_required
def coach_support_tickets(request):
    owner_email = authenticated_coach_email(request)
    read_only = is_coach_view_as(request)
    learners = _caseload_by_email(owner_email)
    if not learners:
        return JsonResponse({"learners": [], "statuses": _status_options(), "readOnly": read_only})

    try:
        with _connect() as conn:
            conn.read_only = True
            rows = conn.execute(
                f"""
                select {TICKET_COLUMNS}, lower(trim(email)) as email_key
                from public.support_tickets
                where lower(trim(email)) = any(%s)
                  and coalesce(is_archived, false) = false
                order by created_at desc, id desc
                """,
                [list(learners)],
            ).fetchall()
    except (SupportTicketsUnavailable, psycopg.Error) as exc:
        return _unavailable(request, exc)

    tickets_by_email: dict[str, list[dict]] = {}
    for row in rows:
        tickets_by_email.setdefault(row["email_key"], []).append(serialize_ticket(row, read_only=read_only))

    payload = [
        _learner_payload(learners[email], tickets)
        for email, tickets in tickets_by_email.items()
        if email in learners
    ]
    payload.sort(key=lambda learner: (-learner["openTickets"], learner["name"].lower()))
    return JsonResponse({"learners": payload, "statuses": _status_options(), "readOnly": read_only})


def _status_options() -> list[dict]:
    return [{"value": status, "label": status_label(status)} for status in TICKET_STATUSES]


def _locked_owned_ticket(conn, ticket_id: int, owner_email: str) -> dict | None:
    """Lock the ticket row, or None when it is not one of this coach's."""
    row = conn.execute(
        f"select {TICKET_COLUMNS} from public.support_tickets where id = %s for update",
        [ticket_id],
    ).fetchone()
    if row is None or row.get("is_archived"):
        return None
    if _normalize_email(row.get("email")) not in _caseload_by_email(owner_email):
        return None
    return row


def _note_entry(note: str, author: str, *, activity: bool = False) -> dict:
    entry = {
        "id": uuid.uuid4().hex,
        "note": note,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": author,
    }
    if activity:
        entry["type"] = "activity"
    return entry


def _not_found(request):
    return coach_error(request, code="ticket_not_found", message="Support ticket not found.", status=404)


@require_http_methods(["PATCH"])
@coach_access_required
def coach_support_ticket_status(request, ticket_id: int):
    owner_email = authenticated_coach_email(request)
    try:
        validator = ObjectValidator(parse_json_object(request))
        new_status = validator.text("status", required=True, lower=True, choices=set(TICKET_STATUSES))
        expected_status = validator.text("expectedStatus", required=True, lower=True)
        validator.check()
    except ValidationError as exc:
        return validation_error_response(exc)

    try:
        with _connect() as conn, conn.transaction():
            row = _locked_owned_ticket(conn, ticket_id, owner_email)
            if row is None:
                return _not_found(request)
            if str(row.get("ticket_type") or "").strip().lower() not in STATUS_EDITABLE_TICKET_TYPES:
                return coach_error(
                    request,
                    code="ticket_status_locked",
                    message="Safeguarding ticket status is managed in the Inclusion system.",
                    status=403,
                )
            current_status = str(row.get("status") or "").strip().lower()
            if current_status != expected_status:
                # Somebody else (usually the Inclusion team) moved it since the
                # coach loaded the page; never silently overwrite their change.
                return coach_error(
                    request,
                    code="ticket_status_conflict",
                    message=f"This ticket is now {status_label(current_status)}. Reload to see the latest.",
                    status=409,
                )
            if current_status != new_status:
                activity = _note_entry(
                    f"Status changed from {status_label(current_status)} to {status_label(new_status)}",
                    owner_email,
                    activity=True,
                )
                row = conn.execute(
                    f"""
                    update public.support_tickets
                    set status = %s,
                        notes = coalesce(notes, '[]'::jsonb) || %s,
                        updated_at = now(),
                        days_to_close = case
                            when %s = 'closed'
                                then floor(extract(epoch from (now() - created_at)) / 86400)::int
                            else days_to_close
                        end
                    where id = %s
                    returning {TICKET_COLUMNS}
                    """,
                    [new_status, Jsonb([activity]), new_status, ticket_id],
                ).fetchone()
                logger.info(
                    "coach_support_ticket_status ticket_id=%s from=%s to=%s",
                    ticket_id, current_status, new_status,
                )
    except (SupportTicketsUnavailable, psycopg.Error) as exc:
        return _unavailable(request, exc)

    return JsonResponse({"ticket": serialize_ticket(row, read_only=False)})


@require_http_methods(["POST"])
@coach_access_required
def coach_support_ticket_notes(request, ticket_id: int):
    owner_email = authenticated_coach_email(request)
    try:
        validator = ObjectValidator(parse_json_object(request))
        note = validator.text("note", required=True, max_length=NOTE_MAX_LENGTH)
        validator.check()
    except ValidationError as exc:
        return validation_error_response(exc)

    try:
        with _connect() as conn, conn.transaction():
            if _locked_owned_ticket(conn, ticket_id, owner_email) is None:
                return _not_found(request)
            row = conn.execute(
                f"""
                update public.support_tickets
                set notes = coalesce(notes, '[]'::jsonb) || %s,
                    updated_at = now()
                where id = %s
                returning {TICKET_COLUMNS}
                """,
                [Jsonb([_note_entry(note, owner_email)]), ticket_id],
            ).fetchone()
            logger.info("coach_support_ticket_note ticket_id=%s", ticket_id)
    except (SupportTicketsUnavailable, psycopg.Error) as exc:
        return _unavailable(request, exc)

    return JsonResponse({"ticket": serialize_ticket(row, read_only=False)}, status=201)


# --- Wellbeing report ---------------------------------------------------------
# The survey behind a ticket (public.wellbeing_safeguarding_monitoring_system,
# joined on support_tickets.wellbeing_record_id), laid out the way the Inclusion
# app's Learner Wellbeing Report shows it. Only the scoring fields are selected:
# the row also carries the learner's phone, address, postcode and line-manager
# details, none of which this page needs.

REPORT_SCORE_KEYS = ("overall", "mental", "protective", "provider", "safeguarding")
WHY_FLAGGED = {
    "high score": "High answer on a risk question",
    "low score": "Low answer on a positive question",
}
TRIGGER_LEVELS = ("high", "medium")


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _utc_iso(value):
    # submitted_at is stored without a zone; it is written in UTC (it matches
    # the ticket's created_at, which is timestamptz).
    if isinstance(value, datetime) and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return _iso(value)


def _trigger_levels(triggers) -> dict:
    levels = {}
    if not isinstance(triggers, dict):
        return levels
    for level in TRIGGER_LEVELS:
        for item in triggers.get(level) or []:
            if isinstance(item, dict):
                key = item.get("question_id") or item.get("question_code")
                if key is not None:
                    levels.setdefault(str(key), level)
    return levels


def serialize_wellbeing_report(row: dict | None) -> dict:
    submission = _json_value(row.get("submission_json")) if row else None
    if not isinstance(submission, dict):
        return {"available": False}

    levels = _trigger_levels(submission.get("triggers"))
    answers = []
    for answer in submission.get("answers") or []:
        if not isinstance(answer, dict):
            continue
        key = str(answer.get("question_id") or answer.get("question_code") or "")
        level = levels.get(key) or levels.get(str(answer.get("question_code") or ""))
        rule = str(answer.get("trigger_rule") or "").strip().lower()
        answers.append({
            "id": key or str(len(answers)),
            "question": str(answer.get("question_text") or "").strip(),
            "category": str(answer.get("category_name") or "").strip(),
            "construct": str(answer.get("construct_type") or "").strip(),
            "learnerAnswer": _number(answer.get("raw_answer")),
            "concernScore": _number(answer.get("normalized_score")),
            "maxScore": _number(answer.get("max_score")) or 10,
            "reverseScored": bool(answer.get("is_reverse_scored")),
            "flag": level,
            "whyFlagged": WHY_FLAGGED.get(rule, "") if level else "",
        })

    triggers = submission.get("triggers") if isinstance(submission.get("triggers"), dict) else {}
    patterns = [str(item) for item in triggers.get("pattern") or [] if isinstance(item, str) and item.strip()]
    labels = submission.get("score_labels") if isinstance(submission.get("score_labels"), dict) else {}
    scores = submission.get("scores") if isinstance(submission.get("scores"), dict) else {}
    high = sum(1 for answer in answers if answer["flag"] == "high")
    medium = sum(1 for answer in answers if answer["flag"] == "medium")
    return {
        "available": True,
        "submittedAt": _utc_iso(row.get("submitted_at")),
        "riskLevel": str(row.get("risk_level") or submission.get("risk_level") or "").strip(),
        "triggerCount": row.get("trigger_count") if row.get("trigger_count") is not None else high + medium + len(patterns),
        "scores": [
            {"key": key, "label": str(labels.get(key) or status_label(key)), "value": _number(scores.get(key))}
            for key in REPORT_SCORE_KEYS
            if _number(scores.get(key)) is not None
        ],
        "patterns": patterns,
        "counts": {"riskFlags": high + medium, "high": high, "medium": medium, "answers": len(answers)},
        "answers": answers,
    }


@require_GET
@coach_access_required
def coach_support_ticket_wellbeing_report(request, ticket_id: int):
    owner_email = authenticated_coach_email(request)
    try:
        with _connect() as conn:
            conn.read_only = True
            ticket_row = conn.execute(
                "select id, email, is_archived, wellbeing_record_id from public.support_tickets where id = %s",
                [ticket_id],
            ).fetchone()
            if (
                ticket_row is None
                or ticket_row.get("is_archived")
                or _normalize_email(ticket_row.get("email")) not in _caseload_by_email(owner_email)
            ):
                return _not_found(request)
            report_row = None
            if ticket_row.get("wellbeing_record_id") is not None:
                report_row = conn.execute(
                    """
                    select submission_json, risk_level, trigger_count, submitted_at
                    from public.wellbeing_safeguarding_monitoring_system
                    where id = %s
                    """,
                    [ticket_row["wellbeing_record_id"]],
                ).fetchone()
    except (SupportTicketsUnavailable, psycopg.Error) as exc:
        return _unavailable(request, exc)

    return JsonResponse({"report": serialize_wellbeing_report(report_row)})
