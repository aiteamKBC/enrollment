import jsPDF from 'jspdf';
import autoTable from 'jspdf-autotable';
import type { DocumentQuestion, IssuedTemplate, ReviewPrintRow, TextRun, BlockStyle } from './types';
import { substitute } from './fields';
import { questionValue, questionVisible } from './questionFields';
import { formatHoursMinutes } from '@/lib/format';
import seedData from './seed.json';
import { blockDefaults, documentPalette, isLavender, isReferencePurple } from './documentTheme';
import { lavenderPainter } from './lavenderPdf';
import { drawReferenceDecorations, purpleReferencePainter } from './purpleReferencePdf';
import { CURRENT_BRAND_ID, BRANDED_PURPLE, isBrandedPresentation } from './brandedPurpleTheme';
import { PDF_HEADER } from './documentHeader';
import { brandedPage, brandedPurplePainter } from './brandedPurplePdf';

const latin = (value: unknown) => String(value ?? '').replace(/[\u2022\u25cf]/g, '-').replace(/[—–]/g, '-').replace(/[‘’]/g, "'").replace(/[“”]/g, '"').replace(/…/g, '...').replace(/£/g, 'GBP ').replace(/€/g, 'EUR ').replace(/[Ā-\u{10FFFF}]/gu, '?');

/** Same jsPDF/print pipeline. Unchanged/legacy snapshots retain their original
 * renderer; edited structured versions use safe, paginated content blocks. */
