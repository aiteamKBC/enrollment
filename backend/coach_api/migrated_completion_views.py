"""Dedicated lifecycle endpoints for Aptem migrated reviews."""
import json

from django.db import connections, transaction
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from coach_api.auth import authenticated_coach_email, coach_access_required
from coach_api.migrated_completion import complete, ensure_document, required_roles, sign, submit
from coach_api.migrated_review_pdf import stored_pdf_response
from coach_api.migrated_reviews import review_family
from coach_api.migrated_respondents import merge_party_answers, writable_keys
from coach_api import migrated_monthly_log
from coach_api.models import CoachCalendarEvent, ImportedReviewInstance, MigratedReviewDocument
from coach_api.migrated_summary_binding import AnswerConflict, check_answer_version, record_answer_edit
from coach_api.migrated_reopening import check_revision
from coach_api.migrated_template_sync import TemplateSyncConflict, active_answers, synchronize_definition_locked
from learner_api.models import EnrolmentUser, LearnerProfile
from .review_sources import booking_for_overlay
from login.permissions import authenticate_request


def _payload(request):
    try:
        value = json.loads(request.body or b"{}")
    except (UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _coach_review(request, review_id, *, pdf_only=False):
    from coach_api.views import _imported_review_definition
    if not review_id.startswith("imported-review:"):
        return None, None
    owner = authenticated_coach_email(request).strip().casefold()
    definition = _imported_review_definition(owner, review_id, **({"pdf_only": True} if pdf_only else {}))
    return owner, definition if definition and definition.get("migratedForm") else None


def _locked_overlay(owner, definition, *, lock_calendar=False):
    from coach_api.views import _owned_migrated_overlay
    return _owned_migrated_overlay(owner, definition, lock=True, lock_calendar=lock_calendar)


def _response(owner, review_id):
    from coach_api.views import _imported_review_definition
    return JsonResponse(_imported_review_definition(owner, review_id))


def _mirror_status(overlay):
    """Mirror LMS local status on only this review's existing calendar row."""
    update = {"status": overlay.status}
    if overlay.status == ImportedReviewInstance.STATUS_COMPLETED:
        update["review_completed_at"] = overlay.completed_at
    calendar = booking_for_overlay(overlay, lock=True)
    if calendar:
        CoachCalendarEvent.objects.filter(pk=calendar.pk).update(**update)


@transaction.atomic
def _sign_and_complete(overlay, role, *, account, signature):
    """Caller holds learner/calendar/overlay locks; all writes succeed together.

    The savepoint also rolls back the signature if completion fails and the
    endpoint catches the validation error to return a conflict response.
    """
    sign(overlay, role, account=account, signature=signature)
    signed = set(overlay.migrated_signatures.values_list("role", flat=True))
    if not (set(required_roles(overlay)) - signed):
        complete(overlay)
        _mirror_status(overlay)


@coach_access_required
@require_POST
def migrated_review_submit(request, review_id):
    owner, definition = _coach_review(request, review_id)
    if not definition or definition.get("readOnly"):
        return JsonResponse({"detail": "Migrated review is unavailable or read-only."}, status=409)
    payload = _payload(request)
    if payload is None or ("answers" in payload and not isinstance(payload["answers"], dict)):
        return JsonResponse({"detail": "answers must be an object."}, status=400)
    with transaction.atomic():
        overlay = _locked_overlay(owner, definition, lock_calendar=True)
        if not overlay:
            return JsonResponse({"detail": "Migrated review association mismatch."}, status=409)
        try:
            check_answer_version(overlay, payload)
            if synchronize_definition_locked(overlay, definition):
                return JsonResponse({"detail": "The review template changed. Reopen the review and check its latest questions before submitting. Your saved answers are unchanged.", "code": "template_sync_changed"}, status=409)
            answers = payload.get("answers", active_answers(overlay.template_snapshot, overlay.answers))
            record_answer_edit(overlay, answers, actor=owner,
                               edited_fields=payload.get("editedFields") if isinstance(payload.get("editedFields"), list) else ())
            submit(overlay, answers)
        except TemplateSyncConflict as exc:
            return JsonResponse({"detail": str(exc), "templateSync": exc.response()}, status=409)
        except AnswerConflict as exc:
            return JsonResponse({"detail": str(exc), "code": "ANSWER_CONFLICT"}, status=409)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)
        overlay.save(update_fields=["meeting_intelligence"])
        _mirror_status(overlay)
    return _response(owner, review_id)


