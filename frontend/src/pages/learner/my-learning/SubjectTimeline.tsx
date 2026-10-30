import { ArrowRight, BarChart3, BookOpen, CalendarDays, Check, CheckCircle2, Clock3, FileText, ListChecks, Play } from 'lucide-react';
import { Link } from 'react-router-dom';
import type { Subject } from './SubjectWorkspace';
import type { PlanModule } from '@/api/trainingPlanDashboard';
import { dateKey } from '@/pages/learner/training-plan-timeline/model';
import { learningDate, learningHref, learningToday, nextLearningWeek, type LearningWeek } from './subjectLearning';
import { formatHoursMinutes } from '@/utils/learnerJourney';
import { HolidayNoteHint } from '@/components/feature/HolidayNoteHint';
import styles from './SubjectWorkspace.module.css';

export function LearningMapHero({ subject, weeks, kind, learnerId, planModule, planLabel }: { subject?: Subject; weeks: LearningWeek[]; kind?: string; learnerId?: string; planModule?: PlanModule; planLabel?: string }) {
  const complete = weeks.filter(week => week.activities.length > 0 && week.activities.every(activity => activity.completed)).length;
  const unit = weeks.every(week => /^week\b/i.test(week.label)) ? 'weeks' : 'sections';
  const next = subject ? nextLearningWeek(subject) : undefined;
  const start = dateKey(planModule?.start_date), end = dateKey(planModule?.end_date);
  return <header className={styles.mapHero}>
    <div className={styles.mapHeroCopy}>
      <p className={styles.eyebrow}>Your learning journey</p>
      <h1>Your next step starts here.</h1>
      <p className={styles.mapHeroSubject}>{subject?.title || 'Explore your learning, one week at a time.'}</p>
      {subject && planLabel && <p className={styles.mapHeroSchedule}><CalendarDays size={14} aria-hidden="true" /><span><strong>{planLabel}</strong>{(start || end) && <> · {start ? learningDate(start) : 'Start date pending'} – {end ? learningDate(end) : 'End date pending'}<span> · Training plan (UK dates)</span></>}</span></p>}
    </div>
    <div className={styles.mapHeroActions}>
      {weeks.length > 0 && <div className={styles.mapHeroProgress}>
        <p>{complete} of {weeks.length} {unit} complete</p>
        {weeks.length <= 6 ? <div className={styles.mapSteps} aria-hidden="true">{weeks.map(week => {
          const done = week.activities.length > 0 && week.activities.every(activity => activity.completed);
          return <span key={week.id} data-state={done ? 'complete' : next?.id === week.id ? 'current' : 'pending'}>{done && <Check size={13} strokeWidth={3} />}</span>;
        })}</div> : <div className={styles.mapHeroTrack} aria-hidden="true"><span style={{ width: `${complete / weeks.length * 100}%` }} /></div>}
      </div>}
      <Link className={styles.mapHeroLink} to={learningHref('catalogue', kind, learnerId, subject?.id)}><BookOpen size={21} aria-hidden="true" />{subject ? 'Open subject' : 'My Learning'}<ArrowRight size={18} aria-hidden="true" /></Link>
    </div>
  </header>;
}

