import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ChevronDown, Table2 } from 'lucide-react';
import { btnSecondary } from '@/pages/users/components/ui';

export function EditorTableMenu({ onCapture, operations }: {
  onCapture: () => void; operations: { label: string; run: () => void }[];
}) {
  const [open, setOpen] = useState(false);
  const [menuLeft, setMenuLeft] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const focusLast = useRef(false);
  useLayoutEffect(() => {
    if (!open) return;
    const position = () => {
      if (!root.current || !menu.current) return;
      const left = root.current.getBoundingClientRect().left;
      setMenuLeft(Math.max(8 - left, Math.min(0, window.innerWidth - 8 - left - menu.current.offsetWidth)));
    };
    position();
    const items = menu.current?.querySelectorAll<HTMLButtonElement>('button');
    (focusLast.current ? items?.[items.length - 1] : items?.[0])?.focus();
    window.addEventListener('resize', position);
    return () => window.removeEventListener('resize', position);
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const outside = (event: MouseEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', outside);
    return () => document.removeEventListener('mousedown', outside);
  }, [open]);
  return <div ref={root} className="relative" onKeyDown={(event) => {
    if (event.key === 'Escape') { event.preventDefault(); setOpen(false); trigger.current?.focus(); }
    if (event.key === 'Tab') setOpen(false);
  }}>
    <button ref={trigger} type="button" aria-label="Table" title="Table" aria-haspopup="menu" aria-expanded={open}
      className={`${btnSecondary} !min-h-9 !gap-1 !px-2 !py-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-400`}
      onMouseDown={(event) => { event.preventDefault(); onCapture(); }}
      onClick={() => { focusLast.current = false; setOpen(!open); }} onKeyDown={(event) => {
        if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) {
          event.preventDefault(); onCapture(); focusLast.current = event.key === 'ArrowUp'; setOpen(true);
        }
      }}><Table2 size={18} aria-hidden="true" /><ChevronDown size={14} aria-hidden="true" /></button>
    {open && <div ref={menu} role="menu" aria-label="Table operations" style={{ left: menuLeft }} className="absolute top-full z-20 mt-1 w-44 max-w-[calc(100vw-1rem)] rounded-lg border border-foreground-200 bg-background-50 p-1 shadow-lg"
      onKeyDown={(event) => {
        const items = Array.from(menu.current?.querySelectorAll<HTMLButtonElement>('button') ?? []);
        const current = items.indexOf(document.activeElement as HTMLButtonElement);
        const target = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1
          : event.key === 'ArrowDown' ? (current + 1) % items.length : event.key === 'ArrowUp' ? (current - 1 + items.length) % items.length : -1;
        if (target >= 0) { event.preventDefault(); items[target]?.focus(); }
      }}>
      {operations.map((operation) => <button key={operation.label} type="button" role="menuitem"
        className="block w-full rounded-md px-3 py-2 text-left text-sm hover:bg-background-100 focus-visible:bg-primary-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-400"
        onMouseDown={(event) => event.preventDefault()} onClick={() => { setOpen(false); operation.run(); }}>{operation.label}</button>)}
    </div>}
  </div>;
}
