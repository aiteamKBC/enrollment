import { lazy, Suspense, useEffect, useId, useMemo, useRef, useState } from 'react';
import DOMPurify from 'dompurify';
import { VideoPlayer, parseVideoUrl } from '@/components/feature/VideoPlayer';
import { SlideDeckViewer } from '@/components/feature/SlideDeckViewer';
import { resolveDocEmbed } from '@/lib/docEmbed';
import { normalizeReadingHtml } from '@/lib/readingHtml';
import { previewUrl } from '@/features/old-otjh/previewUrl';
import { subjectRequest, type SubjectMaterial, type SubjectAttemptResult } from '@/api/studentActivity';
import { RetakeTimeDialog, type RetakeTimeChoice } from '@/components/feature/RetakeTimeDialog';
import type { TimeTrackingSession } from '@/api/timeTracking';
import { CompletionTimeDialog } from '@/components/feature/CompletionTimeDialog';
import type { WorkingRuleReason } from '@/lib/completionValidation';
import { useLegacyStudySession } from './useLegacyStudySession';

const AttachmentPreview = lazy(() => import('../video-watch/page').then((module) => ({ default: module.InlineAttachmentPreview })));

function studyClock(seconds: number) {
  const total = Math.floor(seconds);
  return [Math.floor(total / 3600), Math.floor(total / 60) % 60, total % 60].map(value => String(value).padStart(2, '0')).join(':');
}

function previousSelections(data: SubjectMaterial): Record<string, string[]> {
  const text = (value: string) => new DOMParser().parseFromString(value, 'text/html').body.textContent?.replace(/\s+/g, ' ').trim() || '';
  // The API orders submitted attempts newest first. Never mix attempts or
  // reuse positional option IDs against a potentially changed question bank.
  const latest = data.history[0];
  const selections: Record<string, string[]> = {};
  for (const question of data.quiz?.questions || []) {
    const matches = latest
      ? (latest.answer_review || []).filter((answer) => text(answer.question) === text(question.text))
        .map((answer) => answer.selected)
      : data.historical.answers.filter((answer) => String(answer.question_id) === question.id && text(answer.question_body) === text(question.text))
        .map((answer) => Array.isArray(answer.learner_answer) ? answer.learner_answer : [answer.learner_answer]);
    if (matches.length !== 1 || !matches[0].length) continue;
    const ids: string[] = [];
    for (const selected of matches[0]) {
      if (typeof selected !== 'string') break;
      const options = question.options.filter((option) => text(option.text) === text(selected));
      if (options.length !== 1) break;
      ids.push(options[0].id);
    }
    if (ids.length === matches[0].length) selections[question.id] = ids;
  }
  return selections;
}

