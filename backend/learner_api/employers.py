"""JSON API for organisations and employers (no DRF — plain Django views).

Routes (mounted under /learner_api/ in config/urls.py):
    GET   /learner_api/organisations/         -> {count, results:[organisation rows]}
    POST  /learner_api/organisations/         -> create; returns the new row
    GET   /learner_api/organisations/<id>/    -> one organisation
    PATCH /learner_api/organisations/<id>/    -> update; returns the row
    GET   /learner_api/employers/             -> {count, results:[employer rows]}
    POST  /learner_api/employers/             -> create; returns the new row
    GET   /learner_api/employers/<id>/        -> one employer
    PATCH /learner_api/employers/<id>/        -> update; returns the row
    GET   /learner_api/employers/options/     -> dropdown option lists for both forms

An organisation is a company; an employer is a person at one or more of them. The
employer form's "Employer Group" control picks organisations, which is why the
organisation list supports the ?search=/?page= the picker needs.

Authentication follows views.py: write methods require an authenticated staff
or admin session (``@staff_only(writes_only=True)``). Django's CSRF middleware
is exempted because these are JSON endpoints; cross-site protection is the
``X-Requested-With`` header plus the SameSite=Lax session cookie.
"""
from django.db import DatabaseError, transaction
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse

from .constants import (
    ORGANISATION_STATUS_CHOICES,
    ORGANISATION_GROUP_TYPE_CHOICES,
    LEVY_PAYER_CHOICES,
    HEALTH_SAFETY_CHOICES,
)
from .mappers import (
    ValidationError,
    to_employer_row,
    to_organisation_row,
    write_employer_fields,
    write_organisation_fields,
)
from login.permissions import staff_only

from .models import Employer, EnrolmentUser, Organisation, StaffUser
from .profile_email import (
    ProfileEmailInUse, assert_available_for_email_change, assert_available_for_new_profile,
)
from .views import _error, _parse_body, _send_platform_invitation
from login.services import sync_account

# The picker in the reference UI pages ten rows at a time.
PAGE_SIZE = 10


def _resolve_groups(ids):
    """{organisation id: current name} for the ids that actually exist.

    Passed into write_employer_fields, where a missing id is rejected — so this
    doubles as the existence check for the Employer Group selection.
    """
    if not ids:
        return {}
    rows = Organisation.objects.filter(pk__in=ids, merged_into_organisation_id__isnull=True).values_list("pk", "name")
    return {pk: (name or "").strip() for pk, name in rows}


@csrf_exempt
@staff_only()  # A4: organisation directory — staff/admin only, reads too.
def organisations(request):
    """Organisation profiles — enrolment."Organisations".

    GET supports ?search= (name, case-insensitive), ?postcode=, ?status=, and ?page= — the
    employer form's Employer Group picker is a searchable paged table, and the
    list screen reuses the same endpoint.
    """
    if request.method == "GET":
        search = (request.GET.get("search") or "").strip()
        postcode = (request.GET.get("postcode") or "").strip()
        status = (request.GET.get("status") or "").strip()
        try:
            qs = Organisation.objects.all()
            if request.GET.get("includeMerged") != "1":
                qs = qs.filter(merged_into_organisation_id__isnull=True)
            if search:
                qs = qs.filter(name__icontains=search)
            if postcode:
                qs = qs.filter(post_code__icontains=postcode)
            if status:
                qs = qs.filter(status=status)
            qs = qs.order_by("name", "id")
            total = qs.count()

            # ?page= is optional: without it the whole list is returned, which is
            # what a plain "list all organisations" caller wants.
            page = request.GET.get("page")
            if page:
                try:
                    page_no = max(1, int(page))
                except (TypeError, ValueError):
                    return _error("page must be a whole number.", 400)
                start = (page_no - 1) * PAGE_SIZE
                qs = qs[start:start + PAGE_SIZE]
            else:
                page_no = 1

            rows = [to_organisation_row(o) for o in qs]
        except DatabaseError as exc:
            return _error(f"Database error: {exc}", 502)
        return JsonResponse({
            "count": total,
            "page": page_no,
            "pageSize": PAGE_SIZE,
            "results": rows,
        })

    if request.method == "POST":
        try:
            payload = _parse_body(request)
            fields = write_organisation_fields(payload, require_create=True)
        except ValidationError as exc:
            return _error(str(exc), 400)
        try:
            org = Organisation.objects.create(**fields)
        except DatabaseError as exc:
            return _error(f"Database error: {exc}", 502)
        return JsonResponse(to_organisation_row(org), status=201)

    return _error("Method not allowed.", 405)


