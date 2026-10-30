"""Topic identity and sequential submissions within one curriculum component."""
import json
import re

from .assignment_settings import learner_title
from .submission_visibility import VISIBLE_SUBMISSION_SQL

_RESOURCE_PREFIXES = ('https://', 'http://', '/curriculum_api/curriculum/uploads/')
_TAGS = re.compile(r'<[^>]*>')


def topic_id(value):
    if value in (None, ''):
        return ''
    if not isinstance(value, str) or value not in {'1', '2', '3'}:
        raise ValueError('Choose Topic 1, 2 or 3.')
    return value


def _has_text(value):
    return bool(_TAGS.sub('', str(value or '')).replace('&nbsp;', ' ').strip())


def topic_has_content(topic):
    """A topic a learner can open: it has a question, instructions or a resource."""
    return bool(_has_text(topic.get('question')) or _has_text(topic.get('instructions')) or topic.get('resources'))


def _topic(identity, row):
    resources = row.get('resources') if isinstance(row.get('resources'), list) else []
    return {'id': identity, 'name': str(row.get('name') or ''),
            'question': str(row.get('question') or ''),
            'instructions': str(row.get('instructions') or ''),
            'resources': [file for file in resources if isinstance(file, dict)
                          and str(file.get('url') or '').startswith(_RESOURCE_PREFIXES)]}


def definitions(value):
    """The topics an author configured, keeping their ids.

    The builder stores three slots; an empty slot is not an option and is not
    offered to learners. Ids are never renumbered: submissions are saved
    against them.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if not isinstance(value, list) or not value:
        return []
    result = []
    for identity in ('1', '2', '3'):
        row = next((item for item in value if isinstance(item, dict) and str(item.get('id')) == identity), None)
        topic = _topic(identity, row) if row is not None else None
        if topic and topic_has_content(topic):
            result.append(topic)
    return result


def topics_from_settings(settings):
    settings = settings or {}
    topics = definitions(settings.get("assignmentTopics"))
    if topics:
        return topics
    url = settings.get("assignmentFileUrl") or settings.get("uploadedFileUrl")
    resources = [{"fileName": settings.get("assignmentFileName") or settings.get("uploadedFileName") or "Assignment file",
                  "url": url, "size": settings.get("uploadedFileSize") or 0,
                  "contentType": settings.get("uploadedFileContentType") or ""}] if url else []
    # An assignment without topics is a single Topic 1, even before it has
    # content: drafts and submissions made since topics existed use that id.
    return [_topic('1', {"question": settings.get("assignmentContent") or settings.get("assignmentBrief") or "",
                         "instructions": settings.get("submissionInstructions") or "", "resources": resources})]


def component_settings(cur, component_id):
    cur.execute('SELECT settings_json FROM curriculum.components WHERE id = %s AND type = %s', [component_id, 'assignment'])
    row = cur.fetchone()
    settings = row[0] if row else {}
    if isinstance(settings, str):
        settings = json.loads(settings)
    return settings if isinstance(settings, dict) else {}


def configured_topics(cur, component_id):
    return topics_from_settings(component_settings(cur, component_id))


def prepare_topic_save(cur, payload):
    """Serialize topic selection; a delayed save cannot unlock submitted work."""
    identity = topic_id(payload.get('assignmentTopicId'))
    if not identity:
        return ''
    if payload.get('activityType') != 'assignment':
        raise ValueError('Topics are only available for assignments.')
    settings = component_settings(cur, payload['activityId'])
    topic = next((item for item in topics_from_settings(settings) if item['id'] == identity), None)
    available = bool(topic and topic_has_content(topic))
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))',
                [json.dumps([payload['learnerKind'], str(payload['learnerId']), payload['activityId']])])
    cur.execute(f'''SELECT assignment_topic_id, status, full_submission FROM "Learner".learning_reflection_submissions AS submission
        WHERE learner_kind=%s AND learner_id=%s AND activity_type='assignment' AND activity_id=%s
          AND {VISIBLE_SUBMISSION_SQL} ORDER BY assignment_topic_id''',
        [payload['learnerKind'], str(payload['learnerId']), payload['activityId']])
    existing = cur.fetchall()
    # Work already saved against a topic the author has since emptied stays
    # editable; a new selection needs a topic that is currently offered.
    saved = next((row for row in existing if row[0] == identity), None)
    if not available and saved is None:
        raise ValueError('This topic is not available. Reload the assignment instructions.')
    for other, status, _content in existing:
        if other == "":
            raise ValueError("This assignment has an existing legacy submission. Open its saved form.")
        if other != identity and status in ('draft', 'rejected', 'needs_revision'):
            raise ValueError('Resume and submit your current topic before starting another.')
    inherit_coaching_booking(payload, existing)
    payload['assignmentTopicId'] = identity
    if available:
        payload['assignmentTopicName'] = topic['name']
        payload['assignmentQuestion'] = topic['question']
    else:
        content = json.loads(saved[2]) if isinstance(saved[2], str) else saved[2] or {}
        payload['assignmentTopicName'] = str(content.get('assignmentTopicName') or '')
        payload['assignmentQuestion'] = str(content.get('assignmentQuestion') or '')
    # Coaches see the assignment under the name learners were given.
    if learner_title(settings):
        payload['activityTitle'] = learner_title(settings)
    # These fields describe a topic; the existing marking rules remain authoritative.
    return identity


def inherit_coaching_booking(payload, rows):
    """Reuse this component's owned MCM without modifying submitted topics."""
    from .monthly_assignment import coaching_meeting_booked, month_bounds
    monthly = payload.get('monthlyAssignment')
    if not isinstance(monthly, dict) or not month_bounds(monthly.get('month'))[0]:
        return
    for identity, _status, content in rows:
        if not identity or identity == payload.get('assignmentTopicId'):
            continue
        content = json.loads(content) if isinstance(content, str) else content or {}
        saved = content.get('monthlyAssignment') or {}
        key = saved.get('meetingKey')
        if not key or saved.get('month') != monthly.get('month'):
            continue
        if coaching_meeting_booked(payload['learnerKind'], int(payload['learnerId']), key):
            payload['monthlyAssignment'] = {**monthly, 'meetingKey': key}
            return


STATUS_PRIORITY = {'accepted': 5, 'partial': 4, 'submitted_for_tutor_review': 3, 'rejected': 2, 'needs_revision': 2, 'draft': 1}


def aggregate_statuses(rows):
    """A later optional draft must not make a submitted component incomplete."""
    result = {}
    for row in rows:
        kind, activity, status = row[:3]
        history = row[3] if len(row) > 3 else None
        key = (kind, activity)
        previous = result.get(key)
        history = json.loads(history) if isinstance(history, str) else history
        count = len(history or []) + int(bool(status) and status != 'draft')
        if previous is None:
            result[key] = {'activityType': kind, 'activityId': activity, 'status': status,
                           **({'submissionCount': count} if kind == 'assignment' else {})}
        else:
            if kind == 'assignment':
                previous['submissionCount'] += count
            if STATUS_PRIORITY.get(status, 0) > STATUS_PRIORITY.get(previous['status'], 0):
                previous['status'] = status
    return list(result.values())
