import { jsPDF } from 'jspdf';
import autoTable from 'jspdf-autotable';
import type { MonthDetail, Signature, JournalSummary } from './api';
import { monthLabel } from './report';

type Color = [number, number, number];
export type PdfImage = { data: Uint8Array; format: 'PNG' | 'JPEG' };
export type JournalPdfAssets = { logo: PdfImage; signatures: Map<string, PdfImage> };
const colors = {
  navy: [24, 45, 72] as Color, purple: [103, 58, 183] as Color,
  green: [22, 120, 83] as Color, red: [185, 55, 55] as Color,
  muted: [99, 115, 136] as Color, border: [222, 226, 232] as Color,
  soft: [246, 248, 251] as Color, white: [255, 255, 255] as Color,
};
const margin = 12;
const contentWidth = 273;

function text(value: string | null | undefined) {
  return value?.trim().replace(/[\u2010-\u2015\u2212]/g, '-').replace(/[\u2018\u2019]/g, "'").replace(/[\u201c\u201d]/g, '"') || '-';
}

function numericHours(value: number | string | null | undefined) {
  if (value == null || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export function pdfDuration(value: number | string | null | undefined) {
  const number = numericHours(value);
  if (number === null) return '-';
  const minutes = Math.round(Math.abs(number) * 60);
  return `${number < 0 && minutes ? '-' : ''}${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m`;
}

function date(value?: string | null, numeric = false) {
  const iso = value?.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return iso ? numeric ? `${iso[3]}/${iso[2]}/${iso[1]}` : `${iso[1]}-${iso[2]}-${iso[3]}` : '-';
}

function font(doc: jsPDF, size: number, bold = false, color = colors.navy) {
  doc.setFont('helvetica', bold ? 'bold' : 'normal');
  doc.setFontSize(size);
  doc.setTextColor(...color);
}

function image(doc: jsPDF, asset: PdfImage, x: number, y: number, width: number, height: number) {
  const dimensions = doc.getImageProperties(asset.data);
  const ratio = Math.min(width / dimensions.width, height / dimensions.height);
  const drawnWidth = dimensions.width * ratio;
  const drawnHeight = dimensions.height * ratio;
  doc.addImage(asset.data, asset.format, x + (width - drawnWidth) / 2, y + (height - drawnHeight) / 2, drawnWidth, drawnHeight);
}

function header(doc: jsPDF, report: MonthDetail, assets: JournalPdfAssets, subtitle = 'Monthly off-the-job learning record') {
  image(doc, assets.logo, 12, 8, 38, 18);
  doc.setDrawColor(...colors.border);
  doc.setLineWidth(.3);
  doc.line(56, 8, 56, 26);
  font(doc, 16, true);
  doc.text('Learner Journal', 63, 13.5);
  font(doc, 8.5, false, colors.muted);
  doc.text(subtitle, 63, 20);
  // The legacy feed has no report-level learning topic. Do not substitute an
  // unrelated activity or programme name for that field from the reference.
  doc.setFillColor(...colors.soft);
  doc.roundedRect(242, 9, 43, 15, 3, 3, 'F');
  font(doc, 6.5, true, colors.muted);
  doc.text('REPORTING MONTH', 263.5, 14, { align: 'center' });
  font(doc, 9.5, true);
  doc.text(monthLabel(report.month), 263.5, 20.5, { align: 'center' });
  doc.line(margin, 31, 285, 31);
}

function profile(doc: jsPDF, learner: NonNullable<JournalSummary['learner']>, report: MonthDetail) {
  const fields = [
    ['Learner', learner.name, 'Start date', date(report.profile?.start_date)],
    ['Programme', learner.programme, 'First evidence', date(report.profile?.first_evidence_date)],
    ['Coach', learner.coach_name, 'Planned end', date(report.profile?.planned_end_date)],
  ];
  font(doc, 8.5, true);
  const lines = fields.map(field => doc.splitTextToSize(text(field[1]), 78) as string[]);
  const valueHeight = Math.max(...lines.map(value => value.length)) * 4;
  const height = Math.max(29, valueHeight + 24);
  doc.setFillColor(...colors.soft);
  doc.roundedRect(margin, 35, contentWidth, height, 2.5, 2.5, 'F');
  fields.forEach((field, index) => {
    const x = 18 + index * 91;
    if (index) { doc.setDrawColor(...colors.border); doc.line(x - 6, 39, x - 6, 35 + height - 4); }
    font(doc, 6.5, true, colors.muted); doc.text(field[0].toUpperCase(), x, 41);
    font(doc, 8.5, true); doc.text(lines[index], x, 47, { lineHeightFactor: 1.3 });
    font(doc, 6.5, true, colors.muted); doc.text(field[2].toUpperCase(), x, 50 + valueHeight);
    font(doc, 8.5, true); doc.text(field[3], x, 56 + valueHeight);
  });
  return 35 + height;
}

function metrics(doc: jsPDF, report: MonthDetail, y: number) {
  const target = numericHours(report.training_plan_target);
  const actual = numericHours(report.actual_hours);
  const variance = actual == null || target == null ? null : actual - target;
  const items = [
    { label: 'Training plan hours', value: target, color: colors.navy },
    { label: 'Actual hours - monthly activity logs', value: actual, color: colors.purple },
    { label: 'Not accepted', value: report.not_accepted_hours, color: colors.red },
    { label: 'Variance', value: variance, color: variance != null && variance < 0 ? colors.red : colors.green },
  ];
  items.forEach((item, index) => {
    const x = margin + index * 69.5;
    doc.setFillColor(...colors.soft); doc.roundedRect(x, y, 64.5, 18, 2, 2, 'F');
    doc.setFillColor(...item.color); doc.roundedRect(x, y, 2.2, 18, 1, 1, 'F');
    font(doc, 6.3, true, colors.muted); doc.text(item.label.toUpperCase(), x + 6, y + 6, { maxWidth: 55 });
    font(doc, 12, true); doc.text(pdfDuration(item.value), x + 6, y + 14);
  });
}

function continuedHeader(doc: jsPDF, learner: NonNullable<JournalSummary['learner']>, report: MonthDetail, assets: JournalPdfAssets) {
  image(doc, assets.logo, 12, 7, 25, 11.5);
  font(doc, 8.5, true);
  doc.text(text(learner.name), 44, 12, { maxWidth: 190 });
  font(doc, 7, false, colors.muted);
  doc.text(`Activity log continued - ${monthLabel(report.month)}`, 44, 18);
  doc.setDrawColor(...colors.border); doc.line(margin, 23, 285, 23);
}

function activityLog(doc: jsPDF, report: MonthDetail, learner: NonNullable<JournalSummary['learner']>, assets: JournalPdfAssets, y: number) {
  font(doc, 11.5, true); doc.text('Activity log', margin, y);
  font(doc, 7.5, false, colors.muted);
  doc.text('Recorded off-the-job learning time and whether it is accepted', margin, y + 5);
  autoTable(doc, {
    startY: y + 9, margin: { left: margin, right: margin, top: 28, bottom: 18 },
    theme: 'plain', showHead: 'everyPage', showFoot: 'lastPage', rowPageBreak: 'avoid',
    head: [['Date', 'Activity ID', 'Activity details', 'KSB', 'Type', 'Time', 'Actual', 'Accepted']],
    body: report.rows.length ? report.rows.map(row => [
      date(row.activity_date, true), text(row.source_ref || String(row.id)), text(row.title),
      row.ksb_codes?.join(', ') || '-', text(row.category), text(row.timestamp_label || row.activity_time),
      pdfDuration(row.actual_hours), row.accepted ? 'Yes' : 'No',
    ]) : [[{ content: 'No activities recorded for this month.', colSpan: 8 }]],
    foot: [
      [{ content: 'Accepted total', colSpan: 6, styles: { halign: 'right' } }, pdfDuration(report.actual_hours), ''],
      [{ content: 'Not accepted total', colSpan: 6, styles: { halign: 'right' } }, pdfDuration(report.not_accepted_hours), ''],
    ],
    styles: { font: 'helvetica', fontSize: 7.3, minCellHeight: 8, cellPadding: { top: 2.5, right: 2.4, bottom: 2.5, left: 2.4 },
      lineWidth: 0, overflow: 'linebreak', valign: 'middle', textColor: colors.navy },
    headStyles: { fillColor: colors.navy, textColor: colors.white, fontStyle: 'bold', fontSize: 7, minCellHeight: 10 },
    footStyles: { fillColor: [238, 243, 246], textColor: colors.navy, fontStyle: 'bold', fontSize: 7.2 },
    alternateRowStyles: { fillColor: [247, 249, 252] },
    columnStyles: {
      0: { cellWidth: 22 }, 1: { cellWidth: 28 }, 2: { cellWidth: 98 }, 3: { cellWidth: 23 },
      4: { cellWidth: 29 }, 5: { cellWidth: 37, halign: 'center' },
      6: { cellWidth: 19, halign: 'right', fontStyle: 'bold' }, 7: { cellWidth: 17, halign: 'center' },
    },
    didParseCell: cell => {
      if (cell.section === 'body' && cell.column.index === 7) {
        cell.cell.styles.textColor = cell.cell.raw === 'No' ? colors.red : colors.green;
        cell.cell.styles.fontStyle = 'bold';
      }
    },
    didDrawPage: page => { if (page.pageNumber > 1) continuedHeader(doc, learner, report, assets); },
  });
}

function signoff(doc: jsPDF, report: MonthDetail, learner: NonNullable<JournalSummary['learner']>, assets: JournalPdfAssets) {
  doc.addPage();
  header(doc, report, assets, 'Monthly record sign-off');
  font(doc, 11.5, true); doc.text('Report sign-off', margin, 42);
  font(doc, 8, false, colors.muted);
  doc.text('Saved learner and coach signatures for this monthly learning record.', margin, 48);
  const roles: { role: string; name: string; signature: Signature | null }[] = [
    { role: 'Learner', name: learner.name, signature: report.student_signature },
    { role: 'Coach', name: learner.coach_name, signature: report.coach_signature },
  ];
  autoTable(doc, {
    startY: 55, margin: { left: margin, right: margin, bottom: 18 }, theme: 'grid',
    head: [['Role', 'Signature', 'Print name', 'Date', 'Status']],
    body: roles.map(row => [row.role, row.signature ? '' : 'No saved signature', text(row.signature?.signer_name || row.name),
      date(row.signature?.signed_at), row.signature ? 'Signed' : 'Awaiting signature']),
    styles: { font: 'helvetica', fontSize: 8, cellPadding: 5, lineColor: colors.border, lineWidth: .2, valign: 'middle', overflow: 'linebreak', textColor: colors.navy },
    headStyles: { fillColor: colors.soft, textColor: colors.navy, fontStyle: 'bold', fontSize: 7 },
    bodyStyles: { minCellHeight: 35 },
    columnStyles: { 0: { cellWidth: 25 }, 1: { cellWidth: 108 }, 2: { cellWidth: 68 }, 3: { cellWidth: 32 }, 4: { cellWidth: 40 } },
    didParseCell: cell => {
      if (cell.section === 'body' && cell.column.index === 4) cell.cell.styles.textColor = roles[cell.row.index].signature ? colors.green : colors.muted;
    },
    didDrawCell: cell => {
      if (cell.section !== 'body' || cell.column.index !== 1) return;
      const signature = roles[cell.row.index].signature;
      const asset = signature && assets.signatures.get(signature.url);
      if (asset) image(doc, asset, cell.cell.x + 6, cell.cell.y + 5, cell.cell.width - 12, cell.cell.height - 10);
    },
  });
}

/** Builds searchable, paginated pages from the saved monthly record, never a
 * screenshot of the UI or an unsaved signature draft. */
export function buildJournalPdf(summary: JournalSummary, reports: MonthDetail[], assets: JournalPdfAssets) {
  if (!summary.learner || !reports.length) throw new Error('No learner reports are available to download.');
  for (const report of reports) {
    for (const signature of [report.student_signature, report.coach_signature]) {
      if (signature && !assets.signatures.has(signature.url)) throw new Error('A saved signature is missing from the PDF. Please try again.');
    }
  }
  const doc = new jsPDF({ orientation: 'landscape', unit: 'mm', format: 'a4', compress: true });
  doc.setProperties({ title: `Learner Journal - ${summary.learner.name}`, author: 'Kent Business College', subject: 'Monthly off-the-job learning record' });
  for (const [index, report] of [...reports].sort((a, b) => a.month.localeCompare(b.month)).entries()) {
    if (index) doc.addPage();
    header(doc, report, assets);
    const profileEnd = profile(doc, summary.learner, report);
    metrics(doc, report, profileEnd + 5);
    activityLog(doc, report, summary.learner, assets, profileEnd + 33);
    signoff(doc, report, summary.learner, assets);
  }
  const pageCount = doc.getNumberOfPages();
  for (let page = 1; page <= pageCount; page++) {
    doc.setPage(page);
    doc.setDrawColor(...colors.border); doc.line(margin, 198, 285, 198);
    font(doc, 6.8, false, colors.muted);
    doc.text('Kent Business College  |  Off-the-job learning record', margin, 203);
    doc.text(`Page ${page} of ${pageCount}`, 285, 203, { align: 'right' });
  }
  return doc;
}
