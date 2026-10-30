"""The enrolment review form — read and save one booked review's questionnaire.

    GET   /learner_api/reviews/<kind>/<pk>/<event_key>/
    PATCH /learner_api/reviews/<kind>/<pk>/<event_key>/

The form itself (ILR, Extended ILR, Functional Skills, FS & job role discussion,
comments, programme status) is stored as a jsonb document on the
enrolment."Enrolment_Reviews" row — see EnrolmentReview.form_answers for why a
document rather than columns.

Answers are saved section by section (each panel has its own Save button), so
PATCH merges the posted sections into the stored document instead of replacing
it: two people with the form open cannot blank each other's untouched sections.

Learner Information is *derived*, never stored here — it mirrors the learner
record, so a stale copy would be a compliance problem.
"""
import json
import logging

from django.db import DatabaseError, IntegrityError
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from coach_api.models import CoachCalendarEvent
from login.permissions import _auth_gate_enabled, learner_self_or_staff, staff_only
from login.sessions import authenticate_request

from .calendar import ONBOARDING_REVIEW_LABELS, ONBOARDING_REVIEW_TYPES, _case_owner_record
from .constants import PROGRAMME_STATUS_CHOICES
from .learner_detail import SOURCE_MODELS
from .mappers import _s
from .models import EnrolmentReview
from .review_tables import sync_review_detail

logger = logging.getLogger(__name__)

# The panels each review renders, in order. Kept server-side so the completion
# summary and the frontend cannot drift apart, and so a section name posted for
# the wrong review is rejected rather than silently stored.
SECTIONS_BY_REVIEW = {
    "eligibility-review": (
        "ilr",
        "extendedIlr",
        "functionalSkills",
        "fsJobRoleDiscussion",
        "comments",
        # "programmeStatus" (the Programme Status Selector) was removed from this
        # review: the status is set from the learner's board. Answers already
        # saved under it stay in Form_answers and its detail-table column.
    ),
    "workspace": (
        # "RPL And Experience"
        "priorLearning",
        "rplExperience",
        "plr",
        "skillsRadar",
        "comments",
    ),
    "training-plan": (
        # "Workplace Health & Safety Declaration"
        "healthSafetyVetting",
    ),
    "uln-privacy": (
        # "ULN Privacy Notice & Learner Acknowledgement": the learner reads the
        # ULN privacy notice, then confirms the acknowledgement.
        "ulnPrivacyNotice",
        "learnerAcknowledgement",
    ),
}

# Every section name the API accepts, across all reviews.
ALL_SECTIONS = tuple(dict.fromkeys(s for group in SECTIONS_BY_REVIEW.values() for s in group))


def sections_for(review_type):
    """Panels for one review, falling back to the eligibility set for old rows."""
    return SECTIONS_BY_REVIEW.get(
        _s(review_type), SECTIONS_BY_REVIEW["eligibility-review"]
    )

# Programme Status dropdown. Mirrors the values the rest of the console uses —
# see PROGRAMME_STATUS_CHOICES in constants.py.
PROGRAMME_STATUS_OPTIONS = tuple(PROGRAMME_STATUS_CHOICES)

# "Highest level qualification you wish to report in the ILR" on the PLR panel.
PRIOR_ATTAINMENT_OPTIONS = (
    "99 - Not known",
    "1 - Entry level",
    "2 - Level 1",
    "3 - Level 2",
    "4 - Level 3",
    "5 - Level 4",
    "6 - Level 5",
    "7 - Level 6",
    "8 - Level 7 and above",
    "9 - No qualifications",
)

# Per-subject calculated attainment on the PLR panel.
PRIOR_ATTAINMENT_SUBJECT_LEVELS = ("None", "Level 1", "Level 2")


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


def _iso(value):
    return value.isoformat() if value else None


def _learner_information(learner):
    """The read-only Learner Information panel, derived from the learner record."""
    return {
        "name": _s(getattr(learner, "username", "")),
        "programmeName": _s(getattr(learner, "programme", "")),
        "programmeStartDate": _s(getattr(learner, "learner_start_date", "")) or "--",
        "plannedEndDate": _s(getattr(learner, "learner_end_date", "")) or "--",
        "programmeStatus": _s(getattr(learner, "programme_status", "")),
        "employer": _s(getattr(learner, "employer", "")),
        "manager": _s(getattr(learner, "line_manager", "")),
        "mentor": _s(getattr(learner, "mentor", "")),
    }


