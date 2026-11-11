"""Reproducible, read-only source inventory and extracted-PDF audit.

Reads repository definitions + isolated test artifacts. Never loads .env or DB.
Usage: backend/.venv/Scripts/python.exe tools/audit_document_bindings.py
"""
import ast
import base64
import collections
import csv
import json
import math
import hashlib
import re
from pathlib import Path
import fitz

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.cache/binding-audit'
SEED=json.loads((ROOT/'backend/enrolment_api/document_templates/seed.json').read_text(encoding='utf8'))
TOKEN=re.compile(r'\{\{([\w.-]+)\}\}')
ALIASES={'learner':'EnrolmentUser','personal':'WizardPersonalDetails','details':'WizardIlrLearnerDetails','ilr_details':'WizardIlrLearnerDetails','rpl':'RplReviewDetail','plr':'WizardPlr'}
TABLES={'EnrolmentUser':'enrolment.Created_users','WizardPersonalDetails':'enrolment.Wizard_Personal_Details','WizardIlrLearnerDetails':'enrolment.Wizard_Ilr_Learner_Details','RplReviewDetail':'enrolment.Review_RPL','WizardPlr':'enrolment.Wizard_Plr','ExtendedIlr':'enrolment.Extended_ILR','EnrolmentReview':'enrolment.Enrolment_Reviews','WizardPlrRecord':'enrolment.Wizard_Plr_Records','WizardKsbAssessment':'enrolment.Wizard_Ksb_Assessments','WizardSkillsRadar':'enrolment.Wizard_Skills_Radar','Employer':'enrolment.Employers'}
TABLES.update({'ApprenticeshipAgreement':'enrolment.Apprenticeship_Agreements','IlrDocument':'enrolment.ILR_Documents','TrainingPlanDocument':'enrolment.Training_Plan_Documents','WrittenAgreement':'enrolment.Written_Agreements',
               'ModuleAuthoringModule':'curriculum.modules','ModuleAuthoringWeek':'curriculum.weeks','ModuleAuthoringComponent':'curriculum.components'})

def expression_map(file,function):
    tree=ast.parse((ROOT/'backend/learner_api'/file).read_text(encoding='utf8'))
    func=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==function)
    result={}
    def unpack(node,prefix=''):
        if not isinstance(node,ast.Dict):return
        for key,value in zip(node.keys,node.values):
            if not isinstance(key,ast.Constant):continue
            path=prefix+str(key.value)
            if isinstance(value,ast.Dict):unpack(value,path+'.')
            else:result[path]=ast.unparse(value)
    if function.startswith('derive_') and function in ('derive_particulars','derive_learner_details'):
        for n in func.body:
            if isinstance(n,ast.Return):unpack(n.value)
    else:
        for n in func.body:
            if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name):unpack(n.value,n.targets[0].id+'.')
    return result

MAPS={'apprenticeship-agreement':expression_map('apprenticeship_agreement.py','derive_particulars'),
      'ilr':expression_map('ilr_document.py','derive_learner_details'),
      'training-plan':expression_map('training_plan_document.py','derive_content'),
      'written-agreement':expression_map('written_agreement.py','derive_content')}
COMMON={'learner.fullName':'EnrolmentUser.username','learner.id':'EnrolmentUser.pk','programme.name':'EnrolmentUser.programme (official assignment; confirmed selection is synchronized by programme_assignment.py)',
        'programme.cohort':'EnrolmentUser.cohort','enrolment.startDate':'document_dates: EnrolmentUser.learner_start_date only; missing remains blank with warning, no programme/cohort fallback',
        'enrolment.endDate':'document_dates: EnrolmentUser.learner_end_date only; missing remains blank with warning, no programme/cohort fallback',
        'employer.name':'EnrolmentUser.employer','employer.manager':'EnrolmentUser.line_manager'}
