/** Learners can save every rating without new evidence; staff retain historical review context. */
import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AppIcon } from '@/components/feature/AppIcon';

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
  vi.stubGlobal('AppIcon', AppIcon);
  vi.clearAllMocks();
  wizard.readOnly = false;
  wizard.assessments = {};
});
afterAll(() => vi.unstubAllGlobals());

describe('Skills Radar self-ratings', () => {
  it.each([
    ['Never', 'never', 1],
    ['Frequently', 'frequently', 4],
    ['Consistently', 'consistently', 5],
    ['Champion', 'champion', 9],
  ])('saves %s with an information notice only above four', async (label, level, score) => {
    const user = userEvent.setup();
    render(<SkillsRadar />);
    await user.click(screen.getByRole('button', { name: /start assessment/i }));
    const confirm = screen.getByRole('button', { name: /confirm & next/i });
    expect(confirm).toBeDisabled();
    await user.click(screen.getByRole('radio', { name: new RegExp(`^${label}`) }));
    expect(confirm).toBeEnabled();
    const notice = within(screen.getByRole('dialog')).queryByRole('status');
    if (score > 4) {
      expect(notice).toHaveTextContent('Potential Prior Learning');
      expect(notice).toHaveTextContent('Your rating suggests you may already have relevant knowledge or experience that could be considered during your initial assessment.');
      expect(notice).toHaveTextContent('If you already meet many of the apprenticeship requirements, this may affect how much new training you need and, in some cases, the suitability or funding eligibility of the apprenticeship.');
    } else expect(notice).toBeNull();
    expect(screen.queryByLabelText(/Add a note/)).toBeNull();
    expect(screen.queryByText(/Upload evidence|Select file|may affect your eligibility|A note is required/i)).toBeNull();
    expect(screen.getByRole('dialog').querySelector('input[type="file"]')).toBeNull();
    await user.click(confirm);
    expect(wizard.setSection).toHaveBeenCalledWith('skillsRadar', expect.objectContaining({
      assessments: expect.objectContaining({ k1: expect.objectContaining({ level, note: '', evidenceFiles: [] }) }),
    }));
    expect(screen.getByText('Question 2 of 2')).toBeVisible();
    expect(within(screen.getByRole('dialog')).queryByText('Potential Prior Learning')).toBeNull();
  });

  it('updates the information notice when the learner changes a rating before saving', async () => {
    const user = userEvent.setup();
    render(<SkillsRadar />);
    await user.click(screen.getByRole('button', { name: /start assessment/i }));
    await user.click(screen.getByRole('radio', { name: /Consistently/ }));
    expect(within(screen.getByRole('dialog')).getByText('Potential Prior Learning')).toBeVisible();
    await user.click(screen.getByRole('radio', { name: /Frequently/ }));
    expect(within(screen.getByRole('dialog')).queryByText('Potential Prior Learning')).toBeNull();
    await user.click(screen.getByRole('radio', { name: /Champion/ }));
    expect(within(screen.getByRole('dialog')).getByText('Potential Prior Learning')).toBeVisible();
    expect(screen.getByRole('button', { name: /confirm & next/i })).toBeEnabled();
    expect(wizard.setSection).not.toHaveBeenCalled();
  });

  it('restores a newly saved rating after remounting without requesting evidence', async () => {
    const user = userEvent.setup();
    const view = render(<SkillsRadar />);
    await user.click(screen.getByRole('button', { name: /start assessment/i }));
    await user.click(screen.getByRole('radio', { name: /Champion/ }));
    await user.click(screen.getByRole('button', { name: /confirm & next/i }));
    wizard.assessments = wizard.setSection.mock.calls[0][1].assessments;
    view.unmount();
    render(<SkillsRadar />);
    await user.click(screen.getAllByRole('button', { name: /edit/i })[0]);
    expect(screen.getByRole('radio', { name: /Champion/ })).toBeChecked();
    expect(screen.queryByLabelText(/Add a note/)).toBeNull();
    expect(wizard.setSection).toHaveBeenCalledTimes(1);
  });

  it('preserves historical notes, files and action plans when a learner edits a rating', async () => {
    wizard.assessments = { k1: { ...answer('k1', 'expert', 'Historical note'), evidenceFiles: ['historical.pdf'], actionPlan: { text: 'Retain plan' } } };
    const user = userEvent.setup();
    render(<SkillsRadar />);
    await user.click(screen.getAllByRole('button', { name: /edit/i })[0]);
    expect(screen.queryByLabelText(/Add a note/)).toBeNull();
    expect(screen.queryByText('historical.pdf')).toBeNull();
    await user.click(screen.getByRole('radio', { name: /Consistently/ }));
    await user.click(screen.getByRole('button', { name: /confirm & next/i }));
    expect(wizard.setSection).toHaveBeenCalledWith('skillsRadar', expect.objectContaining({
      assessments: expect.objectContaining({ k1: expect.objectContaining({
        level: 'consistently', note: 'Historical note', evidenceFiles: ['historical.pdf'], actionPlan: { text: 'Retain plan' },
      }) }),
    }));
  });

  it('keeps staff review read-only with historical notes and evidence visible', async () => {
    wizard.readOnly = true;
    wizard.assessments = { k1: { ...answer('k1', 'expert', 'Team lead'), evidenceFiles: ['proof.pdf'] }, k2: answer('k2', 'rarely') };
    const user = userEvent.setup();
    render(<SkillsRadar />);
    expect(screen.getAllByRole('img', { name: 'Flagged: rated above 4' })).toHaveLength(1);
    const firstRow = screen.getByText(/Marketing Concepts/).closest('[role="row"]') as HTMLElement;
    await user.click(within(firstRow).getByRole('button', { name: /view/i }));
    const dialog = within(screen.getByRole('dialog'));
    dialog.getAllByRole('radio').forEach((radio) => expect(radio).toBeDisabled());
    expect(dialog.getByLabelText(/Add a note/)).toHaveValue('Team lead');
    expect(dialog.getByLabelText(/Add a note/)).toHaveAttribute('readonly');
    expect(dialog.getByText('proof.pdf')).toBeVisible();
    expect(dialog.getByText(/This self-assessment does not determine eligibility or RPL/)).toBeVisible();
    expect(wizard.setSection).not.toHaveBeenCalled();
  });
});
