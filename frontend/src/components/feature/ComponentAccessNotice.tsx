import { AppIcon } from './AppIcon';
import { COMPONENT_ACCESS_MESSAGE } from '@/lib/componentAccessWindow';
import { useComponentAccessWindow } from '@/hooks/useComponentAccessWindow';

export function ComponentAccessNotice({ onBack }: { onBack?: () => void }) {
  const access = useComponentAccessWindow();
  return (
    <div role="alert" className="rounded-2xl border border-amber-200 bg-amber-50 p-6 text-center md:p-8">
      <span className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-amber-100 text-amber-700">
        <AppIcon className="ri-time-line text-xl" />
      </span>
      <h2 className="font-heading text-base font-bold text-foreground-900">Learning components are currently closed</h2>
      <p className="mx-auto mt-2 max-w-md text-[13px] leading-relaxed text-foreground-600">
        {COMPONENT_ACCESS_MESSAGE} The current UK time is {access.currentTimeLabel || 'unavailable'}.
        Please return during the access window.
      </p>
      {onBack && (
        <button
          type="button"
          onClick={onBack}
          className="mt-5 inline-flex cursor-pointer items-center gap-1.5 rounded-lg bg-primary-600 px-4 py-2 text-[13px] font-semibold text-white transition-colors hover:bg-primary-700"
        >
          <AppIcon className="ri-arrow-left-line text-[13px]" /> Back to the training plan
        </button>
      )}
    </div>
  );
}
