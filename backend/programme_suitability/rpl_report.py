"""Admin-only Suitability & RPL report aggregation and review workflow."""
from __future__ import annotations

import hashlib
import base64
import binascii
import json
import re
import uuid
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from urllib.parse import urlencode

from django.conf import settings
from django.db import DatabaseError, connections, transaction
from django.http import HttpResponse, JsonResponse
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
from login.permissions import login_required
from enrolment_api import auth as enrolment_auth
from enrolment_api.document_templates.models import IssuedTemplate, RenderHistory
from enrolment_api.document_templates.pdf_artifacts import RPL_PDF_BINDING, issued_url, rpl_issued_pdf
from enrolment_api.document_templates import service as document_service

from .configuration import PROGRAMMES as SUITABILITY_PROGRAMMES
from .models import Assessment, SuitabilityCvExtraction, SuitabilityRplDocumentSignature, SuitabilityRplReduction, SuitabilityRplReport
from .cv_extraction import CvExtractionError, extract_approved_cv
from .server_pdf import render_finalised_report, render_report

MAX_TEXT = 20_000
QUESTION_ONE = 'What is your understanding of the programme you are applying for?'
QUESTION_TWO = 'How will this programme help you in your career development/aspirations, and/or with your progression?'
RPL_ANALYSIS_OUTCOMES = {
    'no-relevant-prior-learning',
    'potential-whole-module-reduction',
    'relevant-partial-no-exemption',
    'insufficient-evidence',
}


class ReportInputError(ValueError):
    pass


class ReportContextConflict(ReportInputError):
    pass


REPORT_DOCUMENT_KEY = 'suitability-rpl-report'
REPORT_SIGNATURE_ROLES = ['admin', 'learner', 'employer']
SIGNATURE_MAX_CHARS = 400_000
SIGNATURE_CONSENT_VERSION = 'suitability-rpl-sign-v1'


def _read_pdf_payload(request):
    if len(request.body) > 16 * 1024 * 1024:
        raise ReportInputError('The report PDF is too large.')
    try:
        data = json.loads(request.body or b'{}')
    except (ValueError, UnicodeDecodeError) as exc:
        raise ReportInputError('Provide a valid JSON object.') from exc
    if not isinstance(data, dict):
        raise ReportInputError('Provide a JSON object.')
    encoded = data.get('pdf')
    if not isinstance(encoded, str) or len(encoded) > 16 * 1024 * 1024:
        raise ReportInputError('Provide a valid report PDF.')
    try:
        pdf = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ReportInputError('Provide a valid report PDF.') from exc
    if not pdf.startswith(b'%PDF-') or b'%%EOF' not in pdf[-2048:]:
        raise ReportInputError('Provide a valid report PDF.')
    return data, encoded, pdf


def _read_issue_request(request):
    """Read issuance metadata; PDF bytes are deliberately never trusted."""
    if len(request.body) > 16 * 1024 * 1024:
        raise ReportInputError('The report issuance request is too large.')
    try:
        data = json.loads(request.body or b'{}')
    except (ValueError, UnicodeDecodeError) as exc:
        raise ReportInputError('Provide a valid JSON object.') from exc
    if not isinstance(data, dict):
        raise ReportInputError('Provide a JSON object.')
    return data


def _issue_source_id(report):
    return f'report-{report.pk}-v{report.report_version}-r{report.revision}-server-v1'


def _signature_issue(learner_id, issue_id):
    issue = IssuedTemplate.objects.filter(
        pk=issue_id, document_key=REPORT_DOCUMENT_KEY, learner_id=learner_id,
    ).first()
    if issue is None:
        return None, None
    artifact = RenderHistory.objects.filter(
        issue=issue, payload__kind='suitability-rpl-issued-pdf-v1',
    ).first()
    return issue, artifact


def _signature_caller(request, learner_id, role):
    account = getattr(request, 'login_account', None)
    if account is None or not account.is_active:
        return None
    if role == SuitabilityRplDocumentSignature.ROLE_ADMIN:
        if account.role == 'admin':
            return account
        if account.role == 'staff':
            from learner_api.constants import ACCESS_ENROLMENT, ACCESS_SUPER_ADMIN
            accesses = enrolment_auth._accesses_of(account)
            return account if accesses & {ACCESS_ENROLMENT, ACCESS_SUPER_ADMIN} else None
        return None
    if role == SuitabilityRplDocumentSignature.ROLE_LEARNER:
        return account if account.role == 'learner' and account.subject_type == 'learner' and account.subject_id == learner_id else None
    if role == SuitabilityRplDocumentSignature.ROLE_EMPLOYER:
        return account if account.role == 'employer' and account.subject_type == 'employer' and enrolment_auth._employs(account, learner_id) else None
    return None


def _signature_status(issue, artifact):
    if issue is None or artifact is None:
        return None
    rows = list(SuitabilityRplDocumentSignature.objects.filter(issued_document_id=issue.pk).order_by('signer_role'))
    signed = {row.signer_role: {
        'signed': True,
        'name': row.signer_name,
        'signedAt': row.signed_at.isoformat() if row.signed_at else None,
        'method': row.signature_method,
    } for row in rows}
    binding = {name: artifact.payload.get(name) for name in RPL_PDF_BINDING}
    return {
        'issueId': str(issue.pk),
        **binding,
        'pdfSha256': artifact.payload.get('sha256'),
        'requiredRoles': REPORT_SIGNATURE_ROLES,
        'signatures': {role: signed.get(role, {'signed': False, 'name': '', 'signedAt': None, 'method': ''}) for role in REPORT_SIGNATURE_ROLES},
        'fullySigned': all(role in signed for role in REPORT_SIGNATURE_ROLES),
        'pdfUrl': f'{issued_url(issue.learner_id, issue.pk)}?{urlencode(binding)}',
        'signingEnabled': issue.template_snapshot.get('pdfTrust') == 'server-rendered-v1',
        'signingStatus': issue.template_snapshot.get('signingStatus', 'unknown'),
    }


@login_required
def suitability_rpl_issued_documents(request, kind, learner_id):
    """List immutable issued report versions visible to the current party.

    This is deliberately read-only and returns envelope metadata only.  The
    PDF endpoint remains the sole way to retrieve the stored bytes, and the
    report/evidence APIs remain staff-only.
    """
    if request.method != 'GET' or kind not in {'apprenticeship', 'commercial'}:
        return JsonResponse({'error': 'Not found.'}, status=404)
    account = getattr(request, 'login_account', None)
    if account is None or not account.is_active:
        return JsonResponse({'error': 'Authentication required.'}, status=401)
    if account.role == 'learner' and not (account.subject_type == 'learner' and account.subject_id == learner_id):
        return JsonResponse({'error': 'Not found.'}, status=404)
    if account.role == 'employer' and not enrolment_auth._employs(account, learner_id):
        return JsonResponse({'error': 'Not found.'}, status=404)
    if account.role in {'staff', 'admin'} and _signature_caller(request, learner_id, SuitabilityRplDocumentSignature.ROLE_ADMIN) is None:
        return JsonResponse({'error': 'You do not have permission to view this document.'}, status=403)
    issues = []
    for issue in IssuedTemplate.objects.filter(
        document_key=REPORT_DOCUMENT_KEY, learner_kind=kind, learner_id=learner_id,
    ).order_by('-issued_at'):
        artifact = RenderHistory.objects.filter(issue=issue, payload__kind='suitability-rpl-issued-pdf-v1').first()
        status = _signature_status(issue, artifact)
        if status is None:
            continue
        issues.append({
            'issueId': status['issueId'],
            'title': 'Suitability & RPL Report',
            'issuedAt': issue.issued_at.isoformat() if issue.issued_at else None,
            'reportId': status['reportId'],
            'reportVersion': status['reportVersion'],
            'reportRevision': status['reportRevision'],
            'pdfSha256': status['pdfSha256'],
            'pdfUrl': status['pdfUrl'],
            'requiredRoles': status['requiredRoles'],
            'signatures': status['signatures'],
            'fullySigned': status['fullySigned'],
            'signingEnabled': status['signingEnabled'],
            'signingStatus': status['signingStatus'],
        })
    return JsonResponse({'documents': issues})


