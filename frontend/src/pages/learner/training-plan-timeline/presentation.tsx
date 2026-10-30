import { hours, dateLabel, monthLabel, sessionTime, moduleStatus } from './presentation.helpers';
import type { TimelineModule } from './model';
import styles from './trainingPlan.module.css';
export function Meter({ value, label }: { value: number | null; label: string }) {
  return <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={value ?? undefined} className={styles.meter}><span style={{ width: `${value ?? 0}%` }} /></div>;
}
export function State({ value }: { value: string }) {
  return <span className={`${styles.state} ${['Completed', 'Attended'].includes(value) ? styles.positive : ['Not attended', 'Overdue'].includes(value) ? styles.warning : ''}`}>{value}</span>;
}

