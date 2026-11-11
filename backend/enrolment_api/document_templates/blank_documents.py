"""Blank learner documents share revision storage, never historical body/signatures."""
import copy
from .registry import TemplateError


def is_blank(revision):
    # 'blank' is retained for existing drafts; new Create uses template questions.
    return revision.changes.get('creationMode') in ('blank', 'template', 'learner')


def initial_content(revision):
    snapshot = revision.data.get('documentTemplateSnapshot')
    if snapshot:
        return copy.deepcopy(snapshot['content']) if revision.changes.get('creationMode') == 'learner' else unanswered_content(snapshot['content'])
    return content_for(revision.base.content)


def unanswered_content(template):
    """Retain latest wording/bindings; remove configured answers, not questions."""
    from .question_fields import legacy_fields, CONTENT
    from .registry import TOKEN
    content = copy.deepcopy(template)
    for block in content['blocks']:
        fields = legacy_fields(block, content['documentKey'])
        if fields:
            for field in fields:
                if not field['systemLinked'] and field['type'] not in CONTENT:
                    field['value'] = ''; field['defaultOptionIds'] = []
            block['fields'] = fields
        if block['type'] == 'field' and not TOKEN.search(block.get('value', '')):
            block['value'] = ''
        if block['type'] == 'table' and len(block.get('columns', [])) == 2:
            block['rows'] = [[row[0], row[1] if TOKEN.search(row[1]) else ''] for row in block.get('rows', [])]
    return content


def template_data(learner, roles, content, snapshot):
    from .registry import seeds
    data = data_for(learner, roles)
    data['learnerName'] = learner.username  # Editor metadata, never a document answer.
    data['unansweredTemplate'] = True
    data['learnerInformation'] = {key: '' for key in ('name', 'programmeName', 'programmeStartDate', 'plannedEndDate', 'programmeStatus', 'employer', 'manager', 'mentor')}
    data.update(answers={}, sections=[b['source'][7:] for b in content['blocks'] if b.get('source', '').startswith('review:')],
                skillsRadar={'items': [], 'answered': 0, 'total': 0}, plrRecords=[], sectionStatus={},
                learnerDetails={}, particulars={}, programme={}, employment={}, learningPlan=[], otjh={}, epa={}, contacts={}, costs={}, delivery={},
                reviewType=content.get('reviewType'), reviewLabel=content['title'], uln='')
    data['documentFieldValues'] = {key: '' for key in seeds()['fields'][content['documentKey']]}
    data['documentTemplateSnapshot'] = copy.deepcopy(snapshot)
    return data


def content_for(template):
    content = copy.deepcopy(template)
    content['blocks'] = [{'id': 'blank-page', 'type': 'paragraph', 'text': ''}]
    if 'pageLayout' in content:
        content['pageLayout'].update(header='', footer='')
    return content


def data_for(learner, roles):
    return {'learnerInformation': {'name': learner.username}, 'fullySigned': False, 'independentlySigned': False,
            'marks': {}, 'signatures': {role: {'signed': False, 'name': '', 'signedAt': None, 'signature': '',
                                            'position': '', 'required': True} for role in roles}}


def validate_body(content):
    def filled(block):
        if block['type'] in ('signature', 'page-break'): return False
        if any(str(block.get(key) or '').strip() for key in ('text', 'value')): return True
        if block.get('fields') or block.get('items'): return True
        return any(str(cell).strip() for row in block.get('rows', []) for cell in row)
    if not any(filled(block) for block in content['blocks']):
        raise TemplateError('Add document content before issuing this new document.')
