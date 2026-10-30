"""Learner KSB rows that must not count as achievement of a programme's standard.

The KSB standards migration (backend/sql/2026-10-14_ksb_standards_migration)
leaves a small number of historical "Learner".learner_progress_ksbs rows exactly
as they were -- manual group headings such as B2 "Creative Thinker", rows that
belong to another standard, rows with no programme -- and registers them in
"Learner".learner_progress_ksb_exclusions instead. Their codes collide with the
standard's own codes (manual B2 is not standard B2), so every reader that turns
progress rows into *achievement* must skip them; readers that only show
activity history may keep listing them.

A registered row is skipped only while its stored code still equals the
registered code and its status is not 'released'. If curriculum sync later
rewrites the activity's KSB rows from current mappings, the new rows are not
skipped -- they carry the current, standard codes.
"""
import time

from django.db import DatabaseError, connections

EXCLUSION_TABLE = '"Learner".learner_progress_ksb_exclusions'

# A table that exists stays existing; a missing one is re-checked after this
# many seconds, so the apply step takes effect without a restart.
_MISSING_RECHECK_SECONDS = 60
_exists_cache = {}


def exclusions_table_exists(using='default'):
    """Whether the exclusion registry exists on this connection's database."""
    cached = _exists_cache.get(using)
    if cached is not None:
        exists, checked_at = cached
        if exists or time.monotonic() - checked_at < _MISSING_RECHECK_SECONDS:
            return exists
    connection = connections[using]
    if connection.vendor != 'postgresql':
        exists = False
    else:
        try:
            with connection.cursor() as cursor:
                cursor.execute('select to_regclass(%s) is not null', [EXCLUSION_TABLE])
                exists = bool(cursor.fetchone()[0])
        except DatabaseError:
            exists = False
    _exists_cache[using] = (exists, time.monotonic())
    return exists


def and_not_excluded(alias='k', connection=None):
    """' AND <predicate>' for a JOIN ... ON or WHERE, or '' when the registry
    cannot apply (no real Django connection, a non-PostgreSQL database, or no
    registry yet). ``connection`` is the Django connection the query runs on."""
    using = getattr(connection, 'alias', None)
    if not isinstance(using, str) or not exclusions_table_exists(using):
        return ''
    return ' AND ' + not_excluded_sql(alias, using)


def not_excluded_sql(alias='k', using='default'):
    """SQL predicate: the learner_progress_ksbs row ``alias`` is not excluded.

    Always valid SQL: '1 = 1' when the registry does not exist (before the
    migration, or on sqlite test databases).
    """
    if not exclusions_table_exists(using):
        return '1 = 1'
    return (
        f'not exists (select 1 from {EXCLUSION_TABLE} ksb_x'
        f' where ksb_x.progress_id = {alias}.progress_id'
        f' and ksb_x.position = {alias}.position'
        f" and ksb_x.ksb_code = upper(btrim({alias}.ksb_code))"
        f" and ksb_x.status <> 'released')"
    )
