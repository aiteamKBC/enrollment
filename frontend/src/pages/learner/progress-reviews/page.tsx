import { ImportedReviewSection, ImportedValue } from '../reviews/imported/ImportedReviewSection';
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { roleNavMap } from '@/mocks/navigation';
import { downloadLearnerMcmPdf, downloadMigratedReviewForParty, fetchLearnerMeetingArtifacts, learnerMeetingArtifactContentUrl, saveLearnerEventReviewAnswers, saveMigratedReviewPartyAnswers, signLearnerProgressReview, signMigratedReviewAsParty, type LearnerCalendarEvent } from '@/api/learnerCalendar';
import { useLinkedLearner } from '@/hooks/useMyLearner';
import { useLearnerWorkspaceAccess } from '@/hooks/useLearnerWorkspaceAccess';
import { responsesForSection, type ProgressReviewResponses } from '@/pages/shared/progressReviewForm';
import { RowsSkeleton } from '@/components/feature/Skeletons';
import { isMigratedContinuationEvent, useReviewSessions } from '@/pages/learner/reviews/useReviewSessions';
import { isoDate } from '@/pages/learner/reviews/bookingDates';
import { CoachMeetingArtifactsPanel } from '@/pages/coach/shared/CoachMeetingArtifactsPanel';
import ProgressReviewPptxModal from '@/pages/coach/progress-reviews/components/ProgressReviewPptxModal';
import styles from './progressReviews.module.css';
import { useMeetingAttendance } from '../reviews/useMeetingAttendance';
import type { MeetingAttendance } from '@/api/meetingAttendance';
import AbsenceReportDialog from '../attendance/components/AbsenceReportDialog';
import AbsenceReportForm from '../attendance/components/AbsenceReportForm';
import { useMeetingBooking } from '../reviews/useMeetingBooking';
import ReviewsHome from './ReviewsHome';
import { reviewOverview, reviewsListHref } from './reviewPresentation';
import { useCoachingReviewDefinitions } from '../monthly-coaching/useCoachingReviewDefinitions';
import { LearnerReviewInstanceForm } from '../reviews/LearnerReviewInstanceForm';
import { useLearnerReviewInstance } from "../reviews/LearnerReviewInstanceForm.helpers";
import { ReviewPdfDownload } from '@/components/reviews/ReviewPdfDownload';

const learnerNav = roleNavMap.learner;

function formatDate(value?: string | null, long = false): string {
  if (!value) return '-';
  return new Date(`${value}T00:00:00`).toLocaleDateString('en-GB', long
    ? { weekday: 'short', day: '2-digit', month: 'long', year: 'numeric' }
    : { day: '2-digit', month: 'short', year: 'numeric' });
}

function monthLabel(value?: string | null): string {
  if (!value) return '';
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString('en-GB', { month: 'short', year: 'numeric' });
}

function formatTime(value?: string | null): string {
  if (!value) return '-';
  const [hour, minute] = value.split(':').map(Number);
  return new Date(2000, 0, 1, hour, minute).toLocaleTimeString('en-GB', {
    hour: '2-digit', minute: '2-digit',
  });
}

function reviewDate(review?: LearnerCalendarEvent | null): string | null {
  return review?.scheduledDate || review?.targetDate || review?.date || null;
}

function progressReviewTitle(review?: LearnerCalendarEvent | null): string {
  if (review?.importedReview) return review.importedReview.name || review.title || 'Review';
  const occurrence = review?.occurrenceNumber ?? review?.sequence;
  if (review?.reviewTemplateId) return `${review.title}${occurrence != null ? ` #${occurrence}` : ' — Manual Review'}`;
  const month = monthLabel(reviewDate(review));
  return `Progress Review${month ? ` — ${month}` : ''}${occurrence != null ? ` #${occurrence}` : ''}`;
}

function reviewTypeLabel(review?: LearnerCalendarEvent | null): string {
  return review?.reviewTypeName || review?.importedReview?.type || 'Formal progress review';
}

function importedSectionName(name: string): string {
  return name.trim().replace(/\s+(completed|incomplete)$/i, '').trim().toLowerCase();
}

