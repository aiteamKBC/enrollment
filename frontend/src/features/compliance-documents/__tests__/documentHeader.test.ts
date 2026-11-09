import { describe, expect, it, vi } from 'vitest';
import jsPDF from 'jspdf';
import { decode } from 'fast-png';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import assets from '../brandingAssets.json';
import { BRANDED_PURPLE, BRAND_ID, CURRENT_BRAND_ID } from '../brandedPurpleTheme';
import { brandedPage } from '../brandedPurplePdf';
import { PDF_HEADER, headerLogoGeometry } from '../documentHeader';
import { renderManagedDocument } from '../render';
import { keys, payloadFor } from './fixtures';

describe('consistent visible PDF logo header', () => {
  it('keeps both supplied original files unchanged and embeds the separate KBC variant', () => {
    const hash=(bytes:Uint8Array)=>createHash('sha256').update(bytes).digest('hex');
    expect(hash(readFileSync('public/assets/document-branding/kbc-logo.png'))).toBe('9e1b69be5626bf13e324bb46922e8db167e50c2e4aee68446274b302b4ff1111');
    expect(hash(readFileSync('public/assets/document-branding/dfe-logo.png'))).toBe('32f58e7547a9eec1dbf8ddedb341c71527da7a27179b5928a559099ec91228e9');
    expect(Buffer.from(assets.kbcDeepPurple.data.split(',')[1],'base64')).toEqual(readFileSync('public/assets/document-branding/kbc-logo-deep-purple.png'));
  });
  it('preserves every alpha byte, all gold/white pixels and all transparent pixels', () => {
    const original=decode(readFileSync('public/assets/document-branding/kbc-logo.png'));
    const variant=decode(readFileSync('public/assets/document-branding/kbc-logo-deep-purple.png'));
    expect([variant.width,variant.height,variant.channels]).toEqual([original.width,original.height,original.channels]);
    let gold=0,white=0,changed=0,exactPurple=0;
    for(let i=0;i<original.data.length;i+=4) {
      const [r,g,b,a]=original.data.slice(i,i+4);
      expect(variant.data[i+3]).toBe(a);
      const isGold=r>g&&g>b&&r>80, isWhite=Math.min(r,g,b)>225&&Math.max(r,g,b)-Math.min(r,g,b)<=10;
      if(isGold)gold++;if(isWhite)white++;
      if(!a||isGold||isWhite)expect(Array.from(variant.data.slice(i,i+4))).toEqual(Array.from(original.data.slice(i,i+4)));
      if(r!==variant.data[i]||g!==variant.data[i+1]||b!==variant.data[i+2])changed++;
      if(a&&BRANDED_PURPLE.heading.every((v,k)=>variant.data[i+k]===v))exactPurple++;
    }
    expect(gold).toBeGreaterThan(4000);expect(white).toBeGreaterThan(5000);
    expect(changed).toBeGreaterThan(20000);expect(exactPurple).toBeGreaterThan(19000);
    expect(assets.kbc.bounds).toEqual(assets.kbcDeepPurple.bounds);
  });
  it('uses exactly the same logo geometry on first, second, third and final pages', () => {
    const doc=new jsPDF();const draw=vi.spyOn(doc,'addImage');
    for(let page=0;page<5;page++){if(page)doc.addPage();brandedPage(doc,CURRENT_BRAND_ID);}
    for(let page=0;page<5;page++)for(const [i,name] of (['kbcDeepPurple','dfe'] as const).entries()) {
      const g=headerLogoGeometry(name),call=draw.mock.calls[page*2+i];
      expect(call.slice(2,6)).toEqual([g.x,g.y,g.width,g.height]);
      expect(g.width/g.height).toBeCloseTo(assets[name].width/assets[name].height,10);
      expect(g.visible.y).toBe(16);expect(g.visible.height).toBe(18);
      expect(g.visible.y+g.visible.height+PDF_HEADER.clearance).toBeLessThanOrEqual(PDF_HEADER.contentTop);
    }
  });
  it.each(keys)('applies the shared header to newly generated %s while preserving its content', key => {
    const value=payloadFor(key);value.template.presentationTheme=CURRENT_BRAND_ID;
    const before=JSON.stringify(value);const doc=renderManagedDocument(value.template,value.data)!;
    expect(doc.output()).toContain(`Page ${doc.getNumberOfPages()} of ${doc.getNumberOfPages()}`);
    expect(JSON.stringify(value)).toBe(before);
    expect(Object.keys((doc.internal as any).collections.addImage_images)).toHaveLength(2);
  });
  it('retains original v3 image sizes/artwork for frozen old presentations', () => {
    const doc=new jsPDF();const draw=vi.spyOn(doc,'addImage');
    brandedPage(doc,BRAND_ID);doc.addPage();brandedPage(doc,BRAND_ID);
    const calls=draw.mock.calls as unknown as unknown[][];
    expect(calls[0][4]).toBe(47);expect(calls[2][4]).toBe(30);
    expect(calls[0][0]).toBe(assets.kbc.data);
    expect(calls[1][0]).toBe(assets.dfe.data);
  });
});
