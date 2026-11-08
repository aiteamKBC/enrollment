"""Admin-only Suitability & RPL report aggregation and review workflow."""
from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import DatabaseError, connections, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect

from enrolment_api.models import ExtendedIlr, WizardCvJob, WizardIlrLearnerDetails
from enrolment_api.skills_radar.catalogue import CatalogueError, PROGRAMMES
from enrolment_api.skills_radar.service import applicable, programme_for, read_legacy_radar
from enrolment_api.wizard_layout import CONN, effective_layout
from enrolment_api.wizard_steps import LEVEL_SCORES
from learner_api.models import EnrolmentUser
from learner_api.training_plan_document import derive_content
from login.permissions import require_role

from .configuration import PROGRAMMES as SUITABILITY_PROGRAMMES
from .models import Assessment, SuitabilityCvExtraction, SuitabilityRplReduction, SuitabilityRplReport
from .cv_extraction import CvExtractionError, extract_approved_cv

MAX_TEXT = 20_000
QUESTION_ONE = 'What is your understanding of the programme you are applying for?'
QUESTION_TWO = 'How will this programme help you in your career development/aspirations, and/or with your progression?'


class ReportInputError(ValueError):
    pass


def _s(value):
    return str(value or '').strip()


def _json_hash(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _account(request):
    return getattr(request, 'login_account', None)


def _actor_id(request):
    value = getattr(_account(request), 'id', None)
    return int(value) if value is not None else None


def _actor_name(request):
    account = _account(request)
    return _s(getattr(account, 'display_name', '')) or _s(getattr(account, 'email', '')) or 'Administrator'


def _learner(kind, learner_id):
    if kind not in {'apprenticeship', 'commercial'}:
        return None
    learner = EnrolmentUser.all_learners.filter(pk=learner_id).first()
    if learner is None:
        return None
    stored_kind = _s(learner.learner_type) or 'apprenticeship'
    return learner if stored_kind == kind else None


def _layout(kind, learner_id):
    try:
        with connections[CONN].cursor() as cursor:
            row = effective_layout(cursor, kind, learner_id)
    except DatabaseError:
        return None, {}
    return (row[0], row[1]) if row else (None, {})


def _question(layout, key, fallback):
    value = (layout.get('texts') or {}).get(key) if isinstance(layout, dict) else None
    return _s(value) or fallback


def _programme_assessment(learner_id):
    return Assessment.objects.filter(learner_id=learner_id).first()


def _context(learner, kind, assessment):
    source_programme_id = _s(getattr(assessment, 'selected_programme', '')) or _s(learner.programme)
    programme_id = (source_programme_id if source_programme_id in PROGRAMMES else programme_for(learner, kind)) or source_programme_id
    programme_name = _s(learner.programme) or source_programme_id
    # Keep the historical context-key input unchanged so existing report rows
    # remain addressable, while displaying the approved Aptem fallback.
    source_reference = _s(learner.reference_number)
    reference = source_reference or _s(learner.aptem_id)
    practical_start = _s(learner.learner_start_date) or _s(learner.start_date)
    raw = [kind, int(learner.pk), source_programme_id, programme_name, source_reference, practical_start]
    return {
        'key': _json_hash(raw),
        'programmeId': programme_id,
        'programmeName': programme_name,
        'enrolmentReference': reference,
        'practicalStartDate': practical_start,
    }


def _suitability_evidence(assessment):
    if assessment is None:
        return {
            'status': 'not_available', 'pathway': '', 'selectedProgramme': '',
            'responses': [], 'review': {},
        }
    configuration = assessment.configuration if isinstance(assessment.configuration, dict) else {}
    saved_responses = assessment.responses if isinstance(assessment.responses, dict) else {}
    selected_programme = _s(getattr(assessment, 'selected_programme', ''))
    pathway = _s(getattr(assessment, 'pathway', '')) or SUITABILITY_PROGRAMMES.get(selected_programme, ('',))[0]
    programme_definitions = configuration.get('programmes', [])
    selected_specification = next(
        (
            {
                key: item.get(key)
                for key in ('id', 'title', 'summary', 'description', 'pathway', 'level', 'responsibilities')
                if key in item
            }
            for item in programme_definitions
            if isinstance(item, dict) and _s(item.get('id')) == selected_programme
        ),
        {},
    )
    questions = [
        question for question in configuration.get('questions', [])
        if isinstance(question, dict) and _s(question.get('pathway')) == pathway
    ]
    questions.sort(key=lambda question: (question.get('order', 0), _s(question.get('id'))))
    rows = []
    for question in questions:
        question_id = _s(question.get('id'))
        raw_answer_ids = saved_responses.get(question_id, [])
        answer_ids = raw_answer_ids if isinstance(raw_answer_ids, list) else []
        answers = [
            answer for answer in question.get('answers', [])
            if isinstance(answer, dict) and _s(answer.get('id')) in answer_ids
        ]
        answers.sort(key=lambda answer: (answer.get('order', 0), _s(answer.get('id'))))
        selected = [
            {'id': _s(answer.get('id')), 'label': _s(answer.get('text'))}
            for answer in answers if _s(answer.get('text'))
        ]
        rows.append({
            'questionId': question_id,
            'group': _s(question.get('title')),
            'question': _s(question.get('description')),
            # ``answers`` remains a compact compatibility field for existing
            # consumers; ``selectedOptions`` carries the complete original
            # option identity and wording for the report PDF.
            'answers': [item['label'] for item in selected],
            'selectedOptions': selected,
            'saved': question_id in saved_responses,
        })
    return {
        'status': 'available',
        'pathway': pathway,
        'selectedProgramme': selected_programme,
        **({'selectedProgrammeSpecification': selected_specification} if selected_specification else {}),
        'recommendedProgramme': _s((assessment.result or {}).get('recommendedProgramme')),
        'outcome': _s((assessment.result or {}).get('outcome')),
        'responses': rows,
        'review': assessment.review or {},
    }


def _programme_metadata(learner, kind, layout, context):
    definitions = ((layout or {}).get('programmeSuitability') or {}).get('programmes', [])
    definitions = definitions if isinstance(definitions, list) else []
    canonical_id = programme_for(learner, kind, layout)
    if not canonical_id and any(item.get('id') == context['programmeId'] for item in definitions if isinstance(item, dict)):
        canonical_id = context['programmeId']
    definition = next((item for item in definitions if isinstance(item, dict) and item.get('id') == canonical_id), {})
    identifier = _s(definition.get('catalogueId'))
    display_name = context['programmeName']
    level = ''
    try:
        with connections[CONN].cursor() as cursor:
            if identifier:
                cursor.execute(
                    'SELECT name, level FROM curriculum.programmes WHERE programme_id = %s '
                    'AND coalesce(is_archived, false) = false AND coalesce(is_active, true) = true '
                    "AND deleted_at IS NULL AND lower(coalesce(status, 'active')) NOT IN ('archived', 'deleted', 'inactive')",
                    [identifier],
                )
            else:
                cursor.execute(
                    'SELECT name, level FROM curriculum.programmes WHERE lower(trim(name)) = lower(trim(%s)) '
                    'AND coalesce(is_archived, false) = false AND coalesce(is_active, true) = true '
                    "AND deleted_at IS NULL AND lower(coalesce(status, 'active')) NOT IN ('archived', 'deleted', 'inactive')",
                    [display_name],
                )
            rows = cursor.fetchall()
        if len(rows) == 1:
            catalogue_name, catalogue_level = rows[0]
            if not display_name or display_name == canonical_id:
                display_name = _s(catalogue_name)
            level = _s(catalogue_level)
    except DatabaseError:
        pass
    return {'id': _s(canonical_id) or context['programmeId'], 'name': display_name, 'level': level}


def _skills_radar(layout, draft, programme_id, kind, learner_id):
    draft_radar = (draft or {}).get('skillsRadar') if isinstance(draft, dict) else {}
    draft_radar = draft_radar if isinstance(draft_radar, dict) else {}
    projected = read_legacy_radar(kind, learner_id) or {}
    projected = projected if isinstance(projected, dict) else {}
    radar = {
        **projected,
        **draft_radar,
        'assessments': {
            **(projected.get('assessments') if isinstance(projected.get('assessments'), dict) else {}),
            **(draft_radar.get('assessments') if isinstance(draft_radar.get('assessments'), dict) else {}),
        },
    }
    catalogue = {}
    pinned = False
    if programme_id:
        try:
            catalogue, pinned = applicable(programme_id, radar, layout or {})
        except (CatalogueError, KeyError):
            # Preserve saved answers even when the matching historical
            # catalogue is unavailable; never substitute a newer description.
            catalogue = {}
    records = {record.get('id'): record for record in catalogue.get('records', [])}
    answers = radar.get('assessments') if isinstance(radar.get('assessments'), dict) else {}
    rows = []
    for ksb_id, raw in answers.items():
        answer = raw if isinstance(raw, dict) else {}
        record = records.get(ksb_id) or {}
        level = _s(answer.get('level')).lower()
        rows.append({
            'id': _s(ksb_id),
            'code': _s(record.get('code')),
            'kind': _s(record.get('kind')),
            'description': _s(record.get('description')) or 'Saved KSB no longer present in the available catalogue.',
            'level': level,
            'score': LEVEL_SCORES.get(level),
            'moduleKey': _s(record.get('moduleKey')),
            'owners': record.get('owners') if isinstance(record.get('owners'), dict) else {},
        })
    rows.sort(key=lambda item: (item['kind'], item['code'], item['id']))
    preferred_groups = {'knowledge': 'Knowledge', 'skill': 'Skills', 'skills': 'Skills',
                        'behaviour': 'Behaviours', 'behavior': 'Behaviours', 'behaviours': 'Behaviours',
                        'behaviors': 'Behaviours'}
    grouped = {}
    for item in rows:
        raw_kind = _s(item.get('kind')) or 'Uncategorised'
        key = raw_kind.casefold()
        label = preferred_groups.get(key, raw_kind)
        group = grouped.setdefault(label, {'label': label, 'answered': 0, 'total': 0, '_scores': []})
        group['total'] += 1
        if item.get('score') is not None:
            group['answered'] += 1
            group['_scores'].append(float(item['score']))
    summary = []
    for category in ('Knowledge', 'Skills', 'Behaviours'):
        group = grouped.get(category, {'label': category, 'answered': 0, 'total': 0, '_scores': []})
        scores = group.pop('_scores')
        group['score'] = round(sum(scores) / len(scores), 1) if scores else None
        summary.append(group)
    return {
        'status': 'available' if rows else 'missing',
        'scaleMaximum': 9,
        'selfAssessment': True,
        'catalogueVersion': _s(catalogue.get('version')),
        'cataloguePinned': pinned,
        'summary': summary,
        'items': rows,
    }


def _cv_evidence(kind, learner_id, draft, details=None):
    row = WizardCvJob.objects.filter(learner_kind=kind, learner_id=learner_id).first()
    cv = (draft or {}).get('cvJob') if isinstance(draft, dict) else {}
    cv = cv if isinstance(cv, dict) else {}
    documents = row.documents if row and isinstance(row.documents, list) else []
    document_rows = [
        {'id': _s(item.get('id')), 'kind': _s(item.get('docKind')), 'filename': _s(item.get('filename')),
         'container': _s(item.get('container')), 'blobName': _s(item.get('blobName')),
         'contentType': _s(item.get('contentType'))}
        for item in documents if isinstance(item, dict)
    ]
    experience = _s(getattr(row, 'experience_text', '')) or _s(cv.get('experienceText'))
    qualifications = []
    for label, stored_value, draft_value in (
        ('Highest qualification', getattr(row, 'highest_qualification', '') if row else '', cv.get('highestQualification')),
        ('Qualification subject', getattr(row, 'highest_qualification_field', '') if row else '', cv.get('highestQualificationField')),
        ('Relevant field qualification', getattr(row, 'highest_field_qualification', '') if row else '', cv.get('highestFieldQualification')),
        ('Previous qualifications', getattr(row, 'pm_qualifications', '') if row else '', cv.get('pmQualifications')),
    ):
        value = _s(stored_value) or _s(draft_value)
        if _s(value):
            qualifications.append({'label': label, 'value': _s(value), 'source': 'CV and job step'})
    if not qualifications and _s(getattr(details, 'highest_qualification', '')):
        qualifications.append({
            'label': 'Highest qualification',
            'value': _s(details.highest_qualification),
            'source': 'ILR learner details',
        })
    has_cv = bool(_s(getattr(row, 'cv_file', '')) if row else _s(cv.get('cvFile'))) or any(item['kind'] == 'cv' for item in document_rows)
    cv_sources = [item for item in document_rows if item['kind'] == 'cv']
    extraction_status = 'not_provided'
    if cv_sources:
        latest = SuitabilityCvExtraction.objects.filter(learner_kind=kind, learner_id=learner_id, source_document_id=cv_sources[0]['id']).order_by('-updated_at').first()
        extraction_status = latest.status if latest else 'pending'
    has_qualifications = bool(qualifications) or any(item['kind'] == 'transcript' for item in document_rows)
    return {
        'cv': {'status': 'provided' if has_cv else 'not_provided', 'extractionStatus': extraction_status, 'experience': '',
               'sourceDocuments': [item for item in document_rows if item['kind'] == 'cv'],
               'documents': [item for item in document_rows if item['kind'] == 'cv']},
        'qualifications': {'status': 'provided' if has_qualifications else 'not_provided',
                           'items': qualifications,
                           'documents': [item for item in document_rows if item['kind'] != 'cv']},
        'plr': {'status': 'not_available', 'message': 'Not available - integration not implemented'},
    }


def _training_plan(learner, skills):
    content = derive_content(learner)
    learning_plan = content.get('learningPlan') if isinstance(content.get('learningPlan'), list) else []
    modules = []
    for row in learning_plan:
        module_id = _s(row.get('moduleId'))
        planned = row.get('plannedOtjHours')
        try:
            planned_value = Decimal(str(planned)) if planned is not None else None
        except InvalidOperation:
            planned_value = None
        codes = []
        for item in skills.get('items', []):
            if item.get('moduleKey') == module_id:
                codes.append(item.get('code'))
        modules.append({
            'moduleId': module_id,
            'title': _s(row.get('activity')),
            'plannedHours': float(planned_value) if planned_value is not None else None,
            'ksbCodes': [code for code in codes if code],
        })
    available = bool(modules) and all(item['moduleId'] and item['plannedHours'] is not None for item in modules)
    total = sum(Decimal(str(item['plannedHours'])) for item in modules) if available else None
    return {
        'available': available,
        'source': 'authoritative_training_plan',
        'plannedHours': float(total) if total is not None else None,
        'modules': modules,
        'message': '' if available else 'Not available - no authoritative Training Plan modules and hours are saved.',
    }


def build_source_snapshot(learner, kind, assessment, context):
    extended = ExtendedIlr.objects.filter(learner_kind=kind, learner_id=learner.pk).first()
    answers = extended.answers if extended and isinstance(extended.answers, dict) else {}
    draft = extended.wizard_draft if extended and isinstance(extended.wizard_draft, dict) else {}
    details = WizardIlrLearnerDetails.objects.filter(learner_kind=kind, learner_id=learner.pk).first()
    layout_version, layout = _layout(kind, learner.pk)
    programme = _programme_metadata(learner, kind, layout, context)
    suitability = _suitability_evidence(assessment)
    skills = _skills_radar(layout, draft, programme['id'], kind, learner.pk)
    evidence = _cv_evidence(kind, learner.pk, draft, details)
    training = _training_plan(learner, skills)
    understanding = answers.get('understanding') if isinstance(answers.get('understanding'), dict) else {}
    additional = answers.get('additionalInformation') if isinstance(answers.get('additionalInformation'), dict) else {}
    employer = answers.get('employer') if isinstance(answers.get('employer'), dict) else {}
    responsibilities = []
    for value in (
        additional.get('jobRoleRelevance'),
        evidence['cv'].get('experience'),
    ):
        text = _s(value)
        if text and text.lower() != 'n/a' and text not in responsibilities:
            responsibilities.append(text)
    snapshot = {
        'learner': {
            'id': str(learner.pk),
            'name': _s(learner.username),
            'reference': context['enrolmentReference'],
            'employer': _s(learner.employer) or _s(employer.get('organisationName')),
            'jobTitle': _s(getattr(details, 'job_title', '')),
            'responsibilities': responsibilities,
        },
        'programme': {**programme, 'startDate': context['practicalStartDate']},
        'programmeUnderstanding': [
            {'question': _question(layout, 'xilr.programmeUnderstanding.label', QUESTION_ONE),
             'answer': _s(understanding.get('programmeUnderstanding'))},
            {'question': _question(layout, 'xilr.careerProgression.label', QUESTION_TWO),
             'answer': _s(understanding.get('careerProgression'))},
        ],
        'programmeSuitability': suitability,
        'skillsRadar': skills,
        'evidence': evidence,
        'trainingPlan': training,
        'limitations': ['PLR integration is not implemented. Missing PLR data is not evidence that prior learning is absent.'],
        'capturedAt': timezone.now().isoformat(),
    }
    versions = {
        'wizardLayoutVersion': layout_version,
        'skillsRadarCatalogueVersions': (draft.get('skillsRadar') or {}).get('catalogueVersions', {}) if isinstance(draft.get('skillsRadar'), dict) else {},
        'skillsRadarEffectiveCatalogueVersion': skills.get('catalogueVersion'),
        'skillsRadarProgrammeId': programme['id'],
        'programmeSuitabilityConfigurationVersion': getattr(assessment, 'configuration_version', None),
        'programmeSuitabilityRevision': getattr(assessment, 'revision', None),
        'moduleConfigurationVersion': getattr(assessment, 'modules_configuration_version', None),
        'moduleSelectionRevision': getattr(assessment, 'modules_revision', None),
        'learningPlanHash': _json_hash(learner.learning_plan or learner.training_plan or []),
    }
    return snapshot, versions


def _report_json(report):
    if report is None:
        return None
    return {
        'id': str(report.pk), 'status': report.status, 'reportReference': report.report_reference,
        'reportVersion': report.report_version, 'revision': report.revision,
        'learnerKind': report.learner_kind, 'learnerId': str(report.learner_id),
        'contextKey': report.context_key, 'programmeId': report.programme_id,
        'programmeName': report.programme_name, 'enrolmentReference': report.enrolment_reference,
        'practicalStartDate': report.practical_start_date,
        'relevantPriorLearning': report.relevant_prior_learning,
        'reductionJustified': report.reduction_justified,
        'assessorEvidenceReview': report.assessor_evidence_review,
        'noReductionReason': report.no_reduction_reason,
        'suitabilityOutcome': report.suitability_outcome,
        'finalSummary': report.final_summary,
        'suitabilityJustification': report.suitability_justification,
        'suitabilityAiMetadata': report.suitability_ai_metadata,
        'suitabilityReviewed': report.suitability_reviewed_at is not None,
        'suitabilityReviewedAt': report.suitability_reviewed_at.isoformat() if report.suitability_reviewed_at else None,
        'suitabilityReviewedBy': report.suitability_reviewed_by_name,
        'rplJustification': report.rpl_justification, 'rplAiMetadata': report.rpl_ai_metadata,
        'rplReviewed': report.rpl_reviewed_at is not None,
        'rplReviewedAt': report.rpl_reviewed_at.isoformat() if report.rpl_reviewed_at else None,
        'rplReviewedBy': report.rpl_reviewed_by_name,
        'cvExtractionAcknowledged': report.cv_extraction_acknowledged,
        'cvExtractionAcknowledgedBy': report.cv_extraction_acknowledged_by,
        'cvExtractionAcknowledgedAt': report.cv_extraction_acknowledged_at.isoformat() if report.cv_extraction_acknowledged_at else None,
        'cvExtractionAcknowledgementReason': report.cv_extraction_acknowledgement_reason,
        'cvEvidenceVersion': {'documentId': report.cv_source_document_id, 'container': report.cv_source_container, 'blobName': report.cv_source_blob_name, 'sha256': report.cv_source_blob_hash, 'extractorVersion': report.cv_extractor_version},
        'sourceSnapshot': report.source_snapshot, 'sourceVersions': report.source_versions,
        'sourceSnapshotHash': report.source_snapshot_hash,
        'reductions': [
            {'id': str(row.pk), 'moduleId': row.module_id, 'moduleTitle': row.module_title,
             'plannedHours': float(row.planned_hours), 'reducedHours': float(row.reduced_hours),
             'ksbCodes': row.ksb_codes or [], 'evidence': row.evidence,
             'assessorComments': row.assessor_comments}
            for row in report.reductions.all()
        ],
        'createdAt': report.created_at.isoformat() if report.created_at else None,
        'updatedAt': report.updated_at.isoformat() if report.updated_at else None,
        'finalisedAt': report.finalised_at.isoformat() if report.finalised_at else None,
    }


def _read_body(request):
    if len(request.body) > 1_000_000:
        raise ReportInputError('The report payload is too large.')
    try:
        data = json.loads(request.body or b'{}')
    except (ValueError, UnicodeDecodeError) as exc:
        raise ReportInputError('Provide a valid JSON object.') from exc
    if not isinstance(data, dict):
        raise ReportInputError('Provide a JSON object.')
    return data


def _text(data, key, required=False):
    value = data.get(key, '')
    if not isinstance(value, str) or len(value) > MAX_TEXT:
        raise ReportInputError(f'{key} must be text of at most {MAX_TEXT:,} characters.')
    value = value.strip()
    if required and not value:
        raise ReportInputError(f'{key} is required.')
    return value


def _nullable_bool(data, key):
    value = data.get(key)
    if value is not None and not isinstance(value, bool):
        raise ReportInputError(f'{key} must be true, false or null.')
    return value


def _decimal(value, field):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ReportInputError(f'{field} must be a number.') from exc
    if not number.is_finite():
        raise ReportInputError(f'{field} must be a finite number.')
    return number.quantize(Decimal('0.01'))


def _validated_reductions(data, snapshot, relevant, justified):
    raw = data.get('reductions', [])
    if not isinstance(raw, list):
        raise ReportInputError('reductions must be a list.')
    if not justified:
        if raw:
            raise ReportInputError('Module reductions are only allowed when a reduction is justified.')
        return []
    if relevant is not True:
        raise ReportInputError('A reduction requires verified relevant prior learning.')
    if not raw:
        raise ReportInputError('Add at least one module reduction.')
    training = snapshot.get('trainingPlan') if isinstance(snapshot.get('trainingPlan'), dict) else {}
    if not training.get('available'):
        raise ReportInputError('Authoritative Training Plan hours are unavailable; reductions cannot be saved.')
    modules = {item.get('moduleId'): item for item in training.get('modules', []) if isinstance(item, dict)}
    seen, result = set(), []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ReportInputError('Each reduction must be an object.')
        module_id = _s(item.get('moduleId'))
        module = modules.get(module_id)
        if not module or module_id in seen:
            raise ReportInputError('Choose each available Training Plan module at most once.')
        seen.add(module_id)
        planned = _decimal(module.get('plannedHours'), 'plannedHours')
        reduced = _decimal(item.get('reducedHours'), 'reducedHours')
        if reduced <= 0 or reduced > planned:
            raise ReportInputError(f'Reduced hours for {module.get("title") or module_id} must be positive and no more than {planned}.')
        evidence = item.get('evidence', '')
        comments = item.get('assessorComments', '')
        if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > MAX_TEXT:
            raise ReportInputError('Verified evidence is required for every module reduction.')
        if not isinstance(comments, str) or len(comments) > MAX_TEXT:
            raise ReportInputError('Assessor comments must be text.')
        ksb_codes = item.get('ksbCodes', [])
        available_codes = set(module.get('ksbCodes') or [])
        if not isinstance(ksb_codes, list) or any(not isinstance(code, str) or code not in available_codes for code in ksb_codes):
            raise ReportInputError('KSB selections must come from the selected Training Plan module mapping.')
        result.append({'moduleId': module_id, 'moduleTitle': _s(module.get('title')),
                       'plannedHours': planned, 'reducedHours': reduced,
                       'ksbCodes': list(dict.fromkeys(ksb_codes)), 'evidence': evidence.strip(),
                       'assessorComments': comments.strip(), 'displayOrder': index})
    total = sum((item['reducedHours'] for item in result), Decimal('0'))
    plan_total = _decimal(training.get('plannedHours'), 'plannedHours')
    if total > plan_total:
        raise ReportInputError('Total reduced hours cannot exceed the Training Plan total.')
    return result


def _review_state(report, data, prefix, text_changed, request):
    key = f'{prefix}Reviewed'
    reviewed = data.get(key, False)
    if not isinstance(reviewed, bool):
        raise ReportInputError(f'{key} must be true or false.')
    if reviewed:
        setattr(report, f'{prefix}_reviewed_by', _actor_id(request))
        setattr(report, f'{prefix}_reviewed_by_name', _actor_name(request))
        setattr(report, f'{prefix}_reviewed_at', timezone.now())
    elif text_changed or getattr(report, f'{prefix}_reviewed_at'):
        setattr(report, f'{prefix}_reviewed_by', None)
        setattr(report, f'{prefix}_reviewed_by_name', '')
        setattr(report, f'{prefix}_reviewed_at', None)


def _validate_finalisation(report, reductions):
    cv = (report.source_snapshot.get('evidence') or {}).get('cv') if isinstance(report.source_snapshot, dict) else {}
    if cv and cv.get('status') == 'provided' and not report.cv_extraction_acknowledged:
        _private_cv_text(report, cv)
    if cv and cv.get('status') == 'provided' and not report.cv_extraction_acknowledged:
        version = (report.rpl_ai_metadata or {}).get('cvEvidenceVersion') or {}
        if version.get('sha256') != report.cv_source_blob_hash or version.get('documentId') != report.cv_source_document_id:
            raise ReportInputError('RPL justification evidence is outdated. Generate and review a new justification.')
    if report.suitability_outcome == SuitabilityRplReport.SUITABILITY_NOT_ASSESSED:
        raise ReportInputError('Choose a programme suitability outcome before finalising.')
    if not report.suitability_justification or not report.suitability_reviewed_at:
        raise ReportInputError('Review and approve the programme suitability justification before finalising.')
    if report.relevant_prior_learning is None or report.reduction_justified is None:
        raise ReportInputError('Complete the prior learning decision before finalising.')
    if not report.assessor_evidence_review:
        raise ReportInputError('Record the assessor evidence review before finalising.')
    if report.reduction_justified and not reductions:
        raise ReportInputError('Add at least one validated module reduction before finalising.')
    if not report.reduction_justified and not report.no_reduction_reason:
        raise ReportInputError('Explain why no training reduction is justified before finalising.')
    if not report.rpl_justification or not report.rpl_reviewed_at:
        raise ReportInputError('Review and approve the RPL justification before finalising.')
    if not report.final_summary:
        raise ReportInputError('Add the final assessor-reviewed summary before finalising.')


def _save_report(request, learner, kind, context, assessment, data):
    with transaction.atomic():
        report = SuitabilityRplReport.objects.select_for_update().filter(
            learner_kind=kind, learner_id=learner.pk, context_key=context['key'],
            status=SuitabilityRplReport.STATUS_DRAFT,
        ).first()
        if report is None:
            finalised = SuitabilityRplReport.objects.select_for_update().filter(
                learner_kind=kind, learner_id=learner.pk, context_key=context['key'],
                status=SuitabilityRplReport.STATUS_FINALISED,
            ).order_by('-report_version', '-updated_at').first()
            if finalised is not None and data.get('newVersion') is not True:
                raise ReportInputError(
                    'This report is finalised and immutable. Start a new report version explicitly to reassess it.'
                )
            snapshot, versions = build_source_snapshot(learner, kind, assessment, context)
            report = SuitabilityRplReport(
                learner_kind=kind, learner_id=learner.pk, context_key=context['key'],
                programme_id=context['programmeId'], programme_name=context['programmeName'],
                enrolment_reference=context['enrolmentReference'], practical_start_date=context['practicalStartDate'],
                report_reference=f'RPL-{learner.pk}-{uuid.uuid4().hex[:8].upper()}',
                report_version=(finalised.report_version + 1) if finalised is not None else 1,
                source_snapshot=snapshot, source_versions=versions, source_snapshot_hash=_json_hash(snapshot),
                created_by=_actor_id(request), revision=0,
            )
        elif data.get('revision') != report.revision:
            raise ReportInputError('This report changed in another session. Reload before saving.')

        relevant = _nullable_bool(data, 'relevantPriorLearning')
        justified = _nullable_bool(data, 'reductionJustified')
        if relevant is False:
            justified = False
        elif relevant is None:
            justified = None
        elif relevant is True and justified is None and data.get('finalise'):
            raise ReportInputError('Decide whether verified prior learning justifies a reduction.')

        evidence_review = _text(data, 'assessorEvidenceReview')
        no_reduction = _text(data, 'noReductionReason')
        if justified is False and not no_reduction:
            raise ReportInputError('A written explanation is required when no reduction is justified.')
        reductions = _validated_reductions(data, report.source_snapshot, relevant, justified)

        suitability = _text(data, 'suitabilityJustification')
        rpl = _text(data, 'rplJustification')
        suitability_changed = suitability != report.suitability_justification
        rpl_changed = rpl != report.rpl_justification
        outcome = data.get('suitabilityOutcome', SuitabilityRplReport.SUITABILITY_NOT_ASSESSED)
        if outcome not in dict(SuitabilityRplReport.SUITABILITY_CHOICES):
            raise ReportInputError('Choose a valid suitability outcome.')

        report.relevant_prior_learning = relevant
        report.reduction_justified = justified
        report.assessor_evidence_review = evidence_review
        report.no_reduction_reason = no_reduction if justified is False else ''
        report.suitability_outcome = outcome
        report.final_summary = _text(data, 'finalSummary')
        report.suitability_justification = suitability
        report.rpl_justification = rpl
        if data.get('cvExtractionAcknowledged') is True:
            reason = _text(data, 'cvExtractionAcknowledgementReason')
            if not reason:
                raise ReportInputError('Record a reason before acknowledging unavailable CV evidence.')
            report.cv_extraction_acknowledged = True
            report.cv_extraction_acknowledged_by = _actor_id(request)
            report.cv_extraction_acknowledged_at = timezone.now()
            report.cv_extraction_acknowledgement_reason = reason
            report.cv_source_blob_hash = ''
        elif data.get('cvExtractionAcknowledged') is False:
            report.cv_extraction_acknowledged = False
            report.cv_extraction_acknowledged_by = None
            report.cv_extraction_acknowledged_at = None
            report.cv_extraction_acknowledgement_reason = ''
        _review_state(report, data, 'suitability', suitability_changed, request)
        _review_state(report, data, 'rpl', rpl_changed, request)
        if data.get('rplReviewed') is True and (report.source_snapshot.get('evidence', {}).get('cv', {}).get('status') == 'provided') and not report.cv_extraction_acknowledged:
            _private_cv_text(report, report.source_snapshot['evidence']['cv'])
            version = (report.rpl_ai_metadata or {}).get('cvEvidenceVersion', {})
            if not version.get('sha256') or version.get('sha256') != report.cv_source_blob_hash or version.get('documentId') != report.cv_source_document_id:
                raise ReportInputError('Generate a fresh RPL justification for the current CV evidence before approving it.')
        report.updated_by = _actor_id(request)
        report.revision += 1

        if data.get('finalise') is not None and not isinstance(data.get('finalise'), bool):
            raise ReportInputError('finalise must be true or false.')
        if data.get('finalise'):
            _validate_finalisation(report, reductions)
            report.status = SuitabilityRplReport.STATUS_FINALISED
            report.finalised_by = _actor_id(request)
            report.finalised_at = timezone.now()
        report.save()
        report.reductions.all().delete()
        SuitabilityRplReduction.objects.bulk_create([
            SuitabilityRplReduction(report=report, module_id=item['moduleId'], module_title=item['moduleTitle'],
                planned_hours=item['plannedHours'], reduced_hours=item['reducedHours'], ksb_codes=item['ksbCodes'],
                evidence=item['evidence'], assessor_comments=item['assessorComments'], display_order=item['displayOrder'])
            for item in reductions
        ])
        return SuitabilityRplReport.objects.prefetch_related('reductions').get(pk=report.pk)


def _current_report(kind, learner_id, context_key):
    return SuitabilityRplReport.objects.prefetch_related('reductions').filter(
        learner_kind=kind, learner_id=learner_id, context_key=context_key,
    ).order_by('status', '-updated_at').first()


@require_role('admin')
@csrf_protect
def suitability_rpl_report(request, kind, learner_id):
    if request.method not in {'GET', 'PUT'}:
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    learner = _learner(kind, learner_id)
    if learner is None:
        return JsonResponse({'error': 'Learner not found.'}, status=404)
    assessment = _programme_assessment(learner_id)
    context = _context(learner, kind, assessment)
    try:
        if request.method == 'PUT':
            report = _save_report(request, learner, kind, context, assessment, _read_body(request))
            return JsonResponse({'report': _report_json(report), 'aiEnabled': bool(getattr(settings, 'SUITABILITY_RPL_AI_ENABLED', False))})
        report = _current_report(kind, learner_id, context['key'])
        if report is not None:
            snapshot, versions = report.source_snapshot, report.source_versions
        else:
            snapshot, versions = build_source_snapshot(learner, kind, assessment, context)
        history = list(SuitabilityRplReport.objects.filter(learner_kind=kind, learner_id=learner_id)
                       .values('id', 'report_reference', 'programme_name', 'status', 'finalised_at').order_by('-updated_at')[:20])
        for item in history:
            item['id'] = str(item['id'])
            item['reportReference'] = item.pop('report_reference')
            item['programmeName'] = item.pop('programme_name')
            item['finalisedAt'] = item.pop('finalised_at').isoformat() if item['finalised_at'] else None
        return JsonResponse({'report': _report_json(report), 'evidence': snapshot, 'sourceVersions': versions,
                             'context': context, 'history': history,
                             'aiEnabled': bool(getattr(settings, 'SUITABILITY_RPL_AI_ENABLED', False))})
    except ReportInputError as exc:
        return JsonResponse({'error': str(exc)}, status=409 if 'another session' in str(exc) else 400)
    except DatabaseError:
        return JsonResponse({'error': 'The Suitability & RPL report store is unavailable.'}, status=503)


def _ai_input(report, draft_type):
    snapshot = report.source_snapshot or {}
    if draft_type == 'suitability':
        learner = snapshot.get('learner') if isinstance(snapshot.get('learner'), dict) else {}
        qualifications = ((snapshot.get('evidence') or {}).get('qualifications') or {})
        return {
            'programme': snapshot.get('programme', {}),
            # Deliberately omit the learner's name, reference, employer and all
            # uploaded-document metadata from the external drafting payload.
            'learnerEvidence': {
                'jobTitle': learner.get('jobTitle'),
                'responsibilities': learner.get('responsibilities', []),
            },
            'programmeUnderstanding': snapshot.get('programmeUnderstanding', []),
            'programmeSuitability': snapshot.get('programmeSuitability', {}),
            'skillsRadarSummary': [
                {'code': item.get('code'), 'level': item.get('level'), 'score': item.get('score')}
                for item in (snapshot.get('skillsRadar') or {}).get('items', [])
            ],
            'qualifications': {
                'status': qualifications.get('status'),
                'items': qualifications.get('items', []),
            },
        }
    learner = snapshot.get('learner') if isinstance(snapshot.get('learner'), dict) else {}
    evidence = snapshot.get('evidence') if isinstance(snapshot.get('evidence'), dict) else {}
    cv = evidence.get('cv') if isinstance(evidence.get('cv'), dict) else {}
    qualifications = evidence.get('qualifications') if isinstance(evidence.get('qualifications'), dict) else {}
    skills = snapshot.get('skillsRadar') if isinstance(snapshot.get('skillsRadar'), dict) else {}
    training = snapshot.get('trainingPlan') if isinstance(snapshot.get('trainingPlan'), dict) else {}
    cv_version = {'documentId': getattr(report, 'cv_source_document_id', ''), 'sha256': getattr(report, 'cv_source_blob_hash', ''), 'extractorVersion': getattr(report, 'cv_extractor_version', '')}
    return {
        'programme': snapshot.get('programme', {}),
        'learnerEvidence': {
            'jobTitle': learner.get('jobTitle'),
            'responsibilities': learner.get('responsibilities', []),
        },
        'cvEvidence': {
            'status': cv.get('status'),
            'extractedText': _private_cv_text(report, cv),
            'evidenceVersion': cv_version,
        },
        'qualifications': {
            'status': qualifications.get('status'),
            'items': qualifications.get('items', []),
        },
        'plrStatus': (evidence.get('plr') or {}),
        'skillsRadar': {
            'scaleMaximum': skills.get('scaleMaximum', 9),
            'catalogueVersion': skills.get('catalogueVersion'),
            'cataloguePinned': skills.get('cataloguePinned'),
            'summary': skills.get('summary', []),
            'items': [
                {
                    key: item.get(key)
                    for key in ('code', 'kind', 'level', 'description', 'moduleKey', 'score')
                    if key in item
                }
                for item in (skills.get('items') or [])
                if isinstance(item, dict)
            ],
        },
        'trainingPlan': {
            'available': training.get('available'),
            'source': training.get('source'),
            'plannedHours': training.get('plannedHours'),
            'modules': training.get('modules', []),
        },
        'relevantPriorLearning': report.relevant_prior_learning,
        'reductionJustified': report.reduction_justified,
        'assessorEvidenceReview': report.assessor_evidence_review,
        'noReductionReason': report.no_reduction_reason,
        'reductions': [
            {'module': row.module_title, 'plannedHours': float(row.planned_hours),
             'reducedHours': float(row.reduced_hours), 'ksbCodes': row.ksb_codes,
             'evidence': row.evidence, 'comments': row.assessor_comments}
            for row in report.reductions.all()
        ],
    }


def _private_cv_text(report, cv):
    """Resolve only the exact frozen CV source; never expose it in snapshots/API."""
    sources = cv.get('sourceDocuments') if isinstance(cv, dict) else []
    if not sources:
        return ''
    source = sources[0] if isinstance(sources[0], dict) else {}
    document_id = _s(source.get('id'))
    if not document_id:
        return ''
    source = sources[0] if isinstance(sources[0], dict) else {}
    try:
        current = extract_approved_cv(report.learner_kind, report.learner_id, source)
    except CvExtractionError as exc:
        raise ReportInputError(f'CV extraction is unavailable: {exc}') from exc
    row = SuitabilityCvExtraction.objects.filter(
        learner_kind=report.learner_kind, learner_id=report.learner_id,
        source_document_id=document_id, source_blob_hash=current.source_blob_hash,
        status=SuitabilityCvExtraction.STATUS_READY,
    ).order_by('-extracted_at').first()
    if not row:
        raise ReportInputError('The approved CV has not been extracted.')
    if report.cv_source_blob_hash and report.cv_source_blob_hash != row.source_blob_hash:
        raise ReportInputError('The approved CV changed. Refresh the evidence and review the RPL justification again.')
    if report.cv_source_document_id and report.cv_source_document_id != document_id:
        raise ReportInputError('The approved CV source changed. Refresh the evidence and review the RPL justification again.')
    report.cv_source_document_id = row.source_document_id
    report.cv_source_container = row.source_container
    report.cv_source_blob_name = row.source_blob_name
    report.cv_source_blob_hash = row.source_blob_hash
    report.cv_extractor_version = row.extractor_version
    return row.extracted_text


def generate_ai_justification(report, draft_type):
    if not getattr(settings, 'SUITABILITY_RPL_AI_ENABLED', False):
        raise ReportInputError('AI drafting is disabled for this environment.')
    if not getattr(settings, 'OPENAI_API_KEY', ''):
        raise ReportInputError('The approved AI service is not configured.')
    if draft_type == 'rpl' and (report.relevant_prior_learning is None or report.reduction_justified is None):
        raise ReportInputError('Save the assessor prior-learning decision before generating the RPL draft.')
    if draft_type == 'rpl' and not getattr(report, 'cv_extraction_acknowledged', False):
        _ai_input(report, draft_type)  # forces current approved-CV extraction or a hard failure
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise ReportInputError('The approved AI client is unavailable.') from exc
    payload = _ai_input(report, draft_type)
    system = (
        'Draft one concise, evidence-based UK apprenticeship assessment justification. '
        'Use only the supplied JSON. Do not invent facts, infer verified learning from a job title or self-assessment, '
        'make a final decision, change modules, change hours, or treat unavailable PLR as evidence of no prior learning. '
        'Return JSON with one key: justification.'
    )
    purpose = ('Explain why the selected programme is suitable, while making clear that an assessor must approve it.'
               if draft_type == 'suitability' else
               'Explain the assessor-entered RPL decision and training adjustment exactly as supplied.')
    model = getattr(settings, 'OPENAI_MODEL', 'gpt-4.1-mini')
    response = OpenAI(api_key=settings.OPENAI_API_KEY).chat.completions.create(
        model=model, messages=[{'role': 'system', 'content': system},
                               {'role': 'user', 'content': f'{purpose}\n\nEvidence JSON:\n{json.dumps(payload, ensure_ascii=False)}'}],
        response_format={'type': 'json_object'}, max_completion_tokens=1800,
    )
    content = _s(response.choices[0].message.content if response.choices else '')
    try:
        result = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ReportInputError('The AI service returned an invalid draft.') from exc
    justification = _s(result.get('justification')) if isinstance(result, dict) else ''
    if not justification or len(justification) > MAX_TEXT:
        raise ReportInputError('The AI service returned an empty or oversized draft.')
    version = payload.get('cvEvidence', {}).get('evidenceVersion', {}) if draft_type == 'rpl' else {}
    return justification, {'provider': 'openai', 'model': model, 'generatedAt': timezone.now().isoformat(),
                           'inputHash': _json_hash(payload), 'cvEvidenceVersion': version, 'generatedText': justification}


@require_role('admin')
@csrf_protect
def suitability_rpl_ai(request, kind, learner_id, draft_type):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    if draft_type not in {'suitability', 'rpl'}:
        return JsonResponse({'error': 'Unknown AI draft type.'}, status=404)
    learner = _learner(kind, learner_id)
    if learner is None:
        return JsonResponse({'error': 'Learner not found.'}, status=404)
    context = _context(learner, kind, _programme_assessment(learner_id))
    try:
        with transaction.atomic():
            report = SuitabilityRplReport.objects.select_for_update().prefetch_related('reductions').filter(
                learner_kind=kind, learner_id=learner_id, context_key=context['key'], status=SuitabilityRplReport.STATUS_DRAFT,
            ).first()
            if report is None:
                raise ReportInputError('Save the report draft before generating an AI justification.')
            justification, metadata = generate_ai_justification(report, draft_type)
            setattr(report, f'{draft_type}_justification', justification)
            setattr(report, f'{draft_type}_ai_metadata', metadata)
            setattr(report, f'{draft_type}_reviewed_by', None)
            setattr(report, f'{draft_type}_reviewed_by_name', '')
            setattr(report, f'{draft_type}_reviewed_at', None)
            report.updated_by = _actor_id(request)
            report.revision += 1
            report.save()
        report = SuitabilityRplReport.objects.prefetch_related('reductions').get(pk=report.pk)
        return JsonResponse({'report': _report_json(report)})
    except ReportInputError as exc:
        return JsonResponse({'error': str(exc)}, status=503 if 'disabled' in str(exc) or 'configured' in str(exc) else 400)
    except DatabaseError:
        return JsonResponse({'error': 'The report draft could not be updated.'}, status=503)