def _normalise_suitability_narrative(value):
    text = _s(value).replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'[ \t]*\n[ \t]*', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _narrative_diagnostics(text, finish_reason=None, usage=None):
    paragraphs = [part.strip() for part in re.split(r'\n\s*\n+', text) if part.strip()]
    usage_dict = usage if isinstance(usage, dict) else {}
    total_tokens = usage_dict.get('total_tokens', 'unknown')
    reason = finish_reason or 'unknown'
    return f'(paragraphs={len(paragraphs)}, length={len(text)}, finish_reason={reason}, total_tokens={total_tokens})'


def _validate_suitability_narrative(value):
    """Reject drafts that are technically valid JSON but not report-ready.

    The provider can return ``finish_reason=stop`` after producing an otherwise
    incomplete string.  Suitability drafts are shown to an assessor, so keep
    malformed or internally-oriented text out of the persisted report.
    """
    text = _normalise_suitability_narrative(value)
    if not text:
        raise ReportInputError('The AI service returned an empty suitability draft.')
    if '\ufffd' in text:
        raise ReportInputError('The AI service returned invalid replacement characters.')
    if len(text) > MAX_TEXT:
        raise ReportInputError('The AI service returned an oversized suitability draft.')
    if re.search(r'\b(?:programmeSpecification|learnerEvidence|programmeUnderstanding|'
                 r'selectedProgrammeSpecification|skillsRadarSummary|cvEvidence|'
                 r'sourceSnapshot|metadata)\b', text, re.IGNORECASE):
        raise ReportInputError('The AI service returned technical or internal field names.')
    if re.search(r'\b(?:json|placeholder|workflow|technical details?|internal field|'
                 r'assessor must (?:review|approve)|approval instructions?)\b', text, re.IGNORECASE):
        raise ReportInputError('The AI service returned internal workflow commentary.')
    if re.search(r'(?:\be\.g\.|for example|such as|including)\s*[,;:]?\s*$', text, re.IGNORECASE):
        raise ReportInputError('The AI service returned an unfinished suitability narrative.')
    if text[-1] not in '.!?\u201d\u2019"\')\u005d}':
        raise ReportInputError('The AI service returned an unfinished suitability narrative.')
    paragraphs = [part.strip() for part in re.split(r'\n\s*\n+', text) if part.strip()]
    if not 1 <= len(paragraphs) <= 3:
        raise ReportInputError('The AI service returned a suitability draft with an invalid paragraph structure.')
    if sum(len(re.findall(r'[.!?](?:\s|$)', paragraph)) for paragraph in paragraphs) < 1:
        raise ReportInputError('The AI service returned an incomplete suitability narrative.')
    return text


def _validate_rpl_narrative(value, report, payload):
    """Keep RPL drafts report-ready and aligned with the recorded decision."""
    text = _normalise_suitability_narrative(value)
    if not text:
        raise ReportInputError('The AI service returned an empty RPL draft.')
    if '\ufffd' in text:
        raise ReportInputError('The AI service returned invalid replacement characters.')
    if len(text) > MAX_TEXT:
        raise ReportInputError('The AI service returned an oversized RPL draft.')
    if re.search(r'\b(?:json|metadata|sourceSnapshot|rplAiMetadata|cvEvidence|'
                 r'learnerEvidence|trainingPlan|reductions?\s+array|internal field|'
                 r'workflow|prompt|artificial intelligence|\bAI\b)\b', text, re.IGNORECASE):
        raise ReportInputError('The AI service returned technical or internal workflow language.')
    if text[-1] not in '.!?\u201d\u2019"\')\u005d}':
        raise ReportInputError('The AI service returned an unfinished RPL narrative.')
    paragraphs = [part.strip() for part in re.split(r'\n\s*\n+', text) if part.strip()]
    if not 1 <= len(paragraphs) <= 3:
        raise ReportInputError('The AI service returned an RPL draft with an invalid paragraph structure.')
    if sum(len(re.findall(r'[.!?](?:\s|$)', paragraph)) for paragraph in paragraphs) < 1:
        raise ReportInputError('The AI service returned an incomplete RPL narrative.')

    relevant = getattr(report, 'relevant_prior_learning', None)
    justified = getattr(report, 'reduction_justified', None)
    lowered = text.casefold()
    if relevant is False and justified is False:
        if re.search(r'\b(?:approved|authorised|granted|confirmed)\s+(?:a\s+|an\s+|the\s+)?'
                     r'(?:module\s+)?(?:reduction|exemption|credit)\b|'
                     r'\b(?:modules?|hours?)\s+were\s+reduced\b|'
                     r'\b(?:a|an|the)\s+(?:module\s+)?reduction\s+is\s+'
                     r'(?:approved|justified|authorised)\b', lowered):
            raise ReportInputError('The RPL draft contradicts the recorded no-reduction decision.')

    modules = ((payload.get('trainingPlan') or {}).get('modules') or []) if isinstance(payload, dict) else []
    authoritative_ksbs = {
        _s(code)
        for module in modules
        if isinstance(module, dict)
        for code in (module.get('ksbCodes') or [])
        if _s(code)
    }
    mentioned_codes = {code.casefold() for code in re.findall(r'\b[KSBI]\d+[A-Z]?\b', text, re.IGNORECASE)}
    if mentioned_codes - {code.casefold() for code in authoritative_ksbs}:
        raise ReportInputError('The RPL draft mentions KSB codes without an authoritative mapping.')
    if not authoritative_ksbs and re.search(r'\b(?:mapped|covered)\s+KSBs?\b', lowered):
        raise ReportInputError('The RPL draft claims KSB mappings that are not available.')
    employer_evidence = payload.get('employerEvidence') if isinstance(payload, dict) else None
    if not employer_evidence and re.search(
        r'\b(?:the\s+)?employer\s+(?:agreed|confirmed|approved|signed|endorsed|verified)\b|'
        r'\bemployer\s+(?:discussion|agreement|sign[- ]off)\s+(?:confirmed|recorded|completed)\b',
        lowered,
    ):
        raise ReportInputError('The RPL draft claims employer evidence that is not recorded.')
    return text


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


def _report_context_key(kind, learner_id, context, reference):
    return _json_hash([
        kind, int(learner_id), context['_sourceProgrammeId'], context['programmeName'],
        reference, context['practicalStartDate'],
    ])


def _context(learner, kind, assessment):
    source_programme_id = _s(getattr(assessment, 'selected_programme', '')) or _s(learner.programme)
    programme_id = (source_programme_id if source_programme_id in PROGRAMMES else programme_for(learner, kind)) or source_programme_id
    programme_name = _s(learner.programme) or source_programme_id
    # The displayed Aptem fallback is not the provider reference in the key.
    source_reference = _s(getattr(learner, 'reference_number', ''))
    reference = source_reference or _s(getattr(learner, 'aptem_id', ''))
    practical_start = _s(learner.learner_start_date) or _s(learner.start_date)
    context = {
        'programmeId': programme_id,
        'programmeName': programme_name,
        'enrolmentReference': reference,
        'practicalStartDate': practical_start,
        '_sourceProgrammeId': source_programme_id,
        '_referenceNumber': source_reference,
    }
    context['key'] = _report_context_key(kind, learner.pk, context, source_reference)
    return context


def _reports_for_context(kind, learner_id, context):
    reports = SuitabilityRplReport.objects.filter(learner_kind=kind, learner_id=learner_id)
    exact = reports.filter(context_key=context['key'])
    if exact.exists():
        return exact

    legacy_key = _report_context_key(kind, learner_id, context, '')
    if context['_referenceNumber'] and not reports.filter(context_key=legacy_key).exists():
        return exact

    # A stored reference only identifies this enrolment if it reproduces the
    # original key. In particular, a displayed Aptem fallback does not count.
    candidates = reports.filter(
        programme_name=context['programmeName'], practical_start_date=context['practicalStartDate'],
    ).exclude(enrolment_reference='').values_list('context_key', 'enrolment_reference').distinct()
    referenced_keys = {
        key for key, reference in candidates
        if key == _report_context_key(kind, learner_id, context, reference)
    }
    if context['_referenceNumber']:
        if not referenced_keys:
            return reports.filter(context_key=legacy_key)
    elif len(referenced_keys) == 1:
        return reports.filter(context_key=referenced_keys.pop())
    elif not referenced_keys:
        return exact
    raise ReportContextConflict('Multiple saved enrolment contexts match this learner. Confirm the learner reference before using or creating a report.')


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
                for key in (
                    'id', 'title', 'summary', 'description', 'pathway', 'level', 'responsibilities',
                    # Preserve authoritative outcomes when a published
                    # programme definition supplies them.  Older layouts do
                    # not contain these keys and remain unchanged.
                    'outcomes', 'ksbMappings', 'knowledge', 'skills', 'behaviours', 'behaviors',
                )
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
        'parentReportId': str(report.parent_report_id) if report.parent_report_id else None,
        'amendmentReason': report.amendment_reason,
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
        if not isinstance(evidence, str) or len(evidence) > MAX_TEXT:
            raise ReportInputError('Reduction evidence must be text.')
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
    if not report.suitability_justification or not report.suitability_reviewed_at:
        raise ReportInputError('Review and approve the programme suitability justification before finalising.')
    if report.relevant_prior_learning is None or report.reduction_justified is None:
        raise ReportInputError('Complete the prior learning decision before finalising.')
    if report.reduction_justified and not reductions:
        raise ReportInputError('Add at least one validated module reduction before finalising.')
    if not report.rpl_justification or not report.rpl_reviewed_at:
        raise ReportInputError('Review and approve the RPL justification before finalising.')


