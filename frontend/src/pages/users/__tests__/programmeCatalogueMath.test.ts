import { expect, it } from 'vitest';
import { calculatePricing, cohortSchedule, costLines, formatAmountInput, fundingBandComparison, gbp, hundredths, moduleEnd, showDateRange } from '../programme-structure/catalogMath';
import { makeKsbCsv, makeKsbMappingCsv, parseKsbCsv } from '../programme-structure/ksbCsv';

it('escapes mapping headers and UTF-8 descriptions and exports each visible assignment', () => {
  const meta = { id: 'one', revision: 1, source_system: 'manual', external_id: '', archived_at: null };
  const modules = ['Planning, "phase one"', 'Delivery'].map((name, index) => ({
    ...meta, id: String(index), name, weeks: 1, lectures: 1, books: null, reviews: null,
    weekly_topics: [], current_revision: null, ksb_ids: index === 0 ? ['one'] : [],
  }));
  const csv = makeKsbMappingCsv([{ ...meta, code: 'K1', description: 'Café, "scope"\nreview' }], modules);
  expect(csv).toBe('code,description,"Planning, ""phase one""","Delivery"\r\n"K1","Café, ""scope""\nreview","Yes","No"\r\n');
});

it('reconciles the entire preview quote in whole pounds and preserves session length precision', () => {
  const costs = { total_cost: '11000', epa_cost: '1000', lecture_hours: '2.25' };
  const sessions = [{ lectures: 8, reviews: 7, books: 3 }];
  for (let base = 0; base < 100; base += 1) {
    const quote = calculatePricing({ ...costs, total_cost: String(1000 + base) }, sessions, true)!;
    expect(Object.values(quote.allocations).reduce((sum, value) => sum + Number(value), 0)).toBe(1000 + base);
    expect(quote.total_training).toBe(String(Math.round(base * 0.8)));
  }
  expect(calculatePricing(costs, sessions, true)?.rates).toEqual({ training_hour: '444', review: '214', book: '167' });
  expect(calculatePricing({ ...costs, total_cost: '11000.25' }, sessions, true)).toBeNull();
  expect(formatAmountInput('6000.00', true)).toBe('6000');
  expect(gbp('6000', true)).toBe('£6,000');
});

it('round-trips a UTF-8 KSB CSV with commas, quoted descriptions and line breaks', () => {
  const rows = [{ code: 'K1', description: 'Practice "safe",\nthen review.' }, { code: 'S1', description: '=A1' }];
  const csv = makeKsbCsv(rows);
  expect(csv).toMatch(/^code,description\r\n/);
  expect(csv).toContain('"\'=A1"');
  expect(parseKsbCsv('\uFEFF' + csv)).toEqual(rows);
  expect(parseKsbCsv('code,description\n\n s2 , Detail ')).toEqual([{ code: 'S2', description: 'Detail' }]);
});

it.each([
  ['code,description\nK1,One\nk1,Two', /Row 3.*Duplicate/],
  ['code,description\nK9,', /Row 2.*description/],
  ['code,description\n,Missing code', /Row 2.*code/],
  ['code,description\nK9,"unclosed', /Row 2.*quoting/],
  ['code,description\nK9,one,extra', /Row 2.*contain/],
  ['code,description\nK9', /Row 2.*contain/],
  ['code,name\nK9,One', /first row/],
  ['code,description\n', /at least one/],
])('rejects invalid KSB CSV data %j', (csv, message) => {
  expect(() => parseKsbCsv(csv)).toThrow(message);
});

it('rejects case-insensitive code collisions with the programme library', () => {
  expect(() => parseKsbCsv('code,description\n k1 ,Replacement', [{ code: 'K1' }])).toThrow('Duplicate KSB code K1');
});

it('reconciles every penny, including the 80% training pool and quarter-penny ties', () => {
  for (let cents = 0; cents < 2000; cents += 1) {
    const total = (1000 + cents / 100).toFixed(2);
    const quote = calculatePricing({ total_cost: total, epa_cost: '1000.00', lecture_hours: '2.50' })!;
    expect(Object.values(quote.allocations).reduce((sum, value) => sum + hundredths(value)!, 0n)).toBe(BigInt(100000 + cents));
    expect(hundredths(quote.total_training)).toBe(BigInt(Math.round(cents * 0.8)));
  }
  const quote = calculatePricing({ total_cost: '0.02', epa_cost: '0', lecture_hours: '2.50' })!;
  expect(quote.allocations).toEqual({ kbc_training: '0.01', distance_training: '0.01', assessment: '0.00', materials: '0.00', epa: '0.00' });
  expect(costLines.map(line => line[2])).toEqual([40, 40, 15, 5, null]);
});

