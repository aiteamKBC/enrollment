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
/**
 * One row per person, not one row per record.
 *
 * A staff member or administrator who also studies a programme has BOTH a
 * `Staff_users` row and a `Created_users` row on the same address (see
 * login/learner_enrolment.py). Concatenating the three sources listed them
 * twice — the same human appearing as two users, each with half the truth.
 *
 * The learner record wins the row, because it is the one that carries the
 * programme, the learning plan and the enrolment actions the directory exists
 * to offer. The staff fields are merged in underneath, so nothing is lost and
 * the row still opens the person's staff record where that is what was clicked.
 *
 * Matched on the normalised email. That is what makes the two records the same
 * person in every other part of the platform — `existing_learner_record` looks
 * a learner up by address for exactly this reason — and a row with no address
 * cannot be matched to anything, so it is always kept as its own row.
 */
/** The Type column for a person holding two records, e.g. "Admin · User".
 *
 * Distinct labels only, in the order the records were loaded, so somebody who
 * is both staff and a learner reads as both rather than as whichever record
 * happened to win the merge. */
export function combinedType(a: DirectoryRow, b: DirectoryRow): string {
  const labels: string[] = [];
  for (const value of [a.type, b.type]) {
    const label = (value || '').trim();
    if (label && !labels.includes(label)) labels.push(label);
  }
  return labels.join(' · ');
}


/** `over` wins, except where it has nothing to say.
 *
 * Merging two records of the same person must not let one record's empty
 * fields erase the other's real ones: a learner record has no position or
 * access, and a staff row has no programme or learning plan. Each fills the
 * other's gaps. */
export function fillBlanks(over: DirectoryRow, under: DirectoryRow): DirectoryRow {
  const out: DirectoryRow = { ...under };
  for (const [key, value] of Object.entries(over)) {
    const empty = value === undefined || value === null || value === '';
    const existing = Reflect.get(out, key);
    if (!empty || !(key in out) || existing === undefined || existing === null || existing === '') {
      Reflect.set(out, key, value);
    }
  }
  return out;
}


export function mergeDirectoryRows(rows: DirectoryRow[]): DirectoryRow[] {
  const byEmail = new Map<string, number>();
  const merged: DirectoryRow[] = [];

  for (const row of rows) {
    const key = (row.email || '').trim().toLowerCase();
    if (!key) {
      merged.push(row);
      continue;
    }
    const seen = byEmail.get(key);
    if (seen === undefined) {
      byEmail.set(key, merged.length);
      merged.push(row);
      continue;
    }
    const existing = merged[seen];
    // Whichever of the two is the learner keeps the row's identity; the other
    // contributes only the fields the learner record does not have.
    const learner = isLearnerRow(row) ? row : isLearnerRow(existing) ? existing : null;
    const other = learner === row ? existing : row;
    // The learner record wins, but only where it actually says something. A
    // plain spread let its empty strings overwrite real values from the staff
    // row -- a learner record carries no position, so merging one in blanked
    // the Type column for somebody who is an Admin.
    const combined = learner ? fillBlanks(learner, other) : fillBlanks(row, existing);
    // Both roles in the Type column. This person really is an Admin AND a
    // learner, and showing one of them made the merged row look like the other
    // record had simply been lost.
    merged[seen] = { ...combined, type: combinedType(existing, row) };
  }

  return merged;
}


/** True when a row value doesn't match a chosen filter value, ignoring case/padding. */
export function differs(rowValue: string | undefined, filterValue: string): boolean {
  return (rowValue ?? '').trim().toLowerCase() !== filterValue.trim().toLowerCase();
}


/** The role(s) a row's Type column names, split back out of a merged row's
 *  "Admin · User" for filtering and options -- an ordinary row has exactly
 *  one. */
export function rowTypes(row: Pick<DirectoryRow, 'type'>): string[] {
  return (row.type || '').split(' · ').map((s) => s.trim()).filter(Boolean);
}


export function matches(row: DirectoryRow, f: UsersFilter): boolean {
  if (f.userName && !row.name.toLowerCase().includes(f.userName.toLowerCase())) return false;
  if (f.email && !row.email.toLowerCase().includes(f.email.toLowerCase())) return false;
  // Folded like programme/cohort below: group names can now come from the
  // curriculum lookup rather than only from the rows themselves.
  if (f.groups && f.groups.length > 0 && !f.groups.some((g) => !differs(row.group, g))) return false;
  if (f.statuses && f.statuses.length > 0 && !f.statuses.includes(row.subscriptionStatus)) return false;
  // A merged row's type is the two roles joined ("Admin · User"), so an exact
  // match would make it invisible under either filter option. Split and check
  // membership instead, which also matches an ordinary single-role row exactly
  // as before.
  if (f.type && f.type !== 'all' && !rowTypes(row).includes(f.type)) return false;
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
  if (f.referenceNumber && !(row.reference ?? '').toLowerCase().includes(f.referenceNumber.toLowerCase())) return false;
  return true;
}
