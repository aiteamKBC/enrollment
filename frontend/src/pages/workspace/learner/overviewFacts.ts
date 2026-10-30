import type { OverviewModule } from '@/api/dashboardTabs';
import type { ModuleMeasure } from '@/pages/learner/training-plan-timeline/progress';
import { percent } from '@/pages/learner/training-plan-timeline/model';

const count = (value: number) => new Intl.NumberFormat('en-GB', { maximumFractionDigits: 2 }).format(value);
export const overviewRatio = (done: number | null | undefined, total: number | null | undefined) =>
  done != null && total != null && total > 0 ? percent(done, total) : null;
export function overviewModulesForDisplay(facts: OverviewModule[]): OverviewModule[] {
  const seen = new Set<string>();
  return [...facts].sort((a, b) => a.title.localeCompare(b.title) || a.id.localeCompare(b.id)).map(fact => {
    const module = { ...fact, activities: { ...fact.activities } };
    const dates = [fact.dateBounds?.first, fact.dateBounds?.last].filter((value): value is string => !!value);
    for (const activity of fact.duplicates || []) {
      if (seen.has(activity.key)) { module.activities.total -= 1; module.activities.completed -= Number(activity.completed); }
      else { seen.add(activity.key); if (activity.date) dates.push(activity.date); }
    }
    if (fact.dateBounds) {
      dates.sort();
      if (!fact.dateBounds.startFixed) module.start = dates[0] || '';
      if (!fact.dateBounds.endFixed) module.end = dates.at(-1) || module.start;
    }
    return module;
  });
}
export function overviewMeasures(module: OverviewModule): ModuleMeasure[] {
  const { attendance, activities, hours, ksb } = module;
  return [
    { label: 'Attendance', value: attendance && attendance.total > 0
      ? Math.round(100 * attendance.attended / attendance.total) : null,
      detail: attendance && attendance.total > 0
        ? `${attendance.attended} / ${attendance.total} recorded sessions attended` : 'No recorded attendance yet' },
    { label: 'Activities', value: overviewRatio(activities.completed, activities.total), detail: activities.total
      ? `${activities.completed} / ${activities.total} completed` : 'No activities assigned' },
    { label: 'Hours', value: overviewRatio(hours.actual, hours.planned), detail: hours.actual == null ? 'Recorded hours unavailable'
      : hours.planned == null || hours.planned <= 0 ? `${count(hours.actual)} hours recorded · target unavailable`
        : `${count(hours.actual)} / ${count(hours.planned)} hours` },
    { label: 'KSBs', value: overviewRatio(ksb?.completed, ksb?.total), detail: ksb == null ? 'KSB progress unavailable'
      : ksb.total ? `${ksb.completed} / ${ksb.total} activity KSB points achieved` : 'No KSB points mapped' },
  ];
}

