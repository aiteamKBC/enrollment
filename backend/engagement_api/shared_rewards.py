"""Durable shared reward evidence; the existing Engagement wallet is sole authority."""
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
import hashlib
import json

from django.conf import settings
from django.db import connections, transaction
from django.utils import timezone

from .segment_rules import RewardError, digest, identifier, integer, intervals, merged_milliseconds
from .reward_policy import (POLICY_VERSION, HOUR_RULE, QUIZ_RULE, BONUS_RULE, BONUS_POINTS,
                            POINTS_PER_GBP, quiz_award, hour_awards, verified_bonus, apply_intent)

DB = "default"
PILOT_MODULE = "MOD-KBC-PMP-M01-SMR"


def _one(cursor):
    row = cursor.fetchone()
    return dict(zip((col[0] for col in cursor.description), row)) if row else None


def _all(cursor):
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _ms(value):
    return int(value.timestamp()*1000)


def _active():
    if getattr(settings, "KBC_SHARED_REWARDS_READY", False) is not True:
        raise RewardError("rewards_not_enabled")


def policy(cursor, module_id=PILOT_MODULE, require=True):
    identifier(module_id)
    cursor.execute('SELECT * FROM "Engagement".shared_reward_config WHERE module_id=%s', [module_id])
    value = _one(cursor)
    if value is None:
        from .learning_policy import all_course_start
        start = all_course_start(cursor)
        if start:
            value = {"module_id": module_id, "enabled": True, "starts_at": start,
                     "programme_start": None, "module_end": None, "semester_end": None,
                     "masterclass_event_id": None}
    if require and (not value or not value["enabled"] or not value["starts_at"] or value["starts_at"]>timezone.now()):
        raise RewardError("rewards_not_enabled")
    return value


def _wallet_lock(cursor, learner_id):
    cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                   ["engagement:wallet:"+str(learner_id)])


def _grant(intent, learner_name, module_id):
    from .services import grant_points
    grant = apply_intent(intent, learner_name=learner_name, grant_points=grant_points)
    # Same default transaction and learner lock as the canonical grant.
    with connections[DB].cursor() as cursor:
        cursor.execute('''INSERT INTO "Engagement".shared_reward_award_links(grant_id,module_id,source_reference)
                          VALUES(%s,%s,%s) ON CONFLICT(grant_id) DO NOTHING''',
                       [grant.pk, module_id, intent.source_id])
    return grant


def configured_quiz_award(cursor, submitted_at, **kwargs):
    from .learning_policy import at
    selected = at(cursor, submitted_at)
    options = selected['settings'] if selected else {}
    return quiz_award(**kwargs, threshold=options.get('quizThreshold',70),
        points_per_correct=options.get('quizPointPerCorrect',1),
        perfect_points_per_correct=options.get('perfectQuizPointPerCorrect',2))


def configured_hour_awards(cursor, learner_id):
    from bisect import bisect_right
    cursor.execute('SELECT effective_at,settings FROM "Engagement".learning_reward_policies ORDER BY effective_at')
    revisions = [(_ms(row[0]), _json(row[1])) for row in cursor.fetchall()]
    starts = [row[0] for row in revisions]
    def rate(milliseconds):
        index = bisect_right(starts,milliseconds)-1
        return revisions[index][1]['pointsPerHour'] if index>=0 else 1
    return hour_awards(learner_id=learner_id, intervals=accepted_intervals(cursor,learner_id), rate_at=rate)


