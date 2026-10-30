import { useEffect, useState, type ComponentType } from 'react';
import { useParams } from 'react-router-dom';
import {
  CheckCircle2,
  Clock3,
  FileText,
  LoaderCircle,
  Paperclip,
  Search,
} from 'lucide-react';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { PageContainer } from '@/components/ui/PageContainer';
import { fetchEmployerDocuments, type EmployerDocuments } from '@/api/employerPortal';
import { btnSecondary } from '@/pages/users/components/ui';
import { SignModal } from './EmployerDocumentRows';
import { EmployerDocumentPreview } from './EmployerDocumentPreview';
import { documentState as stateOf, useEmployerSigning } from './useEmployerSigning';
import { EmployerDocumentsTable, type DocumentFilter } from './EmployerDocumentsTable';
import { useEmployerPortalChrome } from './employerPortalNav';
import styles from './EmployerDocumentsPage.module.css';

type IconComponent = ComponentType<{ className?: string; 'aria-hidden'?: boolean }>;

/** Each card is this list's filter control, so the counts and the filters stay one thing. */
function SummaryCard({
  label,
  value,
  tone,
  Icon,
  active,
  onSelect,
}: {
  label: string;
  value: number;
  tone: 'amber' | 'violet' | 'green' | 'purple';
  Icon: IconComponent;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button type="button" onClick={onSelect} aria-pressed={active}
      className={`${styles.summaryCard} ${styles[`summary_${tone}`]}`}>
      <span className={styles.summaryIcon}><Icon aria-hidden /></span>
      <span className={styles.summaryCopy}>
        <span className={styles.summaryLabel}>{label}</span>
        <span className={styles.summaryValue}>{value}</span>
      </span>
    </button>
  );
}

export default function EmployerDocumentsPage() {
  const { employerId = '' } = useParams();
  const [data, setData] = useState<EmployerDocuments | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<DocumentFilter>('all');

  const load = () => {
    setLoading(true);
    setError(null);
    fetchEmployerDocuments(employerId)
      .then(setData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, [employerId]);

  const signingActions = useEmployerSigning(employerId, load);
  const { shell } = useEmployerPortalChrome(employerId, data?.employer.name);

  // This document queue starts at signing readiness; unfinished reviews stay in Progress reviews.
  const items = (data?.items ?? []).filter(item => item.signable || item.signed);
  const counts = { 'to-sign': 0, waiting: 0, signed: 0, all: items.length };
  for (const item of items) counts[stateOf(item)] += 1;

  return (
    <WorkspaceShell {...shell} pageTitle="All documents" pageSubtitle={data?.employer.name ?? 'Employer'}>
      <PageContainer>
        <div className={styles.page}>
          <section className={styles.hero} aria-labelledby="documents-title">
            <div className={styles.heroCopy}>
              <span className={styles.eyebrow}>Documents</span>
              <h1 id="documents-title">All documents</h1>
              <p>Reviews and agreements for every one of your learners, with a clear view of who has signed.</p>
            </div>

            <div className={styles.heroArtwork} aria-hidden>
              <span className={styles.paperBack} />
              <span className={styles.paperFront} />
              <Paperclip />
            </div>

            <label className={styles.searchBox}>
              <span className="sr-only">Search documents</span>
              <Search aria-hidden />
              <input
                type="search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search learner or document…"
              />
            </label>
          </section>

          {loading && !data && (
            <div role="status" aria-label="Loading documents" aria-live="polite" className={styles.loadingState}>
              <span className={styles.loadingIcon}>
                <LoaderCircle data-testid="documents-loading-spinner" aria-hidden className={`${styles.spinningIcon} animate-spin`} />
              </span>
              <div>
                <p>Loading documents...</p>
                <span>Preparing learner documents</span>
              </div>
            </div>
          )}

          {!loading && error && (
            <div className={styles.errorState} role="alert">
              <p><i className="ri-error-warning-line" aria-hidden />{error}</p>
              <button className={btnSecondary} onClick={load}><i className="ri-refresh-line" aria-hidden />Retry</button>
            </div>
          )}

          {data && !error && (
            <>
              <section className={styles.summaryGrid} aria-label="Document summary">
                <SummaryCard label="All documents" value={counts.all} tone="purple" Icon={FileText}
                  active={filter === 'all'} onSelect={() => setFilter('all')} />
                <SummaryCard label="Awaiting your signature" value={counts['to-sign']} tone="amber" Icon={Clock3}
                  active={filter === 'to-sign'} onSelect={() => setFilter('to-sign')} />
                <SummaryCard label="Signed" value={counts.signed} tone="green" Icon={CheckCircle2}
                  active={filter === 'signed'} onSelect={() => setFilter('signed')} />
              </section>

              <EmployerDocumentsTable items={items} employerId={employerId} search={search} filter={filter}
                opening={signingActions.opening} onSign={signingActions.startSigning} onShow={signingActions.openDocument} />
            </>
          )}
        </div>
      </PageContainer>

      {signingActions.preview && <EmployerDocumentPreview employerId={employerId}
        target={signingActions.preview} onClose={signingActions.closePreview} />}

      {signingActions.signing && data && (
        <SignModal
          item={signingActions.signing.item}
          employerName={data.employer.name}
          reviewDefinition={signingActions.reviewDefinition}
          onClose={signingActions.closeSigning}
          onSign={signingActions.sign}
          onSaveReviewAnswers={signingActions.saveReviewAnswers}
        />
      )}
    </WorkspaceShell>
  );
}
