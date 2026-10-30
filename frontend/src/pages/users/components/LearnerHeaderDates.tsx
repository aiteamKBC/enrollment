import { useEffect, useState } from 'react';
import { updateEnrolmentUser } from '@/api/enrolmentUsers';

export function LearnerHeaderDates({ learnerId, startDate = '', endDate = '' }: {
  learnerId: string;
  startDate?: string;
  endDate?: string;
}) {
  const [start, setStart] = useState(startDate);
  const [end, setEnd] = useState(endDate);
  const [saved, setSaved] = useState({ start: startDate, end: endDate });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  useEffect(() => {
    setStart(startDate);
    setEnd(endDate);
    setSaved({ start: startDate, end: endDate });
    setError('');
    setMessage('');
  }, [learnerId, startDate, endDate]);

  const invalidRange = Boolean(start && end && end < start);
  const dirty = start !== saved.start || end !== saved.end;
  const inputClass = 'min-w-0 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-[13px] font-medium text-slate-800 outline-none hover:border-violet-400 focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-2 disabled:opacity-60';

  return (
    <form className="min-w-0 max-w-full" onSubmit={async (event) => {
      event.preventDefault();
      if (!dirty || saving || invalidRange) return;
      setSaving(true);
      setError('');
      setMessage('');
      try {
        const board = await updateEnrolmentUser(learnerId, { learnerStartDate: start || null, learnerEndDate: end || null });
        const next = { start: board.programme.learnerStartDate || '', end: board.programme.learnerEndDate || '' };
        setStart(next.start);
        setEnd(next.end);
        setSaved(next);
        setMessage('Dates saved');
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Could not save dates. Please try again.');
      } finally {
        setSaving(false);
      }
    }}>
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex min-w-0 flex-col gap-1.5">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Start date</span>
          <input type="date" value={start} max={end || '9999-12-31'} disabled={saving} className={inputClass} onChange={(event) => { setStart(event.target.value); setMessage(''); setError(''); }} />
        </label>
        <label className="flex min-w-0 flex-col gap-1.5">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">End date</span>
          <input type="date" value={end} min={start || undefined} max="9999-12-31" disabled={saving} aria-invalid={invalidRange} className={inputClass} onChange={(event) => { setEnd(event.target.value); setMessage(''); setError(''); }} />
        </label>
        {dirty && <button type="submit" disabled={saving || invalidRange} className="inline-flex items-center gap-1.5 rounded-lg bg-violet-100 px-3 py-2 text-[12px] font-semibold text-violet-800 hover:bg-violet-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-2 disabled:opacity-60">
          <i className={saving ? 'ri-loader-4-line animate-spin' : 'ri-save-line'} aria-hidden="true" />
          {saving ? 'Saving…' : 'Save dates'}
        </button>}
      </div>
      {(invalidRange || error) && <p role="alert" className="mt-2 max-w-sm text-xs text-red-700">{invalidRange ? 'End date must be on or after start date.' : error}</p>}
      {message && <p role="status" className="mt-2 text-xs text-emerald-700">{message}</p>}
    </form>
  );
}
