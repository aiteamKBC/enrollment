"""Versions bind to existing document rows; this is not another issuer/signature API."""
import copy
import json
import logging
import re
import time
from datetime import date, datetime
from decimal import Decimal
from functools import wraps

from django.conf import settings
from django.db import connections, router, transaction
from django.db.models import OuterRef, Subquery
from django.utils import timezone

from .models import IssuedTemplate, RenderHistory, TemplateDraft, TemplateVersion
from .registry import PROGRAMMES, TOKEN, TemplateError, digest, legacy_equivalent, seed, seeds, validate

logger = logging.getLogger(__name__)
_READY = False
_CHECKED_AT = 0.0


def database():
    return router.db_for_write(TemplateVersion) or 'default'


def ready(force=False):
    """Old installations continue using their original renderer until deployment.

    No runtime DDL. The new admin page reports the required additive setup.
    Existing SQLite-only legacy tests do not enable this PostgreSQL integration.
    """
    global _READY, _CHECKED_AT
    connection = connections[database()]
    if not getattr(settings, 'DOCUMENT_TEMPLATES_ENABLED', connection.vendor == 'postgresql'):
        return False
    if connection.vendor == 'sqlite':
        return True
    if force or (not _READY and time.monotonic() - _CHECKED_AT > 10):
        with connection.cursor() as cursor:
            cursor.execute('SELECT ' + ', '.join(['to_regclass(%s)'] * 4),
                [f'enrolment."Document_Template_{name}"' for name in ('Versions', 'Drafts', 'Issues', 'Render_History')])
            _READY = all(cursor.fetchone())
        _CHECKED_AT = time.monotonic()
    return _READY


def actor(request):
    account = getattr(request, 'login_account', None)
    if account:
        return str(getattr(account, 'display_name', '') or getattr(account, 'email', '') or f'{account.role}:{account.subject_id}')
    user = getattr(request, 'user', None)
    return f'django:{user.get_username()}' if user and user.is_authenticated else 'system'


def reference(content, version=None):
    value = validate(content)
    return {'id': str(version.id) if version else 'builtin:' + digest(value), 'number': version.number if version else 0,
            'publishedAt': version.published_at.isoformat() if version else None,
            'publishedBy': version.published_by if version else 'Existing application template',
            'approvalNote': version.approval_note if version else '', 'content': value,
            'legacyEquivalent': legacy_equivalent(value), 'contentHash': digest(value), 'bindingVersion': 2}


def latest(identifier):
    row = TemplateVersion.objects.filter(template_id=identifier).order_by('-number').first() if ready() else None
    if row: return reference(row.content, row), row
    if identifier in seeds()['templates']: return reference(seed(identifier)), None
    raise TemplateError('This template does not have a published version.')


def current_versions():
    result = {key: reference(seed(key)) for key in seeds()['templates']}
    if ready():
        latest_number = TemplateVersion.objects.filter(template_id=OuterRef('template_id')).order_by('-number').values('number')[:1]
        for version in TemplateVersion.objects.filter(number=Subquery(latest_number)):
            result[version.template_id] = reference(version.content, version)
    return result


def index():
    available = ready()
    current = current_versions()
    drafts = {row.template_id: row for row in TemplateDraft.objects.all()} if available else {}
    result = []
    for identifier in sorted(set(current) | set(drafts)):
        published = current.get(identifier); draft = drafts.get(identifier)
        content = published['content'] if published else draft.content
        dirty = bool(draft and (not published or digest(validate(draft.content)) != published['contentHash']))
        result.append({'templateId': identifier, 'documentKey': content['documentKey'], 'title': content['title'],
                       'category': content['category'], 'programmes': content['programmes'], 'publishedVersion': published,
                       'draft': {'revision': draft.revision, 'updatedAt': draft.updated_at.isoformat(), 'updatedBy': draft.updated_by} if dirty else None,
                       'updatedAt': draft.updated_at.isoformat() if dirty else published['publishedAt'] if published else None})
    return {'results': result, 'programmes': PROGRAMMES, 'setupRequired': not available}


def normalise_name(value):
    return re.sub(r'[\s—–-]+', ' ', str(value or '')).strip().casefold()


def programme_id(learner):
    value = normalise_name(getattr(learner, 'programme', ''))
    return next((key for key, name in PROGRAMMES.items() if value in {normalise_name(key), normalise_name(name)}), None)


def applicable(key, learner):
    current = current_versions()
    pid = programme_id(learner)
    matches = [value for value in current.values() if value['content']['documentKey'] == key and pid and pid in value['content']['programmes']]
    if len(matches) > 1:
        raise TemplateError('Document programme applicability is ambiguous. Please ask the enrolment administrator to review the templates.')
    return matches[0] if matches else current[key]


