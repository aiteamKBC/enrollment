"""Keep earned quiz results attached to the curriculum slot they were earned in.

A quiz attempt is stored against the quiz it graded, not the plan component
that offered it. Authors later re-point a quiz component at a different quiz,
or remove the component, and the learner's result then matches nothing in
their plan: the module reads as if the work was never done.

This is a read-only projection. It resolves the slot from the curriculum's own
history (the component's current link and its recorded revisions) and never
rewrites the learner's progress records or the curriculum.
"""
import logging

from django.db import DatabaseError, connections

logger = logging.getLogger(__name__)

SLOTS_SQL = '''
    WITH candidates AS (
        SELECT c.id, c.week_id, c.title, c.module_catalogue_id,
               c.settings_json->>'linkedQuizId' AS current_quiz
        FROM curriculum.components c
        WHERE c.module_catalogue_id = ANY(%s) AND c.type = 'quiz'
    ), links AS (
        SELECT id, current_quiz AS quiz_id FROM candidates WHERE current_quiz = ANY(%s)
        UNION
        SELECT r.entity_id, r.snapshot->'settings_json'->>'linkedQuizId'
        FROM curriculum.record_revisions r
        JOIN candidates c ON c.id = r.entity_id
        WHERE r.entity_type = 'component'
          AND r.snapshot->'settings_json'->>'linkedQuizId' = ANY(%s)
    )
    SELECT c.id, c.module_catalogue_id, c.week_id, w.title, c.title, links.quiz_id
    FROM links
    JOIN candidates c ON c.id = links.id
    LEFT JOIN curriculum.weeks w ON w.id = c.week_id
'''


def _text(value):
    return str(value).strip() if value is not None else ''


def unmatched_quiz_attempts(detail):
    """Attempts whose quiz is not linked by any component in the current plan."""
    components = [c for c in detail.get('components') or [] if isinstance(c, dict)]
    linked = {_text((c.get('quizMeta') or {}).get('quizId')) for c in components if c.get('isQuiz')}
    present = {_text(c.get('componentId')) for c in components if c.get('componentId')}
    return [
        attempt for attempt in detail.get('quizAttempts') or []
        if isinstance(attempt, dict) and _text(attempt.get('quizId'))
        and _text(attempt.get('quizId')) not in linked
        and _text(attempt.get('componentId')) not in present
    ]


def read_quiz_slots(module_ids, quiz_ids):
    """Quiz components in these modules that link, or once linked, these quizzes."""
    with connections['enrolment'].cursor() as cursor:
        cursor.execute(SLOTS_SQL, [module_ids, quiz_ids, quiz_ids])
        return [
            {'componentId': row[0], 'moduleId': row[1], 'weekId': row[2],
             'week': row[3], 'title': row[4], 'quizId': row[5]}
            for row in cursor.fetchall()
        ]


def attach_quiz_slots(detail, attempts, slots):
    """Credit each unmatched attempt to the one slot it can only have come from.

    A component still in the plan that once linked the quiz takes the attempt
    (the response carries its componentId; the stored row is untouched). A
    passed quiz whose only slot was removed is listed in retiredQuizComponents
    so its module can still show the completed work. Anything ambiguous is
    left exactly as it was rather than guessed by title or position.
    """
    components = [c for c in detail.get('components') or [] if isinstance(c, dict)]
    present = {_text(c.get('componentId')) for c in components if c.get('componentId')}
    module_titles = {_text(c.get('moduleId')): c.get('module') for c in components if c.get('moduleId')}
    by_quiz = {}
    for slot in slots:
        by_quiz.setdefault(_text(slot.get('quizId')), {})[_text(slot.get('componentId'))] = slot
    retired = {}
    for attempt in attempts:
        options = by_quiz.get(_text(attempt.get('quizId')), {})
        current = [component_id for component_id in options if component_id in present]
        if len(current) == 1:
            attempt['componentId'] = current[0]
            continue
        removed = [component_id for component_id in options if component_id not in present]
        if current or attempt.get('passed') is not True or len(removed) != 1:
            continue
        slot = options[removed[0]]
        retired.setdefault(removed[0], {
            'module': module_titles.get(_text(slot.get('moduleId'))),
            'week': slot.get('week'),
            'component': _text(slot.get('title')),
            'moduleId': slot.get('moduleId'),
            'weekId': slot.get('weekId'),
            'componentId': removed[0],
            'type': 'quiz',
            'isQuiz': True,
            'expectedOtjh': None,
            # No question count: a removed quiz is a record, not something to sit.
            'quizMeta': {'quizId': attempt.get('quizId'), 'questions': None, 'duration': None, 'timeUnit': None},
            'retired': True,
        })
    detail['retiredQuizComponents'] = list(retired.values())
    return detail


def retain_quiz_progress(detail, *, completed_only=False, strict=False):
    """Attach historical quiz slots needed by this projection.

    Compact learner summaries calculate completion only. Failed attempts remain
    in ``quizAttempts`` for history, but resolving their former curriculum slot
    cannot change a module's completed count. Avoiding that history lookup is
    important for large plans: it otherwise has to inspect every revision of
    every quiz component assigned to the learner.
    """
    detail['retiredQuizComponents'] = []
    attempts = unmatched_quiz_attempts(detail)
    if completed_only:
        attempts = [attempt for attempt in attempts if attempt.get('passed') is True]
    if not attempts:
        return detail
    module_ids = sorted({_text(c.get('moduleId')) for c in detail.get('components') or []
                         if isinstance(c, dict) and c.get('moduleId')})
    if not module_ids:
        return detail
    quiz_ids = sorted({_text(attempt.get('quizId')) for attempt in attempts})
    try:
        slots = read_quiz_slots(module_ids, quiz_ids)
    except DatabaseError as exc:
        if strict:
            raise
        # The plan still renders from current links, as it did before.
        logger.warning('Could not resolve earlier quiz placements: %s', exc)
        return detail
    return attach_quiz_slots(detail, attempts, slots)
