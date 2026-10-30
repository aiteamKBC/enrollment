"""Live, read-only projection of classified Aptem assignments. Never imports rows."""
import json
import mimetypes

from django.db import DatabaseError, connections
from django.http import HttpResponse, JsonResponse
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_GET
from login.permissions import learner_self_or_staff
from . import evidence_storage


CLASSIFIED_SQL = '''
    SELECT DISTINCT ON (e.evidence_id)
      e.evidence_id, e.learner_id AS aptem_id, e.component_id,
      e.evidence_name, e.component_name, e.evidence_status,
      e.file_blob, e.report_blob, e.feedbacks, e.submission_date,
      e.source_hash, e.updated_at AS source_updated_at,
      coalesce((e.completed_date_override AT TIME ZONE 'Europe/London')::date,
               (e.completed_date AT TIME ZONE 'Europe/London')::date,
               v.completed_date,
               (e.submission_date AT TIME ZONE 'Europe/London')::date) AS activity_date,
      a."FullName" AS learner_name, a."Program Name" AS programme_name,
      run.id AS run_id
    FROM enrolment."Created_users" u
    JOIN "LMS"."Aptem_users" a ON a."ID"::text = btrim(u.aptem_id)
      AND nullif(lower(btrim(u."Email")), '') = lower(btrim(a."Email"))
    JOIN LATERAL (
      SELECT r.id FROM fetching_evidence.assignment_classification_runs r
      WHERE r.learner_id = a."ID" AND r.status = 'completed'
      ORDER BY (r.source_fingerprint IS NOT NULL) DESC,
               r.completed_at DESC NULLS LAST, r.id DESC LIMIT 1
    ) run ON true
    JOIN fetching_evidence.assignment_classification_evaluations v
      ON v.run_id = run.id AND v.learner_id = a."ID"
    JOIN fetching_evidence.evidence_items e
      ON e.learner_id = v.learner_id AND e.component_id = v.component_id
      AND (e.evidence_id = v.evidence_id OR v.source_evidence_ids @> to_jsonb(e.evidence_id))
    WHERE lower(btrim(u."Learner_type")) = %s AND u.id::text = %s
      AND (%s = '' OR 'aptem:' || a."ID"::text || ':evidence:' || e.evidence_id::text = %s)
    ORDER BY e.evidence_id, v.id
'''


