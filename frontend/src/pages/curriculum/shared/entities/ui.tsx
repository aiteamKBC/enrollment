import { WEEKEND_DAYS } from './ui.helpers';
// ============================================================================
// Shared chrome for the entity-based Curriculum pages.
//
// Everything here is presentation only — it holds no curriculum rules. It exists
// so Cohorts, Groups, Modules and Holidays look like one product rather than
// four, and so a change to the filter bar or the table shell lands everywhere at
// once. Styling follows the tokens the rest of the Curriculum Studio already
// uses (background-*/foreground-*/primary-*, `transition-smooth`, the 12–13px
// type ramp); no new visual language is introduced.
// ============================================================================

import { Fragment, type ReactNode, useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { RightSlidePanel } from '@/components/feature/RightSlidePanel';
import { showCurriculumConfirm, type CurriculumAlertOptions } from '@/components/feature/CurriculumSweetAlert';
import { SkeletonBlock } from '@/components/feature/Skeletons';
import { SelectMenu, type SelectOption } from '@/components/feature/SelectField';
import { useReducedMotion } from '@/hooks/useReducedMotion';
import { statusTone } from './model';
import { recordCurriculumAction, recordCurriculumSearch } from '@/lib/curriculumActivity';
import { AppIcon } from '@/components/feature/AppIcon';

export interface EntityStat {
  icon: string;
  label: string;
  value: string | number;
  detail?: string;
}

/**
 * A colour per stat in the dense header strip, cycling through so the row
 * reads as a row of badges instead of a run of identical grey text — no page
 * has to hand-pick a colour per figure, and two figures never fight for the
 * same one since they simply take the next tone in line.
 */
const STAT_TONES = [
  { bg: 'bg-indigo-50', icon: 'text-indigo-500', label: 'text-indigo-500', value: 'text-indigo-900', detail: 'text-indigo-400' },
  { bg: 'bg-violet-50', icon: 'text-violet-500', label: 'text-violet-500', value: 'text-violet-900', detail: 'text-violet-400' },
  { bg: 'bg-sky-50', icon: 'text-sky-500', label: 'text-sky-500', value: 'text-sky-900', detail: 'text-sky-400' },
  { bg: 'bg-amber-50', icon: 'text-amber-600', label: 'text-amber-600', value: 'text-amber-900', detail: 'text-amber-500' },
  { bg: 'bg-emerald-50', icon: 'text-emerald-600', label: 'text-emerald-600', value: 'text-emerald-900', detail: 'text-emerald-500' },
  { bg: 'bg-rose-50', icon: 'text-rose-500', label: 'text-rose-500', value: 'text-rose-900', detail: 'text-rose-400' },
];

/** The same idea, tuned to sit on {@link EntityHero}'s dark purple gradient — the
 * light tones above would wash out, so this trades solid pastel fills for a
 * translucent tint plus a matching soft border. */
const HERO_STAT_TONES = [
  { bg: 'bg-indigo-400/10', border: 'border-indigo-300/25', icon: 'text-indigo-200' },
  { bg: 'bg-violet-400/10', border: 'border-violet-300/25', icon: 'text-violet-200' },
  { bg: 'bg-sky-400/10', border: 'border-sky-300/25', icon: 'text-sky-200' },
  { bg: 'bg-amber-400/10', border: 'border-amber-300/25', icon: 'text-amber-200' },
  { bg: 'bg-emerald-400/10', border: 'border-emerald-300/25', icon: 'text-emerald-200' },
  { bg: 'bg-rose-400/10', border: 'border-rose-300/25', icon: 'text-rose-200' },
];

/**
 * The page banner every entity page opens with: what this page manages, the
 * live counts, and the one primary action.
 */
/**
 * The class a secondary action in the page hero wears.
 *
 * `EntityHero` below paints itself on a dark gradient, but nothing ever renders
 * it that way: every caller sits inside `WorkspaceShell`, and
 * `WorkspaceDesign.module.css` repaints the hero `primary-50/0.6` with
 * `!important`, which beats the inline gradient. The headings and body text in
 * that stylesheet are recoloured to match; the buttons were missed, so a
 * secondary action written for the dark hero -- white text on `bg-white/10` --
 * came out as a white-on-lavender ghost that read as disabled.
 *
 * An opaque surface with primary-coloured text is legible either way, and keeps
 * the hierarchy against the filled primary action next to it.
 */
export const HERO_SECONDARY_BUTTON_CLASS = 'inline-flex h-11 shrink-0 items-center justify-center gap-2 rounded-xl border border-primary-200 bg-white px-4 text-[12px] font-bold text-primary-800 shadow-sm shadow-primary-900/5 transition-smooth hover:border-primary-300 hover:bg-primary-50 disabled:cursor-not-allowed disabled:opacity-60';

/** A secondary hero action. Same button on every entity page's hero. */
export function HeroSecondaryButton({ icon, label, onClick, disabled }: {
  icon: string;
  label: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} className={HERO_SECONDARY_BUTTON_CLASS}>
      <AppIcon className={`${icon} text-base`}></AppIcon>
      {label}
    </button>
  );
}

