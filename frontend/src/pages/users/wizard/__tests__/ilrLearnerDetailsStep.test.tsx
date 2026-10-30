/**
 * ILR Learner Details: record values shown read-only, the ILR questions the
 * record cannot answer, and what the step requires before the learner moves on.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AppIcon } from '@/components/feature/AppIcon';
import type { IlrLearnerDetails as Details, WizardDraft } from '../../types';
import { WIZARD_STEPS } from '../../types';
import { missingForStep } from '../validation';

const RECORD = {
  familyName: 'Learner', givenNames: 'Test', dateOfBirth: '1991-06-12', currentPostcode: 'BR8 7EU',
  addressLine1: '1 Waterton', addressLine2: '', addressLine3: 'Swanley', addressLine4: 'Kent',
  telephone: '01002331985', email: 'learner@example.test', nationalInsuranceNumber: '', legalSex: '',
};
const fetchIlrLearnerRecord = vi.fn();
vi.mock('@/api/ilrLearnerRecord', () => ({ fetchIlrLearnerRecord: (...a: unknown[]) => fetchIlrLearnerRecord(...a) }));

const BLANK: Details = {
  yearsAtAddress: null, sinceBirth: null, postcodePriorToEnrolment: '', niNumber: '', niApplied: null,
  legalSex: '', pronouns: '', ethnicity: '', longTermDisability: null, highestQualification: '',
  employmentStatus: '', fullTimeEducation: null, stateBenefits: 'None', signature: null, signatureDate: '',
};
/** Every required answer the learner gives, except the signature. */
const ANSWERED: Details = {
  ...BLANK, postcodePriorToEnrolment: 'BR8 7EU', niNumber: 'AB 12 34 56 C', legalSex: 'Female', ethnicity: 'Irish',
  longTermDisability: false, highestQualification: 'Full level 3', employmentStatus: 'In paid employment',
  employmentStartDate: '2024-01-15', fullTimeEducation: false,
};
const wizard = vi.hoisted(() => ({
  details: {} as Record<string, unknown>,
  personalSignature: undefined as string | undefined,
  setSection: vi.fn(),
}));
vi.mock("../WizardContext.helpers", async (importOriginal) => ({
  ...(await importOriginal<typeof import('../WizardContext')>()),
  useWizard: () => ({
    userId: '41',
    isCommercial: false,
    draft: { ilrDetails: wizard.details, personalDetails: { signature: wizard.personalSignature } },
    setSection: wizard.setSection,
    board: { contact: { savedSignature: undefined } },
  }),
}));

import IlrLearnerDetails from '../steps/IlrLearnerDetails';
import { ageFromDob } from "../WizardContext.helpers";

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('AppIcon', AppIcon);
  wizard.details = { ...BLANK };
  wizard.personalSignature = undefined;
  fetchIlrLearnerRecord.mockResolvedValue(RECORD);
});

