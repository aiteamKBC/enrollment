"""Recover original questions by exact exported course/activity lineage."""
import json

from .subject_content import as_list, quiz_definition


def imported_quiz(cursor, group_id, quiz_id, *, for_review=False):
    cursor.execute('''WITH export AS (
        SELECT CASE WHEN jsonb_typeof(curriculum)='string'
          THEN (curriculum #>> '{}')::jsonb ELSE curriculum END AS payload
        FROM "MBA".course_curriculum WHERE course_id=%s)
        SELECT material FROM export
        CROSS JOIN LATERAL jsonb_array_elements(payload->'sections') section
        CROSS JOIN LATERAL jsonb_array_elements(section->'materials') material
        WHERE material->>'source_component_id'=%s
          AND material->>'component_kind'='quiz' ''', [int(group_id), str(quiz_id)])
    candidates = []
    for (material,) in cursor.fetchall():
        # Django's PostgreSQL cursor returns JSONB as text; direct psycopg
        # cursors may already decode it. Support both without changing loaders.
        if isinstance(material, str):
            try:
                material = json.loads(material)
            except ValueError:
                return None
        if not isinstance(material, dict):
            return None
        definition = material.get('quiz_definition') or {}
        if not isinstance(definition, dict):
            return None
        raw_questions = as_list(definition.get('questions'))
        quiz = {'quiz_id': quiz_id, 'quiz_body': material.get('content_html') or '',
                'configured_questions': definition.get('question_count'),
                'maximum_score': 100, 'passing_score': material.get('passing_grade_percent'),
                'questions': [], 'solutions': []}
        for raw in raw_questions:
            if not isinstance(raw, dict):
                return None
            quiz['questions'].append({'question_id': raw.get('question_id'),
                'question_body': raw.get('body_html') or raw.get('question_text') or raw.get('title') or '',
                'question_type': raw.get('question_type'), 'options': raw.get('answer_options')})
            quiz['solutions'].append({'question_id': raw.get('question_id'),
                                      'correct_answer': raw.get('correct_answers')})
        if quiz not in candidates:
            candidates.append(quiz)
    # Conflicting placements must be reviewed, never chosen arbitrarily.
    if len(candidates) > 1:
        return None
    if candidates and candidates[0]['questions']:
        # Reviewing saved learning does not require automatic marking keys.
        if for_review or quiz_definition(candidates[0])['ready']:
            return candidates[0]
    return stored_quiz(cursor, group_id, quiz_id, for_review=for_review)


def stored_quiz(cursor, group_id, quiz_id, *, for_review=False):
    """Read recovered author content only through an active exact course link.

    A quiz can also be embedded in a material in that course. Neither a title
    match nor a learner's saved answer is evidence of the original question bank.
    """
    cursor.execute('''SELECT COALESCE(NULLIF(m.payload->'quiz', 'null'::jsonb),
        jsonb_build_object('quiz_id',m.payload->'quiz_id',
          'questions',m.payload->'quiz_questions','quiz_body',m.payload->'quiz_body',
          'passing_score',m.payload->'quiz_passing_score',
          'maximum_score',m.payload->'quiz_maximum_score'))
        FROM curriculum.source_courses c
        JOIN curriculum.source_activities a ON a.source_course_id=c.id
          AND a.source_system=c.source_system AND a.deleted_at IS NULL
        JOIN curriculum.source_materials m ON m.material_id=a.source_material_id
          AND m.source_system=c.source_system AND m.deleted_at IS NULL
        WHERE c.source_system='old_lms' AND c.source_course_ref=%s AND c.deleted_at IS NULL
          AND ((a.source_activity_kind='quiz' AND a.source_activity_id=%s AND m.material_id=%s)
            OR (a.source_activity_kind='material' AND a.source_activity_id='material:'||m.material_id
              AND COALESCE(m.payload#>>'{quiz,quiz_id}',m.payload->>'quiz_id')=%s))''',
        [str(group_id), f'quiz:{quiz_id}', int(quiz_id), str(quiz_id)])
    candidates = []
    for (value,) in cursor.fetchall():
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return None
        if not isinstance(value, dict) or str(value.get('quiz_id')) != str(quiz_id):
            return None
        definition = quiz_definition(value)
        if not definition['questions'] or (not for_review and not definition['ready']):
            continue
        if value not in candidates:
            candidates.append(value)
    return candidates[0] if len(candidates) == 1 else None
