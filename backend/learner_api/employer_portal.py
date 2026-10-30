"""The employer-facing view of their own learners.

    GET  /learner_api/employer-portal/<employer_id>/
        -> the employer, their learners, and each learner's outstanding signatures
    GET  /learner_api/employer-portal/<employer_id>/learner/<kind>/<learner_id>/
        -> one learner: details, performance summary, and their signable documents

An employer is a person at one or more organisations (enrolment."Employers").
Their learners are the ones whose "Employer_id" points at them — the reference
added by apply_created_users_employer_id, which is why this can exist at all: the
old free-text "Employer" column could not reliably identify whose learners these
are.

Signing itself is NOT reimplemented here. Reviews are signed through the existing
/learner_api/reviews/<kind>/<pk>/<event_key>/sign/ endpoint with party="employer",
so all three parties share one code path and one set of validation rules.
Compliance PDFs get their own endpoint (they had no signing flow at all) in
enrolment_api/documents.py.

CSRF is exempted for the same reason as the rest of learner_api: an internal
same-origin dev API behind the Vite proxy.
"""
import json
import logging
from concurrent.futures import ThreadPoolExecutor

from django.db import DatabaseError
from django.db.models import JSONField
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from login.permissions import employer_or_staff

from .learner_detail import SOURCE_MODELS
from .identity import learner_profile_for_source
from .employer_review_dates import imported_document_dates
from .mappers import _s, to_employer_row
from .models import Employer, EnrolmentReview, LearnerProfile
from .review_form import (
    MAX_SIGNATURE_CHARS,
    _lookup as _lookup_enrolment_review,
    _serialize_form as _serialize_enrolment_review,
    employer_signature_required,
    sections_for,
)
from .views import _error, _parse_body
from coach_api.models import CoachAbsenceReport, CoachCalendarEvent, ImportedReviewInstance
from coach_api.migrated_completion import is_personal_support_form, signature_states
from coach_api.review_categories import review_event_category
from curriculum_api import review_instances

logger = logging.getLogger(__name__)

# A learner at this status is on programme, so their card leads with performance.
# Anything else (Onboarding, Ready to enrol, On probation, …) is still being set
# up, so their outstanding paperwork leads instead. Both are always shown — the
# status only decides the order.
ACTIVE_STATUS = "active"


def _iso(value):
    return value.isoformat() if value else None


def _learner_kind(learner):
    return "commercial" if _s(getattr(learner, "learner_type", "")) == "commercial" else "apprenticeship"


def _review_signing_rows(kind, learner_id, *, employer_only=True, learner=None):
    """One learner's reviews as the employer sees them.

    `learner` is their Created_users row when the caller already holds it, so
    it is not read a second time. See _review_signing_rows_for.
    """
    if learner is None:
        source_model = SOURCE_MODELS.get(kind)
        if source_model is not None:
            try:
                learner = source_model.all_learners.get(pk=learner_id)
            except (source_model.DoesNotExist, DatabaseError):
                learner = None
    rows = _review_signing_rows_for([(kind, learner_id, learner)], employer_only=employer_only)
    return rows[(kind, learner_id)]


def _active_profile_ids(learners):
    """{learner_id: active LearnerProfile pk}, read in one query.

    learner_profile_for_source resolves one learner at a time; this does the
    enrolment_id match -- the real link -- for all of them at once, and falls
    back to it (email matching, self-healing) only for the ones it missed.
    """
    sources = {learner_id: source for _kind, learner_id, source in learners if source is not None}
    if not sources:
        return {}
    found = {}
    try:
        # Lowest pk first: the same profile .first() picks for one learner.
        for profile in LearnerProfile.objects.filter(
            enrolment_id__in=list(sources), lifecycle_status="active",
        ).order_by("pk"):
            found.setdefault(profile.enrolment_id, profile.pk)
        for learner_id, source in sources.items():
            if learner_id in found:
                continue
            profile = learner_profile_for_source(source, learner_id, active_only=True)
            if profile is not None:
                found[learner_id] = profile.pk
    except DatabaseError:
        logger.exception("_active_profile_ids: lookup failed for %s", list(sources))
    return found


def _review_signing_rows_for(learners, *, employer_only=True):
    """{(kind, learner_id): reviews} for many learners in a fixed number of queries.

    `learners` is (kind, learner_id, Created_users row or None) per learner.
    Asking per learner cost ~30 round trips each, which made an employer's
    landing page take minutes; every lookup here covers all of them at once.

    `employer_only` keeps the list to reviews that actually want an employer
    signature — the employer has no business being shown the RPL review.
    """
    keys = [(kind, learner_id) for kind, learner_id, _source in learners]
    rows_by_learner = {key: [] for key in keys}
    if not keys:
        return rows_by_learner
    learner_ids = list(dict.fromkeys(learner_id for _kind, learner_id in keys))

    try:
        # A cancelled booking's review belongs to a meeting that is not
        # happening -- the learner re-books, and that new review is the one to
        # sign. Same rule as the board's and the coach's review documents.
        reviews = list(
            EnrolmentReview.objects.filter(learner_id__in=learner_ids)
            .exclude(status=EnrolmentReview.STATUS_CANCELLED)
            .order_by("scheduled_date", "id")
        )
    except DatabaseError:
        logger.exception("_review_signing_rows_for: lookup failed for %s", keys)
        return rows_by_learner

    for review in reviews:
        key = (review.learner_kind, review.learner_id)
        if key not in rows_by_learner:
            continue
        required = employer_signature_required(review)
        if employer_only and not required:
            continue
        signed = bool(_s(review.employer_signature))
        rows_by_learner[key].append({
            "kind": "review",
            "eventKey": review.event_key,
            "reviewType": _s(review.review_type),
            "label": _s(review.review_label) or _s(review.review_type),
            "scheduledDate": _s(review.scheduled_date),
            # Only a finished questionnaire can be signed, by any party.
            "signable": bool(review.form_completed),
            "completed": bool(review.form_completed),
            "sectionsTotal": len(sections_for(review.review_type)),
            "employerSignatureRequired": required,
            "signed": signed,
            "signedName": _s(review.employer_signed_name),
            "signedAt": _iso(review.employer_signed_at),
            # Whether the other parties have signed, shown as context so the
            # employer can see they are not the only one outstanding.
            "learnerSigned": bool(_s(review.learner_signature)),
            "adminSigned": bool(_s(review.admin_signature)),
        })

    # Curriculum instances are the canonical review records. Keep them in the
    # existing employer queue shape while the old EnrolmentReview records drain.
    # Instances are normally keyed by the internal LearnerProfile id, while
    # this portal is addressed by the source enrolment id. Read both identities
    # so a valid review cannot disappear from the employer queue.
    profile_ids = _active_profile_ids(learners)
    identity_ids = {}
    for kind, learner_id in keys:
        ids = [str(learner_id)]
        profile_id = profile_ids.get(learner_id)
        if profile_id is not None and str(profile_id) not in ids:
            ids.append(str(profile_id))
        identity_ids[(kind, learner_id)] = ids
    all_identity_ids = list(dict.fromkeys(i for ids in identity_ids.values() for i in ids))

    try:
        instances_by_identity = review_instances.list_review_instances_for_learners(all_identity_ids)
    except DatabaseError:
        instances_by_identity = {}
    instances_by_learner = {}
    unique_instances = {}
    for key, ids in identity_ids.items():
        instance_rows = []
        seen_instance_ids = set()
        for instance_learner_id in ids:
            for instance in instances_by_identity.get(instance_learner_id, []):
                instance_id = _s(instance.get('id'))
                if instance_id and instance_id in seen_instance_ids:
                    continue
                if instance_id:
                    seen_instance_ids.add(instance_id)
                instance_rows.append(instance)
                unique_instances.setdefault(instance.get('id'), instance)
        instances_by_learner[key] = instance_rows
    definitions = review_instances.review_instance_signing_definitions(list(unique_instances.values()))

    wanted = []
    for key, instance_rows in instances_by_learner.items():
        for instance in instance_rows:
            definition = definitions.get(instance.get('id'))
            if definition is None:
                continue
            if employer_only and not definition['template']['visibleTo'].get('employer', False):
                continue
            if not definition['signatures'].get('employer', {}).get('required'):
                continue
            wanted.append((key, instance, definition))
    calendar_ids = {instance.get('calendar_event_id') for _key, instance, _d in wanted if instance.get('calendar_event_id')}
    event_keys = dict(
        CoachCalendarEvent.objects.filter(pk__in=calendar_ids).values_list("pk", "event_key")
    ) if calendar_ids else {}
    for key, instance, definition in wanted:
        signatures = definition['signatures']
        employer_state = signatures.get('employer', {})
        rows_by_learner[key].append({
            "kind": "review",
            "eventKey": _s(event_keys.get(instance.get('calendar_event_id'), '')) or _s(instance.get('id')),
            "reviewInstanceId": _s(instance.get('id')),
            "reviewType": _s(definition['template'].get('reviewTypeCode')) or 'review',
            "label": _s(definition['template'].get('name')) or 'Review',
            "scheduledDate": _s(instance.get('target_date')),
            "signable": instance.get('status') in ('awaiting-signature', 'completed'),
            "completed": instance.get('status') in ('awaiting-signature', 'completed'),
            "sectionsTotal": len(definition.get('sections') or []),
            "employerSignatureRequired": True,
            "signed": bool(employer_state.get('signed')),
            "signedName": _s(employer_state.get('signedName')),
            "signedAt": employer_state.get('signedAt'),
            "learnerSigned": bool(signatures.get('participant', {}).get('signed')),
            "adminSigned": bool(signatures.get('advisor', {}).get('signed')),
        })

    # Imported Aptem continuations have their own overlay and signature store.
    # Only these learners' profile ids are queried; native review rows above
    # keep their existing behavior and document identity.
    overlays = [
        overlay
        for overlay in ImportedReviewInstance.objects.select_related("migrated_template")
        .prefetch_related("migrated_signatures")
        .filter(
            learner_id__in=[int(value) for value in all_identity_ids],
            source_review_id__isnull=False,
            status__in=[ImportedReviewInstance.STATUS_AWAITING_SIGNATURE, ImportedReviewInstance.STATUS_COMPLETED],
        )
        if overlay.migrated_template_id
    ]
    imported_dates = imported_document_dates(overlays)
    for key, ids in identity_ids.items():
        ids = {int(value) for value in ids}
        for overlay in overlays:
            if overlay.learner_id not in ids:
                continue
            signatures = signature_states(overlay)
            employer_state = signatures["employer"]
            if not employer_state["required"]:
                continue
            family = (overlay.template_snapshot.get("templateSource") or {}).get("family") or overlay.migrated_template.review_family
            rows_by_learner[key].append({
                "kind": "review", "eventKey": overlay.event_key,
                "reviewInstanceId": overlay.event_key, "migratedForm": True,
                "reviewType": "aptem_psp" if family == "PSP" else "aptem_mcm" if family == "MCM" else "aptem_progress_review",
                "label": overlay.template_snapshot.get("name") or ("Personal Support Plan" if family == "PSP" else "Monthly Coaching Meeting" if family == "MCM" else "Migrated Progress Review"),
                "scheduledDate": imported_dates.get((overlay.learner_id, overlay.source_review_id), ""),
                "signable": overlay.status == ImportedReviewInstance.STATUS_AWAITING_SIGNATURE,
                "completed": overlay.status == ImportedReviewInstance.STATUS_COMPLETED,
                "sectionsTotal": len(overlay.template_snapshot.get("sections") or []),
                "employerSignatureRequired": True,
                "signed": employer_state["signed"],
                "signedName": employer_state["signedName"] or "",
                "signedAt": employer_state["signedAt"],
                "learnerSigned": signatures["participant"]["signed"],
                "adminSigned": signatures["advisor"]["signed"],
            })
    return rows_by_learner


