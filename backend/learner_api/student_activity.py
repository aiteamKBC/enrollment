"""Learner-scoped view over the historical Last_audit activity mirror."""

import json
import re
import time
from uuid import UUID

from .journal_sources import learner_journal_view
from django.db import DatabaseError, connections
from django.http import JsonResponse, HttpResponseRedirect
from django.middleware.csrf import get_token
from django.views.decorators.http import require_GET, require_POST

from audit_api.last_audit_ledger_views import _connection, _is_completed
from audit_api.learner_exclusions import is_excluded_learner
from login.permissions import coach_owns_learner, learner_self_or_staff, learner_self_or_admin, staff_only, staff_owns_learner
from login.sessions import authenticate_request

from .learner_detail import SOURCE_MODELS
from .active_users import completed_hours_value_from_progress
from .models import EnrolmentUser, LearnerProfile, LearnerProgressEntry
from .learning_plan import _effective_plan_ids
from .student_activity_data import (read_audit_hour_totals, read_evidenced_ksb_counts_bulk,
                                    read_student_activity, read_student_material)
from .student_activity_access import student_activity_available
from .student_activity_data import (summarize_activities, read_curriculum_schedules,
                                    apply_curriculum_schedules, read_activity_sources,
                                    read_activity_source_issues)
from . import subject_store, subject_source
import logging
from .subject_content import (ContentUnavailable, build_material, public_quiz, as_list)
from .builder_activity_dates import read_builder_activity_dates

CURRENT_SUBJECTS_SQL = '''
    WITH source AS (
        SELECT id, CASE WHEN jsonb_typeof("Training_plan"::jsonb)='array'
            THEN "Training_plan"::jsonb ELSE "Learning_plan"::jsonb END AS plan
        FROM enrolment."Created_users" WHERE id=%s
    ), assigned AS (
        SELECT entry->>'moduleId' AS module_id FROM source
        CROSS JOIN LATERAL jsonb_array_elements(
            CASE WHEN jsonb_typeof(plan)='array' THEN plan ELSE '[]'::jsonb END
        ) entry
        UNION
        SELECT coalesce(m.curriculum_module_id,nullif(m.module_ref,'')) FROM source
        JOIN "Learner".learners l ON l.enrolment_id=source.id
        JOIN "Learner".learner_training_plan_modules m ON m.learner_id=l.id
        WHERE jsonb_typeof(source.plan) IS DISTINCT FROM 'array'
    )
    SELECT DISTINCT cm.module_catalogue_id,cm.title
    FROM assigned JOIN curriculum.modules cm ON cm.module_catalogue_id=assigned.module_id
    WHERE (cm.deleted_at IS NULL OR COALESCE(cm.deleted_via_parent, '') <> '')
'''

def _direct_progress_records(enrolment_id, *, profile=None):
    """Progress recorded after the historical audit snapshot was imported.

    The Last_audit total already covers every legacy subject displayed by this
    endpoint.  Only direct/current-platform rows are added here; including the
    imported normalized rows as well would count the historical time twice.
    """
    profile = profile or (
        LearnerProfile.objects.using('enrolment')
        .filter(enrolment_id=enrolment_id)
        .only('id')
        .first()
    )
    if profile is None:
        return []
    entries = (
        profile.progress_entries.using('enrolment')
        .filter(component_link_source__in=('direct', 'quiz_ref'), deleted_at__isnull=True)
        .exclude(kind='activity_event')
        .values(
            'id', 'kind', 'component_ref', 'quiz_ref', 'component_title', 'component_type',
            'module_title', 'week_title', 'reported_time', 'claimed_seconds',
            'verified_seconds', 'time_tracking_source', 'expected_otjh',
            'submitted_at', 'passed',
        )
    )
    records = [{
        'sourceRef': f"progress:{row['id']}" if row.get('id') is not None else None,
        'kind': row['kind'],
        'componentId': row['component_ref'],
        'quizId': row['quiz_ref'],
        'componentTitle': row['component_title'],
        'componentType': row['component_type'],
        'moduleTitle': row['module_title'],
        'weekTitle': row['week_title'],
        'reportedTime': row['reported_time'],
        'claimedSeconds': row['claimed_seconds'],
        'verifiedSeconds': row['verified_seconds'],
        'timeTrackingSource': row['time_tracking_source'],
        'expectedOtjh': float(row['expected_otjh']) if row['expected_otjh'] is not None else None,
        'submittedAt': row['submitted_at'].isoformat() if row['submitted_at'] else '',
        'passed': row['passed'],
    } for row in entries]
    component_ids = sorted({str(row['componentId']) for row in records if row.get('componentId')})
    if not component_ids:
        return records

    # Assignment marking is stored separately from the progress row. Keep the
    # coach decision beside the record so OTJH reporting can distinguish a
    # hand-in from an accepted assignment without changing quiz semantics.
    candidates = {str(enrolment_id), str(profile.id), str(profile.enrolment_id)}
    try:
        with connections['enrolment'].cursor() as cursor:
            cursor.execute('''SELECT DISTINCT ON (activity_id) activity_id,status
                FROM "Learner"."learning_reflection_submissions"
                WHERE learner_id::text=ANY(%s) AND activity_id=ANY(%s)
                ORDER BY activity_id,submitted_at DESC NULLS LAST''', [sorted(candidates), component_ids])
            statuses = {str(activity_id): str(status or '') for activity_id, status in cursor.fetchall()}
    except DatabaseError:
        # Older installations may not have the marking table yet. Preserve the
        # existing progress response rather than making the overview unavailable.
        statuses = {}
    for record in records:
        if record.get('componentId') in statuses:
            record['markingStatus'] = statuses[record['componentId']]
    return records


