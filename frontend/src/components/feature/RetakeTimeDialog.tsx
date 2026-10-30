import { useEffect, useId, useRef } from 'react';
import { createPortal } from 'react-dom';

export type RetakeTimeChoice = 'keep' | 'new';

export function RetakeTimeDialog({ onChoose, onClose }: {
  onChoose: (choice: RetakeTimeChoice) => void;
  onClose: () => void;
}) {
  const title = useId();
  const panel = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    panel.current?.querySelector('button')?.focus();
    return () => { document.body.style.overflow = overflow; if (previous?.isConnected) previous.focus(); };
  }, []);
  return createPortal(<div className="fixed inset-0 z-[110] flex items-center justify-center bg-black/40 p-4">
    <div ref={panel} role="dialog" aria-modal="true" aria-labelledby={title}
      className="w-full max-w-md space-y-4 rounded-2xl bg-white p-6 shadow-xl"
      onKeyDown={event => {
        if (event.key === 'Escape') { event.preventDefault(); close.current(); }
        if (event.key === 'Tab') {
          const buttons = panel.current!.querySelectorAll('button');
          const first = buttons[0], last = buttons[buttons.length - 1];
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        }
      }}>
      <h2 id={title} className="text-lg font-bold">Choose time for this re-attempt</h2>
      <p className="text-sm text-foreground-600">After a successful attempt, which completion date and study duration should count in your learning reports?</p>
      <button type="button" className="w-full rounded-xl border border-primary-300 p-3 text-left font-semibold focus-visible:outline-primary-500" onClick={() => onChoose('keep')}>
        Keep current date and duration
      </button>
      <button type="button" className="w-full rounded-xl border border-primary-300 p-3 text-left font-semibold focus-visible:outline-primary-500" onClick={() => onChoose('new')}>
        Use new attempt date and duration
      </button>
      <p className="text-xs text-foreground-500">If this attempt is unsuccessful, your currently accepted time stays unchanged. Your attempt history is kept.</p>
      <button type="button" className="rounded-lg px-4 py-2 font-semibold" onClick={onClose}>Cancel</button>
    </div>
  </div>, document.body);
}
