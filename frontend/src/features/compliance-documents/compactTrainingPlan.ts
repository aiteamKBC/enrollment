/** Versioned presentation in the existing document pipeline. No source reads,
 * plan writes, allocation, or OTJ business calculations run in the renderer. */
import type jsPDF from 'jspdf';
import autoTable, { type RowInput } from 'jspdf-autotable';
import type { PdfBounds } from './lavenderPdf';
import { formatHoursMinutes } from '@/lib/format';
import type { ActivitiesOverview, Activity, Module } from './trainingPlanActivities';

const purple: [number, number, number] = [101, 32, 127];
const lavender: [number, number, number] = [226, 198, 240];
const border: [number, number, number] = [173, 131, 191];
const ink: [number, number, number] = [27, 17, 35];
const plain = (v: unknown) => String(v ?? '').replace(/<[^>]*>/g, '').replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&');
const escape = (v: unknown) => plain(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const hours = (v: number | null | undefined) => v == null ? '—' : formatHoursMinutes(v);
const date = (v?: string | null) => /^\d{4}-\d{2}-\d{2}$/.test(v || '') ? new Intl.DateTimeFormat('en-GB',
  { day:'2-digit', month:'short', year:'numeric', timeZone:'UTC' }).format(new Date(v + 'T00:00:00Z')) : '—';
const scheduled = (a: Activity) => `${date(a.date)}${a.scheduleKind ? ` (${a.scheduleKind})` : ''}`;
const unique = <T extends {id: string}>(rows: T[]): T[] => [...new Map(rows.map(r => [r.id, r])).values()];
export interface CompactRow { id: string; prefix: string; title: string; hours: number | null; when: string; coaching?: boolean }
const coachingRow = (a: Activity): CompactRow => ({id:a.id, prefix:'', title:'Monthly Coaching Meeting (MCS)',
  hours:a.hours, when:scheduled(a), coaching:true});

/** Preserve source week order and stable IDs. A coaching occurrence is placed
 * only using its captured verified Week FK, never a title/ordinal guess. */
export function compactModuleRows(module: Module): CompactRow[] {
  const meetings = unique(module.activities.filter(a => a.kind === 'meeting' && a.reviewTypeCode === 'mcm'));
  return (module.weekSummaries || []).flatMap(week => {
    const matches = meetings.filter(a => a.weekId === week.id).sort((a,b) => (a.date || '').localeCompare(b.date || '') || a.id.localeCompare(b.id));
    const before = week.kind === 'assignment' && week.dueDates.length ?
      matches.filter(a => a.date && a.date < week.dueDates[0]) : [];
    const beforeIds = new Set(before.map(a => a.id));
    return [...before.map(coachingRow), {id:week.id, prefix:week.kind === 'assignment' ? 'Assignment:' : 'Topic:',
      title:week.title || 'Title not recorded / unavailable', hours:week.hours,
      when:week.kind === 'assignment' && week.dueDates.length ? week.dueDates.map(date).join(' / ') : '—'},
    ...matches.filter(a => !beforeIds.has(a.id)).map(coachingRow)];
  });
}
const progress = (v: ActivitiesOverview) => unique(v.reviews.filter(r => r.reviewTypeCode === 'progress_review'))
  .sort((a,b) => a.date.localeCompare(b.date) || a.id.localeCompare(b.id));
// Find real occurrences without a verified Week for presentation in the final
// module table. Source module/Week relationships and occurrence dates stay intact.
const pendingCoaching = (v: ActivitiesOverview) => {
  const placed = new Set(v.modules.flatMap(m => compactModuleRows(m).filter(r => r.coaching).map(r => r.id)));
  return unique([...(v.unassignedMeetings || []), ...v.modules.flatMap(m => m.activities)]
    .filter(a => a.reviewTypeCode === 'mcm' && !placed.has(a.id)))
    .sort((a,b) => (a.date || '').localeCompare(b.date || '') || a.id.localeCompare(b.id));
};
const displayedModuleRows = (v: ActivitiesOverview, module: Module, index: number) => [
  ...compactModuleRows(module),
  ...(index === v.modules.length - 1 ? pendingCoaching(v).map(coachingRow) : []),
];
const overview = (v: ActivitiesOverview) => [['Programme:',v.programme], ['Planned OTJ Hours:',hours(v.totals.totalHours)],
  ['Programme Dates:',`${date(v.startDate)} – ${date(v.endDate)}`], ['Delivery Responsibility:',v.deliveryResponsibility]];
const moduleHeading = (m: Module, i: number) => `${i+1}. Module: ${m.title} | Planned OTJ Hours: ${hours(m.hours)} | ${date(m.startDate)} – ${date(m.endDate)}`;
const columns = ['Learning Activity', 'Planned OTJ Hours', 'Planned Date / Month'];
const reviewColumns = ['Review Type', 'Planned Date', 'Required Signatories / Participants'];
const note = 'Week hours summarise recorded Components for reference only. Module and meeting OTJ totals remain unchanged; meeting hours are counted once. Projected dates are planned occurrences, not booked appointments.';

export function compactTrainingPlanHtml(v: ActivitiesOverview): string {
  const table = (head: string[] | null, rows: string[][], cls: string) => `<table class="${cls}">${head ?
    `<thead><tr>${head.map(c => `<th>${escape(c)}</th>`).join('')}</tr></thead>` : ''}<tbody>${rows.map(r =>
    `<tr>${r.map(c => `<td>${escape(c)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
  return '<div data-training-plan-summary="v6">' + table(null, overview(v), 'tpc-overview') + v.modules.map((m,i) =>
    `<div class="tpc-module-title" role="heading" aria-level="2">${escape(moduleHeading(m,i))}</div>` +
    `<table class="tpc-details"><tbody><tr><td><strong>Module Description:</strong><br>${escape(m.description || 'Not recorded / unavailable')}</td><td><strong>Main Covered KSBs:</strong><br>${escape(m.coveredKsbs.map(k => k.code).join(', ') || 'Not recorded / unavailable')}</td></tr></tbody></table>` +
    `<table class="tpc-activities"><colgroup><col style="width:61%"><col style="width:16%"><col style="width:23%"></colgroup><thead><tr>${columns.map(c => `<th>${escape(c)}</th>`).join('')}</tr></thead><tbody>` +
    displayedModuleRows(v,m,i).map(r => `<tr data-week-or-meeting-id="${escape(r.id)}"><td>${r.coaching ? `<strong>${escape(r.title)}</strong>` :
      `<strong>${escape(r.prefix)}</strong> ${escape(r.title)}`}</td><td>${escape(hours(r.hours))}</td><td>${escape(r.when)}</td></tr>`).join('') +
    (!(m.weekSummaries || []).length ? '<tr><td colspan="3">Verified Week summaries unavailable.</td></tr>' : '') + '</tbody></table>').join('') +
    '<div class="tpc-section-title" role="heading" aria-level="2">Reviews Schedule</div>' +
    table(reviewColumns, progress(v).length ? progress(v).map(r => [r.title,scheduled(r),r.participants || 'Not recorded / unavailable']) :
      [['No verified Progress Reviews available.','—','—']], 'tpc-reviews') +
    table(['Modules OTJ','Meetings OTJ','Plan Total'], [[hours(v.totals.moduleHours),hours(v.totals.meetingHours),hours(v.totals.totalHours)]], 'tpc-totals') +
    `<p class="tpc-note">${escape(note)}</p></div>`;
}

/** One table per module: AutoTable repeats headers and wraps complete rows on
 * A4. Rich prefixes are drawn explicitly; body titles stay normal weight. */
export function drawCompactTrainingPlan(doc: jsPDF, b: PdfBounds, v: ActivitiesOverview,
  latin: (v: unknown) => string, decorate: () => void) {
  function richLines(row: CompactRow, width: number): string[] {
    doc.setFont('helvetica','bold').setFontSize(9);
    const prefix = latin(row.prefix + ' '), indent = doc.getTextWidth(prefix);
    doc.setFont('helvetica','normal');
    const words = latin(row.title).split(/\s+/), lines: string[] = [];
    let line = '';
    for (const word of words) {
      const candidate = line ? line + ' ' + word : word;
      if (doc.getTextWidth(candidate) <= width - (lines.length ? 0 : indent)) { line = candidate; continue; }
      if (line) { lines.push(line); line = ''; }
      // Never clip an unbroken long title token.
      const pieces: string[] = doc.splitTextToSize(word, width - (lines.length ? 0 : indent));
      lines.push(...pieces.slice(0,-1)); line = pieces.at(-1) || '';
    }
    if (line || !lines.length) lines.push(line);
    return lines;
  }
  const table = (head: string[] | null, rows: RowInput[], widths: number[], opts: {activityRows?: CompactRow[]; dark?: boolean; overview?: boolean; details?: boolean; coaching?: boolean} = {}) => {
    autoTable(doc, {startY:b.getY(), head:head ? [head.map(latin)] : undefined, body:rows,
      tableWidth:b.width, theme:'grid', showHead:'everyPage', rowPageBreak:'avoid',
      margin:{left:b.x, right:210-b.x-b.width, top:b.top, bottom:297-b.bottom},
      styles:{font:'helvetica',fontSize:9,cellPadding:1.25,overflow:'linebreak',valign:'top',
        textColor:opts.dark ? [255,255,255] : ink,fillColor:opts.dark ? purple : [255,255,255],
        lineColor:border,lineWidth:.17,fontStyle:opts.dark ? 'bold' : 'normal'},
      headStyles:{fillColor:lavender,textColor:purple,fontStyle:'bold',fontSize:9.5},
      columnStyles:Object.fromEntries(widths.map((w,i) => [i,{cellWidth:b.width*w}])),
      willDrawPage:decorate,
      didParseCell:data => {
        if (opts.dark) Object.assign(data.cell.styles,{fontSize:10,cellPadding:2});
        if (opts.overview && data.column.index === 0) Object.assign(data.cell.styles,{fillColor:lavender,fontStyle:'bold',textColor:purple});
        if (opts.details) Object.assign(data.cell.styles,{cellPadding:2});
        if (opts.coaching && data.section === 'body' && data.column.index === 0) data.cell.styles.fontStyle='bold';
        if (!opts.activityRows) return;
        if (data.column.index > 0) data.cell.styles.halign='center';
        if (data.section !== 'body' || data.column.index !== 0) return;
        const row=opts.activityRows[data.row.index];
        if (!row) return;
        if (row.coaching) data.cell.styles.fontStyle='bold';
        else data.cell.text=richLines(row,b.width*widths[0]-2.5).map((line,i) => i === 0 ? latin(row.prefix)+' '+line : line);
      },
      willDrawCell:data => {
        const row=opts.activityRows?.[data.row.index];
        if (row && !row.coaching && data.section === 'body' && data.column.index === 0) data.cell.text=[];
        if (opts.details && data.section === 'body') data.cell.text=[];
      },
      didDrawCell:data => {
        if (opts.details && data.section === 'body') {
          const [heading,...content]=String(data.cell.raw).split('\n');
          const x=data.cell.x+2, y=data.cell.y+2+9*.352778*.8;
          doc.setTextColor(...ink).setFont('helvetica','bold').setFontSize(9).text(heading,x,y);
          doc.setFont('helvetica','normal');
          const lines:string[]=doc.splitTextToSize(content.join('\n'),data.cell.width-4);
          lines.forEach((line,i)=>doc.text(line,x,y+(i+1)*9*.352778*1.15));
        }
        const row=opts.activityRows?.[data.row.index];
        if (!row || row.coaching || data.section !== 'body' || data.column.index !== 0) return;
        const lines=richLines(row,data.cell.width-2.5), x=data.cell.x+1.25, y=data.cell.y+1.25+9*.352778*.8;
        doc.setTextColor(...ink).setFontSize(9).setFont('helvetica','bold');
        const prefix=latin(row.prefix)+' ', offset=doc.getTextWidth(prefix);
        doc.text(prefix,x,y); doc.setFont('helvetica','normal');
        lines.forEach((line,i) => doc.text(line,x+(i===0 ? offset : 0),y+i*9*.352778*1.15));
      },
    });
    b.setY((doc as jsPDF & {lastAutoTable:{finalY:number}}).lastAutoTable.finalY+2.8);
  };
  const cells = (rows: string[][]): RowInput[] => rows.map(r => r.map(latin));
  const section = (title: string) => { b.room(18); table(null,cells([[title]]),[1],{overview:true}); };
  b.room(25); table(null,cells(overview(v)),[.29,.71],{overview:true});
  for (const [i,m] of v.modules.entries()) {
    doc.setFont('helvetica','normal').setFontSize(9);
    const detailLines=Math.max(doc.splitTextToSize(latin('Module Description: '+(m.description || 'Not recorded / unavailable')),b.width*.62-4).length,
      doc.splitTextToSize(latin('Main Covered KSBs: '+m.coveredKsbs.map(k=>k.code).join(', ')),b.width*.38-4).length);
    b.room(Math.min(b.bottom-b.top,detailLines*3.7+35));
    table(null,cells([[moduleHeading(m,i)]]),[1],{dark:true});
    table(null,cells([[`Module Description:\n${plain(m.description) || 'Not recorded / unavailable'}`,
      `Main Covered KSBs:\n${m.coveredKsbs.map(k=>k.code).join(', ') || 'Not recorded / unavailable'}`]]),[.62,.38],{details:true});
    const rows=displayedModuleRows(v,m,i);
    table(columns,rows.length ? cells(rows.map(r=>[(r.prefix ? r.prefix+' ' : '')+r.title,hours(r.hours),r.when])) :
      [[{content:'Verified Week summaries unavailable.',colSpan:3}]],[.61,.16,.23],{activityRows:rows});
  }
  section('Reviews Schedule');
  table(reviewColumns,cells(progress(v).length ? progress(v).map(r=>[r.title,scheduled(r),r.participants || 'Not recorded / unavailable']) :
    [['No verified Progress Reviews available.','—','—']]),[.34,.28,.38]);
  b.room(20); table(['Modules OTJ','Meetings OTJ','Plan Total'],cells([[hours(v.totals.moduleHours),hours(v.totals.meetingHours),hours(v.totals.totalHours)]]),[.34,.33,.33]);
  table(null,cells([[note]]),[1]);
}
