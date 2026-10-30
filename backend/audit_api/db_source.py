"""Which physical database an ``audit_api`` request talks to.

The audit endpoints are served twice:

* ``/audit_api/…``      — the live audit branch (the "Automatic" workspace).
* ``/hours_test_api/…`` — a full clone of that branch (the "HOURS-TEST"
  workspace), so hours can be edited without touching the live data.

Both mounts run the *same* view functions; only the database differs. A
context variable carries the choice for the duration of one request, and every
DB access in this app resolves its alias through :func:`resolve` instead of
naming a connection directly.

``audit`` and ``enrolment`` are two aliases over the same physical Neon
database, and the clone contains all of their schemas, so clone mode remaps
both of them onto ``audit_clone``. Anything else (``kbc_attendance``, the live
register) is left alone — it is read-only source data shared by both
workspaces.
"""

from contextvars import ContextVar
from functools import wraps

from django.db import connections


CLONE_ALIAS = "audit_clone"

# The aliases that point at the audit branch and therefore have a counterpart
# inside the clone.
_REMAPPED_ALIASES = frozenset({"audit", "enrolment"})

_use_clone: ContextVar[bool] = ContextVar("audit_api_use_clone", default=False)


def is_clone() -> bool:
    """True while serving a request mounted under the HOURS-TEST prefix."""
    return _use_clone.get()


def resolve(alias: str) -> str:
    """The connection alias to use for ``alias`` in the current request.

    Falls back to ``default`` when the requested alias is not configured, which
    keeps the minimal SQLite test setups working exactly as before.
    """
    if _use_clone.get() and alias in _REMAPPED_ALIASES and CLONE_ALIAS in connections.databases:
        return CLONE_ALIAS
    return alias if alias in connections.databases else "default"


def cache_scope() -> str:
    """Suffix for module-level caches so the two workspaces never share rows."""
    return CLONE_ALIAS if _use_clone.get() else "live"


def clone_view(view):
    """Wrap a view so every DB access inside it resolves to the clone."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        token = _use_clone.set(True)
        try:
            return view(request, *args, **kwargs)
        finally:
            _use_clone.reset(token)

    return wrapper
