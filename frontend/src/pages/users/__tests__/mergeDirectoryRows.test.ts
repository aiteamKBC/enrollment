import { describe, expect, it } from 'vitest';
import { matches, uniqueDirectoryRows } from '../page.helpers';
import type { UserListRow } from '../types';

function row(over: Partial<UserListRow>): UserListRow {
  return {
    id: '1', uuid: null, name: 'Someone', type: 'User',
    email: 'someone@kbc.test', group: '', subscriptionStatus: '',
    subscriptionVerified: false, learningPlan: false, programmeStatus: '',
    ...over,
  } as UserListRow;
}

const staff = row({ id: '43', type: 'Admin', source: 'staff', email: 'someone@kbc.test' });
const learner = row({
  id: '510', type: 'User', source: 'commercial', email: 'SOMEONE@kbc.test',
  programmeStatus: 'Delivery', learningPlan: true, group: 'G1',
});

describe('user directory record identity', () => {
  it('keeps learner and admin records with the same email in separate rows', () => {
    const rows = uniqueDirectoryRows([learner, staff]);
    expect(rows).toHaveLength(2);
    expect(rows.map(({ id, source, type }) => ({ id, source, type }))).toEqual([
      { id: '510', source: 'commercial', type: 'User' },
      { id: '43', source: 'staff', type: 'Admin' },
    ]);
    expect(rows[0].programmeStatus).toBe('Delivery');
    expect(rows[1].programmeStatus).toBe('');
  });

  it('keeps staff and employer records with the same email separate', () => {
    const rows = uniqueDirectoryRows([staff, row({ id: '43', type: 'Employer', source: 'employer', email: staff.email })]);
    expect(rows.map(({ source, type }) => ({ source, type }))).toEqual([
      { source: 'staff', type: 'Admin' },
      { source: 'employer', type: 'Employer' },
    ]);
  });

  it('replaces only an imported copy of the same record', () => {
    const imported = { ...learner, name: 'Updated learner' };
    const rows = uniqueDirectoryRows([imported, learner, staff]);
    expect(rows).toHaveLength(2);
    expect(rows[0].name).toBe('Updated learner');
    expect(rows[1].type).toBe('Admin');
  });

  it('filters each role independently', () => {
    const rows = uniqueDirectoryRows([learner, staff]);
    expect(rows.filter((entry) => matches(entry, { type: 'Admin' } as never))).toEqual([staff]);
    expect(rows.filter((entry) => matches(entry, { type: 'User' } as never))).toEqual([learner]);
  });
});
