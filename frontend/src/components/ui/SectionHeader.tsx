// ============================================================================
// Section header.
//
// The heading row inside a panel. Replaces five separate section-shell
// components that each drew the same thing at a different size.
//
// The count sits in the heading rather than in a badge beside it, because
// "Requires review 12" is one fact and rendering it as two competing chips was
// half of why these pages felt busy.
// ============================================================================
import type { ReactNode } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import { cn } from '@/lib/cn';

export function SectionHeader({
  title,
  count,
  description,
  icon,
  actions,
  className,
}: {
  title: string;
  /** Rendered inline after the title. Omit rather than passing 0 when unknown. */
  count?: number;
  description?: string;
  /** Remix icon class. */
  icon?: string;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('section-header flex flex-wrap items-center justify-between gap-3', className)}>
      <div className="min-w-0">
        <h2 className="flex items-center gap-2 text-[15px] font-semibold text-foreground-900">
          {icon ? <AppIcon className={cn(icon, 'text-[16px] text-foreground-400')}></AppIcon> : null}
          <span className="truncate">{title}</span>
          {typeof count === 'number' ? (
            <span className="text-[15px] font-semibold tabular-nums text-foreground-400">
              {count}
            </span>
          ) : null}
        </h2>
        {description ? (
          <p className="mt-0.5 text-[12px] leading-relaxed text-foreground-500">{description}</p>
        ) : null}
      </div>

      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}

/**
 * The small uppercase label above a group of fields or metrics. Distinct from
 * SectionHeader: this one labels a cluster, it does not head a panel.
 */
export function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <p className="text-[12px] font-semibold uppercase tracking-[0.06em] text-foreground-400">
      {children}
    </p>
  );
}
