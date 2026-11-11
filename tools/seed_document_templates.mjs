// Reproducible migration of repository wording. No eval and no database writes.
import ts from '../frontend/node_modules/typescript/lib/typescript.js';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
async function source(file){ const text=await fs.readFile(path.join(root,file),'utf8'); const ast=ts.createSourceFile(file,text,ts.ScriptTarget.Latest,true);return {text,ast}; }
const aa=await source('frontend/src/lib/apprenticeshipAgreementPdf.ts');
const tp=await source('frontend/src/lib/trainingPlanPdf.ts');
const wa=await source('frontend/src/lib/writtenAgreementPdf.ts');
const ilr=await source('frontend/src/lib/ilrDocumentPdf.ts');
const review=await source('frontend/src/pages/learner/onboarding/reviews/reviewDocument.ts');
const questions=await source('frontend/src/pages/learner/onboarding/reviews/questions.ts');
const waBackend=await fs.readFile(path.join(root,'backend/learner_api/written_agreement.py'),'utf8');
const costItems=[...waBackend.match(/COST_ITEMS = \(([\s\S]*?)\n\)/)[1].matchAll(/^\s*"([^"]*)",?$/gm)].map(m=>m[1]);
function backendNote(name){const source=waBackend.match(new RegExp('"'+name+'": \\(\\s*([\\s\\S]*?)\\s*\\),'))?.[1];if(!source)throw new Error('Missing existing '+name);return [...source.matchAll(/"([^"]*)"/g)].map(m=>m[1]).join('');}
function nodes(src,predicate){const out=[];function walk(n){if(predicate(n))out.push(n);ts.forEachChild(n,walk);}walk(src.ast);return out;}
function literal(n){
 if(ts.isAsExpression(n)||ts.isParenthesizedExpression(n))return literal(n.expression);
 if(ts.isStringLiteral(n)||ts.isNoSubstitutionTemplateLiteral(n))return n.text;
 if(ts.isNumericLiteral(n))return Number(n.text);
 if(ts.isArrayLiteralExpression(n))return n.elements.map(literal);
 if(ts.isObjectLiteralExpression(n))return Object.fromEntries(n.properties.map(p=>[p.name.text,literal(p.initializer)]));
 if(n.kind===ts.SyntaxKind.TrueKeyword)return true;if(n.kind===ts.SyntaxKind.FalseKeyword)return false;
 throw new Error('Not a static literal: '+n.getText());
}
function variable(src,name){const n=nodes(src,n=>ts.isVariableDeclaration(n)&&n.name.getText()===name)[0];if(!n)throw new Error('Missing variable '+name);return n.initializer;}
function text(src,exact){const match=nodes(src,n=>(ts.isStringLiteral(n)||ts.isNoSubstitutionTemplateLiteral(n))&&n.text===exact)[0];if(!match)throw new Error('Missing original wording '+exact);return match.text;}
function callName(n){return ts.isCallExpression(n)?n.expression.getText():'';}
function callStrings(src,name){return nodes(src,n=>callName(n)===name&&n.arguments[0]&&(ts.isStringLiteral(n.arguments[0])||ts.isNoSubstitutionTemplateLiteral(n.arguments[0]))).map(n=>n.arguments[0].text);}
const P=(id,value)=>({id,type:'paragraph',text:value});
const H=(id,value,level=2)=>({id,type:'heading',text:value,level});
const B=(id)=>({id,type:'page-break'});
const signature=(role,label,condition)=>({id:'signature-'+role,type:'signature',role,label,...(condition?{condition}:{})});
const templates={}, fields={};
function add(key,title,category,roles){fields[key]={};return templates[key]={schemaVersion:1,templateId:key,documentKey:key,title,category,programmes:[],blocks:[],signatureRoles:roles};}
function field(key,ref,label,format='text',fallback,source){fields[key][ref]={key:ref,label,group:ref.startsWith('learner.')?'Learner':ref.startsWith('programme.')?'Programme':ref.startsWith('enrolment.')?'Enrolment':'Document',format,...(fallback!==undefined?{fallback}:{}),...(source?{source}:{})};return '{{'+ref+'}}';}
function expression(key,n,aliases,label,format='text'){
 if(ts.isConditionalExpression(n))return expression(key,n.whenTrue,aliases,label,format);
 if(ts.isParenthesizedExpression(n)||ts.isAsExpression(n))return expression(key,n.expression,aliases,label,format);
 if(ts.isBinaryExpression(n))return expression(key,n.left,aliases,label,format);
 if(ts.isCallExpression(n))return expression(key,n.arguments[0],aliases,label,n.expression.getText()==='fmtDate'?'date':format);
 if(ts.isPropertyAccessExpression(n)){
  let ref=n.getText();const first=ref.split('.')[0];if(!(first in aliases))throw new Error('Unknown source '+ref);
  ref='document.'+aliases[first]+ref.slice(first.length);return field(key,ref,label,format);
 }
 if(ts.isStringLiteral(n))return n.text;
 throw new Error('Unhandled field expression '+n.getText());
}
function table(key,id,rows){return {id,type:'table',columns:['Field','Value'],rows};}
function scalarRows(src,key,aliases){return nodes(src,n=>['row','yesNo'].includes(callName(n))&&ts.isStringLiteral(n.arguments[0])).map(n=>{
 const label=n.arguments[0].text;let value;
 if(n.arguments[1].getText().includes('durationWeeks'))value=field(key,'document.programme.durationWeeks',label,'weeks');
 else if(n.arguments[1].getText().includes('epaPeriodFrom'))value=field(key,'document.epa.period',label);
 else value=expression(key,n.arguments[1],aliases,label,callName(n)==='yesNo'?'yesno':'text');
 return {pos:n.pos,label,value};
 });}

// Apprenticeship Agreement: retain all seven original statutory notes.
{
 const key='apprenticeship-agreement', t=add(key,'Apprenticeship Agreement','Compliance',['apprentice','employer']);
 const notes=literal(variable(aa,'NOTES'));
 t.blocks.push(H('aa-title',text(aa,'APPRENTICESHIP AGREEMENT'),1),P('aa-intro',text(aa,'An apprenticeship agreement must be in place from the start of the apprenticeship. The agreement must be extended if the programme is extended.')),
  P('aa-purpose',text(aa,'The purpose of the apprenticeship agreement is to identify:')));
 for(const [i,value] of ['the apprenticeship standard connected to the apprenticeship;','the dates during which the apprenticeship is expected to take place; and','the amount of off the job training that the apprentice is to receive.'].entries())t.blocks.push(P('aa-purpose-'+i,'• '+text(aa,value)));
 t.blocks.push(H('aa-particulars',text(aa,'Apprenticeship Particulars:')),table(key,'aa-fields',[
  [text(aa,'Apprentice name:'),field(key,'document.particulars.apprenticeName','Apprentice name')],
  [text(aa,'Place of work (employer name and location):'),field(key,'document.particulars.employerName','Employer name')+'\n'+field(key,'document.particulars.employerAddress','Employer address')],
  [text(aa,'Apprenticeship standard, level and version:'),field(key,'document.particulars.standard','Apprenticeship standard, level and version')],
  ...[['Start date of apprenticeship (see note 2):','startDate','date'],['End date of apprenticeship (see note 2):','endDate','date'],['Start date of practical period (see note 2):','practicalStartDate','date'],['Estimated end date of practical period (see note 2):','practicalEndDate','date'],['Duration of practical period (see note 2):','durationWeeks','number'],['Planned amount of off-the-job training (hours) (see note 5):','plannedOtjHours','hours']].map(([label,ref,format])=>[text(aa,label),field(key,'document.particulars.'+ref,label,format)]),
 ]),H('aa-signatories',text(aa,'Signatories:')),signature('apprentice',text(aa,'Apprentice:')),signature('employer',text(aa,'Employer:')),B('aa-notes-page'),H('aa-notes-title',text(aa,'APPRENTICESHIP AGREEMENT - NOTES'),1));
 notes.forEach((n,i)=>t.blocks.push(H('aa-note-heading-'+i,n.heading),P('aa-note-body-'+i,n.body)));
}

// Training Plan: original commitments, live curriculum table and all appendices.
{
 const key='training-plan',t=add(key,'Training Plan','Compliance',['apprentice','employer','provider']);
 const paragraphs=callStrings(tp,'paragraph');
 const rows=scalarRows(tp,key,{programme:'programme',employment:'employment',otjh:'otjh',epa:'epa',apprenticeContact:'contacts.apprentice',employerContact:'contacts.employer'});
 const group=(id,start,end)=>table(key,id,rows.filter(r=>r.pos>=start&&r.pos<end).map(r=>[r.label,r.value]));
 const positions=['// ---- Programme ----','// ---- Employment ----','// ---- Commitments ----','// ---- Off-the-job hours ----','// ---- EPA ----','// ---- Signatures ----','// ---- Appendices ----'].map(s=>tp.text.indexOf(s));
 t.blocks.push(H('tp-title',text(tp,'Training Plan'),1),P('tp-intro',paragraphs[0]),P('tp-apprentice','Apprentice: '+field(key,'document.programme.apprenticeName','Apprentice name')),
 H('tp-programme',text(tp,'Apprenticeship Programme Details')),group('tp-programme-fields',positions[0],positions[1]),H('tp-employment',text(tp,'Employment Details')),group('tp-employment-fields',positions[1],positions[2]),B('tp-commitments-page'),H('tp-commitments',text(tp,'Commitment to the Programme')));
 literal(variable(tp,'COMMITMENTS')).forEach((c,i)=>{t.blocks.push(H('tp-commitment-'+i,c.heading),P('tp-commitment-intro-'+i,c.intro));c.items.forEach((v,j)=>t.blocks.push(P(`tp-commitment-${i}-${j}`,`${j+1}. ${v}`)));});
 t.blocks.push(B('tp-plan-page'),H('tp-plan',text(tp,'Learning Plan')),{id:'tp-learning-plan',type:'native',source:'learning-plan',label:'Learning plan activities',columns:['Activity/Unit','Method','Delivery Lead','Planned Date','OTJ (hr)']},
 H('tp-otj',text(tp,'Off-The-Job Training Hours')),P('tp-otj-note',paragraphs.find(p=>p.startsWith('Off-the-job training is'))),group('tp-otj-fields',positions[3],positions[4]),
 H('tp-epa',text(tp,'End Point Assessment')),P('tp-epa-note',paragraphs.find(p=>p.startsWith('End Point Assessment (EPA)'))),group('tp-epa-fields',positions[4],positions[5]),
 B('tp-signatures-page'),H('tp-signatures',text(tp,'Signatures & Declarations')),P('tp-declaration',paragraphs.find(p=>p.startsWith('By signing this agreement'))),signature('apprentice',text(tp,'Apprentice')),signature('employer',text(tp,'Employer')),signature('provider',text(tp,'Training Provider')),
 H('tp-appendices',text(tp,'Appendices')),H('tp-apprentice-contact',text(tp,'1.1 Apprentice Contact Details')));
 const appendix=rows.filter(r=>r.pos>=positions[6]);t.blocks.push(table(key,'tp-apprentice-contacts',appendix.slice(0,4).map(r=>[r.label,r.value])),H('tp-employer-contact',text(tp,'1.2 Employer Contact Details')),table(key,'tp-employer-contacts',appendix.slice(4).map(r=>[r.label,r.value])));
}

// Written Agreement: preserve all terms; dynamic commercial rows stay connected
// to the same issued snapshots (no price/funding or contractual rules invented).
{
 const key='written-agreement',t=add(key,'Written Agreement','Compliance',['learner','employer','provider']);
 const p=callStrings(wa,'paragraph');
 const intro=nodes(wa,n=>callName(n)==='paragraph'&&ts.isTemplateExpression(n.arguments[0]))[0].arguments[0];
 let introText=intro.head.text;intro.templateSpans.forEach(span=>{introText+=field(key,'document.particulars.mainProviderIntro','Main Provider (introduction)','text','the Main Provider','document.particulars.mainProvider')+span.literal.text;});
 const rows=scalarRows(wa,key,{particulars:'particulars',epa:'epa',c:'contacts.current'});
 const particulars=wa.text.indexOf('// ---- Particulars ----'), delivery=wa.text.indexOf('// ---- Delivery ----'),epa=wa.text.indexOf('// ---- EPA ----'),costs=wa.text.indexOf('// ---- Costs ----');
 t.blocks.push(H('wa-title',text(wa,'Written Agreement'),1),P('wa-intro',introText),table(key,'wa-particulars',rows.filter(r=>r.pos>=particulars&&r.pos<delivery).map(r=>[r.label,r.value])),
 H('wa-delivery',text(wa,'Delivery')),P('wa-off-the-job-note',backendNote('offTheJobNote')),{id:'wa-delivery-data',type:'native',source:'delivery',label:'Delivery activities and off-the-job training',columns:['Activity','Method','Hours']},
 H('wa-english-maths',text(wa,'English and maths training delivered off the job')),P('wa-english-maths-note',backendNote('englishMathsNote')),
 B('wa-epa-page'),H('wa-epa',text(wa,'End Point Assessment Organisation')),P('wa-epa-note',p.find(v=>v.startsWith('In accordance with data protection'))),table(key,'wa-epa-fields',rows.filter(r=>r.pos>=epa&&r.pos<costs).map(r=>[r.label,r.value])),
 H('wa-costs',text(wa,'Costs')),P('wa-costs-note',p.find(v=>v.startsWith('Any prior knowledge'))),{id:'wa-cost-items',type:'native',source:'costs',label:'Costs',columns:[text(wa,'Item'),text(wa,'Associated price')],labels:{...Object.fromEntries(costItems.map((label,i)=>['cost-'+i,label])),total:text(wa,'Total price of Apprenticeship'),maximum:text(wa,'Funding Band Maximum value'),balance:text(wa,'Balance due')}},
 H('wa-queries',text(wa,'Queries and complaints')),P('wa-complaints',p.find(v=>v.startsWith('Please refer to the provider'))),{id:'wa-contacts',type:'native',source:'contacts',label:'Queries and complaints contacts',columns:['Field','Value']},
 B('wa-signatures-page'),H('wa-signatures',text(wa,'Declaration & Signatures')));
 const blocks=nodes(wa,n=>callName(n)==='block'&&n.arguments.length===4);
 blocks.forEach((n,i)=>{const role=['learner','employer','provider'][i];const title=ts.isStringLiteral(n.arguments[0])?n.arguments[0].text:field(key,'document.particulars.mainProviderSignatureLabel','Main Provider (signature label)','text','Main Provider','document.particulars.mainProvider')+' (Main Provider):';t.blocks.push(H('wa-declaration-'+role,title),P('wa-declaration-text-'+role,literal(n.arguments[1])),signature(role,title));});
}

// ILR: extract every original row label and its existing data path, plus the
// complete declarations outside the row collection.
{
 const key='ilr',t=add(key,'Individual Learner Record','Compliance',['learner','provider']);
 const aliases={details:'learnerDetails',contact:'answers.contact',kin:'answers.nextOfKin',elig:'answers.eligibility',employer:'answers.employer',other:'answers.otherTraining',circ:'answers.circumstances',und:'answers.understanding',add:'answers.additional',media:'answers.media',decl:'answers.declarations'};
 const array=nodes(ilr,n=>ts.isVariableDeclaration(n)&&n.name.getText()==='rows')[0].initializer;
 array.elements.forEach((node,i)=>{
  const props=Object.fromEntries(node.properties.map(p=>[p.name.text,p.initializer]));const kind=literal(props.kind);
  if(['heading','section','note'].includes(kind))t.blocks.push(kind==='note'?P('ilr-row-'+i,literal(props.text)):H('ilr-row-'+i,literal(props.text),kind==='heading'?1:2));
  else {let value;const label=literal(props.label);
   if(ts.isConditionalExpression(props.value))value=expression(key,props.value.condition.left,aliases,label);
   else value=expression(key,props.value,aliases,label,kind==='yesno'?'yesno':'text');
   t.blocks.push({id:'ilr-row-'+i,type:'field',label,value});}
 });
 t.blocks.push(H('ilr-learning-declaration',text(ilr,'Learning declaration')));
 const declarations=nodes(ilr,n=>ts.isArrayLiteralExpression(n)&&n.elements.length===3&&ts.isStringLiteral(n.elements[0])&&n.elements[0].text.startsWith('I confirm my agreement'))[0];
 literal(declarations).forEach((p,i)=>t.blocks.push(P('ilr-learning-declaration-'+i,p)));
 t.blocks.push(signature('learner','Learner'),H('ilr-provider-declaration',text(ilr,'Provider/Sub-contractor Declaration')),P('ilr-provider-note',text(ilr,'I confirm I have seen evidence to verify the learners identity, immigration permission (if applicable) and relevant eligibility for this qualification/funding')),signature('provider','Provider'));
}

const questionGroups=Object.fromEntries(['ILR_QUESTIONS','EXTENDED_ILR_QUESTIONS','FS_JOB_ROLE_QUESTIONS','RPL_QUESTIONS','HEALTH_SAFETY_QUESTIONS'].map(name=>[name,literal(variable(questions,name))]));
const sectionTitles=literal(variable(review,'SECTION_TITLES'));
const reviews={
 'review-eligibility':{title:'Eligibility Review & FS Discussion',type:'eligibility-review',sections:['ilr','extendedIlr','functionalSkills','fsJobRoleDiscussion','comments']},
 'review-rpl':{title:'RPL And Experience',type:'workspace',sections:['priorLearning','rplExperience','plr','skillsRadar','comments']},
 'review-health-safety':{title:'Workplace Health & Safety Declaration',type:'training-plan',sections:['healthSafetyVetting']},
 'review-uln':{title:'ULN Privacy Notice & Learner Acknowledgement',type:'uln-privacy',sections:['ulnPrivacyNotice','learnerAcknowledgement']},
};
for(const [key,definition] of Object.entries(reviews)){
 const t=add(key,definition.title,'Review',['learner','admin','employer']);t.reviewType=definition.type;
 t.blocks.push(H('review-title',definition.title,1),H('review-learner-information',text(review,'Learner Information')),
 table(key,'review-information',Object.entries({Name:'name','Programme Name':'programmeName','Programme Start Date':'programmeStartDate','Planned End Date':'plannedEndDate','Programme Status':'programmeStatus',Employer:'employer',Manager:'manager',Mentor:'mentor'}).map(([label,ref])=>[text(review,label),field(key,'document.learnerInformation.'+ref,label)])));
 for(const section of definition.sections){
  t.blocks.push(H('review-section-'+section,sectionTitles[section]));
  if(section==='ulnPrivacyNotice'){
   t.blocks.push(P('uln-intro',literal(variable(questions,'ULN_PRIVACY_INTRO'))));
   literal(variable(questions,'ULN_PRIVACY_POINTS')).forEach((p,i)=>{t.blocks.push(P('uln-point-'+i,`${i+1}. ${p}`));if(i===1)literal(variable(questions,'ULN_PURPOSES')).forEach((v,j)=>t.blocks.push(P('uln-purpose-'+j,`   ${String.fromCharCode(97+j)}) ${v}`)));});
   t.blocks.push(P('uln-further-info',literal(variable(questions,'ULN_PRIVACY_FURTHER_INFO'))));
  }
  if(section==='learnerAcknowledgement')t.blocks.push(P('uln-acknowledgement',literal(variable(questions,'LEARNER_ACKNOWLEDGEMENT'))));
  const group={ilr:'ILR_QUESTIONS',extendedIlr:'EXTENDED_ILR_QUESTIONS',fsJobRoleDiscussion:'FS_JOB_ROLE_QUESTIONS',rplExperience:'RPL_QUESTIONS',healthSafetyVetting:'HEALTH_SAFETY_QUESTIONS'}[section];
  const labels=group?Object.fromEntries(questionGroups[group].map(q=>[q.key,q.label])):section==='ulnPrivacyNotice'?{noticeRead:'The learner has read the ULN privacy notice'}:section==='learnerAcknowledgement'?{acknowledged:'The learner confirms the acknowledgement above'}:{};
  t.blocks.push({id:'review-answers-'+section,type:'native',source:'review:'+section,label:sectionTitles[section],labels});
 }
 t.blocks.push(H('review-declaration',text(review,'Declaration')),P('review-declaration-note',text(review,'The parties below confirm the answers recorded in this review are accurate.')),signature('learner',text(review,'Learner signature')),signature('admin',text(review,'Provider signature')),signature('employer',text(review,'Employer signature'),'employer-required'));
}
for(const [key,t] of Object.entries(templates)){
 if(key==='written-agreement') {
  for(const ref of Object.keys(fields[key]))if(ref.startsWith('document.contacts.current.'))delete fields[key][ref];
  for(const party of ['provider','epao','dfe'])for(const property of ['organisation','name','website','email','phone'])field(key,`document.contacts.${party}.${property}`,`${party.toUpperCase()} ${property}`);
 }
 for(const [ref,label] of Object.entries({'learner.fullName':'Learner full name','learner.id':'System learner identifier','programme.name':'Confirmed programme / current programme','programme.cohort':'Cohort','enrolment.startDate':'Enrolment start date','enrolment.endDate':'Planned end date','employer.name':'Employer name','employer.manager':'Manager'}))field(key,ref,label,ref.endsWith('Date')?'date':'text');
 for(const role of t.signatureRoles){field(key,`signature.${role}.name`,`${role} signatory name`);field(key,`signature.${role}.date`,`${role} signature date`,'date');}
 for(const block of t.blocks)if((block.type==='paragraph'||block.type==='heading')&&typeof block.text!=='string')throw new Error('Missing migrated text '+block.id);
}
const result={templates,fields,provenance:{sources:[aa,tp,wa,ilr,review,questions].map(s=>({file:s.ast.fileName,sha256:createHash('sha256').update(s.text).digest('hex')}))}};
const output=JSON.stringify(result,null,2)+'\n';
for(const file of ['backend/enrolment_api/document_templates/seed.json','frontend/src/features/compliance-documents/seed.json']){await fs.mkdir(path.dirname(path.join(root,file)),{recursive:true});await fs.writeFile(path.join(root,file),output,'utf8');}
console.log(JSON.stringify(Object.fromEntries(Object.entries(templates).map(([key,t])=>[key,{blocks:t.blocks.length,fields:Object.keys(fields[key]).length}]))));
