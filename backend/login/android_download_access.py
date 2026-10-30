"""Staff entitlement to the installer only; grants no mobile data or chat access."""


def staff_download_allowed(account):
    if not account or not getattr(account, "is_active", False):
        return False
    if account.role == "admin":
        return True
    if account.role != "staff" or account.subject_type != "staff":
        return False
    from learner_api.models import StaffUser
    from .identity import accesses_for_staff

    staff = StaffUser.objects.filter(pk=account.subject_id).first()
    return bool(staff and "coach" in accesses_for_staff(staff))
