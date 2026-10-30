import type { ReactNode } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import type { MonthState } from './api';
import { monthStatus } from './report';
import styles from './design.module.css';

export function RecordBadge({ children, tone = 'brand' }: { children: ReactNode; tone?: 'brand' | 'positive' | 'pending' | 'neutral' }) {
  return <span className={`${styles.badge} ${tone === 'brand' ? '' : styles[tone]}`}>{children}</span>;
}

export function MonthBadge({ month }: { month: MonthState }) {
  return <RecordBadge tone={month.status === 'complete' ? 'positive' : month.can_complete ? 'brand' : 'pending'}>{monthStatus(month)}</RecordBadge>;
}

export function RecordProgress({ completed, total }: { completed: number; total: number }) {
  const percent = total ? Math.min(100, Math.max(0, Math.round(completed / total * 100))) : 0;
  return <div role="progressbar" aria-label="Months completed" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100} className={styles.progress}>
    <div style={{ width: `${percent}%` }} />
  </div>;
}

export function SignatureChip({ signed, label }: { signed: boolean; label: string }) {
  return <span className={signed ? styles.signed : undefined}>
    <AppIcon className={signed ? 'ri-checkbox-circle-line' : 'ri-time-line'} />{label}
    <span className="sr-only">{signed ? 'Signed' : 'Awaiting signature'}</span>
  </span>;
}