@coach_access_required
@require_POST
def migrated_review_coach_sign(request, review_id):
    owner, definition = _coach_review(request, review_id)
    if not definition:
        return JsonResponse({"detail": "Migrated review not found."}, status=404)
    account = request.login_account
    if account.role != "staff" or account.email.strip().casefold() != owner:
        return JsonResponse({"detail": "Only the assigned coach can sign."}, status=403)
    payload = _payload(request)
    if payload is None:
        return JsonResponse({"detail": "Invalid JSON object."}, status=400)
    with transaction.atomic():
        overlay = _locked_overlay(owner, definition, lock_calendar=True)
        if not overlay:
            return JsonResponse({"detail": "Migrated review association mismatch."}, status=409)
        try:
            check_revision(overlay, payload)
            _sign_and_complete(overlay, "advisor", account=account, signature=payload.get("signature"))
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=409)
    return _response(owner, review_id)


def _party_overlay(review_id, account, *, lock=False, lock_calendar=False):
    if not review_id.startswith("imported-review:") or account.role not in {"learner", "employer"}:
        return None
    if lock_calendar:
        if not lock:
            raise ValueError("Calendar locking requires an overlay lock.")
        owned = _party_overlay(review_id, account)
        if owned is None:
            return None
        # Match submission, rescheduling and reconciliation lock order.
        from coach_api.views import lock_learner_calendar
        lock_learner_calendar(owned.learner_id)
        booking_for_overlay(owned, lock=True)
        # Revalidate the participant's ownership after acquiring parent locks.
    # The template FK is nullable. Locking a row through select_related would
    # include an outer join, which PostgreSQL cannot lock with FOR UPDATE.
    query = ImportedReviewInstance.objects
    if lock:
        query = query.select_for_update()
    overlay = query.filter(event_key=review_id, source_review_id__isnull=False).first()
    if not overlay or not overlay.template_snapshot.get("sections"):
        return None
    from .migrated_completion import is_personal_support_form
    if (account.role == "employer" and is_personal_support_form(overlay)
            and "employer" not in required_roles(overlay)):
        return None
    profile = LearnerProfile.objects.filter(pk=overlay.learner_id).first()
    from .review_sources import enrolment_for_review_profile
    learner = enrolment_for_review_profile(profile)
    if not learner:
        return None
    if account.role == "learner" and account.subject_id != learner.pk:
        return None
    if account.role == "employer" and account.subject_id != learner.employer_id:
        return None
    from coach_api.views import get_learner_db_alias
    with connections[get_learner_db_alias()].cursor() as cursor:
        cursor.execute('SELECT status, completed_date FROM "Learner".reviews WHERE id = %s AND learner_id = %s', [overlay.source_review_id, overlay.learner_id])
        source = cursor.fetchone()
    if not source or str(source[0] or "").strip().casefold() == "completed" or source[1]:
        return None
    return overlay


@ensure_csrf_cookie
@require_GET
def migrated_review_party_csrf(request):
    if authenticate_request(request) is None:
        return JsonResponse({"detail": "Sign in to use this action."}, status=401)
    return JsonResponse({"csrfToken": get_token(request)})


@require_GET
def migrated_review_party_detail(request, review_id):
    account = authenticate_request(request)
    if account is None:
        return JsonResponse({"detail": "Sign in to view this review."}, status=401)
    overlay = _party_overlay(review_id, account)
    if not overlay:
        return JsonResponse({"detail": "Review not found for this account."}, status=404)
    definition = _party_definition(overlay, account)
    if definition is None:
        return JsonResponse({"detail": "Migrated form unavailable."}, status=404)
    return JsonResponse(definition)


def _party_definition(overlay, account):
    from coach_api.views import _imported_review_definition
    # _party_overlay already established learner/employer/source authority.
    # Historical ownership is not membership of the former Coach's caseload.
    profile = LearnerProfile.objects.filter(pk=overlay.learner_id).first()
    if profile is None:
        return None
    definition = _imported_review_definition(overlay.owner_email, overlay.event_key, learners=[profile])
    if not definition or not definition.get("migratedForm"):
        return None
    role = "participant" if account.role == "learner" else "employer"
    definition["readOnly"] = (bool(definition.get("readOnly"))
                              or overlay.status not in {"not-scheduled", "scheduled", "in-progress"}
                              or not writable_keys(overlay, role))
    definition["canCalculateProgress"] = False
    monthly_log = migrated_monthly_log.signing_option(overlay, account, definition)
    if monthly_log:
        definition["mcmMonthlyLog"] = monthly_log
    definition.pop("progressVersion", None)
    definition.pop("historicalReview", None)
    definition.pop("ragHistory", None)
    definition.pop("summaryBinding", None)
    if definition["readOnly"]:
        definition.pop("answerVersion", None)
    return definition


