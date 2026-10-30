import { expect, it } from 'vitest';
import { canManageEnrolment, hasOwnEnrolment } from './enrollmentAccess';
import { homeRouteFor } from './routeAccess';
it('recognises secondary enrolment grants while retaining staff ownership', () => {
  const account = { role: 'staff' as const, access: 'coach', accessWorkspaces: [{ access: 'enrolment', home: '/users' }] };
  expect(canManageEnrolment(account)).toBe(true);
  expect(hasOwnEnrolment(account)).toBe(false);
});
it('opens a staff member own enrolment without granting enrolment administration', () => {
  const account = { role: 'staff' as const, subjectId: 10, access: 'coach', learnerRecordId: 101 };
  expect(canManageEnrolment(account)).toBe(false);
  expect(hasOwnEnrolment(account)).toBe(true);
  expect(homeRouteFor(account)).toBe('/learner/home');
});
it('keeps learner and employer identities separate', () => {
  expect(canManageEnrolment({ role: 'learner' })).toBe(false);
  expect(hasOwnEnrolment({ role: 'learner' })).toBe(true);
  expect(hasOwnEnrolment({ role: 'employer', learnerRecordId: 101 })).toBe(false);
  expect(homeRouteFor({ role: 'employer', subjectId: 25 })).toBe('/employers/25/documents');
});
