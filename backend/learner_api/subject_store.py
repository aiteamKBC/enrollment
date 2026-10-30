"""New learner attempts and subject covers; never modifies Last_audit.

Provisioning is owner-run SQL. There is deliberately no ensure/create helper.
"""

import json
from uuid import uuid4

from django.db import connections, transaction

from .subject_content import grade_quiz

ALIAS = 'enrolment'
ATTEMPTS = '"Learner".subject_activity_attempts'
COVERS = '"Learner".subject_covers'


class StoreUnavailable(ValueError):
    pass


def _rows(cursor):
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def ready(cursor):
    cursor.execute('SELECT to_regclass(%s), to_regclass(%s)', [ATTEMPTS, COVERS])
    row = cursor.fetchone()
    return bool(row and row[0] and row[1])


def covers_for(subject_refs):
    """Only owned catalogue covers; completion comes from the canonical ledger.

    Unlike state(), this never aggregates attempts or reads every course cover.
    Callers must resolve the allowed subject references before calling it.
    """
    refs = list(dict.fromkeys(subject_refs))
    if not refs:
        return {}
    with connections[ALIAS].cursor() as cur:
        if not ready(cur):
            return {}
        cur.execute(f'SELECT subject_ref,storage_path FROM {COVERS} WHERE subject_ref=ANY(%s)', [refs])
        return dict(cur.fetchall())


def state(enrolment_id, aptem_id, activity_id=None, *, group_id=None, catalogue_kind='material'):
    with connections[ALIAS].cursor() as cur:
        if not ready(cur):
            return {'ready': False, 'progress': [], 'history': [], 'covers': {}}
        cur.execute(f'''SELECT group_id, activity_id,
                               coalesce(definition->>'catalogue_kind','material') AS catalogue_kind,
                               bool_or(completed) AS completed,
                               max(score_percent) AS best_percent,
                               count(*) AS attempt_count
                        FROM {ATTEMPTS} WHERE enrolment_id=%s AND aptem_id=%s
                          AND submitted_at IS NOT NULL GROUP BY group_id, activity_id,
                            coalesce(definition->>'catalogue_kind','material')''', [enrolment_id, aptem_id])
        progress = _rows(cur)
        history = []
        if activity_id is not None and group_id is not None:
            cur.execute(f'''SELECT id, kind, answers, score_percent, passed, completed,
                                   reading_confirmed, started_at, submitted_at, definition
                            FROM {ATTEMPTS} WHERE enrolment_id=%s AND aptem_id=%s
                              AND group_id=%s AND activity_id=%s AND submitted_at IS NOT NULL
                              AND coalesce(definition->>'catalogue_kind','material')=%s
                            ORDER BY submitted_at DESC, id DESC''', [enrolment_id, aptem_id, group_id, activity_id, catalogue_kind])
            history = _rows(cur)
            for row in history:
                row['answers'] = _json(row['answers'])
                definition = _json(row.pop('definition'))
                if definition.get('studyTime'):
                    row['study_time'] = definition['studyTime']
                questions = (definition.get('quiz') or {}).get('questions', [])
                row['answer_review'] = [{
                    'question': question['text'],
                    'selected': [option['text'] for option in question['options'] if option['id'] in (row['answers'] or {}).get(question['id'], [])],
                    'correct': set((row['answers'] or {}).get(question['id'], [])) == set(question['solution_ids']),
                } for question in questions]
        cur.execute(f'SELECT subject_ref, storage_path FROM {COVERS}')
        covers = dict(cur.fetchall())
    return {'ready': True, 'progress': progress, 'history': history, 'covers': covers}


def start(enrolment_id, aptem_id, group_id, activity_id, definition, *, attempt_id=None, timing_session=None):
    attempt_id = attempt_id or str(uuid4())
    snapshot = {key: definition.get(key) for key in ('title', 'quiz', 'has_reading', 'available')}
    snapshot['catalogue_kind'] = definition.get('catalogue_kind', 'material')
    if definition.get('retakeTimeChoice') is not None:
        from .retake_timing import choice
        snapshot['retakeTimeChoice'] = choice(definition['retakeTimeChoice'])
    if timing_session is not None:
        snapshot['studyTimingSession'] = timing_session
    if snapshot['catalogue_kind'] not in {'material', 'quiz'}:
        raise ValueError('Invalid activity kind.')
    from .progress_transfer_store import lock_learner, assert_legacy_writable
    with lock_learner(enrolment_id) as cur:
        assert_legacy_writable(cur, enrolment_id, group_id)
        if not ready(cur):
            raise StoreUnavailable('Saving activities is not enabled yet. Please contact your learning team.')
        cur.execute(f'''INSERT INTO {ATTEMPTS}
                        (id,enrolment_id,aptem_id,group_id,activity_id,kind,definition)
                        VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)''',
                    [attempt_id, enrolment_id, aptem_id, group_id, activity_id,
                     'quiz' if definition.get('quiz') else 'activity', json.dumps(snapshot)])
    return attempt_id


