"""Pure, request-scoped component review; never infer completion from a title."""
import hashlib
import json
import re
from collections import Counter

from old_otjh.service import ServiceError

from .replacement_learning import project_replacement_learning


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def activity_type(value, *, quiz=False):
    value = re.sub(r'[^a-z0-9]', '', str(value or '').lower())
    if quiz or value in {'quiz', 'readingquiz'}:
        return 'quiz'
    return {'text': 'reading', 'textlesson': 'reading', 'document': 'reading',
            'audio': 'podcast'}.get(value, value or 'unknown')


def target_fingerprint(component, settings):
    """Bind manual equivalence to the approved target's type and source identity."""
    from .replacement_learning import _object
    legacy = _object(_object(settings).get('legacySettings'))
    return digest({'type': activity_type(component.get('type'), quiz=component.get('isQuiz')),
                   'quizId': (component.get('quizMeta') or {}).get('quizId'),
                   'legacySettings': {key: legacy.get(key) for key in ('sourceLessonId', 'sourceMaterialKey')}})


def saved_review_matches(item, sources, targets):
    """Restore request-local proposals, never completion or shared provenance."""
    rows = item.get('reviewMatches', [])
    if not isinstance(rows, list):
        return {}
    source_by_ref = {source['sourceActivityRef']: source for source in sources}
    target_by_id = {target['componentId']: target for target in targets}
    used = {source['automaticTargetId'] for source in sources if source['automaticTargetId']}
    candidates = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ref, ident = row.get('sourceActivityRef'), row.get('targetComponentId')
        if not isinstance(ref, str) or not isinstance(ident, str):
            continue
        source, target = source_by_ref.get(ref), target_by_id.get(ident)
        if (source and target and not source['automaticTargetId'] and ident not in used
                and source['type'] != 'unknown' and source['type'] == target['type'] == row.get('sourceType')
                and target['fingerprint'] == row.get('targetFingerprint')):
            candidates.append((ref, ident))
    source_uses = Counter(ref for ref, _ in candidates)
    target_uses = Counter(ident for _, ident in candidates)
    return {ref: ident for ref, ident in candidates if source_uses[ref] == target_uses[ident] == 1}


def review_recommendations(item, sources, targets):
    """Allow only recorded request-local type exceptions, after explicit selection."""
    rows = item.get('reviewRecommendations', [])
    if not isinstance(rows, list):
        return {}
    source_by_ref = {source['sourceActivityRef']: source for source in sources}
    target_by_id = {target['componentId']: target for target in targets}
    used = {source.get('automaticTargetId') or source.get('savedTargetId') for source in sources}
    candidates = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ref, ident = row.get('sourceActivityRef'), row.get('targetComponentId')
        if not isinstance(ref, str) or not isinstance(ident, str):
            continue
        source, target = source_by_ref.get(ref), target_by_id.get(ident)
        if (source and target and not source['automaticTargetId'] and not source.get('savedTargetId')
                and ident not in used and row.get('allowDifferentType') is True
                and source['type'] == row.get('sourceType') and target['type'] == row.get('targetType')
                and 'unknown' not in (source['type'], target['type'])
                and target['fingerprint'] == row.get('targetFingerprint')):
            candidates.append((ref, ident))
    source_uses = Counter(ref for ref, _ in candidates)
    target_uses = Counter(ident for _, ident in candidates)
    return {ref: ident for ref, ident in candidates if source_uses[ref] == target_uses[ident] == 1}