def _agreement_signing_row(agreement):
    """The learner's active Apprenticeship Agreement, as a signable row.

    The agreement lives in its own table rather than Enrolment_Documents, but the
    employer sees it in the same list as everything else they must sign — so it
    is projected onto the same row shape here.
    """
    return {
        "kind": "agreement",
        "id": str(agreement.id),
        "docType": "apprenticeship-agreement",
        "label": "Apprenticeship Agreement",
        "generatedAt": _iso(agreement.created_at),
        # Signed by the apprentice and employer only — no provider signature.
        "signable": True,
        "signed": agreement.employer_signed,
        "signedName": _s(agreement.employer_signed_name),
        "signedAt": _iso(agreement.employer_signed_at),
        "parties": ["learner", "employer"],
        "learnerSigned": agreement.apprentice_signed,
        "learnerSignedName": _s(agreement.apprentice_signed_name),
        "learnerSignedAt": _iso(agreement.apprentice_signed_at),
    }


def _training_plan_signing_row(plan):
    """The learner's active Training Plan, as a signable row.

    Signed by all three parties; the employer is one of them, so it appears in
    their queue alongside the reviews and the apprenticeship agreement.
    """
    return {
        "kind": "training-plan",
        "id": str(plan.id),
        "docType": "training-plan",
        "label": "Training Plan",
        "generatedAt": _iso(plan.created_at),
        "signable": True,
        "signed": plan.employer_signed,
        "signedName": _s(plan.employer_signed_name),
        "signedAt": _iso(plan.employer_signed_at),
        # All three parties, so the employer can see who else is outstanding.
        "parties": ["learner", "employer", "provider"],
        "learnerSigned": plan.apprentice_signed,
        "learnerSignedName": _s(plan.apprentice_signed_name),
        "learnerSignedAt": _iso(plan.apprentice_signed_at),
        "providerSigned": plan.provider_signed,
        "providerSignedName": _s(plan.provider_signed_name),
        "providerSignedAt": _iso(plan.provider_signed_at),
    }


def _written_agreement_signing_row(doc):
    """The learner's active Written Agreement, as a signable row.

    Signed by the learner, the employer and the provider — the employer is one
    of the three, so it appears in their queue.
    """
    return {
        "kind": "written-agreement",
        "id": str(doc.id),
        "docType": "written-agreement",
        "label": "Written Agreement",
        "generatedAt": _iso(doc.created_at),
        "signable": True,
        "signed": doc.employer_signed,
        "signedName": _s(doc.employer_signed_name),
        "signedAt": _iso(doc.employer_signed_at),
        "parties": ["learner", "employer", "provider"],
        "learnerSigned": doc.learner_signed,
        "learnerSignedName": _s(doc.learner_signed_name),
        "learnerSignedAt": _iso(doc.learner_signed_at),
        "providerSigned": doc.provider_signed,
        "providerSignedName": _s(doc.provider_signed_name),
        "providerSignedAt": _iso(doc.provider_signed_at),
    }


def _active_documents(model, keys):
    """{(kind, learner_id): the active row of `model`}, in one query.

    The models order newest first, so the first row seen per learner is the
    one .first() returned when this was asked one learner at a time. The
    frozen document content (JSON columns) is never shown here, so it is not
    fetched.
    """
    frozen_content = [field.name for field in model._meta.concrete_fields if isinstance(field, JSONField)]
    try:
        found = {}
        for doc in model.objects.filter(
            learner_id__in=list(dict.fromkeys(learner_id for _kind, learner_id in keys)),
            status=model.STATUS_ACTIVE,
        ).defer(*frozen_content):
            found.setdefault((doc.learner_kind, doc.learner_id), doc)
        return found
    except DatabaseError:
        logger.exception("_active_documents: %s lookup failed for %s", model.__name__, keys)
        return {}


def _document_signing_rows(kind, learner_id):
    """One learner's signable documents. See _document_signing_rows_for."""
    return _document_signing_rows_for([(kind, learner_id)])[(kind, learner_id)]


def _document_signing_rows_for(keys):
    """{(kind, learner_id): signable documents} for many learners at once.

    The Apprenticeship Agreement, Training Plan and Written Agreement come
    first, then the generated compliance PDFs. Those are read with raw SQL
    rather than a model: enrolment."Enrolment_Documents" is owned by
    enrolment_api and has no Django model — see its document_tables.py.
    """
    from django.db import connections

    from enrolment_api.document_tables import ensure_enrolment_documents_table

    from enrolment_api.documents import SIGNING_PARTIES

    from .models import ApprenticeshipAgreement, TrainingPlanDocument, WrittenAgreement

    out = {key: [] for key in keys}
    if not keys:
        return out
    for model, to_row in (
        (ApprenticeshipAgreement, _agreement_signing_row),
        (TrainingPlanDocument, _training_plan_signing_row),
        (WrittenAgreement, _written_agreement_signing_row),
    ):
        for key, doc in _active_documents(model, keys).items():
            if key in out:
                out[key].append(to_row(doc))

    learner_ids = list(dict.fromkeys(learner_id for _kind, learner_id in keys))
    placeholders = ", ".join(["%s"] * len(learner_ids))
    try:
        ensure_enrolment_documents_table()
        with connections["enrolment"].cursor() as cur:
            cur.execute(
                f'''
                select "Learner_kind", "Learner_id",
                       id, "Doc_type", "Doc_name", "Generated_at",
                       "Employer_signature", "Employer_signed_name", "Employer_signed_at",
                       "Learner_signature", "Learner_signed_name", "Learner_signed_at"
                from enrolment."Enrolment_Documents"
                where "Learner_id" in ({placeholders})
                order by "Generated_at" desc
                ''',
                learner_ids,
            )
            rows = cur.fetchall()
    except DatabaseError:
        logger.exception("_document_signing_rows_for: lookup failed for %s", keys)
        return out

    for row in rows:
        (
            learner_kind, row_learner_id,
            doc_id, doc_type, doc_name, generated_at,
            sig, sig_name, sig_at,
            learner_sig, learner_sig_name, learner_sig_at,
        ) = row
        key = (learner_kind, row_learner_id)
        if key not in out:
            continue
        doc_type = _s(doc_type)
        # Only offer documents this type actually asks the employer to sign —
        # a learner-only document has no business appearing in their queue.
        parties = SIGNING_PARTIES.get(doc_type, ("employer",))
        out[key].append({
            "kind": "document",
            "id": str(doc_id),
            "docType": doc_type,
            "label": _s(doc_name) or doc_type,
            "generatedAt": _iso(generated_at),
            # A generated PDF needs no questionnaire finished first, so it is
            # signable as soon as the employer is one of its parties.
            "signable": "employer" in parties,
            "signed": bool(_s(sig)),
            "signedName": _s(sig_name),
            "signedAt": _iso(sig_at),
            # Who else this document is waiting on, so the employer can see
            # whether the learner has signed their side yet.
            "parties": list(parties),
            "learnerSigned": bool(_s(learner_sig)),
            "learnerSignedName": _s(learner_sig_name),
            "learnerSignedAt": _iso(learner_sig_at),
        })
    return out


