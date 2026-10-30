import { useEffect, useId, useState, type CSSProperties, type ReactNode } from 'react';
import type { TrainingPlanDashboard } from '@/api/trainingPlanDashboard';
import { percent, type TimelineModule } from './model';
import { moduleMeasures, moduleProgress, type ModuleMeasure } from './progress';
import { OtjHoursChart } from './OtjHoursChart';
import styles from './ProgressCharts.module.css';
import type { MonthlyHours } from './monthlyHours';

export type DisplayProgressModule = { id: string; title: string; start: string; value: number | null; measures: ModuleMeasure[] };

type Props = {
  modules?: TimelineModule[];
  selected?: TimelineModule;
  data?: TrainingPlanDashboard;
  onModuleSelect?: (module: TimelineModule) => void;
  displayModules?: DisplayProgressModule[];
  selectedDisplayId?: string;
  onDisplaySelect?: (id: string) => void;
  hourPoints?: MonthlyHours[];
  plannedHours?: number | null;
  programmeStartMonth?: string;
  programmeEndMonth?: string;
  programmeSnapshot?: ProgrammeProgressSnapshot;
  showOtjChart?: boolean;
  wholeProgrammeRings?: boolean;
  targetAsOfToday?: number | null;
  moduleStatus?: ReactNode;
  programmeRows?: { id: string; title: string; start?: string; value: number | null; available?: number; measureCount?: number }[];
};
export type ProgrammeProgressSnapshot = {
  overall: number | null;
  activitiesCompleted?: number | null;
  activitiesTotal?: number | null;
  activitiesPercent?: number | null;
  otjhPercent?: number | null;
  attendancePercent?: number | null;
  otjhActual: number | null;
  otjhTarget: number | null;
  ksb: number | null;
  ksbAvailable?: boolean;
  attendancePresent: number | null;
  attendanceTotal: number | null;
};
const colors = ['#6c50a5', '#315c85', '#398171', '#a97824', '#9b5981', '#5e6f91'];
const ringColors = ['#ff5c60', '#7543d3', '#4d9cf5', '#4fb38b'];
const progressTitle = (module: { id: string; title: string }) => module.id.startsWith('legacy:')
  ? `${module.title} (Previous learning)` : module.id.startsWith('current:')
    ? `${module.title} (Current plan)` : module.title;
const percentage = (value: number | null) => value == null ? 'N/A' : `${value}%`;
const hourNumber = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 1 });
// Compact dashboard captions: the "X / Y" part of each measure's existing
// detail text plus a unit. Details without a count are shown unchanged.
const measureUnits: Record<string, string> = { Attendance: 'sessions', Activities: 'activities', Hours: 'hours', KSBs: 'KSB points' };
function measureCount(label: string, detail: string) {
  const match = detail.match(/^([\d.,]+ \/ [\d.,]+)/);
  return match ? { count: match[1], unit: measureUnits[label] || '' } : { count: detail, unit: '' };
}

function ProgressMeasureRings({ measures, label }: { measures: ModuleMeasure[]; label: string }) {
  return <div className={styles.measureRings} aria-label={label}>
    {measures.map((measure, index) => {
      const value = measure.value == null ? 0 : Math.min(100, Math.max(0, measure.value));
      const { count, unit } = measureCount(measure.label, measure.detail);
      return <div className={styles.measureRingItem} key={measure.label} title={measure.detail}>
        <span className={styles.measureRing} style={{ '--measure-progress': `${value}%`, '--measure-color': ringColors[index] } as CSSProperties} role="img" aria-label={`${measure.label}: ${percentage(measure.value)}`}>
          <span>{percentage(measure.value)}</span>
        </span>
        <strong>{measure.label}</strong>
        <small>{measure.detail}</small>
        <span className={styles.measureCount}><b>{count}</b>{unit ? <em>{unit}</em> : null}</span>
      </div>;
    })}
  </div>;
}