def load_direct_progress_records_bulk(enrolment_ids):
    """Load direct progress for several enrolments without per-learner ORM reads."""
    ids = [int(value) for value in dict.fromkeys(enrolment_ids or []) if value is not None]
    if not ids:
        return {}
    profiles = list(
        LearnerProfile.objects.using('enrolment')
        .filter(enrolment_id__in=ids)
        .only('id', 'enrolment_id')
    )
    result = {enrolment_id: [] for enrolment_id in ids}
    component_pairs = []
    entries_by_profile = {}
    entries = LearnerProgressEntry.objects.using('enrolment').filter(
        learner_id__in=[profile.id for profile in profiles],
        component_link_source__in=('direct', 'quiz_ref'),
        deleted_at__isnull=True,
    ).exclude(kind='activity_event').values(
        'id', 'learner_id', 'kind', 'component_ref', 'quiz_ref', 'component_title', 'component_type',
        'module_title', 'week_title', 'reported_time', 'claimed_seconds', 'verified_seconds',
        'time_tracking_source', 'expected_otjh', 'submitted_at', 'passed',
    )
    for row in entries:
        entries_by_profile.setdefault(int(row['learner_id']), []).append(row)
    for profile in profiles:
        rows = entries_by_profile.get(int(profile.id), [])
        records = [{
            'sourceRef': f"progress:{row['id']}" if row.get('id') is not None else None,
            'kind': row['kind'], 'componentId': row['component_ref'], 'quizId': row['quiz_ref'],
            'componentTitle': row['component_title'], 'componentType': row['component_type'],
            'moduleTitle': row['module_title'], 'weekTitle': row['week_title'],
            'reportedTime': row['reported_time'], 'claimedSeconds': row['claimed_seconds'],
            'verifiedSeconds': row['verified_seconds'], 'timeTrackingSource': row['time_tracking_source'],
            'expectedOtjh': float(row['expected_otjh']) if row['expected_otjh'] is not None else None,
            'submittedAt': row['submitted_at'].isoformat() if row['submitted_at'] else '',
            'passed': row['passed'],
        } for row in rows]
        result[int(profile.enrolment_id)] = records
        component_pairs.append((profile, records))
    component_ids = sorted({str(record['componentId']) for _, records in component_pairs for record in records if record.get('componentId')})
    if not component_ids:
        return result
    candidates = sorted({str(value) for profile, _ in component_pairs for value in (profile.enrolment_id, profile.id)})
    try:
        with connections['enrolment'].cursor() as cursor:
            cursor.execute('''SELECT DISTINCT ON (learner_id::text, activity_id) learner_id::text, activity_id, status
                FROM "Learner"."learning_reflection_submissions"
                WHERE learner_id::text=ANY(%s) AND activity_id=ANY(%s)
                ORDER BY learner_id::text, activity_id, submitted_at DESC NULLS LAST''', [candidates, component_ids])
            statuses = {(learner, str(activity)): str(status or '') for learner, activity, status in cursor.fetchall()}
    except DatabaseError:
        statuses = {}
    for profile, records in component_pairs:
        for record in records:
            status = statuses.get((str(profile.enrolment_id), str(record.get('componentId')))) or statuses.get((str(profile.id), str(record.get('componentId'))))
            if status is not None:
                record['markingStatus'] = status
    return result


def _direct_progress_otjh(progress):
    return completed_hours_value_from_progress(progress)


def combined_recorded_otjh(historical_total, direct_total):
    """All recorded learner time across displayed legacy and current subjects."""
    if historical_total is None and not direct_total:
        return None
    return round(float(historical_total or 0) + float(direct_total or 0), 4)


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


