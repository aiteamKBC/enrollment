"""Attach the login identity to every request.

Runs once per request so any view — including the existing ``learner_api`` and
``enrolment_api`` views — can read ``request.login_account`` without importing
the session machinery or paying for a second lookup.

It never rejects a request: authorisation belongs to the decorators in
``permissions.py``, and a middleware that blanket-401s would break the health
and login endpoints themselves.

Failures are swallowed. If the auth database is briefly unreachable, an
anonymous request must still be served rather than 500ing the whole site; the
protected views will then correctly refuse it as unauthenticated.
"""
from __future__ import annotations

import logging

from django.db import DatabaseError, InterfaceError, OperationalError, close_old_connections, connections, router

from .sessions import (
    RENEWED_UNTIL_ATTR,
    authenticate_request,
    mark_session_unreadable,
    refresh_session_cookie,
)

logger = logging.getLogger("login")


def _authenticate_with_file_retry(request):
    """Retry one failed session read for uploads, before any renewal/write.

    Native viewers, downloads and fetch previews all use this same route. A
    stale database connection must still pass the full session check after it
    is reopened; persistent failures retain the normal fail-closed 503.
    """
    try:
        return authenticate_request(request)
    except (OperationalError, InterfaceError):
        if (request.method not in {'GET', 'HEAD'}
                or not request.path_info.startswith('/curriculum_api/curriculum/uploads/')
                or hasattr(request, 'login_account')):
            raise
        from .models import LoginSession
        connection = connections[router.db_for_read(LoginSession) or 'default']
        if connection.in_atomic_block:
            raise
        connection.close()
        logger.warning('Retrying upload session read after a database connection failure')
        return authenticate_request(request)


class LoginSessionMiddleware:
    """Resolve the session inbound; extend its cookie outbound.

    The outbound half exists because a rolling session has two expiries that
    have to agree. ``authenticate_request`` may push ``Expires_at`` forward in
    the database, but the browser only keeps sending the cookie for as long as
    its own ``Max-Age`` says — so a renewal the response never carries back
    leaves the person signed out at the original time regardless. The request
    attribute is the whole of the signal between the two halves; see
    ``sessions.refresh_session_cookie`` for the guards that decide whether it is
    acted on.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            _authenticate_with_file_retry(request)
        except DatabaseError:
            logger.exception("Could not resolve login session")
            request.login_session = None
            request.login_account = None
            setattr(request, RENEWED_UNTIL_ATTR, None)
            # Anonymous *and* flagged. The views behind this must refuse the
            # request either way, but the flag is what stops them refusing it
            # with a 401 -- the one status the SPA reads as "signed out", which
            # would end a perfectly live session over a transient database
            # error. See ``sessions.SESSION_UNREADABLE_ATTR``.
            mark_session_unreadable(request)

        response = None
        try:
            response = self.get_response(request)

            try:
                refresh_session_cookie(request, response)
            except Exception:  # noqa: BLE001 - a cookie refresh must not 500 a good response
                logger.exception("Could not refresh the login session cookie")

            return response
        finally:
            # Pooled connections are request-scoped. Django normally performs
            # this at the request-finished signal, but gate short-circuits can
            # otherwise leave a checked-out connection attached to the worker
            # until the next request. Streaming responses own their iterator's
            # lifetime, so leave those connections to Django's normal cleanup.
            # Returning it here is safe: session refresh and the view have
            # completed, and it does not alter transaction or identity logic.
            if response is None or not getattr(response, "streaming", False):
                close_old_connections()
