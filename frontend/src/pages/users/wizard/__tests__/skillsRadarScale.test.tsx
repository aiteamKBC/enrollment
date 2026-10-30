/**
 * The Skills Radar's 9-point scale: Champion (9) sits above Mastery (8), and
 * every other option keeps its wording and score, so ratings saved before
 * Champion existed still mean what they did.
 */
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { COMPETENCE_LEVELS, MAX_COMPETENCE_SCORE, competenceMeta, competenceScore } from '@/mocks/enrolment-console';

const ksbs = [{ id: 'k1', codes: ['K1'], title: 'Marketing Concepts', theme: 'Knowledge', kind: 'Knowledge' }];
vi.mock('@/api/curriculum', () => ({
  peekKsbProfile: () => ({ results: ksbs }),
  fetchKsbProfile: vi.fn(async () => ({ results: ksbs })),
}));
vi.mock("../WizardContext.helpers", () => ({
  useWizard: () => ({
    draft: { skillsRadar: { assessments: {} } },
    setSection: vi.fn(),
    readOnly: false,
    board: { programme: { name: 'Marketing Executive Level 4' } },
  }),
}));

import SkillsRadar from '../steps/SkillsRadar';

describe('the Skills Radar scale', () => {
  it('runs from Champion (9) down to Never (1), the original eight unchanged', () => {
    expect(COMPETENCE_LEVELS.map((l) => [l.label, l.score])).toEqual([
      ['Champion', 9], ['Mastery', 8], ['Expert', 7], ['Proficient', 6], ['Consistently', 5],
      ['Frequently', 4], ['Occasionally', 3], ['Rarely', 2], ['Never', 1],
    ]);
    expect(MAX_COMPETENCE_SCORE).toBe(9);
    expect(competenceScore('champion')).toBe(9);
    // Legacy 5-point values still land where they did.
    expect(competenceScore('always')).toBe(8);
    expect(competenceMeta('always')?.label).toBe('Mastery');
  });

  it('offers Champion first, scored 9, and flags it like any rating above 4', async () => {
    const user = userEvent.setup();
    render(<SkillsRadar />);
    await user.click(screen.getByRole('button', { name: /start assessment/i }));

    const options = screen.getAllByRole('radio');
    expect(options).toHaveLength(9);
    const champion = screen.getByRole('radio', { name: /Champion/ });
    expect(options[0]).toBe(champion);
    expect(champion.closest('label')).toHaveTextContent('I set the standard in this area');
    expect(champion.closest('label')).toHaveTextContent(/9$/);

    await user.click(champion);
    expect(screen.getByRole('status')).toHaveTextContent('may affect your eligibility for this apprenticeship');
    expect(screen.getByLabelText(/Add a note/)).toBeRequired();
  });
});
