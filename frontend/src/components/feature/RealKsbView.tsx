import { useMemo, useState } from 'react';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { AnimatedProgressCircle } from '@/components/ui/AnimatedProgressCircle';
import { roleNavMap } from '@/mocks/navigation';
import { EmptyState } from '@/pages/users/components/ui';
import type { LearnerDetail } from '@/api/learnerDetail';
import { componentTypeMeta, type KsbProgress, type KsbStatus } from '@/utils/learnerJourney';
import { RowsSkeleton } from '@/components/feature/Skeletons';
import { useKsbProgress } from '@/hooks/useKsbProgress';
import type { ProgressMetric } from '@/api/learnerMetrics';

const learnerNav = roleNavMap.learner;

/* ═══════════════════════════════════════════════════════
   REAL KSB Progress — weighted.

   Each component in the training plan declares the KSBs it
   develops and at what weight. A KSB's progress is therefore
   the weight EARNED from components already completed over
   the weight AVAILABLE across the whole plan, and we can name
   exactly which activities still have to be done.

   Weight always comes from the component's authored mapping,
   never from the codes stored on a completion record — legacy
   records hold learner-picked codes with no weight behind them.
   ═══════════════════════════════════════════════════════ */

const TYPE_META: Record<string, { label: string; icon: string; tint: string; ring: string }> = {
  K: { label: 'Knowledge', icon: 'ri-book-open-line', tint: 'bg-blue-100 text-blue-600', ring: 'text-blue-500' },
  S: { label: 'Skill', icon: 'ri-tools-line', tint: 'bg-violet-100 text-violet-600', ring: 'text-violet-500' },
  B: { label: 'Behaviour', icon: 'ri-user-heart-line', tint: 'bg-amber-100 text-amber-600', ring: 'text-amber-500' },
};
const TYPE_ORDER = ['K', 'S', 'B'];

const STATUS_META: Record<KsbStatus, { label: string; badge: string; dot: string; bar: string }> = {
  complete: { label: 'Fully evidenced', badge: 'bg-emerald-100 text-emerald-700', dot: 'bg-emerald-500', bar: 'bg-emerald-500' },
  'in-progress': { label: 'In progress', badge: 'bg-amber-100 text-amber-700', dot: 'bg-amber-500', bar: 'bg-amber-500' },
  'not-started': { label: 'Not started', badge: 'bg-background-200 text-foreground-500', dot: 'bg-foreground-300', bar: 'bg-foreground-300' },
};

/** Weights are authored as whole numbers but stored as floats. */
function w(n: number): string {
  return n % 1 === 0 ? String(n) : n.toFixed(1);
}

function Ring({ percent, colorClass, size = 64, stroke = 6 }: { percent: number; colorClass: string; size?: number; stroke?: number }) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  return (
    <svg width={size} height={size} className="-rotate-90 shrink-0">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={stroke} className="text-background-200" stroke="currentColor" />
      <AnimatedProgressCircle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={stroke} strokeLinecap="round"
        className={colorClass} stroke="currentColor" strokeDasharray={c} strokeDashoffset={c - (Math.min(100, percent) / 100) * c}
        style={{ transition: 'stroke-dashoffset 700ms ease-out' }} />
    </svg>
  );
}

type Filter = 'all' | KsbStatus;

export function RealKsbView({ real, loading }: { real: LearnerDetail | null; loading: boolean }) {
  return (
    <WorkspaceShell
      role="learner"
      roleLabel={learnerNav.label}
      navItems={learnerNav.items}
      workspaceLabel={learnerNav.workspaceLabel}
      pageTitle="KSB Progress"
      pageSubtitle={real?.programme || 'Knowledge, Skills & Behaviours'}
      userName={real?.name || 'Learner'}
      userRole="Learner"
    >
      <KsbProgressBody real={real} loading={loading} />
    </WorkspaceShell>
  );
}

/**
 * The weighted KSB breakdown, without page chrome — shared with the employer
 * portal so an employer sees exactly the progress their apprentice sees, down to
 * which activities still have to be done for each KSB.
 *
 * `audience` only swaps the second-person copy; the figures are the same.
 */
