/** Display dates recorded against the learner, with no delivery/plan fallback. */
export function learnerRecordedDates(learner?: {
  learnerStartDate?: string | null;
  learnerEndDate?: string | null;
} | null) {
  return {
    start: learner?.learnerStartDate?.trim() || null,
    end: learner?.learnerEndDate?.trim() || null,
  };
}