def _live_subjects(source, aptem_id):
    started = time.monotonic()
    with _connection().cursor() as cursor:
        result = subject_source.read_learner(cursor, aptem_id, getattr(source, 'email', ''))
    logger.info('learner_live_source stage=live_subjects result=%s aptem_id=%s elapsed_ms=%d', 'success' if result is not None else 'historical_fallback', aptem_id, int((time.monotonic() - started) * 1000))
    return result


def _activity_sources(enrolment_id, group_ids):
    with connections['enrolment'].cursor() as cursor:
        cursor.execute(CURRENT_SUBJECTS_SQL, [enrolment_id])
        module_ids = [row[0] for row in cursor.fetchall()]
        return read_activity_sources(cursor, group_ids, module_ids)


def _activity_source_issues(enrolment_id, group_ids):
    with connections['enrolment'].cursor() as cursor:
        cursor.execute(CURRENT_SUBJECTS_SQL, [enrolment_id])
        module_ids = [row[0] for row in cursor.fetchall()]
        return read_activity_source_issues(cursor, group_ids, module_ids)


@require_GET
@learner_self_or_staff(kwarg="pk")
@learner_journal_view
def student_activity(request, kind, pk):
    """Return LMS activities for the requested learner, never for a client id.

    This deliberately resolves Aptem identity from enrolment.Created_users.
    The caller supplies only the learner record id already protected by the
    ownership gate, which prevents changing a query string to inspect another
    learner's audit history.
    """
    model = SOURCE_MODELS.get(kind)
    if model is None:
        return _error("Learner not found.", 404)

    try:
        from .case_file_sources import source_for_case_file
        source = source_for_case_file(request, kind, pk)
        if source is None:
            source = model.all_learners.only("id", "aptem_id", "email").get(pk=pk)
    except model.DoesNotExist:
        return _error("Learner not found.", 404)
    except DatabaseError:
        return _error("Could not read the learner record.", 503)

    if request.GET.get('activity_id') is None:
        from . import canonical_learning
        from .replacement_hours import read_replacement_hours
        try:
            owner = canonical_learning.profile(pk)
            # Share the exact accepted ledger snapshot with the course catalogue;
            # summing separately loaded old/current aggregates can double count.
            records = canonical_learning.entries_for(owner) if owner is not None else None
            payload = canonical_learning.source_subjects(pk, summarize_activities, owner=owner, records=records,
                include_ledger=request.GET.get('view') != 'my-learning')
            # Historical describes lineage, not a failed refresh. This read
            # verified the canonical owner and evidence successfully.
            payload['verification_status'] = 'verified'
            payload['replacement_hours'] = read_replacement_hours(pk, source, owner, records, payload)
            allowed = {f"legacy:{item['id']}" for item in payload['subjects']}
            covers = subject_store.covers_for(sorted(allowed)) if payload['aptem_id'] else {}
            payload['covers'] = {key: _cover_url(value) for key, value in covers.items() if key in allowed}
            return _private(payload)
        except canonical_learning.ServiceError as error:
            return _error(str(error), error.status)
        except DatabaseError:
            return _error('Could not load the consolidated learning record. Please retry.', 503)

    if not student_activity_available(source.aptem_id):
        from . import canonical_learning
        try:
            from .progress_demo import require_activity
            from .legacy_quiz_review import read_review
            group_id, activity_id = int(request.GET.get('group_id', '')), int(request.GET['activity_id'])
            catalogue_kind = request.GET.get('catalogue_kind', 'material')
            if catalogue_kind not in ('material', 'quiz') or min(group_id, activity_id) <= 0:
                return _error('Invalid activity reference.', 400)
            owner = require_activity(source, group_id, activity_id, catalogue_kind)
            if catalogue_kind == 'quiz':
                return _private(read_review(owner, group_id, activity_id, demo_preview=True))
            _, stored = _owned_material(kind, pk, group_id, activity_id, demo_preview=True)
            return _material_response(request, pk, None, stored, kind=kind, group_id=group_id)
        except (ValueError, LookupError):
            return _error('No demonstration activity is assigned to this learner.', 404)
        except DatabaseError:
            return _error('Could not read the demonstration activity. Please retry.', 503)
        except canonical_learning.ServiceError as error:
            return _error(str(error), error.status)

    try:
        aptem_id = int(str(source.aptem_id or "").strip())
    except (TypeError, ValueError):
        return _error("This learner is not linked to Aptem.", 404)
    if not student_activity_available(aptem_id):
        return _error("This learner is not linked to Aptem.", 404)

    if request.GET.get('catalogue_kind') == 'quiz':
        from . import canonical_learning
        from .legacy_quiz_review import read_review
        try:
            group_id = int(request.GET.get('group_id', ''))
            quiz_id = int(request.GET.get('activity_id', ''))
            if not all(0 < value <= 9223372036854775807 for value in (group_id, quiz_id)):
                raise ValueError()
        except (ValueError, TypeError):
            return _error('Invalid quiz reference.', 400)
        try:
            owner = canonical_learning.require_profile(pk)
            if owner['aptem_id'] != aptem_id or not _emails_match(owner['email'], source.email):
                return _error('The consolidated learner identity needs review.', 409)
            payload = read_review(owner, group_id, quiz_id, allow_attempts=True)
            account = authenticate_request(request)
            payload['can_attempt'] = bool(payload['can_attempt'] and account and (
                account.role == 'admin'
                or (account.role == 'learner' and int(account.subject_id) == pk)
                or coach_owns_learner(account, pk)
                or staff_owns_learner(account, pk)))
            payload['csrf_token'] = get_token(request)
            return _private(payload)
        except canonical_learning.ServiceError as error:
            return _error(str(error), error.status)
        except DatabaseError:
            return _error('Could not load the saved quiz. Please retry.', 503)

    # The catalogue branch above always returns. Only an explicitly selected,
    # owned material reaches this reader; never rebuild the historical summary.
    try:
        activity_id = int(request.GET['activity_id'])
        group_id = int(request.GET.get('group_id', ''))
    except (ValueError, TypeError):
        return _error('Invalid activity reference.', 400)
    try:
        with _connection().cursor() as cursor:
            payload = read_student_material(cursor, aptem_id, group_id, activity_id, include_source=True)
    except DatabaseError:
        return _error('Could not read Last_audit activities. Please try again.', 503)
    if payload is None:
        return _error('Activity not found.', 404)
    if is_excluded_learner(aptem_id, payload.get('learner_name', '')):
        return _error('No audit activity record is linked to this learner.', 404)
    audit_email = payload.pop('_identity_email', '') or payload.get('_source', {}).get('learner_email', '')
    if not _emails_match(getattr(source, 'email', ''), audit_email):
        return _error('The previous learning identity could not be verified.', 404)
    return _material_response(request, pk, aptem_id, payload, kind=kind, group_id=group_id)