def _employer_or_404(employer_id):
    try:
        return Employer.objects.get(pk=employer_id), None
    except Employer.DoesNotExist:
        return None, _error("Employer not found.", 404)
    except DatabaseError as exc:
        return None, _error(f"Database error: {exc}", 502)


def _learner_cards(employer_id):
    """One card per learner belonging to this employer, across both kinds.

    Both learner kinds share one table, so a single query covers them; the row's
    own "Learner_type" decides which `kind` its links use.
    """
    model = SOURCE_MODELS["apprenticeship"]
    try:
        learners = list(model.all_learners.filter(employer_id=employer_id).order_by("username", "id"))
    except DatabaseError:
        logger.exception("_learner_cards: lookup failed for employer %s", employer_id)
        return []

    entries = [(_learner_kind(learner), learner.pk, learner) for learner in learners]
    reviews_by_learner = _review_signing_rows_for(entries)
    documents_by_learner = _document_signing_rows_for([(kind, pk) for kind, pk, _learner in entries])

    cards = []
    for kind, _pk, learner in entries:
        status = _s(learner.programme_status)
        reviews = reviews_by_learner[(kind, learner.pk)]
        documents = documents_by_learner[(kind, learner.pk)]
        # Only counts what the employer can actually act on now: an unfinished
        # review is not yet signable, so counting it would show a task they
        # cannot complete.
        outstanding = [
            item for item in (*reviews, *documents)
            if item["signable"] and not item["signed"]
        ]
        cards.append({
            "id": str(learner.pk),
            "kind": kind,
            "name": _s(learner.username),
            "email": _s(learner.email),
            "programme": _s(learner.programme),
            "cohort": _s(learner.cohort),
            "programmeStatus": status,
            "onboardingStatus": _s(learner.onboarding_status),
            # Drives which panel the learner page leads with. Both are shown
            # either way — see the module docstring.
            "isActive": status.lower() == ACTIVE_STATUS,
            "outstandingCount": len(outstanding),
            "documentsTotal": len(reviews) + len(documents),
        })
    _attach_learner_otjh_progress(cards, [learner for _kind, _pk, learner in entries])
    return cards


def _attach_learner_otjh_progress(cards, learners):
    """Attach the canonical OTJ ratio from one caseload read, with plan fallback."""
    if not learners:
        return

    from . import canonical_learning
    from .journal_sources import learner_journal_reads
    from .training_plan_dashboard import plan_planned_hours

    learner_ids = [learner.pk for learner in learners]
    # metrics_bulk applies the same identity checks, accepted-hours rules,
    # enrolment plan precedence and rounding used by the Learner workspace.
    with learner_journal_reads():
        metrics_by_learner = canonical_learning.metrics_bulk(
            learner_ids, learner_workspace=True,
        )

    # Resolve the profile ids in one query; plan_planned_hours needs the
    # canonical owner id only for the Learner's inherited-course fallback.
    profiles_by_learner = {}
    duplicate_profiles = set()
    profiles = LearnerProfile.objects.filter(enrolment_id__in=learner_ids).values(
        'id', 'enrolment_id',
    )
    for profile in profiles:
        enrolment_id = profile['enrolment_id']
        if enrolment_id in profiles_by_learner:
            duplicate_profiles.add(enrolment_id)
        profiles_by_learner[enrolment_id] = profile
    for enrolment_id in duplicate_profiles:
        profiles_by_learner.pop(enrolment_id, None)

    # The cache is request-local and follows the existing group-plan resolver;
    # it never survives a request or becomes stale after an assignment changes.
    preset_cache = {}
    cards_by_learner = {int(card['id']): card for card in cards}
    for learner in learners:
        learner._plan_preset_cache = preset_cache
        metric = metrics_by_learner.get(learner.pk)
        otjh = (metric or {}).get('otjh')
        progress = None
        if otjh is not None:
            actual = (otjh.get('completed_actual') if 'completed_actual' in otjh
                      else otjh.get('actual'))
            planned = otjh.get('planned')
            if planned is None:
                owner = profiles_by_learner.get(learner.pk)
                if owner is not None:
                    try:
                        planned = plan_planned_hours(learner, owner)
                    except Exception:
                        # Preserve canonical actual hours; never turn a failed
                        # inherited-plan read into zero or fail other learners.
                        logger.exception(
                            "employer list OTJ plan unavailable for learner %s", learner.pk,
                        )
            percent = round(actual / planned * 100, 2) if actual is not None and planned else None
            progress = {'actual': actual, 'planned': planned, 'percent': percent}
        card = cards_by_learner.get(int(learner.pk))
        if card is not None:
            card['otjhProgress'] = progress


def _recording_absence_notification(report, employer_id):
    return {
        "id": f"recorded-absence:{report.pk}",
        "text": (
            f"{report.learner_name} reported they will miss {report.session_title} "
            "and watch the recording. Attendance will remain absent."
        ),
        "createdAt": _iso(report.created_at),
        "type": "attendance",
        "category": "Attendance",
        "link": f"/employers/{employer_id}",
    }


@csrf_exempt
@employer_or_staff()
def employer_absence_notifications(request, employer_id):
    """Recording-choice absence alerts for this employer's own learners."""
    if request.method != "GET":
        return _error("Method not allowed.", 405)

    try:
        learner_ids = list(
            SOURCE_MODELS["apprenticeship"].all_learners
            .filter(employer_id=employer_id)
            .values_list("id", flat=True)
        )
        reports = (
            CoachAbsenceReport.objects
            .filter(learner_id__in=learner_ids, recovery_method="recorded")
            .order_by("-created_at")[:50]
        )
        items = [_recording_absence_notification(report, employer_id) for report in reports]
    except DatabaseError:
        logger.exception("Could not load absence notifications for employer %s", employer_id)
        return _error("Could not load absence notifications.", 502)

    return JsonResponse({"items": items})


@csrf_exempt
@employer_or_staff()
def employer_portal(request, employer_id):
    """The employer's landing page: who they are, and their learners."""
    if request.method != "GET":
        return _error("Method not allowed.", 405)

    employer, err = _employer_or_404(employer_id)
    if err:
        return err

    cards = _learner_cards(employer_id)
    return JsonResponse({
        "employer": {
            **to_employer_row(employer),
        },
        "learners": cards,
        "outstandingTotal": sum(c["outstandingCount"] for c in cards),
    })


def _dashboard_otjh_risk(value):
    """Project the saved learner status into the employer dashboard's labels."""
    label = ' '.join(str(value or '').casefold().replace('-', ' ').replace('_', ' ').split())
    return {
        'on track': 'on-track',
        'need attention': 'need-attention',
        'needs attention': 'need-attention',
        'at risk': 'at-risk',
    }.get(label, 'unavailable')


def _read_employer_home_progress(source):
    """Read one learner's compact Student Home projection in an isolated worker."""
    from django.db import close_old_connections, connections
    from .journal_sources import learner_journal_reads
    from .overview_week import read_week

    close_old_connections()
    try:
        with learner_journal_reads():
            return read_week(
                source, home_kind=source.learner_type, home_summary=True,
            ).get('homeProgress')
    finally:
        # Each executor worker owns its Django connections; do not leave them
        # attached to a thread after this bounded read finishes.
        connections.close_all()


def _read_employer_module_progress(source, profile):
    """Read one learner's catalogue rollup in an isolated worker."""
    from django.db import close_old_connections, connections
    from .module_progress import canonical_module_progress

    close_old_connections()
    try:
        return canonical_module_progress(source, profile)
    finally:
        connections.close_all()


