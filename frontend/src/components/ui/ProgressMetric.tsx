// ============================================================================
// Progress representation.
//
// A bar whose colour comes from how healthy the figure is, not from which metric
// it belongs to — so a column of bars in a table can be scanned vertically for
// outliers without a legend.
//
// ProgressMetric pairs the bar with its number and, where it matters, the
// interpretation: "42 / 60 hrs" tells a coach what happened, "12.8 hrs behind
// target" tells them what to do about it.
// ============================================================================
import { memo, type ReactNode } from 'react';
import { cn } from '@/lib/cn';
import { progressTone, toneStyle, type StatusTone } from '@/lib/statusTone';

export const ProgressBar = memo(function ProgressBar({
  percent,
  height = 'h-1.5',
  tone,
  className,
}: {
  percent: number | null;
  height?: string;
  /** Overrides the health-derived colour. Pass a `bg-*` class. */
  tone?: string;
  className?: string;
}) {
  return (
    <div
      className={cn('w-full overflow-hidden rounded-full bg-background-200', height, className)}
      role="progressbar"
      aria-valuenow={percent ?? undefined}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        className={cn(height, 'rounded-full transition-[width] duration-500', tone || progressTone(percent))}
        // Keep a true zero empty; small positive values remain visible.
        style={{ width: `${percent === null || percent <= 0 ? 0 : Math.max(2, Math.min(100, percent))}%` }}
      />
    </div>
  );
});

export const ProgressMetric = memo(function ProgressMetric({
  label,
  value,
  percent,
  note,
  noteTone = 'neutral',
  className,
}: {
  label?: string;
  /** The figure as a coach reads it — "42 / 60 hrs", "87%". */
  value: string;
  percent: number | null;
  /** The interpretation. This is what makes the number actionable. */
  note?: ReactNode;
  noteTone?: StatusTone;
  className?: string;
}) {
  return (
    <div className={cn('min-w-0', className)}>
      {label ? (
        <p className="text-[12px] font-medium uppercase tracking-[0.06em] text-foreground-400">
          {label}
        </p>
      ) : null}
      <p className="mt-0.5 text-[13px] font-semibold tabular-nums text-foreground-900">{value}</p>
      <ProgressBar percent={percent} className="mt-1.5" />
      {note ? (
        <p
          className={cn(
            'mt-1 text-[12px] leading-snug',
            noteTone === 'neutral' ? 'text-foreground-500' : toneStyle(noteTone).text,
          )}
        >
          {note}
        </p>
      ) : null}
    </div>
  );
});
