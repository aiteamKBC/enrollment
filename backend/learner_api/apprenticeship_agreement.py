"""The Apprenticeship Agreement — issue, sign and retrieve.

A statutory contract of service between the apprentice and their employer
(ASCLA 2009 / the 2017 Regulations). It states the standard, the dates the
apprenticeship runs, and the planned off-the-job training hours, and is signed
by those two parties only — the training provider does not sign it (note 6).

Everything on the form is derived from our own records rather than typed twice:
  * apprentice name, employer name and address -> the learner's record
  * start / end dates                          -> the learner's own dates (the
    group delivery window is no longer stored on curriculum.groups)
  * planned off-the-job hours                  -> the learning plan total, the
    sum of total_otjh across the modules on their plan
  * duration of practical period               -> weeks between its start and end

Those values are SNAPSHOT onto the row when the agreement is issued. Once a
party signs, editing the learning plan or moving the group's dates must not
rewrite what they put their name to — so the row is the record, not a live view.

Fields with no source are left blank rather than guessed: an invented standard
version on a statutory document is worse than a line an officer completes.

    GET    /learner_api/apprenticeship-agreement/<pk>/          particulars + agreement
    POST   /learner_api/apprenticeship-agreement/<pk>/issue/    create/reissue
    POST   /learner_api/apprenticeship-agreement/<pk>/sign/     one party signs
"""
import json
import logging
from datetime import date, datetime
from decimal import Decimal

from django.db import DatabaseError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from enrolment_api.auth import employer_signing_party_error, enrolment_login_required

from .learning_plan import _saved_modules, learner_group_module_ids, learner_programme_modules
from .learner_progression import advance_learner
from .mappers import _s
from .models import ApprenticeshipAgreement, EnrolmentUser

logger = logging.getLogger(__name__)

SIGNING_PARTIES = ("apprentice", "employer")
MAX_SIGNATURE_CHARS = 400_000


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


def _iso(value):
    return value.isoformat() if value else ""


def _float(value):
    return float(value) if value is not None else None


# ---------------------------------------------------------------------------
# Deriving the particulars
# ---------------------------------------------------------------------------
def _employer_address(learner):
    """The place of work block: the learner's own value, else their employer's."""
    direct = _s(getattr(learner, "employer_address", ""))
    if direct:
        return direct

    employer_id = getattr(learner, "employer_id", None)
    if not employer_id:
        return ""
    try:
        from .models import Employer

        employer = Employer.objects.filter(pk=employer_id).first()
    except DatabaseError:
        logger.exception("_employer_address: employer lookup failed")
        return ""
    if employer is None:
        return ""

    parts = [
        _s(getattr(employer, "address_1", "")),
        _s(getattr(employer, "address_2", "")),
        _s(getattr(employer, "town_city", "")),
        _s(getattr(employer, "county", "")),
        _s(getattr(employer, "post_code", "")),
    ]
    return "\n".join(p for p in parts if p)


def _plan_modules(learner):
    """The learner's learning-plan modules, with hours. Group preset if unsaved."""
    catalogue = {m["moduleId"]: m for m in learner_programme_modules(learner)}

    saved = _saved_modules(learner)
    if saved:
        module_ids = [_s(m.get("moduleId")) for m in saved]
    else:
        module_ids = learner_group_module_ids(learner)
    return [catalogue[i] for i in module_ids if i in catalogue]


def _to_date(value):
    """A `date` from whatever a date column or the learner's text field holds.

    The group table stores real dates, but the learner's own Start_date/End_date
    are TextField (strings) — so callers of _group_dates once got a `date` on the
    group path and a `str` on the fallback, and the string blew up the moment
    anything subtracted the two or called .isoformat(). Everything is normalised
    to `date` here so downstream code has one type to reason about. Unparseable
    or empty -> None, which every consumer already treats as "unknown".
    """
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    # ISO first (what the DB and most of our writers use), then the UK format an
    # officer might have typed into the free-text field.
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:10] if fmt == "%Y-%m-%d" else text, fmt).date()
        except ValueError:
            continue
    return None


