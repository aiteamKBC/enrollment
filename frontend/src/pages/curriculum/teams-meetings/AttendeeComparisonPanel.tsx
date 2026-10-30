import { AppIcon } from '@/components/feature/AppIcon';
import { InlineError } from '../shared/entities/ui';
import { type AttendeeComparison, type ComparedPerson } from './attendeeComparison';

/** One tallied line of the summary. */
function CountRow({ icon, tone, label, value }: { icon: string; tone: string; label: string; value: number }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className={`inline-flex items-center gap-1.5 text-[12px] font-semibold ${tone}`}>
        <AppIcon className={`${icon} text-sm`}></AppIcon>
        {label}
      </span>
      <span className={`text-[13px] font-bold tabular-nums ${tone}`}>{value}</span>
    </div>
  );
}

/** A named list of people, or nothing at all when there are none to name. */
function PeopleList({ title, tone, people }: { title: string; tone: string; people: ComparedPerson[] }) {
  if (!people.length) return null;
  return (
    <div className="space-y-1.5">
      <h5 className={`text-[11px] font-bold uppercase tracking-wider ${tone}`}>{title}</h5>
      <ul aria-label={title} className="space-y-1">
        {people.map(person => (
          <li key={person.email} className="rounded-lg bg-background-50 px-2.5 py-1.5">
            {person.name && <p className="text-[12px] font-semibold text-foreground-700">{person.name}</p>}
            <p className="break-all text-[11px] text-foreground-500">{person.email}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * What Microsoft holds against what the LMS published, and who is only local.
 *
 * Read-only in both directions: nothing here can be clicked into the invitation
 * fields above it. Somebody found only on Teams is reported, never inserted --
 * adding them to the editable list would make the next Save invite a person
 * nobody in the LMS chose.
 */
export function AttendeeComparisonPanel({ comparison, comparing, error, pending, onCompare, disabled }: {
  comparison: AttendeeComparison | null;
  comparing: boolean;
  error: string | null;
  /** Addresses typed into the form that no save has published yet. */
  pending: string[];
  onCompare: () => void;
  disabled?: boolean;
}) {
  const checked = comparison && Number.isFinite(Date.parse(comparison.checkedAt))
    ? new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: true })
      .format(new Date(comparison.checkedAt))
    : '';
  return (
    <div className="space-y-3 rounded-xl border border-background-200 bg-background-50/50 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h4 className="text-[11px] font-bold uppercase tracking-wider text-foreground-400">Microsoft Teams comparison</h4>
          <p className="mt-0.5 text-[11px] text-foreground-500">
            Reads who is on the Microsoft meeting right now and compares them with the invitations the LMS has published.
            It sends nothing and changes nothing.
          </p>
        </div>
        <button
          type="button"
          onClick={onCompare}
          disabled={comparing || disabled}
          title="Read the current Microsoft attendee list. No invitation is sent and no session is moved."
          className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-lg border border-background-300 bg-white px-3 text-[12px] font-bold text-foreground-700 transition-smooth hover:bg-background-100 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <AppIcon className={comparing ? 'ri-loader-4-line animate-spin text-sm' : 'ri-git-compare-line text-sm'}></AppIcon>
          {comparing ? 'Comparing…' : 'Compare with Teams'}
        </button>
      </div>

      {error && <InlineError message={error} />}

      {comparison && (
        /* The previous reading stays put while a new one runs, so the panel
           does not empty and refill under the reader. The button says which
           it is. */
        <div aria-live="polite" className={`space-y-3 ${comparing ? 'opacity-60' : ''}`}>
          <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
            <CountRow icon="ri-user-line" tone="text-foreground-600" label="LMS invited" value={comparison.lmsCount} />
            <CountRow icon="ri-microsoft-line" tone="text-foreground-600" label="Teams attendees" value={comparison.teamsCount} />
            <CountRow icon="ri-check-line" tone="text-emerald-700" label="Matching" value={comparison.matchingCount} />
            <CountRow icon="ri-error-warning-line" tone={comparison.extraCount ? 'text-amber-700' : 'text-foreground-500'}
              label="Extra on Teams" value={comparison.extraCount} />
            <CountRow icon="ri-close-line" tone={comparison.missingCount ? 'text-red-700' : 'text-foreground-500'}
              label="Missing on Teams" value={comparison.missingCount} />
          </div>

          {comparison.status === 'match' && (
            <p className="text-[12px] font-semibold text-emerald-700">Teams attendees match the LMS invitation list.</p>
          )}

          <PeopleList title="Extra on Teams" tone="text-amber-700" people={comparison.extraOnTeams} />
          <PeopleList title="Missing from Teams" tone="text-red-700" people={comparison.missingFromTeams} />

          {comparison.aliasPossible && (
            /* Microsoft stores an internal alias under its mailbox's primary
               address, so one pair here can be a single person under two
               spellings rather than one loss and one gate-crasher. Both are
               shown: hiding the pair would also hide a real swap. */
            <p className="text-[11px] text-foreground-500">
              An internal alias can come back from Microsoft as its mailbox&rsquo;s primary address, so one pair above may be
              the same person under two spellings. Check the addresses before acting on them.
            </p>
          )}

          {checked && <p className="text-[11px] text-foreground-400">Last checked: {checked}</p>}
        </div>
      )}

      {pending.length > 0 && (
        /* Typed but never saved, so Microsoft was never asked about them.
           Naming them here is what stops the counts above reading as a loss. */
        <div className="space-y-1.5 border-t border-background-200 pt-2">
          <h5 className="text-[11px] font-bold uppercase tracking-wider text-foreground-400">Not published yet</h5>
          <ul aria-label="Not published yet" className="space-y-0.5">
            {pending.map(email => (
              <li key={email} className="break-all text-[11px] text-foreground-500">{email}</li>
            ))}
          </ul>
          <p className="text-[11px] text-foreground-400">Pending local changes are not included in this comparison.</p>
        </div>
      )}
    </div>
  );
}
