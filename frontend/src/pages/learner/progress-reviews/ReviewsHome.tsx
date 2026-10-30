import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { ArrowLeft, ArrowRight, CalendarDays, ChevronDown, FileText, List, Video } from 'lucide-react';
import type { LearnerCalendarEvent } from '@/api/learnerCalendar';
import type { MeetingAttendance } from '@/api/meetingAttendance';
import MeetingAttendanceActions from '../reviews/MeetingAttendanceActions';
import { meetingBookingWarning, meetingCalendarHref } from '../reviews/meetingBooking';
import ProgrammeReviewTimeline from '../reviews/ProgrammeReviewTimeline';
import { completedReviewTimelineStatus, reviewTimelineStatus } from '../reviews/reviewTimelineStatus';
import { isAptemReview, reviewOverview, reviewsListHref, type ReviewDefinitions, type ReviewFilter, type ReviewPresentation } from './reviewPresentation';
import styles from './reviewsHome.module.css';

export interface ReviewsHomeProps {
  sessions: LearnerCalendarEvent[];
  attendance: MeetingAttendance[];
  definitions?: ReviewDefinitions;
  learner: { kind: string; id: string };
  lineManager?: string | null;
  today: string;
  timeZone?: string;
  loading: boolean;
  error: string;
  busy: boolean;
  canAct: boolean;
  titleOf: (session: LearnerCalendarEvent) => string;
  onSchedule: (session: LearnerCalendarEvent) => void;
  onAttend: (id: string) => void;
  onReport: (session: MeetingAttendance) => void;
  basePath?: string;
  viewerRole?: 'participant' | 'employer';
  onOpenReview?: (session: LearnerCalendarEvent) => void;
}

const filters: { id: ReviewFilter; label: string }[] = [{ id: 'all', label: 'All' }, { id: 'upcoming', label: 'Upcoming' }, { id: 'past', label: 'Past' }];
const dateLabel = (value: string | null) => {
  if (!value) return 'Date to be confirmed';
  const date = new Date(`${value}T12:00:00Z`);
  return Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' }).format(date) : 'Date to be confirmed';
};

const progressTimelineStatus = (state: ReviewPresentation, definitions?: ReviewDefinitions) => {
  if (state.session.source !== 'progress-review') return reviewTimelineStatus(state.session, state.attendance);
  const definition = definitions?.[state.session.eventKey] || definitions?.[state.session.id];
  return completedReviewTimelineStatus(state.session, state.attendance, definition?.instance?.status);
};