INFO={'name':'username','programmeName':'programme','programmeStartDate':'learner_start_date','plannedEndDate':'learner_end_date','programmeStatus':'programme_status','employer':'employer','manager':'line_manager','mentor':'mentor'}

def source_for(doc,key,definition):
    key=definition.get('source',key)
    if key in COMMON:return COMMON[key]
    if key.startswith('signature.'):
        _,role,part=key.split('.')
        return f"{model_for(doc)}.{role}_{'signed_name' if part=='name' else 'signed_at'}"
    if key.startswith('document.learnerInformation.'):
        name=key.split('.')[-1]
        return COMMON['enrolment.startDate' if name=='programmeStartDate' else 'enrolment.endDate'] if name in ('programmeStartDate','plannedEndDate') else 'EnrolmentUser.'+INFO[name]+' -> issued header / first-signature review_snapshot'
    if key.startswith('document.answers.'):
        path=key[len('document.answers.'):]
        if path=='eligibility.residentPrevious3Years':path='eligibility.residentPrev3Years (legacy identifier kept)'
        return 'ExtendedIlr.answers.'+path+(' (sameAddressAsLearner=True resolves Created_users address/postcode into new snapshot)' if path in ('nextOfKin.address','nextOfKin.postcode') else '')
    if key=='document.epa.period':return "TrainingPlanDocument.epa: epaPeriodFrom + ' - ' + epaPeriodTo; future derive_content has no canonical EPA dates"
    path=key[len('document.'):]
    if doc=='apprenticeship-agreement':
        path=path.removeprefix('particulars.')
        path={'practicalStartDate':'practicalStart','practicalEndDate':'practicalEnd','plannedOtjHours':'plannedOtjh'}.get(path,path)
    if doc=='ilr':path=path.removeprefix('learnerDetails.')
    value=MAPS.get(doc,{}).get(path)
    if value is None:
        if path.endswith('.website') or path.endswith('.name'):return 'No value supplied by derive_content for this optional contact property'
        return 'UNRESOLVED: no producer expression located for '+path
    for alias,model in sorted(ALIASES.items(),key=lambda x:-len(x[0])):value=re.sub(r'\b'+alias+r'\b',model,value)
    supplements=[]
    for token,explanation in {
        'start':'document_dates -> learner_start_date only; missing remains blank with warning',
        'end':'document_dates -> learner_end_date only; missing remains blank with warning',
        'weeks':'_weeks_between -> Decimal((end-start).days)/7 rounded to 1dp, None for invalid range',
        'hours':'sum selected effective-plan curriculum.modules.total_otjh, established round(...,2)',
        'total_hours':'sum selected effective-plan curriculum.modules.total_otjh, established round(...,2); review meeting hours excluded by existing rule',
        '_employer_address':'Created_users.employer_address if present; else Employer pk=EnrolmentUser.employer_id, address_1/address_2/town_city/county/post_code',
        'programme_level':'curriculum.programmes.level matched through placement_ids(EnrolmentUser).programme_id, else unique assigned name',
        'address_lines':'EnrolmentUser.address_line_1 or address or WizardPersonalDetails.address; address_line_2/3/4',
        'first':'WizardPersonalDetails.first_name (learner_id=pk); else split EnrolmentUser.username',
        'last':'WizardPersonalDetails.last_name (learner_id=pk); else split EnrolmentUser.username',
        'employer_block':'ExtendedIlr.answers.employer for learner_id=pk',
        'employer_answers':'ExtendedIlr.answers.employer for learner_id=pk',
        'employer_postcode':'Employer.post_code via EnrolmentUser.employer_id, matching assigned employer address; ExtendedIlr.answers.employer.postcode only without assigned address',
        '_years_at_address':'WizardIlrLearnerDetails.at_address_since_birth/years_at_address',
    }.items():
        if re.search(r'\b'+token+r'\b',value):supplements.append(explanation)
    return value+('; '+ '; '.join(supplements) if supplements else '')

