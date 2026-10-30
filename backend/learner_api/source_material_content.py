"""Convert stored material snapshots into the existing player contract."""
import json


def object_value(value):
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


def hydrate_material(row):
    saved_media = {key: row.get(key) or '' for key in ('video_iframe_url', 'reading_iframe_url', 'audio_url')}
    payload = object_value(row.get('material_payload'))
    schema = dict(payload)
    cleared = set(payload.get('lms_cleared_media_fields') or [])
    kind = str(row.get('material_content_type') or payload.get('content_type') or '').lower()
    row['title'] = row.get('material_title') or payload.get('material_title') or row.get('title')
    row['activity_type'] = row.get('material_content_type') or row.get('activity_type')
    row['reading_text_body'] = next((payload[key] for key in
        ('reading_text_body', 'text_body', 'html_body') if isinstance(payload.get(key), str) and payload[key]), row.get('reading_text_body') or '')
    row['video_iframe_url'] = row.get('material_video_url') or payload.get('video_iframe_url') or ''
    row['reading_iframe_url'] = row.get('material_reading_url') or payload.get('reading_iframe_url') or ''
    row['audio_url'] = row.get('material_audio_url') or payload.get('audio_iframe_url') or ''
    generic = payload.get('iframe_url') or row.get('material_source_url') or ''
    if generic:
        field = 'video_iframe_url' if kind in {'video', 'recording', 'live'} else 'audio_url' if kind in {'audio', 'podcast'} else 'reading_iframe_url'
        source_field = 'audio_iframe_url' if field == 'audio_url' else field
        if source_field not in cleared:
            row[field] = row[field] or generic
    for field, saved in saved_media.items():
        source_field = 'audio_iframe_url' if field == 'audio_url' else field
        if source_field not in cleared:
            row[field] = row[field] or saved
    quiz = object_value(payload.get('quiz'))
    for target, key in (('quiz_id', 'quiz_id'), ('quiz_body', 'quiz_body'),
                        ('quiz_questions', 'questions'), ('quiz_passing_score', 'passing_score'),
                        ('quiz_maximum_score', 'maximum_score')):
        if key in quiz:
            row[target] = quiz[key]
        elif target in payload:
            row[target] = payload[target]
    if not quiz and (row.get('quiz_id') or row.get('quiz_questions')):
        quiz = {'quiz_id': row.get('quiz_id'), 'quiz_body': row.get('quiz_body'),
                'questions': row.get('quiz_questions'), 'passing_score': row.get('quiz_passing_score'),
                'maximum_score': row.get('quiz_maximum_score')}
    schema['quiz'] = quiz or None
    schema['content_type'] = kind
    # Typed URLs above already select the appropriate media player. A generic
    # URL must not replace those URLs in build_material.
    schema.pop('iframe_url', None)
    row['_material_schema'] = schema
    row['_material_blob_ready'] = bool(row.get('material_backup_status') == 'available'
        and row.get('material_blob_container') and row.get('material_blob_name'))
    return row


def material_quiz_definition(payload):
    """Authored questions only; never expose solutions as a learner attempt."""
    payload = object_value(payload)
    quiz = object_value(payload.get('quiz'))
    questions = quiz.get('questions') or payload.get('quiz_questions') or []
    if isinstance(questions, str):
        questions = json.loads(questions)
    if not isinstance(questions, list):
        return None
    result = []
    for index, question in enumerate(questions, 1):
        if not isinstance(question, dict):
            continue
        body = question.get('question_body') or question.get('question_text')
        if not body:
            continue
        options = question.get('options') or question.get('answer_options') or []
        if not isinstance(options, list):
            options = []
        result.append({'question_id': question.get('question_id') or index,
                       'question_order': question.get('question_order') or index,
                       'question_text': body,
                       'answer_options': [{'option_text': option.get('option_body') or option.get('option_text') or ''}
                                          for option in options if isinstance(option, dict)]})
    return {'description': None, 'questions': result} if result else None
