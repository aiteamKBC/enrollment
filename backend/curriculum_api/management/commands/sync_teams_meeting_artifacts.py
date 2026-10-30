from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from coach_api.views import has_graph_credentials


class Command(BaseCommand):
    help = (
        'Pull Microsoft Teams attendance, transcripts and recordings for '
        'linked curriculum meetings and recently ended coach meetings. Run every five minutes.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--lookback-hours',
            type=int,
            default=24,
            help='Coach meeting lookback in hours (default: 24); curriculum discovery includes early runs.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=100,
            help='Maximum number of meeting series to sync in one run (default: 100).',
        )
        parser.add_argument(
            '--live-session-id',
            action='append',
            dest='live_session_ids',
            default=[],
            help='Sync only this live-session series. May be supplied more than once.',
        )
        parser.add_argument(
            '--skip-coach-meetings',
            action='store_true',
            help='Only sync curriculum live sessions; skip MCM/PR/catch-up coach meeting snapshots.',
        )
        parser.add_argument(
            '--coach-limit',
            type=int,
            default=100,
            help='Maximum number of recently ended coach meetings to sync in one run (default: 100).',
        )

    def handle(self, *args, **options):
        if not has_graph_credentials():
            raise CommandError('Microsoft Graph credentials are not configured.')

        lookback_hours = max(1, int(options['lookback_hours']))
        limit = max(1, int(options['limit']))
        coach_limit = max(1, int(options['coach_limit']))
        requested_ids = [value.strip() for value in options['live_session_ids'] if value.strip()]
        should_sync_coach_meetings = not options['skip_coach_meetings'] and not requested_ids
        # The existing scheduler entrypoint also drains UI-requested jobs.
        # One lease protects a series across concurrent command invocations.
        with connections['default'].cursor() as cursor:
            for series_id in dict.fromkeys(requested_ids):
                cursor.execute("""INSERT INTO curriculum.session_result_jobs(live_session_id,force_refresh)
                    SELECT id,%s FROM curriculum.live_sessions WHERE id=%s
                    ON CONFLICT(live_session_id) DO UPDATE
                    SET state='queued',requested_at=now(),next_attempt_at=now(),attempts=0,force_refresh=EXCLUDED.force_refresh
                    WHERE session_result_jobs.state NOT IN ('queued','running')
                      AND (EXCLUDED.force_refresh OR (session_result_jobs.state='complete'
                           AND session_result_jobs.finished_at<now()-interval '30 minutes'))""",
                    [True, series_id])
        try:
            call_command('process_session_results', limit=limit, scheduled=True,
                         live_session_ids=requested_ids, stdout=self.stdout, stderr=self.stderr)
        finally:
            if should_sync_coach_meetings:
                self._sync_coach_meeting_snapshots(lookback_hours, coach_limit)

        # Whatever the sync just discovered exists only inside Graph until it is
        # copied out, so archiving runs in the same pass rather than waiting for
        # a separate schedule someone has to remember to set up. It is
        # best-effort: a storage problem must not fail the artifact sync that
        # already succeeded.
        self._archive_new_recordings()

    def _archive_new_recordings(self) -> None:
        """Copy newly discovered recordings and transcripts into Azure Blob."""
        from coach_api.recording_archive import archive_configured

        if not archive_configured():
            return
        self.stdout.write('Archiving new Teams recordings into Azure Blob...')
        try:
            call_command('archive_meeting_recordings', verbosity=0)
        except Exception as exc:
            self.stderr.write(self.style.WARNING(f'Recording archive pass failed: {exc}'))

    def _sync_coach_meeting_snapshots(self, lookback_hours: int, limit: int) -> None:
        self.stdout.write('Checking recently ended coach meetings for Teams artifacts and attendance...')
        call_command(
            'sync_coach_meeting_snapshots',
            '--recent',
            '--lookback-hours',
            str(lookback_hours),
            '--limit',
            str(limit),
            '--allow-missing-tables',
            stdout=self.stdout,
            stderr=self.stderr,
        )