def _group_dates(learner):
    """Recorded learner dates, with read-only fallback to their assigned cohort.

    Imported learners can have a complete placement without copied date fields.
    An explicit learner date always wins (it may differ from the cohort date).
    A read-only request may stash its result as ``_resolved_group_dates``.
    """
    resolved = getattr(learner, "__dict__", {}).get("_resolved_group_dates")
    if resolved is not None:
        return resolved
    start = _to_date(getattr(learner, "start_date", None))
    end = _to_date(getattr(learner, "end_date", None))
    programme = _s(getattr(learner, "programme", ""))
    cohort = _s(getattr(learner, "cohort", ""))
    if (start is None or end is None) and programme and cohort:
        from .active_users import cohort_dates
        from .placement_links import placement_ids

        # By the cohort's id where the learner's profile holds one, so a renamed
        # cohort still supplies the date the progression and access gate read.
        try:
            cohort_id = placement_ids(learner)["cohort_id"]
        except DatabaseError:
            logger.warning("Could not read the placement ids for learner %s", getattr(learner, "pk", "?"))
            cohort_id = ""
        cohort_start, cohort_end = cohort_dates(programme, cohort, cohort_id)
        if (start is None and cohort_start) or (end is None and cohort_end):
            origin = "cohort" if start is None and end is None else "learner/cohort"
            return start or cohort_start, end or cohort_end, origin
    return start, end, "learner"


def _weeks_between(start, end):
    """Duration of the practical period in weeks, to 1dp. None when unknown."""
    start, end = _to_date(start), _to_date(end)
    if not start or not end:
        return None
    days = (end - start).days
    if days < 0:
        return None
    return round(Decimal(days) / Decimal(7), 1)


def derive_particulars(learner):
    """Everything the agreement states, derived from the learner's records."""
    start, end, dates_from = _group_dates(learner)
    modules = _plan_modules(learner)
    hours = round(sum(float(m.get("hours") or 0) for m in modules), 2)

    return {
        "apprenticeName": _s(learner.username),
        "employerName": _s(learner.employer),
        "employerAddress": _employer_address(learner),
        # We hold no level/version for the standard, so the programme name is
        # what we can honestly state; an officer completes the rest.
        "standard": _s(learner.programme),
        "startDate": start,
        "endDate": end,
        # One delivery window seeds both periods.
        "practicalStart": start,
        "practicalEnd": end,
        "durationWeeks": _weeks_between(start, end),
        "plannedOtjh": hours,
        "planModules": modules,
        "datesFrom": dates_from,
    }


# ---------------------------------------------------------------------------
# Serialising
# ---------------------------------------------------------------------------
def _party_json(signature, name, at):
    return {
        "signed": bool(_s(signature)),
        "name": _s(name),
        "signedAt": at.isoformat() if at else None,
    }


def _agreement_json(agreement):
    if agreement is None:
        return None
    return {
        "id": str(agreement.id),
        "status": _s(agreement.status),
        "fullySigned": bool(agreement.fully_signed),
        "createdAt": agreement.created_at.isoformat() if agreement.created_at else None,
        "updatedAt": agreement.updated_at.isoformat() if agreement.updated_at else None,
        # The frozen particulars — what the parties actually signed.
        "particulars": {
            "apprenticeName": _s(agreement.apprentice_name),
            "employerName": _s(agreement.employer_name),
            "employerAddress": _s(agreement.employer_address),
            "standard": _s(agreement.standard),
            "startDate": _iso(agreement.start_date),
            "endDate": _iso(agreement.end_date),
            "practicalStartDate": _iso(agreement.practical_start),
            "practicalEndDate": _iso(agreement.practical_end),
            "durationWeeks": _float(agreement.duration_weeks),
            "plannedOtjHours": _float(agreement.planned_otjh),
        },
        "planModules": agreement.plan_modules or [],
        "signatures": {
            "apprentice": _party_json(
                agreement.apprentice_signature,
                agreement.apprentice_signed_name,
                agreement.apprentice_signed_at,
            ),
            "employer": _party_json(
                agreement.employer_signature,
                agreement.employer_signed_name,
                agreement.employer_signed_at,
            ),
        },
        # The marks themselves, so the PDF can embed them.
        "marks": {
            "apprentice": _s(agreement.apprentice_signature),
            "employer": _s(agreement.employer_signature),
        },
        "document": {
            "path": _s(agreement.doc_path),
            "sizeBytes": agreement.size_bytes,
            "stored": bool(_s(agreement.blob_name)),
        },
    }


