"""Reviewed and roster-scoped attendance matching; raw evidence is never edited."""
from collections import defaultdict
import re

from .session_results_policy import attendance_display_name, attendance_identity, attendance_name_key, session_roster


def attendance_bot(record):
    """Recognize explicit note-taking assistants, not ordinary unknown guests."""
    name = attendance_display_name(record).casefold()
    domain = str(record.get('email') or '').strip().casefold().partition('@')[2]
    return bool(re.search(r'\bnote[\s-]*taker\b|\b(?:fireflies\.ai|otter\.ai|fathom)\b.*\bbot\b', name)
                or domain in {'fireflies.ai', 'otter.ai', 'fathom.video'})


def identity_key(record):
    email = str(record.get('email') or '').strip().casefold()
    return ('email', email) if email else ('guest_name', attendance_name_key(attendance_display_name(record)))


def record_key(record):
    return 'record', str(record.get('occurrence_id') or '') + ':' + str(record.get('id') or '')


def ambiguous_guest_names(records):
    """Different reported identities with one name must be reviewed separately.

    Repeated rows for the same Teams identity are reconnects. Without an identity
    ID, different source rows cannot be proven to be the same person.
    """
    identities = defaultdict(set)
    for record in records:
        value = attendance_name_key(attendance_display_name(record))
        if value:
            _name, identity_id = attendance_identity(record)
            identities[value].add(identity_id or str(record.get('email') or '').casefold() or 'row:' + str(record.get('id') or ''))
    return {name for name, values in identities.items() if len(values) > 1}


def _name_parts(value):
    return tuple(re.findall(r'[^\W\d_]+', attendance_name_key(value)))


def _one_typo(left, right):
    """One insertion, deletion, substitution or adjacent transposition.

    Short names need exact evidence: Eva must not become Eve by approximation.
    """
    if min(len(left), len(right)) < 4 or abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        different = [index for index, pair in enumerate(zip(left, right)) if pair[0] != pair[1]]
        return len(different) == 1 or (
            len(different) == 2 and different[1] == different[0] + 1
            and left[different[0]] == right[different[1]]
            and left[different[1]] == right[different[0]])
    short, long = sorted((left, right), key=len)
    index = next((i for i, pair in enumerate(zip(short, long)) if pair[0] != pair[1]), len(short))
    return short[index:] == long[index + 1:]


def _parts_match(query, candidate, typo_budget):
    """Match name parts in order, permitting initials alongside a real name."""
    costs = {0: 0}
    for part in candidate:
        for used, cost in list(costs.items()):
            if used == len(query):
                continue
            token = query[used]
            if token == part or (len(token) == 1 and part.startswith(token)):
                extra = 0
            elif typo_budget and _one_typo(token, part):
                extra = 1
            else:
                continue
            if cost + extra <= typo_budget:
                costs[used + 1] = min(costs.get(used + 1, 2), cost + extra)
    return len(query) in costs


def _automatic_candidates(value, full_names, alternatives):
    """Full names win; otherwise require one candidate across all evidence.

    Check exact name parts, initials and email local parts before considering
    one typo. Domains and plus-address tags never identify a learner.
    """
    if value in full_names:
        return full_names[value], False
    query = _name_parts(value)
    if not query or not any(len(part) > 1 for part in query):
        return {}, True
    compact = ''.join(query)
    for budget in (0, 1):
        matches = {}
        for email, learner, names, local in alternatives:
            joined_local = ''.join(local)
            if (_parts_match(query, names, budget) or (local and (
                    _parts_match(query, local, budget) or compact == joined_local
                    or (budget and _one_typo(compact, joined_local))))):
                matches[email] = learner
        if matches:
            return matches, True
    return {}, True


