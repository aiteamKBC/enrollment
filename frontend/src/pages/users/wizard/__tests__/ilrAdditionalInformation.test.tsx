/**
 * Extended ILR — the "Additional Information" section replaced the age
 * questions, media consent, the learning declaration and the provider
 * declaration (with both signature blocks).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AppIcon } from '@/components/feature/AppIcon';
import type { IlrForm, WizardDraft } from '../../types';
import { WIZARD_STEPS } from '../../types';
import { missingForStep } from '../validation';

const COMPLETE = {
  contact: { byPost: false, byPhone: true, byEmail: true },
  nextOfKin: { fullName: 'Next Of Kin', relationship: 'Parent', email: 'kin@example.com', phone: '07123456780', sameAddressAsLearner: true },
  eligibility: {
    employedInEngland: true, countryOfResidence: 'United Kingdom', ukEeaNational: true, nationality: 'British',
    residentPrev3Years: true, yearsInUk: undefined, requiresWorkPermit: false, evidenceDescription: '', evidenceFiles: [],
  },
  employer: {
    organisationName: 'Employer Ltd', postcode: 'CT1 1AA', address: '1 Office Park', city: 'Canterbury',
    lineManagerName: 'Line Manager', lineManagerEmail: 'manager@example.com', lineManagerPhone: '07123456781',
  },
  otherTraining: { attended12m: false, completedWhen: '' },
  circumstances: { caringResponsibilities: 'None', other: '', careLeaver: false },
  understanding: { programmeUnderstanding: 'Marketing', careerProgression: 'Manager' },
  additionalInformation: { jobRoleRelevance: 'Marketing assistant', residenceNotForFullTimeEducation: 'Yes', ehcp: 'No', otherNames: '' },
  // Legacy sections, no longer asked: left unanswered on purpose.
  additional: { aged16to18: null, aged19to24: null },
  media: { consent: null },
  declarations: {
    plrShared: null, dfeContact: null, epaoDetails: null, kbcHoldsCerts: null, infoAccurate: null,
    over50PercentEngland: true, wageRateBand: 'Apprentice Rate £8.00', knownByOtherName: false, plrAccessAware: true,
  },
  learnerSignature: { firstNames: 'Test', surname: 'Learner', date: '' },
  providerSignature: { printName: '', date: '' },
} as IlrForm;

const wizard = vi.hoisted(() => ({ ilr: {} as IlrForm, setSection: vi.fn() }));
vi.mock("../WizardContext.helpers", () => ({
  useWizard: () => ({
    userId: '41',
    isCommercial: false,
    hydrated: true,
    board: {},
    draft: { ilr: wizard.ilr, personalDetails: { signature: 'data:image/png;base64,AAAA' } },
    setSection: wizard.setSection,
    saveIlr: vi.fn(),
    ilrSaving: false,
    ilrSavedAt: null,
    fileIlrDocument: vi.fn(),
    ilrFiling: false,
  }),
}));
vi.mock("@/hooks/useToast.helpers", () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }));
vi.mock('@/api/extendedIlr', () => ({
  fetchIlrEvidence: vi.fn().mockResolvedValue({ results: [], locked: false }),
  uploadIlrEvidence: vi.fn(),
  deleteIlrEvidence: vi.fn(),
  getIlrEvidenceUrl: vi.fn(),
  fetchIlrEmployerDetails: vi.fn().mockResolvedValue({}),
}));

import Ilr from '../steps/Ilr';

const ILR_STEP = WIZARD_STEPS.findIndex((s) => s.slug === 'ilr');
const missing = (ilr: IlrForm) => missingForStep(ILR_STEP, { ilr } as WizardDraft);
const section = () => screen.getByRole('group', { name: 'Additional Information' });

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('AppIcon', AppIcon);
  wizard.ilr = COMPLETE;
});

describe('Extended ILR additional information', () => {
  it('replaces the old sections, including both signature blocks', () => {
    render(<Ilr />);

    for (const legend of ['Media Consent', 'Learning declaration', 'Provider / Sub-contractor Declaration']) {
      expect(screen.queryByRole('group', { name: legend })).not.toBeInTheDocument();
    }
    expect(screen.queryByText('Are you aged between 16 and 18?')).not.toBeInTheDocument();
    expect(screen.queryByText('Learner signature')).not.toBeInTheDocument();
    expect(within(section()).getByText('EHCP Status')).toBeInTheDocument();
    expect(within(section()).getByRole('link', { name: 'https://www.gov.uk/national-minimum-wage-rates' })).toBeInTheDocument();
  });

  it('no longer copies the Personal details signature into the ILR', () => {
    render(<Ilr />);

    expect(wizard.setSection).not.toHaveBeenCalledWith('ilr', expect.objectContaining({
      learnerSignature: expect.objectContaining({ signatureUrl: expect.anything() }),
    }));
  });

  it('is complete without the removed questions or any signature', () => {
    expect(missing(COMPLETE)).toEqual([]);
  });

  it('requires the three new answers', () => {
    const blank = { ...COMPLETE, additionalInformation: { jobRoleRelevance: ' ', residenceNotForFullTimeEducation: '', ehcp: '', otherNames: '' } };

    expect(missing(blank)).toEqual(['Job role and the programme', 'Residence not for full-time education', 'EHCP']);
  });

  it('treats answers saved before the section existed as unanswered rather than crashing', () => {
    const { additionalInformation: _unused, ...legacy } = COMPLETE;

    expect(missing(legacy as IlrForm)).toEqual(['Job role and the programme', 'Residence not for full-time education', 'EHCP']);
  });

  it('offers Yes/No for residence and EHCP, and the minimum wage rates', async () => {
    render(<Ilr />);

    // LabeledSelect does not tie its label to the <select>, so they are found by
    // position: residence, EHCP, wage rate.
    const [residence, ehcp, wage] = within(section()).getAllByRole('combobox');
    for (const select of [residence, ehcp]) {
      expect(within(select).getAllByRole('option').map((o) => o.textContent)).toEqual(['Select…', 'Yes', 'No']);
    }
    expect(within(wage).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Select…', 'Apprentice Rate £8.00', '18–20 Year Old Rate £10.85', 'National Living Wage (21 and over) £12.71', 'Above £12.71',
    ]);
    await userEvent.selectOptions(ehcp, 'Yes');
    expect(wizard.setSection).toHaveBeenLastCalledWith('ilr', expect.objectContaining({
      additionalInformation: expect.objectContaining({ ehcp: 'Yes', jobRoleRelevance: 'Marketing assistant' }),
    }));
  });

  it('asks for other names only when known by another name, and clears them on No', async () => {
    render(<Ilr />);
    expect(screen.queryByText('Please list any other names you have been known by:')).not.toBeInTheDocument();

    wizard.ilr = {
      ...COMPLETE,
      declarations: { ...COMPLETE.declarations, knownByOtherName: true },
      additionalInformation: { ...COMPLETE.additionalInformation, otherNames: 'Old Name' },
    };
    const { unmount } = render(<Ilr />);
    expect(screen.getByText('Please list any other names you have been known by:')).toBeInTheDocument();

    const group = screen.getAllByRole('group', { name: 'Have you ever been known by any other name?' }).at(-1)!;
    await userEvent.click(within(group).getByRole('radio', { name: 'No' }));
    expect(wizard.setSection).toHaveBeenLastCalledWith('ilr', expect.objectContaining({
      declarations: expect.objectContaining({ knownByOtherName: false }),
      additionalInformation: expect.objectContaining({ otherNames: '' }),
    }));
    unmount();
  });
});
