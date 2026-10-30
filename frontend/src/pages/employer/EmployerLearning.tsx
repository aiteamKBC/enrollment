import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from "@/hooks/useAuth.helpers";
import type { LearnerKind } from '@/api/learnerDetail';
import type { TrainingPlanDashboard } from '@/api/trainingPlanDashboard';
import { timelineModuleForDisplay } from '@/api/dashboardTraining';
import { fetchEmployerLearningOverview, fetchEmployerTraining } from '@/api/employerPortal';
import { OverviewCharts } from '@/pages/workspace/learner/OverviewCharts';
import { DashboardProgressStat } from '@/pages/workspace/learner/DashboardProgressStat';
import { dashboardOtjhProgress } from '@/pages/workspace/learner/dashboardOtjhProgress';
import { ModuleTimeline } from '@/pages/learner/training-plan-timeline/ModuleTimeline';
import { ModuleOverview } from '@/pages/learner/training-plan-timeline/ModuleOverview';
import styles from '@/pages/learner/training-plan-timeline/trainingPlan.module.css';
import layout from '@/pages/learner/training-plan-timeline/TrainingPlanDetails.module.css';
import overviewStyles from '@/pages/workspace/learner/Overview.module.css';

type Identity = { employerId: string; kind: LearnerKind; learnerId: string };

export function EmployerLearningOverview({ employerId, kind, learnerId }: Identity) {
  const { auth } = useAuth();
  const query = useQuery({
    queryKey: ['employer-learning-overview', auth.account?.id, employerId, kind, learnerId],
    queryFn: ({ signal }) => fetchEmployerLearningOverview(employerId, kind, learnerId, signal),
    staleTime: 30_000,
  });
  const payload = query.data;
  const { target, percent } = dashboardOtjhProgress(payload?.metrics.otjh.actual, payload?.metrics.otjh.targetToDate);
  const base = `/employers/${employerId}/learner/${kind}/${learnerId}`;
  return <div className={`${overviewStyles.overview} ${layout.embeddedLearnerTheme} learner-dashboard employer-learner-dashboard`}><div className={overviewStyles.dashboardTabContent}>
    {query.error && <p role="alert">{query.error.message} <button onClick={() => void query.refetch()}>Retry overview</button></p>}
    {!payload && query.isPending && <p role="status">Loading learner progress…</p>}
    {payload && <>
      <div className={`${overviewStyles.metrics} ${overviewStyles.metricsInline}`} style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 16rem), 1fr))' }}>
        <DashboardProgressStat href={base} label="Attendance" value={payload.attendance?.present.toLocaleString('en-GB') ?? '—'}
          summary={payload.attendance ? `${payload.attendance.present} / ${payload.attendance.sessions}` : '—'}
          valueLabel="Attended" targetValue={payload.attendance?.sessions.toLocaleString('en-GB') ?? '—'} targetLabel="Sessions to date"
          percent={payload.attendance?.attendanceRate ?? null} accent="green" />
        <DashboardProgressStat href={`${base}?tab=otjh`} label="OTJ Hours" value={payload.metrics.otjh.actual != null ? `${payload.metrics.otjh.actual.toFixed(2)} h` : 'Unavailable'}
          summary={`${payload.metrics.otjh.actual?.toFixed(2) ?? '—'} / ${target?.toFixed(2) ?? '—'} h`} valueLabel="Actual" targetValue={target != null ? `${target.toFixed(2)} h` : 'Unavailable'}
          targetLabel="Target to date" percent={percent} accent="yellow" />
        <DashboardProgressStat href={`${base}?tab=ksbs`} label="KSB Progress" value={payload.metrics.ksb.percent == null ? '—' : `${payload.metrics.ksb.percent}%`}
          summary={payload.metrics.ksb.total == null ? '—' : `${payload.metrics.ksb.completed} / ${payload.metrics.ksb.total}`}
          percent={payload.metrics.ksb.percent} accent="purple" />
      </div>
      <OverviewCharts payload={payload} programmeStartDate={payload.programmeStartDate} programmeEndDate={payload.programmeEndDate} targetAsOfToday={target} />
    </>}
  </div></div>;
}

export function EmployerTrainingPlan({ employerId, kind, learnerId }: Identity) {
  const { auth } = useAuth();
  const [params] = useSearchParams();
  const [subject, setSubject] = useState(params.get('subject') || '');
  const [month, setMonth] = useState(params.get('month') || '');
  const query = useQuery({
    queryKey: ['employer-training', auth.account?.id, employerId, kind, learnerId, subject, month],
    queryFn: ({ signal }) => fetchEmployerTraining(employerId, kind, learnerId, subject, month, signal),
    staleTime: 30_000,
  });
  const payload = query.data;
  const selected = payload?.selectedModule ?? undefined;
  const modules = useMemo(() => (payload?.timeline.modules || []).map(module => module.id === selected?.id ? selected : timelineModuleForDisplay(module)), [payload, selected]);
  const today = new Date().toLocaleDateString('en-CA', { timeZone: 'Europe/London' });
  const start = payload?.timeline.start || '';
  const end = payload?.timeline.end || '';
  const selectedMonth = month || (start && today.slice(0, 7) < start ? start : end && today.slice(0, 7) > end ? end : today.slice(0, 7));
  const data = useMemo<TrainingPlanDashboard>(() => ({
    months: Object.fromEntries([...(payload?.timeline.monthKeys || []), start, end].filter(Boolean).map(key => [key, { label: '', topics: [], planned: null, source: 'training' }])),
    actual: [], actualAvailable: false, modules: [], moduleLinks: {}, sessions: [], reviews: payload?.timeline.reviews || [],
    coach: { name: payload?.timeline.coachName || '', bookingUrl: null }, contractStatus: 'ready', generatedAt: '',
  }), [payload, start, end]);
  return <div className={`${styles.root} ${layout.root} ${layout.embeddedLearnerTheme} learner-dashboard`}>
    {query.error && <p role="alert">{query.error.message} <button onClick={() => void query.refetch()}>Retry training plan</button></p>}
    {!payload && query.isPending && <p role="status">Loading training plan…</p>}
    {payload && <section aria-label="Monthly learning and coaching">
      <div className={layout.toolbar}><div className={layout.title}><h2>Training plan</h2></div></div>
      <div className={`${layout.cards} ${layout.trainingCards}`}>
        <div className={layout.learningVisuals}><ModuleTimeline data={data} modules={modules} kind={kind} learnerId={learnerId} today={today}
          programmeStartDate={payload.timeline.programmeStartDate} detailsMode="overview" selectedMonth={selectedMonth} selectedId={payload.selectedModuleId || ''}
          canOpenActivities={false} canOpenCalendar={false}
          onMonthChange={next => { setMonth(next); setSubject(''); }} onModuleSelect={module => { setSubject(module.id); setMonth(selectedMonth); }} /></div>
        <ModuleOverview module={selected} hasModules={modules.length > 0} coachName={data.coach.name} href="" canOpenActivities={false} showSchedule={false} />
      </div>
    </section>}
  </div>;
}
