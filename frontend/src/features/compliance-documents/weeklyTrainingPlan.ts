import type jsPDF from 'jspdf';
import autoTable, { type CellInput, type RowInput } from 'jspdf-autotable';
import type { PdfBounds } from './lavenderPdf';
import { BRANDED_PURPLE as T } from './brandedPurpleTheme';
import { formatHoursMinutes } from '@/lib/format';
import type { ActivitiesOverview, Activity, Module, PlanWeek } from './trainingPlanActivities';

const kind = (a: Activity) => (a.kind || '').toLowerCase().replaceAll('-', '_');
const resourceTypes = new Set(['powerpoint', 'ppt', 'presentation', 'video', 'podcast']);
const rgb = (c: readonly number[]): [number, number, number] => [c[0], c[1], c[2]];
const clean = (v: unknown) => String(v ?? '').replace(/<[^>]*>/g, '').replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&');
const hours = (v: number | null | undefined) => v == null ? '—' : formatHoursMinutes(v);
const date = (v?: string | null) => /^\d{4}-\d{2}-\d{2}$/.test(v || '') ?
  new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(new Date(v + 'T00:00:00Z')) : '—';
const label = (a: Activity) => {
  const type = kind(a);
  const names: Record<string, string> = { live_session: 'Live session', powerpoint: 'PowerPoint', ppt: 'PowerPoint',
    presentation: 'PowerPoint', video: 'Video', podcast: 'Podcast', reading: 'Reading', quiz: 'Quiz', assignment: 'Assignment', topic: 'Topic' };
  return type === 'meeting' ? clean(a.title) : `${names[type] || type.replaceAll('_', ' ') || 'Activity'}: ${clean(a.title)}`;
};
const activityDate = (a: Activity) => kind(a) === 'assignment' && a.date ? `Due: ${date(a.date)}` :
  kind(a) === 'meeting' && a.reviewTypeCode === 'mcm' && a.date ?
    `${date(a.date)} (${a.scheduleKind || 'projected'})` : '—';

export interface WeeklySection {
  week: PlanWeek | null; heading: string; activities: Activity[]; resources: Activity[];
}

/** Presentation only: retain every object/ID, occurrence and original order.
 * No coalescing by title, no hour aggregation and no mutation of the snapshot. */
export function weeklySections(module: Module): WeeklySection[] {
  const weeks = module.weeks || [];
  const known = new Set(weeks.map(w => w.sourceId));
  const group = (week: PlanWeek | null, index: number): WeeklySection => {
    const rows = module.activities.filter(a => week ? a.weekId === week.sourceId : !known.has(a.weekId || ''));
    return {
      week, heading: week ? `Week ${week.weekNumber ?? index + 1}: ${clean(week.title) || 'Title not recorded'}` : 'Activities — week relationship unavailable',
      activities: rows.filter(a => !(resourceTypes.has(kind(a)) && (a.hours == null || a.hours === 0))),
      resources: rows.filter(a => resourceTypes.has(kind(a)) && (a.hours == null || a.hours === 0)),
    };
  };
  const result = weeks.map((w, i) => group(w, i));
  if (module.activities.some(a => !known.has(a.weekId || ''))) result.push(group(null, result.length));
  return result;
}

// Document presentation only: keep source titles and assignment topic records
// intact in the snapshot/grouping, but omit topic detail rows from the paper.
const displayedSections = (module: Module): WeeklySection[] => weeklySections(module).map(section => ({
  ...section,
  heading: section.week ? `Week ${section.week.weekNumber ?? (module.weeks || []).indexOf(section.week) + 1}: ${
    clean(section.week.title).replace(/^\s*L\s*\d+(?:\s*[:.\-–—]\s*|\s+)/i, '').trim() || 'Title not recorded'}` : section.heading,
  activities: section.activities.filter(a => kind(a) !== 'topic'),
}));

