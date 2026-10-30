import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from "@/hooks/useAuth.helpers";
import type { LearnerKind } from '@/api/learnerDetail';
import { fetchEmployerMonthlyLog, fetchEmployerMonthlyLogContent, fetchEmployerMonthlyLogSummary } from '@/api/employerPortal';
import { EmptyState } from '@/components/ui/EmptyState';
import { MonthList, MonthIndexSkeleton } from '@/features/monthly-logs/MonthList';
import { MonthlyLog, type MonthlyLogReader } from '@/features/monthly-logs/page';
import design from '@/features/old-otjh/design.module.css';
import journal from '@/features/old-otjh/journal.module.css';
import styles from '@/features/monthly-logs/monthlyLogs.module.css';

export function EmployerMonthlyLogsTab({ employerId, kind, learnerId }: {
  employerId: string; kind: LearnerKind; learnerId: string;
}) {
  const { auth } = useAuth();
  const [month, setMonth] = useState<string>();
  const reader = useMemo<MonthlyLogReader>(() => ({
    cacheScope: `employer-monthly-logs:${employerId}:${kind}:${learnerId}`,
    month: (selected, signal) => fetchEmployerMonthlyLog(employerId, kind, learnerId, selected, signal),
    content: (selected, rowId) => fetchEmployerMonthlyLogContent(employerId, kind, learnerId, selected, rowId),
  }), [employerId, kind, learnerId]);
  const query = useQuery({
    queryKey: ['employer-monthly-logs', auth.account?.id, employerId, kind, learnerId],
    queryFn: ({ signal }) => fetchEmployerMonthlyLogSummary(employerId, kind, learnerId, signal),
    refetchInterval: 7000,
  });
  const base = `/employers/${employerId}/learner/${kind}/${learnerId}`;
  const summary = query.data && Array.isArray(query.data.months)
    ? { ...query.data, read_only: true, months: [...query.data.months].sort((a, b) => a.month.localeCompare(b.month)) }
    : null;
  return <div className={`${design.scope} ${design.page} ${styles.theme} ${month ? journal.canvas : ''}`}>
    {query.isPending ? <MonthIndexSkeleton perspective="learner" />
      : !summary ? <EmptyState variant="error" title="Unable to load monthly logs" description={query.error?.message || 'The monthly logs response was incomplete.'}
        action={<button className={journal.secondaryButton} onClick={() => void query.refetch()}>Try again</button>} />
        : <>
          {query.error && <p role="alert">Updates are temporarily unavailable. <button onClick={() => void query.refetch()}>Try again</button></p>}
          {month ? <MonthlyLog key={`${reader.cacheScope}:${month}`} id={learnerId} month={month} summary={summary}
            base={base} perspective="learner" observer reader={reader} onMonthChange={setMonth} />
            : <MonthList summary={summary} base={base} perspective="learner" onMonthSelect={setMonth} />}
        </>}
  </div>;
}
