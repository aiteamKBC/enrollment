"""Stored resources for an already-authorized APTEM review meeting.

The portal's existing resolver supplies the calendar record and source marker.
Resource lists are stored reads. Protected playback can stream one verified
recording from Graph on demand; refresh and AI remain Coach actions.
"""
from django.http import HttpResponseRedirect, JsonResponse


def _bound_resources(record, snapshot):
    """Apply a saved call claim only after the party route resolved this Review."""
    from coach_api.bound_actual_session import bound_session
    from coach_api.migrated_session_scope import has_verified_meeting_evidence, scope_snapshot
    from coach_api.models import ImportedReviewInstance

    event = getattr(record, '_imported_review_event', None) or {}
    if has_verified_meeting_evidence(snapshot):
        return {**scope_snapshot(record, snapshot, meeting_scoped=True), '_boundSession': True}
    try:
        source_id = int(event.get('reviewId') or 0)
    except (TypeError, ValueError):
        source_id = 0
    if source_id <= 0:
        return snapshot
    overlays = list(ImportedReviewInstance.objects.filter(source_review_id=source_id)[:2])
    if len(overlays) > 1:
        raise ValueError('Review continuation is ambiguous.')
    bound = bound_session(record, overlay=overlays[0]) if overlays else None
    return {**scope_snapshot(record, snapshot, session=bound.context), '_boundSession': True} if bound else snapshot


def is_imported_meeting(record):
    # Set by _learner_calendar_record after source/association resolution,
    # including legacy booking keys and explicitly reconciled Native calendars.
    return isinstance(getattr(record, "_imported_review_event", None), dict)


def stored_resources(record, *, include_attendance=False):
    from coach_api.recording_archive import archived_recording_url
    from coach_api.imported_recording_playback import graph_playback_candidate
    from coach_api.views import public_coach_meeting_artifact, stored_coach_meeting_snapshot

    try:
        snapshot = _bound_resources(record, stored_coach_meeting_snapshot(record))
    except ValueError:
        return JsonResponse({'detail': 'Meeting resources are unavailable for this Review.',
                             'code': 'MEETING_RESOURCES_UNAVAILABLE'}, status=409)
    recordings = []
    for artifact in snapshot["artifacts"]:
        if str(artifact.get("artifact_type", "")).strip().lower() != "recording":
            continue
        recording = public_coach_meeting_artifact(artifact)
        # Metadata alone is not a saved video. Check the existing archive
        # reference without a remote request; never put its signed URL in JSON.
        recording["content_available"] = bool(archived_recording_url(
            record.event_key, artifact.get("graph_artifact_id") or artifact.get("id"),
        ))
        if not recording['content_available'] and graph_playback_candidate(record, snapshot, artifact):
            recording['graph_playback_candidate'] = True
        recordings.append(recording)
    payload = {
        "artifacts": recordings,
        "meetingSummary": None,  # Formal answers retain their own visibility rules.
        "errors": snapshot["errors"],
        "partial": snapshot["partial"],
        "storage": snapshot.get("storage") or {"stored": False},
        "resourceMode": "stored",
    }
    if include_attendance:
        from coach_api.review_attendance_links import attendance_for_display
        payload["attendance"] = attendance_for_display(record, snapshot["attendance"])
    response = JsonResponse(payload, status=207 if snapshot["partial"] else 200)
    response["Cache-Control"] = "private, no-store"
    return response


def stored_recording(request, record, artifact_id):
    from coach_api.recording_archive import archived_recording_url
    from coach_api.imported_recording_playback import graph_playback_candidate, stream_imported_recording
    from coach_api.views import stored_coach_meeting_snapshot

    try:
        snapshot = _bound_resources(record, stored_coach_meeting_snapshot(record))
    except ValueError:
        snapshot = {'artifacts': [], '_boundSession': True}
    artifact = next((row for row in snapshot['artifacts']
                     if row.get('artifact_type') == 'recording'
                     and row.get('graph_artifact_id') == artifact_id), None)
    if snapshot.get('_boundSession') and not artifact:
        return JsonResponse({'detail': 'A saved copy of this recording is not available yet.',
                             'code': 'STORED_RECORDING_UNAVAILABLE'}, status=404)

    # Keep historical archived playback available even when an older snapshot
    # lacks verified provider markers. Direct Graph playback requires them.
    url = archived_recording_url(record.event_key, artifact_id)
    if url:
        response = HttpResponseRedirect(url)
    elif artifact and graph_playback_candidate(record, snapshot, artifact):
        response = stream_imported_recording(request, record, snapshot, artifact)
    else:
        response = JsonResponse({"detail": "A saved copy of this recording is not available yet.",
                                 "code": "STORED_RECORDING_UNAVAILABLE"}, status=404)
    response["Cache-Control"] = "private, no-store"
    return response
