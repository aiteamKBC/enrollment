import { parseNumeric } from '@/lib/format';

/** Dashboard OTJH compares canonical actual hours with the existing
 * learner-detail target-to-date; whole-programme planned hours are separate. */
export function dashboardOtjhProgress(actual: number | null | undefined, targetValue: string | number | null | undefined) {
  const target = parseNumeric(targetValue);
  const percent = actual != null && Number.isFinite(actual) && target != null && target > 0
    ? Math.min(100, Math.round((actual / target) * 100))
    : null;
  return { target, percent };
}
