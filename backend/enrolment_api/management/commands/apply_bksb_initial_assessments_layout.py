"""Backfill the published wizard layout with the new built-in BKSB step.

The frontend registry can repair an older layout at read time, but this
append-only command makes the database's published configuration explicit for
the builder and for staff reviewing the audit trail.  It never rewrites or
deletes an existing layout row.
"""
from copy import deepcopy
import json

from django.core.management.base import BaseCommand
from django.db import connections, transaction

from enrolment_api.wizard_layout import CONN, LAYOUT_TABLE, ensure_layout_table, read_current


STEP = {
    "slug": "initial-assessments",
    "label": "Initial Assessments",
    "builtin": True,
    "hidden": False,
    "items": [{"key": "block.initialAssessments", "builtin": True, "hidden": False}],
}


def with_step(layout):
    value = deepcopy(layout or {})
    steps = value.get("steps") if isinstance(value, dict) else None
    if not isinstance(steps, list):
        return value, False
    if any(isinstance(step, dict) and step.get("slug") == STEP["slug"] for step in steps):
        return value, False
    index = next((i + 1 for i, step in enumerate(steps) if isinstance(step, dict) and step.get("slug") == "policies"), len(steps))
    if any(isinstance(step, dict) and step.get("slug") == "next-steps" for step in steps[index:]):
        index = next(i for i, step in enumerate(steps) if isinstance(step, dict) and step.get("slug") == "next-steps")
    steps.insert(index, deepcopy(STEP))
    return value, True


class Command(BaseCommand):
    help = "Append the Initial Assessments step to the current published wizard layout."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show the change and roll it back.")

    def handle(self, *args, **options):
        with transaction.atomic(using=CONN):
            with connections[CONN].cursor() as cursor:
                ensure_layout_table(cursor)
                current = read_current(cursor)
                if not current:
                    self.stdout.write("No published layout exists; the frontend default includes the step.")
                    if options["dry_run"]:
                        transaction.set_rollback(True, using=CONN)
                    return
                updated, changed = with_step(current[1])
                if not changed:
                    self.stdout.write("The published layout already includes Initial Assessments.")
                else:
                    cursor.execute(
                        f'INSERT INTO enrolment."{LAYOUT_TABLE}" ("Layout", "Created_by") VALUES (%s::jsonb, %s)',
                        [json.dumps(updated), "system:bksb-initial-assessments"],
                    )
                    self.stdout.write("Appended a published layout containing Initial Assessments.")
                if options["dry_run"]:
                    transaction.set_rollback(True, using=CONN)
                    self.stdout.write(self.style.WARNING("Dry run: rolled back."))