def model_for(key):return {'apprenticeship-agreement':'ApprenticeshipAgreement','training-plan':'TrainingPlanDocument','ilr':'IlrDocument','written-agreement':'WrittenAgreement'}.get(key,'EnrolmentReview')

def getpath(value,path):
    for key in path.split('.'):
        if not isinstance(value,dict):return None
        value=value.get(key)
    return value

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    samples=json.loads((OUT/'payloads.json').read_text(encoding='utf8'))
    records=[]
    corrected={'document.particulars.durationWeeks','document.answers.eligibility.residentPrevious3Years','document.learnerDetails.uln','document.learnerDetails.address1','document.answers.nextOfKin.address','document.answers.nextOfKin.postcode',
               'document.programme.level','document.employment.jobTitle','document.employment.startDateWithEmployer','document.particulars.jobTitle','document.particulars.apprenticeshipLevel','document.particulars.employerPostcode'}
    for doc,template in SEED['templates'].items():
        used=set(TOKEN.findall(json.dumps(template)))
        section=''
        sections={}
        for block in template['blocks']:
            if block['type']=='heading':section=block.get('text','')
            for ref in TOKEN.findall(json.dumps(block)):sections.setdefault(ref,section)
        def add(key,label,source,kind='placeholder',format='text',section='',values=None,in_default=True):
            actual=values or {tag:samples[tag+'-'+doc]['template']['fields'].get(key) for tag in ('A','B')}
            missing=[tag for tag,value in actual.items() if value is None or value=='' or value==[] or value=={}]
            no_source=source in ("''",'None') or source.startswith('No value supplied') or 'no canonical EPA' in source
            unresolved=source.startswith('UNRESOLVED')
            fixed=key in corrected or key.startswith(('enrolment.','learningPlan[]','delivery.activities[]','plrRecords[]')) or doc=='review-rpl' and key in ('uln','skillsRadar.items[].score') or key in ('document.programme.startDate','document.programme.endDate','document.programme.practicalStartDate','document.programme.practicalEndDate','document.particulars.startDate','document.particulars.endDate','document.particulars.practicalStartDate','document.particulars.practicalEndDate','document.particulars.plannedEndDate','document.learnerInformation.programmeStartDate','document.learnerInformation.plannedEndDate')
            status='UNRESOLVED_SOURCE' if unresolved else 'NO_CANONICAL_SOURCE' if no_source else 'CORRECTED' if fixed else 'MISSING_FIXTURE_DATA' if len(missing)==2 else 'VERIFIED'
            models=sorted(set(re.findall('|'.join(TABLES),source)))
            for model in ('ModuleAuthoringModule','ModuleAuthoringWeek','ModuleAuthoringComponent'):
                if TABLES[model] in source and model not in models:models.append(model)
            lookup='EnrolmentUser.pk -> learner_id; issued source restricted by learner_id + source_id'
            if 'ExtendedIlr' in models:lookup+='; ExtendedIlr.learner_id=EnrolmentUser.pk (Created_users ids unique across learner kinds)'
            if 'Employer' in models:lookup+='; Employer.pk=EnrolmentUser.employer_id, never a global employer'
            if 'EnrolmentReview' in models:lookup+='; review learner_id + review_type/event_key; frozen IssuedTemplate.review_snapshot after first signing'
            if 'RplReviewDetail' in models:lookup+='; RplReviewDetail.learner_id=pk AND review.learner_id=pk; newest updated_at/id; exclude cancelled review'
            if any(m.startswith('ModuleAuthoring') for m in models):lookup+='; effective saved/group module IDs -> modules.module_catalogue_id -> weeks.module_catalogue_id -> components.week_id; existing visibility predicates retained'
            if 'WizardPersonalDetails' in models or 'WizardIlrLearnerDetails' in models or 'WizardPlr' in models or 'WizardPlrRecord' in models or 'WizardKsbAssessment' in models:lookup+='; corresponding wizard rows filtered by learner_id=pk'
            row={'document':template['title'],'document_key':doc,'section':section or sections.get(key,'Optional builder placeholder'), 'label':label,'binding_key':key,'binding_kind':kind,
                 'actual_source_model':', '.join(models) or model_for(doc),'actual_source_table':', '.join(TABLES[m] for m in models) or TABLES[model_for(doc)],
                 'source_field_or_calculation':source,'learner_lookup':lookup,
                 'transformation':format,'missing_behavior':'blank/null, not No; explicit False and zero preserved; no fabricated source' if no_source else 'displayValue/substitute; empty stays blank; documented registry fallback only',
                 'pdf_location':f"{model_for(doc)} frozen payload -> existing PDF entry point -> renderManagedDocument ({kind}); bindingVersion=2 for new snapshots",
                 'verification':status,'in_default_template':in_default,'missing_in_isolated_fixtures':','.join(missing),'fixture_A_value':actual.get('A'),'fixture_B_value':actual.get('B'),
                 'evidence':f'A-{doc}.pdf; B-{doc}.pdf; A-{doc}-all-bindings.pdf; B-{doc}-all-bindings.pdf; test_binding_audit.py'}
            if kind=='signature' and key.endswith('.image'):
                for tag in ('A','B'):
                    mark=actual[tag]
                    row['fixture_'+tag+'_value']='PNG data URL; sha256='+hashlib.sha256(base64.b64decode(mark.split(',',1)[1])).hexdigest() if mark else ''
            records.append(row)
        for key,definition in SEED['fields'][doc].items():add(key,definition['label'],source_for(doc,key,definition),format=definition['format'],in_default=key in used)
        for block in template['blocks']:
            if block['type']=='signature':
                role=block['role'];payload={tag:samples[tag+'-'+doc]['data'] for tag in ('A','B')}
                for suffix,column in [('image','signature'),('signed','signature')]:
                    add(f'signatures.{role}.{suffix}',block['label']+' '+suffix,f'{model_for(doc)}.{role}_{column}'+(' -> bool(nonempty)' if suffix=='signed' else ''),'signature',section='Signatures',values={tag:(p.get('marks',{}).get(role) or p['signatures'].get(role,{}).get('signature')) if suffix=='image' else p['signatures'].get(role,{}).get('signed') for tag,p in payload.items()})
                if block.get('condition'):add(f'signatures.{role}.required','Employer signature required','EnrolmentReview.employer_signature_required; fallback employer_signature_required(review.review_type)','condition',section='Signatures',values={tag:p['signatures'].get(role,{}).get('required') for tag,p in payload.items()})
                if doc in ('training-plan','written-agreement'):add(f'signatures.{role}.position',block['label']+' position',f'{model_for(doc)}.{role}_position','signature',section='Signatures',values={tag:p['signatures'].get(role,{}).get('position') for tag,p in payload.items()})
            if block.get('source','').startswith('review:'):
                part=block['source'][7:]
                for name,label in block.get('labels',{}).items():add(f'answers.{part}.{name}',label,f'EnrolmentReview.form_answers.{part}.{name}; pinned first-signature IssuedTemplate.review_snapshot thereafter','review answer',section=part,values={tag:getpath(samples[tag+'-'+doc]['data'],f'answers.{part}.{name}') for tag in ('A','B')})
            if block.get('source')=='costs':
                for name,label in block['labels'].items():add('costs.'+name,label,'None','cost',section='Costs',values={'A':None,'B':None})
        native=[]
        if doc=='training-plan':
            native=[('learningPlan[].activity','Activity/Unit','curriculum.modules.title through effective assigned module IDs'),('learningPlan[].deliveryLead','Delivery Lead','EnrolmentUser.learning_provider or institutional Kent Business College default'),('learningPlan[].plannedDate','Planned Date','curriculum.modules.start_date; fallback document_dates learner start'),('learningPlan[].plannedOtjHours','OTJ (hours)','curriculum.modules.total_otjh; float unchanged'),('learningPlan[].weeks[].weekNumber','Week','curriculum.weeks.week_number'),('learningPlan[].weeks[].weekTitle','Week title','curriculum.weeks.title'),('learningPlan[].weeks[].components[].title','Activity title','curriculum.components.title'),('learningPlan[].weeks[].components[].method','Method','curriculum.components.type -> COMPONENT_METHODS'),('learningPlan[].weeks[].components[].otjHours','Activity OTJ','curriculum.components.expected_otjh -> float; established missing -> 0.0')]
        if doc=='written-agreement':native=[('delivery.activities[].title','Delivery activity','curriculum.components.title for assigned module/week'),('delivery.activities[].method','Method','curriculum.components.type -> COMPONENT_METHODS'),('delivery.activities[].hours','Hours','curriculum.components.expected_otjh -> float -> formatHoursMinutes'),('delivery.totalOtjHours','Total planned off-the-job training','sum effective assigned curriculum.modules.total_otjh, round(...,2); modules only')]
        if doc.startswith('review-'):
            parts=[b['source'][7:] for b in template['blocks'] if b.get('source','').startswith('review:')]
            if 'comments' in parts:native.append(('answers.comments.text','Comments','EnrolmentReview.form_answers.comments.text'))
            if 'priorLearning' in parts:native.extend((f'answers.priorLearning.items[].{name}',label,'EnrolmentReview.form_answers.priorLearning.items[].'+name) for name,label in [('description','Description'),('impact','Impact'),('durationReduced','Duration reduced'),('costReduced','Cost reduced'),('offTheJobTimeReduced','OTJ time reduced')])
            if 'functionalSkills' in parts:
                for group in ('initialAssessments','diagnosticAssessments'):
                    native.extend((f'answers.functionalSkills.{group}[].{name}',group+' '+name,'EnrolmentReview.form_answers.functionalSkills.'+group+'[].'+name) for name in ('subject','level','date','outcome'))
                native.extend((f'answers.functionalSkills.{name}',name,'EnrolmentReview.form_answers.functionalSkills.'+name) for name in ('exemptions.*','results.*.score','results.*.assessmentDate'))
            if 'plr' in parts:
                native.extend([('uln','Unique Learner Number','WizardPlr.uln; fallback EnrolmentReview.form_answers.plr.uln'),('answers.plr.reportedAttainment','Highest level reported','EnrolmentReview.form_answers.plr.reportedAttainment'),('answers.plr.subjectLevels.*','Calculated subject level','EnrolmentReview.form_answers.plr.subjectLevels.*')])
                native.extend((f'plrRecords[].{name}',name,'WizardPlrRecord.'+field) for name,field in {'placeOfStudy':'place_of_study','qualificationType':'qualification_type','subject':'subject','level':'level','awardDate':'award_date','credits':'credits','grade':'grade','recordType':'record_type'}.items())
            if 'skillsRadar' in parts:
                native.extend([('skillsRadar.standardLabel','Standard label','WizardSkillsRadar.standard_id -> curriculum.ksb_profiles.name; programme fallback'),('skillsRadar.items[].title','KSB title','WizardSkillsRadar.standard_id -> ksb_profile_for_source / ksb_profile_for_programme; curriculum.ksb_profiles.ksb_items or standard-backed KSB source'),('skillsRadar.items[].theme','KSB theme','WizardSkillsRadar.standard_id -> same authored curriculum KSB profile theme'),('skillsRadar.items[].codes','KSB codes','WizardSkillsRadar.standard_id -> same authored curriculum KSB profile codes'),('skillsRadar.items[].level','Learner rating','WizardKsbAssessment.level'),('skillsRadar.items[].score','Learner score','WizardKsbAssessment.score; new documents print raw score without obsolete /8 assumption'),('skillsRadar.items[].note','Learner note','WizardKsbAssessment.note'),('skillsRadar.answered','Answered count','count WizardKsbAssessment.level nonempty over authored profile plus orphan answers'),('skillsRadar.total','KSB count','length of resolved authored profile plus retained orphan WizardKsbAssessment rows'),('answers.skillsRadar.notes','Reviewer notes','EnrolmentReview.form_answers.skillsRadar.notes')])
        for key,label,source in native:
            def leaf(data,path):
                if '*' in path:
                    first,rest=path.split('*',1);entries=getpath(data,first.rstrip('.')) or {}
                    return [leaf(entry,rest.lstrip('.')) if rest else entry for entry in entries.values()]
                if '[]' in path:
                    first,rest=path.split('[]',1);entries=getpath(data,first) or []
                    return [leaf(entry,rest.lstrip('.')) if rest else entry for entry in entries]
                return getpath(data,path) if path else data
            add(key,label,source,'native',format='formatHoursMinutes: existing rounded-minute display, no source calculation changed' if doc=='written-agreement' and key.endswith(('hours','totalOtjHours')) else 'String(value); dates unchanged in Learning Plan; zero remains zero',section=key.split('.')[0],values={tag:leaf(samples[tag+'-'+doc]['data'],key) for tag in ('A','B')})
    # Configuration inventory was read under SET TRANSACTION READ ONLY.
    config=json.loads((OUT/'configured-templates.json').read_text(encoding='utf8')) if (OUT/'configured-templates.json').exists() else None
    report={'scope':'Repository registry/native collections and current configured template inventory; isolated fixtures, not a production learner-population audit.',
            'configuredTemplates':{k:len(config[k]) for k in ('published','drafts')} if config else 'not read','summary':{},'fields':records,
            'unresolvedBusinessRules':[
                'OTJ module totals vs review-meeting-inclusive totals: preserve established module-only calculation; user reply clarified dates only.',
                'Assessment.modules_selection PCP pathway/module IDs have no declared catalogue link to curriculum.modules.module_catalogue_id. Operational assigned learning plan remains authoritative; no automatic title matching or pathway substitution.',
                'ILR learnerReferenceNumber has no established mapping: EnrolmentUser.reference_number is currently the apprenticeship reference in Training Plan, not proven equivalent to the ILR learner reference.',
                'Funding band, costs, manager role titles, working hours and EPA arrangements have no established producer in these document derivations. Do not invent contractual values.']}
    for key in SEED['templates']:
        rows=[r for r in records if r['document_key']==key];counts=collections.Counter(r['verification'] for r in rows)
        report['summary'][key]={'dynamicBindings':len(rows),'verified':counts['VERIFIED'],'corrected':counts['CORRECTED'],'unresolved':counts['UNRESOLVED_SOURCE']+counts['NO_CANONICAL_SOURCE'],'missingFixtureData':counts['MISSING_FIXTURE_DATA'],'registeredPlaceholders':len(SEED['fields'][key])}
    (OUT/'field-bindings.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf8')
    with (OUT/'field-bindings.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
    verify_pdfs(samples,records)
    print(json.dumps({'bindings':len(records),'summary':report['summary'],'unlocatedProducerExpressions':[r['binding_key'] for r in records if r['source_field_or_calculation'].startswith('UNRESOLVED')]}))

def verify_pdfs(samples,records):
    manifest=json.loads((OUT/'pdf-manifest.json').read_text(encoding='utf8'))
    fitz.TOOLS.set_small_glyph_heights(True)
    results=[]
    for item in manifest:
        name=item['name'];doc=fitz.open(OUT/(name+'.pdf'));probe=fitz.open(OUT/(name+'-all-bindings.pdf'))
        text='\n'.join(p.get_text() for p in doc);probe_text=''.join(p.get_text() for p in probe)
        compact=lambda s:re.sub(r'\s+','',s)
        missing=[row['key'] for row in item['values'] if row['expected'] and compact(row['expected']) not in compact(probe_text)]
        def scalars(value):
            if isinstance(value,list):return [entry for part in value for entry in scalars(part)]
            if isinstance(value,dict):return [entry for part in value.values() for entry in scalars(part)]
            return [] if value is None or value=='' else [str(int(value)) if isinstance(value,float) and value.is_integer() else str(value)]
        native_missing=[]
        for row in records:
            if row['document_key']!=item['key'] or row['binding_kind'] not in ('native','review answer'):continue
            for value in scalars(row['fixture_'+item['tag']+'_value']):
                if value in ('True','False'):value='Yes' if value=='True' else 'No'
                if item['key']=='written-agreement' and row['binding_key'].endswith(('hours','totalOtjHours')):
                    minutes=math.floor(float(value)*60+.5);h,m=divmod(minutes,60)
                    value=f'{h}h {m}m' if h and m else f'{h}h' if h else f'{m}m' if m else '0h'
                if compact(value) not in compact(text):native_missing.append({'binding':row['binding_key'],'expected':value})
        wrong=f'AUDIT_{"B" if item["tag"]=="A" else "A"}_' in text
        expected_marks=set()
        source=samples[name]['data']
        for role,state in source.get('signatures',{}).items():
            if not isinstance(state,dict) or not state.get('signed'):continue
            mark=source.get('marks',{}).get(role) or state.get('signature')
            if mark:
                pixels=fitz.Pixmap(base64.b64decode(mark.split(',',1)[1]))
                expected_marks.add(hashlib.md5(pixels.samples).hexdigest())
        actual_marks={info['digest'].hex() for page in doc for info in page.get_image_info(hashes=True)}
        bounds=[];overlaps=[];images=[]
        for n,p in enumerate(doc):
            spans=[]
            for block in p.get_text('dict')['blocks']:
                if block['type']==1:
                    r=fitz.Rect(block['bbox']);images.append(abs(r.width/r.height-block['width']/block['height'])<.02)
                elif block['type']==0:
                    spans.extend(span for line in block['lines'] for span in line['spans'])
            for i,a in enumerate(spans):
                r=fitz.Rect(a['bbox'])
                if r.x0<32 or r.x1>563 or r.y1>770:bounds.append({'page':n+1,'text':a['text']})
                for b in spans[i+1:]:
                    area=r&fitz.Rect(b['bbox'])
                    if not area.is_empty and area.width>1 and area.height>2 and a['text'].strip() and b['text'].strip():overlaps.append(n+1)
            p.get_pixmap(matrix=fitz.Matrix(1.2,1.2),alpha=False).save(OUT/f'{name}-page-{n+1}.png')
        results.append({'name':name,'pages':len(doc),'missingRegisteredValues':missing,'missingNativeValues':native_missing,'otherLearnerFound':wrong,'outOfReadingBounds':bounds,'overlaps':overlaps,'signatureAspectCorrect':all(images),'signatureImages':len(images),'signaturePixelsMatchSource':expected_marks==actual_marks,'unresolvedTokens':bool(TOKEN.search(text))})
    (OUT/'pdf-verification.json').write_text(json.dumps(results,indent=2),encoding='utf8')
    print(json.dumps({'pdfs':len(results),'missing':sum(len(r['missingRegisteredValues']) for r in results),'missingNative':sum(len(r['missingNativeValues']) for r in results),'crossLearner':sum(r['otherLearnerFound'] for r in results),'bounds':sum(len(r['outOfReadingBounds']) for r in results),'overlaps':sum(len(r['overlaps']) for r in results)}))
    assert all(not(r['missingRegisteredValues'] or r['missingNativeValues'] or r['otherLearnerFound'] or r['outOfReadingBounds'] or r['overlaps'] or r['unresolvedTokens']) and r['signaturePixelsMatchSource'] and r['signatureAspectCorrect'] for r in results), 'PDF binding/geometry/signature verification failed; see pdf-verification.json'

if __name__=='__main__':main()
