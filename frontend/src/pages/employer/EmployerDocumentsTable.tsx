import { useState, type ComponentType } from 'react';
import { Link } from 'react-router-dom';
import { BriefcaseBusiness, CalendarDays, Check, CheckCircle2, Clock3, FileCheck2, FileSignature, FileText, LoaderCircle, MoreHorizontal, ShieldCheck, UserRound } from 'lucide-react';
import type { EmployerOwnedItem } from '@/api/employerPortal';
import { compareSignable, documentState as stateOf, targetKey, type SigningTarget } from './useEmployerSigning';
import styles from './EmployerDocumentsPage.module.css';

export type DocumentFilter = 'to-sign' | 'waiting' | 'signed' | 'all';
type DisplayState = Exclude<DocumentFilter, 'all'> | 'waiting-learner';
type IconComponent = ComponentType<{ className?: string; 'aria-hidden'?: boolean }>;

function displayStateOf(item: EmployerOwnedItem): DisplayState {
  if (item.signed) return 'signed';
  if (item.signable) return 'to-sign';
  return item.kind === 'review' ? 'waiting-learner' : 'waiting';
}

const FILTERS: { key: DocumentFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'to-sign', label: 'To sign' },
  { key: 'waiting', label: 'Waiting on others' },
  { key: 'signed', label: 'Signed' },
];

function formatDate(value: string | null | undefined) {
  if (!value) return 'Date unavailable';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString('en-GB', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    timeZone: 'Europe/London',
  });
}

