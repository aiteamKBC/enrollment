"""Equivalent-field mapping. No programme, consent, declarations or signatures.

Representatives are not assumed to be line managers. Extended ILR Employer
Details uses company information; actual workplace evidence stays separate.
"""
from .eligibility_link import verified_application

DRAFT_FIELDS = {
    'personalDetails.firstName': 'first_name', 'personalDetails.lastName': 'surname',
    'personalDetails.email': 'work_email', 'personalDetails.phone': 'phone_number',
    'personalDetails.country': 'country_of_residence',
    'personalDetails.currentPostcode': 'postcode',
    'personalDetails.addressLine1': 'residential_address_line1', 'personalDetails.addressLine2': 'residential_address_line2',
    'personalDetails.addressLine3': 'residential_town_city', 'personalDetails.addressLine4': 'residential_county',
    'personalDetails.dob': 'date_of_birth', 'personalDetails.address': 'full_address',
    'ilrDetails.postcodePriorToEnrolment': 'postcode',
    'ilrDetails.employmentStatus': 'employment_status',
    'ilrDetails.employmentStartDate': 'start_date_with_employer', 'ilrDetails.jobTitle': 'job_title',
    'ilrDetails.fullTimeEducation': 'full_time_education_prior', 'ilrDetails.expectedLeavingDate': 'expected_leaving_date',
}
ANSWER_FIELDS = {
    'eligibility.countryOfResidence': 'country_of_residence', 'eligibility.ukEeaNational': 'lives_in_uk',
    'eligibility.nationality': 'nationality', 'eligibility.residentPrev3Years': 'resident_prev_3_years',
    'employer.organisationName': 'employer_name',
    'otherTraining.attended12m': 'attended_funded_training', 'otherTraining.completedWhen': 'funded_training_completed_on',
}
LEARNER_FIELDS = {
    'country': 'country_of_residence', 'address_line_1': 'residential_address_line1',
    'address_line_2': 'residential_address_line2', 'address_line_3': 'residential_town_city',
    'address_line_4': 'residential_county',
}


def learner_values(row):
    return {destination: row.get(source) for destination, source in LEARNER_FIELDS.items()}


def mapped(row, fields):
    result = {}
    for path, source in fields.items():
        value = row.get(source)
        if value is None:
            continue
        if hasattr(value, 'isoformat'):
            value = value.isoformat()
        target = result
        parts = path.split('.')
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
    return result


def company_employer_values(row):
    """Company address, independent of the separately verified workplace.

    Active Lines 3/4 use town_city/county. Address contains Lines 1, 2 and 4;
    City is Line 3. Never split historical free text or derive a street/city
    from postcode geography. Saved Enrolment answers are merged above defaults.
    """
    values = {}
    if row.get('employer_postcode'):
        values['postcode'] = row['employer_postcode']
    parts = [str(row.get(key) or '').strip() for key in (
        'employer_address_line1', 'employer_address_line2', 'employer_county')]
    address = ', '.join(part for part in parts if part) or row.get('employer_address')
    if address:
        values['address'] = address
    if row.get('employer_town_city'):
        values['city'] = row['employer_town_city']
    return values


def defaults(learner_id):
    row = verified_application(learner_id)
    if not row:
        return None
    row = dict(row)
    current = row.get('_learner') or {}
    # First-login/staff identity corrections are newer than the application.
    for source, column in {'phone_number':'Phone_number', 'date_of_birth':'Date_of_birth',
                           'work_email':'Email', 'country_of_residence':'Country', 'postcode':'Current_postcode',
                           'residential_address_line1':'Current_address_line_1', 'residential_address_line2':'Current_address_line_2',
                           'residential_town_city':'Current_address_line_3', 'residential_county':'Current_address_line_4'}.items():
        if column in current:
            row[source] = current[column] or ''
    original_name = ' '.join(filter(None, (row.get('first_name'), row.get('surname'))))
    if current.get('Username') and current['Username'] != original_name:
        parts = current['Username'].rsplit(None, 1)
        row['first_name'], row['surname'] = parts[0], parts[1] if len(parts)>1 else ''
    address = [current.get(f'Current_address_line_{n}') or '' for n in range(1,5)]
    if any(address):
        row['full_address'] = ', '.join(part for part in address if part)
    answers = mapped(row, ANSWER_FIELDS)
    answers.setdefault('employer', {}).update(company_employer_values(row))
    years = row.get('years_in_uk')
    if isinstance(years, str) and years.isdecimal():
        answers.setdefault('eligibility', {})['yearsInUk'] = int(years)
    draft = mapped(row, DRAFT_FIELDS)
    # A profile title may be collected later in the wizard. It is no longer
    # collected or copied from the public eligibility application.
    if current.get('Title'):
        draft.setdefault('personalDetails', {})['title'] = current['Title']
    return {'applicationId': str(row['id']), 'draft': draft, 'answers': answers,
            'employerRepresentative': {'name': row.get('representative_name') or '', 'email': row.get('representative_email') or ''}}


def add_defaults(payload, kind, learner_id):
    if kind == 'apprenticeship':
        value = defaults(learner_id)
        if value:
            payload['prefill'] = value
    return payload
