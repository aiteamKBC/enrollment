"""Read-only occupancy policy for already resolved calendar records.

This does not grant permission to book, resolve an imported identity, expire a
lease, or repair a lifecycle. Those checks remain in the canonical resolvers
and reservation writers.
"""

REVIEW_TYPES = frozenset(("mcr", "progress-review", "review"))
OCCUPANCY_FIELDS = (
    "event_type", "status", "sync_state", "scheduled_date", "scheduled_time",
    "duration_minutes", "graph_event_id", "meeting_link", "graph_web_link",
    "sync_attempt_count", "last_sync_attempt_at",
)


def review_occupies_slot(record):
    if not record or not record.scheduled_date or not record.scheduled_time:
        return False
    if record.status == "cancelled" or getattr(record, "sync_state", "") == "cancelled":
        return False
    # A durable reservation is written with its schedule and active status
    # before Graph is called. A failed sync does not release that reservation.
    if record.status in {"scheduled", "in-progress", "awaiting-signature", "completed"}:
        return True
    # Retain in-flight/reconciliation protection even with a stale lifecycle.
    if getattr(record, "sync_state", "") in {"syncing", "reconciliation"}:
        return True
    # operation_id is allocated even for a new placeholder: it is NOT proof
    # of reservation. An actual sync attempt/reference is durable evidence.
    return any(bool(getattr(record, field, None)) for field in (
        "sync_attempt_count", "last_sync_attempt_at", "graph_event_id", "meeting_link", "graph_web_link",
    ))


def calendar_record_occupies_slot(record, *, availability_read=False):
    if getattr(record, "event_type", "") in REVIEW_TYPES:
        return review_occupies_slot(record)
    # Preserve the existing non-Review workflows' selection rules.
    if getattr(record, "sync_state", "") == "cancelled":
        return False
    if availability_read:
        return record.status not in {"cancelled", "not-scheduled", "failed"}
    return record.status in {"not-scheduled", "scheduled", "in-progress", "awaiting-signature"}
