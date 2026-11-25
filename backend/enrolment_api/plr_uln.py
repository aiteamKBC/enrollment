"""Authorization and validation for explicit edits to the existing PLR ULN."""
import re
from django.db import connections
from .models import WizardPlr
from .auth import _accesses_of
from learner_api.constants import ACCESS_ENROLMENT, ACCESS_SUPER_ADMIN


def can_edit_uln(request):
    account = getattr(request, 'login_account', None)
    if account is not None:
        return bool(getattr(account, 'is_active', False) and account.role == 'admin'
                    and _accesses_of(account) & {ACCESS_ENROLMENT, ACCESS_SUPER_ADMIN})
    user = getattr(request, 'user', None)
    return bool(user and getattr(user, 'is_authenticated', False) and (getattr(user, 'is_staff', False)
                                                    or getattr(user, 'is_superuser', False)))


def valid_uln(value):
    return isinstance(value, str) and (value == '' or re.fullmatch(r'[0-9]{10}', value) is not None)


class UlnAssignmentError(ValueError):
    pass


def reserve_uln(value, learner_id):
    """Caller must be atomic. Share this lock across manual edits and PDF imports."""
    connection = connections['enrolment']
    if not connection.in_atomic_block:
        raise RuntimeError('ULN assignment requires a transaction.')
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext('enrolment-plr-uln-assignment'))")
    from .plr_uln_storage import canonical_enabled
    if canonical_enabled():
        with connection.cursor() as cursor:
            cursor.execute('''SELECT 1 WHERE
                EXISTS (SELECT 1 FROM enrolment."Created_users" WHERE "ULN" = %s AND id <> %s)
                OR EXISTS (SELECT 1 FROM enrolment."Wizard_Plr" p
                           LEFT JOIN enrolment."Created_users" u ON u.id = p."Learner_id"
                           WHERE u.id IS NULL AND p."ULN" = %s)''',
                           [value, learner_id, value])
            duplicate = bool(value and cursor.fetchone())
    else:
        duplicate = bool(value and WizardPlr.objects.filter(uln=value).exclude(learner_id=learner_id).exists())
    if duplicate:
        raise UlnAssignmentError('This ULN already belongs to another student. No changes were saved.')
