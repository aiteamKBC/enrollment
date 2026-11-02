def is_staff(request):
    # The existing enrolment gate checks platform staff access grants first.
    account = getattr(request, "login_account", None)
    if account is not None:
        return account.role in {"staff", "admin"}
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))
