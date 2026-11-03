"""Read-only catalogue resolution and version pins in the existing answer document.

Catalogue history lives in Wizard_Form_Layouts, not in the curriculum or module
tables. A learner's first real answer pins a hash from that immutable history.
"""
import copy
import re

from django.core import signing
from django.db import connections

from .catalogue import KEY, CatalogueError, PROGRAMMES, current_catalogues, initial

SALT = 'skills-radar-catalogue-v1'


def read_legacy_radar(kind, learner_id):
    """Recover only the radar projection, without reading unrelated step tables."""
    from enrolment_api.models import WizardKsbAssessment, WizardSkillsRadar
    scope = {'learner_kind': kind, 'learner_id': learner_id}
    row = WizardSkillsRadar.objects.filter(**scope).first()
    answers = list(WizardKsbAssessment.objects.filter(**scope))
    if not row and not answers:
        return None
    return {'standardId': (row.standard_id if row else '') or '', 'assessments': {
        a.ksb_id: {'ksbId': a.ksb_id, 'level': a.level, 'note': a.note or '', 'evidenceFiles': a.evidence_files or [],
                  'actionPlan': {'text': a.action_text or '', 'action': a.action or '', 'goal': a.goal or '',
                                 'dueDate': str(a.due_date) if a.due_date else ''}
                  if (a.action_text or a.action or a.goal or a.due_date) else None}
        for a in answers}}


def published():
    from enrolment_api.wizard_layout import CONN, read_current
    with connections[CONN].cursor() as cursor:
        row = read_current(cursor)
    return (row[0], row[1]) if row else (None, {})


def historical(programme, digest, layout=None):
    seed = initial(programme)
    if seed['version'] == digest:
        return seed
    if layout is not None:
        current = current_catalogues(layout)[programme]
        if current['version'] == digest:
            return current
    from enrolment_api.wizard_layout import CONN, LAYOUT_TABLE
    import json
    with connections[CONN].cursor() as cursor:
        cursor.execute(
            f'SELECT "Layout" FROM enrolment."{LAYOUT_TABLE}" '
            'WHERE "Layout" -> %s -> %s ->> %s = %s ORDER BY id DESC LIMIT 1',
            [KEY, programme, 'version', digest],
        )
        row = cursor.fetchone()
    if row:
        data = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        return data[KEY][programme]
    raise CatalogueError('The saved Skills Radar version is unavailable. Please contact admissions; your answers have not been changed.')


def programme_for(learner, kind, layout=None):
    from programme_suitability.models import Assessment
    from programme_suitability.programme_assignment import CATALOGUE_NAMES
    assessment = Assessment.objects.filter(learner_id=learner.pk).only('selected_programme', 'confirmed_at', 'selection_confirmed_at').first()
    if assessment and (assessment.selection_confirmed_at or assessment.confirmed_at) and assessment.selected_programme in PROGRAMMES:
        return assessment.selected_programme
    normalise = lambda value: re.sub(r'[\s—–-]+', ' ', str(value or '')).strip().casefold()
    name = normalise(learner.programme)
    definitions = ((layout or {}).get('programmeSuitability') or {}).get('programmes', [])
    for pid, title in CATALOGUE_NAMES.items():
        identifier = next((p.get('catalogueId') for p in definitions if p.get('id') == pid), None)
        if name and name in {normalise(pid), normalise(title), normalise(identifier)}:
            return pid
    return None


def has_answers(radar, catalogue):
    answers = radar.get('assessments') or {}
    return any(isinstance(answers.get(r['id']), dict) and (
        answers[r['id']].get('level') or answers[r['id']].get('note') or answers[r['id']].get('evidenceFiles')
        or answers[r['id']].get('actionPlan')) for r in catalogue['records'])


def applicable(programme, radar, layout):
    pins = radar.get('catalogueVersions') or {}
    digest = pins.get(programme) if isinstance(pins, dict) else None
    if digest:
        return historical(programme, digest, layout), True
    legacy = initial(programme)
    if has_answers(radar, legacy):
        return legacy, True
    return current_catalogues(layout)[programme], False


def learner_payload(kind, learner, radar, layout):
    programme = programme_for(learner, kind, layout)
    if programme is None:
        return {'programmeId': None, 'catalogue': None, 'token': '', 'pinned': False}
    catalogue, pinned = applicable(programme, radar, layout)
    token = signing.dumps([kind, int(learner.pk), programme, catalogue['version']], salt=SALT, compress=True)
    return {'programmeId': programme, 'catalogue': catalogue, 'pinned': pinned, 'token': token}


def prepare_answers(kind, learner, incoming, stored):
    """Called under the existing learner/ILR transaction before saving answers.

    Client metadata cannot move a saved pin. Preserve ratings/notes/evidence for
    removed or other-programme codes even when an older client sends a subset.
    No read/preview/import calls this function or writes an ILR record.
    """
    radar = incoming.get('skillsRadar')
    previous = (stored or {}).get('skillsRadar') or {}
    if not isinstance(radar, dict):
        if 'skillsRadar' in incoming:
            raise CatalogueError('Skills Radar answers must be a JSON object.')
        if previous:
            incoming['skillsRadar'] = copy.deepcopy(previous)
        return
    radar = copy.deepcopy(radar)
    if not isinstance(radar.get('assessments', {}), dict):
        raise CatalogueError('Skills Radar assessments must be an object keyed by question identifier.')
    if not isinstance(previous, dict) or not isinstance(previous.get('catalogueVersions', {}), dict):
        raise CatalogueError('The saved Skills Radar version metadata is invalid. Please contact admissions; your saved answers have not been changed.')
    radar['assessments'] = {**(previous.get('assessments') or {}), **(radar.get('assessments') or {})}
    pins = copy.deepcopy(previous.get('catalogueVersions') or {})
    radar.pop('catalogueVersions', None)
    token = radar.pop('catalogueToken', None)
    radar.pop('catalogueVersion', None)
    radar.pop('catalogueProgramme', None)
    if not pins and not any(isinstance(a, dict) and (a.get('level') or a.get('note') or a.get('evidenceFiles') or a.get('actionPlan')) for a in radar['assessments'].values()):
        incoming['skillsRadar'] = radar
        return
    _, layout = published()
    programme = programme_for(learner, kind, layout)
    if programme:
        chosen, pinned = applicable(programme, previous, layout)
        if not pinned and token:
            try:
                binding = signing.loads(token, salt=SALT)
            except signing.BadSignature as exc:
                raise CatalogueError('Invalid Skills Radar version token. Reload the wizard before saving.') from exc
            if not isinstance(binding, list) or len(binding) != 4 or binding[:3] != [kind, int(learner.pk), programme]:
                raise CatalogueError('The Skills Radar version token belongs to another assessment.')
            chosen = historical(programme, binding[3], layout)
        if has_answers(radar, chosen):
            if not pinned and not token and chosen['version'] != initial(programme)['version']:
                raise CatalogueError('Reload the wizard to load the current Skills Radar before saving your first answer.')
            pins[programme] = chosen['version']
    if pins:
        radar['catalogueVersions'] = pins
    incoming['skillsRadar'] = radar
