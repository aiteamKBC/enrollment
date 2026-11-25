import type { Delivery, PinnedCurriculum, Pricing } from './catalogApi';

export const defaultLectureHours = '2.50';
export const costLines = [
  ['kbc_training', 'Training that the provider will deliver to the Apprentice Off the Job', 40],
  ['distance_training', 'Distance, online or blended learning relating to the off-the-job training', 40],
  ['assessment', 'Assessment that the provider will deliver (including Progress Reviews)', 15],
  ['materials', 'Materials (non-capital items) used in the delivery of the apprenticeship', 5],
  ['epa', 'End Point Assessment', null],
] as const;
const pounds = new Intl.NumberFormat('en-GB', { style: 'currency', currency: 'GBP' });
const wholePounds = new Intl.NumberFormat('en-GB', { style: 'currency', currency: 'GBP', maximumFractionDigits: 0 });
export const gbp = (value: string | null, whole = false) =>
  value == null ? 'Not calculated' : (whole ? wholePounds : pounds).format(Number(value));
export const isWholePoundAmount = (value: unknown) => /^[0-9]{1,10}$/.test(String(value ?? ''));
export const monthCount = (count: number) => `${count} ${count === 1 ? 'month' : 'months'}`;

export function hundredths(input: unknown): bigint | null {
  const parts = /^(\d{0,10})(?:\.(\d{0,2}))?$/.exec(String(input ?? '').trim());
  if (!parts || (!parts[1] && !parts[2])) return null;
  return BigInt(parts[1] || '0') * 100n + BigInt((parts[2] || '').padEnd(2, '0'));
}

const decimal = (value: bigint) => `${value / 100n}.${String(value % 100n).padStart(2, '0')}`;
export const formatAmountInput = (value: string, whole = false) => {
  if (whole) return value.replace(/^(\d+)\.0+$/, '$1');
  const amount = hundredths(value);
  return amount === null ? value : decimal(amount);
};

export function fundingBandComparison(total: string | null, maximum: string | null) {
  const cost = hundredths(total);
  const cap = hundredths(maximum);
  if (cost === null || cap === null) return null;
  return { direction: cost > cap ? 'above' : cost < cap ? 'below' : 'equal', difference: decimal(cost >= cap ? cost - cap : cap - cost) } as const;
}

const round = (numerator: bigint, denominator: bigint) => (numerator * 2n + denominator) / (2n * denominator);
export function calculatePricing(
  costs: { total_cost: unknown; epa_cost: unknown; lecture_hours: unknown },
  revisions: Pick<PinnedCurriculum, 'lectures' | 'reviews' | 'books'>[] = [],
  whole = false,
): Pricing | null {
  if (whole && (!isWholePoundAmount(costs.total_cost) || !isWholePoundAmount(costs.epa_cost))) return null;
  const total = whole ? BigInt(String(costs.total_cost)) : hundredths(costs.total_cost);
  const epa = whole ? BigInt(String(costs.epa_cost)) : hundredths(costs.epa_cost);
  const hours = hundredths(costs.lecture_hours);
  if (total === null || epa === null || total < epa || hours === null || hours < 1n || hours > 2400n) return null;
  const amount = whole ? String : decimal;
  const rate = (cost: bigint, units: bigint) => units > 0n ? amount(round(cost, units)) : null;
  const base = total - epa;
  const training = round(base * 80n, 100n);
  const split = (first: bigint, second: bigint, subtotal: bigint) => {
    const exact = [base * first, base * second];
    const amounts = exact.map(amount => amount / 100n);
    const ranked = [0, 1].sort((left, right) => Number(exact[right] % 100n - exact[left] % 100n));
    const leftover = Number(subtotal - amounts[0] - amounts[1]);
    for (const index of ranked.slice(0, leftover)) amounts[index] += 1n;
    return amounts;
  };
  const [kbc, distance] = split(40n, 40n, training);
  const [assessment, materials] = split(15n, 5n, base - training);
  const count = (key: 'lectures' | 'reviews' | 'books') => revisions.length && revisions.every(row => row[key] != null)
    ? revisions.reduce((sum, row) => sum + row[key]!, 0) : null;
  const counts = { lectures: count('lectures'), reviews: count('reviews'), books: count('books') };
  const trainingHours = counts.lectures === null ? null : BigInt(counts.lectures) * hours;
  return {
    total_cost: amount(total), base_cost: amount(base), total_training: amount(training),
    training_hours: trainingHours === null ? null : decimal(trainingHours), counts,
    allocations: { kbc_training: amount(kbc), distance_training: amount(distance), assessment: amount(assessment), materials: amount(materials), epa: amount(epa) },
    rates: { training_hour: rate(training * 100n, trainingHours ?? 0n), review: rate(assessment, BigInt(counts.reviews ?? 0)), book: rate(materials, BigInt(counts.books ?? 0)) },
  };
}

