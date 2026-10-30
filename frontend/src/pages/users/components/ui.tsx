import { useState, type ReactNode } from 'react';

// ---- Button / input class strings (match app conventions) ----
export const btnPrimary =
  'primary-action px-4 py-2.5 bg-primary-500 text-white rounded-xl text-[13px] font-semibold hover:bg-primary-600 transition-smooth cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-1.5';
export const btnGold =
  'px-4 py-2.5 bg-[#b27715] text-white rounded-xl text-[13px] font-semibold shadow-sm shadow-[#b27715]/25 hover:bg-[#965f0f] transition-smooth cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-1.5';
export const btnSecondary =
  'px-4 py-2.5 bg-background-100 border border-background-200 rounded-xl text-[13px] font-medium text-foreground-600 hover:bg-background-200 transition-smooth cursor-pointer inline-flex items-center gap-1.5';
export const btnDestructive =
  'px-4 py-2.5 bg-red-500 text-white rounded-xl text-[13px] font-semibold hover:bg-red-600 transition-smooth cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-1.5';
export const btnSuccess =
  'px-4 py-2.5 bg-emerald-500 text-white rounded-xl text-[13px] font-semibold hover:bg-emerald-600 transition-smooth cursor-pointer inline-flex items-center gap-1.5';
export const inputClass =
  'w-full px-3 py-2 text-[13px] bg-background-50 border border-foreground-200 rounded-lg text-foreground-900 placeholder:text-foreground-300 focus:border-primary-400 focus:ring-1 focus:ring-primary-200/40 outline-none transition-smooth';
export const iconBtn =
  'w-8 h-8 rounded-lg hover:bg-background-100 flex items-center justify-center text-foreground-400 hover:text-foreground-700 transition-smooth cursor-pointer';

// ---- Action link (right-aligned section actions) ----
export function ActionLink({ label, onClick, icon, danger = false }: { label: string; onClick?: () => void; icon?: string; danger?: boolean }) {
  return (
    <button
      onClick={onClick}
      className={`text-[12px] ${danger ? 'text-red-600 hover:text-red-700' : 'text-primary-600 hover:text-primary-700'} hover:underline transition-smooth cursor-pointer inline-flex items-center gap-1 whitespace-nowrap`}
    >
      {icon && <AppIcon className={icon} />}
      {label}
    </button>
  );
}

// ---- Workspace hero banner ----
export function Hero({ icon, title, subtitle, right }: { icon: string; title: string; subtitle?: ReactNode; right?: ReactNode }) {
  return (
    <div
      className="workspace-page-hero relative overflow-hidden rounded-[1.25rem] border border-foreground-200/60 bg-background-50 shadow-lg shadow-foreground-950/10"
    >
      <div className="absolute inset-x-0 top-0 h-px bg-primary-200/70" />
      <div className="absolute inset-x-0 bottom-0 h-px bg-foreground-200/60" />
      <div className="relative p-6 sm:p-8 flex flex-col sm:flex-row items-start sm:items-center gap-5">
        <span className="w-14 h-14 rounded-2xl bg-primary-50 border border-primary-100/70 flex items-center justify-center shrink-0">
          <AppIcon className={`${icon} text-primary-600 text-2xl`} />
        </span>
        <div className="flex-1 min-w-0">
          <h2 className="text-lg font-heading font-bold text-foreground-950 mb-1">{title}</h2>
          {subtitle && <div className="text-[13px] text-foreground-500 leading-relaxed">{subtitle}</div>}
        </div>
        {right && <div className="flex items-center gap-3 shrink-0 flex-wrap">{right}</div>}
      </div>
    </div>
  );
}

// ---- Glass stat pill (inside hero) ----
export function HeroStat({ value, label }: { value: ReactNode; label: string }) {
  return (
    <div className="coach-metric-card">
      <p className="truncate text-[11px] font-medium text-foreground-500">{label}</p>
      <p className="mt-1 text-[25px] font-semibold leading-none tabular-nums text-foreground-900">{value}</p>
    </div>
  );
}

