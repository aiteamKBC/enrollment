import { Fragment, useEffect, useRef, useState, type ReactNode } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import { EmptyState } from '@/components/ui/EmptyState';
import type { Activity, ActivityContent, JournalSummary, MonthDetail } from './api';
import { displayDate, duration, groupActivities, hours, monthLabel } from './report';
import styles from './report.module.css';
import journal from './journal.module.css';
import { MonthBadge, RecordBadge } from './RecordDesign';
import { ActivityExpansion } from './ActivityExpansion';

export function LearnerInformation({ summary, data, actions }: { summary: JournalSummary; data: MonthDetail; actions?: ReactNode }) {
  const fields = [
    { label: 'Learner', value: summary.learner?.name, dateLabel: 'Start date', date: data.profile?.start_date },
    { label: 'Programme', value: summary.learner?.programme, dateLabel: 'First evidence', date: data.profile?.first_evidence_date },
    { label: 'Coach', value: summary.learner?.coach_name, dateLabel: 'Planned end', date: data.profile?.planned_end_date },
  ];
  return <section className={`${journal.card} ${journal.header}`} aria-label="Learner journal">
    <div className={journal.headerTop}>
      <div className={journal.title}><h1 className="font-heading">Learner Journal</h1><MonthBadge month={data} /></div>
      <div className={journal.headerActions}>
        <div className={journal.reportingMonth}><p className={journal.label}>Reporting month</p><h2>{monthLabel(data.month)}</h2></div>
        {actions}
      </div>
    </div>
    <div className={journal.profileGrid}>{fields.map(field => <dl key={field.label} className={journal.profileGroup}>
      <div><dt className={journal.label}>{field.label}</dt><dd>{field.value || '—'}</dd></div>
      <div><dt className={journal.label}>{field.dateLabel}</dt><dd className={journal.profileDate}>{displayDate(field.date)}</dd></div>
    </dl>)}</div>
  </section>;
}

export function MonthlyHours({ data }: { data: MonthDetail }) {
  return <section className={`${journal.card} ${journal.hours}`} aria-label="Monthly hours check">
    <div className={journal.hoursHeading}><h2 className={journal.label}>Monthly hours check</h2><p>Training Plan and LMS figures in one place</p></div>
    <div className={journal.hoursGrid}>
      <div className={journal.metric}><h3 className={journal.label}>Training plan target</h3>
        <p className={journal.metricValue}>{data.training_plan_target == null ? '—' : `${hours(data.training_plan_target)} h`}</p>
        <p className={journal.metricNote}>{data.training_plan_target == null ? 'No monthly target available' : 'Monthly target from your training plan'}</p></div>
      <div className={`${journal.metric} ${journal.acceptedMetric}`}><h3 className={journal.label}>LMS actual</h3>
        <p className={journal.metricValue}>{duration(data.actual_hours)}</p><p className={journal.metricNote}>Hours accepted on this report</p></div>
    </div>
    <p className={journal.hoursNote}>Not accepted <strong>{duration(data.not_accepted_hours)}</strong>
      {Number(data.estimated_hours || 0) > 0 && <> · Estimated — approval required <strong>{duration(data.estimated_hours)}</strong></>}</p>
  </section>;
}

const badge = styles.detailBadge;
function ActivityDescription({ row, expanded, onOpen }: { row: Activity; expanded: boolean; onOpen: (documentId?: number) => void }) {
  return <div className={styles.activityDescription}><h3 className="text-[13px] font-semibold leading-relaxed"><button type="button" className={styles.activityTrigger} aria-expanded={expanded} aria-controls={`activity-content-${row.id}`} onClick={() => onOpen()}>
    <AppIcon className={`${expanded ? 'ri-arrow-down-s-line' : 'ri-arrow-right-s-line'} mt-0.5 shrink-0`} /><span>{row.title}</span></button></h3>
    {row.group_name && <p className="text-[11px] text-foreground-500">{row.group_name}</p>}
    {row.component_name && <p className="text-[11px] text-foreground-500">Component: {row.component_name}</p>}
    {(row.duration_minutes != null || row.page_count != null || row.results.length > 0) && <div className={styles.activityBadges}>
      {row.duration_minutes != null && <span className={badge}>Duration {duration(Number(row.duration_minutes) / 60)}</span>}
      {row.page_count != null && <span className={badge}>{row.page_count} pages</span>}
      {row.results.map(result => <span key={`${result.activity_id}-${result.group_id}`} className={badge}>{result.status || 'Status not recorded'}
        {result.quiz_score != null ? ` · Score ${result.quiz_score}/${result.quiz_maximum_score ?? '—'}` : ''}
        {result.quiz_attempt_number ? ` · Attempt ${result.quiz_attempt_number}` : ''}</span>)}
    </div>}
    <div className={styles.activityNotes}>
      {row.completion_note && <p className="whitespace-pre-wrap">{row.completion_note}</p>}
      <p>Planned: {duration(row.planned_hours)}</p>
    </div>
    {row.documents.map(doc => <div key={doc.id} className={styles.document}>
      <span className={styles.documentName}><AppIcon className="ri-file-text-line shrink-0 text-foreground-400" /><span>{doc.display_name}</span></span>
      {doc.url && <span className={styles.documentActions}><button type="button" onClick={() => onOpen(doc.id)} aria-label={`Open ${doc.display_name}`}>Open</button>
        <a href={doc.url} aria-label={`Download ${doc.display_name}`}>Download</a></span>}
    </div>)}
  </div>;
}

