"""Resolve an imported Review booking to one verified Teams onlineMeeting."""
import json
from dataclasses import dataclass
from urllib import parse as urlparse

from django.db.models import Q

from .migrated_session_scope import booking_fingerprint


@dataclass(frozen=True)
class VerifiedMeetingTarget:
    """Identity returned by Graph for this exact saved calendar event.

    Imported Check Session requests this stronger contract. A Teams URL
    tenant hint is checked for contradictions, never used as the authority.
    """
    base_path: str
    tenant_id: str
    organizer_id: str
    online_meeting_id: str
    calendar_id: int
    graph_event_id: str
    booking_fingerprint: str
    join_url: str = ''


def strict_imported_meeting_target(record, *, identity_out=None):
    """Resolve one onlineMeeting through the owned booking's Graph calendar event.

    The shared resolver intentionally retains its older first-result contract.
    Imported Check Session calls this stricter resolver.
    """
    from coach_api.views import clean_text, microsoft_graph_request
    from curriculum_api.views import teams_meeting_base_path, teams_online_meeting_owner_id

    organizer = clean_text(record.graph_organizer_email) or clean_text(record.owner_email)
    event_id = clean_text(record.graph_event_id)
    if not organizer or not event_id:
        return '', _target_error('MEETING_TARGET_UNVERIFIED')
    event = microsoft_graph_request('GET',
        f"users/{urlparse.quote(organizer, safe='')}/events/{urlparse.quote(event_id, safe='')}")
    if not isinstance(event, dict) or clean_text(event.get('id')) != event_id:
        return '', _target_error('MEETING_TARGET_UNVERIFIED')
    event_organizer = event.get('organizer') or {}
    event_meeting = event.get('onlineMeeting') or {}
    if not isinstance(event_organizer, dict) or not isinstance(event_meeting, dict):
        return '', _target_error('MEETING_TARGET_UNVERIFIED')
    organizer_email = event_organizer.get('emailAddress') or {}
    if not isinstance(organizer_email, dict):
        return '', _target_error('MEETING_TARGET_UNVERIFIED')
    remote_organizer = organizer_email.get('address')
    if remote_organizer and clean_text(remote_organizer).casefold() != organizer.casefold():
        return '', _target_error('MEETING_TARGET_UNVERIFIED')
    join_url = clean_text(event_meeting.get('joinUrl'))
    saved_url = clean_text(record.meeting_link)
    if not join_url or (saved_url and 'teams.microsoft' in saved_url.casefold() and saved_url != join_url):
        return '', _target_error('MEETING_TARGET_UNVERIFIED')
    owner_id = teams_online_meeting_owner_id(organizer, join_url)
    escaped_url = join_url.replace("'", "''")
    query = urlparse.urlencode({'$filter': f"joinWebUrl eq '{escaped_url}'"})
    response = microsoft_graph_request('GET',
        f"users/{urlparse.quote(owner_id, safe='')}/onlineMeetings?{query}")
    if (not isinstance(response, dict) or not isinstance(response.get('value'), list)
            or response.get('@odata.nextLink') or len(response['value']) != 1):
        return '', _target_error('MEETING_TARGET_AMBIGUOUS')
    meeting = response['value'][0]
    if (not isinstance(meeting, dict) or not clean_text(meeting.get('id'))
            or clean_text(meeting.get('joinWebUrl')) != join_url):
        return '', _target_error('MEETING_TARGET_UNVERIFIED')
    if clean_text(event_meeting.get('id')) and clean_text(event_meeting['id']) != clean_text(meeting['id']):
        return '', _target_error('MEETING_TARGET_UNVERIFIED')
    participants = meeting.get('participants') or {}
    if not isinstance(participants, dict):
        return '', _target_error('MEETING_TARGET_UNVERIFIED')
    provider_organizer = participants.get('organizer') or {}
    identity = (provider_organizer.get('identity') or {}) if isinstance(provider_organizer, dict) else None
    provider_owner = (identity.get('user') or {}) if isinstance(identity, dict) else None
    if not isinstance(provider_owner, dict):
        return '', _target_error('MEETING_TARGET_UNVERIFIED')
    provider_owner_id = clean_text(provider_owner.get('id'))
    if provider_owner_id and _is_uuid(owner_id) and provider_owner_id.casefold() != owner_id.casefold():
        return '', _target_error('MEETING_TARGET_UNVERIFIED')
    link_tenant = _link_tenant(join_url)
    provider_tenant = clean_text(provider_owner.get('tenantId'))
    if link_tenant and provider_tenant and provider_tenant.casefold() != link_tenant.casefold():
        return '', _target_error('MEETING_TARGET_UNVERIFIED')
    base = teams_meeting_base_path(organizer, clean_text(meeting['id']), join_url)
    if identity_out is not None:
        # Graph's organizer identity must agree with the independently read
        # user object. Neither the URL nor a client value supplies these IDs.
        from uuid import UUID

        def canonical(value):
            try:
                parsed = UUID(str(value))
                return str(parsed) if parsed.int and str(parsed) == value else None
            except (TypeError, ValueError, AttributeError):
                return None

        tenant_id = canonical(provider_tenant)
        organizer_id = canonical(provider_owner_id)
        if (not tenant_id or not organizer_id or not record.graph_organizer_email
                or not remote_organizer or not isinstance(record.pk, int) or record.pk <= 0):
            return '', _target_error('MEETING_TARGET_UNVERIFIED')
        user = microsoft_graph_request('GET',
            f"users/{urlparse.quote(organizer, safe='')}?$select=id")
        if not isinstance(user, dict) or clean_text(user.get('id')) != organizer_id:
            return '', _target_error('MEETING_TARGET_UNVERIFIED')
        identity_out['target'] = VerifiedMeetingTarget(
            base_path=base, tenant_id=tenant_id, organizer_id=organizer_id,
            online_meeting_id=clean_text(meeting['id']), calendar_id=record.pk,
            graph_event_id=event_id, booking_fingerprint=booking_fingerprint(record),
            join_url=join_url)
    return base, None