export function ProgressCharts({ modules = [], selected, data, onModuleSelect, programmeStartMonth, programmeEndMonth, programmeSnapshot,
  showOtjChart = true, wholeProgrammeRings = false, targetAsOfToday, programmeRows, moduleStatus,
  displayModules, selectedDisplayId, onDisplaySelect, hourPoints, plannedHours }: Props) {
  const chartId = useId();
  const [programmePage, setProgrammePage] = useState(0);
  const displaySelected = displayModules?.find(module => module.id === selectedDisplayId);
  const selectedProgress = selected && data ? moduleProgress(selected, data) : null;
  const selectedMeasures = displaySelected?.measures ?? (selected && data ? moduleMeasures(selected, data) : []);
  const pickerRows = [...(displayModules || modules)].sort((a, b) => (a.start || '9999').localeCompare(b.start || '9999') || a.title.localeCompare(b.title));
  const rows = (programmeRows || displayModules?.map(module => ({ ...module,
    available: module.measures.filter(measure => measure.value != null).length, measureCount: module.measures.length })) || modules.map(module => {
    if (!data) return { ...module, value: null, available: 0, measureCount: 0 };
    const progress = moduleProgress(module, data);
    return { ...module, value: progress.value, available: progress.available, measureCount: progress.measures.length };
  })).sort((a, b) => (a.start || '9999').localeCompare(b.start || '9999') || a.title.localeCompare(b.title));
  const pageSize = 5;
  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  const visibleRows = rows.slice(programmePage * pageSize, (programmePage + 1) * pageSize);
  useEffect(() => { setProgrammePage(0); }, [rows.length]);
  useEffect(() => { if (programmePage >= pageCount) setProgrammePage(pageCount - 1); }, [pageCount, programmePage]);
  // Coach snapshots use the same canonical activity population as the caseload
  // table. Module summaries describe a different population of curriculum slots.
  const hasActivitySnapshot = programmeSnapshot?.activitiesTotal !== undefined;
  const activityDone = hasActivitySnapshot ? programmeSnapshot?.activitiesCompleted ?? null
    : modules.filter(module => !module.id.startsWith('legacy:')).reduce((sum, module) => sum + module.done, 0);
  const activityTotal = hasActivitySnapshot ? programmeSnapshot?.activitiesTotal ?? null
    : modules.filter(module => !module.id.startsWith('legacy:')).reduce((sum, module) => sum + module.activityCount, 0);
  const wholeProgrammeProgress = programmeSnapshot ? {
    value: programmeSnapshot.overall,
    available: 5,
    measures: [
      { label: 'Overall', value: programmeSnapshot.overall, detail: percentage(programmeSnapshot.overall) },
      { label: 'Attendance', value: programmeSnapshot.attendancePercent !== undefined ? programmeSnapshot.attendancePercent : programmeSnapshot.attendancePresent == null || programmeSnapshot.attendanceTotal == null
        ? null : percent(programmeSnapshot.attendancePresent, programmeSnapshot.attendanceTotal),
        detail: programmeSnapshot.attendancePresent == null || programmeSnapshot.attendanceTotal == null
          ? 'Attendance unavailable' : `${programmeSnapshot.attendancePresent} / ${programmeSnapshot.attendanceTotal} sessions attended` },
      { label: 'Activities', value: hasActivitySnapshot ? programmeSnapshot.activitiesPercent ?? null
        : activityTotal && activityDone != null ? percent(activityDone, activityTotal) : null,
        detail: activityTotal == null || activityDone == null ? 'Activity progress unavailable'
          : activityTotal ? `${activityDone} / ${activityTotal} completed across all modules` : 'No activities assigned' },
      { label: 'Hours', value: programmeSnapshot.otjhPercent !== undefined ? programmeSnapshot.otjhPercent : programmeSnapshot.otjhActual == null || programmeSnapshot.otjhTarget == null
        ? null : percent(programmeSnapshot.otjhActual, programmeSnapshot.otjhTarget),
        detail: programmeSnapshot.otjhActual == null || programmeSnapshot.otjhTarget == null
          ? 'Recorded hours unavailable' : `${hourNumber.format(programmeSnapshot.otjhActual)} / ${hourNumber.format(programmeSnapshot.otjhTarget)} hours` },
      { label: 'KSBs', value: programmeSnapshot.ksbAvailable === false ? null : programmeSnapshot.ksb, detail: programmeSnapshot.ksbAvailable === false ? 'KSB progress unavailable' : percentage(programmeSnapshot.ksb) },
    ],
  } : null;
  const chartProgress = wholeProgrammeProgress || selectedProgress;
  const chartWidth = chartProgress?.measures.length === 6 ? 470 : 400;
  return <div className={styles.charts}>
    {programmeSnapshot ? <section className={styles.card} aria-label="Whole programme progress">
      <header><div><p className={styles.eyebrow}>Whole programme</p><h2>Whole programme progress</h2><p className={styles.subtitle}>Summary metrics for this learner across every module</p></div>
        {chartProgress && <strong className={styles.total}>{percentage(chartProgress.value)}<small>Overall</small></strong>}
      </header>
      {chartProgress ? <>
        {wholeProgrammeRings ? <ProgressMeasureRings measures={chartProgress.measures.slice(1)} label="Whole programme progress measures" /> : <>
        <svg className={styles.chart} viewBox={`0 0 ${chartWidth} 235`} role="img" aria-labelledby={`${chartId}-title ${chartId}-description`}>
          <title id={`${chartId}-title`}>Whole programme progress by measure</title>
          <desc id={`${chartId}-description`}>{chartProgress.measures.map(measure => `${measure.label}: ${percentage(measure.value)}, ${measure.detail}`).join('. ')}</desc>
          {[0, 25, 50, 75, 100].map(value => <g key={value}><line x1="34" x2={chartWidth - 7} y1={185 - value * 1.5} y2={185 - value * 1.5} className={styles.gridLine} /><text x="27" y={189 - value * 1.5} textAnchor="end" className={styles.axis}>{value}</text></g>)}
          {chartProgress.measures.map((measure, index) => {
            const x = 46 + index * 71;
            return <g key={measure.label}>
              <rect x={x} y="35" width="36" height="150" rx="5" className={styles.track} />
              {measure.value != null && measure.value > 0 && <rect x={x} y={185 - measure.value * 1.5} width="36" height={measure.value * 1.5} rx="5" fill={colors[index]} />}
              <text x={x + 18} y="23" textAnchor="middle" className={styles.barValue}>{percentage(measure.value)}</text>
              <text x={x + 18} y="207" textAnchor="middle" className={styles.axis}>{measure.label}</text>
            </g>;
          })}
        </svg>
        <dl className={styles.measures}>{chartProgress.measures.map((measure, index) => <div key={measure.label}>
          <dt><i style={{ background: colors[index] }} />{measure.label}</dt><dd>{measure.detail}</dd>
        </div>)}</dl>
        </>}
        {!wholeProgrammeRings && <p className={styles.note}>{hasActivitySnapshot
          ? programmeSnapshot?.attendancePercent !== undefined
            ? 'Activities use the supplied programme totals. Overall, OTJH, KSB and attendance match the case-file cards.'
            : 'Activities use the same programme totals as the coach learner table. Overall, OTJH, KSB and attendance match the case-file cards.'
          : 'Overall, OTJH, KSB and attendance match the case-file cards. Activities are aggregated across all learner modules.'}</p>}
      </> : <p className={styles.empty}>Select a module to see attendance, activities, hours and KSBs.</p>}
    </section> : <section className={`${styles.card} ${styles.moduleProgressCard}`} aria-label="Module progress">
      <header><div><p className={styles.eyebrow}>Selected module</p><h2>Module progress</h2></div>
        <label className={styles.modulePicker}><span className={styles.srOnly}>Select module</span><select value={displaySelected?.id || selected?.id || ''} onChange={event => {
          if (displayModules) { onDisplaySelect?.(event.target.value); return; }
          const next = modules.find(module => module.id === event.target.value);
          if (next) onModuleSelect?.(next);
        }} disabled={!pickerRows.length}>
          {!pickerRows.length && <option value="">No modules</option>}
          {pickerRows.map(module => <option key={module.id} value={module.id}>{progressTitle(module)}</option>)}
        </select></label>
      </header>
      {moduleStatus || (displaySelected || selected ? <ProgressMeasureRings measures={selectedMeasures} label={`${displaySelected?.title || selected?.title} progress measures`} />
        : <p className={styles.empty}>Select a module to see attendance, activities, hours and KSBs.</p>)}
    </section>}
    <section className={`${styles.card} ${styles.programmeProgressCard}`} aria-label="Programme module progress">
      <header><div><p className={styles.eyebrow}>Whole programme</p><h2>Programme progress</h2></div><span className={styles.moduleCount}>{rows.length} modules</span></header>
      <div className={styles.programmeAxis} aria-hidden="true"><span>Module</span><span>Progress</span></div>
      <div className={styles.programmeRows}>{rows.length ? visibleRows.map(module => {
        const progress = { value: module.value, available: module.available };
        return <button type="button" key={module.id} className={styles.module} aria-pressed={(displaySelected?.id || selected?.id) === module.id}
          onClick={() => { if (displayModules) { if (displayModules.some(row => row.id === module.id)) onDisplaySelect?.(module.id); return; } const selectedModule = modules.find(item => item.id === module.id); if (selectedModule) onModuleSelect?.(selectedModule); }} aria-label={`${progressTitle(module)}: ${percentage(progress.value)} overall progress`}>
          <span className={styles.rowHeading}><strong>{progressTitle(module)}</strong><b>{percentage(progress.value)}</b></span>
          <span className={styles.moduleTrack}><span style={{ width: `${progress.value || 0}%` }} /></span>
          {module.measureCount !== undefined && <span className={styles.coverage}>{progress.available} of {module.measureCount} measures available</span>}
        </button>;
      }) : <p className={styles.empty}>Your modules will appear here once assigned.</p>}</div>
      {rows.length > pageSize && <nav className={styles.pagination} aria-label="Programme module pages">
        <span>Showing {programmePage * pageSize + 1}–{Math.min(rows.length, (programmePage + 1) * pageSize)} of {rows.length} modules</span>
        <div className={styles.pageButtons}>
          <button type="button" onClick={() => setProgrammePage(page => Math.max(0, page - 1))} disabled={programmePage === 0} aria-label="Previous module page">‹</button>
          {Array.from({ length: pageCount }, (_, index) => <button type="button" key={index} onClick={() => setProgrammePage(index)} aria-current={programmePage === index ? 'page' : undefined}>{index + 1}</button>)}
          <button type="button" onClick={() => setProgrammePage(page => Math.min(pageCount - 1, page + 1))} disabled={programmePage === pageCount - 1} aria-label="Next module page">›</button>
        </div>
      </nav>}
      <p className={styles.note}>Module progress is based on activities. Attendance, hours and KSBs are shown independently.</p>
    </section>
    {showOtjChart && <OtjHoursChart data={data} points={hourPoints} plannedHours={plannedHours} programmeStartMonth={programmeStartMonth} programmeEndMonth={programmeEndMonth} targetAsOfToday={targetAsOfToday} />}
  </div>;
}
