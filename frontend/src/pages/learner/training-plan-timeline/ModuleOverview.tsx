import { Link } from 'react-router-dom';
import { useCaseFileModuleDetail } from '@/features/coach/case-file/hooks/useCaseFileModuleDetail';
import type { ReactNode } from 'react';
import { ArrowRight, CalendarDays, Clock3, GraduationCap, Layers3, Target, Users, type LucideIcon } from 'lucide-react';
import { percent, type DisplayTimelineModule } from './model';
import { CurriculumTimeline } from './CurriculumTimeline';
import { Meter, State } from './presentation';
import { dateLabel, hours, moduleStatus } from "./presentation.helpers";
import styles from './trainingPlan.module.css';
import layout from './TrainingPlanDetails.module.css';

type Props = { module?: DisplayTimelineModule; hasModules: boolean; coachName: string; href: string; canOpenActivities: boolean; showSchedule?: boolean;
  /** Dates, staff, hours and progress only: for viewers outside the learner's own workspace. */
  simple?: boolean };
const activityLabels: Record<string, string> = {
  reading: 'Readings', video: 'Videos', podcast: 'Podcasts', audio: 'Audio activities', powerpoint: 'Presentations',
  quiz: 'Quizzes', reading_quiz: 'Readings & quizzes', assignment: 'Assignments', checkpoint: 'Checkpoints',
  reflection: 'Reflections', live_session: 'Live sessions', activity: 'Other activities',
};

