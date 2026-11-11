"""Read-only learner obligations, derived from existing documents and marks.

No notification rows, signature images, live document rendering or issuance side
effects. Revision signing rows deliberately use non-active source statuses; their
finalized parent revision determines whether they represent a current request.
"""
import logging
from urllib.parse import quote, urlencode

from django.db import DatabaseError
from django.db.models import TextField, Value
from django.db.models.functions import Coalesce, Length, Trim
from django.http import JsonResponse

from .auth import is_authenticated
from .document_templates import revisions, service
from .document_templates.models import IssuedTemplate, LearnerDocumentRevision
from .document_templates.registry import REVIEW_KEYS, seed
from learner_api.models import ApprenticeshipAgreement, EnrolmentReview, EnrolmentUser, IlrDocument, TrainingPlanDocument, WrittenAgreement

logger = logging.getLogger(__name__)
COMPLIANCE = {
    'apprenticeship-agreement': (ApprenticeshipAgreement, 'apprentice'),
    'ilr': (IlrDocument, 'learner'),
    'training-plan': (TrainingPlanDocument, 'apprentice'),
    'written-agreement': (WrittenAgreement, 'learner'),
}


def response(data, status=200):
    result = JsonResponse(data, status=status)
    result['Cache-Control'] = 'private, no-store'
    result['Vary'] = 'Cookie'
    return result


def marks(query, role):
    # Never transfer signature images/answer snapshots just to calculate a count.
    return query.annotate(signature_length=Length(Trim(Coalesce(role + '_signature', Value(''), output_field=TextField()))))


def target_id(key, source_id=None, revision_id=None):
    if revision_id: return 'signature-revision-' + str(revision_id)
    return 'signature-' + key + '-' + quote(str(source_id), safe="~!*'()-._")


def notification(key, row, revision=None, template_number=None):
    review = key.startswith('review-')
    identity = str(row['id'])
    target = target_id(key, row.get('event_key') if review else identity, revision['id'] if revision else None)
    path = '/learner/onboarding/reviews' if review else '/learner/compliance-documents'
    issued = revision['finalized_at'] if revision else row['issued_at']
    return {
        'id': 'revision:' + str(revision['id']) if revision else 'document:' + key + ':' + identity,
        'documentKey': key, 'documentId': identity,
        'revisionId': str(revision['id']) if revision else None,
        'revisionNumber': revision['number'] if revision else 1,
        'title': revision['content__title'] if revision else seed(key)['title'],
        'category': 'Review' if review else 'Compliance',
        'templateVersion': (revision.get('data__documentTemplateSnapshot__number') if revision.get('data__documentTemplateSnapshot__number') is not None else revision['issue__template_snapshot__number']) if revision else template_number,
        'issuedAt': issued.isoformat() if issued else None,
        'status': 'pending', 'requiresLearnerSignature': True,
        'href': path + '?' + urlencode({'signature': target}),
    }


def current_sources(learner_id, include_reviews=True):
    sources = {}
    for key, (model, role) in COMPLIANCE.items():
        row = marks(model.objects.filter(learner_id=learner_id, status=model.STATUS_ACTIVE), role).order_by('-created_at', '-id').values('id', 'created_at', 'signature_length').first()
        if row: sources[key] = {**row, 'issued_at': row['created_at']}
    if not include_reviews: return sources
    # Match the current-review selector: newest non-cancelled instance per type.
    # Completion is tested AFTER choosing it so an old request cannot resurface
    # just because the current review is still being filled in.
    rows = marks(EnrolmentReview.objects.filter(learner_id=learner_id, review_type__in=REVIEW_KEYS).exclude(status=EnrolmentReview.STATUS_CANCELLED), 'learner').order_by('-id').values('id', 'review_type', 'event_key', 'booked_at', 'status', 'form_completed', 'signature_length')
    for row in rows:
        key = REVIEW_KEYS[row['review_type']]
        if key not in sources: sources[key] = {**row, 'issued_at': row['booked_at']}
    return sources


def pending_for(learner_id, include_reviews=True):
    sources = current_sources(learner_id, include_reviews)
    locked = set(); current = {}; versions = {}
    if revisions.ready():
        rows = LearnerDocumentRevision.objects.filter(issue__learner_id=learner_id).exclude(state='draft').order_by('issue_id', '-number').values(
            'id', 'issue_id', 'number', 'state', 'signature_method', 'signing_source_id', 'finalized_at',
            'content__title', 'content__signatureRoles', 'issue__document_key', 'issue__source_id', 'issue__template_snapshot__number', 'data__documentTemplateSnapshot__number')
        for row in rows:
            key = row['issue__document_key']; source = sources.get(key)
            if not source or str(source['id']) != row['issue__source_id']: continue
            if row['number'] == 1: locked.add(key)
            if row['state'] == 'finalized': current.setdefault(row['issue_id'], row)
    if service.ready():
        for row in IssuedTemplate.objects.filter(learner_id=learner_id, source_id__in=[str(s['id']) for s in sources.values()]).values('document_key', 'source_id', 'template_snapshot__number'):
            source = sources.get(row['document_key'])
            if source and str(source['id']) == row['source_id']: versions[row['document_key']] = row['template_snapshot__number']
    results = []
    for key, source in sources.items():
        if key in locked or source['signature_length']: continue
        if key.startswith('review-') and (not source['form_completed'] or source['status'] not in {'booked', 'issued', 'completed'}): continue
        results.append(notification(key, source, template_number=versions.get(key)))

    # Only the latest finalized copy of a current root is applicable. A draft
    # never supersedes a request. Keep Existing Signatures requests no new mark.
    for key in sources:
        selected = [r for r in current.values() if r['issue__document_key'] == key and r['signature_method'] == 'sign-again' and r['signing_source_id']]
        if not selected: continue
        model, role = COMPLIANCE[key] if key in COMPLIANCE else (EnrolmentReview, 'learner')
        eligible = [r for r in selected if role in (r['content__signatureRoles'] or [])]
        if not eligible: continue
        query = model.objects.filter(learner_id=learner_id, pk__in=[r['signing_source_id'] for r in eligible])
        query = query.filter(status='cancelled', form_completed=True) if key.startswith('review-') else query.filter(status=model.STATUS_SUPERSEDED)
        unsigned = {str(r['id']) for r in marks(query, role).filter(signature_length=0).values('id')}
        for revision in eligible:
            if revision['signing_source_id'] in unsigned: results.append(notification(key, sources[key], revision))
    results.sort(key=lambda r: (r['issuedAt'] or '', r['id']), reverse=True)
    return {'count': len(results), 'results': results}


def pending_signatures(request):
    if request.method != 'GET': return response({'error': 'Method not allowed.'}, 405)
    if not is_authenticated(request): return response({'error': 'Authentication required.'}, 401)
    account = getattr(request, 'login_account', None)
    if not account or not account.is_active or account.role != 'learner' or account.subject_type != 'learner': return response({'error': 'Learner access required.'}, 403)
    try:
        learner = EnrolmentUser.all_learners.filter(pk=account.subject_id).values('learner_type', 'onboarding_status').first()
        if learner is None: return response({'error': 'Not found.'}, 404)
        reviews_accessible = (learner['onboarding_status'] or '').strip().lower() in {'submitted', 'completed'}
        return response({'count': 0, 'results': []} if learner['learner_type'] == 'commercial' else pending_for(account.subject_id, reviews_accessible))
    except DatabaseError:
        logger.exception('Could not load learner pending signature requests')
        return response({'error': 'Could not load signature requests. Please retry.'}, 503)
