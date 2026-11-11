import { buildRows } from '@/pages/learner/onboarding/reviews/reviewDocument';
import { REVIEW_QUESTION_LABELS } from '@/pages/learner/onboarding/reviews/questions';
import { questionFields, questionValue, questionVisible } from './questionFields';
import { substitute } from './fields';
import type { DocumentBlock, DocumentTemplate, IssuedTemplate, TextRun } from './types';
import seedData from './seed.json';
import { isLavender, isReferencePurple, documentPalette } from './documentTheme';
import jsPDF from 'jspdf';
import { learningPlanRows, learningPlanWidths } from './learningPlanLayout';

export const escapeHtml = (value: unknown) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!));
export interface DocumentPage { id: string; breakBlock?: DocumentBlock; blocks: DocumentBlock[] }
export function documentPages(template: DocumentTemplate): DocumentPage[] {
  const pages: DocumentPage[] = [{ id: 'first-page', blocks: [] }];
  for (const block of template.blocks) {
    if (block.type === 'page-break') pages.push({ id: block.id, breakBlock: block, blocks: [] });
    else pages.at(-1)!.blocks.push(block);
  }
  return pages;
}
export function fromPages(template: DocumentTemplate, pages: DocumentPage[]): DocumentTemplate {
  return { ...template, blocks: pages.flatMap((p, i) => [...(i ? [p.breakBlock || { id: p.id, type: 'page-break' as const }] : []), ...p.blocks]) };
}
export const blockIsProtected = (b: DocumentBlock) => ['native', 'field', 'questions', 'signature'].includes(b.type) || b.type === 'table' && b.rows?.some((r) => r.some((v) => v.includes('{{')));

