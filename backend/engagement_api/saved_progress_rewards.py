"""Channel-independent policy routing from real persisted progress and entitlement."""
from django.conf import settings
from django.db import connections,transaction
from . import shared_rewards as r
from .segment_rules import RewardError


def _resolve_component(row):
    # Client titles/module labels/attempt counters are never scope evidence.
    from learner_api.models import EnrolmentUser,LearnerProfile
    from learner_api.learner_detail import build_learner_detail
    source=EnrolmentUser.all_learners.filter(pk=row["enrolment_id"]).first()
    if source is None or not LearnerProfile.objects.filter(
        pk=row["learner_id"],enrolment_id=source.pk,lifecycle_status="active").exists():
        return None
    detail=build_learner_detail(source,source.pk,compact=True)
    if any(isinstance(detail.get(k),dict) and detail[k].get("blocked") is True
           for k in ("accessGate","learningAccess")):
        return None
    candidates=[]
    for component in detail.get("components",[]):
        if not component.get("moduleId") or (row.get('module_ref') and component.get('moduleId') != row['module_ref']):
            continue
        if row["kind"]=="quiz":
            matches=str((component.get("quizMeta") or {}).get("quizId"))==str(row["quiz_ref"])
        else:
            matches=component.get("componentId")==row["component_ref"]
        if matches:
            candidates.append(component)
    if len(candidates)>1:
        raise RewardError("ambiguous_canonical_activity")
    if not candidates:
        return None
    selected = candidates[0]
    return {**selected, "componentId": selected.get('componentId') or 'quiz-'+str(row['quiz_ref']),
            "learnerKind":str(source.learner_type or "").lower()}


def _reconcile_saved_progress(progress_id,expected_learner=None):
    r._active()
    # Read/resolve before the wallet transaction because the maintained detail
    # service may use the enrolment connection to hydrate canonical snapshots.
    with connections[r.DB].cursor() as cursor:
        cursor.execute('''SELECT p.*,l.enrolment_id,l.full_name FROM "Learner".learner_progress_entries p
          JOIN "Learner".learners l ON l.id=p.learner_id
          JOIN "Engagement".shared_reward_saved_progress j ON j.progress_id=p.id WHERE p.id=%s''',[progress_id])
        row=r._one(cursor)
        if not row:
            return {"status":"not_scoped","owned":False}
        if expected_learner is not None and str(row["enrolment_id"])!=str(expected_learner):
            raise RewardError("progress_identity_mismatch")
        from .learning_policy import all_course_start
        global_start = all_course_start(cursor)
        all_scoped = bool(global_start and row['submitted_at'] and row['submitted_at'] >= global_start)
        cursor.execute('SELECT state FROM "Engagement".shared_reward_saved_progress WHERE progress_id=%s',[progress_id])
        state=cursor.fetchone()[0]
        if state in ("done","not_scoped"):
            return {"status":state,"owned":state=="done" or all_scoped,"duplicate":True}
    component=_resolve_component(row)
    with transaction.atomic(using=r.DB),connections[r.DB].cursor() as cursor:
        r._wallet_lock(cursor,str(row["enrolment_id"]))
        cursor.execute('SELECT state FROM "Engagement".shared_reward_saved_progress WHERE progress_id=%s FOR UPDATE',[progress_id])
        state=cursor.fetchone()[0]
        if state in ("done","not_scoped"):
            return {"status":state,"owned":state=="done" or all_scoped,"duplicate":True}
        if component is not None:
            cfg = r.policy(cursor, component['moduleId'], require=False)
            if not cfg or not cfg['enabled'] or not row['submitted_at'] or row['submitted_at'] < cfg['starts_at']:
                component = None
        if component is None:
            cursor.execute('''UPDATE "Engagement".shared_reward_saved_progress SET state='not_scoped',
               checked_at=now(),attempts=attempts+1 WHERE progress_id=%s''',[progress_id])
            return {"status":"not_scoped","owned":all_scoped}
        from .learning_policy import ensure_module
        ensure_module(cursor, component['moduleId'])
        cursor.execute('''UPDATE "Engagement".shared_reward_saved_progress SET module_id=%s,component_id=%s
             WHERE progress_id=%s''',[component['moduleId'],component["componentId"],progress_id])
        # Browser study without observed segments never earns invented hour points.
        # Quiz points still use the exact persisted first submission and score.
        if component.get("tutorValidationRequired"):
            cursor.execute('''SELECT 1 FROM "Learner".learning_reflection_submissions
              WHERE learner_id::text=%s AND activity_id=%s AND progress_entry_id=%s AND status='accepted' ''',
              [str(row["enrolment_id"]),component["componentId"],progress_id])
            if not cursor.fetchone():
                cursor.execute('''UPDATE "Engagement".shared_reward_saved_progress SET state='waiting_review',
                    checked_at=now(),attempts=attempts+1,reason='awaiting_canonical_review' WHERE progress_id=%s''',[progress_id])
                return {"status":"waiting_review","owned":True,"reviewContext":{
                  "component":component,"learnerId":str(row["enrolment_id"]),
                  "learnerName":row["full_name"] or "","progressId":progress_id,
                  "verifiedSeconds":row["verified_seconds"] or 0}}
        if row["kind"]=="quiz":
            cursor.execute('''SELECT id FROM "Learner".learner_progress_entries
                 WHERE learner_id=%s AND kind='quiz' AND quiz_ref=%s ORDER BY entry_order,id LIMIT 1''',
                 [row["learner_id"],row["quiz_ref"]])
            if cursor.fetchone()[0]==progress_id:
                correct,total=row["achieved_score"],row["total_score"]
                if correct is None or total is None or correct!=int(correct) or total!=int(total):
                    raise RewardError("invalid_saved_question_counts")
                intent=r.configured_quiz_award(cursor, row['submitted_at'], learner_id=str(row["enrolment_id"]),quiz_id=str(row["quiz_ref"]),
                    submission_id=str(progress_id),submitted_ordinal=1,correct_answers=int(correct),total_questions=int(total))
                if intent:
                    r._grant(intent,row["full_name"] or "",component['moduleId'])
        cursor.execute('''UPDATE "Engagement".shared_reward_saved_progress SET state='done',reason='',
            checked_at=now(),attempts=attempts+1 WHERE progress_id=%s''',[progress_id])
        return {"status":"done","owned":True}