def classified_rows(kind, learner_id, activity_id=''):
    with connections['enrolment'].cursor() as cur:
        cur.execute(CLASSIFIED_SQL, [kind, str(learner_id), activity_id, activity_id])
        columns = [column[0] for column in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def classified_submission(row, kind, learner_id):
    evidence_id = row['evidence_id']
    activity_id = f"aptem:{row['aptem_id']}:evidence:{evidence_id}"
    completed = row['activity_date'].isoformat() if row['activity_date'] else ''
    feedbacks = row.get('feedbacks')
    if isinstance(feedbacks, str):
        try:
            feedbacks = json.loads(feedbacks)
        except ValueError:
            feedbacks = []
    documents = []
    for part in ('file', 'report'):
        if row.get(f'{part}_blob'):
            documents.append({'evidenceId': evidence_id, 'part': part,
                              'name': row['evidence_name'] if part == 'file' else 'Assessment report.pdf'})
    return {
        'id': activity_id, 'activityId': activity_id, 'activityType': 'assignment',
        'learnerKind': kind, 'learnerId': str(learner_id),
        'learnerName': row['learner_name'], 'programmeName': row['programme_name'],
        'activityTitle': row['evidence_name'], 'moduleTitle': row['component_name'],
        'weekTitle': '', 'status': (row['evidence_status'] or 'unknown').lower(),
        'dateCompleted': completed,
        'submittedAt': row['submission_date'].isoformat() if row['submission_date'] else None,
        'submissionOrigin': 'classified_legacy', 'locked': True,
        'monthlyAssignment': {'version': 2, 'month': completed[:7], 'step': 0},
        'legacyAssignment': {
            'runId': row['run_id'], 'aptemLearnerId': row['aptem_id'],
            'componentId': row['component_id'], 'evidenceIds': [evidence_id],
            'sourceStatus': row['evidence_status'], 'documents': documents,
            'feedbacks': feedbacks if isinstance(feedbacks, list) else [],
        },
    }


def classified_response(kind, learner_id, activity_id=''):
    rows = classified_rows(kind, learner_id, activity_id)
    submissions = [classified_submission(row, kind, learner_id) for row in rows]
    if submissions:
        from .legacy_marking import reviews_for_evidence
        marks = reviews_for_evidence(rows[0]['aptem_id'], [row['evidence_id'] for row in rows])
        for row, submission in zip(rows, submissions):
            submission['legacyAssignment']['lmsReviews'] = marks.get(int(row['evidence_id']), [])
    if activity_id:
        if submissions:
            from .assignment_content import load_assignment_content
            submissions[0]['legacyAssignment']['content'] = load_assignment_content(
                rows[0], submissions[0]['legacyAssignment']['feedbacks'])
        payload = {'submission': submissions[0] if submissions else None}
    else:
        payload = {'assignments': [
            {'id': s['id'], 'activityId': s['activityId'], 'title': s['activityTitle'],
             'moduleTitle': s['moduleTitle'], 'status': s['status'],
             'month': s['monthlyAssignment']['month'], 'dateCompleted': s['dateCompleted'],
             'submittedAt': s['submittedAt'], 'submissionOrigin': s['submissionOrigin']}
            for s in submissions
        ]}
    response = JsonResponse(payload)
    response['Cache-Control'] = 'private, no-store'
    return response



@require_GET
@learner_self_or_staff(query_param='learnerId')
def open_legacy_assignment_document(request, evidence_id):
    kind = request.GET.get('learnerKind')
    learner_id = request.GET.get('learnerId', '')
    activity_id = request.GET.get('activityId', '')
    part = request.GET.get('part', 'file')
    delivery = request.GET.get('delivery', 'url')
    if kind not in {'commercial', 'apprenticeship'} or part not in {'file', 'report'} or delivery not in {'url', 'content', 'preview'} or not activity_id:
        return JsonResponse({'error': 'Invalid assignment document request.'}, status=400)
    try:
        rows = classified_rows(kind, learner_id, activity_id)
        if not rows or rows[0]['evidence_id'] != evidence_id:
            return JsonResponse({'error': 'Assignment document not found.'}, status=404)
        document = rows[0]
    except DatabaseError:
        return JsonResponse({'error': 'Could not load the assignment document.'}, status=503)
    blob = document.get(f'{part}_blob')
    if not blob:
        return JsonResponse({'error': 'Assignment document not found.'}, status=404)
    if not evidence_storage.azure_configured():
        return JsonResponse({'error': 'Document storage is not configured.'}, status=503)
    name = document['evidence_name'] if part == 'file' else 'Assessment report.pdf'
    if delivery == 'preview':
        if part != 'file' or not str(name or '').lower().endswith(('.xlsx', '.zip')):
            return JsonResponse({'error': 'Unsupported preview format.'}, status=415)
        from .assignment_content import _cached_extract
        try:
            preview = _cached_extract(blob, name, (str(document.get('source_hash') or ''),
                                                  str(document.get('source_updated_at') or '')))
        except Exception:
            return JsonResponse({'error': 'Could not load the preview. Please retry.'}, status=503)
        response = JsonResponse(preview)
        response['Cache-Control'] = 'private, no-store'
        return response
    if delivery == 'content':
        if part != 'file' or not str(name or '').lower().endswith(('.docx', '.xlsx', '.zip')):
            return JsonResponse({'error': 'Unsupported content format.'}, status=415)
        try:
            content = evidence_storage.download_blob_bytes('fetch-aptem-evidences', blob, max_bytes=30 * 1024 * 1024)
        except ValueError:
            return JsonResponse({'error': 'This document exceeds the 30 MB preview limit.'}, status=413)
        except Exception:
            return JsonResponse({'error': 'Could not load the Word document. Please retry.'}, status=503)
        response = HttpResponse(content, content_type=mimetypes.guess_type(name)[0] or 'application/octet-stream')
        response['Content-Disposition'] = content_disposition_header(True, name)
        response['Cache-Control'] = 'private, no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        return response
    try:
        url = evidence_storage.get_read_sas('fetch-aptem-evidences', blob)
        download_url = evidence_storage.get_download_sas('fetch-aptem-evidences', blob, filename=name)
    except Exception:
        return JsonResponse({'error': 'Could not open the assignment file.'}, status=503)
    response = JsonResponse({'url': url, 'downloadUrl': download_url,
                             'contentType': mimetypes.guess_type(blob)[0] or ''})
    response['Cache-Control'] = 'private, no-store'
    return response
