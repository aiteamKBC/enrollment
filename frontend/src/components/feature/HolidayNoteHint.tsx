import { AppIcon } from './AppIcon';

// ============================================================================
// The curriculum team's hint for a week a holiday falls in.
//
// One component for every learner screen that lists weeks, so the wording and
// the look are the same wherever a learner meets it. It renders nothing on its
// own account: the server sends the text only for a week whose delivery a
// holiday actually closes and only once the author published it, so anything
// arriving here is already meant to be read.
// ============================================================================

export function HolidayNoteHint({ note, className = '' }: { note?: string | null; className?: string }) {
  const message = (note || '').trim();
  if (!message) return null;
  return (
    <p
      data-testid="learner-week-holiday-note"
      className={`flex items-start gap-1.5 rounded-lg border-l-2 border-amber-400 bg-amber-50 px-2.5 py-1.5 text-left text-[11px] leading-4 text-amber-900 ${className}`}
    >
      <AppIcon className="ri-calendar-event-line mt-px shrink-0 text-[12px] text-amber-600" />
      <span>
        <span className="font-semibold">From your curriculum team: </span>
        {message}
      </span>
    </p>
  );
}
