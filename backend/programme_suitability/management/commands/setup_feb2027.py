"""One-off, explicitly authorized development setup. Never publishes."""
import copy
import hashlib
import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from programme_suitability.models import ModuleConfigurationDraft
from programme_suitability.programme_structure import from_legacy, validate_structure, version
from enrolment_api.wizard_layout import read_current

PROGRAMMES = {
    "ME_L4": "PROG-ME-L4", "MM_L6": "PROG-MM-L6",
    "APM_L4": "PROG-20260907141619067932", "PCP_L6": "PROG-PCP-L6",
}

class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--backup-dir', required=True)

    def handle(self, *args, **opts):
        if connection.vendor != 'postgresql':
            raise CommandError('This controlled setup requires the configured development PostgreSQL.')
        from curriculum_api.views import create_curriculum_cohort
        target_key = 'programme-structure:platform:108'
        backup_dir = Path(opts['backup_dir'])
        backup_dir.mkdir(parents=True, exist_ok=True)
        with transaction.atomic(using='default'):
            with connection.cursor() as q:
                q.execute('LOCK TABLE enrolment."Wizard_Form_Layouts" IN SHARE MODE')
                q.execute('LOCK TABLE programme_modules_admin_drafts IN SHARE ROW EXCLUSIVE MODE')
                q.execute('LOCK TABLE curriculum.cohorts IN SHARE ROW EXCLUSIVE MODE')
                current = read_current(q)
                if not current or current[0] != 16:
                    raise CommandError('Published baseline changed; review again before setup.')
                layout = current[1]
                old = ModuleConfigurationDraft.objects.select_for_update().get(pk='platform:108')
                if old.base_version != 8 or old.configuration.get('_draftRevision') is not None:
                    raise CommandError('Original draft changed; refusing to overwrite concurrent work.')
                target = ModuleConfigurationDraft.objects.filter(actor_key__startswith='programme-structure:').first()
                if target:
                    raise CommandError('A Programme Structure draft already exists for this Admin; review it first.')
                q.execute('SELECT "Role", "Is_active" FROM login."Login_accounts" WHERE id=108')
                account = q.fetchone()
                if not account or account[0] not in ('admin', 'staff') or not account[1]:
                    raise CommandError('Original draft owner is not an active authorized staff account.')
                payload = {'actor_key':old.actor_key,'base_version':old.base_version,'configuration':old.configuration,'updated_at':old.updated_at}
                backup = backup_dir / 'old-draft.json'
                if backup.exists():
                    saved = json.loads(backup.read_text(encoding='utf-8'))
                    if saved != json.loads(json.dumps(payload, cls=DjangoJSONEncoder)):
                        raise CommandError('Backup differs from current draft.')
                else:
                    backup.write_text(json.dumps(payload,cls=DjangoJSONEncoder,ensure_ascii=False,indent=2),encoding='utf-8')
                (backup_dir/'published-v16.json').write_text(json.dumps(layout,ensure_ascii=False,indent=2),encoding='utf-8')
                snapshot = from_legacy(layout['programmeModules'],layout['skillsRadarCatalogues'])
                report = []
                for p in snapshot['programmes']:
                    pid = PROGRAMMES[p['id']]
                    q.execute('SELECT cohort_id,cohort_name,status FROM curriculum.cohorts WHERE programme_id=%s AND lower(cohort_name) IN (%s,%s) AND NOT COALESCE(is_programme_deleted,false)',[pid,'feb 2027','february 2027'])
                    rows = q.fetchall()
                    if len(rows)>1: raise CommandError('Ambiguous February cohort for '+p['id'])
                    if not rows and not opts['apply']:
                        report.append({'programme':p['id'],'action':'create via curriculum service','curriculumProgrammeId':pid})
                        continue
                    created = False
                    if rows:
                        cid, name, status = rows[0]
                    else:
                        q.execute('SELECT DISTINCT programme_name FROM curriculum.cohorts WHERE programme_id=%s AND programme_name IS NOT NULL',[pid])
                        names = [r[0] for r in q.fetchall()]
                        if len(names)!=1: raise CommandError('Ambiguous authoritative programme name for '+pid)
                        response = create_curriculum_cohort({'programmeId':pid,'programme':names[0],'name':'Feb 2027','startDate':'2027-02-01','durationMonths':24})
                        data = json.loads(response.content)
                        if response.status_code != 201 or not data.get('created'):
                            raise CommandError('Curriculum creation did not create a new record: '+json.dumps(data))
                        c = data['cohort']; cid,name,status=c['id'],c['name'],c['status']; created=True
                    if p['id']=='ME_L4' and cid!='COHORT-20260917092025244736D2820D958B44':
                        raise CommandError('ME_L4 identity changed.')
                    c = p['cohorts'][0]
                    c.update(id=cid,curriculumCohortId=cid,name='Feb 2027',status=status)
                    # Retain every pathway field, nested group and rule, not a lossy subset.
                    if p['id']=='PCP_L6':
                        c['routes']=[dict(copy.deepcopy(path),name=path.get('title',path['id'])) for path in p['selectionPolicy']['pathways']]
                    report.append({'programme':p['id'],'id':cid,'created':created,'modules':len(c['modules']),'ksbs':len(p['ksbs']),'mappings':len(c['mappings']),'routes':len(c.get('routes',[]))})
                self.stdout.write(json.dumps(report,indent=2))
                if not opts['apply']: return
                validate_structure(snapshot)
                snapshot['_draftRevision']=1
                snapshot['version']=version(snapshot)
                ModuleConfigurationDraft.objects.create(actor_key=target_key,base_version=16,configuration=snapshot)
                reloaded = ModuleConfigurationDraft.objects.get(pk=target_key)
                if reloaded.configuration != snapshot: raise CommandError('Draft reload differs.')
                if old.configuration != ModuleConfigurationDraft.objects.get(pk=old.pk).configuration:
                    raise CommandError('Original draft changed.')
                (backup_dir/'result.json').write_text(json.dumps({'actor_key':target_key,'baseVersion':16,'revision':1,'cohorts':report},indent=2),encoding='utf-8')
                self.stdout.write('VERIFIED: baseVersion=16 revision=1; original draft and published layout unchanged.')