// ---- White stat card (stats strip) ----
const STAT_TINTS: Record<string, { bg: string; text: string }> = {
  primary: { bg: 'bg-primary-50 border-primary-200/40', text: 'text-primary-600' },
  accent: { bg: 'bg-accent-50 border-accent-200/40', text: 'text-accent-600' },
  emerald: { bg: 'bg-emerald-50 border-emerald-200/40', text: 'text-emerald-600' },
  secondary: { bg: 'bg-secondary-50 border-secondary-200/40', text: 'text-secondary-600' },
  amber: { bg: 'bg-amber-50 border-amber-200/40', text: 'text-amber-600' },
};
export function StatCard({ icon, label, value, tint = 'primary' }: { icon: string; label: string; value: ReactNode; tint?: keyof typeof STAT_TINTS }) {
  const t = STAT_TINTS[tint] ?? STAT_TINTS.primary;
  return (
    <div className="coach-metric-card">
      <div className="flex min-w-0 items-start gap-3">
        <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border ${t.bg}`}>
          <AppIcon className={`${icon} text-[16px] ${t.text}`} />
        </div>
        <div className="min-w-0">
          <p className="truncate text-[11px] font-medium text-foreground-500">{label}</p>
          <p className="mt-1 text-[25px] font-semibold leading-none tabular-nums text-foreground-900">{value}</p>
        </div>
      </div>
    </div>
  );
}

// ---- Collapsible section panel (backbone of the Details Board) ----
export function SectionPanel({
  title,
  icon,
  actions,
  defaultOpen = false,
  children,
}: {
  title: string;
  icon?: string;
  actions?: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="bg-background-50 rounded-2xl border border-foreground-200/60 card-premium">
      <div className="flex items-center justify-between gap-3 px-4 py-3">
        <button
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="flex items-center gap-2.5 cursor-pointer group min-w-0"
        >
          <AppIcon className={`ri-arrow-down-s-line text-foreground-400 transition-transform ${open ? '' : '-rotate-90'}`} />
          {icon && (
            <span className="w-7 h-7 rounded-lg bg-primary-50 border border-primary-200/40 flex items-center justify-center shrink-0">
              <AppIcon className={`${icon} text-primary-600 text-[13px]`} />
            </span>
          )}
          <span className="text-[13px] font-heading font-semibold text-foreground-900 truncate group-hover:text-primary-700">{title}</span>
        </button>
        {actions && <div className="flex items-center gap-3 flex-wrap justify-end">{actions}</div>}
      </div>
      {open && <div className="border-t border-foreground-100 p-4">{children}</div>}
    </div>
  );
}

// ---- Field row: label left, value/control right (readonly | editable) ----
export function FieldRow({
  label,
  required,
  readonly,
  value,
  children,
}: {
  label: string;
  required?: boolean;
  readonly?: boolean;
  value?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-[minmax(0,240px)_1fr] gap-1 sm:gap-4 py-2.5 border-b border-foreground-100 last:border-0">
      <div className="text-[12px] text-foreground-500 font-medium sm:pt-1.5">
        {label}
        {required && <span className="text-red-500 ml-0.5">*</span>}
      </div>
      <div className="text-[13px] text-foreground-900 min-w-0">
        {readonly ? value ?? <span className="text-foreground-300">—</span> : children}
      </div>
    </div>
  );
}

// ---- Yes / No radio row ----
export function YesNoRadio({
  legend,
  value,
  onChange,
  name,
  error,
}: {
  legend: string;
  value: boolean | null;
  onChange: (v: boolean) => void;
  name: string;
  /** Shown under the row, with the choices outlined in red, when the question still needs an answer. */
  error?: string;
}) {
  return (
    <fieldset className="grid grid-cols-1 sm:grid-cols-[1fr_auto] gap-2 sm:gap-4 py-2.5 border-b border-foreground-100 last:border-0 sm:items-center">
      <legend className="sr-only">{legend}</legend>
      <span className="text-[12px] text-foreground-700">{legend}</span>
      <div className={`flex items-center gap-5 shrink-0${error ? ' rounded-lg border border-red-500 px-2.5 py-1' : ''}`}>
        {[
          { v: true, l: 'Yes' },
          { v: false, l: 'No' },
        ].map((o) => (
          <label key={o.l} className="flex items-center gap-1.5 text-[12px] text-foreground-700 cursor-pointer">
            <input
              type="radio"
              name={name}
              checked={value === o.v}
              onChange={() => onChange(o.v)}
              aria-invalid={error ? true : undefined}
              className="accent-primary-500"
            />
            {o.l}
          </label>
        ))}
      </div>
      {error && (
        <p className="sm:col-span-2 justify-self-end inline-block rounded-md bg-red-50 px-2 py-0.5 text-[12px] text-red-600">{error}</p>
      )}
    </fieldset>
  );
}

// ---- Status badge (RAG / programme status) ----
// Tints for the programme statuses in PROGRAMME_STATUS_OPTIONS. Statuses saved
// before that list was trimmed still render, falling back to the neutral tint.
const STATUS_TINTS: Record<string, string> = {
  'Fresh user': 'bg-background-100 text-foreground-600 border-foreground-200/60',
  Onboarding: 'bg-amber-50 text-amber-700 border-amber-200/50',
  Delivery: 'bg-secondary-100 text-secondary-700 border-secondary-200/50',
  'Ready to enrol': 'bg-primary-50 text-primary-700 border-primary-200/50',
  Active: 'bg-emerald-50 text-emerald-700 border-emerald-200/50',
  Withdrawn: 'bg-red-50 text-red-700 border-red-200/50',
  'On break': 'bg-amber-50 text-amber-700 border-amber-200/50',
  Completed: 'bg-blue-50 text-blue-700 border-blue-200/50',
  Finished: 'bg-emerald-50 text-emerald-700 border-emerald-200/50',
};

export function StatusBadge({ status }: { status: string }) {
  const tint = STATUS_TINTS[status] ?? 'bg-background-100 text-foreground-600 border-foreground-200/60';
  return <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${tint} whitespace-nowrap`}>{status}</span>;
}