def _active_agreement(learner_kind, learner_id, lock=False):
    """The learner's active agreement. `lock=True` takes a row lock for the
    caller's transaction so a concurrent sign/issue can't read-modify-write over
    it — only valid inside `transaction.atomic(using="enrolment")`."""
    qs = ApprenticeshipAgreement.objects.filter(
        learner_kind=learner_kind,
        learner_id=learner_id,
        status=ApprenticeshipAgreement.STATUS_ACTIVE,
    )
    if lock:
        qs = qs.select_for_update()
    return qs.first()


def _learner_kind(learner):
    return _s(getattr(learner, "learner_type", "")) or "apprenticeship"


def _load_learner(pk):
    return EnrolmentUser.all_learners.filter(pk=pk).first()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@enrolment_login_required
@csrf_exempt
def apprenticeship_agreement(request, pk):
    """The learner's agreement, plus the particulars a new one would carry.

    `particulars` is always the live derivation, so staff can see what a reissue
    would say; `agreement.particulars` is the frozen copy that was signed.
    """
    if request.method != "GET":
        return _error("Method not allowed.", 405)

    try:
        learner = _load_learner(pk)
        if learner is None:
            return _error("Learner not found.", 404)

        derived = derive_particulars(learner)
        agreement = _active_agreement(_learner_kind(learner), learner.pk)
    except DatabaseError as exc:
        logger.exception("apprenticeship_agreement: build failed")
        return _error(f"Database error: {exc}", 502)

    return JsonResponse({
        "learner": {
            "id": str(learner.id),
            "name": _s(learner.username),
            "programmeStatus": _s(learner.programme_status),
            "group": _s(learner.group),
            "cohort": _s(learner.cohort),
        },
        # What a freshly issued agreement would state, from live data.
        "particulars": {
            "apprenticeName": derived["apprenticeName"],
            "employerName": derived["employerName"],
            "employerAddress": derived["employerAddress"],
            "standard": derived["standard"],
            "startDate": _iso(derived["startDate"]),
            "endDate": _iso(derived["endDate"]),
            "practicalStartDate": _iso(derived["practicalStart"]),
            "practicalEndDate": _iso(derived["practicalEnd"]),
            "durationWeeks": _float(derived["durationWeeks"]),
            "plannedOtjHours": derived["plannedOtjh"],
        },
        "planModules": derived["planModules"],
        "meta": {
            "datesFrom": derived["datesFrom"],
            "moduleCount": len(derived["planModules"]),
        },
        "agreement": _agreement_json(agreement),
    })