def _skills_radar_context(review, learner):
    """The learner's Skills Radar self-assessment, for the RPL review's panel.

    Read live from the wizard's own tables so the review shows the assessment the
    learner actually completed during onboarding, rather than a copy that could
    drift. Read-only here — the Skills Radar is the learner's own record and staff
    editing it would falsify what they are reviewing.

    KSB titles/themes come from the programme's authored profile
    (curriculum.ksb_profiles), the same source the wizard step uses.
    """
    if "skillsRadar" not in sections_for(review.review_type):
        return {}

    from enrolment_api.models import WizardKsbAssessment, WizardSkillsRadar

    try:
        radar = WizardSkillsRadar.objects.filter(learner_id=review.learner_id).first()
        rows = list(WizardKsbAssessment.objects.filter(learner_id=review.learner_id))
    except DatabaseError:
        logger.exception("_skills_radar_context: lookup failed")
        return {"skillsRadar": {"standardId": "", "assessments": [], "ksbs": []}}

    # Resolve the KSB list each answer was given against, so it is shown with
    # its title and theme instead of a bare id. That is the source recorded on
    # the learner's own Skills Radar (a profile, even one archived since, or a
    # standard); the programme's current source is only the fallback -- a
    # programme that later moved to a standard must not orphan signed answers.
    ksbs, standard = [], None
    programme = _s(getattr(learner, "programme", ""))
    answered_against = _s(getattr(radar, "standard_id", ""))
    try:
        from .curriculum import ksb_profile_for_programme, ksb_profile_for_source

        if answered_against:
            standard, ksbs = ksb_profile_for_source(answered_against, programme)
        if not ksbs and programme:
            standard, ksbs = ksb_profile_for_programme(programme)
    except DatabaseError:
        # The panel still lists the answers without titles, so this is not
        # worth failing the whole form over.
        logger.exception("_skills_radar_context: KSB profile lookup failed")

    answers = {
        _s(row.ksb_id): {
            "level": _s(row.level),
            "score": row.score,
            "note": _s(row.note),
        }
        for row in rows
    }

    # Ordered by the authored profile so the review reads in the same order the
    # learner answered. Answers with no matching KSB (profile edited since) are
    # appended rather than dropped — this is a compliance record.
    items = []
    for ksb in ksbs:
        answer = answers.pop(ksb["id"], None)
        items.append({
            "ksbId": ksb["id"],
            "theme": ksb.get("theme", ""),
            "kind": ksb.get("kind", ""),
            "codes": ksb.get("codes", []),
            "title": ksb.get("title", ""),
            "level": (answer or {}).get("level", ""),
            "score": (answer or {}).get("score"),
            "note": (answer or {}).get("note", ""),
        })
    for ksb_id, answer in answers.items():
        items.append({
            "ksbId": ksb_id, "theme": "", "kind": "", "codes": [], "title": "",
            **answer,
        })

    return {
        "skillsRadar": {
            "standardId": _s(getattr(radar, "standard_id", "")),
            "standardLabel": (standard or {}).get("label", "") if standard else "",
            "items": items,
            "answered": sum(1 for i in items if i["level"]),
            "total": len(items),
        }
    }


def _plr_context(review, learner):
    """ULN + existing PLR records for the RPL review's PLR panel.

    Only the RPL review shows this panel, so nothing is queried for the others.
    Read from the wizard's own PLR tables rather than copied into the review, so
    the panel cannot show a stale Personal Learner Record.
    """
    if "plr" not in sections_for(review.review_type):
        return {}

    from enrolment_api.models import WizardPlr, WizardPlrRecord

    try:
        plr = WizardPlr.objects.filter(learner_id=review.learner_id).first()
        records = list(
            WizardPlrRecord.objects.filter(learner_id=review.learner_id).order_by("id")
        )
    except DatabaseError:
        logger.exception("_plr_context: PLR lookup failed")
        return {"uln": "", "plrRecords": []}

    return {
        "uln": _s(getattr(plr, "uln", "")),
        "plrRecords": [
            {
                "placeOfStudy": _s(row.place_of_study),
                "qualificationType": _s(row.qualification_type),
                "subject": _s(row.subject),
                "level": _s(row.level),
                "awardDate": _iso(row.award_date),
                "credits": row.credits,
                "grade": _s(row.grade),
                "recordType": _s(row.record_type),
            }
            for row in records
        ],
    }