def _save_report(request, learner, kind, context, assessment, data):
    with transaction.atomic():
        reports = _reports_for_context(kind, learner.pk, context).select_for_update()
        report = reports.filter(status=SuitabilityRplReport.STATUS_DRAFT).first()
        if report is None:
            finalised = reports.filter(status=SuitabilityRplReport.STATUS_FINALISED).order_by('-report_version', '-updated_at').first()
            if finalised is not None and data.get('newVersion') is not True:
                raise ReportInputError(
                    'This report is finalised and immutable. Start a new report version explicitly to reassess it.'
                )
            if finalised is not None and not _text(data, 'amendmentReason'):
                raise ReportInputError('Provide an amendment reason before creating a new report version.')
            snapshot, versions = build_source_snapshot(learner, kind, assessment, context)
            report = SuitabilityRplReport(
                learner_kind=kind, learner_id=learner.pk, context_key=finalised.context_key if finalised else context['key'],
                programme_id=context['programmeId'], programme_name=context['programmeName'],
                enrolment_reference=context['enrolmentReference'], practical_start_date=context['practicalStartDate'],
                report_reference=f'RPL-{learner.pk}-{uuid.uuid4().hex[:8].upper()}',
                report_version=(finalised.report_version + 1) if finalised is not None else 1,
                source_snapshot=snapshot, source_versions=versions, source_snapshot_hash=_json_hash(snapshot),
                created_by=_actor_id(request), revision=0, parent_report=finalised,
                amendment_reason=_text(data, 'amendmentReason'),
            )
            if finalised is not None:
                for field in ('relevant_prior_learning', 'reduction_justified', 'assessor_evidence_review', 'no_reduction_reason', 'suitability_outcome', 'final_summary', 'suitability_justification', 'rpl_justification'):
                    setattr(report, field, getattr(finalised, field))
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
        reductions = _validated_reductions(data, report.source_snapshot, relevant, justified)

        # Changing the human RPL outcome invalidates any previously approved
        # justification.  Keep the narrative for assessor editing, but require
        # it to be reviewed again against the new decision and reductions.
        decision_changed = (
            report.relevant_prior_learning != relevant
            or report.reduction_justified != justified
        )

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
        amendment_draft = report.parent_report_id is not None and report.revision == 0
        if amendment_draft:
            data = {**data, 'suitabilityReviewed': False, 'rplReviewed': False}
        _review_state(report, data, 'suitability', suitability_changed, request)
        _review_state(report, data, 'rpl', rpl_changed, request)
        if decision_changed:
            report.rpl_reviewed_by = None
            report.rpl_reviewed_by_name = ''
            report.rpl_reviewed_at = None
        if report.status == SuitabilityRplReport.STATUS_DRAFT and report.parent_report_id is None and data.get('rplReviewed') is True and (report.source_snapshot.get('evidence', {}).get('cv', {}).get('status') == 'provided') and not report.cv_extraction_acknowledged:
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


def _current_report(kind, learner_id, context):
    return _reports_for_context(kind, learner_id, context).prefetch_related('reductions').order_by(
        'status', '-report_version', '-updated_at',
    ).first()


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
        context_error = None
        try:
            report = _current_report(kind, learner_id, context)
        except ReportContextConflict as exc:
            report, context_error = None, str(exc)
        if report is not None:
            snapshot, versions = report.source_snapshot, report.source_versions
        else:
            snapshot, versions = build_source_snapshot(learner, kind, assessment, context)
        history = list(SuitabilityRplReport.objects.filter(learner_kind=kind, learner_id=learner_id)
                       .values('id', 'report_reference', 'programme_name', 'status', 'report_version', 'amendment_reason', 'parent_report_id', 'finalised_at').order_by('-report_version', '-updated_at')[:20])
        for item in history:
            item['id'] = str(item['id'])
            item['reportReference'] = item.pop('report_reference')
            item['programmeName'] = item.pop('programme_name')
            item['reportVersion'] = item.pop('report_version')
            item['amendmentReason'] = item.pop('amendment_reason')
            item['parentReportId'] = str(item.pop('parent_report_id')) if item.get('parent_report_id') else None
            item['finalisedAt'] = item.pop('finalised_at').isoformat() if item['finalised_at'] else None
        public_context = {key: value for key, value in context.items() if not key.startswith('_')}
        if report is not None:
            public_context['key'] = report.context_key
        return JsonResponse({'report': _report_json(report), 'evidence': snapshot, 'sourceVersions': versions,
                             'context': public_context, 'contextError': context_error, 'history': history,
                             'aiEnabled': bool(getattr(settings, 'SUITABILITY_RPL_AI_ENABLED', False))})
    except ReportContextConflict as exc:
        return JsonResponse({'error': str(exc)}, status=409)
    except ReportInputError as exc:
        return JsonResponse({'error': str(exc)}, status=409 if 'another session' in str(exc) else 400)
    except DatabaseError:
        return JsonResponse({'error': 'The Suitability & RPL report store is unavailable.'}, status=503)


