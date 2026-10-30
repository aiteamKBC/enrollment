/**
 * Extended ILR — Emergency contact / Next of kin: the contact's own postcode and
 * address are asked only when their address is not the learner's.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AppIcon } from '@/components/feature/AppIcon';
import type { IlrForm, WizardDraft } from '../../types';
import { WIZARD_STEPS } from '../../types';
import { missingForStep } from '../validation';

const COMPLETE: IlrForm = {
  contact: { byPost: false, byPhone: true, byEmail: true },
  nextOfKin: { fullName: 'Next Of Kin', relationship: 'Parent', email: 'kin@example.com', phone: '07123456780', sameAddressAsLearner: true },
  eligibility: {
    employedInEngland: true, countryOfResidence: 'United Kingdom', ukEeaNational: true, nationality: 'British',
    residentPrev3Years: true, yearsInUk: undefined, requiresWorkPermit: null, evidenceDescription: '', evidenceFiles: [],
  },
  employer: {
    organisationName: 'Employer Ltd', postcode: 'CT1 1AA', address: '1 Office Park', city: 'Canterbury',
    lineManagerName: 'Line Manager', lineManagerEmail: 'manager@example.com', lineManagerPhone: '07123456781',
  },
  otherTraining: { attended12m: false, completedWhen: '' },
  circumstances: { caringResponsibilities: 'None', other: '', careLeaver: false },
  understanding: { programmeUnderstanding: 'Marketing', careerProgression: 'Manager' },
  additionalInformation: { jobRoleRelevance: 'Marketing assistant', residenceNotForFullTimeEducation: 'Yes', ehcp: 'No', otherNames: '' },
  additional: { aged16to18: false, aged19to24: false },
  media: { consent: true },
  declarations: {
    plrShared: true, dfeContact: true, epaoDetails: true, kbcHoldsCerts: true, infoAccurate: true,
    over50PercentEngland: true, wageRateBand: 'Band 1', knownByOtherName: false, plrAccessAware: true,
  },
  learnerSignature: { firstNames: 'Test', surname: 'Learner', date: '2026-09-27', signatureUrl: 'data:image/png;base64,AAAA' },
  providerSignature: { printName: '', date: '' },
} as IlrForm;

const wizard = vi.hoisted(() => ({ ilr: {} as IlrForm, setSection: vi.fn() }));
vi.mock("../WizardContext.helpers", () => ({
  useWizard: () => ({
    board: {},
    draft: { ilr: wizard.ilr, personalDetails: {} },
    setSection: wizard.setSection,
    saveIlr: vi.fn(),
    ilrSaving: false,
    ilrSavedAt: null,
    fileIlrDocument: vi.fn(),
    ilrFiling: false,
  }),
}));
vi.mock("@/hooks/useToast.helpers", () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }));

import Ilr from '../steps/Ilr';
import { StepErrorsContext } from "../stepErrors.helpers";

const ILR_STEP = WIZARD_STEPS.findIndex((s) => s.slug === 'ilr');
const missing = (nextOfKin: Partial<IlrForm['nextOfKin']>) =>
  missingForStep(ILR_STEP, { ilr: { ...COMPLETE, nextOfKin: { ...COMPLETE.nextOfKin, ...nextOfKin } } } as WizardDraft);
const kinGroup = () => screen.getByRole('group', { name: 'Emergency contact details / Next of kin' });

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('AppIcon', AppIcon);
  wizard.ilr = COMPLETE;
});

describe('Extended ILR next of kin address', () => {
  it('does not ask for an address when it is the same as the learner’s', () => {
    render(<Ilr />);

    expect(kinGroup()).not.toHaveTextContent('Postcode');
    expect(missing({})).toEqual([]);
  });

  it('asks for the contact’s postcode and address when it is not the learner’s', async () => {
    wizard.ilr = { ...COMPLETE, nextOfKin: { ...COMPLETE.nextOfKin, sameAddressAsLearner: false, postcode: '', address: '' } };
    render(<Ilr />);

    expect(screen.getByPlaceholderText('Post Code')).toBeInTheDocument();
    expect(kinGroup()).toHaveTextContent('Address');

    await userEvent.type(screen.getByPlaceholderText('Post Code'), 'C');
    expect(wizard.setSection).toHaveBeenLastCalledWith('ilr', expect.objectContaining({
      nextOfKin: expect.objectContaining({ postcode: 'C', fullName: 'Next Of Kin' }),
    }));
  });

  it('requires both only when the address is not the learner’s', () => {
    expect(missing({ sameAddressAsLearner: false })).toEqual(['Next of kin — postcode', 'Next of kin — address']);
    expect(missing({ sameAddressAsLearner: false, postcode: 'CT1 1AA', address: '1 High Street' })).toEqual([]);
  });

  it('marks the outstanding boxes red once Next has been pressed', () => {
    wizard.ilr = {
      ...COMPLETE,
      nextOfKin: { ...COMPLETE.nextOfKin, sameAddressAsLearner: false, postcode: '', address: '' },
      employer: { ...COMPLETE.employer, lineManagerName: '' },
      // Media consent is no longer asked; PLR access is a Yes/No row still on the step.
      declarations: { ...COMPLETE.declarations, plrAccessAware: null },
    };
    render(
      <StepErrorsContext.Provider value={{ show: true, missing: missingForStep(ILR_STEP, { ilr: wizard.ilr } as WizardDraft) }}>
        <Ilr />
      </StepErrorsContext.Provider>
    );

    expect(screen.getByText('Please enter line manager name.')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Post Code')).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('Please enter postcode.')).toBeInTheDocument();
    expect(within(screen.getByRole('group', { name: 'Please confirm that you are aware your training provider will need to access your PLR:' })).getByText('Please choose Yes or No.')).toBeInTheDocument();
    // Answered boxes are left alone.
    expect(screen.getByDisplayValue('Next Of Kin')).not.toHaveAttribute('aria-invalid');
  });

  it('clears the contact’s own address when the learner switches to Yes', async () => {
    wizard.ilr = { ...COMPLETE, nextOfKin: { ...COMPLETE.nextOfKin, sameAddressAsLearner: false, postcode: 'CT1 1AA', address: '1 High Street' } };
    render(<Ilr />);

    await userEvent.click(within(screen.getByRole('group', { name: 'Address same as learner?' })).getByRole('radio', { name: 'Yes' }));
    expect(wizard.setSection).toHaveBeenLastCalledWith('ilr', expect.objectContaining({
      nextOfKin: expect.objectContaining({ sameAddressAsLearner: true, postcode: '', address: '' }),
    }));
  });
});
