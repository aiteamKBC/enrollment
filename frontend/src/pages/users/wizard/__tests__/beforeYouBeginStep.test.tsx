/**
 * Before You Begin sits directly after the Introduction, has nothing to answer,
 * and inserting it did not shift which completeness rules apply to which step.
 */
import { describe, it, expect, beforeAll } from 'vitest';
import { render, screen } from '@testing-library/react';
import { WIZARD_STEPS, type WizardDraft } from '../../types';
import { STEP_BODIES } from '../WizardShell';
import { missingForStep } from '../validation';
import BeforeYouBegin from '../steps/BeforeYouBegin';

const index = (slug: string) => WIZARD_STEPS.findIndex((step) => step.slug === slug);

beforeAll(() => {
  (globalThis as Record<string, unknown>).AppIcon = ({ className }: { className?: string }) => <i className={className} />;
});

describe('Before You Begin step', () => {
  it('comes straight after the Introduction, with a body for every step', () => {
    expect(WIZARD_STEPS.slice(0, 3).map((step) => step.slug)).toEqual([
      'introduction', 'before-you-begin', 'personal-details',
    ]);
    expect(STEP_BODIES).toHaveLength(WIZARD_STEPS.length);
    expect(STEP_BODIES[index('before-you-begin')]).toBe(BeforeYouBegin);
  });

  it('has nothing to answer, while Personal Details keeps its own rules', () => {
    const draft = {
      personalDetails: { firstName: '', lastName: '', email: '', phone: '', address: '', dob: '', sex: '' },
    } as unknown as WizardDraft;

    expect(missingForStep(index('before-you-begin'), draft)).toEqual([]);
    expect(missingForStep(index('personal-details'), draft)).toContain('Address');
  });

  it('shows the guidance and the contact address', () => {
    render(<BeforeYouBegin />);

    expect(screen.getByRole('heading', { name: 'Before You Begin' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Supporting Evidence' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'office@kentbusinesscollege.org' }))
      .toHaveAttribute('href', 'mailto:office@kentbusinesscollege.org');
  });
});
