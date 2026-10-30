import { ExtraActivities } from '../monthly-submission/ExtraActivities';
import { useExtraActivities } from '@/api/extraActivities';
import { monthlyPresentation, slideIssue, fillEmptyPresentationSlides } from './monthlyPresentation';
import { AssignmentCoachingBooking } from './AssignmentCoachingBooking';
import { useImpactStatements } from '@/hooks/useImpactStatements';
import { useMonthlyReflections } from '@/hooks/useMonthlyReflections';
import { useAssignedKsbExplanations } from '@/hooks/useAssignedKsbExplanations';
import { useEffect, useRef, useState, type ReactNode, type Dispatch, type SetStateAction } from 'react';
import type { ComponentKsbMapping, LearnerKind, LearnerDetail } from '@/api/learnerDetail';
import { fetchLearnerDetail } from '@/api/learnerDetail';
import { fetchEvidence, uploadEvidence, type EvidenceRecord } from '@/api/evidence';
import { exportMonthlyPresentation, type MonthlyAssignment, type AssignmentQualityCheck } from '@/api/monthlyAssignment';
import { proofreadLearningReflection, transcribeVoiceReflection } from '@/api/reflectionVoice';
import type { LearningReflectionSubmissionInput } from '@/api/reflectionSubmission';
import type { AssignmentAnswers } from './AssignmentSubmissionWizard';
import { CheckCircle2, Circle, Loader2, Info, AlertCircle, ArrowRight } from 'lucide-react';
import { startLiveDictation } from '@/utils/liveDictation';
import { Modal } from '@/pages/users/components/Modal';
import { AssignmentTimeEntries } from './AssignmentTimeEntries';
import { LocalAiTextCheck, LocalAiWritingHint } from './LocalAiTextCheck';
import { McmPresentationUpload } from './McmPresentationUpload';
import { learningFetch, parsePersonalLearning } from '@/lib/personalLearning';

// Keys are supplied by the monthly assignment quality-check endpoint.
const QUALITY_CHECK_STEPS: Record<string, number> = {
  answer: 0, learning: 0, evidence: 1, ksbs: 2, planned: 2, declarations: 2,
  hours: 2, reflection: 3, benefit: 4, impact: 4, action: 5, meeting: 6, presentation: 6,
};

const inputClass = 'mt-2 w-full rounded-xl border border-slate-200 bg-white px-3 py-3 text-sm text-slate-900 focus:border-blue-400 focus:ring-2 focus:ring-blue-100 disabled:bg-slate-50';
const buttonClass = 'rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-800 shadow-sm hover:bg-blue-50 disabled:opacity-40';

/** AI never silently replaces a learner's answer: suggestions require acceptance. */
export function MonthlyAnswerField({ label, value, onChange, disabled, title, rows = 5, minimumWords = 0, onePointPerLine = false, generation, qualityTarget }: {
  label: string; value: string; onChange: (value: string) => void; disabled: boolean; title: string; rows?: number;
  minimumWords?: number; onePointPerLine?: boolean; qualityTarget?: string;
  generation?: { enabled: boolean; busy: boolean; onGenerate: () => void };
}) {
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [suggestion, setSuggestion] = useState('');
  const [error, setError] = useState('');
  const recorder = useRef<MediaRecorder | null>(null);
  const dictation = useRef<ReturnType<typeof startLiveDictation>>(null);
  const [voiceNotice, setVoiceNotice] = useState('');
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const active = useRef(true);
  const valueRef = useRef(value);
  valueRef.current = value;
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      dictation.current?.cancel();
      if (recorder.current?.state === 'recording') recorder.current.stop();
      recorder.current?.stream.getTracks().forEach(track => track.stop());
    };
  }, []);
  const context = { activityTitle: `${title}: ${label}`, moduleLabel: '', weekLabel: '', minimumWords, onePointPerLine };
  const suggestionWords = suggestion.trim().split(/\s+/).filter(Boolean).length;
  const suggestionTooShort = suggestionWords < minimumWords;
  const proofread = async () => {
    setBusy(true); setError(''); setSuggestion('');
    try { const result = await proofreadLearningReflection(value, context); if (active.current) setSuggestion(result.text); }
    catch (e) { if (active.current) setError(e instanceof Error ? e.message : 'Proofreading unavailable.'); }
    finally { if (active.current) setBusy(false); }
  };
  const record = async () => {
    if (dictation.current) { dictation.current.stop(); return; }
    if (recorder.current?.state === 'recording') { recorder.current.stop(); return; }
    setError(''); setSuggestion(''); setVoiceNotice('');
    try {
      const original = valueRef.current;
      const live = startLiveDictation(
        text => { if (active.current) onChangeRef.current([original, text].filter(Boolean).join('\n')); },
        () => { dictation.current = null; if (active.current) { setRecording(false); setVoiceNotice(''); } },
        message => { if (active.current) setError(message); },
      );
      if (live) {
        dictation.current = live;
        setRecording(true);
        setVoiceNotice('Listening — your words appear as you speak.');
        return;
      }
      setVoiceNotice('Live dictation is unavailable in this browser. Your recording will be transcribed when you stop.');
      setBusy(true);
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') throw new Error('Voice recording is not available in this browser. You can type your answer.');
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!active.current) { stream.getTracks().forEach(track => track.stop()); return; }
      const mimeType = ['audio/webm', 'audio/mp4', 'audio/ogg'].find(type => MediaRecorder.isTypeSupported(type));
      const media = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      recorder.current = media;
      const chunks: Blob[] = [];
      // Keep each recording bounded; answers can contain multiple recordings.
      const limit = window.setTimeout(() => { if (media.state === 'recording') media.stop(); }, 120000);
      media.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
      media.onstop = async () => {
        window.clearTimeout(limit); stream.getTracks().forEach(track => track.stop());
        if (!active.current) return;
        setRecording(false); setBusy(true);
        try {
          const result = await transcribeVoiceReflection(new Blob(chunks, { type: media.mimeType }), context);
          if (active.current) onChange([valueRef.current, result.text].filter(Boolean).join('\n'));
        } catch (e) { if (active.current) setError(e instanceof Error ? e.message : 'Could not transcribe recording.'); }
        finally { if (active.current) setBusy(false); }
      };
      media.start(); setRecording(true);
    } catch (e) { setError(e instanceof Error ? e.message : 'Microphone access failed.'); }
    finally { if (active.current) setBusy(false); }
  };
  return <div data-quality-target={qualityTarget} tabIndex={qualityTarget ? -1 : undefined} className="min-w-0 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
    <LocalAiWritingHint />
    <label className="block text-sm font-semibold leading-6 text-slate-900">{label}
      <textarea className={inputClass} rows={rows} value={value} disabled={disabled || busy || recording} onChange={e => { setSuggestion(''); onChange(e.target.value); }} />
    </label>
    <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-slate-100 pt-3 [&>button]:min-h-10">
      <button type="button" className={buttonClass} disabled={disabled || busy} onClick={() => void record()}>{recording ? 'Stop recording' : 'Record by voice'}</button>
      <button type="button" className={buttonClass} disabled={disabled || busy || recording || !value.trim()} onClick={() => void proofread()}>{busy ? 'Processing…' : 'AI proofread & improve'}</button>
      {generation && <button type="button" className="w-full rounded-xl bg-primary-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-primary-700 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-500 sm:w-auto" disabled={disabled || busy || recording || generation.busy || !generation.enabled} onClick={generation.onGenerate}>{generation.busy ? 'Generating?' : 'Generate learning statements'}</button>}
      <span className="ml-auto whitespace-nowrap rounded-full bg-slate-100 px-3 py-1.5 text-xs font-medium text-slate-600">{value.trim().split(/\s+/).filter(Boolean).length} words</span>
    </div>
    {suggestion && <div className="mt-3 rounded-xl border border-blue-200 bg-blue-50 p-3 text-sm">
      <p className="mb-2 text-xs font-semibold">Suggestion: {suggestionWords} words{minimumWords > 0 ? ` / ${minimumWords} minimum` : ''}</p>
      <p className="whitespace-pre-wrap">{suggestion}</p>
      {suggestionTooShort && <p role="alert" className="mt-2 text-xs text-red-700">This suggestion is below the {minimumWords}-word minimum. Add more detail from your own experience to your answer, then try again.</p>}
      <div className="mt-2 flex gap-2"><button type="button" disabled={disabled || busy || recording || suggestionTooShort} className={buttonClass} onClick={() => { onChange(suggestion); setSuggestion(''); }}>Use suggestion</button><button type="button" className={buttonClass} onClick={() => setSuggestion('')}>Keep my answer</button></div>
    </div>}
    <LocalAiTextCheck text={value} disabled={disabled || busy || recording} />
    {voiceNotice && <p role="status" className="mt-2 text-xs text-slate-600">{voiceNotice}</p>}
    {error && <p role="alert" className="mt-2 text-xs text-red-700">{error}</p>}
  </div>;
}