const resourcesTitle = (rows: Activity[]) => `Supporting Resources${rows.every(a => a.hours === 0) ? ' (0h each)' : ''}`;
const resourceItems = (rows: Activity[]) => rows.map(a => `${label(a)}${rows.every(r => r.hours === 0) ? '' : ` [${a.hours == null ? 'OTJ unavailable' : hours(a.hours)}]`}`);
const resourcesText = (rows: Activity[]) => resourceItems(rows).join('   /   ');
const overviewRows = (v: ActivitiesOverview) => [
  ['Programme:', v.programme], ['Planned OTJ Hours:', hours(v.totals.totalHours)],
  ['Programme Dates:', `${date(v.startDate)} – ${date(v.endDate)}`], ['Delivery Responsibility:', v.deliveryResponsibility],
  ['Delivery Schedule:', v.deliverySchedule],
];
const moduleHeading = (m: Module, i: number) => `${i + 1}. Module ${i + 1}: ${m.title}  |  Planned OTJ Hours: ${hours(m.hours)}  |  ${date(m.startDate)} – ${date(m.endDate)}`;
const moduleDetails = (m: Module) => [[`Module Description: ${clean(m.description) || 'Not recorded / unavailable'}`,
  `Main Covered KSBs: ${m.coveredKsbs.map(k => k.code).join(', ') || 'Not recorded / unavailable'}`]];
const columns = ['Learning Activity', 'OTJ Hours', 'Date (if applicable)'];
const meetingColumns = ['Meeting', 'Planned Date', 'OTJ Hours'];
// A single visible schedule replaces the separate placement and signatory
// tables. Preserve captured occurrences, including verified Progress Reviews,
// without repeating the same stable occurrence ID across source collections.
const meetingRows = (v: ActivitiesOverview) => {
  const seen = new Set<string>();
  return [...(v.unassignedMeetings || []), ...(v.unclassifiedMeetings || []),
    ...v.reviews.filter(r => r.reviewTypeCode === 'progress_review')]
    .filter(r => { if (seen.has(r.id)) return false; seen.add(r.id); return true; })
    .map(r => [r.title, `${date(r.date)} (${r.scheduleKind})`, hours(r.hours)]);
};
const note = 'Activity hours are shown for reference and are not added to module totals again. Programme meeting hours are counted once in the existing Plan Total.';

/** Small vector marks keep the PDF portable: no emoji/font or asset dependencies. */
function icon(doc: jsPDF, type: string, x: number, y: number) {
  doc.saveGraphicsState();
  doc.setDrawColor(...rgb(T.accent)).setLineWidth(.24);
  if (type === 'live_session' || type === 'meeting') {
    doc.circle(x + 1.4, y + .7, .55); doc.ellipse(x + 1.4, y + 2.2, 1.15, .6);
    if (type === 'live_session') { doc.circle(x + 3, y + 1, .4); doc.line(x + 2.7, y + 2, x + 3.5, y + 2.5); }
  } else if (type === 'quiz') {
    doc.ellipse(x + 1.6, y + 1.3, 1.4, 1); doc.line(x + 1.5, y + 2.3, x + 1, y + 3);
  } else if (type === 'reading') {
    doc.rect(x, y, 3.3, 2.6); doc.line(x + 1.65, y, x + 1.65, y + 2.6);
  } else if (type === 'resources') {
    doc.lines([[1.6,-.8],[1.6,.8],[-1.6,.8],[-1.6,-.8]], x,y+.8, [1,1], 'S', true);
    doc.lines([[0,2.3],[1.6,.8],[1.6,-.8],[0,-2.3]], x,y+.8, [1,1], 'S', false);
    doc.line(x+1.6,y+1.6,x+1.6,y+3.9);
  } else {
    doc.roundedRect(x + .3, y, 2.5, 3, .3, .3); doc.line(x + .9, y + 1, x + 2.2, y + 1);
    doc.line(x + .9, y + 1.8, x + 2.2, y + 1.8);
  }
  doc.restoreGraphicsState();
}

/** Vector gradients stay sharp in print; no raster assets or remote fetches. */
function gradient(doc: jsPDF, x: number, y: number, width: number, height: number,
  from: readonly number[], to: readonly number[], radius = 0) {
  doc.saveGraphicsState();
  if (radius) { doc.roundedRect(x,y,width,height,radius,radius,null); doc.clip(); doc.discardPath(); }
  for (let i=0;i<80;i++) {
    doc.setFillColor(...rgb(from.map((c,j) => Math.round(c+(to[j]-c)*i/79))));
    doc.rect(x+width*i/80,y,width/80+.3,height,'F');
  }
  doc.restoreGraphicsState();
}

