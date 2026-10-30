import { Loader2, RefreshCw } from 'lucide-react';
import type { CSSProperties } from 'react';
import { useId } from 'react';
import type { ReviewKsbProgress, ReviewProgressMetric, ReviewProgressSnapshot, ReviewRagHistoryEntry } from '@/api/reviewInstances';

/**
 * A Progress Review's Learning Progress area.
 *
 * Purely a renderer for what the backend froze: it never derives a percentage
 * the snapshot does not carry, and never falls back to the learner's current
 * figures. A review with no snapshot says so, which is the honest state for
 * one that was completed without ever being calculated.
 */

function percentLabel(value: number | null): string {
  return value === null || value === undefined ? 'Not recorded' : `${Math.round(value)}%`;
}

function dateLabel(value: string | null | undefined, withTime = false): string {
  if (!value) return 'Not recorded';
  // The API formats timestamps without an offset; those timestamps are UTC.
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) || !value.includes(':')
    ? value
    : `${value.replace(' ', 'T')}Z`;
  const parsed = new Date(normalized);
  if (!Number.isFinite(parsed.getTime())) return value;
  return new Intl.DateTimeFormat('en-GB', {
    day: 'numeric', month: 'short', year: 'numeric',
    ...(withTime ? { hour: '2-digit', minute: '2-digit' } : {}),
    timeZone: 'Europe/London',
  }).format(parsed);
}

function clamp(value: number | null): number | null {
  return value === null || value === undefined ? null : Math.max(0, Math.min(value, 100));
}

function varianceLabel(metric: Pick<ReviewProgressMetric, 'variancePercent' | 'varianceDirection'>): string {
  const variance = metric.variancePercent;
  if (variance === null || variance === undefined || !Number.isFinite(variance)) return 'Target unavailable';
  if (Math.round(Math.abs(variance)) === 0) return 'On target';
  return `${Math.abs(Math.round(variance))}% ${metric.varianceDirection === 'below' ? 'Below' : 'Above'}`;
}

function markerStyle(percent: number): CSSProperties {
  if (percent <= 0) return { left: 0 };
  if (percent >= 100) return { right: 0 };
  return { left: `${percent}%`, transform: 'translateX(-50%)' };
}

function labelStyle(percent: number): CSSProperties {
  if (percent <= 5) return { left: 0 };
  if (percent >= 95) return { right: 0 };
  return { left: `${percent}%`, transform: 'translateX(-50%)' };
}

function MetricBar({ label, metric, tone = 'teal' }: {
  label: string;
  metric: ReviewProgressMetric;
  tone?: 'teal' | 'blue';
}) {
  const actual = clamp(metric?.actualPercent ?? null);
  const expected = clamp(metric?.expectedPercent ?? null);
  return (
    <div className="min-w-0" data-testid={`progress-metric-${label.toLowerCase().replace(/[^a-z]+/g, '-')}`}>
      <p className="text-[14px] font-semibold leading-snug text-[#123f5a]">{label}</p>
      <div
        className="relative mt-3 h-[18px] w-full border border-[#9ca3a8] bg-[#d7d7d7]"
        role="img"
        aria-label={`${label}: ${percentLabel(metric?.actualPercent ?? null)} against an expected ${percentLabel(metric?.expectedPercent ?? null)}`}
      >
        <div
          className={`h-full ${tone === 'blue' ? 'bg-[#174ed4]' : 'bg-[#17a89f]'}`}
          style={{ width: `${actual ?? 0}%` }}
        />
        {expected === null ? null : (
          <span
            aria-hidden
            data-testid="target-marker"
            className="absolute -top-3 -bottom-3 z-10 w-[3px] bg-black"
            style={markerStyle(expected)}
          />
        )}
      </div>
      <div className="relative mt-2 h-5 text-[12px] font-medium text-[#30383d]">
        <span>0%</span>
        {actual === null ? null : (
          <span className="absolute top-0 whitespace-nowrap" style={labelStyle(actual)}>{percentLabel(metric.actualPercent)}</span>
        )}
      </div>
      <p className="mt-0.5 text-center text-[13px] font-medium text-[#30383d]">{varianceLabel(metric)}</p>
    </div>
  );
}

function KsbBar({ metric }: { metric: ReviewKsbProgress | null | undefined }) {
  const label = metric?.title || 'Apprenticeship Standard progress';
  if (!metric?.available || metric.actualPercent === null || metric.expectedPercent === null) {
    return (
      <div className="min-w-0" data-testid="ksb-progress-unavailable">
        <p className="text-[14px] font-semibold leading-snug text-[#123f5a]">{label}</p>
        <div className="mt-3 h-[18px] border border-[#b8bdc1] bg-[#e1e3e5]" aria-hidden />
        <p className="mt-2 text-center text-[12px] text-foreground-500">
          {metric?.reason || 'KSB progress was not available in this snapshot.'}
        </p>
      </div>
    );
  }
  return <MetricBar label={label} metric={{ actual: null, expected: null, planned: null, ...metric, variancePercent: metric.variancePercent ?? metric.actualPercent - metric.expectedPercent, varianceDirection: metric.varianceDirection ?? (metric.actualPercent >= metric.expectedPercent ? 'above' : 'below') }} tone="blue" />;
}

