import { learningFetch } from '@/lib/personalLearning';
import { readLearnerJson } from './learnerRead';
import type { CertificateTemplate } from './platformAdmin';

export type CertificateTemplateSummary = Pick<CertificateTemplate,
  'id' | 'version' | 'title' | 'minimumProgress' | 'requireFinalTest'>;

export interface LearnerCertificateEligibility {
  eligible: boolean;
  missing: string[];
  progressPercent: number;
  minimumProgress: number;
  trackableDone: number;
  trackableTotal: number;
  finalTestPassed: boolean;
  learner: {
    id: string;
    kind: string;
    name: string;
    email: string;
    programme: string;
    cohort: string;
    group: string;
    employer: string;
  };
}

export interface LearnerCertificate {
  id: number;
  certificateNumber: string;
  templateId: number;
  templateVersion: number;
  progressPercent: number;
  snapshot: {
    certificateTitle?: string;
    bodyText?: string;
    layoutConfig?: CertificateTemplate['layoutConfig'];
    learner?: LearnerCertificateEligibility['learner'];
    programme?: string;
    moduleRef?: string;
    moduleTitle?: string;
    progressPercent?: number;
    minimumProgress?: number;
    finalTestPassed?: boolean;
  };
  issuedAt: string | null;
  pdfBlobUrl: string;
  verificationToken: string;
  verificationUrl: string;
  moduleRef?: string;
  moduleTitle?: string;
}

export interface LearnerCertificateStatus {
  configured: boolean;
  template: CertificateTemplate | CertificateTemplateSummary | null;
  certificate: LearnerCertificate | null;
  eligibility?: LearnerCertificateEligibility;
}

async function readJson<T>(response: Response): Promise<T> {
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new Error((data && data.error) || `Request failed (${response.status})`);
  }
  return data as T;
}

export async function fetchLearnerCertificateTemplate(kind: string, id: number | string) {
  return readLearnerJson<{ configured: boolean; template: CertificateTemplateSummary | null; csrfToken?: string }>(
    `/learner_api/certificates/${kind}/${id}/template/?summary=1`, { ttlMs: 30_000 },
  );
}

export async function issueLearnerModuleCertificate(kind: string, id: number | string, subjectRef: string, csrfToken: string) {
  if (!csrfToken) throw new Error('Request verification is unavailable. Please reload the page.');
  const response = await learningFetch(`/learner_api/certificates/${kind}/${id}/modules/${encodeURIComponent(subjectRef)}/issue/`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'X-CSRFToken': csrfToken, 'X-Requested-With': 'XMLHttpRequest' },
  });
  return readJson<LearnerCertificateStatus & { issued: boolean }>(response);
}