@enrolment_login_required
@csrf_exempt
def issue_agreement(request, pk):
    """Issue the agreement, snapshotting the particulars onto a new row.

        POST /learner_api/apprenticeship-agreement/<pk>/issue/

    An existing active agreement is superseded rather than edited, so the
    previously signed version stays on record. Signatures are NOT carried over:
    a reissue may state different dates or hours, and a signature only ever
    attests to the particulars it was given against.
    """
    if request.method != "POST":
        return _error("Method not allowed.", 405)

    try:
        learner = _load_learner(pk)
        if learner is None:
            return _error("Learner not found.", 404)

        kind = _learner_kind(learner)
        derived = derive_particulars(learner)

        # `using="enrolment"` is not optional: these models are routed to the
        # `enrolment` alias, so a bare atomic() would open a transaction on
        # `default` and give the supersede+create below no rollback protection —
        # a failed create would leave the learner with zero active agreements.
        with transaction.atomic(using="enrolment"):
            existing = _active_agreement(kind, learner.pk, lock=True)
            if existing is not None:
                existing.status = ApprenticeshipAgreement.STATUS_SUPERSEDED
                existing.save(update_fields=["status", "updated_at"])

            agreement = ApprenticeshipAgreement.objects.create(
                learner_kind=kind,
                learner_id=learner.pk,
                apprentice_name=derived["apprenticeName"],
                employer_name=derived["employerName"],
                employer_address=derived["employerAddress"],
                standard=derived["standard"],
                start_date=derived["startDate"],
                end_date=derived["endDate"],
                practical_start=derived["practicalStart"],
                practical_end=derived["practicalEnd"],
                duration_weeks=derived["durationWeeks"],
                planned_otjh=derived["plannedOtjh"],
                plan_modules=derived["planModules"],
            )
    except DatabaseError as exc:
        logger.exception("issue_agreement: failed")
        return _error(f"Database error: {exc}", 502)

    return JsonResponse({"agreement": _agreement_json(agreement)}, status=201)


@enrolment_login_required
@csrf_exempt
def sign_agreement(request, pk):
    """Record one party's signature.

        POST /learner_api/apprenticeship-agreement/<pk>/sign/
        {"party": "apprentice" | "employer", "name": "...", "signature": "data:image/png;..."}

    Posting an empty signature withdraws that party's sign-off, so a mistaken
    signature can be taken back. Fully_signed is recomputed either way, so
    withdrawing correctly un-completes the agreement.
    """
    if request.method != "POST":
        return _error("Method not allowed.", 405)

    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        return _error("Request body must be valid JSON.", 400)
    if not isinstance(payload, dict):
        return _error("Request body must be a JSON object.", 400)

    party = _s(payload.get("party")).lower()
    if party not in SIGNING_PARTIES:
        allowed = "', '".join(SIGNING_PARTIES)
        return _error(f"party must be one of '{allowed}'.", 400)
    party_error = employer_signing_party_error(request, party)
    if party_error is not None:
        return party_error

    signature = _s(payload.get("signature"))
    name = _s(payload.get("name"))
    if signature:
        if len(signature) > MAX_SIGNATURE_CHARS:
            return _error("That signature image is too large.", 400)
        if not signature.startswith("data:image/"):
            return _error("signature must be a PNG data URL.", 400)
        if not name:
            return _error("name is required when signing.", 400)

    try:
        learner = _load_learner(pk)
        if learner is None:
            return _error("Learner not found.", 404)

        # Lock the row for the whole read-modify-write: the apprentice and the
        # employer can sign concurrently, and without the lock the second full
        # save() would clobber the first party's signature columns and
        # recalculate `fully_signed` from a stale copy.
        with transaction.atomic(using="enrolment"):
            agreement = _active_agreement(_learner_kind(learner), learner.pk, lock=True)
            if agreement is None:
                return _error("No agreement has been issued for this learner yet.", 404)

            now = timezone.now() if signature else None
            if party == "apprentice":
                agreement.apprentice_signature = signature
                agreement.apprentice_signed_name = name if signature else ""
                agreement.apprentice_signed_at = now
            else:
                agreement.employer_signature = signature
                agreement.employer_signed_name = name if signature else ""
                agreement.employer_signed_at = now

            agreement.recalculate_signed()
            agreement.save()
        promoted = advance_learner(learner)
    except DatabaseError as exc:
        logger.exception("sign_agreement: failed")
        return _error(f"Database error: {exc}", 502)

    return JsonResponse({
        "agreement": _agreement_json(agreement),
        "programmeStatus": _s(learner.programme_status),
        "programmeStatusChangedTo": promoted,
    })
