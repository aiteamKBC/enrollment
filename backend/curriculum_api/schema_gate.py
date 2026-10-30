"""Schema ownership boundary for the Curriculum app.

Background
----------
Curriculum request handlers used to call ``ensure_*_tables()`` helpers that
issued ``CREATE SCHEMA`` / ``CREATE TABLE`` / ``ALTER TABLE`` / ``CREATE INDEX``
— and, worse, historical data backfills — on the way to serving an ordinary
read. That had three consequences:

1. A plain ``GET`` could not run against a read-only connection.
2. Schema drifted implicitly at runtime instead of through migrations, so a
   dropped column silently reappeared on the next process restart.
3. Once foreign keys exist (migration 0038), a runtime backfill that rewrites a
   ``programme_id`` can raise a foreign-key violation and turn a read endpoint
   into a 500.

Design
------
Schema is owned by Django migrations in production. This module keeps the two
responsibilities apart:

``require_tables(...)``
    Read-only. Verifies the expected tables exist and raises
    ``SchemaNotProvisioned`` naming the missing relations and the migration to
    run. Never mutates anything. This is what request paths use.

``provision_schema(...)``
    Mutating. Only runs where the process is explicitly allowed to own schema —
    the SQLite test runner and local development. Production request paths never
    reach it.

``CURRICULUM_ALLOW_RUNTIME_SCHEMA_BOOTSTRAP`` (settings, default ``False``)
    Escape hatch for local Postgres development where running migrations by hand
    is inconvenient. It is *not* enabled in production.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.db import connection

logger = logging.getLogger(__name__)

CURRICULUM_SCHEMA = 'curriculum'

# The migration that first provisions each table, surfaced in the error so an
# operator knows where to look. Several Curriculum tables predate this app's
# migration history and were renamed into place by 0003/0004, so these point at
# the migration that established the CURRENT name rather than claiming a single
# authoritative CREATE. Tables absent from the map fall back to a generic hint.
TABLE_OWNER_MIGRATION = {
    'learner_activity_display_overrides': 'curriculum_api.0070_learner_activity_display_overrides',
    'learner_activity_display_audit': 'curriculum_api.0070_learner_activity_display_overrides',
    'programmes': 'curriculum_api.0003_rename_clear_curriculum_tables',
    'cohorts': 'curriculum_api.0004_rename_selected_curriculum_tables',
    'weeks': 'curriculum_api.0004_rename_selected_curriculum_tables',
    'ksb_mappings': 'curriculum_api.0004_rename_selected_curriculum_tables',
    'modules': 'curriculum_api.0001_ksb_mapping_source_metadata',
    'components': 'curriculum_api.0001_ksb_mapping_source_metadata',
    'groups': 'curriculum_api.0005_create_groups_table',
    'tutor_module_notifications': 'curriculum_api.0049_tutor_module_notifications',
    'free_courses': 'curriculum_api.0029_rename_free_programme_modules_to_free_courses',
    'free_programme_components': 'curriculum_api.0030_free_courses_week_link_and_ids',
    'free_course_weeks': 'curriculum_api.0034_split_free_courses_and_weeks',
    'live_sessions': 'curriculum_api.0012_livesession_livesessionartifact_and_more',
    'live_session_occurrences': 'curriculum_api.0012_livesession_livesessionartifact_and_more',
    'live_session_attendance': 'curriculum_api.0012_livesession_livesessionartifact_and_more',
    'live_session_artifacts': 'curriculum_api.0012_livesession_livesessionartifact_and_more',
    'live_session_recording_events': 'curriculum_api.0012_livesession_livesessionartifact_and_more',
    'live_session_join_launches': 'curriculum_api.0060_live_session_join_launches_updated_at',
    'live_session_recording_views': 'curriculum_api.0066_live_session_recording_views',
    'quizzes': 'quiz_api.0003_initial',
    'quiz_course_links': 'quiz_api.0002_rename_quiz_course_links_module_catalogue_id',
    'quiz_component_links': 'quiz_api.0002_rename_quiz_course_links_module_catalogue_id',
    # week_templates / week_template_components are provisioned outside the
    # Django migration graph by sql/001_week_templates.sql on Neon.
    # review_templates / review_fields are provisioned the same way, by
    # sql/2026-09-10_curriculum_review_templates.sql on Neon, extended (new
    # review_sections table, section_id/parent_field_id/condition_value
    # columns on review_fields) by
    # sql/2026-09-10_curriculum_review_sections_and_advanced_fields.sql, and
    # again (schedule_anchor_date on review_templates, plus the two new
    # review_occurrence_overrides / review_clash_resolutions tables -- see
    # review_schedule.py) by
    # sql/2026-09-10_curriculum_review_schedule_clash_resolution.sql.
    # review_types is provisioned the same way, by
    # sql/2026-09-13_curriculum_review_types.sql, which also adds
    # review_templates.review_type_id and backfills it from the retired
    # coach_surface column.
    'review_types': 'sql/2026-09-13_curriculum_review_types.sql',
}


class SchemaNotProvisioned(RuntimeError):
    """Required Curriculum tables are absent.

    Raised instead of silently creating them inside a request. Carries the
    missing table names so the handler can return a controlled configuration
    error rather than a raw database failure.
    """

    def __init__(self, missing):
        self.missing = list(missing)
        known = sorted({
            TABLE_OWNER_MIGRATION[table]
            for table in self.missing if table in TABLE_OWNER_MIGRATION
        })
        hint = (
            'Apply ' + ', '.join(known) if known
            else 'Run: python manage.py migrate curriculum_api'
        )
        if any(table not in TABLE_OWNER_MIGRATION for table in self.missing):
            hint += (
                ' (note: week_templates/week_template_components are provisioned'
                ' by sql/001_week_templates.sql, not the migration graph)'
            )
        super().__init__(
            'Curriculum schema is not provisioned. Missing: '
            + ', '.join(f'{CURRICULUM_SCHEMA}.{table}' for table in self.missing)
            + '. ' + hint + '.'
        )


def runtime_bootstrap_allowed():
    """May this process create/alter schema outside a migration?

    True only for the SQLite test runner, or when a developer has explicitly
    opted in locally. Production leaves both off, so schema stays migration-owned.
    """
    if connection.vendor != 'postgresql':
        # SQLite is used exclusively by the test runner, which builds its schema
        # in-process because the historical migrations cannot run there.
        return True
    return bool(getattr(settings, 'CURRICULUM_ALLOW_RUNTIME_SCHEMA_BOOTSTRAP', False))


# The single record of which tables this process has seen. Shared by every
# caller (curriculum_api, quiz_api), so no two of them ask the database the same
# question. Only ever holds tables confirmed PRESENT: a table that exists cannot
# stop existing without a deploy, whereas a table that is absent may be created
# a moment later by a migration or a provisioning script, so "missing" is never
# remembered. Bounded by the number of tables in one schema; cleared by
# reset_verification_cache().
_VERIFIED_TABLES = set()

# Whether the one-shot listing of the schema below has already been taken. The
# listing seeds _VERIFIED_TABLES for every table that exists, which is what
# turns a page load's worth of separate probes into a single round trip.
_SCHEMA_LISTED = False


def _existence_query(tables):
    """SQL + params that return, of `tables`, the names that exist."""
    placeholders = ', '.join(['%s'] * len(tables))
    if connection.vendor == 'postgresql':
        return (
            'select table_name from information_schema.tables '
            f'where table_schema = %s and table_name in ({placeholders})',
            [CURRICULUM_SCHEMA, *tables],
        )
    # SQLite has no schemas; the test runner builds these tables unqualified.
    return (
        f"select name from sqlite_master where type='table' and name in ({placeholders})",
        list(tables),
    )


def _listing_query():
    """SQL + params that return every table name in the schema."""
    if connection.vendor == 'postgresql':
        return (
            'select table_name from information_schema.tables where table_schema = %s',
            [CURRICULUM_SCHEMA],
        )
    return ("select name from sqlite_master where type='table'", [])


def _fetch_names(sql, params):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return {row[0] for row in cursor.fetchall()}


def _existing_tables(tables):
    """Which of `tables` exist, in one round trip.

    Takes a listing of the whole schema the first time it is called in a
    process and remembers every table in it, so the probes a single page load
    would otherwise make -- one per table, each a separate trip to a remote
    database -- collapse into that one query. Tables the listing did not name
    are still asked about directly: the listing is a snapshot, and treating
    "absent when we looked" as permanent would hide a table provisioned since.
    """
    global _SCHEMA_LISTED
    if not _SCHEMA_LISTED:
        _VERIFIED_TABLES.update(_fetch_names(*_listing_query()))
        _SCHEMA_LISTED = True
    present = {table for table in tables if table in _VERIFIED_TABLES}
    unknown = [table for table in tables if table not in present]
    if unknown:
        present |= _fetch_names(*_existence_query(unknown))
    return present


def _table_exists(table):
    """Single-table probe. Kept for callers that ask about exactly one table."""
    return table in _existing_tables([table])


def table_exists(table):
    """Does this one table exist in the Curriculum schema?

    Public because curriculum_api.views asks the same question on its own read
    paths. Routing it here means the two share one answer -- and one round trip
    -- rather than each probing information_schema for a schema neither of them
    can change. Raises whatever the database raised; the caller decides what an
    unanswerable probe means.
    """
    return _table_exists(table)


def require_tables(*tables):
    """Verify tables exist. Read-only; raises SchemaNotProvisioned if not.

    Results are memoised because schema does not change under a running process
    without a deploy. A negative result is never cached, so a transient error
    cannot pin a table to "missing" for the process lifetime.

    Every table named here is required; the optional ones are handled by the
    callers, which only reach this gate once runtime_bootstrap_allowed() says
    this process does not own schema.
    """
    seen = set()
    unverified = [
        table for table in tables
        if table not in _VERIFIED_TABLES and not (table in seen or seen.add(table))
    ]
    if not unverified:
        return

    try:
        present = _existing_tables(unverified)
    except Exception:
        # Do not mask a connectivity problem as a schema problem; let the
        # caller's own query surface the real database error.
        logger.debug(
            'Could not verify %s.%s',
            CURRICULUM_SCHEMA, ', '.join(unverified), exc_info=True,
        )
        return

    _VERIFIED_TABLES.update(present)
    missing = [table for table in unverified if table not in present]
    if missing:
        raise SchemaNotProvisioned(missing)


def reset_verification_cache():
    """Forget verified tables (test isolation / after provisioning)."""
    global _SCHEMA_LISTED
    _VERIFIED_TABLES.clear()
    _SCHEMA_LISTED = False
