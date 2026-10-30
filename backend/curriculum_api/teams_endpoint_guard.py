"""Staff-only access for the Teams calendar routes that predate per-view roles.

Every Teams calendar write either moves a date, changes who is invited, changes
how a meeting runs or creates a meeting in Microsoft -- and Microsoft may email
real learners for some of them. The newer routes (calendar actions, schedule
email, forward dates, status check, Retry, Calendar health) each carry
``require_role('admin', 'staff')``. These older ones relied on the API gate
alone (``login.api_gate``), which admits two callers that have no LMS account:

* a Django admin-site session (``django.contrib.auth``) with no platform role;
* anybody at all wherever the gate is switched off.

Either reaches the view with no actor, so the change is written as "System"
with nobody named -- the 26 September 2026 schedule update was recorded that
way. Wrapped here, at the URL, rather than decorated in the view modules: the
no-database suites execute those view functions straight from source, and a
decorator would have to be stubbed in each of them.

The rule is the established one -- a signed-in LMS account whose role is admin
or staff -- with no fallback when the identity is unknown. It is relaxed only
where the API gate itself is switched off by configuration (``API_REQUIRE_AUTH``,
which ``config.settings`` turns off for ``manage.py test`` alone), so the
Django suites that post without signing in keep running. A background job never
comes through a URL: the session-results worker calls its view directly with a
flagged request, so nothing here affects it.
"""
from __future__ import annotations

import functools

#: Same set as ``login.api_gate.STAFF`` and every ``require_role`` on these routes.
TEAMS_STAFF_ROLES = ('admin', 'staff')
_UNGUARDED_METHODS = frozenset({'OPTIONS'})


def _gate_enabled():
    from login.api_gate import _enabled
    return _enabled()


def teams_staff_required(view):
    """Refuse a caller without an admin/staff LMS account: 401 signed out, 403 wrong role."""

    @functools.wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.method not in _UNGUARDED_METHODS and _gate_enabled():
            from login.permissions import _forbidden, _unauthenticated
            from login.sessions import authenticate_request

            account = authenticate_request(request)
            if account is None:
                return _unauthenticated(request)
            if getattr(account, 'role', None) not in TEAMS_STAFF_ROLES:
                return _forbidden(TEAMS_STAFF_ROLES)
        return view(request, *args, **kwargs)

    wrapped.teams_staff_required = True
    return wrapped
