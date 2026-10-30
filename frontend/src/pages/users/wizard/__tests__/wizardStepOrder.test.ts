/** The onboarding wizard's steps, in the order learners and staff walk them. */
import { describe, expect, it } from 'vitest';
import { WIZARD_STEPS } from '../../types';
import { STEP_BODIES } from '../WizardShell';
import Introduction from '../steps/Introduction';
import BeforeYouBegin from '../steps/BeforeYouBegin';
import PersonalDetails from '../steps/PersonalDetails';
import IlrLearnerDetails from '../steps/IlrLearnerDetails';
import Ilr from '../steps/Ilr';
import CvJob from '../steps/CvJob';
import Plr from '../steps/Plr';
import SkillsRadar from '../steps/SkillsRadar';
import Inclusion from '../steps/Inclusion';
import Policies from '../steps/Policies';
import NextSteps from '../steps/NextSteps';

describe('wizard step order', () => {
  it('follows the onboarding flow, with slugs (page addresses) unchanged', () => {
    expect(WIZARD_STEPS.map((s) => [s.slug, s.label])).toEqual([
      ['introduction', 'Welcome'],
      ['before-you-begin', 'Before You Begin'],
      ['personal-details', 'Personal Details'],
      ['ilr-details', 'ILR'],
      ['ilr', 'Extended ILR'],
      ['cv-job', 'CV/Job Description'],
      ['plr', 'Personal Learning Record'],
      ['skills-radar', 'Skills Radar'],
      // Added 2026-10: the inclusion assessment, before the policies are read.
      ['inclusion', 'Inclusion'],
      ['policies', 'Policies'],
      ['next-steps', 'What Happens Now?'],
    ]);
  });

  // Regression: the bodies were a positional list, so reordering the steps left
  // CV/Job Description showing the Skills Radar.
  it('shows each step its own page, whatever the order', () => {
    const bodyFor = (slug: string) => STEP_BODIES[WIZARD_STEPS.findIndex((s) => s.slug === slug)];

    expect(STEP_BODIES).toHaveLength(WIZARD_STEPS.length);
    expect(bodyFor('introduction')).toBe(Introduction);
    expect(bodyFor('before-you-begin')).toBe(BeforeYouBegin);
    expect(bodyFor('personal-details')).toBe(PersonalDetails);
    expect(bodyFor('ilr-details')).toBe(IlrLearnerDetails);
    expect(bodyFor('ilr')).toBe(Ilr);
    expect(bodyFor('cv-job')).toBe(CvJob);
    expect(bodyFor('plr')).toBe(Plr);
    expect(bodyFor('skills-radar')).toBe(SkillsRadar);
    expect(bodyFor('inclusion')).toBe(Inclusion);
    expect(bodyFor('policies')).toBe(Policies);
    expect(bodyFor('next-steps')).toBe(NextSteps);
  });
});