const day = 86_400_000;
const iso = (date: Date) => date.toISOString().split('T')[0];
function utcDate(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith('0000')) return null;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(+date) && iso(date) === value ? date : null;
}

export function inclusiveDays(start: string, end: string) {
  const first = utcDate(start);
  const last = utcDate(end);
  return first && last && last >= first ? (+last - +first) / day + 1 : null;
}

function monthAnniversary(start: Date, months: number) {
  const end = new Date(start);
  end.setUTCDate(1);
  end.setUTCMonth(start.getUTCMonth() + months + 1, 0);
  end.setUTCDate(Math.min(start.getUTCDate(), end.getUTCDate()));
  return end;
}

const inclusiveMonthEnd = (start: Date, months: number) => new Date(+monthAnniversary(start, months) - day);
const supportedDate = (date: Date | null) => date && date.getUTCFullYear() >= 1 && date.getUTCFullYear() <= 9999 ? iso(date) : null;

type CohortDateOptions = {
  trainingOverride?: string | null;
  apprenticeshipOverride?: string | null;
  legacy?: { trainingEnd: string; epaUnit: string };
};

export function cohortDates(start: string, trainingMonths: number | null, epa: number | null, options: CohortDateOptions = {}) {
  const { trainingOverride, apprenticeshipOverride, legacy } = options;
  const first = utcDate(start);
  const validMonths = trainingMonths != null && Number.isInteger(trainingMonths) && trainingMonths >= 1 && trainingMonths <= 120;
  const expectedTraining = legacy ? utcDate(legacy.trainingEnd) : first && validMonths ? inclusiveMonthEnd(first, trainingMonths) : null;
  const trainingEnd = trainingOverride == null ? expectedTraining : utcDate(trainingOverride);
  let expectedApprenticeship = null;
  if (first && trainingEnd && supportedDate(trainingEnd) && epa != null && Number.isInteger(epa) && epa >= 0 && epa <= 3650) {
    if (!epa) expectedApprenticeship = trainingEnd;
    else if (legacy) expectedApprenticeship = legacy.epaUnit === 'months' ? monthAnniversary(trainingEnd, epa)
      : new Date(+trainingEnd + epa * (legacy.epaUnit === 'weeks' ? 7 : 1) * day);
    else if (trainingOverride != null) expectedApprenticeship = inclusiveMonthEnd(new Date(+trainingEnd + day), epa);
    else if (validMonths) expectedApprenticeship = inclusiveMonthEnd(first, trainingMonths + epa);
  }
  return {
    expectedTraining: supportedDate(expectedTraining), expectedApprenticeship: supportedDate(expectedApprenticeship),
    trainingEnd: supportedDate(trainingEnd),
    apprenticeshipEnd: supportedDate(apprenticeshipOverride == null ? expectedApprenticeship : utcDate(apprenticeshipOverride)),
  };
}

export function cohortSchedule(start: string, trainingMonths: number | null, epa: number, options: CohortDateOptions = {}) {
  if (!Number.isInteger(epa) || epa < 0 || epa > 3650) return null;
  const dates = cohortDates(start, trainingMonths, epa, options);
  if (!dates.expectedTraining || !dates.trainingEnd || !dates.apprenticeshipEnd ||
    dates.expectedTraining < start || dates.trainingEnd < start || dates.apprenticeshipEnd < dates.trainingEnd) return null;
  const durationDays = inclusiveDays(start, dates.trainingEnd);
  if (durationDays == null) return null;
  return {
    training_end_date: dates.trainingEnd, automatic_training_end_date: dates.expectedTraining, training_duration_months: trainingMonths,
    training_end_override: options.trainingOverride ?? null, apprenticeship_end_override: options.apprenticeshipOverride ?? null,
    duration_days: durationDays, epa_unit: options.legacy?.epaUnit ?? 'months',
    apprenticeship_end_date: dates.apprenticeshipEnd, apprenticeship_duration_months: trainingMonths == null ? null : trainingMonths + epa,
  };
}

export const showTime = (time: string) => time.slice(0, 5);

export function moduleEnd(start: string, weeks: number) {
  const first = utcDate(start);
  if (!first || !Number.isInteger(weeks) || weeks < 1 || weeks > 520) return null;
  const end = new Date(+first + (weeks * 7 - 1) * day);
  return end.getUTCFullYear() <= 9999 ? iso(end) : null;
}

export function savedDeliveryEndOverride(delivery?: Delivery) {
  // Older responses expose only the effective date. Keep it until explicitly reset.
  return delivery?.end_date_override !== undefined ? delivery.end_date_override : delivery?.end_date ?? null;
}

const dateFormat = new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeZone: 'UTC' });
const dateRangeFormat = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
export const showDate = (value: string) => {
  const date = utcDate(value);
  return date ? dateFormat.format(date) : 'Not calculated';
};

export function showDateRange(start: string, end: string) {
  const first = utcDate(start);
  const last = utcDate(end);
  return first && last && first <= last ? dateRangeFormat.formatRange(first, last) : 'Not calculated';
}
