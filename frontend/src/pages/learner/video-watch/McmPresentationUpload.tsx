import { useEffect, useId, useRef, useState } from 'react';
import { getEvidenceDownloadUrl, uploadEvidence } from '@/api/evidence';
import type { LearnerKind } from '@/api/learnerDetail';

export function McmPresentationUpload({ kind, learnerId, activityId, month, disabled, uploaded, onUploaded }: {
  kind: LearnerKind; learnerId: string; activityId: string; month: string; disabled: boolean;
  uploaded?: { id: string; name: string }; onUploaded: (file: { id: string; name: string }) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const tooltipId = useId();
  const context = `${kind}:${learnerId}:${activityId}:${month}`;
  const currentContext = useRef(context); currentContext.current = context;
  const disabledRef = useRef(disabled); disabledRef.current = disabled;
  const mounted = useRef(true);
  const active = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { setError(''); }, [context]);
  const upload = async (file: File) => {
    if (disabled || active.current) return;
    if (!/\.pptx?$/i.test(file.name) || !file.size || file.size > 50 * 1024 * 1024) {
      setError('Choose a non-empty .ppt or .pptx file up to 50 MB.'); return;
    }
    active.current = true; setBusy(true); setError('');
    try {
      const result = await uploadEvidence(kind, learnerId, file, activityId, { componentId: activityId, componentType: 'assignment', evidenceDescription: `Learner-uploaded MCM presentation (${month})` });
      if (!mounted.current || currentContext.current !== context || disabledRef.current) return;
      if (result.status !== 'approved' || !result.id) throw new Error('This presentation has not passed the file checks. Upload an approved file before continuing.');
      onUploaded({ id: result.id, name: result.filename || file.name });
    } catch (e) {
      if (mounted.current && currentContext.current === context) setError(e instanceof Error ? e.message : 'Could not upload your presentation. Please retry.');
    } finally {
      active.current = false;
      if (mounted.current) setBusy(false);
    }
  };
  const download = async () => {
    if (!uploaded || active.current) return;
    active.current = true; setBusy(true); setError('');
    try {
      const url = await getEvidenceDownloadUrl(kind, learnerId, uploaded.id);
      if (!mounted.current || currentContext.current !== context) return;
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = uploaded.name; anchor.rel = 'noopener'; anchor.target = '_blank'; anchor.click();
    } catch { if (mounted.current && currentContext.current === context) setError('Could not open the presentation. Please retry.'); }
    finally { active.current = false; if (mounted.current) setBusy(false); }
  };
  return <div className="space-y-2">
    <div className="group relative inline-block">
      <button type="button" disabled={disabled || busy} aria-describedby={tooltipId} onClick={() => input.current?.click()} className="rounded-xl border border-slate-300 bg-white px-4 py-2 text-sm font-semibold disabled:opacity-40">{busy ? 'Please wait...' : uploaded ? 'Replace MCM PowerPoint' : 'Upload MCM PowerPoint'}</button>
      <span id={tooltipId} role="tooltip" className="pointer-events-none absolute bottom-full left-0 z-20 mb-2 hidden w-72 rounded-lg bg-slate-900 p-3 text-xs text-white shadow-lg group-hover:block group-focus-within:block">Upload the MCM PowerPoint prepared by you, the learner/user, instead of exporting one from the system.</span>
    </div>
    <input ref={input} type="file" accept=".ppt,.pptx" aria-label="Upload your MCM PowerPoint" className="hidden" disabled={disabled || busy} onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void upload(file); }} />
    <p className="text-xs text-slate-600">Upload your own .ppt or .pptx (up to 50 MB), or export a PowerPoint from this page. Either option satisfies the presentation requirement after review.</p>
    {uploaded && <p className="text-sm text-emerald-700">Uploaded: <button type="button" disabled={busy} onClick={() => void download()} className="break-all underline">{uploaded.name}</button>. Confirm that you have reviewed this presentation.</p>}
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
  </div>;
}
