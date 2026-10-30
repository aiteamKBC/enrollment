import { useId, useRef, useState, type KeyboardEvent } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import { inputClass } from '../components/ui';
import { RichText } from '../wizard/layout/RichText';
import { MAX_TEXT_LENGTH, type TextSlot } from '../wizard/layout/texts';
import { EMPTY_HISTORY, record, redo, undo, type ChangeKind } from './textHistory';

type Wrap = { before: string; after: string; placeholder: string };

const TOOLS: { label: string; icon: string; apply: Wrap | 'heading' | 'list' }[] = [
  { label: 'Make heading', icon: 'ri-heading', apply: 'heading' },
  { label: 'Bold', icon: 'ri-bold', apply: { before: '**', after: '**', placeholder: 'bold text' } },
  { label: 'Italic', icon: 'ri-italic', apply: { before: '*', after: '*', placeholder: 'italic text' } },
  { label: 'Bullet list', icon: 'ri-list-unordered', apply: 'list' },
  { label: 'Insert link', icon: 'ri-link', apply: { before: '[', after: '](https://)', placeholder: 'link text' } },
];

/**
 * Edit one piece of the wizard's wording. Formatted text gets a toolbar that
 * writes the wizard's formatting marks (see richFormat.ts) and a live preview
 * drawn exactly as learners will see it.
 *
 * Every box has its own undo/redo — Undo and Redo buttons, and Ctrl+Z /
 * Ctrl+Y (Ctrl+Shift+Z; ⌘ on a Mac) — covering typing, toolbar formatting and
 * resetting to the standard wording (see textHistory.ts).
 */