# Review types an employer is asked to sign, when the row itself doesn't say.
# Every onboarding review: the employer is a party to the whole onboarding record,
# not only the parts that name them. Per-row `Employer_signature_required` still
# overrides this, so a specific review can be opted out without changing code.
EMPLOYER_SIGNED_REVIEWS = ("training-plan", "eligibility-review", "workspace", "uln-privacy")


def employer_signature_required(review):
    """Whether this review wants an employer signature.

    The column wins when set, so a specific review can be opted in or out;
    otherwise it falls back to the review type. That keeps the decision data-driven
    without needing a value backfilled onto every existing row.
    """
    explicit = review.employer_signature_required
    if explicit is not None:
        return bool(explicit)
    return _s(review.review_type) in EMPLOYER_SIGNED_REVIEWS


def _signatures(review, *, include_saved=True):
    """Sign-off state for both parties.

    `include_saved` is off for the documents list, which shows only whether each
    side has signed — fetching the saved signature there would be one extra query
    per row for an image the list never renders.
    """
    return {
        "learner": {
            "signature": _s(review.learner_signature),
            "name": _s(review.learner_signed_name),
            "signedAt": _iso(review.learner_signed_at),
            "signed": bool(_s(review.learner_signature)),
        },
        "admin": {
            "signature": _s(review.admin_signature),
            "name": _s(review.admin_signed_name),
            "signedAt": _iso(review.admin_signed_at),
            "signed": bool(_s(review.admin_signature)),
        },
        "employer": {
            "signature": _s(review.employer_signature),
            "name": _s(review.employer_signed_name),
            "signedAt": _iso(review.employer_signed_at),
            "signed": bool(_s(review.employer_signature)),
            # Whether this review wants an employer sign-off at all — the
            # employer's "needs signing" list is built from this.
            "required": employer_signature_required(review),
        },
        # A review is only signable once its form is finished, so an unfinished
        # review cannot be signed off by any party.
        "signable": bool(review.form_completed),
    }


def _serialize_form(review, learner, event):
    answers = review.form_answers if isinstance(review.form_answers, dict) else {}
    status = review.section_status if isinstance(review.section_status, dict) else {}
    sections = sections_for(review.review_type)
    return {
        "eventKey": review.event_key,
        "reviewType": review.review_type,
        "reviewLabel": review.review_label,
        "scheduledDate": _iso(review.scheduled_date),
        "scheduledTime": review.scheduled_time.strftime("%H:%M") if review.scheduled_time else None,
        "reviewedBy": _s(review.reviewed_by) or _s(review.coach_name),
        "learnerInformation": _learner_information(learner),
        "answers": answers,
        "sectionStatus": {name: bool(status.get(name)) for name in sections},
        "sections": list(sections),
        "programmeStatusOptions": list(PROGRAMME_STATUS_OPTIONS),
        "priorAttainmentOptions": list(PRIOR_ATTAINMENT_OPTIONS),
        "priorAttainmentSubjectLevels": list(PRIOR_ATTAINMENT_SUBJECT_LEVELS),
        # PLR panel: the ULN and qualification records the learner already has, so
        # the review shows the real Personal Learner Record rather than a blank
        # table. Read-only here -- the wizard's PLR step owns them.
        **_plr_context(review, learner),
        # The learner's Skills Radar self-assessment (RPL review only).
        **_skills_radar_context(review, learner),
        "completed": bool(review.form_completed),
        "completedAt": _iso(review.form_completed_at),
        "startedAt": _iso(review.started_at),
        "meetingLink": _s(review.meeting_link),
        "status": review.status,
        "signatures": _signatures(review),
    }


