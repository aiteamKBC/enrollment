"""Which Microsoft account each Teams attendance row came from, beside the LMS
email that row counts for.

This is a read-only comparison layered on saved attendance. It never decides
presence, hours or status, and a matching display name is never treated as
proof of who joined: only an account Microsoft signed in can be "Matched".

Graph reports a signed-in user as ``identity {id, displayName, tenantId}``
(often with ``emailAddress``) and an anonymous joiner with no tenant. Older
rows carry the legacy nested ``identity.user`` / ``identity.guest`` shape. The
sync may add ``lmsAccountResolution`` to ``raw_data`` after looking the Entra
object id up in the college directory; GET requests only read it.
"""
import json
from datetime import datetime, timezone
from urllib import parse as urllib_parse

from .session_results_policy import evidence_seconds

RESOLUTION_KEY = 'lmsAccountResolution'
STATUSES = ('matched', 'different-account', 'unverified-guest', 'unknown', 'unmatched')
# Worst first: one join on the wrong account flags the person.
_SEVERITY = {'different-account': 0, 'unverified-guest': 1, 'unknown': 2, 'matched': 3}
_STAFF_ROLES = {'organizer', 'presenter', 'coorganizer', 'co-organizer'}
_UNVERIFIED_KINDS = {'guest', 'phone', 'encrypted', 'acsUser', 'azureCommunicationServicesUser'}


def _text(value):
    return str(value or '').strip()


def _email(value):
    return _text(value).casefold()


def raw_of(record):
    raw = record.get('raw_data') if isinstance(record, dict) else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = {}
    return raw if isinstance(raw, dict) else {}


def teams_identity(record):
    """Return (kind, object id, tenant id, display name) for one raw row.

    kind is 'user' for a Microsoft account, one of _UNVERIFIED_KINDS for a
    joiner Microsoft did not sign in, '' when Teams reported no identity.
    """

    identity = raw_of(record).get('identity')
    if not isinstance(identity, dict) or not identity:
        return '', '', '', ''
    for key in ('user', 'guest', 'phone', 'encrypted', 'acsUser', 'applicationInstance', 'application'):
        nested = identity.get(key)
        if isinstance(nested, dict) and (nested.get('id') or nested.get('displayName')):
            kind = 'user' if key in ('user', 'applicationInstance', 'application') else key
            return (kind, _text(nested.get('id')), _text(nested.get('tenantId')),
                    _text(nested.get('displayName') or nested.get('name')))
    odata = _text(identity.get('@odata.type')).casefold()
    tenant = _text(identity.get('tenantId'))
    if 'guest' in odata:
        kind = 'guest'
    elif 'azurecommunicationservices' in odata:
        kind = 'acsUser'
    elif 'phone' in odata:
        kind = 'phone'
    elif tenant:
        kind = 'user'
    else:
        # Teams leaves the tenant empty for someone who joined anonymously.
        kind = 'guest'
    return kind, _text(identity.get('id')), tenant, _text(identity.get('displayName') or identity.get('name'))


def needs_directory_lookup(record, home_tenant):
    """A signed-in account from the college tenant not yet looked up (or whose lookup failed)."""

    kind, object_id, tenant, _name = teams_identity(record)
    if kind != 'user' or not object_id:
        return False
    if home_tenant and tenant and tenant.casefold() != home_tenant.casefold():
        return False
    saved = raw_of(record).get(RESOLUTION_KEY)
    if isinstance(saved, dict) and saved.get('objectId') == object_id and saved.get('status') in ('resolved', 'not-found'):
        return False
    return True


def keep_directory_lookup(previous_raw, raw):
    """Carry a saved lookup into a re-synced row, only while it is still the same account."""

    saved = previous_raw.get(RESOLUTION_KEY) if isinstance(previous_raw, dict) else None
    if isinstance(saved, dict) and saved.get('objectId') and saved.get('objectId') == teams_identity({'raw_data': raw})[1]:
        return {**raw, RESOLUTION_KEY: saved}
    return raw


def lookup_directory_account(graph_request, object_id):
    """Read one account from the college directory (User.Read.All). Never writes to Microsoft."""

    checked = datetime.now(timezone.utc).isoformat()
    try:
        user = graph_request('GET', f"users/{urllib_parse.quote(object_id, safe='')}"
                                    '?$select=id,mail,userPrincipalName,userType')
    except RuntimeError as exc:
        message = str(exc)
        status = 'not-found' if 'HTTP 404' in message else 'denied' if ('HTTP 403' in message or 'HTTP 401' in message) else 'failed'
        return {'objectId': object_id, 'status': status, 'checkedAt': checked}
    return {'objectId': object_id, 'status': 'resolved', 'checkedAt': checked,
            'mail': _email(user.get('mail')), 'userPrincipalName': _email(user.get('userPrincipalName')),
            'userType': _text(user.get('userType'))}