export function ModuleOverview({ module, hasModules, coachName, href, canOpenActivities, showSchedule = true, simple = false }: Props) {
  const focused = useCaseFileModuleDetail(module?.detail?.id);
  const detail = focused?.module ?? module?.detail;
  const timetable = [detail?.session_week_day, detail?.session_start_time
    ? `${detail.session_start_time}${detail.session_end_time ? `–${detail.session_end_time}` : ''}` : ''].filter(Boolean).join(' · ');
  const outcomes = detail?.learning_outcomes || [];
  const codes = module?.ksbCodes || [];
  const achievedCodes = module?.achievedKsbCodes;
  const deliveryEnd = (detail?.effectiveEndDate || '').slice(0, 10);
  const studyHoursProgress = detail?.total_otjh != null && module?.actual != null
    ? percent(module.actual, detail.total_otjh) : null;
  return <section className={`${styles.panel} ${layout.overview} ${simple ? layout.simpleOverview : ''}`} aria-label="Module overview">
    {focused?.error && <p role="alert">{focused.error} <button type="button" onClick={focused.refresh}>Retry module details</button></p>}
    <div className={styles.panelHeading}>
      <div><p className={styles.eyebrow}>In focus</p><h2>Module overview</h2></div>
      {canOpenActivities && module && <Link className={styles.primary} to={href}>Go to module<ArrowRight size={15} /></Link>}
    </div>
    {module ? <>
      <div className={layout.moduleIdentity}><h3 className={styles.overviewTitle}>{module.title}</h3><State value={moduleStatus(module)} /></div>
      {!simple && (detail?.programme_name || detail?.cohort_name || detail?.group_name) && <div className={layout.moduleContext}>
        {detail.programme_name && <p>{detail.programme_name}</p>}
        <div>{detail.cohort_name && <span>Cohort: {detail.cohort_name}</span>}{detail.group_name && <span>Group: {detail.group_name}</span>}</div>
      </div>}
      {!simple && detail?.description && <p className={layout.moduleDescription}>{detail.description}</p>}
      <div className={layout.overviewGroups}>
        <div className={`${layout.overviewGroup} ${layout.scheduleGroup}`}><h3>Schedule</h3><dl className={`${layout.factGrid} ${layout.scheduleFacts}`}>
          <OverviewFact icon={CalendarDays} label="Start date">{module.start ? dateLabel(module.start) : 'To be confirmed'}</OverviewFact>
          <OverviewFact icon={CalendarDays} label="Planned end">{module.end ? dateLabel(module.end) : 'To be confirmed'}</OverviewFact>
          {/* Only when a holiday has actually moved the run. The scheduler
              states this date -- it is the last DELIVERED session -- so the
              learner reads the same delivery end the Module Builder does,
              rather than a stored date that stopped describing the run when
              the closure was ticked. Hidden when the two agree: repeating the
              same date under two labels reads as two different facts. */}
          {!simple && deliveryEnd && deliveryEnd !== module.end && (
            <OverviewFact icon={CalendarDays} label="Delivery ends">{dateLabel(deliveryEnd)}
              <small>Moved by a holiday closure</small></OverviewFact>
          )}
          {!simple && <>
            <OverviewFact icon={Layers3} label="Teaching weeks">{detail?.weeks_number ?? (module.weeks || 'To be confirmed')}</OverviewFact>
            <OverviewFact icon={Clock3} label="Weekly timetable">{timetable || 'To be confirmed'}{detail?.session_start_time && <small>UK time</small>}</OverviewFact>
          </>}
        </dl></div>
        <div className={layout.overviewGroup}><h3>Staff</h3><dl className={layout.factGrid}>
          <OverviewFact icon={Users} label="Coach">{detail?.coach_name || coachName || 'To be assigned'}</OverviewFact>
          <OverviewFact icon={GraduationCap} label="Tutor">{detail?.tutor_name || 'To be assigned'}</OverviewFact>
        </dl></div>
        <div className={layout.overviewGroup}><h3>Study hours</h3><dl className={layout.factGrid}>
          <OverviewFact icon={Target} label="Planned OTJH">{detail?.total_otjh == null ? 'Not provided' : `${hours(detail.total_otjh)} hours`}</OverviewFact>
          <OverviewFact icon={Clock3} label="Hours recorded">{module.actual == null ? 'Unavailable' : `${hours(module.actual)} hours`}</OverviewFact>
        </dl><div className={layout.studyHoursProgress}><Meter value={studyHoursProgress} label="Study hours progress" /><span>{studyHoursProgress == null ? '—' : `${studyHoursProgress}%`}</span></div></div>
      </div>
      {/* The curriculum as the Module Builder holds it: the taught weeks on the
          dates they are delivered, and the delivery days a cohort holiday
          closed still present as Reading Weeks. Without these the learner sees
          an unexplained gap where a bank holiday was. */}
      {!simple && <>
      {showSchedule && <CurriculumTimeline slots={detail?.curriculumSlots} sessions={module.sessions} />}
      {Object.keys(module.activityCounts).length > 0 && <div className={layout.moduleSection}>
        <h3>Learning activities <span>{module.activityCount}</span></h3>
        <ul className={layout.activityBreakdown}>{Object.entries(module.activityCounts).map(([type, count]) =>
          <li key={type}><span>{activityLabels[type] || type.replace(/[_-]/g, ' ')}</span><strong>{count}</strong></li>)}</ul>
      </div>}
      <div className={layout.moduleSection}>
        <h3>Knowledge, skills &amp; behaviours <span>{codes.length || ''}</span></h3>
        {achievedCodes == null ? <p className={styles.hint}>Achieved KSBs are not available yet.</p>
          : achievedCodes.length > 0 ? <div role="group" aria-label="Achieved KSBs">
            <h4>Achieved KSBs ({achievedCodes.length})</h4>
            <div className={layout.ksbCodes}>{achievedCodes.map(code => <span key={code}>{code}</span>)}</div>
          </div> : <p className={styles.hint}>No KSBs achieved yet.</p>}
        {codes.length ? <details className={layout.moduleDisclosure}>
          <summary>View {codes.length} mapped KSBs</summary>
          <div className={layout.ksbCodes}>{codes.map(code => <span key={code}>{code}</span>)}</div>
        </details> : !achievedCodes?.length && <p className={styles.hint}>{module.ksbMappingMissing ? 'KSB mapping is not available yet.' : 'No KSBs mapped yet.'}</p>}
        {codes.length > 0 && module.ksbMappingMissing && <p className={styles.hint}>Some activities are still awaiting KSB mapping.</p>}
      </div>
      {outcomes.length > 0 && <details className={`${layout.moduleSection} ${layout.moduleDisclosure}`}>
        <summary>Learning outcomes ({outcomes.length})</summary>
        <ul>{outcomes.map(outcome => <li key={outcome}>{outcome}</li>)}</ul>
      </details>}
      </>}
      <div className={`${styles.overviewProgress} ${layout.moduleProgress}`}>
        <div><span>Activity progress</span><strong>{module.progress}%</strong></div>
        <Meter value={module.progress} label="Selected module progress" />
        <p>{module.done} of {module.activityCount} activities completed · {Math.max(0, module.activityCount - module.done)} remaining</p>
      </div>
    </> : <p className={styles.empty}>{hasModules ? 'Choose a module above to see its details.' : 'Your modules will appear when they are assigned.'}</p>}
  </section>;
}

function OverviewFact({ icon: Icon, label, children }: { icon: LucideIcon; label: string; children: ReactNode }) {
  return <div><Icon size={16} aria-hidden="true" /><dt>{label}</dt><dd>{children}</dd></div>;
}