def _lookup(kind, pk, event_key):
    """(learner, review, calendar_event) or a JsonResponse error."""
    model = SOURCE_MODELS.get(kind)
    if model is None:
        return None, None, None, _error(
            f"Unknown kind: {kind!r}. Expected 'commercial' or 'apprenticeship'.", 404
        )

    learner = model.all_learners.filter(pk=pk).first()
    if learner is None:
        return None, None, None, _error("Learner not found.", 404)

    # Scoped to this learner so one learner's event key cannot open another's form.
    review = EnrolmentReview.objects.filter(event_key=event_key, learner_id=pk).first()
    if review is None:
        return None, None, None, _error("Review not found.", 404)

    event = CoachCalendarEvent.objects.filter(event_key=event_key, learner_id=pk).first()
    return learner, review, event, None


@csrf_exempt
# Completing a review sets Form_completed and moves the status to Completed,
# which is a claim about the learner's own onboarding. Only the learner or the
# staff conducting the review may write it; a reader (another learner, an
# employer) is refused. Reads stay open — see learner_self_or_staff.
@learner_self_or_staff(kwarg="pk")
def enrolment_review_form(request, kind, pk, event_key):
    if request.method not in ("GET", "PATCH", "POST"):
        return _error("Method not allowed.", 405)

    try:
        learner, review, event, failure = _lookup(kind, pk, event_key)
    except DatabaseError as exc:
        logger.exception("enrolment_review_form: lookup failed")
        return _error(f"Database error: {exc}", 502)
    if failure is not None:
        return failure

    if request.method == "GET":
        # Opening the form for the first time records when the review started, so
        # "issued (or booked) but never opened" is distinguishable from "in progress".
        if review.started_at is None and review.status in (EnrolmentReview.STATUS_BOOKED, EnrolmentReview.STATUS_ISSUED):
            try:
                review.started_at = timezone.now()
                review.save(update_fields=["started_at", "updated_at"])
            except DatabaseError:
                # Not worth failing the read over.
                logger.exception("enrolment_review_form: could not stamp started_at")
        return JsonResponse(_serialize_form(review, learner, event))

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return _error("Invalid JSON body.", 400)

    posted_answers = payload.get("answers")
    if posted_answers is not None and not isinstance(posted_answers, dict):
        return _error("answers must be an object keyed by section.", 400)

    stored = review.form_answers if isinstance(review.form_answers, dict) else {}
    status = review.section_status if isinstance(review.section_status, dict) else {}

    # Merge per section rather than replacing the whole document: each panel saves
    # on its own, and an unrelated open tab must not blank sections it never had.
    allowed = set(sections_for(review.review_type))
    # `is not None`, not truthiness: a section whose only input is optional posts
    # {} when left blank, and that still counts as saving the panel.
    if posted_answers is not None:
        unknown = set(posted_answers) - allowed
        if unknown:
            return _error(
                f"Section(s) not part of this review: {', '.join(sorted(unknown))}.", 400
            )
        merged = dict(stored)
        for section, value in posted_answers.items():
            merged[section] = value
            # Saving a section marks it complete unless the caller says otherwise.
            status[section] = True
        stored = merged

    posted_status = payload.get("sectionStatus")
    if isinstance(posted_status, dict):
        for section, done in posted_status.items():
            if section in allowed:
                status[section] = bool(done)

    reviewed_by = _s(payload.get("reviewedBy"))
    finish = bool(payload.get("finish"))

    try:
        review.form_answers = stored
        review.section_status = status
        if reviewed_by:
            review.reviewed_by = reviewed_by
        if finish:
            review.form_completed = True
            review.form_completed_at = timezone.now()
            review.status = EnrolmentReview.STATUS_COMPLETED
        review.save()
    except DatabaseError as exc:
        logger.exception("enrolment_review_form: save failed")
        return _error(f"Database error: {exc}", 502)

    # Project into the per-review-type table. Best-effort: Form_answers above is
    # the source of truth and is already saved.
    sync_review_detail(review)

    return JsonResponse(_serialize_form(review, learner, event))


# A drawn signature is a PNG data URL. Capped so a huge upload cannot be stored
# in a text column that is later embedded in a PDF.
MAX_SIGNATURE_CHARS = 400_000
SIGNATURE_PARTIES = ("learner", "admin", "employer")

#: Which account roles may legitimately sign as each party. A caller can only
#: attach the signature of a party they actually are.
_PARTY_ROLES = {
    "learner": frozenset({"learner"}),
    "admin": frozenset({"admin", "staff"}),
    "employer": frozenset({"employer"}),
}


