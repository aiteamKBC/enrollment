import type { ReactNode } from 'react';
import { AppIcon } from './AppIcon';

export function ReflectionModal({ children }: { children: ReactNode }) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Reflection"
      className="fixed inset-0 z-50 flex items-center justify-center bg-foreground-950/45 p-3 backdrop-blur-sm md:p-6"
    >
      <div className="max-h-[calc(100vh-1.5rem)] w-full max-w-6xl overflow-y-auto rounded-2xl md:max-h-[calc(100vh-3rem)]">
        {children}
      </div>
    </div>
  );
}

export function ReflectionChoicePopup({
  noun,
  onCancel,
  onAddReflection,
  onFinishWithoutReflection,
  submitting = false,
  error = null,
  onRecoverTiming,
  recoveringTiming = false,
}: {
  noun: string;
  onCancel: () => void;
  onAddReflection: () => void;
  onFinishWithoutReflection: () => void;
  submitting?: boolean;
  error?: string | null;
  onRecoverTiming?: () => void;
  recoveringTiming?: boolean;
}) {
  return (
    <div className="fixed inset-0 z-[100] grid place-items-center px-4 py-6">
      <div className="absolute inset-0 bg-black/40 backdrop-blur-[3px]" aria-hidden="true" />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="reflection-choice-title"
        className="relative w-full max-w-md rounded-2xl border border-background-300 bg-white p-6 text-center shadow-2xl"
      >
        <div className="mx-auto mb-4 grid h-14 w-14 place-items-center rounded-full bg-primary-100">
          <AppIcon className="ri-chat-quote-line text-2xl text-primary-700" />
        </div>
        <h1 id="reflection-choice-title" className="text-lg font-heading font-bold text-foreground-900">
          Do you want to complete a reflection?
        </h1>
        <p className="mt-2 text-sm leading-6 text-foreground-500">
          You can reflect on what you learned, or finish this {noun} without adding one.
        </p>

        <div className="mt-6 space-y-3">
          <button
            type="button"
            onClick={onAddReflection}
            disabled={submitting}
            className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-primary-700 px-4 py-3 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-primary-800"
          >
            <AppIcon className="ri-edit-line" />
            Yes, add reflection
          </button>
          <button
            type="button"
            onClick={onFinishWithoutReflection}
            disabled={submitting}
            className="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-background-300 bg-white px-4 py-3 text-sm font-semibold text-foreground-700 transition-colors hover:bg-background-50"
          >
            <AppIcon className="ri-check-line" />
            {submitting ? 'Finishing...' : 'No, finish without reflection'}
          </button>
          {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-left text-xs font-semibold text-red-700">{error}</p>}
          {error && onRecoverTiming && <button type="button" onClick={onRecoverTiming} disabled={submitting || recoveringTiming}
            className="w-full rounded-xl border border-primary-300 px-4 py-3 text-sm font-semibold text-primary-700">
            {recoveringTiming ? 'Restoring...' : 'Restore saved time'}
          </button>}
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            className="text-sm font-semibold text-foreground-500 underline-offset-4 hover:text-foreground-700 hover:underline"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
