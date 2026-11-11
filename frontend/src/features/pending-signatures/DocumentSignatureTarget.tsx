import { useEffect, useRef, type ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import { signatureTargetId } from './targets';

/** The query selects an already-authorized rendered card. It cannot fetch a
 * different learner, invoke a signature, or open the admin editor. */
export function DocumentSignatureTarget({ documentKey, sourceId, revisionId, children }: { documentKey: string; sourceId?: string | number; revisionId?: string; children: ReactNode }) {
  const { search } = useLocation(); const target = new URLSearchParams(search).get('signature');
  const id = signatureTargetId(documentKey, sourceId, revisionId), selected = !!id && id === target;
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { if (selected) { ref.current?.scrollIntoView?.({ behavior: 'smooth', block: 'center' }); ref.current?.focus({ preventScroll: true }); } }, [selected, id, search]);
  return <div ref={ref} id={id} tabIndex={selected ? -1 : undefined} data-signature-target={selected || undefined} className={selected ? 'scroll-mt-6 rounded-xl ring-2 ring-primary-500 ring-offset-4 ring-offset-background-200 outline-none' : undefined}>{children}</div>;
}