function ImportedAttachmentLinks({ section }: { section: NonNullable<LearnerCalendarEvent['importedReview']>['sections'][number] }) {
  const [preview, setPreview] = useState<{ url: string; name: string } | null>(null);
  const links = section.fields.flatMap((field) => 'links' in field && Array.isArray(field.links) ? field.links : []);
  if (!links.length) return null;
  return <><div className="mt-2 space-y-1">{links.map((link, index) => {
    const url = link.azure_url || link.url || link.href;
    const name = link.text || link.title || `Attachment ${index + 1}`;
    return url ? <button key={index} type="button" onClick={() => setPreview({ url, name })} className="block max-w-full truncate text-left text-xs font-semibold text-primary-600 underline hover:text-primary-800">{name}</button> : null;
  })}</div>{preview && <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/70 p-4" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setPreview(null); }}><div role="dialog" aria-modal="true" aria-label={preview.name} className="flex h-[min(88vh,900px)] w-full max-w-5xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl"><div className="flex items-center justify-between gap-3 border-b border-background-200 px-4 py-3"><p className="truncate text-sm font-bold text-foreground-900">{preview.name}</p><button type="button" aria-label="Close attachment preview" onClick={() => setPreview(null)} className="flex h-8 w-8 items-center justify-center rounded-lg text-foreground-500 hover:bg-background-100"><AppIcon className="ri-close-line" /></button></div><iframe title={preview.name} src={preview.url} className="min-h-0 flex-1 bg-background-100" /></div></div>}</>;
}

function ImportedReviewsSchedule({ section }: { section: NonNullable<LearnerCalendarEvent['importedReview']>['sections'][number] }) {
  const tableRows = section.tables.flatMap((table) => table.rows || []);
  if (tableRows.length > 1) return <div className="overflow-x-auto rounded-xl border border-background-200 bg-white"><table className="min-w-full text-left text-xs"><thead className="bg-background-100 text-[10px] uppercase tracking-wide text-foreground-500"><tr>{(tableRows[0] || []).map((cell, index) => <th key={index} className="px-4 py-3">{<ImportedValue value={cell} />}</th>)}</tr></thead><tbody className="divide-y divide-background-200">{tableRows.slice(1).map((row, index) => <tr key={index}>{(Array.isArray(row) ? row : [row]).map((cell, cellIndex) => <td key={cellIndex} className="whitespace-pre-wrap px-4 py-3">{<ImportedValue value={cell} />}</td>)}</tr>)}</tbody></table></div>;
  const dates = [...(section.rawText || '').matchAll(/\b\d{2}\/\d{2}\/\d{4}\b/g)].map((match) => match[0]);
  const rows = Array.from({ length: Math.ceil(dates.length / 2) }, (_, index) => [dates[index * 2] || '-', dates[index * 2 + 1] || '-']);
  if (!rows.length) return null;
  return <div className="overflow-x-auto rounded-xl border border-background-200 bg-white"><table className="min-w-full text-left text-xs"><thead className="bg-background-100 text-[10px] uppercase tracking-wide text-foreground-500"><tr><th className="px-4 py-3">Review Name / Review Type</th><th className="px-4 py-3">Planned Date</th><th className="px-4 py-3">Actual Date</th></tr></thead><tbody className="divide-y divide-background-200">{rows.map(([planned, actual], index) => <tr key={index}><td className="px-4 py-3"><p className="font-bold text-foreground-900">Progress Review</p><p className="text-foreground-500">Progress Review</p></td><td className="px-4 py-3">{planned}</td><td className="px-4 py-3">{actual}</td></tr>)}</tbody></table></div>;
}

function ImportedFunctionalSkills({ section }: { section: NonNullable<LearnerCalendarEvent['importedReview']>['sections'][number] }) {
  const [preview, setPreview] = useState<{ url: string; name: string } | null>(null);
  const text = section.rawText || '';
  const beforeResults = text.split(/\bResults\b/i)[0];
  const resultsText = text.split(/\bResults\b/i)[1] || '';
  const assessments = [...beforeResults.matchAll(/\b(English|Maths)\s*\n+\s*Assessment Date:\s*([^\n]+)\s*\n+\s*(Level\s*\d+)/gi)].map((match) => ({ subject: match[1], date: match[2], level: match[3] }));
  const exemptions = [...(text.match(/\bExemptions\b([\s\S]*?)(?=\bResults\b|$)/i)?.[1] || '').matchAll(/\b(English|Maths|ICT)\s*\n+\s*(Opt Out|Not Exempt)(?:\s*\n+\s*([^\n]+\.pdf))?/gi)].map((match) => ({ subject: match[1], status: match[2], attachment: match[3] }));
  const results = [...resultsText.matchAll(/\b(English Reading|English Writing|English SLC|Maths|ICT)\s*\n+\s*Assessment Date:\s*\n+\s*([^\n]+)/gi)].map((match) => ({ subject: match[1], score: match[2] }));
  if (!assessments.length && !exemptions.length && !results.length) return null;
  const links = section.fields.flatMap((field) => 'links' in field && Array.isArray(field.links) ? field.links : []);
  const attachment = links[0];
  const attachmentUrl = attachment?.azure_url || attachment?.url || attachment?.href;
  return <div className="space-y-4">
    {assessments.length > 0 && <div><h4 className="mb-2 text-sm font-bold text-foreground-900">Initial Assessments</h4><div className="grid gap-3 sm:grid-cols-2">{assessments.map((item) => <div key={`${item.subject}:${item.date}`} className="rounded-lg border border-background-200 bg-white p-3"><p className="text-sm font-bold text-foreground-900">{item.subject}</p><p className="mt-1 text-xs text-foreground-500">Assessment Date: {item.date}</p><span className="mt-2 inline-flex rounded bg-primary-50 px-2 py-1 text-xs font-bold text-primary-700">{item.level}</span></div>)}</div></div>}
    {exemptions.length > 0 && <div><h4 className="mb-2 text-sm font-bold text-foreground-900">Exemptions</h4><div className="grid gap-3 sm:grid-cols-3">{exemptions.map((item) => <div key={item.subject} className="rounded-lg border border-background-200 bg-white p-3"><div className="flex items-start justify-between gap-2"><p className="text-sm font-bold text-foreground-900">{item.subject}</p><span className="rounded bg-background-200 px-2 py-1 text-[10px] font-bold text-foreground-600">{item.status}</span></div>{item.attachment && attachmentUrl && <button type="button" onClick={() => setPreview({ url: attachmentUrl, name: item.attachment || 'Attachment' })} className="mt-3 block max-w-full truncate text-left text-xs font-semibold text-primary-600 underline">{item.attachment}</button>}</div>)}</div></div>}
    {results.length > 0 && <div><h4 className="mb-2 text-sm font-bold text-foreground-900">Results</h4><div className="grid gap-3 sm:grid-cols-3">{results.map((item) => <div key={`${item.subject}:${item.score}`} className="rounded-lg border border-background-200 bg-white p-3"><p className="text-sm font-bold text-foreground-900">{item.subject}</p><p className="mt-1 text-xs text-foreground-500">Assessment Date:</p><span className="mt-2 inline-flex h-8 w-8 items-center justify-center rounded-full border-4 border-background-200 text-[10px] font-bold text-foreground-700">{item.score}</span></div>)}</div></div>}
    {preview && <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/70 p-4" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setPreview(null); }}><div role="dialog" aria-modal="true" aria-label={preview.name} className="flex h-[min(88vh,900px)] w-full max-w-5xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl"><div className="flex items-center justify-between gap-3 border-b border-background-200 px-4 py-3"><p className="truncate text-sm font-bold text-foreground-900">{preview.name}</p><button type="button" aria-label="Close attachment preview" onClick={() => setPreview(null)} className="flex h-8 w-8 items-center justify-center rounded-lg text-foreground-500 hover:bg-background-100"><AppIcon className="ri-close-line" /></button></div><iframe title={preview.name} src={preview.url} className="min-h-0 flex-1 bg-background-100" /></div></div>}
  </div>;
}

export function ImportedReviewSections({ review }: { review: NonNullable<LearnerCalendarEvent['importedReview']> }) {
  if (!review.sections.length) return <Empty>No section details were imported for this review.</Empty>;
  return <div className="space-y-3">
    {review.sections.map((section) => <div key={section.id}>
      <div className="space-y-3">
        {importedSectionName(section.name) === 'reviews schedule' && <ImportedReviewsSchedule section={section} />}
        {importedSectionName(section.name) === 'functional skills' && <ImportedFunctionalSkills section={section} />}
        {importedSectionName(section.name) !== 'reviews schedule' && <>
        <ImportedReviewSection section={{ ...section, fields: section.fields.map(({ links: _links, ...field }) => field) }} />
        <ImportedAttachmentLinks section={section} />
        </>}
      </div>
    </div>)}
  </div>;
}

function shouldShowLearnerMeetingPanel(review?: LearnerCalendarEvent | null): boolean {
  return Boolean(
    review?.eventKey
    && review?.meetingLink
  );
}

function statusLabel(status?: string): string {
  const labels: Record<string, string> = {
    'not-scheduled': 'Not Scheduled',
    scheduled: 'Scheduled',
    'in-progress': 'In progress',
    'awaiting-signature': 'Awaiting signatures',
    completed: 'Completed',
    cancelled: 'Cancelled',
  };
  return status ? labels[status] || status : '-';
}

function statusStyle(status?: string): string {
  if (status === 'completed') return 'border-emerald-200 bg-emerald-50 text-emerald-700';
  if (status === 'scheduled') return 'border-blue-200 bg-blue-50 text-blue-700';
  if (status === 'in-progress') return 'border-amber-200 bg-amber-50 text-amber-700';
  if (status === 'awaiting-signature') return 'border-violet-200 bg-violet-50 text-violet-700';
  if (status === 'cancelled') return 'border-red-200 bg-red-50 text-red-700';
  if (status === 'not-scheduled') return 'border-amber-200 bg-amber-50 text-amber-700';
  return 'border-background-200 bg-background-100 text-foreground-700';
}

function initials(value?: string | null): string {
  if (!value) return '-';
  return value.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join('').toUpperCase();
}

function asNumber(value?: string | number | null): number | null {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function Empty({ children = 'No information has been recorded for this review.' }: { children?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-background-300 bg-background-100/50 px-5 py-7 text-center">
      <p className="text-xl font-bold text-foreground-300">-</p>
      <p className="mt-1 text-xs text-foreground-400">{children}</p>
    </div>
  );
}

function SavedReviewAnswers({
  sectionId,
  responses,
  emptyMessage,
}: {
  sectionId: string;
  responses?: ProgressReviewResponses;
  emptyMessage: string;
}) {
  const answers = responsesForSection(responses, sectionId);
  if (!answers.length) return <Empty>{emptyMessage}</Empty>;
  return (
    <div className="space-y-3">
      {answers.map((item) => (
        <div key={item.id} className="rounded-xl border border-background-200 bg-background-100/55 p-4">
          <p className="text-[10px] font-bold uppercase tracking-wide text-foreground-400">{item.label}</p>
          {item.type === 'rating' ? (
            <div className="mt-2 flex items-center gap-2">
              <span className="text-lg font-bold text-primary-700">{item.answer}/5</span>
              <div className="flex gap-1">{[1, 2, 3, 4, 5].map((rating) => <AppIcon key={rating} className={`${rating <= Number(item.answer) ? 'ri-star-fill text-amber-400' : 'ri-star-line text-foreground-300'}`}></AppIcon>)}</div>
            </div>
          ) : item.type === 'rag' ? (
            <span className={`mt-2 inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-bold ${item.answer.toLowerCase() === 'green' ? 'bg-emerald-100 text-emerald-700' : item.answer.toLowerCase() === 'amber' ? 'bg-amber-100 text-amber-700' : 'bg-red-100 text-red-700'}`}>
              <AppIcon className="ri-circle-fill text-[8px]"></AppIcon>{item.answer}
            </span>
          ) : (
            <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-foreground-700">{item.answer}</p>
          )}
        </div>
      ))}
    </div>
  );
}

function PersonCard({ role, name, icon, tone }: { role: string; name?: string; icon: string; tone: string }) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-background-200 bg-background-50 p-3.5">
      <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-xs font-bold ${tone}`}>
        {name ? initials(name) : <AppIcon className={icon} />}
      </span>
      <div className="min-w-0">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-foreground-400">{role}</p>
        <p className="truncate text-sm font-bold text-foreground-900">{name || '-'}</p>
      </div>
    </div>
  );
}

function Accordion({
  id, title, icon, open, onToggle, children,
}: {
  id: string; title: string; icon: string; open: boolean; onToggle: (id: string) => void; children: ReactNode;
}) {
  const steps: Record<string, number> = {
    'progress-checks': 1,
    'learner-reflection': 2,
    'manager-reflection': 3,
    'tutor-reflection': 4,
    safeguarding: 5,
    'additional-support': 6,
    actions: 7,
    rag: 8,
  };
  const step = steps[id];
  return (
    <section className={`overflow-hidden rounded-2xl border bg-background-50 transition-all duration-200 ${open ? 'border-primary-200 shadow-[0_10px_30px_rgba(69,26,128,0.08)]' : 'border-foreground-200/70 shadow-[0_3px_12px_rgba(25,12,50,0.035)] hover:border-primary-200 hover:shadow-md'}`}>
      <button type="button" onClick={() => onToggle(id)} aria-expanded={open} className={`flex w-full items-center gap-3 px-4 py-3.5 text-left transition-colors sm:px-5 sm:py-4 ${open ? 'bg-gradient-to-r from-primary-50/90 to-secondary-50/30' : 'hover:bg-primary-50/35'}`}>
        <span className={`relative flex h-10 w-10 shrink-0 items-center justify-center rounded-xl transition-colors ${open ? 'bg-primary-600 text-white shadow-md shadow-primary-600/20' : id === 'rag' ? 'bg-amber-50 text-amber-700' : 'bg-primary-50 text-primary-700'}`}>
          <AppIcon className={`${icon} text-base`} />
          <span className={`absolute -right-1.5 -top-1.5 flex h-5 min-w-5 items-center justify-center rounded-full border-2 border-white px-1 text-[8px] font-extrabold ${open ? 'bg-secondary-500' : 'bg-primary-700'} text-white`}>{step}</span>
        </span>
        <span className="min-w-0 flex-1">
          <span className={`block text-[9px] font-bold uppercase tracking-[0.12em] ${open ? 'text-primary-600' : 'text-foreground-400'}`}>Review section {step} of 8</span>
          <span className="mt-0.5 block text-sm font-bold text-foreground-900 sm:text-[15px]">{title}</span>
        </span>
        <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-all ${open ? 'rotate-180 bg-primary-100 text-primary-700' : 'bg-background-100 text-foreground-500'}`}><AppIcon className="ri-arrow-down-s-line text-lg" /></span>
      </button>
      {open && <div className="border-t border-primary-100 bg-white p-4 sm:p-6">{children}</div>}
    </section>
  );
}