@employer_or_staff()
def employer_dashboard_metrics(request, employer_id):
    """Compact, read-only dashboard facts for this employer's own learners."""
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    employer, err = _employer_or_404(employer_id)
    if err is not None:
        return err
    section = request.GET.get('section', 'all')
    if section not in {'all', 'summary', 'programme', 'progress', 'status'}:
        return _error("Unknown dashboard section.", 400)
    from zoneinfo import ZoneInfo
    from old_otjh.service import ServiceError
    from . import canonical_learning
    from .journal_sources import learner_journal_reads
    from .overview_week import HOME_SOURCE_FIELDS
    from .training_plan_dashboard import plan_planned_hours

    try:
        fields = ('id', 'email', 'aptem_id', 'programme', 'cohort', 'group', 'group_name',
                  'programme_id', 'cohort_id', 'group_id', 'training_plan', 'learning_plan') if section == 'summary' else (
            ('id', 'email', 'aptem_id') if section == 'status' else (
            (*HOME_SOURCE_FIELDS, 'learner_type') if section == 'progress' else (
            *HOME_SOURCE_FIELDS, 'learner_type', 'group', 'training_plan', 'learning_plan', 'modules', 'weeks', 'components')
        ))
        learners = list(SOURCE_MODELS["apprenticeship"].all_learners
                        .filter(employer_id=employer.pk).only(*fields))
        learner_ids = [learner.pk for learner in learners]
        # Validate all learner identities with one profile read; the progress
        # values below come from the same compact home projection as Student Home.
        profiles = {}
        saved = LearnerProfile.objects.filter(enrolment_id__in=learner_ids).values(
            'id', 'enrolment_id', 'email', 'aptem_id', 'otjh_status',
        ) if learner_ids else []
        for profile in saved:
            if profile['enrolment_id'] in profiles:
                raise ServiceError('The consolidated learner identity needs review.', 'identity_review_required', 409)
            profiles[profile['enrolment_id']] = profile
        for learner in learners:
            owner = profiles.get(learner.pk)
            if owner is not None:
                canonical_learning._validated_profile({
                    **owner, 'account_record_id': learner.pk,
                    'account_email': learner.email, 'account_aptem_id': learner.aptem_id,
                })
        progress_learners = [learner for learner in learners if learner.pk in profiles]
        home_metrics_by_learner = {}
        if section == 'summary' and progress_learners:
            # The table needs only Student Home's canonical OTJ hours. Use its
            # bulk ledger reader instead of rebuilding the full home projection
            # (catalogue, attendance and activity calendar) once per learner.
            with learner_journal_reads():
                home_metrics_by_learner = canonical_learning.metrics_bulk(
                    [learner.pk for learner in progress_learners], learner_workspace=True,
                )
            missing_home_metrics = [
                learner for learner in progress_learners
                if not (home_metrics_by_learner.get(learner.pk) or {}).get('otjh')
            ]
            if missing_home_metrics:
                # Use Student Home's compact projection only for records the
                # bulk ledger could not resolve. This keeps the normal path
                # batched while matching the learner card when a bulk result
                # is absent for an otherwise readable learner.
                with ThreadPoolExecutor(max_workers=min(3, len(missing_home_metrics))) as executor:
                    fallback_progress = executor.map(
                        _read_employer_home_progress, missing_home_metrics,
                    )
                    for learner, projection in zip(missing_home_metrics, fallback_progress):
                        home_otjh = (projection or {}).get('otjh')
                        if home_otjh is not None:
                            home_metrics_by_learner[learner.pk] = {'otjh': home_otjh}
            plan_preset_cache = {}
            for learner in progress_learners:
                learner._plan_preset_cache = plan_preset_cache
            for learner in progress_learners:
                home_otjh = (home_metrics_by_learner.get(learner.pk) or {}).get('otjh') or {}
                if home_otjh.get('planned') is None:
                    # Match Student Home's fallback only when SSOT has no plan.
                    # An explicit zero is authoritative and must stay zero.
                    try:
                        home_otjh['planned'] = plan_planned_hours(
                            learner, profiles[learner.pk],
                        )
                    except Exception:
                        # One learner's inherited-plan lookup must not discard
                        # canonical actual hours for the rest of the employer's
                        # caseload. Keep the plan unavailable and log the partial
                        # read so the UI never presents the calculation as zero.
                        logger.exception(
                            "employer_dashboard_metrics: planned hours unavailable for learner %s",
                            learner.pk,
                        )
                        home_otjh['planned'] = None
                    actual = (home_otjh.get('completed_actual')
                              if 'completed_actual' in home_otjh else home_otjh.get('actual'))
                    home_otjh['percent'] = round(actual / home_otjh['planned'] * 100, 2) \
                        if actual is not None and home_otjh['planned'] else None
        module_progress_by_learner = {}
        if section in {'programme', 'progress', 'all'} and progress_learners:
            # Catalogue rollups are independent too. Bound concurrency so a
            # larger employer caseload cannot create an unbounded DB fan-out.
            module_inputs = [
                (learner, LearnerProfile(id=profiles[learner.pk]['id'], enrolment_id=learner.pk))
                for learner in progress_learners
            ]
            with ThreadPoolExecutor(max_workers=min(3, len(module_inputs))) as executor:
                module_progress_by_learner = dict(zip(
                    (learner.pk for learner in progress_learners),
                    executor.map(lambda pair: _read_employer_module_progress(*pair), module_inputs),
                ))
        today = timezone.localdate(timezone=ZoneInfo("Europe/London"))
        rows = {}
        for learner in learners:
            learner_id = learner.pk
            owner = profiles.get(learner_id)
            programme = None
            otjh = None
            attendance = None
            if owner is not None:
                # Student Home counts the assigned catalogue, including work
                # not started yet, rather than only saved progress records.
                profile = LearnerProfile(id=owner['id'], enrolment_id=learner_id)
                if section == 'summary':
                    learner_metrics = home_metrics_by_learner.get(learner_id)
                    home_otjh = (learner_metrics or {}).get('otjh')
                    if home_otjh is not None:
                        actual = (home_otjh.get('completed_actual') if 'completed_actual' in home_otjh
                                  else home_otjh.get('actual'))
                        planned = home_otjh.get('planned')
                        otjh = {
                            'actual': actual,
                            'planned': planned,
                            'percent': round(actual / planned * 100, 2)
                            if actual is not None and planned else None,
                        }
                if section in {'programme', 'progress', 'all'}:
                    modules = module_progress_by_learner.get(learner_id)
                    programme = canonical_learning.point_ratio(
                        sum(module['completed'] for module in modules),
                        sum(module['total'] for module in modules),
                    ) if modules is not None else None
                if section in {'summary', 'progress', 'all'}:
                    # Student Home's My Progress card is sourced from this exact
                    # compact home projection, not the dashboard's target-to-date
                    # measure or the legacy LearnerProfile hour snapshot. The
                    # learner endpoint uses the same journal-backed canonical
                    # values. Only non-summary combined reads need the wider card.
                    if section != 'summary':
                        home_progress = _read_employer_home_progress(learner)
                        home_otjh = (home_progress or {}).get('otjh')
                        if home_otjh is not None:
                            otjh = {key: home_otjh.get(key) for key in ('actual', 'planned', 'percent')}
                if section == 'all':
                    attendance = _overview_part('attendance', learner)['attendance']
            rows[str(learner_id)] = {
                "programme": {key: programme.get(key) for key in ("completed", "total", "percent", "status")} if programme else None,
                "otjh": otjh,
                "attendance": attendance,
                "risk": _dashboard_otjh_risk(owner['otjh_status']) if owner is not None else 'unavailable',
            }
    except (DatabaseError, ServiceError):
        logger.exception("employer_dashboard_metrics: metrics unavailable for employer %s", employer.pk)
        return _error("Could not load learner progress. Please try again.", 503)
    response = JsonResponse({"learners": rows, "asOf": today.isoformat()})
    response["Cache-Control"] = "private, no-store"
    return response


@csrf_exempt
@employer_or_staff()
def employer_portal_documents(request, employer_id):
    """Every signable item across this employer's learners, in one list.

    The same rows the learner page's Documents tab shows (reviews wanting an
    employer signature, then compliance PDFs), each tagged with its learner so
    the portal can sign or open it without visiting that learner first. The
    learners are exactly the employer's own — the same query as the landing
    page's cards — so no learner id from the client is trusted here.
    """
    if request.method != "GET":
        return _error("Method not allowed.", 405)

    employer, err = _employer_or_404(employer_id)
    if err:
        return err

    model = SOURCE_MODELS["apprenticeship"]
    try:
        learners = list(model.all_learners.filter(employer_id=employer.pk).order_by("username", "id"))
    except DatabaseError:
        logger.exception("employer_portal_documents: lookup failed for employer %s", employer.pk)
        return _error("Could not load documents. Please try again.", 503)

    entries = [(_learner_kind(learner), learner.pk, learner) for learner in learners]
    reviews_by_learner = _review_signing_rows_for(entries)
    documents_by_learner = _document_signing_rows_for([(kind, pk) for kind, pk, _learner in entries])

    items = []
    for kind, _pk, learner in entries:
        owner = {
            "id": str(learner.pk),
            "kind": kind,
            "name": _s(learner.username),
            "programme": _s(learner.programme),
        }
        key = (kind, learner.pk)
        for item in (*reviews_by_learner[key], *documents_by_learner[key]):
            items.append({**item, "learner": owner})

    return JsonResponse({
        "employer": {"id": str(employer.pk), "name": employer.full_name},
        "items": items,
        "outstandingTotal": len([i for i in items if i["signable"] and not i["signed"]]),
    })


@csrf_exempt
@employer_or_staff()
def employer_portal_learner(request, employer_id, kind, learner_id):
    """One learner, as their employer sees them.

    Returns the learner's own details and progress summary plus every document
    the employer is asked to sign. The learner is checked to actually belong to
    this employer, so an employer cannot read another's learner by guessing ids.
    """
    if request.method != "GET":
        return _error("Method not allowed.", 405)

    employer, err = _employer_or_404(employer_id)
    if err:
        return err

    model = SOURCE_MODELS.get(kind)
    if model is None:
        return _error(f"Unknown kind: {kind!r}.", 404)

    try:
        learner = model.all_learners.get(pk=learner_id)
    except model.DoesNotExist:
        return _error("Learner not found.", 404)
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)

    # The authorisation check: this endpoint is reachable per employer, so the
    # learner must be one of theirs.
    if learner.employer_id != employer.pk:
        return _error("That learner does not belong to this employer.", 403)

    status = _s(learner.programme_status)
    reviews = _review_signing_rows(kind, learner.pk, learner=learner)
    documents = _document_signing_rows(kind, learner.pk)

    return JsonResponse({
        "employer": {
            "id": str(employer.pk),
            "name": employer.full_name,
        },
        "learner": {
            "id": str(learner.pk),
            "kind": kind,
            "name": _s(learner.username),
            "email": _s(learner.email),
            "phone": _s(learner.phone_number),
            "programme": _s(learner.programme),
            "cohort": _s(learner.cohort),
            # The learner record's employer display name, as the learner's own
            # dashboard header shows it.
            "employer": _s(learner.employer),
            "organization": _s(learner.organization),
            "programmeStatus": status,
            "onboardingStatus": _s(learner.onboarding_status),
            "startDate": _s(learner.start_date),
            "endDate": _s(learner.end_date),
            # The two dates the learner's own dashboard header shows
            # (dashboard_header.header_identity). "Start_date"/"End_date" above
            # are a different pair of columns and are empty for most learners,
            # so the header must read these rather than fall back to them.
            "learnerStartDate": _s(learner.learner_start_date),
            "learnerEndDate": _s(learner.learner_end_date),
            "isActive": status.lower() == ACTIVE_STATUS,
        },
        "performance": _performance(kind, learner),
        "reviews": reviews,
        "documents": documents,
        "outstandingCount": len([
            i for i in (*reviews, *documents) if i["signable"] and not i["signed"]
        ]),
    })