export default function ReviewsHome(props: ReviewsHomeProps) {
  const { sessions, attendance, definitions, learner, today, titleOf } = props;
  const employer = props.viewerRole === 'employer';
  const overview = useMemo(() => reviewOverview(sessions, attendance, today, definitions, props.viewerRole), [sessions, attendance, today, definitions, props.viewerRole]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [routeParams, setRouteParams] = useSearchParams();
  const [localParams, setLocalParams] = useState(() => new URLSearchParams());
  const params = props.onOpenReview ? localParams : routeParams;
  const setParams = (next: URLSearchParams) => props.onOpenReview ? setLocalParams(next) : setRouteParams(next);
  const allView = params.get('view') === 'all';
  const filter = filters.find(item => item.id === params.get('filter'))?.id || 'all';
  const rows = overview[filter];
  const pages = Math.max(1, Math.ceil(rows.length / 8));
  const page = Math.min(pages, Math.max(1, Number.parseInt(params.get('page') || '1', 10) || 1));
  const heading = useRef<HTMLHeadingElement>(null);
  const previousView = useRef(allView);
  useEffect(() => {
    if (previousView.current !== allView) heading.current?.focus();
    previousView.current = allView;
  }, [allView]);
  const basePath = props.basePath || '/learner/progress-reviews';
  const viewHref = (all: boolean) => reviewsListHref(learner, new URLSearchParams(all ? 'view=all' : ''), basePath);
  const detailHref = (state: ReviewPresentation) => {
    const next = new URLSearchParams(reviewsListHref(learner, params, basePath).split('?')[1]);
    if (allView) { next.set('filter', filter); next.set('page', String(page)); }
    return `${basePath}/${encodeURIComponent(state.session.id)}?${next}`;
  };
  const updateView = (selected: ReviewFilter, selectedPage = 1) => {
    const next = new URLSearchParams({ kind: learner.kind, learner: learner.id, view: 'all', filter: selected });
    if (selectedPage > 1) next.set('page', String(selectedPage));
    setParams(next);
  };
  const selectedReview = overview.all.find(item => item.session.id === selectedId);
  const nextActionableReview = overview.nextActionableReview;
  // Selection changes the displayed card only, never the calculated next action.
  // overview.current also retains the existing Native historical fallback.
  const current = selectedReview || nextActionableReview || overview.current;
  const isSelected = Boolean(selectedReview && selectedReview.session.id !== overview.current?.session.id);
  const selectTimelineItem = (id: string) => {
    setSelectedId(id);
    if (allView) setParams(new URLSearchParams({ kind: learner.kind, learner: learner.id }));
  };
  const reminders = overview.attention.filter(item => item.session.id !== overview.current?.session.id);
  const attentionId = useId();

  function listLink(all: boolean, children: ReactNode, className: string) {
    return props.onOpenReview
      ? <button type="button" className={className} onClick={() => { setSelectedId(null); setParams(new URLSearchParams(all ? 'view=all' : '')); }}>{children}</button>
      : <Link className={className} to={viewHref(all)} onClick={() => { if (!all) setSelectedId(null); }}>{children}</Link>;
  }
  function reviewLink(state: ReviewPresentation, children: ReactNode, className?: string) {
    return props.onOpenReview
      ? <button type="button" className={className} onClick={() => props.onOpenReview?.(state.session)}>{children}</button>
      : <Link className={className} to={detailHref(state)}>{children}</Link>;
  }

  function status(state: ReviewPresentation) {
    return <span className={styles.badge} data-tone={state.needsAttention ? 'attention' : state.status === 'completed' ? 'success' : 'neutral'}>{state.label}</span>;
  }
  function action(state: ReviewPresentation, secondary = false) {
    const className = secondary ? styles.secondaryButton : styles.primaryButton;
    if (state.action === 'schedule') return <><button type="button" className={className} disabled={!props.canAct || props.busy} onClick={() => props.onSchedule(state.session)}><CalendarDays size={19} aria-hidden="true"/>Book a time</button>{reviewLink(state, <><FileText size={19} aria-hidden="true"/>View review</>, styles.secondaryButton)}</>;
    return <>
      {state.action === 'join' && state.joinUrl
        ? <a className={className} href={state.joinUrl} target="_blank" rel="noopener noreferrer"><Video size={19} aria-hidden="true"/>Join meeting</a>
        : reviewLink(state, <><FileText size={19} aria-hidden="true"/>{state.action === 'sign' && props.canAct ? 'Read & sign' : 'View review'}</>, className)}
      {employer && state.joinUrl && !state.past && (state.action === 'join'
        ? reviewLink(state, <><FileText size={19} aria-hidden="true"/>View review</>, styles.secondaryButton)
        : <a className={styles.secondaryButton} href={state.joinUrl} target="_blank" rel="noopener noreferrer"><Video size={19} aria-hidden="true"/>Join meeting</a>)}
      {!employer && state.canReschedule && (!state.attendance?.missed || state.session.importedReview) && <button type="button" className={styles.secondaryButton} disabled={!props.canAct || props.busy} onClick={() => props.onSchedule(state.session)}><CalendarDays size={19} aria-hidden="true"/>Reschedule meeting</button>}
    </>;
  }
  function canUseAttendanceActions(state: ReviewPresentation) {
    return !state.session.importedReview && !['completed', 'cancelled', 'awaiting-signature'].includes(state.status);
  }
  function options(state: ReviewPresentation) {
    if (employer) return null;
    const hasCalendarBooking = Boolean(state.session.calendarEventKey || state.attendance?.calendarEventKey);
    if (state.session.importedReview && !hasCalendarBooking) return null;
    const showAttendanceOptions = canUseAttendanceActions(state) && state.attendance && !state.attendance.canAttend && !state.attendance.absenceReported
      && !state.attendance.attendanceConfirmed && (state.attendance.canReportAbsence || state.attendance.missed);
    return <details className={styles.options}><summary>More options<ChevronDown size={18} aria-hidden="true"/></summary><div className={styles.optionItems}>
      {state.action === 'join' ? <Link to={detailHref(state)}>View review details</Link> : null}
      {state.joinUrl && state.action !== 'join' && <a href={state.joinUrl} target="_blank" rel="noopener noreferrer">Open meeting link</a>}
      {(!state.session.importedReview || hasCalendarBooking) && <Link to={meetingCalendarHref(state.session, learner, undefined, state.attendance)}>View in calendar</Link>}
      {showAttendanceOptions && <MeetingAttendanceActions session={state.attendance} busy={props.busy} canAct={props.canAct}
        onAttend={() => props.onAttend(state.attendance!.id)} onReport={() => props.onReport(state.attendance!)} onReschedule={() => props.onSchedule(state.session)}/>}
    </div></details>;
  }
  function attendanceActions(state: ReviewPresentation) {
    if (employer) return null;
    if (!canUseAttendanceActions(state)) return null;
    if (!state.attendance?.canAttend && !state.attendance?.attendanceConfirmed && !state.attendance?.absenceReported) return null;
    return <div className={styles.attendance}>
      {state.attendance.canAttend && !state.attendance.attendanceConfirmed && !state.attendance.absenceReported && <p>Attended this meeting? Record your attendance separately from joining the call.</p>}
      <MeetingAttendanceActions session={state.attendance} featured={state.attendance.canAttend} busy={props.busy} canAct={props.canAct}
        onAttend={() => props.onAttend(state.attendance!.id)} onReport={() => props.onReport(state.attendance!)} onReschedule={() => props.onSchedule(state.session)}/>
    </div>;
  }
  function warning(state: ReviewPresentation) {
    const message = meetingBookingWarning(state.session, state.attendance?.syncWarning);
    return message ? <p className={styles.notice} role="status">{message}</p> : null;
  }

  return <section className={styles.home} aria-label="Reviews sessions">
    <header className={styles.header}><div><h1 ref={heading} tabIndex={-1}>{allView ? 'All reviews' : employer ? 'Progress reviews' : 'My reviews'}</h1><p className={styles.intro}>{allView ? 'Find a review, check its status or read a previous record.' : employer ? 'This learner’s reviews and anything that needs your employer signature.' : 'Your next review and anything that needs your attention.'}</p></div>
      <div className={styles.headerLinks}>{allView ? listLink(false, <><ArrowLeft size={19} aria-hidden="true"/>Back to current review</>, styles.secondaryButton) : listLink(true, <><List size={19} aria-hidden="true"/>View all reviews{sessions.length || (!props.loading && !props.error) ? ` (${sessions.length})` : ''}</>, styles.secondaryButton)}</div>
    </header>
    {sessions.length > 0 && <ProgrammeReviewTimeline label={basePath === '/learner/reviews' ? 'Review' : 'Progress review'} activeId={current?.session.id || null} onSelect={selectTimelineItem} items={overview.all.map(state => ({
      id: state.session.id, title: titleOf(state.session), date: state.date,
      status: progressTimelineStatus(state, definitions),
      markerSelection: isAptemReview(state.session),
    }))} />}
    {props.loading && !sessions.length ? <div className={styles.loading} role="status">Loading your reviews…</div>
      : props.error && !sessions.length ? <div className={styles.empty}><h2>Your reviews could not be loaded</h2><p>Use Try again above to reload your reviews.</p></div>
      : <>
        {props.loading && <p role="status" className={styles.refreshStatus}>Updating your reviews…</p>}
        {allView ? <section className={styles.archive} aria-label="All reviews">
          <div className={styles.filters} role="group" aria-label="Filter reviews">{filters.map(item => <button type="button" key={item.id} aria-pressed={filter === item.id} onClick={() => updateView(item.id)}>{item.label} ({overview[item.id].length})</button>)}</div>
          {!rows.length ? <div className={styles.empty}><h2>{filter === 'past' ? 'No past reviews yet' : filter === 'upcoming' ? 'No upcoming reviews' : 'No reviews yet'}</h2><p>Your reviews will appear here when they are available.</p></div> : <ul className={styles.reviewList}>
            {rows.slice((page - 1) * 8, page * 8).map(state => <li key={state.session.id} className={styles.reviewRow}>
              <div className={styles.rowIdentity}><h2>{reviewLink(state, titleOf(state.session), props.onOpenReview ? styles.textLink : undefined)}</h2><p>{state.session.reviewSource === 'aptem' && <span>{state.status === 'completed' ? 'Historical Reviewer / Coach' : 'Responsible Coach'}: </span>}<span>{state.session.coachName || 'Reviewer to be confirmed'}</span></p><p>{state.description}</p></div>
              <div className={styles.rowDate}>{status(state)}<p>{state.action === 'schedule' ? 'Target date' : state.booked ? 'Meeting date' : 'Review date'}</p><strong>{dateLabel(state.date)}</strong>{state.booked && <p>{state.attendance?.startTime || state.session.scheduledTime || 'Time to be confirmed'}{props.timeZone ? ` · ${props.timeZone}` : ''}</p>}</div>
              <div className={styles.rowActions}>{action(state, true)}{options(state)}{attendanceActions(state)}</div>{warning(state)}
            </li>)}
          </ul>}
          {pages > 1 && <nav className={styles.pagination} aria-label="Review pages"><span>Page {page} of {pages}</span><div><button type="button" disabled={page === 1} onClick={() => updateView(filter, page - 1)}><ArrowLeft size={17} aria-hidden="true"/>Previous</button><button type="button" disabled={page === pages} onClick={() => updateView(filter, page + 1)}>Next<ArrowRight size={17} aria-hidden="true"/></button></div></nav>}
        </section> : <>
          {reminders.length > 0 && <p className={styles.refreshStatus}><a className={styles.textLink} href={`#${attentionId}`}>{reminders.length} {reminders.length === 1 ? 'review needs' : 'reviews need'} your attention<ArrowRight size={17} aria-hidden="true"/></a></p>}
          {current ? <article className={styles.current} aria-label={isSelected ? 'Selected review' : 'Current review'}>
            <div className={styles.currentTop}><p className={styles.eyebrow}>{isSelected ? 'Selected review' : current.isToday ? "Today's review" : current.action === 'sign' || current.past ? 'Your next step' : 'Your next review'}</p>{status(current)}</div>
            <h2 className={styles.title}>{titleOf(current.session)}</h2>
            <div className={styles.appointment}><span className={styles.appointmentIcon}><CalendarDays size={28} aria-hidden="true"/></span><div><p className={styles.dateLabel}>{dateLabel(current.date)}</p><p className={styles.timeLabel}>{current.action === 'schedule' ? 'Target date — a meeting time has not been booked' : current.booked ? `${current.attendance?.startTime || current.session.scheduledTime || 'Time to be confirmed'}${props.timeZone ? ` · ${props.timeZone}` : ''}` : 'Review record'}</p></div></div>
            <div className={styles.meta}><div><small>{current.session.reviewSource === 'aptem' ? current.status === 'completed' ? 'Historical Reviewer / Coach' : 'Responsible Coach' : 'Reviewer / Coach'}</small><strong>{current.session.coachName || 'Not assigned yet'}</strong></div>{props.lineManager && <div><small>Line manager</small><strong>{props.lineManager}</strong></div>}{current.booked && <div><small>Meeting</small><strong>{current.attendance?.meetingProvider || current.session.meetingProvider || 'Location to be confirmed'}{(current.attendance?.durationMinutes ?? current.session.durationMinutes) > 0 ? ` · ${current.attendance?.durationMinutes ?? current.session.durationMinutes} minutes` : ''}</strong></div>}</div>
            <p className={styles.help}>{current.description}</p>{warning(current)}
            <div className={styles.actions}>{action(current)}{options(current)}</div>
            {attendanceActions(current)}
          </article> : <article className={styles.empty} aria-label="Current review"><h2>No current review</h2><p>Your next review will appear here when it is planned. You can still open your previous records.</p>{listLink(true, <>View all reviews<ArrowRight size={18} aria-hidden="true"/></>, styles.secondaryButton)}</article>}
          {reminders.length > 0 && <section id={attentionId} className={styles.attention} aria-label="Reviews needing attention"><h2 className={styles.attentionHeading}>Also needs your attention ({reminders.length})</h2>{reminders.map(state => <article key={state.session.id} className={styles.attentionItem}><div><h3>{titleOf(state.session)}</h3><p>{state.label} · {dateLabel(state.date)}</p><p>{state.description}</p></div>{action(state, true)}</article>)}</section>}
        </>}
        {!employer && <footer className={styles.footer}><Link className={styles.textLink} to={`/learner/calendar?${new URLSearchParams({ kind: learner.kind, learner: learner.id })}`}><CalendarDays size={18} aria-hidden="true"/>Open calendar</Link></footer>}
      </>}
  </section>;
}
