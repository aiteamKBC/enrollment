from django.core.management.base import BaseCommand
from enrolment_api.models import WizardCvJob
from programme_suitability.cv_extraction import extract_approved_cv, CvExtractionError


class Command(BaseCommand):
    help = 'Extract approved CVs (dry-run by default).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Persist extraction results.')
        parser.add_argument('--learner-id', type=int)

    def handle(self, *args, **options):
        apply = options['apply']
        qs = WizardCvJob.objects.all()
        if options.get('learner_id'):
            qs = qs.filter(learner_id=options['learner_id'])
        total = ready = failed = 0
        for row in qs.iterator():
            for item in (row.documents or []):
                if not isinstance(item, dict) or item.get('docKind') != 'cv':
                    continue
                total += 1
                if not apply:
                    self.stdout.write(f"DRY-RUN {row.learner_kind}:{row.learner_id} {item.get('id')}")
                    continue
                try:
                    result = extract_approved_cv(row.learner_kind, row.learner_id, item)
                    ready += result.status == 'ready'
                except (CvExtractionError, ValueError) as exc:
                    failed += 1
                    self.stderr.write(f"{row.learner_kind}:{row.learner_id} {item.get('id')}: {exc}")
        self.stdout.write(self.style.SUCCESS(f"{'APPLIED' if apply else 'DRY-RUN'} total={total} ready={ready} failed={failed}"))