@csrf_exempt
@employer_or_staff()
def employer_review_instance(request, employer_id, kind, learner_id, event_key, as_pdf=False):
    """Read or sign the same Curriculum Review instance used by coach/learner."""
    if request.method not in ("GET", "POST") or (as_pdf and request.method != "GET"):
        return _error("Method not allowed.", 405)
    employer, err = _employer_or_404(employer_id)
    if err:
        return err
    model = SOURCE_MODELS.get(kind)
    if model is None:
        return _error("Unknown learner kind.", 404)
    try:
        learner = model.all_learners.get(pk=learner_id)
    except model.DoesNotExist:
        return _error("Learner not found.", 404)
    if learner.employer_id != employer.pk:
        return _error("That learner does not belong to this employer.", 403)
    from .calendar import _calendar_profile
    profile = _calendar_profile(learner, learner.pk)
    owner_id = profile.pk if profile is not None else learner.pk
    record = CoachCalendarEvent.objects.filter(event_key=event_key).first()
    if record and str(record.learner_id or '') not in {str(learner_id), str(owner_id)} and _s(record.learner_email).casefold() != _s(learner.email).casefold():
        record = None
    instance = review_instances.get_review_instance(getattr(record, 'review_instance_id', '')) if record else review_instances.get_review_instance(event_key)
    if not instance:
        return _error("Review instance not found.", 404)
    if str(instance.get('learner_id')) != str(owner_id):
        return _error("Review instance not found for this learner.", 404)
    definition = review_instances.review_instance_form_definition(instance)
    if not definition['template']['visibleTo'].get('employer', False):
        return _error("This review is not visible to the employer.", 403)
    if request.method == "GET":
        if as_pdf:
            from curriculum_api.review_pdf import learner_information, mcm_pdf_response
            return mcm_pdf_response(definition, learner_information(learner))
        response = JsonResponse(definition)
        response["Cache-Control"] = "private, no-store"
        return response
    try:
        payload = _parse_body(request)
    except (TypeError, ValueError, ValidationError):
        return _error("Invalid JSON body.", 400)

    # Saving the Employer's own answers to whichever fields the Review's
    # Curriculum template opted the Employer into answering -- a distinct
    # shape from the signature POST below, so an existing sign-off caller
    # (which never sends `answers`) is unaffected.
    if isinstance(payload.get('answers'), dict):
        try:
            updated = review_instances.save_review_instance_answers_for_role(
                instance, payload['answers'], 'employer',
                actor=_s(getattr(request.login_account, 'email', '')) or 'employer',
            )
        except PermissionError as exc:
            return _error(str(exc), 403)
        except ValueError as exc:
            return _error(str(exc), 409)
        return JsonResponse(updated)

    signature = _s(payload.get('signature'))
    name = _s(payload.get('name'))
    if not signature.startswith('data:image/'):
        return _error("A valid employer signature is required.", 400)
    if not name:
        return _error("Name is required when signing.", 400)
    try:
        updated = review_instances.record_review_instance_signature(
            instance, 'employer', signed_by=_s(getattr(request.login_account, 'email', '')),
            signed_name=name, signature=signature,
            actor=_s(getattr(request.login_account, 'email', '')) or 'employer',
        )
    except ValueError as exc:
        return _error(str(exc), 409)
    return JsonResponse(updated)


@csrf_exempt
@employer_or_staff()
def employer_enrolment_review(request, employer_id, kind, learner_id, event_key):
    """One legacy Enrolment_Reviews document, read by the learner's employer.

    Backs "Show document" on a signed review. The learner-side read
    (review_form.enrolment_review_form) is limited to the learner and staff, and
    its GET stamps started_at, so the employer gets this read-only view instead,
    limited to the reviews their signing list offers them.
    """
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    employer, err = _employer_or_404(employer_id)
    if err:
        return err
    try:
        learner, review, event, failure = _lookup_enrolment_review(kind, learner_id, event_key)
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)
    if failure is not None:
        return failure
    if learner.employer_id != employer.pk:
        return _error("That learner does not belong to this employer.", 403)
    # Same filter as _review_signing_rows: a review that wants no employer
    # sign-off (the RPL review) is not the employer's to read.
    if not employer_signature_required(review):
        return _error("Review not found.", 404)
    return JsonResponse(_serialize_enrolment_review(review, learner, event))


@csrf_exempt
@employer_or_staff()
def employer_portal_learner_plan(request, employer_id, kind, learner_id):
    """The learner's own training plan, hours and KSBs — for their employer.

    Uses the read-only content projection, without activation, hydration,
    identity repair or snapshot persistence on an employer GET.

    The employer-owns-this-learner check is the same one employer_portal_learner
    makes — the payload is richer, so the guard matters more, not less.
    """
    if request.method != "GET":
        return _error("Method not allowed.", 405)

    employer, err = _employer_or_404(employer_id)
    if err:
        return err

    model = SOURCE_MODELS.get(kind)
    if model is None:
        return _error(f"Unknown kind: {kind!r}.", 404)

    try:
        learner = model.all_learners.get(pk=learner_id)
    except model.DoesNotExist:
        return _error("Learner not found.", 404)
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)

    if learner.employer_id != employer.pk:
        return _error("That learner does not belong to this employer.", 403)

    from .employer_learning import read_plan
    from old_otjh.service import ServiceError

    try:
        response = JsonResponse(read_plan(learner))
    except ServiceError as exc:
        return _error(str(exc), exc.status)
    except DatabaseError:
        logger.exception("employer_portal_learner_plan: projection unavailable")
        return _error("Could not load this learner's plan. Please try again.", 503)
    response['Cache-Control'] = 'private, no-store'
    return response


#: The learner-dashboard reads behind the employer's Overview tab. Each part is
#: served by the same reader as the learner's own endpoint (overview-week,
#: training-plan-dashboard, monthly-logs, profile-photo), so the employer sees
#: the learner's timeline and progress rather than a second calculation of them.
OVERVIEW_PARTS = frozenset({"week", "schedule", "contract", "hours", "photo", "attendance", "dashboard", "training"})

#: Attendance-register totals the dashboard's Attendance figure needs. The
#: employer gets the counts only, never the per-session register behind them.
_ATTENDANCE_FIELDS = ("sessions", "present", "absent", "late", "attendanceRate")

#: Monthly-log fields the dashboard's hours figures need. The employer gets the
#: per-month totals only, never the activity rows or signatures behind them.
_HOURS_MONTH_FIELDS = ("month", "source", "training_plan_target", "not_accepted_hours", "actual_hours")


def _without_meeting_links(payload):
    """Drop join and booking links: the employer's view is read-only.

    Teaching-session join URLs, review meeting links and the coach's booking
    link are the learner's to use. The cards render without them.
    """
    return {
        **payload,
        "sessions": [{**session, "joinUrl": None} for session in payload.get("sessions") or []],
        "reviews": [{**review, "meetingLink": None} for review in payload.get("reviews") or []],
        "coach": {**(payload.get("coach") or {}), "bookingUrl": None},
    }


def _overview_part(part, learner, kind=None):
    if part == "week":
        from .overview_week import read_week

        # Match the coach overview's canonical whole-programme metrics while
        # retaining the employer ownership gate around this endpoint.
        return read_week(learner, dashboard_kind=kind)
    if part in ("schedule", "contract"):
        from .employer_learning import read_schedule
        return read_schedule(learner, 'contract' if part == 'contract' else 'overview')
    if part == "attendance":
        from .attendance import _summarize_attendance
        from .attendance_lectures import lecture_register

        # The same register the learner's dashboard and the coach case file
        # count, so all three show one figure. Timetabled sessions are not
        # attendance: a class with no register entry is neither attended nor
        # missed. Read-only: catch-up outcomes are settled by the learner's
        # own reads, not by their employer viewing the page.
        summary = _summarize_attendance(lecture_register(learner))
        return {"attendance": {key: summary[key] for key in _ATTENDANCE_FIELDS} if summary else None}

    from old_otjh import service as old_service
    from . import monthly_log_sources, monthly_logs

    record = old_service.resolve_record(learner.pk)
    summary = monthly_logs.summary_data(
        {**record, "_profile": monthly_log_sources.profile(learner.pk), "_view_as": True},
        include_open=True,
    )
    return {
        "learner": {key: summary["learner"].get(key) for key in ("aptem_id", "planned_end_date")},
        "months": [{key: month.get(key) for key in _HOURS_MONTH_FIELDS} for month in summary["months"]],
        "training_plan_totals": summary.get("training_plan_totals"),
    }


