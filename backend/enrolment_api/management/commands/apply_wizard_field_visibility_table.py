"""Create the live field visibility overlay without modifying published layouts.

Run only after database-change approval:
    python manage.py apply_wizard_field_visibility_table
"""
from django.core.management.base import BaseCommand
from django.db import connections, transaction

from enrolment_api.wizard_layout import ASSIGNMENT_TABLE, CONN, VISIBILITY_TABLE


class Command(BaseCommand):
    help = "Create the wizard field visibility table and add a snapshot column to layout assignments."

    def handle(self, *args, **options):
        with transaction.atomic(using=CONN):
            with connections[CONN].cursor() as cursor:
                cursor.execute(
                    f'''CREATE TABLE IF NOT EXISTS enrolment."{VISIBILITY_TABLE}" (
                        "Field_key" text PRIMARY KEY,
                        "Hidden" boolean NOT NULL,
                        "Updated_by" text,
                        "Updated_at" timestamptz NOT NULL DEFAULT now()
                    )'''
                )
                cursor.execute(
                    f'ALTER TABLE enrolment."{ASSIGNMENT_TABLE}" '
                    'ADD COLUMN IF NOT EXISTS "Visibility_snapshot" jsonb'
                )
        self.stdout.write(self.style.SUCCESS("Wizard field visibility storage is ready."))