def review_meeting_target(record, *, identity_out=None):
    """Resolve the imported Review's exact Teams meeting and known local reuse.

    This transient check does not reserve a call or create an ownership record.
    Source/Coach/booking authorization belongs to the caller. Checking exact
    saved identifiers catches known historical shared links without a broad
    organizer-calendar search.
    """
    from .models import CoachCalendarEvent

    identity = {}
    base, error = strict_imported_meeting_target(record, identity_out=identity)
    target = identity.get('target')
    if error or not target or not record.meeting_link or record.meeting_link != target.join_url:
        return '', _target_error('MEETING_TARGET_UNVERIFIED')
    duplicates = (CoachCalendarEvent.objects.filter(
        Q(meeting_link=target.join_url) | Q(graph_event_id=record.graph_event_id))
        .exclude(pk=record.pk))
    if duplicates.exists():
        return '', _target_error('MEETING_TARGET_AMBIGUOUS')
    if identity_out is not None:
        identity_out['target'] = target
    return base, None


def _is_uuid(value):
    from uuid import UUID
    try:
        UUID(value)
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _link_tenant(join_url):
    try:
        decoded = urlparse.unquote(join_url)
        values = urlparse.parse_qs(urlparse.urlsplit(decoded).query).get('context') or []
        context = json.loads(urlparse.unquote(values[0])) if values else {}
        return str(context.get('Tid') or context.get('tid') or '') if isinstance(context, dict) else ''
    except (ValueError, TypeError, IndexError):
        return ''


def _target_error(code):
    return {'code': code, 'detail': 'The linked Teams meeting identity is not uniquely verified. Retry or ask support to inspect the booking.'}