def _sign_authorization_error(request, pk, learner, party):
    """Authorise a review signature before any signature is written, or None.

    Unlike the rest of this module, signing is not a plain decorator: the caller
    must clear *two* independent checks, and both fail closed.

    Relationship — may this caller act on *this* learner's review at all? A
    learner may reach only their own (id mismatch -> 404, so another learner's
    review is not confirmed to exist); staff and admin may reach any; an employer
    only one of their own learners (``learner.employer_id`` must be the
    employer's own id, mismatch -> 404); anyone else is refused.

    Party — may this caller sign *as* the party they named? A learner signs only
    as ``learner``, staff/admin only as ``admin``, an employer only as
    ``employer`` (see ``_PARTY_ROLES``). Signing as a party you are not is 403.

    Honours ``LEARNER_API_REQUIRE_AUTH=0`` like every other login gate, so local
    development toggles them all together.
    """
    if not _auth_gate_enabled():
        authenticate_request(request)
        return None

    account = authenticate_request(request)
    if account is None:
        return _error("Authentication required.", 401)

    # Relationship gate. 404 (not 403) on a learner/employer reaching outside
    # their own records, matching login.permissions — a 403 would confirm the
    # review exists.
    if account.role == "learner":
        if account.subject_id != pk:
            return JsonResponse({"error": "Not found."}, status=404)
    elif account.role in ("admin", "staff"):
        pass
    elif account.role == "employer":
        if getattr(learner, "employer_id", None) != account.subject_id:
            return JsonResponse({"error": "Not found."}, status=404)
    else:
        return _error("You do not have permission to sign this review.", 403)

    # Party gate.
    if account.role not in _PARTY_ROLES[party]:
        return _error(f"You cannot sign this review as '{party}'.", 403)

    return None


def record_signature(review, party, signature, name):
    """Save (or, with an empty signature, clear) one party's sign-off.

    The one place a review signature is written, so the learner's page, the
    board and the coach's case file all behave alike. Returns the new programme
    status when this signature finished onboarding, else None. Raises
    DatabaseError when the save fails; the caller has already authorised it.
    """
    now = timezone.now() if signature else None
    if party == "learner":
        review.learner_signature = signature
        review.learner_signed_name = name if signature else ""
        review.learner_signed_at = now
    elif party == "employer":
        review.employer_signature = signature
        review.employer_signed_name = name if signature else ""
        review.employer_signed_at = now
    else:
        review.admin_signature = signature
        review.admin_signed_name = name if signature else ""
        review.admin_signed_at = now
    review.save()

    # Signing the last outstanding review finishes onboarding, which moves the
    # learner into Delivery. Deliberately after the signature is committed and
    # non-fatal: the sign-off is the user's action and must stand on its own.
    from .learning_plan import promote_to_delivery_if_ready

    return promote_to_delivery_if_ready(review) if signature else None


def serialize_review_form(review, learner, event):
    """The review document payload, for callers outside this module."""
    return _serialize_form(review, learner, event)


def lookup_review(kind, pk, event_key):
    """(learner, review, calendar_event, error) for one learner's review."""
    return _lookup(kind, pk, event_key)


@csrf_exempt
def enrolment_review_sign(request, kind, pk, event_key):
    """Sign a completed review, as the learner or as staff.

        POST /learner_api/reviews/<kind>/<pk>/<event_key>/sign/
        {"party": "learner" | "admin", "name": "...", "signature": "data:image/png;base64,..."}

    Only a completed review can be signed -- signing an unfinished questionnaire
    would attest to answers that can still change. Passing an empty signature
    clears that party's sign-off, so a mistaken signature can be withdrawn.
    """
    if request.method != "POST":
        return _error("Method not allowed.", 405)

    try:
        learner, review, event, failure = _lookup(kind, pk, event_key)
    except DatabaseError as exc:
        logger.exception("enrolment_review_sign: lookup failed")
        return _error(f"Database error: {exc}", 502)
    if failure is not None:
        return failure

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return _error("Invalid JSON body.", 400)

    party = _s(payload.get("party")).lower()
    if party not in SIGNATURE_PARTIES:
        allowed = "', '".join(SIGNATURE_PARTIES)
        return _error(f"party must be one of '{allowed}'.", 400)

    # Authorise before anything about the review's state is revealed or written:
    # a caller who may not sign must not learn whether it is completed either.
    auth_error = _sign_authorization_error(request, pk, learner, party)
    if auth_error is not None:
        return auth_error

    if not review.form_completed:
        return _error("This review can only be signed once the form is completed.", 400)

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
        promoted = record_signature(review, party, signature, name)
    except DatabaseError as exc:
        logger.exception("enrolment_review_sign: save failed")
        return _error(f"Database error: {exc}", 502)

    payload = _serialize_form(review, learner, event)
    if promoted:
        payload["programmeStatusChangedTo"] = promoted
    return JsonResponse(payload)


