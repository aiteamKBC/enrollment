"""The signed-in person's saved signature, offered back wherever they sign.

    GET /login_api/me/signature/  -> {"signature": "<data URL>" | "", "savedAt": "<ISO>" | ""}
    PUT /login_api/me/signature/  {"signature": "data:image/png;base64,..."}

Always the caller's own signature: the row is chosen from the session's account
(subject_type, subject_id), never from anything in the request, so nobody can
read or replace another person's signature.

Each account type keeps it where that table already did:

* learners  -> enrolment."Created_users"."Learner_signature" — the signature
  captured at first sign-in (learner_api.first_login_details), which the
  enrolment wizard already reuses;
* employers -> enrolment."Employers"."Signature" — already offered on
  enrolment documents;
* staff     -> enrolment."Staff_users"."Saved_signature" (added by
  apply_saved_signature_columns).

Validation matches first_login_details: a PNG data URL of bounded size. An
uploaded image is converted to PNG in the browser before it gets here.
"""
import logging

from django.db import DatabaseError, connections
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .models import SUBJECT_EMPLOYER, SUBJECT_LEARNER, SUBJECT_STAFF
from .permissions import login_required
from .views import _body, _error, _reject_cross_site

logger = logging.getLogger(__name__)

MAX_SIGNATURE_CHARS = 400_000
SIGNATURE_PREFIX = "data:image/png;base64,"

#: subject_type -> (table, signature column, signatory-name column, saved-at column)
STORES = {
    SUBJECT_LEARNER: ('enrolment."Created_users"', "Learner_signature", "Learner_signature_name", "Learner_signature_saved_at"),
    SUBJECT_EMPLOYER: ('enrolment."Employers"', "Signature", "Signature_name", "Signature_date"),
    SUBJECT_STAFF: ('enrolment."Staff_users"', "Saved_signature", "Saved_signature_name", "Saved_signature_at"),
}


def _conn():
    return connections["enrolment"]


def invalid_signature(signature):
    """Why `signature` cannot be saved, or None when it can."""
    if not isinstance(signature, str) or not signature.startswith(SIGNATURE_PREFIX) or len(signature) <= len(SIGNATURE_PREFIX):
        return "Signature must be a PNG image."
    if len(signature) > MAX_SIGNATURE_CHARS:
        return "Signature image is too large."
    return None


def read_saved_signature(account):
    """(data URL, ISO saved-at) for this account, or ("", "")."""
    store = STORES.get(account.subject_type)
    if store is None:
        return "", ""
    table, sig_col, _name_col, at_col = store
    with _conn().cursor() as cur:
        cur.execute(f'select "{sig_col}", "{at_col}" from {table} where id = %s limit 1', [account.subject_id])
        row = cur.fetchone()
    if not row or not (row[0] or "").strip():
        return "", ""
    saved_at = row[1].isoformat() if hasattr(row[1], "isoformat") else ""
    return row[0], saved_at


@csrf_exempt
@login_required
def my_signature(request):
    account = request.login_account
    store = STORES.get(account.subject_type)
    if store is None:
        return _error("This account cannot keep a saved signature.", 400)

    if request.method == "GET":
        try:
            signature, saved_at = read_saved_signature(account)
        except DatabaseError as exc:
            logger.warning("Could not read saved signature: %s", exc)
            return _error("Could not load your saved signature.", 502)
        return JsonResponse({"signature": signature, "savedAt": saved_at})

    if request.method not in ("PUT", "POST"):
        return _error("Method not allowed.", 405)
    blocked = _reject_cross_site(request)
    if blocked:
        return blocked
    try:
        payload = _body(request)
    except ValueError as exc:
        return _error(str(exc), 400)
    signature = payload.get("signature")
    problem = invalid_signature(signature)
    if problem:
        return _error(problem, 400)

    table, sig_col, name_col, at_col = store
    try:
        with _conn().cursor() as cur:
            cur.execute(
                f'update {table} set "{sig_col}" = %s, "{name_col}" = %s, "{at_col}" = now() '
                f'where id = %s returning "{at_col}"',
                [signature, account.display_name or "", account.subject_id],
            )
            row = cur.fetchone()
    except DatabaseError as exc:
        logger.warning("Could not save signature: %s", exc)
        return _error("Could not save your signature.", 502)
    if row is None:
        return _error("Your account record was not found.", 404)
    return JsonResponse({"signature": signature, "savedAt": row[0].isoformat() if hasattr(row[0], "isoformat") else ""})
