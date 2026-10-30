import { readLearnerJson } from './learnerRead';
import type { LearnerKind } from './learnerDetail';

/**
 * The learner-record values the ILR Learner Details step shows read-only —
 * name, DOB, current address, phone, email — straight from
 * enrolment."Created_users". See backend/enrolment_api/ilr_learner_record.py.
 */
export interface IlrLearnerRecord {
  familyName: string;
  givenNames: string;
  /** As stored on the record — usually YYYY-MM-DD. */
  dateOfBirth: string;
  currentPostcode: string;
  addressLine1: string;
  addressLine2: string;
  addressLine3: string;
  addressLine4: string;
  telephone: string;
  email: string;
  nationalInsuranceNumber: string;
  legalSex: string;
}

export function fetchIlrLearnerRecord(kind: LearnerKind, learnerId: string): Promise<IlrLearnerRecord> {
  return readLearnerJson<IlrLearnerRecord>(
    `/enrolment_api/ilr-learner-record/${kind}/${encodeURIComponent(learnerId)}/`,
    { headers: { 'X-Requested-With': 'XMLHttpRequest' } },
  );
}
