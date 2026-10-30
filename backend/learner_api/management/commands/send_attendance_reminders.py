"""Schedule explicitly after the attendance SQL has been applied by the owner."""
from datetime import timedelta
from html import escape
import os

from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction
from django.utils import timezone

from login.email_azure import is_configured, send_mail
from learner_api.attendance_lectures import read_workspace
from learner_api.attendance_mode import TABLE, _state, _payload
from learner_api.models import EnrolmentUser


def reminder_candidates(lectures, mode, today):
    if not mode['remindersEnabled']:
        return []
    cutoff = (today - timedelta(days=7)).isoformat()
    return [row for row in lectures if row['status'] == 'absent' and row['catchupStatus'] != 'completed'
            and not row['absenceReport'] and cutoff <= row['date'] <= today.isoformat()]


class Command(BaseCommand):
    help = 'Preview recent absence reminders; --send delivers once per learner/lecture.'

    def add_arguments(self, parser):
        parser.add_argument('--learner-id', type=int, action='append', required=True)
        parser.add_argument('--send', action='store_true')

    def handle(self, *args, **options):
        if options['send'] and not is_configured():
            raise CommandError('Approval/reminder email is not configured.')
        sources = EnrolmentUser.all_learners.filter(pk__in=options['learner_id'])
        for source in sources:
            kind = 'commercial' if source.learner_type == 'commercial' else 'apprenticeship'
            workspace = read_workspace(source, kind)
            if not workspace['mode']['available']:
                raise CommandError('Apply backend/sql/2026-09-12_attendance_preferences.sql manually first.')
            candidates = reminder_candidates(workspace['lectures'], workspace['mode'], timezone.localdate())
            for lecture in candidates:
                if not options['send']:
                    self.stdout.write(f"Would remind learner {source.id}: {lecture['id']}")
                    continue
                # Serialize against mode changes and another reminder worker.
                # A failed transport rolls back the claim so it can be retried.
                with transaction.atomic(using='enrolment'), connections['enrolment'].cursor() as cur:
                    cur.execute(f'INSERT INTO {TABLE} (learner_id) VALUES (%s) ON CONFLICT DO NOTHING', [source.id])
                    if not _payload(_state(cur, source.id, lock=True))['remindersEnabled']:
                        break
                    cur.execute('''INSERT INTO "Learner".attendance_reminders (learner_id,session_key)
                        VALUES (%s,%s) ON CONFLICT DO NOTHING RETURNING learner_id''', [source.id, lecture['id']])
                    if not cur.fetchone():
                        continue
                    origin = (os.environ.get('FRONTEND_URL') or 'http://localhost:5173').rstrip('/')
                    sent, _ = send_mail(to=source.email, subject='Catch up on your missed lecture',
                        html_body=f"<p>You missed {escape(lecture['title'])} on {escape(lecture['date'])}.</p>"
                                  f'<p><a href="{escape(origin, quote=True)}/learner/attendance">Open attendance</a> to report an absence or complete the linked activities.</p>')
                    if not sent:
                        raise CommandError('Reminder email could not be sent. The reminder can be retried.')
                self.stdout.write(f"Reminded learner {source.id}: {lecture['id']}")
