import { Check, CircleCheck, CircleDashed, CirclePlay, ClipboardCheck, LockKeyhole } from 'lucide-react';
import type { OnboardingReviewSlot } from '@/api/reviewForm';
import styles from './reviews.module.css';

// Match the existing card's status precedence; this only groups the supplied
// records for display and never derives completion from issuance or signatures.
function reviewPresentationStatus(review: OnboardingReviewSlot) {
  return review.completed ? 'completed' : review.started ? 'progress' : review.issued ? 'ready' : 'unissued';
}

export function ReviewsBanner({ completed, total }: { completed: number; total: number }) {
  return <section className={styles.banner} aria-labelledby="enrolment-reviews-summary">
    <div className={styles.bannerIcon} aria-hidden="true"><ClipboardCheck size={40} strokeWidth={1.8} /></div>
    <div className={styles.bannerContent}>
      <h2 id="enrolment-reviews-summary">Your enrolment reviews</h2>
      <p>Thank you for submitting your enrolment. Your enrolment officer issues each of the four reviews below;
        once a review is issued, open it, complete it and sign it.</p>
      <div className={styles.progress} role="progressbar" aria-label="Enrolment reviews completed" aria-valuemin={0} aria-valuemax={total} aria-valuenow={completed}>
        <div style={{ width: `${(completed / total) * 100}%` }} />
      </div>
    </div>
    <span className={styles.completionBadge} data-complete={completed === total || undefined}>{completed} of {total} completed</span>
    <div className={styles.artwork} aria-hidden="true">
      <div className={styles.checklist}>{[0, 1, 2].map(row => <div key={row}><span><Check size={17} strokeWidth={3} /></span><i /><i /></div>)}</div>
      <span className={styles.spark} />
    </div>
  </section>;
}

const SUMMARY = [
  { status: 'completed', label: 'Completed', Icon: CircleCheck },
  { status: 'progress', label: 'In progress', Icon: CircleDashed },
  { status: 'ready', label: 'Ready to start', Icon: CirclePlay },
  { status: 'unissued', label: 'Not issued', Icon: LockKeyhole },
] as const;

export function ReviewStatusSummary({ reviews }: { reviews: OnboardingReviewSlot[] }) {
  return <dl className={styles.statusSummary} aria-label="Review status summary">
    {SUMMARY.map(({ status, label, Icon }) => <div key={status} className={styles.summaryCard} data-status={status}>
      <span className={styles.summaryIcon} aria-hidden="true"><Icon size={31} strokeWidth={1.9} /></span>
      <div><dt>{label}</dt><dd>{reviews.filter(review => reviewPresentationStatus(review) === status).length}</dd></div>
    </div>)}
  </dl>;
}
