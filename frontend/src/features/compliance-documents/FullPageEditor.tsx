import { useEffect, useRef, useState } from 'react';
import { btnSecondary, inputClass } from '@/pages/users/components/ui';
import { blockIsProtected, documentPages, escapeHtml, fromPages, pageHtml, readPage } from './pageDocument';
import type { DocumentTemplate, FieldDefinition } from './types';
import { documentPalette, isLavender, isReferencePurple, themeVariables } from './documentTheme';
import { BrandedEditorPage } from './BrandedEditorPage';
import { brandedEditorVariables, decorateBrandedContent, editorBrand, editorMargins } from './brandedEditorTheme';
import { BRAND_ID, BRANDED_PURPLE } from './brandedPurpleTheme';
import './documentTheme.css';

export function FullPageEditor({ content, fields, data, presentationTheme, onChange, onConfigure }: {
  content: DocumentTemplate; fields: FieldDefinition[]; data?: Record<string, any>;
  presentationTheme?: string;
  onChange: (content: DocumentTemplate) => void; onConfigure?: (id: string) => void;
}) {
  const pages = documentPages(content); const [selected, select] = useState(0); const index = Math.min(selected, pages.length - 1); const page = pages[index];
  const surface = useRef<HTMLDivElement>(null); const selection = useRef<Range | null>(null); const rendered = useRef('');
  const history = useRef<{ past: DocumentTemplate[]; future: DocumentTemplate[] }>({ past: [], future: [] });
  const [problem, setProblem] = useState(''); const [overflow, setOverflow] = useState(false); const [, refreshHistory] = useState(0);
  const [field, chooseField] = useState(''); const [size, chooseSize] = useState('3');
  const themed = isLavender(content);
  const reference = isReferencePurple(content), palette=documentPalette(content);
  const brand = editorBrand(content, presentationTheme), branded = Boolean(brand);
  const displayContent = branded && !content.designTheme ? { ...content, designTheme: 'purple-reference-v2' as const } : content;
  const pageNumbers=content.pageLayout?.pageNumbers ?? (themed && !reference);
  const settingsMargins = content.pageLayout?.margins || (branded ? BRANDED_PURPLE.margins : themed ? palette.margins : { top: 20, right: 18, bottom: 20, left: 18 });
  const margins = branded ? editorMargins(content, brand, index + 1) : settingsMargins;
  const checkOverflow = () => { if (surface.current) setOverflow(surface.current.scrollHeight > (branded ? surface.current.clientHeight : (297 - margins.top - margins.bottom) * 96 / 25.4) + 4); };
  const snapshotKey = JSON.stringify(page.blocks) + content.title + content.designTheme + presentationTheme + JSON.stringify(data?.signatures) + JSON.stringify(data?.signatureProvenance);
  useEffect(() => {
    if (!surface.current || rendered.current === snapshotKey) return;
    surface.current.innerHTML = pageHtml(page, displayContent, data); if (branded) decorateBrandedContent(surface.current); rendered.current = snapshotKey; selection.current = null; checkOverflow();
  });
  useEffect(() => {
    if (!surface.current || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(checkOverflow); observer.observe(surface.current);
    return () => observer.disconnect();
  });
  const emit = (next: DocumentTemplate, push = true) => {
    if (JSON.stringify(next) === JSON.stringify(content)) return;
    if (push) { history.current.past.push(structuredClone(content)); if (history.current.past.length > 100) history.current.past.shift(); history.current.future = []; }
    onChange(next); refreshHistory((n) => n + 1);
  };
  const capture = () => {
    if (!surface.current) return;
    try {
      const blocks = readPage(surface.current, page); const next = pages.map((p, i) => i === index ? { ...p, blocks } : p);
      if (branded) decorateBrandedContent(surface.current);
      rendered.current = JSON.stringify(blocks) + content.title + content.designTheme + presentationTheme + JSON.stringify(data?.signatures) + JSON.stringify(data?.signatureProvenance);
      emit(fromPages(content, next)); setProblem(''); checkOverflow();
    } catch (e) { setProblem((e as Error).message); surface.current.innerHTML = pageHtml(page, displayContent, data); if (branded) decorateBrandedContent(surface.current); }
  };
  const saveSelection = () => { const active = window.getSelection(); if (active?.rangeCount && surface.current?.contains(active.anchorNode)) selection.current = active.getRangeAt(0).cloneRange(); };
  const restoreSelection = () => { surface.current?.focus(); if (selection.current && surface.current?.contains(selection.current.startContainer)) { const active = window.getSelection(); active?.removeAllRanges(); active?.addRange(selection.current); } };
  const command = (name: string, value?: string) => { restoreSelection(); document.execCommand(name, false, value); capture(); saveSelection(); };
  const insert = (html: string) => command('insertHTML', html);
  const undo = (redo = false) => {
    const source = redo ? history.current.future : history.current.past; const target = redo ? history.current.past : history.current.future;
    const next = source.pop(); if (!next) return; target.push(structuredClone(content)); rendered.current = ''; onChange(next); refreshHistory((n) => n + 1);
  };
  const pageBreak = (atCursor = true) => {
    const next = [...pages]; const id = `page-${crypto.randomUUID()}`;
    let split = page.blocks.length;
    const range = selection.current;
    if (atCursor && range && surface.current?.contains(range.startContainer)) {
      let node: Node = range.startContainer;
      while (node.parentNode && node.parentNode !== surface.current) node = node.parentNode;
      const anchorId = node instanceof HTMLElement ? node.dataset.docGroup ? Array.from(node.querySelectorAll<HTMLElement>('[data-doc-atom]')).at(-1)?.dataset.docId : node.dataset.docId : undefined;
      const position = page.blocks.findIndex((b) => b.id === anchorId);
      if (position >= 0) split = position + 1;
      // Keep linked tables and signatures whole. Ordinary paragraphs can split
      // at the caret while retaining their safe rich-text runs.
      if (node instanceof HTMLElement && !node.dataset.docAtom && !node.dataset.docGroup && !['TABLE', 'UL', 'OL'].includes(node.tagName) && range.collapsed) {
        const tailRange = range.cloneRange(); tailRange.setEnd(node, node.childNodes.length);
        const tail = tailRange.extractContents(); const after = node.cloneNode(false) as HTMLElement;
        after.dataset.docId = `text-${crypto.randomUUID()}`; after.appendChild(tail);
        const holder = document.createElement('div'); holder.appendChild(after);
        try {
          const before = readPage(surface.current!, page);
          const remaining = readPage(holder, { ...page, blocks: [] });
          next[index] = { ...page, blocks: before.slice(0, split) };
          next.splice(index + 1, 0, { id, breakBlock: { id, type: 'page-break' }, blocks: [...remaining, ...before.slice(split)] });
          emit(fromPages(content, next)); select(index + 1); rendered.current = ''; return;
        } catch (e) { setProblem((e as Error).message); surface.current!.innerHTML = pageHtml(page, displayContent, data); if (branded) decorateBrandedContent(surface.current!); return; }
      }
    }
    next[index] = { ...page, blocks: page.blocks.slice(0, split) };
    next.splice(index + 1, 0, { id, breakBlock: { id, type: 'page-break' }, blocks: page.blocks.slice(split) });
    emit(fromPages(content, next)); select(index + 1); rendered.current = '';
  };
  const tableEdit = (action: 'add-row' | 'remove-row' | 'add-column' | 'remove-column') => {
    restoreSelection(); const node = window.getSelection()?.anchorNode; const cell = (node instanceof Element ? node : node?.parentElement)?.closest('td,th');
    const table = cell?.closest('table');
    if (!cell || !table || table.closest('[data-doc-atom]')) { setProblem('Place the cursor inside an editable table cell. System-linked tables keep their data structure.'); return; }
    const row = cell.parentElement as HTMLTableRowElement, column = (cell as HTMLTableCellElement).cellIndex;
    if (action === 'add-row') { const next = table.insertRow(row.rowIndex + 1); for (let i = 0; i < row.cells.length; i++) next.insertCell().appendChild(document.createElement('br')); }
    if (action === 'remove-row' && table.rows.length > 1) table.deleteRow(row.rowIndex);
    if (action === 'add-column' && row.cells.length < 12) for (const r of Array.from(table.rows)) r.insertCell(column + 1).appendChild(document.createElement('br'));
    if (action === 'remove-column' && row.cells.length > 1) for (const r of Array.from(table.rows)) r.deleteCell(column);
    capture(); selection.current = null;
  };
  const toolbarButton = (label: string, action: () => void, disabled = false) => <button type="button" className={`${btnSecondary} !px-2 !py-1`} disabled={disabled} onMouseDown={(e) => { e.preventDefault(); saveSelection(); }} onClick={action}>{label}</button>;
  return <div className="space-y-3" aria-label="Full-page document editor">
    {problem && <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{problem}</p>}
    <div className="grid min-w-0 items-start gap-4 lg:grid-cols-[13rem_minmax(0,1fr)]">
      <nav aria-label="Document pages" className="space-y-2 rounded-xl border bg-background-50 p-3">
        <p className="text-xs font-semibold uppercase text-foreground-600">Document pages</p>
        {pages.map((p, i) => <div key={`${p.id}-${i}`} className="rounded-lg border border-primary-100 p-2">
          <button className={`w-full rounded-md p-2 text-left text-sm ${i === index ? 'bg-primary-100 font-semibold text-primary-900' : ''}`} aria-current={i === index ? 'page' : undefined} onClick={() => { select(i); rendered.current = ''; }}>Page {i + 1}<span className="mt-1 block truncate text-xs font-normal">{p.blocks.find((b) => b.type === 'heading')?.text || 'Document content'}</span></button>
          <div className="mt-1 flex gap-1">{toolbarButton(`Move page ${i + 1} up`, () => { const next = [...pages]; [next[i - 1], next[i]] = [next[i], next[i - 1]]; emit(fromPages(content, next)); select(i - 1); rendered.current = ''; }, i === 0)}
            {toolbarButton(`Move page ${i + 1} down`, () => { const next = [...pages]; [next[i + 1], next[i]] = [next[i], next[i + 1]]; emit(fromPages(content, next)); select(i + 1); rendered.current = ''; }, i === pages.length - 1)}
          </div>
          {toolbarButton(`Remove page ${i + 1}`, () => { if (window.confirm('Remove this editable page from the draft? Published and issued documents are retained.')) { emit(fromPages(content, pages.filter((_, j) => i !== j))); select(Math.max(0, i - 1)); rendered.current = ''; } }, pages.length === 1 || p.blocks.some(blockIsProtected))}
        </div>)}
        {toolbarButton('Add page', () => pageBreak(false))}
        <p className="text-xs text-foreground-600">Pages use explicit breaks. Overflow continues onto additional PDF pages; PDF Preview shows the final pagination.</p>
      </nav>
      <div className="min-w-0 space-y-3">
        <div role="toolbar" aria-label="Document formatting" className="sticky top-0 z-10 flex flex-wrap items-center gap-2 rounded-xl border bg-background-50 p-3">
          {toolbarButton('Bold', () => command('bold'))}{toolbarButton('Italic', () => command('italic'))}{toolbarButton('Underline', () => command('underline'))}
          <label className="text-xs">Font size<select aria-label="Font size" className={inputClass} value={size} onChange={(e) => { chooseSize(e.target.value); command('fontSize', e.target.value); }}><option value="1">8</option><option value="2">10</option><option value="3">12</option><option value="4">14</option><option value="5">18</option><option value="6">24</option></select></label>
          <label className="text-xs">Paragraph style<select aria-label="Paragraph style" className={inputClass} defaultValue="p" onChange={(e) => command('formatBlock', e.target.value)}><option value="p">Paragraph</option><option value="h1">Heading 1</option><option value="h2">Heading 2</option><option value="h3">Heading 3</option></select></label>
          {toolbarButton('Align left', () => command('justifyLeft'))}{toolbarButton('Align centre', () => command('justifyCenter'))}{toolbarButton('Align right', () => command('justifyRight'))}
          {toolbarButton('Bullet list', () => command('insertUnorderedList'))}{toolbarButton('Numbered list', () => command('insertOrderedList'))}
          {toolbarButton('Add table', () => insert('<table style="width:100%;border-collapse:collapse"><thead><tr><th>Column 1</th><th>Column 2</th></tr></thead><tbody><tr><td><br></td><td><br></td></tr></tbody></table><p><br></p>'))}
          {toolbarButton('Add row', () => tableEdit('add-row'))}{toolbarButton('Remove row', () => tableEdit('remove-row'))}{toolbarButton('Add column', () => tableEdit('add-column'))}{toolbarButton('Remove column', () => tableEdit('remove-column'))}
          <select aria-label="Learner or document field" className={`${inputClass} !w-auto max-w-60 text-xs`} value={field} onChange={(e) => chooseField(e.target.value)}><option value="">Choose a learner or document field</option>{fields.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}</select>
          {toolbarButton('Insert dynamic field', () => insert(`<span contenteditable="false" data-field-key="${escapeHtml(field)}">{{${escapeHtml(field)}}}</span>`), !field)}
          {toolbarButton('Insert date', () => { const dateField = fields.find((f) => f.format === 'date'); if (dateField) insert(`<span contenteditable="false" data-field-key="${escapeHtml(dateField.key)}">{{${escapeHtml(dateField.key)}}}</span>`); })}
          {toolbarButton('Insert signature block', () => { const missing = content.signatureRoles.find((role) => !content.blocks.some((b) => b.type === 'signature' && b.role === role)); if (!missing) { setProblem('All required signature blocks are already present on the document pages.'); return; } const next = pages.map((p, i) => i === index ? { ...p, blocks: [...p.blocks, { id: `signature-${missing}`, type: 'signature' as const, role: missing, label: `${missing} signature` }] } : p); emit(fromPages(content, next)); rendered.current = ''; })}
          {toolbarButton('Insert page break', () => pageBreak())}{toolbarButton('Undo', () => undo(), !history.current.past.length)}{toolbarButton('Redo', () => undo(true), !history.current.future.length)}
        </div>
        <details className="rounded-lg border bg-background-50 p-3 text-sm"><summary>Page setup — A4</summary><div className="mt-3 flex flex-wrap gap-3">{(['top', 'right', 'bottom', 'left'] as const).map((name) => <label key={name} className="text-xs">{name} margin (mm)<input type="number" min={10} max={35} className={inputClass} value={settingsMargins[name]} onChange={(e) => emit({ ...content, pageLayout: { size: 'a4', header: '', footer: '', pageNumbers: false, ...content.pageLayout, margins: { ...settingsMargins, [name]: Number(e.target.value) } } })} /></label>)}
          {(['header', 'footer'] as const).map((name) => <label className="text-xs" key={name}>{name}<input className={inputClass} value={content.pageLayout?.[name] || ''} onChange={(e) => emit({ ...content, pageLayout: { size: 'a4', margins: settingsMargins, header: '', footer: '', pageNumbers: false, ...content.pageLayout, [name]: e.target.value } })} /></label>)}
          <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={pageNumbers} onChange={(e) => emit({ ...content, pageLayout: { size: 'a4', margins: settingsMargins, header: '', footer: '', ...content.pageLayout, pageNumbers: e.target.checked } })} />Page numbers</label>
        </div></details>
        {overflow && <p role="status" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs">This editing page exceeds one A4 sheet. {branded && 'Scroll within the editable content to reach the remaining text. '}The PDF flows onto additional pages without cutting content. Insert an explicit page break or check PDF Preview before publishing.</p>}
        <div className="overflow-x-auto rounded-xl border bg-slate-100 p-3 md:p-6">
          <div data-testid="document-editor-paper" className={`mx-auto bg-white shadow ${themed || branded ? 'document-lavender-v1' : 'text-black'} ${reference || branded ? 'document-reference-v2' : ''} ${branded ? 'document-branded-editor' : ''}`} style={{ ...(themed ? themeVariables(content) : {}), ...(branded ? brandedEditorVariables() : {}), width: '210mm', minHeight: '297mm', padding: `${margins.top}mm ${margins.right}mm ${margins.bottom}mm ${margins.left}mm` }}>
            {branded && <BrandedEditorPage page={index + 1} total={pages.length} historical={brand === BRAND_ID} />}
            {content.pageLayout?.header && <p className="document-page-chrome mb-3 text-xs text-slate-600">{content.pageLayout.header}</p>}
            <div ref={surface} contentEditable suppressContentEditableWarning role="textbox" aria-label={`Document page ${index + 1}`} aria-multiline="true" className="document-content min-h-40 outline-none" style={{ minHeight: branded ? undefined : `${297 - margins.top - margins.bottom}mm`, fontFamily: 'Arial, sans-serif', fontSize: branded ? `${BRANDED_PURPLE.font.body}pt` : '9.5pt', lineHeight: branded ? BRANDED_PURPLE.lineHeight : 1.45 }}
              onInput={capture} onMouseUp={saveSelection} onKeyUp={saveSelection}
              onDoubleClick={(e) => { const atom = (e.target as HTMLElement).closest<HTMLElement>('[data-doc-atom]'); if (atom?.dataset.docId) onConfigure?.(atom.dataset.docId); }}
              onPaste={(e) => { e.preventDefault(); command('insertText', e.clipboardData.getData('text/plain')); }}
              onDrop={(e) => { e.preventDefault(); setProblem('Paste plain text or insert fields using the toolbar. External HTML and files cannot be dropped into a document.'); }}
              onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); undo(e.shiftKey); } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y') { e.preventDefault(); undo(true); } }} />
            {(content.pageLayout?.footer || !branded && pageNumbers) && <p className="document-page-chrome document-editor-footer mt-3 text-xs text-slate-600">{content.pageLayout?.footer}{!branded && pageNumbers ? ` · Page ${index + 1}` : ''}</p>}
          </div>
        </div>
        <p className="text-xs text-foreground-600">Edit the page directly. Double-click a linked table, question section or signature area to open its protected settings.</p>
        {branded && <p className="text-xs text-foreground-600">Page badges count editable pages. Overflow can add printed pages; PDF Preview shows the authoritative final pagination.</p>}
      </div>
    </div>
  </div>;
}