def _progress(review):
    """(done, total) sections for one review, so the list can show progress."""
    sections = sections_for(review.review_type)
    status = review.section_status if isinstance(review.section_status, dict) else {}
    return sum(1 for name in sections if status.get(name)), len(sections)


def review_document_rows(rows):
    """The started or finished reviews among `rows`, as listed under Review documents."""
    documents = []
    for row in rows:
        # started_at is stamped when the form is first opened; form_completed when
        # Finish is clicked. Neither means there is nothing to file yet.
        if not row.started_at and not row.form_completed:
            continue
        done, total = _progress(row)
        documents.append({
            "eventKey": row.event_key,
            "reviewType": row.review_type,
            "label": row.review_label or row.review_type,
            "scheduledDate": _iso(row.scheduled_date),
            "reviewedBy": _s(row.reviewed_by) or _s(row.coach_name),
            "completed": bool(row.form_completed),
            "completedAt": _iso(row.form_completed_at),
            "startedAt": _iso(row.started_at),
            "sectionsDone": done,
            "sectionsTotal": total,
            "signatures": _signatures(row, include_saved=False),
        })
    return documents


@learner_self_or_staff(kwarg="pk")
def enrolment_review_documents(request, kind, pk):
    """Reviews a learner has started or finished, for the board's Review documents.

        GET /learner_api/reviews/<kind>/<pk>/

    Only started or completed reviews appear: a review that was merely booked has
    no document to show yet. Cancelled bookings are excluded — their form (if any)
    belongs to a meeting that is not happening.

    Grouped by programme so the panel matches Compliance documents above it.
    """
    if request.method != "GET":
        return _error("Method not allowed.", 405)

    model = SOURCE_MODELS.get(kind)
    if model is None:
        return _error(f"Unknown kind: {kind!r}. Expected 'commercial' or 'apprenticeship'.", 404)

    try:
        learner = model.all_learners.filter(pk=pk).first()
        if learner is None:
            return _error("Learner not found.", 404)
        rows = list(
            EnrolmentReview.objects.filter(learner_id=pk)
            .exclude(status=EnrolmentReview.STATUS_CANCELLED)
            .order_by("scheduled_date", "review_type")
        )
    except DatabaseError as exc:
        logger.exception("enrolment_review_documents: lookup failed")
        return _error(f"Database error: {exc}", 502)

    documents = review_document_rows(rows)

    return JsonResponse({
        "programme": _s(getattr(learner, "programme", "")),
        "documents": documents,
    })


# ── issuing the onboarding reviews ───────────────────────────────────────────
#
#     GET  /learner_api/reviews/<kind>/<pk>/onboarding/  -> the four reviews and their state
#     POST /learner_api/reviews/<kind>/<pk>/issue/       -> body {type}; staff only
#
# The four onboarding reviews are issued by the enrolment team from the
# learner's board rather than booked by the learner: issuing creates the review
# record — no calendar event, no meeting, no invitation — and the learner opens
# it from their Reviews page. Reviews booked before issuing existed keep their
# records and count exactly the same way; completion
# (learning_plan.onboarding_complete) reads these records, never the calendar.


def _current_reviews(pk):
    """{review_type: the learner's current review of that type}: the newest not cancelled."""
    rows = (
        EnrolmentReview.objects.filter(learner_id=pk, review_type__in=ONBOARDING_REVIEW_TYPES)
        .exclude(status=EnrolmentReview.STATUS_CANCELLED)
        .order_by("id")
    )
    return {row.review_type: row for row in rows}


