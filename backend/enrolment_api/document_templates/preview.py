"""Sample or explicitly selected, authorised learner data. Never issues/signs."""
import copy
from types import SimpleNamespace

from .registry import PROGRAMMES, TOKEN, seed, seeds
from .service import field_values, reference


def sample(key, programmes):
    programme = PROGRAMMES[(programmes or ['ME_L4'])[0]]
    learner = SimpleNamespace(pk=999000, username='SAMPLE Learner', programme=programme, cohort='SAMPLE Cohort',
        learner_start_date='2030-01-01', learner_end_date='2032-01-01', employer='SAMPLE Employer', line_manager='SAMPLE Manager')
    parties = seed(key)['signatureRoles']
    signatures = {role: {'signed': False, 'name': '', 'signedAt': None, 'signature': ''} for role in parties}
    signatures['signable'] = False
    if 'employer' in signatures:
        signatures['employer']['required'] = key.startswith('review-')
    data = {'signatures': signatures, 'marks': {role: '' for role in parties}}
    if key == 'apprenticeship-agreement':
        data['particulars'] = {'apprenticeName': learner.username, 'employerName': learner.employer, 'employerAddress': 'SAMPLE Address',
            'standard': programme, 'startDate': '2030-01-01', 'endDate': '2032-01-01', 'practicalStartDate': '2030-01-01',
            'practicalEndDate': '2031-10-01', 'durationWeeks': 91.1, 'plannedOtjHours': 300}
    elif key == 'training-plan':
        data.update(programme={'apprenticeName': learner.username, 'programme': programme, 'standard': programme, 'level': '4',
                              'startDate': '2030-01-01', 'endDate': '2032-01-01'},
            employment={'employerName': learner.employer, 'deliveryAddress': 'SAMPLE Address', 'lineManager': learner.line_manager},
            learningPlan=[{'activity': 'SAMPLE Module', 'deliveryLead': 'SAMPLE Tutor', 'plannedDate': '2030-02-01', 'plannedOtjHours': 4,
                           'weeks': [{'weekNumber': 1, 'weekTitle': 'SAMPLE Week', 'components': [{'title': 'SAMPLE Activity', 'method': 'Workshop', 'otjHours': 4}]}]}],
            otjh={'plannedTotal': 300, 'publishedMinimum': None, 'rplVolume': None}, epa={},
            contacts={'apprentice': {'name': learner.username, 'email': 'sample@example.test'}, 'employer': {'company': learner.employer}})
    elif key == 'written-agreement':
        data.update(particulars={'apprenticeName': learner.username, 'apprenticeshipTitle': programme, 'employer': learner.employer,
            'mainProvider': 'Kent Business College', 'startDate': '2030-01-01', 'plannedEndDate': '2032-01-01'},
            delivery={'activities': [{'title': 'SAMPLE Activity', 'method': 'Workshop', 'hours': 4}], 'totalOtjHours': 300},
            epa={}, costs={'items': [{'item': label, 'price': None} for name, label in next(b['labels'] for b in seed(key)['blocks'] if b.get('source') == 'costs').items() if name.startswith('cost-')], 'total': None}, contacts={})
    elif key == 'ilr':
        data.update(learnerDetails={'givenNames': 'SAMPLE', 'familyName': 'Learner', 'email': 'sample@example.test', 'uln': ''}, answers={})
    else:
        original = seed(key)
        data.update(eventKey='sample-preview', reviewType=original['reviewType'], reviewLabel=original['title'],
            scheduledDate='2030-01-01', scheduledTime='10:00', reviewedBy='SAMPLE Reviewer', completed=False, completedAt=None,
            learnerInformation={'name': learner.username, 'programmeName': programme, 'programmeStartDate': '2030-01-01',
                'plannedEndDate': '2032-01-01', 'programmeStatus': 'SAMPLE', 'employer': learner.employer, 'manager': learner.line_manager, 'mentor': ''},
            answers={}, sectionStatus={}, sections=[b['source'][7:] for b in original['blocks'] if b.get('source', '').startswith('review:')],
            skillsRadar={'items': [], 'answered': 0, 'total': 0}, plrRecords=[], uln='')
    return learner, data


def learner_data(key, learner):
    roles = seed(key)['signatureRoles']
    data = {'signatures': {role: {'signed': False, 'name': '', 'signedAt': None} for role in roles}, 'marks': {role: '' for role in roles}}
    if key == 'apprenticeship-agreement':
        from learner_api.apprenticeship_agreement import derive_particulars
        derived = derive_particulars(learner)
        data['particulars'] = {**derived,
            'practicalStartDate': derived['practicalStart'], 'practicalEndDate': derived['practicalEnd'],
            'plannedOtjHours': derived['plannedOtjh']}
    elif key == 'ilr':
        from learner_api.ilr_document import derive_learner_details, derive_answers
        data.update(learnerDetails=derive_learner_details(learner), answers=derive_answers(learner))
    elif key in ('written-agreement', 'training-plan'):
        from learner_api import written_agreement, training_plan_document
        data.update((written_agreement if key == 'written-agreement' else training_plan_document).derive_content(learner))
    else:
        from learner_api.models import EnrolmentReview
        from learner_api.review_form import _serialize_form, _learner_information
        original = seed(key)
        review = EnrolmentReview.objects.filter(learner_id=learner.pk, review_type=original['reviewType']).exclude(status='cancelled').order_by('-booked_at', '-id').first()
        if review is None:
            review = EnrolmentReview(learner_id=learner.pk, learner_kind='apprenticeship', review_type=original['reviewType'], review_label=original['title'])
        data = _serialize_form(review, learner, None)
        data['learnerInformation'] = _learner_information(learner, verified=True)
        data.pop('template', None)
    return data


def render_payload(content, learner=None):
    key = content['documentKey']
    is_sample = learner is None
    if learner is None: learner, data = sample(key, content['programmes'])
    else: data = learner_data(key, learner)
    result = reference(content)
    values = field_values(key, learner, data)
    for role in seed(key)['signatureRoles']:
        state = (data.get('signatures') or {}).get(role) or {}
        values[f'signature.{role}.name'] = state.get('name') or ''
        values[f'signature.{role}.date'] = state.get('signedAt') or ''
    result['fields'] = values
    used = set(TOKEN.findall(str(content['blocks'])))
    missing = [{'field': ref, 'label': seeds()['fields'][key][ref]['label']} for ref in sorted(used) if values.get(ref) in (None, '')]
    result['missingFields'] = missing
    from .learner_sources import date_warnings
    return {'template': result, 'data': {**copy.deepcopy(data), 'template': result}, 'missingFields': missing,
            'bindingWarnings': [] if is_sample else date_warnings(learner),
            'sample': is_sample, 'notice': 'SAMPLE DATA — no document issued or signing request created.' if is_sample else 'Authorised learner preview only — no document issued or signing request created.'}
