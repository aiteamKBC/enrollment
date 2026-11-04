/**
 * ILR Learner Details: record values shown read-only, the ILR questions the
 * record cannot answer, and what the step requires before the learner moves on.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AppIcon } from '@/components/feature/AppIcon';
import type { IlrLearnerDetails as Details, WizardDraft } from '../../types';
import { WIZARD_STEPS } from '../../types';
import { missingForStep } from '../validation';
import { signatureDeclaration } from '../signatureDeclaration';
import { HEALTH_CONDITIONS } from '../ilrHealthOptions';

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
  employmentStartDate: '2024-01-15', fullTimeEducation: false, workingHoursPerWeek: '37.5',
};
const wizard = vi.hoisted(() => ({
  details: {} as Record<string, unknown>,
  personalSignature: undefined as string | undefined,
  title: undefined as string | undefined,
  finalisedAt: undefined as string | undefined,
  setSection: vi.fn(),
}));
vi.mock("../WizardContext.helpers", async (importOriginal) => ({
  ...(await importOriginal<typeof import('../WizardContext')>()),
  useWizard: () => ({
    userId: '41',
    isCommercial: false,
    draft: { ilrDetails: wizard.details, personalDetails: { signature: wizard.personalSignature, title: wizard.title } },
    setSection: wizard.setSection,
    board: { contact: { savedSignature: undefined } },
    ilrFinalisedAt: wizard.finalisedAt,
    readOnly: false,
  }),
}));

import IlrLearnerDetails from '../steps/IlrLearnerDetails';
import { ageFromDob } from "../WizardContext.helpers";

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('AppIcon', AppIcon);
  wizard.details = { ...BLANK };
  wizard.personalSignature = undefined;
  wizard.title = undefined;
  wizard.finalisedAt = undefined;
  fetchIlrLearnerRecord.mockResolvedValue(RECORD);
});

describe('ILR Learner Details step', () => {
  it('renders a finalised apprenticeship ILR without active editing or signature replacement', async () => {
    wizard.title = 'Ms';
    wizard.personalSignature = 'data:image/png;base64,AAAA';
    wizard.details = { ...ANSWERED };
    wizard.finalisedAt = '2026-11-04T12:00:00Z';
    render(<IlrLearnerDetails />);
    expect(await screen.findByText(/signed and submitted/i)).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: /^Title/ })).toBeDisabled();
    expect(screen.getAllByDisplayValue('BR8 7EU')[0]).toBeDisabled();
    expect(screen.queryByRole('button', { name: /re-sign|clear/i })).not.toBeInTheDocument();
    expect(wizard.setSection).not.toHaveBeenCalled();
  });
  it('offers the existing Title choices and saves through the existing personal-details contract', async () => {
    wizard.title = '';
    render(<IlrLearnerDetails />);
    const select = await screen.findByRole('combobox', { name: /^Title/ });
    await userEvent.selectOptions(select, 'Ms');
    expect(wizard.setSection).toHaveBeenLastCalledWith('personalDetails', expect.objectContaining({ title: 'Ms' }));
    expect(Array.from(select.querySelectorAll('option')).map(option => option.value)).toEqual(['', 'Mr', 'Mrs', 'Miss', 'Ms', 'Mx', 'Dr', 'Prof']);
  });
  it('retains a historical Title outside the current list', async () => {
    wizard.title = 'Reverend';
    render(<IlrLearnerDetails />);
    expect(await screen.findByRole('combobox', { name: /^Title/ })).toHaveValue('Reverend');
  });
  it('protects verified identity while allowing residential corrections before signing', async () => {
    render(<IlrLearnerDetails />);

    expect(fetchIlrLearnerRecord).toHaveBeenCalledWith('apprenticeship', '41');
    expect(await screen.findByDisplayValue('1 Waterton')).not.toHaveAttribute('readonly');
    expect(screen.getByDisplayValue('12/06/1991')).toHaveAttribute('readonly');
    expect(screen.getByDisplayValue('learner@example.test')).toHaveAttribute('readonly');
    expect(screen.getByDisplayValue('Swanley')).not.toHaveAttribute('readonly');
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

  it('shows NI guidance and the stored application confirmation only when the number is absent', async () => {
    render(<IlrLearnerDetails />);
    await screen.findByDisplayValue('1 Waterton');
    expect(screen.getByText(/apply for one by phoning/i)).toBeInTheDocument();
    const confirmation = screen.getByRole('checkbox', { name: /confirm that you have done so/i });
    await userEvent.click(confirmation);
    expect(wizard.setSection).toHaveBeenLastCalledWith('ilrDetails', expect.objectContaining({ niApplied: true }));
    cleanup();
    wizard.details = { ...ANSWERED };
    render(<IlrLearnerDetails />);
    await screen.findByDisplayValue('1 Waterton');
    expect(screen.queryByText(/apply for one by phoning/i)).not.toBeInTheDocument();
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

  it('shows only the retained mark and does not silently repurpose a historical signature', async () => {
    wizard.personalSignature = 'data:image/png;base64,PERSONAL';
    wizard.details = { ...ANSWERED };
    render(<IlrLearnerDetails />);
    await screen.findByDisplayValue('1 Waterton');

    expect(wizard.setSection).not.toHaveBeenCalled();
    expect(screen.getAllByRole('img', { name: 'Your signature' })).toHaveLength(1);
    expect(screen.queryByText('Learner signature')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /confirm signature for this declaration/i })).toBeInTheDocument();
  });

  it('records combined consent only when the learner explicitly confirms, without copying to the retired field', async () => {
    wizard.personalSignature = 'data:image/png;base64,PERSONAL';
    wizard.details = { ...ANSWERED };
    render(<IlrLearnerDetails />);
    await screen.findByDisplayValue('1 Waterton');

    await userEvent.click(screen.getByRole('button', { name: /confirm signature for this declaration/i }));

    expect(wizard.setSection).toHaveBeenCalledWith('personalDetails', expect.objectContaining({
      signature: 'data:image/png;base64,PERSONAL',
      signatureDeclaration: { text: signatureDeclaration(), confirmedAt: expect.any(String) },
    }));
  });

  it('keeps question completion validation independent of the single signature', async () => {
    wizard.personalSignature = 'data:image/png;base64,PERSONAL';
    wizard.details = { ...ANSWERED, ethnicity: '', fullTimeEducation: null };
    render(<IlrLearnerDetails />);
    await screen.findByDisplayValue('1 Waterton');

    const draft = { ilrDetails: wizard.details, personalDetails: { currentPostcode: 'BR8 7EU', signature: wizard.personalSignature } } as unknown as WizardDraft;
    const missing = missingForStep(WIZARD_STEPS.findIndex(step => step.slug === 'ilr-details'), draft);
    expect(missing).toEqual(['Ethnicity', 'Full-time education or training', 'Your signature']);
    expect(wizard.setSection).not.toHaveBeenCalled();
  });

  it('does not wait on optional questions before the signature', async () => {
    wizard.personalSignature = 'data:image/png;base64,PERSONAL';
    wizard.details = { ...ANSWERED, pronouns: '', yearsAtAddress: null, sinceBirth: null };
    render(<IlrLearnerDetails />);
    await screen.findByDisplayValue('1 Waterton');

    expect(screen.getByRole('button', { name: /confirm signature for this declaration/i })).toBeInTheDocument();
  });

  it('still offers the drawing pad when no signature is saved', async () => {
    wizard.details = { ...ANSWERED };
    render(<IlrLearnerDetails />);
    await screen.findByDisplayValue('1 Waterton');

    expect(screen.getAllByRole('button', { name: /^click to sign$/i })).toHaveLength(1);
    expect(screen.queryByRole('button', { name: /saved signature/i })).not.toBeInTheDocument();
  });

  it('marks the ILR’s required questions', async () => {
    render(<IlrLearnerDetails />);
    await screen.findByDisplayValue('1 Waterton');

    // Each Yes/No renders its question twice (visible text + fieldset legend).
    expect(screen.getAllByText(/long-term disability, health problem or any learning difficulties\? \*/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/full-time education or training\? \*/).length).toBeGreaterThan(0);
    expect(screen.getByText(/I certify that the information contained on this form is correct/)).toBeInTheDocument();
    expect(screen.getByText(/confirm the ILR accuracy declaration and authorise this signature/)).toBeInTheDocument();
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

