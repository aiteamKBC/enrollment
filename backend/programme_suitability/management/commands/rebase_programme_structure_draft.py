"""Explicit recovery operation; never publishes or updates learners."""
from django.core.management.base import BaseCommand, CommandError
from programme_suitability.draft_rebase import rebase_shared_draft, RebaseConflict


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument('--base-version', type=int, required=True)
        parser.add_argument('--revision', type=int, required=True)
        parser.add_argument('--published-version', type=int, required=True)
        parser.add_argument('--backup-dir', required=True)
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        try:
            result = rebase_shared_draft(expected_base=options['base_version'], expected_revision=options['revision'], expected_published=options['published_version'], backup_dir=options['backup_dir'], apply=options['apply'])
        except (RebaseConflict, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"{'Saved' if result['applied'] else 'Preflight passed'}: baseVersion={result['baseVersion']}, revision={result['revision']}; no publication or learner changes.")