@csrf_exempt
@employer_or_staff()
def employer_portal_learner_overview(request, employer_id, kind, learner_id, part):
    """The learner's dashboard timeline and progress — for their employer.

    The learner's own dashboard endpoints admit only the learner and staff, so
    an employer signed in to their portal could not load them. This serves the
    same payloads behind the same employer-owns-this-learner check as the rest
    of the portal. GET only, with meeting and booking links removed.
    """
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    if part not in OVERVIEW_PARTS:
        return _error("Unknown overview part.", 404)

    employer, err = _employer_or_404(employer_id)
    if err:
        return err

    model = SOURCE_MODELS.get(kind)
    if model is None:
        return _error(f"Unknown kind: {kind!r}.", 404)

    try:
        learner = model.all_learners.get(pk=learner_id)
    except model.DoesNotExist:
        return _error("Learner not found.", 404)
    except DatabaseError:
        logger.exception("employer_portal_learner_overview: learner lookup failed")
        return _error("Could not load this learner. Please try again.", 503)

    if learner.employer_id != employer.pk:
        return _error("That learner does not belong to this employer.", 403)

    if part == "photo":
        from azure.core.exceptions import AzureError
        from django.http import HttpResponse
        from .profile_photo import photo_bytes

        try:
            content = photo_bytes(learner.pk)
        except (AzureError, RuntimeError):
            logger.warning("Profile photo storage unavailable for learner %s", learner.pk)
            return _error("The photo could not be loaded.", 503)
        response = HttpResponse(content, content_type="image/jpeg") if content is not None else HttpResponse(status=204)
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    from old_otjh.service import ServiceError

    try:
        if part in ('dashboard', 'training'):
            from .employer_learning import read_dashboard
            payload = read_dashboard(learner, kind, 'overview' if part == 'dashboard' else 'training', request)
        else:
            payload = _overview_part(part, learner, kind)
    except ServiceError as exc:
        return _error(str(exc), exc.status)
    except LookupError as exc:
        return _error(str(exc), 404)
    except DatabaseError:
        logger.exception("employer_portal_learner_overview: %s unavailable for %s", part, learner.pk)
        return _error("Could not load this learner's progress. Please try again.", 503)
    response = JsonResponse(payload)
    response["Cache-Control"] = "private, no-store"
    return response


def _owned_learner(employer_id, kind, learner_id):
    """(learner, None) when this learner belongs to this employer, else (None, error response)."""
    employer, err = _employer_or_404(employer_id)
    if err:
        return None, err
    model = SOURCE_MODELS.get(kind)
    if model is None:
        return None, _error(f"Unknown kind: {kind!r}.", 404)
    try:
        learner = model.all_learners.get(pk=learner_id)
    except model.DoesNotExist:
        return None, _error("Learner not found.", 404)
    except DatabaseError:
        logger.exception("_owned_learner: learner lookup failed")
        return None, _error("Could not load this learner. Please try again.", 503)
    if learner.employer_id != employer.pk:
        return None, _error("That learner does not belong to this employer.", 403)
    return learner, None


@employer_or_staff()
def employer_document_preview(request, employer_id, kind, learner_id, document_kind, document_id):
    """Read an owned, issued document without deriving or issuing new content."""
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    learner, err = _owned_learner(employer_id, kind, learner_id)
    if err is not None:
        return err
    from .models import ApprenticeshipAgreement, TrainingPlanDocument, WrittenAgreement
    from .apprenticeship_agreement import _agreement_json
    from .training_plan_document import _document_json as training_plan_json
    from .written_agreement import _document_json as written_agreement_json

    snapshots = {
        "agreement": (ApprenticeshipAgreement, _agreement_json),
        "training-plan": (TrainingPlanDocument, training_plan_json),
        "written-agreement": (WrittenAgreement, written_agreement_json),
    }
    try:
        if document_kind in snapshots:
            model, serialize = snapshots[document_kind]
            document = model.objects.filter(
                pk=document_id, learner_id=learner.pk, learner_kind=kind, status=model.STATUS_ACTIVE,
            ).first()
            if document is None:
                return _error("Document not found.", 404)
            payload = {"kind": document_kind, "document": serialize(document)}
        elif document_kind == "document":
            from enrolment_api.documents import SIGNING_PARTIES, _conn, azure_configured, get_download_sas
            with _conn().cursor() as cursor:
                cursor.execute(
                    'select "Container", "Blob_name", "Doc_type" from enrolment."Enrolment_Documents" '
                    'where id = %s and "Learner_kind" = %s and "Learner_id" = %s',
                    [str(document_id), kind, learner.pk],
                )
                row = cursor.fetchone()
            if not row or "employer" not in SIGNING_PARTIES.get(row[2], ("employer",)):
                return _error("Document not found.", 404)
            if not azure_configured():
                return _error("Document storage is not configured.", 503)
            payload = {"kind": "document", "url": get_download_sas(row[0], row[1])}
        else:
            return _error("Unknown document kind.", 404)
    except (ValueError, ValidationError):
        return _error("Document not found.", 404)
    except DatabaseError:
        logger.exception("employer_document_preview: document read unavailable")
        return _error("Could not load the document. Please try again.", 503)
    response = JsonResponse(payload)
    response["Cache-Control"] = "private, no-store"
    return response


def _employer_progress_definition(event, learner, profile, template_cache):
    """Read an owned occurrence or its template preview without creating it."""
    from curriculum_api import reviews

    instance_id = _s(event.get("reviewInstanceId"))
    template_id = _s(event.get("reviewTemplateId"))
    instance = review_instances.get_review_instance(instance_id) if instance_id else None
    if instance is None and template_id and profile is not None:
        occurrence = event.get("occurrenceNumber") or event.get("sequence")
        instance = review_instances.find_review_instance(template_id, str(profile.pk), occurrence)
    if instance is not None:
        owner_id = profile.pk if profile is not None else learner.pk
        if str(instance.get("learner_id")) != str(owner_id):
            return None
        definition = review_instances.review_instance_form_definition(instance)
    elif template_id:
        if template_id not in template_cache:
            template = reviews.get_review_template_row(template_id, include_deleted=True)
            template_cache[template_id] = review_instances.build_definition_snapshot(template) if template else None
        snapshot = template_cache[template_id]
        if snapshot is None:
            return None
        definition = {
            "instance": None, "occurrenceNumber": event.get("occurrenceNumber") or event.get("sequence"),
            "template": snapshot, "sections": snapshot["sections"],
            "signatures": {role: {"required": bool(snapshot["signatures"].get(role)), "signed": False}
                           for role in review_instances.SIGNATURE_ROLES},
        }
    else:
        return None
    return definition if definition["template"]["visibleTo"].get("employer", False) else None


def _employer_progress_review_rows(learner, event_key=None, month=None):
    """(rows, definitions, None) for the progress reviews this employer may read.

    One visibility rule serves the review list, the review document and the
    meeting recording: a review the employer cannot open is not one whose
    recording they can watch either.
    """
    from .calendar import _calendar_profile, coaching_events_for_learner

    try:
        profile = _calendar_profile(learner, learner.pk)
        # The dashboard's monthly summary needs only this month's occurrences.
        # Keep using the Learner calendar source, but avoid reading the learner's
        # full review history and unrelated future/past events for that card.
        events = coaching_events_for_learner(learner, profile, month=month) if month else coaching_events_for_learner(learner, profile)
        rows, definitions, template_cache = [], {}, {}
        for source in events:
            # Imported Gateway/support reviews also use the legacy progress-review
            # routing source; their original review type determines this list.
            category = review_event_category(source)
            is_progress = category == "progress-review"
            if source.get("reviewTemplateId") and not source.get("reviewTypeCode"):
                is_progress = False
            if event_key is not None and event_key not in {source.get("id"), source.get("eventKey")}:
                continue
            migrated_state = None
            if source.get("migratedForm") and category in {"mcr", "progress-review", "review"}:
                overlay = ImportedReviewInstance.objects.filter(
                    event_key=source["eventKey"], learner_id=getattr(profile, "pk", learner.pk),
                ).first()
                if overlay is None:
                    continue
                if category == "review" and not is_personal_support_form(overlay):
                    continue
                migrated_state = signature_states(overlay)["employer"]
                if category in {"mcr", "review"}:
                    is_progress = bool(migrated_state["required"])
            if not is_progress:
                continue
            event = dict(source)
            definition = None
            if event.get("reviewTemplateId") or event.get("reviewInstanceId"):
                definition = _employer_progress_definition(event, learner, profile, template_cache)
                if definition is None:
                    continue
                state = definition["signatures"].get("employer", {})
                instance = definition.get("instance") or {}
                event.update({"employerSigned": bool(state.get("signed")),
                              "employerSignatureRequired": bool(state.get("required"))})
                if instance:
                    event["reviewInstanceId"] = instance["id"]
                    event["status"] = instance["status"]
                definitions[event["eventKey"]] = definition
            elif migrated_state is not None:
                event.update({"employerSigned": bool(migrated_state["signed"]), "employerSignatureRequired": bool(migrated_state["required"])})
            # Only the owned, employer-visible PR appointment can be joined.
            # Keep the calendar reader's sync verdict (and its empty link on failure).
            booking_status = _s(source.get("bookingStatus") or source.get("status")).lower().replace("_", "-").replace(" ", "-")
            review_status = _s(event.get("status")).lower().replace("_", "-").replace(" ", "-")
            imported = event.get("importedReview") or {}
            can_join = (
                category == "progress-review" and event.get("scheduledDate")
                and booking_status in {"scheduled", "in-progress"}
                and review_status not in {"completed", "awaiting-signature", "cancelled", "deleted", "superseded"}
                and (not imported or (event.get("calendarEventKey") and not imported.get("completedDate")))
            )
            event["meetingLink"] = (source.get("meetingLink") or "") if can_join else ""
            rows.append(event)
        rows.sort(key=lambda event: (event.get("date") or event.get("targetDate") or "", event.get("sequence") or 0, event["eventKey"]))
    except DatabaseError:
        logger.exception("employer_portal_progress_reviews: review read unavailable for %s", learner.pk)
        return None, None, _error("Could not load progress reviews. Please try again.", 503)
    return rows, definitions, None