export function MonthlyAssignmentSteps({ step, data, onChange, answers, onAnswer, kind, learnerId, title, plannedOtjh, mappings, evidenceFiles, evidenceUploader, timeControl, disabled, payload, checks, checking, onCheck, onSave, historical = false, question = '', activityId = '', revisingRejected = false, onNavigateToCheck }: {
  step: number; data: MonthlyAssignment; onChange: Dispatch<SetStateAction<MonthlyAssignment>>;
  answers: AssignmentAnswers; onAnswer: (key: keyof AssignmentAnswers, value: string) => void;
  kind: LearnerKind; learnerId: string; title: string; plannedOtjh: number | null;
  mappings: ComponentKsbMapping[]; evidenceFiles: EvidenceRecord[]; evidenceUploader: ReactNode; timeControl: ReactNode;
  disabled: boolean; payload: () => LearningReflectionSubmissionInput;
  checks: AssignmentQualityCheck[]; checking: boolean; onCheck: () => Promise<boolean>; onSave: () => Promise<boolean>;
  historical?: boolean; question?: string; activityId?: string; revisingRejected?: boolean;
  onNavigateToCheck?: (step: number, target: string) => void;
}) {
  const personal = parsePersonalLearning(learnerId);
  const personalStudy = personal?.mode === 'study';
  useEffect(() => {
    if (step !== 7 || disabled || historical || data.uploadedPresentation) return;
    if (fillEmptyPresentationSlides(data, answers.whatYouLearned, answers.businessImpact) === data) return;
    onChange(current => fillEmptyPresentationSlides(current, answers.whatYouLearned, answers.businessImpact));
  }, [step, disabled, historical, data, answers.whatYouLearned, answers.businessImpact, onChange]);
  const ksbGenerationStatus = useAssignedKsbExplanations(step === 2 && !disabled && !historical,
    { learnerId, learnerKind: kind, activityId, question, answer: answers.assignmentAnswer, mappings }, data, onChange);
  const presentationContext = `${kind}:${learnerId}:${activityId}:${data.month}`;
  const presentationContextRef = useRef(presentationContext);
  presentationContextRef.current = presentationContext;
  const [library, setLibrary] = useState<EvidenceRecord[]>([]);
  const [activityDetails, setActivityDetails] = useState<{ title: string; date: string; text: string } | null>(null);
  const [showLibrary, setShowLibrary] = useState(false);
  const [detail, setDetail] = useState<LearnerDetail | null>(null);
  const extra = useExtraActivities(kind, learnerId, data.month, [3, 4, 5].includes(step));
  const monthlyActivities = (detail?.activityFeed || []).filter(a => a.at.slice(0, 7) === data.month).map(a => {
    const records = a.kind === 'quiz' ? detail?.quizAttempts || [] : a.kind === 'video' ? detail?.videoProgress || [] : detail?.componentProgress || [];
    const progress = records.find(p => p.submittedAt === a.at && (a.componentId ? p.componentId === a.componentId : a.kind === 'quiz' && 'quizId' in p && p.quizId === a.quizId));
    return { title: a.title, date: a.at.slice(0, 10), reflection: progress?.feedback || '', ksbs: progress?.ksbs || [] };
  }).concat(extra.activities.filter(activity => activity.status !== 'draft').map(activity => ({ title: activity.title, date: activity.submittedAt || '', reflection: activity.reflection || activity.answer, ksbs: activity.ksbs })));
  const impactGeneration = useImpactStatements(step === 4 && !disabled && !historical && Boolean(detail) && !extra.loading && !extra.error, learnerId, activityId, question,
    answers.assignmentAnswer, answers.whatYouLearned, data, answers.businessImpact, onChange, value => onAnswer('businessImpact', value), 'impact', monthlyActivities);
  const actionGeneration = useImpactStatements(step === 5 && !disabled && !historical && Boolean(detail) && !extra.loading && !extra.error, learnerId, activityId, question,
    answers.assignmentAnswer, answers.whatYouLearned, data, answers.businessImpact, onChange, value => onAnswer('businessImpact', value), 'action', monthlyActivities);
  const monthlyReflectionStatus = useMonthlyReflections(step === 3 && !disabled && !historical && Boolean(detail) && !extra.loading && !extra.error, learnerId,
    { month: data.month, question, answer: answers.assignmentAnswer,
      learning: { learned: answers.whatYouLearned, understood: data.understood, skills: data.gainedSkills },
      activities: monthlyActivities }, data, onChange);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [linkUrl, setLinkUrl] = useState('');
  const [linkName, setLinkName] = useState('');
  const [ksbPickerOpen, setKsbPickerOpen] = useState(false);
  const [ksbCategory, setKsbCategory] = useState('K');
  const [ksbSearch, setKsbSearch] = useState('');
  const [selectedKsbs, setSelectedKsbs] = useState<string[]>([]);
  const [ksbLoading, setKsbLoading] = useState(false);
  const [ksbLoadError, setKsbLoadError] = useState('');
  const programmeKsbs = [...new Map([...(detail?.ksbs || []), ...mappings].map(k => [k.code, k])).values()];
  const loadProgrammeKsbs = async () => {
    setKsbLoading(true); setKsbLoadError('');
    try { setDetail(await fetchLearnerDetail(kind, learnerId, { force: true })); }
    catch { setKsbLoadError('Could not load your programme KSBs. Please try again.'); }
    finally { setKsbLoading(false); }
  };
  const patch = (value: Partial<MonthlyAssignment>) => onChange(current => ({ ...current, ...value }));
  const field = (key: keyof MonthlyAssignment, label: string, minimumWords = 0, qualityTarget?: string) => <MonthlyAnswerField qualityTarget={qualityTarget} label={label} title={title} value={String(data[key] || '')} onChange={value => patch({ [key]: value })} disabled={disabled || busy} minimumWords={minimumWords} />;
  const check = (key: keyof MonthlyAssignment, label: string, qualityTarget?: string) => <label data-quality-target={qualityTarget} tabIndex={qualityTarget ? -1 : undefined} className="flex min-h-14 cursor-pointer items-center gap-3 rounded-xl bg-slate-50 px-4 py-3"><input type="checkbox" checked={data[key] === true} disabled={disabled || busy} onChange={e => patch({ [key]: e.target.checked })} className="m-0 h-4 w-4 shrink-0 accent-blue-600 disabled:cursor-not-allowed" /><span className="min-w-0 text-sm font-medium leading-6 tracking-normal text-slate-800">{label}</span></label>;
  useEffect(() => {
    let active = true;
    if (historical) return;
    if ([2, 3, 4, 5].includes(step)) fetchLearnerDetail(kind, learnerId).then(result => { if (active) setDetail(result); }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [step, kind, learnerId, historical]);
  const addFile = (file: EvidenceRecord) => {
    if (!data.evidence.some(e => e.id === file.id)) patch({ evidence: [...data.evidence, { id: file.id, name: file.filename, points: '' }] });
  };
  const openLibrary = async () => {
    setBusy(true); setError('');
    try { setLibrary(await fetchEvidence(kind, learnerId, { status: 'approved' })); setShowLibrary(true); }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not load the evidence library.'); }
    finally { setBusy(false); }
  };
  const generate = async () => {
    setBusy(true); setError('');
    try {
      const fresh = await fetchLearnerDetail(kind, learnerId, { force: true });
      if (presentationContextRef.current !== presentationContext) return;
      setDetail(fresh);
      const element = document.createElement('div'); element.innerHTML = question;
      const slides = monthlyPresentation(title, answers.assignmentAnswer, answers.whatYouLearned, answers.businessImpact, data, fresh, element.textContent || '');
      patch({ slides, presentationReviewed: false, presentationToken: '' });
      setNotice('Your full-month presentation is ready to review. The selected design and KBC logo will be applied when you export.');
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not load monthly activities. Your existing slides are preserved.'); }
    finally { setBusy(false); }
  };
  const [uploadingDesignName, setUploadingDesignName] = useState('');
  const uploadDesign = async (file: File) => {
    if (!file.name.toLowerCase().endsWith('.pptx') || file.size > 15 * 1024 * 1024) { setError('Choose a .pptx reference up to 15 MB. Save older .ppt files as .pptx first.'); return; }
    setBusy(true); setError(''); setUploadingDesignName(file.name);
    try {
      const uploaded = await uploadEvidence(kind, learnerId, file, `presentation-reference-${activityId}`);
      if (uploaded.status !== 'approved') throw new Error('This file has not passed the upload checks. Choose another reference.');
      const response = await learningFetch(`/learner_api/reflection/assignment/presentation-design/?learnerId=${encodeURIComponent(learnerId)}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ learnerKind: kind, evidenceId: uploaded.id }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Could not read this design reference.');
      if (presentationContextRef.current !== presentationContext) return;
      patch({ presentationDesign: result.design, presentationReviewed: false, presentationToken: '' });
      setNotice('Reference template saved. Its layouts, backgrounds and artwork will be used in your exported PowerPoint.');
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not read the design reference.'); }
    finally { setBusy(false); setUploadingDesignName(''); }
  };
  const exportDeck = async () => {
    const invalidIndex = data.slides.findIndex(slide => Boolean(slideIssue(slide)));
    if (invalidIndex !== -1) {
      setError(`Slide ${invalidIndex + 1}: ${slideIssue(data.slides[invalidIndex])}`);
      const target = document.getElementById(`assignment-slide-${invalidIndex}`);
      target?.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
      target?.querySelector<HTMLTextAreaElement | HTMLInputElement>(!data.slides[invalidIndex].title?.trim() ? 'input' : 'textarea')?.focus({ preventScroll: true });
      return;
    }
    setBusy(true); setError('');
    try {
      if (!disabled && !await onSave()) return;
      const { blob, token } = await exportMonthlyPresentation(payload());
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = `monthly-assignment-${data.month}.pptx`; anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 30000);
      if (!disabled) patch({ presentationToken: token });
      setNotice('PowerPoint exported. Slide content is also saved in your draft.');
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not export the presentation.'); }
    finally { setBusy(false); }
  };
  return <div className="space-y-5">
    {step === 0 && <section data-quality-target="learning" tabIndex={-1} className="space-y-4 border-t border-slate-200 pt-6">
      <div><h3 className="text-lg font-semibold text-slate-900">Your learning statements</h3><p className="mt-1 text-sm leading-6 text-slate-600">Review each statement and make it your own. Each field needs at least 20 words.</p></div>
      <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-3">
      <MonthlyAnswerField title={title} label="I learned… (at least 20 words)" value={answers.whatYouLearned} disabled={disabled} onChange={value => onAnswer('whatYouLearned', value)} minimumWords={20} />
      {field('understood', 'I understood… (at least 20 words)', 20)}
      {field('gainedSkills', 'I gained skills in… (at least 20 words)', 20)}
    </div></section>}
    {step === 1 && <>
      <h3 data-quality-target="evidence" tabIndex={-1} className="text-lg font-semibold">Evidence & cross-referencing</h3>
      <p className="text-sm text-slate-600">Uploading files, reusing evidence and adding links are optional. You can continue and submit without evidence. If you attach any, linking it to numbered answer points is also optional.</p>
      <ol className="list-inside list-decimal rounded-xl bg-blue-50 p-4 text-sm">{answers.assignmentAnswer.split('\n').filter(line => line.trim()).map((line, i) => <li className="mb-2" key={i}>{line}</li>)}</ol>
      <section className="space-y-4 rounded-2xl border border-slate-200 bg-white p-4 sm:p-6">
        <div><h4 className="text-base font-semibold text-slate-900">Upload or reuse a file (optional)</h4><p className="mt-1 text-sm text-slate-600">Upload supporting work, or choose a file already in your evidence library.</p></div>
        <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-4">{evidenceUploader}</div>
      <div className="flex flex-wrap gap-2">{evidenceFiles.filter(f => f.status === 'approved' && !data.evidence.some(e => e.id === f.id)).map(file => <button key={file.id} type="button" disabled={disabled} className={buttonClass} onClick={() => addFile(file)}>Attach {file.filename}</button>)}<button type="button" className={buttonClass} disabled={disabled || busy} onClick={() => void openLibrary()}>Pull from evidence library</button></div>
      {showLibrary && <div className="rounded-xl border border-slate-200 bg-slate-50 p-4"><div className="flex flex-wrap items-center justify-between gap-3"><h5 className="font-semibold">Evidence library</h5><button type="button" className={buttonClass} onClick={() => setShowLibrary(false)}>Close library</button></div>{library.length === 0 && <p className="mt-3 text-sm text-slate-600">No uploaded evidence available yet.</p>}<div className="mt-3 max-h-72 space-y-2 overflow-y-auto">{library.map(file => <div key={file.id} className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white p-3 text-sm"><span className="min-w-0 break-words">{file.filename}</span><button type="button" className={buttonClass + ' shrink-0'} disabled={disabled || data.evidence.some(e => e.id === file.id)} onClick={() => addFile(file)}>{data.evidence.some(e => e.id === file.id) ? 'Attached' : 'Attach'}</button></div>)}</div></div>}
      </section>
      <section className="space-y-4 rounded-2xl border border-slate-200 bg-white p-4 sm:p-6">
        <div><h4 className="text-base font-semibold text-slate-900">Add a link (optional)</h4><p className="mt-1 text-sm text-slate-600">Link to online work, a video or a document that supports your answer.</p></div>
      <fieldset disabled={disabled || busy} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-[1fr_1.5fr_auto] lg:items-end">
        <label className="min-w-0 text-sm font-medium text-slate-700">Link title<input aria-label="Evidence link name" placeholder="e.g. Project demonstration" value={linkName} onChange={e => setLinkName(e.target.value)} className={inputClass} /></label>
        <label className="min-w-0 text-sm font-medium text-slate-700">Web address<input type="url" aria-label="Evidence URL" placeholder="https://example.com" value={linkUrl} onChange={e => setLinkUrl(e.target.value)} className={inputClass} /></label>
        <button type="button" className="rounded-xl bg-primary-600 px-5 py-3 text-sm font-semibold text-white hover:bg-primary-700 disabled:opacity-40" onClick={() => {
        try { const url = new URL(linkUrl); if (!['https:', 'http:'].includes(url.protocol)) throw new Error(); patch({ evidence: [...data.evidence, { id: `link:${crypto.randomUUID()}`, name: linkName.trim() || url.hostname, url: url.href, points: '' }] }); setLinkUrl(''); setLinkName(''); setError(''); }
        catch { setError('Enter a valid HTTP or HTTPS evidence link.'); }
      }}>Add link</button></fieldset>
      </section>
      <section className="space-y-4 border-t border-slate-200 pt-6">
        <div><h4 className="text-base font-semibold text-slate-900">Attached evidence ({data.evidence.length})</h4><p className="mt-1 text-sm text-slate-600">These items are included in your submission. Linking them to answer points is optional.</p></div>
        {data.evidence.length === 0 && <p className="rounded-xl border border-dashed border-slate-300 p-5 text-sm text-slate-500">No evidence attached. This is optional; you can continue without adding files or links.</p>}
        {data.evidence.map(entry => <article key={entry.id} className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
          <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 bg-slate-50 p-4 sm:px-5">
            <div className="min-w-0 flex-1"><span className="text-xs font-semibold uppercase tracking-wide text-primary-700">{entry.url ? 'Link' : 'File'}</span><h5 className="mt-1 break-words text-sm font-semibold text-slate-900">{entry.name}</h5>{entry.url && /^https?:\/\//i.test(entry.url) && <a href={entry.url} target="_blank" rel="noopener noreferrer" className="mt-1 block break-all text-sm text-primary-700 underline">Open link (new tab)</a>}</div>
            <button type="button" disabled={disabled} className="rounded-lg border border-red-200 px-3 py-2 text-xs font-semibold text-red-700 hover:bg-red-50 disabled:opacity-40" onClick={() => patch({ evidence: data.evidence.filter(item => item.id !== entry.id), claims: data.claims.map(c => ({ ...c, evidenceIds: c.evidenceIds.filter(id => id !== entry.id) })) })}>Remove from submission</button>
          </div>
          <div className="grid gap-3 p-4 sm:grid-cols-[minmax(0,240px)_1fr] sm:items-center sm:gap-6 sm:p-5">
            <label className="block text-sm font-medium text-slate-800">Answer point numbers <span className="font-normal text-slate-500">(optional)</span><input className={inputClass} placeholder="e.g. 3, 4" aria-describedby={`evidence-points-help-${entry.id}`} value={entry.points} disabled={disabled} onChange={e => patch({ evidence: data.evidence.map(item => item.id === entry.id ? { ...item, points: e.target.value } : item) })} /></label>
            <p id={`evidence-points-help-${entry.id}`} className="text-sm leading-relaxed text-slate-600">Use the numbers from your answer above, not file page numbers. For example, <strong>3, 4</strong> means this evidence supports points 3 and 4. You can leave this blank.</p>
          </div>
        </article>)}
      </section>
    </>}
    {step === 2 && <section className="mx-auto w-full max-w-6xl space-y-6">
      <header><h3 className="text-xl font-semibold text-slate-900">KSBs & hours claimed</h3><p className="mt-2 text-sm leading-6 text-slate-600">Record your learning time and review each KSB explanation. Selecting supporting evidence is optional.</p></header>
      <section className="space-y-4 rounded-2xl border border-slate-200 bg-slate-50 p-4 sm:p-6" aria-label="Learning time" data-quality-target="hours" tabIndex={-1}>
        <div className="flex flex-wrap items-center justify-between gap-3"><h4 className="font-semibold text-slate-900">Your learning time</h4><span className="rounded-full border border-slate-200 bg-white px-3 py-1 text-sm">Planned: {plannedOtjh == null ? 'Not set' : `${plannedOtjh} hours`}</span></div>
        {disabled && !data.timeEntries?.length ? <fieldset disabled>{timeControl}</fieldset> :
          <AssignmentTimeEntries kind={kind} learnerId={learnerId} dailyLimit={payload().activityType !== 'extra_activity' && !parsePersonalLearning(learnerId)} month={data.month} entries={data.timeEntries || []} disabled={disabled} onChange={timeEntries => patch({ timeEntries })} />}
        {check('paidHours', 'This learning was completed during paid working hours.')}
      </section>
      <section className="rounded-2xl border border-blue-200 bg-blue-50 p-4 sm:p-5">
        <h4 className="font-semibold text-blue-950">Review your KSB explanations</h4>
        <p className="mt-2 text-sm leading-6 text-blue-900">Assigned KSBs are drafted from your answer. Evidence is optional; readable attachments are considered when available. Review the drafts and complete any blank fields. Write your own explanation for KSBs you add yourself. Existing explanations are preserved.</p>
        {ksbGenerationStatus && <p role="status" className="mt-3 border-t border-blue-200 pt-3 text-sm leading-6 text-blue-900">{ksbGenerationStatus}</p>}
      </section>
      <div className="flex flex-wrap items-center justify-between gap-2"><h4 data-quality-target="ksbs" tabIndex={-1} className="text-lg font-semibold text-slate-900">Your KSB claims</h4><span className="text-sm text-slate-600">{data.claims.length} claims</span></div>
      <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-2">
      {data.claims.map((claim, index) => {
        const mapping = mappings.find(m => m.code === claim.code);
        const words = claim.explanation.trim().split(/\s+/).filter(Boolean).length;
        return <article key={claim.code} className="min-w-0 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <header className="space-y-3 border-b border-slate-200 bg-slate-50 p-4 sm:p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap items-center gap-2"><strong className="rounded-lg bg-slate-900 px-3 py-1.5 text-sm text-white">{claim.code}</strong><span className="text-xs font-medium text-slate-600">{mapping ? 'Assignment KSB' : 'Added by you'}{mapping?.classification ? ` ? ${mapping.classification}` : ''}</span></div>
              <button type="button" disabled={disabled} className="min-h-10 rounded-lg px-3 py-2 text-xs font-medium text-red-700 hover:bg-red-50 disabled:opacity-40" onClick={() => patch({ claims: data.claims.filter((_, i) => i !== index), plannedReviewed: false })}>Remove claim</button>
            </div>
            <p className="break-words text-sm leading-6 text-slate-700">{mapping?.description || detail?.ksbs.find(k => k.code === claim.code)?.description}</p>
          </header>
          <div className="space-y-5 p-4 sm:p-5">
            <div>
              <LocalAiWritingHint />
              <label className="block text-sm font-semibold text-slate-900">How did you apply this KSB?
                <textarea rows={5} className={`${inputClass} min-h-36 resize-y font-normal leading-6`} value={claim.explanation} disabled={disabled} placeholder="Describe what you did, how you applied this KSB and what you learned." onChange={e => patch({ claims: data.claims.map((c, i) => i === index ? { ...c, explanation: e.target.value } : c) })} />
              </label>
              <LocalAiTextCheck text={claim.explanation} disabled={disabled} />
              <div className="mt-2 flex flex-wrap justify-between gap-2 text-xs"><span className="text-slate-500">At least 20 words</span><span className={words >= 20 ? 'font-medium text-emerald-700' : 'font-medium text-amber-800'}>{words} words{words < 20 ? ' ? Needs more detail' : ''}</span></div>
            </div>
            <fieldset className="min-w-0 border-t border-slate-100 pt-4">
              <legend className="pr-2 text-sm font-semibold text-slate-900">Supporting evidence (optional)</legend>
              <p className="mb-3 text-xs leading-5 text-slate-500">You may select files or links that support this explanation, or leave this blank.</p>
              <div className="space-y-2">{data.evidence.map(e => <label className={`flex min-h-14 cursor-pointer items-center gap-3 rounded-xl border px-4 py-3 text-sm ${claim.evidenceIds.includes(e.id) ? 'border-blue-200 bg-blue-50 text-blue-950' : 'border-slate-200 text-slate-700'}`} key={e.id}><input className="m-0 h-4 w-4 shrink-0 accent-blue-600 disabled:cursor-not-allowed" type="checkbox" disabled={disabled} checked={claim.evidenceIds.includes(e.id)} onChange={event => patch({ claims: data.claims.map((c, i) => i === index ? { ...c, evidenceIds: event.target.checked ? [...c.evidenceIds, e.id] : c.evidenceIds.filter(id => id !== e.id) } : c) })} /><span className="min-w-0 break-words text-sm font-medium leading-6 tracking-normal">{e.name}</span></label>)}</div>
              {!data.evidence.length && <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900">No evidence attached. You can complete this KSB explanation without files or links.</p>}
            </fieldset>
          </div>
        </article>;
      })}
      </div>
      <div className="flex flex-wrap gap-2">
        <button type="button" disabled={disabled} className={buttonClass} onClick={() => { setSelectedKsbs([]); setKsbSearch(''); setKsbPickerOpen(true); void loadProgrammeKsbs(); }}>Add programme KSBs</button>
        <button type="button" disabled={disabled} className={buttonClass} onClick={() => patch({ claims: mappings.map(m => data.claims.find(c => c.code === m.code) || { code: m.code, explanation: '', evidenceIds: [] }), plannedReviewed: false })}>Reset claims to planned KSBs</button>
      </div>
      {ksbPickerOpen && <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/40 p-3" onClick={() => setKsbPickerOpen(false)}>
        <section role="dialog" aria-modal="true" aria-label="Choose programme KSBs" onKeyDown={e => { if (e.key === 'Escape') setKsbPickerOpen(false); }} onClick={e => e.stopPropagation()} className="flex max-h-[calc(100dvh-2rem)] w-full max-w-2xl flex-col overflow-hidden rounded-2xl bg-white shadow-xl">
          <header className="flex shrink-0 items-start justify-between gap-4 border-b p-4"><div><h3 className="text-lg font-semibold">Choose programme KSBs</h3><p className="mt-1 text-sm text-slate-600">Select additional knowledge, skills and behaviours. Already assigned KSBs cannot be added twice.</p></div><button type="button" aria-label="Close KSB picker" className={buttonClass} onClick={() => setKsbPickerOpen(false)}>Close</button></header>
          <div className="min-h-0 space-y-4 overflow-y-auto p-4">
            <div className="grid grid-cols-3 gap-2" role="group" aria-label="KSB categories">{[['K', 'Knowledge'], ['S', 'Skills'], ['B', 'Behaviours']].map(([code, label]) => <button type="button" key={code} aria-pressed={ksbCategory === code} onClick={() => setKsbCategory(code)} className={`rounded-xl border p-3 text-sm ${ksbCategory === code ? 'border-primary-600 bg-primary-50 text-primary-700' : 'border-slate-200'}`}><strong className="block">{code}</strong>{label}</button>)}</div>
            <input autoFocus aria-label="Search programme KSBs" placeholder="Search by code or description" className={inputClass} value={ksbSearch} onChange={e => setKsbSearch(e.target.value)} />
            {ksbLoading ? <p role="status">Loading programme KSBs?</p> : ksbLoadError ? <div role="alert"><p>{ksbLoadError}</p><button type="button" className={buttonClass} onClick={() => void loadProgrammeKsbs()}>Retry</button></div> : <div className="space-y-2">
              {programmeKsbs.filter(k => k.code.toUpperCase().startsWith(ksbCategory) && `${k.code} ${k.description}`.toLowerCase().includes(ksbSearch.toLowerCase())).map(k => {
                const assigned = mappings.some(m => m.code === k.code) || data.claims.some(c => c.code === k.code);
                return <label key={k.code} className={`flex min-h-14 items-center gap-3 rounded-xl border p-4 ${assigned ? 'border-slate-200 bg-slate-100 text-slate-500' : 'cursor-pointer border-primary-100 hover:bg-primary-50'}`}><input type="checkbox" disabled={disabled || assigned} checked={assigned || selectedKsbs.includes(k.code)} onChange={e => setSelectedKsbs(current => e.target.checked ? [...current, k.code] : current.filter(code => code !== k.code))} className="m-0 h-4 w-4 shrink-0 accent-blue-600 disabled:cursor-not-allowed" /><span className={`min-w-0 text-sm font-medium leading-6 tracking-normal ${assigned ? 'text-slate-500' : 'text-slate-800'}`}><strong>{k.code}</strong>{assigned && <span className="ml-2 text-xs">Already assigned</span>}<span className="mt-1 block break-words">{k.description}</span></span></label>;
              })}
              {!programmeKsbs.some(k => k.code.toUpperCase().startsWith(ksbCategory) && `${k.code} ${k.description}`.toLowerCase().includes(ksbSearch.toLowerCase())) && <p className="text-sm text-slate-600">No programme KSBs match this category or search.</p>}
            </div>}
          </div>
          <footer className="flex shrink-0 items-center justify-between gap-3 border-t p-4"><span className="text-sm">{selectedKsbs.length} selected</span><button type="button" className={buttonClass} disabled={disabled || ksbLoading || Boolean(ksbLoadError) || !selectedKsbs.length} onClick={() => { onChange(current => ({ ...current, plannedReviewed: false, claims: [...current.claims, ...selectedKsbs.filter(code => !current.claims.some(c => c.code === code) && !mappings.some(m => m.code === code)).map(code => ({ code, explanation: '', evidenceIds: [] }))] })); setKsbPickerOpen(false); }}>Add selected KSBs</button></footer>
        </section>
      </div>}
      <section className="space-y-3 rounded-2xl border border-slate-200 p-4 sm:p-6">
      <h4 className="font-semibold text-slate-900">Confirm your learning</h4>
      <p className="text-sm text-slate-600">All four declarations are required before you can submit your assignment. You can save a draft and return to them later.</p>
      {check('plannedReviewed', 'I have reviewed the planned hours and KSBs against my actual learning.', 'planned')}
      {check('newKnowledge', 'This activity developed new knowledge.', 'declarations')}
      {check('newSkills', 'This activity developed new skills or behaviours.')}
      {check('sharingConsent', 'If I include evidence, my employer accepts sharing it and it contains no confidential information.')}
      </section>
    </section>}
    {step === 3 && <><ExtraActivities kind={kind} learnerId={learnerId} month={data.month} />
      <h3 className="text-lg font-semibold">Full-month reflection — {data.month}</h3>
      <div className="overflow-x-auto rounded-xl border border-slate-200">
        <table className="w-full text-left text-xs">
          <thead className="bg-blue-50"><tr><th className="p-3">Activity</th><th className="p-3">Date</th><th className="p-3">Result</th><th className="p-3">KSBs</th><th className="p-3">Time / source</th><th className="p-3">Learning and impact</th></tr></thead>
          <tbody>{(detail?.activityFeed || []).filter(a => a.at.slice(0, 7) === data.month).map((a, i) => {
            const records = a.kind === 'quiz' ? detail?.quizAttempts || [] : a.kind === 'video' ? detail?.videoProgress || [] : detail?.componentProgress || [];
            const progress = records.find(p => p.submittedAt === a.at && (a.componentId ? p.componentId === a.componentId : a.kind === 'quiz' && 'quizId' in p && p.quizId === a.quizId));
            return <tr key={i} className="border-t">
              <td className="p-3">{a.title}</td><td className="p-3">{a.at.slice(0, 10)}</td><td className="p-3">{a.detail || a.action}</td>
              <td className="p-3">{progress?.ksbs?.join(', ') || 'Not recorded'}</td>
              <td className="p-3">{progress?.reportedTime || progress?.timeTaken || 'Not recorded'}<span className="block text-slate-500">{progress?.timeTrackingSource || ''}</span></td>
              <td className="w-72 max-w-xs p-3 text-left align-top">
                {progress?.feedback?.trim() ? <div className="space-y-2">
                  <p className="line-clamp-3 break-words text-sm leading-6 text-slate-700">{progress.feedback.replace(/\s+/g, ' ').trim().slice(0, 160)}{progress.feedback.replace(/\s+/g, ' ').trim().length > 160 ? '…' : ''}</p>
                  <button type="button" className="min-h-10 rounded-lg px-3 py-2 text-sm font-semibold text-blue-700 hover:bg-blue-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600" aria-label={`Read more about ${a.title}`} onClick={() => setActivityDetails({ title: a.title, date: a.at.slice(0, 10), text: progress.feedback })}>Read more</button>
                </div> : <span className="text-slate-500">Not recorded</span>}
              </td>
            </tr>;
          })}</tbody>
        </table>
        {detail && !(detail.activityFeed || []).some(a => a.at.slice(0, 7) === data.month) && <p className="p-3 text-sm">No recorded activities for this month.</p>}
        {!detail && <p className="p-3 text-sm">Loading your recorded activities…</p>}
      </div>
      {activityDetails && <Modal title="Learning and impact" onClose={() => setActivityDetails(null)} size="max-w-2xl">
        <div className="mb-5 rounded-xl bg-slate-50 p-4">
          <h3 className="break-words text-base font-semibold text-slate-900">{activityDetails.title}</h3>
          <p className="mt-1 text-sm text-slate-600">Activity date: {activityDetails.date}</p>
        </div>
        <div className="whitespace-pre-wrap break-words text-sm leading-7 text-slate-800">{activityDetails.text}</div>
      </Modal>}
      <section className="rounded-xl border border-blue-200 bg-blue-50 p-4 text-sm leading-6 text-blue-900">
        <h4 className="font-semibold">Your monthly reflection drafts</h4>
        <p className="mt-1">These two reflections are drafted automatically from your assignment answer and this month's recorded activities. Review them before submitting. Your existing text is preserved.</p>
        {monthlyReflectionStatus && <p role="status" className="mt-3 border-t border-blue-200 pt-3">{monthlyReflectionStatus}</p>}
      </section>
      {field('lmsReflection', 'Reflect on your LMS activities and assignment (at least 20 words)', 0, 'reflection')}
      {field('extraActivities', 'Additional activities outside the LMS (optional)')}
      {field('integratedReflection', 'How does the learning fit together? (at least 20 words)')}
    </>}
    {step === 4 && <>
      <section className="rounded-xl border border-blue-200 bg-blue-50 p-4 text-sm leading-6 text-blue-900">
        <h3 className="font-semibold">Your impact drafts</h3>
        <p className="mt-1">These fields are drafted from your assignment answer, this month's recorded activities and learning reflections. Review and edit them to match your experience. Existing text is preserved; confirm the employer declaration yourself.</p>
        {impactGeneration.status && <div role={impactGeneration.phase === 'error' ? 'alert' : 'status'} aria-live="polite" className={`mt-4 flex items-start gap-3 rounded-xl border p-4 ${impactGeneration.phase === 'error' ? 'border-red-200 bg-red-50 text-red-900' : impactGeneration.phase === 'success' ? 'border-emerald-200 bg-emerald-50 text-emerald-900' : 'border-blue-200 bg-white text-blue-900'}`}>
          {impactGeneration.phase === 'loading' ? <Loader2 aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0 animate-spin" /> : impactGeneration.phase === 'success' ? <CheckCircle2 aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0" /> : impactGeneration.phase === 'error' ? <AlertCircle aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0" /> : <Info aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0" />}
          <div className="min-w-0"><p className="font-semibold">{impactGeneration.phase === 'loading' ? 'Generating your impact drafts...' : impactGeneration.phase === 'success' ? 'Your drafts are ready' : impactGeneration.phase === 'error' ? 'Generation could not finish' : 'More details needed'}</p><p className="mt-1">{impactGeneration.status}</p></div>
        </div>}
      </section>
      {field('careerImpact', 'Impact on your career (at least 20 words)', 0, 'impact')}{field('jobImpact', 'Impact on your job performance (at least 20 words)')}{field('employerImpact', 'Impact on employer performance (at least 20 words)')}{check('employerBenefit', 'I can explain how my employer has benefited from this learning.', 'benefit')}<MonthlyAnswerField title={title} label="Measurable business outcomes (at least 20 words)" value={answers.businessImpact} onChange={value => onAnswer('businessImpact', value)} disabled={disabled} minimumWords={20} /></>}
    {step === 5 && <>
      <section className="rounded-xl border border-blue-200 bg-blue-50 p-4 text-sm leading-6 text-blue-900">
        <h3 className="font-semibold">Your action plan & EPA drafts</h3>
        <p className="mt-1">These drafts use your assignment answer, this month's recorded activities and reflections. Review the proposed actions and EPA preparation before submitting. Your existing text is preserved.</p>
        {actionGeneration.status && <div role={actionGeneration.phase === 'error' ? 'alert' : 'status'} aria-live="polite" className={`mt-4 flex items-start gap-3 rounded-xl border p-4 ${actionGeneration.phase === 'error' ? 'border-red-200 bg-red-50 text-red-900' : actionGeneration.phase === 'success' ? 'border-emerald-200 bg-emerald-50 text-emerald-900' : 'border-blue-200 bg-white text-blue-900'}`}>
          {actionGeneration.phase === 'loading' ? <Loader2 aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0 animate-spin" /> : actionGeneration.phase === 'success' ? <CheckCircle2 aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0" /> : actionGeneration.phase === 'error' ? <AlertCircle aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0" /> : <Info aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0" />}
          <div className="min-w-0"><p className="font-semibold">{actionGeneration.phase === 'loading' ? 'Generating your action plan & EPA drafts...' : actionGeneration.phase === 'success' ? 'Your drafts are ready' : actionGeneration.phase === 'error' ? 'Generation could not finish' : 'More details needed'}</p><p className="mt-1">{actionGeneration.status}</p></div>
        </div>}
      </section>
      {field('actionPlan', 'Your action plan for next month (at least 20 words)', 0, 'action')}{field('epaPreparedness', 'How has this prepared you for EPA? (at least 20 words)')}</>}
    {step === 7 && <><h3 className="text-lg font-semibold">Submission quality checks</h3><p className="text-sm text-slate-600">{revisingRejected ? 'Run quality checks again for this revised submission. You can select any check to return to that section and make corrections. A verified MCM booking from your rejected submission in the same month satisfies the booking requirement; you do not need to book again. Your presentation must still pass its check.' : 'All checks must be green before you can submit your assignment. Complete the coaching meeting booking and presentation in Step 7 (Coaching & presentation), then run the checks again.'}</p><button type="button" className={buttonClass} disabled={checking || disabled} onClick={() => void onCheck()}>{checking ? 'Checking…' : 'Run quality checks'}</button>{checks.map(c => {
      const targetStep = QUALITY_CHECK_STEPS[c.key];
      const content = <>{c.passed ? <CheckCircle2 aria-hidden="true" className="h-4 w-4 shrink-0" /> : <Circle aria-hidden="true" className="h-4 w-4 shrink-0" />}<span className="flex-1">{c.label}</span></>;
      const classes = `flex w-full items-center gap-2 rounded-xl border p-3 text-left text-sm ${c.passed ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-amber-200 bg-amber-50 text-amber-900'}`;
      return targetStep !== undefined && onNavigateToCheck
        ? <button key={c.key} type="button" className={`${classes} cursor-pointer transition-shadow hover:shadow-md focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600`} onClick={() => onNavigateToCheck(targetStep, c.key)}>{content}<span className="sr-only"> - Go to Step {targetStep + 1}</span><ArrowRight aria-hidden="true" className="h-4 w-4 shrink-0" /></button>
        : <div key={c.key} className={classes}>{content}</div>;
    })}</>}
    {step === 6 && <>
      {revisingRejected && <p className="rounded-xl border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900">You do not need a new MCM booking when your rejected submission already has a verified booking for this submission month. The final quality checks verify this automatically.</p>}
        <h3 data-quality-target="meeting" tabIndex={-1} className="text-lg font-semibold">Coaching & presentation</h3>
      <p className="text-sm text-slate-600">Book coaching from the 20th of a month through the 5th of the following month. You can finish both tasks here and keep the whole submission as a draft until ready.</p>
      <AssignmentCoachingBooking assignmentId={activityId} topicId={payload().assignmentTopicId} kind={kind} learnerId={learnerId} month={data.month} title={title} meetingKey={data.meetingKey} disabled={disabled || historical} onSave={onSave} onSelect={meetingKey => patch({ meetingKey })} />
      <section data-quality-target="presentation" tabIndex={-1} className="space-y-5 rounded-2xl border border-slate-200 bg-white p-4 sm:p-6">
        <div className="flex items-start gap-3"><span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary-100 text-sm font-bold text-primary-700">2</span><div><h4 className="text-base font-semibold text-slate-900">Prepare your presentation</h4><p className="mt-1 text-sm text-slate-600">Generate, review and export your slides, or upload your own MCM PowerPoint below.</p></div></div>
      <section className="space-y-3 rounded-xl border border-dashed border-slate-300 bg-slate-50 p-4">
        <div>
          <p className="text-sm font-semibold">PowerPoint design reference (optional)</p>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <label className={`relative rounded-lg border border-slate-300 bg-white px-4 py-2.5 focus-within:ring-2 focus-within:ring-blue-500 ${disabled || busy ? 'cursor-not-allowed opacity-50' : 'cursor-pointer hover:bg-blue-50'}`}>
              <span className="text-sm font-medium text-slate-800">{data.presentationDesign ? 'Change reference' : 'Choose reference'}</span>
              <input type="file" aria-label="PowerPoint design reference" accept=".pptx" disabled={disabled || busy} className="sr-only" onChange={e => { const file = e.target.files?.[0]; e.target.value = ''; if (file) void uploadDesign(file); }} />
            </label>
            <span role="status" className="min-w-0 break-words text-sm text-slate-700">{uploadingDesignName ? `Reading ${uploadingDesignName}...` : data.presentationDesign ? `Selected: ${data.presentationDesign.name}` : 'No reference selected. The default KBC design will be used.'}</span>
          </div>
        </div>
        <p className="text-sm leading-6 text-slate-600">Upload a .pptx up to 15 MB. The full reference is saved securely and reused with its masters, layouts, backgrounds and artwork. Its text is replaced with your monthly content and KBC branding is added. Choose a cover and content slide with room for text; charts, embedded media and animations are not reused.</p>
        {data.presentationDesign && <div className="flex flex-wrap items-center gap-3 text-sm"><span className="h-5 w-5 rounded border" style={{ backgroundColor: `#${data.presentationDesign.accent}` }} /><span>{data.presentationDesign.name} ? {data.presentationDesign.font}</span><button type="button" disabled={disabled || busy} className={buttonClass} onClick={() => patch({ presentationDesign: undefined, presentationReviewed: false, presentationToken: '' })}>Use default KBC design</button></div>}
        {data.presentationDesign && !data.presentationDesign.evidenceId && <p role="alert" className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900">This older reference saved colours only. Upload the original PPTX again to use its full template.</p>}
        {data.presentationDesign?.evidenceId && <div className="grid gap-3 sm:grid-cols-2">{(['coverSlide', 'contentSlide'] as const).map(key => <label key={key} className="block text-sm">{key === 'coverSlide' ? 'Reference cover slide' : 'Reference content slide'}<select className={inputClass} disabled={disabled || busy} value={data.presentationDesign?.[key] || 1} onChange={e => patch({ presentationDesign: { ...data.presentationDesign!, [key]: Number(e.target.value) }, presentationReviewed: false, presentationToken: '' })}>{Array.from({ length: data.presentationDesign?.slideCount || 1 }, (_, i) => <option key={i} value={i + 1}>Slide {i + 1}</option>)}</select></label>)}</div>}
        {busy && <p role="status" className="flex items-center gap-2 text-sm"><Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />Preparing your presentation...</p>}
      </section>
      <div role="note" className="flex items-start gap-3 rounded-xl border border-blue-200 bg-blue-50 p-4 text-sm leading-6 text-blue-900">
        <Info aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0" />
        <div>
          <p className="font-semibold">Keep your slides up to date</p>
          <p className="mt-1">Empty learning, reflection, impact and action-plan slides are filled automatically from your saved answers when you open this step. Slides that already contain text are preserved. Complete any missing answers in Steps 4?6. To rebuild the whole presentation from your latest answers, click Generate full-month presentation.</p>
          <p className="mt-2 font-medium">Generating again replaces all current slides, including your manual slide edits. Review the updated slides before exporting.</p>
        </div>
      </div>
      <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap"><button type="button" className={buttonClass} disabled={disabled || busy} onClick={() => { if (!data.slides.length || window.confirm('Replace your edited slides with a fresh draft from your answers?')) generate(); }}>Generate full-month presentation</button><button type="button" className={buttonClass} disabled={disabled || busy || data.slides.length >= 200} onClick={() => patch({ slides: [...data.slides, { title: '', body: '' }], presentationReviewed: false, presentationToken: '' })}>Add slide</button></div>
      {!data.slides.length && <p className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-5 text-sm text-slate-500">No slides yet. Generate a presentation from your answers and monthly activities, or add your first slide.</p>}
      <div className="space-y-4">
      {data.slides.map((slide, i) => <fieldset id={`assignment-slide-${i}`} disabled={disabled || busy} key={i} className={`rounded-xl border p-4 sm:p-5 ${slideIssue(slide) ? 'border-amber-300 bg-amber-50/40' : 'border-slate-200 bg-slate-50'}`}><legend className="px-2 text-sm">Slide {i + 1}</legend>{slideIssue(slide) && <p className="mb-3 text-sm font-medium text-amber-900">{slideIssue(slide)}</p>}<label className="block text-xs">Title<input className={inputClass} value={slide.title} onChange={e => patch({ slides: data.slides.map((s, index) => index === i ? { ...s, title: e.target.value } : s), presentationReviewed: false, presentationToken: '' })} /></label><label className="block text-xs">Content<textarea className={inputClass} rows={5} value={slide.body} onChange={e => patch({ slides: data.slides.map((s, index) => index === i ? { ...s, body: e.target.value } : s), presentationReviewed: false, presentationToken: '' })} /></label><button type="button" className={buttonClass} onClick={() => { if (window.confirm(`Remove slide ${i + 1}${slide.title.trim() ? `: "${slide.title.trim()}"` : ''}? Its content will be deleted from this presentation.`)) patch({ slides: data.slides.filter((_, index) => index !== i), presentationReviewed: false, presentationToken: '' }); }}>Remove slide</button></fieldset>)}
      </div>
      <div className="space-y-4 border-t border-slate-200 pt-5">
      <McmPresentationUpload kind={kind} learnerId={learnerId} activityId={activityId} month={data.month} disabled={disabled || historical || busy} uploaded={data.uploadedPresentation} onUploaded={file => patch({ uploadedPresentation: file, presentationReviewed: false, presentationToken: '' })} />
      {check('presentationReviewed', 'I have reviewed the slides and they accurately represent my own work.')}
      <button type="button" className={buttonClass} disabled={busy || !data.slides.length} onClick={() => void exportDeck()}>{busy ? 'Please wait…' : 'Export PowerPoint (.pptx)'}</button>
      {data.presentationToken && <p className="text-sm text-emerald-700">PowerPoint export complete. Editing your submission content or slides requires another reviewed export.</p>}
      </div>
      </section>

    </>}
    {notice && <p role="status" className="rounded-xl bg-blue-50 p-3 text-sm text-blue-900">{notice}</p>}
    {error && <p role="alert" className="rounded-xl bg-red-50 p-3 text-sm text-red-700">{error}</p>}
  </div>;
}
