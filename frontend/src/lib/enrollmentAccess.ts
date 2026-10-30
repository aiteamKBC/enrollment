import type { AuthUser } from '@/api/auth';

export function canManageEnrolment(account: Pick<AuthUser, 'role' | 'access' | 'accesses' | 'accessWorkspaces'>): boolean {
  const grants = [account.access, ...(account.accesses ?? []), ...(account.accessWorkspaces ?? []).map(item => item.access)];
  return account.role === 'admin' || (account.role === 'staff' && grants.some(grant => grant === 'enrolment' || grant === 'super-admin'));
}

export function hasOwnEnrolment(account: Pick<AuthUser, 'role' | 'learnerRecordId'>): boolean {
  return account.role === 'learner' || Boolean(['admin', 'staff'].includes(account.role) && account.learnerRecordId);
}