export function KsbProgressBody({
  real,
  loading,
  showHero = true,
  audience = 'learner',
  metric,
}: {
  real: LearnerDetail | null;
  loading: boolean;
  showHero?: boolean;
  audience?: 'learner' | 'observer';
  metric?: ProgressMetric | null;
}) {
  const isObserver = audience === 'observer';
  const [filter, setFilter] = useState<Filter>('all');
  const [expanded, setExpanded] = useState<string | null>(null);

  const progress = useKsbProgress(real);

  const total = progress.length;
  const complete = progress.filter((k) => k.status === 'complete').length;
  const inProgress = progress.filter((k) => k.status === 'in-progress').length;
  const notStarted = progress.filter((k) => k.status === 'not-started').length;

  // Percentages represent KSB completion, matching the x/y counts shown to the
  // learner. Authored weight remains visible as separate supporting data.
  const earnedWeight = progress.reduce((s, k) => s + k.earnedWeight, 0);
  const availableWeight = progress.reduce((s, k) => s + k.availableWeight, 0);
  const overallPct = total > 0 ? Math.round((complete / total) * 100) : 0;

  const visible = useMemo(() => progress.filter((k) => (
    filter === 'all' ? true
      : k.status === filter
  )), [progress, filter]);

  const groups = useMemo(() => {
    const allBy: Record<string, KsbProgress[]> = {};
    const visibleBy: Record<string, KsbProgress[]> = {};
    for (const k of progress) (allBy[k.type] ||= []).push(k);
    for (const k of visible) (visibleBy[k.type] ||= []).push(k);
    return TYPE_ORDER.filter((t) => visibleBy[t]?.length).map((t) => {
      const allItems = allBy[t] || [];
      const items = visibleBy[t] || [];
      const av = allItems.reduce((s, k) => s + k.availableWeight, 0);
      const ea = allItems.reduce((s, k) => s + k.earnedWeight, 0);
      const completed = allItems.filter((k) => k.status === 'complete').length;
      return {
        type: t, ...TYPE_META[t], items,
        complete: completed,
        total: allItems.length,
        pct: allItems.length > 0 ? Math.round((completed / allItems.length) * 100) : 0,
        earned: ea, available: av,
      };
    });
  }, [progress, visible]);

  if (metric === null) return loading ? <RowsSkeleton rows={4} /> : <p>KSB details are unavailable. Please try again.</p>;
  if (metric) return <section className="rounded-2xl border border-foreground-200 bg-white p-5" aria-label="Programme KSB points">
          <h2 className="font-semibold">Programme KSB Progress</h2>
          <p className="mt-2 text-2xl font-bold">{metric.percent == null ? '—' : `${metric.percent}%`}</p>
          <p className="mt-1 text-sm text-foreground-500">{metric.total == null
            ? 'Previous KSB details are not available yet.'
            : `${metric.completed} of ${metric.total} points achieved across all components`}</p>
          {!!metric.codes?.length && <div className="mt-5 overflow-x-auto"><table className="w-full text-left text-sm">
            <thead><tr className="border-b text-foreground-500"><th className="py-3">KSB</th><th>Points achieved</th><th>Total points</th><th>Progress</th></tr></thead>
            <tbody>{metric.codes.map(item => <tr key={item.code} className="border-b border-foreground-100">
              <td className="py-3 font-semibold">{item.code}</td><td>{item.completed}</td><td>{item.total}</td><td>{item.percent}%</td>
            </tr>)}</tbody>
          </table></div>}
        </section>;
  return (
    <div className={showHero ? 'p-3 md:p-6 space-y-5 md:space-y-6' : 'space-y-4 md:space-y-5'}>
        {/* Hero */}
        {showHero && (
        <section
          className="relative min-h-[150px] overflow-hidden rounded-3xl border border-primary-700/40 p-6 text-white shadow-[0_18px_45px_rgba(35,8,76,0.20)] md:p-7"
          style={{ background: 'linear-gradient(115deg, oklch(var(--primary-950)) 0%, oklch(var(--primary-900)) 42%, oklch(var(--primary-700)) 100%)' }}
        >
          <div className="pointer-events-none absolute -left-20 -top-28 h-64 w-64 rounded-full bg-primary-400/20 blur-3xl" />
          <div className="pointer-events-none absolute -bottom-32 right-28 h-64 w-64 rounded-full bg-secondary-400/15 blur-3xl" />

          <div className="relative flex min-h-[94px] flex-col justify-between gap-6 sm:flex-row sm:items-center">
            <div className="flex min-w-0 items-center gap-4">
              <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl border border-white/15 bg-white/10 text-white shadow-inner backdrop-blur">
                <AppIcon className="ri-bar-chart-2-line text-2xl" />
              </span>
              <div className="min-w-0">
                <span className="mb-1.5 inline-flex items-center gap-1.5 rounded-full bg-white/10 px-2.5 py-1 text-[9px] font-semibold uppercase tracking-wider text-white/75">
                  <AppIcon className="ri-checkbox-circle-line" />KSB completion
                </span>
                <h1 className="text-2xl font-heading font-bold tracking-tight !text-white md:text-3xl">KSB Progress</h1>
                <p className="mt-1 text-sm !text-white/65">
                  Track the KSBs evidenced by completed activities in {isObserver ? 'this' : 'your'} training plan
                </p>
              </div>
            </div>

            <div className="flex w-full shrink-0 items-center justify-center gap-4 rounded-2xl border border-white/15 bg-white/10 px-4 py-3 shadow-lg shadow-black/10 backdrop-blur-md sm:w-auto">
              <div className="relative flex items-center justify-center">
                <Ring percent={overallPct} colorClass="text-emerald-300" size={68} stroke={6} />
                <span className="absolute text-sm font-heading font-bold tabular-nums !text-white">{overallPct}%</span>
              </div>
              <div className="min-w-[96px]">
                <p className="text-[9px] font-semibold uppercase tracking-wider !text-white/55">Fully evidenced</p>
                <p className="mt-1 text-xl font-heading font-bold leading-none tabular-nums !text-white">
                  {complete}<span className="text-sm font-medium !text-white/50"> / {total}</span>
                </p>
                <p className="mt-1 text-[9px] !text-white/50">{w(earnedWeight)} / {w(availableWeight)} mapped weight</p>
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/15">
                  <div className="h-full rounded-full bg-emerald-300 transition-all duration-700" style={{ width: `${overallPct}%` }} />
                </div>
              </div>
            </div>
          </div>
        </section>
        )}

        {/* Stat strip */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 md:gap-4">
          <StatCard icon="ri-checkbox-circle-line" tint="bg-emerald-100 text-emerald-600" label="Fully evidenced" value={complete} total={total} barClass="bg-emerald-500" />
          <StatCard icon="ri-progress-4-line" tint="bg-amber-100 text-amber-600" label="In progress" value={inProgress} total={total} barClass="bg-amber-500" />
          <StatCard icon="ri-circle-line" tint="bg-background-200 text-foreground-500" label="Not started" value={notStarted} total={total} barClass="bg-foreground-300" />
        </div>

        {/* Filters */}
        {total > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            {([
              ['all', `All ${total}`],
              ['complete', `Fully evidenced ${complete}`],
              ['in-progress', `In progress ${inProgress}`],
              ['not-started', `Not started ${notStarted}`],
            ] as [Filter, string][]).map(([key, label]) => (
              <button
                key={key}
                onClick={() => setFilter(key)}
                className={`px-3 py-1.5 rounded-lg text-[11px] font-semibold transition-colors cursor-pointer ${
                  filter === key ? 'bg-foreground-900 text-background-50' : 'bg-background-100 text-foreground-500 hover:text-foreground-700'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        )}

        {/* Category cards */}
        {loading ? (
          <div className="bg-background-50 rounded-xl border border-foreground-200/60 p-5"><RowsSkeleton rows={4} /></div>
        ) : total === 0 ? (
          <div className="bg-background-50 rounded-xl border border-foreground-200/60 p-6"><EmptyState text="No KSBs defined for this programme yet." /></div>
        ) : visible.length === 0 ? (
          <div className="bg-background-50 rounded-xl border border-foreground-200/60 p-6"><EmptyState text="No KSBs match this filter." /></div>
        ) : (
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 md:gap-5 items-start">
            {groups.map((g) => (
              <div key={g.type} className="bg-background-50 rounded-xl border border-foreground-200/60 p-4 md:p-5">
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-2.5">
                    <span className={`w-9 h-9 rounded-lg flex items-center justify-center ${g.tint}`}><AppIcon className={`${g.icon} text-base`} /></span>
                    <div>
                      <p className="text-sm font-heading font-bold text-foreground-900">{g.label}</p>
                      <p className="text-[11px] text-foreground-400">
                        {g.complete} of {g.total} fully evidenced
                        {g.available > 0 && <> · {w(g.earned)}/{w(g.available)} weight</>}
                      </p>
                    </div>
                  </div>
                  <div className="relative flex items-center justify-center">
                    <Ring percent={g.pct} colorClass={g.ring} size={48} stroke={5} />
                    <span className="absolute text-[10px] font-heading font-bold tabular-nums text-foreground-700">{g.pct}%</span>
                  </div>
                </div>
                <div className="space-y-1.5 max-h-[32rem] overflow-y-auto pr-1">
                  {g.items.map((k) => (
                    <KsbRow
                      key={k.code}
                      ksb={k}
                      isObserver={isObserver}
                      open={expanded === k.code}
                      onToggle={() => setExpanded(expanded === k.code ? null : k.code)}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
    </div>
  );
}

/* One KSB: collapsed shows weight + progress; expanded names the activities. */
function KsbRow({ ksb, open, onToggle, isObserver }: { ksb: KsbProgress; open: boolean; onToggle: () => void; isObserver?: boolean }) {
  const st = STATUS_META[ksb.status];
  const hasContributors = ksb.contributors.length > 0;

  return (
    <div className={`rounded-lg border transition-colors ${open ? 'border-primary-200 bg-white' : 'border-transparent hover:bg-background-100/60'}`}>
      <button
        onClick={onToggle}
        disabled={!hasContributors}
        className={`w-full text-left px-2.5 py-2 ${hasContributors ? 'cursor-pointer' : 'cursor-default'}`}
      >
        <div className="flex items-start gap-2">
          <span className={`shrink-0 w-2 h-2 rounded-full mt-1.5 ${st.dot}`} />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[12px] font-semibold text-primary-600">{ksb.code}</span>
              <span className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-semibold tabular-nums ${
                ksb.availableWeight > 0
                  ? 'bg-primary-50 text-primary-700'
                  : 'bg-background-100 text-foreground-400'
              }`}>
                <AppIcon className="ri-scales-3-line text-[9px]" />
                {w(ksb.earnedWeight)} / {w(ksb.availableWeight)} weight
              </span>
            </div>
            <p className="text-[12px] text-foreground-600 leading-snug line-clamp-2">{ksb.description}</p>
          </div>
          <div className="shrink-0 flex items-center gap-1.5">
            <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${st.badge}`}>{st.label}</span>
            {hasContributors && <AppIcon className={`ri-arrow-down-s-line text-foreground-400 text-sm transition-transform ${open ? 'rotate-180' : ''}`} />}
          </div>
        </div>

        <div className="mt-1.5 ml-4 flex items-center gap-2">
          <div className="flex-1 h-1.5 rounded-full bg-background-200 overflow-hidden">
            <div className={`h-full rounded-full ${st.bar}`} style={{ width: `${ksb.pct}%`, transition: 'width 700ms ease-out' }} />
          </div>
          <span className="text-[10px] font-semibold text-foreground-500 tabular-nums w-8 text-right">{ksb.pct}%</span>
        </div>
      </button>

      {open && hasContributors && (
        <div className="px-2.5 pb-2.5 pt-0.5 ml-4 space-y-2">
          {/* Criteria — what it takes to finish this KSB */}
          <div className={`rounded-lg border p-2.5 ${ksb.status === 'complete' ? 'border-emerald-200 bg-emerald-50/60' : 'border-amber-200 bg-amber-50/60'}`}>
            <p className="text-[10px] font-semibold uppercase tracking-wider text-foreground-500 mb-1 flex items-center gap-1">
              <AppIcon className={ksb.status === 'complete' ? 'ri-checkbox-circle-line text-emerald-600' : 'ri-flag-line text-amber-600'} />
              {ksb.status === 'complete' ? 'Achieved' : 'To achieve this KSB'}
            </p>
            <p className="text-[11px] text-foreground-700 leading-relaxed">
              {ksb.status === 'complete' ? (
                isObserver ? (
                  <>All {ksb.totalCount} {ksb.totalCount === 1 ? 'activity' : 'activities'} that develop this KSB are
                    complete, earning the full {w(ksb.availableWeight)} weight.</>
                ) : (
                  <>You have completed all {ksb.totalCount} {ksb.totalCount === 1 ? 'activity' : 'activities'} that develop
                    this KSB, earning the full {w(ksb.availableWeight)} weight.</>
                )
              ) : isObserver ? (
                <>{ksb.totalCount - ksb.doneCount} of {ksb.totalCount}{' '}
                  {ksb.totalCount === 1 ? 'activity' : 'activities'} below still to do, worth the last{' '}
                  <span className="font-semibold">{w(ksb.remainingWeight)}</span> weight
                  ({w(ksb.earnedWeight)} of {w(ksb.availableWeight)} so far).</>
              ) : (
                <>Complete the remaining {ksb.totalCount - ksb.doneCount} of {ksb.totalCount}{' '}
                  {ksb.totalCount === 1 ? 'activity' : 'activities'} below to earn the last{' '}
                  <span className="font-semibold">{w(ksb.remainingWeight)}</span> weight
                  ({w(ksb.earnedWeight)} of {w(ksb.availableWeight)} so far).</>
              )}
            </p>
          </div>

          {/* Activities that fulfil this KSB */}
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-wider text-foreground-400 mb-1">
              Activities that develop this KSB
            </p>
            <ul className="space-y-1">
              {ksb.contributors.map((c, i) => {
                const meta = componentTypeMeta(c.title);
                return (
                  <li key={`${c.componentId}-${i}`} className="flex items-start gap-2 rounded-lg border border-background-200 bg-background-50 px-2 py-1.5">
                    <AppIcon className={`${c.done ? 'ri-checkbox-circle-fill text-emerald-600' : 'ri-checkbox-blank-circle-line text-foreground-300'} text-sm shrink-0 mt-0.5`} />
                    <span className="min-w-0 flex-1">
                      <span className="block text-[11px] font-semibold text-foreground-800 leading-snug truncate">
                        {meta.detail || meta.label}
                      </span>
                      <span className="block text-[10px] text-foreground-400 truncate">
                        {[meta.label, c.week].filter(Boolean).join(' · ')}
                      </span>
                    </span>
                    <span className="shrink-0 text-right">
                      <span className={`block text-[11px] font-semibold tabular-nums ${c.done ? 'text-emerald-700' : 'text-foreground-500'}`}>
                        +{w(c.weight)}
                      </span>
                      {c.classification && (
                        <span className="block text-[9px] text-foreground-400 capitalize">{c.classification}</span>
                      )}
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}

function StatCard({ icon, tint, label, value, total, barClass, hint }: {
  icon: string; tint: string; label: string; value: number; total: number; barClass: string; hint?: string;
}) {
  const pct = total ? Math.round((value / total) * 100) : 0;
  const iconGradient = tint.includes('emerald')
    ? 'bg-gradient-to-br from-[#b9f6db] via-[#34d399] to-[#059669] text-white shadow-sm shadow-emerald-500/25'
    : tint.includes('amber')
      ? 'bg-gradient-to-br from-[#f8dda0] via-[#d49a38] to-[#b27715] text-white shadow-sm shadow-[#b27715]/25'
      : 'bg-gradient-to-br from-[#e5e7eb] via-[#9ca3af] to-[#4b5563] text-white shadow-sm shadow-foreground-400/25';
  return (
    <div className="coach-metric-card">
      <div className="flex items-center gap-2 mb-2">
        <span className={`flex h-10 w-10 items-center justify-center rounded-xl ring-1 ring-black/5 ${iconGradient}`}><AppIcon className={`${icon} text-base`} /></span>
        <span className="text-xs text-foreground-400">{label}</span>
      </div>
      <p className="text-xl font-heading font-bold text-foreground-900 tabular-nums leading-tight">{value}<span className="text-sm text-foreground-400">/{total}</span></p>
      <div className="mt-2 h-1.5 w-full rounded-full bg-background-200 overflow-hidden">
        <div className={`h-full rounded-full ${barClass}`} style={{ width: `${pct}%` }} />
      </div>
      {hint && <p className="text-[10px] text-foreground-400 mt-1.5 leading-snug">{hint}</p>}
    </div>
  );
}
