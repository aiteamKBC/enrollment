import { createContext, useContext } from 'react';


export type ToastType = 'success' | 'error' | 'warning' | 'info';


export interface ToastItem {
  id: string;
  type: ToastType;
  title: string;
  message?: string;
  duration?: number;
}


export interface ToastContextValue {
  toasts: ToastItem[];
  toast: (opts: Omit<ToastItem, 'id'>) => void;
  success: (title: string, message?: string) => void;
  error: (title: string, message?: string) => void;
  warning: (title: string, message?: string) => void;
  info: (title: string, message?: string) => void;
  dismiss: (id: string) => void;
}


export const ToastContext = createContext<ToastContextValue | null>(null);


export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within ToastProvider');
  return ctx;
}


/**
 * The toast API, or null when there is no ToastProvider above.
 *
 * For callers to which toasting is a nicety rather than a requirement —
 * AuthProvider is the case this was added for. It sits below ToastProvider in
 * the real tree, but making that a hard dependency would mean every test and
 * every future mounting of AuthProvider had to supply one to do anything at
 * all, for a message it does not need to function.
 */
export function useToastOptional(): ToastContextValue | null {
  return useContext(ToastContext);
}
