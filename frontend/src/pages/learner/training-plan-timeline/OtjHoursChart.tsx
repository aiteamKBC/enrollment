import { useId, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react';
import { monthlyHours, type MonthlyHours, type MonthlyHoursSource } from './monthlyHours';
import styles from './ProgressCharts.module.css';

const hourNumber = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 1 });

function ratio(value: number | null, target: number | null) {
  return value == null || target == null || target <= 0 ? null : Math.round(value / target * 100);
}

function reportingMonth() {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'Europe/London' }).slice(0, 7);
}

function signed(value: number) {
  return `${value > 0 ? '+' : ''}${hourNumber.format(value)}`;
}

function scaleFor(rows: MonthlyHours[]) {
  const maximum = Math.max(0, ...rows.flatMap(row => [row.target || 0, row.submitted || 0, row.completed || 0]));
  if (!maximum) return { maximum: 10, step: 2.5 };
  const rough = maximum / 4;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const fraction = rough / magnitude;
  const nice = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10;
  const step = nice * magnitude;
  return { maximum: step * 4, step };
}

function MonthTooltip({ row, id }: { row: MonthlyHours; id: string }) {
  const targetRatio = row.target == null ? null : 100;
  const submittedRatio = ratio(row.submitted, row.target);
  const completedRatio = ratio(row.completed, row.target);
  const variance = row.target == null || row.completed == null ? null : row.completed - row.target;
  return <div id={id} role="tooltip" className={styles.monthlyTooltip}>
    <strong>{row.label}</strong>
    <dl>
      <div><dt><i className={styles.targetKey} />Target:</dt><dd>{targetRatio == null ? 'N/A' : `${targetRatio}% (${hourNumber.format(row.target!)} hours)`}</dd></div>
      <div><dt><i className={styles.submittedKey} />Submitted:</dt><dd>{submittedRatio == null ? 'N/A' : `${submittedRatio}% (${hourNumber.format(row.submitted!)} hours)`}</dd></div>
      <div><dt><i className={styles.completedKey} />Completed:</dt><dd>{completedRatio == null ? 'N/A' : `${completedRatio}% (${hourNumber.format(row.completed!)} hours)`}</dd></div>
      <div><dt>Total completed or pending:</dt><dd>{row.submitted == null || row.completed == null ? 'N/A' : `${hourNumber.format(row.submitted + row.completed)} hours`}</dd></div>
      <div className={styles.variance}><dt>Variance:</dt><dd>{variance == null || completedRatio == null ? 'N/A' : `${completedRatio - 100 > 0 ? '+' : ''}${completedRatio - 100}% (${signed(variance)} hours)`}</dd></div>
    </dl>
  </div>;
}

