"""Synthetic ULN cutover rehearsal on a VERIFIED private localhost cluster only.

Never imports production settings/.env. Creates a new disposable database;
retains it for inspection. No production URL argument is accepted.
"""
import json
import os
from pathlib import Path
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from threading import Barrier
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlparse
import uuid

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ['CURRICULUM_WARM'] = '0'
os.environ['ENGLAND_HOLIDAY_AUTO_SYNC'] = '0'
config = json.loads((ROOT / 'private/enrollment_rehearsal.json').read_text())
parsed = urlparse(config['url'])
if parsed.hostname != '127.0.0.1' or parsed.port != 55439:
    raise SystemExit('Only the private localhost PostgreSQL cluster is permitted.')
database = 'enrollment_uln_' + uuid.uuid4().hex[:12]
with psycopg.connect(config['url'], autocommit=True) as control:
    directory = control.execute('SHOW data_directory').fetchone()[0]
    if Path(directory).resolve() != (ROOT / 'private/postgres-data').resolve():
        raise SystemExit('Unexpected PostgreSQL data directory. No writes performed.')
    control.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))

# Block all non-loopback connections, including accidental integrations.
original_connect = socket.socket.connect
def local_only(sock, address):
    if not isinstance(address, tuple) or address[0] != '127.0.0.1' or address[1] != 55439:
        raise AssertionError('External network blocked by ULN rehearsal.')
    return original_connect(sock, address)
socket.socket.connect = local_only

from django.conf import settings
from enrollment_service import settings_test
options = {k: getattr(settings_test, k) for k in dir(settings_test) if k.isupper()}
db = {'ENGINE': 'django.db.backends.postgresql', 'NAME': database,
      'HOST': '127.0.0.1', 'PORT': 55439, 'USER': parsed.username, 'PASSWORD': parsed.password,
      'OPTIONS': {'options': '-c statement_timeout=10000 -c lock_timeout=5000'}}
options.update(DATABASES={k: dict(db) for k in ('default', 'enrolment')},
               DATABASE_ROUTERS=['learner_api.routers.EnrolmentRouter'],
               ENROLMENT_ULN_STORAGE='created_users')
settings.configure(**options)
import django
django.setup()

from django.db import connections, transaction, DatabaseError, IntegrityError, DataError
from django.test import RequestFactory, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from learner_api.models import EnrolmentUser
from enrolment_api.models import (ExtendedIlr, WizardPersonalDetails, WizardIlrLearnerDetails,
    WizardPlr, WizardPlrRecord, WizardCvJob, WizardKsbAssessment, WizardPolicyAck, WizardSkillsRadar)
from enrolment_api.plr_uln_storage import read_uln, write_uln
from enrolment_api.plr_uln import UlnAssignmentError
from enrolment_api.wizard_steps import read_plr, PLR_LOAD_ERROR
from enrolment_api.extended_ilr import extended_ilr
from enrolment_api.wizard_bootstrap import wizard_bootstrap
from enrolment_api.plr_pdf_import import plr_pdf_import
from enrolment_api.plr_pdf_preview import plr_pdf_preview
from enrolment_api.plr_fixtures import synthetic_pdf

connection = connections['enrolment']
with connection.cursor() as c:
    c.execute('CREATE SCHEMA enrolment')
with connection.schema_editor() as editor:
    for model in (EnrolmentUser, ExtendedIlr, WizardPersonalDetails, WizardIlrLearnerDetails,
                  WizardPlr, WizardPlrRecord, WizardCvJob, WizardKsbAssessment,
                  WizardPolicyAck, WizardSkillsRadar):
        editor.create_model(model)
with connection.cursor() as c:
    for table in ('Wizard_Plr', 'Extended_ILR'):
        c.execute(sql.SQL('CREATE UNIQUE INDEX ON enrolment.{} ("Learner_kind", "Learner_id")').format(sql.Identifier(table)))
    c.execute('CREATE UNIQUE INDEX ON enrolment."Wizard_Plr_Records" ("Learner_kind","Learner_id","Record_ref")')

SQL_DIR = ROOT / 'backend/sql/uln_created_users'
EXPAND = (SQL_DIR / '01_expand.sql').read_text()
RECONCILE = (SQL_DIR / '02_reconcile_once.sql').read_text()
def run_sql(source):
    # Rehearse the exact statements, within the test's rollback boundary.
    source = source.replace('BEGIN;', '').replace('COMMIT;', '')
    with connection.cursor() as c:
        c.execute(source)

def learner():
    return EnrolmentUser.objects.create(username='Morgan Synthetic Learner',
        date_of_birth='1994-03-12', learner_type='apprenticeship', programme_status='Onboarding')

