import { describe, expect, it } from 'vitest';
import { mayAccessRoute } from './routeAccess';

const account = (role: 'admin' | 'staff') => ({
  role,
  access: role === 'admin' ? 'super-admin' : 'enrolment',
  accessWorkspaces: [],
});

describe('Suitability & RPL report route access', () => {
  it('admits administrators and rejects enrolment staff', () => {
    expect(mayAccessRoute('/admin/suitability-rpl-report', account('admin'))).toBe(true);
    expect(mayAccessRoute('/admin/suitability-rpl-report', account('staff'))).toBe(false);
  });
});
