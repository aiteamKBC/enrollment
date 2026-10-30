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
from django.db import DatabaseError
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

from .models import Employer, Organisation, StaffUser
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
    rows = Organisation.objects.filter(pk__in=ids).values_list("pk", "name")
    return {pk: (name or "").strip() for pk, name in rows}


@csrf_exempt
@staff_only()  # A4: organisation directory — staff/admin only, reads too.
def organisations(request):
    """Organisation profiles — enrolment."Organisations".

    GET supports ?search= (name, case-insensitive), ?status=, and ?page= — the
    employer form's Employer Group picker is a searchable paged table, and the
    list screen reuses the same endpoint.
    """
    if request.method == "GET":
        search = (request.GET.get("search") or "").strip()
        status = (request.GET.get("status") or "").strip()
        try:
            qs = Organisation.objects.all()
            if search:
                qs = qs.filter(name__icontains=search)
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
            emp = Employer.objects.create(**fields)
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
            for attr, value in fields.items():
                setattr(emp, attr, value)
            try:
                emp.save(update_fields=[*fields.keys(), "updated_at"])
            except DatabaseError as exc:
                return _error(f"Database error: {exc}", 502)
            # The login account holds its own copy of the address an invitation
            # is sent to; correcting it here has to reach that copy.
            if any(field in fields for field in ("email", "full_name")):
                sync_account("employer", emp.pk, subject=emp)
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
