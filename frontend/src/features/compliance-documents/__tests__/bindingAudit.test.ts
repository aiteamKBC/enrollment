import {describe,expect,it} from 'vitest';
import {mkdirSync,readFileSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {definitions,displayValue,substitute} from '../fields';
import {questionFields,questionValue,questionVisible} from '../questionFields';
import {buildRows,buildReviewPdf} from '@/pages/learner/onboarding/reviews/reviewDocument';
import {REVIEW_QUESTION_LABELS} from '@/pages/learner/onboarding/reviews/questions';
import {previewPdf} from '../previewPdf';
import {payloadFor,keys} from './fixtures';
import type {PreviewResponse} from '../types';

describe('versioned learner binding fixes',()=>{
 it('distinguishes missing, explicit No, unknown answers, zero and multiple options',()=>{
  const value=payloadFor('ilr',true);value.template.bindingVersion=2;
  const name='document.learnerDetails.longTermDisability';
  for(const [raw,shown] of [[null,''],[false,'Yes [ ]   No [X]'],[true,'Yes [X]   No [ ]'],['Not recorded','Not recorded']] as const){
   value.template.fields[name]=raw;expect(substitute('{{'+name+'}}',value.template)).toBe(shown);
  }
  expect(displayValue(0)).toBe('0');expect(displayValue('0.7500')).toBe('0.7500');
  const field=questionFields(value.template.content.blocks.find(b=>b.type==='field')!,'ilr')[0];
  field.binding={kind:'review',source:'review:test',key:'answer'};field.type='checkbox';field.options=[{id:'a',label:'A'},{id:'b',label:'B'}];
  expect(questionValue(field,value.template,{answers:{test:{answer:['a','b']}}})).toEqual(['a','b']);
  const dependent={...field,id:'dependent',condition:{field:field.id,values:['b']}};
  expect(questionVisible(dependent,[field],value.template,{answers:{test:{answer:['a','b']}}})).toBe(true);
 });
 it('prints saved PLR qualifications/ULN even without reviewer notes, only for new binding snapshots',()=>{
  const value=payloadFor('review-rpl',true);value.template.bindingVersion=2;value.data.template=value.template;
  value.data.uln='1234567890';value.data.plrRecords=[{placeOfStudy:'AUDIT_COLLEGE',subject:'AUDIT_QUALIFICATION',qualificationType:'Diploma',level:'3',awardDate:'2027-08-09',credits:0,grade:'Pass',recordType:'Manual'}];
  const rows=buildRows(value.data as any,REVIEW_QUESTION_LABELS);expect(JSON.stringify(rows)).toContain('AUDIT_QUALIFICATION');expect(JSON.stringify(rows)).toContain('1234567890');
  expect(rows).toContainEqual({kind:'text',label:'Credits',value:'0'});
  expect(buildReviewPdf(value.data as any,REVIEW_QUESTION_LABELS).output()).toContain('AUDIT_QUALIFICATION');
  delete value.template.bindingVersion;
  expect(JSON.stringify(buildRows(value.data as any,REVIEW_QUESTION_LABELS))).not.toContain('AUDIT_QUALIFICATION');
 });
 it('keeps booleans and multiselect answers readable without reinterpreting learner text',()=>{
  const value=payloadFor('review-health-safety',true);value.template.bindingVersion=2;value.data.template=value.template;
  value.data.answers.healthSafetyVetting={basicArrangements:false,dayOneInduction:['Option A','Option B'],fireSafety:'{{learner.fullName}}'};
  const rows=buildRows(value.data as any,REVIEW_QUESTION_LABELS);
  expect(rows).toContainEqual(expect.objectContaining({value:'No'}));expect(rows).toContainEqual(expect.objectContaining({value:'Option A, Option B'}));
  const output=buildReviewPdf(value.data as any,REVIEW_QUESTION_LABELS).output();expect(output).toContain('{{learner.fullName}}');
 });
 it.each(keys)('%s leaves old snapshots unchanged when new binding metadata is absent',async key=>{
  const old=payloadFor(key,true);const before=JSON.stringify(old);
  const first=await previewPdf(old),second=await previewPdf(old);
  expect(first.size).toBe(second.size);expect(JSON.stringify(old)).toBe(before);expect(old.template.bindingVersion).toBeUndefined();
 });
});

if(process.env.BINDING_AUDIT_ARTIFACTS)it('verifies backend-issued A/B data through every real PDF entry point and every registered placeholder',async()=>{
 const target=resolve(process.env.BINDING_AUDIT_ARTIFACTS!);mkdirSync(target,{recursive:true});
 const samples=JSON.parse(readFileSync(resolve(target,'payloads.json'),'utf8')) as Record<string,PreviewResponse>;
 const manifest=[];
 for(const [name,value] of Object.entries(samples)){
  const before=JSON.stringify(value),blob=await previewPdf(value),tag=name.slice(0,1);
  writeFileSync(resolve(target,name+'.pdf'),new Uint8Array(await blob.arrayBuffer()));
  const probe=structuredClone(value);
  probe.template.content.blocks.push({id:'audit-registry-probe',type:'table',columns:['Audited binding','Value'],rows:Object.keys(definitions(value.template.content.documentKey)).map(key=>[key,'{{'+key+'}}'])});
  probe.data.template=probe.template;probe.template.legacyEquivalent=false;
  const probeBlob=await previewPdf(probe);writeFileSync(resolve(target,name+'-all-bindings.pdf'),new Uint8Array(await probeBlob.arrayBuffer()));
  manifest.push({name,tag,key:value.template.content.documentKey,values:Object.entries(value.template.fields).map(([key,raw])=>({key,raw,expected:displayValue(raw,definitions(value.template.content.documentKey)[key],value.template.bindingVersion)}))});
  expect(JSON.stringify(value)).toBe(before);
 }
 writeFileSync(resolve(target,'pdf-manifest.json'),JSON.stringify(manifest,null,2));
 const previous=JSON.parse(readFileSync(resolve(target,'../document-design-samples.json'),'utf8')) as Record<string,PreviewResponse>;
 const normalise=(bytes:Buffer)=>bytes.toString('latin1').replace(/\/CreationDate\s*\([^)]*\)/g,'/CreationDate (FIXED)').replace(/\/ID\s*\[\s*<[^>]*>\s*<[^>]*>\s*\]/g,'/ID [FIXED]');
 const history=[];
 for(const [name,value] of Object.entries(previous)){
  const current=Buffer.from(await (await previewPdf(value)).arrayBuffer());
  const equal=normalise(current)===normalise(readFileSync(resolve(target,'../document-design',name+'.pdf')));
  history.push({name,unchanged:equal});expect(equal).toBe(true);
 }
 writeFileSync(resolve(target,'historical-render-verification.json'),JSON.stringify(history,null,2));
},60000);
