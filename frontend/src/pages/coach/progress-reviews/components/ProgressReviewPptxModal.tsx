import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { AppIcon } from '@/components/feature/AppIcon';
import { useToast } from "@/hooks/useToast.helpers";
import {
  fetchLatestRun,
  fetchMcmLatestRun,
  fetchMcmPack,
  fetchProgressReviewDownloadUrl,
  fetchReviewPack,
  generateMcm,
  generateProgressReview,
  publishProgressReview,
  uploadOwnDeck,
  type ProgressReviewGenerateResult,
  type ProgressReviewPack,
} from '@/api/progressReviews';
import DeckEditor from './DeckEditor';
import PptxSlidesViewer from './PptxSlidesViewer';
import { formatDateLabel } from '../../shared/calendarEvents';
import type { SlidesDeckTarget } from './slidesTarget';

type Phase = 'loading' | 'idle' | 'working' | 'ready' | 'failed' | 'error';
type RevisionSource = 'generated' | 'edited' | 'uploaded';

/** The deck version the modal is showing. */
interface DeckVersion {
  reviewId: string;
  status: 'draft' | 'completed';
  source: RevisionSource;
  at: string | null;
}

const SOURCE_LABEL: Record<RevisionSource, string> = {
  generated: 'Generated from LMS data',
  edited: 'Edited',
  uploaded: 'Uploaded from PowerPoint',
};

function str(value: unknown, fallback = 'Not available'): string {
  if (value === null || value === undefined || value === '') return fallback;
  return String(value);
}

function pctLabel(value: unknown): string {
  if (value === null || value === undefined || value === '' || value === 'Not available') return 'Not available';
  return typeof value === 'number' ? `${value}%` : String(value);
}