def issuance_reference(key, learner):
    """New originals use the shared purple default; publications stay explicit."""
    selected = applicable(key, learner)
    if selected['number'] == 0:
        selected = reference({**selected['content'], 'designTheme': 'purple-reference-v2'})
    from .pdf_artifacts import new_presentation
    return new_presentation(selected)


def path_value(value, path):
    # Paths are taken ONLY from the repository-controlled registry, never eval.
    for part in path.split('.'):
        if not isinstance(value, dict): return None
        value = value.get(part)
    return value


def scalar(value):
    if isinstance(value, (date, datetime)): return value.isoformat()
    if isinstance(value, Decimal): return str(value)
    if value is None or isinstance(value, (str, int, float, bool)): return value
    return ''


def field_values(key, learner, payload):
    from .learner_sources import document_dates
    start, end, _ = document_dates(learner)
    common = {'learner.fullName': getattr(learner, 'username', ''), 'learner.id': str(learner.pk),
              'programme.name': getattr(learner, 'programme', ''), 'programme.cohort': getattr(learner, 'cohort', ''),
              'enrolment.startDate': start, 'enrolment.endDate': end,
              'employer.name': getattr(learner, 'employer', ''), 'employer.manager': getattr(learner, 'line_manager', '')}
    result = {}
    for ref, definition in seeds()['fields'][key].items():
        if ref.startswith('signature.'): continue
        source = definition.get('source', ref)
        value = path_value(payload, source[9:]) if source.startswith('document.') else common.get(source)
        if source == 'document.answers.eligibility.residentPrevious3Years':
            # Stable template identifier predates the wizard's actual saved key.
            eligibility = (payload.get('answers') or {}).get('eligibility') or {}
            value = eligibility.get('residentPrev3Years', eligibility.get('residentPrevious3Years'))
        if ref == 'document.epa.period':
            ep = payload.get('epa') or {}; value = ' - '.join(str(ep[k]) for k in ('epaPeriodFrom', 'epaPeriodTo') if ep.get(k))
        if value in (None, '') and 'fallback' in definition: value = definition['fallback']
        result[ref] = scalar(value)
    return result


def bind_issue(key, document, learner, payload):
    if not ready(): return None
    selected = issuance_reference(key, learner)
    selected['originalPdfVersion'] = 2 if selected.get('presentationTheme') else 1
    from .learner_sources import date_warnings
    selected['bindingWarnings'] = date_warnings(learner)
    version_id = None if selected['number'] == 0 else selected['id']
    binding = IssuedTemplate.objects.create(document_key=key, source_id=str(document.pk), learner_kind=document.learner_kind,
        learner_id=document.learner_id, version_id=version_id, template_snapshot=selected,
        field_values=field_values(key, learner, payload))
    logger.info('Document template bound key=%s source=%s learner=%s version=%s', key, document.pk, document.learner_id, selected['id'])
    if selected.get('presentationTheme'):
        from .pdf_artifacts import store_source
        store_source(binding, document, learner, original=True)
    return binding


def bind_review_issue(review, learner):
    # Only the issued header fields are frozen now. Review answers/context are
    # captured by the existing signature writer when the first party signs.
    # Issuing must not read or write unrelated Skills Radar/PLR answer stores.
    from learner_api.review_form import _learner_information
    from .registry import REVIEW_KEYS
    return bind_issue(REVIEW_KEYS[review.review_type], review, learner,
                      {'learnerInformation': _learner_information(learner, verified=True)})


def mirror_booked_review(model, event_key, defaults):
    """Keep the calendar's existing upsert, binding only freshly created rows."""
    if not ready(): return model.objects.update_or_create(event_key=event_key, defaults=defaults)
    with transaction.atomic(using=database()):
        review, created = model.objects.update_or_create(event_key=event_key, defaults=defaults)
        if created:
            from learner_api.learner_detail import SOURCE_MODELS
            source = SOURCE_MODELS.get(review.learner_kind)
            learner = source.all_learners.filter(pk=review.learner_id).first() if source else None
            if learner is not None: bind_review_issue(review, learner)
            else: logger.warning('Booked review %s has no source learner; retained its original template.', review.pk)
        return review, created


def binding_for(key, document):
    if not ready(): return None
    return IssuedTemplate.objects.filter(document_key=key, source_id=str(document.pk), learner_id=document.learner_id).first()


REVIEW_SNAPSHOT_KEYS = ('answers', 'learnerInformation', 'skillsRadar', 'uln', 'plrRecords', 'sections', 'reviewLabel', 'reviewedBy', 'scheduledDate', 'scheduledTime')


