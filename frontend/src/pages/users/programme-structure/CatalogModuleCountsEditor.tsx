import { useEffect, useState, type FormEvent } from 'react';
import { btnPrimary, btnSecondary } from '@/pages/users/components/ui';
import type { CurriculumModule } from './catalogApi';
import { catalogueFocus, catalogueInput } from './catalogControls';

const count = (value: string) => value !== '' && /^\d{1,5}$/.test(value) && Number(value) <= 10000;

export function CatalogModuleCountsEditor({ module, busy, error, onSave, onCancel, onReload, onDirtyChange }: {
  module: CurriculumModule; busy: boolean; error: string;
  onSave: (counts: { reviews: number; books: number }) => void;
  onCancel: () => void; onReload?: () => void; onDirtyChange?: (dirty: boolean) => void;
}) {
  const [reviews, setReviews] = useState(module.reviews == null ? '' : String(module.reviews));
  const [books, setBooks] = useState(module.books == null ? '' : String(module.books));
  const dirty = reviews !== (module.reviews == null ? '' : String(module.reviews)) ||
    books !== (module.books == null ? '' : String(module.books));
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || onReload || !count(reviews) || !count(books)) return;
    onSave({ reviews: Number(reviews), books: Number(books) });
  }

  return <form aria-label="Edit module counts" className="mx-auto max-w-2xl space-y-5" onSubmit={submit}>
    <h2 className="text-xl font-semibold">Edit Progress Reviews and Books</h2>
    <p className="text-sm text-foreground-700">{module.name} · LMS curriculum stays linked. These counts belong to Enrolment.</p>
    <p className="text-sm text-foreground-600">Enter both counts, including 0 when none are planned. Saving creates a new module revision and updates future scheduled deliveries. Deliveries that have started keep their pinned revision.</p>
    {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</p>}
    <div className="grid gap-4 rounded-xl border border-background-200 bg-background-50 p-4 sm:grid-cols-2">
      {([['Progress Reviews', reviews, setReviews], ['Books', books, setBooks]] as const).map(([label, value, setValue]) =>
        <label key={label} className="space-y-1.5 text-sm font-medium">{label}
          <input className={catalogueInput} type="number" inputMode="numeric" min="0" max="10000" step="1" required value={value}
            onChange={event => setValue(event.target.value)} />
        </label>)}
    </div>
    <div className="flex flex-wrap gap-2">
      <button className={`${btnPrimary} ${catalogueFocus}`} type="submit" disabled={busy || Boolean(onReload) || !count(reviews) || !count(books)}>Save counts</button>
      {onReload && <button className={`${btnSecondary} ${catalogueFocus}`} type="button" onClick={onReload}>Reload latest</button>}
      <button className={`${btnSecondary} ${catalogueFocus}`} type="button" disabled={busy} onClick={onCancel}>Cancel</button>
    </div>
  </form>;
}
