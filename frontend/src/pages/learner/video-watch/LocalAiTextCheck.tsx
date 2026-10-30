import { useContext, useEffect, useRef, useState } from 'react';
import { AssignmentAiCheckContext } from './AssignmentAiCheckContext';


type Segment = { start: number; end: number; flagged: boolean };
type Result = { status: string; message: string; advisoryOnly: true; segments: Segment[] };

export function LocalAiWritingHint() {
  const context = useContext(AssignmentAiCheckContext);
  return context?.enabled ? <p className="mb-2 text-xs leading-5 text-slate-600">Write in your own words and acknowledge any AI assistance. This check can make mistakes; built-in AI suggestions may also be flagged. A tutor must review any concern.</p> : null;
}

export function LocalAiTextCheck({ text, disabled }: { text: string; disabled: boolean }) {
  const context = useContext(AssignmentAiCheckContext);
  const [result, setResult] = useState<{ text: string; data: Result } | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const active = useRef<AbortController | null>(null);
  const generation = useRef(0);
  useEffect(() => {
    generation.current += 1;
    active.current?.abort(); active.current = null;
    setResult(null); setError(''); setBusy(false);
    return () => { generation.current += 1; active.current?.abort(); };
  }, [text, context?.learnerId, context?.learnerKind, context?.enabled]);
  if (!context?.enabled) return null;
  const check = async () => {
    if (active.current || disabled || !text.trim()) return;
    const controller = new AbortController();
    active.current = controller;
    const version = generation.current;
    const current = () => generation.current === version && !controller.signal.aborted;
    setBusy(true); setError(''); setResult(null);
    const timeout = window.setTimeout(() => controller.abort(), 120000);
    try {
      const endpoint = '/learner_api/reflection/assignment/ai-check/';
      const csrfResponse = await fetch(endpoint, { credentials: 'same-origin', signal: controller.signal });
      const csrf = await csrfResponse.json();
      if (!csrfResponse.ok || typeof csrf.csrfToken !== 'string') throw new Error('Request verification failed. Please reload and retry.');
      const response = await fetch(endpoint, {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf.csrfToken }, signal: controller.signal,
        body: JSON.stringify({ learnerId: context.learnerId, learnerKind: context.learnerKind, text }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'The AI writing check is unavailable.');
      let end = 0;
      if (!data || data.advisoryOnly !== true || typeof data.message !== 'string'
          || !['review_suggested', 'no_signal', 'insufficient_text', 'unsupported_text'].includes(data.status)
          || !Array.isArray(data.segments) || data.segments.some((s: Segment) => {
            const invalid = !s || !Number.isInteger(s.start) || !Number.isInteger(s.end) || s.start < end || s.end <= s.start || s.end > text.length || typeof s.flagged !== 'boolean';
            if (s) end = s.end;
            return invalid;
          })) throw new Error('The AI writing check returned an invalid result. Please retry.');
      if (current()) setResult({ text, data });
    } catch (e) {
      if (generation.current === version) setError(controller.signal.aborted ? 'The AI writing check timed out. You can retry; your answer is unchanged.' : e instanceof Error ? e.message : 'The AI writing check failed.');
    } finally {
      window.clearTimeout(timeout);
      if (generation.current === version) { setBusy(false); active.current = null; }
    }
  };
  const data = result?.text === text ? result.data : null;
  let offset = 0;
  const highlights = data?.segments.map((segment, index) => {
    const prefix = text.slice(offset, segment.start);
    offset = segment.end;
    return <span key={index}>{prefix}{segment.flagged ? <mark className="bg-amber-200 text-slate-900" title="Suspected AI writing — tutor review required">{text.slice(segment.start, segment.end)}</mark> : text.slice(segment.start, segment.end)}</span>;
  });
  return <div className="mt-3 space-y-2">
    <button type="button" className="rounded-lg border border-slate-300 px-3 py-2 text-xs font-semibold disabled:opacity-40" disabled={disabled || busy || !text.trim()} onClick={() => void check()}>{busy ? 'Checking…' : 'Check for possible AI writing'}</button>
    
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    {data && <div role="status" className="rounded-lg border border-slate-200 p-3 text-sm">
      <p className="font-semibold">{data.status === 'review_suggested' ? 'Suspected AI writing — ask your tutor to review' : data.status === 'no_signal' ? 'No AI-writing signal found' : 'Not assessed'}</p>
      {data.status !== 'no_signal' && <p className="mt-1">{data.message}</p>}
      {data.status === 'review_suggested' && <div aria-label="Passages for tutor review" className="mt-3 whitespace-pre-wrap break-words">{highlights}{text.slice(offset)}</div>}
    </div>}
  </div>;
}