def reconcile_saved_progress(progress_id,expected_learner=None):
    result=_reconcile_saved_progress(progress_id,expected_learner)
    review=result.pop("reviewContext",None)
    if review:
        # Scope proof is now committed and visible to the canonical queue alias.
        from learner_api.marking_queue_entry import queue_for_marking
        component=review["component"]
        queue_for_marking(component_id=component["componentId"],kind=component["learnerKind"],
          learner_id=review["learnerId"],context={
           "learnerName":review["learnerName"],"activityType":component.get("type") or "quiz",
           "activityTitle":component.get("component") or component["componentId"],
           "moduleTitle":component.get("module") or "","weekTitle":component.get("week") or "",
           "actualTimeHours":review["verifiedSeconds"]/3600,
           "progressEntryId":progress_id,"sharedRewardProgressId":progress_id})
    return result

def reconcile_pending_saved(limit=100):
    r._active()
    with connections[r.DB].cursor() as cursor:
        cursor.execute('''SELECT progress_id FROM "Engagement".shared_reward_saved_progress
          WHERE state IN ('pending','waiting_review') ORDER BY checked_at ASC NULLS FIRST,created_at,progress_id LIMIT %s''',
          [r.integer(limit,1,1000)])
        ids=[row[0] for row in cursor.fetchall()]
    result=[]
    for pid in ids:
        try:
            item=reconcile_saved_progress(pid)
        except Exception as exc:
            code=exc.code if isinstance(exc,RewardError) else "reconciliation_retry_required"
            with connections[r.DB].cursor() as cursor:
                cursor.execute('''UPDATE "Engagement".shared_reward_saved_progress SET
                 checked_at=now(),attempts=attempts+1,reason=%s WHERE progress_id=%s AND state<>'done' ''',[code,pid])
            item={"status":"pending","code":code}
        result.append({"progressId":pid,**item})
    return result


def owns_saved_progress(learner_id,record,progress_id=None):
    if getattr(settings,"KBC_SHARED_REWARDS_READY",False) is not True:
        return False
    if type(progress_id) is not int or progress_id<=0:
        # Missing persisted identity is not a licence to apply legacy policy.
        raise RewardError("persisted_progress_id_required")
    with connections[r.DB].cursor() as cursor:
        cursor.execute('''SELECT s.learner_id FROM "Engagement".shared_reward_outbox o
            JOIN "Engagement".shared_reward_sessions s ON s.session_id=o.session_id
            WHERE o.progress_id=%s''',[progress_id])
        registered=cursor.fetchone()
    if registered:
        if registered[0]!=str(learner_id):
            raise RewardError("progress_identity_mismatch")
        try:
            r.reconcile_progress(progress_id)
        except Exception:
            pass  # committed outbox retains the retry; legacy never stacks
        return True
    return reconcile_saved_progress(progress_id,expected_learner=learner_id)["owned"]
