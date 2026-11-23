import type jsPDF from 'jspdf';
import autoTable from 'jspdf-autotable';
import type { PdfBounds } from './lavenderPdf';
import { BRANDED_PURPLE as T } from './brandedPurpleTheme';
import { formatHoursMinutes } from '@/lib/format';
import { drawWeeklyTrainingPlan, weeklyTrainingPlanHtml } from './weeklyTrainingPlan';
import { drawCompactTrainingPlan, compactTrainingPlanHtml } from './compactTrainingPlan';

export interface Activity { id: string; title: string; kind?: string; hours: number | null; date?: string | null; timing?: string; source?: string; reviewTypeCode?: string; scheduleKind?: string; weekId?: string; parentComponentId?: string }
interface Review extends Activity { templateId: string; date: string; participants: string | null; scheduleKind: 'projected' | 'booked' | 'completed' }
export interface PlanWeek { sourceId: string; title: string | null; weekNumber?: number | null; order?: number | null }
export interface WeekSummary { id: string; title: string; kind: 'topic' | 'assignment'; hours: number | null;
  componentIds: string[]; assignmentComponentIds: string[]; dueDates: string[]; startDate?: string | null; endDate?: string | null }
export interface Module { academicModuleId: string; title: string; description: string | null; hours: number | null;
  startDate: string | null; endDate: string | null; coveredKsbs: { code: string }[]; activities: Activity[]; weeks?: PlanWeek[]; weekSummaries?: WeekSummary[] }
export interface ActivitiesOverview { version: 'activities-overview-v1' | 'activities-overview-v2' | 'activities-overview-v3' | 'activities-overview-v4' | 'activities-overview-v5' | 'activities-overview-v6'; programme: string; startDate: string; endDate: string;
  deliveryResponsibility: string; deliverySchedule: string; modules: Module[]; reviews: Review[];
  totals: { moduleHours: number; meetingHours: number; totalHours: number };
  warnings: { message?: string; code: string }[]; blockers: { message?: string; code: string }[];
  unassignedMeetings?: Review[]; unclassifiedMeetings?: Review[] }

const plain = (v: unknown) => String(v ?? '').replace(/<[^>]*>/g, '').replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&');
const hours = (v: number | null | undefined) => v == null ? '—' : formatHoursMinutes(v);
const when = (v: string | null | undefined) => /^\d{4}-\d{2}-\d{2}$/.test(v || '') ?
  new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(new Date(v + 'T00:00:00Z')) : plain(v) || '—';
const activityLabel = (r: Activity) => r.kind === 'meeting' ? r.title :
  `${r.kind === 'topic' ? 'Topic' : r.kind === 'assignment' ? 'Assignment' : plain(r.kind).replaceAll('_', ' ')}: ${plain(r.title)}`;
const overviewRows = (v: ActivitiesOverview) => [
  ['Programme', v.programme], ['Planned OTJ Hours', hours(v.totals.totalHours)],
  ['Programme Dates', `${when(v.startDate)} – ${when(v.endDate)}`],
  ['Delivery Responsibility', v.deliveryResponsibility], ['Delivery Schedule', v.deliverySchedule],
];
const activityRows = (m: Module, v: ActivitiesOverview) => m.activities.map(r => [activityLabel(r), hours(r.hours),
  v.version === 'activities-overview-v1' ? when(r.date || r.timing) :
    r.kind === 'assignment' ? when(r.date) : r.kind === 'meeting' && r.reviewTypeCode === 'mcm' && r.date ?
      `${when(r.date)} (${r.scheduleKind || 'projected'})` : '—']);
const reviewRows = (v: ActivitiesOverview) => v.reviews.map(r => [r.title, `${when(r.date)} (${r.scheduleKind})`, r.participants || 'Not recorded / unavailable']);
const moduleLabel = (m: Module, i: number) => `${i + 1}. Module: ${m.title} | Planned OTJ Hours: ${hours(m.hours)} | ${when(m.startDate)} – ${when(m.endDate)}`;
const columns = ['Planned Learning Activity', 'Planned OTJ Hours', 'Planned Date / Month'];
const note = 'Activity hours are shown for reference and are not added to module totals again. Reviews shown within modules and in the Reviews Schedule refer to the same occurrences; their OTJ hours count once. Undated activities retain the recorded LMS sequence.';
const correctedNote = 'Activity hours are shown for reference and are not added to module totals again. Monthly Coaching appears only within verified module weeks; the Reviews Schedule lists Progress Reviews only. The existing Meetings OTJ total includes all applicable programme meetings, including types omitted from these tables. Required roles do not indicate completed signatures.';
const reviewColumns = (v: ActivitiesOverview) => ['Review Type', 'Planned Date', v.version === 'activities-overview-v2' ? 'Required Signatories / Participants' : 'Participants / Format'];

