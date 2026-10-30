import { useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from "@/hooks/useAuth.helpers";
import { coachViewAs } from '@/lib/coachViewAs';
import { AppIcon } from '@/components/feature/AppIcon';
import { SkeletonBlock } from '@/components/feature/Skeletons';
import { EmptyState } from '@/components/ui/EmptyState';
import styles from '@/features/monthly-logs/monthlyLogs.module.css';
import { getCoachMonthlyLogLearners, coachLogQueryOptions } from '../api/monthlyLogsApi';
import { filterCoachMonthlyLogLearners } from '../selectors/monthlyLogsSelectors';
import type { CoachMonthlyLogLearner } from '../types/monthlyLogs.types';

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0]).join('').toUpperCase() || 'L';
}

function CoachMonthlyLogsSkeleton() {
  return <div className={styles.coachWorkspace} role="status" aria-label="Loading coach monthly logs" aria-busy="true">
    <span className="sr-only">Loading coach monthly logs…</span>
    <header className={`${styles.coachHero} ${styles.coachLoadingHero}`} aria-hidden="true">
      <div className={styles.coachLoadingHeading}>
        <SkeletonBlock className="h-7 w-52 max-w-full" />
        <SkeletonBlock className="h-3 w-72 max-w-full" />
      </div>
    </header>
    <div className={styles.coachWorkspaceGrid} aria-hidden="true">
      <aside className={styles.coachLearnerPanel}>
        <div className={styles.coachLearnerPanelHeader}>
          <SkeletonBlock className="h-5 w-24" />
          <SkeletonBlock className="h-10 w-full rounded-lg" />
        </div>
        <div className={styles.coachLoadingLearners}>
          {[0, 1, 2, 3].map(index => <div key={index}>
            <SkeletonBlock className="h-11 w-11 shrink-0 rounded-full" />
            <div><SkeletonBlock className="h-3 w-28 max-w-full" /><SkeletonBlock className="h-2.5 w-40 max-w-full" /></div>
          </div>)}
        </div>
      </aside>
      <section className={styles.coachLogOverview}>
        <div className={`${styles.coachProfileCard} ${styles.coachLoadingCard}`}><SkeletonBlock className="h-16 w-full" /></div>
        <div className={`${styles.coachSummaryCard} ${styles.coachLoadingCard}`}><SkeletonBlock className="h-20 w-full" /></div>
        <div className={`${styles.coachMonthsCard} ${styles.coachLoadingCard}`}><SkeletonBlock className="h-40 w-full" /></div>
      </section>
    </div>
  </div>;
}

export function CoachMonthlyLogLearners({ selectedLearnerId, renderSelected }: {
  selectedLearnerId?: string;
  renderSelected: (learner: CoachMonthlyLogLearner) => ReactNode;
}) {
  const { auth } = useAuth();
  const [search, setSearch] = useState('');
  const query = useQuery({
    queryKey: ['monthly-logs', auth.account?.id, coachViewAs()?.email, 'learners'],
    queryFn: getCoachMonthlyLogLearners,
    ...coachLogQueryOptions,
    // A coach returning to this route should keep the successful list visible;
    // React Query can still refresh it without reverting to the first-load UI.
    gcTime: Infinity,
  });
  if (query.isPending) return <CoachMonthlyLogsSkeleton />;
  if (query.error) return <EmptyState variant="error" title="Unable to load monthly logs" description={query.error.message}
    action={<button className={styles.coachRetryButton} onClick={() => void query.refetch()}>Try again</button>} />;

  const learners = filterCoachMonthlyLogLearners(query.data.learners, search);
  const selected = query.data.learners.find(learner => String(learner.id) === selectedLearnerId);

  return <div className={styles.coachWorkspace}>
    <header className={styles.coachHero}>
      <div><h1>Monthly Logs</h1><p>Review activities and monthly signatures</p></div>
      <img src="/assets/monthly-submission-calendar.png" alt="" aria-hidden="true" />
    </header>

    <div className={styles.coachWorkspaceGrid}>
      <aside className={styles.coachLearnerPanel} aria-label="Monthly log learners">
        <div className={styles.coachLearnerPanelHeader}>
          <h2>Learners</h2>
          <label className={styles.coachLearnerSearch}>
            <span className="sr-only">Search learners</span>
            <AppIcon className="ri-search-line" aria-hidden="true" />
            <input type="search" placeholder="Search learners..." value={search} onChange={event => setSearch(event.target.value)} />
          </label>
        </div>
        {learners.length ? <nav className={styles.coachLearnerList} aria-label="Choose a learner">
          {learners.map((learner, index) => {
            const active = learner.id === selected?.id;
            return <Link
              className={styles.coachLearnerLink}
              data-tone={index % 5}
              aria-current={active ? 'page' : undefined}
              to={`/coach/monthly-logs/${learner.id}`}
              key={learner.id}
            >
              <span className={styles.coachLearnerAvatar}>{initials(learner.name)}</span>
              <span className={styles.coachLearnerText}><strong>{learner.name}</strong><small>{learner.programme}</small></span>
              <AppIcon className="ri-arrow-right-s-line" aria-hidden="true" />
            </Link>;
          })}
        </nav> : <div className={styles.coachLearnerEmpty}><EmptyState title="No learners found" /></div>}
      </aside>

      <section className={styles.coachSelectedLearner} aria-label={selected ? `${selected.name} monthly logs` : 'Selected learner monthly logs'}>
        {selected ? renderSelected(selected) : <EmptyState title={selectedLearnerId ? 'Learner not found' : 'Choose a learner to view monthly logs'} description="Choose a learner from your assigned caseload." />}
      </section>
    </div>
  </div>;
}