it('matches the saved quote policy for selected revisions, partial counts and precise lecture durations', () => {
  const costs = { total_cost: '11000', epa_cost: '1000', lecture_hours: '2.50' };
  const group = [{ lectures: 8, reviews: 2, books: 1 }];
  expect(calculatePricing(costs, group)?.rates).toEqual({ training_hour: '400.00', review: '750.00', book: '500.00' });
  expect(calculatePricing({ ...costs, lecture_hours: '2.25' }, group)?.rates.training_hour).toBe('444.44');
  expect(calculatePricing(costs, [...group, { lectures: null, reviews: 0, books: 2 }])?.rates)
    .toEqual({ training_hour: null, review: '750.00', book: '166.67' });
  expect(calculatePricing(costs, [{ lectures: 0, reviews: 0, books: 0 }])?.rates)
    .toEqual({ training_hour: null, review: null, book: null });
  expect(calculatePricing({ ...costs, total_cost: '999.999' }, group)).toBeNull();
});

it('anchors inclusive cohort boundaries and EPA months to the original start date', () => {
  const cases = [
    ['2027-01-01', 1, 0, '2027-01-31', '2027-01-31'],
    ['2027-01-31', 1, 1, '2027-02-27', '2027-03-30'],
    ['2028-01-31', 1, 1, '2028-02-28', '2028-03-30'],
    ['2028-02-29', 12, 1, '2029-02-27', '2029-03-28'],
    ['2027-12-01', 2, 1, '2028-01-31', '2028-02-29'],
    ['9999-12-01', 1, 0, '9999-12-31', '9999-12-31'],
  ] as const;
  for (const [start, training, epa, end, apprenticeshipEnd] of cases) {
    expect(cohortSchedule(start, training, epa)).toMatchObject({
      training_end_date: end, apprenticeship_end_date: apprenticeshipEnd,
      training_duration_months: training, apprenticeship_duration_months: training + epa, epa_unit: 'months',
    });
  }
  expect(cohortSchedule('2027-02-29', 1, 0)).toBeNull();
  expect(cohortSchedule('9999-12-02', 1, 0)).toBeNull();
  expect(cohortSchedule('2027-01-01', 1, 0.5)).toBeNull();
  expect(moduleEnd('2027-01-04', 2)).toBe('2027-01-17');
  expect(showDateRange('2027-01-04', '2027-01-17')).toMatch(/4\s*[–-]\s*17 Jan 2027/);
});

it('compares the funding band with the total without changing the base calculation', () => {
  const quote = calculatePricing({ total_cost: '12000.01', epa_cost: '1000', lecture_hours: '2.50' })!;
  expect(quote.base_cost).toBe('11000.01');
  expect(fundingBandComparison(quote.total_cost, '12000')).toEqual({ direction: 'above', difference: '0.01' });
  expect(fundingBandComparison(quote.total_cost, '13000')).toEqual({ direction: 'below', difference: '999.99' });
  expect(fundingBandComparison(quote.total_cost, '12000.01')).toEqual({ direction: 'equal', difference: '0.00' });
  expect(fundingBandComparison(quote.total_cost, '')).toBeNull();
  expect(formatAmountInput('1000.1')).toBe('1000.10');
});

it('uses the day after a manual training end for EPA and validates inclusive month boundaries', () => {
  expect(cohortSchedule('2028-01-01', 1, 1, { trainingOverride: '2028-01-30' })).toMatchObject({
    automatic_training_end_date: '2028-01-31', training_end_date: '2028-01-30', apprenticeship_end_date: '2028-02-28',
  });
  expect(cohortSchedule('2027-12-01', 1, 1, { trainingOverride: '2028-01-31' })?.apprenticeship_end_date).toBe('2028-02-29');
  expect(cohortSchedule('2027-01-01', 1, 0, { trainingOverride: '2027-01-30' })?.apprenticeship_end_date).toBe('2027-01-30');
  expect(cohortSchedule('9999-12-01', 1, 0, { trainingOverride: '9999-12-31' })?.apprenticeship_end_date).toBe('9999-12-31');
  expect(cohortSchedule('9999-12-01', 1, 1, { trainingOverride: '9999-12-31' })).toBeNull();
  expect(cohortSchedule('2028-01-01', 1, 1, { trainingOverride: '2027-12-31' })).toBeNull();
  expect(cohortSchedule('2028-01-01', 1, 1, { apprenticeshipOverride: '2028-01-30' })).toBeNull();
});