describe('ILR Learner Details step', () => {
  it('shows the record’s own details read-only rather than asking for them again', async () => {
    render(<IlrLearnerDetails />);

    expect(fetchIlrLearnerRecord).toHaveBeenCalledWith('apprenticeship', '41');
    expect(await screen.findByDisplayValue('1 Waterton')).toHaveAttribute('readonly');
    expect(screen.getByDisplayValue('12/06/1991')).toHaveAttribute('readonly');
    expect(screen.getByDisplayValue('learner@example.test')).toHaveAttribute('readonly');
    expect(screen.getByDisplayValue('Swanley')).toHaveAttribute('readonly');
  });

  it('starts the prior postcode as the current one, in a single update', async () => {
    render(<IlrLearnerDetails />);

    await waitFor(() => expect(wizard.setSection).toHaveBeenCalledWith('ilrDetails', expect.objectContaining({ postcodePriorToEnrolment: 'BR8 7EU' })));
    expect(wizard.setSection).toHaveBeenCalledTimes(1);
  });

  it('keeps a prior postcode the learner already gave', async () => {
    wizard.details = { ...BLANK, postcodePriorToEnrolment: 'CT1 1AA' };
    render(<IlrLearnerDetails />);
    await screen.findByDisplayValue('1 Waterton');

    expect(wizard.setSection).not.toHaveBeenCalled();
  });

  it('asks a learner with no NI number to confirm they have applied for one', async () => {
    render(<IlrLearnerDetails />);

    expect(await screen.findByText(/apply for one by phoning/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('checkbox', { name: /applied for a National Insurance number/i }));
    expect(wizard.setSection).toHaveBeenLastCalledWith('ilrDetails', expect.objectContaining({ niApplied: true }));
  });

  it('fills the years with the learner’s age when they have lived there since birth', async () => {
    render(<IlrLearnerDetails />);
    await screen.findByDisplayValue('1 Waterton');

    await userEvent.click(screen.getByRole('checkbox', { name: /since birth/i }));

    expect(wizard.setSection).toHaveBeenLastCalledWith('ilrDetails', expect.objectContaining({
      sinceBirth: true, yearsAtAddress: ageFromDob('1991-06-12'),
    }));
  });

  it('shows the worked-out age locked and greyed while Since birth is ticked', async () => {
    const age = ageFromDob('1991-06-12')!;
    wizard.details = { ...BLANK, sinceBirth: true, yearsAtAddress: age, postcodePriorToEnrolment: 'BR8 7EU' };
    render(<IlrLearnerDetails />);
    await screen.findByDisplayValue('1 Waterton');

    const years = screen.getByRole('spinbutton', { name: /years at this address/i });
    expect(years).toBeDisabled();
    expect(years).toHaveValue(age);
    expect(years.className).toContain('disabled:!bg-background-200');
    // Already in step with the age, so nothing needs writing.
    expect(wizard.setSection).not.toHaveBeenCalled();
  });

  it('never signs the certification for the learner, even with a Personal details signature on file', async () => {
    wizard.personalSignature = 'data:image/png;base64,PERSONAL';
    wizard.details = { ...ANSWERED };
    render(<IlrLearnerDetails />);
    await screen.findByDisplayValue('1 Waterton');

    expect(wizard.setSection).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /click to sign with your saved signature/i })).toBeInTheDocument();
    expect(screen.queryByText(/draw a new signature/i)).not.toBeInTheDocument();
  });

  it('signs with the saved signature when the learner clicks to sign', async () => {
    wizard.personalSignature = 'data:image/png;base64,PERSONAL';
    wizard.details = { ...ANSWERED };
    render(<IlrLearnerDetails />);
    await screen.findByDisplayValue('1 Waterton');

    await userEvent.click(screen.getByRole('button', { name: /click to sign with your saved signature/i }));

    expect(wizard.setSection).toHaveBeenCalledWith('ilrDetails', expect.objectContaining({
      signature: 'data:image/png;base64,PERSONAL',
      signatureDate: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/),
    }));
  });

  it('lists what is still unanswered when the learner clicks to sign too early, and does not sign', async () => {
    wizard.personalSignature = 'data:image/png;base64,PERSONAL';
    wizard.details = { ...ANSWERED, ethnicity: '', fullTimeEducation: null };
    render(<IlrLearnerDetails />);
    await screen.findByDisplayValue('1 Waterton');

    const sign = screen.getByRole('button', { name: /click to sign with your saved signature/i });
    expect(sign).toBeEnabled();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();

    await userEvent.click(sign);

    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('Please answer these before signing');
    expect(alert).toHaveTextContent('Ethnicity');
    expect(alert).toHaveTextContent('Full-time education or training');
    expect(alert).not.toHaveTextContent('Declaration signature');
    expect(wizard.setSection).not.toHaveBeenCalled();
  });

  it('does not wait on optional questions before the signature', async () => {
    wizard.personalSignature = 'data:image/png;base64,PERSONAL';
    wizard.details = { ...ANSWERED, pronouns: '', yearsAtAddress: null, sinceBirth: null };
    render(<IlrLearnerDetails />);
    await screen.findByDisplayValue('1 Waterton');

    expect(screen.getByRole('button', { name: /click to sign with your saved signature/i })).toBeInTheDocument();
  });

  it('still offers the drawing pad when no signature is saved', async () => {
    wizard.details = { ...ANSWERED };
    render(<IlrLearnerDetails />);
    await screen.findByDisplayValue('1 Waterton');

    expect(screen.getByRole('button', { name: /^click to sign$/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /saved signature/i })).not.toBeInTheDocument();
  });

  it('marks the ILR’s required questions', async () => {
    render(<IlrLearnerDetails />);
    await screen.findByDisplayValue('1 Waterton');

    // Each Yes/No renders its question twice (visible text + fieldset legend).
    expect(screen.getAllByText(/long-term disability, health problem or any learning difficulties\? \*/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/full-time education or training\? \*/).length).toBeGreaterThan(0);
    expect(screen.getByText(/I certify that the information contained on this form is correct/)).toHaveTextContent('*');
  });
});

