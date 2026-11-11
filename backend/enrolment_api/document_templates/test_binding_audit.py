"""Learner A/B source-to-issued-payload audit, isolated in-memory storage only."""
import copy
import json
import os
import re
import base64
import struct
import zlib
from contextlib import ExitStack
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone
from learner_api import apprenticeship_agreement as aa, ilr_document as ilr, training_plan_document as tp, written_agreement as wa, review_form
from learner_api.models import EnrolmentUser, Employer, RplReviewDetail, EnrolmentReview
from enrolment_api.models import WizardPersonalDetails, WizardIlrLearnerDetails, WizardPlr, WizardPlrRecord, WizardKsbAssessment, WizardSkillsRadar, ExtendedIlr
from curriculum_api.models import ModuleAuthoringModule, ModuleAuthoringWeek, ModuleAuthoringComponent
from . import service, preview, revisions
from .learner_sources import document_dates, date_warnings, plan_modules
from .models import IssuedTemplate, RenderHistory
from .registry import PROGRAMMES, REVIEW_KEYS, seed, seeds, validate
from .test_revisions import MARK
from . import test_templates as helpers

def mark_for(learner):
    if learner.email.startswith('a@'):return MARK
    def chunk(kind,data):return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
    pixels=b''.join(b'\x00'+b''.join(bytes((20,60,180)) if (x+y)%5==0 else bytes((255,255,255)) for x in range(64)) for y in range(16))
    image=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',64,16,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(pixels))+chunk(b'IEND',b'')
    return 'data:image/png;base64,'+base64.b64encode(image).decode()


