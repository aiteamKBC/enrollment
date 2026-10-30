import { Link } from 'react-router-dom';
import type { CSSProperties } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import { ProgressBar } from '@/components/ui/ProgressMetric';
import { SkeletonBlock } from '@/components/feature/Skeletons';
import styles from './Overview.module.css';

export function DashboardProgressStat({ href, label, value, summary, valueLabel = 'Current', targetValue, targetLabel = 'Target', percent, accent, loading = false }: {
  href: string; label: string; value: string; summary: string; valueLabel?: string; targetValue?: string;
  targetLabel?: string; percent: number | null; accent: 'purple' | 'green' | 'yellow';
  /** The figures are still being read; show placeholders, never a provisional zero or dash. */
  loading?: boolean;
}) {
  const ringPercent = percent == null ? 0 : Math.min(100, Math.max(0, percent));
  const ringLabel = percent == null ? '—' : `${Number.isInteger(percent) ? percent : Number(percent.toFixed(1))}%`;
  const progressAccent = percent == null ? accent : percent < 50 ? 'red' : percent < 80 ? 'yellow' : 'green';
  if (loading) return <Link to={href} aria-label={`Open ${label}`} aria-busy="true" data-accent={accent} className={`group ${styles.metric}`}>
    <div className={styles.metricTop}>
      <span className={`${styles.metricRing} animate-pulse`} role="img" aria-label={`${label}: loading`} style={{ '--metric-progress': '0%' } as CSSProperties} />
      <div className={styles.metricBody}>
        <div className={styles.metricHeading}><p className={styles.metricLabel}>{label}</p></div>
        <SkeletonBlock className="mt-1.5 h-3 w-20" />
        <span role="status" className="sr-only">Loading {label}…</span>
      </div>
      <AppIcon aria-hidden="true" className={`ri-arrow-right-s-line ${styles.metricArrow}`} />
    </div>
  </Link>;
  return <Link to={href} aria-label={`Open ${label}`} data-accent={progressAccent} className={`group ${styles.metric}`}>
    <div className={styles.metricTop}>
      <span className={styles.metricRing} role="img" aria-label={`${label}: ${ringLabel}`} style={{ '--metric-progress': `${ringPercent}%` } as CSSProperties}>
        <span className={styles.metricRingValue}>{ringLabel}</span>
      </span>
      <div className={styles.metricBody}>
        <div className={styles.metricHeading}><p className={styles.metricLabel}>{label}</p></div>
        <p className={styles.metricDetail}>{summary}</p>
        <dl className={styles.metricA11yData}>
          <div><dt>{valueLabel}</dt><dd>{value}</dd></div>
          {targetValue != null && <div><dt>{targetLabel}</dt><dd>{targetValue}</dd></div>}
        </dl>
        <ProgressBar percent={percent} tone={styles.metricFill} className={styles.metricA11yProgress} />
      </div>
      <AppIcon aria-hidden="true" className={`ri-arrow-right-s-line ${styles.metricArrow}`} />
    </div>
  </Link>;
}