def _private(payload, status=200):
    response = JsonResponse(payload, status=status)
    response['Cache-Control'] = 'private, no-store'
    return response


def _emails_match(current, audited):
    current, audited = str(current or '').strip().casefold(), str(audited or '').strip().casefold()
    return not current or not audited or current == audited


def _cover_url(path):
    from curriculum_api.upload_storage import UPLOAD_URL_PREFIX, blob_name_for
    return UPLOAD_URL_PREFIX + blob_name_for(path)


def _definition_for(stored, group_id=None, *, attachment_resolver=None):
    row = stored['_source']
    quiz_id = row.get('quiz_id')
    # Never fetch definitions from the old LMS during learner requests.
    schema = row.get('_material_schema')
    def archive_url(reference):
        from .media_proxy import _legacy_attachment_upload_path

        path = _legacy_attachment_upload_path(reference)
        return '/curriculum_api/curriculum/uploads/' + path if path else ''
    definition = build_material(stored, schema, attachment_resolver=archive_url if attachment_resolver is None else attachment_resolver)
    if group_id is not None and quiz_id and definition.get('quiz') and not definition['quiz']['ready']:
        from .subject_quiz import imported_quiz
        try:
            with _connection().cursor() as cursor:
                recovered_quiz = imported_quiz(cursor, group_id, quiz_id)
        except DatabaseError:
            recovered_quiz = None
        if recovered_quiz:
            from .subject_content import quiz_definition
            definition['quiz'] = quiz_definition(recovered_quiz)
            definition['available'] = True
    if row.get('_material_blob_ready'):
        import mimetypes
        from urllib.parse import unquote, urlsplit

        mime = row.get('material_blob_content_type') or ''
        media_kind = 'pdf' if mime == 'application/pdf' else 'audio' if mime.startswith('audio/') else 'video' if mime.startswith('video/') else 'document'
        file_name = (row.get('material_blob_name') or '').rsplit('/', 1)[-1]
        if not mimetypes.guess_type(file_name)[0]:
            file_name = stored['title']
            if not mimetypes.guess_type(file_name)[0]:
                file_name += mimetypes.guess_extension(mime) or ''
        primary = {'kind': media_kind, 'url': '', 'title': stored['title'],
            'file_name': file_name or stored['title'], 'source_material_file': True, 'can_embed': True}
        archive_path = '/curriculum_api/curriculum/uploads/' + (row.get('material_blob_name') or '')
        # Re-linked legacy lessons can also contain a reading companion or
        # other attachments. Replace only their exact registered primary file.
        def is_registered_file(value):
            parsed = urlsplit(value or '')
            return not parsed.netloc and unquote(parsed.path) == archive_path
        archived = [item for item in definition['media'] if is_registered_file(item['url'])]
        registered_primary = any(is_registered_file(stored.get(field))
                                 for field in ('video_url', 'audio_url', 'reading_url'))
        if archived or registered_primary:
            definition['media'] = [primary, *[item for item in definition['media'] if item not in archived]]
        else:
            definition['media'] = [primary]
        definition['available'] = True
        definition['has_reading'] = definition['has_reading'] or media_kind in {'pdf', 'document'}
    definition['source_live'] = False
    if row.get('quiz_definition_ambiguous') and definition.get('quiz'):
        # The same reading is linked to different quizzes in the original LMS.
        # Show its content/history, but do not grade a newly invented selection.
        definition['quiz'].update(ready=False, message='The programme team needs to confirm which quiz belongs to this activity.')
    return definition


