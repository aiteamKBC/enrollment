import copy
import json
from datetime import date
from django.db import connections
from django.test import TestCase, override_settings
from enrolment_api.wizard_layout import CONN
from .intake_calendar import cohort_assignment_window
from .programme_assignment import resolve_automatic_cohort
from .programme_structure import validate_structure
from .models import ModuleConfigurationDraft


@override_settings(PROGRAMME_STRUCTURE_COHORT_LEARNER_ENABLED=True)
class CohortCalendarTests(TestCase):
    databases = {'default','enrolment'}
    def setUp(self):
        connection=connections[CONN]
        connection.ensure_connection()
        connection.connection.create_function('to_regclass',1,lambda name: name if connection.connection.execute(
            "SELECT name FROM enrolment.sqlite_master WHERE type='table' AND name='Wizard_Form_Layouts'").fetchone() else None)
        self.programme={'id':'ME_L4','curriculumProgrammeId':'PROG-ME-L4','title':'Marketing Executive',
            'modules':[],'ksbs':[],'mappings':[],'cohorts':[]}

    def cohort(self, cid, year, month, start, end, status='planned'):
        result={'id':cid,'programmeId':'ME_L4','name':f'Cohort {month}/{year}',
            'startDate':f'{year}-{month:02}-01','status':status,
            'assignmentPeriod':dict(zip(('startMonth','startDay','endMonth','endDay'),(*start,*end))),
            'modules':[{'id':cid+'-module','title':'Module','sessions':12,'selection':'required'}],'mappings':[]}
        self.programme['cohorts'].append(result)
        with connections['default'].cursor() as q:
            q.execute('INSERT INTO curriculum.cohorts (cohort_id,programme_id,cohort_name,status) VALUES (%s,%s,%s,%s)',[cid,'PROG-ME-L4',result['name'],status])
        return result

    def publish_fixture(self):
        snapshot={'schemaVersion':2,'programmes':[self.programme]}
        validate_structure(snapshot)
        with connections[CONN].cursor() as q:
            q.execute('DELETE FROM enrolment."Wizard_Form_Layouts"')
            q.execute('INSERT INTO enrolment."Wizard_Form_Layouts" (id,"Layout") VALUES (%s,%s)',[100,json.dumps({'programmeStructure':snapshot})])

    def test_boundaries_cross_year_and_future_years_use_real_ids_not_labels(self):
        for year in (2027,2028,2035):
            self.cohort(f'REAL-FEB-{year}',year,2,(10,15),(2,15))
            self.cohort(f'REAL-JUNE-{year}',year,6,(2,16),(6,15))
            self.cohort(f'REAL-OCT-{year}',year,10,(6,16),(10,14))
        self.publish_fixture()
        for day,cid in [(date(2026,10,15),'REAL-FEB-2027'),(date(2027,2,15),'REAL-FEB-2027'),
            (date(2027,2,16),'REAL-JUNE-2027'),(date(2027,6,15),'REAL-JUNE-2027'),
            (date(2027,6,16),'REAL-OCT-2027'),(date(2027,10,14),'REAL-OCT-2027'),
            (date(2027,10,15),'REAL-FEB-2028'),(date(2028,2,29),'REAL-JUNE-2028'),
            (date(2034,11,20),'REAL-FEB-2035')]:
            with self.subTest(day=day):
                self.assertEqual(resolve_automatic_cohort('default','PROG-ME-L4',day)[0],cid)

    def test_unpublished_draft_is_not_used_and_latest_previous_fallback_is_preserved(self):
        self.cohort('REAL-OCT-2029',2029,10,(6,16),(10,14))
        self.publish_fixture()
        draft=copy.deepcopy(self.programme)
        draft['cohorts'].append({'id':'DRAFT-FEB-2030','name':'Feb 2030'})
        ModuleConfigurationDraft.objects.create(actor_key='programme-structure:shared',base_version=100,configuration={'programmes':[draft]})
        actual=resolve_automatic_cohort('default','PROG-ME-L4',date(2029,11,20))
        self.assertEqual(actual,('REAL-OCT-2029','Cohort 10/2029',True))

    def test_missing_archived_empty_and_wrong_programme_are_not_assigned(self):
        c=self.cohort('REAL-FEB',2027,2,(10,15),(2,15))
        self.publish_fixture()
        with connections['default'].cursor() as q:
            q.execute("UPDATE curriculum.cohorts SET programme_id='OTHER'")
        self.assertIsNone(resolve_automatic_cohort('default','PROG-ME-L4',date(2027,1,1)))
        with connections['default'].cursor() as q:
            q.execute("UPDATE curriculum.cohorts SET programme_id='PROG-ME-L4',status='archived'")
        self.assertIsNone(resolve_automatic_cohort('default','PROG-ME-L4',date(2027,1,1)))
        with connections['default'].cursor() as q:
            q.execute("UPDATE curriculum.cohorts SET status='planned'")
        c['modules']=[];self.publish_fixture()
        self.assertIsNone(resolve_automatic_cohort('default','PROG-ME-L4',date(2027,1,1)))

    def test_completed_target_and_previous_cohorts_are_never_assigned(self):
        feb = self.cohort('REAL-FEB-2027', 2027, 2, (10, 15), (2, 15), status='completed')
        self.cohort('REAL-JUNE-2027', 2027, 6, (2, 16), (6, 15), status='planned')
        self.cohort('REAL-OCT-2027', 2027, 10, (6, 16), (10, 14), status='completed')
        self.publish_fixture()
        # The completed target cannot be used, nor can it be selected as a
        # previous-cohort fallback before June begins.
        self.assertIsNone(resolve_automatic_cohort('default', 'PROG-ME-L4', date(2027, 2, 15)))
        self.assertEqual(resolve_automatic_cohort('default', 'PROG-ME-L4', date(2027, 2, 16))[0], 'REAL-JUNE-2027')
        # Skip completed October and fall back to the most recent eligible June.
        self.assertEqual(resolve_automatic_cohort('default', 'PROG-ME-L4', date(2027, 7, 1))[:2],
                         ('REAL-JUNE-2027', 'Cohort 6/2027'))
        feb['status'] = 'planned'
        self.publish_fixture()
        self.assertEqual(resolve_automatic_cohort('default', 'PROG-ME-L4', date(2027, 1, 1))[0], 'REAL-FEB-2027')

    def test_unpublished_curriculum_status_edit_does_not_change_published_eligibility(self):
        cohort = self.cohort('REAL-FEB-2027', 2027, 2, (10, 15), (2, 15), status='planned')
        self.publish_fixture()
        with connections['default'].cursor() as q:
            q.execute("UPDATE curriculum.cohorts SET status='completed' WHERE cohort_id='REAL-FEB-2027'")
        self.assertEqual(resolve_automatic_cohort('default', 'PROG-ME-L4', date(2027, 1, 1))[0], 'REAL-FEB-2027')
        cohort['status'] = 'completed'
        self.publish_fixture()
        self.assertIsNone(resolve_automatic_cohort('default', 'PROG-ME-L4', date(2027, 1, 1)))

    def test_overlaps_and_invalid_dates_are_rejected_without_modifying_snapshot(self):
        self.cohort('FEB',2027,2,(10,15),(2,15))
        self.cohort('JUNE',2027,6,(2,15),(6,15))
        snapshot={'programmes':[self.programme]}
        with self.assertRaisesRegex(ValueError,'overlap'):validate_structure(snapshot)
        with self.assertRaisesRegex(ValueError,'valid'):
            cohort_assignment_window({'startDate':'2027-02-01','assignmentPeriod':{'startMonth':2,'startDay':30,'endMonth':6,'endDay':15}})

    def test_real_cohort_without_published_structure_stays_pending(self):
        self.cohort('REAL-FEB',2027,2,(10,15),(2,15))
        self.assertIsNone(resolve_automatic_cohort('default','PROG-ME-L4',date(2027,1,1)))

    def test_assignment_and_fallback_ignore_missing_or_changed_delivery_dates(self):
        feb=self.cohort('FEB-2035',2035,2,(10,15),(2,15))
        june=self.cohort('JUNE-2035',2035,6,(2,16),(6,15))
        feb['assignmentPeriod']['year']=2035
        feb['startDate']='1999-08-01'
        june['assignmentPeriod']['year']=2035
        june.pop('startDate')
        june['name']='Summer'
        self.publish_fixture()
        self.assertEqual(resolve_automatic_cohort('default','PROG-ME-L4',date(2034,11,20))[0],'FEB-2035')
        # Returned names remain authoritative Curriculum metadata, not snapshot labels.
        self.assertEqual(resolve_automatic_cohort('default','PROG-ME-L4',date(2035,6,16)),('JUNE-2035','Cohort 6/2035',True))

    def test_delivery_date_is_not_a_silent_substitute_for_missing_assignment_year(self):
        with self.assertRaisesRegex(ValueError,'year'):
            cohort_assignment_window({'name':'Undated Cohort','startDate':'2035-01-01',
                'assignmentPeriod':{'startMonth':10,'startDay':15,'endMonth':2,'endDay':15}})

    def test_pcp_assignment_does_not_change_route_credit_count_or_owner_rules(self):
        from .modules_configuration import defaults
        original=next(p for p in defaults()['programmes'] if p['id']=='PCP_L6')
        cohort=self.cohort('REAL-PCP',2027,2,(10,15),(2,15))
        self.programme.update(id='PCP_L6',curriculumProgrammeId='PROG-PCP-L6')
        cohort.update(programmeId='PCP_L6',modules=[],routes=[dict(copy.deepcopy(r),name=r['title']) for r in original['pathways']])
        with connections['default'].cursor() as q:
            q.execute("UPDATE curriculum.cohorts SET programme_id='PROG-PCP-L6'")
        self.publish_fixture()
        before=copy.deepcopy(self.programme)
        self.assertEqual(resolve_automatic_cohort('default','PROG-PCP-L6',date(2027,1,1))[0],'REAL-PCP')
        self.assertEqual(self.programme,before)

    def test_saved_cohort_period_edit_only_changes_assignment_after_publication(self):
        self.cohort('FEB-2035',2035,2,(10,15),(2,15))
        self.cohort('JUNE-2035',2035,6,(2,16),(6,15))
        self.publish_fixture()
        changed=copy.deepcopy(self.programme)
        changed['cohorts'][0]['assignmentPeriod']['endDay']=14
        changed['cohorts'][1]['assignmentPeriod']['startDay']=15
        snapshot={'schemaVersion':2,'programmes':[changed]}
        validate_structure(snapshot)
        ModuleConfigurationDraft.objects.create(actor_key='programme-structure:shared',base_version=100,configuration=snapshot)
        self.assertEqual(ModuleConfigurationDraft.objects.get().configuration,snapshot)
        self.assertEqual(resolve_automatic_cohort('default','PROG-ME-L4',date(2035,2,15))[0],'FEB-2035')
        original=copy.deepcopy(self.programme)
        self.programme=changed
        self.publish_fixture()  # Isolated published fixture, never the real publisher/Neon.
        self.assertEqual(resolve_automatic_cohort('default','PROG-ME-L4',date(2035,2,15))[0],'JUNE-2035')
        for before,after in zip(original['cohorts'],changed['cohorts']):
            self.assertEqual(before['id'],after['id'])
            self.assertEqual(before['modules'],after['modules'])