describe('ILR follow-up questions', () => {
  const renderWith = async (details: Partial<Details>) => {
    wizard.details = { ...ANSWERED, ...details };
    render(<IlrLearnerDetails />);
    await screen.findByDisplayValue('1 Waterton');
  };

  it('asks about the job only when in paid employment', async () => {
    await renderWith({ employmentStatus: 'In paid employment' });

    expect(screen.getByText('Start date with Employer')).toBeInTheDocument();
    expect(screen.getByText('Job title')).toBeInTheDocument();
    expect(screen.getAllByText('Are you self employed?').length).toBeGreaterThan(0);
    expect(screen.queryByText('Length of unemployment')).not.toBeInTheDocument();
  });

  it.each([
    'Not in paid employment, looking for work and available to start work',
    'Not in paid employment, not looking for work and/or not available to start work',
  ])('asks about unemployment for “%s”', async (status) => {
    await renderWith({ employmentStatus: status });

    expect(screen.getByText('Length of unemployment')).toBeInTheDocument();
    expect(screen.getAllByText('Do you volunteer?').length).toBeGreaterThan(0);
    expect(screen.queryByText('Start date with Employer')).not.toBeInTheDocument();
  });

  it('asks nothing extra when employment is not known', async () => {
    await renderWith({ employmentStatus: 'Not known / not provided' });

    expect(screen.queryByText('Start date with Employer')).not.toBeInTheDocument();
    expect(screen.queryByText('Length of unemployment')).not.toBeInTheDocument();
  });

  it('preselects under six months and clears the job answers when moving out of paid employment', async () => {
    const user = userEvent.setup();
    await renderWith({ employmentStatus: 'In paid employment', employmentStartDate: '2024-01-15', jobTitle: 'Assistant', selfEmployed: false });

    // The Status dropdown, found by the answer it currently shows.
    await user.selectOptions(screen.getByDisplayValue('In paid employment'), 'Not in paid employment, looking for work and available to start work');

    expect(wizard.setSection).toHaveBeenLastCalledWith('ilrDetails', expect.objectContaining({
      employmentStatus: 'Not in paid employment, looking for work and available to start work',
      employmentStartDate: '', jobTitle: '', selfEmployed: null,
      lengthOfUnemployment: 'Learner has been unemployed for less than 6 months',
    }));
  });

  it('clears the claim answer when the benefit goes back to None', async () => {
    const user = userEvent.setup();
    await renderWith({ stateBenefits: 'Universal Credit', benefitClaimBasis: 'In my own right' });

    await user.selectOptions(screen.getByDisplayValue('Universal Credit'), 'None');

    expect(wizard.setSection).toHaveBeenLastCalledWith('ilrDetails', expect.objectContaining({ stateBenefits: 'None', benefitClaimBasis: '' }));
  });

  it('asks for the expected leaving date only when in full-time education', async () => {
    await renderWith({ fullTimeEducation: true });
    expect(screen.getByText('Please provide expected leaving date')).toBeInTheDocument();
    cleanup();
    await renderWith({ fullTimeEducation: false });
    expect(screen.queryByText('Please provide expected leaving date')).not.toBeInTheDocument();
  });

  it('asks how a benefit is claimed for every option except None', async () => {
    await renderWith({ stateBenefits: 'Universal Credit' });
    expect(screen.getByText(/in your own right or as part of a joint claim/)).toBeInTheDocument();
    cleanup();
    await renderWith({ stateBenefits: 'None' });
    expect(screen.queryByText(/in your own right or as part of a joint claim/)).not.toBeInTheDocument();
  });
});

describe('ILR Learner Details completeness', () => {
  const index = WIZARD_STEPS.findIndex((s) => s.slug === 'ilr-details');
  const draftWith = (details: Partial<Details> | undefined) =>
    ({ ilrDetails: details === undefined ? undefined : { ...BLANK, ...details } }) as unknown as WizardDraft;
  const complete: Partial<Details> = {
    postcodePriorToEnrolment: 'CT1 1AA', niNumber: 'AB 12 34 56 C', legalSex: 'Female', ethnicity: 'Irish',
    longTermDisability: false, highestQualification: 'Full level 3', employmentStatus: 'In paid employment',
    employmentStartDate: '2024-01-15', fullTimeEducation: false, signature: 'data:image/png;base64,AAAA',
  };

  it('is named ILR and sits after Personal Details, ahead of the Extended ILR and CV/Job Description', () => {
    const slugs = WIZARD_STEPS.map((s) => s.slug);
    expect(WIZARD_STEPS[index].label).toBe('ILR');
    expect(slugs.slice(slugs.indexOf('personal-details'), slugs.indexOf('cv-job') + 1))
      .toEqual(['personal-details', 'ilr-details', 'ilr', 'cv-job']);
  });

  it('is complete once every required answer is given', () => {
    expect(missingForStep(index, draftWith(complete))).toEqual([]);
  });

  it('names every unanswered required question', () => {
    expect(missingForStep(index, draftWith({}))).toEqual([
      'Postcode prior to enrolment',
      'National Insurance number, or confirm you have applied for one',
      'Legal Sex',
      'Ethnicity',
      'Long-term disability, health problem or learning difficulty',
      'Highest qualification achieved',
      'Employment status',
      'Full-time education or training',
      'Declaration signature',
    ]);
  });

  it('requires the start date only from learners in paid employment', () => {
    expect(missingForStep(index, draftWith({ ...complete, employmentStatus: 'In paid employment', employmentStartDate: '' })))
      .toEqual(['Start date with employer']);
    expect(missingForStep(index, draftWith({ ...complete, employmentStatus: 'In paid employment', employmentStartDate: '2024-01-15' })))
      .toEqual([]);
    expect(missingForStep(index, draftWith({ ...complete, employmentStatus: 'Not known / not provided' }))).toEqual([]);
  });

  it('accepts confirmation of an NI application in place of the number', () => {
    expect(missingForStep(index, draftWith({ ...complete, niNumber: '', niApplied: true }))).toEqual([]);
  });

  it('rejects a malformed NI number', () => {
    expect(missingForStep(index, draftWith({ ...complete, niNumber: 'QQ123456C' }))).toEqual([
      'National Insurance number — enter a valid number, for example AB 12 34 56 C',
    ]);
  });

  it('treats a draft saved before this step existed as outstanding', () => {
    expect(missingForStep(index, draftWith(undefined))).not.toEqual([]);
  });
});
