import { afterEach, expect, it, vi } from 'vitest';
import { fetchEligibilityForm, submitApprenticeshipApplication, type ApplicationSubmission } from '../apprenticeshipApplication';
import { resolveLayout } from '@/pages/apprenticeship-application/layout/resolve';

afterEach(() => vi.unstubAllGlobals());

it.each([undefined, false, 'true', true])('uses only the explicit server capability (%s) to enable workplace preview', async capability => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ layout: { sections: [], workplaceEnabled: true }, version: 2, workplaceEnabled: capability }),
  }));
  const response = await fetchEligibilityForm();
  expect(response.workplaceEnabled).toBe(capability === true);
  const layout = resolveLayout(response.layout, response.workplaceEnabled === true);
  expect(layout.sections.flatMap(section => section.items).some(item => item.key === 'workplaceArrangement')).toBe(capability === true);
});

it('recognises pendingAssessment independently of technical retry outcomes', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: true, status: 201,
    json: async () => ({ submitted: true, eligible: null, reasons: [], outcome: 'pendingAssessment',
      assessmentStatus: 'pending_assessment', assessmentReason: 'Workplace evidence needs assessment.' }),
  }));
  const response = await submitApprenticeshipApplication({} as ApplicationSubmission);
  expect(response).toEqual({ eligible: null, reasons: [], outcome: 'pendingAssessment',
    assessmentStatus: 'pending_assessment', assessmentReason: 'Workplace evidence needs assessment.' });
});

it.each(['approved', 'rejected', 'more_evidence_required'])('recognises a current manual %s decision on an idempotent submission retry', async status => {
  const review = { status, version: 1, message: 'A current staff decision is available.' };
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 201,
    json: async () => ({ submitted: true, eligible: status === 'rejected' ? false : null,
      reasons: [], outcome: status === 'rejected' ? 'notEligible' : 'pendingAssessment', review }),
  }));
  expect((await submitApprenticeshipApplication({} as ApplicationSubmission)).review).toEqual(review);
});
