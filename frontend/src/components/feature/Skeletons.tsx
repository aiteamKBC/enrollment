// ============================================================================
// Loading skeletons — the whole site's placeholder vocabulary.
//
// These started life in CurriculumSkeletons.tsx and moved here once the rest of
// the app needed them: a module named for one section that every section imports
// is a name that lies about its scope.
//
// One animation, deliberately: Tailwind's `animate-pulse` over `bg-background-200`.
// The gold shimmer in index.css is decorative and is not a loading pattern — two
// competing "this is loading" animations read as two different kinds of wait.
//
// The rule for using these: a skeleton stands in for content whose SHAPE is
// already known — a table with rows, a grid of cards, a page with a hero. It is
// not action feedback. A button that has been clicked keeps its spinner, because
// what the reader is waiting for there is an outcome, not a layout.
// ============================================================================
import { useState } from 'react';
import { SIDEBAR_RAIL_WIDTH, SIDEBAR_EXPANDED_WIDTH, SIDEBAR_CONTENT_GAP } from './Sidebar';
import { LEARNER_SIDEBAR_WIDTH } from './learnerShellAssets';

interface SkeletonProps {
  className?: string;
}

export function SkeletonBlock({ className = '' }: SkeletonProps) {
  return <span className={`kbc-skeleton block rounded bg-background-200 animate-pulse ${className}`} />;
}

/** A paragraph's worth of lines. The last one is short, as prose usually is. */
export function SkeletonText({ lines = 3, className = '' }: { lines?: number; className?: string }) {
  return (
    <div className={`space-y-2 ${className}`}>
      {Array.from({ length: lines }).map((_, index) => (
        <SkeletonBlock
          key={index}
          className={`h-2.5 ${index === lines - 1 ? 'w-1/2' : 'w-full'}`}
        />
      ))}
    </div>
  );
}

export function HeroStatsSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
      {Array.from({ length: count }).map((_, index) => (
        <div key={index} className="bg-background-50 rounded-xl border border-foreground-200/60 p-4 animate-pulse">
          <div className="flex items-center gap-2 mb-3">
            <SkeletonBlock className="w-7 h-7 rounded-md" />
            <SkeletonBlock className="h-2.5 w-24" />
          </div>
          <SkeletonBlock className="h-6 w-12" />
        </div>
      ))}
    </div>
  );
}

export function TableRowsSkeleton({ rows = 8, columns = 6, gridClass }: { rows?: number; columns?: number; gridClass: string }) {
  return (
    <>
      {Array.from({ length: rows }).map((_, rowIndex) => (
        <div key={rowIndex} className={`${gridClass} gap-3 px-4 py-3.5 items-center animate-pulse`}>
          {Array.from({ length: columns }).map((_, columnIndex) => (
            <SkeletonBlock
              key={columnIndex}
              className={`${columnIndex === 0 ? 'h-3 w-40 max-w-full' : 'h-3 w-16 mx-auto'} ${columnIndex === columns - 1 ? 'h-6 w-12' : ''}`}
            />
          ))}
        </div>
      ))}
    </>
  );
}

/**
 * Rows for a real `<table>`, to be rendered inside its `<tbody>`.
 *
 * Separate from TableRowsSkeleton because that one is for the CSS-grid "tables"
 * the curriculum pages use: a `<div>` cannot go inside `<tbody>`, and a `<tr>`
 * cannot take a grid class. Same look, different host element.
 *
 * `leading` puts an avatar-and-name pair in the first cell, which is what most
 * of these tables actually start with.
 */
