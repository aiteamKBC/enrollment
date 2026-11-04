import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { canPreviewIlrStep } from './localIlrPreview';
import { shouldRedirectLearnerWizardStep } from '@/pages/learner/onboarding/navigation';

beforeEach(() => {
  vi.stubEnv('DEV', true);
  vi.stubEnv('MODE', 'eligibility-local');
  vi.stubEnv('VITE_LOCAL_ILR_NAVIGATION_PREVIEW', '1');
  vi.stubGlobal('window', { location: { hostname: '127.0.0.1' } });
});
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

it('unlocks only the two ILR pages and their direct URLs locally', () => {
  expect(canPreviewIlrStep('ilr-details')).toBe(true);
  expect(canPreviewIlrStep('ilr')).toBe(true);
  expect(canPreviewIlrStep('policies')).toBe(false);
  expect(shouldRedirectLearnerWizardStep({ready:true,currentIndex:3,reachable:2,stepSlug:'ilr'})).toBe(false);
  expect(shouldRedirectLearnerWizardStep({ready:true,currentIndex:7,reachable:2,stepSlug:'policies'})).toBe(true);
});

it('keeps normal restrictions when the temporary flag is off', () => {
  vi.stubEnv('VITE_LOCAL_ILR_NAVIGATION_PREVIEW', '0');
  expect(canPreviewIlrStep('ilr')).toBe(false);
});

it('cannot enable this preview in production, another mode, or on a remote host', () => {
  vi.stubEnv('DEV', false);
  expect(canPreviewIlrStep('ilr')).toBe(false);
  vi.stubEnv('DEV', true);
  vi.stubEnv('MODE', 'development');
  expect(canPreviewIlrStep('ilr')).toBe(false);
  vi.stubEnv('MODE', 'eligibility-local');
  vi.stubGlobal('window', { location: { hostname: 'example.com' } });
  expect(canPreviewIlrStep('ilr')).toBe(false);
});
