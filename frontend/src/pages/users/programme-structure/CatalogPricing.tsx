import { useState } from 'react';
import { CircleAlert, RotateCcw } from 'lucide-react';
import { btnSecondary } from '@/pages/users/components/ui';
import { programmePath, type Detail, type Pricing } from './catalogApi';
import { calculatePricing, costLines, formatAmountInput, fundingBandComparison, gbp, hundredths, isWholePoundAmount } from './catalogMath';
import { catalogueFocus, catalogueInput, UnitInput } from './catalogControls';
import { useCatalogueResource } from './useCatalogueResource';
import styles from './CatalogPricing.module.css';

export function PricingWorkspace({ detail, preview, revision }: { detail: Detail; preview: boolean; revision: number }) {
  const { programme } = detail;
  const initial = {
    total_cost: formatAmountInput(programme.total_cost, preview), epa_cost: formatAmountInput(programme.epa_cost, preview), lecture_hours: programme.lecture_hours,
    funding_band_maximum: formatAmountInput(programme.funding_band_maximum ?? '', preview),
  };
  const [draft, setDraft] = useState(initial);
  const [cohortId, setCohortId] = useState('');
  const [groupId, setGroupId] = useState('');
  const cohorts = programme.archived_at ? [] : detail.cohorts.filter(row => !row.archived_at);
  const groups = detail.groups.filter(row => row.cohort_id === cohortId && !row.archived_at);
  const group = cohorts.some(row => row.id === cohortId) ? groups.find(row => row.id === groupId) : undefined;
  const dirty = Object.keys(initial).some(key => draft[key as keyof typeof initial] !== initial[key as keyof typeof initial]);
  const official = useCatalogueResource<{ pricing: Pricing }>(
    preview ? null : `${programmePath(programme.id)}pricing/${group && !programme.archived_at ? `?group_id=${encodeURIComponent(group.id)}` : ''}`, revision,
  );
  const revisions = group ? detail.deliveries.filter(row => row.group_id === group.id && !row.archived_at).map(row => row.curriculum) : [];
  const calculated = calculatePricing(draft, revisions, preview);
  const fundingValid = !draft.funding_band_maximum ||
    (preview ? isWholePoundAmount(draft.funding_band_maximum) : hundredths(draft.funding_band_maximum) !== null);
  const pricing = fundingValid ? (!dirty && official.data && !official.loading && !official.error ? official.data.pricing : calculated) : null;
  const counts = pricing?.counts ?? calculatePricing(initial, revisions, preview)?.counts;
  const comparison = fundingBandComparison(pricing?.total_cost ?? null, draft.funding_band_maximum);
  const inputs = [
    ['total_cost', 'Total programme cost (GBP)'], ['epa_cost', 'EPA cost (GBP)'],
    ['funding_band_maximum', 'Funding band maximum (GBP)'], ['lecture_hours', preview ? 'Hours per session' : 'Hours per lecture'],
  ] as const;
  const money = (value?: string | null) => gbp(value ?? null, preview);
  const unitRates = [
    ['Training hourly rate', 'training_hour', 'lectures', preview ? 'session' : 'lecture'],
    [preview ? 'Progress review unit price' : 'Review unit price', 'review', 'reviews', preview ? 'progress review' : 'review'],
    ['Book unit price', 'book', 'books', 'book'],
  ] as const;
  return <div className="space-y-4">
    <section className="rounded-xl border border-background-200 bg-background-50 p-4 sm:p-5" aria-labelledby="pricing-calculator">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div><h3 id="pricing-calculator" className="text-base font-semibold">Comparison calculator</h3>
          <p id="pricing-calculator-help" className="mt-1 text-sm text-foreground-600">Changes here are temporary comparisons per apprentice. They do not update the stored programme.</p></div>
        {dirty && <button type="button" className={`${btnSecondary} ${catalogueFocus}`} onClick={() => setDraft(initial)}><RotateCcw size={14} aria-hidden /> Reset inputs</button>}
      </div>
      <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {inputs.map(([key, title]) => <div className="space-y-1 text-sm font-medium" key={key}>
          <label htmlFor={`pricing-${key}`}>{title}</label>
          {key === 'lecture_hours'
            ? <input className={catalogueInput} id={`pricing-${key}`} name={key} type="number" inputMode="decimal" min="0.01" max="24" step="0.01"
              value={draft[key]} onChange={event => {
                const value = event.currentTarget.value;
                setDraft(current => ({ ...current, [key]: value }));
              }} />
            : <UnitInput id={`pricing-${key}`} name={key} unit="GBP" min={0} max="9999999999.99" step="0.01" wholePounds={preview}
              value={draft[key]} onValue={value => setDraft(current => ({ ...current, [key]: value }))} />}
        </div>)}
      </div>
      {(!calculated || !fundingValid) && <p role="alert" className="mt-3 text-sm text-red-700">Enter {preview ? 'whole-pound amounts' : 'valid amounts in GBP'}. The total must cover EPA, and hours per {preview ? 'session' : 'lecture'} must be between 0.01 and 24.</p>}
      {official.error && <p role="alert" className="mt-3 text-sm text-red-700">Saved quote unavailable: {official.error} Calculator results below are estimates from the loaded catalogue.</p>}
    </section>
    <dl className={`grid grid-cols-2 gap-3 ${draft.funding_band_maximum && fundingValid ? 'lg:grid-cols-4' : 'lg:grid-cols-3'}`}>
      {([
        ['Total programme cost', pricing?.total_cost], ['EPA cost', pricing?.allocations.epa],
        ['Base excluding EPA', pricing?.base_cost],
        ...(draft.funding_band_maximum && fundingValid ? [['Funding band maximum', draft.funding_band_maximum]] : []),
      ]).map(([label, value]) => <div key={label} className="min-w-0 rounded-xl border border-background-200 bg-background-50 p-3 sm:p-4">
        <dt className="text-xs font-medium text-foreground-600">{label}</dt>
        <dd className="mt-1 break-words text-lg font-semibold tabular-nums lg:text-xl">{money(value)}</dd>
      </div>)}
    </dl>
    {comparison && <div role="status" className={`flex items-start gap-2 rounded-lg px-3 py-2.5 text-sm ${comparison.direction === 'above' ? 'bg-amber-50 text-amber-950' : 'bg-background-100 text-foreground-700'}`}>
      {comparison.direction === 'above' && <CircleAlert size={16} className="mt-0.5 shrink-0" aria-hidden />}
      <p><strong>Funding band comparison:</strong>{' '}
        {comparison.direction === 'equal' ? 'The total matches the funding band maximum.'
          : `The total is ${money(comparison.difference)} ${comparison.direction} the funding band maximum.`}
        {comparison.direction === 'above' && ' This does not determine the employer balance.'}</p>
    </div>}
    <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
      <section className="min-w-0 rounded-xl border border-background-200 bg-background-50 p-4 sm:p-5">
        <h3 className="text-base font-semibold">Written Agreement allocation</h3>
        <p className="mb-3 mt-1 text-sm text-foreground-600">Percentages apply to the base cost. EPA is a separate fixed amount.</p>
        <table className={`${styles.allocation} w-full table-fixed text-left text-xs sm:text-sm`}>
          <caption className="sr-only">Cost allocation and reconciliation</caption>
          <colgroup><col /><col className="w-10 sm:w-16" /><col className="w-28 sm:w-36" /></colgroup>
          <thead className="border-y border-background-200 bg-background-100 text-foreground-700">
            <tr><th scope="col" className="py-2 pl-2">Category</th><th scope="col" className="py-2 pr-1 text-right">Basis</th><th scope="col" className="py-2 pr-2 text-right">Amount</th></tr>
          </thead>
          <tbody className="divide-y divide-background-200">
            {costLines.map(([key, label, share]) => <tr key={key}>
              <th scope="row" className="break-words py-2.5 pl-2 pr-1 font-normal leading-relaxed">{label}</th>
              <td className="py-2.5 pr-1 text-right text-foreground-600">{share === null ? 'Fixed' : `${share}%`}</td>
              <td className="break-words py-2.5 pr-2 text-right tabular-nums">{money(pricing?.allocations[key])}</td>
            </tr>)}
          </tbody>
          <tfoot className="border-t border-background-200 bg-background-100 font-semibold">
            <tr><th scope="row" colSpan={2} className="py-2.5 pl-2">Reconciliation total</th><td className="break-words py-2.5 pr-2 text-right tabular-nums">{money(pricing?.total_cost)}</td></tr>
          </tfoot>
        </table>
      </section>
      <section className="min-w-0 rounded-xl border border-background-200 bg-background-50 p-4 sm:p-5">
        <h3 className="text-base font-semibold">Group unit rates</h3>
        <p className="mt-1 break-words text-sm text-foreground-600">{group ? `Based on one apprentice in ${group.name}.` : 'Choose one cohort and group to calculate rates. Groups are never combined.'}</p>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-1">
          <div className="space-y-1 text-sm font-medium">
            <label htmlFor="pricing-cohort">Cohort</label>
            <select id="pricing-cohort" className={catalogueInput} disabled={!cohorts.length} value={cohortId} onChange={event => { setCohortId(event.target.value); setGroupId(''); }}>
              <option value="">Select a cohort</option>{cohorts.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}
            </select></div>
          <div className="space-y-1 text-sm font-medium">
            <label htmlFor="pricing-group">Group</label>
            <select id="pricing-group" className={catalogueInput} disabled={!cohortId || !groups.length} value={group?.id ?? ''} onChange={event => setGroupId(event.target.value)}>
              <option value="">{cohortId && !groups.length ? 'No groups in this cohort' : 'Select a group'}</option>
              {groups.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}
            </select></div>
        </div>
        <dl className="mt-4 divide-y divide-background-200 rounded-lg bg-background-100 px-3">
          {unitRates.map(([label, key, count, unit]) => <div key={key} className="flex items-baseline justify-between gap-3 py-2.5 text-sm">
            <dt>{label}{group && <span className="mt-0.5 block text-xs text-foreground-600">
              {counts?.[count] == null ? revisions.length ? `Missing ${unit} counts on one or more modules` : 'No scheduled modules'
                : `${counts[count]} ${unit}${counts[count] === 1 ? '' : 's'}`}
            </span>}</dt>
            <dd className="text-right font-semibold tabular-nums">{money(pricing?.rates[key])}</dd>
          </div>)}
        </dl>
        <p className="mt-2 text-xs text-foreground-600">Training pool (80% of base): {money(pricing?.total_training)}. Missing or zero counts leave the corresponding rate uncalculated.</p>
      </section>
    </div>
  </div>;
}