def resolve_directory_accounts(rows, *, graph_request, save_raw, home_tenant=''):
    """Look up the email behind signed-in college accounts in saved attendance rows.

    Only ``raw_data`` gains the lookup result; attendance seconds, status and
    hours are untouched. Stops at the first permission refusal so a missing
    grant costs one call, not one per participant. Returns counts.
    """

    counts = {'resolved': 0, 'notFound': 0, 'failed': 0, 'denied': False}
    cache = {}
    for row in rows:
        if not needs_directory_lookup(row, home_tenant):
            continue
        _kind, object_id, _tenant, _name = teams_identity(row)
        if object_id not in cache:
            cache[object_id] = lookup_directory_account(graph_request, object_id)
        result = cache[object_id]
        if result['status'] == 'denied':
            counts['denied'] = True
            break
        if result['status'] == 'failed':
            counts['failed'] += 1
            continue
        save_raw(row, {**raw_of(row), RESOLUTION_KEY: result})
        counts['resolved' if result['status'] == 'resolved' else 'notFound'] += 1
    return counts


def account_of(record, home_tenant=''):
    """Describe the account behind one raw row; emails only where Microsoft vouches for them."""

    raw = raw_of(record)
    kind, object_id, tenant, display_name = teams_identity(record)
    reported = _email(record.get('email') or raw.get('emailAddress'))
    saved = raw.get(RESOLUTION_KEY) if isinstance(raw.get(RESOLUTION_KEY), dict) else {}
    lookup = saved.get('status', '') if saved.get('objectId') == object_id else ''
    account = {
        'sourceRecordId': _text(record.get('id') or record.get('graph_record_id')),
        'displayName': display_name or _text(record.get('display_name')),
        'teamsRole': _text(record.get('role') or raw.get('role')),
        'verification': 'unknown', 'tenant': '', 'accountType': 'unknown',
        'email': '', 'emailSource': '', 'otherEmails': [], 'enteredEmail': '', 'lookup': lookup,
    }
    if kind == 'user':
        account['verification'] = 'verified'
        account['tenant'] = ('external' if home_tenant and tenant and tenant.casefold() != home_tenant.casefold()
                             else 'home' if home_tenant and tenant else '')
        emails = [reported] if reported else []
        if lookup == 'resolved':
            emails += [saved.get('mail'), saved.get('userPrincipalName')]
        emails = list(dict.fromkeys(email for email in emails if email))
        account['accountType'] = ('external organisation' if account['tenant'] == 'external'
                                  else 'guest in college directory' if lookup == 'resolved' and _text(saved.get('userType')).casefold() == 'guest'
                                  else 'Microsoft work or school account')
        if emails:
            account.update(email=emails[0], otherEmails=emails[1:],
                           emailSource='teams' if reported else 'directory')
    elif kind:
        account['verification'] = 'unverified'
        account['accountType'] = {'phone': 'phone dial-in'}.get(kind, 'anonymous guest')
        account['enteredEmail'] = reported
    else:
        account['enteredEmail'] = reported
    return account


def account_status(account, expected_email):
    if account['verification'] == 'unverified':
        return 'unverified-guest'
    emails = {account['email'], *account['otherEmails']} - {''}
    if account['verification'] != 'verified' or not emails:
        return 'unknown'
    return 'matched' if expected_email and expected_email in emails else 'different-account'


def account_reason(account, status, linked_by):
    if status == 'matched':
        return 'Signed in to Microsoft with the LMS email.'
    if status == 'different-account':
        text = f"Signed in to Microsoft as {account['email']}, not the LMS email."
        if linked_by == 'alias':
            text += ' Staff linked this account to the learner.'
        elif linked_by == 'name':
            text += ' It counts for this learner because the Teams name was matched automatically within the session learner list.'
        return text
    if status == 'unverified-guest':
        text = ('Joined by phone' if account['accountType'] == 'phone dial-in'
                else 'Joined without signing in to Microsoft')
        text += ', so Microsoft cannot confirm who this was.'
        if account['enteredEmail']:
            text += f" The email {account['enteredEmail']} was typed in, not verified."
        if linked_by == 'name':
            text += ' It counts for this learner because the Teams name was matched automatically within the session learner list; that is not proof of identity.'
        return text
    if account['verification'] == 'verified':
        if account['tenant'] == 'external':
            return 'Signed in with an account from another organisation; Microsoft did not share its email.'
        if account['lookup'] == 'not-found':
            return 'Signed in to Microsoft, but the college directory has no account with this ID.'
        if account['lookup'] in ('failed', 'denied'):
            return 'Signed in to Microsoft; looking up its email failed. Sync attendance to try again.'
        return 'Signed in to Microsoft; its email has not been looked up yet. Sync attendance to look it up.'
    return 'Teams did not report which account was used for this join.'


