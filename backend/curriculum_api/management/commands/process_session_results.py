"""Run from the existing scheduler, separately from web requests."""
import json
import logging
import re
import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction
from django.test import RequestFactory

from curriculum_api.session_archive import archive_series, provision_container
from curriculum_api.session_component_identity import reconcile_component_links


def sync_post_lecture_feedback(series_id):
    """Best-effort side effect; it must not change the Teams sync verdict."""
    try:
        from engagement_api.feedback_delivery import sync_post_lecture_feedback_from_attendance
        sync_post_lecture_feedback_from_attendance(live_session_ids=[series_id])
    except Exception as feedback_failure:
        logging.getLogger(__name__).warning(
            'Live Sessions Feedback sync failed for %s: %s',
            series_id, type(feedback_failure).__name__,
        )


class Command(BaseCommand):
    help = 'Process saved session sync jobs. Schedule every five minutes; never run on a web request.'

    @staticmethod
    def _sync_error_summary(result):
        """Keep the failing Graph endpoint and status, never its path or response body."""
        labels = {'attendance': 'Attendance', 'recording': 'Recording',
                  'transcript': 'Transcript', 'attendance reporting': 'Attendance reporting'}
        summaries = []
        for raw in result.get('errors') or []:
            detail = str(raw)
            label = labels.get(detail.split(':', 1)[0].strip().lower())
            if label:
                status = re.search(r'\bHTTP ([1-5][0-9]{2})\b', detail)
                summaries.append(f'{label} sync failed' + (f' (HTTP {status.group(1)})' if status else ''))
            else:
                summaries.append('Teams returned incomplete results')
        return list(dict.fromkeys(summaries))

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=10)
        parser.add_argument('--provision-container', action='store_true')
        parser.add_argument('--scheduled', action='store_true', help='Discover files for linked meetings, including runs before their scheduled date.')
        parser.add_argument('--live-session-id', action='append', dest='live_session_ids', default=[],
                            help='Process only this series. May be supplied more than once.')

    def handle(self, *args, **options):
        if options['provision_container']:
            provision_container()
            self.stdout.write('Private recording container is available.')
            return
        requested_ids = list(dict.fromkeys(value.strip() for value in options.get('live_session_ids', []) if value.strip()))
        scope_params = [requested_ids] if requested_ids else []
        if options['scheduled']:
            scheduled_scope = ' AND o.live_session_id=ANY(%s)' if requested_ids else ''
            with connections['default'].cursor() as cursor:
                # A series can exhaust its attempts before its next lecture. Reopen
                # it sooner when a known recording still lacks a private archive.
                cursor.execute('''INSERT INTO curriculum.session_result_jobs(live_session_id)
                    SELECT DISTINCT o.live_session_id FROM curriculum.live_session_occurrences o
                    JOIN curriculum.live_sessions s ON s.id=o.live_session_id
                    JOIN curriculum.modules m ON m.module_catalogue_id=s.module_catalogue_id
                    WHERE m.deleted_at IS NULL AND NOT coalesce(m.is_programme_deleted,false)
                      AND coalesce(s.organizer_email,'')<>''
                      AND (coalesce(s.online_meeting_id,'')<>'' OR coalesce(s.join_url,'')<>'')
                      AND o.status NOT IN ('cancelled','deleted','superseded')
                      AND s.status NOT IN ('cancelled','deleted','superseded','failed')''' + scheduled_scope + '''
                    ON CONFLICT(live_session_id) DO UPDATE SET state='queued',requested_at=now(),next_attempt_at=now(),attempts=0,force_refresh=false
                    WHERE (session_result_jobs.state='complete'
                           AND (session_result_jobs.finished_at < now()-interval '24 hours'
                                OR (session_result_jobs.finished_at < now()-interval '5 minutes'
                                    AND EXISTS (
                                        SELECT 1 FROM curriculum.live_session_occurrences recent
                                        WHERE recent.live_session_id=session_result_jobs.live_session_id
                                          AND recent.status NOT IN ('cancelled','deleted','superseded')
                                          AND recent.scheduled_end BETWEEN
                                              (now() AT TIME ZONE 'UTC')-interval '48 hours'
                                              AND (now() AT TIME ZONE 'UTC')
                                    ))))
                       OR (session_result_jobs.state='failed'
                           AND (EXISTS (
                                    SELECT 1 FROM curriculum.live_session_occurrences later
                                    WHERE later.live_session_id=session_result_jobs.live_session_id
                                      AND later.status NOT IN ('cancelled','deleted','superseded')
                                      AND later.scheduled_end > (session_result_jobs.finished_at AT TIME ZONE 'UTC')
                                      AND later.scheduled_end <= (now() AT TIME ZONE 'UTC')
                                )
                                OR (session_result_jobs.attempts>=8
                                    AND (session_result_jobs.finished_at < now()-interval '6 hours'
                                         OR (session_result_jobs.finished_at < now()-interval '1 hour'
                                             AND EXISTS (
                                                SELECT 1 FROM curriculum.live_session_occurrences pending_occurrence
                                                JOIN curriculum.live_session_artifacts pending_artifact
                                                  ON pending_artifact.occurrence_id=pending_occurrence.id
                                                LEFT JOIN curriculum.session_result_archive pending_archive
                                                  ON pending_archive.artifact_id=pending_artifact.id
                                                WHERE pending_occurrence.live_session_id=session_result_jobs.live_session_id
                                                  AND pending_artifact.artifact_type='recording'
                                                  AND coalesce(pending_archive.status,'')<>'ready'
                                            )))))) ''', scope_params)
        processed, failed = 0, 0
        job_scope = ' AND live_session_id=ANY(%s)' if requested_ids else ''
        for _ in range(max(1, min(options['limit'], 100))):
            lease = uuid.uuid4().hex
            with transaction.atomic(), connections['default'].cursor() as cursor:
                cursor.execute('''SELECT live_session_id,force_refresh FROM curriculum.session_result_jobs
                    WHERE ((state IN ('queued','failed') AND next_attempt_at<=now() AND attempts<8)
                       OR (state='running' AND next_attempt_at < now()-interval '2 hours'))''' + job_scope + '''
                    ORDER BY requested_at FOR UPDATE SKIP LOCKED LIMIT 1''', scope_params)
                row = cursor.fetchone()
                if not row:
                    break
                series_id = row[0]
                cursor.execute("UPDATE curriculum.session_result_jobs SET state='running',started_at=now(),next_attempt_at=now(),lease_id=%s,attempts=attempts+1 WHERE live_session_id=%s", [lease, series_id])
            error = ''
            try:
                from curriculum_api.views import curriculum_teams_meeting_artifacts
                request = RequestFactory().post('/internal/session-result-worker/')
                request.session_result_worker = True
                request.session_result_force = row[1]
                # The lease names this run in the Teams integrity log, so a status
                # this job changes is traceable to the job that changed it.
                request.session_result_job_id = f'session-results:{lease}'
                response = curriculum_teams_meeting_artifacts(request, series_id)
                result = json.loads(response.content)
                errors = self._sync_error_summary(result)
                if response.status_code >= 300:
                    errors.append('Teams sync could not complete.')
                errors.extend(archive_series(series_id, lease_id=lease))
                try:
                    reconcile_component_links(series_id)
                except Exception as link_failure:
                    logging.getLogger(__name__).warning('Session component link repair failed: %s', type(link_failure).__name__)
                    errors.append('Curriculum recording link repair failed.')
                error = '; '.join(errors)
                if not error:
                    sync_post_lecture_feedback(series_id)
            except Exception as failure:
                logging.getLogger(__name__).warning('Session job %s failed: %s', series_id, type(failure).__name__)
                error = 'Session processing failed. Check Graph, database and Azure configuration.'
            with connections['default'].cursor() as cursor:
                cursor.execute('''UPDATE curriculum.session_result_jobs SET state=CASE WHEN requested_at>started_at THEN 'queued' ELSE %s END,finished_at=now(),last_error=%s,
                    next_attempt_at=CASE WHEN requested_at>started_at THEN now()
                        ELSE now()+least(interval '6 hours',interval '5 minutes'*power(2,least(attempts,6))) END
                    WHERE live_session_id=%s AND lease_id=%s''', ['failed' if error else 'complete', error[:1500], series_id, lease])
            processed += 1
            failed += bool(error)
        self.stdout.write(f'Processed {processed} session jobs; {failed} need retry.')
        if failed:
            error = CommandError('Some session jobs need retry; saved results remain available.')
            error.session_jobs_need_retry = True
            error.failed_jobs = failed
            raise error
