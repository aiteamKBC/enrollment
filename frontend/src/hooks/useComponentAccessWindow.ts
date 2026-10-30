import { useEffect, useRef, useState } from 'react';
import { readLearnerJson } from '@/api/learnerRead';
import { componentAccessWindow, type ComponentAccessWindow, type WorkingHoursHoliday } from '@/lib/componentAccessWindow';

/**
 * The working-rules calendar, re-checked at least once a minute so 07:00/19:00
 * takes effect without a refresh.
 *
 * `componentId` (or `quizId`, for a quiz, which reaches its cohort through its
 * week rather than a module) scopes the holidays to that activity's own cohort,
 * so a closure authored against another programme never reaches this learner.
 * Without either, the unscoped college calendar is returned, which is what the
 * calendar-only callers want.
 *
 * Nothing here restricts learning. It feeds the completion dialog and the
 * informational notice only.
 */
export function useComponentAccessWindow(
  componentId?: string | null,
  quizId?: string | number | null,
): ComponentAccessWindow {
  const [access, setAccess] = useState(() => componentAccessWindow());
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const holidays = useRef<WorkingHoursHoliday[]>([]);
  const [holidayList, setHolidayList] = useState<WorkingHoursHoliday[]>([]);
  const scope = (componentId || '').trim();
  const quizScope = String(quizId ?? '').trim();

  useEffect(() => {
    let active = true;
    let pending = false;
    const url = scope
      ? `/learner_api/working-hours/holidays/?componentId=${encodeURIComponent(scope)}`
      : quizScope
        ? `/learner_api/working-hours/holidays/?quizId=${encodeURIComponent(quizScope)}`
        : '/learner_api/working-hours/holidays/';
    const update = () => {
      setAccess(componentAccessWindow(new Date(), holidays.current));
      if (pending) return;
      pending = true;
      readLearnerJson<{ holidays: WorkingHoursHoliday[] }>(url, { ttlMs: 30_000 })
        .then(data => {
          if (!Array.isArray(data.holidays) || data.holidays.some(row => !row || !/^\d{4}-\d{2}-\d{2}$/.test(row.start) || !/^\d{4}-\d{2}-\d{2}$/.test(row.end) || row.end < row.start)) {
            throw new Error('Invalid holiday calendar response.');
          }
          if (!active) return;
          holidays.current = data.holidays;
          setHolidayList(data.holidays);
          setAccess(componentAccessWindow(new Date(), holidays.current));
          setReady(true);
          setError('');
        })
        .catch(() => {
          if (!active) return;
          setReady(false);
          setError('Could not load the holiday calendar. Retry before submitting your activity.');
        })
        .finally(() => { pending = false; });
    };
    update();
    const timer = window.setInterval(update, 30_000);
    window.addEventListener('focus', update);
    document.addEventListener('visibilitychange', update);
    return () => {
      active = false;
      window.clearInterval(timer);
      window.removeEventListener('focus', update);
      document.removeEventListener('visibilitychange', update);
    };
  }, [reload, scope, quizScope]);

  return {
    ...access,
    holidays: holidayList,
    holidayCalendarReady: ready,
    holidayError: error,
    refreshHolidays: () => setReload(value => value + 1),
  };
}
