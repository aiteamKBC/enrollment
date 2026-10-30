"""Recording what an auditor changed, for the Audit Trail.

The ``"Audit"`` schema's override and annotation tables are all upserts: a
correction overwrites the previous one in place. Each carries ``updated_by`` and
``updated_at``, so the row says who touched it *last* -- and nothing at all says
what it held before, or who set that. An auditor moving a piece of evidence from
one month to another moves which month its hours count in, and until now that
edit left only a timestamp behind.

Two things this module exists to get right.

**The sandbox must never write live history.** ``/hours_test_api/`` serves these
same views against ``audit_clone``, a separate Neon branch, precisely so hours
can be edited without touching live data. But ``versioning`` writes its
revisions through the default connection whatever alias the business write
used, so recording a clone edit would put a HOURS-TEST change into the *live*
trail, described as though it had happened to a real learner. That is worse than
not recording it, so :func:`record_audit_rows` refuses in clone mode and says so
in the log rather than silently.

**Actual hours are deliberately not here.** ``actual_hours`` already has a
purpose-built history that is stronger than this one:
``"Last_audit"."activity_actual_hours_revision"`` stores the previous and
proposed values, who proposed and who decided, each with their source, a
snapshot of the source state, a base fingerprint that makes a stale approval
impossible, and the rule version it was computed under. Duplicating that into
the generic trail would add a second, weaker account of the same event.
"""
from __future__ import annotations

import logging

from .db_source import is_clone

logger = logging.getLogger(__name__)


def _sql(columns):
    """The columns as a `returning` fragment, quoted for mixed-case safety."""
    return ', '.join(f'"{column}"' for column in columns)


#: One auditor annotation on a curriculum component.
ANNOTATION_COLUMNS = (
    'component_id', 'planned_hours', 'mapped_ksbs', 'updated_by', 'updated_at',
)
ANNOTATION_SQL = _sql(ANNOTATION_COLUMNS)

#: An auditor's correction to one imported activity. ``payload`` is the
#: correction and ``source_payload`` is what the import actually said, so a diff
#: between revisions shows the correction being changed rather than the activity.
#: ``override_key`` is synthetic: the table's primary key is
#: ``(aptem_id, activity_id)`` and ``versioning`` indexes history by ONE column,
#: so keying on ``activity_id`` alone would file two learners' corrections to
#: activities that happen to share an id under the same entity -- and each would
#: read as an edit to the other's.
ACTIVITY_OVERRIDE_COLUMNS = (
    'override_key', 'aptem_id', 'activity_id', 'operation', 'payload', 'source_payload',
    'updated_by', 'updated_at', 'created_at',
)
ACTIVITY_OVERRIDE_RETURNING = (
    'aptem_id', 'activity_id', 'operation', 'payload', 'source_payload',
    'updated_by', 'updated_at',
    # Last, so every positional read above is unchanged. Not a snapshot column
    # (the registration lists its own): it is read so a correction this write
    # inserted -- both stamps from the insert's `now()` -- reads as created,
    # while the upsert's `updated_at = now()` keeps an older one an edit.
    'created_at',
)
ACTIVITY_OVERRIDE_SQL = _sql(ACTIVITY_OVERRIDE_RETURNING)

#: Auditor corrections to a learner's profile and employer fields.
PROFILE_OVERRIDE_COLUMNS = ('learner_id', 'values', 'updated_by', 'updated_at')
PROFILE_OVERRIDE_SQL = _sql(PROFILE_OVERRIDE_COLUMNS)

#: Auditor-managed evidence: uploads, selections and date overlays.
#: ``azure_container`` and ``azure_blob_name`` are not collected -- together they
#: locate the file in storage, which is a way to fetch it rather than a fact
#: about it.
EVIDENCE_OVERRIDE_COLUMNS = (
    'evidence_id', 'learner_id', 'is_uploaded', 'document_name', 'component_name',
    'evidence_kind', 'evidence_status', 'evidence_date',
    'source_evidence_id', 'source_activity_id', 'source_activity_month',
    'source_activity_category', 'uploaded_by', 'updated_at',
    # Read for the created-or-first-recorded decision only; the registration
    # keeps its own snapshot list. Every update path moves `updated_at`.
    'created_at',
)
EVIDENCE_OVERRIDE_SQL = _sql(EVIDENCE_OVERRIDE_COLUMNS)


def record_activity_override(row):
    """One activity correction, keyed by learner and activity together."""
    if row is None:
        return
    key = ':'.join('' if part is None else str(part) for part in (row[0], row[1]))
    record_audit_rows('activity_overrides', ACTIVITY_OVERRIDE_COLUMNS, (key,) + tuple(row))


def record_audit_rows(table, columns, row, *, deleted=False):
    """Record one auditor write. Never raises.

    ``row`` is the tuple the write's ``returning`` clause produced, in the order
    of ``columns``. A missing row (nothing matched) records nothing.
    """
    if row is None:
        return
    if is_clone():
        # Deliberately silent about the values: a HOURS-TEST edit is not a fact
        # about any learner, and the point of this branch is that it leaves no
        # trace in the live account of who changed what.
        logger.debug('HOURS-TEST write to %s not recorded in the live trail.', table)
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
        logger.warning('Could not record an auditor write to %s.', table, exc_info=True)
