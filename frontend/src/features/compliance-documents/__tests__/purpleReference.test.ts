import { describe, expect, it } from 'vitest';
import jsPDF from 'jspdf';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { designDraft, isReferencePurple } from '../documentTheme';
import { learningPlanRows, learningPlanWidths, type LearningPlanModule } from '../learningPlanLayout';
import { renderManagedDocument } from '../render';
import { documentPages, pageHtml } from '../pageDocument';
import { previewPdf } from '../previewPdf';
import { keys, payloadFor } from './fixtures';
import type { PreviewResponse } from '../types';

const hours = [0.75, 2.5, 0.15, 0, 2, '0.7500'];
const methods = ['Live session', 'Pre-recorded video', 'Reading material', 'Podcast', 'Quiz', 'PowerPoint'];
const plan: LearningPlanModule[] = Array.from({ length: 3 }, (_, m) => ({
  activity: `MODULE-${m + 1} Marketing technology, project controls and workplace evidence development`,
  deliveryLead: 'SYNTHETIC Professional Learning Provider and Workplace Development Coaching Team',
  plannedDate: '2030-02-01', plannedOtjHours: '12.50',
  weeks: Array.from({ length: 2 }, (_, w) => ({ weekNumber: w + 1, weekTitle: 'Applying current practice and reflecting on workplace learning',
    components: methods.map((method, i) => ({
      title: `ACTIVITY-${m + 1}-${w + 1}-${i + 1} ${i === 5 && m === 2 && w === 1 ? 'FINAL-LEARNING-ROW ' : ''}Review evidence and discuss practical application of programme knowledge within the workplace.`,
      method, otjHours: hours[i],
    })),
  })),
}));

