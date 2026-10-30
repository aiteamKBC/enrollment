"""Extended ILR + wizard draft read/write endpoint.

GET  /enrolment_api/extended-ilr/<kind>/<id>/   -> {answers, draft, meta}
PUT  /enrolment_api/extended-ilr/<kind>/<id>/   -> upsert, returns the same shape

`answers` is the wizard's IlrForm document and `draft` is every other wizard step
(personal details, skills radar, PLR, CV/job, policies), both stored verbatim as
jsonb. The server does not enumerate their fields — the ILR is reworded whenever
the ESFA revises it, and mirroring every question here would mean a backend change
for each edit. What the server does own is the envelope: which learner the row
belongs to, and the signature/completion flags other features report on, which are
derived from the document on every write so they cannot drift from it.

`draft` is optional on write: omitting it leaves any stored draft untouched, so a
caller that only has ILR data cannot silently wipe the other steps.
"""
import json

from django.db import DatabaseError, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from learner_api.models import CommercialUser, EnrolmentUser

from .auth import enrolment_login_required
from .models import ExtendedIlr
from .wizard_steps import (
    ILR_DETAILS_KEY,
    project_draft,
    read_ilr_details,
    read_projection,
    without_ilr_details,
)
from .wizard_layout import project_custom_fields

KINDS = {"apprenticeship": EnrolmentUser, "commercial": CommercialUser}

# Guardrail against a runaway payload filling the column. Covers `answers` plus
# `draft` together — the ILR is ~8KB, but the draft carries the skills-radar
# assessments (one entry per KSB) and signature data URLs, so allow headroom.
MAX_ANSWERS_BYTES = 2 * 1024 * 1024


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


def _s(value):
    return "" if value is None else str(value).strip()


def _signature_state(answers):
    """Derive the flat signature/completion columns from the answers document.

    Kept server-side so the flags always agree with the stored document even if a
    client sends them inconsistently (or not at all).
    """
    learner = answers.get("learnerSignature") or {}
    provider = answers.get("providerSignature") or {}
    learner_signed = bool(learner.get("signatureUrl"))
    provider_signed = bool(provider.get("signatureUrl"))
    return {
        "learner_signed": learner_signed,
        "learner_signed_date": _s(learner.get("date")) or None,
        "provider_signed": provider_signed,
        "provider_signed_date": _s(provider.get("date")) or None,
        # "Completed" means the compliance artefact is finished: both parties signed.
        "completed": learner_signed and provider_signed,
    }


def _next_of_kin_state(answers):
    """The next of kin's own address as columns; cleared when it is the learner's."""
    kin = answers.get("nextOfKin") or {}
    own_address = kin.get("sameAddressAsLearner") is False
    return {
        "next_of_kin_postcode": (_s(kin.get("postcode")) or None) if own_address else None,
        "next_of_kin_address": (_s(kin.get("address")) or None) if own_address else None,
    }


def _payload(row):
    return {
        "answers": row.answers or {},
        "draft": row.wizard_draft or {},
        "meta": {
            "learnerKind": row.learner_kind,
            "learnerId": row.learner_id,
            "learnerName": _s(row.learner_name),
            "learnerSigned": row.learner_signed,
            "learnerSignedDate": _s(row.learner_signed_date),
            "providerSigned": row.provider_signed,
            "providerSignedDate": _s(row.provider_signed_date),
            "completed": row.completed,
            "updatedAt": row.updated_at.isoformat() if row.updated_at else "",
        },
    }


def _empty_payload(kind, learner_id, learner_name):
    """Shape returned when a learner has no saved ILR yet — a 200, not a 404.

    The wizard opens on a blank form for every learner, so "nothing saved" is a
    normal state, not an error the UI should have to special-case.
    """
    return {
        "answers": None,
        "draft": None,
        "meta": {
            "learnerKind": kind,
            "learnerId": learner_id,
            "learnerName": learner_name,
            "learnerSigned": False,
            "learnerSignedDate": "",
            "providerSigned": False,
            "providerSignedDate": "",
            "completed": False,
            "updatedAt": "",
        },
    }


def read_extended_ilr(kind, learner_id, learner_name):
    """The GET payload, as a plain dict.

    Split out from the view so the wizard-bootstrap endpoint can compose this
    with the learner's board in one response without re-implementing the
    fallbacks below or paying a second HTTP round-trip. Raises DatabaseError,
    which the caller is expected to turn into its own error response.
    """
    payload = _read_extended_ilr_row(kind, learner_id, learner_name)
    # The ILR Learner Details step lives in its own table, never in this row's
    # draft snapshot, so it is read from there and handed back alongside.
    details = read_ilr_details(kind, int(learner_id))
    if details is not None:
        payload["draft"] = {**(payload.get("draft") or {}), ILR_DETAILS_KEY: details}
    return payload


