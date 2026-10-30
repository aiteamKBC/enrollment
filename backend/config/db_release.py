"""Return pooled database connections when a request is cancelled.

Django hands a pooled connection back at the end of a request: ``CONN_MAX_AGE``
is 0 whenever a psycopg pool is configured, so ``close_old_connections`` --
wired to the ``request_finished`` signal -- releases it once the response is
closed.

A cancelled request never gets that far. Under ASGI the client disconnecting
cancels the task mid-flight, ``CancelledError`` unwinds the middleware chain,
no response is ever built or closed, and ``request_finished`` never fires. The
connection stays checked out of the pool forever. The frontend aborts requests
routinely (unmount signals and per-call timeouts), so the leak accumulates: once
all ``DB_POOL_MAX_SIZE`` slots are gone every subsequent request -- including the
session lookup that authenticates it -- waits the full pool timeout and then
fails, and the process only recovers on restart.

Releasing on the way out of an unwinding request is what closes that gap.
``CancelledError`` derives from ``BaseException``, so catching ``Exception`` is
not enough. The success path is deliberately left alone: Django already releases
there, and nothing here should sit between a healthy response and its caller.
"""
from __future__ import annotations

import logging

from django.db import close_old_connections

logger = logging.getLogger(__name__)


class ReleaseConnectionOnAbortMiddleware:
    """Release checked-out connections when a request dies without a response."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            return self.get_response(request)
        except BaseException:
            try:
                close_old_connections()
            except Exception:
                # A failure here must not replace the exception that is already
                # unwinding -- that one is what says why the request ended.
                logger.exception('Could not release database connections for %s', request.path)
            raise
