"""Resolve the current case owner and keep coach names/emails together."""
from .models import StaffUser


def _text(value):
    return str(value or "").strip()


def _unique_staff_for_name(name):
    """Return the one staff account matching a coach name, if unambiguous."""
    staff = list(StaffUser.objects.filter(username__iexact=name).only("email")[:2])
    return staff[0] if len(staff) == 1 else None


def _is_legacy_email(email):
    """Identify the old KBC domain that can be repaired from Staff_users."""
    return _text(email).casefold().endswith("@kbc.ac.uk")


def case_owner_coach(owner, source=None):
    name = _text(owner)
    if not name:
        return {"coach_name": "", "coach_email": ""}
    source_name = _text(getattr(source, "coach_name", "")) if source is not None else ""
    source_email = _text(getattr(source, "coach_email", "")) if source is not None else ""
    # Staff_users is authoritative when the source contains the retired KBC
    # domain. This repairs stale learner-side values while avoiding a database
    # lookup for already-resolved addresses and preserving duplicate-name
    # disambiguation for other legacy sources.
    source_matches_name = source_name.casefold() == name.casefold()
    should_resolve_staff = _is_legacy_email(source_email) or not source_email or not source_matches_name
    staff = _unique_staff_for_name(name) if should_resolve_staff else None
    if staff is not None and _text(staff.email):
        return {"coach_name": name, "coach_email": _text(staff.email)}
    # An email already saved with this name still disambiguates duplicate or
    # legacy free-text names when no unique staff account can be resolved.
    if (source is not None
            and _text(getattr(source, "coach_name", "")).casefold() == name.casefold()
            and _text(getattr(source, "coach_email", ""))):
        return {"coach_name": name, "coach_email": _text(source.coach_email)}
    # Legacy free-text and ambiguous names remain visible, without retaining
    # the previous coach's email or choosing another person's address.
    return {"coach_name": name,
            "coach_email": ""}


def source_coach(source):
    """None means a legacy row with no assignment; empty strings mean cleared."""
    owner = _text(getattr(source, "case_owner", ""))
    if owner:
        return case_owner_coach(owner, source)
    name = getattr(source, "coach_name", None)
    email = getattr(source, "coach_email", None)
    if name is not None or email is not None:
        clean_name = _text(name)
        staff = _unique_staff_for_name(clean_name) if clean_name and _is_legacy_email(email) else None
        if staff is not None and _text(staff.email):
            return {"coach_name": clean_name, "coach_email": _text(staff.email)}
        return {"coach_name": clean_name, "coach_email": _text(email)}
    return None


def current_coach(source, profile=None, historical=None):
    contact = source_coach(source)
    if contact is not None:
        return contact
    if profile is not None and (profile.coach_name or profile.coach_email):
        return {"coach_name": _text(profile.coach_name), "coach_email": _text(profile.coach_email)}
    historical = historical or {}
    return {"coach_name": _text(historical.get("coach_name")),
            "coach_email": _text(historical.get("coach_email"))}
