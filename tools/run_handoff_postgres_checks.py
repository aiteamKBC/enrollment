"""Exercise receiver transactions only on the privately provisioned local cluster."""
import copy
import json
from pathlib import Path
import sys
import unittest
import uuid

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from enrollment_service.lms_receiver import import_payload
from enrollment_service.handoff_contract import Conflict

config = json.loads((ROOT / 'private/enrollment_regression.json').read_text())
conn = psycopg.connect(config['url'])
with conn.cursor() as cur:
    cur.execute('SHOW data_directory')
    if Path(cur.fetchone()[0]).resolve() != Path(config['verified_local_directory']).resolve():
        raise SystemExit('Local database directory verification failed.')
    cur.execute('SELECT current_database()')
    if cur.fetchone()[0] != 'enrollment_regression':
        raise SystemExit('Not the private regression database.')
    cur.execute("SELECT to_regclass('enrolment.\"Created_users\"')")
    if cur.fetchone()[0] is None:
        cur.execute((ROOT / 'database/schema.sql').read_text(encoding='utf-8'), prepare=False)
    cur.execute((ROOT / 'integrations/lms-receiver.sql').read_text(encoding='utf-8'), prepare=False)
conn.commit()


class HandoffPostgresTests(unittest.TestCase):
    def setUp(self):
        self.uid = 910001
        self.uuid = str(uuid.uuid4())
        with conn.cursor() as cur:
            cur.execute('INSERT INTO enrolment."Created_users" (id,uuid,"Username","Email","Learner_type","Programme_status") OVERRIDING SYSTEM VALUE VALUES(%s,%s,%s,%s,%s,%s) RETURNING row_to_json("Created_users")', [self.uid, self.uuid, 'Synthetic handoff learner', 'synthetic@example.invalid', 'apprenticeship', 'Ready to enrol'])
            user = cur.fetchone()[0]
            cur.execute('INSERT INTO login."Login_accounts" ("Subject_type","Subject_id","Email","Role") VALUES(%s,%s,%s,%s) RETURNING row_to_json("Login_accounts")', ['learner',self.uid,'synthetic@example.invalid','learner'])
            account = cur.fetchone()[0]
        self.payload = {'version': 1, 'rows': {'enrolment.Created_users': [user], 'login.Login_accounts': [account]}}
        conn.rollback()  # Payload is synthetic; target starts empty for each test.

    def tearDown(self):
        conn.rollback()

    def test_new_learner_and_retry_are_atomic_and_idempotent(self):
        self.assertEqual(import_payload(conn, self.payload, 'synthetic-digest'), 'accepted')
        self.assertEqual(import_payload(conn, self.payload, 'synthetic-digest'), 'already-accepted')
        with conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM enrolment."Created_users" WHERE uuid=%s', [self.uuid])
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute('SELECT "Subject_id" FROM login."Login_accounts" WHERE "Email"=%s', ['synthetic@example.invalid'])
            self.assertEqual(cur.fetchone()[0], self.uid)

    def test_conflicting_learner_id_never_overwrites_target(self):
        with conn.cursor() as cur:
            cur.execute('INSERT INTO enrolment."Created_users" (id,uuid,"Email") OVERRIDING SYSTEM VALUE VALUES(%s,%s,%s)', [self.uid,str(uuid.uuid4()),'other@example.invalid'])
        with self.assertRaises(Conflict): import_payload(conn,self.payload,'synthetic-digest')
        with conn.cursor() as cur:
            cur.execute('SELECT "Email" FROM enrolment."Created_users" WHERE id=%s', [self.uid])
            self.assertEqual(cur.fetchone()[0],'other@example.invalid')

    def test_profile_plan_and_signed_document_transfer_together(self):
        changed=copy.deepcopy(self.payload)
        changed['rows']['enrolment.Created_users'][0]['learner_id']=910010
        changed['rows'].update({
            'Learner.learners':[{'id':910010,'uuid':self.uuid,'enrolment_id':self.uid,
                'full_name':'Synthetic handoff learner','email':'synthetic@example.invalid',
                'lifecycle_status':'Delivery','learner_type':'apprenticeship'}],
            'Learner.learner_training_plan_modules':[{'id':910011,'learner_id':910010,'position':1,'module_title':'Synthetic module'}],
            'Learner.learner_training_plan_weeks':[{'id':910012,'plan_module_id':910011,'position':1,'week_title':'Synthetic week'}],
            'Learner.learner_training_plan_components':[{'id':910013,'plan_week_id':910012,'position':1,'component_title':'Synthetic lesson','expected_otjh':'2.50'}],
            'enrolment.Wizard_Personal_Details':[{'id':910014,'Learner_id':self.uid,'Learner_kind':'apprenticeship','Signature':'synthetic-signature','Signature_date':'2026-10-30'}],
        })
        self.assertEqual(import_payload(conn,changed,'synthetic-digest'),'accepted')
        with conn.cursor() as cur:
            cur.execute('SELECT learner_id FROM enrolment."Created_users" WHERE id=%s',[self.uid])
            self.assertEqual(cur.fetchone()[0],910010)
            cur.execute('''SELECT c.component_title,c.expected_otjh FROM "Learner".learner_training_plan_components c
                JOIN "Learner".learner_training_plan_weeks w ON w.id=c.plan_week_id
                JOIN "Learner".learner_training_plan_modules m ON m.id=w.plan_module_id
                WHERE m.learner_id=910010''')
            title,hours=cur.fetchone()
            self.assertEqual((title,str(hours)),('Synthetic lesson','2.50'))
            cur.execute('SELECT "Signature","Signature_date"::text FROM enrolment."Wizard_Personal_Details" WHERE "Learner_id"=%s',[self.uid])
            self.assertEqual(cur.fetchone(),('synthetic-signature','2026-10-30'))

    def test_conflicting_login_rolls_back_the_whole_import(self):
        with conn.cursor() as cur:
            cur.execute('INSERT INTO login."Login_accounts" ("Subject_type","Subject_id","Email","Role") VALUES(%s,%s,%s,%s)', ['staff', 999,'synthetic@example.invalid','admin'])
        with self.assertRaises(Conflict), conn.transaction():
            import_payload(conn,self.payload,'synthetic-digest')
        with conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM enrolment."Created_users" WHERE id=%s', [self.uid])
            self.assertEqual(cur.fetchone()[0],0)

    def test_existing_active_learner_is_not_rewritten(self):
        with conn.cursor() as cur:
            cur.execute('INSERT INTO enrolment."Created_users" (id,uuid,"Email","Programme_status","Username") OVERRIDING SYSTEM VALUE VALUES(%s,%s,%s,%s,%s)', [self.uid,self.uuid,'synthetic@example.invalid','Active','Study-owned name'])
        self.assertEqual(import_payload(conn,self.payload,'synthetic-digest'),'accepted')
        with conn.cursor() as cur:
            cur.execute('SELECT "Programme_status","Username" FROM enrolment."Created_users" WHERE id=%s',[self.uid])
            self.assertEqual(cur.fetchone(),('Active','Study-owned name'))

    def test_child_identity_conflict_rolls_back_parent(self):
        changed = copy.deepcopy(self.payload)
        changed['rows']['enrolment.Wizard_Personal_Details'] = [{'id':1,'Learner_id':999}]
        with self.assertRaises(Conflict), conn.transaction(): import_payload(conn,changed,'synthetic-digest')
        with conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM enrolment."Created_users" WHERE id=%s',[self.uid])
            self.assertEqual(cur.fetchone()[0],0)

    def test_staff_learner_uses_existing_staff_login_without_changing_grants(self):
        staff_uuid=str(uuid.uuid4())
        with conn.cursor() as cur:
            cur.execute('INSERT INTO enrolment."Staff_users" (uuid,"Username","Email","Access") VALUES(%s,%s,%s,%s) RETURNING id',[staff_uuid,'Synthetic staff','synthetic@example.invalid','coach'])
            staff_id=cur.fetchone()[0]
            cur.execute('INSERT INTO login."Login_accounts" ("Subject_type","Subject_id","Email","Role") VALUES(%s,%s,%s,%s)', ['staff',staff_id,'synthetic@example.invalid','staff'])
        changed=copy.deepcopy(self.payload)
        changed['rows']['login.Login_accounts']=[]
        changed['existingStaff']={'uuid':staff_uuid,'email':'synthetic@example.invalid'}
        self.assertEqual(import_payload(conn,changed,'synthetic-digest'),'accepted')
        with conn.cursor() as cur:
            cur.execute('SELECT "Role","Subject_id" FROM login."Login_accounts" WHERE "Email"=%s',['synthetic@example.invalid'])
            self.assertEqual(cur.fetchone(),('staff',staff_id))

    def test_unknown_staff_identity_cannot_be_provisioned_by_handoff(self):
        changed=copy.deepcopy(self.payload)
        changed['rows']['login.Login_accounts']=[]
        changed['existingStaff']={'uuid':str(uuid.uuid4()),'email':'synthetic@example.invalid'}
        with self.assertRaises(Conflict): import_payload(conn,changed,'synthetic-digest')


result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(HandoffPostgresTests))
conn.close()
raise SystemExit(0 if result.wasSuccessful() else 1)
