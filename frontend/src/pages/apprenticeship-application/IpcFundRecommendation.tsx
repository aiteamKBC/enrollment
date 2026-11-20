import { ArrowUpRight } from 'lucide-react';
import styles from './IpcFundRecommendation.module.css';

const PCP_L6_PROGRAMME_ID = 'PCP_L6';
// Eligibility submissions store display names; the curriculum uses stable IDs.
const PROGRAMME_IDS: Readonly<Record<string, string>> = {
  'Project Controls Professional Level 6': PCP_L6_PROGRAMME_ID,
};

function isPcpLevel6(programme: unknown): boolean {
  return typeof programme === 'string'
    && (PROGRAMME_IDS[programme] ?? programme) === PCP_L6_PROGRAMME_ID;
}

export function IpcFundRecommendation({ programme, ineligible }: { programme: unknown; ineligible: boolean }) {
  if (!ineligible || !isPcpLevel6(programme)) return null;
  return <section className={styles.card} aria-label="IPC Fund recommendation">
    <header className={styles.header}>
      <img className={styles.logo} src="/assets/ipc-logo.png" width="666" height="375" alt="Institute of Project Controls (IPC) logo" />
      <div className={styles.headerCopy}>
        <h3>Your journey doesn't have to stop here.</h3>
        <p>IPC Fund <span aria-hidden="true">·</span> Jan/Feb 2027 Cohort</p>
      </div>
    </header>
    <div className={styles.body}>
    <p>Explore the IPC Fund: a separate professional development opportunity with its own funding assessment, independent of the PCP Level 6 apprenticeship.</p>
    <div className={styles.funding}>
      <strong className={styles.amount}>50%–75%</strong>
      <p><strong>Potential financial support</strong><br />of the approved commercial cost</p>
    </div>
    <p className={styles.disclaimer}>Funding is limited and subject to an individual assessment by the IPC Fund Committee. An award is not guaranteed.</p>
    <p className={styles.disclaimer}>Apprenticeship ineligibility does not imply eligibility for IPC funding.</p>
    <div className={styles.actions}>
      <a className={styles.primary} href="https://instituteofprojectcontrols.com/bursary-scholarship-application" target="_blank" rel="noopener noreferrer">Apply for the IPC Fund <ArrowUpRight size={17} aria-hidden="true" /></a>
      <a className={styles.secondary} href="https://instituteofprojectcontrols.com/scholarships" target="_blank" rel="noopener noreferrer">View IPC Scholarships &amp; Fund Announcements <ArrowUpRight size={17} aria-hidden="true" /></a>
    </div>
    </div>
  </section>;
}
