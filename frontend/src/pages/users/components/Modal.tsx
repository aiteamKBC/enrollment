import { useEffect, useId, useRef, type ReactNode, type RefObject } from 'react';
import { createPortal } from 'react-dom';
// Explicit, not auto-imported: vitest.config.ts deliberately leaves
// unplugin-auto-import out, so a dialog rendered in a test would crash on it.
import { AppIcon } from '@/components/feature/AppIcon';

interface ModalProps {
  /** Mandatory access checks retain focus and cannot be dismissed. */
  dismissible?: boolean;
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  /** max-width tailwind class, e.g. "max-w-3xl" */
  size?: string;
  /** Optional styling for a specific feature; shared dialog behavior is unchanged. */
  className?: string;
  /**
   * Changing this scrolls the body back to the top — for a modal that steps
   * through content in place (e.g. question 3 -> 4) so each step starts at the top
   * instead of inheriting the previous scroll position.
   */
  scrollResetKey?: string | number;
  /** Trigger may temporarily lose focus while awaiting an API response. */
  returnFocusRef?: RefObject<HTMLElement | null>;
}

/**
 * Generic modal: dimmed backdrop, title bar, body, footer.
 * Traps focus, closes on Esc, and restores focus to the trigger on close.
 */
export function Modal({ title, onClose, children, footer, size = 'max-w-3xl', className = '', scrollResetKey, returnFocusRef, dismissible = true }: ModalProps) {
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);
  const returnFocus = useRef(returnFocusRef);
  returnFocus.current = returnFocusRef;

  useEffect(() => {
    if (scrollResetKey === undefined) return;
    const body = bodyRef.current;
    if (!body) return;
    // Assign scrollTop rather than calling scrollTo(): it's universally supported
    // and doesn't need a jsdom shim in tests.
    body.scrollTop = 0;
  }, [scrollResetKey]);

  // Callers commonly pass an inline arrow, so `onClose` is a new function on every
  // render. Read it through a ref inside the effect below, otherwise the effect
  // re-runs on each keystroke/selection and yanks focus back to the first control
  // — which scrolls the dialog and looks like the page jumping.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = dismissible ? onClose : () => undefined;

  useEffect(() => {
    previouslyFocused.current = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    // Focus the panel itself rather than its first control: focusing a control
    // deep in the dialog makes the browser scroll it into view, and preventScroll
    // keeps the page where the user left it either way.
    panel?.focus({ preventScroll: true });

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (e.key === 'Tab' && panel) {
        const nodes = Array.from(
          panel.querySelectorAll<HTMLElement>(
            'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
          )
        ).filter((el) => el.offsetParent !== null);
        if (nodes.length === 0) return;
        const first = nodes[0];
        const last = nodes[nodes.length - 1];
        if (e.shiftKey && (document.activeElement === first || document.activeElement === panel)) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };

    document.addEventListener('keydown', onKeyDown, true);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
      document.body.style.overflow = prevOverflow;
      // preventScroll: the trigger may be far down the page; restoring focus
      // shouldn't yank the viewport to it.
      (returnFocus.current?.current || previouslyFocused.current)?.focus({ preventScroll: true });
    };
    // Mount-only: see onCloseRef above.
  }, []);

  /* Portalled to <body>: rendered inline it sits deep inside the scrolled page, so
     opening it scrolled the viewport up to the panel — clicking "Answer" on row 19
     jumped to the top of the page. A portal also keeps `position: fixed` from being
     captured by an ancestor with a transform/filter. */
  const modal = (
    /* items-start (not items-center): a centred panel re-centres itself whenever
       its content height changes — picking a radio would visibly shift the whole
       dialog and scroll the answer you just clicked out of view. */
    <div className="fixed inset-0 z-[100] flex items-start justify-center p-4 overflow-y-auto">
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={() => onCloseRef.current()} aria-hidden="true" />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={`relative w-full ${size} mt-[5vh] mb-8 bg-background-50 rounded-2xl border border-foreground-200 shadow-2xl max-h-[90vh] flex flex-col overflow-hidden outline-none ${className}`}
      >
        <div className="flex items-start justify-between gap-4 px-5 py-4 border-b border-foreground-100 shrink-0">
          <h2 id={titleId} className="text-[15px] font-heading font-semibold text-foreground-900 leading-snug">{title}</h2>
          {dismissible && <button
            onClick={onClose}
            aria-label="Close"
            className="w-8 h-8 rounded-lg flex items-center justify-center text-foreground-400 hover:bg-background-100 hover:text-foreground-700 transition-smooth cursor-pointer shrink-0"
          >
            <AppIcon className="ri-close-line text-[18px]" />
          </button>}
        </div>
        <div ref={bodyRef} className="flex-1 overflow-y-auto p-5">{children}</div>
        {footer && <div className="flex items-center justify-end gap-3 px-5 py-4 border-t border-foreground-100 shrink-0">{footer}</div>}
      </div>
    </div>
  );

  // Guard for SSR/tests where document may not exist.
  return typeof document === 'undefined' ? modal : createPortal(modal, document.body);
}
