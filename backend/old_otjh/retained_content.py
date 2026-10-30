"""Read the original per-learner audit JSON, without importing its records.

The ledger still determines which activities belong to the report. These
queries only recover content for those exact component IDs and this Aptem ID.
"""
from .content import obj, values, http_url


def read(learner, ids):
    from .repository import source_query
    if not ids:
        return {}
    params = [learner['aptem_id'], learner['aptem_id'], list(map(str, sorted(ids)))]
    rows = source_query('''WITH learner_source AS (
        SELECT programme_structure::jsonb AS structure FROM "Audit".learner_match WHERE aptem_id=%s
        UNION ALL
        SELECT programme_structure::jsonb FROM "Audit".learner_match_market_research_l4 WHERE aptem_id=%s
      ), components AS (
        SELECT item FROM learner_source
        CROSS JOIN LATERAL jsonb_path_query(structure, '$.months[*]."LMS activities"[*]') AS top(entry)
        CROSS JOIN LATERAL (SELECT top.entry AS item UNION ALL
          SELECT child FROM jsonb_array_elements(CASE WHEN jsonb_typeof(top.entry->'items')='array'
            THEN top.entry->'items' ELSE '[]'::jsonb END) child) flat
      )
      SELECT item->>'component_id' AS component_id, item->>'title' AS title,
        item->>'material_type' AS material_type, item->>'group_id' AS group_id,
        item->>'iframe_url' AS iframe_url, item->>'preview_url' AS preview_url,
        item->>'text_body' AS text_body
      FROM components WHERE item->>'component_id'=ANY(%s)''', params)
    attempts = source_query('''SELECT q.key AS component_id, q.value AS attempt
        FROM "Audit".learner_match lm CROSS JOIN LATERAL jsonb_each(lm.quiz_attempts) q
        WHERE lm.aptem_id=%s AND q.key=ANY(%s)''', [learner['aptem_id'], params[-1]])
    result = {}
    for row in rows:
        try:
            ident = int(row['component_id'])
        except (ValueError, TypeError):
            continue
        if ident not in ids:
            continue
        item = result.setdefault(ident, {'urls': [], 'html': None, 'quiz': None, 'groups': set()})
        if row.get('group_id'):
            item['groups'].add(str(row['group_id']))
        # A WordPress quiz permalink opens the LMS/login, not a saved attempt.
        if row.get('material_type') != 'quiz':
            for value in (row.get('iframe_url'), row.get('preview_url')):
                url = http_url(value)
                if url and url not in item['urls']:
                    item['urls'].append(url)
        item['html'] = item['html'] or row.get('text_body')
    for row in attempts:
        try:
            ident = int(row['component_id'])
        except (ValueError, TypeError):
            continue
        attempt = obj(row.get('attempt'))
        # Validate the identity inside the stored payload as well as its key.
        if ident not in ids or str(attempt.get('component_id', ident)) != str(ident):
            continue
        body = obj(attempt.get('quiz_body'))
        questions = [q for q in values(body.get('questions')) if isinstance(q, dict) and q.get('question_text')]
        if not questions:
            continue
        item = result.setdefault(ident, {'urls': [], 'html': None, 'quiz': None, 'groups': set()})
        item['quiz'] = {'state': 'attempted', 'attempt': {
            'title': attempt.get('title'), 'status': attempt.get('status') or 'recorded',
            'score': attempt.get('highest_score'), 'maximum_score': attempt.get('maximum_score'),
            'score_percent': attempt.get('score_percent'),
            'attempt_number': attempt.get('attempt_number') or body.get('attempt_number'),
            'completed_at': attempt.get('completed_at'),
            'quiz_body': {'questions': questions},
        }}
    return result
