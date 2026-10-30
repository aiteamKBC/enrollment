// ============================================================================
// Empty states.
//
// Twenty-four variants existed, from `py-3.5` to `py-24` — a sevenfold spread,
// so the same "nothing here" was a footnote on one page and half a screen on
// another. Two sizes now: `sm` inside a panel, `md` where the queue itself is
// empty. Neither stretches to fill the viewport.
//
// The variants exist because the next action differs and only one of them is
// the user's fault:
//
//   empty       — there is genuinely nothing yet. Explain when it will fill.
//   no-matches  — the filters excluded everything. Offer to clear them.
//   error       — the fetch failed. Offer to retry.
//
// Rendering all three as one line of grey text, which is what most pages did,
// leaves a coach unable to tell "you have no absence reports" from "we could not
// load your absence reports".
// ============================================================================
import type { ReactNode } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import { cn } from '@/lib/cn';
import { toneStyle, type StatusTone } from '@/lib/statusTone';

type EmptyVariant = 'empty' | 'no-matches' | 'error';

const VARIANT_DEFAULTS: Record<EmptyVariant, { icon: string; tone: StatusTone }> = {
  empty: { icon: 'ri-inbox-line', tone: 'brand' },
  'no-matches': { icon: 'ri-filter-off-line', tone: 'neutral' },
  error: { icon: 'ri-error-warning-line', tone: 'critical' },
};

export function EmptyState({
  variant = 'empty',
  title,
  description,
  icon,
  action,
  size = 'md',
  className,
}: {
  variant?: EmptyVariant;
  title: string;
  /** Why it is empty, or when it will fill. One or two lines. */
  description?: string;
  /** Overrides the variant's icon. */
  icon?: string;
  /** The next action — Clear filters, Try again. */
  action?: ReactNode;
  size?: 'sm' | 'md';
  className?: string;
}) {
  const defaults = VARIANT_DEFAULTS[variant];
  const style = toneStyle(defaults.tone);

  return (
    <div
      className={cn(
        'ui-empty-state flex flex-col items-center justify-center px-6 text-center',
        size === 'md' ? 'py-10' : 'py-7',
        className,
      )}
    >
      <span
        className={cn(
          'mb-3 flex items-center justify-center rounded-full',
          style.bg,
          style.text,
          size === 'md' ? 'h-11 w-11' : 'h-9 w-9',
        )}
      >
        <AppIcon className={cn(icon || defaults.icon, size === 'md' ? 'text-[18px]' : 'text-[16px]')}></AppIcon>
      </span>

      <p className="text-[13px] font-semibold text-foreground-900">{title}</p>
      {description ? (
        <p className="mt-1 max-w-sm text-[12px] leading-relaxed text-foreground-500">{description}</p>
      ) : null}
      {action ? (
        <div className="mt-3.5 flex flex-wrap items-center justify-center gap-2">{action}</div>
      ) : null}
    </div>
  );
}

/**
 * The retry / clear-filters button that goes in `action`. Here so the two
 * recovery affordances look the same everywhere they appear.
 */
export function EmptyStateAction({
  label,
  icon,
  onClick,
  emphasis = 'secondary',
}: {
  label: string;
  icon?: string;
  onClick: () => void;
  emphasis?: 'primary' | 'secondary';
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'inline-flex h-9 items-center gap-1.5 rounded-lg px-3.5 text-[12px] font-semibold transition',
        emphasis === 'primary'
          ? 'primary-action bg-primary-600 text-white hover:bg-primary-700'
          : 'border border-foreground-200 bg-background-50 text-foreground-700 hover:border-foreground-300',
      )}
    >
      {icon ? <AppIcon className={icon}></AppIcon> : null}
      {label}
    </button>
  );
}
