import { useState } from 'react';
import { Link } from 'react-router-dom';
import { AppIcon } from '@/components/feature/AppIcon';
import { EmptyState } from '@/components/ui/EmptyState';
import { SkeletonBlock } from '@/components/feature/Skeletons';
import { duration, hours, monthLabel, monthStatus } from '@/features/old-otjh/report';
import type { LogMonth, LogPerspective, LogSummary } from './api';
import styles from './monthlyLogs.module.css';
import type { CoachLogYear } from '@/features/coach/monthly-logs/api/monthlyLogsApi';

export function MonthList({ summary, base, perspective, onMonthSelect }: { summary: LogSummary; base: string; perspective: LogPerspective; onMonthSelect?: (month: string) => void }) {
  return perspective === 'coach'
    ? <CoachMonthList summary={summary} base={base} />
    : <LearnerMonthList summary={summary} base={base} perspective={perspective} onMonthSelect={onMonthSelect} />;
}

function LearnerMonthList({ summary, base, perspective, onMonthSelect }: { summary: LogSummary; base: string; perspective: LogPerspective; onMonthSelect?: (month: string) => void }) {
  const [year, setYear] = useState('all');
  const [pendingOnly, setPendingOnly] = useState(false);
  const signature = perspective === 'learner' ? 'student_signature' : 'coach_signature';
  const years = [...new Set(summary.months.map(item => item.month.slice(0, 4)))];
  const selectedYear = years.includes(year) ? year : 'all';
  const yearMonths = summary.months.filter(item => selectedYear === 'all' || item.month.startsWith(selectedYear));
  const pending = yearMonths.filter(item => !item.is_open && !item[signature]);
  const visible = pendingOnly ? pending : yearMonths;
  const groups = new Map<string, LogMonth[]>();
  visible.forEach(item => {
    const key = item.month.slice(0, 4);
    groups.set(key, [...(groups.get(key) || []), item]);
  });
  const closed = summary.months.filter(item => !item.is_open);
  const signed = closed.filter(item => item[signature]).length;
  const total = closed.length;
  const recordedTotal = summary.months.length;
  const percent = total ? Math.round(signed / total * 100) : 0;
  const targetFor = (item: LogMonth) => item.training_plan_target;
  const targetWarnings = [...new Set(summary.months.filter(item => targetFor(item) == null).map(item => item.target_warning).filter(Boolean))];
  const signer = perspective === 'learner' ? 'Learner' : 'Coach';
  const planTotals = summary.training_plan_totals;

  return <div className={styles.index}>
    <header className={`${styles.indexHeader} ${perspective === 'learner' ? styles.learnerIndexHeader : ''}`}>
      <div className={styles.identity}><p className={styles.eyebrow}>Monthly learning record</p>
        <h1>{summary.learner?.name}</h1>
        <div className={styles.learnerMeta}><span>{summary.learner?.programme}</span><span>Coach: {summary.learner?.coach_name || 'Unassigned'}</span></div>
      </div>
      <span className={styles.calendarArt} aria-hidden="true"><img src="/assets/monthly-submission-calendar.png" alt="" /></span>
      <div className={styles.completion}>
        <span className={styles.completionIcon} aria-hidden="true"><AppIcon className="ri-user-line" /></span>
        <div className={styles.completionDetails}>
          <div><span>{signer} signatures</span><strong>{signed}<span> / {total}</span></strong></div>
          <div className={styles.progress} role="progressbar" aria-label={`${signer} signatures saved`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><span style={{ width: `${percent}%` }} /></div>
          <p>{total - signed ? `${total - signed} ${total - signed === 1 ? 'month awaits' : 'months await'} a signature` : total ? 'All closed-month signatures saved' : recordedTotal ? 'Current month in progress' : 'No months recorded yet'}</p>
        </div>
      </div>
    </header>
    {planTotals && <section className={styles.planTotal} aria-label="Training Plan OTJ hours">
      <span className={styles.planTotalIcon} aria-hidden="true"><AppIcon className="ri-time-line" /></span>
      <div><p>Total OTJ hours</p><strong>{hours(planTotals.accepted_hours)} h</strong><span>Accepted</span></div>
      <div><p>Training Plan</p><strong>{planTotals.planned_hours == null ? 'Unavailable' : `${hours(planTotals.planned_hours)} h`}</strong><span>Planned</span></div>
    </section>}
    {targetWarnings.map(warning => <p key={warning} role="status" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">{warning}</p>)}
    {!recordedTotal ? <EmptyState title="No monthly logs yet" description="Your recorded activities will appear here as you complete them." /> : <>
      <div className={styles.toolbar}>
        <div className={styles.filters} role="group" aria-label="Filter monthly logs">
          <button type="button" aria-pressed={!pendingOnly} onClick={() => setPendingOnly(false)}>All months<span>{yearMonths.length}</span></button>
          <button type="button" aria-pressed={pendingOnly} onClick={() => setPendingOnly(true)}>Awaiting signature<span>{pending.length}</span></button>
        </div>
        <label className={styles.yearFilter}><AppIcon className="ri-calendar-2-line" /><span className="sr-only">Filter by year</span>
          <select value={selectedYear} onChange={event => setYear(event.target.value)}><option value="all">All years</option>{years.map(value => <option key={value} value={value}>{value}</option>)}</select>
        </label>
      </div>
      {!visible.length ? <div className={styles.noResults}><AppIcon className="ri-checkbox-circle-line" /><h2>All signatures saved{selectedYear !== 'all' ? ` for ${selectedYear}` : ''}</h2><p>There are no months awaiting a {perspective} signature in this view.</p><button type="button" onClick={() => setPendingOnly(false)}>View all months</button></div>
        : <div className={styles.years}>{[...groups].map(([value, months]) => <section key={value} className={styles.yearGroup} aria-label={`${value} monthly logs`}>
          <header className={styles.yearHeading}><h2>{value}</h2><span>{months.length} {months.length === 1 ? 'month' : 'months'}</span></header>
          <div className={styles.months}>{months.map(item => <article key={item.month} className={styles.month}>
            <div className={styles.monthIdentity}><span className={styles.monthIcon}><AppIcon className="ri-file-text-line" /></span><div><h3>{monthLabel(item.month)}</h3><p>{item.row_count} {item.row_count === 1 ? 'activity' : 'activities'}</p></div></div>
            <dl className={styles.hours}><div><dt>Accepted hours</dt><dd><AppIcon className="ri-time-line" />{duration(item.actual_hours)}</dd></div><div><dt>Target hours</dt><dd><AppIcon className="ri-focus-2-line" />{targetFor(item) == null ? 'Unavailable' : `${hours(targetFor(item))} h`}</dd></div></dl>
            {Number(item.estimated_hours || 0) > 0 && <p className="mt-2 text-xs font-medium text-amber-700">Estimated — approval required: {duration(item.estimated_hours)}</p>}
            <div className={styles.signatures}>{item.is_open ? <span className={styles.unsigned}><AppIcon className="ri-time-line" />Month in progress</span> : <><SignatureState role="Learner" signed={!!item.student_signature} /><SignatureState role="Coach" signed={!!item.coach_signature} /></>}</div>
            <span className={`${styles.status} ${item.status === 'complete' ? styles.complete : ''}`}><AppIcon className={item.status === 'complete' ? 'ri-check-line' : 'ri-time-line'} />{item.is_open ? 'In progress' : monthStatus(item)}</span>
            {onMonthSelect
              ? <button type="button" className={styles.openMonth} onClick={() => onMonthSelect(item.month)} aria-label={`Review month: ${monthLabel(item.month)}`}><span>{item.is_open ? 'View current log' : 'View report'}</span><AppIcon className="ri-arrow-right-line" /></button>
              : <Link className={styles.openMonth} to={`${base}/${item.month}`} aria-label={`Review month: ${monthLabel(item.month)}`}><span>{item.is_open ? 'View current log' : 'View report'}</span><AppIcon className="ri-arrow-right-line" /></Link>}
          </article>)}</div>
        </section>)}</div>}
    </>}
    <p className={styles.scheduleNote}><AppIcon className="ri-information-line" />The current month updates as activities are recorded. Signatures become available after month-end.</p>
  </div>;
}

function learnerInitials(name?: string) {
  return String(name || '').split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0]).join('').toUpperCase() || 'L';
}

export function CoachMonthList({ summary, projection, base, year: controlledYear, onYearChange, pendingOnly: controlledPending, onPendingChange }: { summary?: LogSummary; projection?: CoachLogYear; base: string; year?: string; onYearChange?: (year: string) => void; pendingOnly?: boolean; onPendingChange?: (pending: boolean) => void }) {
  const [year, setYear] = useState('');
  const months = projection ? projection.months.map(item => ({ month: item.month, row_count: item.activities,
    actual_hours: item.acceptedOtjhHours, training_plan_target: item.targetHours, is_open: item.isOpen,
    student_signature: item.learnerSigned, coach_signature: item.coachSigned, target_warning: null })) : summary!.months;
  const [localPending, setLocalPending] = useState(false);
  const pendingOnly = controlledPending ?? localPending;
  const setPendingOnly = onPendingChange ?? setLocalPending;
  const years = projection ? projection.years.map(String) : [...new Set(months.map(item => item.month.slice(0, 4)))];
  const selectedYear = controlledYear ?? (years.includes(year) ? year : years.at(-1) || '');
  const yearMonths = months.filter(item => !selectedYear || item.month.startsWith(selectedYear));
  const pending = yearMonths.filter(item => !item.is_open && !item.coach_signature);
  const visible = pendingOnly ? pending : yearMonths;
  const closed = months.filter(item => !item.is_open);
  const signed = projection?.summary.coachSignatures.completed ?? closed.filter(item => item.coach_signature).length;
  const total = projection?.summary.coachSignatures.total ?? closed.length;
  const percent = total ? Math.round(signed / total * 100) : 0;
  const plan = projection ? { accepted_hours: projection.summary.acceptedOtjhHours, planned_hours: projection.summary.trainingPlanHours } : summary?.training_plan_totals;
  const acceptedHours = plan ? `${hours(plan.accepted_hours)} h` : 'Unavailable';
  const plannedHours = plan?.planned_hours == null ? 'Unavailable' : `${hours(plan.planned_hours)} h`;
  const warnings = [...new Set(months.map(item => item.target_warning).filter(Boolean))];

  return <div className={styles.coachLogOverview}>
    <section className={styles.coachProfileCard} aria-label="Selected learner summary">
      <div className={styles.coachProfileIdentity}>
        <span className={styles.coachProfileAvatar}>{learnerInitials((projection?.learner ?? summary?.learner)?.name)}</span>
        <div><h2>{(projection?.learner ?? summary?.learner)?.name}</h2><p>{(projection?.learner ?? summary?.learner)?.programme}</p></div>
      </div>
      <div className={styles.coachProfileFact}>
        <span>Coach signatures</span><strong>{signed} <small>/ {total}</small></strong>
        <p>{total - signed ? `${total - signed} ${total - signed === 1 ? 'month awaits' : 'months await'} signature` : total ? 'All signatures saved' : 'No closed months yet'}</p>
        <span className="sr-only" role="progressbar" aria-label="Coach signatures saved" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} />
      </div>
      <div className={styles.coachProfileFact}><span>Training plan</span><strong className={styles.coachPlanValue}>{plannedHours}</strong></div>
    </section>

    <section className={styles.coachSummaryCard} aria-labelledby="coach-monthly-summary-heading">
      <h2 id="coach-monthly-summary-heading">Summary</h2>
      <div className={styles.coachSummaryGrid}>
        <div><span className={styles.coachSummaryIcon}><AppIcon className="ri-time-line" /></span><p>Accepted OTJ hours<strong>{acceptedHours}</strong></p></div>
        <div><span className={styles.coachSummaryIcon}><AppIcon className="ri-file-list-3-line" /></span><p>Training plan<strong>{plannedHours}</strong></p></div>
      </div>
    </section>

    {warnings.map(warning => <p key={warning} role="status" className={styles.coachWarning}>{warning}</p>)}

    <section className={styles.coachMonthsCard} aria-label="Learner monthly log reports">
      <div className={styles.coachMonthsToolbar}>
        <div className={styles.coachMonthTabs} role="group" aria-label="Filter monthly logs">
          <button type="button" aria-pressed={!pendingOnly} onClick={() => setPendingOnly(false)}>All months <span>{yearMonths.length}</span></button>
          <button type="button" aria-pressed={pendingOnly} onClick={() => setPendingOnly(true)}>Awaiting signature <span>{pending.length}</span></button>
        </div>
        {years.length > 0 && <label className={styles.coachYearFilter}><span>Year</span><select aria-label="Filter by year" value={selectedYear} onChange={event => (onYearChange ?? setYear)(event.target.value)}>
          {years.map(value => <option key={value} value={value}>{value}</option>)}
        </select></label>}
      </div>

      {!months.length ? <EmptyState title="No monthly logs yet" description="Recorded learner activities will appear here as they are completed." />
        : !visible.length ? <div className={styles.coachNoMonths}><AppIcon className="ri-checkbox-circle-line" /><h3>All signatures saved for {selectedYear}</h3><p>There are no months awaiting a coach signature in this view.</p><button type="button" onClick={() => setPendingOnly(false)}>View all months</button></div>
          : <div className={styles.coachTableScroll}><table className={styles.coachMonthTable}>
            <thead><tr><th scope="col">Month</th><th scope="col">Activities</th><th scope="col">Accepted OTJ hours</th><th scope="col">Target hours</th><th scope="col">Learner signature</th><th scope="col">Coach signature</th><th scope="col">Report</th></tr></thead>
            <tbody>{visible.map(item => <tr key={item.month}>
              <td><strong>{monthLabel(item.month)}</strong></td>
              <td>{item.row_count} {item.row_count === 1 ? 'activity' : 'activities'}</td>
              <td>{duration(item.actual_hours)}</td>
              <td>{item.training_plan_target == null ? 'Unavailable' : `${hours(item.training_plan_target)} h`}</td>
              <td><CoachSignatureState signed={!!item.student_signature} open={!!item.is_open} /></td>
              <td><CoachSignatureState signed={!!item.coach_signature} open={!!item.is_open} /></td>
              <td><Link className={styles.coachReportButton} to={`${base}/${item.month}`} aria-label={`Review month: ${monthLabel(item.month)}`}>{item.is_open ? 'View log' : 'View report'}</Link></td>
            </tr>)}</tbody>
          </table></div>}
      <p className={styles.coachHelperNote}><AppIcon className="ri-information-line" />Signatures become available after month-end.</p>
    </section>
  </div>;
}

