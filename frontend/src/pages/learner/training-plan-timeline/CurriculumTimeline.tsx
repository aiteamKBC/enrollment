import { CalendarDays, CalendarOff, Video } from 'lucide-react';
import type { PlanCurriculumSlot, PlanSession } from '@/api/trainingPlanDashboard';
import { buildCurriculumTimeline, groupCurriculumTimeline } from './model';
import { dateLabel, monthLabel, sessionTime } from "./presentation.helpers";
import layout from './CurriculumTimeline.module.css';

/**
 * The learner's view of the module curriculum, in the curriculum's own order.
 *
 * Built entirely from the scheduler's slot spine, so this is the same timeline
 * the Curriculum Module Builder shows: every slot is a taught session on the
 * date it is actually delivered, and a slot a cohort holiday falls on states
 * the holiday behind it — its name, its date, its type and any note the
 * GOV.UK feed carried — as a heads-up alongside the session, not a closure. A
 * learner who only ever saw session dates had no way to tell a holiday from a
 * mistake, and the session itself is not cancelled by it.
 */
export function CurriculumTimeline({
  slots,
  sessions,
  title = 'Module schedule',
  limit,
}: {
  slots: PlanCurriculumSlot[] | undefined;
  sessions: PlanSession[];
  title?: string;
  /** Show at most this many rows, with a count of the rest. Omit to show all. */
  limit?: number;
}) {
  const rows = buildCurriculumTimeline(slots, sessions);
  if (!rows.length) return null;
  const shown = limit && rows.length > limit ? rows.slice(0, limit) : rows;
  const hidden = rows.length - shown.length;
  // Kept defensive for a payload from an older, genuinely closing scheduler;
  // today's spine never emits a 'reading-week' row.
  const closed = rows.filter(row => row.kind === 'reading-week').length;
  const flagged = rows.filter(row => row.kind === 'session' && row.holidays.length).length;
  const taught = rows.length - closed;

  return <div className={layout.root} data-testid="curriculum-timeline">
    <h4 className={layout.heading}>
      {title}
      <span>
        {taught} {taught === 1 ? 'session' : 'sessions'}
        {closed ? ` · ${closed} reading ${closed === 1 ? 'week' : 'weeks'}` : ''}
        {flagged ? ` · ${flagged} on a holiday` : ''}
      </span>
    </h4>
    {groupCurriculumTimeline(shown).map(group => <section key={group.key} className={layout.month}>
      <h5 className={layout.monthLabel}>{monthLabel(group.key)}</h5>
      <ol className={layout.rows}>
        {group.rows.map(row => row.kind === 'reading-week'
          ? <li key={`slot-${row.slotNumber}`} className={layout.readingWeek} data-testid="learner-reading-week">
            <div className={layout.readingHeader}>Reading Week</div>
            <CalendarOff size={15} aria-hidden="true" />
            <div>
              <p className={layout.rowTitle}>
                <span className={layout.weekNumber}>Week {row.slotNumber}</span>
                <span className={layout.rowDate}>{dateLabel(row.date)}</span>
              </p>
              {row.holidays.map((holiday, index) => <p key={`${holiday.id || holiday.label}-${index}`} className={layout.holiday}>
                <strong>Holiday:</strong> {holiday.label || 'Holiday'}
                {' · '}{dateLabel(holiday.startDate)}
                {holiday.endDate && holiday.endDate !== holiday.startDate ? ` – ${dateLabel(holiday.endDate)}` : ''}
                {holiday.type ? ` · ${holiday.type}` : ''}
                {holiday.notes ? ` · ${holiday.notes}` : ''}
              </p>)}
              {!row.holidays.length && <p className={layout.holiday}><strong>Holiday</strong> closure on this delivery day.</p>}
              <p className={layout.noSession}>No live session scheduled</p>
            </div>
          </li>
          : <li key={`slot-${row.slotNumber}`} className={row.holidays.length ? layout.readingWeek : layout.session} data-testid={row.holidays.length ? 'learner-holiday-session' : undefined}>
            {row.start ? <Video size={15} aria-hidden="true" /> : <CalendarDays size={15} aria-hidden="true" />}
            <div>
              <p className={layout.rowTitle}>
                <span className={layout.weekNumber}>Week {row.slotNumber}</span>
                <strong>{row.weekTitle || 'Week title to be confirmed'}</strong>
                <span className={layout.detailInline}>
                  {row.start ? sessionTime(row.start) : `${dateLabel(row.date)} · Time to be confirmed`}
                  {row.minutes ? ` · ${row.minutes} min` : ''}
                  {row.attended === true ? ' · Attended' : row.attended === false ? ' · Not attended' : ''}
                </span>
              </p>
              {/* States the clash and stops there: the session still runs, so
                  this is a heads-up next to it, not a closure notice. */}
              {row.holidays.map((holiday, index) => <p key={`${holiday.id || holiday.label}-${index}`} className={layout.holiday}>
                <strong>Heads up, holiday:</strong> {holiday.label || 'Holiday'}
                {' · '}{dateLabel(holiday.startDate)}
                {holiday.endDate && holiday.endDate !== holiday.startDate ? ` – ${dateLabel(holiday.endDate)}` : ''}
                {holiday.type ? ` · ${holiday.type}` : ''}
                {holiday.notes ? ` · ${holiday.notes}` : ''}
              </p>)}
              {/* Only this week's own note, and only one the curriculum team
                  published: the server sends nothing for a week whose hint is
                  off, so there is no hidden text here to leak. */}
              {row.holidays.length > 0 && row.holidayNote
                && <p className={layout.holidayNote} data-testid="learner-holiday-note">
                  <strong>From your curriculum team:</strong> {row.holidayNote}
                </p>}
            </div>
          </li>)}
      </ol>
    </section>)}
    {hidden > 0 && <p className={layout.more}>{hidden} more {hidden === 1 ? 'week' : 'weeks'} in this module.</p>}
  </div>;
}