export function drawWeeklyTrainingPlan(doc: jsPDF, b: PdfBounds, v: ActivitiesOverview,
  latin: (value: unknown) => string, decorate: () => void) {
  type Options = { widths?: number[]; dark?: boolean; darkHead?: boolean; overview?: boolean; resources?: boolean; week?: WeeklySection };
  const table = (head: RowInput[] | undefined, body: RowInput[], options: Options = {}) => {
    const inset = options.week || options.resources ? 1.2 : 0;
    const width = b.width - inset * 2;
    autoTable(doc, { startY: b.getY(), head, body, tableWidth: width, theme: 'grid', rowPageBreak: 'avoid',
      margin: { left: b.x + inset, right: 210 - b.x - b.width + inset, top: b.top + inset, bottom: 297 - b.bottom + inset },
      styles: { font: 'helvetica', fontSize: options.dark ? 11 : 9, cellPadding: options.dark ? 1.7 : .8, overflow: 'linebreak', valign: 'top',
        textColor: options.dark ? [255, 255, 255] : rgb(options.resources ? T.heading : T.text),
        fillColor: options.dark ? rgb(T.secondary) : options.resources ? rgb(T.section) : [255, 255, 255],
        fontStyle: options.dark ? 'bold' : 'normal', lineColor: rgb(T.border), lineWidth: options.dark ? 0 : .16 },
      headStyles: { fillColor: rgb(options.darkHead ? T.secondary : T.section),
        textColor: options.darkHead ? [255,255,255] : rgb(T.heading), fontStyle: 'bold' },
      columnStyles: Object.fromEntries((options.widths || []).map((fraction, i) => [i, { cellWidth: width * fraction }])),
      willDrawPage: decorate,
      didParseCell: data => {
        if (options.resources) Object.assign(data.cell.styles, {fontSize:8.8, cellPadding:{top:1, bottom:1, left:7, right:1.5}});
        if (options.overview && data.column.index === 0) Object.assign(data.cell.styles, {fillColor: rgb(T.section), fontStyle: 'bold'});
        if (!options.week) return;
        if (data.section === 'head' && data.row.index === 0) Object.assign(data.cell.styles,
          { fillColor: [213, 177, 235], textColor: rgb(T.heading), fontSize: 11, cellPadding: 1.3, lineWidth:0 });
        if (data.column.index > 0) data.cell.styles.halign = 'center';
        if (data.section === 'body' && data.column.index === 0 && data.row.index >= 0 && data.row.index < options.week.activities.length)
          data.cell.styles.cellPadding = {top:.8, bottom:.8, right:1.15, left:7};
      },
      willDrawCell: data => {
        if (options.dark || (options.week && data.section === 'head' && data.row.index === 0)) {
          const dark = !!options.dark;
          gradient(doc,data.cell.x,data.cell.y,data.cell.width,data.cell.height,
            dark ? [102,36,129] : [232,211,245], dark ? [133,43,161] : [197,140,226],1.5);
          // The hook paints the background; AutoTable paints text and rules.
          data.cell.styles.fillColor = false;
        }
      },
      didDrawCell: data => {
        if (options.week && data.section === 'body' && data.column.index === 0 && data.row.index >= 0 && data.row.index < options.week.activities.length)
          icon(doc, kind(options.week.activities[data.row.index]), data.cell.x + 1.5, data.cell.y + .9);
        if (options.resources && data.section === 'head') icon(doc,'resources',data.cell.x+1.5,data.cell.y+1);
      },
      didDrawPage: data => {
        // Frame each real page segment; do not keep a whole long week on one
        // page or clip any activities/resources to fit the reference image.
        if (options.overview) {
          const top = data.pageNumber === 1 ? data.table.settings.startY : b.top;
          doc.setDrawColor(...rgb(T.border)).setLineWidth(.2);
          doc.roundedRect(b.x,top,b.width,(data.cursor?.y || top)-top,2,2,'S');
        }
      },
    });
    b.setY((doc as jsPDF & { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 2.2);
  };
  const cells = (rows: string[][]) => rows.map(row => row.map(latin));
  const heading = (title: string, dark = true) => {
    b.room(20);
    if (dark) table(undefined, cells([[title]]), {dark:true});
    else {
      const y=b.getY(); gradient(doc,b.x,y,b.width,8,[232,211,245],[197,140,226],1.5);
      doc.setFillColor(...rgb(T.secondary)).roundedRect(b.x+1,y+.7,6.6,6.6,1,1,'F');
      doc.setTextColor(255,255,255).setFontSize(12).setFont('helvetica','bold').text('=',b.x+3,y+5.2);
      doc.setTextColor(...rgb(T.heading)).setFontSize(12).text(latin(title),b.x+10,y+5.4);
      b.setY(y+10);
    }
  };
  b.room(20); table(undefined, cells(overviewRows(v)), {widths:[.27, .73], overview:true});
  for (const [i, module] of v.modules.entries()) {
    b.room(34); heading(moduleHeading(module, i));
    table(undefined, cells(moduleDetails(module)), {widths:[.7, .3]});
    for (const section of displayedSections(module)) {
      const rows: RowInput[] = cells(section.activities.map(a => [label(a), hours(a.hours), activityDate(a)]));
      if (!rows.length && !section.resources.length) rows.push([{content:'No recorded activities in this week.', colSpan:3}]);
      // Keep the week heading, column headings and the first real row together.
      doc.setFont('helvetica', 'normal').setFontSize(9);
      const first = section.activities[0];
      const firstText = first ? label(first) : resourcesTitle(section.resources) + '\n' + resourcesText(section.resources);
      const firstLines = doc.splitTextToSize(latin(firstText), first ? b.width * .62 - 8 : b.width - 4).length;
      const headingLines = doc.splitTextToSize(latin(section.heading), b.width - 4).length;
      b.room(Math.min(headingLines * 4.5 + 11 + firstLines * 3.6, b.bottom - b.top));
      const firstPage = doc.getCurrentPageInfo().pageNumber, cardTop = b.getY();
      const weekCell: CellInput = {content:latin(section.heading), colSpan:3};
      table([[weekCell], columns.map(latin)], rows, {widths:[.62, .145, .235], week:section});
      if (section.resources.length) {
        // A real table head stays with its first content and repeats when a
        // long resource list continues, instead of orphaning the box label.
        doc.setFont('helvetica', 'normal').setFontSize(8.8);
        const resourceLines = doc.splitTextToSize(latin(resourcesText(section.resources)), b.width - 10).length;
        b.room(Math.min(10 + resourceLines * 3.6, b.bottom - b.top));
        table([[latin(resourcesTitle(section.resources) + ` — ${section.week ? `Week ${section.week.weekNumber ?? (module.weeks || []).indexOf(section.week) + 1}` : 'Week unavailable'}`)]],
          cells([[resourcesText(section.resources)]]), {resources:true});
      }
      // One enclosing week card, including its resource box. On a page break
      // draw a continuation frame within the normal document content bounds.
      const lastPage = doc.getCurrentPageInfo().pageNumber, cardBottom = b.getY() - 1;
      doc.saveGraphicsState();
      doc.setDrawColor(...rgb(T.border)).setLineWidth(.2);
      for (let page=firstPage;page<=lastPage;page++) {
        doc.setPage(page);
        const top = page === firstPage ? cardTop : b.top;
        const bottom = page === lastPage ? cardBottom : b.bottom;
        doc.roundedRect(b.x,top,b.width,bottom-top,2,2,'S');
      }
      doc.setPage(lastPage); doc.restoreGraphicsState();
      b.setY(b.getY()+1);
    }
  }
  heading('Reviews Schedule', false);
  const meetings = meetingRows(v);
  table([meetingColumns.map(latin)], cells(meetings.length ? meetings :
    [['No planned reviews recorded.', '—', '—']]), {widths:[.5, .32, .18]});
  b.room(18);
  table([['Modules OTJ', 'Meetings OTJ', 'Plan Total'].map(latin)],
    cells([[hours(v.totals.moduleHours), hours(v.totals.meetingHours), hours(v.totals.totalHours)]]), {darkHead:true});
  table(undefined, cells([[note]]));
}

export function weeklyTrainingPlanHtml(v: ActivitiesOverview): string {
  const escape = (value: unknown) => clean(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const table = (head: string[] | null, rows: string[][], className = '') => `<table class="${className}">${head ? `<thead><tr>${head.map(c => `<th>${escape(c)}</th>`).join('')}</tr></thead>` : ''}<tbody>${rows.map(r => `<tr>${r.map(c => `<td>${escape(c)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
  // These native headings deliberately avoid generic h2/h3 decoration. The
  // editor's document-wide heading/table skin must not replace the weekly skin.
  return '<div data-training-plan-weekly="compact">' + table(null, overviewRows(v), 'tp-overview') +
    v.modules.map((module, i) => `<div class="tp-module-title" role="heading" aria-level="2">${escape(moduleHeading(module, i))}</div>` +
    `<table class="tp-module-details"><tbody><tr><td><strong>Module Description:</strong> ${escape(module.description || 'Not recorded / unavailable')}</td><td><strong>Main Covered KSBs:</strong> ${escape(module.coveredKsbs.map(k => k.code).join(', ') || 'Not recorded / unavailable')}</td></tr></tbody></table>` + displayedSections(module).map(section =>
      `<div class="tp-week-card"><div class="tp-week-title" role="heading" aria-level="3">${escape(section.heading)}</div>` +
      `<table class="tp-activities"><colgroup><col style="width:62%"><col style="width:14.5%"><col style="width:23.5%"></colgroup><thead><tr>${columns.map(c => `<th>${escape(c)}</th>`).join('')}</tr></thead><tbody>` +
      section.activities.map(a => `<tr><td>${activityIconHtml(kind(a))}${activityLabelHtml(a, escape)}</td><td>${escape(hours(a.hours))}</td><td>${escape(activityDate(a))}</td></tr>`).join('') + '</tbody></table>' +
      (section.resources.length ? `<div class="tp-resources">${activityIconHtml('resources')}<div><strong>${escape(resourcesTitle(section.resources))}</strong><div class="tp-resource-list">${resourceItems(section.resources).map(item => {
        const separator = item.indexOf(':');
        return `<span class="tp-resource-item"><strong>${escape(item.slice(0, separator + 1))}</strong> ${escape(item.slice(separator + 1).trim())}</span>`;
      }).join('<span class="tp-resource-separator"> / </span>')}</div></div></div>` : '') +
      (!section.activities.length && !section.resources.length ? '<p>No recorded activities in this week.</p>' : '') + '</div>').join('')).join('') +
    '<div class="tp-reviews-title" role="heading" aria-level="2">' + activityIconHtml('document') + 'Reviews Schedule</div>' +
    table(meetingColumns, meetingRows(v).length ? meetingRows(v) : [['No planned reviews recorded.', '—', '—']], 'tp-meetings') +
    table(['Modules OTJ', 'Meetings OTJ', 'Plan Total'], [[hours(v.totals.moduleHours), hours(v.totals.meetingHours), hours(v.totals.totalHours)]], 'tp-totals') + `<p class="tp-note">${escape(note)}</p></div>`;
}

function activityLabelHtml(a: Activity, escape: (value: unknown) => string): string {
  const value = label(a), separator = value.indexOf(':');
  return separator < 0 ? `<strong>${escape(value)}</strong>` : `<strong>${escape(value.slice(0, separator + 1))}</strong> ${escape(value.slice(separator + 1).trim())}`;
}

function activityIconHtml(type: string): string {
  const paths = type === 'reading' ? '<path d="M2 4q4-2 8 0q4-2 8 0v12q-4-2-8 0q-4-2-8 0zM10 4v12"/>' :
    type === 'quiz' ? '<path d="M4 3h12v10H9l-4 4v-4H4zM8 6h4M8 9h3"/>' :
    type === 'live_session' ? '<circle cx="10" cy="5" r="2.5"/><circle cx="4" cy="7" r="1.6"/><circle cx="16" cy="7" r="1.6"/><path d="M6 16v-3a4 4 0 0 1 8 0v3M1 15v-2a3 3 0 0 1 4-3M19 15v-2a3 3 0 0 0-4-3"/>' :
    type === 'meeting' ? '<path d="M3 11V9a7 7 0 0 1 14 0v2M5 10H2v6h3zM15 10h3v6h-3zM15 17l-5 1"/>' :
    type === 'resources' ? '<path d="M2 5l8-4 8 4v10l-8 4-8-4zM2 5l8 4 8-4M10 9v10M6 3l8 4"/>' :
    '<rect x="4" y="2" width="12" height="16" rx="1"/><path d="M7 6h6M7 10h6M7 14h4"/>';
  return `<svg class="tp-icon" aria-hidden="true" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`;
}