def with_template(key, document, payload):
    if document is None or not ready(): return payload
    binding = binding_for(key, document)
    if binding is None:
        # Pre-builder records use the retained original renderer, regardless of
        # what is published later. No historical source/file is rewritten.
        return {**payload, 'template': {**reference(seed(key)), 'bindingVersion': 1, 'legacy': True, 'fields': {}}}
    result = copy.deepcopy(payload)
    if key.startswith('review-'):
        if binding.review_snapshot:
            result.update(copy.deepcopy(binding.review_snapshot))
        else:
            for ref, value in binding.field_values.items():
                prefix = 'document.learnerInformation.'
                if ref.startswith(prefix): result.setdefault('learnerInformation', {})[ref[len(prefix):]] = value or ''
    result['template'] = {**copy.deepcopy(binding.template_snapshot), 'fields': copy.deepcopy(binding.field_values), 'issueId': str(binding.pk)}
    for role in seed(key)['signatureRoles']:
        state = (result.get('signatures') or {}).get(role) or {}
        result['template']['fields'][f'signature.{role}.name'] = state.get('name') or ''
        result['template']['fields'][f'signature.{role}.date'] = state.get('signedAt') or ''
    if binding.template_snapshot.get('presentationTheme'):
        from .pdf_artifacts import issued_url, fingerprint
        result['pdfUrl'] = issued_url(binding.learner_id, binding.pk, fingerprint(result))
    return result


def archive_render(key, document, payload, by=''):
    binding = binding_for(key, document)
    if binding is None: return
    payload = with_template(key, document, payload)
    fingerprint = digest(payload)
    RenderHistory.objects.get_or_create(issue=binding, fingerprint=fingerprint, defaults={'payload': payload, 'recorded_by': by})
    if binding.template_snapshot.get('presentationTheme'):
        from .pdf_artifacts import store
        store(binding, payload, actor=by)


def sign_review_with_history(review, party, signature, name, writer):
    from .registry import REVIEW_KEYS
    key = REVIEW_KEYS.get(review.review_type)
    from .revisions import source_locked, RevisionLocked
    if key and source_locked(key, review): raise RevisionLocked('This historical or fully signed document is locked. Create a new learner revision for changes.')
    binding = binding_for(key, review) if key else None
    if binding is None: return writer(review, party, signature, name)
    from learner_api.models import EnrolmentUser
    from learner_api.review_form import _serialize_form
    with transaction.atomic(using=database()):
        locked = type(review).objects.select_for_update().get(pk=review.pk)
        if key and source_locked(key, locked): raise RevisionLocked('This historical or fully signed document is locked. Create a new learner revision for changes.')
        binding = IssuedTemplate.objects.select_for_update().get(pk=binding.pk)
        result = writer(locked, party, signature, name)
        learner = EnrolmentUser.all_learners.get(pk=locked.learner_id)
        payload = _serialize_form(locked, learner, None)
        any_signature = any(bool((payload['signatures'].get(role) or {}).get('signed')) for role in ('learner', 'admin', 'employer'))
        if any_signature and binding.review_snapshot is None:
            binding.review_snapshot = {k: copy.deepcopy(payload[k]) for k in REVIEW_SNAPSHOT_KEYS if k in payload}
            binding.save(update_fields=['review_snapshot'])
        archive_render(key, locked, payload, name)
        if not any_signature and binding.review_snapshot is not None:
            # Existing signature withdrawal still works. Its earlier signed
            # rendering stays in immutable history; the next signing snapshots
            # the newly reviewed answers instead of replacing that history.
            binding.review_snapshot = None
            binding.save(update_fields=['review_snapshot'])
        review.__dict__.update(locked.__dict__)
        return result


def review_is_signed(review):
    if not ready(): return False
    from .registry import REVIEW_KEYS
    key = REVIEW_KEYS.get(review.review_type)
    return bool(key and binding_for(key, review) and (review.learner_signature or review.admin_signature or review.employer_signature))


def review_write_transaction(view):
    """Coordinate existing answer saves with the existing signature writer.

    Authentication remains outside this decorator. Locking the source row
    before reading it prevents a delayed whole-model save clearing new marks.
    """
    @wraps(view)
    def wrapped(request, kind, pk, event_key):
        if request.method not in {'PATCH', 'POST'} or not ready():
            return view(request, kind, pk, event_key)
        from learner_api.models import EnrolmentReview
        with transaction.atomic(using=database()):
            EnrolmentReview.objects.select_for_update().filter(event_key=event_key, learner_id=pk).first()
            result = view(request, kind, pk, event_key)
            if result.status_code == 200:
                from .pdf_artifacts import store_source
                from .registry import REVIEW_KEYS
                source = EnrolmentReview.objects.filter(event_key=event_key, learner_id=pk).first()
                binding = binding_for(REVIEW_KEYS.get(source.review_type), source) if source else None
                if binding and binding.template_snapshot.get('presentationTheme'):
                    from learner_api.models import EnrolmentUser
                    store_source(binding, source, EnrolmentUser.all_learners.get(pk=pk))
            return result
    return wrapped
