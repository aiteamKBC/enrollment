/**
 * One row per person in the user directory.
 *
 * Somebody who is both staff and a learner — an administrator or coach who also
 * studies a programme — has a row in `Staff_users` AND a row in
 * `Created_users`, on the same address. The directory loads those two lists
 * separately and used to concatenate them, so that person appeared twice: the
 * same human listed as two users, each carrying half the truth.
 *
 * The learner record wins the merged row, because it is the one with the
 * programme, the learning plan and the enrolment actions the directory exists
 * to offer.
 */
import { describe, expect, it } from 'vitest';
import { matches, mergeDirectoryRows } from "../page.helpers";
import type { UserListRow } from '../types';

function row(over: Partial<UserListRow>): UserListRow {
  return {
    id: '1',
    uuid: null,
    name: 'Someone',
    type: 'User',
    email: 'someone@kbc.test',
    group: '',
    subscriptionStatus: '',
    subscriptionVerified: false,
    learningPlan: false,
    programmeStatus: '',
    ...over,
  } as UserListRow;
}

const staff = row({
  id: '43', name: 'Mahmoud Fouda', type: 'Admin', source: 'staff',
  email: 'Mahmoud.Fouda@kentbusinesscollege.com',
});
const learner = row({
  id: '510', name: 'Mahmoud Fouda', type: 'User', source: 'commercial',
  email: 'mahmoud.fouda@kentbusinesscollege.com',
  programmeStatus: 'Delivery', learningPlan: true, group: 'G1',
});

describe('mergeDirectoryRows', () => {
  it('lists a person who is both staff and a learner once', () => {
    expect(mergeDirectoryRows([learner, staff])).toHaveLength(1);
  });

  it('matches the two records regardless of address casing', () => {
    // The staff row spells it Mahmoud.Fouda@…, the learner row mahmoud.fouda@…
    // — the same person, and the same match the backend makes.
    expect(mergeDirectoryRows([staff, learner])).toHaveLength(1);
  });

  it('keeps the learner record as the row, whichever order they arrive in', () => {
    for (const rows of [[learner, staff], [staff, learner]]) {
      const [merged] = mergeDirectoryRows(rows);
      expect(merged.id).toBe('510');
      expect(merged.source).toBe('commercial');
      expect(merged.programmeStatus).toBe('Delivery');
      expect(merged.learningPlan).toBe(true);
    }
  });

  it('shows both roles in the Type column', () => {
    // The person really is both; showing one made the merged row look as
    // though the other record had been lost.
    const [merged] = mergeDirectoryRows([staff, learner]);

    expect(merged.type).toBe('Admin · User');
  });

  it('does not repeat a role the two records agree on', () => {
    const [merged] = mergeDirectoryRows([
      row({ id: '1', email: 'x@kbc.test', source: 'staff', type: 'User' }),
      row({ id: '2', email: 'x@kbc.test', source: 'commercial', type: 'User' }),
    ]);

    expect(merged.type).toBe('User');
  });

  it('leaves a single-record row’s type untouched', () => {
    const [merged] = mergeDirectoryRows([staff]);

    expect(merged.type).toBe('Admin');
  });

  it('keeps the staff position where the learner record has none', () => {
    // Nothing is lost by merging: the staff row fills in underneath.
    const [merged] = mergeDirectoryRows([
      staff, row({ ...learner, type: '' }),
    ]);

    expect(merged.type).toBe('Admin');
  });

  it('leaves everybody else alone', () => {
    const others = [
      row({ id: '2', email: 'a@kbc.test', source: 'staff' }),
      row({ id: '3', email: 'b@kbc.test', source: 'commercial' }),
      row({ id: '4', email: 'c@kbc.test', source: 'employer' }),
    ];

    expect(mergeDirectoryRows(others)).toHaveLength(3);
  });

  it('never merges rows that have no address', () => {
    // Two blank addresses are not evidence of the same person.
    const blanks = [
      row({ id: '5', email: '', source: 'staff' }),
      row({ id: '6', email: '', source: 'commercial' }),
    ];

    expect(mergeDirectoryRows(blanks)).toHaveLength(2);
  });

  it('preserves the order the rows arrived in', () => {
    const first = row({ id: '9', email: 'first@kbc.test', source: 'commercial' });
    const merged = mergeDirectoryRows([first, learner, staff]);

    expect(merged.map(r => r.id)).toEqual(['9', '510']);
  });

  it('merges a staff row with an employer row on one address', () => {
    // Neither is a learner, so the later record simply fills the earlier one in
    // rather than one of them being dropped.
    const merged = mergeDirectoryRows([
      row({ id: '7', email: 'dual@kbc.test', source: 'staff', type: 'Admin' }),
      row({ id: '8', email: 'dual@kbc.test', source: 'employer', group: 'Acme' }),
    ]);

    expect(merged).toHaveLength(1);
    expect(merged[0].group).toBe('Acme');
  });
});

describe('the Type filter on a merged row', () => {
  it('finds a merged "Admin · User" row when filtering for Admin', () => {
    const [merged] = mergeDirectoryRows([staff, learner]);

    expect(matches(merged, { type: 'Admin' } as never)).toBe(true);
  });

  it('finds the same merged row when filtering for User', () => {
    const [merged] = mergeDirectoryRows([staff, learner]);

    expect(matches(merged, { type: 'User' } as never)).toBe(true);
  });

  it('still excludes a row that holds neither role', () => {
    const [merged] = mergeDirectoryRows([staff, learner]);

    expect(matches(merged, { type: 'Employer' } as never)).toBe(false);
  });

  it('behaves exactly as before on an ordinary, unmerged row', () => {
    expect(matches(staff, { type: 'Admin' } as never)).toBe(true);
    expect(matches(staff, { type: 'User' } as never)).toBe(false);
  });
});
