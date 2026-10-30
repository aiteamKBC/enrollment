from datetime import timedelta

from django.db import IntegrityError
from django.utils import timezone as dj_timezone

from .models import PointsGrant, PointsRule


def grant_points(
    rule_key,
    learner_id,
    learner_name,
    points=None,
    event_reference=None,
    *,
    awarded_by=None,
    source_type='hook',
    source_id=None,
    reason=None,
):
    """Award points to a learner under the active PointsRule identified by `rule_key`.

    Single entry point every grant-worthy event goes through — the quiz/video/
    component progress hook, flash-card flips, manual staff grants, and
    recognition awards all call this rather than creating PointsGrant rows
    directly, so idempotency and provenance stay centralised.

    `event_reference` should uniquely identify *this specific occurrence*
    of the event (e.g. an attendance record id, a quiz attempt id). Pass
    it whenever the caller's code could plausibly run twice for the same
    occurrence (a retry, a duplicate webhook, ...) — a repeat call with the
    same (rule_key, event_reference) returns the existing grant instead of
    creating a second one. Omit it only for events that can't double-fire
    (e.g. manual grants, which are allowed to repeat freely).

    `source_type` other than 'adjustment' clamps a negative `points` to 0 —
    only an explicit staff adjustment/clawback may reduce a balance.

    Raises PointsRule.DoesNotExist if `rule_key` doesn't match an active
    rule. Callers are expected to catch exceptions from this function —
    a points-granting failure must never block the real feature (the
    attendance/quiz/evidence save) that triggered it.
    """
    rule = PointsRule.objects.get(key=rule_key, active=True)

    if event_reference is not None:
        existing = PointsGrant.objects.filter(rule=rule, event_reference=event_reference).first()
        if existing is not None:
            return existing

    awarded_points = rule.points if points is None else points
    if source_type != 'adjustment' and awarded_points < 0:
        awarded_points = 0

    try:
        return PointsGrant.objects.create(
            rule=rule,
            learner_id=learner_id,
            learner_name=learner_name,
            points=awarded_points,
            event_reference=event_reference,
            awarded_by=awarded_by,
            source_type=source_type,
            source_id=source_id,
            reason=reason,
        )
    except IntegrityError:
        # Another call for the same (rule, event_reference) won the race —
        # treat it the same as the pre-check above finding it.
        return PointsGrant.objects.get(rule=rule, event_reference=event_reference)


def points_summary(learner_id):
    """The one authoritative points balance for a learner.

    balance = sum of all grants - sum of all non-rejected voucher claims.
    A 'pending' or 'approved' claim already reserves its points (see
    voucher_claims_collection in views.py — reserve-at-claim); only
    'rejected' claims are excluded, since a rejection refunds the spend.
    """
    from django.db.models import Sum

    from .models import VoucherClaim

    earned = PointsGrant.objects.filter(learner_id=learner_id).aggregate(total=Sum('points'))['total'] or 0
    committed = (
        VoucherClaim.objects.filter(learner_id=learner_id)
        .exclude(status='rejected')
        .aggregate(total=Sum('points'))['total']
        or 0
    )
    return {
        'learnerId': learner_id,
        'earned': earned,
        'committed': committed,
        'balance': earned - committed,
    }


# Weights for compute_engagement_score — a documented business rule, not a
# magic number. Attendance and KSB progress are weighted highest (30% each)
# since they're the two clearest, least-gameable signals of a learner
# actually engaging; OTJH and quiz average round it out at 20% each.
ENGAGEMENT_SCORE_WEIGHTS = {
    'attendance_rate': 0.30,
    'ksb_progress': 0.30,
    'otjh_progress': 0.20,
    'quiz_average': 0.20,
}


