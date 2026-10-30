"""Admin-only advisory comparisons of locally stored author content. No external IO."""
from collections import Counter
from html import unescape
import json
import math
import re

from django.db import connections
from .source_material_content import hydrate_material


def decoded(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


BODY_FIELDS = ('readingContent', 'contentHtml', 'lessonContent', 'transcript', 'speakerNotes',
               'assignmentBrief', 'submissionInstructions', 'reflectionQuestion',
               'reading_text_body', 'text_body', 'html_body', 'content_html', 'quiz_body')


def has_quiz(value):
    value = decoded(value)
    if not isinstance(value, dict):
        return False
    if value.get('quiz_id') not in (None, '', 0, '0'):
        return True
    if decoded(value.get('quiz_questions')) or decoded(value.get('questions')):
        return True
    return any(has_quiz(value.get(key)) for key in ('quiz', 'quiz_definition'))


def verified_source_types(context, items):
    """Correct composite legacy labels only for verified question-free readings.

    This is private to transfer review/approval; shared catalogue data is untouched.
    """
    completed = {(item['courseRef'][7:], ref) for item in items
                 for ref in item.get('completedSourceActivities',
                     [ref for course, ref in context[6] if str(course) == item['courseRef'][7:]])}
    candidates = {(str(d['source_course_ref']), d['source_activity_id']) for d in context[5]
                  if (str(d['source_course_ref']), d['source_activity_id']) in completed
                  and d['source_activity_id'].startswith('material:')
                  and re.sub(r'[^a-z]', '', str(d.get('source_activity_type', '')).lower()) == 'readingquiz'
                  and not has_quiz(d.get('source_payload'))}
    if not candidates:
        return context
    with connections['enrolment'].cursor() as cursor:
        cursor.execute('''SELECT c.source_course_ref,a.source_activity_id,m.content_type,
            a.source_payload,m.payload,history.quiz_id,history.quiz_questions
            FROM curriculum.source_courses c
            JOIN curriculum.source_activities a ON a.source_course_id=c.id
              AND a.source_system=c.source_system AND a.deleted_at IS NULL
            JOIN curriculum.source_materials m ON m.material_id=a.source_material_id
              AND m.source_system=c.source_system AND m.deleted_at IS NULL
            LEFT JOIN "Last_audit".activities history ON a.source_activity_id='material:' || history.activity_id::text
            WHERE c.source_system='old_lms' AND c.deleted_at IS NULL
              AND c.source_course_ref=ANY(%s) AND a.source_activity_id=ANY(%s)''',
            [sorted({k[0] for k in candidates}), sorted({k[1] for k in candidates})])
        evidence = {}
        for course, ref, kind, metadata, payload, quiz_id, questions in cursor.fetchall():
            key = (str(course), ref)
            if key in candidates:
                evidence.setdefault(key, []).append(
                    str(kind).lower() in {'reading', 'pdf', 'word', 'doc', 'docx', 'text', 'document', 'html'}
                    and not any(has_quiz(v) for v in (metadata, payload, {'quiz_id': quiz_id, 'questions': questions})))
    verified = {key for key, facts in evidence.items() if facts and all(facts)}
    result = list(context)
    result[5] = [{**d, 'source_activity_type': 'reading'} if (str(d['source_course_ref']), d['source_activity_id']) in verified
                 else d for d in context[5]]
    return tuple(result)


def authored_text(value, quiz=False):
    """Whitelist author fields; never compare answers, grades, URLs or learner text."""
    value = decoded(value)
    if not isinstance(value, dict):
        return ''
    if quiz:
        bank = decoded(value.get('quiz')) or decoded(value.get('quiz_definition')) or value
        if not isinstance(bank, dict):
            return ''
        questions = decoded(bank.get('questions') or value.get('quiz_questions'))
        if not isinstance(questions, list):
            return ''
        return ' '.join(str(next((q[k] for k in ('question_body', 'question_text', 'body_html', 'text', 'title')
                                 if isinstance(q.get(k), str) and q[k]), ''))
                        for q in questions if isinstance(q, dict))
    return ' '.join(value[k] for k in BODY_FIELDS if isinstance(value.get(k), str))


def tokens(text):
    text = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', ' ', text, flags=re.I | re.S)
    text = unescape(re.sub(r'<[^>]*>', ' ', text)).casefold()
    stop = {'the', 'a', 'an', 'and', 'or', 'of', 'to', 'in', 'is', 'are', 'for', 'with',
            'what', 'which', 'how', 'this', 'that', 'following', 'question', 'select'}
    return Counter(word for word in re.findall(r'\w+', text) if word not in stop)


def add_scores(review, old_text, new_text):
    """TF-IDF cosine similarity of actual text, scoped to each destination module."""
    for module in review['modules']:
        course = module['courseRef'][7:]
        documents = {t['componentId']: tokens(new_text.get((module['moduleId'], t['componentId']), ''))
                     for t in module['targets']}
        for source in module['sources']:
            origin = tokens(old_text.get((course, source['sourceActivityRef']), ''))
            candidates = [t for t in module['targets'] if t['type'] == source['type'] and source['type'] != 'unknown']
            corpus = [origin] + [documents[t['componentId']] for t in candidates]
            counts = Counter(term for document in corpus for term in document)
            weights = {term: math.log((1 + len(corpus)) / (1 + count)) + 1 for term, count in counts.items()}
            def vector(document):
                return {term: (1 + math.log(count)) * weights[term] for term, count in document.items()}
            left = vector(origin)
            norm = math.sqrt(sum(v * v for v in left.values()))
            scores = []
            for target in candidates:
                right = vector(documents[target['componentId']])
                denom = norm * math.sqrt(sum(v * v for v in right.values()))
                scores.append({'componentId': target['componentId'], 'contentAvailable': bool(right), 'score':
                    round(sum(v * right.get(term, 0) for term, v in left.items()) / denom, 6) if denom else None})
            source['contentAvailable'] = bool(origin)
            source['contentScores'] = scores
    return review


def content_review(review, context):
    modules = review['modules']
    if not modules:
        return review
    wanted = {(m['courseRef'][7:], s['sourceActivityRef']): s['type'] for m in modules for s in m['sources']}
    target_types = {(m['moduleId'], t['componentId']): t['type'] for m in modules for t in m['targets']}
    old_text, new_text, payloads = {}, {}, {}
    courses = sorted({key[0] for key in wanted})
    with connections['enrolment'].cursor() as cursor:
        if courses:
            cursor.execute('''SELECT c.source_course_ref,a.source_activity_id,m.payload,
                  jsonb_build_object('reading_text_body',history.reading_text_body,
                    'quiz_id',history.quiz_id,'quiz_body',history.quiz_body,
                    'quiz_questions',history.quiz_questions)
                FROM curriculum.source_courses c
                JOIN curriculum.source_activities a ON a.source_course_id=c.id
                  AND a.source_system=c.source_system AND a.deleted_at IS NULL
                LEFT JOIN curriculum.source_materials m ON m.material_id=a.source_material_id
                  AND m.source_system=c.source_system AND m.deleted_at IS NULL
                LEFT JOIN "Last_audit".activities history
                  ON a.source_activity_kind='material'
                  AND a.source_activity_id='material:' || history.activity_id::text
                WHERE c.source_system='old_lms' AND c.deleted_at IS NULL
                  AND c.source_course_ref=ANY(%s) AND a.source_activity_id=ANY(%s)''',
                [courses, sorted({key[1] for key in wanted})])
            source_quiz_ids = {}
            for course, ref, payload, historical in cursor.fetchall():
                key = (str(course), ref)
                if key in wanted:
                    # Use the same payload/historical merge as the learner's player.
                    row = hydrate_material({**(decoded(historical) or {}), 'material_payload': payload})
                    text = (authored_text({'quiz_questions': row.get('quiz_questions')}, True) or authored_text(payload, True)) if wanted[key] == 'quiz' else row.get('reading_text_body') or authored_text(payload)
                    payloads.setdefault(key, set()).add(text)
                    qid = row.get('quiz_id')
                    if wanted[key] == 'quiz' and str(qid).isdigit():
                        source_quiz_ids.setdefault(key, set()).add(int(qid))
            # Exported author content supplies sources that have no stored material payload.
            cursor.execute('''WITH exports AS (
                SELECT course_id,CASE WHEN jsonb_typeof(curriculum)='string'
                THEN (curriculum #>> '{}')::jsonb ELSE curriculum END AS payload
                FROM "MBA".course_curriculum WHERE course_id=ANY(%s))
                SELECT course_id,material FROM exports
                CROSS JOIN LATERAL jsonb_array_elements(payload->'sections') section
                CROSS JOIN LATERAL jsonb_array_elements(section->'materials') material
                WHERE material->>'source_component_id'=ANY(%s)''',
                [[int(c) for c in courses], sorted({key[1].split(':')[1] for key in wanted})])
            exports = {}
            for course, raw in cursor.fetchall():
                raw = decoded(raw)
                if not isinstance(raw, dict):
                    continue
                kind = 'quiz' if raw.get('component_kind') == 'quiz' else 'material'
                key = (str(course), f"{kind}:{raw.get('source_component_id')}")
                if key in wanted:
                    exports.setdefault(key, set()).add(authored_text(raw, wanted[key] == 'quiz'))
            for key in wanted:
                texts = {text for text in payloads.get(key, set()) if tokens(text)} or {text for text in exports.get(key, set()) if tokens(text)}
                # Conflicting stored placements are not evidence for a recommendation.
                old_text[key] = next(iter(texts)) if len(texts) == 1 else ''
                # A material's ID can differ from its embedded quiz ID. The
                # existing player also recovers exported questions by quiz ID.
                if not texts and len(source_quiz_ids.get(key, set())) == 1:
                    from .subject_quiz import imported_quiz
                    recovered = imported_quiz(cursor, int(key[0]), next(iter(source_quiz_ids[key])), for_review=True)
                    old_text[key] = authored_text(recovered, True)
        if target_types:
            cursor.execute('''SELECT module_catalogue_id,id,settings_json FROM curriculum.components
                WHERE module_catalogue_id=ANY(%s) AND id=ANY(%s)''',
                [sorted({k[0] for k in target_types}), sorted({k[1] for k in target_types})])
            settings = {(mid, ident): decoded(value) or {} for mid, ident, value in cursor.fetchall()}
            quiz_ids = {}
            for component in context[3]['components']:
                key = (component.get('moduleId'), component.get('componentId'))
                if key not in target_types:
                    continue
                value = settings.get(key, {})
                new_text[key] = authored_text(value, target_types[key] == 'quiz')
                qid = (component.get('quizMeta') or {}).get('quizId') or value.get('linkedQuizId')
                if target_types[key] == 'quiz' and str(qid).isdigit():
                    quiz_ids[key] = int(qid)
            if quiz_ids:
                cursor.execute('''SELECT quiz_id,question_text FROM curriculum.quiz_questions
                    WHERE quiz_id=ANY(%s) AND is_archived=false ORDER BY quiz_id,sort_order,id''',
                    [sorted(set(quiz_ids.values()))])
                banks = {}
                for qid, body in cursor.fetchall():
                    banks.setdefault(qid, []).append(body or '')
                for key, qid in quiz_ids.items():
                    new_text[key] = ' '.join(banks.get(qid, []))
    return add_scores(review, old_text, new_text)
