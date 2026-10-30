import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import {
  fetchAbsenceReports,
  submitAbsenceReport,
  type LearnerAbsenceReport,
  type MissedAttendanceSession,
} from '@/api/absenceReports';
import { useMyLearner } from '@/hooks/useMyLearner';
import { Panel } from '@/components/ui/Panel';
import { SectionHeader } from '@/components/ui/SectionHeader';
import { EmptyState } from '@/components/ui/EmptyState';
import styles from '../attendance.module.css';
import CatchupBooking, { type CatchupBookAction } from './CatchupBooking';
import type { LearnerCalendarEvent } from '@/api/learnerCalendar';

const REASONS = [
  { value: 'illness', label: 'Illness or medical appointment', icon: 'ri-heart-pulse-line' },
  { value: 'emergency', label: 'Family or personal emergency', icon: 'ri-alarm-warning-line' },
  { value: 'travel', label: 'Travel disruption', icon: 'ri-bus-line' },
  { value: 'technical', label: 'Technical issue', icon: 'ri-wifi-off-line' },
  { value: 'other', label: 'Other reason', icon: 'ri-more-line' },
];

const statusClass: Record<string, string> = {
  approved: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  'catch-up complete': 'bg-primary-50 text-primary-700 border-primary-200',
  pending: 'bg-amber-50 text-amber-700 border-amber-200',
  declined: 'bg-red-50 text-red-700 border-red-200',
};

function displayDate(value: string) {
  return new Date(`${value}T00:00:00`).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
}

interface EvidencePreview {
  title: string;
  objectUrl: string;
  isPdf: boolean;
}

export interface AbsenceReportFormProps {
  scope?: 'meetings';
  /** Opens a lecture report on the recovery choice that launched the form. */
  initialRecoveryMethod?: 'catch-up';
  /** Preselect the missed session that matches this date + title, once loaded. */
  preselectMatch?: { id?: string; dateIso: string; title: string } | null;
  /** Called once a report has been saved, in addition to the inline confirmation. */
  onSubmitted?: (report: LearnerAbsenceReport) => void;
  onCancel?: () => void;
  /** Renders the supporting "What happens next" + safeguarding callout. Off by default for compact (drawer) use. */
  showGuidance?: boolean;
  /** Renders the "Previous reports" list. Defaults to true. */
  showHistory?: boolean;
  /** Compact layout for the Attendance page dialog. */
  compact?: boolean;
}