def _local_pdf_urls(definition, kind, pk, group_id, activity_id):
    if not kind or group_id is None:
        return definition
    return {**definition, 'media': [
        {**{key: value for key, value in item.items() if key != 'source_material_file'}, 'url': f'/learner_api/student-activity/{kind}/{pk}/{group_id}/{activity_id}/source-file/'}
        if item.get('source_material_file') else
        {**item, 'url': f'/learner_api/student-activity/{kind}/{pk}/{group_id}/{activity_id}/files/{item["attachment_id"]}/'}
        if item.get('kind') == 'pdf' and item.get('attachment_id') else item
        for item in definition.get('media', [])
    ]}


def _material_response(request, pk, aptem_id, stored, *, kind=None, group_id=None, attachment_resolver=None):
    row = stored['_source']
    definition = _local_pdf_urls(_definition_for(stored, group_id, attachment_resolver=attachment_resolver),
                                 kind, pk, group_id, row['activity_id'])
    try:
        saved = subject_store.state(pk, aptem_id, row['activity_id'], group_id=group_id)
    except DatabaseError:
        return _error('Could not load your attempt history. Please try again.', 503)
    account = authenticate_request(request)
    historical_answers = []
    for answer in as_list(row.get('quiz_answers')):
        if not isinstance(answer, dict):
            continue
        # Historical learner answers are reviewable; never forward their grading
        # keys as an indirect answer key for an open retake.
        historical_answers.append({key: answer.get(key) for key in (
            'question_id', 'question_body', 'question_type', 'learner_answer', 'is_correct',
        )})
    payload = {**definition, 'quiz': public_quiz(definition['quiz']),
               'learner_name': stored['learner_name'], 'history': saved['history'],
               'completed': _is_completed(row) or any(attempt.get('completed') for attempt in saved['history']),
               'historical': {'score': row.get('quiz_score'), 'maximum_score': row.get('result_maximum_score'),
                              'passed': row.get('quiz_passed'), 'attempt_number': row.get('quiz_attempt_number'),
                              'answers': historical_answers, 'status': row.get('status')},
               'persistence_ready': saved['ready'],
               'can_attempt': bool(aptem_id and account and saved['ready'] and (
                   account.role == 'admin'
                   or (account.role == 'learner' and int(account.subject_id) == pk)
                   or coach_owns_learner(account, pk)
                   or staff_owns_learner(account, pk)
               )),
               'csrf_token': get_token(request)}
    return _private(payload)


def _owned_quiz(kind, pk, group_id, quiz_id):
    from . import canonical_learning
    from .legacy_quiz_review import read_review
    model = SOURCE_MODELS.get(kind)
    if model is None:
        raise LookupError('Learner not found.')
    try:
        source = model.all_learners.only('id', 'aptem_id', 'email').get(pk=pk)
    except model.DoesNotExist as error:
        raise LookupError('Learner not found.') from error
    if not student_activity_available(source.aptem_id):
        raise LookupError('No previous learning is linked to this learner.')
    owner = canonical_learning.require_profile(pk)
    if owner['aptem_id'] != int(str(source.aptem_id).strip()) or not _emails_match(owner['email'], source.email):
        raise canonical_learning.ServiceError('The consolidated learner identity needs review.', 'identity_conflict', 409)
    return owner['aptem_id'], read_review(owner, group_id, quiz_id, allow_attempts=True)


