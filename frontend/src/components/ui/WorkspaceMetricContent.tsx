import type { ReactNode } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';

/** The compact number-first metric used by the Super Admin dashboard. */
export function WorkspaceMetricContent({ label, value, note, icon, iconClassName = '', valueClassName = 'text-primary-800', valuePosition = 'inline' }: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  icon?: string;
  iconClassName?: string;
  valueClassName?: string;
  valuePosition?: 'inline' | 'end' | 'stacked';
}) {
  if (valuePosition === 'stacked') {
    return (
      <div className="workspace-metric-content workspace-metric-stacked flex min-w-0 flex-col items-center gap-2 text-center">
        <p className="workspace-metric-label text-xs font-medium leading-snug text-foreground-700">{label}</p>
        <p className={`workspace-metric-value text-2xl font-semibold leading-tight tabular-nums ${valueClassName}`}>{value}</p>
        {note && <p className="workspace-metric-note text-[10px] leading-snug text-foreground-500">{note}</p>}
      </div>
    );
  }
  return (
    <div className="workspace-metric-content flex w-full min-w-0 items-center gap-3">
      {icon && <span className={`workspace-metric-icon flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary-100/60 text-primary-600 shadow-md shadow-primary-900/10 ring-1 ring-inset ring-primary-200/60 ${iconClassName}`}>
        <AppIcon name={icon} className="h-5 w-5" aria-hidden="true" />
      </span>}
      <div className={`min-w-0 ${valuePosition === 'end' ? 'flex-1' : ''}`}>
        {valuePosition === 'inline' && <p className={`font-heading text-xl font-semibold leading-none tabular-nums ${valueClassName}`}>{value}</p>}
        <p className={`${valuePosition === 'inline' ? 'mt-1.5' : ''} text-[10px] font-medium leading-tight text-foreground-500`}>{label}</p>
        {note && <p className="workspace-metric-note mt-0.5 text-[10px] leading-snug text-foreground-400">{note}</p>}
      </div>
      {valuePosition === 'end' && <p className={`workspace-metric-value flex min-h-10 min-w-10 max-w-[45%] shrink-0 items-center justify-center break-words rounded-xl bg-primary-100/60 px-2 py-1 text-center font-heading text-xl font-semibold leading-tight tabular-nums shadow-md shadow-primary-900/10 ring-1 ring-inset ring-primary-200/60 ${valueClassName}`}>{value}</p>}
    </div>
  );
}
