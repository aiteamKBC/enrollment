/**
 * A self-rating above 4 is flagged: the learner is warned it may affect their
 * eligibility and must explain it in a note, and the enrolment team sees the
 * same flag on the read-only list.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const ksbs = [
  { id: 'k1', codes: ['K1'], title: 'Marketing Concepts', theme: 'Knowledge', kind: 'Knowledge' },
  { id: 'k2', codes: ['K2'], title: 'Commercial Awareness', theme: 'Knowledge', kind: 'Knowledge' },
];
vi.mock('@/api/curriculum', () => ({
  peekKsbProfile: () => ({ results: ksbs }),
  fetchKsbProfile: vi.fn(async () => ({ results: ksbs })),
}));

const wizard = vi.hoisted(() => ({
  readOnly: false,
  assessments: {} as Record<string, unknown>,
  setSection: vi.fn(),
}));
vi.mock("../WizardContext.helpers", () => ({
  useWizard: () => ({
    draft: { skillsRadar: { assessments: wizard.assessments } },
    setSection: wizard.setSection,
    readOnly: wizard.readOnly,
    board: { programme: { name: 'Marketing Executive Level 4' } },
  }),
}));

import SkillsRadar from '../steps/SkillsRadar';

const answer = (ksbId: string, level: string, note = '') => ({ ksbId, level, note, evidenceFiles: [], actionPlan: null });

beforeEach(() => {
  vi.clearAllMocks();
  wizard.readOnly = false;
  wizard.assessments = {};
});

describe('Skills Radar high self-ratings', () => {
  it('warns about eligibility and requires a note for a rating above 4', async () => {
    const user = userEvent.setup();
    render(<SkillsRadar />);
    await user.click(screen.getByRole('button', { name: /start assessment/i }));

    await user.click(screen.getByRole('radio', { name: /Consistently/ }));

    expect(screen.getByRole('status')).toHaveTextContent('may affect your eligibility for this apprenticeship');
    const confirm = screen.getByRole('button', { name: /confirm & next/i });
    expect(confirm).toBeDisabled();
    expect(screen.getByLabelText(/Add a note/)).toBeRequired();

    await user.type(screen.getByLabelText(/Add a note/), 'I have run campaigns for three years.');
    expect(confirm).toBeEnabled();
    await user.click(confirm);

    expect(wizard.setSection).toHaveBeenCalledWith('skillsRadar', expect.objectContaining({
      assessments: expect.objectContaining({ k1: expect.objectContaining({ level: 'consistently', note: 'I have run campaigns for three years.' }) }),
    }));
  });

  it('does not flag or require a note for a rating of 4 or below', async () => {
    const user = userEvent.setup();
    render(<SkillsRadar />);
    await user.click(screen.getByRole('button', { name: /start assessment/i }));

    await user.click(screen.getByRole('radio', { name: /Frequently/ }));

    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.getByLabelText(/Add a note/)).not.toBeRequired();
    expect(screen.getByRole('button', { name: /confirm & next/i })).toBeEnabled();
  });

  it('a note of only spaces does not satisfy the requirement', async () => {
    const user = userEvent.setup();
    render(<SkillsRadar />);
    await user.click(screen.getByRole('button', { name: /start assessment/i }));
    await user.click(screen.getByRole('radio', { name: /Mastery/ }));

    await user.type(screen.getByLabelText(/Add a note/), '   ');

    expect(screen.getByRole('button', { name: /confirm & next/i })).toBeDisabled();
  });

  it('shows the enrolment team which answers are flagged on the read-only list', async () => {
    wizard.readOnly = true;
    wizard.assessments = { k1: answer('k1', 'expert', 'Team lead'), k2: answer('k2', 'rarely') };
    const user = userEvent.setup();
    render(<SkillsRadar />);

    expect(screen.getByText('1 rated above 4')).toBeInTheDocument();
    expect(screen.getAllByRole('img', { name: 'Flagged: rated above 4' })).toHaveLength(1);

    const firstRow = screen.getByText(/Marketing Concepts/).closest('div') as HTMLElement;
    await user.click(within(firstRow).getByRole('button', { name: /view/i }));
    expect(screen.getByRole('status')).toHaveTextContent('Flagged: the learner rated this above 4.');
  });
});
