import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { keys, payloadFor } from './fixtures';
import assets from '../brandingAssets.json';
import { BRAND_ID } from '../brandedPurpleTheme';
import { previewPdf } from '../previewPdf';
import { renderManagedDocument } from '../render';
import { buildReviewPdf } from '@/pages/learner/onboarding/reviews/reviewDocument';

describe('versioned KBC and DfE PDF branding', () => {
  it('embeds the supplied official PNGs byte for byte, without substitutes', () => {
    for (const name of ['kbc','dfe'] as const) {
      expect(Buffer.from(assets[name].data.split(',')[1], 'base64')).toEqual(readFileSync(resolve(`public/assets/document-branding/${name}-logo.png`)));
    }
  });
  it.each(keys)('renders %s with both logos, native text and dynamic footer without changing its content', key => {
    const value=payloadFor(key); value.template.presentationTheme=BRAND_ID;
    const before=JSON.stringify(value);
    const doc=renderManagedDocument(value.template,value.data)!;
    expect(doc).not.toBeNull();
    expect(Object.keys((doc.internal as any).collections.addImage_images)).toHaveLength(2);
    const pdf=doc.output();
    expect(pdf).toContain('Page 1 of ' + doc.getNumberOfPages());
    expect(pdf).toContain('Page ' + doc.getNumberOfPages() + ' of ' + doc.getNumberOfPages());
    expect(JSON.stringify(value)).toBe(before);
  });
  it('retains the legacy renderer for snapshots without the new frozen presentation version', () => {
    const value=payloadFor('ilr'); value.template.content.designTheme='purple-reference-v2';
    const doc=renderManagedDocument(value.template,value.data)!;
    expect(doc.output()).not.toContain('Page 1 of ');
    expect(Object.keys((doc.internal as any).collections.addImage_images || {})).toHaveLength(0);
  });
  it('uses server preview bytes and rejects explicitly unavailable historical artifacts', async () => {
    const value=payloadFor('ilr');value.pdfBase64=btoa('%PDF-1.3\nexact server bytes\n%%EOF');
    expect((await previewPdf(value)).type).toBe('application/pdf');
    value.pdfUnavailable='Historical PDF unavailable';
    await expect(previewPdf(value)).rejects.toThrow('Historical PDF unavailable');
  });
  it('preserves every row in a long multipage field/value table and numbers its final page', () => {
    const value=payloadFor('ilr');value.template.presentationTheme=BRAND_ID;
    value.template.content.blocks=[{id:'title',type:'heading',level:1,text:'Long document'},
      {id:'long-table',type:'table',columns:['Field','Value'],rows:Array.from({length:170},(_,i)=>['Field '+i,'Real learner answer '+i])}];
    const doc=renderManagedDocument(value.template,value.data)!;
    expect(doc.getNumberOfPages()).toBeGreaterThan(3);
    expect(doc.output()).toContain('Real learner answer 169');
    expect(doc.output()).toContain(`Page ${doc.getNumberOfPages()} of ${doc.getNumberOfPages()}`);
  });
  it('retains configured header/footer text alongside branding on long documents', () => {
    const value=payloadFor('ilr');value.template.presentationTheme=BRAND_ID;
    value.template.content.pageLayout={size:'a4',margins:{top:12,right:12,bottom:20,left:12},header:'Approved learner document header '.repeat(12),footer:'Provider document control '.repeat(12),pageNumbers:true};
    const doc=renderManagedDocument(value.template,value.data)!;
    expect(doc.output()).toContain('Approved learner document header');
    expect(doc.output()).toContain('Provider document control');
    expect(doc.output()).toContain(`Page ${doc.getNumberOfPages()} of ${doc.getNumberOfPages()}`);
  });
  it('keeps answers bound to stable review keys even when two configured questions have identical wording', () => {
    const value=payloadFor('review-eligibility');value.template.presentationTheme=BRAND_ID;
    const block=value.template.content.blocks.find(b=>b.source==='review:ilr')!;
    block.labels={over16:'Repeated question',withinContractTime:'Repeated question'};
    value.data.answers={ilr:{over16:'FIRST UNIQUE VALUE',withinContractTime:'SECOND UNIQUE VALUE'}};
    const doc=buildReviewPdf(value.data as any,{});
    expect(doc.output()).toContain('FIRST UNIQUE VALUE');
    expect(doc.output()).toContain('SECOND UNIQUE VALUE');
  });
});
