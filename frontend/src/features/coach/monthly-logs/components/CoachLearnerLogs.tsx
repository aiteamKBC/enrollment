import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from "@/hooks/useAuth.helpers";
import { coachViewAs } from '@/lib/coachViewAs';
import { EmptyState } from '@/components/ui/EmptyState';
import { CoachMonthList, MonthIndexSkeleton } from '@/features/monthly-logs/MonthList';
import { MonthlyLog } from '@/features/monthly-logs/page';
import { coachLogQueryOptions, getCoachLogYear, getCoachLogDetail, type CoachLogYear, type CoachLogDetail } from '../api/monthlyLogsApi';

export function CoachLearnerLogs({ id, month }: { id: string; month?: string }) {
  const { auth } = useAuth();
  const [year, setYear] = useState(new Date().getFullYear());
  const [pendingOnly, setPendingOnly] = useState(false);
  const query = useQuery<CoachLogYear | CoachLogDetail>({
    queryKey: ['monthly-logs', auth.account?.id, 'coach', coachViewAs()?.email ?? null, id, month ? 'detail' : 'year', month ?? year],
    queryFn: () => month ? getCoachLogDetail(id, month) : getCoachLogYear(id, year),
    ...coachLogQueryOptions,
  });
  if (query.isPending) return <MonthIndexSkeleton perspective="coach" />;
  if (query.error && !query.data) return <EmptyState variant="error" title="Unable to load monthly logs" description={query.error.message}
    action={<button onClick={() => void query.refetch()}>Try again</button>} />;
  if (!query.data) return null;
  const base = `/coach/monthly-logs/${id}`;
  return <>
    {query.error && <p role="alert">Updates are temporarily unavailable. <button onClick={() => void query.refetch()}>Try again</button></p>}
    {'detail' in query.data && month
      ? <MonthlyLog id={id} month={month} base={base} perspective="coach" summary={query.data.summary} loadedDetail={query.data.detail} />
      : 'months' in query.data ? <CoachMonthList projection={query.data} base={base} year={String(year)} onYearChange={value => setYear(Number(value))}
        pendingOnly={pendingOnly} onPendingChange={setPendingOnly} /> : null}
  </>;
}
