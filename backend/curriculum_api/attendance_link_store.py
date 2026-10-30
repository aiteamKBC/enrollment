"""Persistence for reviewed identities; no schema provisioning or external calls."""
import json
from collections import defaultdict

from django.db import DatabaseError, connections, transaction
from .session_results_policy import attendance_name_key


def identity_rules(module_ids, database='default'):
    if not module_ids:
        return []
    try:
        with transaction.atomic(using=database), connections[database].cursor() as cursor:
            cursor.execute('''SELECT module_catalogue_id,identity_kind,identity_value,
                learner_profile_id,canonical_email,display_name,active,revision
                FROM curriculum.live_session_attendance_identity_rules
                WHERE module_catalogue_id=ANY(%s)''', [list(module_ids)])
            columns = [column[0] for column in cursor.description]
            saved = [dict(zip(columns, row)) for row in cursor.fetchall()]
    except DatabaseError as error:
        if getattr(error.__cause__ or error, 'sqlstate', None) != '42P01':
            raise
        # Existing attendance remains readable before the additive migration.
        saved = []
    # Preserve existing reviewed guest matches across the same module as well.
    # The old table did not store a destination module. Infer a module only when its training-plan roster and explicit shared-source
    # ownership identify exactly one destination for the learner.
    # Conflicting legacy decisions for one name never become a recurring rule.
    groups = defaultdict(list)
    try:
        with transaction.atomic(using=database), connections[database].cursor() as cursor:
            cursor.execute('''WITH candidates AS (
                SELECT DISTINCT l.id,target.module_catalogue_id,l.display_name,
                    l.learner_profile_id,l.canonical_email
                FROM curriculum.live_session_attendance_identity_links l
                JOIN curriculum.live_session_occurrences o ON o.id=l.occurrence_id
                JOIN curriculum.live_sessions s ON s.id=o.live_session_id
                JOIN curriculum.modules target ON
                    (target.module_catalogue_id=s.module_catalogue_id
                     OR target.teams_shared_source_module_id=s.module_catalogue_id)
                    AND target.deleted_at IS NULL
                JOIN "Learner".learner_training_plan_modules p
                  ON p.module_ref=target.module_catalogue_id AND p.learner_id=l.learner_profile_id
            ), scoped AS (SELECT candidates.*,count(*) OVER (PARTITION BY id) AS module_count FROM candidates)
            SELECT module_catalogue_id,display_name,learner_profile_id,canonical_email
            FROM scoped WHERE module_count=1 AND module_catalogue_id=ANY(%s)''', [list(module_ids)])
            for module_id, name, learner_id, email in cursor.fetchall():
                key = attendance_name_key(name)
                if key:
                    groups[(module_id, key)].append((name, learner_id, email))
    except DatabaseError as error:
        if getattr(error.__cause__ or error, 'sqlstate', None) != '42P01':
            raise
    existing = {(row['module_catalogue_id'], row['identity_kind'], row['identity_value']) for row in saved}
    for (module_id, name_key), candidates in groups.items():
        if (module_id, 'guest_name', name_key) in existing or len({(row[1], row[2]) for row in candidates}) != 1:
            continue
        name, learner_id, email = candidates[0]
        saved.append({'module_catalogue_id': module_id, 'identity_kind': 'guest_name',
                      'identity_value': name_key, 'learner_profile_id': learner_id,
                      'canonical_email': email, 'display_name': name, 'active': True, 'revision': 0})
    return saved


def save_rule(cursor, module_id, spec, before, after, actor):
    """Caller locks the module and wraps the rule plus its audit in a transaction."""
    kind, value = spec
    revision = int((before or {}).get('revision') or 0) + 1
    after = {**after, 'revision': revision, 'identity_kind': kind, 'identity_value': value}
    cursor.execute('''INSERT INTO curriculum.live_session_attendance_identity_rules
        (module_catalogue_id,identity_kind,identity_value,learner_profile_id,
         canonical_email,display_name,active,revision)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (module_catalogue_id,identity_kind,identity_value) DO UPDATE SET
          learner_profile_id=EXCLUDED.learner_profile_id,canonical_email=EXCLUDED.canonical_email,
          display_name=EXCLUDED.display_name,active=EXCLUDED.active,revision=EXCLUDED.revision,
          updated_at=CURRENT_TIMESTAMP''', [module_id, kind, value,
            after['learner_profile_id'], after['canonical_email'], after['display_name'], after['active'], revision])
    cursor.execute('''INSERT INTO curriculum.live_session_attendance_identity_audit
        (module_catalogue_id,identity_kind,identity_value,revision,actor,before_snapshot,after_snapshot)
        VALUES (%s,%s,%s,%s,%s,%s,%s)''', [module_id, kind, value, revision, actor,
            json.dumps(before or {}, default=str), json.dumps(after, default=str)])
    return after