function CoachSignatureState({ signed, open }: { signed: boolean; open: boolean }) {
  const label = open ? 'Month in progress' : signed ? 'Signed' : 'Awaiting signature';
  return <span className={styles.coachSignatureState} data-tone={open ? 'progress' : signed ? 'signed' : 'pending'}>{label}</span>;
}

function SignatureState({ role, signed }: { role: string; signed: boolean }) {
  return <span className={signed ? styles.signed : styles.unsigned}><AppIcon className={signed ? 'ri-checkbox-circle-line' : 'ri-user-line'} />{role}<span className="sr-only">: {signed ? 'Signed' : 'Awaiting signature'}</span></span>;
}

export function MonthIndexSkeleton({ perspective }: { perspective: LogPerspective }) {
  return <div className={styles.index} role="status" aria-label="Loading monthly records" aria-busy="true"><span className="sr-only">Loading monthly records…</span><div aria-hidden="true">
    <div className={`${styles.indexHeader} ${perspective === 'learner' ? styles.learnerIndexHeader : ''}`}><div className="space-y-3"><SkeletonBlock className="h-3 w-36" /><SkeletonBlock className="h-7 w-56 max-w-full" /><SkeletonBlock className="h-3 w-64 max-w-full" /></div></div>
    <div className={styles.toolbar}><SkeletonBlock className="h-9 w-64 max-w-full" /></div>
    <div className={styles.yearGroup}><div className={styles.yearHeading}><SkeletonBlock className="h-5 w-16" /></div>{[0, 1, 2, 3].map(item => <div key={item} className={styles.skeletonRow}><SkeletonBlock className="h-10 w-10 rounded-lg" /><SkeletonBlock className="h-4 w-40 max-w-full" /><SkeletonBlock className="ml-auto h-4 w-24" /></div>)}</div>
  </div></div>;
}
