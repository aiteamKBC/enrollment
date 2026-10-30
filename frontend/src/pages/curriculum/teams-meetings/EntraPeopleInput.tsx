import { useEffect, useId, useRef, useState } from 'react';
import { emailList } from "../module-builder/EmailChipsInput.helpers";
import { searchEntraPeople, type EntraPerson } from './entraDirectory';

const isEmail = (value: string) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value);

export function EntraPeopleInput({ value, onChange, label, single = false, includeGuests = false }: {
  value: string; onChange: (value: string) => void; label: string; single?: boolean; includeGuests?: boolean;
}) {
  type RemovedEmail = { email: string; index: number; remaining: string[] };
  const [draft, setDraft] = useState('');
  const [people, setPeople] = useState<EntraPerson[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  // Kept apart from `error` so the next search clears a failed one without
  // also clearing a message about the typed text.
  const [searchError, setSearchError] = useState('');
  const [hasMore, setHasMore] = useState(false);
  const [active, setActive] = useState(-1);
  const [undoStack, setUndoStack] = useState<RemovedEmail[]>([]);
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();
  const emails = emailList(value);

  useEffect(() => {
    setPeople([]);
    setActive(-1);
    setHasMore(false);
    setSearchError('');
    if (!open || draft.trim().length < 2 || draft.trim().length > 80) { setLoading(false); return; }
    const controller = new AbortController();
    setLoading(true);
    const timer = window.setTimeout(async () => {
      try {
        const result = await searchEntraPeople(draft, controller.signal, includeGuests);
        if (controller.signal.aborted) return;
        setPeople(result.people);
        setHasMore(result.hasMore);
      } catch (failure) {
        if (!controller.signal.aborted) setSearchError(failure instanceof Error ? failure.message : 'Entra search is unavailable.');
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }, 300);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [draft, includeGuests, open]);

  const commit = (raw: string) => {
    const additions = emailList(raw);
    if (!additions.length) return;
    if (!additions.every(isEmail) || (single && additions.length > 1)) {
      setError(single ? 'Choose one person or enter one full email address.' : 'Choose a search result or enter full email addresses.');
      return;
    }
    const next = single ? additions : [...emails, ...additions];
    onChange(Array.from(new Map(next.map(email => [email.toLowerCase(), email])).values()).join('\n'));
    setUndoStack([]);
    setDraft('');
    setPeople([]);
    setError('');
    setOpen(false);
  };

  const removeAt = (index: number) => {
    const email = emails[index];
    if (!email) return;
    const remaining = emails.filter((_, i) => i !== index);
    onChange(remaining.join('\n'));
    setUndoStack(stack => [...stack, { email, index, remaining }].slice(-20));
    input.current?.focus();
  };

  const undoLastRemoval = () => {
    const entry = undoStack[undoStack.length - 1];
    if (!entry) return;
    const current = emailList(value);
    // Do not reinsert a stale deletion after another edit changed the field.
    if (current.join('\n') !== entry.remaining.join('\n')) {
      setUndoStack([]);
      return;
    }
    const restored = [...current];
    restored.splice(Math.min(entry.index, restored.length), 0, entry.email);
    onChange(restored.join('\n'));
    setUndoStack(stack => stack.slice(0, -1));
  };

  return (
    <div className="relative" onKeyDown={event => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z' && !draft.trim() && undoStack.length) {
        event.preventDefault();
        undoLastRemoval();
      }
    }} onBlur={event => {
      if (!event.currentTarget.contains(event.relatedTarget as Node)) {
        if (draft.trim()) commit(draft);
        setOpen(false);
      }
    }}>
      <div className={`flex w-full flex-wrap items-center gap-1.5 rounded-lg border border-background-200 bg-background-50 px-2.5 py-2 focus-within:border-primary-300 ${single ? 'min-h-11' : 'min-h-[84px]'}`}>
        {emails.map((email, index) => (
          <span key={`${email}-${index}`} className={`inline-flex max-w-full items-center gap-1 rounded-full border px-2 py-1 text-[11px] font-semibold ${isEmail(email) ? 'border-primary-200 bg-primary-50 text-primary-800' : 'border-red-200 bg-red-50 text-red-700'}`}>
            <button type="button" className="truncate text-left hover:underline" title="Click to edit" onClick={() => {
              setDraft(email); onChange(emails.filter((_, i) => i !== index).join('\n')); setUndoStack([]); setOpen(true); input.current?.focus();
            }}>{email}</button>
            <button type="button" aria-label={`Remove ${email}`} className="rounded px-1 hover:bg-black/10" onClick={() => removeAt(index)}>×</button>
          </span>
        ))}
        <input ref={input} value={draft} role="combobox" aria-label={label}
          aria-autocomplete="list" aria-expanded={open && people.length > 0} aria-controls={listId}
          aria-activedescendant={active >= 0 && people[active] ? `${listId}-${active}` : undefined}
          placeholder={single && emails.length ? 'Search to replace...' : 'Search Entra by name or email...'}
          className="min-w-[160px] flex-1 bg-transparent p-1 text-[13px] text-foreground-900 outline-none placeholder:text-foreground-400"
          onChange={event => { setDraft(event.target.value); setError(''); setOpen(true); }}
          onFocus={() => { setError(''); setOpen(true); }}
          onKeyDown={event => {
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
              event.preventDefault(); setOpen(true);
              setActive(current => people.length ? (current < 0 ? (event.key === 'ArrowDown' ? 0 : people.length - 1)
                : (current + (event.key === 'ArrowDown' ? 1 : people.length - 1)) % people.length) : -1);
            } else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setOpen(false); }
            else if (event.key === 'Enter') { event.preventDefault(); commit(open && people[active] ? people[active].email : draft); }
            else if (event.key === 'Backspace' && !draft && emails.length) removeAt(emails.length - 1);
          }}
          onPaste={event => {
            const pasted = event.clipboardData.getData('text');
            if (emailList(pasted).length > 1 && emailList(pasted).every(isEmail)) { event.preventDefault(); commit(pasted); }
          }} />
      </div>
      {open && people.length > 0 && (
        <div id={listId} role="listbox" aria-label={`${label} results`} className="absolute z-50 mt-1 max-h-56 w-full overflow-auto rounded-xl border border-background-200 bg-white p-1 shadow-xl">
          {people.map((person, index) => (
            <button key={person.id} id={`${listId}-${index}`} role="option" aria-selected={active === index} type="button"
              className={`block w-full rounded-lg px-3 py-2 text-left text-[12px] hover:bg-primary-50 ${active === index ? 'bg-primary-50' : ''}`}
              onMouseDown={event => event.preventDefault()} onClick={() => { commit(person.email); input.current?.focus(); }}>
              <span className="block font-semibold text-foreground-900">{person.name}</span>
              <span className="block break-all text-foreground-500">{person.email}</span>
            </button>
          ))}
          {hasMore && <p className="px-3 py-2 text-[11px] text-foreground-500">Type more to narrow the results.</p>}
        </div>
      )}
      <div aria-live="polite" className="mt-1 text-[11px]">
        {error ? <span className="text-red-700">{error}</span> : loading ? <span>Searching Entra...</span>
          : searchError ? <span className="text-red-700">{searchError}</span>
          : open && draft.trim().length >= 2 && !people.length ? <span>No matching account. You can enter a full email address.</span> : null}
      </div>
    </div>
  );
}
