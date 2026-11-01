"""Strict JSON configuration boundary, shared by publishing and previews."""
import copy
import json
import math
import re
from pathlib import Path

PROGRAMMES = {"ME_L4": ("marketing", 4), "MM_L6": ("marketing", 6),
              "APM_L4": ("project-controls", 4), "PCP_L6": ("project-controls", 6)}
PATHWAYS = {"marketing", "project-controls"}
KEY = "programmeSuitability"


class ConfigurationError(ValueError):
    pass


def defaults():
    return json.loads(Path(__file__).with_name("seed.json").read_text(encoding="utf-8"))


def number(value, label, minimum=0, maximum=100, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ConfigurationError(f"{label} must be a finite number.")
    if not minimum <= value <= maximum or (integer and int(value) != value):
        raise ConfigurationError(f"{label} must be {'an integer ' if integer else ''}between {minimum} and {maximum}.")
    return value


def text(value, label, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ConfigurationError(f"{label} is required and must be at most {limit} characters.")
    return value.strip()


def flag(item, key):
    if not isinstance(item.get(key), bool):
        raise ConfigurationError(f"{key} must be true or false.")


def identifier(value, label):
    value = text(value, label, 100)
    if value in {"__proto__", "prototype", "constructor"} or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ConfigurationError(f"{label} must contain only letters, numbers, underscores or hyphens.")
    return value


def validate(raw):
    if not isinstance(raw, dict):
        raise ConfigurationError("Programme suitability configuration must be an object.")
    config = copy.deepcopy(raw)
    if type(config.get("schemaVersion")) is not int or config["schemaVersion"] != 1:
        raise ConfigurationError("Unsupported suitability configuration version.")
    flag(config, "enabled")
    text(config.get("introduction"), "Assessment introduction", 4000)
    programmes = config.get("programmes")
    if not isinstance(programmes, list) or len(programmes) != 4:
        raise ConfigurationError("Keep the four stable programme definitions; deactivate instead of removing them.")
    seen = set()
    active = set()
    for p in programmes:
        if not isinstance(p, dict) or not isinstance(p.get("id"), str) or p["id"] not in PROGRAMMES or p["id"] in seen:
            raise ConfigurationError("Invalid or duplicate programme identifier.")
        seen.add(p["id"])
        if (p.get("pathway"), p.get("level")) != PROGRAMMES[p["id"]]:
            raise ConfigurationError("Programme levels and pathways must match their stable identifiers.")
        for key in ("title", "summary", "description"):
            text(p.get(key), f"{p['id']} {key}", 6000)
        flag(p, "active")
        number(p.get("order"), "Programme order", 0, 10000, True)
        bullets = p.get("responsibilities")
        if not isinstance(bullets, list) or len(bullets) > 50:
            raise ConfigurationError("Programme responsibilities must be a list of at most 50 bullet points.")
        for bullet in bullets:
            text(bullet, "Responsibility", 1000)
        if p["active"]:
            active.add(p["id"])
        # This is an optional reference, never a new catalogue record.
        if p.get("catalogueId") is not None:
            text(p["catalogueId"], "Existing catalogue ID", 100)
    rules = config.get("rules")
    if not isinstance(rules, dict):
        raise ConfigurationError("Scoring rules are required.")
    for key in ("minimumScore", "minimumDifference", "minimumCoverage", "mixedQuestionThreshold"):
        number(rules.get(key), key, 0, 100)
    number(rules.get("minimumCriticalIndicators"), "Minimum critical indicators", 0, 100, True)
    for key in ("criticalStrength", "mixedAffinityThreshold"):
        number(rules.get(key), key, 0.01, 1)
    questions = config.get("questions")
    if not isinstance(questions, list) or len(questions) > 100:
        raise ConfigurationError("The assessment supports at most 100 questions.")
    ids, slugs, answer_ids = set(), set(), set()
    weights = {pathway: 0 for pathway in PATHWAYS}
    for q in questions:
        if not isinstance(q, dict):
            raise ConfigurationError("Each question must be an object.")
        qid = identifier(q.get("id"), "Question ID")
        slug = identifier(q.get("slug"), "Question slug")
        if qid in ids or slug in slugs:
            raise ConfigurationError("Question IDs and slugs must be unique.")
        ids.add(qid)
        slugs.add(slug)
        pathway = q.get("pathway")
        if not isinstance(pathway, str) or pathway not in PATHWAYS:
            raise ConfigurationError("Unknown question pathway.")
        for key in ("active", "required", "multiple", "critical"):
            flag(q, key)
        text(q.get("title"), "Question title", 300)
        text(q.get("description"), "Question description", 3000)
        number(q.get("weight"), "Question weight", 0.01, 1000)
        number(q.get("order"), "Question order", 0, 10000, True)
        target = q.get("criticalProgramme")
        if q["critical"] and (not isinstance(target, str) or target not in PROGRAMMES or PROGRAMMES[target] != (pathway, 6)):
            raise ConfigurationError("A critical indicator must target its pathway's Level 6 programme.")
        answers = q.get("answers")
        if not isinstance(answers, list) or not 1 <= len(answers) <= 30:
            raise ConfigurationError("Each question needs between 1 and 30 answer choices.")
        enabled_answers = 0
        for a in answers:
            if not isinstance(a, dict):
                raise ConfigurationError("Each answer must be an object.")
            aid = identifier(a.get("id"), "Answer ID")
            if aid in answer_ids:
                raise ConfigurationError("Answer IDs must be globally unique.")
            answer_ids.add(aid)
            text(a.get("text"), "Answer text", 3000)
            flag(a, "active")
            number(a.get("order"), "Answer order", 0, 10000, True)
            enabled_answers += int(a["active"])
            affinities = a.get("affinities")
            if not isinstance(affinities, dict):
                raise ConfigurationError("Answer affinities must be a programme-to-strength mapping.")
            for pid, strength in affinities.items():
                if pid not in PROGRAMMES or PROGRAMMES[pid][0] != pathway:
                    raise ConfigurationError("Answer affinities must reference a programme in the question's pathway.")
                number(strength, "Answer strength", 0, 1)
        if q["active"]:
            if not enabled_answers:
                raise ConfigurationError("An active question needs an active answer.")
            weights[pathway] += q["weight"]
    if config["enabled"]:
        for pathway in PATHWAYS:
            if not weights[pathway] or any(pid not in active for pid, info in PROGRAMMES.items() if info[0] == pathway):
                raise ConfigurationError("Each enabled pathway needs active questions and both active programmes.")
    return config


def integrate_layout(raw, previous, layout):
    """One publication transaction and version, owned by the existing wizard."""
    value = raw.get(KEY) if raw.get(KEY) is not None else (previous or {}).get(KEY)
    if value is not None:
        layout[KEY] = validate(value)


def learner_configuration(config):
    """Present the assessment without numerical question/answer weights."""
    out = {key: copy.deepcopy(config[key]) for key in ("enabled", "introduction", "programmes")}
    out["questions"] = []
    for q in sorted(config["questions"], key=lambda item: item["order"]):
        if not q["active"]:
            continue
        question = {key: q[key] for key in ("id", "slug", "title", "description", "pathway", "required", "multiple", "order")}
        question["answers"] = [{key: a[key] for key in ("id", "text", "order")}
                               for a in sorted(q["answers"], key=lambda item: item["order"]) if a["active"]]
        out["questions"].append(question)
    return out