export function EntityHero({
  eyebrow,
  title,
  description,
  stats,
  primaryAction,
  secondaryActions,
  loading,
}: {
  eyebrow: string;
  title: string;
  description: string;
  stats: EntityStat[];
  primaryAction?: { label: string; icon?: string; onClick: () => void; disabled?: boolean };
  secondaryActions?: ReactNode;
  loading?: boolean;
}) {
  return (
    <section className="workspace-page-hero overflow-hidden rounded-2xl border border-white/10 bg-primary-950 text-white shadow-xl" style={{ background: 'var(--kbc-hero-gradient)' }}>
      <div className="relative p-5 sm:p-6">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_20%_0%,rgba(255,255,255,0.18),transparent_34%),linear-gradient(135deg,rgba(109,40,217,0.35),rgba(15,23,42,0))]" />
        <div className="relative flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
          <div className="max-w-3xl">
            <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/60">{eyebrow}</p>
            <h2 className="mt-2 text-2xl font-heading font-bold text-white sm:text-3xl">{title}</h2>
            <p className="mt-2 max-w-2xl text-[13px] leading-6 text-white/75">{description}</p>
          </div>
          {/* Wraps, because this section is `overflow-hidden` and the row used to
              be a single un-wrapping line: a third action, or a long label on
              the second one, pushed the last button past the right edge and it
              was clipped away entirely rather than moving to a new line. That is
              how "View archived" went missing on Cohorts ("Cohort + group +
              module") while surviving on Groups ("Group + module"). */}
          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center sm:justify-end">
            {primaryAction && (
              <button
                type="button"
                onClick={primaryAction.onClick}
                disabled={primaryAction.disabled}
                // Filled rather than white-on-gradient, for the same reason as
                // HERO_SECONDARY_BUTTON_CLASS: the hero the shell actually
                // renders is light, and a white button on it is only as loud as
                // the secondary ones beside it.
                className="inline-flex h-11 shrink-0 items-center justify-center gap-2 rounded-xl bg-primary-600 px-4 text-[12px] font-bold text-white shadow-sm shadow-primary-900/20 transition-smooth hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-60"
              >
                <AppIcon className={`${primaryAction.icon || 'ri-add-line'} text-base`}></AppIcon>
                {primaryAction.label}
              </button>
            )}
            {secondaryActions}
          </div>
        </div>
        <div className="relative mt-4 grid grid-cols-2 gap-2.5 lg:grid-cols-4">
          {stats.map(stat => (
            <div key={stat.label} className="coach-metric-card">
              <div className="flex items-center gap-2 text-foreground-500">
                <AppIcon className={`${stat.icon} text-sm text-primary-600`}></AppIcon>
                <span className="truncate text-[10px] font-medium uppercase tracking-wider">{stat.label}</span>
              </div>
              <p className="mt-1.5 text-[25px] font-semibold leading-none tabular-nums text-foreground-900">
                {loading ? <span className="inline-block h-5 w-10 animate-pulse rounded bg-background-200" /> : stat.value}
              </p>
              {stat.detail && <p className="mt-1 text-[11px] leading-snug text-foreground-500">{stat.detail}</p>}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function slug(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '') || 'filter';
}

/**
 * The order control the entity pages share. `options` is an
 * `EntitySortOption` list from `model` -- only its value/label are read here, so
 * the bar never needs to know what any of them actually sort on.
 */
export interface EntitySortSelect {
  value: string;
  onChange: (value: string) => void;
  options: Array<{ value: string; label: string }>;
  /** The value that means "unsorted", so Reset knows where to put it back. */
  defaultValue?: string;
  label?: string;
}

export interface FilterSelect {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: Array<{ value: string; label: string }>;
  /** Show an explicit "Select all" choice that clears this filter. */
  includeSelectAll?: boolean;
  disabled?: boolean;
  /** Why the control is disabled — shown in place of the hint when set. */
  disabledHint?: string;
}

/**
 * Search + cascading selects. Every entity page filters the same way, and the
 * chosen filters are what a create form prefills from — see `EntityFilterBar`
 * callers, which pass the same state into their drawer.
 */
export function EntityFilterBar({
  search,
  onSearch,
  placeholder,
  selects,
  sort,
  onReset,
  summary,
  loading,
  trailing,
  isDirty,
  disabled,
  searchDisabled,
}: {
  search: string;
  onSearch: (value: string) => void;
  placeholder: string;
  selects: FilterSelect[];
  /**
   * Which order the list is in. Rendered after the filters and next to them
   * because it is chosen the same way, but it narrows nothing -- so it counts
   * towards "dirty" only once it has moved off its default, and Reset returns
   * it there with the rest.
   */
  sort?: EntitySortSelect;
  onReset: () => void;
  summary?: string;
  /**
   * A request for `summary`'s answer is in flight. Callers that keep showing
   * the previous rows while a filter change refetches (so the table does not
   * flash empty) still need to say so somewhere -- otherwise the only sign
   * anything happened is the summary text itself changing, which is easy to
   * miss next to a dropdown the reader just clicked. A spinner next to that
   * text is the whole fix; it never touches the rows underneath.
   */
  loading?: boolean;
  trailing?: ReactNode;
  /** Override when a select is required context rather than a resettable filter. */
  isDirty?: boolean;
  /** Disable the full toolbar when there are no records to filter. */
  disabled?: boolean;
  /** Disable search while leaving contextual selects, such as Cohort, usable. */
  searchDisabled?: boolean;
}) {
  const sortDirty = Boolean(sort) && sort!.value !== (sort!.defaultValue ?? '');
  const dirty = !disabled && (isDirty ?? (Boolean(search) || selects.some(select => select.value) || sortDirty));
  return (
    // This toolbar reports its own searches, filters and sorts below, by name.
    // The mark tells the LMS-wide capture listener to leave it alone, so one
    // filter change is one row in the audit trail rather than two.
    <div
      data-audit="manual"
      aria-busy={loading}
      className="rounded-2xl border border-foreground-200/60 bg-background-50 p-3.5"
    >
      <div className="flex flex-col gap-2.5 xl:flex-row xl:items-end xl:justify-between">
        <div className="grid flex-1 gap-2.5 sm:grid-cols-2 xl:grid-cols-4">
          <label className="block">
            <span className="mb-1 block text-[10px] font-bold uppercase tracking-wider text-foreground-400">Search</span>
            <span className="relative block">
              <AppIcon className="ri-search-line absolute left-3 top-1/2 -translate-y-1/2 text-sm text-foreground-300"></AppIcon>
              <input
                value={search}
                onChange={event => {
                  onSearch(event.target.value);
                  // What was searched for, recorded once the typing settles —
                  // see curriculumActivity. Reported here rather than in each
                  // page so the Audit Trail sees every entity list the same
                  // way, and so no page can be left out by being forgotten.
                  recordCurriculumSearch(event.target.value, placeholder || 'search');
                }}
                placeholder={placeholder}
                disabled={disabled || searchDisabled}
                className="h-10 w-full rounded-lg border border-background-200 bg-background-50 pl-9 pr-3 text-[13px] text-foreground-900 outline-none transition-smooth focus:border-primary-300 disabled:cursor-not-allowed disabled:bg-background-100 disabled:text-foreground-400"
              />
            </span>
          </label>
          {selects.map(select => (
            <div key={select.label} className="block">
              <span id={`filter-${slug(select.label)}`} className="mb-1 block text-[10px] font-bold uppercase tracking-wider text-foreground-400">
                {select.label}
              </span>
              <SelectMenu
                size="sm"
                value={select.value}
                onChange={value => {
                  select.onChange(value);
                  recordCurriculumAction('filter', { filter: select.label, value });
                }}
                options={select.options}
                disabled={disabled || select.disabled}
                disabledHint={disabled ? 'Filters become available when this list has records.' : select.disabledHint}
                ariaLabelledBy={`filter-${slug(select.label)}`}
                clearable={select.includeSelectAll}
                placeholder={select.includeSelectAll ? 'Select all' : (select.options[0]?.label || 'All')}
              />
            </div>
          ))}
          {sort && (
            <div className="block">
              <span id="filter-sort" className="mb-1 block text-[10px] font-bold uppercase tracking-wider text-foreground-400">
                {sort.label || 'Sort by'}
              </span>
              <SelectMenu
                size="sm"
                value={sort.value}
                onChange={value => {
                  sort.onChange(value);
                  recordCurriculumAction('sort', { sort: value });
                }}
                options={sort.options.map(option => ({ value: option.value, label: option.label }))}
                disabled={disabled}
                disabledHint={disabled ? 'Sorting becomes available when this list has records.' : undefined}
                ariaLabelledBy="filter-sort"
                placeholder={sort.options[0]?.label || 'Default order'}
              />
            </div>
          )}
        </div>
        <div className="flex w-full flex-wrap items-center gap-2 xl:w-auto xl:shrink-0 xl:justify-end">
          {trailing}
          <button
            type="button"
            onClick={onReset}
            disabled={disabled || !dirty}
            className="inline-flex h-10 items-center gap-1.5 rounded-lg border border-background-200 bg-background-50 px-3 text-[12px] font-semibold text-foreground-600 transition-smooth hover:bg-background-100 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <AppIcon className="ri-refresh-line text-sm"></AppIcon>
            Reset
          </button>
        </div>
      </div>
      {summary && (
        <div
          role={loading ? 'status' : undefined}
          aria-live="polite"
          aria-label={loading ? summary : undefined}
          className={`mt-3 flex min-h-9 items-center gap-2 rounded-lg text-[11px] font-bold ${loading
            ? 'border border-primary-200 bg-primary-50 px-3 text-primary-700 shadow-sm'
            : 'text-foreground-400'}`}
        >
          {loading && <AppIcon className="ri-loader-4-line animate-spin text-base text-primary-600"></AppIcon>}
          <span>{summary}</span>
          {loading && <span className="ml-auto hidden text-[10px] font-semibold text-primary-500 sm:inline">Please wait…</span>}
        </div>
      )}
    </div>
  );
}

/**
 * A CSS-grid table. Rows are buttons so the whole row opens the record — the
 * action cell stops propagation for the inline edit/delete controls.
 *
 * Two states sit on top of the rows, both there because a save and the refresh
 * that follows it are seconds apart. `refreshing` runs a bar under the header
 * while a background load is in flight: the list stays readable and the user is
 * told it is moving, rather than being shown a stale page with no explanation.
 * `highlightKey` marks the record a save just wrote — the row flashes and is
 * scrolled into view, so a create in a long list is something the user sees
 * happen instead of something they have to go and find.
 */
export function EntityTable<T>({
  columns,
  gridClass,
  rows,
  rowKey,
  renderRow,
  renderRowDetail,
  isRowExpanded,
  getRowHref,
  onRowIntent,
  loading,
  refreshing,
  highlightKey,
  empty,
}: {
  columns: Array<{ label: string; align?: 'left' | 'center' | 'right' }>;
  gridClass: string;
  rows: T[];
  rowKey: (row: T) => string;
  renderRow: (row: T) => ReactNode;
  /**
   * A panel drawn under one row, full width, for detail that belongs to that
   * row rather than to a page of its own -- the rows either side stay where
   * they are, so the reader keeps the comparison the list was giving them.
   *
   * Only ever called for the row `isRowExpanded` names, and only rendered when
   * both are supplied: a table that passes neither is built exactly as it was.
   */
  renderRowDetail?: (row: T) => ReactNode;
  isRowExpanded?: (row: T) => boolean;
  /**
   * Makes the whole row navigate, not just whichever cell happens to render a
   * link. Row actions (RowActions/NamedActions) already stop their clicks from
   * bubbling, so they keep working untouched; a cell with its own `<Link>`
   * (StackedCell's `href`) should do the same, or the click fires both.
   */
  getRowHref?: (row: T) => string | undefined;
  /**
   * Called when a row is about to be opened -- pointer over it, or keyboard
   * focus on it. For fetching what the next page will need before the click,
   * so it is already in hand when the click happens. Must be cheap and
   * idempotent: it fires again every time the pointer crosses the row.
   */
  onRowIntent?: (row: T) => void;
  loading?: boolean;
  /** A background reload is running behind the rows already on screen. */
  refreshing?: boolean;
  /** Row key of the record a save just wrote, or null. */
  highlightKey?: string | null;
  empty: ReactNode;
}) {
  const navigate = useNavigate();
  const [flashKey, setFlashKey] = useState<string | null>(null);
  const rowNodes = useRef(new Map<string, HTMLDivElement>());
  // Both cues below carry information, so a reduced-motion viewer keeps them and
  // loses only the movement: the bar sits still and the row holds a flat tint
  // for the same length of time.
  const reduceMotion = useReducedMotion();

  // `rows` is in the dependencies on purpose: the optimistic row is replaced by
  // the server's copy when the refresh lands, and the highlight has to survive
  // that swap rather than ending on the row that was thrown away.
  useEffect(() => {
    if (!highlightKey) { setFlashKey(null); return undefined; }
    setFlashKey(highlightKey);
    rowNodes.current.get(highlightKey)?.scrollIntoView({
      block: 'center',
      behavior: reduceMotion ? 'auto' : 'smooth',
    });
    const timer = setTimeout(() => setFlashKey(null), 2600);
    return () => clearTimeout(timer);
  }, [highlightKey, reduceMotion, rows]);

  return (
    <div className="overflow-hidden rounded-2xl border border-foreground-200/60 bg-background-50">
      <div className="overflow-x-auto">
        <div className="min-w-[860px]">
          <div className={`${gridClass} gap-3 border-b border-background-200 px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-foreground-400`}>
            {columns.map(column => (
              <span
                key={column.label}
                className={column.align === 'right' ? 'text-right' : column.align === 'center' ? 'text-center' : ''}
              >
                {column.label}
              </span>
            ))}
          </div>
          <div className="relative h-0.5 overflow-hidden" aria-hidden="true">
            {refreshing && !loading && (
              <span
                className={reduceMotion
                  ? 'absolute inset-0 bg-primary-500/40'
                  : 'absolute inset-y-0 left-0 w-1/4 animate-entity-refresh rounded-full bg-primary-500/70'}
              />
            )}
          </div>
          <span className="sr-only" role="status" aria-live="polite">
            {refreshing && !loading ? 'Refreshing the list' : ''}
          </span>
          {loading ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 6 }).map((_, index) => <SkeletonBlock key={index} className="h-14 w-full" />)}
            </div>
          ) : rows.length ? (
            <div className="divide-y divide-background-200/70">
              {rows.map(row => {
                const key = rowKey(row);
                const href = getRowHref?.(row);
                const expanded = Boolean(renderRowDetail && isRowExpanded?.(row));
                const line = (
                  <div
                    ref={node => {
                      if (node) rowNodes.current.set(key, node);
                      else rowNodes.current.delete(key);
                    }}
                    role={href ? 'button' : undefined}
                    tabIndex={href ? 0 : undefined}
                    onPointerEnter={onRowIntent ? () => onRowIntent(row) : undefined}
                    onFocus={onRowIntent ? () => onRowIntent(row) : undefined}
                    onClick={href ? () => navigate(href) : undefined}
                    onKeyDown={href ? event => {
                      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); navigate(href); }
                    } : undefined}
                    className={`${gridClass} gap-3 px-4 py-2.5 transition-smooth hover:bg-background-100/60${href ? ' cursor-pointer' : ''}${
                      flashKey === key ? (reduceMotion ? ' bg-primary-100/70' : ' animate-row-flash') : ''
                    }`}
                  >
                    {renderRow(row)}
                  </div>
                );
                // Unexpandable tables keep the DOM they always had: the row is
                // the child, and the divider between rows is the divider it
                // was. Only a table that actually draws detail gets a wrapper.
                if (!renderRowDetail) return <Fragment key={key}>{line}</Fragment>;
                return (
                  <div key={key} className={expanded ? 'bg-background-100/40' : undefined}>
                    {line}
                    {expanded && (
                      <div className="border-t border-background-200/70 px-4 py-3">
                        {renderRowDetail(row)}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            empty
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Numbered pages under a table the server sends one page at a time.
 *
 * The window of page numbers is centred on the current page, so page 9 of 14 is
 * one click away rather than six. `noun` names what is being counted, because
 * "1–50 of 141" without it makes the reader look back up at the table to find
 * out what they are looking at.
 *
 * Rendered as nothing at all when there is only one page: a single disabled
 * page button is furniture that says the list is paginated and then refuses to
 * paginate.
 */
export function EntityPagination({
  page,
  pages,
  total,
  pageSize,
  noun = 'rows',
  onPage,
}: {
  page: number;
  pages: number;
  total: number;
  pageSize: number;
  noun?: string;
  onPage: (page: number) => void;
}) {
  if (pages <= 1) return null;

  const first = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const last = Math.min(page * pageSize, total);
  const width = 5;
  const half = Math.floor(width / 2);
  const start = pages <= width ? 1 : Math.min(Math.max(1, page - half), pages - width + 1);
  const numbers = Array.from({ length: Math.min(width, pages) }, (_, index) => start + index);

  return (
    <nav
      aria-label={`${noun} pages`}
      className="flex flex-col items-center justify-between gap-3 rounded-2xl border border-foreground-200/60 bg-background-50 px-4 py-2.5 sm:flex-row"
    >
      <p className="text-[12px] text-foreground-500">
        Showing <strong className="font-semibold tabular-nums text-foreground-800">{first}–{last}</strong> of{' '}
        <strong className="font-semibold tabular-nums text-foreground-800">{total}</strong> {noun}
      </p>
      <div className="flex items-center gap-1">
        <button
          type="button"
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
          aria-label="Previous page"
          className="flex h-8 w-8 items-center justify-center rounded-md text-foreground-500 transition hover:bg-background-100 disabled:opacity-30 disabled:hover:bg-transparent"
        >
          <AppIcon className="ri-arrow-left-s-line"></AppIcon>
        </button>
        {numbers.map(number => (
          <button
            key={number}
            type="button"
            onClick={() => onPage(number)}
            aria-current={number === page ? 'page' : undefined}
            aria-label={`Page ${number}`}
            className={`h-8 min-w-[32px] rounded-md px-2 text-[12px] font-semibold tabular-nums transition ${
              number === page ? 'bg-primary-600 text-white' : 'text-foreground-600 hover:bg-background-100'
            }`}
          >
            {number}
          </button>
        ))}
        <button
          type="button"
          disabled={page >= pages}
          onClick={() => onPage(page + 1)}
          aria-label="Next page"
          className="flex h-8 w-8 items-center justify-center rounded-md text-foreground-500 transition hover:bg-background-100 disabled:opacity-30 disabled:hover:bg-transparent"
        >
          <AppIcon className="ri-arrow-right-s-line"></AppIcon>
        </button>
      </div>
    </nav>
  );
}

export function EntityEmptyState({
  icon,
  title,
  message,
  action,
}: {
  icon: string;
  title: string;
  message: string;
  action?: { label: string; onClick: () => void };
}) {
  return (
    <div className="p-12 text-center">
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-background-100 text-foreground-400">
        <AppIcon className={`${icon} text-xl`}></AppIcon>
      </div>
      <p className="mt-3 text-sm font-bold text-foreground-900">{title}</p>
      <p className="mt-1 text-[12px] text-foreground-400">{message}</p>
      {action && (
        <button
          type="button"
          onClick={action.onClick}
          className="mt-4 inline-flex h-10 items-center gap-2 rounded-xl bg-primary-600 px-4 text-[12px] font-bold text-white transition-smooth hover:bg-primary-700"
        >
          <AppIcon className="ri-add-line text-sm"></AppIcon>
          {action.label}
        </button>
      )}
    </div>
  );
}

export function StatusBadge({ status }: { status: unknown }) {
  const label = String(status ?? '').trim() || 'Unknown';
  return (
    <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide ${statusTone(status)}`}>
      {label}
    </span>
  );
}

export function RowActions({ actions }: {
  actions: Array<{ icon: string; label: string; onClick: () => void; tone?: 'default' | 'danger'; disabled?: boolean }>;
}) {
  return (
    <span className="flex items-center justify-end gap-1 self-center">
      {actions.map(action => (
        <button
          key={action.label}
          type="button"
          title={action.label}
          aria-label={action.label}
          disabled={action.disabled}
          onClick={event => { event.stopPropagation(); action.onClick(); }}
          className={`flex h-8 w-8 items-center justify-center rounded-lg border transition-smooth disabled:cursor-not-allowed disabled:opacity-50 ${
            action.tone === 'danger'
              ? 'border-red-100 bg-red-50 text-red-600 hover:bg-red-100'
              : 'border-background-200 bg-background-50 text-foreground-500 hover:bg-background-100'
          }`}
        >
          <AppIcon className={`${action.icon} text-sm`}></AppIcon>
        </button>
      ))}
    </span>
  );
}

/**
 * Row actions that say what they do. The icon-only `RowActions` above is fine for
 * the obvious edit/archive pair; anything else — "show the groups below", "open
 * this in the Module Builder" — is not guessable from a glyph, and a reader
 * should not have to hover every button to tell two of them apart. Icon + short
 * label here, the full sentence in `title`.
 *
 * Widen the actions column rather than dropping the words.
 *
 * NOTE: the Teams Meetings page still carries its own local copy of this, from
 * before it was shared. That one should be replaced by this import.
 */
export function NamedActions({ actions }: {
  actions: Array<{
    icon: string;
    label: string;
    title: string;
    onClick: () => void;
    disabled?: boolean;
    primary?: boolean;
    busy?: boolean;
  }>;
}) {
  return (
    <span className="flex flex-nowrap items-center justify-end gap-1.5 self-center">
      {actions.map(action => (
        <button
          key={action.label}
          type="button"
          title={action.title}
          disabled={action.disabled}
          onClick={event => { event.stopPropagation(); action.onClick(); }}
          className={`inline-flex h-8 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg border px-2.5 text-[11px] font-bold transition-smooth disabled:cursor-not-allowed disabled:opacity-50 ${
            action.primary
              ? 'primary-action border-primary-600 bg-primary-600 text-white hover:bg-primary-700'
              : 'border-background-200 bg-background-50 text-foreground-600 hover:bg-background-100'
          }`}
        >
          <AppIcon className={`${action.busy ? 'ri-loader-4-line animate-spin' : action.icon} text-sm`}></AppIcon>
          {action.label}
        </button>
      ))}
    </span>
  );
}

/** Two-line cell: a strong primary value with a muted secondary line under it. */
export function StackedCell({ primary, secondary, href }: { primary: ReactNode; secondary?: ReactNode; href?: string }) {
  const body = (
    <>
      <span className="block truncate text-[13px] font-semibold text-foreground-900">{primary}</span>
      {secondary != null && <span className="block truncate text-[11px] text-foreground-400">{secondary}</span>}
    </>
  );
  if (!href) return <span className="min-w-0 self-center">{body}</span>;
  return (
    <Link
      to={href}
      // A row this cell sits in may itself be clickable to the same place
      // (EntityTable's `getRowHref`) — without this the click bubbles and
      // fires both navigations.
      onClick={event => event.stopPropagation()}
      className="min-w-0 self-center transition-smooth hover:text-primary-700"
    >
      {body}
    </Link>
  );
}

export function PlainCell({ children, align = 'left' }: { children: ReactNode; align?: 'left' | 'center' | 'right' }) {
  return (
    <span className={`min-w-0 self-center truncate text-[12px] text-foreground-700 ${align === 'right' ? 'text-right' : align === 'center' ? 'text-center' : ''}`}>
      {children}
    </span>
  );
}

// ----------------------------------------------------------------- form kit

export function FormField({
  label,
  children,
  hint,
  error,
  required,
  as = 'label',
}: {
  label: string;
  children: ReactNode;
  hint?: string;
  error?: string;
  required?: boolean;
  /**
   * `'label'` for a field that is one control. `'group'` for a field made of
   * several controls — a tick list, a row of toggles. A `<label>` names the
   * labelable elements inside it, and `<button>` is labelable: wrapping a tick
   * list in one gives every row the whole list's text as its accessible name,
   * so screen readers (and tests) cannot tell the rows apart.
   */
  as?: 'label' | 'group';
}) {
  const heading = (
    <span className="mb-1 flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-foreground-400">
      {label}
      {required && <span className="text-red-500">*</span>}
    </span>
  );
  const footer = error ? (
    <span className="mt-1 block text-[11px] font-semibold text-red-600">{error}</span>
  ) : hint ? (
    <span className="mt-1 block text-[11px] text-foreground-400">{hint}</span>
  ) : null;

  if (as === 'group') {
    return (
      <div className="block" role="group" aria-label={label}>
        {heading}
        {children}
        {footer}
      </div>
    );
  }

  return (
    <label className="block">
      {heading}
      {children}
      {footer}
    </label>
  );
}

const CONTROL_CLASS = 'h-10 w-full rounded-lg border border-background-200 bg-background-50 px-3 text-[13px] text-foreground-900 outline-none transition-smooth focus:border-primary-300 disabled:cursor-not-allowed disabled:bg-background-100 disabled:text-foreground-400';

export function TextControl({
  value,
  onChange,
  type = 'text',
  placeholder,
  disabled,
  min,
  max,
  step,
  inputMode,
}: {
  value: string;
  onChange: (value: string) => void;
  type?: string;
  placeholder?: string;
  disabled?: boolean;
  min?: string | number;
  max?: string | number;
  step?: string | number;
  inputMode?: 'text' | 'numeric' | 'decimal';
}) {
  return (
    <input
      type={type}
      value={value}
      min={min}
      max={max}
      step={step}
      inputMode={inputMode}
      disabled={disabled}
      placeholder={placeholder}
      onChange={event => onChange(event.target.value)}
      // A number input still owns the scroll wheel and arrow keys while
      // focused: a page scroll or an accidental arrow press silently nudges
      // the value. Blurring on wheel, and letting arrow keys move focus
      // instead of the value, keeps typed input as the only way to change it.
      onWheel={type === 'number' ? event => event.currentTarget.blur() : undefined}
      onKeyDown={type === 'number' ? event => {
        if (event.key === 'ArrowUp' || event.key === 'ArrowDown') event.preventDefault();
      } : undefined}
      className={CONTROL_CLASS}
    />
  );
}

export function TextAreaControl({
  value,
  onChange,
  rows = 3,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  rows?: number;
  placeholder?: string;
}) {
  return (
    <textarea
      value={value}
      rows={rows}
      placeholder={placeholder}
      onChange={event => onChange(event.target.value)}
      className="w-full rounded-lg border border-background-200 bg-background-50 px-3 py-2 text-[13px] text-foreground-900 outline-none transition-smooth focus:border-primary-300"
    />
  );
}

export function SelectControl({
  value,
  onChange,
  options,
  disabled,
  placeholder,
  ariaLabel,
}: {
  value: string;
  onChange: (value: string) => void;
  /**
   * `SelectOption`, not `{value,label}`: a picker whose rows carry a reason —
   * the tutor list marking who is already teaching in the slot — needs the
   * group heading, the meta chip and the second line the menu already draws.
   */
  options: SelectOption[];
  disabled?: boolean;
  placeholder?: string;
  /**
   * Only needed inside a `FormField as="group"`: a `<label>` names the control
   * it wraps, but a `role="group"` does not name the controls inside it, so a
   * select sharing its field with another control has no accessible name
   * unless it is given one here.
   */
  ariaLabel?: string;
}) {
  return (
    <SelectMenu
      value={value}
      onChange={onChange}
      options={options}
      disabled={disabled}
      placeholder={placeholder || 'Select'}
      clearable={Boolean(placeholder)}
      ariaLabel={ariaLabel}
    />
  );
}

export interface MultiSelectOption {
  value: string;
  label: string;
  /** Second line, for whatever tells two similarly-named rows apart. */
  description?: string;
  /** Short chip on the right, e.g. "already runs this". */
  badge?: string;
  /** Ticked and not togglable — something already true of this row. */
  locked?: boolean;
}

/**
 * A picker for "which of these, one or many" — the same list a `SelectControl`
 * would draw, with a checkbox on every row instead of one radio-shaped choice.
 *
 * Written as a visible list rather than a dropdown with multi-select semantics:
 * the point of the control is that picking a second row is obviously allowed,
 * and a closed dropdown showing one value does not say that.
 */
export function MultiSelectControl({
  value,
  onChange,
  options,
  emptyMessage = 'Nothing to choose from.',
  selectAllLabel,
  searchable = false,
  searchPlaceholder = 'Search options...',
}: {
  value: string[];
  onChange: (value: string[]) => void;
  options: MultiSelectOption[];
  /** Shown in place of the list when there is nothing to pick. */
  emptyMessage?: string;
  /** Adds a select-all / clear toggle above the list, named by this. */
  selectAllLabel?: string;
  /** Adds an inline filter for long option lists without changing the selection. */
  searchable?: boolean;
  searchPlaceholder?: string;
}) {
  const [search, setSearch] = useState('');
  const selected = new Set(value.map(String));
  const togglable = options.filter(option => !option.locked);
  const allSelected = togglable.length > 0 && togglable.every(option => selected.has(option.value));
  const query = search.trim().toLocaleLowerCase();
  const visibleOptions = query
    ? options.filter(option => [option.label, option.description, option.badge].filter(Boolean)
      .some(text => String(text).toLocaleLowerCase().includes(query)))
    : options;

  const toggle = (option: MultiSelectOption) => {
    if (option.locked) return;
    // Order is preserved: the first pick stays first, which is what the module
    // form reads as the delivery it patches.
    onChange(selected.has(option.value)
      ? value.filter(item => String(item) !== option.value)
      : [...value, option.value]);
  };

  if (!options.length) {
    return (
      <p className="rounded-lg border border-background-200 bg-background-100 px-3 py-2.5 text-[12px] text-foreground-500">
        {emptyMessage}
      </p>
    );
  }

  return (
    <div className="space-y-2">
      {selectAllLabel && togglable.length > 1 && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-[11px] font-semibold text-foreground-500">
            {selected.size} of {options.length} selected
          </p>
          <button
            type="button"
            onClick={() => onChange(allSelected
              ? value.filter(item => !togglable.some(option => option.value === String(item)))
              : Array.from(new Set([...value.map(String), ...togglable.map(option => option.value)])))}
            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-primary-200 bg-primary-50 px-2.5 text-[11px] font-bold text-primary-700 transition-smooth hover:bg-primary-100"
          >
            <AppIcon className={allSelected ? 'ri-checkbox-blank-line text-sm' : 'ri-checkbox-multiple-line text-sm'}></AppIcon>
            {allSelected ? `Clear ${selectAllLabel}` : `Select all ${selectAllLabel}`}
          </button>
        </div>
      )}
      {searchable && (
        <label className="relative block">
          <span className="sr-only">Search options</span>
          <AppIcon className="ri-search-line pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-foreground-400"></AppIcon>
          <input
            type="search"
            value={search}
            onChange={event => setSearch(event.target.value)}
            placeholder={searchPlaceholder}
            aria-label="Search options"
            className="h-9 w-full rounded-lg border border-background-200 bg-background-50 pl-8 pr-3 text-[12px] text-foreground-800 outline-none transition-smooth placeholder:text-foreground-400 focus:border-primary-300 focus:ring-2 focus:ring-primary-100"
          />
        </label>
      )}
      <div className="max-h-64 space-y-1.5 overflow-y-auto rounded-lg border border-background-200 bg-background-50 p-2">
        {visibleOptions.length ? visibleOptions.map(option => {
          const active = selected.has(option.value);
          return (
            <button
              key={option.value}
              type="button"
              onClick={() => toggle(option)}
              aria-pressed={active}
              disabled={option.locked}
              className={`flex w-full items-center gap-2 rounded-lg border px-2.5 py-2 text-left transition-smooth ${
                active ? 'border-primary-300 bg-primary-50' : 'border-transparent hover:bg-background-100'
              } ${option.locked ? 'cursor-not-allowed opacity-70' : ''}`}
            >
              <span className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                active ? 'border-primary-600 bg-primary-600 text-white' : 'border-background-300'
              }`}
              >
                {active && <AppIcon className="ri-check-line text-[10px]"></AppIcon>}
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex min-w-0 items-center gap-1.5">
                  <span className="truncate text-[12px] font-semibold text-foreground-900">{option.label}</span>
                  {option.badge && (
                    <span className="shrink-0 rounded-full bg-background-200 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-foreground-600">
                      {option.badge}
                    </span>
                  )}
                </span>
                {option.description && (
                  <span className="block truncate text-[11px] text-foreground-400">{option.description}</span>
                )}
              </span>
            </button>
          );
        }) : (
          <p className="px-2 py-5 text-center text-[11px] text-foreground-500">No options match “{search.trim()}”.</p>
        )}
      </div>
    </div>
  );
}

const COLOR_PRESETS = ['#6d28d9', '#2563eb', '#0f766e', '#16a34a', '#ea580c', '#dc2626', '#be123c', '#334155'];

export function ColorControl({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <input
        type="color"
        value={/^#[0-9a-f]{6}$/i.test(value) ? value : '#6941c6'}
        onChange={event => onChange(event.target.value)}
        className="h-10 w-14 cursor-pointer rounded-lg border border-background-200 bg-background-50 p-1"
        aria-label="Pick a colour"
      />
      {COLOR_PRESETS.map(preset => (
        <button
          key={preset}
          type="button"
          onClick={() => onChange(preset)}
          aria-label={`Use ${preset}`}
          style={{ backgroundColor: preset }}
          className={`h-7 w-7 rounded-lg border-2 transition-smooth ${value.toLowerCase() === preset ? 'border-foreground-900' : 'border-transparent'}`}
        />
      ))}
    </div>
  );
}

/** Anything larger than this is refused: the image is stored on the record itself. */
const COVER_IMAGE_MAX_BYTES = 3 * 1024 * 1024;

/**
 * An optional picture for a record, picked from the device. The file is read
 * into a data: URL and saved on the record itself, so there is no upload to wait
 * for and nothing to clean up when the record is deleted.
 *
 * One way in on purpose: a pasted address is someone else's file, and the card
 * quietly loses its picture the day that address stops answering. Records saved
 * before this still hold a URL and go on rendering it.
 */
export function CoverImageControl({ value, onChange, onError, alt = '' }: {
  value: string;
  onChange: (value: string) => void;
  /** Told why a picked file was refused, so the caller can say so its own way. */
  onError?: (message: string) => void;
  alt?: string;
}) {
  const [broken, setBroken] = useState(false);
  useEffect(() => { setBroken(false); }, [value]);
  const showImage = Boolean(value) && !broken;

  const readFile = (file?: File) => {
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      onError?.('That file is not an image. Choose a PNG, JPG, SVG or WebP.');
      return;
    }
    if (file.size > COVER_IMAGE_MAX_BYTES) {
      onError?.('That image is over 3 MB. Choose a smaller one so the record saves quickly.');
      return;
    }
    const reader = new FileReader();
    reader.onload = () => { onChange(String(reader.result || '')); };
    reader.onerror = () => { onError?.('That image could not be read. Try another file.'); };
    reader.readAsDataURL(file);
  };

  return (
    <div className="grid gap-3 rounded-lg border border-background-200 bg-background-50 p-3 sm:grid-cols-[160px_minmax(0,1fr)]">
      <div className="overflow-hidden rounded-lg border border-background-200 bg-background-50">
        {showImage ? (
          <img src={value} alt={alt} className="h-24 w-full object-cover" onError={() => setBroken(true)} />
        ) : (
          <div className="flex h-24 items-center justify-center bg-background-100 text-foreground-300">
            <AppIcon className="ri-image-add-line text-2xl"></AppIcon>
          </div>
        )}
      </div>
      <div className="flex min-w-0 flex-col justify-center gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <label className="inline-flex h-9 cursor-pointer items-center gap-1.5 rounded-lg border border-primary-200 bg-primary-50 px-3 text-[12px] font-bold text-primary-700 transition-smooth hover:bg-primary-100">
            <AppIcon className="ri-upload-cloud-2-line text-sm"></AppIcon>
            {value ? 'Replace image' : 'Upload image'}
            <input
              type="file"
              accept="image/*"
              className="sr-only"
              onChange={event => { readFile(event.target.files?.[0]); event.target.value = ''; }}
            />
          </label>
          {value && (
            <button
              type="button"
              onClick={() => onChange('')}
              className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-red-200 bg-background-50 px-3 text-[12px] font-bold text-red-600 transition-smooth hover:bg-red-50"
            >
              <AppIcon className="ri-delete-bin-line text-sm"></AppIcon>
              Remove
            </button>
          )}
        </div>
        <p className="text-[11px] font-medium text-foreground-400">PNG, JPG, SVG or WebP, up to 3 MB.</p>
        {broken && Boolean(value) && (
          <p className="text-[11px] font-semibold text-amber-600">This image did not load. The record keeps its icon until another is uploaded.</p>
        )}
      </div>
    </div>
  );
}

const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
export const WEEKEND_HINT = 'Saturday and Sunday are weekend holidays in England — delivery on these days is unusual.';

/** Delivery days as a comma-separated string, which is how the API stores them. */
export function WeekdayControl({ value, onChange, maxSelections }: { value: string; onChange: (value: string) => void; maxSelections?: number }) {
  const selected = value.split(',').map(day => day.trim()).filter(Boolean);
  const toggle = (day: string) => {
    const next = selected.includes(day) ? selected.filter(item => item !== day) : [...selected, day];
    onChange(WEEKDAYS.filter(item => next.includes(item)).join(', '));
  };
  const weekendPicked = WEEKEND_DAYS.some(day => selected.includes(day));
  return (
    <div>
      <div className="flex flex-wrap gap-1.5">
        {WEEKDAYS.map(day => {
          const active = selected.includes(day);
          const weekend = WEEKEND_DAYS.includes(day);
          return (
            <button
              key={day}
              type="button"
              onClick={() => toggle(day)}
              aria-pressed={active}
              disabled={!active && maxSelections !== undefined && selected.length >= maxSelections}
              title={weekend ? WEEKEND_HINT : undefined}
              className={`h-9 rounded-lg border px-3 text-[11px] font-bold transition-smooth disabled:cursor-not-allowed disabled:opacity-40 ${
                active
                  ? 'border-primary-600 bg-primary-600 text-white'
                  : weekend
                    ? 'border-dashed border-background-300 bg-background-50 text-foreground-400 hover:bg-background-100'
                    : 'border-background-200 bg-background-50 text-foreground-600 hover:bg-background-100'
              }`}
            >
              {day.slice(0, 3)}
            </button>
          );
        })}
      </div>
      {weekendPicked && (
        <p className="mt-2 flex items-start gap-1.5 text-[11px] font-semibold text-amber-600">
          <i className="ri-information-line mt-px" aria-hidden />
          <span>{WEEKEND_HINT}</span>
        </p>
      )}
    </div>
  );
}

/**
 * A third footer action, beside Cancel. Used by the structure wizard for "skip
 * this step"; `confirmWhenDirty` is the sentence the confirm shows when the form
 * holds answers the action would throw away, and without it the action fires
 * straight away.
 */
export interface DrawerExtraAction {
  label: string;
  icon?: string;
  onClick: () => void;
  confirmWhenDirty?: string;
}

/**
 * Asks before the drawer closes even when nothing has been typed. A form on its
 * own page closes silently when it is clean; a step of a chain does not, because
 * the cross there abandons a run, not just a form.
 */
export interface DrawerCloseConfirm {
  title: string;
  text: string;
  /** The button that goes through with the close. Cancel always keeps editing. */
  confirmLabel: string;
  /**
   * A third answer, for when closing is not the only alternative to staying —
   * a chain that has written records can also take them back out. Runs before
   * the drawer closes; throwing from `onDeny` leaves both the dialog and the
   * drawer open, with the thrown message shown in the dialog.
   */
  denyLabel?: string;
  onDeny?: () => void | Promise<void>;
  /** 'danger' when the third answer is the destructive one. See `denyButtonTone`. */
  denyTone?: 'safe' | 'danger';
  /** What to say once the third answer has run and this dialog has closed. */
  denySuccess?: () => CurriculumAlertOptions | null | undefined;
}

/**
 * What a form is handed when something is driving it as one step of a chain —
 * the structure wizard is the only such caller. It is deliberately additive:
 * unset, every form behaves exactly as it does on its own page.
 */
export interface FormChainStep {
  /** Rendered under the header, above the fields: which step this is. */
  banner?: ReactNode;
  /** Beside Cancel, e.g. "Use an existing cohort". */
  extraAction?: DrawerExtraAction;
  /**
   * Returns to the previous step's form, reopened against the record it already
   * saved. Absent on the first step of a chain, since there is nowhere to go back to.
   */
  backAction?: DrawerExtraAction;
  /**
   * The chain closes the drawer and confirms at the end of the run, so the form
   * does neither: it saves, hands the record to `onSaved` and stops there.
   */
  chained?: boolean;
  /** Overrides the submit button, e.g. "Create cohort & continue". */
  submitLabel?: string;
  /**
   * Overrides the Cancel button. A chain that has already written records is not
   * cancelling anything by closing, so it says "Stop here" instead.
   */
  cancelLabel?: string;
  /** Overrides the drawer width, so a chain does not resize between steps. */
  width?: string;
  /** Asks before the cross, Escape or the backdrop ends the run, dirty or not. */
  closeConfirm?: DrawerCloseConfirm;
}

/**
 * The receipt for a field two people changed at once.
 *
 * By the time this renders the merge has already happened: the colleague's
 * other changes are in the fields, and the ones this reader had also changed
 * still hold what they typed. So it never asks a question -- it reports, names
 * each field, and gets out of the way when dismissed. Every drawer that can be
 * open on a shared record shows the same one, in the same place, so the sentence
 * reads the same wherever people meet each other.
 */
export function CoEditNotice({ notices, onDismiss }: { notices: { field: string; text: string }[]; onDismiss: () => void }) {
  if (!notices.length) return null;
  return (
    <div
      data-testid="co-edit-notice"
      className="flex items-start justify-between gap-3 rounded-xl border border-amber-200/70 bg-amber-50 px-3 py-2 text-[12px] font-medium text-amber-800"
    >
      <span className="flex min-w-0 items-start gap-2">
        <AppIcon className="ri-refresh-line mt-0.5 shrink-0 text-base"></AppIcon>
        <span className="min-w-0">
          <span className="block font-bold">Someone else saved this while you were editing. Their changes are on this form; yours are still here.</span>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {notices.map(notice => <li key={notice.field}>{notice.text}</li>)}
          </ul>
        </span>
      </span>
      <button
        type="button"
        onClick={onDismiss}
        className="shrink-0 rounded-lg px-2 py-1 font-bold text-amber-700 hover:bg-amber-100"
      >
        Got it
      </button>
    </div>
  );
}

/**
 * The focused create/edit surface. Simple records are edited here rather than on
 * a page of their own; anything operational (a module's schedule, its Teams
 * series, its components) gets a full workspace instead.
 */
export function EntityDrawer({
  open,
  title,
  subtitle,
  banner,
  onClose,
  onSubmit,
  submitLabel,
  submitDisabled = false,
  cancelLabel = 'Cancel',
  extraAction,
  backAction,
  saving,
  error,
  dirty = false,
  closeConfirm,
  children,
  width = 'w-[520px]',
}: {
  open: boolean;
  title: string;
  subtitle?: string;
  /** Held above the scroll area, so a step rail stays put while the form scrolls. */
  banner?: ReactNode;
  onClose: () => void;
  onSubmit: () => void | Promise<void>;
  submitLabel: string;
  submitDisabled?: boolean;
  cancelLabel?: string;
  extraAction?: DrawerExtraAction;
  /** A step back in a chain, e.g. "Back to Cohort" — placed before `extraAction`. */
  backAction?: DrawerExtraAction;
  saving?: boolean;
  error?: string | null;
  /**
   * The form has been answered but not saved. Closing a dirty drawer asks
   * before it throws the answers away; a clean one closes straight away.
   */
  dirty?: boolean;
  /**
   * Asks on the way out even when the form is clean. The dirty question above
   * still wins when there are answers to lose, since it is the more urgent one.
   */
  closeConfirm?: DrawerCloseConfirm;
  children: ReactNode;
  width?: string;
}) {
  const firstFieldRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const input = firstFieldRef.current?.querySelector<HTMLElement>('input, select, textarea');
    input?.focus();
  }, [open]);

  // True while the discard dialog is up. Escape reaches this component and
  // SweetAlert's own handler in the same event, so without the flag dismissing
  // the dialog with the keyboard immediately raises a second one.
  const confirmingDiscard = useRef(false);

  // Every way out of the drawer — the header cross, the backdrop, Escape and
  // Cancel — comes through here, so none of them can lose typed answers.
  const requestClose = () => {
    if (saving) return;
    if (!dirty && !closeConfirm) { onClose(); return; }
    if (confirmingDiscard.current) return;
    confirmingDiscard.current = true;
    // Unsaved answers are the more urgent question, so they lead the wording even
    // when the caller supplied its own; a clean form gets the caller's wording on
    // its own. The caller's third answer rides along either way — a chain that
    // wrote records can still take them back out with a form full of answers —
    // and where there is one the caller's own confirm label is kept, so "throw
    // this form away" and "throw the run away" never read as the same button.
    const ask: DrawerCloseConfirm = dirty
      ? {
        title: 'Discard unsaved changes?',
        text: closeConfirm
          ? `This form has answers that have not been saved, and closing it now throws them away. ${closeConfirm.text}`
          : 'This form has answers that have not been saved. Closing it now throws them away.',
        confirmLabel: closeConfirm?.confirmLabel || 'Discard changes',
        denyLabel: closeConfirm?.denyLabel,
        onDeny: closeConfirm?.onDeny,
        denyTone: closeConfirm?.denyTone,
        denySuccess: closeConfirm?.denySuccess,
      }
      : closeConfirm!;
    void showCurriculumConfirm({
      title: ask.title,
      text: ask.text,
      icon: 'warning',
      confirmButtonText: ask.confirmLabel,
      cancelButtonText: 'Keep editing',
      denyButtonText: ask.denyLabel,
      denyButtonTone: ask.denyTone,
      denySuccess: ask.denySuccess,
      // The third answer does its own work first and only then closes, so a
      // failure it throws keeps the drawer open with the answers still in it.
      onDeny: ask.denyLabel ? async () => { await ask.onDeny?.(); onClose(); } : undefined,
      onConfirm: () => { onClose(); },
    }).finally(() => { confirmingDiscard.current = false; });
  };

  // Skipping a step is a way out of the form too, so it asks about unsaved
  // answers on the same terms the cross and Cancel do.
  const runExtraAction = () => {
    if (saving || !extraAction) return;
    if (!dirty || !extraAction.confirmWhenDirty) { extraAction.onClick(); return; }
    if (confirmingDiscard.current) return;
    confirmingDiscard.current = true;
    void showCurriculumConfirm({
      title: extraAction.label,
      text: extraAction.confirmWhenDirty,
      icon: 'warning',
      confirmButtonText: extraAction.label,
      cancelButtonText: 'Keep editing',
      onConfirm: () => { extraAction.onClick(); },
    }).finally(() => { confirmingDiscard.current = false; });
  };

  // Same pattern as `runExtraAction`, for stepping back instead of forward.
  const runBackAction = () => {
    if (saving || !backAction) return;
    if (!dirty || !backAction.confirmWhenDirty) { backAction.onClick(); return; }
    if (confirmingDiscard.current) return;
    confirmingDiscard.current = true;
    void showCurriculumConfirm({
      title: backAction.label,
      text: backAction.confirmWhenDirty,
      icon: 'warning',
      confirmButtonText: backAction.label,
      cancelButtonText: 'Keep editing',
      onConfirm: () => { backAction.onClick(); },
    }).finally(() => { confirmingDiscard.current = false; });
  };

  // The slide panel stays mounted so it can animate, but its form does not: a
  // closed drawer that keeps its inputs in the DOM leaves tab-reachable fields
  // behind the page, and a page with two drawers ends up with two sets of
  // identically-labelled controls.
  if (!open) return <RightSlidePanel isOpen={false} onClose={onClose} title={title} width={width}><div /></RightSlidePanel>;

  return (
    <RightSlidePanel isOpen={open} onClose={requestClose} title={title} width={width}>
      <form
        className="flex h-full min-h-0 flex-col"
        onSubmit={event => { event.preventDefault(); void onSubmit(); }}
      >
        {banner && <div className="shrink-0 border-b border-background-200 bg-background-100 px-5 py-3.5">{banner}</div>}
        <div ref={firstFieldRef} className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-5">
          {subtitle && <p className="text-[12px] leading-5 text-foreground-500">{subtitle}</p>}
          {error && (
            <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2.5 text-[12px] font-semibold text-red-700">
              <AppIcon className="ri-error-warning-line mt-0.5 text-sm"></AppIcon>
              <span>{error}</span>
            </div>
          )}
          {children}
        </div>
        <div className="flex shrink-0 items-center justify-between gap-2 border-t border-background-200 px-5 py-4">
          <div className="flex items-center">
            {backAction && (
              <button
                type="button"
                onClick={runBackAction}
                disabled={saving}
                className="inline-flex h-10 items-center gap-1.5 rounded-xl px-3 text-[12px] font-bold text-foreground-500 transition-smooth hover:bg-background-100 hover:text-foreground-700 disabled:opacity-50"
              >
                <AppIcon className="ri-arrow-left-line text-sm"></AppIcon>
                {backAction.label}
              </button>
            )}
            {extraAction && (
              <button
                type="button"
                onClick={runExtraAction}
                disabled={saving}
                className="inline-flex h-10 items-center gap-1.5 rounded-xl px-3 text-[12px] font-bold text-foreground-500 transition-smooth hover:bg-background-100 hover:text-foreground-700 disabled:opacity-50"
              >
                {extraAction.icon && <AppIcon className={`${extraAction.icon} text-sm`}></AppIcon>}
                {extraAction.label}
              </button>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={requestClose}
              disabled={saving}
              className="inline-flex h-10 items-center rounded-xl border border-background-200 bg-background-50 px-4 text-[12px] font-bold text-foreground-600 transition-smooth hover:bg-background-100 disabled:opacity-50"
            >
              {cancelLabel}
            </button>
            <button
              type="submit"
              disabled={saving || submitDisabled}
              className="inline-flex h-10 items-center gap-2 rounded-xl bg-primary-600 px-4 text-[12px] font-bold text-white transition-smooth hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {saving && <AppIcon className="ri-loader-4-line animate-spin text-sm"></AppIcon>}
              {submitLabel}
            </button>
          </div>
        </div>
      </form>
    </RightSlidePanel>
  );
}

// -------------------------------------------------------------- workspaces

export interface WorkspaceTab {
  key: string;
  label: string;
  icon: string;
  count?: number;
  /** The count's data is still loading: a spinner stands in for the number. */
  countLoading?: boolean;
}

/**
 * What kind of record a {@link ParentBadge} names.
 *
 * A colour per *record type*, not a cycling one like {@link STAT_TONES}. The
 * stat rail cycles because its figures differ from page to page and only need to
 * be told apart from each other; a parent chain is the same four kinds of thing
 * everywhere, so the colour can carry which kind it is. A programme is violet on
 * the group page, the module page and the cohort page alike — cycling would have
 * given one programme a different colour on each, which is worse than grey.
 */
export type ParentBadgeTone = 'programme' | 'cohort' | 'group' | 'module' | 'neutral';

/** Hover tones are held apart so an unlinked badge is not styled as clickable. */
const PARENT_BADGE_TONES: Record<ParentBadgeTone, { base: string; hover: string }> = {
  // Violet at the top of the chain, matching the purple the studio already uses
  // for the programme itself, then cooling down through the levels below it.
  programme: { base: 'border-violet-200 bg-violet-50 text-violet-700', hover: 'hover:bg-violet-100 hover:text-violet-900' },
  cohort: { base: 'border-sky-200 bg-sky-50 text-sky-700', hover: 'hover:bg-sky-100 hover:text-sky-900' },
  group: { base: 'border-emerald-200 bg-emerald-50 text-emerald-700', hover: 'hover:bg-emerald-100 hover:text-emerald-900' },
  module: { base: 'border-amber-200 bg-amber-50 text-amber-800', hover: 'hover:bg-amber-100 hover:text-amber-900' },
  neutral: { base: 'border-background-200 bg-background-100 text-foreground-600', hover: 'hover:bg-background-200 hover:text-foreground-900' },
};

/**
 * A parent record named as a pill in a workspace subtitle — the group a module
 * runs for, the cohort above it, the programme above that.
 *
 * A badge rather than a bold word because the subtitle is a chain of records,
 * each one somewhere you can go. Set as prose with emphasised words, nothing
 * separated the names from the joining text: "in Cohort C1 in programme
 * Osama-Final-Prog" read as one sentence, and which parts were parents and which
 * were labels had to be worked out. The Cohort workspace already framed its
 * programme this way; this is that pill, shared, so the three workspaces cannot
 * drift apart.
 *
 * `href` is optional. A parent that could not be resolved is still named — just
 * not linked — so the chain never quietly drops a link and reads as complete.
 */
export function ParentBadge({ label, href, tone = 'neutral' }: {
  label: ReactNode;
  href?: string;
  /** The kind of record being named, which is what picks the colour. */
  tone?: ParentBadgeTone;
}) {
  const palette = PARENT_BADGE_TONES[tone] || PARENT_BADGE_TONES.neutral;
  const shape = `inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold ${palette.base}`;
  if (!href) return <span className={shape}>{label}</span>;
  return <Link to={href} className={`${shape} transition-smooth ${palette.hover}`}>{label}</Link>;
}

/** Breadcrumb + title + KPI rail + tab strip, shared by the three workspaces. */
export function WorkspaceHeader({
  breadcrumbs,
  eyebrow,
  title,
  subtitle,
  accentColor,
  stats,
  actions,
  dense = false,
}: {
  breadcrumbs: Array<{ label: string; href?: string }>;
  eyebrow: string;
  title: string;
  subtitle: ReactNode;
  accentColor?: string;
  stats: EntityStat[];
  actions?: ReactNode;
  /**
   * The short header: one line of identity and the same figures as a single
   * inline strip instead of a grid of cards. For pages whose real content is
   * the tab below, where a full-height banner is only pushing it off screen.
   */
  dense?: boolean;
}) {
  if (dense) {
    return (
      <section className="relative overflow-hidden rounded-2xl border border-foreground-200/70 bg-background-50 shadow-sm">
        <div className="absolute inset-y-0 left-0 w-1.5" style={{ backgroundColor: accentColor || 'oklch(var(--primary-500))' }} aria-hidden="true" />
        <div className="flex flex-col gap-2.5 px-4 py-3 pl-5 sm:px-5 sm:pl-6">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
            <nav className="flex flex-wrap items-center gap-1.5 text-[11px] text-foreground-400">
              {breadcrumbs.map((crumb, index) => (
                <span key={`${crumb.label}-${index}`} className="flex items-center gap-1.5">
                  {index > 0 && <AppIcon className="ri-arrow-right-s-line text-[10px]"></AppIcon>}
                  {crumb.href ? (
                    <Link to={crumb.href} className="font-semibold transition-smooth hover:text-primary-700">{crumb.label}</Link>
                  ) : (
                    <span className="font-semibold text-foreground-600">{crumb.label}</span>
                  )}
                </span>
              ))}
            </nav>
            {actions && <div className="ml-auto flex flex-wrap items-center gap-2">{actions}</div>}
          </div>
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h1 className="text-lg font-heading font-black leading-tight tracking-tight text-foreground-950">{title}</h1>
            <p className="text-[11px] font-semibold uppercase tracking-wider text-primary-600">{eyebrow}</p>
            {subtitle && <p className="text-[12px] text-foreground-500">{subtitle}</p>}
          </div>
          {/* The same figures, read as a row of badges rather than six cards
              or a run of grey text. */}
          <div className="flex flex-wrap items-center gap-2 border-t border-foreground-200/60 pt-2.5">
            {stats.map((stat, index) => {
              const tone = STAT_TONES[index % STAT_TONES.length];
              return (
                <span key={stat.label} className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[12px] ${tone.bg}`}>
                  <AppIcon className={`${stat.icon} text-sm ${tone.icon}`}></AppIcon>
                  <span className={`text-[10px] font-bold uppercase tracking-wider ${tone.label}`}>{stat.label}</span>
                  <span className={`font-extrabold ${tone.value}`}>{stat.value}</span>
                  {stat.detail && <span className={`text-[11px] ${tone.detail}`}>{stat.detail}</span>}
                </span>
              );
            })}
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="relative overflow-hidden rounded-2xl border border-foreground-200/70 bg-background-50 shadow-sm">
      <div className="absolute inset-y-0 left-0 w-1.5" style={{ backgroundColor: accentColor || 'oklch(var(--primary-500))' }} aria-hidden="true" />
      <div className="p-5 pl-6 sm:p-6 sm:pl-7">
        <nav className="mb-2 flex flex-wrap items-center gap-1.5 text-[11px] text-foreground-400">
          {breadcrumbs.map((crumb, index) => (
            <span key={`${crumb.label}-${index}`} className="flex items-center gap-1.5">
              {index > 0 && <AppIcon className="ri-arrow-right-s-line text-[10px]"></AppIcon>}
              {crumb.href ? (
                <Link to={crumb.href} className="font-semibold transition-smooth hover:text-primary-700">{crumb.label}</Link>
              ) : (
                <span className="font-semibold text-foreground-600">{crumb.label}</span>
              )}
            </span>
          ))}
        </nav>
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div className="min-w-0">
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-primary-600">{eyebrow}</p>
            <h1 className="mt-1 text-2xl font-heading font-black leading-tight tracking-tight text-foreground-950">{title}</h1>
            <p className="mt-1 text-[13px] leading-6 text-foreground-500">{subtitle}</p>
          </div>
          {actions && <div className="flex flex-wrap gap-2 xl:justify-end">{actions}</div>}
        </div>
        <div className="mt-5 grid grid-cols-2 gap-3 border-t border-foreground-200/60 pt-4 sm:grid-cols-3 xl:grid-cols-6">
          {stats.map(stat => (
            <div key={stat.label} className="coach-metric-card">
              <div className="flex items-center gap-1.5 text-foreground-500">
                <AppIcon className={`${stat.icon} text-sm text-primary-600`}></AppIcon>
                <span className="truncate text-[10px] font-medium uppercase tracking-wider">{stat.label}</span>
              </div>
              <p className="mt-1 text-[25px] font-semibold leading-none tabular-nums text-foreground-900">{stat.value}</p>
              {stat.detail && <p className="mt-1 text-[11px] leading-snug text-foreground-500">{stat.detail}</p>}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

export function WorkspaceTabs({
  tabs,
  active,
  onChange,
  trailing,
}: {
  tabs: WorkspaceTab[];
  active: string;
  onChange: (key: string) => void;
  trailing?: ReactNode;
}) {
  return (
    <div className="sticky top-0 z-20 flex flex-col gap-1.5 rounded-2xl border border-foreground-200/70 bg-background-50/95 p-1.5 shadow-sm backdrop-blur sm:flex-row sm:items-center sm:gap-2">
      <div className="flex w-full min-w-0 flex-1 items-center gap-1.5 overflow-x-auto">
        {tabs.map(tab => (
          <button
            key={tab.key}
            type="button"
            onClick={() => onChange(tab.key)}
            aria-pressed={active === tab.key}
            className={`group inline-flex min-h-10 items-center gap-2 whitespace-nowrap rounded-xl px-3 py-1.5 text-[12px] font-bold transition-smooth ${
              active === tab.key ? 'bg-primary-600 text-white shadow-sm' : 'text-foreground-600 hover:bg-background-100 hover:text-foreground-900'
            }`}
          >
            <span className={`flex h-7 w-7 items-center justify-center rounded-lg ${active === tab.key ? 'bg-white/[0.16] text-white' : 'bg-background-100 text-foreground-500 group-hover:bg-background-50'}`}>
              <AppIcon className={`${tab.icon} text-[14px]`}></AppIcon>
            </span>
            <span>{tab.label}</span>
            {tab.countLoading ? (
              <span role="status" aria-label={`Loading ${tab.label.toLowerCase()} count`} className={`inline-flex rounded-full px-1.5 py-0.5 text-[10px] ${active === tab.key ? 'bg-white/[0.15] text-white' : 'bg-foreground-100 text-foreground-500'}`}>
                <AppIcon className="ri-loader-4-line animate-spin text-[10px]"></AppIcon>
              </span>
            ) : tab.count != null && (
              <span className={`rounded-full px-1.5 py-0.5 text-[10px] ${active === tab.key ? 'bg-white/[0.15] text-white' : 'bg-foreground-100 text-foreground-500'}`}>
                {tab.count}
              </span>
            )}
          </button>
        ))}
      </div>
      {trailing && (
        <div className="flex w-full items-center gap-2 border-t border-background-200 pt-1.5 sm:w-auto sm:shrink-0 sm:border-l sm:border-t-0 sm:pl-2 sm:pt-0">
          {trailing}
        </div>
      )}
    </div>
  );
}

export function WorkspacePanel({ title, description, actions, children }: {
  title: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-foreground-200/60 bg-background-50">
      <div className="flex flex-col gap-2 border-b border-background-200 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <h3 className="text-[13px] font-heading font-bold text-foreground-950">{title}</h3>
          {description && <p className="mt-0.5 text-[12px] text-foreground-500">{description}</p>}
        </div>
        {actions && <div className="flex shrink-0 flex-wrap gap-2">{actions}</div>}
      </div>
      <div className="p-5">{children}</div>
    </section>
  );
}

export function DetailRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-background-200/70 py-2.5 last:border-b-0">
      <span className="text-[11px] font-bold uppercase tracking-wider text-foreground-400">{label}</span>
      <span className="min-w-0 text-right text-[13px] font-semibold text-foreground-900">{value}</span>
    </div>
  );
}

export function InlineError({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-[12px] font-semibold text-red-700">
      <AppIcon className="ri-error-warning-line text-sm"></AppIcon>
      <span className="min-w-0 flex-1">{message}</span>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-red-200 bg-white px-3 text-[11px] font-bold text-red-700 transition-smooth hover:bg-red-100"
        >
          <AppIcon className="ri-refresh-line"></AppIcon>
          Retry
        </button>
      )}
    </div>
  );
}
