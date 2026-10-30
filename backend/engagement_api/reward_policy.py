"""Pure shared reward policy; not an API and not a second points ledger.

Inputs must come from authorised, persisted LMS records. Client assertions,
raw stopwatch values and local demonstration data are not accepted evidence.
All returned intents target the existing Engagement.points_grants authority.
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import calendar
import json

POLICY_VERSION = "2026-10-first-attempt-v2"
HOUR_RULE = "accepted_learning_hour_v2"
QUIZ_RULE = "first_submitted_quiz_v2"
BONUS_RULE = "verified_learning_bonus_v2"
POINTS_PER_GBP = 100
BONUS_POINTS = {
    "inclusiveness": 500, "linkedin-start": 1000,
    "linkedin-learning": 1000, "promotion-story": 20000,
    "career-planner": 100, "module-feedback": 100,
    "wellbeing": 100, "semester-winner": 1000,
}


def _id(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 180:
        raise ValueError("A canonical record ID is required.")
    return value


def _integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError("A non-negative integer is required.")
    return value


@dataclass(frozen=True)
class AwardIntent:
    rule_key: str
    learner_id: str
    points: int
    event_reference: str
    source_id: str
    policy_version: str = POLICY_VERSION


def _intent(rule, learner, scope, points, source):
    _id(learner)
    _id(source)
    _integer(points, 1)
    # Stable learner-inclusive reference is also unique across retries and
    # presentation channels; it never contains health or questionnaire answers.
    digest = sha256(json.dumps([learner, scope], separators=(",", ":")).encode()).hexdigest()
    return AwardIntent(rule, learner, points, "kbc-v2:" + digest, source)


def quiz_award(*, learner_id, quiz_id, submission_id, submitted_ordinal,
               correct_answers, total_questions, threshold=70, points_per_correct=1, perfect_points_per_correct=2):
    """Use the persisted first submission order, not a start/abandoned attempt.

    A multiple-answer question counts once, and only when its answer set is
    fully correct. Threshold uses the unrounded correct-answer proportion,
    matching the current mobile policy; authored quiz pass marks are unchanged.
    Existing earlier failures remain first attempts across policy activation.
    """
    _id(learner_id); _id(quiz_id); _id(submission_id)
    _integer(submitted_ordinal, 1); _integer(correct_answers); _integer(total_questions, 1)
    if correct_answers > total_questions:
        raise ValueError("Correct answers exceed total questions.")
    _integer(threshold, 1); _integer(points_per_correct); _integer(perfect_points_per_correct)
    if submitted_ordinal != 1 or correct_answers * 100 < total_questions * threshold:
        return None
    amount = correct_answers * (perfect_points_per_correct if correct_answers == total_questions else points_per_correct)
    if not amount:
        return None
    return _intent(QUIZ_RULE, learner_id, ["quiz", quiz_id], amount, submission_id)


def accepted_seconds(intervals):
    """Union already-accepted active playback/study intervals across devices.

    Each row is an immutable LMS acceptance record for one active interval,
    not a session's wall-clock envelope. The caller must select only intervals
    owned by the authenticated learner and approved by the acceptance service.
    Pending/rejected rows and divergent duplicate receipt IDs fail closed.
    Times are integer UTC epoch milliseconds; sub-second remainder carries.
    """
    seen = {}
    for row in intervals:
        if not isinstance(row, dict) or row.get("status") != "accepted":
            raise ValueError("Only accepted LMS intervals can earn hour points.")
        receipt = _id(row.get("receipt_id"))
        start = _integer(row.get("active_start_ms")); end = _integer(row.get("active_end_ms"))
        if end <= start:
            raise ValueError("An active interval must have positive duration.")
        value = (start, end)
        if receipt in seen and seen[receipt] != value:
            raise ValueError("An acceptance receipt changed; reconciliation is required.")
        seen[receipt] = value
    total = 0
    current = None
    for start, end in sorted(seen.values()):
        if current is None:
            current = [start, end]
        elif start <= current[1]:
            current[1] = max(current[1], end)
        else:
            total += current[1] - current[0]
            current = [start, end]
    if current:
        total += current[1] - current[0]
    return total // 1000


def hour_awards(*, learner_id, intervals, rate_at=None):
    """One stable intent per cumulative whole accepted hour, across modules.

    Repeat/reordered/offline reconciliation returns the same references. The
    authoritative grant service supplies the unique constraint. Returns no
    grant for a partial hour. Never use authored expected hours as intervals.
    """
    _id(learner_id)
    # Preserve stable hour ordinals across policy edits. The rate at the actual
    # end of each whole hour applies; partial hours carry across changes.
    intervals = list(intervals)
    accepted_seconds(intervals)  # Validate immutable receipts before unioning.
    merged = []
    for row in sorted(intervals, key=lambda r: (r['active_start_ms'], r['active_end_ms'])):
        start, end = row['active_start_ms'], row['active_end_ms']
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    result, elapsed, ordinal = [], 0, 1
    for start, end in merged:
        while ordinal * 3600000 <= elapsed + end - start:
            completed_at = start + ordinal * 3600000 - elapsed
            rate = rate_at(completed_at) if rate_at else 1
            _integer(rate)
            if rate:
                result.append(_intent(HOUR_RULE, learner_id, ["hour", ordinal], rate, "accepted-hour:" + str(ordinal)))
            ordinal += 1
        elapsed += end - start
    return result


def _month_shift(value, months):
    year, zero_month = divmod(value.year * 12 + value.month - 1 + months, 12)
    month = zero_month + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def verified_bonus(*, learner_id, opportunity, verification_id, verified,
                   today, module_id=None, module_end=None, programme_start=None,
                   semester_end=None, confirmed_winner=False,
                   masterclass_booking_verified=False):
    """Completion-only staff verification; never read health questionnaire text.

    Calendar inputs are staff-confirmed group dates in Europe/London. The caller
    resolves those dates; learner-entered preferences cannot authorise awards.
    """
    _id(learner_id); _id(verification_id)
    if verified is not True or type(today) is not date:
        raise ValueError("Staff verification and the London date are required.")
    if opportunity not in BONUS_POINTS:
        raise ValueError("Unknown reward opportunity.")
    scope = ["bonus", opportunity]
    if opportunity == "module-feedback":
        _id(module_id)
        if type(module_end) is not date or not 0 <= (module_end - today).days < 14 or masterclass_booking_verified is not True:
            raise ValueError("Verified feedback and masterclass booking must be inside the final 14 days.")
        scope.append(module_id)
    elif opportunity == "wellbeing":
        if type(programme_start) is not date or not _month_shift(programme_start, 2) <= today < _month_shift(programme_start, 4):
            raise ValueError("Wellbeing reward is only available in programme months 3 and 4.")
    elif opportunity == "semester-winner":
        _id(module_id)
        if type(semester_end) is not date or semester_end.month not in (1, 4, 9) or semester_end.day < calendar.monthrange(semester_end.year, semester_end.month)[1] - 6 or today < semester_end or confirmed_winner is not True:
            raise ValueError("A team-confirmed semester result is required.")
        # One winner reward per completed module, including retry on another day.
        scope.append(module_id)
    return _intent(BONUS_RULE, learner_id, scope, BONUS_POINTS[opportunity], verification_id)


def apply_intent(intent, *, learner_name, grant_points):
    """Reuse engagement_api.services.grant_points; never create a replica ledger.

    The caller supplies the existing service only after entitlement/accepted
    evidence checks. It must suppress superseded fixed awards for that same
    policy cohort and preserve all historical grants. No client entry point.
    """
    if not isinstance(intent, AwardIntent) or intent.policy_version != POLICY_VERSION:
        raise ValueError("A reviewed policy intent is required.")
    return grant_points(intent.rule_key, intent.learner_id, learner_name,
                        points=intent.points, event_reference=intent.event_reference,
                        source_type="hook", source_id=intent.source_id,
                        reason=POLICY_VERSION)
