/**
 * The Policies step: the Kent Business College policies, each opened from
 * storage and acknowledged by the learner — and no IBIS documents.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AppIcon } from '@/components/feature/AppIcon';
import { POLICY_DOCS_KBC } from '@/mocks/enrolment-console';
import { WIZARD_STEPS, type WizardDraft } from '../../types';
import { missingForStep } from '../validation';

const wizard = vi.hoisted(() => ({ acknowledged: {} as Record<string, boolean>, setSection: vi.fn() }));
vi.mock("../WizardContext.helpers", () => ({
  useWizard: () => ({ draft: { policies: { acknowledged: wizard.acknowledged } }, setSection: wizard.setSection }),
}));

import Policies from '../steps/Policies';

const POLICIES_STEP = WIZARD_STEPS.findIndex((s) => s.slug === 'policies');
const missing = (acknowledged: Record<string, boolean>) =>
  missingForStep(POLICIES_STEP, { policies: { acknowledged } } as WizardDraft);

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('AppIcon', AppIcon);
  wizard.acknowledged = {};
});

describe('Policies step', () => {
  it('lists every KBC policy, opening each from storage in a new tab', () => {
    render(<Policies />);

    expect(POLICY_DOCS_KBC).toHaveLength(15);
    const link = screen.getByRole('link', { name: 'A1_-_Library_Policy.pdf' });
    expect(link).toHaveAttribute('href', '/enrolment_api/policy-documents/kbc-policy-library/');
    expect(link).toHaveAttribute('target', '_blank');
    expect(screen.getAllByRole('checkbox')).toHaveLength(15);
    expect(screen.getByText('0 of 15 acknowledged')).toBeInTheDocument();
  });

  it('no longer shows any IBIS document', () => {
    render(<Policies />);

    expect(screen.queryByText('IBIS')).not.toBeInTheDocument();
    expect(screen.queryByText(/IBIS\.pdf/)).not.toBeInTheDocument();
  });

  it('records a tick against the document’s own id', async () => {
    render(<Policies />);

    const row = screen.getByRole('link', { name: 'A1_-_Library_Policy.pdf' }).parentElement!;
    await userEvent.click(within(row).getByRole('checkbox'));

    expect(wizard.setSection).toHaveBeenCalledWith('policies', { acknowledged: { 'kbc-policy-library': true } });
  });

  it('is complete only once all fifteen are acknowledged', () => {
    const all = Object.fromEntries(POLICY_DOCS_KBC.map((d) => [d.id, true]));
    expect(missing(all)).toEqual([]);
    expect(missing({ ...all, 'kbc-policy-library': false })).toEqual(['1 document not acknowledged']);
  });

  it('does not count a tick given to a document from the previous set', () => {
    const oldSet = Object.fromEntries(Array.from({ length: 11 }, (_, i) => [`kbc-${i}`, true]));
    expect(missing(oldSet)).toEqual(['15 documents not acknowledged']);
  });
});