@csrf_exempt
@employer_or_staff()
def employer_portal_progress_reviews(request, employer_id, kind, learner_id, event_key=None, as_pdf=False):
    """Same programme review source as the learner, with employer visibility."""
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    summary_month = request.GET.get("summary_month")
    if summary_month and (len(summary_month) != 7 or summary_month[4] != "-"
                          or not summary_month[:4].isdigit() or int(summary_month[:4]) < 1
                          or not summary_month[5:].isdigit()
                          or not 1 <= int(summary_month[5:]) <= 12):
        return _error("Invalid review summary month.", 400)
    learner, err = _owned_learner(employer_id, kind, learner_id)
    if err is not None:
        return err
    rows, definitions, err = _employer_progress_review_rows(learner, event_key, month=summary_month)
    if err is not None:
        return err
    if event_key is not None:
        if not rows:
            return _error("Progress review not found for this learner.", 404)
        definition = definitions.get(rows[0]["eventKey"])
        if as_pdf:
            from curriculum_api.review_pdf import learner_information, mcm_pdf_response, historical_pdf_response
            information = learner_information(learner)
            if definition is not None:
                response = mcm_pdf_response(definition, information)
            elif rows[0].get("importedReview") and not rows[0].get("migratedForm"):
                from .aptem_review_pdf import original_review_pdf
                review = rows[0]["importedReview"]
                response = historical_pdf_response(review, information, identifier=review["id"],
                                                   original_content=original_review_pdf(review))
            else:
                return _error("A review document is not available yet.", 409)
        else:
            response = JsonResponse({"event": rows[0], "definition": definition})
    elif summary_month:
        # The home card and journey markers need dates/status only. Review
        # definitions and imported review bodies remain on the full endpoint.
        fields = ("id", "eventKey", "title", "source", "importedReviewType", "reviewTypeCode",
                  "sequence", "status", "bookingStatus", "date", "targetDate", "scheduledDate",
                  "scheduledTime", "employerSigned", "employerSignatureRequired")
        response = JsonResponse({"events": [{key: row.get(key) for key in fields} for row in rows]})
    else:
        response = JsonResponse({"events": rows, "definitions": definitions})
    response["Cache-Control"] = "private, no-store"
    return response


def _employer_review_meeting(employer_id, kind, learner_id, event_key):
    """(record, None) for a review meeting this employer may watch, else (None, error).

    Ownership and visibility are the portal's existing rule: the learner must
    belong to this employer, and the event must be one of the progress reviews
    their portal already lists.
    """
    learner, err = _owned_learner(employer_id, kind, learner_id)
    if err is not None:
        return None, err
    rows, _definitions, err = _employer_progress_review_rows(learner, event_key)
    if err is not None:
        return None, err
    row = next((item for item in rows if event_key in {item.get("id"), item.get("eventKey")}), None)
    if row is None:
        return None, _error("Progress review not found for this learner.", 404)
    from .calendar import _learner_calendar_record

    record = _learner_calendar_record(kind, learner.pk, row.get("calendarEventKey") or row.get("eventKey") or event_key)
    if record is None:
        return None, _error("This review has no meeting of its own.", 404)
    return record, None


@employer_or_staff()
def employer_portal_review_artifacts(request, employer_id, kind, learner_id, event_key):
    """The meeting recordings for a review this employer can already open.

    Employers watch the formal recording only: transcripts, attendance rows and
    meeting summaries stay with staff. Reading this panel must not move the
    review on or write attendance either, so the coach/learner status
    transition is deliberately not applied -- an employer opening a page is not
    evidence that anybody attended.
    """
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    record, err = _employer_review_meeting(employer_id, kind, learner_id, event_key)
    if err is not None:
        return err
    from .imported_meeting_resources import is_imported_meeting, stored_resources
    if is_imported_meeting(record):
        return stored_resources(record)
    from coach_api.views import fetch_coach_meeting_graph_snapshot, persist_coach_meeting_snapshots

    snapshot, error_payload, status_code = fetch_coach_meeting_graph_snapshot(record)
    if error_payload:
        return JsonResponse(error_payload, status=status_code)
    storage = persist_coach_meeting_snapshots(
        record,
        artifacts=snapshot["artifacts"],
        attendance_reports=snapshot["attendanceReports"],
        attendance_tracker=snapshot["attendanceTracker"],
    )
    recordings = [artifact for artifact in snapshot["artifacts"]
                  if _s(artifact.get("artifact_type")).lower() == "recording"]
    response = JsonResponse({
        "artifacts": recordings,
        # Attendance rows and AI meeting drafts never cross the employer
        # boundary; the formal review answers are returned by the review reads.
        "meetingSummary": None,
        "errors": snapshot["errors"],
        "partial": snapshot["partial"],
        "storage": storage,
    }, status=status_code)
    response["Cache-Control"] = "private, no-store"
    return response


@employer_or_staff()
def employer_portal_review_artifact_content(request, employer_id, kind, learner_id, event_key,
                                            artifact_type, artifact_id):
    """Play or download one recording of an employer-visible review meeting."""
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    if _s(artifact_type).lower() != "recording":
        return _error("Only meeting recordings are available to employers.", 403)
    record, err = _employer_review_meeting(employer_id, kind, learner_id, event_key)
    if err is not None:
        return err
    from .imported_meeting_resources import is_imported_meeting, stored_recording
    if is_imported_meeting(record):
        return stored_recording(request, record, artifact_id)
    from coach_api.views import coach_meeting_artifact_content_response

    # The archived copy is keyed by the calendar event, not by the review key
    # the employer's portal uses to address it.
    return coach_meeting_artifact_content_response(request, record, record.event_key, artifact_type, artifact_id)


def _monthly_log_urls(payload, employer_id, kind, learner_id):
    """Keep report materials and evidence behind the portal ownership check."""
    import re

    prefix = f"/learner_api/employer-portal/{employer_id}/learner/{kind}/{learner_id}/monthly-logs/"
    source = f"/learner_api/monthly-logs/{learner_id}/"
    if isinstance(payload, dict):
        return {key: _monthly_log_urls(value, employer_id, kind, learner_id) for key, value in payload.items()}
    if isinstance(payload, list):
        return [_monthly_log_urls(value, employer_id, kind, learner_id) for value in payload]
    if isinstance(payload, str) and payload.startswith(source):
        suffix = payload[len(source):]
        if re.fullmatch(r"(?:documents/[\w-]+|canonical-documents/\d+|\d{4}-\d{2}/activities/\d+/materials/\d+)/", suffix):
            return prefix + suffix
    return payload


@csrf_exempt
@employer_or_staff()
def employer_portal_monthly_logs(request, employer_id, kind, learner_id, month=None,
                                 row_id=None, material_id=None, file_id=None, document_source=None):
    """The learner Monthly Logs reader, scoped to this employer and GET only."""
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    learner, err = _owned_learner(employer_id, kind, learner_id)
    if err is not None:
        return err

    from old_otjh.service import ServiceError
    from . import canonical_learning, monthly_logs
    from .journal_sources import learner_journal_reads
    from .subject_content import ContentUnavailable

    def read_report(request):
        profile = canonical_learning.require_profile(learner.pk)
        record = {**profile, "id": learner.pk, "_profile": profile,
                  "_canonical_profile": profile, "_view_as": True}
        if file_id is not None:
            reader = monthly_logs.canonical_document_response if document_source == "canonical" else monthly_logs.document_response
            return reader(learner.pk, file_id)
        if material_id is not None:
            return monthly_logs.material_response(record, month, row_id, material_id)
        if row_id is not None:
            response = monthly_logs.content_response(record, month, row_id)
            payload = json.loads(response.content)
        elif month is not None:
            payload = monthly_logs.detail_data(record, month, include_open=True)
        else:
            payload = {**monthly_logs.summary_data(record, include_open=True), "read_only": True, "csrf_token": ""}
        return JsonResponse(_monthly_log_urls(payload, employer_id, kind, learner_id))

    try:
        with learner_journal_reads():
            response = read_report(request)
    except ServiceError as exc:
        return _error(str(exc), exc.status)
    except ContentUnavailable as exc:
        return _error(str(exc), 503)
    except DatabaseError:
        logger.exception("employer_portal_monthly_logs: logs unavailable for %s", learner.pk)
        return _error("Could not load this learner's monthly logs. Please try again.", 503)
    response["Cache-Control"] = "private, no-store"
    response["Vary"] = "Cookie"
    return response