function whenLabel(iso: string | null): string {
  if (!iso) return '';
  const at = new Date(iso);
  return Number.isNaN(at.getTime())
    ? ''
    : at.toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

const buttonBase = 'inline-flex h-10 items-center justify-center gap-2 rounded-lg px-4 text-[12px] font-semibold transition disabled:cursor-not-allowed disabled:opacity-50';
const primaryButton = `${buttonBase} bg-foreground-950 text-white hover:bg-foreground-800`;
const secondaryButton = `${buttonBase} border border-foreground-200 bg-white text-foreground-700 hover:bg-background-100`;
const accentButton = `${buttonBase} border border-primary-200 bg-primary-50 text-primary-700 hover:bg-primary-100`;
const toolButton = 'inline-flex h-9 items-center gap-1.5 rounded-lg px-2.5 text-[12px] font-semibold text-foreground-600 transition hover:bg-background-100 hover:text-foreground-900 disabled:opacity-50';

export default function ProgressReviewPptxModal({
  open,
  target,
  onClose,
  onGenerated,
  kind = 'progress_review',
  access = 'owner',
}: {
  /** 'mcm' renders the Monthly Coaching Meeting deck for the month up to the meeting. */
  kind?: 'progress_review' | 'mcm';
  /** The deck's owner creates, edits and replaces it (the learner for an MCM,
   * the coach for a Progress Review); a viewer can only open and download it. */
  access?: 'owner' | 'viewer';
  open: boolean;
  target: SlidesDeckTarget | null;
  onClose: () => void;
  /** Lets the caller's own button relabel to "Slides" after a draft is saved. */
  onGenerated?: () => void;
}) {
  const { success, error: toastError } = useToast();
  const [phase, setPhase] = useState<Phase>('loading');
  const [pack, setPack] = useState<ProgressReviewPack | null>(null);
  const [packError, setPackError] = useState<string | null>(null);
  const [deck, setDeck] = useState<DeckVersion | null>(null);
  const [workLabel, setWorkLabel] = useState('');
  const [actionError, setActionError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [viewing, setViewing] = useState(false);
  const [editing, setEditing] = useState(false);
  const uploadRef = useRef<HTMLInputElement>(null);

  const learnerId = target?.learnerId || '';
  const meetingDate = target?.meetingDate || '';
  const isMcm = kind === 'mcm';
  const isOwner = access === 'owner';
  const deckLabel = isMcm ? 'Monthly Coaching Meeting' : 'Progress Review';

  // Opening the modal only looks the deck up — an existing deck is shown as
  // it is and nothing is generated until the owner asks for it.
  useEffect(() => {
    if (!open || !learnerId || !meetingDate) return;
    let cancelled = false;
    setPhase('loading');
    setPack(null);
    setPackError(null);
    setDeck(null);
    setActionError(null);
    setViewing(false);
    setEditing(false);

    Promise.all([
      isMcm ? fetchMcmPack(learnerId, meetingDate) : fetchReviewPack(learnerId, meetingDate),
      (isMcm
        ? fetchMcmLatestRun(learnerId, meetingDate, { includeDraft: isOwner })
        : fetchLatestRun(learnerId, meetingDate, { includeDraft: isOwner })
      ).catch(() => ({ exists: false as const })),
    ])
      .then(([packData, latestRun]) => {
        if (cancelled) return;
        setPack(packData);
        if (latestRun.exists && latestRun.reviewId && (
          latestRun.generationStatus === 'completed' || (isOwner && latestRun.generationStatus === 'draft')
        )) {
          setDeck({
            reviewId: latestRun.reviewId,
            status: latestRun.generationStatus === 'draft' ? 'draft' : 'completed',
            source: latestRun.revisionSource || 'generated',
            at: latestRun.generatedAt || null,
          });
          setPhase('ready');
        } else {
          setPhase('idle');
        }
      })
      .catch((err) => {
        if (cancelled) return;
        setPackError(err instanceof Error ? err.message : 'Unable to load the review pack preview.');
        setPhase('error');
      });

    return () => { cancelled = true; };
  }, [open, learnerId, meetingDate, isMcm, isOwner]);

  if (!open || !target) return null;

  function showVersion(result: ProgressReviewGenerateResult, fallback: RevisionSource) {
    setDeck({
      reviewId: result.reviewId,
      status: result.generationStatus === 'draft' ? 'draft' : 'completed',
      source: result.revisionSource || fallback,
      at: new Date().toISOString(),
    });
    setPhase('ready');
  }

  async function run(label: string, work: () => Promise<ProgressReviewGenerateResult>, fallback: RevisionSource, done: string) {
    setWorkLabel(label);
    setPhase('working');
    setActionError(null);
    try {
      showVersion(await work(), fallback);
      success(done, `${deckLabel} slides for ${str(target?.learnerName, 'this learner')}.`);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Something went wrong. Please try again.');
      setPhase(deck ? 'ready' : 'failed');
    }
  }

  function handleGenerate() {
    if (deck && !window.confirm(
      'Generate a new draft from the latest LMS data? The saved version stays unchanged until you save this draft.',
    )) return;
    void run(
      'Generating slides from LMS data…',
      () => (isMcm ? generateMcm(learnerId, meetingDate) : generateProgressReview(learnerId, meetingDate)),
      'generated',
      deck ? 'Slides regenerated' : 'Slides generated',
    );
  }

  async function handlePublish() {
    if (!deck || deck.status !== 'draft') return;
    setWorkLabel('Saving slides...');
    setPhase('working');
    setActionError(null);
    try {
      const result = await publishProgressReview(deck.reviewId);
      setDeck({ ...deck, status: 'completed', at: new Date().toISOString(), source: result.revisionSource || deck.source });
      setPhase('ready');
      onGenerated?.();
      success('Slides saved', `${deckLabel} slides for ${str(target?.learnerName, 'this learner')}.`);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'The slides could not be saved. Please try again.');
      setPhase('ready');
    }
  }

  function handleUpload(file: File | undefined) {
    if (!file) return;
    if (deck && !window.confirm(
      deck.status === 'draft'
        ? `Use "${file.name}" as the new draft? The saved version stays unchanged until you save.`
        : `Use "${file.name}" as a new draft? The current version stays unchanged until you save.`,
    )) return;
    void run('Uploading your presentation…', () => uploadOwnDeck(kind, learnerId, meetingDate, file), 'uploaded', 'Presentation uploaded');
  }

  async function handleDownload() {
    if (!deck) return;
    setDownloading(true);
    try {
      const url = await fetchProgressReviewDownloadUrl(deck.reviewId);
      window.open(url, '_blank', 'noopener,noreferrer');
    } catch (err) {
      toastError('Download failed', err instanceof Error ? err.message : 'Unable to fetch the download link.');
    } finally {
      setDownloading(false);
    }
  }

  // An edit or re-upload from the editor is a new version: show it straight away.
  function handleEdited(newReviewId: string) {
    setDeck({ reviewId: newReviewId, status: 'completed', source: 'edited', at: new Date().toISOString() });
    setEditing(false);
    setViewing(true);
    onGenerated?.();
    success('Slides updated', 'A new version was saved. The previous version is kept.');
  }

  const reviewSection = pack?.review as Record<string, unknown> | undefined;
  const warnings = pack?.source_warnings || [];
  const periodLabel = reviewSection
    ? `${formatDateLabel(str(reviewSection.review_period_start, ''))} to ${formatDateLabel(str(reviewSection.review_period_end, ''))}`
    : '';
  const otj = pack?.otj as Record<string, unknown> | undefined;
  const summaryCards: Array<[string, string]> = [
    ['Attendance', pctLabel((pack?.attendance as Record<string, unknown>)?.attendance_percentage)],
    ['Programme Progress', pctLabel((pack?.progress as Record<string, unknown>)?.current_programme_progress_percentage)],
    ['OTJ Status', str(otj?.risk_status)],
    isMcm
      ? ['OTJ Hours', otj?.completed_otj_hours === undefined || otj?.completed_otj_hours === 'Not available' ? 'Not available' : `${otj.completed_otj_hours}h`]
      : ['EPA Readiness', pctLabel((pack?.epa as Record<string, unknown>)?.current_readiness)],
  ];
  const busy = phase === 'working';

  // Keep fixed overlays outside transformed table rows and scrolling containers.
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-[rgba(10,12,24,0.6)] backdrop-blur-sm" onClick={busy ? undefined : onClose} />
      <div role="dialog" aria-label={`${deckLabel} slides`} className="relative flex max-h-[90vh] w-full max-w-[680px] flex-col overflow-hidden rounded-2xl border border-white/10 bg-white shadow-2xl">
        <header className="border-b border-foreground-200 px-6 py-5">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="text-lg font-semibold text-foreground-950">{deckLabel} Slides</h2>
              <p className="mt-1 truncate text-sm font-medium text-foreground-800">
                {str(target.learnerName)} · {str(target.programme)}
              </p>
              <p className="mt-0.5 text-[12px] text-foreground-500">
                {isMcm ? 'Month reviewed' : target.periodLabel}{periodLabel ? ` · ${periodLabel}` : ` · ${formatDateLabel(meetingDate)}`}
              </p>
            </div>
            <button type="button" onClick={onClose} disabled={busy} aria-label="Close" title="Close" className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-lg text-foreground-500 hover:bg-background-100 hover:text-foreground-900 disabled:opacity-50">
              <AppIcon className="ri-close-line" />
            </button>
          </div>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto px-6 py-5">
          {phase === 'loading' ? (
            <div className="flex items-center gap-2 py-8 text-sm text-foreground-500">
              <AppIcon className="ri-loader-4-line animate-spin" /> Loading…
            </div>
          ) : packError ? (
            <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{packError}</div>
          ) : (
            <>
              {/* Where the slides stand — the first thing the user needs to know. */}
              {deck ? (
                <div className={`flex items-center gap-3 rounded-xl border px-4 py-3 ${deck.status === 'draft' ? 'border-amber-200 bg-amber-50' : 'border-emerald-200 bg-emerald-50'}`}>
                  <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-lg ${deck.status === 'draft' ? 'bg-amber-100 text-amber-700' : 'bg-emerald-100 text-emerald-700'}`}><AppIcon className="ri-slideshow-2-line" /></span>
                  <div className="min-w-0">
                    <p className={`text-sm font-semibold ${deck.status === 'draft' ? 'text-amber-900' : 'text-emerald-900'}`}>
                      {deck.status === 'draft' ? 'Draft ready — not saved' : 'Slides ready'}
                    </p>
                    <p className={`text-[12px] ${deck.status === 'draft' ? 'text-amber-800' : 'text-emerald-800'}`}>{SOURCE_LABEL[deck.source]}{whenLabel(deck.at) ? ` · ${whenLabel(deck.at)}` : ''}</p>
                  </div>
                </div>
              ) : phase !== 'working' && (
                <div className="rounded-xl border border-dashed border-foreground-300 bg-background-50 px-4 py-3">
                  <p className="text-sm font-semibold text-foreground-900">No slides yet</p>
                  <p className="text-[12px] text-foreground-600">
                    {isOwner
                      ? 'Generate them from the learner’s LMS data, or upload your own PowerPoint.'
                      : isMcm ? 'The learner has not created their slides for this meeting yet.' : 'No slides have been created for this review yet.'}
                  </p>
                </div>
              )}

              {busy && (
                <div className="flex items-center gap-2 rounded-xl border border-foreground-200 bg-background-50 px-4 py-3 text-sm text-foreground-700">
                  <AppIcon className="ri-loader-4-line animate-spin" /> {workLabel}
                </div>
              )}

              {actionError && (
                <div role="alert" className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{actionError}</div>
              )}

              <div className="grid grid-cols-2 gap-3">
                {summaryCards.map(([label, value]) => (
                  <div key={label} className="rounded-lg border border-foreground-200 bg-white px-3 py-2.5">
                    <p className="text-[10px] font-bold uppercase tracking-wide text-foreground-400">{label}</p>
                    <p className="mt-1 text-sm font-semibold text-foreground-900">{value}</p>
                  </div>
                ))}
              </div>

              {warnings.length > 0 && (
                <details open={!deck} className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5">
                  <summary className="cursor-pointer text-[11px] font-bold uppercase tracking-wide text-amber-700">
                    Missing data ({warnings.length})
                  </summary>
                  <ul className="mt-1.5 list-disc space-y-1 pl-4 text-[12px] leading-snug text-amber-800">
                    {warnings.map((warning) => <li key={warning}>{warning}</li>)}
                  </ul>
                  <p className="mt-1.5 text-[11px] text-amber-700/80">These are gaps in the source data — they don’t stop the slides being created, and you can fill them in with Edit.</p>
                </details>
              )}
            </>
          )}
        </div>

        {phase !== 'loading' && !packError && (
          <footer className="flex flex-wrap items-center gap-2 border-t border-foreground-200 px-6 py-4">
            <input
              ref={uploadRef} type="file" className="hidden"
              accept=".pptx,application/vnd.openxmlformats-officedocument.presentationml.presentation"
              onChange={(e) => { handleUpload(e.target.files?.[0]); e.target.value = ''; }}
            />
            {/* The owner's tools sit apart from the everyday actions on the right. */}
            {isOwner && deck && (
              <div className="flex flex-wrap items-center gap-1">
                <button type="button" className={toolButton} disabled={busy} onClick={() => setEditing(true)}>
                  <AppIcon className="ri-edit-2-line" /> Edit
                </button>
                <button type="button" className={toolButton} disabled={busy} onClick={() => uploadRef.current?.click()}>
                  <AppIcon className="ri-upload-2-line" /> Upload your own
                </button>
                <button type="button" className={toolButton} disabled={busy} onClick={handleGenerate}>
                  <AppIcon className="ri-refresh-line" /> Regenerate
                </button>
              </div>
            )}
            <div className="ml-auto flex flex-wrap items-center gap-2">
              {deck ? (
                <>
                  <button type="button" className={accentButton} disabled={busy} onClick={() => setViewing(true)}>
                    <AppIcon className="ri-slideshow-2-line" /> {deck.status === 'draft' ? 'View draft' : 'View slides'}
                  </button>
                  <button type="button" className={primaryButton} disabled={busy || downloading} onClick={handleDownload}>
                    <AppIcon className={downloading ? 'ri-loader-4-line animate-spin' : 'ri-download-line'} />
                    {downloading ? 'Fetching link…' : deck.status === 'draft' ? 'Download draft' : 'Download PPTX'}
                  </button>
                  {isOwner && deck.status === 'draft' && (
                    <button type="button" className={primaryButton} disabled={busy} onClick={() => { void handlePublish(); }}>
                      <AppIcon className="ri-save-3-line" /> Save slides
                    </button>
                  )}
                </>
              ) : isOwner ? (
                <>
                  <button type="button" className={secondaryButton} disabled={busy} onClick={() => uploadRef.current?.click()}>
                    <AppIcon className="ri-upload-2-line" /> Upload your own
                  </button>
                  <button type="button" className={primaryButton} disabled={busy} onClick={handleGenerate}>
                    <AppIcon className={busy ? 'ri-loader-4-line animate-spin' : 'ri-slideshow-line'} />
                    {phase === 'failed' ? 'Try again' : 'Generate PPTX'}
                  </button>
                </>
              ) : (
                <button type="button" className={secondaryButton} onClick={onClose}>Close</button>
              )}
            </div>
          </footer>
        )}
      </div>

      {viewing && deck && (
        <PptxSlidesViewer
          reviewId={deck.reviewId}
          title={`${deckLabel} · ${str(target.learnerName)}`}
          onClose={() => setViewing(false)}
          onDownload={handleDownload}
          onEdit={isOwner ? () => { setViewing(false); setEditing(true); } : undefined}
        />
      )}

      {editing && deck && (
        <DeckEditor
          reviewId={deck.reviewId}
          title={`Edit ${deckLabel} slides · ${str(target.learnerName)}`}
          onClose={() => setEditing(false)}
          onSaved={handleEdited}
        />
      )}
    </div>,
    document.body,
  );
}
