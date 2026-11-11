import { useCallback, useId, useState, type ReactNode } from 'react';
import { ChevronRight } from 'lucide-react';

export interface DocumentHistoryControl {
  expanded: boolean;
  toggle: () => void;
  panelId: string;
  onCountChange: (count: number) => void;
}

/** One instance per stable learner/document/source key in the profile. All
 * state is local presentation state; opening history never writes document data. */
export function DocumentHistory({ title, children }: {
  title: string;
  children: (value: { chevron: ReactNode; count: ReactNode; historyControl: DocumentHistoryControl }) => ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const [count, setCount] = useState(0);
  const panelId = useId();
  const toggle = useCallback(() => setExpanded(value => !value), []);
  return children({
    chevron: count > 0 ? <button type="button" aria-expanded={expanded} aria-controls={panelId}
      aria-label={`${expanded ? 'Collapse' : 'Expand'} version history for ${title}`}
      className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-primary-700 transition-colors hover:bg-primary-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2"
      onClick={toggle}><ChevronRight size={16} aria-hidden="true" className={`transition-transform duration-150 motion-reduce:transition-none ${expanded ? 'rotate-90' : ''}`} /></button> : null,
    count: count > 0 ? <span className="shrink-0 text-[11px] text-foreground-500">{count} {count === 1 ? 'version' : 'versions'}</span> : null,
    historyControl: { expanded, toggle, panelId, onCountChange: setCount },
  });
}