@override_settings(DOCUMENT_REVISIONS_ENABLED=True)
@patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH':'1'})
class LearnerBindingAuditTests(TestCase):
    databases = {'default', 'enrolment'}
    request = helpers.TemplateWorkflowTests.request
    payload = helpers.TemplateWorkflowTests.payload
    save = helpers.TemplateWorkflowTests.save
    publish = helpers.TemplateWorkflowTests.publish

    @classmethod
    def setUpClass(cls):
        # These are additional fixtures, never a migration or a production DDL.
        if connection.vendor != 'sqlite' or connection.settings_dict['NAME'] != ':memory:':
            raise RuntimeError('Binding audit requires isolated in-memory SQLite.')
        with connection.schema_editor() as editor:
            for model in (Employer, WizardPersonalDetails, WizardIlrLearnerDetails, WizardPlr, WizardPlrRecord,
                          WizardKsbAssessment, WizardSkillsRadar, RplReviewDetail,
                          ModuleAuthoringModule, ModuleAuthoringWeek, ModuleAuthoringComponent):
                constraints=[f for f in model._meta.fields if getattr(f,'db_constraint',False)]
                for field in constraints:field.db_constraint=False
                try:editor.create_model(model)
                finally:
                    for field in constraints:field.db_constraint=True
                editor.deferred_sql.clear()
        with connection.cursor() as cursor:
            for name, kind in [('start_date','TEXT'),('end_date','TEXT'),('learner_roster_mode','TEXT')]:
                cursor.execute(f'ALTER TABLE curriculum.modules ADD COLUMN {name} {kind}')
        super().setUpClass()

    def setUp(self):
        from django.test import RequestFactory
        self.factory = RequestFactory()
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        # PostgreSQL ANY(array) is the only SQL adaptation. The real module,
        # week/component queries, visibility clauses and DB values still run.
        real_cursor = connection.cursor
        class ArrayCursor:
            def __init__(self): self.inner = real_cursor()
            def __enter__(self): self.inner.__enter__(); return self
            def __exit__(self,*args): return self.inner.__exit__(*args)
            def __getattr__(self,key): return getattr(self.inner,key)
            def execute(self,sql,params=None):
                # Live-only fixtures never enter the archive-window branch;
                # SQLite has no PostgreSQL interval literal syntax.
                sql=sql.replace("interval '5 minutes'",'0')
                if 'ANY(%s)' in sql:
                    params=list(params or [])
                    index=sql[:sql.index('ANY(%s)')].count('%s')
                    values=list(params[index]); params[index:index+1]=values
                    sql=sql.replace('= ANY(%s)','IN ('+','.join(['%s']*len(values))+')')
                return self.inner.execute(sql,params)
        self.stack.enter_context(patch.object(connection,'cursor',side_effect=lambda: ArrayCursor()))
        self.stack.enter_context(patch('learner_api.learning_plan._aptem_subject_modules',return_value={}))
        self.stack.enter_context(patch('learner_api.learning_plan._group_module_ids',side_effect=lambda programme,*a,**kw:[('A' if 'Marketing' in programme else 'B')+'-module']))
        self.stack.enter_context(patch('learner_api.curriculum.ksb_profile_for_programme',side_effect=lambda programme:({'label':programme},[{'id':'K1','title':'Audited workplace knowledge','kind':'knowledge','theme':'Knowledge','codes':['K1']}])) )
        self.people=[]
        for tag, programme in [('A','ME_L4'),('B','APM_L4')]:
            employer=Employer.objects.create(first_name=f'AUDIT_{tag}_EMPLOYER',surname='Contact',address_1=f'AUDIT_{tag}_WORKPLACE',post_code=f'{tag}1 1AA')
            learner=EnrolmentUser.all_learners.create(username=f'AUDIT_{tag}_LEARNER',email=f'{tag.lower()}@audit.invalid',phone_number=f'0700000000{1 if tag=="A" else 2}',
                employer_id=employer.pk,employer=f'AUDIT_{tag}_COMPANY',line_manager=f'AUDIT_{tag}_MANAGER',learning_provider=f'AUDIT_{tag}_PROVIDER',
                programme=PROGRAMMES[programme],cohort=f'AUDIT_{tag}_COHORT',group=f'AUDIT_{tag}_GROUP',
                start_date=date(2030,1,1),end_date=date(2033,1,1),learner_start_date='2030-02-01' if tag=='A' else '2031-03-01',
                learner_end_date='2032-02-01' if tag=='A' else '2033-03-01',address_line_1=f'AUDIT_{tag}_HOME',address_line_2=f'AUDIT_{tag}_STREET',address_line_3=f'AUDIT_{tag}_TOWN',address_line_4=f'AUDIT_{tag}_COUNTY',current_postcode=f'{tag}2 2BB',national_insurance_number=f'{tag}{tag}123456C',
                learning_plan=[{'moduleId':tag+'-module','assignmentMode':'explicit'}],programme_status='Onboarding')
            with connection.cursor() as cursor:cursor.execute('INSERT INTO curriculum.programmes (programme_id,name,level) VALUES (%s,%s,%s)',[programme,PROGRAMMES[programme],'4'])
            WizardPersonalDetails.objects.create(learner_kind='apprenticeship',learner_id=learner.pk,first_name=f'AUDIT_{tag}_GIVEN',last_name=f'AUDIT_{tag}_FAMILY',date_of_birth=date(1998 if tag=='A' else 1995,4,5),sex='Female' if tag=='A' else 'Male')
            WizardIlrLearnerDetails.objects.create(learner_kind='apprenticeship',learner_id=learner.pk,job_title=f'AUDIT_{tag}_JOB',employment_start_date=date(2029 if tag=='A' else 2028,6,7),long_term_disability=tag=='A',self_employed=tag=='B',full_time_education=False,highest_qualification='Level 3',ethnicity=f'AUDIT_{tag}_ETHNICITY',years_at_address=0,postcode_prior_to_enrolment=f'{tag}4 4DD',employment_status='In paid employment')
            WizardPlr.objects.create(learner_kind='apprenticeship',learner_id=learner.pk,uln=f'{1 if tag=="A" else 2}234567890')
            WizardPlrRecord.objects.create(learner_kind='apprenticeship',learner_id=learner.pk,record_ref=f'{tag}-qualification',subject=f'AUDIT_{tag}_QUALIFICATION',place_of_study=f'AUDIT_{tag}_COLLEGE',qualification_type='Diploma',level='3',credits=0,award_date=date(2027,8,9),grade='Pass',record_type='Manual')
            WizardSkillsRadar.objects.create(learner_kind='apprenticeship',learner_id=learner.pk,standard_id='')
            WizardKsbAssessment.objects.create(learner_kind='apprenticeship',learner_id=learner.pk,ksb_id='K1',level='Expert' if tag=='A' else 'Rarely',score=7 if tag=='A' else 2,note=f'AUDIT_{tag}_KSB_NOTE')
            answers={'contact':{'byPost':True,'byPhone':False,'byEmail':None},'eligibility':{'residentPrev3Years':tag=='A','yearsInUk':0,'countryOfResidence':'United Kingdom'},
                'nextOfKin':{'fullName':f'AUDIT_{tag}_KIN','sameAddressAsLearner':True,'address':'stale kin address','postcode':'STALE'},
                'employer':{'organisationName':learner.employer,'postcode':f'{tag}3 3CC','lineManagerEmail':f'manager-{tag.lower()}@audit.invalid','lineManagerPhone':f'0800000000{1 if tag=="A" else 2}'}}
            # Populate all repository-declared answer paths, including retained
            # legacy keys, without altering any real wizard or learner row.
            for ref,definition in seeds()['fields']['ilr'].items():
                if not ref.startswith('document.answers.'):continue
                parts=ref[len('document.answers.'):].split('.');node=answers
                for part in parts[:-1]:node=node.setdefault(part,{})
                if parts[-1]=='residentPrevious3Years':continue
                node.setdefault(parts[-1],tag=='A' if definition['format']=='yesno' else f'AUDIT_{tag}_{parts[-1]}')
            ExtendedIlr.objects.create(learner_kind='apprenticeship',learner_id=learner.pk,answers=answers)
            ModuleAuthoringModule.objects.create(module_catalogue_id=tag+'-module',programme_id=programme,programme_name=learner.programme,title=f'AUDIT_{tag}_MODULE',total_otjh=Decimal('3.40'))
            with connection.cursor() as cursor:cursor.execute('UPDATE curriculum.modules SET start_date=%s WHERE module_catalogue_id=%s',['2030-05-06' if tag=='A' else '2031-07-08',tag+'-module'])
            ModuleAuthoringWeek.objects.create(id=tag+'-week',module_catalogue_id=tag+'-module',week_number=1,title=f'AUDIT_{tag}_WEEK')
            for index,(kind,hours) in enumerate([('video','.75'),('live_session','2.5'),('reading','.15'),('quiz','0')]):
                ModuleAuthoringComponent.objects.create(id=f'{tag}-component-{index}',week_id=tag+'-week',module_catalogue_id=tag+'-module',type=kind,title=f'AUDIT_{tag}_ACTIVITY_{index}',expected_otjh=Decimal(hours),display_order=index)
            for review_type,key in REVIEW_KEYS.items():
                content=seed(key); review_answers={}
                for block in content['blocks']:
                    if block.get('source','').startswith('review:'):
                        section=block['source'][7:];review_answers[section]={name:(f'AUDIT_{tag}_ANSWER_{name}' if name not in ('over16','noticeRead','acknowledged') else 'Yes' if tag=='A' else 'No') for name in block.get('labels',{})}
                if review_type=='workspace':review_answers.update(plr={'reportedAttainment':'Level 3','subjectLevels':{'English':'Level 2'}},priorLearning={'items':[{name:f'AUDIT_{tag}_{name}' for name in ('description','impact','durationReduced','costReduced','offTheJobTimeReduced')}]},skillsRadar={'notes':f'AUDIT_{tag}_REVIEW_RADAR_NOTE'},comments={'text':f'AUDIT_{tag}_REVIEW_COMMENT'})
                if review_type=='eligibility-review':review_answers.update(functionalSkills={'initialAssessments':[{'subject':f'AUDIT_{tag}_ENGLISH','level':'2','date':'2029-03-04','outcome':f'AUDIT_{tag}_RESULT'}],'diagnosticAssessments':[{'subject':f'AUDIT_{tag}_MATHS','level':'2','date':'2029-05-06','outcome':f'AUDIT_{tag}_DIAGNOSTIC'}],'exemptions':{'English':f'AUDIT_{tag}_EXEMPTION'},'results':{'English':{'score':f'AUDIT_{tag}_SCORE','assessmentDate':'2029-07-08'}}},comments={'text':f'AUDIT_{tag}_REVIEW_COMMENT'})
                signature_fields={}
                for role in ('learner','admin','employer'):
                    signature_fields.update({role+'_signature':mark_for(learner),role+'_signed_name':f'AUDIT_{tag}_{role.upper()}_SIGNER',role+'_signed_at':timezone.now()})
                EnrolmentReview.objects.create(event_key=f'audit-{tag}-{review_type}',learner_kind='apprenticeship',learner_id=learner.pk,review_type=review_type,review_label=content['title'],form_answers=review_answers,form_completed=True,**signature_fields)
            self.people.append(learner)
        self.learner=self.people[0]

    def issued(self,key,learner):
        self.publish({**seed(key),'designTheme':'purple-reference-v2'})
        if key.startswith('review-'):
            source=EnrolmentReview.objects.get(learner_id=learner.pk,review_type=seed(key)['reviewType'])
            service.bind_review_issue(source,learner)
            payload=review_form._serialize_form(source,learner,None)
            binding=service.binding_for(key,source);binding.review_snapshot={name:copy.deepcopy(payload[name]) for name in service.REVIEW_SNAPSHOT_KEYS if name in payload};binding.save(update_fields=['review_snapshot'])
            return source,review_form._serialize_form(source,learner,None)
        module,view,field={'apprenticeship-agreement':(aa,aa.issue_agreement,'agreement'),'ilr':(ilr,ilr.issue_ilr,'document'),'training-plan':(tp,tp.issue_training_plan,'document'),'written-agreement':(wa,wa.issue_written_agreement,'document')}[key]
        response=self.payload(self.request(view,'POST',pk=learner.pk),201)[field]
        model,serialize,_=revisions.source_api(key);source=model.objects.get(pk=response['id'])
        for role in seed(key)['signatureRoles']:
            setattr(source,role+'_signature',mark_for(learner));setattr(source,role+'_signed_name',f'AUDIT_{"A" if learner==self.people[0] else "B"}_{role.upper()}_SIGNER');setattr(source,role+'_signed_at',timezone.now())
            if hasattr(source,role+'_position'):setattr(source,role+'_position',f'AUDIT_{"A" if learner==self.people[0] else "B"}_{role.upper()}_ROLE')
        source.recalculate_signed();source.save()
        return source,serialize(source)

    def test_real_sources_are_learner_scoped_and_new_dates_use_learner_specific_values(self):
        for learner in self.people:
            tag='A' if learner==self.people[0] else 'B'
            details=ilr.derive_learner_details(learner);answers=ilr.derive_answers(learner)
            self.assertEqual(details['givenNames'],f'AUDIT_{tag}_GIVEN');self.assertEqual(details['uln'],f'{1 if tag=="A" else 2}234567890')
            self.assertEqual(answers['nextOfKin']['address'],'\n'.join(f'AUDIT_{tag}_{name}' for name in ('HOME','STREET','TOWN','COUNTY')))
            self.assertEqual(ExtendedIlr.objects.get(learner_id=learner.pk).answers['nextOfKin']['address'],'stale kin address')
            for key in seeds()['templates']:
                result=preview.render_payload({**seed(key),'designTheme':'purple-reference-v2'},learner)
                text=json.dumps(result,default=str);self.assertNotIn(f'AUDIT_{"B" if tag=="A" else "A"}_',text)
                self.assertEqual(result['template']['fields']['enrolment.startDate'],learner.learner_start_date)
                self.assertFalse(result['sample']);self.assertEqual(result['bindingWarnings'],[])
                if key=='ilr':self.assertIs(result['template']['fields']['document.answers.eligibility.residentPrevious3Years'],tag=='A')
                if key=='training-plan':
                    self.assertEqual(result['data']['programme']['level'],'4');self.assertEqual(result['data']['employment']['jobTitle'],f'AUDIT_{tag}_JOB')
                    self.assertEqual(result['data']['learningPlan'][0]['plannedDate'],'2030-05-06' if tag=='A' else '2031-07-08')
                    self.assertEqual(result['data']['learningPlan'][0]['weeks'][0]['components'][0]['otjHours'],.75)
                if key=='apprenticeship-agreement':self.assertNotEqual(result['template']['fields']['document.particulars.durationWeeks'],'')
                if key=='written-agreement':self.assertEqual(result['data']['particulars']['employerPostcode'],f'{tag}1 1AA')

    def test_saved_empty_cross_programme_retired_and_inherited_plan_rules_are_preserved(self):
        learner=self.people[0]
        learner.learning_plan=[];self.assertEqual(plan_modules(learner),[])
        learner.learning_plan=[{'moduleId':'B-module','assignmentMode':'explicit'}];self.assertEqual([m['moduleId'] for m in plan_modules(learner)],['B-module'])
        learner.learning_plan=[{'moduleId':'retired','moduleTitle':'Historical retained module','hours':1.25,'assignmentMode':'explicit'}]
        self.assertEqual(plan_modules(learner)[0]['hours'],1.25)
        learner.learning_plan=[{'moduleId':'B-module'}];self.assertEqual([m['moduleId'] for m in plan_modules(learner)],['B-module','A-module'])

    def test_only_student_dates_are_used_with_missing_invalid_and_conflict_warnings(self):
        learner=self.people[0];learner.learner_start_date='';self.assertIsNone(document_dates(learner)[0])
        self.assertEqual(document_dates(learner)[1],date(2032,2,1))
        self.assertEqual(date_warnings(learner)[0]['code'],'missing_date')
        learner.learner_start_date='not a date';self.assertIsNone(document_dates(learner)[0]);self.assertEqual(date_warnings(learner)[0]['code'],'invalid_date')
        learner.learner_start_date='2035-01-01';self.assertEqual(date_warnings(learner)[0]['code'],'conflicting_dates')
        learner.learner_start_date='2030-02-01';learner.learner_end_date=None
        self.assertIsNone(document_dates(learner)[1]);self.assertEqual(date_warnings(learner)[0]['field'],'learner_end_date')
        personal=WizardIlrLearnerDetails.objects.get(learner_id=learner.pk);personal.employment_start_date=date(1900,2,2);personal.save()
        self.assertEqual(ilr.derive_learner_details(learner)['employmentStartDate'],'1900-02-02') # source value, never a default

    def test_all_new_document_paths_leave_missing_student_dates_blank_even_with_cohort_dates(self):
        learner=self.people[0]
        learner.learner_start_date=None;learner.learner_end_date=''
        learner.save(update_fields=['learner_start_date','learner_end_date'])
        self.assertIsNotNone(learner.start_date);self.assertIsNotNone(learner.end_date)
        for key in seeds()['templates']:
            result=preview.render_payload(seed(key),learner)
            self.assertIsNone(result['template']['fields']['enrolment.startDate'])
            self.assertIsNone(result['template']['fields']['enrolment.endDate'])
            self.assertEqual([warning['code'] for warning in result['bindingWarnings']],['missing_date','missing_date'])
            source,issued=self.issued(key,learner)
            self.assertIsNone(issued['template']['fields']['enrolment.startDate'])
            self.assertIsNone(issued['template']['fields']['enrolment.endDate'])
            if key=='apprenticeship-agreement':
                self.assertIsNone(source.start_date);self.assertIsNone(source.end_date)
                self.assertIsNone(source.practical_start);self.assertIsNone(source.practical_end)
                self.assertIsNone(source.duration_weeks)
            elif key in ('training-plan','written-agreement'):
                period=issued['programme'] if key=='training-plan' else issued['particulars']
                self.assertEqual(period['startDate'],'')
                self.assertEqual(period['endDate' if key=='training-plan' else 'plannedEndDate'],'')
            elif key.startswith('review-'):
                self.assertEqual(issued['learnerInformation']['programmeStartDate'],'')
                self.assertEqual(issued['learnerInformation']['plannedEndDate'],'')

    def test_latest_valid_rpl_source_and_cancelled_preview_are_scoped(self):
        learner=self.people[0];review=EnrolmentReview.objects.get(learner_id=learner.pk,review_type='workspace')
        RplReviewDetail.objects.create(review=review,event_key='old-detail',learner_id=learner.pk,uln='old-uln',reported_attainment='Old attainment')
        RplReviewDetail.objects.create(review=review,event_key='new-detail',learner_id=learner.pk,uln='updated-uln',reported_attainment='Current attainment')
        cancelled=EnrolmentReview.objects.create(learner_id=learner.pk,learner_kind='apprenticeship',event_key='cancelled',review_type='workspace',review_label='Cancelled',status='cancelled',form_answers={'comments':{'text':'CANCELLED_ANSWER'}})
        RplReviewDetail.objects.create(review=cancelled,event_key='cancelled',learner_id=learner.pk,uln='CANCELLED_ULN')
        details=ilr.derive_learner_details(learner)
        self.assertEqual(details['uln'],'updated-uln');self.assertEqual(details['priorAttainment'],'Current attainment')
        self.assertNotIn('CANCELLED_ANSWER',json.dumps(preview.render_payload(seed('review-rpl'),learner),default=str))

    def test_other_learner_cannot_read_compliance_sources(self):
        for view in (aa.apprenticeship_agreement,ilr.ilr_document,tp.training_plan_document,wa.written_agreement):
            request=self.factory.get('/isolated-audit/')
            request.user=SimpleNamespace(is_authenticated=False)
            request.login_account=SimpleNamespace(role='learner',is_active=True,subject_type='learner',subject_id=self.people[0].pk)
            self.assertEqual(view(request,pk=self.people[1].pk).status_code,404)

    def test_all_four_assigned_levels_not_recommendations(self):
        learner=self.people[0]
        from .learner_sources import programme_level
        for key,name in PROGRAMMES.items():
            with connection.cursor() as cursor:cursor.execute('INSERT OR REPLACE INTO curriculum.programmes (programme_id,name,level) VALUES (%s,%s,%s)',[key,name,'6' if key.endswith('L6') else '4'])
            learner.programme=name;self.assertEqual(programme_level(learner),'6' if key.endswith('L6') else '4')

    def test_each_pcp_pathway_does_not_silently_replace_the_official_operational_plan(self):
        from programme_suitability.models import Assessment
        from programme_suitability.modules_configuration import defaults
        learner=self.people[0];learner.programme=PROGRAMMES['PCP_L6']
        with connection.cursor() as cursor:cursor.execute('INSERT INTO curriculum.programmes (programme_id,name,level) VALUES (%s,%s,%s)',['PCP_L6',learner.programme,'6'])
        row=Assessment.objects.create(learner_id=learner.pk,learner_kind='apprenticeship',configuration_version=1,configuration={},pathway='project-controls',selected_programme='PCP_L6')
        for path in next(p for p in defaults()['programmes'] if p['id']=='PCP_L6')['pathways']:
            row.modules_selection={'programme':'PCP_L6','pathway':path['id'],'modulesByPathway':{path['id']:[]}};row.save()
            data=tp.derive_content(learner)
            self.assertEqual(data['programme']['programme'],PROGRAMMES['PCP_L6']);self.assertEqual(data['programme']['level'],'6')
            self.assertEqual([m['moduleId'] for m in data['learningPlan']],['A-module'])

    def test_frozen_issued_content_revisions_and_artifacts_for_both_learners(self):
        artifacts={}
        for learner in self.people:
            tag='A' if learner==self.people[0] else 'B'
            for key in seeds()['templates']:
                source,issued=self.issued(key,learner)
                artifacts[f'{tag}-{key}']={'template':issued['template'],'data':issued,'sample':False,'notice':'ISOLATED AUDIT FIXTURE — no real learner','missingFields':[],'previewToken':None}
                frozen=copy.deepcopy(issued)
                learner.username=f'AUDIT_{tag}_LATER_NAME';learner.programme='Later reassignment';learner.learner_start_date='2040-01-01'
                self.assertEqual(revisions.serialize_source(key,source,learner),frozen)
                learner.refresh_from_db()
                draft=revisions.create_draft(key,str(source.pk),learner,'isolated audit')
                self.assertEqual(draft.issue.learner_id,learner.pk)
                self.assertEqual(revisions.payload(draft)['template']['fields'],frozen['template']['fields'])
                self.assertEqual(revisions.payload(draft)['marks'] if 'marks' in frozen else revisions.payload(draft)['signatures'],frozen.get('marks',frozen['signatures']))
                self.assertIsNone(revisions.get_revision(draft.pk,self.people[1 if tag=='A' else 0].pk))
        self.assertEqual(RenderHistory.objects.count(),0)
        if os.environ.get('BINDING_AUDIT_ARTIFACTS'):
            path=Path(os.environ['BINDING_AUDIT_ARTIFACTS']);path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(artifacts,default=str),encoding='utf8')

    def test_old_envelopes_and_registry_values_preserve_false_zero_decimal_and_sample_separation(self):
        self.assertEqual(service.scalar(Decimal('0.7500')),'0.7500');self.assertEqual(service.scalar(False),False);self.assertEqual(service.scalar(0),0)
        for key in seeds()['templates']:
            sample=preview.render_payload(seed(key));self.assertTrue(sample['sample']);self.assertIn('SAMPLE',sample['notice'])
        source,payload=self.issued('ilr',self.people[0]);binding=service.binding_for('ilr',source)
        historical=copy.deepcopy(binding.template_snapshot);historical.pop('bindingVersion');binding.template_snapshot=historical;binding.save(update_fields=['template_snapshot'])
        self.assertNotIn('bindingVersion',ilr._document_json(source)['template'])