/** The same immutable snapshot feeds Django PDF, browser preview and editor. */
export function drawTrainingPlanActivities(doc: jsPDF, b: PdfBounds, v: ActivitiesOverview,
  latin: (value: unknown) => string, decorate: () => void) {
  if (v.version === 'activities-overview-v6') return drawCompactTrainingPlan(doc, b, v, latin, decorate);
  if (v.version === 'activities-overview-v5') return drawWeeklyTrainingPlan(doc, b, v, latin, decorate);
  const rgb = (colour: string | readonly number[]): [number, number, number] =>
    (typeof colour === 'string' ? [1, 3, 5].map(i => parseInt(colour.slice(i, i + 2), 16)) : [...colour]) as [number, number, number];
  const table = (head: string[] | null, rows: string[][], widths?: number[], dark = false) => {
    b.room(head ? 18 : 12);
    autoTable(doc, { startY: b.getY(), head: head ? [head.map(latin)] : undefined,
      body: rows.map(row => row.map(latin)), tableWidth: b.width, theme: 'grid', rowPageBreak: 'avoid',
      margin: { left: b.x, right: 210 - b.x - b.width, top: b.top, bottom: 297 - b.bottom },
      styles: { font: 'helvetica', fontSize: 9, cellPadding: 1.7, overflow: 'linebreak', valign: 'top',
        textColor: dark ? [255, 255, 255] : rgb(T.text), fillColor: dark ? rgb(T.secondary) : [255, 255, 255],
        lineColor: rgb(T.border), lineWidth: .16, fontStyle: dark ? 'bold' : 'normal' },
      headStyles: { fillColor: rgb(T.section), textColor: rgb(T.heading), fontStyle: 'bold' },
      columnStyles: Object.fromEntries((widths || []).map((width, i) => [i, { cellWidth: b.width * width }])),
      willDrawPage: decorate,
    });
    b.setY((doc as jsPDF & { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 2.5);
  };
  table(null, overviewRows(v), [.25, .75]);
  if (v.version !== 'activities-overview-v4' && v.blockers.length) table(['Draft — source data requires attention'], v.blockers.map(i => [i.message || i.code]));
  if (!v.modules.length) table(['Modules'], [['Confirmed module data unavailable. This draft cannot be issued.']]);
  v.modules.forEach((m, i) => {
    b.room(37);
    table(null, [[moduleLabel(m, i)]], undefined, true);
    table(null, [[`Module Description: ${plain(m.description) || 'Not recorded / unavailable'}`,
      `Main Covered KSBs: ${m.coveredKsbs.map(k => k.code).join(', ') || 'Not recorded / unavailable'}`]], [.67, .33]);
    table(columns, activityRows(m, v).length ? activityRows(m, v) : [['Activities unavailable', '—', '—']], [.62, .16, .22]);
  });
  b.room(24);
  table(null, [['Reviews Schedule']], undefined, true);
  table(reviewColumns(v), reviewRows(v).length ? reviewRows(v) :
    [[v.version === 'activities-overview-v2' ? 'No verified Progress Review occurrences available in this saved period.' : 'No programme review occurrences resolved in this saved period.', '—', '—']], [.4, .29, .31]);
  table(['Modules OTJ', 'Meetings OTJ', 'Plan Total'], [[hours(v.totals.moduleHours), hours(v.totals.meetingHours), hours(v.totals.totalHours)]]);
  table(null, [[v.version === 'activities-overview-v2' ? correctedNote : note],
    ...(v.version === 'activities-overview-v4' ? [] : v.warnings.map(i => [i.message || i.code]))]);
}

export function trainingPlanActivitiesHtml(v: ActivitiesOverview): string {
  if (v.version === 'activities-overview-v6') return compactTrainingPlanHtml(v);
  if (v.version === 'activities-overview-v5') return weeklyTrainingPlanHtml(v);
  const escape = (value: unknown) => plain(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const table = (head: string[] | null, rows: string[][]) => `<table>${head ? `<thead><tr>${head.map(c => `<th>${escape(c)}</th>`).join('')}</tr></thead>` : ''}<tbody>${rows.map(r => `<tr>${r.map(c => `<td>${escape(c)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
  return table(null, overviewRows(v)) + (v.version === 'activities-overview-v4' ? '' : v.blockers.map(i => `<p>${escape(i.message || i.code)}</p>`).join('')) +
    v.modules.map((m, i) => `<h2>${escape(moduleLabel(m, i))}</h2>` + table(null, [[
      `Module Description: ${m.description || 'Not recorded / unavailable'}`,
      `Main Covered KSBs: ${m.coveredKsbs.map(k => k.code).join(', ') || 'Not recorded / unavailable'}`]]) + table(columns, activityRows(m, v))).join('') +
    '<h2>Reviews Schedule</h2>' + table(reviewColumns(v), reviewRows(v)) +
    table(['Modules OTJ', 'Meetings OTJ', 'Plan Total'], [[hours(v.totals.moduleHours), hours(v.totals.meetingHours), hours(v.totals.totalHours)]]) +
    `<p>${escape(v.version === 'activities-overview-v2' ? correctedNote : note)}</p>` + (v.version === 'activities-overview-v4' ? '' : v.warnings.map(i => `<p>${escape(i.message || i.code)}</p>`).join(''));
}
