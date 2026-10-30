import { useEffect, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
// Imported explicitly rather than relying on the auto-import plugin, which only
// runs in the app build — the drawer is rendered by page tests too.
import { AppIcon } from './AppIcon';

interface RightSlidePanelProps {
  isOpen: boolean;
  onClose: () => void;
  children: ReactNode;
  title?: string;
  width?: string;
  coloredHeader?: boolean;
}

export function RightSlidePanel({ isOpen, onClose, children, title, width = 'w-[440px]', coloredHeader = false }: RightSlidePanelProps) {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => {
      document.body.style.overflow = '';
    };
  }, [isOpen]);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape' && isOpen) onClose();
    }
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  if (!mounted) return null;

  const panel = (
    <>
      {/* Backdrop overlay — no blur */}
      <div
        onClick={onClose}
        className={`workspace-drawer-backdrop fixed inset-0 z-40 bg-foreground-950/20 transition-opacity duration-500 ease-out ${
          isOpen ? 'opacity-100' : 'opacity-0 pointer-events-none'
        }`}
      />

      {/* Slide panel — starts from very top, no margin */}
      <div
        className={`workspace-drawer fixed top-0 right-0 h-full z-50 bg-background-50 border-l border-foreground-200 shadow-xl shadow-foreground-950/10 flex flex-col transition-transform duration-500 ease-out ${width} max-w-[calc(100vw-2rem)] !mt-0 ${
          isOpen ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        {/* Header */}
        {title && (
          <div className={`workspace-drawer-header flex items-center justify-between px-5 py-4 shrink-0 ${
            coloredHeader
              ? 'bg-gradient-to-r from-primary-950 via-primary-900 to-primary-800 border-b border-white/10'
              : 'border-b border-foreground-200'
          }`}>
            <h3 className={`text-sm font-heading font-semibold ${coloredHeader ? 'text-white' : 'text-foreground-900'}`}>{title}</h3>
            <button
              onClick={onClose}
              className={`w-8 h-8 rounded-lg flex items-center justify-center transition-smooth cursor-pointer ${
                coloredHeader
                  ? 'text-white/70 hover:text-white hover:bg-white/15'
                  : 'text-foreground-400 hover:text-foreground-700 hover:bg-background-100'
              }`}
            >
              <AppIcon className="ri-close-line text-lg"></AppIcon>
            </button>
          </div>
        )}

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-5">
          {children}
        </div>
      </div>
    </>
  );

  return createPortal(panel, document.body);
}
