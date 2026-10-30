import { useEffect, useId, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { AppIcon } from '@/components/feature/AppIcon';
import styles from '../attendance.module.css';

export default function AbsenceReportDialog({ children, onClose, title = 'Report Absence' }: { children: ReactNode; onClose: () => void; title?: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const element = dialog.current!;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    element.showModal();
    document.body.style.overflow = 'hidden';
    return () => {
      element.close();
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, []);

  return createPortal(<dialog ref={dialog} aria-labelledby={titleId} className={`${styles.page} ${styles.absenceDialog}`}
    onCancel={event => { event.preventDefault(); onClose(); }}>
    <div className={styles.dialogHeading}>
      <h2 id={titleId}><AppIcon className="ri-calendar-event-line" />{title}</h2>
      <button type="button" aria-label={title === 'Report Absence' ? 'Close absence report' : title === 'Book Catchup Session' ? 'Close catch-up booking' : `Close ${title.toLowerCase()}`} onClick={onClose}><AppIcon className="ri-close-line" /></button>
    </div>
    {children}
  </dialog>, document.body);
}