def finish(enrolment_id, aptem_id, group_id, activity_id, attempt_id, answers, reading_confirmed, *, catalogue_kind='material', learner_kind=None, timing_payload=None):
    from .retake_timing import choice
    retake_choice = choice((timing_payload or {}).get('retakeTimeChoice'))
    from .progress_transfer_store import lock_learner, assert_legacy_writable
    with lock_learner(enrolment_id):
        with connections[ALIAS].cursor() as cur:
            assert_legacy_writable(cur, enrolment_id, group_id)
            if not ready(cur):
                raise StoreUnavailable('Saving activities is not enabled yet. Please contact your learning team.')
            cur.execute(f'''SELECT definition, submitted_at, score_percent, passed, completed
                            FROM {ATTEMPTS} WHERE id=%s AND enrolment_id=%s AND aptem_id=%s
                              AND group_id=%s AND activity_id=%s
                              AND coalesce(definition->>'catalogue_kind','material')=%s FOR UPDATE''',
                        [attempt_id, enrolment_id, aptem_id, group_id, activity_id, catalogue_kind])
            row = cur.fetchone()
            if not row:
                raise LookupError('Attempt not found.')
            if row[1] is not None:
                # Retrying a successful network request cannot add another result.
                saved_time = _json(row[0]).get('studyTime')
                return {'score_percent': row[2], 'passed': row[3], 'completed': row[4], 'already_submitted': True,
                        **({'study_time': saved_time} if saved_time else {})}
            definition = _json(row[0])
            if definition.get('has_reading') and reading_confirmed is not True:
                raise ValueError('Confirm that you have completed the reading material.')
            if not definition.get('quiz') and reading_confirmed is not True:
                raise ValueError('Confirm that you have completed this activity.')
            result = grade_quiz(definition, answers) if definition.get('quiz') else {
                'score_percent': None, 'passed': None,
            }
            if not definition.get('available'):
                raise ValueError('This activity has no available content.')
            result['completed'] = result['passed'] is True if definition.get('quiz') else True
            from .subject_timing import completion_time
            measured = completion_time(definition, timing_payload or {}, kind=learner_kind,
                learner_id=enrolment_id, group_id=group_id, activity_id=activity_id, attempt_id=attempt_id)
            if measured is not None:
                if retake_choice is not None:
                    measured['retakeTimeChoice'] = retake_choice
                if definition.get('retakeTimeChoice') is not None:
                    measured['retakeTimeChoice'] = definition['retakeTimeChoice']
                # Same locked attempt and transaction as grading: retrying a
                # Finish cannot award time twice or leave time without a result.
                cur.execute(f'''UPDATE {ATTEMPTS} SET definition=jsonb_set(definition,'{{studyTime}}',%s::jsonb)
                                WHERE id=%s''', [json.dumps(measured), attempt_id])
                result['study_time'] = measured
            cur.execute(f'''UPDATE {ATTEMPTS} SET answers=%s::jsonb,score_percent=%s,passed=%s,
                               completed=%s,reading_confirmed=%s,submitted_at=CURRENT_TIMESTAMP
                            WHERE id=%s''', [json.dumps(answers), result['score_percent'], result['passed'],
                                           result['completed'], reading_confirmed is True, attempt_id])
    return result


def save_cover(subject_ref, storage_path, account_id):
    with connections[ALIAS].cursor() as cur:
        if not ready(cur):
            raise StoreUnavailable('Uploading subject covers is not enabled yet.')
        cur.execute(f'''INSERT INTO {COVERS} (subject_ref,storage_path,updated_by)
                        VALUES (%s,%s,%s) ON CONFLICT (subject_ref) DO UPDATE
                        SET storage_path=EXCLUDED.storage_path,updated_by=EXCLUDED.updated_by,
                            updated_at=CURRENT_TIMESTAMP''', [subject_ref, storage_path, account_id])


def overlay_progress(items, progress):
    # An activity can be reused in several courses. Each placement has its own
    # attempts, just as the original LMS keys results by learner/course/activity.
    by_id = {(row['group_id'], row['activity_id'], row.get('catalogue_kind', 'material')): row
             for row in progress if row.get('group_id') is not None}
    for item in items:
        old_completed = bool(item['completed'])
        maximum, score = item.get('quiz_maximum_score'), item.get('quiz_score')
        old_percent = float(score) / float(maximum) * 100 if score is not None and maximum and float(maximum) > 0 else None
        item['historical_completed'] = old_completed
        item['historical_score_percent'] = old_percent
        new = by_id.get((item['group_id'], item['source_activity_id'], item.get('catalogue_kind', 'material')), {})
        new_percent = float(new['best_percent']) if new.get('best_percent') is not None else None
        values = [value for value in (old_percent, new_percent) if value is not None]
        item['best_score_percent'] = max(values) if values else None
        item['new_attempt_count'] = new.get('attempt_count', 0)
        item['completed'] = old_completed or bool(new.get('completed'))
        # Hours deliberately remain the audited source hours, never attempt count.
    return items
