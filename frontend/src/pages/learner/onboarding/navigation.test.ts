import { describe, expect, it } from 'vitest';
import { shouldRedirectLearnerWizardStep } from './navigation';

describe('learner onboarding navigation guard', () => {
  it.each(['policies', 'initial-assessments', 'next-steps'])('keeps %s locked until preceding steps are complete', (stepSlug) => {
    expect(shouldRedirectLearnerWizardStep({
      ready: true, currentIndex: 11, reachable: 8, stepSlug,
    })).toBe(true);
    expect(shouldRedirectLearnerWizardStep({
      ready: true, currentIndex: 11, reachable: 11, stepSlug,
    })).toBe(false);
  });

  it('blocks direct navigation to any locked later step', () => {
    expect(shouldRedirectLearnerWizardStep({
      ready: true, currentIndex: 10, reachable: 3,
    })).toBe(true);
  });

  it('does not redirect before the existing draft is ready', () => {
    expect(shouldRedirectLearnerWizardStep({
      ready: false, currentIndex: 10, reachable: 3,
    })).toBe(false);
  });

  it('allows the current step and earlier steps', () => {
    expect(shouldRedirectLearnerWizardStep({ ready: true, currentIndex: 3, reachable: 3 })).toBe(false);
    expect(shouldRedirectLearnerWizardStep({ ready: true, currentIndex: 2, reachable: 3 })).toBe(false);
  });
});
