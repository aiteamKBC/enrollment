// ============================================================================
// The one page container.
//
// WorkspaceShell's <main> deliberately carries no padding and no width, so
// before this existed every page picked its own: seven padding recipes, four
// gaps, two max-widths and two background colours across nineteen screens, one
// of them a raw hex.
//
// The max-width is wide because these are operational screens — a coach
// reconciling a hundred learners wants the horizontal space for columns, not a
// reading measure. Text-heavy blocks constrain themselves locally instead.
//
// The background comes from the shell. Pages must not set their own.
// ============================================================================
import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

export function PageContainer({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('page-container mx-auto w-full min-w-0 space-y-3 p-3 md:space-y-4 md:p-6', className)}>
      {children}
    </div>
  );
}