def _owned_material(kind, pk, group_id, activity_id, *, demo_preview=False):
    model = SOURCE_MODELS.get(kind)
    if model is None:
        raise LookupError('Learner not found.')
    try:
        source = model.all_learners.only('id', 'aptem_id', 'email').get(pk=pk)
    except model.DoesNotExist as error:
        raise LookupError('Learner not found.') from error
    if not student_activity_available(source.aptem_id):
        if not demo_preview:
            raise LookupError('No previous learning is linked to this learner.')
        from .progress_demo import require_activity
        require_activity(source, group_id, activity_id)
        with _connection().cursor() as cursor:
            stored = read_student_material(cursor, None, group_id, activity_id,
                include_source=True, demo_enrolment_id=pk)
        if stored is None:
            raise LookupError('Activity not found.')
        return None, stored
    aptem_id = int(str(source.aptem_id).strip())
    with _connection().cursor() as cursor:
        stored = read_student_material(cursor, aptem_id, group_id, activity_id, include_source=True)
    if is_excluded_learner(aptem_id, (stored or {}).get('learner_name', '')):
        raise LookupError('Activity not found.')
    if not _emails_match(getattr(source, 'email', ''), (stored or {}).get('_source', {}).get('learner_email', '')):
        raise LookupError('The previous learning identity could not be verified.')
    if stored is None:
        raise LookupError('Activity not found.')
    return aptem_id, stored


@require_GET
@learner_self_or_staff(kwarg='pk')
def source_material_file(request, kind, pk, group_id, activity_id):
    from django.conf import settings
    from .material_storage import (
        OFFICE_PREVIEW_SUFFIXES, curriculum_archive_url, file_response, office_preview_response,
    )
    try:
        _aptem_id, stored = _owned_material(kind, pk, group_id, activity_id, demo_preview=True)
        row = stored['_source']
        if not row.get('_material_blob_ready'):
            return _error('This file has not been copied to the LMS yet.', 404)
        archive_url = curriculum_archive_url(row, settings)
        if archive_url:
            if str(request.GET.get('preview') or '').lower() in {'1', 'true', 'yes'}:
                archive_url += '?preview=1'
            response = HttpResponseRedirect(archive_url)
        elif row.get('material_blob_content_type') == 'application/pdf':
            from .material_storage import pdf_response
            response = pdf_response(row, settings, request)
        elif (str(request.GET.get('preview') or '').lower() in {'1', 'true', 'yes'}
              and str(row.get('material_blob_name') or '').lower().endswith(OFFICE_PREVIEW_SUFFIXES)):
            response = office_preview_response(row, settings)
        else:
            # Streamed, not redirected: the inline Word/Excel preview fetches
            # this, and Azure's response carries no CORS headers for this site.
            response = file_response(row, settings, request)
        response['Cache-Control'] = 'private, no-store'
        response['Referrer-Policy'] = 'no-referrer'
        return response
    except LookupError:
        return _error('Activity not found.', 404)
    except ValueError:
        return _error('File storage is temporarily unavailable.', 503)
    except DatabaseError:
        return _error('Could not load this file. Please try again.', 503)


@require_GET
@learner_self_or_staff(kwarg='pk')
def subject_file(request, kind, pk, group_id, activity_id, attachment_id):
    from .subject_content import _attachment_id
    from .media_proxy import _legacy_attachment_upload_path
    try:
        _aptem_id, stored = _owned_material(kind, pk, group_id, activity_id, demo_preview=True)
        # Previously issued file links keep working when the attachment has
        # been archived. Never stream missing bytes from the old LMS.
        owned = {_attachment_id(stored.get(field) or '')
                 for field in ('reading_url', 'audio_url', 'video_url')}
        if str(attachment_id) not in owned:
            return _error('File not found for this activity.', 404)
        path = _legacy_attachment_upload_path(str(attachment_id))
        if not path:
            return _error('This file has not been copied to the LMS yet.', 404)
        return HttpResponseRedirect('/curriculum_api/curriculum/uploads/' + path)
    except LookupError:
        return _error('Activity not found.', 404)
    except (DatabaseError, ContentUnavailable):
        return _error('Could not load this file. Please try again.', 503)


