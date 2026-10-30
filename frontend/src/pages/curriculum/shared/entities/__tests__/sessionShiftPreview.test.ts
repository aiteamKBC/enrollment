import { describe, expect, it } from 'vitest';
import { type HolidayShiftSessionLike } from '../sessionShiftPreview';
import { buildHolidayShiftPlan } from "../sessionShiftPreview.helpers";

function session(date: string, skippedHolidays: string[] = []): HolidayShiftSessionLike {
  return { date, skippedHolidays };
}

const holidayLabelFor = (date: string) => (
  date === '2027-02-10' || date === '2027-03-24' ? 'Easter 27' : ''
);

// The clash rule is parked: a ticked holiday landing on a session is a WARNING
// and nothing else. It does not move that session, it does not push the run
// behind it, it does not produce a replacement date and it does not move the
// module's end date. The only thing a holiday still produces is its own name
// against the session's own date, which is what the warning reads.
//
// This file used to pin the opposite — that a clash rolled a session onto the
// next delivery day and reported it as moved — and was rewritten when the rule
// was parked. The shifting arithmetic it covered is kept, commented out, in
// sessionShiftPreview.tsx.
describe('buildHolidayShiftPlan', () => {
  it('names the holiday on a session without moving anything', () => {
    const sessions = [
      session('2027-02-03'),
      session('2027-02-10', ['2027-02-10']),
      session('2027-02-17'),
      session('2027-03-24', ['2027-03-24']),
      session('2027-03-31'),
    ];

    const plan = buildHolidayShiftPlan(sessions, holidayLabelFor);

    // A session a holiday falls on keeps its own date: where it was due and
    // where it runs are the same day, and it is not reported as moved.
    expect(plan.shifts[1]).toMatchObject({
      sessionNumber: 2, moved: false, originalDate: '2027-02-10', actualDate: '2027-02-10',
    });
    // The holiday is still named against it — that is the warning.
    expect(plan.shifts[1].clashes).toEqual([{ date: '2027-02-10', holiday: 'Easter 27' }]);

    // The session after it is untouched in every way.
    expect(plan.shifts[2]).toMatchObject({
      sessionNumber: 3, moved: false, originalDate: '2027-02-17', actualDate: '2027-02-17',
    });
    expect(plan.shifts[2].clashes).toEqual([]);

    // Every clash field stays inert, on the clashing session as much as on the
    // clear ones.
    expect(plan.shifts.every(shift => !shift.moved)).toBe(true);
    expect(plan.shifts.map(shift => shift.gapLabel)).toEqual(['', '', '', '', '']);
    expect(plan.shifts.map(shift => shift.headline)).toEqual(['', '', '', '', '']);
    expect(plan.shifts.map(shift => shift.detail)).toEqual(['', '', '', '', '']);

    // Nothing moved, so nothing is summarised as moved.
    expect(plan.movedCount).toBe(0);
    expect(plan.movedRangeLabel).toBe('');

    // Both closed dates are still listed, so a screen can warn about them.
    expect(plan.closures).toEqual([
      { date: '2027-02-10', label: 'Easter 27' },
      { date: '2027-03-24', label: 'Easter 27' },
    ]);
  });

  it('ends the run where the plan ends it, holidays or not', () => {
    const plan = buildHolidayShiftPlan(
      [session('2027-02-03'), session('2027-02-10', ['2027-02-10'])],
      holidayLabelFor,
    );

    // The run a holiday never touched ends on the same day this one does, so
    // the pair a caller renders reads as "unchanged" rather than "rolled".
    expect(plan.shiftedEndDate).toBe('2027-02-10');
    expect(plan.originalEndDate).toBe('2027-02-10');
  });

  it('keeps the planner’s own original end date when it supplies one', () => {
    const plan = buildHolidayShiftPlan(
      [session('2027-02-03'), session('2027-02-10', ['2027-02-10'])],
      holidayLabelFor,
      '2027-02-10',
    );

    expect(plan.originalEndDate).toBe('2027-02-10');
  });
});
