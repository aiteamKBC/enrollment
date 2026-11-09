import type jsPDF from 'jspdf';
import autoTable from 'jspdf-autotable';
import { LAVENDER, type DocumentPalette } from './documentTheme';
import { substitute } from './fields';
import type { BlockStyle, IssuedTemplate, TextRun } from './types';

type Kind = 'paragraph' | 'heading' | 'subheading' | 'title' | 'declaration' | 'list' | 'signature-detail';
type Colour = readonly [number, number, number];
type Piece = { text: string; width: number; size: number; bold: boolean; italic: boolean; underline: boolean };
export interface PdfBounds { x: number; width: number; top: number; bottom: number; getY: () => number; setY: (y: number) => void; page: () => void; room: (height: number) => void }

/** Presentation only. Native rows, answers, calculations and signing data are
 * resolved by the existing managed renderer; this paints those same values. */
export function lavenderPainter(doc: jsPDF, template: IssuedTemplate, b: PdfBounds, latin: (v: unknown) => string, T: DocumentPalette = LAVENDER) {
  const rgb = (value: Colour): [number, number, number] => [...value];
  const font = (p: Piece, colour: Colour = T.text) => doc.setFont('helvetica', p.bold && p.italic ? 'bolditalic' : p.bold ? 'bold' : p.italic ? 'italic' : 'normal').setFontSize(p.size).setTextColor(...rgb(colour));
  const lineHeight = (size: number) => size * .352778 * T.lineHeight;

  function text(runs: TextRun[], style: BlockStyle = {}, kind: Kind = 'paragraph', prefix = '', interpolate = true) {
    const decorated = kind === 'heading' || kind === 'declaration';
    const inset = decorated || kind === 'signature-detail' ? T.padding : 0;
    const maxWidth = b.width - inset * 2;
    const colour = kind === 'title' || kind === 'heading' ? T.heading : kind === 'subheading' ? T.secondary : T.text;
    const defaultSize = kind === 'title' ? T.font.title : kind === 'heading' ? T.font.section : kind === 'subheading' ? T.font.subheading : T.font.body;
    const baseSize = style.fontSize ?? defaultSize;
    const lines: Piece[][] = []; let line: Piece[] = [], width = 0;
    const prefixPiece: Piece = { text: prefix, width: 0, size: baseSize, bold: style.bold ?? false, italic: !!style.italic, underline: false };
    font(prefixPiece); const indent = prefix ? doc.getTextWidth(prefix) : 0;
    const flush = () => { lines.push(line); line = []; width = 0; };
    const add = (part: Piece) => {
      const limit = maxWidth - (lines.length ? indent : 0);
      if (width + part.width > limit && line.length) flush();
      if (!line.length && /^\s+$/.test(part.text)) return;
      const previous = line.at(-1);
      if (previous && previous.size === part.size && previous.bold === part.bold && previous.italic === part.italic && previous.underline === part.underline) { previous.text += part.text; previous.width += part.width; }
      else line.push({ ...part });
      width += part.width;
    };
    for (const run of [...(prefix ? [{ text: prefix }] : []), ...runs]) {
      const p: Piece = { text: '', width: 0, size: run.fontSize ?? baseSize, bold: run.bold ?? style.bold ?? false, italic: run.italic ?? style.italic ?? false, underline: run.underline ?? style.underline ?? false };
      const value = latin(interpolate ? substitute(run.text, template) : run.text);
      for (const token of value.split(/(\n|[^\S\n]+|[^\s]+)/).filter(Boolean)) {
        if (token === '\n') { flush(); continue; }
        font(p); const tokenWidth = doc.getTextWidth(token);
        if (tokenWidth <= maxWidth - indent) add({ ...p, text: token, width: tokenWidth });
        else for (const char of token) { font(p); add({ ...p, text: char, width: doc.getTextWidth(char) }); }
      }
    }
    if (line.length) flush();
    const heights = lines.map((parts) => lineHeight(Math.max(baseSize, ...parts.map(p => p.size))));
    if (kind === 'heading' || kind === 'title' || kind === 'subheading') b.room(Math.min(heights.reduce((a, c) => a + c, 0) + inset * 2 + (style.spaceAfter ?? 5) + (kind === 'heading' ? 30 : 12), b.bottom - b.top));
    if (decorated) { b.room(heights[0] + inset * 2); b.setY(b.getY() + inset); }
    lines.forEach((parts, index) => {
      const height = heights[index];
      b.room(height + (decorated ? inset : 0));
      const top = b.getY();
      if (decorated) {
        doc.setFillColor(...rgb(kind === 'heading' ? T.section : T.label)).rect(b.x, top - (index === 0 ? inset : 0), b.width, height + (index === 0 ? inset : 0) + (index === lines.length - 1 ? inset : 0), 'F');
        doc.setDrawColor(...rgb(T.border)).setLineWidth(.6).line(b.x, top - (index === 0 ? inset : 0), b.x, top + height + (index === lines.length - 1 ? inset : 0));
      }
      const total = parts.reduce((sum, part) => sum + part.width, 0);
      const hanging = index ? indent : 0;
      let x = b.x + inset + hanging + (style.align === 'center' ? (maxWidth - hanging - total) / 2 : style.align === 'right' ? maxWidth - hanging - total : 0);
      const baseline = top + Math.max(baseSize, ...parts.map(p => p.size)) * .352778 * .85;
      for (const part of parts) {
        font(part, colour); doc.text(part.text, x, baseline);
        if (part.underline) doc.setDrawColor(...rgb(colour)).setLineWidth(.18).line(x, baseline + .6, x + part.width, baseline + .6);
        x += part.width;
      }
      b.setY(top + height);
    });
    if (decorated) b.setY(b.getY() + inset);
    if (kind === 'title') { b.room(2); doc.setDrawColor(...rgb(T.border)).setLineWidth(.5).line(b.x, b.getY() + 2, b.x + b.width, b.getY() + 2); b.setY(b.getY() + 3); }
    b.setY(b.getY() + (style.spaceAfter ?? (kind === 'heading' || kind === 'title' ? 5 : 3)));
  }

  function table(columns: string[], rows: unknown[][], interpolate = true) {
    b.room(18);
    autoTable(doc, {
      startY: b.getY(), head: [columns.map(v => latin(substitute(v, template)))],
      body: rows.map(row => row.map(v => latin(interpolate ? substitute(String(v ?? ''), template) : v))),
      margin: { left: b.x, right: 210 - b.x - b.width, top: b.top, bottom: 297 - b.bottom },
      tableWidth: b.width, theme: 'grid', rowPageBreak: 'avoid',
      styles: { font: 'helvetica', fontSize: T.font.table, textColor: rgb(T.text), fillColor: rgb(T.white), cellPadding: T.tablePadding, overflow: 'linebreak', valign: 'top', lineColor: rgb(T.border), lineWidth: .18 },
      headStyles: { fillColor: rgb(T.section), textColor: rgb(T.heading), fontStyle: 'bold', cellPadding: 2.8 },
      ...(columns.length === 2 ? { columnStyles: { 0: { fillColor: rgb(T.label), cellWidth: b.width * .38 } } } : {}),
    });
    b.setY((doc as jsPDF & { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 4);
  }

  function signatureHeight(label: string, state: Record<string, any>, provenance?: Record<string, any>) {
    const name = `Name: ${state.name || ''}${state.position ? ` | Position: ${state.position}` : ''}`;
    const date = `Date: ${state.signedAt ? String(state.signedAt).slice(0, 10).split('-').reverse().join('/') : ''}`;
    const history = provenance ? `Historical signature reproduced from Revision R${provenance.sourceRevision}. Signed by ${provenance.name || state.name || ''} on ${String(provenance.signedAt || '').slice(0, 10)}. This signature records approval of that earlier revision; it does not record approval of this revised wording.` : '';
    doc.setFont('helvetica','normal').setFontSize(8.5);
    const nameLines = doc.splitTextToSize(latin(name), b.width - 6) as string[];
    const historyLines = history ? doc.splitTextToSize(latin(history), b.width - 6) as string[] : [];
    doc.setFont('helvetica','bold').setFontSize(10.5);
    const labels = doc.splitTextToSize(latin(substitute(label, template)), b.width - 6) as string[];
    const labelHeight = labels.length * lineHeight(10.5) + 4;
    const height = labelHeight + 26 + (nameLines.length + 1 + historyLines.length) * lineHeight(8.5) + 6;
    return { name, date, history, labelHeight, height };
  }

  function signature(label: string, state: Record<string, any>, mark: unknown, provenance?: Record<string, any>) {
    const { name, date, history, labelHeight, height } = signatureHeight(label, state, provenance);
    // A pathological signatory name still flows as full text, never clips a panel.
    const panel = height <= b.bottom - b.top;
    if (panel) b.room(height);
    else b.room(Math.min(labelHeight + 29, b.bottom - b.top));
    const top = b.getY();
    if (panel) doc.setDrawColor(...rgb(T.border)).setLineWidth(.2).roundedRect(b.x,top,b.width,height,1.5,1.5,'S');
    text([{text:label}],{fontSize:10.5,bold:true,spaceAfter:2},'heading');
    const imageTop=b.getY(); b.room(22);
    if (state.signed && typeof mark === 'string' && mark.startsWith('data:image/')) {
      try {
        const image=doc.getImageProperties(mark); const scale=Math.min(70/image.width,18/image.height);
        doc.addImage(mark,image.fileType,b.x+3,b.getY(),image.width*scale,image.height*scale);
      } catch { /* Preserve existing corrupt-image fallback without inventing a mark. */ }
    }
    b.setY(Math.max(b.getY(),imageTop)+20);
    doc.setDrawColor(...rgb(T.border)).setLineWidth(.2).line(b.x+3,b.getY(),b.x+Math.min(80,b.width-3),b.getY()); b.setY(b.getY()+2);
    text([{text:name}],{fontSize:8.5,spaceAfter:2},'signature-detail','',false);
    text([{text:date}],{fontSize:8.5,spaceAfter:2},'signature-detail','',false);
    if (history) text([{text:history}],{fontSize:8,italic:true,spaceAfter:3},'signature-detail','',false);
    b.setY(Math.max(b.getY(),panel?top+height:b.getY())+4);
  }

  function finish() {
    const layout=template.content.pageLayout;
    const total=doc.getNumberOfPages();
    for (let page=1;page<=total;page++) {
      doc.setPage(page);
      doc.setDrawColor(...rgb(T.border)).setLineWidth(.3).line(b.x,Math.max(7,b.top-6),b.x+b.width,Math.max(7,b.top-6));
      doc.setFont('helvetica','normal').setFontSize(T.font.caption).setTextColor(...rgb(T.muted));
      const header=layout ? layout.header : page>1 ? template.content.title : '';
      if (header) {
        const lines=doc.splitTextToSize(latin(substitute(header,template)),b.width) as string[];
        doc.text(lines,b.x,Math.max(5,b.top-8-(lines.length-1)*lineHeight(T.font.caption)),{lineHeightFactor:T.lineHeight});
      }
      const footerY=Math.min(291,b.bottom+9);
      doc.setDrawColor(...rgb(T.border)).line(b.x,footerY-4,b.x+b.width,footerY-4);
      if (layout?.footer) doc.text(doc.splitTextToSize(latin(substitute(layout.footer,template)),b.width-22),b.x,footerY,{lineHeightFactor:T.lineHeight});
      if (layout?.pageNumbers ?? true) doc.text(`${page} / ${total}`,b.x+b.width,footerY,{align:'right'});
    }
  }
  return { text, table, signature, signatureHeight, finish };
}
