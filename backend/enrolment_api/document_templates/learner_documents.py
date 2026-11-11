"""New document snapshots: current learner data, latest template, fresh signatures."""
import copy
import json
from django.core.serializers.json import DjangoJSONEncoder
from . import service


def snapshot_data(learner, content, template):
    from .pdf_artifacts import new_presentation
    template = new_presentation(template)
    from .preview import learner_data
    from .revisions import required_roles
    from .learner_sources import date_warnings
    key = content['documentKey']
    data = copy.deepcopy(learner_data(key, learner))
    roles = required_roles(key, data)
    for name in ('template', 'documentRevision', 'signatureProvenance', 'unansweredTemplate'):
        data.pop(name, None)
    data.update(learnerName=learner.username, fullySigned=False, independentlySigned=False,
                completed=False, completedAt=None, status='draft', marks={})
    data['signatures'] = {role: {'signed': False, 'name': '', 'signedAt': None, 'signature': '',
                                 'position': '', 'required': role in roles} for role in content['signatureRoles']}
    data['documentFieldValues'] = service.field_values(key, learner, data)
    data['documentTemplateSnapshot'] = copy.deepcopy(template)
    data['learnerDataSnapshot'] = True
    data['bindingWarnings'] = date_warnings(learner)
    # Derivers legitimately return dates/Decimals; persist exactly their JSON form.
    return json.loads(json.dumps(data, cls=DjangoJSONEncoder))


def signing_values(key, data):
    """Populate a fresh signing row from THIS snapshot, never an older document."""
    if key == 'apprenticeship-agreement':
        particulars = data.get('particulars', {})
        mapping = {'apprentice_name': 'apprenticeName', 'employer_name': 'employerName', 'employer_address': 'employerAddress',
                   'standard': 'standard', 'start_date': 'startDate', 'end_date': 'endDate',
                   'practical_start': 'practicalStartDate', 'practical_end': 'practicalEndDate',
                   'duration_weeks': 'durationWeeks', 'planned_otjh': 'plannedOtjHours', 'plan_modules': 'planModules'}
        return {name: copy.deepcopy(particulars[source]) for name, source in mapping.items() if source in particulars}
    if key == 'ilr':
        return {'learner_details': copy.deepcopy(data.get('learnerDetails', {})), 'answers': copy.deepcopy(data.get('answers', {}))}
    if key == 'training-plan':
        return {name: copy.deepcopy(data.get(source, default)) for name, source, default in (
            ('programme', 'programme', {}), ('employment', 'employment', {}), ('learning_plan', 'learningPlan', []),
            ('otjh', 'otjh', {}), ('epa', 'epa', {}), ('contacts', 'contacts', {}))}
    if key == 'written-agreement':
        return {name: copy.deepcopy(data.get(name, {})) for name in ('particulars', 'delivery', 'epa', 'costs', 'contacts')}
    return {'section_status': copy.deepcopy(data.get('sectionStatus', {})),
            'learner_name': data.get('learnerInformation', {}).get('name', ''),
            'scheduled_date': data.get('scheduledDate') or None, 'scheduled_time': data.get('scheduledTime') or None}
