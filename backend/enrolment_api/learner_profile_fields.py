"""Wizard write-through fields on the existing learner profile, never identity keys."""
import re

PROFILE_FIELDS = {
    'title': ('title', 30), 'country': ('country', 100),
    'currentPostcode': ('current_postcode', 12),
    'addressLine1': ('address_line_1', 200), 'addressLine2': ('address_line_2', 200),
    'addressLine3': ('address_line_3', 100), 'addressLine4': ('address_line_4', 100),
    'phone': ('phone_number', 40),
}


def clean_profile(personal, learner):
    from .eligibility_form import COUNTRY_OPTIONS
    from .postcode_lookup import is_valid, formatted
    values = {}
    for key, (attribute, limit) in PROFILE_FIELDS.items():
        if key not in personal:
            continue
        if not isinstance(personal[key], str):
            raise ValueError(f'{key} must be text.')
        value = ' '.join(personal[key].split())
        if len(value) > limit:
            raise ValueError(f'Keep {key} to {limit} characters or fewer.')
        values[attribute] = value
    country = values.get('country', getattr(learner, 'country', '') or '')
    if 'country' in values and country and country not in COUNTRY_OPTIONS and country != getattr(learner, 'country', ''):
        raise ValueError('Choose a listed country.')
    postcode = values.get('current_postcode')
    if 'currentPostcode' in personal and not postcode:
        raise ValueError('Enter your current postcode.')
    if postcode and country == 'United Kingdom':
        if not is_valid(postcode):
            raise ValueError('Enter a valid UK postcode.')
        values['current_postcode'] = formatted(postcode)
    phone = values.get('phone_number')
    if phone and (not re.fullmatch(r'\+?[0-9 ()-]+', phone) or not 7 <= len(re.sub(r'\D', '', phone)) <= 15):
        raise ValueError('Enter a valid mobile number.')
    return values


def transport_profile(values):
    return {key: values[attribute] for key, (attribute, _limit) in PROFILE_FIELDS.items() if attribute in values}


def without_profile_fields(personal):
    # Keep the wizard snapshot as the learner's latest explicit answer. The
    # typed profile columns are the durable record, while this existing draft
    # contract preserves deliberate clears and prevents an older Eligibility
    # prefill from replacing an edit on refresh.
    return dict(personal)