@require_POST
@learner_self_or_admin(kwarg='pk')
def start_subject_attempt(request, kind, pk, group_id, activity_id):
    from . import canonical_learning
    try:
        catalogue_kind = request.GET.get('catalogue_kind', 'material')
        if catalogue_kind == 'quiz':
            from .subject_content import quiz_definition
            from .subject_quiz import imported_quiz
            aptem_id, stored = _owned_quiz(kind, pk, group_id, activity_id)
            if stored['completed']:
                return _error('This quiz is already complete. You can review its saved results.', 409)
            with _connection().cursor() as cursor:
                original = imported_quiz(cursor, group_id, activity_id)
            if not original:
                return _error('The original quiz is not available for grading yet.', 409)
            definition = {'title': stored['title'], 'quiz': quiz_definition(original),
                'catalogue_kind': 'quiz', 'has_reading': False, 'available': True, 'media': [], 'reading_html': ''}
        elif catalogue_kind == 'material':
            aptem_id, stored = _owned_material(kind, pk, group_id, activity_id)
            definition = _definition_for(stored, group_id)
        else:
            return _error('Invalid activity kind.', 400)
        if definition.get('quiz') and not definition['quiz']['ready']:
            return _error(definition['quiz']['message'], 409)
        if not definition['available']:
            return _error('No activity content is available yet.', 409)
        payload = json.loads(request.body) if request.body else {}
        if not isinstance(payload, dict):
            return _error('Invalid attempt request.', 400)
        from .retake_timing import choice
        retake_choice = choice(payload.get('retakeTimeChoice'))
        if retake_choice is not None:
            definition['retakeTimeChoice'] = retake_choice
        timing = None
        if payload.get('track_time') is True:
            from .subject_timing import create_session
            attempt_id, timing = create_session(kind, pk, group_id, activity_id, definition)
            subject_store.start(pk, aptem_id, group_id, activity_id, definition,
                                attempt_id=attempt_id, timing_session=timing)
        else:
            attempt_id = subject_store.start(pk, aptem_id, group_id, activity_id, definition)
        public = _local_pdf_urls(definition, kind, pk, group_id, activity_id)
        return _private({'attempt_id': attempt_id, 'definition': {**public, 'quiz': public_quiz(definition['quiz'])},
                         **({'timing': timing} if timing else {})}, 201)
    except canonical_learning.ServiceError as error:
        return _error(str(error), error.status)
    except LookupError as error:
        return _error(str(error), 404)
    except (ContentUnavailable, subject_store.StoreUnavailable) as error:
        return _error(str(error), 503)
    except (ValueError, TypeError):
        return _error('Invalid attempt request.', 400)
    except DatabaseError:
        return _error('Could not start this attempt. Please try again.', 503)


@require_POST
@learner_self_or_admin(kwarg='pk')
def submit_subject_attempt(request, kind, pk, group_id, activity_id, attempt_id):
    from . import canonical_learning
    from .working_rules import DeclaredCompletionError, validation_response_payload
    try:
        if len(request.body) > 256 * 1024:
            return _error('The submitted answers are too large.', 413)
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise ValueError('Invalid submission.')
        UUID(str(attempt_id))
        catalogue_kind = request.GET.get('catalogue_kind', 'material')
        if catalogue_kind not in {'material', 'quiz'}:
            return _error('Invalid activity kind.', 400)
        reader = _owned_quiz if catalogue_kind == 'quiz' else _owned_material
        aptem_id, _stored = reader(kind, pk, group_id, activity_id)
        result = subject_store.finish(pk, aptem_id, group_id, activity_id, str(attempt_id),
                                      payload.get('answers', {}), payload.get('reading_confirmed') is True,
                                      catalogue_kind=catalogue_kind, learner_kind=kind, timing_payload=payload)
        return _private(result)
    except canonical_learning.ServiceError as error:
        return _error(str(error), error.status)
    except LookupError as error:
        return _error(str(error), 404)
    except subject_store.StoreUnavailable as error:
        return _error(str(error), 503)
    except DeclaredCompletionError as error:
        return _private(validation_response_payload(error), 422)
    except (ValueError, TypeError) as error:
        return _error(str(error) if not isinstance(error, json.JSONDecodeError) else 'Invalid submission.', 400)
    except DatabaseError:
        return _error('Could not save your attempt. Please try again.', 503)


def _builder_cover_url(value):
    """Module Builder stores uploaded artwork as image data URLs.

    These are allowed only for an image source, never for activity links/iframes.
    """
    from .subject_content import safe_url
    value = str(value or '').strip()
    if len(value) <= 4 * 1024 * 1024 + 256 and re.fullmatch(
        r'data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+', value,
    ):
        return value
    return safe_url(value)