// ---- Empty state ----
export function EmptyState({ text }: { text: string }) {
  return <p className="text-[12px] text-foreground-400 italic py-2">{text}</p>;
}

// ---- Pagination: first / prev / page / of N / next / last ----
export function Pagination({
  page,
  totalPages,
  onChange,
}: {
  page: number;
  totalPages: number;
  onChange: (p: number) => void;
}) {
  const go = (p: number) => onChange(Math.min(Math.max(1, p), Math.max(1, totalPages)));
  const btn = 'w-8 h-8 rounded-lg border border-background-200 flex items-center justify-center text-foreground-500 hover:bg-background-100 transition-smooth cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed';
  return (
    <div className="flex items-center justify-center gap-2 py-2">
      <button className={btn} onClick={() => go(1)} disabled={page <= 1} aria-label="First page"><AppIcon className="ri-arrow-left-double-line text-sm" /></button>
      <button className={btn} onClick={() => go(page - 1)} disabled={page <= 1} aria-label="Previous page"><AppIcon className="ri-arrow-left-s-line text-sm" /></button>
      <div className="flex items-center gap-1.5 text-[12px] text-foreground-600">
        <input
          type="number"
          value={page}
          min={1}
          max={Math.max(1, totalPages)}
          onChange={(e) => go(Number(e.target.value))}
          aria-label="Page number"
          className="w-12 px-2 py-1 text-center text-[12px] bg-background-50 border border-foreground-200 rounded-lg outline-none focus:border-primary-400"
        />
        <span>of {Math.max(1, totalPages)}</span>
      </div>
      <button className={btn} onClick={() => go(page + 1)} disabled={page >= totalPages} aria-label="Next page"><AppIcon className="ri-arrow-right-s-line text-sm" /></button>
      <button className={btn} onClick={() => go(totalPages)} disabled={page >= totalPages} aria-label="Last page"><AppIcon className="ri-arrow-right-double-line text-sm" /></button>
    </div>
  );
}

// ---- File list: links + optional delete + add affordance ----
export interface FileItem {
  id: string;
  name: string;
  url?: string;
}
export function FileList({
  files,
  onDelete,
  onAdd,
  emptyText = 'No evidence',
  addLabel = 'Add file',
}: {
  files: FileItem[];
  onDelete?: (id: string) => void;
  onAdd?: () => void;
  emptyText?: string;
  addLabel?: string;
}) {
  return (
    <div className="space-y-2">
      {files.length === 0 ? (
        <EmptyState text={emptyText} />
      ) : (
        <div className="divide-y divide-foreground-100 border border-foreground-100 rounded-lg">
          {files.map((f) => (
            <div key={f.id} className="flex items-center justify-between gap-3 px-3 py-2">
              <a href={f.url ?? '#'} className="text-[12px] text-primary-600 hover:underline inline-flex items-center gap-1.5 min-w-0">
                <AppIcon className="ri-file-text-line shrink-0" />
                <span className="truncate">{f.name}</span>
              </a>
              {onDelete && (
                <button onClick={() => onDelete(f.id)} aria-label={`Delete ${f.name}`} className="text-red-500 hover:text-red-600 transition-smooth cursor-pointer shrink-0">
                  <AppIcon className="ri-delete-bin-line text-sm" />
                </button>
              )}
            </div>
          ))}
        </div>
      )}
      {onAdd && (
        <button onClick={onAdd} className="text-[12px] text-primary-600 hover:text-primary-700 hover:underline transition-smooth cursor-pointer inline-flex items-center gap-1">
          <AppIcon className="ri-add-line" />
          {addLabel}
        </button>
      )}
    </div>
  );
}

// ---- Simple data table shell ----
export function Table({ headers, children }: { headers: string[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-b border-foreground-200/70 bg-background-100/50">
            {headers.map((h, i) => (
              <th key={i} className="text-left py-2.5 px-3 text-[11px] font-semibold text-foreground-500 uppercase tracking-wider whitespace-nowrap">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}
