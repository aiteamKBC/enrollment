import { learningFetch } from '@/lib/personalLearning';
/** Versioned assignment data, persisted inside full_submission (not browser-only). */
export interface MonthlyAssignment {
  version: 2;
  month: string;
  timeEntries?: Array<{ topic: string; hours: string; date: string }>;
  step: number;
  understood: string;
  gainedSkills: string;
  evidence: Array<{ id: string; name: string; url?: string; points: string }>;
  claims: Array<{ code: string; explanation: string; evidenceIds: string[] }>;
  plannedReviewed: boolean;
  newKnowledge: boolean;
  newSkills: boolean;
  sharingConsent: boolean;
  paidHours: boolean;
  lmsReflection: string;
  extraActivities: string;
  integratedReflection: string;
  careerImpact: string;
  jobImpact: string;
  employerImpact: string;
  employerBenefit: boolean;
  actionPlan: string;
  epaPreparedness: string;
  meetingKey: string;
  slides: Array<{ title: string; body: string }>;
  presentationDesign?: { name: string; accent: string; font: string; ratio: number; evidenceId?: string; slideCount?: number; coverSlide?: number; contentSlide?: number };
  presentationReviewed: boolean;
  presentationToken: string;
  uploadedPresentation?: { id: string; name: string };
}

export const MONTHLY_STEPS = ['Assignment answer', 'Evidence & cross-referencing', 'KSBs & hours claimed', 'Full-month reflection', 'Impact & employer benefit', 'Action plan & EPA', 'Coaching & presentation', 'Quality checks'];

export function emptyMonthlyAssignment(codes: string[], month: string): MonthlyAssignment {
  return {
    version: 2, month, timeEntries: [], step: 0, understood: '', gainedSkills: '', evidence: [],
    claims: codes.map(code => ({ code, explanation: '', evidenceIds: [] })),
    plannedReviewed: false, newKnowledge: false, newSkills: false, sharingConsent: false, paidHours: false,
    lmsReflection: '', extraActivities: '', integratedReflection: '', careerImpact: '', jobImpact: '',
    employerImpact: '', employerBenefit: false, actionPlan: '', epaPreparedness: '', meetingKey: '',
    slides: [], presentationReviewed: false, presentationToken: '',
  };
}

export interface AssignmentQualityCheck { key: string; label: string; passed: boolean }

export async function checkMonthlyAssignment(payload: unknown): Promise<AssignmentQualityCheck[]> {
  const res = await learningFetch('/learner_api/reflection/assignment/check/', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Could not check your submission. Your draft is still available.');
  return data.checks;
}

export async function exportMonthlyPresentation(payload: unknown): Promise<{ blob: Blob; token: string }> {
  const res = await learningFetch('/learner_api/reflection/assignment/presentation/', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || 'Could not generate the PowerPoint.');
  }
  return { blob: await res.blob(), token: res.headers.get('X-Presentation-Token') || '' };
}
