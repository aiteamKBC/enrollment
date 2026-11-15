import { useEffect, useState } from 'react';
import { ArrowRight, CalendarDays, Check, FileText, LockKeyhole, ShieldCheck } from 'lucide-react';
import { fetchAgreement } from '@/api/apprenticeshipAgreement';
import { fetchIlrDocument } from '@/api/ilrDocument';
import { fetchTrainingPlanDocument } from '@/api/trainingPlanDocument';
import { fetchWrittenAgreement } from '@/api/writtenAgreement';
import { revisionHistory } from '@/features/compliance-documents/revisionApi';
import type { DocumentKey } from '@/features/compliance-documents/types';
import { PENDING_SIGNATURES_CHANGED, PENDING_SIGNATURES_STORAGE_KEY } from '@/features/pending-signatures/events';
import styles from './firstSession.module.css';

const documents = ['Apprenticeship Agreement', 'Individual Learner Record', 'Training Plan', 'Written Agreement'];
type SignatureStatus = boolean | 'loading' | 'unavailable';

async function signedRequirement(learnerId: string, key: DocumentKey, party: string,
  document: { id: string; signatures: Record<string, { signed: boolean }> } | null) {
  if (!document) return false;
  const history = await revisionHistory(learnerId, key);
  // Match the same issued source shown on Compliance Documents. Drafts and
  // retained marks remain historical and never become a new signature record.
  const revised = history.results.filter(row => String(row.sourceId) === String(document.id)
    && row.state === 'finalized')
    .sort((a, b) => b.number - a.number)[0];
  return (revised ? revised.signatures[party]?.signed : document.signatures[party]?.signed) === true;
}

/** Display evidence only. These indicators never determine booking eligibility. */
export function LockedSessionPanel({ learnerId, onCompliance }: { learnerId: string; onCompliance: () => void }) {
  const [snapshot, setSnapshot] = useState<{ learnerId: string; statuses: SignatureStatus[] } | null>(null);
  useEffect(() => {
    let active = true, sequence = 0;
    const load = () => {
      const request = ++sequence;
      setSnapshot({ learnerId, statuses: documents.map(() => 'loading') });
      const reads = [
        fetchAgreement(learnerId).then(value => signedRequirement(learnerId, 'apprenticeship-agreement', 'apprentice', value.agreement)),
        fetchIlrDocument(learnerId).then(value => signedRequirement(learnerId, 'ilr', 'learner', value.document)),
        fetchTrainingPlanDocument(learnerId).then(value => signedRequirement(learnerId, 'training-plan', 'apprentice', value.document)),
        fetchWrittenAgreement(learnerId).then(value => signedRequirement(learnerId, 'written-agreement', 'learner', value.document)),
      ];
      reads.forEach((read, index) => {
        const update = (status: SignatureStatus) => {
          if (!active || request !== sequence) return;
          setSnapshot(previous => ({ learnerId, statuses: documents.map((_, i) => i === index ? status : previous?.statuses[i] ?? 'loading') }));
        };
        void read.then(update, () => update('unavailable'));
      });
    };
    const onStorage = (event: StorageEvent) => { if (event.key === PENDING_SIGNATURES_STORAGE_KEY) load(); };
    load();
    window.addEventListener('focus', load);
    window.addEventListener(PENDING_SIGNATURES_CHANGED, load);
    window.addEventListener('storage', onStorage);
    return () => {
      active = false;
      window.removeEventListener('focus', load);
      window.removeEventListener(PENDING_SIGNATURES_CHANGED, load);
      window.removeEventListener('storage', onStorage);
    };
  }, [learnerId]);
  const statuses: SignatureStatus[] = snapshot?.learnerId === learnerId ? snapshot.statuses : documents.map(() => 'loading');

  return (
    <section className={styles.hero} aria-labelledby="first-session-requirements">
      <svg className={styles.waves} viewBox="0 0 1400 600" preserveAspectRatio="none" aria-hidden="true">
        <path d="M760 600C900 370 1040 660 1220 560S1450 510 1500 550V650H760Z" fill="#dcb9fa" fillOpacity=".4" />
        <path d="M870 0C1140 180 1070 360 1400 270V0Z" fill="#d7adfa" fillOpacity=".24" />
        <path d="M1160 0C1270 55 1310 125 1400 130V0Z" fill="#c994f3" fillOpacity=".2" />
      </svg>
      <div className={styles.heroContent}>
        <div className={styles.eyebrow}><CalendarDays size={23} aria-hidden="true" />YOUR FIRST LEARNING SESSION</div>
        <h2 id="first-session-requirements" className={styles.lockedHeading}>Sign your compliance documents first</h2>
        <svg className={styles.underline} viewBox="0 0 158 16" aria-hidden="true"><path d="M3 12Q76 -1 154 6" fill="none" stroke="currentColor" strokeWidth="4" strokeLinecap="round" /></svg>
        <p className={styles.description}>
          Your first learning session can be booked once you have signed all four of your compliance documents:
          the Apprenticeship Agreement, Individual Learner Record, Training Plan and Written Agreement.
        </p>
        <ul className={styles.requirements} aria-label="Compliance document signing requirements">
          {documents.map((name, index) => {
            const status = statuses[index];
            const label = status === true ? 'Signed by you' : status === false ? 'Not signed by you' : status === 'loading' ? 'Checking signature status' : 'Signature status unavailable';
            return <li key={name} className={styles.documentTile} aria-label={`${name}: ${label}`}>
              <span className={styles.documentIcon}>
                <FileText size={36} strokeWidth={1.8} aria-hidden="true" />
                <span className={styles.indicator} data-signed={status === true ? 'true' : undefined}>
                  {status === true ? <Check size={15} strokeWidth={3} aria-hidden="true" /> : <LockKeyhole size={12} aria-hidden="true" />}
                </span>
              </span>
              <span>{name}</span><span className={styles.signatureLabel}>{label}</span>
            </li>;
          })}
        </ul>
        <button type="button" className={styles.complianceButton} onClick={onCompliance}>
          <ShieldCheck size={23} aria-hidden="true" /><span>Go to Compliance documents</span><ArrowRight size={25} aria-hidden="true" />
        </button>
      </div>
      <SessionArtwork />
    </section>
  );
}

/** Existing calendar asset with decorative study objects; no learner status. */
function SessionArtwork() {
  return <div className={styles.artwork} aria-hidden="true">
    <div className={styles.artworkGlow} />
    <div className={styles.books}><span /><span /></div>
    <div className={styles.plant}><i /><i /><i /><span /></div>
    <img className={styles.calendar} src="/coach-meetings-calendar.webp" alt="" draggable={false} />
    <div className={styles.checklist}>{[0, 1, 2].map(row => <div key={row}><Check size={24} strokeWidth={3} /><span /><span /></div>)}</div>
  </div>;
}