describe('supplied purple reference PDF design', () => {
  it.each(keys)('%s applies the vector theme through the actual preview/download renderer without mutating data', async key => {
    const value=payloadFor(key,true); value.template.content=designDraft(value.template.content); value.data.template=value.template;
    const before=JSON.stringify(value), pdf=renderManagedDocument(value.template,value.data)!;
    expect(pdf.output().includes('0.91 0.84 0.99 rg')).toBe(true);
    expect(pdf.output().includes('0.184 0.047 0.31 rg')).toBe(true);
    expect(pdf.output().includes(' c\nh\nf')).toBe(true); // closed, curved vector decoration
    expect((await previewPdf(value)).size).toBeGreaterThan(3000);
    expect(JSON.stringify(value)).toBe(before);
  });
  it('upgrades only an editable copy and retains explicit Original layout', () => {
    const old=payloadFor('ilr').template.content; old.designTheme='lavender-v1';
    const before=structuredClone(old), next=designDraft(old);
    expect(old).toEqual(before); expect(isReferencePurple(next)).toBe(true);
    expect(designDraft({...old,designTheme:'classic'}).designTheme).toBe('classic');
  });
  it('reserves complete date and precise OTJ values while giving Activity the largest column', () => {
    const doc=new jsPDF(); doc.setFontSize(9.5);
    const rows=learningPlanRows(plan), layout=learningPlanWidths(186,rows,value=>doc.getTextWidth(value));
    expect(layout.stacked).toBe(false); expect(layout.widths.reduce((a,b)=>a+b)).toBe(186);
    expect(layout.widths[0]).toBeGreaterThan(Math.max(...layout.widths.slice(1)));
    expect(layout.widths[4]).toBeGreaterThanOrEqual(22); expect(layout.widths[3]).toBeGreaterThanOrEqual(26);
    expect(rows.filter(r=>r.kind==='activity').slice(0,6).map(r=>r.values[4])).toEqual(['0.75','2.5','0.15','0','2','0.7500']);
    expect(rows.filter(r=>r.kind==='module').map(r=>r.values[4])).toEqual(['12.50','12.50','12.50']);
    expect(learningPlanWidths(110,rows,value=>doc.getTextWidth(value)).stacked).toBe(true);
  });
  it('keeps all modules, weeks, methods, final rows and exact numbers across repeated table pages', () => {
    const value=payloadFor('training-plan'); value.template.content.designTheme='purple-reference-v2'; value.data.learningPlan=plan;
    const before=JSON.stringify(value), pdf=renderManagedDocument(value.template,value.data)!, output=pdf.output();
    expect(pdf.getNumberOfPages()).toBeGreaterThan(3);
    for(const marker of ['MODULE-1','MODULE-2','MODULE-3','ACTIVITY-1-1-1','FINAL-LEARNING-ROW','0.7500','12.50','0.75','2.5','0.15','2030-02-01',...methods])expect(output).toContain(marker);
    const head=output.split('(OTJ ').slice(1);
    expect(head.length).toBeGreaterThan(1);
    expect(JSON.stringify(value)).toBe(before);
    const dom=document.createElement('div'); dom.innerHTML=documentPages(value.template.content).map(page=>pageHtml(page,value.template.content,value.data)).join('');
    expect(dom.querySelectorAll('[data-learning-plan] col')).toHaveLength(5);
    expect(dom.querySelectorAll('[data-plan-kind="week"]')).toHaveLength(6);
    expect(dom.querySelectorAll('.plan-number')).toHaveLength(39);
    expect(dom.textContent).toContain('FINAL-LEARNING-ROW'); expect(dom.textContent).toContain('0.7500');
  });
  it('renders extremely long individual rows and explicit narrow margins without dropping content', () => {
    const value=payloadFor('training-plan');value.template.content.designTheme='purple-reference-v2';
    value.data.learningPlan=[{...plan[0],weeks:[{weekNumber:1,components:[{title:'Long activity evidence. '.repeat(400)+'LONG-ROW-END',method:'Reading material',otjHours:'0.7500'}]}]}];
    const output=renderManagedDocument(value.template,value.data)!.output();
    expect(output).toContain('LONG-ROW-END'); expect(output).toContain('(0.7500)');
    value.template.content.pageLayout={size:'a4',margins:{top:20,left:50,right:50,bottom:30},header:'',footer:'',pageNumbers:true};
    const narrow=renderManagedDocument(value.template,value.data)!.output();
    expect(narrow).toContain('LONG-ROW-END'); expect(narrow).toContain('0.7500');
  });
  it('retains historical signature provenance, custom headers/footers and explicit formatting', () => {
    const value=payloadFor('ilr',true); value.template.content.designTheme='purple-reference-v2';
    value.template.content.pageLayout={size:'a4',margins:{top:10,left:18,right:18,bottom:10},header:'Custom header '.repeat(18)+'HEADER-END',footer:'Custom footer '.repeat(18)+'FOOTER-END',pageNumbers:true};
    value.template.content.blocks[0].style={fontSize:16,bold:false,align:'right'};
    value.data.signatures.learner={signed:true,name:'Historical Signer',signedAt:'2030-01-02T10:00:00Z'};
    value.data.signatureProvenance={learner:{sourceRevision:1,name:'Historical Signer',signedAt:'2030-01-02T10:00:00Z'}};
    const before=JSON.stringify(value), pdf=renderManagedDocument(value.template,value.data)!, output=pdf.output();
    for(const text of ['Historical signature reproduced from Revision R1','does not record approval','Historical Signer','HEADER-END','FOOTER-END'])expect(output).toContain(text);
    expect(JSON.stringify(value)).toBe(before);
  });
});

if(process.env.PURPLE_REFERENCE_ARTIFACTS)it('writes all eight real short/long PDF previews and a measured multi-module Training Plan',async()=>{
  const target=resolve(process.env.PURPLE_REFERENCE_ARTIFACTS!);mkdirSync(target,{recursive:true});
  const samples=JSON.parse(readFileSync(resolve(target,'../document-design-samples.json'),'utf8')) as Record<string,PreviewResponse>;
  for(const [name,value] of Object.entries(samples)){
    // Preserve v1 input before generating the new independently versioned design.
    const old=await previewPdf(value);mkdirSync(resolve(target,'v1-after'),{recursive:true});
    writeFileSync(resolve(target,'v1-after',name+'.pdf'),new Uint8Array(await old.arrayBuffer()));
    value.template.content.designTheme='purple-reference-v2';value.data.template=value.template;
    const before=JSON.stringify(value), blob=await previewPdf(value);
    writeFileSync(resolve(target,name+'.pdf'),new Uint8Array(await blob.arrayBuffer()));expect(JSON.stringify(value)).toBe(before);
    if(name==='training-plan-short'){
      value.data.learningPlan=plan;
      const realistic=await previewPdf(value);writeFileSync(resolve(target,'training-plan-realistic.pdf'),new Uint8Array(await realistic.arrayBuffer()));
      writeFileSync(resolve(target,'realistic-plan.json'),JSON.stringify(plan));
    }
    if(name==='ilr-short')writeFileSync(resolve(target,'browser-sample.json'),JSON.stringify(value));
  }
},60000);
