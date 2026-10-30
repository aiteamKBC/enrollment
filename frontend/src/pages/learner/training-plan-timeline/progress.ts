import type { TrainingPlanDashboard } from '@/api/trainingPlanDashboard';
import { systemDateParts } from '@/lib/format';
import { percent, type TimelineModule } from './model';

export type ModuleMeasure = { label: string; value: number | null; detail: string };
const count = (value: number) => new Intl.NumberFormat('en-GB', { maximumFractionDigits: 2 }).format(value);
const ratio = (done: number | null | undefined, total: number | null | undefined) =>
  done != null && total != null && total > 0 ? percent(done, total) : null;

export function programmeReviewProgress(
  reviews: TrainingPlanDashboard['reviews'],
  now: string | number | Date = Date.now(),
) {
  const today = systemDateParts(now);
  const todayKey = today
    ? `${today.year}-${String(today.month).padStart(2, '0')}-${String(today.day).padStart(2, '0')}`
    : null;
  const unique = [...new Map(reviews.filter(review => review.source !== 'student-support' && review.status !== 'cancelled')
    .map(review => [review.eventKey, review])).values()];
  const due = todayKey == null ? [] : unique.filter(review => {
    const targetDate = review.targetDate || review.date;
    return typeof targetDate === 'string' && /^\d{4}-\d{2}-\d{2}/.test(targetDate)
      && targetDate.slice(0, 10) <= todayKey;
  });
  const completed = due.filter(review => review.status === 'completed').length;
  return { completed, total: due.length, percent: ratio(completed, due.length) };
}

export function moduleMeasures(module: TimelineModule, data: TrainingPlanDashboard, now = Date.now()): ModuleMeasure[] {
  const ended = [...new Map(module.sessions.filter(session => {
    if (['cancelled', 'deleted'].includes(session.status)) return false;
    const start = Date.parse(session.start);
    const explicitEnd = Date.parse(session.end || '');
    const end = Number.isFinite(explicitEnd) ? explicitEnd
      : Number.isFinite(start) ? start + Math.max(0, session.minutes || 0) * 60_000 : Number.NaN;
    return Number.isFinite(end) && end <= now;
  }).map(session => [session.id, session])).values()];
  const attended = ended.filter(session => session.attended === true).length;
  const pendingAttendance = ended.filter(session => typeof session.attended !== 'boolean').length;
  const ledgerAttendance = module.attendanceProgress;
  const planned = module.detail?.total_otjh;
  const ksb = module.ksbProgress;
  return [
    { label: 'Attendance', value: ledgerAttendance ? ratio(ledgerAttendance.attended, ledgerAttendance.total) : ratio(attended, ended.length), detail: ledgerAttendance
      ? ledgerAttendance.total ? `${ledgerAttendance.attended} / ${ledgerAttendance.total} scheduled sessions attended` : 'No scheduled sessions assigned'
      : ended.length
      ? `${attended} / ${ended.length} ended sessions attended${pendingAttendance ? ` · ${pendingAttendance} pending` : ''}`
      : 'No ended sessions yet' },
    { label: 'Activities', value: ratio(module.done, module.activityCount), detail: module.activityCount ? `${module.done} / ${module.activityCount} completed` : 'No activities assigned' },
    { label: 'Hours', value: ratio(module.actual, planned), detail: module.actual == null ? 'Recorded hours unavailable'
      : planned == null || planned <= 0 ? `${count(module.actual)} hours recorded · target unavailable` : `${count(module.actual)} / ${count(planned)} hours` },
    { label: 'KSBs', value: ratio(ksb?.completed, ksb?.total), detail: ksb == null ? 'KSB progress unavailable'
      : ksb.total ? `${ksb.completed} / ${ksb.total} activity KSB points achieved` : 'No KSB points mapped' },
  ];
}

export function moduleProgress(module: TimelineModule, data: TrainingPlanDashboard, now = Date.now()) {
  const measures = moduleMeasures(module, data, now);
  const activities = measures.find(measure => measure.label === 'Activities');
  return { measures, available: measures.filter(measure => measure.value != null).length,
    value: activities?.value ?? null };
}
