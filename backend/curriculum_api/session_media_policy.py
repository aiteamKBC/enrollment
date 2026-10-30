"""Pure association/visibility rules for archived session media."""
import json

from .session_results_policy import instant


def artifact_metadata(row):
    value = row.get('metadata')
    if isinstance(value, dict):
        return value
    try:
        value = json.loads(value or '{}')
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def recordings_for_transcript(transcript, recordings):
    """Prefer Graph's explicit correlation; never pair by list order or title."""
    correlation = transcript.get('content_correlation_id')
    if correlation:
        matches = [row for row in recordings if row.get('content_correlation_id') == correlation]
        if matches:
            return matches
        recordings = [row for row in recordings if not row.get('content_correlation_id')]
    call = transcript.get('call_id')
    if not call:
        return []
    matches = [row for row in recordings if row.get('call_id') == call]
    start, end = instant(transcript.get('created_datetime')), instant(transcript.get('end_datetime'))
    if start and end:
        return [row for row in matches
                if instant(row.get('created_datetime')) and instant(row.get('end_datetime'))
                and instant(row['created_datetime']) < end and instant(row['end_datetime']) > start]
    return matches if len(matches) == 1 else []


def hidden_artifact_ids(artifacts):
    recordings = [row for row in artifacts if row['artifact_type'] == 'recording']
    hidden = {row['id'] for row in recordings if artifact_metadata(row).get('lmsHiddenFromLearners') is True}
    if not hidden:
        return hidden
    for row in artifacts:
        if row['artifact_type'] != 'transcript':
            continue
        matches = recordings_for_transcript(row, recordings)
        # An unassociated transcript might disclose the hidden recording's speech.
        if not matches or any(recording['id'] in hidden for recording in matches):
            hidden.add(row['id'])
    return hidden


def transcript_timing_ready(artifact):
    timeline = artifact_metadata(artifact).get('lmsTranscriptTimeline')
    return isinstance(timeline, dict) and timeline.get('version') == 1 and isinstance(timeline.get('cues'), list)


def recording_transcript_links(recording, artifacts):
    recordings = [row for row in artifacts if row['artifact_type'] == 'recording']
    links = []
    recording_start = instant(recording.get('created_datetime'))
    for transcript in artifacts:
        if transcript['artifact_type'] != 'transcript':
            continue
        if not any(row['id'] == recording['id'] for row in recordings_for_transcript(transcript, recordings)):
            continue
        transcript_start = instant(transcript.get('created_datetime'))
        links.append({'id': transcript['id'], 'timingReady': transcript_timing_ready(transcript),
                      'offsetSeconds': (transcript_start - recording_start).total_seconds()
                      if transcript_start and recording_start else None})
    return links
