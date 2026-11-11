"""Promote the reviewed initial setup in place; no cohorts or layouts are written."""
import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connections, router, transaction
from programme_suitability.models import ModuleConfigurationDraft
from programme_suitability.programme_structure import validate_structure
from enrolment_api.programme_structure_views import SHARED_DRAFT_KEY

class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument('--source', required=True)
        parser.add_argument('--base-version', type=int, required=True)
        parser.add_argument('--revision', type=int, required=True)
        parser.add_argument('--backup', required=True)
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **opts):
        db=router.db_for_write(ModuleConfigurationDraft) or 'default'
        with transaction.atomic(using=db):
            if connections[db].vendor=='postgresql':
                with connections[db].cursor() as q:
                    q.execute('LOCK TABLE programme_modules_admin_drafts IN SHARE ROW EXCLUSIVE MODE')
            qs=ModuleConfigurationDraft.objects.using(db)
            if qs.filter(pk=SHARED_DRAFT_KEY).exists():
                raise CommandError('Shared draft already exists. No records changed.')
            row=qs.select_for_update().get(pk=opts['source'])
            if row.base_version!=opts['base_version'] or row.configuration.get('_draftRevision',0)!=opts['revision']:
                raise CommandError('Source base version or revision changed. No records changed.')
            other=qs.filter(actor_key__startswith='programme-structure:').exclude(pk=row.pk)
            if other.exists():
                raise CommandError('Other Programme Structure drafts exist; review before sharing.')
            validate_structure(row.configuration)
            backup=Path(opts['backup'])
            data={'actor_key':row.actor_key,'base_version':row.base_version,'configuration':row.configuration,'updated_at':row.updated_at}
            encoded=json.dumps(data,cls=DjangoJSONEncoder,ensure_ascii=False,indent=2)
            backup.parent.mkdir(parents=True,exist_ok=True)
            if backup.exists() and backup.read_text(encoding='utf-8')!=encoded:
                raise CommandError('Backup differs; retain and review it. No records changed.')
            backup.write_text(encoded,encoding='utf-8')
            if not opts['apply']:
                self.stdout.write('Validated. Backup saved; database unchanged.')
                return
            updated=qs.filter(pk=row.pk,updated_at=row.updated_at).update(actor_key=SHARED_DRAFT_KEY)
            if updated!=1: raise CommandError('Concurrent source change detected.')
            saved=qs.get(pk=SHARED_DRAFT_KEY)
            if saved.configuration!=row.configuration or saved.base_version!=row.base_version or saved.updated_at!=row.updated_at:
                raise CommandError('Shared draft verification failed.')
            self.stdout.write(f'Shared draft verified: baseVersion={saved.base_version}, revision={saved.configuration.get("_draftRevision",0)}. Configuration and timestamps unchanged.')