describe('Apprenticeship ILR health and hours', () => {
  const show = async (details: Partial<Details>) => {
    wizard.details = { ...ANSWERED, ...details };
    render(<IlrLearnerDetails />);
    await screen.findByDisplayValue('1 Waterton');
  };

  it('shows all 22 coded conditions for Yes and filters them by search', async () => {
    await show({ longTermDisability: true });
    const list = document.querySelector('[aria-label="Health condition options"]') as HTMLElement;
    expect(HEALTH_CONDITIONS).toHaveLength(22);
    expect(within(list).getAllByRole('checkbox')).toHaveLength(22);
    expect(within(list).getByRole('checkbox', { name: /Asperger’s syndrome/ })).toBeInTheDocument();
    await userEvent.type(screen.getByRole('searchbox', { name: 'Search health conditions' }), 'dys');
    expect(within(list).getAllByRole('checkbox')).toHaveLength(3);
    expect(within(list).getByRole('checkbox', { name: 'Dyslexia' })).toBeInTheDocument();
  });

  it('adds multiple codes without duplication and removes a selected chip', async () => {
    await show({ longTermDisability: true, healthConditionCodes: ['12', '13'], primaryHealthConditionCode: '12' });
    expect(screen.getByRole('button', { name: 'Remove Dyslexia' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Remove Dyslexia' }));
    expect(wizard.setSection).toHaveBeenLastCalledWith('ilrDetails', expect.objectContaining({
      healthConditionCodes: ['13'], primaryHealthConditionCode: '',
    }));
    await userEvent.click(screen.getByRole('checkbox', { name: 'Dyscalculia' }));
    expect(wizard.setSection).toHaveBeenLastCalledWith('ilrDetails', expect.objectContaining({ healthConditionCodes: ['12'] }));
  });

  it('restricts primary choices to selected conditions and shows support details for Other', async () => {
    await show({ longTermDisability: true, healthConditionCodes: ['12', '93'], primaryHealthConditionCode: '12' });
    const primary = screen.getByRole('combobox', { name: /Which of these do you consider/ });
    expect(Array.from(primary.querySelectorAll('option')).map(option => option.value)).toEqual(['', '12', '93']);
    expect(screen.getByRole('textbox', { name: /additional support/i })).toBeInTheDocument();
  });

  it('treats nondisclosure as exclusive and hides primary and support questions', async () => {
    await show({ longTermDisability: true, healthConditionCodes: ['98'] });
    expect(screen.queryByRole('combobox', { name: /Which of these do you consider/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: /additional support/i })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('checkbox', { name: 'Dyslexia' }));
    expect(wizard.setSection).toHaveBeenLastCalledWith('ilrDetails', expect.objectContaining({ healthConditionCodes: ['12'] }));
  });

  it('clears dependent answers when Health changes to No', async () => {
    await show({ longTermDisability: true, healthConditionCodes: ['93'], primaryHealthConditionCode: '93', additionalSupportDetails: 'Quiet room' });
    await userEvent.click(within(screen.getByRole('group', { name: /long-term disability/i })).getByRole('radio', { name: 'No' }));
    expect(wizard.setSection).toHaveBeenLastCalledWith('ilrDetails', expect.objectContaining({
      longTermDisability: false, healthConditionCodes: [], primaryHealthConditionCode: '', additionalSupportDetails: '',
    }));
    cleanup();
    await show({ longTermDisability: false });
    expect(screen.queryByRole('searchbox', { name: 'Search health conditions' })).not.toBeInTheDocument();
  });

  it('requires decimal hours for paid work only and clears them when employment changes', async () => {
    await show({ employmentStatus: 'In paid employment', workingHoursPerWeek: '37.5' });
    const hours = screen.getByRole('spinbutton', { name: 'Working hours per week' });
    expect(hours).toHaveValue(37.5);
    fireEvent.change(hours, { target: { value: '24.25' } });
    expect(wizard.setSection).toHaveBeenLastCalledWith('ilrDetails', expect.objectContaining({ workingHoursPerWeek: '24.25' }));
    await userEvent.selectOptions(screen.getByDisplayValue('In paid employment'), 'Not known / not provided');
    expect(wizard.setSection).toHaveBeenLastCalledWith('ilrDetails', expect.objectContaining({ workingHoursPerWeek: '' }));
  });
});

describe('ILR Learner Details completeness', () => {
  const index = WIZARD_STEPS.findIndex((s) => s.slug === 'ilr-details');
  const draftWith = (details: Partial<Details> | undefined) =>
    ({ personalDetails: { currentPostcode: 'CT1 1AA', signature: 'data:image/png;base64,SAVED', signatureDeclaration: { text: signatureDeclaration(), confirmedAt: '2026-11-03T10:00:00Z' } }, ilrDetails: details === undefined ? undefined : { ...BLANK, ...details } }) as unknown as WizardDraft;
  const complete: Partial<Details> = {
    postcodePriorToEnrolment: 'CT1 1AA', niNumber: 'AB 12 34 56 C', legalSex: 'Female', ethnicity: 'Irish',
    longTermDisability: false, highestQualification: 'Full level 3', employmentStatus: 'In paid employment',
    employmentStartDate: '2024-01-15', fullTimeEducation: false, workingHoursPerWeek: '37.5',
  };
  it('requires Title for a new learner only at ILR and retains historical completion compatibility', () => {
    const draft = draftWith(complete);
    expect(missingForStep(index, draft)).toEqual([]);
    draft.personalDetails.title = '';
    expect(missingForStep(index, draft)).toEqual(['Title']);
    draft.personalDetails.title = 'Ms';
    expect(missingForStep(index, draft)).toEqual([]);
    expect(missingForStep(0, { ...draft, personalDetails: { ...draft.personalDetails, title: '' } })).toEqual([]);
  });

  it('is named ILR and follows Before You Begin, ahead of Extended ILR and CV/Job Description', () => {
    const slugs = WIZARD_STEPS.map((s) => s.slug);
    expect(WIZARD_STEPS[index].label).toBe('ILR');
    expect(slugs.slice(slugs.indexOf('before-you-begin'), slugs.indexOf('cv-job') + 1))
      .toEqual(['before-you-begin', 'ilr-details', 'ilr', 'programme-suitability', 'cv-job']);
  });

  it('is complete once every required answer is given', () => {
    expect(missingForStep(index, draftWith(complete))).toEqual([]);
  });

  it('names every unanswered required question', () => {
    const draft = draftWith({});
    draft.personalDetails.currentPostcode = '';
    expect(missingForStep(index, draft)).toEqual([
      'Current postcode',
      'NI number application confirmation',
      'Legal Sex',
      'Ethnicity',
      'Long-term disability, health problem or learning difficulty',
      'Highest qualification achieved',
      'Employment status',
      'Full-time education or training',
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