export function TextEditor({
  slot,
  texts,
  onChange,
}: {
  slot: TextSlot;
  texts: Record<string, string> | undefined;
  /** `undefined` puts the standard wording back. */
  onChange: (value: string | undefined) => void;
}) {
  const id = useId();
  const ref = useRef<HTMLTextAreaElement>(null);
  const edited = typeof texts?.[slot.key] === 'string';
  // The raw edit while editing — even when blank, so the box can be cleared and
  // retyped instead of snapping back to the standard wording. (The eligibility
  // form builder uses this editor too, for slots of its own registry.)
  const value = edited ? texts![slot.key] : slot.default;
  const empty = edited && value.trim() === '';
  const tooLong = value.length > MAX_TEXT_LENGTH;

  const [history, setHistory] = useState(EMPTY_HISTORY);

  /** Show `next`; the standard wording is stored as "no edit". */
  const show = (next: string) => onChange(next === slot.default ? undefined : next);

  /** A change made in this box: recorded for undo, then shown. */
  const set = (next: string, kind: ChangeKind = 'type') => {
    if (next === value) return;
    setHistory((h) => record(h, value, kind, Date.now()));
    show(next);
  };

  const step = (direction: 'undo' | 'redo') => {
    const result = direction === 'undo' ? undo(history, value) : redo(history, value);
    if (!result) return;
    setHistory(result.history);
    show(result.value);
  };

  /** Ctrl+Z / ⌘Z undo; Ctrl+Y, Ctrl+Shift+Z / ⌘⇧Z redo. Other keys pass through. */
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
    const key = e.key.toLowerCase();
    if (key === 'z' && !e.shiftKey) {
      e.preventDefault();
      step('undo');
    } else if (key === 'y' || (key === 'z' && e.shiftKey)) {
      e.preventDefault();
      step('redo');
    }
  };

  const historyButton = 'inline-flex cursor-pointer items-center gap-1 rounded-md border border-foreground-200 px-2 py-0.5 text-[11.5px] font-medium text-foreground-600 hover:bg-background-100 disabled:cursor-not-allowed disabled:opacity-40';

  /** Apply a toolbar action to the selection, keeping the caret in a useful place. */
  const apply = (tool: (typeof TOOLS)[number]['apply']) => {
    const el = ref.current;
    if (!el) return;
    const start = el.selectionStart ?? value.length;
    const end = el.selectionEnd ?? value.length;
    let next: string;
    let caret: [number, number];
    if (tool === 'heading' || tool === 'list') {
      const marker = tool === 'heading' ? '## ' : '- ';
      const lineStart = value.lastIndexOf('\n', start - 1) + 1;
      const block = value.slice(lineStart, end);
      const marked = block.split('\n').map((l) => (l.startsWith(marker) ? l.slice(marker.length) : `${marker}${l}`)).join('\n');
      next = value.slice(0, lineStart) + marked + value.slice(end);
      caret = [lineStart, lineStart + marked.length];
    } else {
      const selected = value.slice(start, end) || tool.placeholder;
      next = value.slice(0, start) + tool.before + selected + tool.after + value.slice(end);
      caret = [start + tool.before.length, start + tool.before.length + selected.length];
    }
    set(next, 'action');
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(caret[0], caret[1]);
    });
  };

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2">
          <label htmlFor={id} className="text-[12px] font-medium text-foreground-600">{slot.label}</label>
          {edited && <span className="rounded-full bg-primary-100 px-2 py-0.5 text-[10.5px] font-medium text-primary-700">Edited</span>}
        </span>
        <span className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className={historyButton}
            disabled={history.past.length === 0}
            aria-label={`Undo change to ${slot.label}`}
            title="Undo (Ctrl+Z)"
            onClick={() => step('undo')}
          >
            <AppIcon className="ri-arrow-go-back-line" />Undo
          </button>
          <button
            type="button"
            className={historyButton}
            disabled={history.future.length === 0}
            aria-label={`Redo change to ${slot.label}`}
            title="Redo (Ctrl+Y)"
            onClick={() => step('redo')}
          >
            <AppIcon className="ri-arrow-go-forward-line" />Redo
          </button>
          {edited && (
            <button type="button" className="cursor-pointer text-[11.5px] font-medium text-primary-600 hover:underline" onClick={() => set(slot.default, 'action')}>
              Reset to standard wording
            </button>
          )}
        </span>
      </div>

      {slot.kind === 'line' ? (
        <input id={id} className={inputClass} value={value} onChange={(e) => set(e.target.value)} onKeyDown={onKeyDown} aria-invalid={empty || tooLong || undefined} />
      ) : (
        <>
          {slot.kind === 'rich' && (
            <div role="toolbar" aria-label={`Format ${slot.label}`} aria-controls={id} className="flex flex-wrap gap-1">
              {TOOLS.map((tool) => (
                <button
                  key={tool.label}
                  type="button"
                  aria-label={tool.label}
                  title={tool.label}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => apply(tool.apply)}
                  className="flex h-7 w-7 cursor-pointer items-center justify-center rounded-md border border-foreground-200 text-foreground-600 hover:bg-background-100"
                >
                  <AppIcon className={tool.icon} />
                </button>
              ))}
            </div>
          )}
          <textarea
            id={id}
            ref={ref}
            rows={slot.kind === 'rich' ? Math.min(18, Math.max(6, value.split('\n').length + 1)) : 3}
            className={`${inputClass} font-[inherit] leading-relaxed`}
            value={value}
            onChange={(e) => set(e.target.value)}
            onKeyDown={onKeyDown}
            aria-invalid={empty || tooLong || undefined}
          />
          {slot.kind === 'rich' && (
            <div className="rounded-lg border border-dashed border-foreground-200 bg-background-50 p-3">
              <p className="mb-2 text-[10.5px] font-semibold uppercase tracking-wider text-foreground-400">Preview</p>
              <RichText source={value} classes={{ container: 'space-y-2 text-[13px] leading-relaxed text-foreground-700' }} />
            </div>
          )}
        </>
      )}
      {slot.kind === 'rich' && (
        <p className="text-[11px] text-foreground-400">## heading · **bold** · *italic* · - bullet · [link text](https://…). A blank line starts a new paragraph.</p>
      )}
      {slot.hint && <p className="text-[11px] text-amber-700">{slot.hint}</p>}
      {empty && <p role="alert" className="text-[11px] text-red-600">Empty wording falls back to the standard wording when published.</p>}
      {tooLong && <p role="alert" className="text-[11px] text-red-600">Too long — keep it under {MAX_TEXT_LENGTH.toLocaleString()} characters.</p>}
    </div>
  );
}
