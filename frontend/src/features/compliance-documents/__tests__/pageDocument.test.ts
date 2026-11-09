import { describe, expect, it } from 'vitest';
import { documentPages, fromPages, pageHtml, readPage } from '../pageDocument';
import { contentFor, keys, payloadFor } from './fixtures';
import { renderManagedDocument } from '../render';

describe('safe full-page editing and rendering', () => {
  it('retains the existing document-title override after rich-text editing', () => {
    const value = payloadFor('apprenticeship-agreement', true), template = value.template;
    template.content.title = 'Approved replacement title';
    const heading = template.content.blocks[0]; heading.runs = [{ text: heading.text! }];
    const dom = document.createElement('div'); dom.innerHTML = pageHtml(documentPages(template.content)[0], template.content);
    expect(dom.querySelector('h1')?.textContent).toBe('Approved replacement title');
    expect(renderManagedDocument(template, value.data)!.output()).toContain('Approved replacement title');
    heading.text = 'Explicit document heading'; heading.runs = [{ text: heading.text }];
    expect(renderManagedDocument(template, value.data)!.output()).toContain('Explicit document heading');
  });
  it.each(keys)('keeps every original block and controlled data binding for %s', (key) => {
    const content = contentFor(key); const pages = documentPages(content);
    expect(fromPages(content, pages)).toEqual(content);
    for (const page of pages) {
      const dom = document.createElement('div'); dom.innerHTML = pageHtml(page, content, payloadFor(key).data);
      const blocks = readPage(dom, page);
      expect(blocks.map((b) => b.id)).toEqual(page.blocks.map((b) => b.id));
      page.blocks.forEach((original, i) => {
        if (original.text !== undefined) expect(blocks[i].text).toBe(original.text);
        if (['native', 'field', 'signature'].includes(original.type)) expect(blocks[i]).toEqual(original);
      });
    }
  });
  it('serialises supported formatting and field chips instead of executable HTML', () => {
    const content = contentFor('apprenticeship-agreement'); const page = { id: 'test', blocks: [] };
    const dom = document.createElement('div'); dom.innerHTML = '<p>Hello <b>bold</b> <i>italic</i> <u>underlined</u> <span contenteditable="false" data-field-key="learner.fullName">Resolved name</span><img src="x" onerror="alert(1)"></p><ul><li>One</li><li>Two</li></ul>';
    const blocks = readPage(dom, page); expect(blocks[0].text).toBe('Hello bold italic underlined {{learner.fullName}}');
    expect(blocks[0].runs).toEqual(expect.arrayContaining([expect.objectContaining({ text: 'bold', bold: true }), expect.objectContaining({ text: 'underlined', underline: true })]));
    expect(blocks[1].type).toBe('list'); expect(JSON.stringify(blocks)).not.toContain('onerror');
    expect(pageHtml({ ...page, blocks }, content)).not.toContain('<img');
  });
  it('rejects removal or duplication of linked tables and signature identity', () => {
    const content = contentFor('training-plan'); const page = documentPages(content)[0];
    const dom = document.createElement('div'); dom.innerHTML = pageHtml(page, content);
    dom.querySelector('[data-doc-atom]')!.remove(); expect(() => readPage(dom, page)).toThrow('cannot be deleted');
    dom.innerHTML = pageHtml(page, content); const atom = dom.querySelector('[data-doc-atom]')!; dom.appendChild(atom.cloneNode(true));
    expect(() => readPage(dom, page)).toThrow('cannot be copied');
  });
  it('renders full long rich content, lists, page numbers and historical provenance in the PDF', () => {
    const value = payloadFor('apprenticeship-agreement', true); const t = value.template;
    t.content.blocks.push({ id: 'rich', type: 'paragraph', text: ('Long text ').repeat(900) + 'LAST-WORD', runs: [{ text: ('Long text ').repeat(900), bold: true, underline: true }, { text: 'LAST-WORD', italic: true }] });
    t.content.blocks.push({ id: 'list', type: 'list', ordered: true, items: [[{ text: 'First item' }], [{ text: 'Second item' }]] });
    t.content.pageLayout = { size: 'a4', margins: { top: 20, right: 18, bottom: 20, left: 18 }, header: 'Revision R2', footer: 'Record', pageNumbers: true };
    value.data.signatureProvenance = { apprentice: { sourceRevision: 1, name: 'Historical Signer', signedAt: '2030-01-02' } };
    const pdf = renderManagedDocument(t, value.data)!; const output = pdf.output();
    expect(pdf.getNumberOfPages()).toBeGreaterThan(2); expect(output).toContain('LAST-WORD'); expect(output).toContain('Historical signature reproduced from Revision R1');
    expect(output).toContain('1. First item'); expect(output).toContain('Revision R2');
  });
});
