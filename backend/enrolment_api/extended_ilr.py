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

from django.core import signing
from django.db import DatabaseError, connections, transaction
from django.utils import timezone
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from learner_api.models import CommercialUser, EnrolmentUser

from .auth import enrolment_login_required
from .models import ExtendedIlr, WizardIlrLearnerDetails
from .ilr_finalisation import legacy_review_required, published_ilr_custom_keys, signed_snapshot, validate_finalisation
from .ilr_health import validate_ilr_additions
from .ilr_learner_record import learner_record
from .skills_radar.catalogue import CatalogueError
from .wizard_steps import (
    ILR_DETAILS_KEY,
    project_draft,
    read_ilr_details,
    read_projection,
    without_ilr_details,
)
from .learner_profile_fields import clean_profile, transport_profile, without_profile_fields
from .wizard_layout import LayoutError, project_custom_fields, read_version, record_layout_progress

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


def read_extended_ilr(kind, learner_id, learner_name, show_signed_snapshot=False, learner=None):
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
    if kind == 'apprenticeship' and show_signed_snapshot:
        snapshot = signed_snapshot(int(learner_id))
        payload['meta']['ilrFinalisedAt'] = snapshot.get('finalisedAt', '') if snapshot else ''
        payload['meta']['ilrLegacyReadOnly'] = bool(not snapshot and learner and legacy_review_required(learner, int(learner_id)))
        if snapshot:
            payload['draft'] = {**(payload.get('draft') or {}),
                                'personalDetails': snapshot['personalDetails'],
                                ILR_DETAILS_KEY: snapshot[ILR_DETAILS_KEY]}
            custom = dict((payload['draft'].get('custom') or {}))
            custom.update(snapshot.get('ilrCustom') or {})
            payload['draft']['custom'] = custom
    from .eligibility_prefill import add_defaults
    add_defaults(payload, kind, int(learner_id))
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
    elif payload['draft']:
        # A historical submitted wizard can have its signature only in the
        # per-step table. Later Extended ILR saves must not make that view
        # disappear merely because Wizard_draft is now non-empty.
        if kind == 'apprenticeship' and 'personalDetails' not in payload['draft']:
            personal = (read_projection(kind, int(learner_id)) or {}).get('personalDetails')
            if personal:
                payload['draft'] = {**payload['draft'], 'personalDetails': personal}
    if payload['draft'] and 'skillsRadar' not in payload['draft']:
        # Older partial draft writers may have left radar answers only in the
        # per-step tables. Recover them without replacing other saved sections.
        from .skills_radar.service import read_legacy_radar
        radar = read_legacy_radar(kind, int(learner_id))
        if radar is not None:
            payload['draft'] = {**payload['draft'], 'skillsRadar': radar}
    return payload