def apply_reviewed_identities(records, aliases=(), legacy_links=(), rules=(), *, learners=None):
    """Resolve staff decisions consistently for the register and reporting.

    A revoked rule blocks older links and automatic name matching. A duplicate
    guest name blocks the module rule, but an explicit per-record review wins.
    Optional learners is the full authorized roster, never a search-filtered
    subset. Automatic decisions are derived on read and remain correctable.
    """
    aliases = {str(row['alias_email']).strip().casefold(): row for row in aliases}
    legacy = {(str(row['occurrence_id']), str(row['attendance_row_id'])): row for row in legacy_links}
    rules = {(row['identity_kind'], row['identity_value']): row for row in rules}
    ambiguous = ambiguous_guest_names(records)
    candidates = defaultdict(dict)
    alternatives = []
    for email, learner in (learners or {}).items():
        name_key = attendance_name_key(learner.get('name'))
        if name_key and email:
            candidates[name_key][email] = learner
        if email:
            local, separator, _domain = str(email).partition('@')
            alternatives.append((email, learner, _name_parts(name_key),
                                 _name_parts(local.split('+', 1)[0]) if separator else ()))
    result = []
    expanded = []
    claims = defaultdict(set)
    cached_candidates = {}
    for source in records:
        row = dict(source)
        key = identity_key(row)
        specific = rules.get(record_key(row))
        rule = specific or rules.get(key)
        collision = key[0] == 'guest_name' and key[1] in ambiguous
        row['identityReviewRequired'] = False
        row['identityAmbiguous'] = collision
        row['identityRule'] = rule
        if rule is not None:
            if rule.get('active') and (specific or not collision):
                row['email'] = rule['canonical_email']
            else:
                row['identityReviewRequired'] = key[0] != 'email'
        elif collision:
            row['identityReviewRequired'] = True
        else:
            old = (aliases.get(key[1]) if key[0] == 'email' else
                   legacy.get((str(row.get('occurrence_id')), str(row.get('id')))))
            if old:
                row['email'] = old['canonical_email']
                row['identityRule'] = {**old, 'active': True, 'revision': 0,
                                       'identity_kind': key[0], 'identity_value': key[1]}
            elif (key[0] == 'guest_name' and str(row.get('role') or '').strip().casefold() in {'', 'attendee'}
                  and not attendance_bot(row)):
                if key[1] not in cached_candidates:
                    cached_candidates[key[1]] = _automatic_candidates(key[1], candidates, alternatives)
                matches, extended = cached_candidates[key[1]]
                if len(matches) == 1:
                    email, learner = next(iter(matches.items()))
                    row['email'] = email
                    row['identityRule'] = {
                        'active': True, 'revision': 0, 'automatic': True,
                        'identity_kind': 'guest_name', 'identity_value': key[1],
                        'learner_profile_id': learner.get('learnerProfileId'),
                        'canonical_email': email, 'display_name': attendance_display_name(source),
                    }
                    if extended:
                        expanded.append(row)
                elif len(matches) > 1:
                    row['identityReviewRequired'] = True
                    row['identityAmbiguous'] = True
        if row.get('email') and not row['identityReviewRequired']:
            _name, reported_id = attendance_identity(source)
            participant = (('identity', reported_id) if reported_id else
                           ('email', str(source['email']).strip().casefold()) if source.get('email') else
                           record_key(source))
            claims[str(row['email']).strip().casefold()].add(participant)
        result.append(row)
    # Two distinct reported people must not collapse onto one learner just
    # because one used a first name and the other a surname/local part.
    # Keep explicit reviews and existing full-name/email decisions authoritative.
    for row in expanded:
        if len(claims[str(row['email']).strip().casefold()]) > 1:
            row.update(email='', identityRule=None, identityReviewRequired=True, identityAmbiguous=True)
    return result


def unmatched_roster(records, *, complete):
    """Keep same-name participants separate so a reviewer can choose one."""
    ambiguous = ambiguous_guest_names(records) | {identity_key(row)[1] for row in records if row.get('identityAmbiguous')}
    groups = defaultdict(list)
    ordinary = []
    for row in records:
        if identity_key(row)[1] in ambiguous and not row.get('email'):
            groups[(identity_key(row)[1], attendance_identity(row)[1] or str(row['id']))].append(row)
        else:
            ordinary.append(row)
    result = session_roster(set(), ordinary, complete=complete)
    for rows in groups.values():
        for person in session_roster(set(), rows, complete=complete):
            person['identityAmbiguous'] = True
            person['suggestedLearnerProfileId'] = None
            result.append(person)
    return result


def link_specs(selected, all_records):
    """Derive scope from server-owned report rows, never a client-supplied name."""
    keys = {identity_key(row) for row in selected}
    if len(keys) != 1:
        raise ValueError('Choose one Teams identity at a time.')
    kind, value = next(iter(keys))
    if kind == 'guest_name' and (not value or value in ambiguous_guest_names(all_records)):
        # A same-name collision is reviewed one distinct Teams identity at a time.
        identities = {attendance_identity(row)[1] or 'row:' + str(row['id']) for row in selected}
        if len(identities) != 1:
            raise ValueError('Several participants used this name. Review each participant separately.')
        return [record_key(row) for row in selected]
    return [(kind, value)]
