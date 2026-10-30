"""Read-time Aptem presentation only. No database, templates or PDF dependencies.

Normalized values remain authoritative, including explicit nulls. Captured field
definitions can supply missing questions, but never arbitrary payload metadata.
"""
from copy import deepcopy
import json
import math
import re


def _object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _key(value):
    return re.sub(r"[^a-z0-9]", "", _text(value).casefold())


def _identities(field):
    ids = {str(field[k]) for k in ("sourceFieldId", "fieldId", "id")
           if isinstance(field.get(k), (str, int)) and str(field[k]).strip()}
    names = {_key(field[k]) for k in ("sourceFieldKey", "key", "name") if _key(field.get(k))}
    return ids, names


def _private(name):
    key = _key(name)
    return bool(re.search(r"(?:Id|_id)$", name) or key in {
        "id", "ownerid", "documentid", "storageid", "reviewid", "learnerid",
        "eventkey", "sourcepayload", "webmeetinglink", "meetinglink", "meetingurl", "joinurl",
        "permissions", "configuration", "headerroute", "contentroute", "route",
    })


def _scalar(value):
    # Unknown structured controls (e.g. radar) keep their normalized renderer.
    return value is None or isinstance(value, (str, bool, int)) or (
        isinstance(value, float) and math.isfinite(value)
    )


def _boolean(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().casefold() in {"true", "false", "yes", "no"}:
        return value.strip().casefold() in {"true", "yes"}
    return None


def _ordered(fields):
    if not isinstance(fields, list):
        return []
    def order(pair):
        value = pair[1].get("order")
        return (float(value) if isinstance(value, (int, float)) and math.isfinite(value) else pair[0], pair[0])
    return [field for _, field in sorted(
        ((i, f) for i, f in enumerate(fields) if isinstance(f, dict)), key=order,
    )]


def _captured_fields(payload):
    """Flatten only historically active branches, using captured source values."""
    result, seen = [], set()
    def walk(fields, path="", depth=0):
        if depth > 12:
            return
        for field in _ordered(fields):
            name, title = _text(field.get("name")), _text(field.get("title"))
            if (not name or not re.fullmatch(r"[\w-]+", name) or not title
                    or _private(name) or _private(title) or field.get("type") not in (1, 2, 4, 5, 6, 11, 13)):
                continue
            ids, _ = _identities(field)
            identity = "id:" + sorted(ids)[0] if ids else "key:" + path + name
            if identity in seen:
                continue
            seen.add(identity)
            # A missing answer is not filled from another field or current state.
            value = payload.get(name)
            if not _scalar(value):
                continue
            result.append((field, identity, value))
            condition = _boolean(value)
            if condition is not None:
                branch = "ifTrue" if condition else "ifFalse"
                walk(field.get(branch), path + name + ":" + branch + ":", depth + 1)
    walk(payload.get("fields"))
    return result


def _merge_fields(normalized, payload):
    fields = deepcopy(normalized)
    candidates = _captured_fields(payload)
    label_counts = {}
    for source, _, _ in candidates:
        label = _key(source.get("title"))
        label_counts[label] = label_counts.get(label, 0) + 1
    used, last_position = set(), -1
    for source, identity, value in candidates:
        source_ids, source_names = _identities(source)
        label = _key(source.get("title"))
        matches = []
        for index, field in enumerate(fields):
            if not isinstance(field, dict):
                continue
            ids, names = _identities(field)
            if field.get("sourceFieldKey") == identity:
                match = True
            elif ids and source_ids:
                match = bool(ids & source_ids)
            elif names and source_names:
                match = bool(names & source_names)
            elif not ids and not names:
                match = _key(field.get("label")) in {label, *source_names}
            else:
                match = False
            if match:
                matches.append(index)
        if matches:
            # Ambiguous label-only matches are retained, never duplicated/guessed.
            if len(matches) == 1:
                last_position = matches[0]
            continue
        if identity in used or (label_counts.get(label, 0) > 1 and any(
                isinstance(f, dict) and _key(f.get("label")) == label and not any(_identities(f)) for f in fields)):
            continue
        used.add(identity)
        field = {
            "label": source["title"], "value": value,
            "sourceFieldKey": identity, "historicalSupplement": True, "preserveEmpty": True,
        }
        description = _text(source.get("description"))
        if description:
            field["description"] = description
        if source.get("type") == 11:
            field["fieldType"] = "title_description"
        # Insert after the last source anchor, keeping existing normalized order.
        last_position += 1
        fields.insert(last_position, field)
    return fields


def enrich_historical_review(review, raw_data):
    """Enrich an already-authorized, completed Aptem DTO without mutating inputs."""
    if review.get("status") != "completed" and not review.get("completedDate"):
        return review
    data = _object(raw_data)
    captured = data.get("sections")
    if not isinstance(captured, list):
        captured = []
    result = deepcopy(review)
    sections = result.get("sections") or []
    for section in sections:
        section["historicalPresentation"] = True
        candidates = [s for s in captured if isinstance(s, dict) and
                      _key(s.get("section_name") or s.get("name")) == _key(section.get("name"))]
        if len(candidates) == 1 and sum(
                _key(s.get("name")) == _key(section.get("name")) for s in sections) == 1:
            payload = _object(candidates[0].get("source_payload"))
            # Preserve the form IDs of normalized fields even when inserting before them.
            original = section.get("fields") or []
            for index, field in enumerate(original):
                if isinstance(field, dict) and not field.get("historicalSupplement"):
                    field.setdefault("historicalIndex", index)
            section["fields"] = _merge_fields(original, payload)
            if not original and section.get("rawText") and section["fields"]:
                # A text-only normalized export is primary content, not a known
                # duplicate of the newly recovered structured questions.
                section["preserveRawText"] = True
    # These human-readable labels are presentation metadata, never replacements
    # for the stored normalized value or the learner's current profile record.
    programme = ""
    for section in sections:
        if _key(section.get("name")) == "learnerinformation":
            for field in section.get("fields") or []:
                if isinstance(field, dict) and _key(field.get("label")) in {"programme", "programmename"}:
                    programme = _text(field.get("value"))
                    if programme:
                        break
    odata = _object(data.get("live_odata"))
    programme = programme or _text(odata.get("ProgramName"))
    if programme:
        result["historicalProgramme"] = programme
    rag = _text(odata.get("RagLevel"))
    if rag and not rag.isnumeric():
        for section in sections:
            if _key(section.get("name")) in {"rag", "ragstatus"}:
                for field in section.get("fields") or []:
                    if (isinstance(field, dict) and _key(field.get("label")) in {"raglevel", "currentraglevel"}
                            and not isinstance(field.get("value"), bool)
                            and re.fullmatch(r"\d+(?:\.0+)?", str(field.get("value")) or "")):
                        field["displayValue"] = rag
    return result