export function ActivityLog({ data, aptemId, loadContent, contentScope, initialSourceRef }: { data: MonthDetail; aptemId?: number;
  loadContent?: (rowId: number) => Promise<ActivityContent>; contentScope?: string; initialSourceRef?: string }) {
  const matches = initialSourceRef ? data.rows.filter(row => row.source_ref === initialSourceRef) : [];
  const focusedRowId = matches.length === 1 ? matches[0].id : null;
  const [expanded, setExpanded] = useState<{ rowId: number; documentId?: number } | null>(() => focusedRowId == null ? null : { rowId: focusedRowId });
  const trigger = useRef<HTMLElement | null>(null);
  const section = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (focusedRowId == null) return;
    setExpanded({ rowId: focusedRowId });
    const button = section.current?.querySelector<HTMLButtonElement>(`button[aria-controls="activity-content-${focusedRowId}"]`);
    trigger.current = button ?? null;
    button?.focus({ preventScroll: true });
    button?.closest('tr')?.scrollIntoView?.({ block: 'start' });
  }, [focusedRowId]);
  const open = (rowId: number, documentId?: number) => {
    trigger.current = document.activeElement as HTMLElement;
    setExpanded(previous => previous?.rowId === rowId && documentId === undefined ? null : { rowId, documentId });
  };
  const close = () => { setExpanded(null); trigger.current?.focus({ preventScroll: true }); };
  const total = data.total_actual_hours == null ? null : duration(data.total_actual_hours);
  return <section ref={section} className={`${journal.card} ${journal.activityLog}`} aria-label="Activity log">
    <div className={journal.sectionHeading}><div><h2 className="font-heading">Activity Log</h2><p>Activities recorded on this month’s report</p></div>
      <RecordBadge>{data.rows.length} {data.rows.length === 1 ? 'activity' : 'activities'}</RecordBadge></div>
    {initialSourceRef && matches.length !== 1 && <p role="status" className="px-5 py-4 text-sm text-foreground-600">
      {matches.length ? 'More than one record is linked to this activity. Please select the correct record below.' : 'This activity is not recorded in this month’s log.'}
    </p>}
    {data.rows.length === 0 ? <EmptyState title="No activities are available for this month" description="Please contact your coach." /> : <>
      <div className={journal.tableCaption}><span>Monthly Activity Log</span></div>
      <div className={styles.activityTableWrap}><table className={styles.activityTable} aria-label="Monthly activity log">
        <thead><tr>{['Date', 'Category', 'Activity', 'Timestamp', 'Actual', 'KSB scope'].map(title => <th scope="col" key={title}>{title}</th>)}</tr></thead>
        {groupActivities(data.rows).map(group => <tbody key={group.label} aria-label={group.label}>
          <tr className={styles.groupRow}><th scope="rowgroup" colSpan={6}>
            <h3>{group.label === 'Activities' ? 'LMS activities' : group.label} <span>({group.activities.length})</span></h3>
            {group.label === 'Assignments' && <span className={styles.groupNote}>One row per assignment · submissions, assessment documents and hours</span>}
          </th></tr>
          {group.activities.map(row => <Fragment key={row.id}><tr className={styles.activityRow} style={{ scrollMarginTop: '6rem' }}>
            <td data-label="Date"><span className="whitespace-nowrap font-mono text-[11px]">{displayDate(row.activity_date)}</span></td>
            <td data-label="Category"><span className="text-[12px] text-foreground-600">{row.category}</span></td>
            <td className={styles.activityCell}><ActivityDescription row={row} expanded={expanded?.rowId === row.id} onOpen={documentId => open(row.id, documentId)} /></td>
            <td data-label="Timestamp"><span className="font-mono text-[11px]">{row.timestamp_label || row.activity_time || '—'}</span></td>
            <td data-label="Actual"><span className={`${styles.actualValue} whitespace-nowrap font-mono text-[12px] font-medium`}>{row.actual_pending ? 'Pending' : duration(row.actual_hours)}</span>
              <span className={`mt-1 block text-[10px] ${row.actual_pending || row.actual_estimated ? 'text-amber-700' : row.accepted ? 'text-emerald-700' : 'text-foreground-500'}`}>{row.actual_pending ? 'Actual pending' : row.actual_estimated ? 'Estimated — approval required' : row.actual_status_label || (row.accepted ? 'Accepted' : 'Not accepted')}</span></td>
            <td data-label="KSB scope"><div className="flex flex-wrap gap-1">{row.ksb_codes?.length ? row.ksb_codes.map(code => <span key={code} className={styles.ksbBadge} data-kind={code.charAt(0).toUpperCase()}>{code}</span>) : <span className="text-foreground-400">—</span>}</div></td>
          </tr>{expanded?.rowId === row.id && <tr className={styles.expansionRow}><td colSpan={6} id={`activity-content-${row.id}`}>
            <ActivityExpansion key={`${row.id}-${expanded.documentId ?? 'content'}`} row={row} month={data.month} aptemId={aptemId} initialDocumentId={expanded.documentId} onClose={close} loadContent={loadContent} contentScope={contentScope} />
          </td></tr>}</Fragment>)}
        </tbody>)}
      </table></div></>}
    <div className={journal.totals}>
      <span>{data.rows.length} activities</span><span>Planned <strong>{duration(data.planned_hours)}</strong></span>
      {total && <span>Total actual <strong>{total}</strong></span>}<span>Accepted <strong>{duration(data.actual_hours)}</strong></span>
    </div>
  </section>;
}