function Html({ value }: { value: string }) {
  // Memoised so a parent re-render (ticking a checkbox, picking a quiz answer)
  // keeps the same innerHTML object: React 19 re-applies innerHTML on a new
  // identity, which would restart any embedded <video>.
  const { innerHtml, embeds } = useMemo(() => {
    const normalized = normalizeReadingHtml(value);
    const clean = DOMPurify.sanitize(normalized, { FORBID_TAGS: ['form'], FORBID_ATTR: ['srcdoc'] });
    const document = new DOMParser().parseFromString(normalized, 'text/html');
    return {
      innerHtml: { __html: clean },
      embeds: [...document.querySelectorAll('iframe')].map((frame) => frame.getAttribute('src') || '').filter((url) => /^https?:\/\//i.test(url)),
    };
  }, [value]);
  return <div className="space-y-4"><div className="prose max-w-none break-words" dangerouslySetInnerHTML={innerHtml} />
    {embeds.map((url, index) => <Media key={`${index}:${url}`} value={url} kind="embed" title={`Embedded content ${index + 1}`} />)}
  </div>;
}

export function Media({ value, kind, title, fileName, canEmbed = true, onEnded, onPlayingChange }: {
  value: string; kind: string; title: string; fileName?: string; canEmbed?: boolean; onEnded?: () => void; onPlayingChange?: (playing: boolean) => void;
}) {
  let url: URL;
  try { url = new URL(value, window.location.origin); } catch { return <p>Material link is unavailable.</p>; }
  if (!['https:', 'http:'].includes(url.protocol)) return <p>Material link is unavailable.</p>;
  const fileUrl = url.href;
  const storedFile = url.origin === window.location.origin
    && (/^\/learner_api\/student-activity\/[^/]+\/\d+\/\d+\/\d+\/source-file\/$/.test(url.pathname)
      || /^\/login_api\/advanced-admin\/learners\/\d+\/learning\/material\/\d+\/\d+\/source-file\/$/.test(url.pathname));
  const original = <a href={fileUrl} target="_blank" rel="noopener noreferrer" className="text-sm font-semibold text-primary-700 underline">Open material in a new tab</a>;
  if (!canEmbed) return <div className="rounded-xl border bg-background-100 p-4"><p className="mb-2 text-sm">Open this material in a new tab to view it.</p>{original}</div>;
  // Legacy type labels can describe the lesson rather than its linked media.
  // Provider pages need their embedded player, even in a reading/audio lesson.
  const spotify = url.hostname === 'open.spotify.com';
  const providerVideo = ['youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be',
    'vimeo.com', 'www.vimeo.com', 'player.vimeo.com'].includes(url.hostname);
  const providerUrl = spotify || providerVideo ? previewUrl(fileUrl) || fileUrl : fileUrl;
  const parsedVideo = parseVideoUrl(providerUrl);
  // Preserve private Vimeo access parameters already handled by previewUrl.
  if (providerVideo && parsedVideo.kind === 'vimeo') parsedVideo.src = providerUrl;
  if (!spotify && (kind === 'video' || providerVideo)) return <section className="space-y-2"><div className="relative aspect-video overflow-hidden rounded-xl bg-black"><VideoPlayer parsed={storedFile ? { kind: 'file', src: fileUrl } : parsedVideo} title={title} onEnded={onEnded} onPlayingChange={onPlayingChange} /></div>{original}</section>;
  if (kind === 'pdf') return <section className="space-y-2"><Suspense fallback={<p role="status">Loading file preview…</p>}><AttachmentPreview url={fileUrl} title={title} fileName={fileName || 'document.pdf'} /></Suspense>{original}</section>;
  if (kind === 'audio' && !spotify) {
    const drive = url.href.match(/drive\.google\.com\/(?:file\/d\/|(?:open|uc)\?[^#]*id=)([\w-]{10,})/);
    return <section className="space-y-2"><audio controls src={drive ? `/learner_api/media/google-drive/${drive[1]}/` : fileUrl}
      onPlaying={e => onPlayingChange?.(!e.currentTarget.muted && e.currentTarget.volume > 0)}
      onPause={() => onPlayingChange?.(false)} onSeeking={() => onPlayingChange?.(false)}
      onSeeked={e => onPlayingChange?.(!e.currentTarget.paused && !e.currentTarget.muted && e.currentTarget.volume > 0)}
      onVolumeChange={e => onPlayingChange?.(!e.currentTarget.paused && !e.currentTarget.muted && e.currentTarget.volume > 0)}
      onWaiting={() => onPlayingChange?.(false)} onEnded={() => { onPlayingChange?.(false); onEnded?.(); }} className="w-full" />{original}</section>;
  }
  if (storedFile && kind === 'document') {
    if (/\.(pptx?|ppsx?|pptm|ppsm|doc|docm)$/i.test(fileName || '')) {
      const preview = new URL(fileUrl);
      preview.searchParams.set('preview', '1');
      return <section className="space-y-2"><iframe title={title} src={preview.href} className="h-[65vh] min-h-[320px] w-full rounded-xl border bg-white" allowFullScreen />{original}</section>;
    }
    return <section className="space-y-2"><Suspense fallback={<p role="status">Loading file preview…</p>}><AttachmentPreview url={fileUrl} title={title} fileName={fileName} /></Suspense>{original}</section>;
  }
  if (url.origin === window.location.origin && /\.(docx|xlsx?|csv|txt|md|rtf|pdf|pptx|ppsx|pptm|ppsm)$/i.test(url.pathname)) return <section className="space-y-2"><Suspense fallback={<p role="status">Loading file preview…</p>}><AttachmentPreview url={url.href} title={title} /></Suspense>{original}</section>;
  let src = providerUrl;
  if (url.hostname === 'drive.google.com') src = src.replace(/\/view(?:\?.*)?$/, '/preview');
  else if (/\.(pdf|pptx|ppsx|pptm|docx?|xlsx?)$/i.test(url.pathname) || /docs\.google\.com/.test(url.hostname)) {
    const embed = resolveDocEmbed(url.href);
    if (embed.mode === 'deck') return <div className="space-y-2"><SlideDeckViewer src={embed.src} title={title} fallback={(reason) => <p>{reason}</p>} />{original}</div>;
    if (embed.mode === 'unavailable') return <div className="space-y-2"><p className="text-sm">{embed.reason}</p>{original}</div>;
    src = embed.src;
  }
  return <section className="space-y-2"><iframe title={title} src={src} className="h-[65vh] min-h-[320px] w-full rounded-xl border bg-white"
    sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-downloads allow-presentation" allow="autoplay; fullscreen; picture-in-picture" allowFullScreen />{original}</section>;
}

export function StudentMaterial({ kind, learnerId, groupId, activityId, completed = false, onProgress, readOnly = false, baseUrl, reviewQuiz = false, catalogueQuiz = false }: {
  kind: string; learnerId: string; groupId: number; activityId: number; completed?: boolean;
  onProgress?: (result: SubjectAttemptResult) => void; readOnly?: boolean; baseUrl?: string; reviewQuiz?: boolean; catalogueQuiz?: boolean;
}) {
  const [data, setData] = useState<SubjectMaterial | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [busy, setBusy] = useState(false);
  const [correction, setCorrection] = useState<{ reason: WorkingRuleReason | ''; holidayName: string } | null>(null);
  const [result, setResult] = useState('');
  const [savedResult, setSavedResult] = useState<SubjectAttemptResult | null>(null);
  const playingMedia = useRef(new Set<number>());
  const activityElement = useRef<HTMLDivElement>(null);
  const completionHintId = useId();
  const base = baseUrl || `/learner_api/student-activity/${encodeURIComponent(kind)}/${encodeURIComponent(learnerId)}`;
  const attempts = `${base}/${groupId}/${activityId}/attempts/`;
  const quizQuery = catalogueQuiz || reviewQuiz ? '?catalogue_kind=quiz' : '';
  const materialUrl = `${base}/?group_id=${groupId}&activity_id=${activityId}${catalogueQuiz || reviewQuiz ? '&catalogue_kind=quiz' : ''}`;
  const timerKey = `legacy-study:v1:${encodeURIComponent(kind)}:${encodeURIComponent(learnerId)}:${groupId}:${catalogueQuiz || reviewQuiz ? 'quiz' : 'material'}:${activityId}`;
  const currentIdentity = useRef(timerKey);
  currentIdentity.current = timerKey;
  const study = useLegacyStudySession(timerKey, !!data?.can_attempt && !!data?.available && !readOnly && !reviewQuiz
    && !((catalogueQuiz || !data?.quiz) && (completed || data?.completed || savedResult?.completed)), busy || !!correction, activityElement);
  const attemptId = study.draft?.attemptId || null;
  const answers = study.draft?.answers || {};
  const confirmed = study.draft?.confirmed || false;
  const { setAnswers, setConfirmed } = study;
  useEffect(() => {
    const controller = new AbortController();
    setData(null); setError(''); setResult(''); setSavedResult(null); setCorrection(null); setBusy(false);
    playingMedia.current.clear();
    void subjectRequest<SubjectMaterial>(materialUrl, { signal: controller.signal })
      .then((payload) => { if (!controller.signal.aborted) setData(payload); })
      .catch((failure: unknown) => { if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'Could not load material.'); });
    return () => controller.abort();
  }, [materialUrl, retry]);
  const post = <T,>(url: string, body: unknown) => subjectRequest<T>(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': data?.csrf_token || '' }, body: JSON.stringify(body) });
  const [retakeTimeOpen, setRetakeTimeOpen] = useState(false);
  const start = () => {
    if (data && (data.completed || data.history.length || data.historical.attempt_number || savedResult)) {
      setRetakeTimeOpen(true); return;
    }
    void beginAttempt();
  };
  const beginAttempt = async (retakeTimeChoice?: RetakeTimeChoice) => {
    if (readOnly || reviewQuiz || !data || busy || !data.can_attempt || !data.persistence_ready || !data.available || (data.quiz && !data.quiz.ready)) return;
    setBusy(true); setError(''); setResult('');
    try {
      const response = await post<{ attempt_id: string; definition: Partial<SubjectMaterial>; timing: TimeTrackingSession }>(`${attempts}${quizQuery}`, { track_time: true,
        ...(retakeTimeChoice ? { retakeTimeChoice } : {}) });
      if (currentIdentity.current !== timerKey) return;
      study.begin(response.attempt_id, response.timing);
      setData({ ...data, ...response.definition });
    } catch (failure) { if (currentIdentity.current === timerKey) setError(failure instanceof Error ? failure.message : 'Could not start the attempt.'); }
    finally { if (currentIdentity.current === timerKey) setBusy(false); }
  };
  const submit = async (declaredCompletedAt?: string) => {
    if (readOnly || reviewQuiz || !data || busy || !data.can_attempt || !data.persistence_ready || !data.available) return;
    setBusy(true); setError('');
    try {
      if (!attemptId || !study.draft) throw new Error('Start the activity before submitting.');
      const saved = await post<SubjectAttemptResult>(`${attempts}${attemptId}/${quizQuery}`, { answers, reading_confirmed: data.quiz ? confirmed : true,
        trackingToken: study.draft.timing.trackingToken, timeTakenSeconds: Math.floor(study.draft.elapsedSeconds),
        ...(declaredCompletedAt ? { declaredCompletedAt } : {}) });
      if (currentIdentity.current !== timerKey) return;
      setResult(saved.score_percent == null ? 'Activity completed.' : `Attempt saved: ${Number(saved.score_percent).toFixed(0)}% · ${saved.passed ? 'Passed' : 'Not passed'}. Your highest score is kept.`);
      setSavedResult(saved);
      study.clear(); setCorrection(null);
      onProgress?.(saved);
      try {
        const latest = await subjectRequest<SubjectMaterial>(materialUrl);
        if (currentIdentity.current === timerKey) setData(latest);
      } catch {
        setError('Your attempt was saved, but its history could not be refreshed.');
      }
    } catch (failure) {
      if (currentIdentity.current !== timerKey) return;
      if (failure && typeof failure === 'object' && 'validation' in failure && failure.validation) {
        setCorrection(failure.validation as { reason: WorkingRuleReason | ''; holidayName: string });
      }
      setError(failure instanceof Error ? failure.message : 'Could not save your attempt.');
    }
    finally { if (currentIdentity.current === timerKey) setBusy(false); }
  };
  if (!data && error) return <div role="alert" className="rounded-xl border border-red-200 p-4">{error} <button onClick={() => setRetry((v) => v + 1)} className="font-semibold underline">Try again</button></div>;
  if (!data) return <p role="status" className="p-5">Loading activity…</p>;
  const quiz = data.quiz;
  const displayedAnswers = attemptId ? answers : previousSelections(data);
  const reviewing = !attemptId && (data.history.length > 0 || data.historical.answers.length > 0);
  const isComplete = completed || data.completed || savedResult?.completed || data.history.some((attempt) => attempt.completed);
  const reviewOnly = readOnly || reviewQuiz || (catalogueQuiz && isComplete);
  const answered = quiz?.questions.every((question) => (answers[question.id]?.length || 0) > 0) ?? true;
  const needsConfirmation = !!quiz && data.has_reading;
  const blockedReason = !data.persistence_ready
    ? 'Saving progress is temporarily unavailable. Please contact your learning team.'
    : !data.available
      ? 'This activity has no available content to complete yet.'
      : quiz && !quiz.ready ? quiz.message : '';
  const actionsDisabled = busy || !data.can_attempt || !!blockedReason;
  const completionHint = blockedReason || (data.can_attempt
    ? quiz ? 'Complete the quiz and submit your answers. Passing records this activity as complete.' : isComplete ? 'This activity is complete and included in your progress.' : 'Select Start activity before studying. When finished, select Submit & complete to save your study time and completion.'
    : '');
  const actionClass = 'shrink-0 rounded-xl bg-primary-600 px-5 py-2.5 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50';
  return <div ref={activityElement} className="space-y-6 rounded-2xl border border-foreground-200 bg-white p-4 sm:p-6">
    <section aria-label="Activity completion" className="sticky top-3 z-10 space-y-3 rounded-xl border border-primary-100 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><h4 className="text-sm font-bold text-foreground-900">Activity progress</h4>
          <p aria-live="polite" className={`mt-1 text-sm font-semibold ${isComplete ? 'text-emerald-700' : 'text-foreground-500'}`}>{isComplete ? 'Complete' : 'Not complete'}</p>
        </div>
        {!reviewOnly && (quiz || !isComplete) && (!attemptId ? <button type="button" onClick={start} disabled={actionsDisabled} aria-describedby={completionHint ? completionHintId : undefined} className={actionClass}>{busy ? 'Starting…' : quiz ? isComplete || data.history.length || data.historical.attempt_number ? 'Try quiz again' : 'Start quiz' : 'Start activity'}</button>
          : <button type="button" onClick={() => void submit()} disabled={actionsDisabled || !answered || (needsConfirmation && !confirmed)} aria-describedby={completionHint ? completionHintId : undefined} className={actionClass}>{busy ? 'Saving…' : quiz ? 'Submit answers' : 'Submit & complete'}</button>
        )}
      </div>
      {!reviewOnly && (!isComplete || !!attemptId) && <div className="flex flex-wrap items-center gap-3 rounded-lg bg-primary-50 p-3 text-sm">
        <span>Study time <strong className="ml-2 font-mono tabular-nums" aria-label="Study time">{studyClock(study.draft?.elapsedSeconds || 0)}</strong></span>
        {attemptId && <button type="button" className="rounded-lg border border-primary-300 px-3 py-1 font-semibold" onClick={() => study.setPaused(!study.paused)}>{study.paused ? 'Resume timer' : 'Pause timer'}</button>}
        <span className="text-xs text-foreground-600">{!attemptId ? 'Start before studying to record your time.' : study.paused ? 'Timer paused.' : study.draft?.timing.countingMode === 'active_playback' ? 'Time counts during audible playback while this page is visible.' : 'Time counts while this page is visible.'}</span>
      </div>}
      {study.storageError && <p role="alert" className="text-sm text-amber-800">{study.storageError}</p>}
      {savedResult?.study_time && <p className="text-sm text-emerald-700">Study time saved: {Math.floor(savedResult.study_time.verifiedSeconds / 60)}m {savedResult.study_time.verifiedSeconds % 60}s</p>}
      {!reviewOnly && completionHint && <p id={completionHintId} className={`text-sm ${blockedReason ? 'text-amber-800' : 'text-foreground-500'}`}>{completionHint}</p>}
      {needsConfirmation && !!attemptId && <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={confirmed} disabled={actionsDisabled} onChange={(event) => setConfirmed(event.target.checked)} className="mt-1 accent-primary-600" />I have completed the reading material.</label>}
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      {result && <p role="status" className={`rounded-xl p-3 text-sm font-semibold ${savedResult?.completed ? 'bg-emerald-50 text-emerald-800' : 'bg-amber-50 text-amber-800'}`}>{result}</p>}
    </section>
    {!data.available && <p>No material is available for this activity yet.</p>}
    {data.media.map((item, index) => <Media key={`${index}:${item.url}`} value={item.url} kind={item.kind} title={item.title} fileName={item.file_name} canEmbed={item.can_embed} onPlayingChange={playing => {
      if (playing) playingMedia.current.add(index); else playingMedia.current.delete(index);
      study.setPlaying(playingMedia.current.size > 0);
    }} onEnded={() => setConfirmed(true)} />)}
    {data.reading_html && <Html value={data.reading_html} />}
    {data.unavailable_attachments?.map((name, index) => <p key={index} className="text-sm text-amber-800">Attachment unavailable: {name}</p>)}
    {quiz && <section className="space-y-4" aria-label="Activity quiz">
      <div className="flex flex-wrap items-center justify-between gap-3"><h4 className="text-lg font-bold">Quiz · {quiz.questions.length} {quiz.questions.length === 1 ? 'question' : 'questions'}</h4>{quiz.passing_percent != null && <p className="text-sm text-foreground-500">Pass mark: {Number(quiz.passing_percent).toFixed(0)}%</p>}</div>
      {quiz.body && <Html value={quiz.body} />}
      {reviewing && <p className="rounded-xl border border-primary-100 bg-primary-50 p-3 text-sm text-primary-800">{reviewOnly ? 'Previous answers are shown for review.' : 'Your previous answers are selected below for review. Choose Try quiz again to start a new attempt. Answers that no longer match the current questions remain in your attempt history.'}</p>}
      {!quiz.ready && <p role="status" className="rounded-xl bg-amber-50 p-3 text-sm text-amber-900">{quiz.message} Your previous results are kept.</p>}
      {quiz.questions.map((question, index) => <fieldset key={question.id} disabled={!attemptId || actionsDisabled || reviewOnly} className="space-y-3 rounded-xl border border-foreground-200 p-4">
        <legend className="px-2 text-sm font-bold">Question {index + 1}{question.type === 'multi_choice' ? ' · Select all that apply' : ''}</legend><Html value={question.text} />
        {question.options.map((option) => <label key={option.id} className={`flex ${attemptId ? 'cursor-pointer' : 'cursor-default'} items-start gap-3 rounded-lg border p-3 ${displayedAnswers[question.id]?.includes(option.id) ? 'border-primary-400 bg-primary-50' : 'border-foreground-200'}`}>
          <input type={question.type === 'multi_choice' ? 'checkbox' : 'radio'} name={`question-${question.id}`} value={option.id} checked={displayedAnswers[question.id]?.includes(option.id) || false}
            onChange={(event) => setAnswers((previous) => ({ ...previous, [question.id]: question.type === 'multi_choice' ? event.target.checked ? [...(previous[question.id] || []), option.id] : (previous[question.id] || []).filter((value) => value !== option.id) : [option.id] }))} className="mt-1 shrink-0 accent-primary-600" /><Html value={option.text} />
        </label>)}
      </fieldset>)}
    </section>}
    {reviewQuiz && data.historical.attempt_number == null && data.historical.score == null && !data.historical.answers.length && <p className="text-sm text-foreground-500">No saved attempt is available for this quiz.</p>}
    {(data.historical.answers.length > 0 || data.historical.score != null || data.history.length > 0) && <section className="space-y-3 border-t pt-4" aria-label="Attempt history">
      <h4 className="font-bold">Your attempt history</h4>
      {reviewQuiz && <p className="text-sm text-foreground-600">{[data.historical.attempt_number != null ? `Attempt ${data.historical.attempt_number}` : '', data.historical.passed === true ? 'Passed' : data.historical.passed === false ? 'Not passed' : ''].filter(Boolean).join(' · ')}</p>}
      {(data.historical.answers.length > 0 || data.historical.score != null) && <details className="rounded-xl border p-3"><summary className="cursor-pointer text-sm font-semibold">Previous learning{data.historical.score != null ? ` · ${data.historical.score}${data.historical.maximum_score ? ` / ${data.historical.maximum_score}` : ''}` : ''}</summary>
        <div className="mt-3 space-y-4">{data.historical.answers.map((answer, index) => <div key={`${answer.question_id}:${index}`}><Html value={answer.question_body || `Question ${index + 1}`} /><p className="mt-1 text-sm text-foreground-600">Your answer: {Array.isArray(answer.learner_answer) ? answer.learner_answer.map(String).join(', ') : String(answer.learner_answer ?? 'Not recorded')}</p></div>)}</div>
      </details>}
      {data.history.map((attempt) => <details key={attempt.id} className="rounded-xl border p-3"><summary className="cursor-pointer text-sm">{new Date(attempt.submitted_at).toLocaleString('en-GB')} · {attempt.score_percent == null ? 'Activity completed' : `${Number(attempt.score_percent).toFixed(0)}% · ${attempt.passed ? 'Passed' : 'Not passed'}`}</summary>
        <div className="mt-3 space-y-3">{attempt.study_time && <p className="text-sm">Measured study time: {Math.floor(attempt.study_time.verifiedSeconds / 60)}m {attempt.study_time.verifiedSeconds % 60}s</p>}{attempt.answer_review?.map((answer, index) => <div key={index}><Html value={answer.question} /><p className="text-sm">Your answer: {answer.selected.join(', ')} · {answer.correct ? 'Correct' : 'Incorrect'}</p></div>)}</div>
      </details>)}
    </section>}
    {retakeTimeOpen && <RetakeTimeDialog onClose={() => setRetakeTimeOpen(false)} onChoose={selection => {
      setRetakeTimeOpen(false); void beginAttempt(selection);
    }} />}
    {correction && <CompletionTimeDialog reason={correction.reason} holidayName={correction.holidayName} holidays={[]}
      submitting={busy} serverError={error} onClose={() => setCorrection(null)} onSubmit={at => void submit(at)} />}
  </div>;
}