class A_MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tx = transaction.atomic(using='enrolment'); self.tx.__enter__()
    def tearDown(self):
        transaction.set_rollback(True, using='enrolment'); self.tx.__exit__(None, None, None)
    def test_backfill_uses_created_user_id_preserves_zeros_and_nine_orphans(self):
        owner = learner()
        with connection.cursor() as c:
            c.execute('ALTER TABLE enrolment."Created_users" ADD COLUMN learner_id bigint')
            c.execute('UPDATE enrolment."Created_users" SET learner_id=900000 WHERE id=%s',[owner.pk])
        scope = dict(learner_kind='apprenticeship', learner_id=owner.pk)
        WizardPlr.objects.create(**scope, uln='0012345678')
        for n in range(9):
            WizardPlr.objects.create(learner_kind='commercial', learner_id=900000 + n, uln='unchanged-orphan')
        before = list(WizardPlr.objects.order_by('id').values())
        run_sql(EXPAND)
        with connection.cursor() as c:
            c.execute("SET LOCAL enrolment.uln_legacy_writers_drained = 'yes'")
        run_sql(RECONCILE)
        self.assertEqual(read_uln('apprenticeship', owner.pk), '0012345678')
        self.assertEqual(before, list(WizardPlr.objects.order_by('id').values()))
        with connection.cursor() as c:
            c.execute('SELECT count(*) FROM enrolment."Created_users"'); self.assertEqual(c.fetchone()[0], 1)
    def test_legacy_writer_after_expansion_is_reconciled_at_cutover(self):
        owner=learner(); run_sql(EXPAND)
        with override_settings(ENROLMENT_ULN_STORAGE='legacy'):
            write_uln('apprenticeship',owner.pk,'0012345678')
            self.assertEqual(read_uln(None,owner.pk),'0012345678')
            write_uln('apprenticeship',owner.pk,'0098765432')
        with connection.cursor() as c: c.execute("SET LOCAL enrolment.uln_legacy_writers_drained='yes'")
        run_sql(RECONCILE)
        self.assertEqual(read_uln(None,owner.pk),'0098765432')
        with self.assertRaises(DatabaseError),transaction.atomic(using='enrolment'):
            run_sql(RECONCILE)
    def test_format_constraint_accepts_null_and_rejects_non_ascii_empty_and_bad_lengths(self):
        owner = learner(); run_sql(EXPAND)
        for value in ('', '123', '12345678901', '123456789a', '\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19\uff10', '123456789\n'):
            with self.assertRaises((IntegrityError, DataError)), transaction.atomic(using='enrolment'):
                with connection.cursor() as c:
                    c.execute('UPDATE enrolment."Created_users" SET "ULN"=%s WHERE id=%s', [value, owner.pk])
        self.assertEqual(read_uln(None, owner.pk), '')
    def test_backfill_requires_writer_drain_and_rejects_invalid_or_conflicting_values(self):
        owner = learner(); run_sql(EXPAND)
        with self.assertRaises(DatabaseError), transaction.atomic(using='enrolment'):
            run_sql(RECONCILE)
        scope = dict(learner_kind='apprenticeship', learner_id=owner.pk)
        row = WizardPlr.objects.create(**scope, uln='invalid')
        with connection.cursor() as c: c.execute("SET LOCAL enrolment.uln_legacy_writers_drained='yes'")
        with self.assertRaises(DatabaseError), transaction.atomic(using='enrolment'):
            run_sql(RECONCILE)
        row.uln='0123456789'; row.save()
        # Cross-scope conflicts cannot be silently mapped or corrected.
        WizardPlr.objects.create(learner_kind='commercial', learner_id=owner.pk, uln='9876543210')
        with self.assertRaises(DatabaseError), transaction.atomic(using='enrolment'):
            run_sql(RECONCILE)
        self.assertEqual(read_uln(None, owner.pk), '')
    def test_duplicate_legacy_owners_abort_without_partial_backfill(self):
        a,b=learner(),learner(); run_sql(EXPAND)
        for owner in (a,b):
            WizardPlr.objects.create(learner_kind='apprenticeship',learner_id=owner.pk,uln='0012345678')
        with connection.cursor() as c: c.execute("SET LOCAL enrolment.uln_legacy_writers_drained='yes'")
        with self.assertRaises(DatabaseError),transaction.atomic(using='enrolment'):
            run_sql(RECONCILE)
        self.assertEqual((read_uln(None,a.pk),read_uln(None,b.pk)),('',''))
    def test_candidate_unique_index_excludes_nulls_but_blocks_duplicate_nonnull(self):
        a, b = learner(), learner(); run_sql(EXPAND)
        with connection.cursor() as c:
            c.execute('CREATE UNIQUE INDEX synthetic_uln_candidate ON enrolment."Created_users" ("ULN") WHERE "ULN" IS NOT NULL')
            c.execute('UPDATE enrolment."Created_users" SET "ULN"=%s WHERE id=%s', ['0012345678', a.pk])
        with self.assertRaises(IntegrityError), transaction.atomic(using='enrolment'):
            with connection.cursor() as c:
                c.execute('UPDATE enrolment."Created_users" SET "ULN"=%s WHERE id=%s', ['0012345678', b.pk])
        # This index exists only in this rolled-back synthetic test; production
        # uniqueness is deferred until identity/re-enrolment policy is confirmed.

