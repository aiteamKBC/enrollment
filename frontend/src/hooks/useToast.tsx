import { ToastContext, useToast, useToastOptional, type ToastType, type ToastItem, type ToastContextValue } from './useToast.helpers';
export type { ToastType, ToastItem } from './useToast.helpers';
import { createContext, useContext, useState, useCallback, useMemo, type ReactNode } from 'react';

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const dismiss = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  const toast = useCallback((opts: Omit<ToastItem, 'id'>) => {
    const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const item: ToastItem = { ...opts, id, duration: opts.duration ?? 4000 };
    setToasts(prev => [...prev, item]);
    if (item.duration && item.duration > 0) {
      setTimeout(() => dismiss(id), item.duration);
    }
  }, [dismiss]);

  const success = useCallback((title: string, message?: string) => toast({ type: 'success', title, message }), [toast]);
  const error = useCallback((title: string, message?: string) => toast({ type: 'error', title, message }), [toast]);
  const warning = useCallback((title: string, message?: string) => toast({ type: 'warning', title, message }), [toast]);
  const info = useCallback((title: string, message?: string) => toast({ type: 'info', title, message }), [toast]);

  const value = useMemo<ToastContextValue>(() => ({
    toasts, toast, success, error, warning, info, dismiss,
  }), [toasts, toast, success, error, warning, info, dismiss]);

  return <ToastContext.Provider value={value}>{children}</ToastContext.Provider>;
}