#: A learner's handed-in assignments: every status except an unsent draft.
_SUBMITTED_ASSIGNMENTS_SQL = """
    select activity_id, activity_title, module_title, week_title, status, submitted_at, date_completed
    from "Learner"."learning_reflection_submissions"
    where learner_kind = %s and learner_id = %s and activity_type = 'assignment' and status <> 'draft'
    order by coalesce(submitted_at, date_completed) desc nulls last, activity_title
"""

#: Scanned-clean uploads, keyed by the activity they were uploaded to.
_ASSIGNMENT_FILES_SQL = """
    select id, original_filename, section_ref, uploaded_at
    from "Learner"."evidence_files"
    where learner_kind = %s and learner_id = %s and status = 'approved' and section_ref = any(%s)
    order by uploaded_at
"""


def _iso_any(value):
    return value.isoformat() if hasattr(value, "isoformat") else (_s(value) or None)


def _learner_assignments(kind, learner):
    """The learner's submitted assignments, LMS then legacy Aptem, without marks or feedback.

    The employer sees what was handed in, when, and where it stands; the tutor's
    score, written feedback and the Aptem assessment report are left out.
    """
    from django.db import connections

    from . import legacy_assignments

    with connections["enrolment"].cursor() as cur:
        cur.execute(_SUBMITTED_ASSIGNMENTS_SQL, [kind, str(learner.pk)])
        submissions = cur.fetchall()
        files_by_activity = {}
        activity_ids = [row[0] for row in submissions]
        if activity_ids:
            cur.execute(_ASSIGNMENT_FILES_SQL, [kind, str(learner.pk), activity_ids])
            for file_id, name, section_ref, uploaded_at in cur.fetchall():
                files_by_activity.setdefault(section_ref, []).append(
                    {"source": "lms", "id": str(file_id), "name": _s(name) or "Uploaded file"},
                )

    items = [{
        "id": f"lms:{activity_id}",
        "source": "lms",
        "title": _s(title) or "Assignment",
        "moduleTitle": _s(module),
        "weekTitle": _s(week),
        "status": _s(status),
        "submittedAt": _iso_any(submitted_at) or _iso_any(completed),
        "files": files_by_activity.get(activity_id, []),
    } for activity_id, title, module, week, status, submitted_at, completed in submissions]

    for row in legacy_assignments.classified_rows(kind, learner.pk):
        submission = legacy_assignments.classified_submission(row, kind, learner.pk)
        documents = submission["legacyAssignment"]["documents"]
        items.append({
            "id": submission["id"],
            "source": "aptem",
            "title": submission["activityTitle"] or "Assignment",
            "moduleTitle": submission["moduleTitle"] or "",
            "weekTitle": "",
            "status": submission["status"],
            "submittedAt": submission["submittedAt"] or submission["dateCompleted"] or None,
            # The learner's own upload only; the "report" part is the assessor's.
            "files": [
                {"source": "aptem", "id": str(doc["evidenceId"]), "activityId": submission["activityId"], "name": doc["name"]}
                for doc in documents if doc["part"] == "file"
            ],
        })
    return items


@csrf_exempt
@employer_or_staff()
def employer_portal_learner_assignments(request, employer_id, kind, learner_id):
    """Every assignment this learner has handed in, with their uploaded files."""
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    learner, err = _owned_learner(employer_id, kind, learner_id)
    if err:
        return err
    try:
        items = _learner_assignments(kind, learner)
    except DatabaseError:
        logger.exception("employer_portal_learner_assignments: unavailable for %s", learner.pk)
        return _error("Could not load assignments. Please try again.", 503)
    response = JsonResponse({"assignments": items})
    response["Cache-Control"] = "private, no-store"
    return response


@csrf_exempt
@employer_or_staff()
def employer_portal_assignment_file(request, employer_id, kind, learner_id, file_id):
    """A short-lived link to one uploaded assignment file.

    Only a scanned-clean file, uploaded by this learner, to an assignment they
    have handed in — an evidence upload with no submitted assignment behind it
    is not the employer's to open.
    """
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    learner, err = _owned_learner(employer_id, kind, learner_id)
    if err:
        return err
    from django.conf import settings
    from django.db import connections

    from .evidence_storage import azure_configured, get_download_sas

    if not azure_configured():
        return _error("Document storage is not configured.", 503)
    try:
        with connections["enrolment"].cursor() as cur:
            cur.execute(
                """
                select f.blob_name, f.original_filename
                from "Learner"."evidence_files" f
                where f.id = %s and f.learner_kind = %s and f.learner_id = %s and f.status = 'approved'
                  and exists (
                    select 1 from "Learner"."learning_reflection_submissions" s
                    where s.learner_kind = f.learner_kind and s.learner_id = f.learner_id
                      and s.activity_type = 'assignment' and s.status <> 'draft'
                      and s.activity_id = f.section_ref
                  )
                """,
                [str(file_id), kind, str(learner.pk)],
            )
            row = cur.fetchone()
    except DatabaseError:
        logger.exception("employer_portal_assignment_file: lookup failed")
        return _error("Could not open the file. Please try again.", 503)
    if not row:
        return _error("File not found.", 404)
    blob_name, name = row
    try:
        url = get_download_sas(settings.AZURE_APPROVED_CONTAINER, blob_name, filename=_s(name) or None)
    except Exception:
        logger.warning("employer_portal_assignment_file: SAS failed for %s", file_id)
        return _error("Could not open the file. Please try again.", 503)
    response = JsonResponse({"url": url})
    response["Cache-Control"] = "private, no-store"
    return response


@csrf_exempt
@employer_or_staff()
def employer_portal_legacy_assignment_file(request, employer_id, kind, learner_id, evidence_id):
    """A short-lived link to a legacy (Aptem) assignment upload — the learner's file, never the assessor's report."""
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    learner, err = _owned_learner(employer_id, kind, learner_id)
    if err:
        return err
    from . import evidence_storage, legacy_assignments

    activity_id = _s(request.GET.get("activityId"))
    if not activity_id:
        return _error("activityId is required.", 400)
    if not evidence_storage.azure_configured():
        return _error("Document storage is not configured.", 503)
    try:
        rows = legacy_assignments.classified_rows(kind, learner.pk, activity_id)
    except DatabaseError:
        logger.exception("employer_portal_legacy_assignment_file: lookup failed")
        return _error("Could not open the file. Please try again.", 503)
    if not rows or str(rows[0]["evidence_id"]) != str(evidence_id) or not rows[0].get("file_blob"):
        return _error("File not found.", 404)
    document = rows[0]
    try:
        url = evidence_storage.get_download_sas(
            "fetch-aptem-evidences", document["file_blob"], filename=_s(document["evidence_name"]) or None,
        )
    except Exception:
        logger.warning("employer_portal_legacy_assignment_file: SAS failed for %s", evidence_id)
        return _error("Could not open the file. Please try again.", 503)
    response = JsonResponse({"url": url})
    response["Cache-Control"] = "private, no-store"
    return response


def _performance(kind, learner):
    """A progress summary for the employer.

    Deliberately a summary, not the learner's full workspace: an employer sees
    how their apprentice is tracking, not every quiz answer. Counts come from the
    active learner profile's progress log, which is the same source the learner's
    own dashboard reads.
    """
    summary = {
        "quizzesTaken": 0,
        "quizzesPassed": 0,
        "averageScore": None,
        "componentsCompleted": 0,
        "ksbsEvidenced": 0,
        "completedHours": None,
        "lastActivityAt": None,
    }

    try:
        from .learner_detail import _active_profile_for_source

        profile = _active_profile_for_source(learner, learner.pk)
    except DatabaseError:
        logger.exception("_performance: profile lookup failed")
        return summary
    if profile is None:
        return summary

    progress = profile.training_plan_progress
    progress = progress if isinstance(progress, list) else []

    quizzes = [r for r in progress if isinstance(r, dict) and r.get("kind", "quiz") == "quiz"]
    others = [r for r in progress if isinstance(r, dict) and r.get("kind", "quiz") != "quiz"]

    scores = []
    passed = 0
    for attempt in quizzes:
        score = attempt.get("scorePercent", attempt.get("score"))
        try:
            value = float(score)
        except (TypeError, ValueError):
            continue
        scores.append(value)
        if attempt.get("passed") is True or value >= 50:
            passed += 1

    dates = [
        _s(r.get("completedAt") or r.get("submittedAt") or r.get("date"))
        for r in progress if isinstance(r, dict)
    ]
    dates = sorted(d for d in dates if d)

    ksbs = profile.ksbs if isinstance(profile.ksbs, list) else []

    summary.update({
        "quizzesTaken": len(quizzes),
        "quizzesPassed": passed,
        "averageScore": round(sum(scores) / len(scores), 1) if scores else None,
        "componentsCompleted": len(others),
        "ksbsEvidenced": len([k for k in ksbs if isinstance(k, dict) and k.get("evidenced")]),
        "completedHours": _s(getattr(profile, "completed_hours", "")) or None,
        "lastActivityAt": dates[-1] if dates else None,
    })
    return summary