export function ProgressReviewsListPage() {
  const learner = useLinkedLearner();
  return <ProgressReviewsList key={`${learner.kind}:${learner.id}`} />;
}

function ProgressReviewsList() {
  const { myLearner, learner, sessions: reviews, setEvents, bookingCalendar, loading, error, refresh } = useReviewSessions('progress-review', true);
  const attendance = useMeetingAttendance(myLearner);
  const [absence, setAbsence] = useState<MeetingAttendance | null>(null);
  // Reuse the existing read-only signature loader; opening this page cannot
  // create a review instance or submit a signature.
  const signatures = useCoachingReviewDefinitions(myLearner.kind, myLearner.id, reviews);
  const booking = useMeetingBooking({
    learner: myLearner, rules: bookingCalendar, attendance: attendance.data?.sessions || [],
    titleOf: progressReviewTitle, setEvents, refresh: () => { refresh(); attendance.refresh(); },
  });

  return (
    <WorkspaceShell role="learner" roleLabel={learnerNav.label} navItems={learnerNav.items}
      workspaceLabel={learnerNav.workspaceLabel} pageTitle="Progress Reviews"
      pageSubtitle="Your next progress review and previous records" userName={learner?.name || 'Learner'}
      userRole={learner?.programme ? `${learner.programme} Learner` : 'Learner'}>
      <main className="page-container min-w-0 w-full space-y-4 p-3 md:p-6">
        {error && <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-base text-red-700">{error}<button type="button" onClick={refresh} className="ml-3 min-h-12 font-bold underline">Try again</button></div>}
        {booking.notice}
        {attendance.error && <p role="alert" className="rounded-xl bg-red-50 p-4 text-base text-red-700">{attendance.error}<button type="button" onClick={attendance.refresh} className="ml-2 min-h-12 underline">Retry attendance</button></p>}
        {attendance.notice && <p role="status" className="rounded-xl bg-emerald-50 p-4 text-base text-emerald-700">{attendance.notice}</p>}
        {signatures.error && <p role="status" className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-base text-amber-900">Some signature details could not be loaded. Open a review to check its signatures.<button type="button" onClick={signatures.refresh} className="ml-2 min-h-12 underline">Retry signature details</button></p>}
        <ReviewsHome sessions={reviews} attendance={attendance.data?.sessions || []} definitions={signatures.definitions}
          learner={myLearner} lineManager={learner?.lineManager} today={attendance.data?.today || bookingCalendar?.today || isoDate(new Date())}
          timeZone={attendance.data?.timeZone} loading={loading} error={error} busy={Boolean(attendance.busy)} canAct={attendance.canAct}
          titleOf={progressReviewTitle} onSchedule={booking.openBooking} onAttend={attendance.attend} onReport={setAbsence}/>
        {booking.dialog}
        {absence?.absenceSessionId && absence.date && <AbsenceReportDialog onClose={() => setAbsence(null)}>
          <AbsenceReportForm key={absence.id} scope="meetings" compact showHistory={false}
            preselectMatch={{ id: absence.absenceSessionId, dateIso: absence.date, title: absence.title }}
            onSubmitted={() => attendance.refresh()} onCancel={() => setAbsence(null)} />
        </AbsenceReportDialog>}
      </main>
    </WorkspaceShell>
  );
}

export default function ProgressReviewsPage() {
  const navigate = useNavigate();
  const [listParams] = useSearchParams();
  const { reviewId } = useParams<{ reviewId: string }>();
  const { myLearner, learner, sessions: reviews, setEvents, bookingCalendar, loading, error: loadError, refresh } = useReviewSessions('progress-review');
  const { canProgress } = useLearnerWorkspaceAccess(myLearner.id);
  const attendance = useMeetingAttendance(myLearner);
  const [actionError, setError] = useState('');
  const error = loadError || actionError;
  const [selectedId, setSelectedId] = useState(reviewId || '');
  const [openSections, setOpenSections] = useState<string[]>(['progress-checks']);
  const [showSlidesModal, setShowSlidesModal] = useState(false);

  const completed = useMemo(() => reviews.filter((review) => review.status === 'completed'), [reviews]);
  const planned = useMemo(() => reviews.filter((review) => !['completed', 'cancelled'].includes(review.status)), [reviews]);

  useEffect(() => {
    if (reviewId) setSelectedId(reviewId);
  }, [reviewId]);

  useEffect(() => {
    if (reviewId || !reviews.length || reviews.some((review) => review.id === selectedId)) return;
    setSelectedId((planned[0] || completed.at(-1) || reviews[0]).id);
  }, [reviewId, reviews, planned, completed, selectedId]);

  const selected = reviewId
    ? reviews.find(review => review.id === reviewId) || reviews.find(review => review.reviewSource === 'aptem' && `imported-review:${review.reviewId}` === reviewId) || null
    : reviews.find(review => review.id === selectedId) || planned[0] || completed.at(-1) || reviews[0] || null;
  const migratedEvent = isMigratedContinuationEvent(selected);
  const instanceBacked = Boolean(selected?.reviewTemplateId || selected?.reviewInstanceId || migratedEvent);
  const reviewInstance = useLearnerReviewInstance(
    myLearner.kind,
    myLearner.id,
    instanceBacked ? (selected?.formEventKey || selected?.eventKey || selected?.id || '') : '',
  );
  const selectedIndex = selected ? reviews.findIndex((review) => review.id === selected.id) : -1;
  const previousReview = selectedIndex > 0 ? reviews[selectedIndex - 1] : null;
  const booking = useMeetingBooking({ learner: myLearner, rules: bookingCalendar, attendance: attendance.data?.sessions || [],
    titleOf: progressReviewTitle, setEvents, refresh: () => { refresh(); attendance.refresh(); } });
  const selectedPresentation = selected ? reviewOverview([selected], attendance.data?.sessions || [],
    attendance.data?.today || bookingCalendar?.today || isoDate(new Date()), { [selected.id]: reviewInstance.definition }).all[0] : null;
  const canBook = selectedPresentation?.action === 'schedule';
  const canReschedule = selectedPresentation?.canReschedule;

  const progressVariance = asNumber(learner?.progressVariance);

  const toggleSection = (id: string) => {
    setOpenSections((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  };

  const addToCalendar = () => {
    if (!selected?.scheduledDate || !selected.scheduledTime) return;
    const start = `${selected.scheduledDate.replaceAll('-', '')}T${selected.scheduledTime.replace(':', '')}00`;
    const startDate = new Date(`${selected.scheduledDate}T${selected.scheduledTime}:00`);
    const endDate = new Date(startDate.getTime() + selected.durationMinutes * 60_000);
    const pad = (value: number) => String(value).padStart(2, '0');
    const end = `${endDate.getFullYear()}${pad(endDate.getMonth() + 1)}${pad(endDate.getDate())}T${pad(endDate.getHours())}${pad(endDate.getMinutes())}00`;
    const content = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'BEGIN:VEVENT', `DTSTART:${start}`, `DTEND:${end}`, `SUMMARY:${progressReviewTitle(selected)}`, selected.meetingLink ? `URL:${selected.meetingLink}` : '', 'END:VEVENT', 'END:VCALENDAR'].filter(Boolean).join('\r\n');
    const url = URL.createObjectURL(new Blob([content], { type: 'text/calendar' }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `progress-review-${selected.sequence}.ics`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const loadArtifacts = useCallback((eventKey: string, signal?: AbortSignal) => (
    fetchLearnerMeetingArtifacts(myLearner.kind, myLearner.id, eventKey, signal)
  ), [myLearner.id, myLearner.kind]);
  const artifactContentUrl = useCallback((eventKey: string, artifactType: string, artifactId: string, options: { preview?: boolean } = {}) => (
    learnerMeetingArtifactContentUrl(myLearner.kind, myLearner.id, eventKey, artifactType, artifactId, options)
  ), [myLearner.id, myLearner.kind]);

  const showSlides = () => setShowSlidesModal(true);

  const saveSignature = async (signature: string) => {
    if (!selected || !canProgress) return;
    if (migratedEvent) {
      await signMigratedReviewAsParty(selected.formEventKey || selected.eventKey || selected.id, signature, {
        reviewRevision: reviewInstance.definition?.reviewRevision,
      });
      reviewInstance.refresh();
      return;
    }
    const result = await signLearnerProgressReview(myLearner.kind, myLearner.id, selected.eventKey || selected.id, { name: learner?.name || 'Learner', signature });
    setEvents((current) => current.map((event) => event.id === selected.id ? { ...event, ...result.event } : event));
    if (instanceBacked) reviewInstance.refresh();
  };

  return (
    <WorkspaceShell
      role="learner"
      roleLabel={learnerNav.label}
      navItems={learnerNav.items}
      workspaceLabel={learnerNav.workspaceLabel}
      pageTitle="Progress Reviews"
      pageSubtitle="Formal progress reviews with your coach and line manager"
      userName={learner?.name || 'Learner'}
      userRole={learner?.programme ? `${learner.programme} Learner` : 'Learner'}
    >
      <div className={`page-container ${styles.page} min-w-0 w-full space-y-3 p-3 md:space-y-4 md:p-6`}>
        {error && <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700"><AppIcon className="ri-error-warning-line mr-2" />{error}<button type="button" onClick={refresh} className="ml-3 font-bold underline">Try again</button></div>}
        {booking.notice}
        {booking.dialog}
        {attendance.error && <p role="alert" className="rounded-xl bg-red-50 p-4 text-base text-red-700">{attendance.error}<button type="button" onClick={attendance.refresh} className="ml-2 min-h-12 underline">Retry attendance</button></p>}

        <button type="button" onClick={() => navigate(reviewsListHref(myLearner, listParams))} className="inline-flex h-9 items-center gap-2 self-start rounded-xl border border-primary-200 bg-primary-50 px-3.5 text-xs font-bold text-primary-700 shadow-sm transition hover:-translate-x-0.5 hover:bg-primary-100">
          <AppIcon className="ri-arrow-left-line" /> Back to Reviews
        </button>

        <section className="learner-super-admin-hero relative overflow-hidden rounded-3xl p-5 text-primary-800 sm:p-6 workspace-page-hero">
          <div className="pointer-events-none absolute -right-24 -top-28 h-80 w-80 rounded-full bg-secondary-300/15 blur-3xl hidden"></div>
          <div className="relative flex flex-col gap-5 xl:flex-row xl:items-center xl:justify-between">
            <div className="flex items-start gap-4">
              <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-primary-200/60 bg-primary-100/60 text-xl text-primary-600 shadow-lg"><AppIcon className="ri-team-line" /></span>
              <div>
                <div className="mb-1.5 flex flex-wrap items-center gap-2">
                  <span className="text-[10px] font-bold uppercase tracking-[0.14em] text-primary-600">Formal review</span>
                  <span className="rounded-full border border-primary-200/60 bg-primary-100/60 px-2.5 py-1 text-[10px] font-bold text-foreground-500">{loading ? 'Loading...' : learner?.programme || '-'}</span>
                </div>
                <h1 className="font-heading text-xl font-bold text-primary-800 sm:text-2xl">Your review record</h1>
                <p className="mt-1 max-w-2xl text-sm leading-6 text-foreground-500">Each review brings together you, your coach and line manager to discuss learning, progress and next actions.</p>
              </div>
            </div>
            <div className="grid grid-cols-3 gap-2 sm:min-w-[390px]">
              {([
                { label: 'Completed', value: loading ? '-' : completed.length, color: 'text-emerald-600', icon: 'ri-checkbox-circle-line' },
                { label: 'Planned', value: loading ? '-' : planned.length, color: 'text-primary-600', icon: 'ri-calendar-event-line' },
                { label: 'All Reviews', value: loading ? '-' : reviews.length, color: 'text-foreground-900', icon: 'ri-stack-line' },
              ] as const).map(({ label, value, color, icon }) => (
                <div key={label} className="rounded-2xl border border-white/[0.08] bg-white/[0.07] px-3 py-3 text-center backdrop-blur">
                  <AppIcon className={`${icon} ${color === 'text-foreground-900' ? 'text-secondary-200' : color.replace('600', '300')} text-sm`} />
                  <p className="mt-0.5 text-xl font-bold text-primary-800">{value}</p>
                  <p className="text-[9px] font-semibold uppercase tracking-wider text-foreground-500">{label}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        {loading ? (
          <section className="rounded-2xl border border-background-200 bg-background-50 p-10 text-center text-sm text-foreground-400">Loading progress reviews...</section>
        ) : !selected ? (
          <section className="rounded-2xl border border-background-200 bg-background-50 p-6"><Empty>{reviewId ? 'This progress review is no longer available. Return to Progress Review to choose a current session.' : 'No progress reviews have been created for this learner.'}</Empty></section>
        ) : (
          <div>
            <main className="space-y-4">
              <section className="overflow-hidden rounded-3xl border border-background-200 bg-background-50 shadow-[0_10px_35px_rgba(25,12,50,0.07)]">
                <div className="learner-super-admin-hero relative overflow-hidden border-b border-primary-200/60 p-5 text-primary-800 sm:p-6 workspace-page-hero">
                  <div className="pointer-events-none absolute -right-16 -top-20 h-56 w-56 rounded-full bg-secondary-400/10 blur-3xl hidden"></div>
                  <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                    <div className="relative">
                      <span className={`inline-flex rounded-full border border-white/15 bg-white/10 px-2.5 py-1 text-[10px] font-bold text-white/80`}>{statusLabel(selected?.status)}</span>
                      <h2 className="mt-2 text-xl font-bold text-primary-800">{progressReviewTitle(selected)}</h2>
                      <p className="mt-1 text-sm text-foreground-500">{formatDate(reviewDate(selected), true)} at {formatTime(selected?.scheduledTime)}</p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {canBook && <button type="button" disabled={!canProgress || reviewInstance.loading} onClick={() => booking.openBooking(selected)} className={`${styles.primaryButton} inline-flex items-center rounded-lg px-4 py-2.5 text-xs font-extrabold disabled:opacity-50`}><AppIcon className="ri-calendar-line mr-1.5" />Book a time</button>}
                      {canReschedule && <button type="button" disabled={!canProgress || !attendance.data || Boolean(attendance.error) || reviewInstance.loading || Boolean(reviewInstance.error)} onClick={() => booking.openBooking(selected)} className={`${styles.primaryButton} inline-flex items-center rounded-lg px-4 py-2.5 text-xs font-extrabold disabled:opacity-50`}><AppIcon className="ri-calendar-line mr-1.5" />Reschedule meeting</button>}
                      {selected?.meetingLink && <a href={selected.meetingLink} target="_blank" rel="noopener noreferrer" className={`${styles.primaryButton} meeting-join-action inline-flex items-center rounded-lg px-4 py-2.5 text-xs font-extrabold`}><AppIcon className="ri-video-chat-line mr-1.5" />Join meeting</a>}
                      {selected.importedReview && <ReviewPdfDownload availability={{ available: true, reason: '' }} label="Download PDF" onDownload={() => downloadLearnerMcmPdf(myLearner.kind, myLearner.id, selected.eventKey || selected.id)} />}
                      {!selected.importedReview && <button type="button" onClick={showSlides} className="inline-flex items-center rounded-lg border border-primary-200/60 bg-white px-3.5 py-2 text-xs font-bold text-primary-900 shadow-sm hover:bg-primary-50 disabled:opacity-60"><AppIcon className="ri-slideshow-line mr-1.5" />Show slides</button>}
                      {!selected.importedReview && <button type="button" onClick={addToCalendar} disabled={!selected?.scheduledDate || !selected.scheduledTime} className="rounded-lg border border-primary-200/60 bg-primary-100/60 px-3.5 py-2 text-xs font-bold text-primary-800 disabled:cursor-not-allowed disabled:opacity-40"><AppIcon className="ri-calendar-check-line mr-1.5" />Add to calendar</button>}
                    </div>
                  </div>
                </div>

                <div className="space-y-5 p-5 sm:p-6">
                  {shouldShowLearnerMeetingPanel(selected) ? (
                    <CoachMeetingArtifactsPanel event={{ ...selected, eventKey: selected.calendarEventKey || selected.eventKey || selected.id }} storedOnly={selected.reviewSource === 'aptem'} fetchArtifacts={loadArtifacts} contentUrl={artifactContentUrl} showAttendance={false} visibleArtifactTypes={['recording']} className="border-primary-100 bg-primary-50/30" />
                  ) : null}

                  {selected && !selected.importedReview && !instanceBacked && selected.learnerSigned && ['awaiting-signature', 'completed'].includes(selected.status) ? (
                    <div className="flex flex-col gap-3 rounded-xl border border-violet-200 bg-violet-50 p-4 sm:flex-row sm:items-center">
                      <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-violet-100 text-violet-700"><AppIcon className="ri-checkbox-circle-line" /></span>
                      <div className="flex-1"><p className="text-sm font-bold text-violet-950">Slides signed by learner</p><p className="mt-1 text-xs text-violet-700">{`Signed ${selected.learnerSignedAt ? formatDate(selected.learnerSignedAt.split('T')[0]) : ''}`}</p></div>
                    </div>
                  ) : null}
                  <div>
                    <p className="mb-3 text-[10px] font-bold uppercase tracking-[0.14em] text-foreground-400">Review participants</p>
                    <div className="grid gap-3 sm:grid-cols-3">
                      <PersonCard role="Learner" name={learner?.name} icon="ri-user-line" tone="bg-primary-100 text-primary-700" />
                      <PersonCard role="Coach" name={selected?.coachName} icon="ri-user-star-line" tone="bg-amber-100 text-amber-700" />
                      <PersonCard role="Line manager" name={learner?.lineManager} icon="ri-briefcase-line" tone="bg-emerald-100 text-emerald-700" />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                    {[
                      ['Duration', selected?.durationMinutes ? `${selected.durationMinutes} minutes` : '-', 'ri-time-line'],
                      ['Meeting type', selected?.meetingProvider || '-', 'ri-video-chat-line'],
                      ['Scheduled time', formatTime(selected?.scheduledTime), 'ri-calendar-schedule-line'],
                      ['Review window', previousReview ? `Since ${progressReviewTitle(previousReview)}` : 'Programme to date', 'ri-history-line'],
                    ].map(([label, value, icon]) => (
                      <div key={label} className="rounded-xl bg-background-100 p-3.5">
                        <AppIcon className={`${icon} text-primary-500`} />
                        <p className="mt-2 text-[9px] font-semibold uppercase tracking-wider text-foreground-400">{label}</p>
                        <p className="mt-0.5 text-xs font-bold text-foreground-800">{value}</p>
                      </div>
                    ))}
                  </div>
                </div>
              </section>

              {reviewInstance.loading ? <div className="rounded-xl border border-background-200 bg-white p-5"><RowsSkeleton rows={4} /></div> : reviewInstance.error ? <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">{reviewInstance.error}<button type="button" onClick={reviewInstance.refresh} className="ml-2 underline">Retry review</button></p> : reviewInstance.definition ? <LearnerReviewInstanceForm definition={reviewInstance.definition} onDownload={migratedEvent ? () => downloadMigratedReviewForParty(selected.formEventKey || selected.eventKey || selected.id) : undefined} onSign={canProgress ? saveSignature : undefined} signatoryName={learner?.name || 'Learner'} onSaveAnswers={migratedEvent ? (canProgress ? (answers, version) => saveMigratedReviewPartyAnswers(selected.formEventKey || selected.eventKey || selected.id, answers, version) : undefined) : answers => saveLearnerEventReviewAnswers(myLearner.kind, myLearner.id, selected.eventKey || selected.id, answers)} /> : instanceBacked ? <p className="p-4 text-sm text-foreground-500">This review form is not available.</p> : <>
              {selected.importedReview ? <div className="space-y-3">
                {selected.importedReview.sections.map((section, index) => <Accordion key={section.id} id={`imported-section:${section.id}`} title={section.name.replace(/\s+(completed|incomplete)$/i, '').trim()} icon="ri-file-list-3-line" open={openSections.includes(`imported-section:${section.id}`) || (!openSections.some((id) => id.startsWith('imported-section:')) && index === 0)} onToggle={toggleSection}>
                  <ImportedReviewSections review={{ ...selected.importedReview, sections: [section] }} />
                </Accordion>)}
                {!selected.importedReview.sections.length && <Empty>Detailed questions and answers have not been imported for this review yet.</Empty>}
              </div> : <>
              <Accordion id="progress-checks" title="Progress Checks" icon="ri-check-double-line" open={openSections.includes('progress-checks')} onToggle={toggleSection}>
                <SavedReviewAnswers sectionId="progress-checks" responses={selected?.reviewResponses} emptyMessage="No progress checks have been recorded for this Progress Review." />
              </Accordion>
              <Accordion id="learner-reflection" title="Learner Reflections & Ratings" icon="ri-user-heart-line" open={openSections.includes('learner-reflection')} onToggle={toggleSection}><SavedReviewAnswers sectionId="learner-reflection" responses={selected?.reviewResponses} emptyMessage="Learner reflection data is not available for this Progress Review." /></Accordion>
              <Accordion id="manager-reflection" title="Manager Reflections & Ratings" icon="ri-briefcase-line" open={openSections.includes('manager-reflection')} onToggle={toggleSection}><SavedReviewAnswers sectionId="manager-reflection" responses={selected?.reviewResponses} emptyMessage="Manager reflection data is not available for this Progress Review." /></Accordion>
              <Accordion id="tutor-reflection" title="Tutor Reflections & Ratings" icon="ri-user-star-line" open={openSections.includes('tutor-reflection')} onToggle={toggleSection}><SavedReviewAnswers sectionId="tutor-reflection" responses={selected?.reviewResponses} emptyMessage="Tutor reflection data is not available for this Progress Review." /></Accordion>
              <Accordion id="safeguarding" title="Safeguarding & Key Themes" icon="ri-shield-check-line" open={openSections.includes('safeguarding')} onToggle={toggleSection}><SavedReviewAnswers sectionId="safeguarding" responses={selected?.reviewResponses} emptyMessage="No safeguarding or key-theme discussion has been recorded for this Progress Review." /></Accordion>
              <Accordion id="additional-support" title="Additional Support" icon="ri-hand-heart-line" open={openSections.includes('additional-support')} onToggle={toggleSection}><SavedReviewAnswers sectionId="additional-support" responses={selected?.reviewResponses} emptyMessage="No additional support information has been recorded for this Progress Review." /></Accordion>
              <Accordion id="actions" title="Progress Targets & Actions" icon="ri-focus-3-line" open={openSections.includes('actions')} onToggle={toggleSection}><SavedReviewAnswers sectionId="actions" responses={selected?.reviewResponses} emptyMessage="No targets or actions have been recorded for this Progress Review." /></Accordion>
              <Accordion id="rag" title="RAG Status" icon="ri-traffic-light-line" open={openSections.includes('rag')} onToggle={toggleSection}>
                <div className="grid gap-3 sm:grid-cols-3">
                  <div className="rounded-xl bg-background-100 p-4"><p className="text-[10px] font-semibold uppercase tracking-wider text-foreground-400">OTJH status</p><p className="mt-1 text-base font-bold text-foreground-900">{learner?.otjhStatus || '-'}</p></div>
                  <div className="rounded-xl bg-background-100 p-4"><p className="text-[10px] font-semibold uppercase tracking-wider text-foreground-400">Progress variance</p><p className="mt-1 text-base font-bold text-foreground-900">{progressVariance === null ? '-' : `${Math.round(progressVariance * 100)}%`}</p></div>
                  <div className="rounded-xl bg-background-100 p-4"><p className="text-[10px] font-semibold uppercase tracking-wider text-foreground-400">Coach RAG</p><p className="mt-1 text-base font-bold text-foreground-900">{selected?.reviewResponses?.rag_status || '-'}</p></div>
                </div>
                {responsesForSection(selected?.reviewResponses, 'rag').length > 0 && <div className="mt-4"><SavedReviewAnswers sectionId="rag" responses={selected?.reviewResponses} emptyMessage="No RAG assessment has been recorded for this Progress Review." /></div>}
              </Accordion>
              </>}
                  </>}
            </main>
          </div>
        )}
      </div>
      {showSlidesModal && selected && (
        <ProgressReviewPptxModal
          open kind="progress_review" access="viewer"
          target={{
            learnerId: myLearner.id, meetingDate: reviewDate(selected) || '', learnerName: learner?.name,
            programme: learner?.programme, completed: selected.status === 'completed',
            periodLabel: selected.sequence ? `Review ${selected.sequence}` : undefined,
          }}
          onClose={() => setShowSlidesModal(false)}
        />
      )}
    </WorkspaceShell>
  );
}