function documentType(item: EmployerOwnedItem) {
  if (item.kind === 'review') return 'Review';
  return item.docType
    .replace(/[-_]+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function itemDate(item: EmployerOwnedItem) {
  return formatDate(item.kind === 'review' ? item.scheduledDate : item.generatedAt);
}

type Party = { key: string; label: string; signed: boolean; Icon: IconComponent };

function partiesFor(item: EmployerOwnedItem): Party[] {
  if (item.kind === 'review') {
    return [
      { key: 'learner', label: 'Learner', signed: item.learnerSigned, Icon: UserRound },
      { key: 'provider', label: 'Provider', signed: item.adminSigned, Icon: ShieldCheck },
      { key: 'employer', label: 'You', signed: item.signed, Icon: BriefcaseBusiness },
    ];
  }

  const parties: Party[] = [];
  if (item.parties?.includes('learner')) {
    parties.push({ key: 'learner', label: 'Learner', signed: Boolean(item.learnerSigned), Icon: UserRound });
  }
  if (item.parties?.includes('provider')) {
    parties.push({ key: 'provider', label: 'Provider', signed: Boolean(item.providerSigned), Icon: ShieldCheck });
  }
  if (item.parties?.includes('employer') !== false) {
    parties.push({ key: 'employer', label: 'You', signed: item.signed, Icon: BriefcaseBusiness });
  }
  return parties;
}

function PartyPills({ item }: { item: EmployerOwnedItem }) {
  const parties = partiesFor(item);
  if (parties.length === 0) return <span className={styles.mutedValue}>No signing parties</span>;

  return (
    <div className={styles.parties} aria-label="Signing parties">
      {parties.map(({ key, label, signed, Icon }) => (
        <span
          key={key}
          className={`${styles.partyPill} ${signed ? styles.partySigned : ''}`}
          title={signed ? `${label} signed` : `${label} has not signed yet`}
        >
          {signed ? <Check aria-hidden /> : <Icon aria-hidden />}
          {label}
        </span>
      ))}
    </div>
  );
}

function DocumentStatus({ item }: { item: EmployerOwnedItem }) {
  const state = displayStateOf(item);
  const content = {
    'to-sign': { label: 'Awaiting your signature', Icon: Clock3 },
    waiting: { label: 'Waiting on others', Icon: Clock3 },
    'waiting-learner': { label: 'Awaiting completion', Icon: Clock3 },
    signed: { label: 'Signed', Icon: CheckCircle2 },
  }[state];
  const Icon = content.Icon;

  return (
    <span className={`${styles.statusPill} ${styles[`status_${state}`]}`}>
      <Icon aria-hidden />
      {content.label}
    </span>
  );
}

function DocumentAction({
  item,
  opening,
  onSign,
  onShow,
}: {
  item: EmployerOwnedItem;
  opening: boolean;
  onSign: () => void;
  onShow: () => void;
}) {
  return (
    <div className={styles.documentActions}>
      <button type="button" onClick={onShow} disabled={opening} className={styles.showButton}>
        {opening ? <LoaderCircle className={styles.buttonSpinner} aria-hidden /> : <FileText aria-hidden />}
        {opening ? 'Opening…' : 'Preview'}
      </button>
      {!item.signed && item.signable && (
        <button type="button" onClick={onSign} className={styles.signButton}>
          <FileSignature aria-hidden />
          Sign
        </button>
      )}
    </div>
  );
}

function DocumentListRow({
  item,
  employerId,
  opening,
  onSign,
  onShow,
}: {
  item: EmployerOwnedItem;
  employerId: string;
  opening: boolean;
  onSign: () => void;
  onShow: () => void;
}) {
  const learnerHref = `/employers/${employerId}/learner/${item.learner.kind}/${item.learner.id}`;

  return (
    <article className={`${styles.documentRow} ${stateOf(item) === 'to-sign' ? styles.actionRequired : ''}`}>
      <div className={styles.documentIdentity}>
        <span className={styles.documentIcon} aria-hidden>
          {item.kind === 'review' ? <FileCheck2 /> : <FileText />}
        </span>
        <span className={styles.documentCopy}>
          <button type="button" onClick={onShow} className={styles.documentName} title={`Preview ${item.label}`}>{item.label}</button>
          <span className={styles.documentMeta}>
            <Link to={learnerHref}>{item.learner.name || 'Learner'}</Link>
            <span aria-hidden>·</span>
            <span>{documentType(item)}</span>
          </span>
        </span>
      </div>

      <div className={styles.dateCell} data-label="Date">
        <CalendarDays aria-hidden />
        <time>{itemDate(item)}</time>
      </div>

      <div className={styles.statusCell} data-label="Status">
        <DocumentStatus item={item} />
        {item.kind === 'review' && item.migratedForm && item.signed && !item.completed && (
          <span className={styles.statusNote}>Awaiting final completion</span>
        )}
      </div>
      <div className={styles.partiesCell} data-label="Parties"><PartyPills item={item} /></div>

      <div className={styles.actionCell}>
        <DocumentAction item={item} opening={opening} onSign={onSign} onShow={onShow} />
      </div>

      <details className={styles.overflowMenu}>
        <summary aria-label={`More actions for ${item.label}`}><MoreHorizontal aria-hidden /></summary>
        <div className={styles.overflowPanel}>
          <Link to={learnerHref}><UserRound aria-hidden />View learner profile</Link>
        </div>
      </details>
    </article>
  );
}

export function EmployerDocumentsTable({ items, employerId, opening, onSign, onShow, search = '', filter: ownedFilter }: {
  items: EmployerOwnedItem[];
  employerId: string;
  opening: string | null;
  onSign: (target: SigningTarget) => void;
  onShow: (target: SigningTarget) => void;
  search?: string;
  /** Supplied when the page's own counts are the filter control; omitted keeps these chips. */
  filter?: DocumentFilter;
}) {
  const [ownFilter, setOwnFilter] = useState<DocumentFilter>('all');
  const filter = ownedFilter ?? ownFilter;
  const counts = { 'to-sign': 0, waiting: 0, signed: 0, all: items.length };
  for (const item of items) counts[stateOf(item)] += 1;
  const query = search.trim().toLowerCase();
  const visible = items
    .filter(item => filter === 'all' || stateOf(item) === filter)
    .filter(item => !query || item.label.toLowerCase().includes(query) || item.learner.name.toLowerCase().includes(query))
    .sort((a, b) => compareSignable(a, b) || a.learner.name.localeCompare(b.learner.name));
  const target = (item: EmployerOwnedItem): SigningTarget => ({ item, kind: item.learner.kind, learnerId: item.learner.id });
  return (
    <section className={styles.documentsSection} aria-label="Documents">
      {ownedFilter === undefined && (
        <div className={styles.toolbar}>
          <nav className={styles.filters} aria-label="Filter documents">
            {FILTERS.map((item) => (
              <button
                key={item.key}
                type="button"
                onClick={() => setOwnFilter(item.key)}
                aria-pressed={filter === item.key}
                className={filter === item.key ? styles.activeFilter : ''}
              >
                {item.label}
                <span>{counts[item.key]}</span>
              </button>
            ))}
          </nav>
        </div>
      )}

      {visible.length === 0 ? (
        <div className={styles.emptyState}>
          <FileText aria-hidden />
          <p>{items.length === 0
            ? 'No documents yet. They appear here once the provider has prepared them for your learners.'
            : 'No documents match this filter.'}</p>
        </div>
      ) : (
        <div className={styles.documentList}>
          <div className={styles.listHeader} aria-hidden>
            <span>Document</span>
            <span>Date</span>
            <span>Status</span>
            <span>Parties</span>
            <span>Action</span>
            <span />
          </div>
          {visible.map((item) => {
            const signingTarget = target(item);
            return (
              <DocumentListRow
                key={targetKey(signingTarget)}
                item={item}
                employerId={employerId}
                opening={opening === targetKey(signingTarget)}
                onSign={() => onSign(signingTarget)}
                onShow={() => onShow(signingTarget)}
              />
            );
          })}
        </div>
      )}

      <footer className={styles.listFooter}>
        Showing <strong>{visible.length}</strong> of <strong>{items.length}</strong> documents
      </footer>
    </section>
  );
}