@csrf_exempt
@staff_only()  # A4: organisation detail — staff/admin only, reads too.
def organisation_detail(request, pk):
    try:
        org = Organisation.objects.get(pk=pk)
    except Organisation.DoesNotExist:
        return _error("Organisation not found.", 404)
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)

    if request.method == "GET":
        return JsonResponse(to_organisation_row(org))

    if request.method in ("PATCH", "PUT"):
        if org.merged_into_organisation_id is not None:
            return _error("This organisation was merged. Edit its active target.", 409)
        try:
            payload = _parse_body(request)
            fields = write_organisation_fields(payload)
        except ValidationError as exc:
            return _error(str(exc), 400)
        if fields:
            for attr, value in fields.items():
                setattr(org, attr, value)
            try:
                # auto_now on Updated_at only fires when it's in update_fields.
                org.save(update_fields=[*fields.keys(), "updated_at"])
            except DatabaseError as exc:
                return _error(f"Database error: {exc}", 502)
            # A renamed organisation would otherwise leave stale names on the
            # employers pointing at it, since those are denormalised.
            if "name" in fields:
                _resync_group_names(org)
        return JsonResponse(to_organisation_row(org))

    return _error("Method not allowed.", 405)


@csrf_exempt
@staff_only()
def merge_organisation(request, pk):
    """Merge a source profile into an active target without erasing its history."""
    if request.method != "POST":
        return _error("Method not allowed.", 405)
    actor = getattr(request, "login_account", None)
    if actor is None or actor.role != "admin":
        return _error("Only an admin can merge organisations.", 403)
    try:
        payload = _parse_body(request)
        target_id = int(payload.get("targetOrganisationId"))
    except (ValueError, TypeError, ValidationError):
        return _error("targetOrganisationId must identify an organisation.", 400)
    if pk == target_id:
        return _error("An organisation cannot be merged into itself.", 400)
    try:
        with transaction.atomic(using="enrolment"):
            locked = {
                org.pk: org for org in Organisation.objects.select_for_update()
                .filter(pk__in=[pk, target_id]).order_by("pk")
            }
            source, target = locked.get(pk), locked.get(target_id)
            if source is None or target is None:
                return _error("Organisation not found.", 404)
            if source.merged_into_organisation_id or target.merged_into_organisation_id:
                return _error("Both organisations must be active.", 409)
            # Keep the source row as an audit trail. A learner's ID is the
            # authoritative association; text labels are refreshed alongside it.
            reassigned = EnrolmentUser.all_learners.filter(organisation_id=pk).update(
                organisation_id=target_id, organization=target.name, employer=target.name,
            )
            for emp in Employer.objects.filter(employer_group_ids__contains=pk):
                ids = [int(i) for i in emp.employer_group_ids or []]
                ids = list(dict.fromkeys(target_id if i == pk else i for i in ids))
                emp.employer_group_ids = ids
                names = _resolve_groups(ids)
                emp.employer_group_names = [names.get(i, "") for i in ids]
                emp.save(update_fields=["employer_group_ids", "employer_group_names", "updated_at"])
            source.merged_into_organisation_id = target_id
            source.merged_at = timezone.now()
            source.merged_by_account_id = actor.pk
            source.save(update_fields=["merged_into_organisation_id", "merged_at", "merged_by_account_id", "updated_at"])
        return JsonResponse({"source": to_organisation_row(source),
                             "target": to_organisation_row(target), "learnersReassigned": reassigned})
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)


def _resync_group_names(org):
    """Refresh the denormalised organisation names held on its employers.

    Best-effort: the ids are the real link, so a failure here leaves a stale
    label rather than a broken relationship.
    """
    try:
        name = (org.name or "").strip()
        for emp in Employer.objects.filter(employer_group_ids__contains=org.pk):
            ids = [int(i) for i in (emp.employer_group_ids or [])]
            names = _resolve_groups(ids)
            emp.employer_group_names = [names.get(i, name) for i in ids]
            emp.save(update_fields=["employer_group_names", "updated_at"])
        # The id is authoritative, but the learner directory still carries a
        # text label for older clients and exports.
        EnrolmentUser.all_learners.filter(organisation_id=org.pk).update(
            organization=name, employer=name,
        )
    except DatabaseError:
        pass