def record_observations(*, learner_id, profile_id, component, tracking, token,
                        event_id, sequence, observed_intervals):
    """Called only after facade auth/entitlement and signature/mode verification."""
    _active()
    learner_id = identifier(str(learner_id)); event_id = identifier(event_id)
    sequence = integer(sequence, 1)
    module_id = component.get("moduleId")
    started = tracking["startedAt"]
    if timezone.is_naive(started):
        raise RewardError("invalid_signed_start")
    now = timezone.now()
    expires = tracking.get('trackingExpiresAt', started+timedelta(hours=24))
    if now < started or now >= expires:
        raise RewardError("tracking_session_expired")
    checked = intervals(observed_intervals, max(0, _ms(now)-_ms(started)),
                        max_offset_ms=max(86400000, _ms(expires)-_ms(started)))
    windows = tracking.get('trackingWindows')
    if windows and any(not any(left <= start and end <= right for left, right in windows)
                       for start, end in checked):
        raise RewardError('observation_outside_timing_lease')
    session_id = identifier(tracking["sessionId"])
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    fingerprint = digest([session_id, sequence, checked])
    amount = sum(end-start for start, end in checked)
    with transaction.atomic(using=DB), connections[DB].cursor() as cursor:
        from .learning_policy import ensure_module
        ensure_module(cursor, module_id)
        cfg = policy(cursor, module_id)
        if started < cfg["starts_at"]:
            raise RewardError("session_precedes_policy")
        _wallet_lock(cursor, learner_id)
        cursor.execute('''INSERT INTO "Engagement".shared_reward_sessions(
             session_id,learner_id,learner_kind,profile_id,component_id,module_id,week_id,activity_kind,activity_id,
             counting_mode,started_at,expires_at,token_sha256,review_required,component_type,component_title,module_title,week_title)
             VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
             [session_id, learner_id, tracking["learnerKind"], profile_id, component["componentId"], module_id, component["weekId"],
              tracking["activityKind"], str(tracking["activityId"]), tracking["countingMode"],
              started, expires, token_hash, bool(component.get("tutorValidationRequired")),
              component.get("type") or tracking["activityKind"],component.get("component") or component["componentId"],
              component.get("module") or "",component.get("week") or ""])
        cursor.execute('SELECT * FROM "Engagement".shared_reward_sessions WHERE session_id=%s FOR UPDATE', [session_id])
        session = _one(cursor)
        if not session or (session["learner_id"], session["profile_id"], session["component_id"],
             session["module_id"], session["activity_kind"], session["activity_id"], session["counting_mode"],
             session["started_at"], session["token_sha256"]) != (
             learner_id, profile_id, component["componentId"], module_id, tracking["activityKind"],
             str(tracking["activityId"]), tracking["countingMode"], started, token_hash):
            raise RewardError("session_identity_conflict")
        if windows and expires > session['expires_at']:
            cursor.execute('UPDATE "Engagement".shared_reward_sessions SET expires_at=%s WHERE session_id=%s',
                           [expires, session_id])
        cursor.execute('''SELECT * FROM "Engagement".shared_reward_observations
             WHERE event_id=%s OR (session_id=%s AND sequence=%s) ORDER BY event_id''', [event_id, session_id, sequence])
        previous = _all(cursor)
        if previous:
            if len(previous)!=1 or (previous[0]["event_id"],previous[0]["session_id"],
                previous[0]["sequence"],previous[0]["payload_sha256"]) != (event_id,session_id,sequence,fingerprint):
                raise RewardError("observation_id_conflict")
            cursor.execute('''SELECT coalesce(sum(observed_ms),0) FROM "Engagement".shared_reward_observations
                 WHERE session_id=%s AND sequence<=%s''',[session_id,sequence])
            # SUM(bigint) is Decimal on PostgreSQL; JSON receipts require a number.
            batch_cumulative=int(cursor.fetchone()[0])
            return {"source":"lms","eventId":event_id,"sessionId":session_id,"sequence":sequence,
                    "status":"pending","observedMilliseconds":previous[0]["observed_ms"],
                    "cumulativeObservedMilliseconds":batch_cumulative,"duplicate":True}
        cursor.execute('SELECT 1 FROM "Engagement".shared_reward_outbox WHERE session_id=%s', [session_id])
        if cursor.fetchone():
            raise RewardError("progress_already_submitted")
        if sequence != session["last_sequence"]+1:
            raise RewardError("sequence_gap")
        if checked and checked[0][0] < session["last_end_offset_ms"]:
            raise RewardError("overlapping_observation")
        cursor.execute('''INSERT INTO "Engagement".shared_reward_observations
             (event_id,session_id,sequence,payload_sha256,intervals,observed_ms)
             VALUES(%s,%s,%s,%s,%s::jsonb,%s)''',
             [event_id,session_id,sequence,fingerprint,json.dumps(checked),amount])
        cursor.execute('''UPDATE "Engagement".shared_reward_sessions SET last_sequence=%s,
             last_end_offset_ms=%s,observed_ms=observed_ms+%s WHERE session_id=%s''',
             [sequence,checked[-1][1] if checked else session["last_end_offset_ms"],amount,session_id])
    return {"source":"lms","eventId":event_id,"sessionId":session_id,"sequence":sequence,
            "status":"pending","observedMilliseconds":amount,
            "cumulativeObservedMilliseconds":session["observed_ms"]+amount,"duplicate":False}


def _persisted_progress(cursor, job, session):
    cursor.execute('''SELECT p.id,p.learner_id,p.kind,p.quiz_ref,p.component_ref,p.verified_seconds,
       p.time_tracking_source,p.time_tracking_session_ref,p.started_at,p.submitted_at,
       p.achieved_score,p.total_score,p.entry_order,l.enrolment_id,l.lifecycle_status,l.full_name
       FROM "Learner".learner_progress_entries p JOIN "Learner".learners l ON l.id=p.learner_id
       WHERE p.id=%s''', [job["progress_id"]])
    row = _one(cursor)
    if not row or str(row["enrolment_id"])!=session["learner_id"] or row["learner_id"]!=session["profile_id"]:
        raise RewardError("progress_identity_mismatch")
    expected_id = row["quiz_ref"] if row["kind"]=="quiz" else row["component_ref"]
    if (row["kind"] != session["activity_kind"] or str(expected_id) != session["activity_id"]
        or row["time_tracking_session_ref"] != session["session_id"]
        or row["started_at"] != session["started_at"]
        or not row["submitted_at"] or row["submitted_at"] < row["started_at"]
        or row["submitted_at"] > session["expires_at"]
        or (row["time_tracking_source"] or "").split(":")[0] != "signed_session_capped_"+session["counting_mode"]
        or (row["time_tracking_source"] or "").endswith(":input")):
        raise RewardError("progress_evidence_mismatch")
    budget = row["verified_seconds"]
    if type(budget) is not int or budget < 0:
        raise RewardError("missing_verified_duration")
    if session["observed_ms"] > budget*1000+999:
        raise RewardError("observed_duration_exceeds_saved_budget")
    return row


def accepted_intervals(cursor, learner_id, module_id=None):
    where = " AND s.module_id=%s" if module_id else ""
    cursor.execute('''SELECT i.receipt_id,i.active_start,i.active_end
        FROM "Engagement".shared_reward_active_intervals i
        JOIN "Engagement".shared_reward_sessions s ON s.session_id=i.session_id
        WHERE i.learner_id=%s'''+where+' ORDER BY i.active_start,i.active_end,i.receipt_id',
        [str(learner_id)]+([module_id] if module_id else []))
    return [{"learner_id":str(learner_id),"receipt_id":row["receipt_id"],"status":"accepted",
             "active_start_ms":_ms(row["active_start"]),"active_end_ms":_ms(row["active_end"])}
            for row in _all(cursor)]


def _ensure_review_link(session, row):
    # The canonical queue owns coach submissions/audit history. Only registered,
    # persisted study may request the narrow explicit-progress refresh path.
    from learner_api.marking_queue_entry import queue_for_marking
    # The committed signed session's learner kind was bound by the facade.
    kind=session["learner_kind"]
    queue_for_marking(component_id=session["component_id"],kind=kind,
      learner_id=session["learner_id"],context={
        "learnerName":row["full_name"] or "", "activityType":session["component_type"],
        "activityTitle":session["component_title"],"moduleTitle":session["module_title"],
        "weekTitle":session["week_title"],"actualTimeHours":row["verified_seconds"]/3600,
        "progressEntryId":row["id"],"sharedRewardProgressId":row["id"]})


def reconcile_progress(progress_id):
    """One transactional durable job. A failed grant rolls back acceptance and job completion."""
    _active()
    with transaction.atomic(using=DB), connections[DB].cursor() as cursor:
        cursor.execute('SELECT * FROM "Engagement".shared_reward_outbox WHERE progress_id=%s', [progress_id])
        job = _one(cursor)
        if not job:
            return {"status":"not_scoped"}
        cursor.execute('SELECT * FROM "Engagement".shared_reward_sessions WHERE session_id=%s', [job["session_id"]])
        session = _one(cursor)
        _wallet_lock(cursor, session["learner_id"])
        cursor.execute('SELECT * FROM "Engagement".shared_reward_outbox WHERE progress_id=%s FOR UPDATE', [progress_id])
        job = _one(cursor)
        if job["state"]=="done":
            return {"status":"done","duplicate":True}
        cfg = policy(cursor, session["module_id"])
        row = _persisted_progress(cursor, job, session)
        if row["submitted_at"] < cfg["starts_at"]:
            raise RewardError("progress_precedes_policy")
        if session["review_required"]:
            _ensure_review_link(session,row)
            cursor.execute('''SELECT status FROM "Learner".learning_reflection_submissions
               WHERE learner_id::text=%s AND activity_id=%s AND progress_entry_id=%s
               ORDER BY submitted_at DESC LIMIT 1''',
               [session["learner_id"],session["component_id"],row["id"]])
            review = cursor.fetchone()
            if not review or review[0]!="accepted":
                cursor.execute('''UPDATE "Engagement".shared_reward_outbox SET state='waiting_review',
                    attempts=attempts+1,reason='awaiting_canonical_review',checked_at=now() WHERE progress_id=%s''',
                    [progress_id])
                return {"status":"waiting_review"}
        budget = row["verified_seconds"]*1000
        from .learning_policy import at, activity_type
        selected = at(cursor, session['started_at'])
        if selected and (session['observed_ms'] < 240000 or activity_type(
                {'type':session['component_type']}, session['activity_kind']) not in selected['settings']['hourActivities']):
            budget = 0
        cursor.execute('''SELECT event_id,intervals FROM "Engagement".shared_reward_observations
                          WHERE session_id=%s ORDER BY sequence''', [session["session_id"]])
        observed = _all(cursor)
        remaining = budget
        accepted = []
        for event in observed:
            for index,(start,end) in enumerate(_json(event["intervals"])):
                amount = min(end-start,remaining)
                if amount > 0:
                    accepted.append(("segment:"+digest([event["event_id"],index]),start,start+amount))
                    remaining -= amount
        total = budget-remaining
        cursor.execute('''INSERT INTO "Engagement".shared_reward_acceptances
            (session_id,progress_id,accepted_ms,reason,policy_version)
            VALUES(%s,%s,%s,'canonical_saved_study',%s) ON CONFLICT DO NOTHING''',
            [session["session_id"],row["id"],total,POLICY_VERSION])
        for receipt,start,end in accepted:
            cursor.execute('''INSERT INTO "Engagement".shared_reward_active_intervals
               (receipt_id,session_id,learner_id,active_start,active_end) VALUES(%s,%s,%s,%s,%s)
               ON CONFLICT DO NOTHING''', [receipt,session["session_id"],session["learner_id"],
               session["started_at"]+timedelta(milliseconds=start),session["started_at"]+timedelta(milliseconds=end)])
        intents = configured_hour_awards(cursor, session["learner_id"])
        if intents:
            cursor.execute('''SELECT g.event_reference FROM "Engagement".points_grants g
                JOIN "Engagement".points_rules r ON r.id=g.rule_id
                WHERE g.learner_id=%s AND r.key=%s AND g.event_reference=ANY(%s)''',
                [session["learner_id"],HOUR_RULE,[intent.event_reference for intent in intents]])
            existing_refs={item[0] for item in cursor.fetchall()}
            intents=[intent for intent in intents if intent.event_reference not in existing_refs]
        if row["kind"]=="quiz":
            cursor.execute('''SELECT id FROM "Learner".learner_progress_entries
                 WHERE learner_id=%s AND kind='quiz' AND quiz_ref=%s ORDER BY entry_order,id LIMIT 1''',
                 [row["learner_id"],row["quiz_ref"]])
            first = cursor.fetchone()
            if first and first[0]==row["id"]:
                correct,total_q = row["achieved_score"],row["total_score"]
                if correct is None or total_q is None or Decimal(correct)!=int(correct) or Decimal(total_q)!=int(total_q):
                    raise RewardError("invalid_saved_question_counts")
                intent = configured_quiz_award(cursor, row['submitted_at'], learner_id=session["learner_id"],quiz_id=str(row["quiz_ref"]),
                    submission_id=str(row["id"]),submitted_ordinal=1,
                    correct_answers=int(correct),total_questions=int(total_q))
                if intent:
                    intents.append(intent)
        for intent in intents:
            _grant(intent,row["full_name"] or "",session["module_id"])
        cursor.execute('''UPDATE "Engagement".shared_reward_outbox SET state='done',reason='',
             attempts=attempts+1,checked_at=now(),completed_at=now() WHERE progress_id=%s''',[progress_id])
        return {"status":"done","acceptedMilliseconds":total,"awardIntents":len(intents),"duplicate":False}


def reconcile_pending(limit=100):
    _active()
    with connections[DB].cursor() as cursor:
        cursor.execute('''SELECT progress_id FROM "Engagement".shared_reward_outbox
            WHERE state IN ('pending','waiting_review')
            ORDER BY checked_at ASC NULLS FIRST,created_at,progress_id LIMIT %s''',
            [integer(limit,1,1000)])
        ids = [row[0] for row in cursor.fetchall()]
    results=[]
    for progress_id in ids:
        try:
            results.append({"progressId":progress_id,**reconcile_progress(progress_id)})
        except RewardError as exc:
            # A stable reason only; no learner answers or raw exceptions.
            with connections[DB].cursor() as cursor:
                cursor.execute('''UPDATE "Engagement".shared_reward_outbox SET reason=%s,
                    attempts=attempts+1,checked_at=now() WHERE progress_id=%s AND state<>'done' ''',
                    [exc.code,progress_id])
            results.append({"progressId":progress_id,"status":"pending","code":exc.code})
        except Exception:
            with connections[DB].cursor() as cursor:
                cursor.execute('''UPDATE "Engagement".shared_reward_outbox
                    SET reason='reconciliation_retry_required', attempts=attempts+1,checked_at=now()
                    WHERE progress_id=%s AND state<>'done' ''',[progress_id])
            results.append({"progressId":progress_id,"status":"pending","code":"reconciliation_retry_required"})
    return results


def owns_progress_record(learner_id, record):
    """Post-commit hook: suppress old fixed awards only for durably registered new-policy work."""
    if getattr(settings,"KBC_SHARED_REWARDS_READY",False) is not True or not isinstance(record,dict):
        return False
    ref=record.get("timeTrackingSessionId")
    if not ref:
        return False
    with connections[DB].cursor() as cursor:
        cursor.execute('''SELECT o.progress_id FROM "Engagement".shared_reward_outbox o
           JOIN "Engagement".shared_reward_sessions s ON s.session_id=o.session_id
           WHERE s.session_id=%s AND s.learner_id=%s''',[str(ref),str(learner_id)])
        found=cursor.fetchone()
    if not found:
        return False
    try:
        reconcile_progress(found[0])
    except Exception:
        # Durable row remains pending. Never stack legacy points on retry failure.
        pass
    return True


def voucher_request(*,learner_id,learner_name,request_id,amount_gbp,module_id=PILOT_MODULE):
    _active();learner_id=identifier(str(learner_id));request_id=identifier(request_id)
    amount_gbp=integer(amount_gbp,1,100000)
    from .models import Reward,VoucherClaim
    from .services import points_summary
    with transaction.atomic(using=DB),connections[DB].cursor() as cursor:
        from .learning_policy import ensure_module
        ensure_module(cursor,module_id)
        policy(cursor,module_id);_wallet_lock(cursor,learner_id)
        cursor.execute('''SELECT r.*,c.points,c.status,c.requested_at FROM "Engagement".shared_reward_voucher_requests r
             JOIN "Engagement".voucher_claims c ON c.id=r.claim_id WHERE request_id=%s''',[request_id])
        prior=_one(cursor)
        if prior:
            if prior["learner_id"]!=learner_id or prior["amount_gbp"]!=amount_gbp:
                raise RewardError("voucher_request_conflict")
            return _voucher_result(prior,module_id,True)
        cursor.execute('SELECT reward_id FROM "Engagement".shared_reward_voucher_options WHERE amount_gbp=%s AND enabled',
                       [amount_gbp]);option=cursor.fetchone()
        if not option:
            raise RewardError("voucher_option_unavailable")
        reward=Reward.objects.select_for_update().get(pk=option[0])
        if not reward.active or reward.delivery_type!="digital" or reward.points!=amount_gbp*POINTS_PER_GBP:
            raise RewardError("voucher_option_unavailable")
        held=VoucherClaim.objects.filter(reward=reward,status__in=["pending","approved","fulfilled"]).count()
        if held>=reward.stock:
            raise RewardError("voucher_out_of_stock")
        if points_summary(learner_id)["balance"]<reward.points:
            raise RewardError("insufficient_points")
        claim=VoucherClaim.objects.create(learner_id=learner_id,learner_name=learner_name,reward=reward,
            points=reward.points,status="pending",delivery_type="digital",delivery_method="Email")
        cursor.execute('''INSERT INTO "Engagement".shared_reward_voucher_requests
            (request_id,learner_id,amount_gbp,claim_id) VALUES(%s,%s,%s,%s)''',
            [request_id,learner_id,amount_gbp,claim.pk])
        return _voucher_result({"request_id":request_id,"learner_id":learner_id,"amount_gbp":amount_gbp,
            "claim_id":claim.pk,"points":claim.points,"status":claim.status,"requested_at":claim.requested_at},module_id,False)


def _voucher_result(row,module_id,duplicate):
    return {"source":"lms","moduleId":module_id,"learnerId":row["learner_id"],
      "request":{"requestId":row["request_id"],"claimId":str(row["claim_id"]),
       "amountGbp":row["amount_gbp"],"points":row["points"],"status":row["status"],
       "requestedAt":row["requested_at"].isoformat()},"duplicate":duplicate}


TITLES = {
 "inclusiveness":"Complete the inclusiveness test",
 "linkedin-start":"Share your programme start on LinkedIn",
 "linkedin-learning":"Share what you learned on LinkedIn",
 "promotion-story":"Share your promotion case study",
 "career-planner":"Complete the career planner",
 "module-feedback":"Module feedback and London masterclass booking",
 "wellbeing":"Mid-module wellbeing questionnaire",
 "semester-winner":"Module leaderboard winner and Award of the Semester",
}


def set_leaderboard(*,learner_id,module_id,opted_in,display_name):
    _active();learner_id=identifier(str(learner_id))
    if type(opted_in) is not bool:
        raise RewardError("invalid_opt_in",400)
    if not isinstance(display_name,str):
        raise RewardError("invalid_display_name",400)
    name=display_name.strip()
    if len(name)>80 or any(ord(c)<32 or ord(c)==127 for c in name) or (opted_in and not name):
        raise RewardError("invalid_display_name",400)
    with transaction.atomic(using=DB),connections[DB].cursor() as cursor:
        from .learning_policy import ensure_module
        ensure_module(cursor,module_id)
        policy(cursor,module_id);_wallet_lock(cursor,learner_id)
        cursor.execute('''INSERT INTO "Engagement".shared_reward_leaderboard_optins
             (learner_id,module_id,display_name,opted_in) VALUES(%s,%s,%s,%s)
             ON CONFLICT(learner_id,module_id) DO UPDATE SET display_name=EXCLUDED.display_name,
               opted_in=EXCLUDED.opted_in,updated_at=now()''',[learner_id,module_id,name,opted_in])
    return {"source":"lms","moduleId":module_id,"learnerId":learner_id,"optedIn":opted_in,"displayName":name}


def summary(*,learner_id,module_id=PILOT_MODULE):
    from zoneinfo import ZoneInfo
    from .services import points_summary
    from .reward_policy import _month_shift
    learner_id=identifier(str(learner_id))
    today=timezone.now().astimezone(ZoneInfo("Europe/London")).date()
    with connections[DB].cursor() as cursor:
        cfg=policy(cursor,module_id,require=False)
        from .learning_policy import at, DEFAULTS
        current_policy = at(cursor)
        formula = current_policy['settings'] if current_policy else DEFAULTS
        enabled=bool(getattr(settings,"KBC_SHARED_REWARDS_READY",False) is True and cfg and cfg["enabled"] and cfg["starts_at"] and cfg["starts_at"]<=timezone.now())
        accepted=accepted_intervals(cursor,learner_id,module_id)
        accepted_ms=merged_milliseconds([(r["active_start_ms"],r["active_end_ms"]) for r in accepted])
        cursor.execute('''SELECT count(*) FROM "Engagement".shared_reward_sessions s
           LEFT JOIN "Engagement".shared_reward_acceptances a ON a.session_id=s.session_id
           WHERE s.learner_id=%s AND s.module_id=%s AND a.session_id IS NULL''',[learner_id,module_id])
        pending=cursor.fetchone()[0]
        cursor.execute('''SELECT opportunity,scope_key,verified_at FROM "Engagement".shared_reward_bonus_verifications
           WHERE learner_id=%s AND (module_id=%s OR opportunity NOT IN ('module-feedback','semester-winner'))''',
           [learner_id,module_id])
        verified={r["opportunity"]:r for r in _all(cursor)}
        opportunities=[]
        for key,amount in formula.get('bonusPoints',BONUS_POINTS).items():
            start=end=None
            status="available"
            if key=="module-feedback":
                end=cfg.get("module_end") if cfg else None
                start=end-timedelta(days=13) if end else None
            elif key=="wellbeing":
                began=cfg.get("programme_start") if cfg else None
                if began:
                    start=_month_shift(began,2);end=_month_shift(began,4)-timedelta(days=1)
            elif key=="semester-winner":
                start=cfg.get("semester_end") if cfg else None
            if key in ("module-feedback","wellbeing","semester-winner") and not start:
                status="needs_dates"
            elif start and today<start:
                status="scheduled"
            elif end and today>end:
                status="closed"
            if key in verified:
                status="completed"
            opportunities.append({"id":key,"title":TITLES[key],"points":amount,"status":status,
              "opensOn":start.isoformat() if start else None,"closesOn":end.isoformat() if end else None,
              "verifiedAt":verified[key]["verified_at"].isoformat() if key in verified else None,
              "actionable":False,"actionUrl":None})
        cursor.execute('''SELECT o.reward_id,o.amount_gbp,r.points,
           greatest(0,r.stock-count(c.id)) AS remaining
           FROM "Engagement".shared_reward_voucher_options o
           JOIN "Engagement".rewards r ON r.id=o.reward_id
           LEFT JOIN "Engagement".voucher_claims c ON c.reward_id=r.id AND c.status IN ('pending','approved','fulfilled')
           WHERE o.enabled AND r.active AND r.delivery_type='digital' AND r.points=o.amount_gbp*100
           GROUP BY o.reward_id,o.amount_gbp,r.points,r.stock ORDER BY o.amount_gbp''')
        options=[{"rewardId":str(r["reward_id"]),"amountGbp":r["amount_gbp"],"points":r["points"],
                  "remaining":r["remaining"]} for r in _all(cursor) if r["remaining"]>0] if enabled else []
        cursor.execute('''SELECT r.*,c.points,c.status,c.requested_at FROM "Engagement".shared_reward_voucher_requests r
             JOIN "Engagement".voucher_claims c ON c.id=r.claim_id WHERE r.learner_id=%s ORDER BY r.created_at DESC,r.request_id LIMIT 200''',[learner_id])
        claims=[_voucher_result(r,module_id,False)["request"] for r in _all(cursor)]
        cursor.execute('''SELECT opted_in,display_name FROM "Engagement".shared_reward_leaderboard_optins
                          WHERE learner_id=%s AND module_id=%s''',[learner_id,module_id]);own=_one(cursor)
        cursor.execute('''SELECT o.display_name,coalesce(sum(g.points),0) AS points
          FROM "Engagement".shared_reward_leaderboard_optins o
          LEFT JOIN "Engagement".shared_reward_award_links a ON a.module_id=o.module_id
          LEFT JOIN "Engagement".points_grants g ON g.id=a.grant_id AND g.learner_id=o.learner_id
          WHERE o.module_id=%s AND o.opted_in
          GROUP BY o.learner_id,o.display_name ORDER BY points DESC,o.display_name,o.learner_id LIMIT 50''',[module_id])
        ranked=_all(cursor)
        entries=[]
        if enabled and own and own["opted_in"]:
            rank=0;previous_points=None
            for i,row in enumerate(ranked):
                if row["points"]!=previous_points:
                    rank=i+1
                previous_points=row["points"]
                entries.append({"rank":rank,"displayName":row["display_name"],"points":row["points"]})
    return {"source":"lms","moduleId":module_id,"learnerId":learner_id,
      "policy":{"enabled":enabled,"version":POLICY_VERSION,
                "startsAt":cfg["starts_at"].isoformat() if cfg and cfg["starts_at"] else None,"pointsPerGbp":100,
                "formula":formula,"minimumStudySeconds":240 if current_policy else 0},
      "points":points_summary(learner_id),
      "study":{"acceptedStudySeconds":accepted_ms//1000,"basis":"reward_accepted_active_intervals",
               "formalOtj":False,"pendingSessionCount":pending},
      "opportunities":opportunities,"voucherOptions":options,"voucherRequests":claims,
      "leaderboard":{"optedIn":bool(own and own["opted_in"]),"displayName":own["display_name"] if own else "",
                     "entries":entries}}


def verify_bonus(*,learner_id,learner_name,module_id,opportunity,verification_id,
                 evidence_reference,completion_date,staff_id,confirmed_winner=False,learner_kind=None):
    """Staff attests completion. Resolve approved file ownership, never read its contents."""
    from zoneinfo import ZoneInfo
    _active()
    learner_id=identifier(str(learner_id));verification_id=identifier(verification_id)
    evidence_reference=identifier(evidence_reference);staff_id=identifier(str(staff_id))
    if opportunity not in BONUS_POINTS:
        raise RewardError("unknown_opportunity",400)
    today=timezone.now().astimezone(ZoneInfo("Europe/London")).date()
    with transaction.atomic(using=DB),connections[DB].cursor() as cursor:
        from .learning_policy import ensure_module
        ensure_module(cursor,module_id)
        cfg=policy(cursor,module_id);_wallet_lock(cursor,learner_id)
        if not hasattr(completion_date,"isoformat") or completion_date>today or completion_date<cfg["starts_at"].astimezone(ZoneInfo("Europe/London")).date():
            raise RewardError("completion_outside_policy",400)
        # An approved upload is necessary supporting evidence; the staff action,
        # not upload/virus-scan success, attests that the opportunity was completed.
        if learner_kind not in ("apprenticeship","commercial"):
            raise RewardError("learner_kind_unresolved",400)
        cursor.execute('''SELECT id FROM "Learner".evidence_files
            WHERE id::text=%s AND learner_id::text=%s AND learner_kind=%s AND status='approved' ''',
            [evidence_reference,learner_id,learner_kind])
        if not cursor.fetchone():
            raise RewardError("verified_evidence_not_owned_or_approved",404)
        booked=False
        if opportunity=="module-feedback" and cfg.get("masterclass_event_id"):
            cursor.execute('''SELECT 1 FROM "Engagement".event_bookings
               WHERE learner_id=%s AND event_id=%s AND status='booked' LIMIT 1''',
               [learner_id,cfg["masterclass_event_id"]])
            booked=bool(cursor.fetchone())
        if opportunity=="semester-winner":
            cursor.execute('''SELECT o.learner_id,coalesce(sum(
                 CASE WHEN b.opportunity='semester-winner' THEN 0 ELSE g.points END),0) AS points
               FROM "Engagement".shared_reward_leaderboard_optins o
               LEFT JOIN "Engagement".shared_reward_award_links a ON a.module_id=o.module_id
               LEFT JOIN "Engagement".points_grants g ON g.id=a.grant_id AND g.learner_id=o.learner_id
               LEFT JOIN "Engagement".shared_reward_bonus_verifications b ON b.grant_id=g.id
               WHERE o.module_id=%s AND o.opted_in
               GROUP BY o.learner_id''',[module_id])
            board={item["learner_id"]:item["points"] for item in _all(cursor)}
            if learner_id not in board:
                raise RewardError("winner_must_opt_in")
            if board[learner_id]!=max(board.values()):
                raise RewardError("winner_must_top_module_leaderboard")
        intent=verified_bonus(learner_id=learner_id,opportunity=opportunity,
            verification_id=verification_id,verified=True,today=completion_date,module_id=module_id,
            module_end=cfg.get("module_end"),programme_start=cfg.get("programme_start"),
            semester_end=cfg.get("semester_end"),confirmed_winner=confirmed_winner,
            masterclass_booking_verified=booked)
        from .learning_policy import at
        from dataclasses import replace
        scope=intent.event_reference
        cursor.execute('''SELECT * FROM "Engagement".shared_reward_bonus_verifications
             WHERE id=%s OR (learner_id=%s AND scope_key=%s)''',[verification_id,learner_id,scope])
        previous=_all(cursor)
        if previous:
            prior=previous[0]
            if len(previous)!=1 or prior["learner_id"]!=learner_id or prior["scope_key"]!=scope:
                raise RewardError("verification_id_conflict")
            if prior["id"]==verification_id and (prior["evidence_reference"]!=evidence_reference
                 or prior["completion_date"]!=completion_date or prior["module_id"]!=module_id
                 or prior["opportunity"]!=opportunity):
                raise RewardError("verification_id_conflict")
            # Different verification IDs for the same once-only opportunity still
            # return the original grant, never another reward.
            return {"verified":True,"duplicate":True,"verificationId":prior["id"],"grantId":str(prior["grant_id"]),"points":prior["points"]}
        selected = at(cursor)
        if selected:
            amount = selected['settings']['bonusPoints'][opportunity]
            if not amount:
                raise RewardError('bonus_disabled')
            intent = replace(intent,points=amount)
        cursor.execute('''INSERT INTO "Engagement".shared_reward_bonus_verifications
          (id,learner_id,module_id,opportunity,scope_key,evidence_reference,completion_date,verified_by,points)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
          [verification_id,learner_id,module_id,opportunity,scope,evidence_reference,completion_date,staff_id,intent.points])
        grant=_grant(intent,learner_name,module_id)
        cursor.execute('UPDATE "Engagement".shared_reward_bonus_verifications SET grant_id=%s WHERE id=%s',[grant.pk,verification_id])
        return {"verified":True,"duplicate":False,"verificationId":verification_id,"grantId":str(grant.pk),"points":intent.points}
