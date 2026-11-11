import { describe, expect, it } from 'vitest';
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { designDraft, isLavender } from '../documentTheme';
import { documentPages, pageHtml, readPage } from '../pageDocument';
import { renderManagedDocument } from '../render';
import { previewPdf } from '../previewPdf';
import { keys, payloadFor } from './fixtures';
import type { PreviewResponse } from '../types';

describe('versioned light-purple document design', () => {
  it.each(keys)('%s uses the shared design without changing its data, blocks or bindings', async key => {
    const value = payloadFor(key, true);
    value.template.content.designTheme = 'lavender-v1';
    value.data.template = value.template;
    const before = JSON.stringify(value);
    const blob = await previewPdf(value);
    expect(blob.size).toBeGreaterThan(3000);
    expect(renderManagedDocument(value.template,value.data)!.output()).toContain('0.95 0.93 1. rg');
    expect(JSON.stringify(value)).toBe(before);
    expect(isLavender(value.template.content)).toBe(true);
  });
  it.each(keys)('%s historical structured output stays identical with explicit Original layout', key => {
    const value = payloadFor(key,true);
    const stable = () => { const pdf=renderManagedDocument(value.template,value.data)!; pdf.setCreationDate(new Date('2030-01-01T00:00:00Z')); pdf.setFileId('A'.repeat(32)); return pdf.output(); };
    const old=stable(); value.template.content.designTheme='classic';
    expect(stable()).toBe(old);
    const next=designDraft(structuredClone(value.template.content));
    expect(next.designTheme).toBe('classic');
  });
  it('prepares the theme only on an editable copy and preserves all explicit formatting', () => {
    const value=payloadFor('ilr',true).template.content;
    value.blocks[0].style={fontSize:14,bold:false,align:'right',spaceAfter:7};
    value.blocks[0].runs=[{text:value.blocks[0].text!,italic:true,fontSize:16}];
    value.pageLayout={size:'a4',margins:{top:25,left:20,right:21,bottom:24},header:'Custom header',footer:'Custom footer',pageNumbers:false};
    const old=structuredClone(value), next=designDraft(structuredClone(value));
    expect(value).toEqual(old); expect(next).toEqual({...old,designTheme:'purple-reference-v2'});
    const page=documentPages(next)[0], dom=document.createElement('div'); dom.innerHTML=pageHtml(page,next);
    expect(dom.querySelector('h1')!.style.fontSize).toBe('14pt');
    expect(dom.querySelector('h1')!.style.fontWeight).toBe('normal');
    const roundtrip=readPage(dom,page);
    expect(roundtrip[0].style).toEqual(old.blocks[0].style);
    expect(roundtrip[0].runs).toEqual(expect.arrayContaining([expect.objectContaining({fontSize:16,italic:true})]));
  });
  it('does not turn shared heading defaults into stored rich-text overrides', () => {
    const content=designDraft(payloadFor('apprenticeship-agreement').template.content);
    const page=documentPages(content)[0], dom=document.createElement('div'); dom.innerHTML=pageHtml(page,content);
    expect(dom.querySelector('h1')!.style.fontSize).toBe('');
    const first=readPage(dom,page)[0];
    expect(first.runs!.every(run=>run.fontSize===undefined && run.bold===undefined)).toBe(true);
  });
  it('keeps false run overrides and custom table/list block formatting after page editing', () => {
    const content=designDraft(payloadFor('ilr').template.content);
    const page={id:'custom',blocks:[
      {id:'p',type:'paragraph' as const,text:'Plain inside bold',runs:[{text:'Plain inside bold',bold:false}],style:{bold:true}},
      {id:'table',type:'table' as const,columns:['A','B'],rows:[['1','2']],style:{fontSize:12,align:'right' as const}},
      {id:'list',type:'list' as const,ordered:false,items:[[{text:'Item'}]],style:{fontSize:14}},
    ]};
    const dom=document.createElement('div'); dom.innerHTML=pageHtml(page,content);
    const blocks=readPage(dom,page);
    expect(blocks[0].runs![0].bold).toBe(false);
    expect(blocks[1].style).toEqual(page.blocks[1].style);
    expect(blocks[2].style).toEqual(page.blocks[2].style);
  });
  it('keeps historical signature provenance and complete custom header/footer text', () => {
    const value=payloadFor('ilr',true); value.template.content.designTheme='lavender-v1';
    value.template.content.pageLayout={size:'a4',margins:{top:10,left:18,right:18,bottom:10},header:'Custom header '.repeat(18)+'HEADER-END',footer:'Custom footer '.repeat(18)+'FOOTER-END',pageNumbers:false};
    value.data.signatures.learner={signed:true,name:'Historical Signer',signedAt:'2030-01-02T10:00:00Z'};
    value.data.signatureProvenance={learner:{sourceRevision:1,name:'Historical Signer',signedAt:'2030-01-02T10:00:00Z'}};
    const before=JSON.stringify(value), pdf=renderManagedDocument(value.template,value.data)!;
    const output=pdf.output();
    expect(output).toContain('Historical signature reproduced from Revision R1');
    expect(output).toContain('Historical Signer'); expect(output).toContain('HEADER-END'); expect(output).toContain('FOOTER-END');
    expect(JSON.stringify(value)).toBe(before);
  });
});

// Optional local review artifacts. Normal tests are fully self-contained.
if (process.env.DOCUMENT_DESIGN_ARTIFACTS) {
  it('renders all sixteen synthetic short/long previews for visual inspection', async () => {
    const target=resolve(process.env.DOCUMENT_DESIGN_ARTIFACTS!); mkdirSync(target,{recursive:true});
    const samples=JSON.parse(readFileSync(resolve(target,'../document-design-samples.json'),'utf8')) as Record<string,PreviewResponse>;
    for (const [name,value] of Object.entries(samples)) {
      const before=JSON.stringify(value);
      const blob=await previewPdf(value);
      writeFileSync(resolve(target,name+'.pdf'),new Uint8Array(await blob.arrayBuffer()));
      const historical=structuredClone(value); delete historical.template.content.designTheme;
      historical.data.template=historical.template;
      historical.template.legacyEquivalent=false;
      const oldBlob=await previewPdf(historical);
      writeFileSync(resolve(target,name+'-classic.pdf'),new Uint8Array(await oldBlob.arrayBuffer()));
      expect(JSON.stringify(value)).toBe(before);
    }
  },60000);
}
