"""Validate ILR learner-details additions against the published DfE categories.

LLDDCat codes: https://guidance.submit-learner-data.service.gov.uk/26-27/ilr/entity/LLDDandHealthProblem/field/LLDDCat
Code 15 is retained for the requested historical option, but expired in 2025;
it must not be silently substituted for a current reporting code.
"""
from decimal import Decimal, InvalidOperation
import re


HEALTH_CODES = frozenset({
    '4', '5', '6', '7', '8', '9', '10', '11', '12', '13', '14', '15',
    '16', '17', '18', '93', '94', '95', '96', '97', '98', '99',
})
NONDISCLOSURE_CODES = frozenset({'98', '99'})
OTHER_CODES = frozenset({'93', '94', '95', '96', '97'})
NI_RE = re.compile(r'^[A-CEGHJ-PR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]$', re.I)


def validate_ilr_additions(details, *, finalise=False):
    """Reject invalid new values on save; require completed answers at Sign + Next.

    Missing keys are valid for historical drafts. Explicitly supplied values are
    always checked, including from staff, without changing staff permissions.
    """
    if not isinstance(details, dict):
        raise ValueError('ILR details must be an object.')
    codes = details.get('healthConditionCodes', [])
    primary = details.get('primaryHealthConditionCode', '')
    support = details.get('additionalSupportDetails', '')
    if not isinstance(codes, list) or any(not isinstance(code, str) or code not in HEALTH_CODES for code in codes):
        raise ValueError('Choose listed health conditions.')
    if len(codes) != len(set(codes)) or len(codes) > 22:
        raise ValueError('Choose each health condition only once.')
    if not isinstance(primary, str) or (primary and (primary not in codes or primary in NONDISCLOSURE_CODES)):
        raise ValueError('Choose a primary condition from your selected conditions.')
    if not isinstance(support, str) or len(support) > 4000:
        raise ValueError('Keep additional support details to 4,000 characters or fewer.')
    if any(code in NONDISCLOSURE_CODES for code in codes) and (len(codes) != 1 or primary or support.strip()):
        raise ValueError('A nondisclosure choice cannot be combined with other health answers.')
    health = details.get('longTermDisability')
    if health is not None and not isinstance(health, bool):
        raise ValueError('Choose Yes or No for the health question.')
    if health is not True and (codes or primary or support.strip()):
        raise ValueError('Clear health follow-up answers unless the health answer is Yes.')
    if support.strip() and not any(code in OTHER_CODES for code in codes):
        raise ValueError('Additional support details require an Other condition.')
    if finalise and health is True and (not codes or (codes[0] not in NONDISCLOSURE_CODES and not primary)):
        raise ValueError('Select your health conditions and a primary condition before signing.')

    hours = details.get('workingHoursPerWeek', '')
    if hours is None:
        hours = ''
    if hours != '':
        if isinstance(hours, bool) or not isinstance(hours, (str, int, float, Decimal)):
            raise ValueError('Enter valid working hours per week.')
        try:
            number = Decimal(str(hours))
        except InvalidOperation as exc:
            raise ValueError('Enter valid working hours per week.') from exc
        if not number.is_finite() or number <= 0 or number > 168 or number.as_tuple().exponent < -2:
            raise ValueError('Working hours per week must be greater than 0 and no more than 168, with at most two decimal places.')
        if details.get('employmentStatus') != 'In paid employment':
            raise ValueError('Clear working hours when not in paid employment.')
    elif finalise and details.get('employmentStatus') == 'In paid employment':
        raise ValueError('Enter your working hours per week before signing.')

    ni = details.get('niNumber', '')
    if ni and (not isinstance(ni, str) or not NI_RE.fullmatch(ni.strip())):
        raise ValueError('Enter a valid National Insurance number.')
    applied = details.get('niApplied')
    if (applied is not None and not isinstance(applied, bool)) or (ni and applied is True):
        raise ValueError('NI application confirmation is only available without an NI number.')
    if finalise and not ni and applied is not True:
        raise ValueError('Confirm that you have applied for a National Insurance number.')


def working_hours_value(value):
    return Decimal(str(value)) if value not in (None, '') else None
