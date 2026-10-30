// ============================================================================
// Shared chrome for the Super Admin console pages.
//
// Every page in this section is the same shape — workspace shell, hero banner
// with headline figures, then a body that is loading, failed, empty or a table.
// Before this, each of those states was re-implemented per page (and several
// pages simply had no failure state, because their fixtures could not fail).
// Pulling it here means a page module is now just "fetch X, render X".
// ============================================================================
import { type ReactNode } from 'react';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { roleNavMap } from '@/mocks/navigation';
import { useAuth } from "@/hooks/useAuth.helpers";
import { RowsSkeleton } from '@/components/feature/Skeletons';
import { AppIcon } from '@/components/feature/AppIcon';
import { WorkspaceHeroBanner } from '@/components/feature/WorkspaceHeroBanner';



export interface HeroStat {
  label: string;
  value: ReactNode;
}

/**
 * Page frame: shell + hero. `stats` render as the tiles on the right of the
 * banner; pass figures that come from the response, not constants.
 */
export function AdminPage({
  title, subtitle, icon, heroTitle, heroBlurb, stats, actions, children, workspaceRole = "admin",
}: {
  workspaceRole?: "admin" | "compliance" | "curriculum";
  title: string;
  subtitle: string;
  icon: string;
  heroTitle: string;
  heroBlurb: ReactNode;
  stats?: HeroStat[];
  actions?: ReactNode;
  children: ReactNode;
}) {
  const { auth } = useAuth();
  const adminNav = roleNavMap[workspaceRole];
  return (
    <WorkspaceShell
      role={workspaceRole}
      roleLabel={adminNav.label}
      navItems={adminNav.items}
      workspaceLabel={adminNav.workspaceLabel}
      pageTitle={title}
      pageSubtitle={subtitle}
      userName={auth.account?.displayName || auth.user?.fullName || 'Platform Admin'}
      userRole={workspaceRole === "admin" ? "Super Administrator" : adminNav.label}
    >
      <div className="admin-console-page min-w-0 space-y-4 p-3 md:space-y-5 md:p-6 [&_table_th]:!bg-primary-50/60 [&_table_th]:!font-semibold [&_table_th]:!text-primary-700 [&_tbody_tr:hover]:!bg-primary-50/40">
        <AdminPageHeader title={heroTitle} description={heroBlurb} icon={icon} stats={stats} actions={actions} />
        {children}
      </div>
    </WorkspaceShell>
  );
}

/** Presentation shared by the console and the certificate editor. */
export function AdminPageHeader({ title, description, icon, stats, actions, eyebrow = 'Administration' }: {
  title: string;
  description: ReactNode;
  icon: string;
  stats?: HeroStat[];
  actions?: ReactNode;
  eyebrow?: string;
}) {
  return (
    <WorkspaceHeroBanner heading="h1" title={title} description={description} icon={icon} eyebrow={eyebrow} stats={stats} actions={actions} />
  );
}

/** Panel that resolves loading / error / empty before rendering its children. */
export function DataPanel({
  loading, error, empty, emptyMessage, emptyIcon = 'ri-inbox-line', onRetry, children, className = '', skeleton,
}: {
  loading: boolean;
  error: string | null;
  empty?: boolean;
  emptyMessage?: string;
  emptyIcon?: string;
  onRetry?: () => void;
  children: ReactNode;
  className?: string;
  /** Override the placeholder when the panel holds something other than rows. */
  skeleton?: ReactNode;
}) {
  if (loading) {
    // Skeleton rather than a spinner: this panel is the body of every admin
    // console page, so what is arriving is always a list of things — and the
    // page keeps its height instead of collapsing and jumping back.
    return (
      <div className={`rounded-2xl border border-[var(--kbc-border)] bg-[var(--kbc-surface)] p-5 ${className}`}>
        {skeleton ?? <RowsSkeleton rows={5} />}
      </div>
    );
  }
  if (error) {
    return (
      <div className={`bg-red-50 rounded-xl border border-red-200/60 p-8 text-center ${className}`}>
        <span className="w-10 h-10 rounded-xl bg-red-100 flex items-center justify-center mx-auto mb-3">
          <AppIcon className="ri-error-warning-line text-red-600 text-lg"></AppIcon>
        </span>
        <p className="text-sm font-semibold text-red-900">Could not load this page</p>
        <p className="text-[12px] text-red-700 mt-1 max-w-md mx-auto">{error}</p>
        {onRetry && (
          <button onClick={onRetry} className="mt-4 cursor-pointer rounded-xl border border-red-200 bg-red-50/50 px-4 py-2 text-xs font-semibold text-red-700 transition-colors hover:bg-red-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500">
            Try again
          </button>
        )}
      </div>
    );
  }
  if (empty) {
    return (
      <div className={`rounded-2xl border border-[var(--kbc-border)] bg-[var(--kbc-surface)] p-10 text-center ${className}`}>
        <span className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-xl bg-primary-100/60 text-primary-600 shadow-md shadow-primary-900/10">
          <AppIcon name={emptyIcon} className="text-lg" aria-hidden="true" />
        </span>
        <p className="text-[13px] text-foreground-500">{emptyMessage || 'Nothing to show yet.'}</p>
      </div>
    );
  }
  return <>{children}</>;
}

/** Status pill with the console's four tones. */
export function StatusBadge({ status, tone }: { status: string; tone: 'ok' | 'bad' | 'warn' | 'neutral' }) {
  const map = {
    ok: 'bg-emerald-50/50 text-emerald-700 border-emerald-200/60',
    bad: 'bg-red-50/50 text-red-700 border-red-200/60',
    warn: 'bg-amber-50/50 text-amber-700 border-amber-200/60',
    neutral: 'bg-primary-50/60 text-primary-700 border-primary-200/60',
  };
  return (
    <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border whitespace-nowrap capitalize ${map[tone]}`}>
      {status}
    </span>
  );
}

/** Card explaining that a screen reports rather than configures. */
export function SourceNote({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-start gap-2.5 rounded-xl border border-primary-200/40 bg-primary-50/40 p-3.5">
      <AppIcon className="ri-information-line mt-0.5 shrink-0 text-sm text-primary-600" aria-hidden="true"></AppIcon>
      <p className="text-[11px] text-foreground-500 leading-relaxed">{children}</p>
    </div>
  );
}

/** Pagination footer shared by the long tables. */
export function Pager({ page, pageSize, count, onPage }: {
  page: number; pageSize: number; count: number; onPage: (p: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(count / pageSize));
  if (count === 0) return null;
  const from = (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, count);
  return (
    <div className="px-4 py-3 border-t border-background-100/60 flex items-center justify-between gap-3 flex-wrap">
      <p className="text-[11px] text-foreground-400">Showing {from}–{to} of {count}</p>
      <div className="flex items-center gap-2">
        <button
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
          className="cursor-pointer rounded-lg border border-primary-200/60 bg-primary-50/60 px-3 py-1.5 text-xs font-medium text-primary-700 transition-colors hover:bg-primary-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Previous
        </button>
        <span className="text-[11px] text-foreground-400">Page {page} of {pages}</span>
        <button
          disabled={page >= pages}
          onClick={() => onPage(page + 1)}
          className="cursor-pointer rounded-lg border border-primary-200/60 bg-primary-50/60 px-3 py-1.5 text-xs font-medium text-primary-700 transition-colors hover:bg-primary-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Next
        </button>
      </div>
    </div>
  );
}
