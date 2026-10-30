"""Real-time engagement-points hook for learner progress.

Single entry point called from `learner_api.active_users.save_progress_record`
via `transaction.on_commit(..., using="enrolment")` the instant a progress row
actually commits — every progress-recording path (quiz, video, component)
funnels through that one function, so this is the one place award logic for
progress-driven points lives.

Fired post-commit (not inline) because progress and engagement route to
different Django DB aliases — `enrolment` vs `default` — even though both
currently point at the same Neon database (see `learner_api.routers`). An
inline grant inside the progress transaction could survive a later rollback
of that very save; on_commit cannot.

Contract with the caller: this function is BEST-EFFORT and must NEVER raise —
a points failure must not surface as a progress-save error the learner never
asked about. The caller's on_commit registration also can't propagate an
exception anywhere useful by the time it fires, so the guarantee has to live
here.

KNOWN GAP (out of scope here): `save_progress_record`'s callers accept any
quiz/component id without verifying it belongs to *this* learner's assigned
training plan — so this hook can be driven by an activity the learner was
never assigned. Closing that means adding an assignment check to the quiz/
video/component submission endpoints in `learner_api`, which is a change to
the core progress-recording path (OTJH hours, KSB coverage) with a much
larger blast radius than engagement itself, and needs its own dedicated pass
against the training-plan-assignment schema rather than a guess made from here.
"""
import logging

logger = logging.getLogger(__name__)

# Component-type completions that earn points, and which rule they earn.
# Deliberately narrow: reflection/activity/workplace-evidence/live_session/
# "recording placeholder" completions are self-reported by the learner with
# no independent verification, so they are not auto-granted here. Live-session
# attendance and evidence approval get their own, properly-sourced rules
# (verified_teams_attendance / the evidence-approval path) rather than trusting
# a learner's own "I completed this" tick.
_COMPONENT_TYPE_RULES = {
    "reading": "pdf_viewed",
    "powerpoint": "powerpoint_viewed",
    "podcast": "podcast_attended",
}


def award_for_progress(learner_id, learner_name, record, progress_id=None):
    """Grant engagement points for one progress record, if it qualifies.

    `record` is the same dict `save_progress_record` was called with — a
    "kind" ("quiz" | "video" | "component"), an "attempt" (1-based), and
    kind-specific fields (`componentType` for "component").

    Points are awarded ONCE PER LEARNER PER ACTIVITY, on the FIRST ATTEMPT ONLY:
      - quiz      -> "quiz_passed", only if attempt == 1 AND the attempt passed.
                     A failed first attempt earns nothing, even if a later retake passes.
      - video     -> "recorded_session_attended", only on the first watch (attempt 1).
      - component -> rule from `_COMPONENT_TYPE_RULES` by `componentType`, first
                     completion only; component types not in that map earn nothing.

    Never raises: a missing/inactive rule, a DB error, anything — all swallowed
    and logged, so a dropped grant never surfaces as a progress-save failure.
    """
    # Registered shared-policy submissions own their entire award decision.
    # Run before the legacy first-attempt guard: later accepted study still counts.
    try:
        from django.conf import settings
        if getattr(settings, "KBC_SHARED_REWARDS_READY", False) is True:
            from .saved_progress_rewards import owns_saved_progress
            if owns_saved_progress(learner_id, record, progress_id):
                return
    except Exception:
        # An unavailable authority cannot safely fall back to a second award.
        # The transactional outbox retains registered work for reconciliation.
        logger.warning("Shared reward reconciliation is pending.")
        return

    if learner_id is None or not isinstance(record, dict) or record.get("attempt") != 1:
        return  # first attempt only — nothing else ever grants

    try:
        # Lazy import: keeps this module cheap to import and avoids any
        # import-time coupling for callers that touch learner_api at module load.
        from .models import PointsRule
        from .services import grant_points

        lid = str(learner_id)
        name = learner_name or ""
        kind = record.get("kind")

        if kind == "quiz" and record.get("passed"):
            # event_reference MUST carry the learner id: grant_points de-dupes on
            # (rule, event_reference) with NO learner filter (see services.py), so a
            # reference of just "quiz:64" would collide across learners. This form is
            # unique per (learner, quiz) and idempotent against a double-submit.
            quiz_id = record.get("quizId")
            grant_points(
                "quiz_passed", lid, name,
                event_reference=f"quiz:{quiz_id}:learner:{lid}",
                source_type="hook", source_id=str(quiz_id) if quiz_id is not None else None,
            )
        elif kind == "video":
            component_id = record.get("componentId")
            grant_points(
                "recorded_session_attended", lid, name,
                event_reference=f"video:{component_id}:learner:{lid}",
                source_type="hook", source_id=str(component_id) if component_id is not None else None,
            )
        elif kind == "component":
            rule_key = _COMPONENT_TYPE_RULES.get(record.get("componentType"))
            if rule_key:
                component_id = record.get("componentId")
                grant_points(
                    rule_key, lid, name,
                    event_reference=f"component:{component_id}:learner:{lid}",
                    source_type="hook", source_id=str(component_id) if component_id is not None else None,
                )
    except PointsRule.DoesNotExist:
        logger.info("award_for_progress: no active rule for learner %s's %s", learner_id, record.get("kind"))
    except Exception:  # noqa: BLE001 — a dropped grant must never look like a progress failure
        logger.warning("award_for_progress failed for learner %s", learner_id, exc_info=True)
