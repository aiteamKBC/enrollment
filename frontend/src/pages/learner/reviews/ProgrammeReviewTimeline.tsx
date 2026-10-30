import { useCallback, useEffect, useRef, useState } from 'react';
import { CalendarDays, Check, ChevronLeft, ChevronRight, Circle, Clock3, Signature, X } from 'lucide-react';
import type { TimelineStatus } from './reviewTimelineStatus';
import styles from './programmeReviewTimeline.module.css';
import selection from './reviewMarkerSelection.module.css';

export interface TimelineReview {
  id: string;
  title: string;
  date: string | null;
  status: TimelineStatus;
  sequence?: number | null;
  markerSelection?: boolean;
}

const statusLabels: Record<TimelineStatus, string> = {
  planned: 'Planned', scheduled: 'Scheduled', 'in-progress': 'In Progress', 'awaiting-signature': 'Awaiting signatures', attended: 'Attended', missed: 'Missed',
};

const icons = { planned: Circle, scheduled: CalendarDays, 'in-progress': Clock3, 'awaiting-signature': Signature, attended: Check, missed: X };

function dateLabel(value: string | null) {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return 'Date to confirm';
  const date = new Date(`${value}T12:00:00Z`);
  if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== value) return 'Date to confirm';
  return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(date);
}

export default function ProgrammeReviewTimeline({ label, items, activeId, onSelect, showStatus = false }: {
  label: string; items: TimelineReview[]; activeId: string | null; onSelect: (id: string) => void; showStatus?: boolean;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [scrollPosition, setScrollPosition] = useState({ left: 0, max: 0 });
  const updateScrollPosition = useCallback(() => {
    const viewport = scrollRef.current;
    if (!viewport) return;
    const max = Math.max(0, viewport.scrollWidth - viewport.clientWidth);
    const left = Math.max(0, viewport.scrollLeft);
    setScrollPosition(current => current.left === left && current.max === max ? current : { left, max });
  }, []);

  useEffect(() => {
    const viewport = scrollRef.current;
    if (!viewport) return;
    updateScrollPosition();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(updateScrollPosition);
    observer?.observe(viewport);
    if (viewport.firstElementChild) observer?.observe(viewport.firstElementChild);
    window.addEventListener('resize', updateScrollPosition);
    return () => { observer?.disconnect(); window.removeEventListener('resize', updateScrollPosition); };
  }, [items.length, updateScrollPosition]);

  const scrollByPage = (direction: -1 | 1) => {
    const viewport = scrollRef.current;
    if (!viewport) return;
    const max = Math.max(0, viewport.scrollWidth - viewport.clientWidth);
    viewport.scrollLeft = Math.max(0, Math.min(max, viewport.scrollLeft + direction * Math.max(100, viewport.clientWidth * .8)));
    updateScrollPosition();
  };

  if (!items.length) return null;
  return <section className={styles.timeline} aria-label={`${label} programme timeline`}>
    <div className={styles.heading}>
      <div><p className={styles.eyebrow}>YOUR PROGRAMME JOURNEY</p><h2>{label} timeline</h2></div>
      <div className={styles.headingActions}>
        <span className={styles.count}>{items.length} {items.length === 1 ? 'meeting' : 'meetings'}</span>
        {scrollPosition.max > 1 && <div className={styles.scrollControls} role="group" aria-label="Timeline scroll controls">
          <button type="button" aria-label={`Scroll to previous ${label.toLowerCase()} meetings`} disabled={scrollPosition.left <= 1} onClick={() => scrollByPage(-1)}><ChevronLeft size={18} aria-hidden="true" /></button>
          <button type="button" aria-label={`Scroll to next ${label.toLowerCase()} meetings`} disabled={scrollPosition.left >= scrollPosition.max - 1} onClick={() => scrollByPage(1)}><ChevronRight size={18} aria-hidden="true" /></button>
        </div>}
      </div>
    </div>
    <div ref={scrollRef} className={styles.scroll} role="region" tabIndex={0} aria-label={`Scroll through ${label.toLowerCase()} meetings`} onScroll={updateScrollPosition}>
      <ol className={styles.steps}>{items.map((item, index) => {
        const Icon = icons[item.status];
        const statusLabel = statusLabels[item.status];
        const sequence = item.sequence ?? index + 1;
        return <li key={item.id} className={styles.step} data-status={item.status} data-active={item.id === activeId} data-selection={item.markerSelection ? 'marker' : undefined}>
          <button type="button" className={`${styles.stepLink} ${selection.control}`} data-selection={item.markerSelection ? 'marker' : undefined} aria-label={`${item.title}, ${dateLabel(item.date)}, ${statusLabel}`} aria-pressed={item.id === activeId} onClick={() => onSelect(item.id)}>
            <span className={styles.date}>{dateLabel(item.date)}</span>
            <span className={`${styles.marker} ${selection.marker}`}><Icon size={14} strokeWidth={2.4} aria-hidden="true" /></span>
            <span className={styles.title}>{label === 'Monthly coaching' ? 'MCM' : 'Review'} {sequence}</span>
            {showStatus && <span className={styles.status}>{statusLabel}</span>}
          </button>
        </li>;
      })}</ol>
    </div>
    <ul className={styles.legend} aria-label="Timeline status colours">
      {(Object.keys(statusLabels) as TimelineStatus[]).map(status =>
        <li key={status} data-status={status}><span aria-hidden="true" />{statusLabels[status]}</li>)}
    </ul>
  </section>;
}
