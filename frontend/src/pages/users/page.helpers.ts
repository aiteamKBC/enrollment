import { type StaffUserRow } from '@/api/staffUsers';
import { type EmployerRow } from '@/api/employers';
import type { UserListRow, UsersFilter } from './types';

// The learning plan is only editable once the three onboarding reviews are
// signed and the learner has moved into delivery — see the backend's
// promote_to_delivery_if_ready.
export const DELIVERY_STATUS = 'Delivery';


/** Statuses whose learning plan can still be changed from the enrolment
 *  directory. Delivery is the planning stage, but a plan is not frozen when
 *  teaching starts: modules get added, swapped or corrected mid-programme, and
 *  an enrolment officer had no way to do it without moving the learner
 *  backwards. 'On break' is included for the same reason Shift module includes
 *  it — a paused learner is still on a programme.
 *
 *  Anything else (Withdrawn, Completed, EnteredEpa, Onboarding) opens the plan
 *  read-only: those plans are history or not yet started. */
export const PLAN_EDITABLE_STATUSES: string[] = [DELIVERY_STATUS, 'Active', 'On break'];


/**
 * A directory row. Learner rows are plain UserListRows; staff rows carry the
 * extra Staff_users columns the edit modal prefills from, so the list holds the
 * union and narrows on `source === 'staff'`.
 */
export type DirectoryRow = UserListRow & Partial<StaffUserRow> & { employer?: EmployerRow };


/**
 * Whether a directory row is a learner record.
 *
 * Keyed on `source` — which table the row came from — and never on the Type
 * column. Type is an editable label on the learner row (User, Referrer,
 * Caseowner...), so reading "is this a learner" off it meant relabelling
 * somebody silently took away their programme, their learning plan, their
 * status badge and every action on the row, including the Edit button needed to
 * put it back. What a row *is* cannot be a field somebody can type over.
 */
export function isLearnerRow(row: Pick<UserListRow, 'source'>): boolean {
  return row.source !== 'staff' && row.source !== 'employer';
}


/**
 * An employer contact, shaped like a UserListRow so the directory can list them
 * alongside learners and staff.
 *
 * Employers aren't learners or staff: they have no subscription, learning plan or
 * programme, so those columns stay blank rather than being filled with something
 * that isn't true. Their organisations stand in for the Group column, which is
 * the closest real equivalent — a learner's group is their cohort, an employer's
 * is the company they belong to.
 */
/** Keep staff, learner and employer records separate even when emails match.
 * Import updates may contain an already loaded record, so deduplicate only by
 * the record's own source and id. The first copy is the newest. */
export function uniqueDirectoryRows(rows: DirectoryRow[]): DirectoryRow[] {
  const seen = new Set<string>();
  return rows.filter((row) => {
    const key = `${row.source ?? 'apprenticeship'}:${row.id}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}


/** True when a row value doesn't match a chosen filter value, ignoring case/padding. */
export function differs(rowValue: string | undefined, filterValue: string): boolean {
  return (rowValue ?? '').trim().toLowerCase() !== filterValue.trim().toLowerCase();
}


export function matches(row: DirectoryRow, f: UsersFilter): boolean {
  if (f.userName && !row.name.toLowerCase().includes(f.userName.toLowerCase())) return false;
  if (f.email && !row.email.toLowerCase().includes(f.email.toLowerCase())) return false;
  // Folded like programme/cohort below: group names can now come from the
  // curriculum lookup rather than only from the rows themselves.
  if (f.groups && f.groups.length > 0 && !f.groups.some((g) => !differs(row.group, g))) return false;
  if (f.statuses && f.statuses.length > 0 && !f.statuses.includes(row.subscriptionStatus)) return false;
  if (f.type && f.type !== 'all' && row.type !== f.type) return false;
  // Programme and cohort are picked from the curriculum lists, but the values on
  // the row were written as free text at create time, so both sides are folded
  // before comparing — casing drift on an older row shouldn't empty the results.
  if (f.programme && differs(row.programme, f.programme)) return false;
  if (f.cohort && differs(row.cohort, f.cohort)) return false;
  if (f.programmeStatus && (row.programmeStatus ?? '') !== f.programmeStatus) return false;
  // 'any' is the unset value here, not '' — the select's own placeholder. Owner
  // names are free text on the row like programme and cohort, so they are
  // compared the same folded way.
  if (f.caseOwner && f.caseOwner !== 'any' && differs(row.caseOwner, f.caseOwner)) return false;
  return true;
}
