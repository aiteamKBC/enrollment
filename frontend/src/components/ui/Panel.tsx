// ============================================================================
// The card surface.
//
// One definition, so "a panel" means one thing. The audit found fourteen radius
// values and forty-plus one-off shadows across these pages, most of them on
// something that was conceptually just this.
//
// Two rules worth stating, because breaking them is what made the old pages feel
// busy:
//
//  - A panel is a meaningful group, not a wrapper for every metric. If it holds
//    one number, it should be a CompactMetric in a row instead.
//  - Panels do not nest. Inside a panel, separate content with spacing, a
//    divider or a subtle background — not another bordered box.
//
// `padding="none"` exists for panels whose child is a DataTable or a list that
// draws its own edges.
// ============================================================================
import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

const PADDING_CLASS = {
  none: '',
  sm: 'p-3',
  md: 'p-4',
  lg: 'p-5',
} as const;

export function Panel({
  children,
  padding = 'md',
  className,
}: {
  children: ReactNode;
  padding?: keyof typeof PADDING_CLASS;
  className?: string;
}) {
  return (
    <section
      className={cn(
        'ui-panel rounded-xl border border-foreground-100/70 bg-background-50 shadow-sm',
        PADDING_CLASS[padding],
        className,
      )}
    >
      {children}
    </section>
  );
}

/**
 * A divider for separating content inside a panel — the alternative to nesting
 * another bordered box.
 */
export function PanelDivider({ className }: { className?: string }) {
  return <hr className={cn('border-t border-foreground-100', className)} />;
}
