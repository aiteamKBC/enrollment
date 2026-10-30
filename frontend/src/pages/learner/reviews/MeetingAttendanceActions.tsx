import type { MeetingAttendance } from '@/api/meetingAttendance';
import { AppIcon } from '@/components/feature/AppIcon';
import styles from './featuredMeeting.module.css';

export default function MeetingAttendanceActions({ session, busy, canAct, onAttend, onReport, onReschedule, featured = false }: {
  session?: MeetingAttendance; busy: boolean; canAct: boolean; featured?: boolean;
  onAttend: () => void; onReport: () => void; onReschedule: () => void;
}) {
  if (!session || !session.date) return null;
  return <div className={styles.actions}>
    {session.attendanceConfirmed ? <span className={styles.confirmed}><AppIcon className="ri-checkbox-circle-line" />Attended · {session.creditedMinutes} min</span>
      : session.absenceReported ? <span className={styles.reported}>Absence reported</span>
        : <>
          {featured && !session.missed && <button type="button" className={styles.schedule} disabled={!canAct || busy || !session.canAttend} onClick={onAttend}><AppIcon className="ri-checkbox-circle-line" />{busy ? 'Saving…' : 'Attend'}</button>}
          {session.canReportAbsence && <button type="button" className={styles.report} disabled={!canAct || busy} onClick={onReport}><AppIcon className="ri-calendar-close-line" />Report Absence</button>}
        </>}
    {session.missed && !session.attendanceConfirmed && <button type="button" className={styles.schedule} disabled={!canAct || busy} onClick={onReschedule}><AppIcon className="ri-calendar-event-line" />Reschedule meeting</button>}
  </div>;
}