def identity_check(records, *, learners, aliases, reviewed_links, name_match, staff, organizer_email, home_tenant=''):
    """Group raw rows by the LMS person they count for, and compare accounts.

    learners: {learner email: {'name', 'learnerProfileId'}} from the saved register.
    aliases: {alias email: learner email}; reviewed_links: {row id: learner email};
    name_match(row) -> learner email or None, the register's automatic name decision.
    staff: {email: [LMS role labels]} from the series and module.
    """

    people = {}
    for record in records:
        account = account_of(record, home_tenant)
        row_email = _email(record.get('email'))
        row_id = _text(record.get('id'))
        teams_role = account['teamsRole'].casefold()
        person, linked_by = None, ''
        if row_email in learners:
            person, linked_by = ('learner', row_email), 'email'
        elif aliases.get(row_email) in learners:
            person, linked_by = ('learner', aliases[row_email]), 'alias'
        elif reviewed_links.get(row_id) in learners:
            person, linked_by = ('learner', reviewed_links[row_id]), 'reviewed-link'
        elif name_match(record) in learners:
            person, linked_by = ('learner', name_match(record)), 'name'
        else:
            verified = [account['email'], *account['otherEmails']] if account['verification'] == 'verified' else []
            staff_email = next((email for email in [row_email, *verified] if email in staff), '')
            if staff_email:
                person, linked_by = ('staff', staff_email), 'email'
            elif teams_role == 'organizer' and organizer_email:
                person, linked_by = ('staff', organizer_email), 'organiser-role'
        if person is None:
            _kind, object_id, _tenant, _name = teams_identity(record)
            key = ('unlinked', (account['email'] if account['verification'] == 'verified' else '')
                   or (object_id and 'id:' + object_id) or 'row:' + row_id)
        else:
            key = person
        entry = people.setdefault(key, {'kind': key[0], 'expectedEmail': '' if key[0] == 'unlinked' else key[1],
                                        'linkedBy': set(), 'rows': []})
        if linked_by:
            entry['linkedBy'].add(linked_by)
        entry['rows'].append((record, account, linked_by))

    participants = []
    for (kind, identifier), entry in people.items():
        expected = entry['expectedEmail']
        accounts = {}
        for record, account, linked_by in entry['rows']:
            # Several rows for one account are repeat joins, not different people.
            _kind, object_id, _tenant, _name = teams_identity(record)
            account_key = object_id or account['email'] or account['sourceRecordId']
            item = accounts.setdefault(account_key, {**account, 'records': [], 'linkedBy': linked_by,
                                                     'sourceRecordIds': []})
            item['records'].append(record)
            item['sourceRecordIds'].append(account['sourceRecordId'])
        account_rows = []
        for item in accounts.values():
            records_for_account = item.pop('records')
            status = 'unmatched' if kind == 'unlinked' else account_status(item, expected)
            item.update(status=status, joins=len(records_for_account), seconds=evidence_seconds(records_for_account),
                        reason=('Not linked to any learner or staff member on this session.'
                                if status == 'unmatched' else account_reason(item, status, item['linkedBy'])))
            del item['sourceRecordId']
            account_rows.append(item)
        account_rows.sort(key=lambda item: (_SEVERITY.get(item['status'], 9), item['displayName'].casefold()))
        status = account_rows[0]['status']
        if kind == 'learner':
            learner = learners[identifier]
            name, roles, profile_id = learner.get('name') or identifier, ['Learner'], learner.get('learnerProfileId')
        else:
            name = next((item['displayName'] for item in account_rows if item['displayName']), '') or 'Unnamed participant'
            roles, profile_id = (staff.get(identifier) or ['Organiser']) if kind == 'staff' else [], None
        participants.append({
            'kind': kind, 'name': name, 'roles': roles, 'learnerProfileId': profile_id,
            'expectedEmail': expected, 'linkedBy': sorted(entry['linkedBy']),
            'teamsRoles': sorted({item['teamsRole'] for item in account_rows if item['teamsRole']}),
            'status': status, 'reason': account_rows[0]['reason'], 'accounts': account_rows,
        })
    order = {'learner': 0, 'staff': 1, 'unlinked': 2}
    participants.sort(key=lambda item: (order[item['kind']], item['name'].casefold()))
    counts = {status: sum(1 for item in participants if item['status'] == status) for status in STATUSES}
    lookup_pending = sum(1 for item in participants for account in item['accounts']
                         if account['verification'] == 'verified' and not account['email']
                         and account['tenant'] != 'external' and account['lookup'] in ('', 'failed', 'denied'))
    return {'participants': participants, 'counts': counts, 'lookupPending': lookup_pending}


def staff_roles(series, tutor_emails=()):
    """LMS staff for a series: {email: [role labels]}. Read from LMS records, never from Teams names."""

    def emails(value):
        if isinstance(value, str):
            try:
                value = json.loads(value) if value.strip().startswith('[') else [value]
            except ValueError:
                value = [value]
        result = []
        for item in value or []:
            if isinstance(item, dict):
                item = item.get('email') or item.get('address') or (item.get('emailAddress') or {}).get('address')
            if _email(item):
                result.append(_email(item))
        return result

    roles = {}
    for label, values in (('Organiser', [series.get('organizer_email')]), ('Co-organiser', emails(series.get('co_organizers'))),
                          ('Presenter', emails(series.get('presenters'))), ('Tutor', list(tutor_emails))):
        for email in values:
            email = _email(email)
            if email and label not in roles.setdefault(email, []):
                roles[email].append(label)
    return roles

