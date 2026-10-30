import type { ReactNode } from 'react';
import { aptemHistoricalProgressPresentation, calendarLabel, percentage, type ImportedProgressMetric, type ImportedProgressNormalizedData, type ProgressCard } from './presentation';
import styles from './importedReview.module.css';

const number = (value: number | null, suffix = '') => value === null ? 'Not recorded' : `${Math.round(value).toLocaleString('en-GB')}${suffix}`;
const clamp = (n: number) => Math.min(100, Math.max(0, n));

function Bar({ value, target, label }: { value: number | null; target?: number | null; label: string }) {
  return <div className={styles.bar} role="img" aria-label={`${label}: ${number(value, '%')}${target != null ? `; target ${number(target, '%')}` : ''}`}>
    <span style={{ width: `${clamp(value ?? 0)}%` }} />
    {target != null && <i style={{ left: `${clamp(target)}%` }} />}
  </div>;
}

function Metrics({ entries }: { entries: [string, number | null][] }) {
  return <dl className={styles.metrics}>{entries.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{number(value)}</dd></div>)}</dl>;
}

function Progress({ metric, label }: { metric: ImportedProgressMetric; label: string }) {
  const variance = metric.variancePercentage;
  return <>
    <div className={styles.metricHeading}><strong>{number(metric.percentage, '%')}</strong>
      {variance !== null && <span className={styles.badge} title="Difference from target in percentage points">
        {variance === 0 ? 'On target' : `${number(Math.abs(variance), '%')} ${variance < 0 ? 'Below' : 'Above'}`}
      </span>}
    </div>
    <Bar label={label} value={metric.percentage} target={metric.targetPercentage} />
    <p className={styles.note}>Target: {number(metric.targetPercentage, '%')}</p>
  </>;
}

function Timeline({ dates }: { dates: Extract<ProgressCard, { kind: 'timeline' }>['dates'] }) {
  // Date.UTC is used only for relative placement of supplied calendar components.
  const ordinal = (value: string) => Date.UTC(Number(value.slice(0, 4)), Number(value.slice(5, 7)) - 1, Number(value.slice(8, 10)));
  const start = dates.find(date => date.label === 'Programme start');
  const end = dates.find(date => date.label === 'Apprenticeship end');
  const snapshot = dates.find(date => date.label === 'Review snapshot');
  const elapsed = start && end && snapshot ? percentage(ordinal(snapshot.value) - ordinal(start.value), ordinal(end.value) - ordinal(start.value)) : null;
  return <article className={styles.card}><h4>Programme timeline</h4>
    {dates.length ? <><Bar label="Programme time elapsed at review snapshot" value={elapsed} />
      <ol className={styles.timeline}>{dates.map(date => <li key={date.label}><span className={styles.timelineDot} aria-hidden="true" /><span>{date.label}<strong>{calendarLabel(date.value)}</strong></span></li>)}</ol>
      {snapshot && <p className={styles.note}>Dates reflect the saved review snapshot.</p>}</>
      : <p className={styles.note}>Programme dates were not recorded in this review.</p>}
  </article>;
}

function Activities({ card }: { card: Extract<ProgressCard, { kind: 'activities' }> }) {
  const ratio = percentage(card.completed, card.total);
  return <article className={styles.card}><h4>Learning Plan Activities</h4>
    <div className={styles.activityRing} role="img" aria-label={`${number(card.completed)} of ${number(card.total)} activities completed`}>
      <svg viewBox="0 0 120 120" aria-hidden="true">
        <circle className={styles.ringTrack} cx="60" cy="60" r="52" />
        {ratio !== null && <circle className={styles.ringFill} cx="60" cy="60" r="52" pathLength="100" strokeDasharray={`${clamp(ratio)} 100`} transform="rotate(-90 60 60)" />}
      </svg>
      <div aria-hidden="true"><strong>{number(card.completed)}</strong><span>of {number(card.total)}</span></div>
    </div>
    <Metrics entries={[["Completed", card.completed], ["Submitted", card.submitted], ["Remaining", card.remaining], ["Target", card.target]]} />
  </article>;
}

