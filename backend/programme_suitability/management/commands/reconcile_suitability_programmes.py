"""Review historical confirmations; only explicit --apply writes safe missing assignments."""
from django.core.management.base import BaseCommand
from django.db import DatabaseError, transaction

from programme_suitability.models import Assessment
from programme_suitability.programme_assignment import ProgrammeAssignmentError, sync_confirmed_programme_to_learner


class Command(BaseCommand):
    help = "Reconcile confirmed suitability choices. Defaults to dry-run; never overwrites a different existing programme."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--apply", action="store_true", help="Apply safe missing programme assignments.")
        mode.add_argument("--dry-run", action="store_true", help="Report only (the default).")
        parser.add_argument("--learner-id", action="append", type=int, help="Restrict to a learner id; repeatable.")

    def handle(self, *args, **options):
        apply = options["apply"]
        rows = Assessment.objects.filter(selection_confirmed_at__isnull=False).exclude(selected_programme="")
        if options["learner_id"]:
            rows = rows.filter(learner_id__in=options["learner_id"])
        counts = {"APPLIED": 0, "WOULD_APPLY": 0, "ALREADY_SYNCED": 0, "REVIEW": 0}
        # A normal ID snapshot also works behind Neon's transaction pooler;
        # server-side iterator cursors do not survive per-row transactions.
        for pk in rows.order_by("pk").values_list("pk", flat=True):
            try:
                with transaction.atomic():
                    row = Assessment.objects.select_for_update().get(pk=pk) if apply else Assessment.objects.get(pk=pk)
                    already = bool(row.review.get("programmeAssignment", {}).get("status") == "APPLIED")
                    result = sync_confirmed_programme_to_learner(row, actor="reconcile_suitability_programmes", reconcile=True, dry_run=not apply)
                    status = "ALREADY_SYNCED" if already else result["status"]
                counts[status] += 1
                self.stdout.write(f'{status} learner={row.learner_id} programme={result.get("programmeId", "")}')
            except (ProgrammeAssignmentError, DatabaseError, ValueError) as exc:
                counts["REVIEW"] += 1
                self.stdout.write(f"REVIEW assessment={pk}: {exc}")
        self.stdout.write(f'{"APPLY" if apply else "DRY-RUN"}: ' + ", ".join(f"{key}={value}" for key, value in counts.items()))