export function TableBodySkeleton({
  rows = 8,
  columns = 6,
  leading = true,
}: {
  rows?: number;
  columns?: number;
  leading?: boolean;
}) {
  return (
    <>
      {Array.from({ length: rows }).map((_, rowIndex) => (
        <tr key={rowIndex} className="border-t border-foreground-100">
          {Array.from({ length: columns }).map((_, columnIndex) => (
            <td key={columnIndex} className="py-3 px-3">
              {columnIndex === 0 && leading ? (
                <span className="flex items-center gap-2.5">
                  <SkeletonBlock className="w-8 h-8 rounded-full shrink-0" />
                  <SkeletonBlock className="h-3 w-28" />
                </span>
              ) : (
                <SkeletonBlock className={columnIndex === columns - 1 ? 'h-7 w-20 rounded-lg' : 'h-3 w-16'} />
              )}
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

/**
 * A single card, for pages that already lay their own grid out — mapping this
 * into the existing container keeps the placeholders in the same cells the real
 * cards will land in, which a self-wrapping CardGridSkeleton cannot do.
 */
export function CardSkeleton({ lines = 2 }: { lines?: number }) {
  return (
    <div className="bg-background-50 rounded-xl border border-foreground-200/60 p-4 animate-pulse">
      <div className="flex items-center gap-3 mb-3">
        <SkeletonBlock className="w-10 h-10 rounded-xl shrink-0" />
        <div className="flex-1 min-w-0 space-y-2">
          <SkeletonBlock className="h-3 w-2/5 min-w-[7rem]" />
          <SkeletonBlock className="h-2.5 w-3/5" />
        </div>
        <SkeletonBlock className="h-5 w-14 rounded-full shrink-0" />
      </div>
      <SkeletonText lines={lines} />
    </div>
  );
}

export function CardGridSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
      {Array.from({ length: count }).map((_, index) => (
        <div key={index} className="bg-background-50 rounded-xl border border-foreground-200/60 p-5 animate-pulse">
          <div className="flex items-start justify-between mb-4">
            <div className="flex items-center gap-3">
              <SkeletonBlock className="w-10 h-10 rounded-xl" />
              <div className="space-y-2">
                <SkeletonBlock className="h-3 w-36" />
                <SkeletonBlock className="h-2.5 w-24" />
              </div>
            </div>
            <SkeletonBlock className="h-5 w-16 rounded-full" />
          </div>
          <div className="grid grid-cols-2 gap-3 mb-4">
            {Array.from({ length: 4 }).map((_, metricIndex) => (
              <div key={metricIndex} className="space-y-2">
                <SkeletonBlock className="h-2.5 w-16" />
                <SkeletonBlock className="h-4 w-10" />
              </div>
            ))}
          </div>
          <SkeletonBlock className="h-8 w-full rounded-lg" />
        </div>
      ))}
    </div>
  );
}

export function ListSkeleton({ count = 5 }: { count?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: count }).map((_, index) => (
        <div key={index} className="bg-background-50 rounded-xl border border-foreground-200/60 p-4 animate-pulse">
          <div className="flex items-center gap-4">
            <SkeletonBlock className="w-10 h-10 rounded-lg shrink-0" />
            <div className="flex-1 space-y-2">
              <SkeletonBlock className="h-3.5 w-56 max-w-full" />
              <SkeletonBlock className="h-2.5 w-80 max-w-full" />
            </div>
            <SkeletonBlock className="h-6 w-16 rounded-full" />
          </div>
        </div>
      ))}
    </div>
  );
}

/**
 * Rows to drop INSIDE a panel that already has its own border and heading —
 * the commonest case in this app, where the card is part of the page layout and
 * only its contents are still arriving. Draws no frame of its own, so it never
 * doubles the border it sits in.
 */
export function RowsSkeleton({
  rows = 4,
  avatar = true,
  className = '',
}: {
  rows?: number;
  avatar?: boolean;
  className?: string;
}) {
  return (
    <div className={`space-y-3 ${className}`} aria-busy="true">
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="flex items-center gap-3">
          {avatar && <SkeletonBlock className="w-9 h-9 rounded-lg shrink-0" />}
          <div className="flex-1 min-w-0 space-y-2">
            <SkeletonBlock className="h-3 w-1/3 min-w-[8rem]" />
            <SkeletonBlock className="h-2.5 w-2/3" />
          </div>
          <SkeletonBlock className="h-6 w-16 rounded-full shrink-0" />
        </div>
      ))}
    </div>
  );
}

/**
 * One bordered panel's worth of placeholder — for a card whose contents are
 * still loading but whose frame is already on screen.
 */
export function PanelSkeleton({ lines = 4, className = '' }: { lines?: number; className?: string }) {
  return (
    <div className={`bg-background-50 rounded-2xl border border-foreground-200/60 p-5 ${className}`}>
      <div className="flex items-center gap-3 mb-4">
        <SkeletonBlock className="w-9 h-9 rounded-xl" />
        <div className="space-y-2">
          <SkeletonBlock className="h-3 w-40" />
          <SkeletonBlock className="h-2.5 w-24" />
        </div>
      </div>
      <SkeletonText lines={lines} />
    </div>
  );
}

/** A form's worth of label-and-field pairs. */
export function FormSkeleton({ fields = 6, columns = 2 }: { fields?: number; columns?: number }) {
  return (
    <div className={`grid grid-cols-1 ${columns > 1 ? 'sm:grid-cols-2' : ''} gap-4`}>
      {Array.from({ length: fields }).map((_, index) => (
        <div key={index} className="space-y-2">
          <SkeletonBlock className="h-2.5 w-24" />
          <SkeletonBlock className="h-9 w-full rounded-lg" />
        </div>
      ))}
    </div>
  );
}

