"""Carry a programme's or group's new name to the learners placed on it.

Curriculum children already follow a rename: they hold the parent's id, and
propagate_programme_name refreshes the copy of the name they carry. Learner
records store names too -- the enrolment row ("Created_users") holds nothing
else, and the learner profile keeps the name beside its id -- so a rename left
both saying where the learner had been. Learner reads now follow the profile's
ids (learner_api.placement_links), but the enrolment screens and reports still
show these names, and a learner without an id is still found by them.

Same rules as cohort_rename.propagate_cohort_name:

* a record is renamed through its profile's id; one with no id only by the
  exact old name, and only while no other record answers to the old or the
  new name -- an ambiguous label is left alone rather than guessed;
* only the old name is replaced, so no learner changes programme or group;
* every write uses the caller's connection, so inside the save's transaction a
  failure rolls the rename back with it.
"""
from django.db import connection


def _s(value):
    return str(value or '').strip()


def _refresh(affected, reason):
    # Raw updates, like cohort_rename's; invalidate the same learner projections
    # an ordinary record save would.
    from learner_api.read_model import enqueue_learner_home_refresh

    for learner_id, kind in affected:
        if learner_id is not None:
            enqueue_learner_home_refresh(
                'commercial' if _s(kind).casefold() == 'commercial' else 'apprenticeship',
                learner_id, reason=reason, using='default',
            )


def propagate_programme_name_to_learners(programme_id, old_name, name):
    """Rename a programme on the learner records placed on it."""
    programme_id, old_name, name = _s(programme_id), _s(old_name), _s(name)
    if not programme_id or not old_name or not name or old_name == name:
        return
    # Enrolment and learner placement schemas are PostgreSQL-only.
    if connection.vendor != 'postgresql':
        return
    params = {'id': programme_id, 'old': old_name, 'name': name}
    with connection.cursor() as cursor:
        cursor.execute(
            '''SELECT 1 FROM curriculum.programmes
               WHERE name IN (%(old)s, %(name)s) AND programme_id <> %(id)s LIMIT 1''',
            params,
        )
        params['legacy'] = cursor.fetchone() is None
        cursor.execute(
            '''UPDATE "Learner".learners SET programme = %(name)s
               WHERE programme = %(old)s
                 AND (programme_id = %(id)s
                      OR (%(legacy)s AND nullif(btrim(programme_id), '') IS NULL))
               RETURNING enrolment_id, learner_type''',
            params,
        )
        affected = set(cursor.fetchall())
        cursor.execute(
            '''UPDATE enrolment."Created_users" u SET "Programme" = %(name)s
               WHERE u."Programme" = %(old)s
                 AND (EXISTS (
                        SELECT 1 FROM "Learner".learners l
                        WHERE l.enrolment_id = u.id AND l.programme_id = %(id)s)
                      OR (%(legacy)s AND NOT EXISTS (
                        SELECT 1 FROM "Learner".learners l
                        WHERE l.enrolment_id = u.id
                          AND nullif(btrim(l.programme_id), '') IS NOT NULL)))
               RETURNING u.id, u."Learner_type"''',
            params,
        )
        affected.update(cursor.fetchall())
    _refresh(affected, 'programme-rename')


def propagate_group_name_to_learners(group, name):
    """Rename a group on its modules' copy of the name and on its learners.

    ``group`` is the row as it was before the rename.
    """
    from . import views, versioning

    group_id, old_name, name = _s(group.get('group_id')), _s(group.get('group_name')), _s(name)
    if not group_id or not name or name == old_name:
        return
    # Modules carry the group's name beside its id, as they carry the cohort's;
    # the cohort rename refreshes that copy, and so must this.
    with versioning.audit_context(
        actor_type=versioning.ACTOR_SYSTEM, source='recalculation', action='recalculated',
        metadata={'recalculated_from': 'group_rename', 'reason_code': 'group_name_propagation'},
    ):
        views.update_authoring_rows(
            views.AUTHORING_MODULES_TABLE, 'group_id = %s and coalesce(group_name, \'\') <> %s',
            [group_id, name], {'group_name': name},
        )
    if not old_name or connection.vendor != 'postgresql':
        return
    # A record with no group id is matched within its cohort by the old name.
    # Group names repeat across cohorts, so that needs this group's cohort, and
    # no other live group there answering to the old or the new name.
    params = {
        'id': group_id, 'old': old_name, 'name': name,
        'cohort_id': _s(group.get('cohort_id')),
        'programme': _s(group.get('programme_name')), 'cohort': _s(group.get('cohort_name')),
    }
    with connection.cursor() as cursor:
        cursor.execute(
            '''SELECT 1 FROM curriculum.groups
               WHERE cohort_id = %(cohort_id)s AND group_id <> %(id)s
                 AND group_name IN (%(old)s, %(name)s) AND deleted_at IS NULL LIMIT 1''',
            params,
        )
        params['legacy'] = bool(params['cohort_id']) and cursor.fetchone() is None
        cursor.execute(
            '''UPDATE "Learner".learners SET group_name = %(name)s
               WHERE group_name = %(old)s
                 AND (group_id = %(id)s
                      OR (%(legacy)s AND nullif(btrim(group_id), '') IS NULL
                          AND (cohort_id = %(cohort_id)s
                               OR (nullif(btrim(cohort_id), '') IS NULL
                                   AND programme = %(programme)s AND cohort = %(cohort)s))))
               RETURNING enrolment_id, learner_type''',
            params,
        )
        affected = set(cursor.fetchall())
        cursor.execute(
            '''UPDATE enrolment."Created_users" u SET "Group" = %(name)s
               WHERE u."Group" = %(old)s
                 AND (EXISTS (
                        SELECT 1 FROM "Learner".learners l
                        WHERE l.enrolment_id = u.id AND l.group_id = %(id)s)
                      OR (%(legacy)s
                          AND NOT EXISTS (
                            SELECT 1 FROM "Learner".learners l
                            WHERE l.enrolment_id = u.id
                              AND nullif(btrim(l.group_id), '') IS NOT NULL)
                          AND (EXISTS (
                                 SELECT 1 FROM "Learner".learners l
                                 WHERE l.enrolment_id = u.id AND l.cohort_id = %(cohort_id)s)
                               OR (u."Programme" = %(programme)s AND u."Cohort" = %(cohort)s
                                   AND NOT EXISTS (
                                     SELECT 1 FROM "Learner".learners l
                                     WHERE l.enrolment_id = u.id
                                       AND nullif(btrim(l.cohort_id), '') IS NOT NULL)))))
               RETURNING u.id, u."Learner_type"''',
            params,
        )
        affected.update(cursor.fetchall())
    _refresh(affected, 'group-rename')