export function SubjectTimeline({ subject, weeks, search, onOpen, holidayNotes }: { subject: Subject; weeks: LearningWeek[]; search: string; onOpen: (week: string) => void;
  /** The curriculum team's published holiday hints for this module, by week id. */
  holidayNotes?: Map<string, string> }) {
  const today = learningToday();
  const next = nextLearningWeek(subject, today);
  const term = search.trim().toLowerCase();
  const visible = weeks.filter(w => !term || `${w.label} ${w.title} ${subject.title}`.toLowerCase().includes(term) || w.activities.some(a => a.title.toLowerCase().includes(term)));
  if (!visible.length) return <div className={styles.empty}><BookOpen size={28} /><p>{weeks.length ? 'No weeks or activities match your search.' : 'Weeks will appear when activities are added to this module.'}</p></div>;
  return <div className={styles.timelineSection}>
    <div className={styles.timelineHeading}><div><h2>Your weekly timeline</h2><p>A clear path through your learning.</p></div>{term && <span>{visible.length} of {weeks.length} sections shown</span>}</div>
    <ol className={styles.timeline} aria-label={`${subject.title} weekly timeline`}>
    {visible.map(week => {
      const done = week.activities.filter(a => a.completed).length;
      const percent = week.activities.length ? Math.round(done / week.activities.length * 10000) / 100 : 0;
      const complete = week.activities.length > 0 && done === week.activities.length;
      const current = !!week.start && week.start <= today && (week.end || week.start) >= today;
      const upcoming = !!week.start && week.start > today;
      const highlighted = !complete && week.activities.length > 0 && (current || (next?.id === week.id && !upcoming));
      const status = !week.activities.length ? 'Materials coming soon' : complete ? 'Complete' : current ? 'This week' : done ? 'In progress' : week.start && week.start > today ? 'Upcoming' : 'Not started';
      const state = complete ? 'complete' : current || done ? 'current' : 'pending';
      const StatusIcon = complete ? CheckCircle2 : current || done ? Clock3 : CalendarDays;
      const ActionIcon = complete ? FileText : highlighted || done ? Play : BookOpen;
      const number = /^week\s*(\d+)/i.exec(week.label)?.[1];
      const ksbs = [...new Set(week.activities.flatMap(a => a.native?.ksbMappings?.map(k => k.code) || []))];
      const hours = week.activities.map(a => a.legacy ? a.legacy.planned_hours_mapped ? a.legacy.planned : null
        : a.native?.isQuiz || a.native?.type?.toLowerCase() === 'quiz' ? 0 : a.native?.expectedOtjh ?? null);
      const planned = hours.length && hours.every(h => h != null && Number.isFinite(h)) ? hours.reduce((sum, h) => sum + h!, 0) : null;
      return <li key={week.id} className={styles.timelineItem} data-state={state} data-highlighted={highlighted || undefined} aria-current={current ? 'date' : undefined}>
        <span className={styles.timelineDot} aria-hidden="true">{complete && <Check size={13} strokeWidth={3} />}</span>
        <button type="button" className={styles.weekCard} onClick={() => onOpen(week.id)} aria-label={`Open ${week.label}: ${week.title}`}>
          <div className={styles.weekTop}>
            <span className={styles.weekIdentity}><span className={styles.weekNumber} aria-hidden="true">{number || <BookOpen size={17} />}</span><span>{week.label}{week.start && <span className={styles.weekDates}>{learningDate(week.start)}{week.end && week.end !== week.start ? ` – ${learningDate(week.end)}` : ''}</span>}</span></span>
            <span className={styles.weekStatus} data-state={state}><StatusIcon size={13} aria-hidden="true" />{status}</span>
          </div>
          <h3>{week.title}</h3>
          <div className={styles.weekFacts}>
            <div><ListChecks size={19} aria-hidden="true" /><div><span>Activities</span><strong>{done} / {week.activities.length}</strong></div></div>
            <div><Clock3 size={19} aria-hidden="true" /><div><span>Planned OTJH</span><strong>{planned == null ? 'Not available' : formatHoursMinutes(planned)}</strong></div></div>
            <div><BarChart3 size={19} aria-hidden="true" /><div><span>Progress</span><strong>{percent}%</strong></div></div>
          </div>
          {ksbs.length > 0 && <p className={styles.weekKsbs}>KSBs <span>{ksbs.join(', ')}</span></p>}
          <div className={styles.weekTrack} role="progressbar" aria-label={`${week.label} progress`} aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${percent}%` }} /></div>
          <div className={styles.weekAction}><ActionIcon size={18} aria-hidden="true" /><span>{complete ? 'Review week' : highlighted || done ? 'Continue week' : 'View week'}</span><ArrowRight size={17} aria-hidden="true" /></div>
        </button>
        {/* Outside the card's button: a hint is something to read, not part of
            the label that opens the week. */}
        <HolidayNoteHint note={week.weekId ? holidayNotes?.get(week.weekId) : undefined} className="mt-2" />
      </li>;
    })}
    </ol>
  </div>;
}