@require_POST
@csrf_protect
def migrated_review_party_answers(request, review_id):
    account = authenticate_request(request)
    if account is None:
        return JsonResponse({"detail": "Sign in to answer this review."}, status=401)
    if account.role not in {"learner", "employer"} or request.GET.get("viewAsCoach") or request.GET.get("viewAsLearner"):
        return JsonResponse({"detail": "This account cannot answer as a review participant."}, status=403)
    payload = _payload(request)
    if payload is None or not isinstance(payload.get("answers"), dict):
        return JsonResponse({"detail": "answers must be an object keyed by field id."}, status=400)
    role = "participant" if account.role == "learner" else "employer"
    with transaction.atomic():
        from .migrated_completion import is_personal_support_form, complete_personal_support_if_ready
        current = _party_overlay(review_id, account)
        overlay = _party_overlay(review_id, account, lock=True,
                                 lock_calendar=bool(current and is_personal_support_form(current)))
        if not overlay:
            return JsonResponse({"detail": "Review not found for this account."}, status=404)
        try:
            check_answer_version(overlay, payload)
            # Read-only party GETs never change the template. A write checks the
            # current managed rules under the same locks as coach saves.
            from coach_api.views import _imported_review_definition
            definition = _imported_review_definition(overlay.owner_email, overlay.event_key)
            if not definition or not definition.get("migratedForm") or definition.get("readOnly"):
                return JsonResponse({"detail": "This review is read-only."}, status=409)
            if synchronize_definition_locked(overlay, definition):
                return JsonResponse({"detail": "The review template changed. Reopen the review to check its latest questions before saving. Your unsaved answers have been kept on screen.", "code": "template_sync_changed"}, status=409)
            answers = merge_party_answers(overlay, payload["answers"], role)
            with transaction.atomic():
                record_answer_edit(overlay, active_answers(overlay.template_snapshot, answers),
                                   actor=account.email or str(account.pk), role=role, account_id=account.pk)
                overlay.answers = answers
                overlay.save(update_fields=["answers", "meeting_intelligence", "updated_at"])
                complete_personal_support_if_ready(overlay)
        except PermissionError as exc:
            return JsonResponse({"detail": str(exc)}, status=403)
        except (AnswerConflict, TemplateSyncConflict) as exc:
            return JsonResponse({"detail": str(exc)}, status=409)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)
        return JsonResponse(_party_definition(overlay, account))


@require_GET
def migrated_review_party_pdf(request, review_id):
    account = authenticate_request(request)
    if account is None:
        return JsonResponse({"detail": "Sign in to view this PDF."}, status=401)
    with transaction.atomic():
        overlay = _party_overlay(review_id, account, lock=True)
        if not overlay or overlay.status != ImportedReviewInstance.STATUS_COMPLETED:
            return JsonResponse({"detail": "Completed review not found for this account."}, status=404)
        return _completed_pdf_response(overlay)


@require_POST
def migrated_review_party_sign(request, review_id):
    account = authenticate_request(request)
    if account is None:
        return JsonResponse({"detail": "Sign in to sign this review."}, status=401)
    if account.role not in {"learner", "employer"} or request.GET.get("viewAsCoach") or request.GET.get("viewAsLearner"):
        return JsonResponse({"detail": "This account cannot sign as a review participant."}, status=403)
    payload = _payload(request)
    if payload is None:
        return JsonResponse({"detail": "Invalid JSON object."}, status=400)
    for option in ("applyMonthlyLogSignature", "retryMonthlyLogSignature"):
        if option in payload and type(payload[option]) is not bool:
            return JsonResponse({"detail": f"{option} must be a boolean."}, status=400)
    copy_log = payload.get("applyMonthlyLogSignature") is True
    retry_log = payload.get("retryMonthlyLogSignature") is True
    if (copy_log or retry_log) and account.role != "learner":
        return JsonResponse({"detail": "Only the learner can sign their Monthly Log."}, status=403)
    with transaction.atomic():
        overlay = _party_overlay(review_id, account, lock=True, lock_calendar=True)
        if not overlay:
            return JsonResponse({"detail": "Review not found for this account."}, status=404)
        role = "participant" if account.role == "learner" else "employer"
        try:
            already_signed = overlay.migrated_signatures.filter(role=role, signer_account_id=account.pk).exists()
            if retry_log or (copy_log and migrated_monthly_log.saved_state(overlay) and already_signed):
                migrated_monthly_log.validate_retry(overlay, account, payload)
            else:
                check_revision(overlay, payload)
                month = None
                if copy_log and not migrated_monthly_log.saved_state(overlay):
                    from coach_api.views import _imported_review_definition
                    definition = _imported_review_definition(overlay.owner_email, overlay.event_key)
                    option = migrated_monthly_log.signing_option(overlay, account, definition or {})
                    if not option:
                        raise ValueError("The MCM reporting month is unavailable. Reopen the review, or sign only the MCM.")
                    month = option["month"]
                _sign_and_complete(overlay, role, account=account, signature=payload.get("signature"))
                if month:
                    migrated_monthly_log.record_request(overlay, account, month)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=409)
    response = {"signed": True, "role": role}
    if copy_log or retry_log:
        response["monthlyLogSync"] = migrated_monthly_log.sync_request(overlay)
    return JsonResponse(response)