def _read_extended_ilr_row(kind, learner_id, learner_name):
    row = ExtendedIlr.objects.filter(learner_kind=kind, learner_id=learner_id).first()
    if row is None:
        # No ILR row, but the per-step tables may still hold a partly-filled
        # wizard (e.g. a learner who only completed Personal Details).
        payload = _empty_payload(kind, int(learner_id), learner_name)
        projected = read_projection(kind, int(learner_id))
        if projected:
            payload["draft"] = projected
        return payload

    payload = _payload(row)
    # Rows written before Wizard_draft existed have an empty draft; rebuild it
    # from the per-step tables so nothing looks lost in the wizard.
    if not payload["draft"]:
        payload["draft"] = read_projection(kind, int(learner_id))
    return payload


@enrolment_login_required
@csrf_exempt
def extended_ilr(request, kind, learner_id):
    model = KINDS.get(kind)
    if model is None:
        return _error(f"Unknown learner kind '{kind}'. Expected one of: {', '.join(sorted(KINDS))}.", 400)

    try:
        learner = model.objects.filter(pk=learner_id).first()
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)
    if learner is None:
        return _error("Learner not found.", 404)
    learner_name = _s(learner.username)

    if request.method == "GET":
        try:
            return JsonResponse(read_extended_ilr(kind, learner_id, learner_name))
        except DatabaseError as exc:
            return _error(f"Database error: {exc}", 502)

    if request.method in ("PUT", "PATCH", "POST"):
        if not request.body:
            return _error("Request body is required.", 400)
        if len(request.body) > MAX_ANSWERS_BYTES:
            return _error("Payload too large.", 413)
        try:
            body = json.loads(request.body)
        except ValueError:
            return _error("Request body must be valid JSON.", 400)
        if not isinstance(body, dict):
            return _error("Request body must be a JSON object.", 400)

        # `answers` is the ILR step and is optional, mirroring `draft` below: a
        # client that only wants to save the other steps omits it, and the
        # stored answers (and the signature/`completed` flags derived from them)
        # are left untouched rather than wiped. Only a full replacement is ever
        # applied — the ILR is a signed compliance record, so a *partial*
        # answers object must never land here and silently drop the rest. We
        # therefore no longer fall back to treating the whole body as answers:
        # that fallback meant an ILR-unaware caller sending {draft: ...} had its
        # entire body (draft and all) written into `answers`, corrupting the
        # record and clearing the signed flags.
        answers = body.get("answers")
        if answers is not None and not isinstance(answers, dict):
            return _error("'answers' must be a JSON object.", 400)

        # The wizard's other steps. Optional for the same reason: omitting the
        # key leaves any stored draft alone rather than wiping it.
        draft = body.get("draft")
        if draft is not None and not isinstance(draft, dict):
            return _error("'draft' must be a JSON object.", 400)

        if answers is None and draft is None:
            return _error("Provide 'answers' and/or 'draft' to save.", 400)

        defaults = {"learner_name": learner_name}
        if answers is not None:
            # Signature/`completed` flags are derived from the answers, so they
            # are only recomputed when answers are actually being replaced.
            defaults["answers"] = answers
            defaults.update(_signature_state(answers))
            defaults.update(_next_of_kin_state(answers))
        if draft is not None:
            # Everything but the ILR Learner Details step, which is a separate
            # record kept in its own table (project_draft below writes it there).
            defaults["wizard_draft"] = without_ilr_details(draft)
        try:
            with transaction.atomic(using="enrolment"):
                row, _ = ExtendedIlr.objects.update_or_create(
                    learner_kind=kind,
                    learner_id=learner_id,
                    defaults=defaults,
                )
                # Same transaction as the document, so the queryable per-step
                # tables can never disagree with the draft they came from.
                if draft is not None:
                    project_draft(kind, int(learner_id), draft)
                    # The wizard builder's custom fields, into their own columns.
                    # Conditions on Extended ILR questions read the stored answers.
                    project_custom_fields(kind, int(learner_id), draft, answers=row.answers)
        except DatabaseError as exc:
            return _error(f"Database error: {exc}", 502)

        return JsonResponse(_payload(row))

    return _error("Method not allowed.", 405)