export function OtjHoursChart({ data, points, plannedHours, programmeStartMonth, programmeEndMonth, targetAsOfToday }: {
  data?: MonthlyHoursSource;
  points?: MonthlyHours[];
  plannedHours?: number | null;
  programmeStartMonth?: string;
  programmeEndMonth?: string;
  targetAsOfToday?: number | null;
}) {
  const chartId = useId();
  const tooltipId = `${chartId}-monthly-tooltip`;
  const [activeMonth, setActiveMonth] = useState('');
  const monthlyScrollRef = useRef<HTMLDivElement>(null);
  const monthlyPan = useRef<{ pointerId: number; x: number; left: number } | null>(null);
  const months = points ?? (data ? monthlyHours(data, programmeStartMonth, programmeEndMonth) : []);
  const active = months.find(row => row.key === activeMonth);
  const activeIndex = Math.max(0, months.findIndex(row => row.key === activeMonth));
  const scale = scaleFor(months);
  const ticks = Array.from({ length: 5 }, (_, index) => scale.step * (4 - index));
  const targetToDate = months.filter(row => row.key <= reportingMonth());
  const expectedTarget = targetToDate.length
    ? targetToDate.reduce((sum, row) => sum + (row.target ?? 0), 0) : null;
  const totalSubmitted = months.every(row => row.submitted != null) ? months.reduce((sum, row) => sum + row.submitted!, 0) : null;
  const totalCompleted = months.every(row => row.completed != null) ? months.reduce((sum, row) => sum + row.completed!, 0) : null;
  const progressTarget = targetAsOfToday != null && Number.isFinite(targetAsOfToday) && targetAsOfToday > 0
    ? targetAsOfToday : expectedTarget;
  const monthlyPlanned = months.length ? months.reduce((sum, row) => sum + (row.target ?? 0), 0) : null;
  const requiredOtjh = plannedHours ?? data?.requiredOtjh;
  const plannedTarget = requiredOtjh != null && Number.isFinite(requiredOtjh) && requiredOtjh > 0
    ? requiredOtjh : monthlyPlanned && monthlyPlanned > 0 ? monthlyPlanned : progressTarget;
  const overallPercent = ratio(totalCompleted, plannedTarget);
  const targetPosition = ratio(progressTarget, plannedTarget);
  const overallVariance = progressTarget == null || totalCompleted == null ? null : totalCompleted - progressTarget;
  const variancePercent = ratio(overallVariance, progressTarget);
  const completedWidth = Math.min(100, overallPercent || 0);
  const submittedWidth = Math.min(Math.max(0, 100 - completedWidth), ratio(totalSubmitted, plannedTarget) || 0);
  const beginMonthlyPan = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.pointerType !== 'mouse' || event.button !== 0) return;
    const scroll = monthlyScrollRef.current;
    if (!scroll) return;
    monthlyPan.current = { pointerId: event.pointerId, x: event.clientX, left: scroll.scrollLeft };
    scroll.dataset.panning = 'true';
    scroll.setPointerCapture?.(event.pointerId);
  };
  const moveMonthlyPan = (event: ReactPointerEvent<HTMLDivElement>) => {
    const scroll = monthlyScrollRef.current;
    const pan = monthlyPan.current;
    if (!scroll || !pan || pan.pointerId !== event.pointerId) return;
    const distance = event.clientX - pan.x;
    if (Math.abs(distance) > 2) event.preventDefault();
    scroll.scrollLeft = pan.left - distance;
  };
  const endMonthlyPan = (event: ReactPointerEvent<HTMLDivElement>) => {
    const scroll = monthlyScrollRef.current;
    if (!monthlyPan.current || monthlyPan.current.pointerId !== event.pointerId) return;
    scroll?.releasePointerCapture?.(event.pointerId);
    if (scroll) delete scroll.dataset.panning;
    monthlyPan.current = null;
  };

  return <section className={`${styles.card} ${styles.monthlyCard}`} aria-label="Off-the-job hours by month">
    <header><div><p className={styles.eyebrow}>Whole programme</p><h2>Off-The-Job Hours</h2><p className={styles.subtitle}>Target, submitted and completed hours for every month</p></div></header>
    <div className={styles.monthlyLegend} aria-label="Chart legend">
      <span><i className={styles.targetKey} />Target</span>
      <span><i className={styles.submittedKey} />Submitted</span>
      <span><i className={styles.completedKey} />Completed</span>
    </div>
    {active && <div className={styles.tooltipPosition} style={{ '--tooltip-left': `${(activeIndex + .5) / months.length * 100}%` } as CSSProperties}>
      <MonthTooltip row={active} id={tooltipId} />
    </div>}
    {months.length ? <div ref={monthlyScrollRef} className={styles.monthlyScroll} role="region" tabIndex={0}
      aria-label="Monthly off-the-job hours chart. Scroll horizontally to view more months."
      onPointerDown={beginMonthlyPan} onPointerMove={moveMonthlyPan} onPointerUp={endMonthlyPan} onPointerCancel={endMonthlyPan}>
      <div className={styles.monthlyPlot} style={{ minWidth: `${Math.max(36, months.length * 4.25)}rem` }}>
        <div className={styles.yAxis} aria-hidden="true">{ticks.map(value => <span key={value} style={{ bottom: `${value / scale.maximum * 100}%` }}>{hourNumber.format(value)}</span>)}</div>
        <div className={styles.grid} aria-hidden="true">{ticks.map(value => <i key={value} style={{ bottom: `${value / scale.maximum * 100}%` }} />)}</div>
        <div className={styles.monthBars} style={{ '--month-count': months.length } as CSSProperties}>{months.map(row => {
          const targetHeight = row.target == null ? null : row.target / scale.maximum * 100;
          const submittedHeight = row.submitted == null ? 0 : row.submitted / scale.maximum * 100;
          const completedHeight = row.completed == null ? 0 : row.completed / scale.maximum * 100;
          return <button key={row.key} type="button" className={styles.monthColumn}
            aria-label={`${row.label}: target ${row.target == null ? 'unavailable' : `${hourNumber.format(row.target)} hours`}, submitted ${row.submitted == null ? 'unavailable' : `${hourNumber.format(row.submitted)} hours`}, completed ${row.completed == null ? 'unavailable' : `${hourNumber.format(row.completed)} hours`}`}
            aria-describedby={activeMonth === row.key ? tooltipId : undefined}
            onMouseEnter={() => setActiveMonth(row.key)} onMouseLeave={() => setActiveMonth(current => current === row.key ? '' : current)}
            onFocus={() => setActiveMonth(row.key)} onBlur={() => setActiveMonth(current => current === row.key ? '' : current)}>
            <span className={styles.barArea}>
              <i className={styles.submittedBar} style={{ bottom: `${completedHeight}%`, height: `${submittedHeight}%` }} />
              <i className={styles.completedBar} style={{ height: `${completedHeight}%` }} />
              {targetHeight != null && <i className={styles.targetLine} style={{ bottom: `${targetHeight}%` }} />}
            </span>
            <span className={styles.monthLabel}>{new Date(`${row.key}-01T12:00:00Z`).toLocaleDateString('en-GB', { month: 'short', timeZone: 'UTC' })}<small>{row.key.slice(0, 4)}</small></span>
          </button>;
        })}</div>
      </div>
    </div> : <p className={styles.empty}>Monthly hour targets will appear here once the training plan is available.</p>}
    {months.length > 0 && <div className={styles.overall}>
      <div><strong>Overall progress</strong><span>{totalCompleted == null ? 'Recorded hours unavailable' : `${hourNumber.format(totalCompleted)}h completed${totalSubmitted != null && totalSubmitted > 0 ? ` · ${hourNumber.format(totalSubmitted)}h submitted` : ''}`}</span></div>
      <div className={styles.overallTrack} role="progressbar" aria-label="Overall off-the-job hours progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={overallPercent == null ? undefined : completedWidth}>
        {totalSubmitted != null && progressTarget != null && <i className={styles.submittedOverall} style={{ left: `${completedWidth}%`, width: `${submittedWidth}%` }} />}
        <i className={styles.completedOverall} style={{ width: `${completedWidth}%` }} />
        {targetPosition != null && <b style={{ left: `${Math.min(100, targetPosition)}%` }} />}
      </div>
      <strong className={overallVariance != null && overallVariance < 0 ? styles.behind : styles.ahead}>
        {variancePercent == null || overallVariance == null ? 'N/A' : `${variancePercent > 0 ? '+' : ''}${variancePercent}% (${signed(overallVariance)}h)`}
      </strong>
    </div>}
  </section>;
}
