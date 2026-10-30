"""Migrated-only answer provenance. Call mutations under the overlay row lock."""
from copy import deepcopy

from django.utils import timezone

from .migrated_reviews import TEXT_ANSWER_LIMIT, meeting_summary_field


class AnswerConflict(ValueError):
    pass


def answer_version(overlay):
    return overlay.updated_at.isoformat()


def check_answer_version(overlay, payload):
    # Every initialized migrated form now exposes the same overlay version,
    # including legacy/unbound forms whose definition has not changed yet.
    if payload.get("answerVersion") != answer_version(overlay):
        raise AnswerConflict("This review changed. Reopen it and review the latest answers before saving. Your unsaved text has been kept on screen.")


def preserve_original(state):
    """Do not describe an already edited legacy summary as original AI output."""
    if (not isinstance(state.get("aiSummaryOriginal"), dict)
            and isinstance(state.get("summary"), dict)
            and state.get("summaryStatus") == "ready" and not state.get("editedAt")):
        state["aiSummaryOriginal"] = deepcopy(state["summary"])
        state["aiSummaryProvenance"] = {key: state.get(key) for key in (
            "generatedAt", "generatedFromTranscript", "transcriptArtifactId", "transcriptHash", "model",
            "source", "eventKey", "graphEventId", "generatedBy", "transcriptTruncated",
        )}


def binding_state(overlay, state=None):
    try:
        field = meeting_summary_field(getattr(overlay, "template_snapshot", {}) or {})
    except ValueError as exc:
        return {"status": "invalid-binding", "message": str(exc)}
    if not field:
        return {"status": "no-binding"}
    state = state if state is not None else (overlay.meeting_intelligence or {})
    key = field["key"]
    saved = state.get("summaryBinding") or {}
    answers = overlay.answers or {}
    if saved.get("fieldKey") == key and saved.get("state") in {
        "NEVER_POPULATED", "AI_POPULATED_UNEDITED", "COACH_EDITED", "COACH_CLEARED",
    }:
        result = dict(saved)
    else:
        # Presence is evidence of a prior write, including an explicit empty
        # answer. An empty value alone never establishes overwrite eligibility.
        result = {"fieldKey": key, "state": (
            "COACH_CLEARED" if answers[key] in (None, "") else "COACH_EDITED"
        ) if key in answers else "NEVER_POPULATED"}
    result.update({"answerPresent": key in answers, "answer": answers.get(key)})
    return result


def populate_answer(overlay, state, summary_text):
    result = binding_state(overlay, state)
    if result.get("status") in {"invalid-binding", "no-binding"}:
        return result
    key = result["fieldKey"]
    if overlay.status not in {"scheduled", "in-progress"}:
        result["status"] = "read-only"
    elif result["state"] != "NEVER_POPULATED" or key in (overlay.answers or {}):
        result["status"] = "answer-preserved"
    elif not summary_text:
        result["status"] = "summary-unavailable"
    elif len(summary_text) > TEXT_ANSWER_LIMIT:
        result["status"] = "summary-too-long"
    else:
        overlay.answers = {**(overlay.answers or {}), key: summary_text}
        result.update(state="AI_POPULATED_UNEDITED", status="populated",
                      populatedAt=timezone.now().isoformat(), answer=summary_text, answerPresent=True)
    state["summaryBinding"] = {k: v for k, v in result.items() if k not in {"answer", "answerPresent"}}
    return result


def record_answer_edit(overlay, answers, *, actor, edited_fields=(), role="advisor", account_id=None):
    if not isinstance(answers, dict):
        raise ValueError("answers must be an object keyed by field id.")
    state = deepcopy(overlay.meeting_intelligence or {})
    audit = state.setdefault("answerEdits", {})
    previous = overlay.answers or {}
    for key, value in answers.items():
        if key not in previous or previous[key] != value:
            audit[key] = {"actor": actor, "role": role, "accountId": account_id,
                          "at": timezone.now().isoformat()}
    overlay.meeting_intelligence = state
    field = meeting_summary_field(overlay.template_snapshot)
    if not field:
        return
    key = field["key"]
    changed = ((key in previous) != (key in answers) or previous.get(key) != answers.get(key)
               or key in edited_fields)
    if not changed:
        return
    preserve_original(state)
    state["summaryBinding"] = {
        **{k: v for k, v in binding_state(overlay, state).items() if k not in {"answer", "answerPresent"}},
        "fieldKey": key,
        "state": "COACH_CLEARED" if answers.get(key) in (None, "") else "COACH_EDITED",
        "status": "answer-preserved", "editedAt": timezone.now().isoformat(), "editedBy": actor,
        "editedRole": role,
    }
    overlay.meeting_intelligence = state