@require_role('admin')
@csrf_protect
def preview_suitability_rpl_report(request, kind, learner_id):
    """Render a non-persistent preview through the canonical server pipeline."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    learner = _learner(kind, learner_id)
    if learner is None:
        return JsonResponse({'error': 'Learner not found.'}, status=404)
    try:
        assessment = _programme_assessment(learner_id)
        context = _context(learner, kind, assessment)
        saved = _current_report(kind, learner_id, context)
        if saved is not None and saved.status == SuitabilityRplReport.STATUS_FINALISED:
            issue = IssuedTemplate.objects.filter(
                document_key=REPORT_DOCUMENT_KEY, source_id=_issue_source_id(saved), learner_id=learner_id, learner_kind=kind,
            ).first()
            if issue is not None:
                artifact = RenderHistory.objects.filter(issue=issue, payload__kind='suitability-rpl-issued-pdf-v1').first()
                response = rpl_issued_pdf(issue, artifact, {
                    'reportId': saved.pk, 'reportVersion': saved.report_version, 'reportRevision': saved.revision,
                })
                return response if response is not None else JsonResponse({'error': 'This exact saved PDF is unavailable.'}, status=409)
            pdf = render_finalised_report(saved)
        else:
            pdf = _render_draft_preview(request, learner, kind, assessment, context, saved)
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = 'inline; filename="Suitability-and-RPL-Report.pdf"'
        response['Cache-Control'] = 'no-store'
        return response
    except ReportContextConflict as exc:
        return JsonResponse({'error': str(exc)}, status=409)
    except ReportInputError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    except DatabaseError:
        return JsonResponse({'error': 'The Suitability & RPL report store is unavailable.'}, status=503)
    except Exception:
        return JsonResponse({'error': 'The Suitability & RPL PDF could not be generated.'}, status=500)


def _render_draft_preview(request, learner, kind, assessment, context, saved):
    data = _read_body(request)
    if saved is not None:
        snapshot = saved.source_snapshot if isinstance(saved.source_snapshot, dict) else {}
    else:
        snapshot, _versions = build_source_snapshot(learner, kind, assessment, context)

    relevant = _nullable_bool(data, 'relevantPriorLearning')
    justified = _nullable_bool(data, 'reductionJustified')
    reductions = _validated_reductions(data, snapshot, relevant, justified)
    outcome = data.get('suitabilityOutcome', SuitabilityRplReport.SUITABILITY_NOT_ASSESSED)
    if outcome not in dict(SuitabilityRplReport.SUITABILITY_CHOICES):
        raise ReportInputError('Choose a valid suitability outcome.')
    suitability_reviewed = data.get('suitabilityReviewed', False)
    rpl_reviewed = data.get('rplReviewed', False)
    if not isinstance(suitability_reviewed, bool) or not isinstance(rpl_reviewed, bool):
        raise ReportInputError('Reviewed values must be true or false.')

    reduction_rows = [SimpleNamespace(
        module_id=item['moduleId'], module_title=item['moduleTitle'],
        planned_hours=item['plannedHours'], reduced_hours=item['reducedHours'],
        ksb_codes=item['ksbCodes'], evidence=item['evidence'],
        assessor_comments=item['assessorComments'],
    ) for item in reductions]
    preview = SimpleNamespace(
        status=getattr(saved, 'status', SuitabilityRplReport.STATUS_DRAFT),
        report_reference=getattr(saved, 'report_reference', 'DRAFT'),
        report_version=getattr(saved, 'report_version', 1),
        revision=getattr(saved, 'revision', 0),
        practical_start_date=context['practicalStartDate'],
        source_snapshot=snapshot,
        relevant_prior_learning=relevant,
        reduction_justified=justified,
        assessor_evidence_review=_text(data, 'assessorEvidenceReview'),
        no_reduction_reason=_text(data, 'noReductionReason'),
        suitability_outcome=outcome,
        final_summary=_text(data, 'finalSummary'),
        suitability_justification=_text(data, 'suitabilityJustification'),
        suitability_reviewed_at=timezone.now() if suitability_reviewed else None,
        rpl_justification=_text(data, 'rplJustification'),
        rpl_reviewed_at=timezone.now() if rpl_reviewed else None,
        finalised_at=getattr(saved, 'finalised_at', None),
        reductions=SimpleNamespace(all=lambda: reduction_rows),
    )
    return render_report(preview)


@require_role('admin')
@csrf_protect
def issue_suitability_rpl_report(request, kind, learner_id):
    """Store one immutable, server-rendered PDF for a finalised report revision.

    Existing artifacts are returned before rendering so a renderer upgrade can
    never overwrite or reinterpret an already-issued historical PDF.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    learner = _learner(kind, learner_id)
    if learner is None:
        return JsonResponse({'error': 'Learner not found.'}, status=404)
    try:
        data = _read_issue_request(request)
        report_id = data.get('reportId')
        try:
            report_id = int(report_id)
        except (TypeError, ValueError) as exc:
            raise ReportInputError('Provide the finalised report id.') from exc
        report = SuitabilityRplReport.objects.prefetch_related('reductions').filter(
            pk=report_id, learner_kind=kind, learner_id=learner_id,
        ).first()
        if report is None:
            return JsonResponse({'error': 'Report not found.'}, status=404)
        if report.status != SuitabilityRplReport.STATUS_FINALISED:
            return JsonResponse({'error': 'Only a finalised report can be issued.'}, status=409)
        if data.get('reportRevision') != report.revision or data.get('reportVersion') != report.report_version:
            return JsonResponse({'error': 'The report changed before issuance. Reload the finalised report and retry.'}, status=409)
        if not report.suitability_reviewed_at or not report.rpl_reviewed_at:
            return JsonResponse({'error': 'Both approved justifications are required before issuance.'}, status=409)
        source_id = _issue_source_id(report)
        existing = IssuedTemplate.objects.filter(document_key=REPORT_DOCUMENT_KEY, source_id=source_id).first()
        if existing is not None:
            stored = RenderHistory.objects.filter(issue=existing, payload__kind='suitability-rpl-issued-pdf-v1').first()
            if stored is None or not stored.payload.get('sha256'):
                return JsonResponse({'error': 'This report revision is already issued and immutable.'}, status=409)
            return JsonResponse({'issued': True, 'issueId': str(existing.pk), 'sha256': stored.payload['sha256'], 'alreadyIssued': True, 'signingEnabled': True})
        try:
            pdf = render_finalised_report(report)
        except Exception as exc:
            raise DatabaseError('The trusted report PDF could not be generated. No document was issued.') from exc
        sha256 = hashlib.sha256(pdf).hexdigest()
        encoded = base64.b64encode(pdf).decode()
        snapshot = {
            'dynamic': True,
            'documentKey': REPORT_DOCUMENT_KEY,
            'title': 'Suitability & Recognition of Prior Learning Report',
            'reportId': str(report.pk),
            'reportReference': report.report_reference,
            'reportVersion': report.report_version,
            'reportRevision': report.revision,
            'reportSnapshotHash': report.source_snapshot_hash,
            'pdfSha256': sha256,
            'signatureRoles': REPORT_SIGNATURE_ROLES,
            'signingStatus': 'pending',
            'pdfTrust': 'server-rendered-v1',
            'rendererVersion': 'suitability-rpl-canonical-v3',
            'originalPdfVersion': 4,
        }
        with transaction.atomic(using=document_service.database()):
            existing = IssuedTemplate.objects.filter(document_key=REPORT_DOCUMENT_KEY, source_id=source_id).first()
            if existing is not None:
                stored = RenderHistory.objects.filter(issue=existing, payload__kind='suitability-rpl-issued-pdf-v1').first()
                if stored is None or not stored.payload.get('sha256'):
                    return JsonResponse({'error': 'This report revision is already issued and immutable.'}, status=409)
                return JsonResponse({'issued': True, 'issueId': str(existing.pk), 'sha256': stored.payload['sha256'], 'alreadyIssued': True, 'signingEnabled': True})
            issue = IssuedTemplate.objects.create(
                document_key=REPORT_DOCUMENT_KEY,
                source_id=source_id,
                learner_kind=kind,
                learner_id=learner_id,
                version=None,
                template_snapshot=snapshot,
                field_values={},
                review_snapshot={'reportId': str(report.pk), 'reportRevision': report.revision, 'reportSnapshotHash': report.source_snapshot_hash},
            )
            RenderHistory.objects.create(
                issue=issue,
                fingerprint=document_service.digest({'kind': 'suitability-rpl-issued-pdf-v1', 'sha256': sha256, 'sourceId': source_id}),
                payload={
                    'kind': 'suitability-rpl-issued-pdf-v1',
                    'pdf': encoded,
                    'sha256': sha256,
                    'reportId': str(report.pk),
                    'reportVersion': report.report_version,
                    'reportRevision': report.revision,
                    'reportSnapshotHash': report.source_snapshot_hash,
                },
                recorded_by=document_service.actor(request),
            )
        return JsonResponse({'issued': True, 'issueId': str(issue.pk), 'sha256': sha256, 'alreadyIssued': False, 'signingEnabled': True}, status=201)
    except ReportInputError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    except DatabaseError:
        return JsonResponse({'error': 'The Suitability & RPL document store is unavailable.'}, status=503)