def _builder_subject_metadata(cursor, refs):
    """Resolve current module IDs without matching historical source records."""
    from .subject_content import clean_text
    native = [ref[8:] for ref in refs if ref.startswith('current:')]
    if not native:
        return {}, {}
    cursor.execute('''
        SELECT m.module_catalogue_id,m.title,m.cover_image_url
        FROM curriculum.modules m
        WHERE (m.deleted_at IS NULL OR COALESCE(m.deleted_via_parent, '') <> '')
          AND m.module_catalogue_id=ANY(%s)
    ''', [native])
    covers, links = {}, {}
    for module_id, title, cover in cursor.fetchall():
        row = {'id': module_id, 'title': clean_text(title), 'cover': _builder_cover_url(cover)}
        if module_id in native:
            ref = f'current:{module_id}'
            covers[ref] = row['cover']
            links[ref] = {'id': row['id'], 'title': row['title']}
    return covers, links


def builder_subject_links(cursor, source, refs, read=None):
    """``_builder_subject_metadata`` links, reading only refs this request has not resolved.

    Each ref resolves independently, so a Dashboard tab that resolves the plan's
    modules and then its components' modules reads each module once. Without the
    cache ``read_tab`` installs on its request-scoped source, this is one read.
    ``read`` lets a caller keep its own module-level reader binding.
    """
    read = read or _builder_subject_metadata
    cache = getattr(source, '_builder_link_cache', None)
    if cache is None:
        return read(cursor, refs)[1]
    wanted = list(dict.fromkeys(refs))
    missing = [ref for ref in wanted if ref not in cache]
    if missing:
        _, found = read(cursor, missing)
        cache.update({ref: found.get(ref) for ref in missing})
    return {ref: dict(cache[ref]) for ref in wanted if cache[ref] is not None}


def _effective_current_subjects(cursor, learner_id):
    """Return the same effective module set shown by the enrolment plan.

    A saved plan can gain modules inherited from its group after it was last
    agreed.  Reading only the stored JSON made My Learning omit those modules
    until somebody saved the plan again, while the enrolment modal already
    counted them.  Reuse the plan's effective-assignment rule so both screens
    agree without writing anything during a learner read.
    """
    learner = EnrolmentUser.all_learners.only(
        'programme', 'group', 'learning_plan', 'training_plan',
    ).get(pk=learner_id)
    module_ids = _effective_plan_ids(learner, {})
    if not module_ids:
        return []
    cursor.execute('''
        SELECT module_catalogue_id,title
        FROM curriculum.modules
        WHERE module_catalogue_id=ANY(%s)
          AND (deleted_at IS NULL OR deleted_via_parent IS NOT NULL)
    ''', [module_ids])
    titles = {module_id: title for module_id, title in cursor.fetchall()}
    return [
        {'id': module_id, 'title': titles[module_id]}
        for module_id in module_ids if module_id in titles
    ]


@require_GET
@learner_self_or_staff(kwarg='pk')
def subject_covers(request, pk):
    refs = list(dict.fromkeys(request.GET.get('refs', '').split(',')))
    if len(refs) > 150 or any(len(ref) > 180 for ref in refs):
        return _error('Invalid subject list.', 400)
    try:
        with connections['enrolment'].cursor() as cur:
            available = subject_store.ready(cur)
            covers = {}
            if available:
                cur.execute(f'SELECT subject_ref,storage_path FROM {subject_store.COVERS} WHERE subject_ref=ANY(%s)', [refs])
                covers = {key: _cover_url(path) for key, path in cur.fetchall()}
            current_subjects = _effective_current_subjects(cur, pk)
            builder_covers, builder_subjects = _builder_subject_metadata(
                cur, list(dict.fromkeys(refs + [f"current:{subject['id']}" for subject in current_subjects])),
            )
            covers.update(builder_covers)
            dates = read_builder_activity_dates(cur, [subject['id'] for subject in current_subjects])
            from .previous_learning_groups import read_previous_learning_groups
            previous_groups = read_previous_learning_groups(cur, pk, current_subjects)
            from .subject_origins import read_imported_courses, read_replacement_courses
            imported_courses = read_imported_courses(cur, current_subjects)
            replacement_courses = read_replacement_courses(cur, current_subjects, pk)
        from .progress_transfer import cover_transfer_metadata
        return _private({'covers': covers, 'activity_dates': dates, 'current_subjects': current_subjects,
                         'builder_subjects': builder_subjects, 'previous_learning_groups': previous_groups,
                         'imported_courses': imported_courses, 'replacement_courses': replacement_courses,
                         **cover_transfer_metadata(pk)})
    except canonical_learning.ServiceError as error:
        return _error(str(error), error.status)
    except DatabaseError:
        return _error('Could not load subject images.', 503)


@require_POST
@staff_only()
def upload_subject_cover(request, subject_ref):
    # Keep a clear response for an older browser tab instead of writing a second
    # cover that would disagree with the module's own artwork.
    return _error('Manage this image in Module Builder using Upload image.', 409)
logger = logging.getLogger(__name__)
