// ============================================================================
// Why a learner cannot start yet, in words.
//
// The backend decides it (learner_progression.access_gate) using the same
// conditions that would activate them; this only phrases the answer. The
// waiting page used to assume the answer was always "your start date has not
// arrived", so a learner held back by an unassigned learning plan was told to
// wait for a date that had already gone by.
// ============================================================================

/** What progression is still waiting for. Ordered by what has to happen first. */
export type LearnerAccessReason =
  | 'documents'
  | 'plan'
  | 'start-date-missing'
  | 'start-date-future'
  | 'invitation';

export interface LearnerAccessGate {
  blocked: boolean;
  reasons: LearnerAccessReason[];
  /** ISO date, or '' when the learner has none. */
  startDate: string;
  /** Named compliance documents still unsigned, for apprenticeship learners. */
  outstandingDocuments: string[];
}

/** Teaching opens on the cohort start date, independently of saved status. */
export interface LearnerLearningAccess {
  blocked: boolean;
  startDate: string;
}

/**
 * Whether the learner's assigned programme is theirs to browse.
 *
 * Deliberately NOT conditional on the start date having arrived. A learner
 * waiting for their cohort to begin still has a real programme attached, and
 * hiding the workspace until the date lands left them with a single Dashboard
 * tab and no way to see what they had enrolled on. The blockers that remain are
 * the ones where there is genuinely nothing behind the menu yet: no learning
 * plan assigned, no invitation sent, paperwork unsigned.
 */
export function canViewAssignedProgramme(kind: string | undefined, gate?: LearnerAccessGate): boolean {
  // No gate at all means the backend told us nothing about this learner's
  // progression, which is not the same as telling us they are clear.
  return kind === 'commercial' && !!gate
    && !gate.reasons.some(reason => reason === 'plan' || reason === 'invitation' || reason === 'documents');
}

export interface WaitingCopy {
  title: string;
  /** Body paragraphs, most important first. */
  lines: string[];
  /** The "what happens next" list. */
  steps: string[];
}

function formatDate(iso: string): string {
  if (!iso) return '';
  const date = new Date(`${iso.slice(0, 10)}T00:00:00`);
  return Number.isNaN(date.getTime())
    ? iso
    : new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'long', year: 'numeric' }).format(date);
}

function isPast(iso: string): boolean {
  if (!iso) return false;
  const date = new Date(`${iso.slice(0, 10)}T00:00:00`);
  if (Number.isNaN(date.getTime())) return false;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return date.getTime() < today.getTime();
}

/**
 * The copy for a learner who is waiting to start. `commercial` only affects the
 * wording of the last step — a commercial learner's access opens on its own,
 * where an apprentice's follows their enrolment.
 */
export function waitingCopy(
  gate: LearnerAccessGate | null | undefined,
  { commercial = false }: { commercial?: boolean } = {},
): WaitingCopy {
  const reasons = gate?.reasons ?? [];
  const startDate = formatDate(gate?.startDate || '');
  const documents = gate?.outstandingDocuments ?? [];
  const has = (reason: LearnerAccessReason) => reasons.includes(reason);
  const lines: string[] = [];
  const steps: string[] = [];

  // No gate, or nothing blocking: the old, date-shaped message is still the
  // right one — there is genuinely nothing to do but wait.
  if (reasons.length === 0) {
    return {
      title: 'Your programme starts soon',
      lines: [
        startDate
          ? `Your programme is scheduled to start on ${startDate}.`
          : 'Your programme start date has not been set yet.',
        'Your learning access will become active automatically when the programme begins.',
      ],
      steps: [
        startDate
          ? `Your programme starts on ${startDate}.`
          : 'Your programme start date is confirmed by your programme team.',
        commercial
          ? 'Your learning access will activate automatically when it starts.'
          : 'Your training plan and learning materials appear here when it starts.',
      ],
    };
  }

  const title = has('documents')
    ? 'Your enrolment paperwork is still being completed'
    : has('plan')
      ? 'Your learning plan is being prepared'
      : has('start-date-missing')
        ? 'Your start date has not been set yet'
        : has('start-date-future')
          ? 'Your programme starts soon'
          : 'Your platform invitation is pending';

  if (has('documents')) {
    lines.push(
      documents.length > 0
        ? `Your enrolment documents are not all signed yet — still outstanding: ${documents.join(', ')}.`
        : 'Your enrolment documents are not all signed yet.',
    );
    steps.push('Your enrolment documents are signed by everyone they need.');
  }

  if (has('plan')) {
    lines.push('Your programme team has not assigned your learning plan yet — that is what your start is waiting on.');
    steps.push('Your programme team assigns your learning plan.');
  }

  if (has('start-date-missing')) {
    lines.push('Your start date has not been confirmed yet.');
    steps.push('Your start date is confirmed by your programme team.');
  } else if (has('start-date-future')) {
    lines.push(`Your programme is scheduled to start on ${startDate}.`);
    steps.push(`Your programme starts on ${startDate}.`);
  } else if (isPast(gate?.startDate || '')) {
    // The date has gone by, so saying "you are waiting for it" would be a
    // plain untruth — name it as passed and leave the real blocker above.
    lines.push(`Your start date of ${startDate} has already passed, so nothing else is waiting on it.`);
  }

  if (has('invitation')) {
    lines.push('Your programme team has not sent your platform invitation yet.');
    steps.push('Your programme team sends your invitation email.');
    steps.push('Follow the invitation to set your password and sign in.');
  }

  steps.push(
    commercial
      ? 'Your learning access activates automatically once that is done.'
      : 'Your training plan and learning materials appear here once that is done.',
  );

  return { title, lines, steps };
}