class B_ApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with transaction.atomic(using='enrolment'):
            run_sql(EXPAND)
            with connection.cursor() as c: c.execute("SET LOCAL enrolment.uln_legacy_writers_drained='yes'")
            run_sql(RECONCILE)
    def setUp(self):
        self.tx=transaction.atomic(using='enrolment'); self.tx.__enter__()
        self.learner=learner()
        self.scope=dict(learner_kind='apprenticeship',learner_id=self.learner.pk)
        self.stack=ExitStack()
        for target, value in (
            ('enrolment_api.extended_ilr.signed_snapshot', None),
            ('enrolment_api.extended_ilr.legacy_review_required', False),
            ('enrolment_api.extended_ilr.project_custom_fields', None),
            ('enrolment_api.eligibility_prefill.add_defaults', None),
            ('learner_api.learning_plan.promote_learner_if_ready', False),
            ('enrolment_api.wizard_bootstrap.advance_learner', None),
            ('enrolment_api.wizard_bootstrap.to_board', {'contact': {}, 'programme': {}}),
            ('enrolment_api.auth._accesses_of', {'enrolment'}),
        ): self.stack.enter_context(patch(target, return_value=value))
        self.stack.enter_context(patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH':'1'}))
        ExtendedIlr.objects.create(**self.scope, wizard_draft={'personalDetails':{'signature':'preserved-progress'},
            'skillsRadar':{}, 'plr':{'uln':'stale-value','records':[]}})
    def tearDown(self):
        self.stack.close(); transaction.set_rollback(True,using='enrolment'); self.tx.__exit__(None,None,None)
    def request(self, body=None, role='admin', bootstrap=False):
        factory=RequestFactory()
        req=factory.get('/synthetic/') if body is None else factory.put('/synthetic/',json.dumps(body),content_type='application/json')
        req.user=SimpleNamespace(is_authenticated=role=='admin',is_staff=role=='admin')
        if role!='admin': req.login_account=SimpleNamespace(is_active=True,role='learner',subject_id=self.learner.pk,id=1)
        return (wizard_bootstrap if bootstrap else extended_ilr)(req,kind='apprenticeship',learner_id=self.learner.pk)
    def upload(self, preview=False):
        req=RequestFactory().post('/synthetic/',{'file':SimpleUploadedFile('synthetic.pdf',synthetic_pdf(),content_type='application/pdf')})
        req.user=SimpleNamespace(is_authenticated=True,is_staff=True); req._dont_enforce_csrf_checks=True
        return (plr_pdf_preview if preview else plr_pdf_import)(req,kind='apprenticeship',learner_id=self.learner.pk)
    def draft(self):
        response=self.request(); self.assertEqual(response.status_code,200,response.content)
        return json.loads(response.content)['draft']
    def test_manual_admin_save_clear_stale_draft_rollback_and_both_bootstrap_interfaces(self):
        response=self.request({'draft':self.draft(),'ulnUpdate':{'value':'0012345678','expected':''}})
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(read_uln(None,self.learner.pk),'0012345678')
        stale=self.draft()
        response=self.request({'draft':stale,'ulnUpdate':{'value':'','expected':'0012345678'}})
        self.assertEqual(response.status_code,200,response.content)
        self.assertIsNone(self.canonical_db_value())
        self.assertIsNone(WizardPlr.objects.get(**self.scope).uln)
        with override_settings(ENROLMENT_ULN_STORAGE='legacy'):
            self.assertEqual(read_uln('apprenticeship',self.learner.pk),'')
        # Even a stale legacy value cannot resurrect a deliberately cleared ULN.
        WizardPlr.objects.filter(**self.scope).update(uln='0012345678')
        for role in ('admin','learner'):
            response=self.request({'draft':stale},role)
            self.assertEqual(response.status_code,200,response.content)
            response=self.request(role=role,bootstrap=True)
            self.assertEqual(response.status_code,200,response.content)
            payload=json.loads(response.content)['ilr']
            self.assertEqual(payload['draft']['plr']['uln'],'')
            self.assertEqual(payload['draft']['personalDetails']['signature'],'preserved-progress')
            self.assertEqual(payload['meta']['canEditUln'],role=='admin')
        write_uln('apprenticeship',self.learner.pk,'0123456789')
        with override_settings(ENROLMENT_ULN_STORAGE='legacy'):
            self.assertEqual(read_uln('apprenticeship',self.learner.pk),'0123456789')
        with self.assertRaises(RuntimeError), transaction.atomic(using='enrolment'):
            write_uln('apprenticeship',self.learner.pk,'9876543210'); raise RuntimeError('synthetic rollback')
        self.assertEqual(self.canonical_db_value(),'0123456789')
        self.assertEqual(WizardPlr.objects.get(**self.scope).uln,'0123456789')
    def canonical_db_value(self):
        with connection.cursor() as c:
            c.execute('SELECT "ULN" FROM enrolment."Created_users" WHERE id=%s',[self.learner.pk]); return c.fetchone()[0]
    def test_student_cannot_assign_and_empty_plr_is_valid(self):
        self.assertEqual(self.draft()['plr'],{'uln':'','records':[]})
        response=self.request({'draft':self.draft(),'ulnUpdate':{'value':'0012345678','expected':''}},'learner')
        self.assertEqual(response.status_code,403)
        response=self.request({'draft':self.draft()},'learner')
        self.assertEqual(response.status_code,200,response.content)
        self.assertIsNone(self.canonical_db_value())
    def test_unresolved_orphan_uln_is_reserved_without_mapping_or_mutation(self):
        orphan=WizardPlr.objects.create(learner_kind='commercial',learner_id=900000,uln='0012345678')
        before=orphan.__dict__.copy()
        with self.assertRaises(UlnAssignmentError):
            write_uln('apprenticeship',self.learner.pk,'0012345678')
        orphan.refresh_from_db()
        self.assertEqual(orphan.uln,before['uln'])
        self.assertIsNone(self.canonical_db_value())
    def test_review_and_new_ilr_consumers_use_canonical_null_without_historical_fallback(self):
        from learner_api.ilr_document import derive_learner_details
        from learner_api.review_form import _plr_context
        WizardPlr.objects.create(**self.scope,uln='stale-legacy')
        write_uln('apprenticeship',self.learner.pk,'0012345678')
        review=SimpleNamespace(review_type='rpl',learner_id=self.learner.pk)
        with patch('learner_api.review_form.sections_for',return_value=['plr']):
            self.assertEqual(_plr_context(review,self.learner)['uln'],'0012345678')
        with patch('learner_api.ilr_document._rpl_detail',return_value=SimpleNamespace(uln='9999999999')):
            self.assertEqual(derive_learner_details(self.learner)['uln'],'0012345678')
            write_uln('apprenticeship',self.learner.pk,'')
            self.assertEqual(derive_learner_details(self.learner)['uln'],'')
        with patch('learner_api.review_form.sections_for',return_value=['plr']):
            self.assertEqual(_plr_context(review,self.learner)['uln'],'')
    def test_admin_edit_delete_student_add_and_stale_save_do_not_change_uln_or_restore_deleted_rows(self):
        write_uln('apprenticeship',self.learner.pk,'0012345678')
        initial=self.draft()
        initial['plr']['records']=[{'id':'synthetic-manual','subject':'Original','recordType':'Manual','credits':0}]
        response=self.request({'draft':initial},'learner')
        self.assertEqual(response.status_code,200,response.content)
        stale=self.draft(); record=stale['plr']['records'][0]
        change={'action':'edit','id':record['id'],'expected':record,'record':{**record,'subject':'Updated'}}
        self.assertEqual(self.request({'draft':stale,'plrRecordChange':change},'learner').status_code,403)
        response=self.request({'draft':stale,'plrRecordChange':change})
        self.assertEqual(response.status_code,200,response.content)
        current=self.draft(); record=current['plr']['records'][0]
        self.assertEqual(record['subject'],'Updated')
        response=self.request({'draft':current,'plrRecordChange':{'action':'delete','id':record['id'],'expected':record}})
        self.assertEqual(response.status_code,200,response.content)
        response=self.request({'draft':stale},'learner')
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(self.draft()['plr'],{'uln':'0012345678','records':[]})
    def test_pdf_preview_import_idempotence_and_student_visibility(self):
        before=self.upload(preview=True); self.assertEqual(before.status_code,200,before.content)
        self.assertIsNone(self.canonical_db_value())
        response=self.upload(); self.assertEqual(response.status_code,200,response.content)
        result=json.loads(response.content)
        self.assertEqual(result['added'],3)
        self.assertEqual(self.canonical_db_value(),'1234509876')
        self.assertEqual(WizardPlr.objects.get(**self.scope).uln,'1234509876')
        self.assertEqual(json.loads(self.request(role='learner').content)['draft']['plr'],result['plr'])
        self.assertEqual(json.loads(self.upload().content)['added'],0)
    def test_import_failure_rolls_back_canonical_uln_legacy_mirror_and_qualifications(self):
        with patch.object(ExtendedIlr,'save',side_effect=DatabaseError('private synthetic data')):
            response=self.upload()
        self.assertEqual(response.status_code,503,response.content)
        self.assertIsNone(self.canonical_db_value())
        self.assertFalse(WizardPlr.objects.filter(**self.scope).exists())
        self.assertFalse(WizardPlrRecord.objects.filter(**self.scope).exists())
    def test_real_plr_sql_failure_isolated_to_section_in_both_interfaces_and_saves(self):
        def fail(**kwargs):
            with connection.cursor() as c: c.execute('SELECT nonexistent_synthetic_column FROM enrolment."Wizard_Plr_Records"')
        with patch.object(WizardPlrRecord.objects,'filter',side_effect=fail), self.assertLogs('enrolment_api.wizard_steps',level='ERROR') as logs:
            for role in ('admin','learner'):
                response=self.request(role=role,bootstrap=True)
                self.assertEqual(response.status_code,200,response.content)
                draft=json.loads(response.content)['ilr']['draft']
                self.assertEqual(draft['plr'],{'loadError':PLR_LOAD_ERROR})
                self.assertEqual(draft['personalDetails']['signature'],'preserved-progress')
                response=self.request({'draft':{**draft,'policies':{'acknowledged':{'synthetic':True}}}},role)
                self.assertEqual(response.status_code,200,response.content)
                self.assertEqual(json.loads(response.content)['draft']['plr'],{'loadError':PLR_LOAD_ERROR})
        self.assertNotIn('nonexistent_synthetic_column',' '.join(logs.output))
        self.assertTrue(WizardPolicyAck.objects.get(**self.scope,policy_id='synthetic').acknowledged)
        self.assertEqual(read_plr('apprenticeship',self.learner.pk),{'uln':'','records':[]})
    def test_optional_column_failure_cannot_break_created_user_queries_or_enrolment(self):
        with connection.cursor() as c: c.execute('ALTER TABLE enrolment."Created_users" RENAME COLUMN "ULN" TO "synthetic_hidden_uln"')
        self.assertTrue(EnrolmentUser.objects.filter(pk=self.learner.pk).exists())
        response=self.request(bootstrap=True)
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(json.loads(response.content)['ilr']['draft']['plr'],{'loadError':PLR_LOAD_ERROR})
        with override_settings(ENROLMENT_ULN_STORAGE='legacy'):
            self.assertEqual(self.draft()['plr'],{'uln':'','records':[]})

class C_ConcurrencyTests(unittest.TestCase):
    def test_two_concurrent_assignments_have_exactly_one_owner(self):
        a,b=learner(),learner(); barrier=Barrier(2)
        def assign(owner):
            try:
                barrier.wait(timeout=10)
                write_uln('apprenticeship',owner,'0098765432')
                return 'assigned'
            except UlnAssignmentError:
                return 'duplicate'
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(assign,[a.pk,b.pk]))
        self.assertCountEqual(results,['assigned','duplicate'])
        with connection.cursor() as c:
            c.execute('SELECT count(*) FROM enrolment."Created_users" WHERE "ULN"=%s',['0098765432'])
            self.assertEqual(c.fetchone()[0],1)

if __name__=='__main__':
    print('Verified private PostgreSQL; synthetic-only database:',database,flush=True)
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    report={'database':database,'synthetic_only':True,'postgres_version':None,'tests':result.testsRun,
            'failures':len(result.failures),'errors':len(result.errors),'success':result.wasSuccessful(),
            'production_writes':False,'production_unique_constraint':'deferred'}
    with connection.cursor() as c: c.execute('SHOW server_version'); report['postgres_version']=c.fetchone()[0]
    (ROOT/'.cache/uln-postgres-results.json').write_text(json.dumps(report,indent=2)+'\n')
    connections.close_all()
    sys.exit(not result.wasSuccessful())