export default function AbsenceReportForm({
  initialRecoveryMethod,
  preselectMatch = null,
  onSubmitted,
  onCancel,
  showGuidance = false,
  showHistory = true,
  compact = false,
  scope,
}: AbsenceReportFormProps) {
  const myLearner = useMyLearner();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [sessionId, setSessionId] = useState('');
  const [reasonType, setReasonType] = useState('');
  const [otherReason, setOtherReason] = useState('');
  const [explanation, setExplanation] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [selectedFilePreviewUrl, setSelectedFilePreviewUrl] = useState('');
  const [fileError, setFileError] = useState('');
  const [dragging, setDragging] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [submittedReport, setSubmittedReport] = useState<LearnerAbsenceReport | null>(null);
  const [reports, setReports] = useState<LearnerAbsenceReport[]>([]);
  const [missedSessions, setMissedSessions] = useState<MissedAttendanceSession[]>([]);
  const [reportsLoading, setReportsLoading] = useState(true);
  const [requestError, setRequestError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [evidencePreview, setEvidencePreview] = useState<EvidencePreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const [didPreselect, setDidPreselect] = useState(false);
  const [recoveryMethod, setRecoveryMethod] = useState<'' | 'recorded' | 'catch-up' | 'alternative'>(initialRecoveryMethod || '');
  const [targetOccurrenceId, setTargetOccurrenceId] = useState('');
  const [recordingDate, setRecordingDate] = useState('');
  const [recordingTime, setRecordingTime] = useState('');
  const [catchupBooking, setCatchupBooking] = useState<{ sessionId: string; event: LearnerCalendarEvent } | null>(null);
  const [bookingBusy, setBookingBusy] = useState(false);
  // The catch-up time is booked by the submit button itself ("Book catch-up & submit").
  const bookCatchup = useRef<CatchupBookAction | null>(null);
  const [catchupDraftReady, setCatchupDraftReady] = useState(false);
  const [catchupLinkError, setCatchupLinkError] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const selectedBooking = catchupBooking?.sessionId === sessionId ? catchupBooking.event : null;
  const recoveryApproved = Boolean(submittedReport?.recoveryMethod)
    && submittedReport?.status.toLowerCase() === 'approved';
  const selectBooking = useCallback((event: LearnerCalendarEvent | null) => {
    setCatchupBooking(event ? { sessionId, event } : null);
  }, [sessionId]);

  const selectedSession = useMemo(
    () => missedSessions.find((session) => session.id === sessionId),
    [missedSessions, sessionId],
  );
  const availableSessions = useMemo(() => {
    const reportedSessions = new Set(
      reports
        .map((report) => report.attendanceId || `${report.sessionDate}|${report.sessionTitle.trim().toLowerCase()}`),
    );
    return missedSessions.filter(
      (session) => !reportedSessions.has(session.reportId || `${session.dateIso}|${session.title.trim().toLowerCase()}`),
    );
  }, [missedSessions, reports]);
  const hasReason = Boolean(reasonType && (reasonType !== 'other' || otherReason.trim()));
  const canUploadEvidence = Boolean(sessionId && hasReason);
  const canSubmit = Boolean(sessionId && hasReason && confirmed && !bookingBusy
    && (scope === 'meetings' || (recoveryMethod === 'recorded' && recordingDate && recordingTime)
      || (recoveryMethod === 'catch-up' && (selectedBooking || catchupDraftReady))
      || (recoveryMethod === 'alternative' && targetOccurrenceId)));
  const booksOnSubmit = scope !== 'meetings' && recoveryMethod === 'catch-up' && !selectedBooking;
  const resolvedCount = reports.filter((report) => report.status !== 'Pending').length;

  const selectAlternativeRecovery = () => {
    if (!selectedSession) return;
    setRecoveryMethod('alternative');
    setTargetOccurrenceId('');
    setRecordingDate('');
    setRecordingTime('');
    setConfirmed(false);
  };

  const selectRecordedRecovery = () => {
    setRecoveryMethod('recorded');
    setTargetOccurrenceId('');
    setRecordingDate('');
    setRecordingTime('');
    setConfirmed(false);
  };

  useEffect(() => {
    let cancelled = false;
    setReportsLoading(true);
    fetchAbsenceReports(myLearner.kind, myLearner.id, scope)
      .then((data) => {
        if (!cancelled) {
          setReports(data.results);
          setMissedSessions(scope && preselectMatch?.id ? data.missedSessions.filter(session => session.id === preselectMatch.id) : data.missedSessions);
        }
      })
      .catch((error: unknown) => { if (!cancelled) setRequestError(error instanceof Error ? error.message : 'Could not load reports.'); })
      .finally(() => { if (!cancelled) setReportsLoading(false); });
    return () => { cancelled = true; };
  }, [myLearner.kind, myLearner.id, scope, preselectMatch?.id]);

  // Preselect the missed session a caller opened this form for (e.g. from a
  // row in the attendance history) once the real session list has loaded.
  useEffect(() => {
    if (didPreselect || reportsLoading || !preselectMatch) return;
    const match = missedSessions.find(
      (session) => preselectMatch.id ? session.id === preselectMatch.id : session.dateIso === preselectMatch.dateIso
        && session.title.trim().toLowerCase() === preselectMatch.title.trim().toLowerCase(),
    );
    if (match) setSessionId(match.id);
    setDidPreselect(true);
  }, [didPreselect, reportsLoading, preselectMatch, missedSessions]);

  useEffect(() => {
    if (!file) {
      setSelectedFilePreviewUrl('');
      return undefined;
    }
    const objectUrl = URL.createObjectURL(file);
    setSelectedFilePreviewUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [file]);

  const selectFile = (nextFile?: File) => {
    setFileError('');
    if (!nextFile) return;
    const supported = nextFile.type.startsWith('image/') || nextFile.type === 'application/pdf';
    if (!supported) {
      setFileError('Please upload an image or PDF file.');
      return;
    }
    if (nextFile.size > 10 * 1024 * 1024) {
      setFileError('The file must be smaller than 10 MB.');
      return;
    }
    setFile(nextFile);
  };

  const resetForm = () => {
    setSessionId('');
    setReasonType('');
    setOtherReason('');
    setExplanation('');
    setFile(null);
    setFileError('');
    setSubmitted(false);
    setSubmittedReport(null);
    setRequestError('');
    setRecoveryMethod('');
    setTargetOccurrenceId('');
    setRecordingDate('');
    setRecordingTime('');
    setCatchupBooking(null);
    setConfirmed(false);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  // Saves the report. `catchupPending` saves a catch-up report whose new booking
  // is made right after, linked to this report by the server in that same request.
  const saveReport = async (bookingOverride?: LearnerCalendarEvent, catchupPending = false): Promise<LearnerAbsenceReport | null> => {
    const activeBooking = bookingOverride || selectedBooking;
    const ready = Boolean(sessionId && hasReason && confirmed
      && (scope === 'meetings' || (recoveryMethod === 'recorded' && recordingDate && recordingTime)
        || (recoveryMethod === 'catch-up' && (activeBooking || catchupPending))
        || (recoveryMethod === 'alternative' && targetOccurrenceId)));
    if (!ready || !selectedSession || submitting) return null;
    setSubmitting(true);
    setRequestError('');
    const payload = new FormData();
    payload.append('sessionId', selectedSession.sessionId);
    payload.append('sessionTitle', selectedSession.title);
    payload.append('sessionDate', selectedSession.dateIso);
    payload.append('sessionTime', selectedSession.startTime);
    payload.append('reasonCategory', reasonType);
    payload.append('otherReason', otherReason.trim());
    payload.append('explanation', explanation.trim());
    payload.append('recoveryMethod', recoveryMethod);
    if (recoveryMethod === 'catch-up' && activeBooking) payload.append('catchupEventKey', activeBooking.eventKey);
    if (recoveryMethod === 'catch-up' && !activeBooking && catchupPending) payload.append('catchupPending', '1');
    if (recoveryMethod === 'alternative' && targetOccurrenceId) payload.append('targetOccurrenceId', targetOccurrenceId);
    if (recoveryMethod === 'recorded') {
      const start = new Date(`${selectedSession.dateIso}T${selectedSession.startTime || '00:00'}`);
      const end = new Date(`${selectedSession.dateIso}T${selectedSession.endTime || selectedSession.startTime || '00:00'}`);
      const duration = Math.max(15, Math.round((end.getTime() - start.getTime()) / 60000) || 60);
      payload.append('recordingDate', recordingDate);
      payload.append('recordingTime', recordingTime);
      payload.append('recordingDurationMinutes', String(duration));
    }
    if (file) payload.append('evidence', file);
    try {
      return await submitAbsenceReport(myLearner.kind, myLearner.id, payload);
    } catch (error) {
      setRequestError(error instanceof Error ? error.message : 'Could not submit the absence report.');
      return null;
    } finally {
      setSubmitting(false);
    }
  };

  const finishSubmitted = (created: LearnerAbsenceReport) => {
    // A retried submission returns the report that was already saved.
    setReports((current) => [created, ...current.filter((report) => report.id !== created.id)]);
    setSubmittedReport(created);
    setSubmitted(true);
    onSubmitted?.(created);
  };

  const submitReport = async (bookingOverride?: LearnerCalendarEvent): Promise<boolean> => {
    const created = await saveReport(bookingOverride);
    if (created) finishSubmitted(created);
    return Boolean(created);
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    if (booksOnSubmit) {
      // 1) Save the report; 2) book the catch-up linked to it in one server request.
      // A booking that fails leaves the report without a catch-up, never a stray booking.
      const created = await saveReport(undefined, true);
      if (!created) return;
      const booked = await bookCatchup.current?.(created.id);
      if (booked) setCatchupBooking({ sessionId, event: booked });
      else setCatchupLinkError('Your absence report is saved, but the catch-up could not be booked. Use Book Catchup Session on this lecture to choose another time.');
      finishSubmitted(created);
      return;
    }
    await submitReport();
  };

  const closeEvidencePreview = () => {
    if (evidencePreview?.objectUrl.startsWith('blob:')) {
      URL.revokeObjectURL(evidencePreview.objectUrl);
    }
    setEvidencePreview(null);
    setPreviewLoading(false);
    setPreviewError('');
  };

  const openEvidencePreview = (report: LearnerAbsenceReport) => {
    if (!report.evidenceUrl) return;
    if (evidencePreview?.objectUrl.startsWith('blob:')) {
      URL.revokeObjectURL(evidencePreview.objectUrl);
    }
    const isPdf = /\.pdf(?:\?|$)/i.test(report.evidenceUrl);
    setEvidencePreview({
      title: `${report.reference} - ${report.sessionTitle}`,
      objectUrl: report.evidenceUrl,
      isPdf,
    });
    setPreviewLoading(false);
    setPreviewError('');
  };

  const openSelectedFilePreview = () => {
    if (!file) return;
    const objectUrl = URL.createObjectURL(file);
    if (evidencePreview?.objectUrl.startsWith('blob:')) {
      URL.revokeObjectURL(evidencePreview.objectUrl);
    }
    setPreviewError('');
    setPreviewLoading(false);
    setEvidencePreview({
      title: file.name,
      objectUrl,
      isPdf: file.type === 'application/pdf',
    });
  };

  const recoverySchedule = submittedReport?.recoveryMethod === 'recorded' && recordingDate && recordingTime
    ? `${displayDate(recordingDate)} at ${recordingTime}`
    : submittedReport?.recoveryMethod === 'catch-up' && selectedBooking?.scheduledDate && selectedBooking.scheduledTime
      ? `${displayDate(selectedBooking.scheduledDate)} at ${selectedBooking.scheduledTime}`
      : submittedReport?.recoveryMethod === 'alternative' && submittedReport.alternativeSession
        ? `${displayDate(submittedReport.alternativeSession.dateIso)} at ${submittedReport.alternativeSession.startTime}`
        : '';
  const recoveryEventKey = submittedReport?.recoveryMethod === 'recorded'
    ? `absence-recording:${submittedReport.id}`
    : submittedReport?.recoveryMethod === 'alternative'
      ? `absence-alternative:${submittedReport.id}`
      : selectedBooking?.eventKey || '';
  const calendarParams = new URLSearchParams({ kind: myLearner.kind, learner: myLearner.id });
  if (recoveryEventKey) calendarParams.set('event', recoveryEventKey);
  const calendarHref = `/learner/calendar?${calendarParams.toString()}`;

  return (
    <div className="space-y-4">
      {submitted ? (
        <Panel className="mx-auto max-w-2xl text-center">
          <span className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-emerald-100">
            <AppIcon className="ri-check-line text-2xl text-emerald-600" />
          </span>
          <p className="mb-2 text-[11px] font-bold uppercase tracking-[0.14em] text-emerald-600">{recoveryApproved ? 'Recovery confirmed' : 'Report submitted'}</p>
          <h2 className="mb-2 text-[16px] font-semibold text-foreground-900">{recoveryApproved ? 'Added to your calendar' : 'Thanks for letting us know'}</h2>
          <p className="mx-auto mb-5 max-w-lg text-[13px] leading-6 text-foreground-500">
            Your report for <strong className="text-foreground-800">{submittedReport?.sessionTitle}</strong> has been saved{recoveryApproved ? submittedReport?.recoveryMethod === 'alternative' ? ' and your alternative session is ready to join.' : ' and your recovery plan is confirmed.' : ' for your coach to review.'}
          </p>
          {catchupLinkError && <p role="alert" className="mx-auto mb-5 max-w-lg rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[12px] text-amber-900">{catchupLinkError}</p>}
          <div className="mx-auto mb-5 grid max-w-xl gap-3 rounded-xl bg-background-100/70 p-4 text-left sm:grid-cols-2">
            {submittedReport?.recoveryMethod && <div><p className="text-[10px] uppercase tracking-wide text-foreground-400">Recovery plan</p><p className="text-[13px] font-semibold text-foreground-800">{submittedReport.recoveryMethod === 'recorded' ? 'Watch the recording / confirmed (attendance stays absent)' : submittedReport.recoveryMethod === 'alternative' ? 'Alternative cohort session / confirmed' : catchupLinkError ? 'Coach catch-up / not booked yet' : 'Coach catch-up booked / confirmed'}</p></div>}
            {recoverySchedule && <div><p className="text-[10px] uppercase tracking-wide text-foreground-400">Recovery time</p><p className="text-[13px] font-semibold text-foreground-800">{recoverySchedule}</p></div>}
            <div><p className="text-[10px] uppercase tracking-wide text-foreground-400">Reference</p><p className="text-[13px] font-semibold text-foreground-800">{submittedReport?.reference}</p></div>
            <div><p className="text-[10px] uppercase tracking-wide text-foreground-400">Current status</p><p className={`text-[13px] font-semibold ${recoveryApproved ? 'text-emerald-600' : 'text-amber-600'}`}>{recoveryApproved ? 'Confirmed' : 'Pending review'}</p></div>
            <div><p className="text-[10px] uppercase tracking-wide text-foreground-400">Session</p><p className="text-[13px] font-semibold text-foreground-800">{submittedReport ? displayDate(submittedReport.sessionDate) : ''}</p></div>
            <div><p className="text-[10px] uppercase tracking-wide text-foreground-400">Evidence</p><p className="truncate text-[13px] font-semibold text-foreground-800">{file?.name || (explanation.trim() ? 'Written explanation' : 'Not provided')}</p></div>
          </div>
          {recoveryApproved && submittedReport?.recoveryMethod === 'alternative' && submittedReport.alternativeSession?.joinUrl && (
            <a href={submittedReport.alternativeSession.joinUrl} target="_blank" rel="noreferrer" className="mx-auto mb-4 inline-flex items-center gap-2 rounded-lg bg-primary-600 px-4 py-2.5 text-[13px] font-semibold text-white transition hover:bg-primary-700">
              <AppIcon className="ri-microsoft-line" /> Join alternative session
            </a>
          )}
          <div className="flex flex-wrap justify-center gap-2">
            {recoveryApproved && <Link to={calendarHref} className="inline-flex items-center gap-2 rounded-lg bg-primary-600 px-4 py-2.5 text-[13px] font-semibold text-white transition hover:bg-primary-700"><AppIcon className="ri-calendar-check-line" />View in calendar</Link>}
            <button type="button" onClick={resetForm} className="rounded-lg border border-foreground-200 bg-background-50 px-4 py-2.5 text-[13px] font-semibold text-foreground-600 transition hover:bg-background-100">
              Submit another report
            </button>
          </div>
        </Panel>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-5">
          <Panel className={compact ? styles.absenceForm : 'space-y-5'}>
            {!compact && <SectionHeader title="Absence details" description="Fields marked with * are required" icon="ri-edit-box-line" />}

            <div>
              <label htmlFor="missed-session" className="mb-2 block text-[13px] font-semibold text-foreground-700">{scope === 'meetings' ? 'Meeting *' : 'Lecture *'}</label>
              <select id="missed-session" value={sessionId} onChange={(event) => { setSessionId(event.target.value); setRecoveryMethod(''); setTargetOccurrenceId(''); setRecordingDate(''); setRecordingTime(''); setCatchupBooking(null); setConfirmed(false); }} required disabled={reportsLoading || availableSessions.length === 0 || bookingBusy || submitting} className="w-full rounded-xl border border-foreground-200 bg-background-50 px-3.5 py-3 text-[13px] text-foreground-800 outline-none transition focus:border-primary-400 focus:ring-2 focus:ring-primary-100 disabled:cursor-not-allowed disabled:bg-background-100 disabled:text-foreground-400">
                <option value="">{scope === 'meetings' ? reportsLoading ? 'Loading meeting...' : availableSessions.length ? 'Choose a meeting' : 'No unreported meetings available' : reportsLoading ? 'Loading lectures...' : availableSessions.length === 0 ? 'No unreported absent or upcoming lectures' : 'Choose an absent or upcoming lecture'}</option>
                {availableSessions.map((session) => <option key={session.id} value={session.id}>{displayDate(session.dateIso)} - {session.title}{session.status === 'upcoming' ? ' (Upcoming)' : ''}</option>)}
              </select>
              {selectedSession && (compact ? <p className={styles.absenceDate}>Date: {displayDate(selectedSession.dateIso)}{selectedSession.startTime && `, ${selectedSession.startTime}`}{selectedSession.endTime && ` – ${selectedSession.endTime}`}</p> : (
                <div className="mt-3 grid gap-2 rounded-xl border border-primary-100 bg-primary-50/60 p-3 sm:grid-cols-3">
                  <div className="flex items-center gap-2 text-[12px] text-foreground-600"><AppIcon className="ri-time-line text-primary-500" />{selectedSession.startTime}{selectedSession.endTime ? ` - ${selectedSession.endTime}` : ''}</div>
                  <div className="flex items-center gap-2 text-[12px] text-foreground-600"><AppIcon className="ri-user-star-line text-primary-500" />{selectedSession.coach || 'Coach not assigned'}</div>
                  <div className="flex items-center gap-2 text-[12px] text-foreground-600"><AppIcon className="ri-book-open-line text-primary-500" />{selectedSession.module || selectedSession.sessionType.replaceAll('_', ' ')}</div>
                </div>
              ))}
            </div>

            <fieldset>
              <legend className="mb-2 block text-[13px] font-semibold text-foreground-700">Main reason *</legend>
              {compact ? <select aria-label="Main reason" value={reasonType} onChange={event => setReasonType(event.target.value)} required>
                <option value="">Select a reason</option>
                {REASONS.map(reason => <option key={reason.value} value={reason.value}>{reason.label}</option>)}
              </select> : <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {REASONS.map((reason) => (
                  <label key={reason.value} className={`flex cursor-pointer items-center gap-2.5 rounded-xl border p-3 text-[12px] font-medium transition ${reasonType === reason.value ? 'border-primary-400 bg-primary-50 text-primary-700 ring-1 ring-primary-200' : 'border-foreground-200 bg-background-50 text-foreground-600 hover:border-primary-200'}`}>
                    <input type="radio" name="reason" value={reason.value} checked={reasonType === reason.value} onChange={(event) => setReasonType(event.target.value)} className="sr-only" />
                    <AppIcon className={`${reason.icon} text-base ${reasonType === reason.value ? 'text-primary-600' : 'text-foreground-400'}`} />
                    <span>{reason.label}</span>
                    {reasonType === reason.value && <AppIcon className="ri-check-line ml-auto text-primary-600" />}
                  </label>
                ))}
              </div>}
              {reasonType === 'other' && (
                <div className="mt-3 rounded-xl border border-primary-200 bg-primary-50/40 p-3.5">
                  <label htmlFor="other-absence-reason" className="mb-2 block text-[12px] font-semibold text-foreground-700">
                    Please specify the reason *
                  </label>
                  <input
                    id="other-absence-reason"
                    type="text"
                    value={otherReason}
                    onChange={(event) => setOtherReason(event.target.value)}
                    maxLength={120}
                    required
                    autoFocus
                    placeholder="Type your reason here..."
                    className="w-full rounded-lg border border-foreground-200 bg-background-50 px-3.5 py-2.5 text-[13px] text-foreground-800 outline-none transition placeholder:text-foreground-300 focus:border-primary-400 focus:ring-2 focus:ring-primary-100"
                  />
                  <p className="mt-1.5 text-right text-[10px] text-foreground-400">{otherReason.length}/120</p>
                </div>
              )}
            </fieldset>

            {scope === 'meetings' ? <p className={styles.recoveryHint}>Your coach will review your absence report. Use Reschedule on the meeting page to arrange another time.</p> : <fieldset className={styles.recoveryPlan} disabled={!selectedSession || bookingBusy || submitting}>
              <legend>Choose your recovery plan *</legend>
              <p className={styles.recoveryIntro}>Your choice is confirmed immediately and added to your calendar.</p>
              <div className={styles.recoveryOptions}>
                <label>
                  <input aria-label="Another group session" type="radio" name="recovery-method" value="alternative" required checked={recoveryMethod === 'alternative'} onChange={selectAlternativeRecovery} />
                  <AppIcon className="ri-team-line" />
                  <span><strong>Another group session</strong><small>Join the same session with another group.</small></span>
                </label>
                <label>
                  <input aria-label="Coach catch-up" type="radio" name="recovery-method" value="catch-up" required checked={recoveryMethod === 'catch-up'} onChange={() => { setRecoveryMethod('catch-up'); setTargetOccurrenceId(''); setRecordingDate(''); setRecordingTime(''); setConfirmed(false); }} />
                  <AppIcon className="ri-calendar-event-line" />
                  <span><strong>Coach catch-up</strong><small>Book a dedicated time with your coach.</small></span>
                </label>
                <label>
                  <input aria-label="Watch the recording" type="radio" name="recovery-method" value="recorded" required checked={recoveryMethod === 'recorded'} onChange={selectRecordedRecovery} />
                  <AppIcon className="ri-video-line" />
                  <span><strong>Watch the recording</strong><small>Does not make up the absence. Choose when you plan to watch it.</small></span>
                </label>
              </div>
            </fieldset>}
            {recoveryMethod === 'alternative' && selectedSession && <div className={styles.recoveryDetails}>
              {(selectedSession.alternativeSessions?.length ?? 0) > 0 ? <>
                <label htmlFor="alternative-session">Available equivalent session *</label>
                <select id="alternative-session" value={targetOccurrenceId} onChange={event => { setTargetOccurrenceId(event.target.value); setConfirmed(false); }} required>
                  <option value="">Choose a future session</option>
                  {(selectedSession.alternativeSessions || []).map(option => <option key={option.id} value={option.id}>{displayDate(option.dateIso)} · {option.startTime}{option.endTime ? `–${option.endTime}` : ''} · {option.group || option.cohort}</option>)}
                </select>
                <p className={styles.recoveryHint}>You stay in your current group; access applies only to this session.</p>
              </> : <div className={styles.recoveryUnavailable} role="status"><AppIcon className="ri-information-line" /><span><strong>No equivalent session is available.</strong> Choose a coach catch-up to make up this absence.</span></div>}
            </div>}
            {recoveryMethod === 'recorded' && selectedSession && <div className={styles.recoveryDetails}>
              <div className={styles.recoveryUnavailable}><AppIcon className="ri-information-line" /><span>Watching the recording supports your learning, but attendance remains absent.</span></div>
              <div className={styles.bookingFields}>
                <label>Viewing date *<input aria-label="Recording viewing date" type="date" min={selectedSession.dateIso} value={recordingDate} onChange={event => { setRecordingDate(event.target.value); setConfirmed(false); }} required /></label>
                <label>Viewing time *<input aria-label="Recording viewing time" type="time" value={recordingTime} onChange={event => { setRecordingTime(event.target.value); setConfirmed(false); }} required /></label>
              </div>
            </div>}
            {recoveryMethod === 'catch-up' && selectedSession && <CatchupBooking key={selectedSession.id} lecture={selectedSession} selectedKey={selectedBooking?.eventKey || ''} onSelect={selectBooking} onBusyChange={setBookingBusy} disabled={submitting} bookRef={bookCatchup} onDraftChange={setCatchupDraftReady} />}

            <OptionalDetails compact={compact}>
            <div>
              <div className="mb-2 flex items-center justify-between gap-3">
                <label htmlFor="absence-explanation" className="text-[13px] font-semibold text-foreground-700">Additional information (optional)</label>
                <span className="text-[11px] text-foreground-400">{explanation.length}/600</span>
              </div>
              <textarea id="absence-explanation" value={explanation} onChange={(event) => setExplanation(event.target.value)} maxLength={600} rows={4} placeholder="Add any details your coach should know..." className="w-full resize-none rounded-xl border border-foreground-200 bg-background-50 px-3.5 py-3 text-[13px] leading-6 text-foreground-800 outline-none transition placeholder:text-foreground-300 focus:border-primary-400 focus:ring-2 focus:ring-primary-100" />
            </div>

            <div>
              <p className="mb-2 text-[13px] font-semibold text-foreground-700">Supporting evidence <span className="font-normal text-foreground-400">(optional)</span></p>
              {!file ? (
                <button
                  type="button"
                  disabled={!canUploadEvidence}
                  onClick={() => { if (canUploadEvidence) fileInputRef.current?.click(); }}
                  onDragOver={(event) => { event.preventDefault(); if (canUploadEvidence) setDragging(true); }}
                  onDragLeave={() => setDragging(false)}
                  onDrop={(event) => { event.preventDefault(); setDragging(false); if (canUploadEvidence) selectFile(event.dataTransfer.files[0]); }}
                  className={`flex w-full flex-col items-center justify-center rounded-xl border-2 border-dashed px-4 py-7 text-center transition ${!canUploadEvidence ? 'cursor-not-allowed border-foreground-200 bg-background-100/70 opacity-60' : dragging ? 'border-primary-400 bg-primary-50' : 'border-foreground-300 bg-background-100/40 hover:border-primary-300 hover:bg-primary-50/40'}`}
                >
                  <span className={`mb-2 flex h-11 w-11 items-center justify-center rounded-xl ${canUploadEvidence ? 'bg-primary-100' : 'bg-background-200'}`}><AppIcon className={`${canUploadEvidence ? 'ri-upload-cloud-2-line text-primary-600' : 'ri-lock-line text-foreground-400'} text-xl`} /></span>
                  <span className="text-[13px] font-semibold text-foreground-700">{canUploadEvidence ? 'Drop an image here or click to browse' : 'Complete the session and reason above first'}</span>
                  <span className="mt-1 text-[11px] text-foreground-400">{canUploadEvidence ? 'JPG, PNG or PDF - maximum 10 MB' : 'Evidence upload will unlock automatically'}</span>
                </button>
              ) : (
                <div className="flex items-center gap-3 rounded-xl border border-emerald-200 bg-emerald-50/70 p-3.5">
                  {file.type.startsWith('image/') && selectedFilePreviewUrl ? (
                    <img src={selectedFilePreviewUrl} alt="Selected evidence preview" className="h-14 w-14 shrink-0 rounded-lg border border-white object-cover shadow-sm" />
                  ) : (
                    <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-lg bg-white text-xl text-red-500 shadow-sm"><AppIcon className="ri-file-pdf-2-line" /></span>
                  )}
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[13px] font-semibold text-foreground-800">{file.name}</p>
                    <p className="text-[11px] text-foreground-400">{(file.size / 1024 / 1024).toFixed(2)} MB - ready to upload</p>
                    <button type="button" onClick={openSelectedFilePreview} className="mt-1 inline-flex items-center gap-1 text-[11px] font-semibold text-primary-600 hover:underline"><AppIcon className="ri-eye-line" />Preview selected file</button>
                  </div>
                  <button type="button" onClick={() => { setFile(null); if (fileInputRef.current) fileInputRef.current.value = ''; }} aria-label="Remove attachment" className="flex h-8 w-8 items-center justify-center rounded-lg text-foreground-400 transition hover:bg-red-50 hover:text-red-600"><AppIcon className="ri-delete-bin-line" /></button>
                </div>
              )}
              <input ref={fileInputRef} type="file" disabled={!canUploadEvidence} accept="image/jpeg,image/png,image/webp,application/pdf" onChange={(event) => selectFile(event.target.files?.[0])} className="hidden" />
              {fileError && <p className="mt-2 flex items-center gap-1 text-[12px] text-red-600"><AppIcon className="ri-error-warning-line" />{fileError}</p>}
            </div>

            </OptionalDetails>
            <label className={`flex cursor-pointer items-start gap-3 rounded-xl bg-background-100/60 p-3.5 ${compact ? styles.absenceConfirmation : ''}`}>
              <input type="checkbox" required checked={confirmed} onChange={event => setConfirmed(event.target.checked)} disabled={bookingBusy || submitting} className="mt-0.5 h-4 w-4 rounded border-foreground-300 text-primary-500 focus:ring-primary-300" />
              <span className="text-[12px] leading-5 text-foreground-500">I confirm the information in this report is accurate and I understand my coach may contact me for more details.</span>
            </label>

            <div className={`flex flex-col-reverse gap-3 border-t border-foreground-100 pt-4 sm:flex-row sm:items-center sm:justify-between ${compact ? styles.absenceFooter : ''}`}>
              {onCancel && <button type="button" onClick={onCancel} className="rounded-lg border border-foreground-200 px-4 py-2 text-sm font-semibold">Cancel</button>}
              <p className="flex items-center gap-1.5 text-[11px] text-foreground-400"><AppIcon className="ri-shield-check-line text-emerald-500" />Your information is only shared with the relevant support team.</p>
              <button type="submit" disabled={!canSubmit || submitting} className="inline-flex items-center justify-center gap-2 rounded-xl bg-primary-600 px-5 py-3 text-[13px] font-bold text-white shadow-sm transition hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-40">
                {bookingBusy ? <><AppIcon className="ri-loader-4-line animate-spin" /> Booking catch-up...</>
                  : submitting ? <><AppIcon className="ri-loader-4-line animate-spin" /> Saving report...</>
                    : <>{booksOnSubmit ? 'Book catch-up & submit' : 'Submit absence report'} <AppIcon className="ri-arrow-right-line" /></>}
              </button>
            </div>
            {requestError && <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[12px] text-red-700"><AppIcon className="ri-error-warning-line mr-1.5" />{requestError}</p>}
          </Panel>
        </form>
      )}

      {showGuidance && !submitted && (
        <Panel>
          <SectionHeader title="What happens next?" icon="ri-route-line" />
          <ol className="mt-4 space-y-4">
            {[
              ['1', 'Report received', 'Your coach is notified in the LMS and by email.'],
              ['2', 'Recovery confirmed', 'Your selected recovery plan is added immediately.'],
              ['3', 'Coach notified', 'Your coach receives the selected plan in the LMS and by email.'],
            ].map(([step, title, description], index) => (
              <li key={step} className="relative flex gap-3">
                {index < 2 && <span className="absolute left-[13px] top-7 h-8 w-px bg-foreground-200" />}
                <span className="z-10 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary-100 text-[11px] font-bold text-primary-600">{step}</span>
                <div><p className="text-[13px] font-semibold text-foreground-800">{title}</p><p className="mt-0.5 text-[11px] leading-4 text-foreground-400">{description}</p></div>
              </li>
            ))}
          </ol>
        </Panel>
      )}

      {showHistory && (
        <Panel>
          <SectionHeader title="Previous reports" description="Last 90 days" icon="ri-history-line" />
          <div className="mt-3 space-y-2.5">
            {reportsLoading && <div className="py-6 text-center text-[12px] text-foreground-400"><AppIcon className="ri-loader-4-line mr-1.5 animate-spin" />Loading reports...</div>}
            {!reportsLoading && reports.length === 0 && <EmptyState size="sm" icon="ri-file-list-3-line" title="No absence reports yet" />}
            {reports.map((report) => (
              <article key={report.id} className="rounded-xl border border-foreground-200/70 bg-background-50 p-3.5">
                <div className="mb-2 flex items-start justify-between gap-2"><div className="min-w-0"><p className="truncate text-[13px] font-semibold text-foreground-800">{report.sessionTitle}</p><p className="mt-0.5 text-[10px] text-foreground-400">{displayDate(report.sessionDate)} - {report.reference}</p></div><span className={`shrink-0 rounded-full border px-2 py-0.5 text-[9px] font-bold ${statusClass[report.status.trim().toLowerCase()] || statusClass.pending}`}>{report.status}</span></div>
                {report.recoveryMethod === 'alternative' && report.alternativeSession && <div className="mb-2 rounded-lg border border-primary-100 bg-primary-50/50 p-2.5 text-[11px] text-foreground-600">
                  <p className="font-semibold text-foreground-800">Alternative: {report.alternativeSession.group || report.alternativeSession.cohort}</p>
                  <p>{displayDate(report.alternativeSession.dateIso)} · {report.alternativeSession.startTime}{report.alternativeSession.endTime ? `–${report.alternativeSession.endTime}` : ''}</p>
                  {report.alternativeSession.joinUrl && <a href={report.alternativeSession.joinUrl} target="_blank" rel="noreferrer" className="mt-1 inline-flex font-semibold text-primary-600 hover:underline">Join approved session</a>}
                  {!report.alternativeSession.joinUrl && <p className="mt-1 text-amber-700">Session link unavailable</p>}
                </div>}
                <div className="flex items-center gap-1.5 border-t border-foreground-100 pt-2 text-[10px] text-foreground-400">
                  <AppIcon className="ri-attachment-2" />
                  {report.evidenceUrl ? (
                    <button
                      type="button"
                      onClick={() => openEvidencePreview(report)}
                      className="inline-flex items-center gap-1 font-medium text-primary-600 transition hover:text-primary-700 hover:underline"
                      title="Preview evidence"
                    >
                      {/\.pdf(?:\?|$)/i.test(report.evidenceUrl) ? 'View PDF evidence' : 'View image evidence'}
                      <AppIcon className="ri-eye-line" />
                    </button>
                  ) : (
                    <span className="truncate">{report.evidenceText ? 'Written explanation' : 'No evidence provided'}</span>
                  )}
                </div>
              </article>
            ))}
          </div>
        </Panel>
      )}

      {showGuidance && (
        <div className="rounded-xl border border-amber-200/70 bg-amber-50 p-4">
        <div className="flex gap-3"><AppIcon className="ri-information-line mt-0.5 text-amber-600" /><div><p className="text-[12px] font-semibold text-amber-900">Need urgent support?</p><p className="mt-1 text-[11px] leading-4 text-amber-700">Contact your coach directly if your absence relates to wellbeing or safeguarding.</p><Link to="/learner/messages" className="compact-action mt-2 text-[11px] font-bold text-amber-800 hover:underline">Message my coach <AppIcon className="ri-arrow-right-s-line" /></Link></div></div>
        </div>
      )}

      {evidencePreview && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-3 backdrop-blur-sm sm:p-6" onMouseDown={(event) => { if (event.target === event.currentTarget) closeEvidencePreview(); }}>
          <section className="flex h-[90vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
            <header className="flex items-center justify-between gap-4 border-b border-foreground-200 px-4 py-3 sm:px-5">
              <div className="min-w-0">
                <p className="text-[10px] font-bold uppercase tracking-wider text-primary-600">Evidence preview</p>
                <h3 className="truncate text-[13px] font-bold text-foreground-900">{evidencePreview.title}</h3>
              </div>
              <button type="button" onClick={closeEvidencePreview} aria-label="Close evidence preview" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-foreground-500 transition hover:bg-background-100 hover:text-foreground-900">
                <AppIcon className="ri-close-line text-xl" />
              </button>
            </header>
            <div className="relative flex min-h-0 flex-1 items-center justify-center bg-background-200/60 p-2 sm:p-4">
              {previewLoading && <div className="text-sm font-medium text-foreground-500"><AppIcon className="ri-loader-4-line mr-2 animate-spin text-primary-600" />Loading evidence...</div>}
              {!previewLoading && previewError && <div className="rounded-xl border border-red-200 bg-red-50 p-5 text-center"><AppIcon className="ri-error-warning-line mb-2 block text-2xl text-red-500" /><p className="text-sm font-semibold text-red-700">{previewError}</p></div>}
              {!previewLoading && !previewError && evidencePreview.objectUrl && evidencePreview.isPdf && (
                <iframe
                  src={evidencePreview.objectUrl}
                  title={evidencePreview.title}
                  className="h-full w-full rounded-lg bg-white"
                  onError={() => setPreviewError('Could not display this PDF evidence.')}
                />
              )}
              {!previewLoading && !previewError && evidencePreview.objectUrl && !evidencePreview.isPdf && (
                <img
                  src={evidencePreview.objectUrl}
                  alt={evidencePreview.title}
                  className="max-h-full max-w-full rounded-lg object-contain shadow-sm"
                  onError={() => setPreviewError('Could not display this image evidence.')}
                />
              )}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

function OptionalDetails({ compact, children }: { compact: boolean; children: ReactNode }) {
  return compact ? <details className={styles.absenceOptional}><summary>Add details or evidence (optional)</summary>{children}</details> : <>{children}</>;
}
