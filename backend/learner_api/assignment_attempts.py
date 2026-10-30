"""Server-owned assignment attempt history inside the existing JSON document."""
from copy import deepcopy
import json

HISTORY_KEY = 'assignmentAttemptHistory'


def document(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            value = {}
    return value if isinstance(value, dict) else {}


def timestamp(value):
    return value.isoformat() if hasattr(value, 'isoformat') else value or None


def attempt_document(payload, number, status, submitted_at=None, feedback=None, reviewer=None, reviewed_at=None):
    """Select an immutable attempt, never fall back to the latest draft."""
    stored = document(payload)
    rows = list(stored.get(HISTORY_KEY) or [])
    if status and status != 'draft':
        rows.append(snapshot(stored, status, submitted_at, feedback, reviewer, reviewed_at))
    if number < 1 or number > len(rows):
        raise ValueError('Submission attempt not found.')
    entry = rows[number - 1]
    return {**document(entry.get('content')), 'submissionAttemptNumber': number,
            'status': entry.get('status'), 'submittedAt': entry.get('submittedAt'),
            'coachFeedback': entry.get('coachFeedback', ''), 'reviewedBy': entry.get('reviewedBy', ''),
            'reviewedAt': entry.get('reviewedAt'), 'locked': True}


def snapshot(payload, status, submitted_at=None, feedback=None, reviewer=None, reviewed_at=None):
    content = deepcopy(document(payload))
    content.pop(HISTORY_KEY, None)
    content.pop('submissionAttempts', None)
    return {'status': status, 'submittedAt': timestamp(submitted_at),
            'coachFeedback': feedback or '', 'reviewedBy': reviewer or '',
            'reviewedAt': timestamp(reviewed_at), 'content': content}


def preserve_attempts(incoming, stored, status=None, submitted_at=None, feedback=None, reviewer=None, reviewed_at=None):
    """Archive once on leaving a reviewed submission; repeated draft saves add nothing."""
    stored = document(stored)
    history = deepcopy(stored.get(HISTORY_KEY, []))
    if not isinstance(history, list):
        raise ValueError('The saved attempt history is invalid. Please contact support.')
    if status and status != 'draft':
        history.append(snapshot(stored, status, submitted_at, feedback, reviewer, reviewed_at))
    incoming.pop('submissionAttempts', None)
    incoming[HISTORY_KEY] = history


def submission_attempts(payload, status, submitted_at=None, feedback=None, reviewer=None, reviewed_at=None):
    stored = document(payload)
    rows = list(stored.get(HISTORY_KEY) or [])
    if status and status != 'draft':
        rows.append(snapshot(stored, status, submitted_at, feedback, reviewer, reviewed_at))
    result = []
    for index, entry in enumerate(rows, 1):
        content = document(entry.get('content'))
        result.append({
            'number': index, 'status': entry.get('status', ''),
            'submittedAt': entry.get('submittedAt'), 'reviewedAt': entry.get('reviewedAt'),
            'reviewedBy': entry.get('reviewedBy', ''), 'coachFeedback': entry.get('coachFeedback', ''),
            'answer': content.get('assignmentAnswer') or content.get('learningReflection') or '',
            'actualTimeHours': content.get('actualTimeHours'),
        })
    return result