function inline(value: string, data?: Record<string, any>): string {
  return value.split(/(\{\{[A-Za-z0-9_.-]+\}\})/g).map((part) => {
    const key = part.match(/^\{\{(.+)\}\}$/)?.[1];
    if (!key) return escapeHtml(part).replaceAll('\n', '<br>');
    return `<span contenteditable="false" data-field-key="${escapeHtml(key)}" class="rounded bg-violet-50 px-1 text-violet-800" title="${escapeHtml(part)}">${escapeHtml(data?.template ? substitute(part, data.template) || part : part)}</span>`;
  }).join('');
}
function rich(runs: TextRun[], data?: Record<string, any>, themed = false): string {
  return runs.map((r) => `<span style="${r.bold ? 'font-weight:bold;' : themed && r.bold === false ? 'font-weight:normal;' : ''}${r.italic ? 'font-style:italic;' : themed && r.italic === false ? 'font-style:normal;' : ''}${r.underline ? 'text-decoration:underline;' : themed && r.underline === false ? 'text-decoration:none;' : ''}${r.fontSize ? `font-size:${r.fontSize}pt;` : ''}">${inline(r.text, data)}</span>`).join('');
}
const tableHtml = (columns: unknown[], rows: unknown[][]) => `<table style="width:100%;border-collapse:collapse"><thead><tr>${columns.map((v) => `<th style="border:1px solid #cbd5e1;padding:6px;text-align:left">${escapeHtml(v)}</th>`).join('')}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((v) => `<td style="border:1px solid #cbd5e1;padding:6px;vertical-align:top">${escapeHtml(v)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
const themedTable = (columns: unknown[], rows: unknown[][]) => `<table${columns.length === 2 ? ' data-label-table="true"' : ''}><thead><tr>${columns.map(v => `<th>${escapeHtml(v)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(v => `<td>${escapeHtml(v)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;

function overrides(style: DocumentBlock['style'] = {}): string {
  return `${style.fontSize !== undefined ? `font-size:${style.fontSize}pt;` : ''}${style.bold !== undefined ? `font-weight:${style.bold ? 'bold' : 'normal'};` : ''}${style.italic !== undefined ? `font-style:${style.italic ? 'italic' : 'normal'};` : ''}${style.underline !== undefined ? `text-decoration:${style.underline ? 'underline' : 'none'};` : ''}${style.align ? `text-align:${style.align};` : ''}${style.spaceAfter !== undefined ? `margin-bottom:${style.spaceAfter}mm;` : ''}`;
}

function sourceHtml(block: DocumentBlock, template: DocumentTemplate, data?: Record<string, any>): string {
  const themed = isLavender(template), table = themed ? themedTable : tableHtml;
  const fields = questionFields(block, template.documentKey);
  if (fields.length) {
   let rows: unknown[][] = [];
   const flush = () => { const html = rows.length ? table(block.columns?.length === 2 ? block.columns : block.source?.startsWith('review:') ? ['Question', 'Answer'] : ['Question', 'Value'], rows) : ''; rows = []; return html; };
   const parts = fields.flatMap((f, i) => {
    if (data?.template && !questionVisible(f, fields.slice(0, i), data.template, data)) return [];
    const label = data?.template ? substitute(f.label, data.template) : f.label;
    const help = data?.template ? substitute(f.helpText, data.template) : f.helpText;
    if (['heading', 'description', 'divider'].includes(f.type)) return [(themed ? flush() : '') + (f.type === 'divider' ? '<hr>' : `<${f.type === 'heading' ? 'h3' : 'p'}>${escapeHtml(label)}</${f.type === 'heading' ? 'h3' : 'p'}>${help ? `<small>${escapeHtml(help)}</small>` : ''}`)];
    const value = data?.template ? questionValue(f, data.template, data) : f.binding?.value || (f.binding ? 'Existing saved answer' : f.value);
    const selected = Array.isArray(value) ? value : [value === true ? 'Yes' : value === false ? 'No' : String(value ?? '')];
    const shown = f.options.length ? f.options.map((o) => `${selected.includes(o.id) ? '[X]' : '[ ]'} ${data?.template ? substitute(o.label, data.template) : o.label}`).join('   ') : typeof value === 'boolean' ? value ? 'Yes' : 'No' : Array.isArray(value) ? value.join(', ') : value ?? '';
    const row = [label + (f.required ? ' *' : '') + (help ? '\n' + help : ''), shown];
    if (themed) { rows.push(row); return []; }
    return [table(['Question', 'Value'], [row])];
   });
   return parts.join('') + flush();
  }
  if (block.type === 'signature') {
    const role = block.role!, state = data?.signatures?.[role] || {};
    if (block.condition === 'employer-required' && data && !state.required) return '';
    const mark = data?.marks?.[role] || state.signature || '';
    const valid = /^data:image\/(?:png|jpeg|webp);base64,[a-z0-9+/=]+$/i.test(mark);
    const provenance = data?.signatureProvenance?.[role];
    return `<section ${themed ? 'class="document-signature"' : 'style="min-height:110px;margin:12px 0"'}><strong>${escapeHtml(block.label)}</strong>${valid ? `<div><img src="${mark}" alt="${escapeHtml(block.label)}" ${themed ? '' : 'style="height:55px;max-width:250px;object-fit:contain"'}></div>` : '<div style="height:40px"></div>'}<div style="border-top:1px solid #94a3b8;max-width:260px;padding-top:4px">${escapeHtml(state.name || '')}</div><div>Date: ${escapeHtml(state.signedAt ? String(state.signedAt).slice(0, 10).split('-').reverse().join('/') : '')}</div>${provenance ? `<small>Historical signature from Revision R${provenance.sourceRevision}. It does not approve changes to this copy.</small>` : ''}</section>`;
  }
  if (!data) return `<div class="rounded border border-violet-100 p-3"><strong>${escapeHtml(block.label || 'Linked document data')}</strong><p>Existing issued data; resolved values appear in PDF Preview.</p></div>`;
  if (block.source?.startsWith('review:')) {
    const rows = buildRows(data as any, REVIEW_QUESTION_LABELS); const section = block.source.slice(7); const index = data.sections.indexOf(section);
    const starts = rows.map((r, i) => r.kind === 'section' ? i : -1).filter((i) => i >= 0);
    return table(['Question', 'Answer'], rows.slice(starts[index + 1] + 1, starts[index + 2] ?? rows.length).filter((r) => r.kind === 'text' || r.kind === 'note').map((r) => r.kind === 'text' ? [r.label, r.value] : [r.kind === 'note' ? r.text : '', '']));
  }
  if (block.source === 'learning-plan') {
    if (isReferencePurple(template)) {
      const rows=learningPlanRows(data.learningPlan||[]);
      const measureDoc=new jsPDF();measureDoc.setFont('helvetica','normal').setFontSize(9.5);
      const measure=(value:string)=>{measureDoc.setFont('helvetica','normal');const normal=measureDoc.getTextWidth(value);measureDoc.setFont('helvetica','bold');return Math.max(normal,measureDoc.getTextWidth(value));};
      const margins=template.pageLayout?.margins||documentPalette(template).margins;
      const layout=learningPlanWidths(210-margins.left-margins.right,rows,measure);
      const columns=block.columns||[];
      if(layout.stacked)return rows.map(row=>row.kind==='week'||row.kind==='empty'?table([columns[0]],[[row.values[0]]]):table(['Field','Value'],columns.map((label,i)=>[label,row.values[i]]))).join('');
      return `<table data-learning-plan="true"><colgroup>${layout.widths.map(width=>`<col style="width:${width}mm">`).join('')}</colgroup><thead><tr>${columns.map(label=>`<th>${escapeHtml(data?.template?substitute(label,data.template):label)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>row.kind==='week'||row.kind==='empty'?`<tr data-plan-kind="${row.kind}"><td colspan="5">${escapeHtml(row.values[0])}</td></tr>`:`<tr data-plan-kind="${row.kind}">${row.values.map((value,i)=>`<td${i===4?' class="plan-number"':i===3?' class="plan-date"':''}>${escapeHtml(value)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
    }
    const rows: unknown[][] = [];
    for (const m of data.learningPlan || []) {
      rows.push([m.activity, '', m.deliveryLead, m.plannedDate, m.plannedOtjHours]);
      for (const w of m.weeks || []) { rows.push([`Week ${w.weekNumber} ${w.weekTitle || ''}`, '', '', '', '']); for (const c of w.components || []) rows.push([c.title, c.method, '', '', c.otjHours]); }
    }
    return table(block.columns || [], rows);
  }
  if (block.source === 'delivery') return table(block.columns || [], (data.delivery?.activities || []).map((r: any) => [r.title, r.method, r.hours]));
  if (block.source === 'contacts') return ['provider', 'epao', 'dfe'].map((key) => data.contacts?.[key] ? table(['Field', 'Value'], Object.entries(data.contacts[key])) : '').join('');
  return table(block.columns || ['Field', 'Value'], []);
}

export function pageHtml(page: DocumentPage, template: DocumentTemplate, data?: Record<string, any>): string {
  const themed = isLavender(template);
  return page.blocks.map((block, index) => {
    if (block.type === 'field' && !block.fields) {
      if (index > 0 && page.blocks[index - 1].type === 'field' && !page.blocks[index - 1].fields) return '';
      const fields: DocumentBlock[] = [];
      for (let i = index; i < page.blocks.length && page.blocks[i].type === 'field' && !page.blocks[i].fields; i++) fields.push(page.blocks[i]);
      if (themed) return `<div data-doc-group="true" contenteditable="false"><table data-label-table="true"><thead><tr><th>Field</th><th>Value</th></tr></thead><tbody>${fields.map(f => `<tr data-doc-id="${escapeHtml(f.id)}" data-doc-atom="true"><td>${inline(f.label || '', data)}</td><td>${inline(f.value || '', data)}</td></tr>`).join('')}</tbody></table></div>`;
      return `<div data-doc-group="true" contenteditable="false" style="margin:10px 0"><table style="width:100%;border-collapse:collapse"><thead><tr><th style="border:1px solid #cbd5e1;padding:6px;text-align:left">Field</th><th style="border:1px solid #cbd5e1;padding:6px;text-align:left">Value</th></tr></thead><tbody>${fields.map((f) => `<tr data-doc-id="${escapeHtml(f.id)}" data-doc-atom="true"><td style="border:1px solid #cbd5e1;padding:6px">${inline(f.label || '', data)}</td><td style="border:1px solid #cbd5e1;padding:6px">${inline(f.value || '', data)}</td></tr>`).join('')}</tbody></table></div>`;
    }
    const attrs = `data-doc-id="${escapeHtml(block.id)}"`; const style = block.style || {};
    if (blockIsProtected(block)) return `<div ${attrs} data-doc-atom="true" contenteditable="false" class="my-3 cursor-default rounded border border-transparent hover:border-violet-300" tabindex="0">${sourceHtml(block, template, data)}</div>`;
    if (themed && block.type === 'table') return `<table ${attrs} style="${overrides(style)}"><thead><tr>${(block.columns || []).map(c => `<th>${inline(c, data)}</th>`).join('')}</tr></thead><tbody>${(block.rows || []).map(row => `<tr>${row.map(c => `<td>${inline(c, data) || '<br>'}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
    if (block.type === 'table') return `<table ${attrs} style="width:100%;border-collapse:collapse;margin:10px 0"><thead><tr>${(block.columns || []).map((c) => `<th style="border:1px solid #cbd5e1;padding:6px">${inline(c, data)}</th>`).join('')}</tr></thead><tbody>${(block.rows || []).map((r) => `<tr>${r.map((c) => `<td style="border:1px solid #cbd5e1;padding:6px">${inline(c, data) || '<br>'}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
    if (block.type === 'list') return `<${block.ordered ? 'ol' : 'ul'} ${attrs} style="list-style:${block.ordered ? 'decimal' : 'disc'};${themed ? overrides(style) : 'padding-left:25px;'}">${(block.items || []).map((r) => `<li>${rich(r, data, themed)}</li>`).join('')}</${block.ordered ? 'ol' : 'ul'}>`;
    const tag = block.type === 'heading' ? `h${block.level || 2}` : 'p';
    const original = seedData.templates[template.documentKey], first = original.blocks[0] as DocumentBlock;
    const titleOverride = block.type === 'heading' && block.id === first.id && block.text === first.text && template.title !== original.title;
    if (themed) return `<${tag} ${attrs}${block.type === 'declaration' ? ' class="document-declaration"' : isReferencePurple(template) && tag==='h1' && index>0 ? ' class="document-section-title"' : ''} style="${overrides(style)}">${titleOverride ? inline(template.title, data) : block.runs ? rich(block.runs, data, true) : inline(block.text || '', data) || '<br>'}</${tag}>`;
    return `<${tag} ${attrs} style="margin:0 0 ${style.spaceAfter ?? (block.type === 'heading' ? 5 : 3)}mm;text-align:${style.align || 'left'};font-size:${style.fontSize || (block.type === 'heading' ? block.level === 1 ? 18 : 12 : 9.5)}pt;font-weight:${style.bold ?? block.type === 'heading' ? 'bold' : 'normal'};${style.italic ? 'font-style:italic;' : ''}${style.underline ? 'text-decoration:underline;' : ''}">${titleOverride ? inline(template.title, data) : block.runs ? rich(block.runs, data) : inline(block.text || '', data) || '<br>'}</${tag}>`;
  }).join('') || '<p><br></p>';
}

function readRuns(node: Node, inherited: Omit<TextRun, 'text'> = {}): TextRun[] {
  if (node.nodeType === Node.TEXT_NODE) return [{ text: node.textContent || '', ...inherited }];
  if (!(node instanceof HTMLElement)) return [];
  if (node.dataset.fieldKey) return [{ text: '{{' + node.dataset.fieldKey + '}}', ...inherited }];
  if (node.tagName === 'BR') return [{ text: '\n', ...inherited }];
  const style = { ...inherited };
  if (['B', 'STRONG'].includes(node.tagName) || node.style.fontWeight === 'bold' || Number(node.style.fontWeight) >= 600) style.bold = true;
  else if (node.style.fontWeight === 'normal' || node.style.fontWeight === '400') style.bold = false;
  if (['I', 'EM'].includes(node.tagName) || node.style.fontStyle === 'italic') style.italic = true;
  else if (node.style.fontStyle === 'normal') style.italic = false;
  if (node.tagName === 'U' || node.style.textDecoration.includes('underline')) style.underline = true;
  else if (node.style.textDecoration.includes('none')) style.underline = false;
  const sizes: Record<string, number> = { '1': 8, '2': 10, '3': 12, '4': 14, '5': 18, '6': 24, '7': 24 };
  if (node.tagName === 'FONT' && sizes[node.getAttribute('size') || '']) style.fontSize = sizes[node.getAttribute('size')!];
  if (node.style.fontSize) { const value = Number.parseFloat(node.style.fontSize); if (Number.isFinite(value)) style.fontSize = Math.max(7, Math.min(24, value)); }
  return Array.from(node.childNodes).flatMap((child) => readRuns(child, style));
}

export function readPage(element: HTMLElement, original: DocumentPage): DocumentBlock[] {
  const protectedBlocks = new Map(original.blocks.filter(blockIsProtected).map((b) => [b.id, b]));
  const seen = new Set<string>(); const result: DocumentBlock[] = [];
  for (let child of Array.from(element.childNodes)) {
    if (child.nodeType === Node.TEXT_NODE && child.textContent?.trim()) {
      const paragraph = document.createElement('p'); element.insertBefore(paragraph, child); paragraph.appendChild(child); child = paragraph;
    }
    if (child instanceof HTMLElement && child.dataset.docGroup) {
      for (const atom of child.querySelectorAll<HTMLElement>('[data-doc-atom]')) {
        const id = atom.dataset.docId!, block = protectedBlocks.get(id);
        if (!block || seen.has(id)) throw new Error('System-linked blocks cannot be copied or rebound.');
        result.push(block); seen.add(id);
      }
      continue;
    }
    if (child instanceof HTMLElement && child.dataset.docAtom) {
      const id = child.dataset.docId!; const block = protectedBlocks.get(id);
      if (!block || seen.has(id)) throw new Error('System-linked blocks cannot be copied or rebound.');
      result.push(block); seen.add(id); continue;
    }
    if (!child.textContent?.trim() && !(child instanceof HTMLElement)) continue;
    let id = child instanceof HTMLElement ? child.dataset.docId : undefined;
    if (!id || seen.has(id)) id = `text-${crypto.randomUUID()}`;
    if (child instanceof HTMLElement) child.dataset.docId = id;
    seen.add(id);
    const old = original.blocks.find((b) => b.id === id);
    if (child instanceof HTMLElement && child.tagName === 'TABLE') {
      const rows = Array.from(child.querySelectorAll('tr')).map((r) => Array.from(r.children).map((c) => readRuns(c).map((r) => r.text).join('')));
      if (!rows.length) continue;
      result.push({ id, type: 'table', columns: rows[0], rows: rows.slice(1).map((r) => [...r, ...Array(Math.max(0, rows[0].length - r.length)).fill('')].slice(0, rows[0].length)), ...(old?.style ? { style: old.style } : {}) }); continue;
    }
    if (child instanceof HTMLElement && ['UL', 'OL'].includes(child.tagName)) { result.push({ id, type: 'list', ordered: child.tagName === 'OL', items: Array.from(child.children).map((li) => readRuns(li)), ...(old?.style ? { style: old.style } : {}) }); continue; }
    const runs = readRuns(child); if (runs.length === 1 && runs[0].text === '\n') runs[0].text = '';
    const heading = child instanceof HTMLElement ? child.tagName.match(/^H([123])$/) : null;
    result.push({ id, type: heading ? 'heading' : old?.type === 'declaration' || old?.type === 'date' ? old.type : 'paragraph',
      ...(heading ? { level: Number(heading[1]) } : {}), text: runs.map((r) => r.text).join(''), runs,
      style: { ...(old?.style || {}), align: child instanceof HTMLElement && ['left', 'center', 'right'].includes(child.style.textAlign) ? child.style.textAlign as 'left' : 'left' } });
  }
  if ([...protectedBlocks.keys()].some((id) => !seen.has(id))) throw new Error('System-linked fields and signature blocks cannot be deleted. Edit their settings or surrounding text.');
  return result;
}
