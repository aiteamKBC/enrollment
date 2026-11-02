"""Pure deterministic scoring. Decimal arithmetic and no external services."""
from decimal import Decimal

from .configuration import ConfigurationError, PATHWAYS, validate
from .selection import score_based_result


def calculate(configuration, pathway, responses, complete=False):
    config = validate(configuration)
    if not isinstance(pathway, str) or pathway not in PATHWAYS or not isinstance(responses, dict):
        raise ConfigurationError("Choose a valid pathway and provide question responses.")
    questions = {q["id"]: q for q in config["questions"] if q["active"] and q["pathway"] == pathway}
    programmes = [p for p in config["programmes"] if p["active"] and p["pathway"] == pathway]
    if not questions or len(programmes) != 2:
        raise ConfigurationError("This pathway is not available.")
    if set(responses) - set(questions):
        raise ConfigurationError("A response references an unknown, inactive or different-pathway question.")
    totals = {p["id"]: Decimal(0) for p in programmes}
    critical = {p["id"]: [] for p in programmes}
    available = sum((Decimal(str(q["weight"])) for q in questions.values()), Decimal(0))
    answered = Decimal(0)
    evidence = Decimal(0)
    mixed = Decimal(0)
    missing = []
    breakdown = []
    rules = config["rules"]
    for qid, q in questions.items():
        weight = Decimal(str(q["weight"]))
        selected = responses.get(qid)
        points = {pid: Decimal(0) for pid in totals}
        if qid not in responses:
            if q["required"]:
                missing.append(qid)
            breakdown.append({"questionId": qid, "title": q["title"], "weight": float(weight), "answered": False,
                              "points": {pid: 0 for pid in totals}, "selectedAnswers": []})
            continue
        if not isinstance(selected, list) or any(not isinstance(aid, str) for aid in selected):
            raise ConfigurationError(f"{qid}: select answers or an empty list for none apply.")
        if len(set(selected)) != len(selected) or (not q["multiple"] and len(selected) > 1):
            raise ConfigurationError(f"{qid}: invalid number of selected answers.")
        allowed = {a["id"]: a for a in q["answers"] if a["active"]}
        if set(selected) - set(allowed):
            raise ConfigurationError(f"{qid}: an answer is unknown, inactive or belongs to another question.")
        answered += weight  # Explicit none-apply is a completed question, with zero programme evidence.
        for aid in selected:
            for pid, affinity in allowed[aid]["affinities"].items():
                if pid in points:
                    points[pid] += weight * Decimal(str(affinity))
        points = {pid: min(weight, value) for pid, value in points.items()}
        if any(points.values()):
            evidence += weight
        contradictory = all(value / weight >= Decimal(str(rules["mixedAffinityThreshold"])) for value in points.values())
        if contradictory:
            mixed += weight
        target = q.get("criticalProgramme")
        if q["critical"] and target in points and not contradictory and any(
            allowed[aid]["affinities"].get(target, 0) >= rules["criticalStrength"] for aid in selected
        ):
            critical[target].append(qid)
        for pid in totals:
            totals[pid] += points[pid]
        breakdown.append({"questionId": qid, "title": q["title"], "weight": float(weight), "answered": True,
                          "points": {pid: float(value) for pid, value in points.items()},
                          "selectedAnswers": [{"id": aid, "text": allowed[aid]["text"]} for aid in selected]})
    if complete and missing:
        raise ConfigurationError("Answer each required question (or choose none apply): " + ", ".join(missing))
    def percentage(value):
        return float(value / available * 100) if available else 0.0
    scores = {pid: percentage(value) for pid, value in totals.items()}
    coverage = percentage(answered)
    evidence_coverage = percentage(evidence)
    ordered = sorted(scores, key=lambda pid: scores[pid], reverse=True)
    winner, other = ordered
    difference = scores[winner] - scores[other]
    reasons = []
    if not answered or not evidence:
        outcome = "INSUFFICIENT_INFORMATION"
        reasons.append("NO_RESPONSIBILITY_EVIDENCE")
    else:
        if coverage < rules["minimumCoverage"]:
            reasons.append("LOW_ASSESSMENT_COVERAGE")
        if scores[winner] < rules["minimumScore"]:
            reasons.append("BELOW_MINIMUM_MATCH")
        if difference <= 0 or difference < rules["minimumDifference"]:
            reasons.append("CLOSE_PROGRAMME_SCORES")
        if mixed and percentage(mixed) >= rules["mixedQuestionThreshold"]:
            reasons.append("CONTRADICTORY_RESPONSIBILITIES")
        if next(p["level"] for p in programmes if p["id"] == winner) == 6 and len(critical[winner]) < rules["minimumCriticalIndicators"]:
            reasons.append("MISSING_CRITICAL_LEVEL_6_EVIDENCE")
        outcome = "FURTHER_REVIEW_REQUIRED" if reasons else f"RECOMMENDED_{winner}"
    recommended = winner if outcome.startswith("RECOMMENDED_") else None
    strongest = sorted((b for b in breakdown if recommended and b["points"][recommended] > 0),
                       key=lambda b: b["points"][recommended], reverse=True)[:3]
    strongest = [{**row, "selectedAnswers": [a for a in row["selectedAnswers"] if any(
        choice["id"] == a["id"] and choice["affinities"].get(recommended, 0) > 0
        for choice in questions[row["questionId"]]["answers"])]} for row in strongest]
    result = {"outcome": outcome, "recommendedProgramme": recommended,
            "scores": {pid: round(value, 2) for pid, value in scores.items()},
            "coverage": round(coverage, 2), "evidenceCoverage": round(evidence_coverage, 2),
            "availableWeight": float(available), "criticalIndicators": critical,
            "reasons": reasons or ["CLEAR_RESPONSIBILITY_MATCH"], "missingRequired": missing,
            "strongestEvidence": strongest, "breakdown": breakdown}
    return score_based_result(config, pathway, result)
