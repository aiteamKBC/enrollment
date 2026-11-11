import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { canPreviewFinalEnrolmentStep } from './localEnrolmentDesignPreview';
import { shouldRedirectLearnerWizardStep } from '@/pages/learner/onboarding/navigation';

beforeEach(() => {
  vi.stubEnv('DEV', true);
  vi.stubEnv('VITE_LOCAL_ENROLMENT_DESIGN_PREVIEW', '1');
  vi.stubGlobal('window', { location: { hostname: 'localhost' } });
});
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

it('opens only the final three steps and their URLs without marking preceding steps complete', () => {
  for (const stepSlug of ['policies', 'initial-assessments', 'next-steps']) {
    expect(canPreviewFinalEnrolmentStep(stepSlug)).toBe(true);
    expect(shouldRedirectLearnerWizardStep({ ready: true, currentIndex: 11, reachable: 8, stepSlug })).toBe(false);
  }
  for (const stepSlug of ['inclusion', 'ilr', 'skills-radar', 'programme-suitability', undefined]) {
    expect(canPreviewFinalEnrolmentStep(stepSlug)).toBe(false);
  }
  expect(shouldRedirectLearnerWizardStep({ ready: true, currentIndex: 8, reachable: 3, stepSlug: 'inclusion' })).toBe(true);
});

it('restores the normal lock when disabled and cannot unlock production or remote pages', () => {
  vi.stubEnv('VITE_LOCAL_ENROLMENT_DESIGN_PREVIEW', '0');
  expect(shouldRedirectLearnerWizardStep({ ready: true, currentIndex: 11, reachable: 8, stepSlug: 'next-steps' })).toBe(true);
  vi.stubEnv('VITE_LOCAL_ENROLMENT_DESIGN_PREVIEW', '1');
  vi.stubEnv('DEV', false);
  expect(canPreviewFinalEnrolmentStep('policies')).toBe(false);
  vi.stubEnv('DEV', true);
  vi.stubGlobal('window', { location: { hostname: 'example.com' } });
  expect(canPreviewFinalEnrolmentStep('policies')).toBe(false);
});
