"""Migrated summary suggestions; no Graph transport or Native lifecycle changes."""
from copy import deepcopy
import hashlib
import re

from django.utils import timezone

from .migrated_summary_binding import binding_state, populate_answer, preserve_original
from .migrated_reviews import TEXT_ANSWER_LIMIT


TIMING = re.compile(r"(?P<start>(?:\d{2,}:)?\d{2}:\d{2}\.\d{3})[ \t]+-->[ \t]+(?P<end>(?:\d{2,}:)?\d{2}:\d{2}\.\d{3})(?:[ \t]+[^\n]*)?")


def _timestamp(value):
    parts = value.split(":")
    minutes, seconds = int(parts[-2]), float(parts[-1])
    if minutes >= 60 or seconds >= 60:
        raise ValueError("The WebVTT transcript contains an invalid timestamp.")
    return (int(parts[0]) * 3600 if len(parts) == 3 else 0) + minutes * 60 + seconds


def parse_upload(upload):
    from .views import COACH_MEETING_TRANSCRIPT_UPLOAD_MAX_BYTES, coach_meeting_transcript_text

    if upload is None:
        raise ValueError("Select a .vtt or .txt transcript.")
    filename = str(upload.name).replace("\\", "/").rsplit("/", 1)[-1]
    extension = filename.rsplit(".", 1)[-1].lower()
    if extension not in {"vtt", "txt"}:
        raise ValueError("Upload a transcript with a .vtt or .txt file extension.")
    if (upload.content_type or "").split(";", 1)[0].lower() not in {"", "text/vtt", "text/plain", "application/octet-stream"}:
        raise ValueError("Upload a plain-text .vtt or .txt transcript.")
    limit = COACH_MEETING_TRANSCRIPT_UPLOAD_MAX_BYTES
    if upload.size > limit:
        raise ValueError("The transcript must be no larger than 5 MB.")
    content = upload.read(limit + 1)
    if not content or len(content) > limit:
        raise ValueError("The transcript is empty or larger than the 5 MB limit.")
    try:
        decoded = content.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        raise ValueError("The transcript must use UTF-8 text encoding.") from None
    if any((ord(c) < 32 and c not in "\t\n") or 127 <= ord(c) < 160 for c in decoded):
        raise ValueError("The transcript contains binary or unsupported control characters.")
    text = decoded.strip()
    if extension == "vtt":
        blocks = re.split(r"\n[ \t]*\n", text)
        if not re.fullmatch(r"WEBVTT(?:[ \t].*)?", blocks[0].split("\n")[0]) or "-->" in blocks[0]:
            raise ValueError("The uploaded file is not a valid WebVTT transcript.")
        cues = []
        for block in blocks[1:]:
            lines = block.split("\n")
            if re.match(r"^(?:NOTE(?:[ \t]|$)|STYLE$|REGION$)", lines[0]):
                continue
            index = 0 if "-->" in lines[0] else 1
            timing = TIMING.fullmatch(lines[index]) if len(lines) > index else None
            if not timing or len(lines) <= index + 1 or _timestamp(timing['end']) <= _timestamp(timing['start']):
                raise ValueError("The WebVTT transcript contains a malformed or empty cue.")
            if any("-->" in line for line in lines[index + 1:]):
                raise ValueError("Separate WebVTT cues with a blank line.")
            cues.append(block)
        # Reuse the same spoken-text/speaker extraction used by Native and Teams.
        text = coach_meeting_transcript_text("WEBVTT\n\n" + "\n\n".join(cues))
    if not text.strip():
        raise ValueError("The uploaded file does not contain any transcript text.")
    return text, {"source": "uploaded_transcript", "filename": filename,
                  "contentHash": hashlib.sha256(content).hexdigest(),
                  "transcriptHash": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def apply_suggestion(overlay, state, summary, provenance):
    """Caller holds the overlay lock. Both input sources use the same write rule."""
    from .views import meeting_summary_plain_text

    preserve_original(state)
    text = meeting_summary_plain_text(summary)
    if not isinstance(state.get("aiSummaryOriginal"), dict):
        state["aiSummaryOriginal"] = deepcopy(summary)
        state["aiSummaryProvenance"] = deepcopy(provenance)
    state["latestSummarySuggestion"] = {"summary": deepcopy(summary), "provenance": deepcopy(provenance)}
    result = populate_answer(overlay, state, text)
    state["latestSummarySuggestion"]["bindingResult"] = result.get("status")
    state["summaryGeneration"] = {"status": "ready", "source": provenance["source"],
                                  "attemptedAt": timezone.now().isoformat(), "attemptedBy": overlay.owner_email,
                                  "bindingResult": result.get("status"), "transcriptHash": provenance.get("transcriptHash")}
    return result


def summary_binding_response(overlay):
    """Coach-only presentation; party detail already removes summaryBinding."""
    from .views import meeting_summary_plain_text

    result = binding_state(overlay)
    if not result.get("fieldKey"):
        return result
    state = overlay.meeting_intelligence or {}
    latest = state.get("latestSummarySuggestion") or {}
    provenance = latest.get("provenance") or {}
    if isinstance(latest.get("summary"), dict):
        text = meeting_summary_plain_text(latest["summary"])
        result.update(suggestionText=text, suggestionSource=provenance.get("source"),
                      provisional=bool(provenance.get('source') == 'teams' and provenance.get('provisional')),
                      summaryTooLong=len(text) > TEXT_ANSWER_LIMIT,
                      generatedAt=provenance.get("generatedAt"), transcriptTruncated=bool(provenance.get("transcriptTruncated")),
                      replacementAvailable=bool(text and text != result.get("answer") and result["state"] != "NEVER_POPULATED"))
    elif result.get("status") == "summary-too-long":
        result["suggestionText"] = meeting_summary_plain_text(state.get("aiSummaryOriginal") or {})
    generation = state.get("summaryGeneration") or {}
    latest_status = generation_status(state)
    result["generationStatus"] = latest_status if latest_status in {"ready", "failed", "unavailable"} else None
    result["generationAttemptedAt"] = generation.get("attemptedAt")
    if result["generationStatus"] == "failed" and generation.get("error"):
        result["generationError"] = generation["error"]
    if result.get('suggestionText') and provenance.get('source') != 'uploaded_transcript':
        # All callers (including initial form load) must validate a Teams cache;
        # an invalid suggestion must not remain available as a replacement.
        from django.db import DatabaseError
        from .review_sources import booking_for_overlay
        from .migrated_session_scope import (has_verified_meeting_evidence,
                                             stored_transcript_selection, summary_matches_session)
        from .views import stored_coach_meeting_snapshot, stored_coach_meeting_transcript_for_summary
        try:
            record = booking_for_overlay(overlay)
            transcript = None
            if record:
                saved = stored_coach_meeting_snapshot(record)
                if has_verified_meeting_evidence(saved) and not state.get('actualSession'):
                    selection = stored_transcript_selection(record, saved, meeting_scoped=True)
                    if selection['code'] is None:
                        transcript = stored_coach_meeting_transcript_for_summary(
                            record, artifact_ids=selection['artifactIds'])
                else:
                    transcript = stored_coach_meeting_transcript_for_summary(record)
            verified = bool(record and summary_matches_session(
                {**provenance, 'sessionProvenance': state.get('sessionProvenance')}, record,
                transcript))
        except DatabaseError:
            verified = False
        if not verified:
            result.pop('suggestionText', None)
            result['replacementAvailable'] = False
            result['generationStatus'] = 'unavailable'
            result['sessionCode'] = 'SUMMARY_SESSION_MISMATCH'
            result['message'] = 'The stored suggestion cannot be verified for this session. Your saved answer is unchanged.'
    return result


def generation_status(state):
    """Latest attempt drives presentation; keep older summary/provenance intact."""
    status = (state.get("summaryGeneration") or {}).get("status")
    if status in {"ready", "failed", "unavailable"}:
        return status
    if isinstance((state.get("latestSummarySuggestion") or {}).get("summary"), dict):
        return "ready"
    return state.get("summaryStatus", "not-generated")
