import { useEffect, useId, useRef, useState } from 'react';

export type AttendanceCorrectionAction = 'present' | 'absent' | 'reset';

/** Shared staff/coach actions; cancellation never deletes the Teams evidence. */
export function AttendanceCorrectionMenu({ learnerName, manualStatus, disabled = false, saving = false, onAction }: {
  learnerName: string; manualStatus?: 'present' | 'absent' | null; disabled?: boolean; saving?: boolean;
  onAction: (action: AttendanceCorrectionAction) => void;
}) {
  const [open, setOpen] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener('pointerdown', close);
    return () => document.removeEventListener('pointerdown', close);
  }, [open]);
  useEffect(() => { if (disabled || saving) setOpen(false); }, [disabled, saving]);
  const choose = (action: AttendanceCorrectionAction) => {
    setOpen(false);
    if (action === 'reset') setConfirmReset(true);
    else onAction(action);
    button.current?.focus();
  };
  return <div ref={root} className="relative inline-flex flex-col gap-1" onKeyDown={event => {
    if (event.key === 'Escape') { setOpen(false); setConfirmReset(false); button.current?.focus(); }
  }}>
    {manualStatus && <span className="text-xs text-primary-700">Manually adjusted</span>}
    <button ref={button} type="button" aria-label={`Edit attendance for ${learnerName}`} aria-expanded={open}
      aria-controls={id} disabled={disabled || saving} onClick={() => { setOpen(value => !value); setConfirmReset(false); }}
      className="rounded-lg border bg-white px-3 py-2 text-sm font-semibold disabled:opacity-50">
      {saving ? 'Saving…' : 'Edit attendance ▾'}
    </button>
    {open && <div id={id} role="group" aria-label={`Attendance actions for ${learnerName}`}
      className="z-20 flex min-w-48 flex-col gap-1 rounded-lg border bg-white p-2 shadow-lg">
      <button type="button" className="rounded px-3 py-2 text-left hover:bg-green-50" onClick={() => choose('present')}>✓ Mark present</button>
      <button type="button" className="rounded px-3 py-2 text-left hover:bg-red-50" onClick={() => choose('absent')}>✕ Mark absent</button>
      <button type="button" disabled={!manualStatus} className="rounded px-3 py-2 text-left hover:bg-gray-50 disabled:opacity-50" onClick={() => choose('reset')}>↶ Reset manual correction</button>
    </div>}
    {confirmReset && <div role="group" aria-label="Confirm attendance reset" className="rounded-lg border bg-white p-3 text-sm">
      <p>Remove your attendance correction and return to the original attendance result?</p>
      <button type="button" disabled={saving || disabled} onClick={() => { setConfirmReset(false); onAction('reset'); }}>Confirm reset</button>
      <button type="button" disabled={saving} onClick={() => { setConfirmReset(false); button.current?.focus(); }}>Cancel</button>
    </div>}
  </div>;
}