@csrf_exempt
@staff_only()  # A4: employer directory (PII) — staff/admin only, reads too.
def employers(request):
    """Employer profiles — enrolment."Employers".

    GET supports ?search= (name or email) and ?organisation= to list the people
    at one organisation.
    """
    if request.method == "GET":
        search = (request.GET.get("search") or "").strip()
        organisation = (request.GET.get("organisation") or "").strip()
        try:
            qs = Employer.objects.all()
            if search:
                from django.db.models import Q
                qs = qs.filter(
                    Q(first_name__icontains=search)
                    | Q(surname__icontains=search)
                    | Q(email__icontains=search)
                )
            if organisation:
                try:
                    # jsonb containment — matches the GIN index on the id array.
                    qs = qs.filter(employer_group_ids__contains=int(organisation))
                except (TypeError, ValueError):
                    return _error("organisation must be a whole number.", 400)
            rows = [to_employer_row(e) for e in qs.order_by("surname", "first_name", "id")]
        except DatabaseError as exc:
            return _error(f"Database error: {exc}", 502)
        return JsonResponse({"count": len(rows), "results": rows})

    if request.method == "POST":
        try:
            payload = _parse_body(request)
            fields = write_employer_fields(
                payload, require_create=True, resolve_groups=_resolve_groups
            )
        except ValidationError as exc:
            return _error(str(exc), 400)
        try:
            with transaction.atomic(using="enrolment"):
                assert_available_for_new_profile(fields.get("email"))
                emp = Employer.objects.create(**fields)
        except ProfileEmailInUse as exc:
            return _error(str(exc), 409)
        except DatabaseError as exc:
            return _error(f"Database error: {exc}", 502)

        row = to_employer_row(emp)
        # Unconditional, as on the learner and staff forms. Nothing is stamped on
        # the row to say so: unlike those two tables, enrolment."Employers" has no
        # "Invite_to_platform" column, and adding one would store a transient
        # action as though it were a property of the person. Whether they were
        # invited is answered by login."Invitations" — which stays empty until an
        # administrator sends the invitation from the Accounts page.
        row["invitation"] = _send_platform_invitation(
            request, "employer", emp.id, subject=emp
        )
        return JsonResponse(row, status=201)

    return _error("Method not allowed.", 405)


@csrf_exempt
@staff_only()  # A4: employer detail (PII) — staff/admin only, reads too.
def employer_detail(request, pk):
    try:
        emp = Employer.objects.get(pk=pk)
    except Employer.DoesNotExist:
        return _error("Employer not found.", 404)
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)

    if request.method == "GET":
        return JsonResponse(to_employer_row(emp))

    if request.method in ("PATCH", "PUT"):
        try:
            payload = _parse_body(request)
            fields = write_employer_fields(payload, resolve_groups=_resolve_groups)
        except ValidationError as exc:
            return _error(str(exc), 400)
        if fields:
            try:
                with transaction.atomic(using="enrolment"):
                    if "email" in fields:
                        assert_available_for_email_change(emp.email, fields["email"])
                    for attr, value in fields.items():
                        setattr(emp, attr, value)
                    emp.save(update_fields=[*fields.keys(), "updated_at"])
                    # Keep the invitation destination aligned with this profile.
                    if any(field in fields for field in ("email", "full_name")):
                        sync_account("employer", emp.pk, subject=emp)
            except ProfileEmailInUse as exc:
                return _error(str(exc), 409)
            except DatabaseError as exc:
                return _error(f"Database error: {exc}", 502)
        return JsonResponse(to_employer_row(emp))

    return _error("Method not allowed.", 405)


@staff_only()  # A4: `owners` discloses every staff username — staff/admin only.
def employer_options(request):
    """Canonical dropdown lists for the organisation and employer forms.

    Served from the same constants the API validates against, so the two can't
    drift. `owners` is every staff account — an organisation is owned by a member
    of staff, whatever their position, so this is deliberately unfiltered rather
    than restricted to the Caseowner/Admin positions a learner's case owner is.

    Staff/admin only: these are the console's organisation/employer authoring
    forms (only staff create organisations), and `owners` leaks the full staff
    username list, so an open GET is the same staff-directory disclosure class as
    A4. Learner/employer callers get 403.
    """
    if request.method != "GET":
        return _error("Method not allowed.", 405)

    owners = []
    try:
        owners = [
            (u.username or "").strip()
            for u in StaffUser.objects.order_by("username")
            if (u.username or "").strip()
        ]
    except DatabaseError:
        # The forms are still usable with a free-text owner if this lookup fails.
        pass

    return JsonResponse({
        "status": ORGANISATION_STATUS_CHOICES,
        "groupType": ORGANISATION_GROUP_TYPE_CHOICES,
        "levyPayer": LEVY_PAYER_CHOICES,
        "healthAndSafety": HEALTH_SAFETY_CHOICES,
        "owners": owners,
    })
