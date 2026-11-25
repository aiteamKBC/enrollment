import { Check, CircleCheck, ClipboardList, Hourglass, Layers3, RotateCcw } from 'lucide-react';
import type { ApplicationsPage } from '@/api/eligibilityApplications';
import { applicationStatistics } from './applicationStatistics';

export function EligibilityHero() {
  return <section className="ea-hero" aria-labelledby="eligibility-review-title">
    <svg className="ea-hero-wave" viewBox="0 0 1400 120" preserveAspectRatio="none" aria-hidden="true" focusable="false">
      <defs><linearGradient id="ea-hero-wash"><stop stopColor="#f1e3fc" /><stop offset="1" stopColor="#ddc0f5" /></linearGradient></defs>
      <path d="M0 0H680C865 0 865 120 1040 120H0Z" fill="url(#ea-hero-wash)" />
      <path d="M1040 120C1180 120 1260 24 1380 40Q1400 40 1400 60V120Z" fill="#c99aee" fillOpacity=".45" />
      <path d="M0 0H680C865 0 865 120 1040 120" fill="none" stroke="#edc659" strokeWidth="1.5" />
    </svg>
    <div className="ea-hero-copy">
      <span className="ea-hero-icon" aria-hidden="true"><ClipboardList size={42} strokeWidth={1.5} /><span><Check size={23} strokeWidth={3} /></span></span>
      <div><h2 id="eligibility-review-title">Review and screen eligibility applications</h2><p>Review saved screening applications. Pending assessment is separate from technical verification or invitation failures.</p></div>
    </div>
    <img className="ea-hero-illustration" src="/images/eligibility/eligibility-hero.png" alt="" />
  </section>;
}

export function EligibilityStatistics({ data, search, status }: { data: ApplicationsPage | null; search: string; status: string }) {
  const counts = applicationStatistics(data, search, status);
  const cards = [
    { label: 'Total applications', value: counts.total, Icon: Layers3 },
    { label: 'Pending assessment', value: counts.pending, Icon: Hourglass },
    { label: 'Assessed applications', value: counts.assessed, Icon: CircleCheck },
  ];
  return <section className="ea-statistics" aria-label="Application statistics">
    {cards.map(({ label, value, Icon }, index) => <div className="ea-stat" key={label}>
      <span className="ea-stat-icon" aria-hidden="true"><Icon size={34} strokeWidth={1.6} /></span>
      <div><p className="ea-stat-value">{value === null ? <span aria-label="Count unavailable" title="An accurate global count is unavailable from the current response.">—</span> : value.toLocaleString('en-GB')}</p><h2>{label}</h2></div>
      {index === 0 && <svg className="ea-stat-laurel" viewBox="0 0 85 95" aria-hidden="true" focusable="false"><g fill="currentColor"><path d="M12 95C29 65 53 36 73 5" fill="none" stroke="currentColor" /><path d="M24 76C1 76 10 57 10 57C26 60 26 69 24 76ZM31 66C14 51 26 38 26 38C37 48 36 58 31 66ZM41 51C23 36 38 24 38 24C47 37 45 45 41 51ZM52 36C39 19 53 10 53 10C59 22 57 30 52 36ZM62 22C54 10 70 0 70 0C73 13 67 19 62 22ZM27 76C38 57 51 66 51 66C43 79 33 78 27 76ZM39 59C48 42 62 46 62 46C58 60 47 62 39 59ZM50 43C57 26 72 31 72 31C67 43 57 46 50 43ZM61 26C67 13 79 18 79 18C75 29 67 30 61 26Z" /></g></svg>}
    </div>)}
  </section>;
}

export function EligibilityEmptyState({ onClear }: { onClear: () => void }) {
  return <section className="ea-empty" aria-labelledby="eligibility-empty-title">
    <img src="/images/eligibility/eligibility-empty-state.png" alt="" />
    <h2 id="eligibility-empty-title">No applications match these filters.</h2>
    <p>Try adjusting your search or filter criteria.</p>
    <button type="button" className="ea-clear" onClick={onClear}><RotateCcw size={21} aria-hidden="true" />Clear filters</button>
  </section>;
}