@login_required
@csrf_protect
def suitability_rpl_signatures(request, kind, learner_id, issue_id):
    """Read the signing envelope or record one authenticated party signature."""
    if kind not in {'apprenticeship', 'commercial'}:
        return JsonResponse({'error': 'Not found.'}, status=404)
    issue, artifact = _signature_issue(learner_id, issue_id)
    if issue is None or artifact is None or issue.learner_kind != kind:
        return JsonResponse({'error': 'Not found.'}, status=404)
    account = getattr(request, 'login_account', None)
    if account is None:
        return JsonResponse({'error': 'Authentication required.'}, status=401)
    if account.role == 'learner' and account.subject_id != learner_id:
        return JsonResponse({'error': 'Not found.'}, status=404)
    if account.role == 'employer' and not enrolment_auth._employs(account, learner_id):
        return JsonResponse({'error': 'Not found.'}, status=404)
    if account.role not in {'admin', 'staff', 'learner', 'employer'}:
        return JsonResponse({'error': 'You do not have permission to sign this document.'}, status=403)
    if account.role == 'staff' and _signature_caller(request, learner_id, SuitabilityRplDocumentSignature.ROLE_ADMIN) is None:
        return JsonResponse({'error': 'You do not have permission to sign this document.'}, status=403)
    if request.method == 'GET':
        return JsonResponse({'status': _signature_status(issue, artifact)})
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    try:
        data = json.loads(request.body or b'{}')
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'error': 'Provide a valid JSON object.'}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({'error': 'Provide a JSON object.'}, status=400)
    role = data.get('role')
    expected_role = {
        'admin': SuitabilityRplDocumentSignature.ROLE_ADMIN,
        'staff': SuitabilityRplDocumentSignature.ROLE_ADMIN,
        'learner': SuitabilityRplDocumentSignature.ROLE_LEARNER,
        'employer': SuitabilityRplDocumentSignature.ROLE_EMPLOYER,
    }.get(account.role)
    if role != expected_role or _signature_caller(request, learner_id, role) is None:
        return JsonResponse({'error': 'You cannot sign this document as that party.'}, status=403)
    if issue.template_snapshot.get('pdfTrust') != 'server-rendered-v1':
        return JsonResponse({'error': 'Electronic signing is disabled until this PDF has been verified by the server renderer.'}, status=409)
    signature = data.get('signature')
    name = data.get('name')
    consent_text = data.get('consentText')
    if not isinstance(signature, str) or not signature.startswith('data:image/') or len(signature) > SIGNATURE_MAX_CHARS:
        return JsonResponse({'error': 'Provide a supported signature image.'}, status=400)
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
        return JsonResponse({'error': 'Provide the signatory name.'}, status=400)
    if data.get('consent') is not True or not isinstance(consent_text, str) or not consent_text.strip():
        return JsonResponse({'error': 'Confirm consent before signing.'}, status=400)
    snapshot = issue.template_snapshot if isinstance(issue.template_snapshot, dict) else {}
    if artifact.payload.get('sha256') != snapshot.get('pdfSha256'):
        return JsonResponse({'error': 'The issued PDF integrity binding is invalid.'}, status=409)
    for key in ('reportId', 'reportVersion', 'reportRevision'):
        if str(artifact.payload.get(key)) != str(snapshot.get(key)):
            return JsonResponse({'error': 'The issued report version binding is invalid.'}, status=409)
    try:
        bound_report_id = int(artifact.payload['reportId'])
        bound_report_version = int(artifact.payload['reportVersion'])
        bound_report_revision = int(artifact.payload['reportRevision'])
    except (KeyError, TypeError, ValueError):
        return JsonResponse({'error': 'The issued report version binding is invalid.'}, status=409)
    now = timezone.now()
    try:
        with transaction.atomic():
            if SuitabilityRplDocumentSignature.objects.filter(issued_document_id=issue.pk, signer_role=role).exists():
                return JsonResponse({'error': 'This party has already signed this issued report version.'}, status=409)
            SuitabilityRplDocumentSignature.objects.create(
                issued_document_id=issue.pk,
                document_key=REPORT_DOCUMENT_KEY,
                learner_kind=kind,
                learner_id=learner_id,
                report_id=bound_report_id,
                report_version=bound_report_version,
                report_revision=bound_report_revision,
                pdf_sha256=artifact.payload['sha256'],
                signer_role=role,
                login_account_id=int(account.id),
                signer_subject_type=account.subject_type,
                signer_subject_id=int(account.subject_id),
                signer_name=name.strip(),
                signature_method='drawn',
                signature=signature,
                consent_version=SIGNATURE_CONSENT_VERSION,
                consent_text=consent_text.strip(),
                consented_at=now,
                signed_at=now,
                ip_address=(request.META.get('REMOTE_ADDR') or '')[:255],
                user_agent=(request.META.get('HTTP_USER_AGENT') or '')[:1000],
                audit_metadata={'issuedDocumentId': str(issue.pk), 'pdfSha256': artifact.payload['sha256']},
            )
    except DatabaseError:
        return JsonResponse({'error': 'Could not record the signature. Retry without changing the issued document.'}, status=503)
    return JsonResponse({'status': _signature_status(issue, artifact)}, status=201)


