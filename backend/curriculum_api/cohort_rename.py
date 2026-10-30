"""Carry a cohort's display name to its current placement records atomically."""
from django.db import connection


class CohortRenameConflict(ValueError):
    pass


def propagate_cohort_name(cohort, name):
    # Called inside the cohort PATCH transaction, before writing the parent.
    # Names are still stored on enrolments; ambiguous legacy placements must
    # never be moved to a different cohort just because their labels match.
    from . import views, versioning

    cohort_id = cohort['cohort_id']
    old_name = cohort.get('cohort_name') or ''
    programme = cohort.get('programme_name') or ''
    if name == old_name:
        return
    if not connection.in_atomic_block:
        raise RuntimeError('Cohort name propagation requires the parent transaction.')
    with connection.cursor() as cursor:
        cursor.execute(
            f'''SELECT cohort_id FROM {views.authoring_table_name(views.COHORT_AUTHORING_DETAILS_TABLE)}
               WHERE programme_name = %s AND cohort_name IN (%s, %s)
                 AND cohort_id <> %s AND deleted_at IS NULL
                 {'FOR UPDATE' if connection.vendor == 'postgresql' else ''}''',
            [programme, old_name, name, cohort_id],
        )
        if cursor.fetchone():
            raise CohortRenameConflict('Another cohort in this programme has the old or new name. Resolve the ambiguous placement before renaming.')
        affected = set()
        # Enrolment and learner placement schemas are PostgreSQL-only. The
        # curriculum-only SQLite harness has no learner records to propagate.
        if connection.vendor == 'postgresql':
            # All writes use the parent's connection, including the enrolment and
            # profile schemas, so a failure rolls back the entire rename.
            # An enrolment whose profile carries this cohort's id is renamed by
            # that link, even when its programme label has gone stale since; one
            # without an id still needs its programme and old name to match.
            # Either way only the old name is replaced: no learner changes cohort.
            cursor.execute(
                '''UPDATE enrolment."Created_users" u SET "Cohort" = %s
                   WHERE u."Cohort" = %s
                     AND (EXISTS (
                            SELECT 1 FROM "Learner".learners l
                            WHERE l.enrolment_id = u.id AND l.cohort_id = %s)
                          OR (u."Programme" = %s AND NOT EXISTS (
                            SELECT 1 FROM "Learner".learners l
                            WHERE l.enrolment_id = u.id
                              AND nullif(btrim(l.cohort_id), '') IS NOT NULL
                              AND l.cohort_id <> %s)))
                   RETURNING u.id, u."Learner_type"''',
                [name, old_name, cohort_id, programme, cohort_id],
            )
            affected = set(cursor.fetchall())
            cursor.execute(
                '''UPDATE "Learner".learners SET cohort = %s
                   WHERE cohort_id = %s OR
                     (nullif(btrim(cohort_id), '') IS NULL AND programme = %s AND cohort = %s)
                   RETURNING enrolment_id, learner_type''',
                [name, cohort_id, programme, old_name],
            )
            affected.update(cursor.fetchall())
    with versioning.audit_context(
        actor_type=versioning.ACTOR_SYSTEM, source='recalculation', action='recalculated',
        metadata={'recalculated_from': 'cohort_rename', 'reason_code': 'cohort_name_propagation'},
    ):
        for table in (views.GROUPS_TABLE, views.AUTHORING_MODULES_TABLE):
            views.update_authoring_rows(
                table, 'cohort_id = %s and coalesce(cohort_name, \'\') <> %s',
                [cohort_id, name], {'cohort_name': name},
            )
    # Raw updates deliberately avoid save helpers that can rebuild placements.
    # Still invalidate the same learner projections as an ordinary record save.
    from learner_api.read_model import enqueue_learner_home_refresh
    for learner_id, kind in affected:
        if learner_id is not None:
            enqueue_learner_home_refresh(
                'commercial' if str(kind or '').strip().casefold() == 'commercial' else 'apprenticeship',
                learner_id, reason='cohort-rename', using='default',
            )
