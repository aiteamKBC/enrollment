"""Recording what a manual auditor changed, for the Audit Trail.

Two shapes of write live in this app and they need opposite treatment.

**The plan already logs itself.** ``plan_tables.log_plan_event`` appends to
``"Manual_audit".plan_events`` with the entity, the action, the value before,
the value after and the actor -- a revision log in all but name, called from ten
places covering group creation, archiving, membership, months, activities and
exemptions. Re-deriving those events from the plan tables would produce a second
and worse account of them, so this module records ``plan_events`` itself: one
hook surfaces all ten in the trail.

**The overrides log nothing.** ``learner_hours_overrides``,
``learner_profile_date_overrides``, ``activity_overrides`` and the sign-offs are
upserts carrying ``updated_by`` and ``updated_at``. Each says who touched it last
and nothing about what it held before -- and between them they decide a
learner's hours, which month their evidence counts in, and whether a month is
signed off. Those are recorded here at the statement.

A note on keys, because getting it wrong would have been silent and wrong.
``versioning`` indexes history by ONE column, and these tables have composite
primary keys: ``(aptem_id, period)``, ``(learner_id, programme_key,
report_month, signer_role)``. Keying on the first column alone would file every
month of a learner's overrides under the same entity, so each month's edit would
read as an edit to the previous month's. Each recorder therefore supplies a
synthetic key joining the real ones, and that is what the trail indexes.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _key(*parts):
    """The composite primary key as one value the trail can index."""
    return ':'.join('' if part is None else str(part) for part in parts)


#: The plan's own event log, surfaced in the trail rather than re-derived.
PLAN_EVENT_COLUMNS = (
    'id', 'entity_type', 'entity_id', 'action', 'old_value', 'new_value', 'actor', 'at',
)
PLAN_EVENT_SQL = ', '.join(PLAN_EVENT_COLUMNS)

#: An auditor's replacement for a learner's hours in one period.
HOURS_OVERRIDE_COLUMNS = (
    'override_key', 'aptem_id', 'period',
    'planned_hours', 'actual_hours', 'not_accepted_hours', 'updated_by', 'updated_at',
)
#: What the statement returns, in order -- the synthetic key is added here, not
#: selected from the table.
HOURS_OVERRIDE_RETURNING = (
    'aptem_id', 'period',
    'planned_hours', 'actual_hours', 'not_accepted_hours', 'updated_by', 'updated_at',
)

#: An auditor's replacement for a learner's start / first-evidence / end dates.
DATE_OVERRIDE_COLUMNS = (
    'aptem_id', 'start_date', 'first_evidence_date', 'planned_end_date',
    'updated_by', 'updated_at',
)

#: The monthly sign-off. ``signature_data`` is a drawn signature and is not
#: collected; who signed, whether they confirmed the review and when are.
SIGNOFF_COLUMNS = (
    'signoff_key', 'learner_id', 'programme_key', 'report_month', 'signer_role',
    'signer_name', 'review_confirmed', 'signed_at', 'snapshot_hash',
    'audit_version', 'updated_at', 'created_at',
)
SIGNOFF_RETURNING = (
    'learner_id', 'programme_key', 'report_month', 'signer_role',
    'signer_name', 'review_confirmed', 'signed_at', 'snapshot_hash',
    'audit_version', 'updated_at',
    # Last, so positional reads are unchanged. Not a snapshot column -- the
    # registration lists its own. The insert stamps both with `now()` and the
    # upsert moves only `updated_at`, so only a sign-off this write created
    # reads as created.
    'created_at',
)


def record(table, columns, row, *, deleted=False):
    """Record one manual-audit write. Never raises."""
    if row is None:
        return
    try:
        from system_audit.writes import record_table_rows

        record_table_rows(
            table,
            [dict(zip(columns, row))],
            reason='deleted' if deleted else '',
            deleted=deleted,
            using='audit',
        )
    except Exception:
        logger.warning('Could not record a manual-audit write to %s.', table, exc_info=True)


def record_hours_override(row, *, deleted=False):
    """One learner-hours override, keyed by learner and period together."""
    if row is None:
        return
    record('manual_learner_hours_overrides', HOURS_OVERRIDE_COLUMNS,
           (_key(row[0], row[1]),) + tuple(row), deleted=deleted)


def record_signoff(row):
    """One monthly sign-off, keyed by learner, programme, month and role."""
    if row is None:
        return
    record('monthly_audit_signoffs', SIGNOFF_COLUMNS,
           (_key(row[0], row[1], row[2], row[3]),) + tuple(row))