function HistoricalStandard({ card }: { card: Extract<ProgressCard, { kind: 'standard' }> }) {
  return <article className={styles.card}><h4>Progress</h4><p className={styles.standardTitle}>{card.title}</p>
    <Bar label={card.title} value={card.percentage} target={card.targetPercentage} />
    <div className={styles.metricHeading}><strong>{number(card.percentage, '%')}</strong>
      {card.status && <span className={styles.badge}>{card.status}</span>}
    </div>
  </article>;
}

function HistoricalHours({ card }: { card: Extract<ProgressCard, { kind: 'hours' }> }) {
  return <article className={`${styles.card} ${styles.historicalHours}`}><h4>Off-The-Job Hours</h4>
    <p className={styles.standardTitle}>Overall Progress</p>
    <Bar label="Off-the-job hours" value={card.percentage} />
    <div className={styles.metricHeading}><strong>{number(card.percentage, '%')} <span className={styles.completedHours}>({number(card.completed, 'h')})</span></strong></div>
    <dl className={styles.hours}>{([['Minimum Required', card.minimum], ['Planned Hours (ILR)', card.planned], ['Completed', card.completed], ['Forecast', card.forecast]] as [string, number | null][]).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{number(value, 'h')}</dd></div>)}</dl>
  </article>;
}

export function ImportedProgressCards({ data, renderUnknown, historical = false }: { data: ImportedProgressNormalizedData; renderUnknown: (value: unknown) => ReactNode; historical?: boolean }) {
  const cards = historical ? aptemHistoricalProgressPresentation(data) : data.cards;
  if (!cards.length) return <p className={styles.note}>Learning progress details are unavailable for this review.</p>;
  return <div className={historical ? styles.historicalCards : styles.cards}>{cards.map((card, index) => {
    if (card.kind === 'timeline') return <Timeline key={index} dates={card.dates} />;
    if (historical && card.kind === 'activities') return <Activities key={index} card={card} />;
    if (historical && card.kind === 'standard') return <HistoricalStandard key={index} card={card} />;
    if (historical && card.kind === 'hours') return <HistoricalHours key={index} card={card} />;
    if (card.kind === 'activities') {
      return <article key={index} className={styles.card}><h4>Learning Plan Progress</h4>
        <Progress metric={card} label="Learning Plan Progress" />
        <div className={styles.activities}><h5>Activity completion counts</h5>
          <p>{number(card.completed)} of {number(card.total)} completed</p>
          {historical && <p className={styles.note}>Completed activities out of the recorded total.</p>}
          <Metrics entries={[["Submitted", card.submitted], ["Remaining", card.remaining], ["Target count", card.target]]} />
      </div></article>;
    }
    if (card.kind === 'standard') {
      return <article key={index} className={styles.card}><h4>Standard progress</h4><p className={styles.standardTitle}>{card.title}</p>
        <Progress metric={card} label={card.title} />
      </article>;
    }
    if (card.kind === 'programme') return <article key={index} className={styles.card}><h4>Programme progress</h4>
      <Progress metric={card} label="Programme progress" />
    </article>;
    if (card.kind === 'hours') return <article key={index} className={styles.card}><h4>Off-The-Job Hours</h4>
      <Progress metric={card} label="Off-the-job hours" />
      <dl className={styles.hours}>{([['Minimum required', card.minimum], ['Planned hours (ILR)', card.planned], ['Completed', card.completed], ['Forecast', card.forecast]] as [string, number | null][]).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{number(value, 'h')}</dd></div>)}</dl>
    </article>;
    return <article key={index} className={styles.card}><h4>Additional progress information</h4>{renderUnknown(card.value)}</article>;
  })}</div>;
}