def onboarding_review_slots(pk):
    """One entry per onboarding review, in order: issued or not, and how far it has got."""
    current = _current_reviews(pk)
    slots = []
    for review_type in ONBOARDING_REVIEW_TYPES:
        row = current.get(review_type)
        slot = {"type": review_type, "label": ONBOARDING_REVIEW_LABELS[review_type], "issued": row is not None}
        if row is not None:
            done, total = _progress(row)
            slot.update({
                "eventKey": row.event_key,
                "issuedAt": _iso(row.booked_at),
                "started": bool(row.started_at),
                "completed": bool(row.form_completed),
                "completedAt": _iso(row.form_completed_at),
                "sectionsDone": done,
                "sectionsTotal": total,
                "reviewedBy": _s(row.reviewed_by) or _s(row.coach_name),
                "signatures": _signatures(row, include_saved=False),
            })
        slots.append(slot)
    return slots


def _slots_payload(learner, kind, pk):
    # Enrolment reviews are an apprenticeship requirement; commercial delivery has none.
    slots = onboarding_review_slots(pk) if kind == "apprenticeship" else []
    return {
        "programme": _s(getattr(learner, "programme", "")),
        "reviews": slots,
        "allIssued": bool(slots) and all(s["issued"] for s in slots),
        "allCompleted": bool(slots) and all(s.get("completed") for s in slots),
    }


def _learner_for(kind, pk):
    model = SOURCE_MODELS.get(kind)
    if model is None:
        return None, _error(f"Unknown kind: {kind!r}. Expected 'commercial' or 'apprenticeship'.", 404)
    learner = model.all_learners.filter(pk=pk).first()
    if learner is None:
        return None, _error("Learner not found.", 404)
    return learner, None


@learner_self_or_staff(kwarg="pk")
def onboarding_reviews(request, kind, pk):
    """The learner's four onboarding reviews: which are issued, started, completed, signed."""
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    try:
        learner, failure = _learner_for(kind, pk)
        if failure is not None:
            return failure
        return JsonResponse(_slots_payload(learner, kind, pk))
    except DatabaseError as exc:
        logger.exception("onboarding_reviews: lookup failed")
        return _error(f"Database error: {exc}", 502)


@csrf_exempt
@staff_only()
def issue_onboarding_review(request, kind, pk):
    """Issue one onboarding review to the learner. Staff only; each review is issued once."""
    if request.method != "POST":
        return _error("Method not allowed.", 405)
    if kind != "apprenticeship":
        return _error("Enrolment reviews are only issued to apprenticeship learners.", 400)
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return _error("Invalid JSON body.", 400)
    review_type = payload.get("type") if isinstance(payload, dict) else None
    if review_type not in ONBOARDING_REVIEW_TYPES:
        return _error(f"Unknown review type: {review_type!r}.", 400)

    learner = None
    try:
        learner, failure = _learner_for(kind, pk)
        if failure is not None:
            return failure
        if review_type in _current_reviews(pk):
            return JsonResponse({"error": "This review has already been issued.", **_slots_payload(learner, kind, pk)}, status=409)
        owner_email, owner_name, owner_id = _case_owner_record(learner)
        account = getattr(request, "login_account", None)
        issued_by = (getattr(account, "display_name", None) or getattr(account, "email", None) or "") if account else ""
        EnrolmentReview.objects.create(
            # One key per learner and review type, so a double click (or two
            # officers at once) hits the unique Event_key rather than issuing twice.
            event_key=f"review:{kind}:{pk}:{review_type}",
            review_type=review_type,
            review_label=ONBOARDING_REVIEW_LABELS[review_type],
            learner_kind=kind,
            learner_id=pk,
            learner_name=_s(getattr(learner, "username", "")),
            learner_email=_s(getattr(learner, "email", "")),
            coach_id=owner_id,
            coach_name=owner_name,
            coach_email=owner_email,
            status=EnrolmentReview.STATUS_ISSUED,
            notes=f"Issued by {issued_by}" if issued_by else "Issued",
            booked_at=timezone.now(),
        )
    except IntegrityError:
        return JsonResponse({"error": "This review has already been issued.", **_slots_payload(learner, kind, pk)}, status=409)
    except DatabaseError as exc:
        logger.exception("issue_onboarding_review: save failed")
        return _error(f"Database error: {exc}", 502)

    return JsonResponse(_slots_payload(learner, kind, pk), status=201)
