import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import { Link } from 'react-router-dom';
import { Bell } from 'lucide-react';
import { formatSystemTimestamp } from '@/lib/format';
import type { PendingSignatures } from './api';

export function PendingSignatureBadge({ count, label }: { count: number; label: string }) {
  return count > 0 ? <span aria-label={`${count} ${label}`} className="inline-flex min-w-5 shrink-0 items-center justify-center rounded-full bg-primary-600 px-1.5 py-0.5 text-[10px] font-semibold leading-4 text-white">{count}</span> : null;
}

export function SignatureNotifications({ data, loading, error, refresh }: { data: PendingSignatures; loading: boolean; error: string; refresh: () => void }) {
  const [open, setOpen] = useState(false); const root = useRef<HTMLDivElement>(null), bell = useRef<HTMLButtonElement>(null), panel = useRef<HTMLDivElement>(null);
  const id = useId(), title = useId();
  const close = () => setOpen(false);
  useEffect(() => {
    if (!open) return;
    const outside = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('pointerdown', outside); return () => document.removeEventListener('pointerdown', outside);
  }, [open]);
  const keys = (e: KeyboardEvent) => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); bell.current?.focus(); }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Home' || e.key === 'End') {
      e.preventDefault(); setOpen(true);
      window.requestAnimationFrame(() => {
        const items = Array.from(panel.current?.querySelectorAll<HTMLElement>('a, button') || []);
        if (!items.length) { panel.current?.focus(); return; }
        const current = items.indexOf(document.activeElement as HTMLElement);
        const next = e.key === 'Home' ? 0 : e.key === 'End' ? items.length - 1 : e.key === 'ArrowDown' ? (current + 1) % items.length : (current < 0 ? items.length - 1 : current - 1 + items.length) % items.length;
        items[next]?.focus();
      });
    }
  };
  return <div ref={root} className="relative shrink-0" onKeyDown={keys} onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) close(); }}>
    <button ref={bell} type="button" className="relative rounded-lg p-2 text-primary-700 hover:bg-primary-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-500" aria-label="Signature notifications" aria-expanded={open} aria-controls={id} aria-haspopup="dialog" onClick={() => setOpen((value) => !value)}>
      <Bell size={21} aria-hidden="true" />
      {data.count > 0 && <span className="absolute -right-1 -top-1"><PendingSignatureBadge count={data.count} label="pending learner signatures" /></span>}
    </button>
    {open && <div ref={panel} id={id} tabIndex={-1} role="dialog" aria-labelledby={title} className="fixed inset-x-4 top-20 z-[110] max-h-[70vh] overflow-auto rounded-xl border border-primary-100 bg-background-50 shadow-xl sm:absolute sm:left-auto sm:right-0 sm:top-full sm:mt-3 sm:w-[22rem]">
      <h2 id={title} className="border-b border-foreground-100 px-4 py-3 text-sm font-semibold">Notifications</h2>
      {loading && <p role="status" className="p-4 text-sm text-foreground-500">Loading signature requests…</p>}
      {error && <div className="space-y-2 p-4 text-sm"><p role="alert">{error}</p><button type="button" onClick={refresh} className="font-semibold text-primary-700 underline">Retry</button></div>}
      {!loading && !error && !data.count && <p className="p-4 text-sm text-foreground-500">No signatures required.</p>}
      <ul className="divide-y divide-foreground-100">{data.results.map((item) => <li key={item.id} className="space-y-1 p-4 text-sm">
        <p className="text-xs font-semibold text-primary-700">Signature Required</p><p className="font-semibold text-foreground-900">{item.title}</p>
        <p className="text-xs text-foreground-500">{item.revisionId ? `Revision R${item.revisionNumber}` : 'Original document'}{item.templateVersion ? ` · Template V${item.templateVersion}.0` : ''}</p>
        <p>Your signature is required.</p>
        {item.issuedAt && <p className="text-xs text-foreground-500">Issued {formatSystemTimestamp(item.issuedAt, { dateStyle: 'medium', timeStyle: 'short' })}</p>}
        <Link className="mt-2 inline-flex rounded-lg bg-primary-600 px-3 py-2 text-xs font-semibold text-white hover:bg-primary-700" to={item.href} onClick={close} aria-label={`Review & Sign — ${item.title}${item.revisionId ? ` R${item.revisionNumber}` : ''}`}>Review &amp; Sign</Link>
      </li>)}</ul>
    </div>}
  </div>;
}
