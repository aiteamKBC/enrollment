import { useEffect, useId, useRef } from 'react';
import { createPortal } from 'react-dom';

export function ManualTimeSummary({ seconds }: { seconds: number }) {
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor(total / 60) % 60;
  const remainder = total % 60;
  const decimalHours = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 4 }).format(total / 3600);
  return <div className="space-y-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-left text-foreground-900">
    <p>You entered <strong>{hours} {hours === 1 ? 'hour' : 'hours'} and {minutes} {minutes === 1 ? 'minute' : 'minutes'}{remainder > 0 ? ` and ${remainder} ${remainder === 1 ? 'second' : 'seconds'}` : ''}</strong>.</p>
    <p className="text-lg font-semibold">{decimalHours} {total === 3600 ? 'hour' : 'hours'}</p>
    <p className="text-sm">Please check this duration before submitting. If it is incorrect, choose Edit time to change it.</p>
  </div>;
}

export function ManualTimeConfirmation({ duration, onConfirm, onEdit }: {
  duration: number | string; onConfirm: () => void; onEdit: () => void;
}) {
  const titleId = useId();
  const descriptionId = useId();
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    panel.current?.querySelector('button')?.focus();
    return () => {
      document.body.style.overflow = overflow;
      if (previous?.isConnected) previous.focus();
    };
  }, []);
  return createPortal(<div className="fixed inset-0 z-[150] flex items-center justify-center bg-foreground-950/40 p-4 backdrop-blur-sm">
    <div ref={panel} role="alertdialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={descriptionId}
      className="max-h-[90dvh] w-full max-w-md space-y-5 overflow-y-auto rounded-2xl border border-foreground-200 bg-white p-6 shadow-2xl"
      onKeyDown={event => {
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onEdit(); }
        if (event.key === 'Tab') {
          const buttons = panel.current!.querySelectorAll('button');
          const first = buttons[0], last = buttons[buttons.length - 1];
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        }
      }}>
      <h2 id={titleId} className="text-xl font-bold text-foreground-950">Check your time entry</h2>
      <div id={descriptionId}>{typeof duration === 'number' ? <ManualTimeSummary seconds={duration} />
        : <div className="space-y-3 rounded-xl border border-amber-200 bg-amber-50 p-4">
          <p>You entered <strong>{duration}</strong>.</p>
          <p className="text-sm">Check the value and its units before submitting. Choose Edit time if you need to correct it.</p>
        </div>}</div>
      <div className="grid grid-cols-2 gap-3">
        <button type="button" onClick={onEdit} className="rounded-xl border border-foreground-300 px-4 py-3 text-sm font-semibold focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-500">Edit time</button>
        <button type="button" onClick={onConfirm} className="rounded-xl bg-emerald-700 px-4 py-3 text-sm font-semibold text-white hover:bg-emerald-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-500">Confirm time</button>
      </div>
    </div>
  </div>, document.body);
}