export function renderManagedDocument(template: IssuedTemplate | undefined, data: Record<string, any>, options: { reviewRows?: ReviewPrintRow[] } = {}): jsPDF | null {
  if (!template || template.legacyEquivalent && !isLavender(template.content) && !isBrandedPresentation(template.presentationTheme)) return null;
  const branded = isBrandedPresentation(template.presentationTheme), consistentHeader=template.presentationTheme===CURRENT_BRAND_ID;
  const layout = template.content.pageLayout;
  const themed = branded || isLavender(template.content);
  const reference = branded || isReferencePurple(template.content), palette = branded ? BRANDED_PURPLE : documentPalette(template.content);
  const X = layout?.margins.left ?? (reference ? palette.margins.left : 18), W = 210 - X - (layout?.margins.right ?? (reference ? palette.margins.right : 18));
  const doc = new jsPDF({ unit: 'mm', format: 'a4' });
  const decoratedPages = new Set<number>();
  const ensureDecoration = () => { const number = doc.getCurrentPageInfo().pageNumber; if (reference && !decoratedPages.has(number)) { if (branded) brandedPage(doc,template.presentationTheme); else drawReferenceDecorations(doc); decoratedPages.add(number); } };
  if (reference) ensureDecoration();
  let TOP = branded ? Math.max(layout?.margins.top ?? 0, consistentHeader?PDF_HEADER.contentTop:29) : layout?.margins.top ?? (themed ? palette.margins.top : 20);
  let BOTTOM = 297 - (reference ? Math.max(layout?.margins.bottom ?? 0, palette.margins.bottom) : layout?.margins.bottom ?? 20);
  if (themed) {
    doc.setFont('helvetica', 'normal').setFontSize(palette.font.caption);
    const chromeHeight = (value: string, width: number) => value ? doc.splitTextToSize(latin(substitute(value, template)), width).length * palette.font.caption * .352778 * palette.lineHeight + 12 : 0;
    TOP = Math.max(TOP, chromeHeight(layout ? layout.header : reference ? '' : template.content.title, W));
    if (branded && layout?.header) TOP = Math.max(TOP, (consistentHeader?PDF_HEADER.top+Math.max(PDF_HEADER.kbcHeight,PDF_HEADER.dfeHeight)+3:24) + chromeHeight(layout.header, W) - 12 + 3);
    BOTTOM = Math.min(BOTTOM, 297 - chromeHeight(layout?.footer || '', W - (branded ? 32 : 22)));
    if (BOTTOM - TOP < 50) throw new Error('The configured header/footer leaves too little room for document content. Shorten it before previewing.');
  }
  const firstTop = TOP;
  // Continuation tables must start below the top-right waves. The first title
  // occupies the clear left side of the decoration, as in the supplied PDF.
  if (reference) TOP = branded ? Math.max(consistentHeader?TOP:21, layout?.margins.top ?? 0, layout?.header ? TOP : 0) : Math.max(TOP, 32);
  let y = firstTop;
  doc.setProperties({ title: template.content.title });
  const page = () => { doc.addPage(); ensureDecoration(); y = TOP; };
  const room = (height: number) => { if (y + height > BOTTOM) page(); };
  const bounds = { x: X, width: W, top: TOP, bottom: BOTTOM, getY: () => y, setY: (next:number) => { y = next; }, page, room };
  const referencePaint = branded ? brandedPurplePainter(doc,template,bounds,latin,ensureDecoration) : reference ? purpleReferencePainter(doc,template,bounds,latin,ensureDecoration) : null;
  const paint = referencePaint || (themed ? lavenderPainter(doc, template, bounds, latin) : null);
  const para = (value: string, size: number = reference ? palette.font.body : 9.5, bold = false, italic = false, align: 'left' | 'center' | 'right' = 'left', after = 3, interpolate = true) => {
    if (paint) { paint.text([{ text: value }], { fontSize: size, bold, italic, align, spaceAfter: after }, 'paragraph', '', interpolate); return; }
    doc.setFont('helvetica', bold && italic ? 'bolditalic' : bold ? 'bold' : italic ? 'italic' : 'normal').setFontSize(size).setTextColor(20);
    const lines = doc.splitTextToSize(latin(interpolate ? substitute(value, template) : value), W) as string[];
    for (const line of lines) { room(size * 0.45 + 2); doc.text(line, align === 'center' ? X + W / 2 : align === 'right' ? X + W : X, y, { align }); y += size * 0.45 + 1; }
    y += after;
  };
  const table = (columns: string[], rows: unknown[][], interpolate = true) => {
    if (paint) { paint.table(columns, rows, interpolate); return; }
    room(16);
    autoTable(doc, { startY: y, head: [columns.map((v) => latin(substitute(v, template)))], body: rows.map((row) => row.map((v) => latin(interpolate ? substitute(String(v ?? ''), template) : v))),
      margin: { left: X, right: 210 - X - W, top: TOP, bottom: 297 - BOTTOM }, theme: 'grid', styles: { font: 'helvetica', fontSize: 9, cellPadding: 2, overflow: 'linebreak' },
      headStyles: { fillColor: [240, 242, 246], textColor: [30, 30, 30], fontStyle: 'bold' } });
    y = (doc as jsPDF & { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 5;
  };
  const rich = (runs: TextRun[], style: BlockStyle = {}, prefix = '') => {
    if (paint) { paint.text(runs, style, prefix ? 'list' : 'paragraph', prefix); return; }
    type Piece = { text: string; width: number; size: number; bold: boolean; italic: boolean; underline: boolean };
    let line: Piece[] = [], width = 0;
    const font = (p: Piece) => doc.setFont('helvetica', p.bold && p.italic ? 'bolditalic' : p.bold ? 'bold' : p.italic ? 'italic' : 'normal').setFontSize(p.size).setTextColor(20);
    const flush = () => {
      const size = Math.max(style.fontSize || 9.5, ...line.map((p) => p.size)); room(size * .45 + 2);
      let x = X + (style.align === 'center' ? (W - width) / 2 : style.align === 'right' ? W - width : 0);
      for (const p of line) { font(p); doc.text(p.text, x, y); if (p.underline) doc.setDrawColor(20).setLineWidth(.2).line(x, y + .8, x + p.width, y + .8); x += p.width; }
      y += size * .45 + 1; line = []; width = 0;
    };
    for (const run of [...(prefix ? [{ text: prefix }] : []), ...runs]) {
      const part = { text: '', width: 0, size: run.fontSize || style.fontSize || 9.5, bold: run.bold ?? !!style.bold, italic: run.italic ?? !!style.italic, underline: run.underline ?? !!style.underline };
      // Measure characters so long tokens/dynamic values also wrap without clipping.
      for (const ch of latin(substitute(run.text, template))) {
        if (ch === '\n') { flush(); continue; }
        font(part); const w = doc.getTextWidth(ch);
        if (width + w > W && line.length) flush();
        const last = line[line.length - 1];
        if (last && last.size === part.size && last.bold === part.bold && last.italic === part.italic && last.underline === part.underline) { last.text += ch; last.width += w; }
        else line.push({ ...part, text: ch, width: w });
        width += w;
      }
    }
    if (line.length) flush(); y += style.spaceAfter ?? 3;
  };
  const reviewSection = (source: string, labels: Record<string, string> = {}) => {
    const section = source.slice(7); const index = (data.sections || []).indexOf(section);
    if (['ulnPrivacyNotice', 'learnerAcknowledgement'].includes(section)) {
      const answer = data.answers?.[section] || {};
      table(['Question', 'Answer'], Object.entries(labels).map(([key, label]) => [substitute(label, template), (template.bindingVersion??1)>=2 && typeof answer[key]==='boolean' ? answer[key]?'Yes':'No' : answer[key] ?? 'Not completed.']), false);
      return;
    }
    const rows = options.reviewRows || [];
    const sections = rows.map((r, i) => r.kind === 'section' ? i : -1).filter((i) => i >= 0);
    // buildRows starts with Learner Information, followed by the unchanged
    // review section order, then its declaration/signatures.
    const start = sections[index + 1], end = sections[index + 2] ?? rows.length;
    if (start == null) return;
    const recorded = rows.slice(start + 1, end).filter(r => r.kind === 'text' || r.kind === 'note');
    if (branded && Object.keys(labels).length) {
      // A new unsigned document must still contain its configured questions.
      // Existing formatted answers/notes win; missing manual answers stay blank.
      const answer = data.answers?.[section] || {};
      const configuredLabels = new Set(Object.values(labels));
      const full = Object.entries(labels).map(([key,label]) => {
        const raw=answer[key];
        const value=raw==null?'':typeof raw==='boolean'?raw?'Yes':'No':Array.isArray(raw)?raw.map(String).join(', '):String(raw);
        return [substitute(label,template),value];
      });
      for (const row of recorded) if (row.kind==='note'||!configuredLabels.has(row.label||'')) full.push(row.kind==='text' ? [substitute(row.label||'',template),row.value||''] : [row.text||'','']);
      table(['Question','Answer'],full,false);
    } else table(['Question', 'Answer'], recorded.map(r => r.kind === 'text' ? [substitute(r.label || '', template), r.value || ''] : [r.text || '', '']), false);
  };
  const renderFields = (fields: DocumentQuestion[]) => {
    let rows: string[][] = [];
    const flush = () => { if (rows.length) { table(['Question', 'Value'], rows, false); rows = []; } };
    fields.forEach((field, index) => {
      if (!questionVisible(field, fields.slice(0, index), template, data)) return;
      const label = substitute(field.label, template) + (field.required ? ' *' : '');
      if (field.type === 'divider') { flush(); room(7); doc.setDrawColor(180).line(X, y, X + W, y); y += 7; }
      else if (field.type === 'heading') { flush(); if (paint) paint.text([{ text: field.label }], { bold: true, fontSize: palette.font.section }, 'heading'); else para(field.label, 12, true); }
      else if (field.type === 'description') { flush(); para(field.label); }
      else {
        const value = questionValue(field, template, data);
        let display = value == null ? '' : typeof value === 'boolean' ? value ? 'Yes' : 'No' : Array.isArray(value) ? value.join(', ') : String(value);
        if (field.options.length) {
          const selected = Array.isArray(value) ? value : [value === true ? 'Yes' : value === false ? 'No' : String(value ?? '')];
          display = field.options.map((o) => `${selected.includes(o.id) ? '[X]' : '[ ]'} ${substitute(o.label, template)}`).join(field.type === 'checkbox' ? '\n' : '   ');
        }
        if (field.binding?.kind === 'cost') display = `GBP ${display}`;
        rows.push([label + (field.helpText ? '\n' + substitute(field.helpText, template) : ''),
          field.systemLinked || display ? display : field.type === 'multi_line' ? '\n\n' : '____________________']);
      }
      if (field.helpText && ['heading', 'description', 'divider'].includes(field.type)) para(field.helpText, 8.5, false, true);
    });
    flush();
  };
  for (let index = 0; index < template.content.blocks.length; index++) {
    const block = template.content.blocks[index];
    const style = themed ? blockDefaults(block, template.content) : block.style || {};
    if (branded && !block.style?.fontSize) style.fontSize = block.type === 'heading' ? block.level === 1 && index === 0 ? palette.font.title : block.level === 3 ? palette.font.subheading : palette.font.section : palette.font.body;
    if (branded && ['paragraph','declaration'].includes(block.type) && block.style?.spaceAfter === undefined) style.spaceAfter = 2;
    const headingKind = block.level === 1 && (!reference || index === 0 || template.content.blocks[index-1]?.type==='page-break') ? 'title' : block.level === 3 ? 'subheading' : 'heading';
    if(referencePaint && block.type==='heading' && !['heading','paragraph','declaration','date','signature'].includes(template.content.blocks[index-1]?.type||'')){
      // Keep a compact declaration/signature sequence together, including any
      // intervening role heading or declaration text. This avoids a final page
      // containing just the provider's mark, without shrinking or dropping text.
      let height=0, signatures=0;
      for(let at=index;at<template.content.blocks.length;at++){
        const candidate=template.content.blocks[at];
        if(candidate.type==='signature'){
          const state=data.signatures?.[candidate.role!]||{};
          if(candidate.condition==='employer-required'&&!state.required)continue;
          height+=referencePaint.signatureHeight(candidate.label||'',state,data.signatureProvenance?.[candidate.role!]).height+4;signatures++;
        } else if(['heading','paragraph','declaration','date'].includes(candidate.type)){
          const defaults=blockDefaults(candidate,template.content),size=Math.max(defaults.fontSize||10,...(candidate.runs||[]).map(r=>r.fontSize||0));
          height+=referencePaint.textHeight(candidate.runs?.map(r=>r.text).join('')??candidate.text??'',size,!!defaults.bold,defaults.spaceAfter??3);
        } else break;
      }
      if(signatures>1&&height<=BOTTOM-TOP)room(height);
    }
    if(referencePaint && block.type==='heading'){
      const next=template.content.blocks[index+1];
      let columns:string[]=[], rows:unknown[][]=[];
      if(next?.type==='field'){
        columns=['Field','Value'];
        for(let at=index+1;template.content.blocks[at]?.type==='field';at++){const f=template.content.blocks[at];rows.push([substitute(f.label||'',template),substitute(f.value||'',template)]);}
      } else if(next?.type==='table'){columns=next.columns||[];rows=(next.rows||[]).map(row=>row.map(v=>substitute(v,template)));}
      else if(next?.source?.startsWith('review:')){
        const section=next.source.slice(7), answer=data.answers?.[section]||{};
        columns=['Question','Answer'];rows=Object.entries(next.labels||{}).map(([key,label])=>[substitute(label,template),answer[key]??'']);
      }
      if(rows.length)room(Math.min(referencePaint.tableStartHeight(columns,rows)+18,BOTTOM-TOP));
    }
    if (reference && block.type === 'heading' && template.content.blocks[index + 1]?.source === 'learning-plan') room(70);
    if (paint && (block.type === 'heading' || block.type === 'signature' && template.content.blocks[index - 1]?.type !== 'signature')) {
      let height = block.type === 'heading' ? 20 : 0;
      let count = 0;
      for (let next = block.type === 'signature' ? index : index + 1; template.content.blocks[next]?.type === 'signature'; next++) {
        const signature = template.content.blocks[next], state = data.signatures?.[signature.role!] || {};
        if (signature.condition === 'employer-required' && !state.required) continue;
        height += paint.signatureHeight(signature.label || '', state, data.signatureProvenance?.[signature.role!]).height + 4; count++;
      }
      if (count && height <= BOTTOM - TOP) room(height);
    }
    if (block.fields && (block.type !== 'native' || Object.keys(block.labels || {}).length > 0)) { renderFields(block.fields); continue; }
    if (block.type === 'page-break') { page(); continue; }
    if (block.type === 'list') { block.items?.forEach((item, i) => rich(item, style, block.ordered ? `${i + 1}. ` : '- ')); continue; }
    if (block.runs && ['heading', 'paragraph', 'declaration', 'date'].includes(block.type)) {
      const original = seedData.templates[template.content.documentKey], first = original.blocks[0] as { id: string; text?: string };
      const titleOverride = block.type === 'heading' && block.id === first.id && block.text === first.text && template.content.title !== original.title;
      const runs = titleOverride ? [{ text: template.content.title }] : block.runs;
      if (paint) paint.text(runs, reference && block.type==='heading' && headingKind==='heading' && block.level===1 && !block.style?.fontSize ? {...style,fontSize:palette.font.section} : style, block.type === 'heading' ? headingKind : block.type === 'declaration' ? 'declaration' : 'paragraph');
      else rich(runs, { ...style, ...(block.type === 'heading' ? { fontSize: style.fontSize || (block.level === 1 ? 18 : 12), bold: style.bold ?? true } : {}) }); continue;
    }
    if (block.type === 'heading') {
      const original = seedData.templates[template.content.documentKey];
      const originalTitle = original.blocks[0] as { id: string; text?: string };
      const title = block.id === originalTitle.id && block.text === originalTitle.text && template.content.title !== original.title ? template.content.title : block.text || '';
      if (paint) paint.text([{ text: title }], reference && headingKind==='heading' && block.level===1 && !block.style?.fontSize ? {...style,fontSize:palette.font.section} : style, headingKind);
      else { room(15); para(title, style.fontSize || (block.level === 1 ? 18 : block.level === 3 ? 10.5 : 12), style.bold ?? true, !!style.italic, style.align, style.spaceAfter ?? 5); }
    }
    else if (['paragraph', 'declaration', 'date'].includes(block.type)) {
      if (paint) paint.text([{ text: block.text || '' }], style, block.type === 'declaration' ? 'declaration' : 'paragraph');
      else para(block.text || '', style.fontSize || 9.5, !!style.bold, !!style.italic, style.align, style.spaceAfter);
    }
    else if (block.type === 'field') {
      const rows = [[block.label || '', block.value || '']];
      while (template.content.blocks[index + 1]?.type === 'field' && !template.content.blocks[index + 1]?.fields) { const next = template.content.blocks[++index]; rows.push([next.label || '', next.value || '']); }
      table(['Field', 'Value'], rows);
    }
    else if (block.type === 'table') table(block.columns || [], block.rows || []);
    else if (block.type === 'signature') {
      const state = data.signatures?.[block.role!] || {};
      if (block.condition === 'employer-required' && !state.required) continue;
      if (paint) { paint.signature(block.label || '', state, data.marks?.[block.role!] || state.signature, data.signatureProvenance?.[block.role!]); continue; }
      room(43); para(block.label || '', 10.5, true);
      const mark = data.marks?.[block.role!] || state.signature;
      if (state.signed && typeof mark === 'string' && mark.startsWith('data:image/')) {
        try { const props = doc.getImageProperties(mark); const ratio = props.height / props.width; const width = Math.min(60, 17 / ratio); doc.addImage(mark, 'PNG', X, y, width, width * ratio); } catch { /* Existing corrupt-mark fallback: retain the signature area. */ }
      }
      y += 19; doc.setDrawColor(150).line(X, y, X + 75, y); y += 5;
      para(`Name: ${state.name || ''}${state.position ? ` · Position: ${state.position}` : ''}`, 8.5, false, false, 'left', 3, false);
      const signedAt = state.signedAt ? String(state.signedAt).slice(0, 10).split('-').reverse().join('/') : '';
      para(`Date: ${signedAt}`, 8.5); y += 3;
      const provenance = data.signatureProvenance?.[block.role!];
      if (provenance) para(`Historical signature reproduced from Revision R${provenance.sourceRevision}. Signed by ${provenance.name || state.name || ''} on ${String(provenance.signedAt || '').slice(0, 10)}. This signature records approval of that earlier revision; it does not record approval of this revised wording.`, 8, false, true, 'left', 5, false);
    } else if (block.type === 'native') {
      if (block.source?.startsWith('review:')) { reviewSection(block.source, block.labels); if (block.fields) renderFields(block.fields); continue; }
      if (block.source === 'learning-plan') {
        if (referencePaint) { if (data.learningPlan?.length) referencePaint.learningPlan((block.columns || []).map(value=>substitute(value,template)), data.learningPlan); else para('No learning plan activities have been recorded.'); continue; }
        const rows: unknown[][] = [];
        for (const module of data.learningPlan || []) {
          rows.push([module.activity || '', '', module.deliveryLead || '', module.plannedDate || '', module.plannedOtjHours ?? '']);
          for (const week of module.weeks || []) {
            rows.push([`  Week ${week.weekNumber || ''}${week.weekTitle ? ` - ${week.weekTitle}` : ''}`, '', '', '', '']);
            for (const component of week.components || []) rows.push([`    ${component.title || ''}`, component.method || '', '', '', component.otjHours ?? '']);
          }
          if (!module.weeks?.length) rows.push(['No weeks have been authored for this module yet.', '', '', '', '']);
        }
        if (rows.length) table(block.columns || [], rows, false); else para('No learning plan activities have been recorded.');
      } else if (block.source === 'delivery') {
        const delivery = data.delivery || {};
        table(block.columns || [], (delivery.activities || []).map((r: any) => [r.title || '', r.method || '', r.hours == null ? '' : formatHoursMinutes(r.hours)]), false);
        if (!delivery.activities?.length) para('No delivery activities have been recorded.');
        para(`Total planned off-the-job training: ${data.unansweredTemplate && delivery.totalOtjHours == null ? '' : formatHoursMinutes(delivery.totalOtjHours || 0)}`, 9.5, true);
      } else if (block.source === 'costs') {
        const costs = data.costs || {};
        table(block.columns || [], [...(costs.items || []).map((r: any, i: number) => [substitute(block.labels?.[`cost-${i}`] || r.item || '', template), r.price == null ? 'GBP' : `GBP ${r.price}`]),
          ...[[block.labels?.total || 'Total price of Apprenticeship', costs.total], [block.labels?.maximum || 'Funding Band Maximum value', costs.fundingBandMaximum], [block.labels?.balance || 'Balance due', costs.balanceDue]].map(([name, price]) => [substitute(String(name), template), price == null ? 'GBP' : `GBP ${price}`])], false);
      } else if (block.source === 'contacts') {
        for (const key of ['provider', 'epao', 'dfe']) {
          const c = data.contacts?.[key]; if (!c) continue;
          table(block.columns || [], [['Organisation', c.organisation || ''], ...(c.name ? [['First contact name', c.name]] : []), ...(c.website ? [['Website', c.website]] : []), ['E-mail address', c.email || ''], ['Phone number', c.phone || '']], false);
        }
      }
    }
  }
  if (paint) paint.finish();
  else if (layout) for (let number = 1; number <= doc.getNumberOfPages(); number++) {
    doc.setPage(number); doc.setFont('helvetica', 'normal').setFontSize(8).setTextColor(90);
    if (layout.header) doc.text(latin(substitute(layout.header, template)), X, Math.max(6, TOP - 7), { maxWidth: W });
    if (layout.footer) doc.text(latin(substitute(layout.footer, template)), X, Math.min(291, BOTTOM + 9), { maxWidth: W - 22 });
    if (layout.pageNumbers) doc.text(`${number} / ${doc.getNumberOfPages()}`, X + W, Math.min(291, BOTTOM + 9), { align: 'right' });
  }
  return doc;
}