/** The page header every workspace page opens with — title, subtitle, action. */
export function HeroSkeleton() {
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="space-y-2.5">
        <SkeletonBlock className="h-5 w-56" />
        <SkeletonBlock className="h-2.5 w-72 max-w-full" />
      </div>
      <SkeletonBlock className="h-9 w-28 rounded-xl shrink-0" />
    </div>
  );
}

/**
 * The whole-page fallback, shown while a route's chunk downloads (router/index.ts).
 * Every route is lazy() and the boundary above it is keyed by pathname, so this
 * is what the site shows between a click and the page arriving.
 *
 * It draws the shell's CHROME and nothing else, deliberately. It used to add a
 * hero, four stat cards and a six-row table — "a typical workspace page" — and
 * that was the page-flash people reported as loading the wrong page: open the
 * learner overview, which is cards and a calendar, and you got a grey table
 * first, then the real layout replaced it. Nothing here can know the shape of
 * the page being fetched, and this module's own rule is that a skeleton stands
 * in for content whose shape is ALREADY KNOWN. So the content area stays empty:
 * an empty frame cannot be mistaken for the wrong page, and the chrome's own
 * pulse is what says "loading".
 *
 * The chrome mirrors WorkspaceShell exactly — rail width, the h-14 header, the
 * h-8 breadcrumb strip — so the swap to the real page moves nothing. The header
 * used to be missing entirely here, which shifted the whole page up 56px and
 * back down again on every navigation.
 *
 * The rail width is imported rather than typed as a class so it cannot drift
 * away from the real sidebar's.
 */
export function PageSkeleton({ workspaceRole, hideSidebar = false }: { workspaceRole?: string; hideSidebar?: boolean } = {}) {
  const learnerShell = workspaceRole === 'learner';
  const [sidebarExpanded] = useState(() => {
    try {
      return localStorage.getItem('kbc_sidebar_pinned') === 'true';
    } catch {
      return false;
    }
  });
  return (
    <div
      className={`flex h-screen overflow-hidden bg-background-200 ${workspaceRole ? 'dashboard-theme' : ''} ${learnerShell ? 'kbc-learner-route-skeleton' : ''}`}
      data-workspace-role={workspaceRole}
      aria-busy="true"
      aria-label="Loading page"
    >
      {/* Sidebar rail — hidden below md, matching the real off-canvas drawer. */}
      {!hideSidebar && (
        <div
          className={`hidden lg:block shrink-0 p-4 space-y-4 ${learnerShell ? 'kbc-learner-route-skeleton-sidebar' : 'my-3 ml-3 mr-3 rounded-[24px] bg-brand-deep'}`}
          style={{ width: learnerShell ? LEARNER_SIDEBAR_WIDTH : sidebarExpanded ? SIDEBAR_EXPANDED_WIDTH : SIDEBAR_RAIL_WIDTH, marginInline: learnerShell ? 0 : SIDEBAR_CONTENT_GAP / 2 }}
        >
          <SkeletonBlock className="h-9 w-9 rounded-xl mx-auto" />
          <div className="space-y-3 pt-2">
            {Array.from({ length: 7 }).map((_, index) => (
              <SkeletonBlock key={index} className="h-8 w-full rounded-lg" />
            ))}
          </div>
        </div>
      )}

      <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
        {/* Header — same height, border and surface as Header.tsx. */}
        <div className={`flex h-16 shrink-0 items-center gap-3 px-2 sm:px-3 md:px-4 lg:px-5 ${learnerShell ? 'kbc-learner-route-skeleton-header' : 'mx-2 mb-2 mt-2 rounded-[20px] border-b border-foreground-100 bg-background-50 lg:ml-0 lg:mr-3 lg:mt-3'}`}>
          <div className="min-w-0 flex-1 space-y-2">
            <SkeletonBlock className="h-3 w-48 max-w-[45%]" />
            <SkeletonBlock className="h-2 w-64 max-w-[60%]" />
          </div>
          <SkeletonBlock className="h-9 w-9 rounded-lg shrink-0" />
          <SkeletonBlock className="h-9 w-9 rounded-full shrink-0" />
        </div>

        {/* Breadcrumb strip — same height, border and surface as the shell's. */}
        <div className="mx-2 flex h-8 shrink-0 items-center rounded-xl border-b border-background-300/40 bg-background-200 px-3 md:px-5 lg:ml-0 lg:mr-3">
          <SkeletonBlock className="h-2 w-40" />
        </div>

        {/* Content: intentionally empty — see the note above. */}
        <div className="flex-1 bg-background-200" />
      </div>
    </div>
  );
}
