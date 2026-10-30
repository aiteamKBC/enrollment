/**
 * How a coach's decision on one activity reads to the learner.
 *
 * An activity the curriculum author sent for tutor validation is not finished
 * when the learner completes it — completing hands it in, and the coach's
 * decision is what finishes it. Three states matter, and the whole surface
 * follows the same one so a green tick can never sit beside a red verdict:
 *
 *   pending   the coach has not looked at it yet
 *   accepted  validated — the activity is done
 *   rejected  sent back — the learner has more to do
 *
 * Shared between the training-plan row and the activity page. They showed the
 * learner different things about the same submission when each had its own
 * copy of this.
 */
export type MarkingVerdictKey = 'pending' | 'accepted' | 'rejected';

export interface MarkingVerdict {
  key: MarkingVerdictKey;
  /** What the learner reads. */
  label: string;
  /** One line saying what happens next, for a banner with room for it. */
  detail: string;
  icon: string;
  /** Badge/pill colours. */
  tone: string;
  /** Panel border + background, for a full-width banner. */
  panel: string;
  /** Icon colour inside a panel. */
  panelIcon: string;
}

const PENDING: MarkingVerdict = {
  key: 'pending',
  label: 'Pending coach review',
  detail: 'You have submitted this. Your coach will review it and you will see their decision here.',
  icon: 'ri-time-line',
  tone: 'bg-amber-100 text-amber-700',
  panel: 'border-amber-200 bg-amber-50/60',
  panelIcon: 'text-amber-600',
};

const ACCEPTED: MarkingVerdict = {
  key: 'accepted',
  label: 'Accepted',
  detail: 'Your coach has validated this activity. Nothing further is needed.',
  icon: 'ri-checkbox-circle-line',
  tone: 'bg-emerald-100 text-emerald-700',
  panel: 'border-emerald-200 bg-emerald-50/60',
  panelIcon: 'text-emerald-600',
};

const REJECTED: MarkingVerdict = {
  key: 'rejected',
  label: 'Rejected',
  detail: 'Your coach has asked for more work on this. Read their feedback, then submit again.',
  icon: 'ri-close-circle-line',
  tone: 'bg-red-100 text-red-700',
  panel: 'border-red-200 bg-red-50/60',
  panelIcon: 'text-red-600',
};

/**
 * The verdict for a submission status, or null when nothing has been handed in.
 *
 * A null return means "not submitted yet", which is different from pending:
 * the learner still has to finish the activity, so the ordinary completion
 * criteria apply rather than a marking state.
 *
 * 'partial' counts as accepted — the learner was awarded something. 'referred'
 * counts as rejected — it came back for more work. 'escalated' is still with
 * staff, so it reads as pending.
 */
export function markingVerdict(status: string | undefined | null): MarkingVerdict | null {
  switch (status) {
    case 'accepted':
    case 'partial':
      return ACCEPTED;
    case 'rejected':
    case 'referred':
      return REJECTED;
    case 'submitted_for_tutor_review':
    case 'escalated':
      return PENDING;
    default:
      return null;
  }
}