@coach_access_required
@require_POST
def migrated_review_complete(request, review_id):
    owner, definition = _coach_review(request, review_id)
    if not definition:
        return JsonResponse({"detail": "Migrated review not found."}, status=404)
    payload = _payload(request)
    if payload is None:
        return JsonResponse({"detail": "Invalid JSON object."}, status=400)
    with transaction.atomic():
        overlay = _locked_overlay(owner, definition, lock_calendar=True)
        if not overlay:
            return JsonResponse({"detail": "Migrated review association mismatch."}, status=409)
        try:
            check_revision(overlay, payload)
            complete(overlay)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=409)
        _mirror_status(overlay)
    return _response(owner, review_id)


def _pdf_context(overlay, definition=None):
    if definition is None:
        # Participants retain their own ownership gate; they need neither coach
        # mutation permission nor membership of the original coach's caseload.
        # Only persisted identity/date display context is read, never live form,
        # template, learner progress or meeting intelligence services.
        from coach_api.views import _review_learner_identity, get_learner_db_alias
        profile = LearnerProfile.objects.filter(pk=overlay.learner_id).first()
        with connections[get_learner_db_alias()].cursor() as cursor:
            cursor.execute(
                'SELECT planned_scheduled_date, review_type, status, completed_date '
                'FROM "Learner".reviews WHERE id = %s AND learner_id = %s',
                [overlay.source_review_id, overlay.learner_id],
            )
            source = cursor.fetchone()
        if not profile or not source or str(source[2] or "").strip().casefold() == "completed" or source[3]:
            raise ValueError("Migrated source association is unavailable.")
        definition = {
            **_review_learner_identity(profile),
            "instance": {"targetDate": source[0]},
            "historicalReview": {"type": source[1]},
        }
    calendar = booking_for_overlay(overlay)
    return {
        "learner_name": definition.get("learnerName") or "",
        "learner_email": definition.get("learnerEmail") or "",
        "programme": definition.get("programme") or "",
        "scheduled_date": calendar.scheduled_date if calendar and calendar.scheduled_date else definition["instance"].get("targetDate"),
        "coach_name": overlay.owner_email,
        "source_family": review_family((definition.get("historicalReview") or {}).get("type")),
    }


def _final_document(overlay, definition=None):
    """Caller holds the completed overlay lock, including during first render.

    Recheck under that lock so simultaneous coach/participant downloads and the
    compatibility endpoint all reuse the winner's immutable document. Existing
    documents need no display context or rendering. The unique overlay FK is a
    second persistence guard. Only local rendering occurs while holding the lock.
    """
    document = MigratedReviewDocument.objects.filter(overlay=overlay).first()
    return document if document is not None else ensure_document(overlay, **_pdf_context(overlay, definition))


def _completed_pdf_response(overlay, definition=None):
    try:
        document = _final_document(overlay, definition)
    except Exception:
        # ensure_document inserts only after rendering, inside a savepoint.
        # A retry never needs to change completion, answers or signatures.
        return JsonResponse({"detail": "Unable to prepare the signed PDF. Please try again."}, status=503)
    return stored_pdf_response(overlay, document)


@coach_access_required
@require_POST
def migrated_review_generate_pdf(request, review_id):
    owner, definition = _coach_review(request, review_id, pdf_only=True)
    if not definition:
        return JsonResponse({"detail": "Migrated review not found."}, status=404)
    with transaction.atomic():
        overlay = _locked_overlay(owner, definition)
        if not overlay or overlay.status != ImportedReviewInstance.STATUS_COMPLETED:
            return JsonResponse({"detail": "Complete the review before generating its PDF."}, status=409)
        try:
            document = _final_document(overlay, definition)
        except Exception:
            # The completed review stays intact; no incomplete document is stored.
            return JsonResponse({"detail": "The LMS PDF could not be generated. Retry this action."}, status=503)
    return JsonResponse({"available": True, "documentId": document.pk})


def migrated_review_pdf_response(request, review_id, definition):
    owner = authenticated_coach_email(request).strip().casefold()
    with transaction.atomic():
        overlay = _locked_overlay(owner, definition)
        if not overlay or overlay.event_key != review_id or overlay.status != ImportedReviewInstance.STATUS_COMPLETED:
            return JsonResponse({"detail": "The migrated review is not complete."}, status=409)
        return _completed_pdf_response(overlay, definition)
