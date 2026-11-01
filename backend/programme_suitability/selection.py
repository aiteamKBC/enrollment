"""Self-service selection policy over authoritative, persisted recommendation results."""
import copy
import math
from .configuration import ConfigurationError

REVIEW_OUTCOMES = {"FURTHER_REVIEW_REQUIRED", "INSUFFICIENT_INFORMATION"}


def score_based_result(configuration, pathway, result):
    """Interpret the final matching indices without discarding admissions diagnostics."""
    out = copy.deepcopy(result)
    programmes = [p for p in configuration["programmes"] if p["active"] and p["pathway"] == pathway]
    low = next((p for p in programmes if p["level"] == 4), None)
    high = next((p for p in programmes if p["level"] == 6), None)
    scores = out.get("scores") or {}
    values = [scores.get(p["id"]) for p in (low, high)] if low and high else []
    valid = len(values) == 2 and all(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100 for value in values)
    out.setdefault("admissionsReviewOutcome", out.get("outcome", "INSUFFICIENT_INFORMATION"))
    out.setdefault("admissionsFlags", list(out.get("reasons") or []))
    flags = [flag for flag in out.get("recommendationFlags", []) if flag != "SCORE_TIE_LEVEL_4_DEFAULT"]
    if valid and any(value > 0 for value in values):
        winner = high if values[1] > values[0] else low
        out["recommendedProgramme"] = winner["id"]
        out["outcome"] = f"RECOMMENDED_{winner['id']}"
        if values[0] == values[1]:
            flags.append("SCORE_TIE_LEVEL_4_DEFAULT")
    else:
        out["recommendedProgramme"] = None
        out["outcome"] = "INSUFFICIENT_INFORMATION"
    out["recommendationFlags"] = flags
    out["recommendationPolicy"] = "WEIGHTED_SCORE_V2"
    return out


def recommended_programme(configuration, pathway, result):
    result = score_based_result(configuration, pathway, result)
    identifier = result.get("recommendedProgramme")
    if not identifier or result.get("outcome") != f"RECOMMENDED_{identifier}":
        return None
    return next((p for p in configuration["programmes"]
                 if p["id"] == identifier and p["active"] and p["pathway"] == pathway), None)


def allowed_selections(configuration, pathway, result):
    recommended = recommended_programme(configuration, pathway, result)
    if recommended is None:
        return []
    allowed = [recommended["id"]]
    if recommended["level"] == 6:
        allowed.extend(p["id"] for p in configuration["programmes"]
                       if p["active"] and p["pathway"] == pathway and p["level"] == 4)
    return allowed


def validate_selection(configuration, pathway, result, selection):
    if not isinstance(selection, str):
        raise ConfigurationError("Use a programme identifier for your selection.")
    if selection and selection not in allowed_selections(configuration, pathway, result):
        raise ConfigurationError("This programme is not available for self-selection from your assessment. You can request an admissions review.")
    return selection


def selection_source(result, selection):
    if not selection:
        return "PENDING_REVIEW" if result.get("outcome") in REVIEW_OUTCOMES else "NOT_SELECTED"
    return "SYSTEM_RECOMMENDATION" if selection == result.get("recommendedProgramme") else "LEARNER_ALTERNATIVE"