def module_review(item, context):
    from .my_learning import native_done
    native, definitions, done, metadata = context[3], context[5], context[6], context[9]
    components = [{k: v for k, v in c.items() if k != 'previousLearning'}
                  for c in [*native['components'], *native.get('retiredQuizComponents', [])]
                  if c.get('moduleId') == item['moduleId']]
    course = item['courseRef'][7:]
    refs = sorted(set(item.get('completedSourceActivities',
                              [ref for old, ref in done if str(old) == course])))
    definitions = {d['source_activity_id']: d for d in definitions if str(d['source_course_ref']) == course}
    activities = [{'group_id': int(course), 'catalogue_kind': ref.split(':')[0],
                   'source_activity_id': int(ref.split(':')[1]),
                   'activity_id': f'catalogue:{course}:{ref}', 'completed': True} for ref in refs]
    projected = project_replacement_learning(components, metadata['replacement_settings'],
                                            {'current:' + item['moduleId']: item['courseRef']}, activities)
    matches = {c['componentId']: c['previousLearning'] for c in projected
               if c.get('previousLearning', {}).get('completed') is True}
    automatic = {f"{m['catalogueKind']}:{m['sourceActivityId']}": ident for ident, m in matches.items()}
    sources = []
    for ref in refs:
        d = definitions.get(ref, {})
        sources.append({'sourceActivityRef': ref, 'title': d.get('source_activity_title') or ref,
                        'week': d.get('source_section_title') or '',
                        'type': activity_type(d.get('source_activity_type'), quiz=ref.startswith('quiz:')),
                        'automaticTargetId': automatic.get(ref)})
    targets = [{'componentId': c['componentId'], 'title': c.get('component') or c.get('title') or c['componentId'],
                'week': c.get('week') or '', 'type': activity_type(c.get('type'), quiz=c.get('isQuiz')),
                'plannedHours': c.get('expectedOtjh'),
                'fingerprint': target_fingerprint(c, metadata['replacement_settings'].get(c['componentId']))}
               for c in components if c.get('componentId') and not c.get('retired')]
    saved_matches = saved_review_matches(item, sources, targets)
    for source in sources:
        if source['sourceActivityRef'] in saved_matches:
            source['savedTargetId'] = saved_matches[source['sourceActivityRef']]
    recommendations = review_recommendations(item, sources, targets)
    for source in sources:
        if source['sourceActivityRef'] in recommendations:
            source['recommendedTargetId'] = recommendations[source['sourceActivityRef']]
    target_ids = {target['componentId'] for target in targets}
    # native_done caches its facts; keep this read-only review from mutating context.
    completion_context = dict(native)
    completed_ids = sorted({c['componentId'] for c in components
                            if c.get('componentId') in target_ids and native_done(c, completion_context)})
    # My Learning retains passed quizzes removed from the current plan. Keep
    # that existing credit in the preview, without offering them as destinations.
    retained_ids = sorted({c['componentId'] for c in native.get('retiredQuizComponents', [])
                           if c.get('moduleId') == item['moduleId'] and c.get('componentId')
                           and c['componentId'] not in target_ids and native_done(c, completion_context)})
    return {'moduleId': item['moduleId'], 'courseRef': item['courseRef'], 'title': item['newTitle'],
            'sources': sources, 'targets': targets, 'completedTargetIds': completed_ids,
            'retainedCompletedIds': retained_ids}, matches, components


def component_counts(module, matched_ids):
    """Count unique destinations and retained native quiz completions."""
    target_ids = {target['componentId'] for target in module['targets']}
    matched = target_ids.intersection(matched_ids)
    retained = set(module.get('retainedCompletedIds', [])) - target_ids
    completed = target_ids.intersection(module['completedTargetIds']) | matched | retained
    all_ids = target_ids | retained
    return {'total': len(all_ids), 'matched': len(matched), 'completed': len(completed),
            'remaining': len(all_ids - completed)}


def build_review(request_id, items, context):
    modules = [module_review(item, context)[0] for item in items]
    return {'modules': modules, 'reviewToken': digest({'requestId': str(request_id), 'modules': modules})}


def validate_decisions(review, decisions):
    """Require explicit unique choices; type exceptions are request-local pairs."""
    needed = {(module['moduleId'], source['sourceActivityRef']): (module, source)
              for module in review['modules'] for source in module['sources'] if not source['automaticTargetId']}
    if decisions is None and not needed:
        return {}
    if not isinstance(decisions, dict) or decisions.get('reviewToken') != review['reviewToken']:
        raise ServiceError('Component matches changed or have not been reviewed. Reload component matches before confirming.',
                           'transfer_review_changed', 409)
    rows = decisions.get('mappings')
    if not isinstance(rows, list):
        raise ServiceError('Review every unmatched component before confirming.', 'transfer_matches_required', 400)
    used = {(module['moduleId'], source['automaticTargetId']) for module in review['modules']
            for source in module['sources'] if source['automaticTargetId']}
    result = {}
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'moduleId', 'sourceActivityRef', 'targetComponentId'}
                or any(not isinstance(value, str) or not value for value in row.values())):
            raise ServiceError('Choose a destination for every unmatched component.', 'invalid_transfer_match', 400)
        key = (row['moduleId'], row['sourceActivityRef'])
        if key not in needed or key in result:
            raise ServiceError('A source component is invalid or selected more than once.', 'invalid_transfer_match', 400)
        module, source = needed[key]
        target = next((target for target in module['targets'] if target['componentId'] == row['targetComponentId']), None)
        if (target is None or source['type'] == 'unknown'
                or (source['type'] != target['type'] and source.get('recommendedTargetId') != row['targetComponentId'])
                or (row['moduleId'], row['targetComponentId']) in used):
            raise ServiceError('Choose a unique component of the same type in the destination module.', 'invalid_transfer_match', 400)
        used.add((row['moduleId'], row['targetComponentId']))
        result[key] = target
    if result.keys() != needed.keys():
        raise ServiceError('Every completed old component must have a match before approval.', 'transfer_matches_required', 409)
    return result


def manual_completion(item, source_ref, target):
    kind, ident = source_ref.split(':')
    return {'courseRef': item['courseRef'], 'activityId': f"catalogue:{item['courseRef'][7:]}:{source_ref}",
            'sourceActivityId': int(ident), 'catalogueKind': kind, 'completed': True,
            'manualMatch': True, 'targetFingerprint': target['fingerprint']}
