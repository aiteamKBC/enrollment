"""Versioned all-course learning rewards. Never rewrites historical grants."""
import json
from django.db import connections, transaction
from django.utils import timezone
from .segment_rules import RewardError, integer
from .reward_policy import BONUS_POINTS

ACTIVITY_TYPES = ("reading", "powerpoint", "podcast", "video", "quiz")
DEFAULTS = {
    "pointsPerHour": 1, "quizThreshold": 70, "quizPointPerCorrect": 1,
    "perfectQuizPointPerCorrect": 2,
    "hourActivities": list(ACTIVITY_TYPES),
    "bonusPoints": dict(BONUS_POINTS),
}
def validate(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULTS):
        raise RewardError("invalid_learning_policy", 400)
    result = {key: integer(value[key], 0, 1000) for key in (
        "pointsPerHour", "quizPointPerCorrect", "perfectQuizPointPerCorrect")}
    result["quizThreshold"] = integer(value["quizThreshold"], 1, 100)
    activities = value["hourActivities"]
    if (not isinstance(activities, list) or any(type(x) is not str or x not in ACTIVITY_TYPES for x in activities)
            or len(set(activities)) != len(activities)):
        raise RewardError("invalid_activity_mapping", 400)
    result["hourActivities"] = sorted(activities)
    bonuses = value['bonusPoints']
    if not isinstance(bonuses,dict) or set(bonuses) != set(BONUS_POINTS):
        raise RewardError('invalid_bonus_mapping',400)
    result['bonusPoints'] = {key:integer(amount,0,100000) for key,amount in bonuses.items()}
    return result

def at(cursor, instant=None):
    cursor.execute('''SELECT id,effective_at,settings FROM "Engagement".learning_reward_policies
        WHERE effective_at<=%s ORDER BY effective_at DESC,id DESC LIMIT 1''', [instant or timezone.now()])
    row = cursor.fetchone()
    if not row:
        return None
    return {"id": row[0], "effectiveAt": row[1],
            "settings": json.loads(row[2]) if isinstance(row[2], str) else row[2]}

def all_course_start(cursor):
    cursor.execute('SELECT min(effective_at) FROM "Engagement".learning_reward_policies')
    return cursor.fetchone()[0]

def publish(settings, operator, expected_id):
    checked = validate(settings)
    with transaction.atomic(using="default"), connections["default"].cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ["engagement:learning-policy"])
        current = at(cursor)
        if (current["id"] if current else None) != expected_id:
            raise RewardError("policy_changed_refresh_before_saving")
        cursor.execute('''INSERT INTO "Engagement".learning_reward_policies(effective_at,settings,created_by)
            VALUES(clock_timestamp(),%s::jsonb,%s) RETURNING id,effective_at''',
            [json.dumps(checked), str(operator)])
        row = cursor.fetchone()
    return {"id": row[0], "effectiveAt": row[1].isoformat(), "settings": checked}

def activity_type(component, activity_kind=None):
    if activity_kind == "quiz" or component.get("isQuiz") or component.get("type") == "quiz":
        return "quiz"
    if activity_kind == "video":
        return "video"
    kind = str(component.get("type") or "").lower().replace("-", "_")
    if kind in ("reading", "powerpoint", "podcast", "video"):
        return kind
    if component.get("audioUrl"):
        return "podcast"
    if component.get("videoUrl"):
        return "video"
    return None

def ensure_module(cursor, module_id):
    """Only called within an authorised mutation after canonical assignment checks."""
    start = all_course_start(cursor)
    if start:
        cursor.execute('''INSERT INTO "Engagement".shared_reward_config(module_id,enabled,starts_at,updated_by)
            VALUES(%s,true,%s,'all-course-policy') ON CONFLICT DO NOTHING''', [module_id, start])