@enrolment_login_required
@csrf_exempt
def extended_ilr(request, kind, learner_id):
    model = KINDS.get(kind)
    if model is None:
        return _error(f"Unknown learner kind '{kind}'. Expected one of: {', '.join(sorted(KINDS))}.", 400)

    try:
        learner = model.objects.filter(pk=learner_id).first()
    except DatabaseError:
        return _error('The learner could not be loaded. Please try again.', 503)
    if learner is None:
        return _error("Learner not found.", 404)
    learner_name = _s(learner.username)

    if request.method == "GET":
        try:
            own = getattr(getattr(request, 'login_account', None), 'role', None) == 'learner'
            return JsonResponse(read_extended_ilr(kind, learner_id, learner_name, own, learner))
        except DatabaseError:
            return _error('Your answers could not be loaded. Please try again.', 503)

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

        layout_version = body.get("layoutVersion")
        if layout_version is not None and (not isinstance(layout_version, int) or isinstance(layout_version, bool) or layout_version < 1):
            return _error("'layoutVersion' must be a positive integer or null.", 400)
        layout_token = body.get("layoutToken")
        if layout_version is not None:
            try:
                token_data = signing.loads(layout_token, salt="enrolment.wizard-layout.learner-version")
            except (signing.BadSignature, TypeError):
                return _error("The wizard version reference is invalid. Reload the wizard before saving.", 409)
            if token_data != {"kind": kind, "learnerId": int(learner_id), "version": layout_version}:
                return _error("The wizard version reference does not match this learner. Reload before saving.", 409)
        checkpoint = body.get("skillsRadarCheckpoint", False)
        submitted = body.get("submitted", False)
        if not isinstance(checkpoint, bool) or not isinstance(submitted, bool):
            return _error("'skillsRadarCheckpoint' and 'submitted' must be boolean values.", 400)
        if submitted and _s(getattr(learner, "onboarding_status", "")).lower() not in {"submitted", "completed"}:
            return _error("The learner must be marked submitted before recording the submitted wizard version.", 409)

        if answers is None and draft is None and not (checkpoint or submitted):
            return _error("Provide 'answers' and/or 'draft' to save.", 400)

        if draft is not None and ILR_DETAILS_KEY in draft:
            try:
                validate_ilr_additions(draft[ILR_DETAILS_KEY])
            except ValueError as exc:
                return _error(str(exc), 400)

        account = getattr(request, 'login_account', None)
        learner_write = bool(account and account.role == 'learner' and kind == 'apprenticeship')
        finalise = body.get('finalizeIlr') is True
        if finalise and not learner_write:
            return _error('Only the apprenticeship learner can finalise their ILR.', 403)
        if finalise and (not draft or not isinstance(draft.get('personalDetails'), dict)
                         or not isinstance(draft.get(ILR_DETAILS_KEY), dict)):
            return _error('Complete the ILR before signing and continuing.', 400)

        if draft is not None and 'programmeModules' in draft:
            from programme_suitability.configuration import ConfigurationError
            from programme_suitability.modules_service import validate_choices
            try:
                draft['programmeModules'] = validate_choices(kind, int(learner_id), draft['programmeModules'])
            except ConfigurationError as exc:
                return _error(str(exc), 400)
            except DatabaseError:
                return _error('Could not verify module choices. Please retry.', 503)

        try:
            with transaction.atomic(using="enrolment"):
                layout_snapshot = None
                if layout_version is not None:
                    with connections["enrolment"].cursor() as cursor:
                        version_row = read_version(cursor, layout_version)
                    if version_row is None:
                        return _error("The wizard version used for these answers is unavailable. Reload the wizard before saving.", 409)
                    layout_snapshot = version_row[1]
                # Serialize *every* learner save with Sign + Next. An old tab
                # cannot win a race after the signed snapshot is committed.
                # Main's catalogue pinning also requires this lock for drafts.
                if learner_write or draft is not None:
                    learner = model.objects.select_for_update().get(pk=learner_id)
                signed = signed_snapshot(int(learner_id)) if learner_write else None
                legacy = bool(learner_write and not signed and legacy_review_required(learner, int(learner_id)))
                if signed:
                    for key in ('personalDetails', ILR_DETAILS_KEY):
                        if draft is not None and key in draft and draft[key] != signed[key]:
                            return _error('Your ILR has been signed and can no longer be changed.', 409)
                    frozen_custom = signed.get('ilrCustom') or {}
                    protected_custom_keys = set(frozen_custom) | published_ilr_custom_keys()
                    incoming_custom = (draft or {}).get('custom') or {}
                    if any(key in incoming_custom and (key not in frozen_custom or incoming_custom[key] != frozen_custom[key])
                           for key in protected_custom_keys):
                        return _error('Your ILR has been signed and can no longer be changed.', 409)
                    if finalise:
                        return JsonResponse(read_extended_ilr(kind, learner_id, learner_name, True))
                    # Later wizard steps stay editable. Do not rewrite signed
                    # sections (or staff's later corrections) from this learner
                    # request, even when it carries an unchanged stale copy.
                    draft = {key: value for key, value in draft.items()
                             if key not in ('personalDetails', ILR_DETAILS_KEY)} if draft is not None else None
                    if draft is not None and 'custom' in draft:
                        draft['custom'] = {key: value for key, value in (draft['custom'] or {}).items()
                                           if key not in protected_custom_keys}
                elif legacy:
                    if finalise or (draft is not None and any(key in draft for key in ('personalDetails', ILR_DETAILS_KEY))):
                        return _error('This historical ILR is read-only pending review.', 409)
                    protected_custom_keys = published_ilr_custom_keys()
                    if any(key in protected_custom_keys for key in ((draft or {}).get('custom') or {})):
                        return _error('This historical ILR is read-only pending review.', 409)
                elif finalise:
                    try:
                        validate_finalisation(draft, learner)
                    except ValueError as exc:
                        return _error(str(exc), 400)

                profile = {}
                if kind == 'apprenticeship' and isinstance((draft or {}).get('personalDetails'), dict) and not signed and not legacy:
                    personal = draft['personalDetails']
                    try:
                        profile = clean_profile(personal, learner)
                    except ValueError as exc:
                        return _error(str(exc), 400)
                    draft = {**draft, 'personalDetails': without_profile_fields(personal)}
                defaults = {"learner_name": learner_name}
                if answers is not None:
                    defaults["answers"] = answers
                    defaults.update(_signature_state(answers))
                    defaults.update(_next_of_kin_state(answers))
                if draft is not None:
                    stored_row = ExtendedIlr.objects.filter(learner_kind=kind, learner_id=learner_id).first()
                    previous = (stored_row.wizard_draft or {}) if stored_row else {}
                    stored = previous
                    if 'skillsRadar' in draft and 'skillsRadar' not in stored:
                        from .skills_radar.service import read_legacy_radar
                        radar = read_legacy_radar(kind, int(learner_id))
                        if radar is not None:
                            stored = {**stored, 'skillsRadar': radar}
                    from .skills_radar.service import prepare_answers
                    prepare_answers(kind, learner, draft, stored)
                    if signed or legacy:
                        merged = {**previous, **without_ilr_details(draft)}
                        if 'custom' in draft:
                            merged['custom'] = {**(previous.get('custom') or {}), **(draft['custom'] or {})}
                        defaults["wizard_draft"] = merged
                    else:
                        defaults["wizard_draft"] = without_ilr_details(draft)
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
                    project_custom_fields(kind, int(learner_id), draft, answers=row.answers, layout=layout_snapshot)
                if profile:
                    for attribute, value in profile.items():
                        setattr(learner, attribute, value)
                    learner.save(update_fields=list(profile))
                if finalise:
                    finalised_at = timezone.now()
                    custom_keys = published_ilr_custom_keys()
                    snapshot = {
                        'personalDetails': draft['personalDetails'],
                        ILR_DETAILS_KEY: draft[ILR_DETAILS_KEY],
                        'ilrCustom': {key: value for key, value in (draft.get('custom') or {}).items()
                                      if key in custom_keys},
                        'learnerRecord': learner_record(learner),
                        'finalisedAt': finalised_at.isoformat(),
                    }
                    updated = WizardIlrLearnerDetails.objects.filter(
                        learner_kind=kind, learner_id=learner_id,
                    ).update(ilr_finalised_at=finalised_at,
                             ilr_finalised_by=account.id,
                             ilr_signed_snapshot=snapshot)
                    if updated != 1:
                        raise DatabaseError('ILR details row missing during finalisation.')
                if checkpoint or submitted:
                    with connections["enrolment"].cursor() as cursor:
                        record_layout_progress(
                            cursor,
                            kind,
                            int(learner_id),
                            layout_version,
                            checkpoint=checkpoint,
                            submitted=submitted,
                        )
        except DatabaseError:
            return _error('Your answers could not be saved. Please try again.', 503)
        except LayoutError as exc:
            return _error(str(exc), 409)
        except CatalogueError as exc:
            return _error(str(exc), 409)

        response = _payload(row)
        if 'meta' in response and learner_write:
            response['meta']['ilrFinalisedAt'] = (snapshot['finalisedAt'] if finalise
                                                   else signed.get('finalisedAt', '') if signed else '')
            response['meta']['ilrLegacyReadOnly'] = legacy
        if 'draft' in response and draft is not None:
            stored = response['draft'] or {}
            if ILR_DETAILS_KEY in draft:
                stored = {**stored, ILR_DETAILS_KEY: draft[ILR_DETAILS_KEY]}
            if profile:
                stored = {**stored, 'personalDetails': {**(stored.get('personalDetails') or {}),
                                                       **transport_profile(profile)}}
            response['draft'] = stored
        return JsonResponse(response)

    return _error("Method not allowed.", 405)
