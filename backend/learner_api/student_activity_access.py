"""Eligibility for the read-only historical student-activity view.

The initial rollout used a fixed list for Anna Rundell and the next nine
learners. The Created_users audit on 2026-09-08 found that every one of its 368
non-empty Aptem IDs exists in Last_audit.learners, so eligibility now follows
the actual identity link instead of a name/order-based allow-list. The endpoint
still verifies that the Aptem learner exists before returning any data.
"""


def student_activity_available(aptem_id):
    try:
        return int(str(aptem_id).strip()) > 0
    except (TypeError, ValueError):
        return False
