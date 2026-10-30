import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useSearchParams } from 'react-router-dom';
import type { DashboardOverviewPayload } from '@/api/dashboardTabs';
import { ProgressCharts, type DisplayProgressModule } from '@/pages/learner/training-plan-timeline/ProgressCharts';
import { dateKey } from '@/pages/learner/training-plan-timeline/model';
import { monthRange } from '@/pages/learner/training-plan-timeline/monthlyHours';
import styles from '@/pages/learner/training-plan-timeline/trainingPlan.module.css';
import layout from '@/pages/learner/training-plan-timeline/TrainingPlanDetails.module.css';
import { overviewMeasures, overviewModulesForDisplay, overviewRatio } from './overviewFacts';

export function OverviewCharts({ payload, programmeStartDate, programmeEndDate, targetAsOfToday }: {
  payload: DashboardOverviewPayload; programmeStartDate?: string | null; programmeEndDate?: string | null;
  targetAsOfToday?: number | null;
}) {
  const [params] = useSearchParams();
  const { hash } = useLocation();
  const anchor = useRef<HTMLDivElement>(null);
  const modules = useMemo(() => overviewModulesForDisplay(payload.modules), [payload.modules]);
  const start = dateKey(programmeStartDate).slice(0, 7) || modules.map(module => module.start.slice(0, 7)).filter(Boolean).sort()[0] || '';
  const end = [dateKey(programmeEndDate).slice(0, 7), payload.hours.end,
    ...modules.map(module => [module.end, module.deliveryEnd || ''].sort().at(-1)!.slice(0, 7))].sort().at(-1) || '';
  const current = new Date().toLocaleDateString('en-CA', { timeZone: 'Europe/London' }).slice(0, 7);
  const requested = params.get('month') || '';
  const [month, setMonth] = useState(() => /^\d{4}-(0[1-9]|1[0-2])$/.test(requested) ? requested
    : start && current < start ? start : end && current > end ? end : current);
  useEffect(() => { setMonth(previous => start && previous < start ? start : end && previous > end ? end : previous); }, [start, end]);
  const [selection, setSelection] = useState(params.get('subject') || '');
  const selected = modules.find(module => module.id === selection)
    || modules.find(module => module.start && module.start.slice(0, 7) <= month && module.end.slice(0, 7) >= month);
  const displayed = useMemo<DisplayProgressModule[]>(() => modules.map(module => ({ id: module.id, title: module.title, start: module.start,
    value: overviewRatio(module.activities.completed, module.activities.total), measures: overviewMeasures(module) })), [modules]);
  const programmeRows = useMemo(() => {
    const byId = new Map<string, DisplayProgressModule>();
    for (const module of displayed) if (!byId.has(module.id)) byId.set(module.id, module);
    return payload.programme.map(row => {
      const module = byId.get(row.id);
      return { id: row.id, title: row.title, value: row.percent, start: module?.start,
        available: module?.measures.filter(measure => measure.value != null).length ?? 0, measureCount: module?.measures.length ?? 0 };
    });
  }, [payload.programme, displayed]);
  const points = useMemo(() => {
    const byMonth = new Map<string, DashboardOverviewPayload['hours']['months'][number]>();
    for (const row of payload.hours.months) if (!byMonth.has(row.key)) byMonth.set(row.key, row);
    return monthRange(payload.hours.months.map(row => row.key), start, end).map(key => ({
      ...(byMonth.get(key) || { key, target: null, submitted: 0, completed: 0 }),
      label: new Date(`${key}-01T12:00:00Z`).toLocaleDateString('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' }),
    }));
  }, [payload.hours.months, start, end]);
  const subject = params.get('subject') || '';
  useEffect(() => { if (subject || hash === '#module-timeline') anchor.current?.scrollIntoView({ block: 'start' }); }, [subject, requested, hash]);
  return <div ref={anchor} className={layout.root}><div className={`${styles.root} ${layout.root}`}>
    <div className={`${layout.topRow} ${layout.activityOverviewTopRow} ${layout.overviewSingle}`} data-layout="split">
      <div className={layout.activityOverviewMain}><ProgressCharts displayModules={displayed} selectedDisplayId={selected?.id}
        onDisplaySelect={setSelection} programmeRows={programmeRows} hourPoints={points} plannedHours={payload.hours.planned}
        targetAsOfToday={targetAsOfToday} /></div>
    </div>
  </div></div>;
}