function LearningPlanDonut({ percent }: { percent: number | null }) {
  const visible = clamp(percent);
  const radius = 66;
  const circumference = 2 * Math.PI * radius;
  return (
    <div className="flex flex-col items-center">
      <p className="mb-5 text-center text-[18px] font-medium text-[#174c67]">Learning Plan Progress</p>
      <svg width="176" height="176" viewBox="0 0 176 176" role="img" aria-label={`Learning Plan Progress: ${percentLabel(percent)}`} className="shrink-0">
        <circle cx="88" cy="88" r={radius} fill="none" stroke="#dce4ec" strokeWidth="22" />
        {visible === null ? null : (
          <circle cx="88" cy="88" r={radius} fill="none" stroke="#174ed4" strokeWidth="22" strokeDasharray={`${circumference * visible / 100} ${circumference}`} transform="rotate(-90 88 88)" />
        )}
        <text x="88" y="96" textAnchor="middle" className="fill-[#111827] text-[29px] font-medium">{percentLabel(percent)}</text>
      </svg>
    </div>
  );
}

function RagHistory({ entries }: { entries: ReviewRagHistoryEntry[] }) {
  if (!entries.length) return null;
  return (
    <div className="mt-4 border-t border-background-200 pt-4">
      <h4 className="text-[12px] font-bold uppercase tracking-[0.12em] text-foreground-400">RAG status</h4>
      <ul className="mt-2 space-y-1.5">
        {entries.map((entry) => (
          <li key={entry.reviewInstanceId} className="flex flex-wrap items-baseline justify-between gap-2 text-[13px]">
            <span className="font-semibold text-foreground-700">
              {entry.reviewName || 'Progress Review'}
              {entry.targetDate ? ` - ${dateLabel(entry.targetDate)}` : ''}
            </span>
            <span className="text-foreground-600">{entry.rag || 'None'}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ReviewProgressPanel({
  snapshot,
  ragHistory = [],
  canCalculate,
  showCalculateAction = canCalculate,
  calculating,
  disabled = false,
  onCalculate,
}: {
  snapshot: ReviewProgressSnapshot | null | undefined;
  ragHistory?: ReviewRagHistoryEntry[];
  /** False once the review reaches the signature step -- the figures a party
   *  is signing must not move underneath them. */
  canCalculate: boolean;
  /** Opt in to showing an unavailable action without granting execution. */
  showCalculateAction?: boolean;
  calculating: boolean;
  disabled?: boolean;
  onCalculate: () => void;
}) {
  const actionHelpId = useId();
  return (
    <section aria-label="Learning progress" className="rounded-2xl border border-background-200 bg-background-50 p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-base font-bold text-foreground-900">Learning Progress</h3>
          {snapshot ? (
            <p className="mt-1 text-[12px] text-foreground-500">
              Calculated from {dateLabel(snapshot.calculatedFrom)} · Calculated at {dateLabel(snapshot.calculatedAt, true)}
            </p>
          ) : (
            <p className="mt-1 text-[13px] text-foreground-500">No progress snapshot calculated yet.</p>
          )}
        </div>
        {showCalculateAction ? (
          <button
            type="button"
            onClick={() => { if (canCalculate && !calculating && !disabled) onCalculate(); }}
            disabled={!canCalculate || calculating || disabled}
            aria-describedby={!canCalculate ? actionHelpId : undefined}
            className="inline-flex h-9 shrink-0 items-center justify-center gap-2 rounded-lg border border-background-300 bg-white px-4 text-xs font-bold text-foreground-700 shadow-sm transition hover:bg-background-100 disabled:opacity-60"
          >
            {calculating
              ? <Loader2 size={15} className="animate-spin" />
              : <RefreshCw size={15} />}
            {calculating ? 'Calculating...' : snapshot ? 'Recalculate' : 'Calculate'}
          </button>
        ) : null}
      </div>
      {showCalculateAction && !canCalculate && <p id={actionHelpId} className="mt-1 text-xs text-foreground-500 sm:text-right">Available to the assigned Coach only.</p>}

      {snapshot ? (
        <div className="mt-5 overflow-hidden border border-[#ccd5df] bg-white">
          <div className="grid md:grid-cols-[34%_66%]">
            <div className="flex min-h-[350px] items-center justify-center px-5 py-8 md:border-r md:border-[#ccd5df]">
              <LearningPlanDonut percent={snapshot.programmeProgress?.actualPercent ?? null} />
            </div>
            <div className="grid gap-7 border-t border-[#ccd5df] px-5 py-7 md:border-t-0 md:px-7">
              <KsbBar metric={snapshot.ksbProgress} />
              <MetricBar label="Off-the-job hours progress" metric={snapshot.offTheJobHours} />
              <MetricBar label="Programme progress" metric={snapshot.programmeProgress} />
            </div>
          </div>
        </div>
      ) : !showCalculateAction ? (
        <p className="mt-3 text-[13px] text-foreground-500">No progress was calculated for this review.</p>
      ) : null}

      <RagHistory entries={ragHistory} />
    </section>
  );
}