def _ai_input(report, draft_type):
    snapshot = report.source_snapshot or {}
    if draft_type == 'suitability':
        learner = snapshot.get('learner') if isinstance(snapshot.get('learner'), dict) else {}
        qualifications = ((snapshot.get('evidence') or {}).get('qualifications') or {})
        suitability = snapshot.get('programmeSuitability')
        suitability = suitability if isinstance(suitability, dict) else {}
        # The AI explains the selected current programme only.  Historical
        # recommendation/outcome fields remain in the frozen snapshot but are
        # not learner evidence for this narrative and can invite unsupported
        # progression claims.
        suitability = {
            key: value for key, value in suitability.items()
            if key not in {'recommendedProgramme', 'outcome', 'review'}
        }
        return {
            'programme': snapshot.get('programme', {}),
            # Deliberately omit the learner's name, reference, employer and all
            # uploaded-document metadata from the external drafting payload.
            'learnerEvidence': {
                'jobTitle': learner.get('jobTitle'),
                'responsibilities': learner.get('responsibilities', []),
            },
            'programmeUnderstanding': snapshot.get('programmeUnderstanding', []),
            'programmeSuitability': suitability,
            'skillsRadarSummary': [
                {
                    key: item.get(key)
                    for key in ('code', 'kind', 'level', 'score', 'description', 'moduleKey')
                    if key in item
                }
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


def _rpl_analysis_payload(report):
    """Build the private, evidence-only payload for pre-decision RPL analysis.

    This deliberately does not include the saved RPL decision or reductions: the
    analysis is advisory and must assess the evidence before the assessor records
    an outcome.  CV text is included only in this private server-to-AI payload;
    the returned recommendation is validated and never contains that text.
    """
    snapshot = report.source_snapshot if isinstance(report.source_snapshot, dict) else {}
    evidence = snapshot.get('evidence') if isinstance(snapshot.get('evidence'), dict) else {}
    cv = evidence.get('cv') if isinstance(evidence.get('cv'), dict) else {}
    qualifications = evidence.get('qualifications') if isinstance(evidence.get('qualifications'), dict) else {}
    skills = snapshot.get('skillsRadar') if isinstance(snapshot.get('skillsRadar'), dict) else {}
    training = snapshot.get('trainingPlan') if isinstance(snapshot.get('trainingPlan'), dict) else {}
    modules = []
    for item in training.get('modules') or []:
        if not isinstance(item, dict):
            continue
        modules.append({
            'moduleId': _s(item.get('moduleId')),
            'moduleName': _s(item.get('title') or item.get('moduleTitle') or item.get('moduleName')),
            'plannedHours': item.get('plannedHours'),
            'content': _s(item.get('content') or item.get('description')),
            'authoritativeKsbMappings': [
                mapping for mapping in (item.get('authoritativeKsbMappings') or item.get('ksbMappings') or [])
                if isinstance(mapping, dict)
            ],
        })

    # The frozen snapshot carries the authoritative module ids/hours. Enrich it
    # with live curriculum content and mappings for analysis only; no learner or
    # report data is changed by this read.
    try:
        from curriculum_api.models import ModuleAuthoringKsbMapping, ModuleAuthoringModule

        module_ids = [item['moduleId'] for item in modules if item['moduleId']]
        live_modules = {
            row.module_catalogue_id: row
            for row in ModuleAuthoringModule.objects.filter(module_catalogue_id__in=module_ids)
        }
        mappings = {}
        mapping_codes = {}
        for row in ModuleAuthoringKsbMapping.objects.filter(
            module_catalogue_id__in=module_ids, deleted_at__isnull=True,
        ).order_by('module_catalogue_id', 'ksb_code', 'id'):
            code = _s(row.ksb_code)
            if not code or code in mapping_codes.setdefault(row.module_catalogue_id, set()):
                continue
            mapping_codes[row.module_catalogue_id].add(code)
            mappings.setdefault(row.module_catalogue_id, []).append({
                'code': code,
                'description': _s(row.ksb_description),
                'source': _s(row.source_type),
            })
        for item in modules:
            row = live_modules.get(item['moduleId'])
            if row is not None:
                item['moduleName'] = _s(row.title) or item['moduleName']
                item['content'] = _s(row.description) or item['content']
            item['authoritativeKsbMappings'] = mappings.get(item['moduleId'], [])
    except Exception:
        # A curriculum projection may be unavailable in an older environment.
        # The recommendation must then expose the limitation, never fabricate it.
        pass

    private_cv = ''
    extraction_warning = ''
    if cv.get('status') == 'provided' and not getattr(report, 'cv_extraction_acknowledged', False):
        try:
            private_cv = _private_cv_text(report, cv)
        except ReportInputError as exc:
            extraction_warning = str(exc)
    elif cv.get('status') == 'provided':
        extraction_warning = 'The assessor acknowledged that the approved CV could not be used.'

    # Evidence references are deliberately namespaced and deterministic. They
    # are identifiers for the evidence catalogue sent in this private request,
    # not KSB codes, module ids, labels, or excerpts from the CV.
    evidence_references = []

    def add_reference(reference_id, source, label):
        if reference_id and not any(item['id'] == reference_id for item in evidence_references):
            evidence_references.append({'id': reference_id, 'source': source, 'label': label})

    learner = snapshot.get('learner') if isinstance(snapshot.get('learner'), dict) else {}
    if _s(learner.get('jobTitle')):
        add_reference('evidence.learner.job-title', 'learner_record', 'Saved job title')
    for index, responsibility in enumerate(learner.get('responsibilities') or [], start=1):
        if _s(responsibility):
            add_reference(f'evidence.learner.responsibility.{index}', 'learner_record', f'Saved responsibility {index}')
    for index, answer in enumerate(snapshot.get('programmeUnderstanding') or [], start=1):
        if answer:
            add_reference(f'evidence.programme-understanding.{index}', 'programme_understanding', f'Saved programme understanding response {index}')
    for index, item in enumerate(skills.get('items') or [], start=1):
        if isinstance(item, dict) and (_s(item.get('code')) or _s(item.get('description'))):
            add_reference(f'evidence.skills-radar.{index}', 'skills_radar', f'Saved Skills Radar item {index}')
    for index, item in enumerate(qualifications.get('items') or [], start=1):
        if item:
            add_reference(f'evidence.qualification.{index}', 'qualification_record', f'Saved qualification record {index}')
    if cv.get('status') == 'provided':
        add_reference('evidence.cv.approved', 'approved_cv_extraction', 'Approved CV extraction')
    if evidence.get('plr'):
        add_reference('evidence.plr.status', 'plr_record', 'Recorded prior learning status')
    for item in modules:
        module_id = _s(item.get('moduleId'))
        if module_id:
            add_reference(f'evidence.training-plan.module.{module_id}', 'training_plan', f'Saved Training Plan module {module_id}')

    return {
        'programme': snapshot.get('programme', {}),
        'learnerEvidence': {
            'jobTitle': ((snapshot.get('learner') or {}).get('jobTitle') if isinstance(snapshot.get('learner'), dict) else ''),
            'responsibilities': ((snapshot.get('learner') or {}).get('responsibilities', []) if isinstance(snapshot.get('learner'), dict) else []),
        },
        'programmeUnderstanding': snapshot.get('programmeUnderstanding', []),
        'skillsRadar': {
            'catalogueVersion': skills.get('catalogueVersion'),
            'cataloguePinned': skills.get('cataloguePinned'),
            'summary': skills.get('summary', []),
            'items': [
                {key: item.get(key) for key in ('code', 'kind', 'level', 'description', 'moduleKey', 'score') if key in item}
                for item in (skills.get('items') or []) if isinstance(item, dict)
            ],
        },
        'qualifications': {'status': qualifications.get('status'), 'items': qualifications.get('items', [])},
        'plr': evidence.get('plr') or {'status': 'not_available'},
        'trainingPlan': {
            'available': training.get('available'),
            'plannedHours': training.get('plannedHours'),
            'modules': [
                {
                    **item,
                    # Make the authority boundary explicit to the model. Skills
                    # Radar codes are evidence, not module mappings.
                    'authoritativeKsbMappingStatus': 'available' if item.get('authoritativeKsbMappings') else 'not_available',
                    'authoritativeKsbCodes': [_s(mapping.get('code')) for mapping in item.get('authoritativeKsbMappings', []) if _s(mapping.get('code'))],
                }
                for item in modules
            ],
        },
        'privateCvText': private_cv,
        'extractionWarning': extraction_warning,
        'evidenceReferences': evidence_references,
        'evidenceVersion': {
            'sourceSnapshotHash': report.source_snapshot_hash,
            'documentId': report.cv_source_document_id,
            'container': report.cv_source_container,
            'blobName': report.cv_source_blob_name,
            'sha256': report.cv_source_blob_hash,
            'extractorVersion': report.cv_extractor_version,
        },
    }


def _analysis_strings(value):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ReportInputError('The AI recommendation returned an invalid evidence list.')
    values = [_s(item) for item in value if _s(item)]
    if any(len(item) > 2_000 for item in values):
        raise ReportInputError('The AI recommendation contains oversized evidence text.')
    return values


def _analysis_evidence_refs(value, allowed_ids):
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ReportInputError('The AI recommendation returned an invalid evidence list.')
    refs = []
    for item in value:
        evidence_id = _s(item.get('evidenceId'))
        detail = _s(item.get('detail'))
        safe_id = evidence_id[:120] if evidence_id else '<missing>'
        if not evidence_id or evidence_id not in allowed_ids:
            raise ReportInputError(f'The AI recommendation returned an unsupported evidence reference ID: {safe_id}.')
        if not detail or len(detail) > 2_000:
            raise ReportInputError('The AI recommendation returned an invalid evidence reference detail.')
        refs.append({'evidenceId': evidence_id, 'detail': detail})
    return refs


def _validate_rpl_analysis(result, payload):
    if not isinstance(result, dict):
        raise ReportInputError('The AI recommendation must be a JSON object.')
    outcome = _s(result.get('overallOutcome'))
    if outcome not in RPL_ANALYSIS_OUTCOMES:
        raise ReportInputError('The AI recommendation returned an unknown RPL outcome.')
    reasoning = _s(result.get('reasoning'))
    if not reasoning or len(reasoning) > 8_000:
        raise ReportInputError('The AI recommendation returned invalid overall reasoning.')
    modules = result.get('modules')
    if not isinstance(modules, list):
        raise ReportInputError('The AI recommendation must include one analysis per Training Plan module.')
    expected = {item.get('moduleId') for item in (payload.get('trainingPlan') or {}).get('modules', []) if item.get('moduleId')}
    allowed_evidence_ids = {
        _s(reference.get('id'))
        for reference in (payload.get('evidenceReferences') or [])
        if isinstance(reference, dict) and _s(reference.get('id'))
    }
    seen = set()
    clean_modules = []
    private_text = _s(payload.get('privateCvText'))
    for item in modules:
        if not isinstance(item, dict):
            raise ReportInputError('Each module recommendation must be an object.')
        module_id = _s(item.get('moduleId'))
        if not module_id or module_id in seen or module_id not in expected:
            raise ReportInputError('The AI recommendation must use the authoritative Training Plan module IDs.')
        seen.add(module_id)
        candidate = item.get('wholeModuleReductionRecommended')
        if not isinstance(candidate, bool):
            raise ReportInputError(
                f'Module {module_id} needs a boolean whole-module reduction recommendation; '
                f'received {type(candidate).__name__}.'
            )
        evidence_sufficiency = _s(item.get('evidenceSufficiency'))
        if evidence_sufficiency not in {'sufficient', 'partial', 'insufficient', 'needs-review'}:
            raise ReportInputError('Each module recommendation needs an explicit evidence sufficiency status.')
        evidence = _analysis_evidence_refs(item.get('supportingEvidence'), allowed_evidence_ids)
        missing = _analysis_strings(item.get('missingEvidence'))
        explanation = _s(item.get('explanation'))
        if not explanation or len(explanation) > 4_000:
            raise ReportInputError('Each module recommendation needs a clear explanation.')
        authoritative = next((m for m in (payload.get('trainingPlan') or {}).get('modules', []) if m.get('moduleId') == module_id), {})
        authoritative_mappings = [
            {
                'code': _s(mapping.get('code')),
                'description': _s(mapping.get('description')),
                'source': _s(mapping.get('source')),
            }
            for mapping in (authoritative.get('authoritativeKsbMappings') or [])
            if isinstance(mapping, dict) and _s(mapping.get('code'))
        ]
        # KSB relationships are authoritative curriculum data. They are never
        # accepted from, or compared against, model-generated fields.
        if candidate and (evidence_sufficiency != 'sufficient' or not authoritative_mappings or not evidence or missing):
            raise ReportInputError('A whole-module candidate requires complete mapped evidence with no unresolved gaps.')
        clean_item = {
            'moduleId': module_id,
            'moduleName': _s(authoritative.get('moduleName')),
            'plannedHours': authoritative.get('plannedHours'),
            'wholeModuleReductionRecommended': candidate,
            'evidenceSufficiency': evidence_sufficiency,
            'supportingEvidence': evidence,
            'authoritativeKsbMappings': authoritative_mappings,
            'missingEvidence': missing,
            'explanation': explanation,
        }
        clean_modules.append(clean_item)
    if seen != expected:
        raise ReportInputError('The AI recommendation did not analyse every authoritative Training Plan module.')
    if outcome == 'potential-whole-module-reduction' and not any(item['wholeModuleReductionRecommended'] for item in clean_modules):
        raise ReportInputError('The AI outcome requires at least one complete-module candidate.')
    if outcome != 'potential-whole-module-reduction' and any(item['wholeModuleReductionRecommended'] for item in clean_modules):
        raise ReportInputError('The AI recommendation cannot suggest a whole-module removal for this outcome.')
    serialised = json.dumps({'reasoning': reasoning, 'modules': clean_modules}, ensure_ascii=False)
    if private_text:
        fragments = [private_text]
        fragments.extend(part.strip() for part in re.split(r'[\n.!?]+', private_text) if len(part.strip()) >= 32)
        if any(fragment and fragment in serialised for fragment in fragments):
            raise ReportInputError('The AI recommendation exposed raw private CV text.')
    return {'overallOutcome': outcome, 'reasoning': reasoning, 'modules': clean_modules,
            'evidenceGaps': _analysis_strings(result.get('evidenceGaps', [])),
            'warnings': _analysis_strings(result.get('warnings', []))}


def generate_rpl_evidence_analysis(report):
    if not getattr(settings, 'SUITABILITY_RPL_AI_ENABLED', False):
        raise ReportInputError('AI drafting is disabled for this environment.')
    api_key = getattr(settings, 'SUITABILITY_RPL_AI_API_KEY', '') or getattr(settings, 'OPENAI_API_KEY', '')
    if not api_key:
        raise ReportInputError('The approved AI service is not configured.')
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise ReportInputError('The approved AI client is unavailable.') from exc
    payload = _rpl_analysis_payload(report)
    system = (
        'Analyse the supplied apprenticeship RPL evidence independently before an assessor records a decision. '
        'Return JSON with exactly: overallOutcome, reasoning, modules, evidenceGaps, warnings. '
        'overallOutcome must be one of no-relevant-prior-learning, potential-whole-module-reduction, '
        'relevant-partial-no-exemption, insufficient-evidence. '
        'Analyse every authoritative Training Plan module. Return each module with exactly these fields: moduleId, '
        'wholeModuleReductionRecommended (a strict JSON boolean), evidenceSufficiency (one of sufficient, partial, insufficient, needs-review), '
        'supportingEvidence, missingEvidence, and explanation. Never omit or null the boolean. '
        'moduleId is only an exact selector copied from the authoritative input. Do not output moduleName, plannedHours, or ksbMappings; '
        'the server attaches those authoritative values after validation. '
        'Use this JSON shape for each module: {"moduleId":"authoritative-id",'
        '"wholeModuleReductionRecommended":false,"evidenceSufficiency":"needs-review",'
        '"supportingEvidence":[],"missingEvidence":["verification needed"],'
        '"explanation":"complete-module assessment"}. '
        'Use each module\'s authoritativeKsbMappings/authoritativeKsbCodes only as analysis context; never return KSB codes or mapping objects. '
        'Skills Radar codes and moduleKey values are learner evidence only and are never authoritative module mappings. '
        'Use partial, insufficient, or needs-review when authoritative mappings are unavailable; never invent or reproduce a replacement mapping. '
        'wholeModuleReductionRecommended may be true only when the supplied verified evidence '
        'covers the complete module outcomes and every supplied authoritative KSB mapping, with no missing evidence. '
        'Never recommend removing a module because of partial topic coverage, a job title, self-assessment scores, or a generic qualification. '
        'Distinguish reported experience from demonstrated competence. Missing PLR, employer agreement, KSB mappings or verification is a gap, '
        'not proof that prior learning is absent. Never invent evidence, mappings, hours, costs or employer agreement. '
        'The evidenceReferences array is the canonical allowlist for supportingEvidence. Each supportingEvidence item must be exactly '
        '{"evidenceId":"<exact id from evidenceReferences>","detail":"short explanation"}. Use only those exact namespaced IDs. '
        'Evidence IDs are distinct from KSB codes and Training Plan module IDs; never use labels, KSB codes, module IDs, or CV excerpts as evidenceId. '
        'An empty supportingEvidence list is valid when evidence is missing, partial, or needs review. Never invent or substitute an ID. '
        'Never return raw CV text, private document content, JSON field names, or workflow instructions. '
        'Keep the overall reasoning concise at approximately 20–40 words, preferably one or two short sentences. Mention only the main evidence-based reason; this is a writing guideline, not a hard limit. '
        'Keep each module explanation concise for internal review; it is not shown in the compact assessor panel. '
        'The recommendation is advisory only and must not select or save the assessor decision.'
    )
    request = {
        'model': getattr(settings, 'OPENAI_MODEL', 'gpt-4.1-mini'),
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': f'Evidence for analysis:\n{json.dumps(payload, ensure_ascii=False)}'},
        ],
        'response_format': {'type': 'json_object'},
        'max_completion_tokens': 4096,
    }
    if request['model'].lower().startswith('gpt-5'):
        request['reasoning_effort'] = 'minimal'
    response = OpenAI(api_key=api_key, base_url=getattr(settings, 'SUITABILITY_RPL_AI_BASE_URL', '') or None).chat.completions.create(**request)
    choice = response.choices[0] if response.choices else None
    finish_reason = getattr(choice, 'finish_reason', None) if choice else None
    usage = response.usage.model_dump() if getattr(response, 'usage', None) else None
    if finish_reason not in (None, 'stop'):
        raise ReportInputError(f'The AI recommendation did not finish safely. {_narrative_diagnostics("", finish_reason, usage)}')
    content = _s(choice.message.content if choice else '')
    try:
        result = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ReportInputError('The AI service returned an invalid RPL recommendation.') from exc
    recommendation = _validate_rpl_analysis(result, payload)
    metadata = {
        'provider': getattr(settings, 'SUITABILITY_RPL_AI_PROVIDER', 'openai'),
        'model': request['model'], 'generatedAt': timezone.now().isoformat(),
        'sourceSnapshotHash': report.source_snapshot_hash,
        'cvEvidenceVersion': payload.get('evidenceVersion'), 'usage': usage,
        'finishReason': finish_reason,
    }
    return recommendation, metadata


def _private_cv_text(report, cv):
    """Resolve only the exact frozen CV source; never expose it in snapshots/API."""
    sources = cv.get('sourceDocuments') if isinstance(cv, dict) else []
    if not sources:
        return ''
    source = sources[0] if isinstance(sources[0], dict) else {}
    document_id = _s(source.get('id'))
    if not document_id:
        return ''
    # Frozen report snapshots use the privacy-safe normalized ``kind`` field,
    # while the extraction service consumes the upload manifest's ``docKind``.
    # Restore that vocabulary at this boundary without changing the snapshot
    # or broadening which documents the extractor accepts.
    source = dict(source)
    if not source.get('docKind') and source.get('kind'):
        source['docKind'] = source['kind']
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
    api_key = getattr(settings, 'SUITABILITY_RPL_AI_API_KEY', '') or getattr(settings, 'OPENAI_API_KEY', '')
    if not api_key:
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
    if draft_type == 'suitability':
        system = (
            'Draft a concise, professional UK apprenticeship programme suitability justification. '
            'Use only the supplied evidence and write directly about the learner in complete sentences. '
            'Aim for approximately 150-200 words in two coherent paragraphs; if evidence is limited, stay concise rather than padding the narrative. '
            'Prioritise the learner-specific saved job title, responsibilities, Programme Understanding answers, selected Programme Suitability answers, '
            'and qualifications only where specific verified details are supplied. Explain two or three specific connections between demonstrated responsibilities '
            'or selected answers and the selected ME_L4 requirements. Ground the conclusion in the saved learner evidence, not in the programme description alone. '
            'Explain why the apprenticeship would develop significant new occupational knowledge, skills and behaviours only where authoritative standard outcomes '
            'and supporting learner evidence are supplied; if those mappings or evidence are absent, identify that limitation for assessor review rather than claiming coverage. '
            'Do not repeat the full programme specification, produce a list, infer responsibilities or achievements, or make generic or exaggerated claims. '
            'Do not claim KSB coverage unless authoritative mappings and supporting learner evidence are explicitly supplied. '
            'Treat missing, empty, or placeholder-like information as insufficient evidence and say only what cannot be substantiated. '
            'Discuss programme suitability only. Do not mention exemptions, prior-learning reductions, training hours, duration, funding, costs, or any other RPL decision. '
            'Keep the selected programme distinct from any recommended future programme. Do not claim employer discussion, agreement or sign-off unless it is explicitly recorded in the supplied evidence. '
            'Do not claim DfE compliance or audit approval. Never mention AI, JSON, technical fields, prompts, or internal workflow instructions. '
            'Do not invent facts. Return JSON with one key: justification.'
        )
    else:
        system = (
            'Draft a professional, report-ready UK apprenticeship assessment narrative. '
            'Use only the supplied evidence and write directly about the learner and the assessor-recorded decision, with complete sentences and a clear conclusion. '
            'Aim for approximately 150-200 words in one or two coherent paragraphs. '
            'Do not include internal workflow instructions, approval instructions, prompts, metadata, field names, JSON, or technical details. '
            'Do not invent facts, infer verified learning from a job title or self-assessment, make a decision, change modules, change hours, calculate prices, '
            'or treat unavailable PLR as evidence of no prior learning. '
            'Treat missing, empty, or placeholder-like learner information as an evidence limitation and flag only genuine gaps or contradictions. '
            'For No Relevant Prior Learning, distinguish the recorded label from the evidence conclusion: say No Relevant Prior Learning identified only when the supplied verified evidence supports that conclusion; otherwise say that the evidence is insufficient to justify a reduction. '
            'Distinguish reported experience or self-assessment from demonstrated occupational competence. Use specific supplied qualification details, Skills Radar results, module names, and the authoritative planned hours where available, without calling a source verified unless the evidence says so. '
            'Assess evidence against the saved Training Plan and occupational-standard outcomes only where authoritative mappings are supplied. If module KSB mappings, PLR, employer discussion or other mandatory evidence are absent, flag the deficiency for assessor review and never treat the absence as proof that prior learning does not exist. '
            'Explain why the full standard Training Plan remains unchanged, including its total hours when supplied. '
            'For RPL without reduction, explain why the supplied evidence does not substantiate exemption or reduction against the relevant modules and available KSB evidence. '
            'For approved reductions, connect only the supplied CV, qualification, and work evidence to the affected modules and mapped KSBs, '
            'explain the recorded reduction for each affected module without changing it, and do not invent employer agreement, competence, hours or financial values. '
            'Never refer to arrays, JSON, fields, or internal data structures, and do not claim DfE compliance or audit approval. '
            'Return JSON with one key: justification.'
        )
    if draft_type == 'suitability':
        purpose = ('Explain why the selected ME_L4 programme is suitable using the learner\'s saved role, responsibilities, '
                   'Programme Understanding answers, selected Programme Suitability answers, qualifications, and only the most relevant programme requirements.')
    elif report.relevant_prior_learning is False and report.reduction_justified is False:
        plan = payload.get('trainingPlan') if isinstance(payload.get('trainingPlan'), dict) else {}
        module_names = ', '.join(_s(item.get('title')) for item in plan.get('modules', []) if isinstance(item, dict) and _s(item.get('title')))
        purpose = (f'The assessor recorded No Relevant Prior Learning. Explain why the supplied verified evidence does not establish a justified '
                   f'module-specific exemption or training reduction, while preserving the full standard Training Plan of {plan.get("plannedHours")} planned hours. '
                   f'Connect the evidence to these saved modules where supported: {module_names or "the saved modules"}. '
                   'State genuine evidence limitations without treating unavailable PLR or missing KSB mappings as proof that prior learning does not exist.')
    else:
        purpose = 'Explain the assessor-entered RPL decision and training adjustment exactly as supplied, without changing modules, hours, or costs.'
    model = getattr(settings, 'OPENAI_MODEL', 'gpt-4.1-mini')
    base_url = getattr(settings, 'SUITABILITY_RPL_AI_BASE_URL', '') or None
    request = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': f'{purpose}\n\nEvidence JSON:\n{json.dumps(payload, ensure_ascii=False)}'},
        ],
        'response_format': {'type': 'json_object'},
        'max_completion_tokens': 4096,
    }
    if model.lower().startswith('gpt-5'):
        request['reasoning_effort'] = 'minimal'
    response = OpenAI(api_key=api_key, base_url=base_url).chat.completions.create(**request)
    finish_reason = getattr(response.choices[0], 'finish_reason', None) if response.choices else None
    usage = response.usage.model_dump() if getattr(response, 'usage', None) else None
    if finish_reason not in (None, 'stop'):
        diagnostics = _narrative_diagnostics('', finish_reason, usage)
        raise ReportInputError(f'The AI service did not finish the draft safely. {diagnostics}')
    content = _s(response.choices[0].message.content if response.choices else '')
    try:
        result = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ReportInputError('The AI service returned an invalid draft.') from exc
    justification = _s(result.get('justification')) if isinstance(result, dict) else ''
    if not justification or len(justification) > MAX_TEXT:
        raise ReportInputError('The AI service returned an empty or oversized draft.')
    if '\ufffd' in justification:
        raise ReportInputError('The AI service returned invalid replacement characters.')
    if draft_type == 'suitability':
        try:
            justification = _validate_suitability_narrative(justification)
        except ReportInputError as exc:
            diagnostics = _narrative_diagnostics(_normalise_suitability_narrative(justification), finish_reason, usage)
            raise ReportInputError(f'{exc} {diagnostics}') from exc
    else:
        try:
            justification = _validate_rpl_narrative(justification, report, payload)
        except ReportInputError as exc:
            diagnostics = _narrative_diagnostics(_normalise_suitability_narrative(justification), finish_reason, usage)
            raise ReportInputError(f'{exc} {diagnostics}') from exc
    version = payload.get('cvEvidence', {}).get('evidenceVersion', {}) if draft_type == 'rpl' else {}
    return justification, {'provider': getattr(settings, 'SUITABILITY_RPL_AI_PROVIDER', 'openai'), 'model': model, 'generatedAt': timezone.now().isoformat(),
                           'inputHash': _json_hash(payload), 'cvEvidenceVersion': version,
                           'usage': usage, 'finishReason': finish_reason, 'generatedText': justification}


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
            report = _reports_for_context(kind, learner_id, context).select_for_update().prefetch_related('reductions').filter(
                status=SuitabilityRplReport.STATUS_DRAFT,
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
    except ReportContextConflict as exc:
        return JsonResponse({'error': str(exc)}, status=409)
    except ReportInputError as exc:
        return JsonResponse({'error': str(exc)}, status=503 if 'disabled' in str(exc) or 'configured' in str(exc) else 400)
    except DatabaseError:
        return JsonResponse({'error': 'The report draft could not be updated.'}, status=503)


@require_role('admin')
@csrf_protect
def suitability_rpl_analysis(request, kind, learner_id):
    """Return an unsaved, assessor-review-only RPL evidence analysis."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    learner = _learner(kind, learner_id)
    if learner is None:
        return JsonResponse({'error': 'Learner not found.'}, status=404)
    context = _context(learner, kind, _programme_assessment(learner_id))
    try:
        report = _reports_for_context(kind, learner_id, context).prefetch_related('reductions').filter(
            status=SuitabilityRplReport.STATUS_DRAFT,
        ).first()
        if report is None:
            raise ReportInputError('Save the report draft before analysing RPL evidence.')
        recommendation, metadata = generate_rpl_evidence_analysis(report)
        return JsonResponse({'recommendation': recommendation, 'metadata': metadata})
    except ReportContextConflict as exc:
        return JsonResponse({'error': str(exc)}, status=409)
    except ReportInputError as exc:
        return JsonResponse({'error': str(exc)}, status=503 if 'disabled' in str(exc) or 'configured' in str(exc) else 400)
    except DatabaseError:
        return JsonResponse({'error': 'The RPL evidence analysis could not be completed.'}, status=503)