def compute_engagement_score(attendance_rate, ksb_progress, otjh_progress, quiz_average):
    """0-100 composite engagement score from four real signals — never from
    points (points are a reward mechanism; scoring engagement off them would
    be circular/gameable). Any input may be None (no data yet for that
    signal, e.g. no quiz attempts) — the score is the weighted average of
    only the signals that have data, so a learner with no quiz history isn't
    penalised to zero on it.
    """
    inputs = {
        'attendance_rate': attendance_rate,
        'ksb_progress': ksb_progress,
        'otjh_progress': otjh_progress,
        'quiz_average': quiz_average,
    }
    available = {key: value for key, value in inputs.items() if value is not None}
    if not available:
        return None

    weight_total = sum(ENGAGEMENT_SCORE_WEIGHTS[key] for key in available)
    weighted_sum = sum(value * ENGAGEMENT_SCORE_WEIGHTS[key] for key, value in available.items())
    return round(weighted_sum / weight_total)


def compute_message_response_rates(learner_ids, lookback_days=90):
    """{engagement learner_id: response rate 0-100} for a batch of learners.

    response rate = % of a coach's messages to this learner that got a
    learner reply within 24h — a *learner* engagement/responsiveness signal.

    Bridges two identity spaces with ONE extra join, not a per-learner
    lookup: chat's `ChatLearner` is keyed by a different table
    (`Learner"."learners"`) than engagement's `learner_id`
    (`Created_users.id`) — see `chat/models.py` vs `engagement_api/models.py`.
    The bridge is email (`EnrolmentUser.email` <-> `ChatLearner.email`).

    Best-effort: any failure (missing chat data for this learner, a schema
    hiccup) returns an empty/partial dict rather than breaking the analytics
    endpoint this feeds — a secondary metric must never take down the page.
    """
    if not learner_ids:
        return {}
    try:
        from chat.models import ChatLearner, Conversation, Message
        from learner_api.models import EnrolmentUser

        int_ids = sorted({int(lid) for lid in learner_ids if str(lid).isdigit()})
        if not int_ids:
            return {}

        emails_by_engagement_id = {
            str(row['id']): (row['email'] or '').strip().lower()
            for row in EnrolmentUser.objects.filter(id__in=int_ids).values('id', 'email')
            if row['email']
        }
        if not emails_by_engagement_id:
            return {}

        chat_learner_id_by_email = {
            (row['email'] or '').strip().lower(): row['id']
            for row in ChatLearner.objects.all().values('id', 'email')
        }

        engagement_id_by_chat_learner_id = {}
        for engagement_id, email in emails_by_engagement_id.items():
            chat_learner_id = chat_learner_id_by_email.get(email)
            if chat_learner_id is not None:
                engagement_id_by_chat_learner_id[chat_learner_id] = engagement_id
        if not engagement_id_by_chat_learner_id:
            return {}

        conversations = list(
            Conversation.objects.filter(learner_id__in=engagement_id_by_chat_learner_id).values('id', 'learner_id')
        )
        if not conversations:
            return {}
        engagement_id_by_conversation_id = {
            conv['id']: engagement_id_by_chat_learner_id[conv['learner_id']] for conv in conversations
        }

        cutoff = dj_timezone.now() - timedelta(days=lookback_days)
        messages_by_conversation = {}
        for message in (
            Message.objects.filter(conversation_id__in=engagement_id_by_conversation_id, created_at__gte=cutoff)
            .order_by('conversation_id', 'created_at')
            .values('conversation_id', 'sender_type', 'created_at')
        ):
            messages_by_conversation.setdefault(message['conversation_id'], []).append(message)

        coach_message_totals = {}
        answered_within_24h = {}
        for conversation_id, thread in messages_by_conversation.items():
            engagement_id = engagement_id_by_conversation_id[conversation_id]
            for index, message in enumerate(thread):
                if message['sender_type'] != 'coach':
                    continue
                coach_message_totals[engagement_id] = coach_message_totals.get(engagement_id, 0) + 1
                for later in thread[index + 1:]:
                    if later['sender_type'] != 'learner':
                        continue
                    if later['created_at'] - message['created_at'] <= timedelta(hours=24):
                        answered_within_24h[engagement_id] = answered_within_24h.get(engagement_id, 0) + 1
                    break

        return {
            engagement_id: round(answered_within_24h.get(engagement_id, 0) / total * 100)
            for engagement_id, total in coach_message_totals.items()
        }
    except Exception:  # noqa: BLE001 — a secondary metric must never break the roster it feeds
        return {}
