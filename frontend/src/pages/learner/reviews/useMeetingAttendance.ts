import { useCallback, useEffect, useRef, useState } from 'react';
import type { LearnerKind } from '@/api/learnerDetail';
import { confirmMeetingAttendance, fetchMeetingAttendance, type MeetingAttendanceResponse } from '@/api/meetingAttendance';
import { useLiveRefresh } from '@/hooks/useRefreshOnReturn';
import { useLearnerWorkspaceAccess } from '@/hooks/useLearnerWorkspaceAccess';

export function useMeetingAttendance(learner: { kind: LearnerKind; id: string }) {
  const [data, setData] = useState<MeetingAttendanceResponse | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const saving = useRef(false);
  const refresh = useCallback(() => setRevision(value => value + 1), []);
  const access = useLearnerWorkspaceAccess(learner.id);
  useLiveRefresh(refresh);
  useEffect(() => {
    let current = true;
    void fetchMeetingAttendance(learner.kind, learner.id).then(value => {
      if (current) { setData(value); setError(''); }
    }).catch(reason => { if (current) setError(reason instanceof Error ? reason.message : 'Could not load attendance.'); });
    return () => { current = false; };
  }, [learner.kind, learner.id, revision]);
  const attend = async (id: string) => {
    if (saving.current || !access.canProgress || !data?.sessions.find(session => session.id === id)?.canAttend) return;
    saving.current = true; setBusy(id); setError(''); setNotice('');
    try {
      const result = await confirmMeetingAttendance(learner.kind, learner.id, id, data.csrfToken);
      setData(current => current && ({ ...current, sessions: current.sessions.map(session => session.id === id
        ? { ...session, canAttend: false, attendanceConfirmed: true, creditedMinutes: result.creditedMinutes, canReportAbsence: false } : session) }));
      setNotice(`Attendance recorded. ${result.creditedMinutes} minutes credited for the full meeting.`);
      refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not record attendance.'); }
    finally { saving.current = false; setBusy(null); }
  };
  return { data, error, notice, busy, attend, refresh, canAct: access.canProgress };
}
