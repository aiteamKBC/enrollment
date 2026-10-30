import { SYSTEM_TIME_ZONE } from './format';

/** One closed date range. `label` names the holiday when the scoped
 *  (componentId) endpoint answered; the unscoped answer carries no names. */
export interface WorkingHoursHoliday { start: string; end: string; label?: string }

export interface ComponentAccessWindow {
  open: boolean;
  currentTimeLabel: string;
  closedReason: 'outside-hours' | 'weekend' | 'invalid' | null;
  outsideWorkingHours: boolean;
  outsideReason: 'outside-hours' | 'weekend' | 'holiday' | null;
  /** The learner's own closed dates, when a scoped read supplied them. */
  holidays?: WorkingHoursHoliday[];
  holidayCalendarReady?: boolean;
  holidayError?: string;
  refreshHolidays?: () => void;
}

/** Learning content stays open 24/7, but UK out-of-hours work is identified so
 * the learner can explicitly confirm the manually reported time. */
export function componentAccessWindow(at: Date | number = new Date(), holidays: WorkingHoursHoliday[] = []): ComponentAccessWindow {
  const date = at instanceof Date ? at : new Date(at);
  if (Number.isNaN(date.getTime())) return {
    open: false,
    currentTimeLabel: '',
    closedReason: 'invalid',
    outsideWorkingHours: false,
    outsideReason: null,
  };

  const formatter = new Intl.DateTimeFormat('en-GB', {
    timeZone: SYSTEM_TIME_ZONE,
    weekday: 'long',
    hourCycle: 'h23',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZoneName: 'short',
  });
  const currentTimeLabel = formatter.format(date);
  const parts = Object.fromEntries(formatter.formatToParts(date).map(part => [part.type, part.value]));
  const hour = Number(parts.hour);
  const weekend = parts.weekday === 'Saturday' || parts.weekday === 'Sunday';
  const dayParts = Object.fromEntries(new Intl.DateTimeFormat('en-GB', {
    timeZone: SYSTEM_TIME_ZONE, year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(date).map(part => [part.type, part.value]));
  const day = `${dayParts.year}-${dayParts.month}-${dayParts.day}`;
  const holiday = holidays.some(range => range.start <= day && day <= range.end);
  const outsideReason = holiday ? 'holiday' : weekend
    ? 'weekend'
    : hour < 7 || hour >= 19
      ? 'outside-hours'
      : null;

  return {
    open: true,
    currentTimeLabel,
    closedReason: null,
    outsideWorkingHours: outsideReason !== null,
    outsideReason,
  };
}

export const COMPONENT_ACCESS_MESSAGE =
  'Learning components are available at any time.';
