"""The same gate for normal requests and middleware-free batch dispatch."""
import re

_READ_PATHS = frozenset({
    '/audit_api/last-audit/cohort/',
    '/audit_api/last-audit/manual/summary',
    '/audit_api/last-audit/manual/rows',
    '/audit_api/last-audit/manual/finalization',
})


def is_transition_path(path):
    return (path.startswith('/audit_api/old-otjh/') or path in _READ_PATHS
            or bool(re.fullmatch(r'/audit_api/learners/[0-9]+/signoff/', path)))


def refusal(path, account):
    # Signing the previous record is optional and no longer blocks LMS learning
    # APIs for existing (Aptem) learners. Kept as the api_gate hook point.
    return None
