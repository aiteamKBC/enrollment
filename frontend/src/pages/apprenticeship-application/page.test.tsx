import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Page from './page';
import {
  ApplicationSubmitError,
  fetchEligibilityForm,
  lookupPostcode,
  submitApprenticeshipApplication,
  type PostcodeCheck,
} from '@/api/apprenticeshipApplication';
import { isRecordedPath } from '@/lib/activityTrail';
import routes from '@/router/config';
import { defaultLayout, resolveLayout } from './layout/resolve';
import type { EligibilityLayout } from './layout/types';
import { APPLICATION_AGE_NOTICE, APPLICATION_AGE_REASON, EMPLOYER_POSTCODE_UNAVAILABLE, WORKPLACE_POSTCODE_UNAVAILABLE, INVALID_POSTCODE, UNKNOWN_POSTCODE } from './form';
import { formSections, toPayload } from './form';
import { WORKPLACE_LABELS } from '@/api/apprenticeshipApplication';

vi.mock('@/api/apprenticeshipApplication', async original => ({
  ...await original<typeof import('@/api/apprenticeshipApplication')>(),
  submitApprenticeshipApplication: vi.fn(),
  fetchEligibilityForm: vi.fn(),
  lookupPostcode: vi.fn(),
}));

const EXETER: PostcodeCheck = { status: 'found', postcode: 'EX1 2MP', country: 'England', region: 'South West', district: 'Exeter', inEngland: true };
const EDINBURGH: PostcodeCheck = { status: 'found', postcode: 'EH1 1YZ', country: 'Scotland', region: '', district: 'City of Edinburgh', inEngland: false };
const ELIGIBLE = { eligible: true, reasons: [], outcome: 'invited' as const };

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-14T12:00:00Z'));
  vi.mocked(fetchEligibilityForm).mockResolvedValue({ layout: null, version: null });
  vi.mocked(lookupPostcode).mockReset().mockResolvedValue(EXETER);
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.useRealTimers(); });

const open = async () => {
  render(<MemoryRouter><Page /></MemoryRouter>);
  await screen.findByText(/^Section 1 of/);
};
const type = (label: RegExp, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
const choose = (group: RegExp, option: string) =>
  fireEvent.click(within(screen.getByRole('radiogroup', { name: group })).getByLabelText(option));
const next = () => fireEvent.click(screen.getByRole('button', { name: /Continue/ }));
const heading = (name: string) => screen.findByRole('heading', { name });

function fillPersonalDetails(programme = 'Marketing Manager Level 6') {
  type(/First Name/, 'Sam');
  type(/Surname/, 'Example');
  type(/^3\. Title/, 'Mr');
  type(/Work Email Address/, 'sam@employer.example');
  type(/Date of Birth/, '1995-04-03');
  type(/Choose Your Applying Programme/, programme);
}

function fillEmployment(hours: 'Yes' | 'No' | null = 'Yes') {
  fireEvent.change(screen.getByLabelText(/Your Employment Status/), { target: { value: 'In paid employment' } });
  type(/Start Date with Employer/, '2020-09-01');
  type(/Job Title/, 'Marketing Executive');
  if (hours) choose(/50% of your working hours in England/, hours);
  type(/^4\. Employer/, 'Example Ltd');
  type(/Employer Address Line 1/, '2 Business Park');
  type(/Employer Address Line 3/, 'Exampletown');
  type(/Employer Postcode/, 'EX3 4MP');
  type(/Employer Representative Name/, 'Alex Manager');
  type(/Employer Representative Email/, 'alex@employer.example');
}

function fillResidency() {
  fireEvent.change(screen.getByLabelText(/Country of Residence/), { target: { value: 'United Kingdom' } });
  choose(/UK\/EEA National/, 'Yes');
  fireEvent.change(screen.getByLabelText(/Please state your nationality/), { target: { value: 'United Kingdom' } });
  fireEvent.change(screen.getByLabelText(/residency status/), { target: { value: 'Pre-settled Status' } });
  choose(/resident in the UK and Islands/, 'Yes');
  choose(/ordinary residence in the UK/, 'Yes');
  choose(/full-time education or training/, 'No');
  choose(/government-funded training programmes/, 'No');
  type(/Residential Address Line 1/, '1 Example Street');
  type(/Residential Address Line 3/, 'Exampletown');
}

async function reachResidency() {
  fillPersonalDetails(); next();
  await heading('Employment');
  fillEmployment(); next();
  await heading('Residency');
  fillResidency();
}

function legacyLayout(): EligibilityLayout {
  const layout = defaultLayout();
  const personal = layout.sections[0];
  const programme = personal.items.find(item => item.key === 'programme')!;
  personal.items = personal.items.filter(item => item !== programme);
  layout.sections.push({ slug: 'programme', title: 'Programme Suitability', builtin: true, hidden: false,
    items: [{ ...programme, options: ['Data Analyst Level 4'], required: true },
      { key: 'notPreviouslyCompletedProgramme', builtin: true, hidden: false, required: true }] });
  return layout;
}

it('resolves a legacy four-section publication to three sections and keeps programme options and answers', async () => {
  const layout = legacyLayout();
  const before = JSON.stringify(layout);
  vi.mocked(fetchEligibilityForm).mockResolvedValue({ layout, version: 5 });
  vi.mocked(submitApprenticeshipApplication).mockResolvedValue(ELIGIBLE);
  await open();
  expect(screen.getByText('Section 1 of 3')).toBeInTheDocument();
  expect(screen.getByLabelText(/^7\. Choose Your Applying Programme/)).toBeInTheDocument();
  fillPersonalDetails('Data Analyst Level 4'); next();
  await heading('Employment'); fillEmployment(); next();
  await heading('Residency');
  expect(screen.getByText('Section 3 of 3')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Continue/ })).not.toBeInTheDocument();
  expect(screen.queryByRole('radiogroup', { name: /not previously completed/ })).not.toBeInTheDocument();
  fillResidency();
  fireEvent.click(screen.getByRole('button', { name: /Previous/ }));
  await heading('Employment');
  expect(screen.getByLabelText(/Job Title/)).toHaveValue('Marketing Executive');
  fireEvent.click(screen.getByRole('button', { name: /Previous/ }));
  expect(screen.getByLabelText(/Choose Your Applying Programme/)).toHaveValue('Data Analyst Level 4');
  next(); await heading('Employment'); next(); await heading('Residency');
  expect(within(screen.getByRole('radiogroup', { name: /resident in the UK and Islands/ })).getByLabelText('Yes')).toBeChecked();
  fireEvent.click(screen.getByRole('button', { name: /Submit application/ }));
  await heading('You Are Eligible to Proceed!');
  expect(submitApprenticeshipApplication).toHaveBeenCalledWith(expect.objectContaining({
    programme: 'Data Analyst Level 4', notPreviouslyCompletedProgramme: null, layoutVersion: 5,
  }));
  expect(JSON.stringify(layout)).toBe(before);
  expect(resolveLayout(layout).sections.map(section => section.slug)).toEqual(['personal-details', 'employment', 'residency']);
});

it('shows the address placeholders with published labels and hints, preserving typed answers during navigation', async () => {
  const layout = defaultLayout();
  layout.texts = { 'employerAddressLine1.label': 'Company street address', 'employerAddressLine1.hint': 'Use your employer registered address.',
    'employerTownCity.label': 'Employer Town or City', 'employerCounty.label': 'Employer County',
    'residentialTownCity.label': 'Residential Town or City', 'residentialCounty.label': 'Residential County',
    'employerPostcode.hint': 'Enter a postcode, e.g. ME14 1XQ', 'postcode.hint': 'For example ME14 1XQ' };
  for (const [slug, prefix] of [['employment', 'employer'], ['residency', 'residential']]) {
    const section = layout.sections.find(section => section.slug === slug)!;
    section.items.push({ key: `${prefix}AddressLine3`, builtin: true, hidden: false, required: true });
    section.items.find(item => item.key === `${prefix}County`)!.required = true;
  }
  const before = JSON.stringify(layout);
  vi.mocked(fetchEligibilityForm).mockResolvedValue({ layout, version: 6 });
  await open(); fillPersonalDetails(); next(); await heading('Employment');
  type(/Your Employment Status/, 'In paid employment');
  const placeholders = [
    ['AddressLine1', 'House number and street name'],
    ['AddressLine2', 'Flat number, building name or area'],
    ['TownCity', 'Town or City'], ['County', 'County and Country'],
  ];
  for (const [, placeholder] of placeholders) {
    const field = screen.getByPlaceholderText(placeholder);
    expect(field).toHaveValue('');
  }
  expect(screen.getByLabelText(/Company street address/)).toHaveAttribute('placeholder', placeholders[0][1]);
  expect(screen.getByText('Use your employer registered address.')).toBeInTheDocument();
  expect(screen.getByLabelText(/Employer Postcode/)).toHaveAttribute('placeholder', 'Enter postcode');
  expect(document.getElementById('application-employerPostcode-hint')).toBeNull();
  expect(document.querySelectorAll('input[id^="application-employerAddress"], #application-employerTownCity, #application-employerCounty, #application-employerPostcode')).toHaveLength(5);
  expect(screen.queryByLabelText(/Employer Town or City|Employer County/)).not.toBeInTheDocument();
  expect(screen.getByLabelText(/Employer Address Line 3/)).toHaveAttribute('aria-required', 'true');
  expect(screen.getByLabelText(/Employer Address Line 4/)).toHaveAttribute('aria-required', 'false');
  expect(screen.queryByText(/e\.g\.|for example/i)).not.toBeInTheDocument();
  type(/Company street address/, '2 Business Park');
  type(/Start Date with Employer/, '2020-09-01'); type(/Job Title/, 'Marketing Executive');
  choose(/50% of your working hours in England/, 'Yes'); type(/^4\. Employer/, 'Example Ltd');
  type(/Employer Address Line 3/, 'Exampletown'); type(/Employer Postcode/, 'EX3 4MP');
  type(/Employer Representative Name/, 'Alex Manager'); type(/Employer Representative Email/, 'alex@employer.example');
  next(); await heading('Residency');
  for (const [, placeholder] of placeholders) {
    expect(screen.getByPlaceholderText(placeholder)).toHaveValue('');
  }
  expect(screen.getByLabelText(/Residential Postcode/)).toHaveAttribute('placeholder', 'Enter postcode');
  expect(document.getElementById('application-postcode-hint')).toBeNull();
  expect(document.querySelectorAll('input[id^="application-residentialAddress"], #application-residentialTownCity, #application-residentialCounty, #application-postcode')).toHaveLength(5);
  expect(screen.queryByLabelText(/Residential Town or City|Residential County/)).not.toBeInTheDocument();
  expect(screen.getByLabelText(/Residential Address Line 3/)).toHaveAttribute('aria-required', 'true');
  expect(screen.getByLabelText(/Residential Address Line 4/)).toHaveAttribute('aria-required', 'false');
  fireEvent.click(screen.getByRole('button', { name: /Previous/ }));
  expect(screen.getByLabelText(/Company street address/)).toHaveValue('2 Business Park');
  expect(JSON.stringify(layout)).toBe(before);
});

it('submits all structured address parts and keeps composed legacy fields compatible', async () => {
  vi.mocked(submitApprenticeshipApplication).mockResolvedValue(ELIGIBLE);
  await reachEmployment();
  type(/Employer Address Line 2/, 'Building B'); type(/Employer Address Line 4/, 'Kent');
  expect(screen.getByLabelText(/Employer Address Line 1/)).toHaveAttribute('aria-required', 'true');
  expect(screen.getByLabelText(/Employer Address Line 3/)).toHaveAttribute('aria-required', 'true');
  expect(screen.getByLabelText(/Employer Address Line 2/)).toHaveAttribute('aria-required', 'false');
  expect(screen.getByLabelText(/Employer Address Line 1/)).toHaveAttribute('maxlength', '200');
  next(); await heading('Residency'); fillResidency();
  type(/Residential Address Line 2/, 'Flat 4'); type(/Residential Address Line 4/, 'Kent');
  fireEvent.click(screen.getByRole('button', { name: /Submit application/ }));
  await heading('You Are Eligible to Proceed!');
  expect(submitApprenticeshipApplication).toHaveBeenCalledWith(expect.objectContaining({
    employerAddressLine1: '2 Business Park', employerAddressLine2: 'Building B', employerTownCity: 'Exampletown', employerCounty: 'Kent',
    residentialAddressLine1: '1 Example Street', residentialAddressLine2: 'Flat 4', residentialTownCity: 'Exampletown', residentialCounty: 'Kent',
    employerAddress: '2 Business Park, Building B, Exampletown, Kent', fullAddress: '1 Example Street, Flat 4, Exampletown, Kent',
  }));
});

it.each(['Employer Address Line 1', 'Employer Address Line 3'])('requires %s before Employment Continue', async label => {
  await reachEmployment(); type(new RegExp(label), ''); next();
  expect(await screen.findByText('This field is required.')).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Employment' })).toBeInTheDocument();
  expect(submitApprenticeshipApplication).not.toHaveBeenCalled();
});

it.each(['Residential Address Line 1', 'Residential Address Line 3'])('requires %s before final submission', async label => {
  await open(); await reachResidency(); type(new RegExp(label), '');
  fireEvent.click(screen.getByRole('button', { name: /Submit application/ }));
  expect(await screen.findByText('This field is required.')).toBeInTheDocument();
  expect(submitApprenticeshipApplication).not.toHaveBeenCalled();
});

it('supports overseas postal codes without UK lookup, validation or stale UK geography', async () => {
  vi.mocked(submitApprenticeshipApplication).mockResolvedValue(ELIGIBLE);
  await open(); await reachResidency();
  type(/Country of Residence/, 'France');
  type(/Residential Address Line 1/, '10 Rue Exemple'); type(/Residential Address Line 3/, 'Paris');
  vi.mocked(lookupPostcode).mockClear();
  type(/Residential Postcode \/ Postal Code/, 'CF10 1EP');
  fireEvent.blur(screen.getByLabelText(/Residential Postcode \/ Postal Code/));
  await act(async () => new Promise(resolve => window.setTimeout(resolve, 650)));
  expect(lookupPostcode).not.toHaveBeenCalled();
  expect(screen.queryByText('Exeter, South West, England')).not.toBeInTheDocument();
  type(/Residential Postcode \/ Postal Code/, '75001');
  fireEvent.click(screen.getByRole('button', { name: /Submit application/ }));
  await heading('You Are Eligible to Proceed!');
  expect(submitApprenticeshipApplication).toHaveBeenCalledWith(expect.objectContaining({ countryOfResidence: 'France', postcode: '75001', residentialTownCity: 'Paris' }));
});

it('upgrades old full-address layout slots while preserving labels, positions and visibility', () => {
  const raw = defaultLayout();
  const groups = [
    ['employment', 'employerAddress', ['employerAddressLine1', 'employerAddressLine2', 'employerTownCity', 'employerCounty']],
    ['residency', 'fullAddress', ['residentialAddressLine1', 'residentialAddressLine2', 'residentialTownCity', 'residentialCounty']],
  ] as const;
  for (const [slug, key, keys] of groups) {
    const section = raw.sections.find(section => section.slug === slug)!;
    const index = section.items.findIndex(item => item.key === keys[0]);
    section.items = section.items.filter(item => !(keys as readonly string[]).includes(item.key));
    section.items.splice(index, 0, { key, builtin: true, hidden: key === 'fullAddress', required: false });
  }
  raw.texts = { 'employerAddress.label': 'Company location' };
  const before = JSON.stringify(raw);
  const resolved = resolveLayout(raw);
  expect(resolved.texts?.['employerAddressLine1.label']).toBe('Company location');
  const employment = resolved.sections.find(section => section.slug === 'employment')!;
  expect(employment.items.filter(item => item.key.startsWith('employerAddress')).map(item => item.key)).toEqual(['employerAddressLine1', 'employerAddressLine2']);
  expect(employment.items.some(item => item.key === 'employerAddress')).toBe(false);
  const residential = resolved.sections.find(section => section.slug === 'residency')!;
  expect(residential.items.filter(item => item.key.startsWith('residential')).every(item => item.hidden)).toBe(true);
  expect(JSON.stringify(raw)).toBe(before);
});

it('requires the programme choice before leaving Personal Details', async () => {
  await open(); fillPersonalDetails(''); next();
  expect(screen.getByText('Section 1 of 3')).toBeInTheDocument();
  expect(screen.getByLabelText(/Choose Your Applying Programme/)).toHaveAttribute('aria-invalid', 'true');
  expect(screen.getByLabelText(/Choose Your Applying Programme/)).toHaveFocus();
  expect(submitApprenticeshipApplication).not.toHaveBeenCalled();
});

it('walks the three sections and submits one complete application', async () => {
  vi.mocked(submitApprenticeshipApplication).mockResolvedValue(ELIGIBLE);
  await open();

  expect(screen.getByRole('heading', { name: 'Eligibility Form' })).toBeInTheDocument();
  expect(screen.queryByText('All fields marked with * are required.')).not.toBeInTheDocument();
  expect(screen.queryByText('No LMS account needed to apply')).not.toBeInTheDocument();
  expect(screen.getByLabelText(/^1\. First Name/)).toBeInTheDocument();
  expect(screen.getByLabelText(/^6\. Date of Birth/)).toBeInTheDocument();
  expect(screen.queryByLabelText(/Residential Address Line 1/)).not.toBeInTheDocument();
  expect(screen.queryByLabelText(/^\d+\. Residential Postcode /)).not.toBeInTheDocument();
  expect(screen.getByText('Section 1 of 3')).toBeInTheDocument();
  expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '33');
  expect(screen.queryByRole('button', { name: /Previous/ })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Save for later/ })).not.toBeInTheDocument();
  fillPersonalDetails('Associate Project Manager Level 4');
  next();

  expect(await heading('Employment')).toHaveFocus();
  expect(screen.getByText('67% complete')).toBeInTheDocument();
  fillEmployment();
  next();

  expect(await heading('Residency')).toBeInTheDocument();
  fillResidency();
  choose(/government-funded training programmes/, 'Yes');
  type(/When was it completed/, '2026-03-01');
  expect(screen.getByText('100% complete')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /Submit application/ }));

  expect(await heading('You Are Eligible to Proceed!')).toBeInTheDocument();
  expect(submitApprenticeshipApplication).toHaveBeenCalledTimes(1);
  expect(submitApprenticeshipApplication).toHaveBeenCalledWith({
    firstName: 'Sam', surname: 'Example', title: 'Mr', workEmail: 'sam@employer.example', phoneNumber: '',
    fullAddress: '1 Example Street, Exampletown', postcode: '', dateOfBirth: '1995-04-03',
    employmentStatus: 'In paid employment', startDateWithEmployer: '2020-09-01', jobTitle: 'Marketing Executive', majorityHoursInEngland: true,
    employerName: 'Example Ltd', employerAddress: '2 Business Park, Exampletown', employerPostcode: 'EX3 4MP',
    representativeName: 'Alex Manager', representativeEmail: 'alex@employer.example',
    livesInUk: true, countryOfResidence: 'United Kingdom', nationality: 'United Kingdom', residentPrev3Years: true, yearsInUk: '',
    residenceNotForEducation: true, fullTimeEducationPrior: false, expectedLeavingDate: null, residencyStatus: 'Pre-settled Status',
    programme: 'Associate Project Manager Level 4', attendedFundedTraining: true,
    fundedTrainingCompletedOn: '2026-03-01', notPreviouslyCompletedProgramme: null,
    employerAddressLine1: '2 Business Park', employerAddressLine2: '', employerTownCity: 'Exampletown', employerCounty: '',
    residentialAddressLine1: '1 Example Street', residentialAddressLine2: '', residentialTownCity: 'Exampletown', residentialCounty: '',
    custom: {}, layoutVersion: null,
  });
  expect(screen.getByText('Thank you for completing your application.')).toBeInTheDocument();
});

it.each(['standard', 'missing', 'hidden-moved'])('keeps the residency Yes confirmation directly below its parent and numbers only visible questions (%s layout)', async mode => {
  const layout = defaultLayout();
  const residency = layout.sections.find(section => section.slug === 'residency')!;
  if (mode !== 'standard') {
    residency.items = residency.items.filter(item => item.key !== 'residenceNotForEducation');
    if (mode === 'hidden-moved') residency.items.push({ key: 'residenceNotForEducation', builtin: true, hidden: true });
    vi.mocked(fetchEligibilityForm).mockResolvedValue({ layout, version: 1 });
  }
  const original = JSON.stringify(layout);
  await open();
  fillPersonalDetails(); next();
  await heading('Employment'); fillEmployment(); next();
  await heading('Residency');
  const confirmationLabel = /Please confirm that your ordinary residence in the UK for the last 3 years has not been mainly or wholly for the purpose of full-time education\./;
  expect(screen.queryByRole('radiogroup', { name: confirmationLabel })).not.toBeInTheDocument();
  expect(screen.getByLabelText(/^6\. Residential Address Line 1/)).toBeInTheDocument();
  const parent = screen.getByRole('radiogroup', { name: /Have you been ordinarily resident in the UK and Islands for the last 3 years/ });
  choose(/Have you been ordinarily resident/, 'Yes');
  const confirmation = screen.getByRole('radiogroup', { name: confirmationLabel });
  expect(parent).toHaveAccessibleName(/^5\./);
  expect(confirmation).toHaveAccessibleName(/^6\./);
  expect(parent.parentElement!.parentElement!.nextElementSibling).toBe(confirmation.parentElement!.parentElement);
  expect(screen.getByLabelText(/^7\. Residential Address Line 1/)).toBeInTheDocument();
  expect(screen.queryByLabelText(/How many full years/)).not.toBeInTheDocument();
  choose(/Have you been ordinarily resident/, 'No');
  expect(screen.queryByRole('radiogroup', { name: confirmationLabel })).not.toBeInTheDocument();
  expect(screen.getByLabelText(/^6\. How many full years/)).toBeInTheDocument();
  expect(screen.getByLabelText(/^7\. Residential Address Line 1/)).toBeInTheDocument();
  choose(/Have you been ordinarily resident/, 'Yes');
  expect(screen.getByRole('radiogroup', { name: confirmationLabel })).toHaveAccessibleName(/^6\./);
  expect(JSON.stringify(layout)).toBe(original);
});

it.each(['standard', 'legacy'])('shows the future leaving date immediately after education Yes and omits hidden answers (%s layout)', async mode => {
  if (mode === 'legacy') {
    const layout = defaultLayout();
    for (const section of layout.sections) section.items = section.items.filter(item => item.key !== 'expectedLeavingDate');
    vi.mocked(fetchEligibilityForm).mockResolvedValue({ layout, version: 1 });
  }
  vi.mocked(submitApprenticeshipApplication).mockResolvedValue(ELIGIBLE);
  await open();
  fillPersonalDetails(); next(); await heading('Employment'); fillEmployment(); next();
  await heading('Residency'); fillResidency();
  expect(screen.queryByLabelText(/Please provide expected leaving date/)).not.toBeInTheDocument();
  choose(/full-time education or training prior to enrolment/, 'Yes');
  const date = screen.getByLabelText(/Please provide expected leaving date/);
  expect(date).toHaveAttribute('placeholder', 'dd/mm/yyyy');
  expect(date).toHaveAttribute('type', 'text');
  const parent = screen.getByRole('radiogroup', { name: /full-time education or training prior to enrolment/ });
  expect(parent.parentElement!.parentElement!.nextElementSibling).toContainElement(date);
  type(/Please provide expected leaving date/, '2027-06-30');
  choose(/full-time education or training prior to enrolment/, 'No');
  expect(screen.queryByLabelText(/Please provide expected leaving date/)).not.toBeInTheDocument();
  const layout = resolveLayout(defaultLayout());
  expect(toPayload({ fullTimeEducationPrior: false, expectedLeavingDate: '2027-06-30' }, formSections(layout), null).expectedLeavingDate).toBeNull();
  choose(/full-time education or training prior to enrolment/, 'Yes');
  fireEvent.click(screen.getByRole('button', { name: /Submit application/ }));
  await heading('You Are Eligible to Proceed!');
  expect(submitApprenticeshipApplication).toHaveBeenCalledWith(expect.objectContaining({ fullTimeEducationPrior: true, expectedLeavingDate: '2027-06-30' }));
});

it('keeps the applicant on a section until its required answers are valid', async () => {
  await open();
  next();
  expect(screen.getByText('Section 1 of 3')).toBeInTheDocument();
  expect(screen.getByLabelText(/First Name/)).toHaveAttribute('aria-invalid', 'true');
  expect(screen.getByLabelText(/First Name/)).toHaveFocus();
  // Phone number is optional.
  expect(screen.getByLabelText(/Phone Number/)).toHaveAttribute('aria-invalid', 'false');

  fillPersonalDetails();
  type(/Work Email Address/, 'not-an-email');
  type(/Date of Birth/, '2027-01-01');
  next();
  expect(await screen.findByText('Enter a valid email address.')).toBeInTheDocument();
  expect(screen.getByText('The date cannot be in the future.')).toBeInTheDocument();
  expect(screen.getByText('Section 1 of 3')).toBeInTheDocument();
  // Address and applicant postcode are no longer part of Section 1.
  expect(screen.queryByLabelText(/Residential Address Line 1/)).not.toBeInTheDocument();
  expect(screen.queryByLabelText(/^\d+\. Residential Postcode /)).not.toBeInTheDocument();
  expect(lookupPostcode).not.toHaveBeenCalled();
});

it('uses the employment dropdown for all statuses and preserves paid answers while switching', async () => {
  await open();
  fillPersonalDetails();
  next();
  await heading('Employment');
  const status = screen.getByLabelText(/Your Employment Status/);

  fireEvent.change(status, { target: { value: 'In paid employment' } });
  type(/Start Date with Employer/, '2020-09-01');
  type(/Job Title/, 'Marketing Executive');
  expect(screen.queryByText(/Please provide the details of a representative who will support you with your application/)).not.toBeInTheDocument();
  expect(screen.queryByText('Apprenticeship Employment Requirements')).not.toBeInTheDocument();

  for (const value of [
    'Self employment',
    'Not in paid employment, looking for work and available to start work',
    'Not in paid employment, not looking for work and/or not available to start work',
  ]) {
    fireEvent.change(status, { target: { value } });
    expect(screen.getByText('Apprenticeship Employment Requirements')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Continue/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Previous/ })).toBeEnabled();
    expect(screen.queryByLabelText(/Start Date with Employer/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Employer Address Line 1/)).not.toBeInTheDocument();
  }

  fireEvent.change(status, { target: { value: 'In paid employment' } });
  expect(screen.getByRole('button', { name: /Continue/ })).toBeEnabled();
  expect(screen.queryByText('Apprenticeship Employment Requirements')).not.toBeInTheDocument();
  expect(screen.getByLabelText(/Start Date with Employer/)).toHaveValue('2020-09-01');
  expect(screen.getByLabelText(/Job Title/)).toHaveValue('Marketing Executive');
});

it('keeps the England-hours declaration at the end of published Employment questions', () => {
  const layout = defaultLayout();
  const employment = layout.sections.find(section => section.slug === 'employment')!;
  const declaration = employment.items.find(item => item.key === 'majorityHoursInEngland')!;
  employment.items = [declaration, ...employment.items.filter(item => item !== declaration)];
  const resolved = resolveLayout(layout);
  const resolvedEmployment = resolved.sections.find(section => section.slug === 'employment')!;
  const questionKeys = resolvedEmployment.items.filter(item => item.key !== 'note.representative' && item.key !== 'note.employmentRequirements').map(item => item.key);
  expect(questionKeys.at(-1)).toBe('majorityHoursInEngland');
});

it('blocks navigation and direct submission for every non-paid employment status', async () => {
  await open();
  fillPersonalDetails();
  next();
  await heading('Employment');
  const status = screen.getByLabelText(/Your Employment Status/);
  const form = screen.getByRole('form');
  for (const value of [
    'Self employment',
    'Not in paid employment, looking for work and available to start work',
    'Not in paid employment, not looking for work and/or not available to start work',
  ]) {
    fireEvent.change(status, { target: { value } });
    fireEvent.submit(form);
    expect(screen.getByText('Section 2 of 3')).toBeInTheDocument();
    expect(submitApprenticeshipApplication).not.toHaveBeenCalled();
  }
});

it('allows Previous and return to Employment so a restricted applicant can correct their status', async () => {
  await open();
  fillPersonalDetails(); next();
  await heading('Employment');
  fireEvent.change(screen.getByLabelText(/Your Employment Status/), { target: { value: 'Self employment' } });
  fireEvent.click(screen.getByRole('button', { name: /Previous/ }));
  await heading('Personal Details');
  expect(screen.getByLabelText(/First Name/)).toHaveValue('Sam');
  next();
  await heading('Employment');
  expect(screen.getByLabelText(/Your Employment Status/)).toHaveValue('Self employment');
  expect(screen.getByRole('button', { name: /Continue/ })).toBeDisabled();
  fillEmployment(); next();
  await heading('Residency');
});

it.each(['missing', 'hidden'] as const)('shows the exact linked restriction notice in a published layout with a %s notice', async state => {
  const layout = defaultLayout();
  const employment = layout.sections.find(section => section.slug === 'employment')!;
  if (state === 'missing') employment.items = employment.items.filter(item => item.key !== 'note.employmentRequirements');
  else employment.items.find(item => item.key === 'note.employmentRequirements')!.hidden = true;
  vi.mocked(fetchEligibilityForm).mockResolvedValue({ layout, version: 3 });
  await open();
  fillPersonalDetails(); next();
  await heading('Employment');
  fireEvent.change(screen.getByLabelText(/Your Employment Status/), { target: { value: 'Self employment' } });
  const notice = screen.getByRole('note');
  expect(notice).toHaveTextContent('Apprenticeship Employment Requirements');
  expect(within(notice).getByText('An apprenticeship is a paid job that combines employment with training. To start a government-funded apprenticeship in England, you must normally be employed and paid by an employer who can support your training.')).toBeInTheDocument();
  expect(within(notice).getByText('You cannot continue with this application at this stage.')).toBeInTheDocument();
  expect(notice.querySelector('p:last-child')?.textContent).toBe('If you expect to start paid employment, believe your circumstances may qualify, or have any questions, please contact our admissions team at office@kentbusinesscollege.org.');
  expect(within(notice).getByRole('link', { name: 'office@kentbusinesscollege.org' })).toHaveAttribute('href', 'mailto:office@kentbusinesscollege.org');
  const resolvedItems = resolveLayout(layout).sections.find(section => section.slug === 'employment')!.items;
  expect(resolvedItems[resolvedItems.findIndex(item => item.key === 'employmentStatus') + 1].key).toBe('note.employmentRequirements');
  fireEvent.change(screen.getByLabelText(/Your Employment Status/), { target: { value: 'In paid employment' } });
  expect(screen.queryByRole('note')).not.toBeInTheDocument();
  next();
  expect(screen.getByRole('heading', { name: 'Employment' })).toBeInTheDocument();
  expect(screen.getByLabelText(/Employer Representative Email/)).toHaveAttribute('aria-invalid', 'true');
  fillEmployment(); next();
  await heading('Residency');
});

it('shows where a postcode in England is, and says nothing under any other postcode', async () => {
  vi.mocked(lookupPostcode).mockImplementation(async (postcode: string) => (postcode.toUpperCase().startsWith('EH') ? EDINBURGH : EXETER));
  await open();
  fillPersonalDetails();
  next();
  await heading('Employment');
  fireEvent.change(screen.getByLabelText(/Your Employment Status/), { target: { value: 'In paid employment' } });
  type(/Employer Postcode/, 'eh1 1yz');
  fireEvent.blur(screen.getByLabelText(/Employer Postcode/));

  await vi.waitFor(() => expect(lookupPostcode).toHaveBeenCalledWith('eh1 1yz'));
  await vi.waitFor(() => expect(screen.queryByText(/Checking postcode/)).not.toBeInTheDocument());
  expect(screen.queryByText(/Edinburgh/)).not.toBeInTheDocument();
  expect(screen.queryByText(/outside England/)).not.toBeInTheDocument();
  expect(screen.getByLabelText(/Employer Postcode/)).toHaveAttribute('aria-invalid', 'false');
});

it('keeps background postcode feedback informational until Continue is attempted', async () => {
  vi.mocked(lookupPostcode).mockResolvedValueOnce({ status: 'notFound' }).mockResolvedValue({ status: 'unavailable' });
  await open();
  fillPersonalDetails();
  next();
  await heading('Employment');
  fireEvent.change(screen.getByLabelText(/Your Employment Status/), { target: { value: 'In paid employment' } });
  type(/Employer Postcode/, 'ZZ9 9ZZ');
  fireEvent.blur(screen.getByLabelText(/Employer Postcode/));
  await vi.waitFor(() => expect(lookupPostcode).toHaveBeenCalled());
  expect(screen.getByLabelText(/Employer Postcode/)).toHaveAttribute('aria-invalid', 'false');
  expect(screen.queryByText(/could not (find|check) this postcode/)).not.toBeInTheDocument();
});

async function reachEmployment(hours: 'Yes' | 'No' | null = 'Yes') {
  await open();
  fillPersonalDetails(); next();
  await heading('Employment');
  fillEmployment(hours);
}

function deferredCheck() {
  let resolve!: (value: PostcodeCheck) => void;
  const promise = new Promise<PostcodeCheck>(done => { resolve = done; });
  return { promise, resolve };
}

it.each([
  ['CT1 1AA', 'England'], ['CF10 1EP', 'Wales'], ['EH1 1YZ', 'Scotland'], ['BT1 5GS', 'Northern Ireland'],
])('verifies %s in %s on Continue without blocking by country', async (postcode, country) => {
  vi.mocked(lookupPostcode).mockResolvedValue({ ...EXETER, postcode, country, inEngland: country === 'England' });
  await reachEmployment();
  type(/Employer Postcode/, postcode);
  next();
  await heading('Residency');
  expect(lookupPostcode).toHaveBeenCalledWith(postcode);
  expect(submitApprenticeshipApplication).not.toHaveBeenCalled();
});

it.each([['', 'This field is required.'], ['ss', INVALID_POSTCODE]])('blocks Continue for employer postcode "%s" before lookup', async (postcode, message) => {
  await reachEmployment();
  type(/Employer Postcode/, postcode);
  next();
  expect(await screen.findByText(message)).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Employment' })).toBeInTheDocument();
  expect(screen.getByLabelText(/Employer Postcode/)).toHaveAttribute('aria-invalid', 'true');
  expect(lookupPostcode).not.toHaveBeenCalled();
});

it('checks required fields before starting Continue verification', async () => {
  await reachEmployment();
  type(/Employer Representative Email/, '');
  next();
  expect(await screen.findByText('This field is required.')).toBeInTheDocument();
  expect(lookupPostcode).not.toHaveBeenCalled();
});

it('blocks a nonexistent postcode and clears its error after editing', async () => {
  vi.mocked(lookupPostcode).mockResolvedValueOnce({ status: 'notFound' }).mockResolvedValue(EXETER);
  await reachEmployment();
  type(/Employer Postcode/, 'ZZ9 9ZZ'); next();
  expect(await screen.findByText(UNKNOWN_POSTCODE)).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Employment' })).toBeInTheDocument();
  type(/Employer Postcode/, 'EX1 2MP');
  expect(screen.queryByText(UNKNOWN_POSTCODE)).not.toBeInTheDocument();
  next(); await heading('Residency');
});

it('offers retry for unavailable verification without treating the postcode as invalid', async () => {
  vi.mocked(lookupPostcode).mockResolvedValueOnce({ status: 'unavailable' }).mockResolvedValue(EXETER);
  await reachEmployment(); next();
  expect(await screen.findByText(EMPLOYER_POSTCODE_UNAVAILABLE)).toBeInTheDocument();
  expect(screen.queryByText(UNKNOWN_POSTCODE)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Continue/ })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'Retry postcode verification' }));
  await heading('Residency');
  expect(lookupPostcode).toHaveBeenCalledTimes(2);
});

it('shares blur and Continue verification, disables repeated clicks, and reuses a successful result', async () => {
  const pending = deferredCheck();
  vi.mocked(lookupPostcode).mockReturnValue(pending.promise);
  await reachEmployment();
  fireEvent.blur(screen.getByLabelText(/Employer Postcode/));
  next();
  const button = screen.getByRole('button', { name: 'Verifying postcode…' });
  expect(button).toBeDisabled();
  fireEvent.click(button);
  fireEvent.submit(screen.getByRole('form'));
  expect(lookupPostcode).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('button', { name: /Previous/ })).toBeEnabled();
  await act(async () => pending.resolve(EXETER));
  await heading('Residency');
  fireEvent.click(screen.getByRole('button', { name: /Previous/ }));
  await heading('Employment'); next(); await heading('Residency');
  expect(lookupPostcode).toHaveBeenCalledTimes(1);
});

it('requires fresh verification when a previously verified postcode changes', async () => {
  await reachEmployment(); next(); await heading('Residency');
  fireEvent.click(screen.getByRole('button', { name: /Previous/ }));
  await heading('Employment');
  vi.mocked(lookupPostcode).mockResolvedValue({ status: 'notFound' });
  type(/Employer Postcode/, 'ZZ9 9ZZ');
  expect(screen.queryByText('Exeter, South West, England')).not.toBeInTheDocument();
  next();
  expect(await screen.findByText(UNKNOWN_POSTCODE)).toBeInTheDocument();
  expect(lookupPostcode).toHaveBeenCalledWith('ZZ9 9ZZ');
  expect(screen.getByRole('heading', { name: 'Employment' })).toBeInTheDocument();
});

it('ignores a lookup that finishes after the applicant changes postcode', async () => {
  const pending = deferredCheck();
  vi.mocked(lookupPostcode).mockReturnValueOnce(pending.promise).mockResolvedValue({ status: 'notFound' });
  await reachEmployment(); next();
  type(/Employer Postcode/, 'ZZ9 9ZZ');
  expect(screen.getByRole('button', { name: /Continue/ })).toBeEnabled();
  await act(async () => pending.resolve(EXETER));
  expect(screen.getByRole('heading', { name: 'Employment' })).toBeInTheDocument();
  next();
  expect(await screen.findByText(UNKNOWN_POSTCODE)).toBeInTheDocument();
});

it('does not advance when Previous is used during postcode verification', async () => {
  const pending = deferredCheck();
  vi.mocked(lookupPostcode).mockReturnValue(pending.promise);
  await reachEmployment(); next();
  fireEvent.click(screen.getByRole('button', { name: /Previous/ }));
  await heading('Personal Details');
  await act(async () => pending.resolve(EXETER));
  expect(screen.getByRole('heading', { name: 'Personal Details' })).toBeInTheDocument();
});

it('allows a No hours declaration to complete initial screening as Not Eligible', async () => {
  vi.mocked(submitApprenticeshipApplication).mockResolvedValue({ eligible: false, reasons: ['The 50% working-hours requirement is not met.'], outcome: 'notEligible' });
  await reachEmployment('No'); next(); await heading('Residency'); fillResidency();
  fireEvent.click(screen.getByRole('button', { name: /Submit application/ }));
  await heading('You Are Not Currently Eligible');
  expect(screen.getByText('Reason: The 50% working-hours requirement is not met.')).toBeInTheDocument();
  expect(submitApprenticeshipApplication).toHaveBeenCalledWith(expect.objectContaining({ majorityHoursInEngland: false }));
});

it('keeps a missing working-hours answer required before postcode verification', async () => {
  await reachEmployment(null); next();
  expect(await screen.findByText('This field is required.')).toBeInTheDocument();
  expect(screen.getByRole('radiogroup', { name: /50% of your working hours in England/ })).toHaveAttribute('aria-invalid', 'true');
  expect(lookupPostcode).not.toHaveBeenCalled();
});

async function reachWorkplacePreview(layout: EligibilityLayout | null = null) {
  vi.mocked(fetchEligibilityForm).mockResolvedValue({ layout, version: null, workplaceEnabled: true });
  await reachEmployment();
}
const arrangeWorkplace = (value: string) => type(/Is your employer's address also your usual place of work/, value);

it('keeps workplace preview off by default even when a saved layout tries to enable it', async () => {
  const layout = defaultLayout(true);
  vi.mocked(fetchEligibilityForm).mockResolvedValue({ layout, version: null });
  await reachEmployment();
  expect(screen.queryByLabelText(/Is your employer's address also/)).not.toBeInTheDocument();
  expect(screen.queryByLabelText(/Main Workplace Postcode/)).not.toBeInTheDocument();
  const sections = formSections(resolveLayout(layout));
  expect(toPayload({ workplaceArrangement: 'different', mainWorkplacePostcode: 'ss' }, sections, null)).not.toHaveProperty('workplaceArrangement');
});

it('shows exact workplace choices after Employer Postcode and requires an explicit answer', async () => {
  await reachWorkplacePreview();
  const select = screen.getByLabelText(/Is your employer's address also/);
  expect(within(select).getAllByRole('option').slice(1).map(option => option.textContent)).toEqual(Object.values(WORKPLACE_LABELS));
  expect(within(select).getAllByRole('option').slice(1).map(option => (option as HTMLOptionElement).value)).toEqual(['same', 'different', 'remote_hybrid_multiple']);
  const labels = [...document.querySelectorAll('label')].map(label => label.textContent);
  const position = labels.findIndex(label => label?.includes('Employer Postcode'));
  expect(labels[position + 1]).toContain("Is your employer's address also your usual place of work?");
  next();
  expect(await screen.findByText('This field is required.')).toBeInTheDocument();
  expect(lookupPostcode).not.toHaveBeenCalled();
  arrangeWorkplace('same');
  expect(screen.queryByLabelText(/Main Workplace Postcode/)).not.toBeInTheDocument();
  next(); await heading('Residency');
  expect(lookupPostcode).toHaveBeenCalledTimes(1);
});

it.each([
  ['CT1 1AA', 'England'], ['CF10 1EP', 'Wales'], ['EH1 1YZ', 'Scotland'], ['BT1 5GS', 'Northern Ireland'],
])('verifies a different workplace in %s (%s) without changing country decisions at Continue', async (postcode, country) => {
  await reachWorkplacePreview();
  arrangeWorkplace('different');
  expect(screen.getByLabelText(/Main Workplace Postcode/)).toHaveAttribute('aria-required', 'true');
  next();
  expect(await screen.findByText('This field is required.')).toBeInTheDocument();
  vi.mocked(lookupPostcode).mockImplementation(async value => value === postcode ? { ...EXETER, postcode, country, inEngland: country === 'England' } : EXETER);
  type(/Main Workplace Postcode/, postcode); next();
  await heading('Residency');
  expect(lookupPostcode).toHaveBeenCalledWith(postcode);
});

it('requires a verifiable usual workplace for remote/hybrid screening without declaring ineligibility', async () => {
  await reachWorkplacePreview(); arrangeWorkplace('remote_hybrid_multiple'); next();
  expect(await screen.findByText('This field is required.')).toBeInTheDocument();
  expect(screen.getByLabelText(/Main Workplace Postcode/)).toHaveAttribute('aria-required', 'true');
  expect(lookupPostcode).not.toHaveBeenCalled();
  expect(screen.queryByRole('heading', { name: /Eligible/ })).not.toBeInTheDocument();
  type(/Main Workplace Postcode/, 'CT1 1AA'); next(); await heading('Residency');
  expect(lookupPostcode).toHaveBeenCalledTimes(2);
});

it('discards stale postcode answers and validation errors when arrangement changes', async () => {
  await reachWorkplacePreview(); arrangeWorkplace('different');
  type(/Main Workplace Postcode/, 'ss'); next();
  expect(await screen.findByText(INVALID_POSTCODE)).toBeInTheDocument();
  arrangeWorkplace('same');
  expect(screen.queryByText(INVALID_POSTCODE)).not.toBeInTheDocument();
  expect(screen.queryByLabelText(/Main Workplace Postcode/)).not.toBeInTheDocument();
  arrangeWorkplace('remote_hybrid_multiple');
  expect(screen.getByLabelText(/Main Workplace Postcode/)).toHaveValue('');
  type(/Main Workplace Postcode/, 'CT1 1AA');
  arrangeWorkplace('different');
  expect(screen.getByLabelText(/Main Workplace Postcode/)).toHaveValue('');
});

it('blocks an unknown workplace postcode and offers retry when its lookup fails', async () => {
  vi.mocked(lookupPostcode).mockImplementation(async value => value === 'ZZ9 9ZZ' ? { status: 'notFound' } : value === 'CT1 1AA' ? { status: 'unavailable' } : EXETER);
  await reachWorkplacePreview(); arrangeWorkplace('different');
  type(/Main Workplace Postcode/, 'ZZ9 9ZZ'); next();
  expect(await screen.findByText(UNKNOWN_POSTCODE)).toBeInTheDocument();
  type(/Main Workplace Postcode/, 'CT1 1AA'); next();
  expect(await screen.findByText(WORKPLACE_POSTCODE_UNAVAILABLE)).toBeInTheDocument();
  expect(screen.queryByText(UNKNOWN_POSTCODE)).not.toBeInTheDocument();
  vi.mocked(lookupPostcode).mockResolvedValue(EXETER);
  fireEvent.click(screen.getByRole('button', { name: 'Retry postcode verification' }));
  await heading('Residency');
});

it('invalidates pending workplace verification after editing the location or arrangement', async () => {
  const pending = deferredCheck();
  vi.mocked(lookupPostcode).mockImplementation(value => value === 'CT1 1AA' ? pending.promise : Promise.resolve(EXETER));
  await reachWorkplacePreview(); arrangeWorkplace('different');
  type(/Main Workplace Postcode/, 'CT1 1AA'); next();
  await vi.waitFor(() => expect(lookupPostcode).toHaveBeenCalledWith('CT1 1AA'));
  expect(screen.getByRole('button', { name: 'Verifying postcode…' })).toBeDisabled();
  arrangeWorkplace('remote_hybrid_multiple');
  await act(async () => pending.resolve(EXETER));
  expect(screen.getByRole('heading', { name: 'Employment' })).toBeInTheDocument();
  expect(screen.getByLabelText(/Main Workplace Postcode/)).toHaveValue('');
  type(/Main Workplace Postcode/, 'EX1 2MP');
  next(); await heading('Residency');
});

it('restores workplace questions in an old published layout without mutating it and sends only answers', async () => {
  const layout = defaultLayout();
  const original = JSON.stringify(layout);
  await reachWorkplacePreview(layout);
  arrangeWorkplace('different'); type(/Main Workplace Postcode/, 'CT1 1AA');
  const sections = formSections(resolveLayout(layout, true));
  const body = toPayload({ employmentStatus: 'In paid employment', workplaceArrangement: 'same', mainWorkplacePostcode: 'stale' }, sections, null);
  expect(body).toMatchObject({ workplaceArrangement: 'same', mainWorkplacePostcode: '' });
  expect(body).not.toHaveProperty('main_workplace_country');
  expect(body).not.toHaveProperty('assessment_status');
  expect(JSON.stringify(layout)).toBe(original);
});

it('submits preview answers through the existing API without location or assessment metadata', async () => {
  vi.mocked(submitApprenticeshipApplication).mockResolvedValue(ELIGIBLE);
  await reachWorkplacePreview(); arrangeWorkplace('different');
  type(/Main Workplace Postcode/, 'CT1 1AA'); next();
  await heading('Residency'); fillResidency();
  fireEvent.click(screen.getByRole('button', { name: /Submit application/ }));
  await heading('You Are Eligible to Proceed!'); // Phase 1 outcome handling remains unchanged.
  const body = vi.mocked(submitApprenticeshipApplication).mock.calls[0][0];
  expect(body).toMatchObject({ workplaceArrangement: 'different', mainWorkplacePostcode: 'CT1 1AA' });
  expect(body).not.toHaveProperty('assessment_status');
  expect(body).not.toHaveProperty('main_workplace_country');
});

it.each(['same', 'different', 'remote_hybrid_multiple'])('submits a No hours answer as Not Eligible for %s', async arrangement => {
  const reason = 'You do not expect to spend at least 50% of your working hours in England.';
  vi.mocked(submitApprenticeshipApplication).mockResolvedValue({
    eligible: false, reasons: [reason], outcome: 'notEligible', assessmentStatus: 'not_eligible', assessmentReason: reason,
  });
  await reachWorkplacePreview(); arrangeWorkplace(arrangement);
  if (arrangement !== 'same') type(/Main Workplace Postcode/, 'CT1 1AA');
  choose(/50% of your working hours in England/, 'No'); next();
  await heading('Residency'); fillResidency();
  fireEvent.click(screen.getByRole('button', { name: /Submit application/ }));
  await vi.waitFor(() => expect(screen.getByRole('heading', { name: 'You Are Not Currently Eligible' })).toHaveFocus());
  expect(screen.getByText(`Reason: ${reason}`)).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'office@kentbusinesscollege.org' })).toHaveAttribute('href', 'mailto:office@kentbusinesscollege.org');
  expect(screen.queryByRole('heading', { name: /pending assessment/i })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Submit application/ })).not.toBeInTheDocument();
  expect(vi.mocked(submitApprenticeshipApplication).mock.calls[0][0]).toMatchObject({ majorityHoursInEngland: false, workplaceArrangement: arrangement });
});

it('describes the initial screening pass and activation email with concise applicant messaging', async () => {
  vi.mocked(submitApprenticeshipApplication).mockResolvedValue({ ...ELIGIBLE, assessmentStatus: 'provisional_eligible', assessmentReason: null });
  await reachWorkplacePreview(); arrangeWorkplace('same'); next();
  await heading('Residency'); fillResidency();
  fireEvent.click(screen.getByRole('button', { name: /Submit application/ }));
  expect(await heading('You Are Eligible to Proceed!')).toBeInTheDocument();
  expect(screen.getByText('Thank you for completing your application.')).toBeInTheDocument();
  expect(screen.getByText('Based on the information you have provided, you have successfully passed our initial eligibility screening and can proceed to the next stage of your apprenticeship application.')).toBeInTheDocument();
  expect(screen.getByText('You will receive an email shortly with instructions to activate your account and begin the enrolment process.')).toBeInTheDocument();
  expect(screen.queryByText(/This initial screening pass is not final funding approval|Final funding eligibility is subject to verification|Admissions must also verify/)).not.toBeInTheDocument();
});

it('shows the backend Not Eligible result with an age-specific reason for a two-day-old DOB', async () => {
  vi.setSystemTime(new Date('2026-11-03T12:00:00Z'));
  vi.mocked(submitApprenticeshipApplication).mockResolvedValue({ eligible: false, outcome: 'notEligible', reasons: [APPLICATION_AGE_REASON], assessmentStatus: 'not_eligible' });
  await open(); fillPersonalDetails(); type(/Date of Birth/, '2026-11-01'); next();
  await heading('Employment'); fillEmployment(); type(/Start Date with Employer/, '2026-11-02'); next();
  await heading('Residency'); fillResidency(); choose(/resident in the UK and Islands/, 'No'); type(/How many full years/, '0');
  fireEvent.click(screen.getByRole('button', { name: /Submit application/ }));
  await heading('You Are Not Currently Eligible');
  expect(screen.getByText(`Reason: ${APPLICATION_AGE_REASON}`)).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'office@kentbusinesscollege.org' })).toHaveAttribute('href', 'mailto:office@kentbusinesscollege.org');
  expect(screen.queryByRole('heading', { name: 'You Are Eligible to Proceed!' })).not.toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: /pending assessment/i })).not.toBeInTheDocument();
});

it('shows one concise under-16 notice without repeating policy text and preserves progression to screening', async () => {
  await open();
  expect(screen.getByText('Enter your date of birth.')).toBeInTheDocument();
  expect(screen.queryByText(/application-stage screening policy/)).not.toBeInTheDocument();
  fillPersonalDetails(); type(/Date of Birth/, '2011-11-04');
  expect(screen.getAllByText(APPLICATION_AGE_NOTICE, { exact: true })).toHaveLength(1);
  expect(screen.queryByText('Enter your date of birth.')).not.toBeInTheDocument();
  expect(screen.queryByText(APPLICATION_AGE_REASON)).not.toBeInTheDocument();
  type(/Date of Birth/, '1995-04-03');
  expect(screen.queryByText(APPLICATION_AGE_NOTICE)).not.toBeInTheDocument();
  expect(screen.getByText('Enter your date of birth.')).toBeInTheDocument();
  type(/Date of Birth/, '2011-11-04');
  next(); await heading('Employment');
});

it('does not restore lengthy DOB instructions from an older published layout', async () => {
  const layout = defaultLayout();
  layout.texts = { 'dateOfBirth.hint': 'Our application-stage screening policy requires you to be at least 16. Admissions must verify your age at apprenticeship start.' };
  vi.mocked(fetchEligibilityForm).mockResolvedValue({ layout, version: 7 });
  await open();
  expect(screen.getByText('Enter your date of birth.')).toBeInTheDocument();
  expect(screen.queryByText(/Our application-stage screening policy/)).not.toBeInTheDocument();
});

it('blocks employment before birth inline and permits a valid planned employment date', async () => {
  await open(); fillPersonalDetails(); next(); await heading('Employment'); fillEmployment();
  type(/Start Date with Employer/, '1990-01-01'); next();
  expect(await screen.findByText('Enter a date after your date of birth.')).toBeInTheDocument();
  expect(screen.getByLabelText(/Start Date with Employer/)).toHaveAttribute('aria-invalid', 'true');
  expect(screen.getByLabelText(/Start Date with Employer/)).toHaveFocus();
  type(/Start Date with Employer/, '2027-01-01');
  expect(screen.getByLabelText(/Start Date with Employer/)).not.toHaveAttribute('max');
  expect(screen.getByText(/Admissions must confirm the planned employment arrangements/)).toBeInTheDocument();
  next(); await heading('Residency');
});

it('shows impossible residency duration as an inline correction rather than an eligibility outcome', async () => {
  await open(); fillPersonalDetails(); type(/Date of Birth/, '2020-01-01'); next();
  await heading('Employment'); fillEmployment(); type(/Start Date with Employer/, '2025-01-01'); next();
  await heading('Residency'); fillResidency(); choose(/resident in the UK and Islands/, 'No');
  type(/How many full years/, '9'); fireEvent.click(screen.getByRole('button', { name: /Submit application/ }));
  expect(await screen.findByText('The number of full years in the UK cannot exceed your age. Please check your answers.')).toBeInTheDocument();
  expect(submitApprenticeshipApplication).not.toHaveBeenCalled();
  expect(screen.getByLabelText(/How many full years/)).toHaveFocus();
});

it('restores mandatory workplace screening questions from a hidden or optional Employment layout', async () => {
  const layout = defaultLayout();
  const employment = layout.sections.find(section => section.slug === 'employment')!;
  employment.hidden = true;
  for (const item of employment.items) { item.hidden = true; item.required = false; }
  vi.mocked(fetchEligibilityForm).mockResolvedValue({ layout, version: null, workplaceEnabled: true });
  await open(); fillPersonalDetails(); next();
  await heading('Employment');
  expect(screen.getByLabelText(/Your Employment Status/)).toHaveAttribute('aria-required', 'true');
  fireEvent.change(screen.getByLabelText(/Your Employment Status/), { target: { value: 'In paid employment' } });
  expect(screen.getByLabelText(/Employer Postcode/)).toHaveAttribute('aria-required', 'true');
  expect(screen.getByRole('radiogroup', { name: /50% of your working hours in England/ })).toHaveAttribute('aria-required', 'true');
  expect(screen.getByLabelText(/Is your employer's address also/)).toHaveAttribute('aria-required', 'true');
  next();
  expect(submitApprenticeshipApplication).not.toHaveBeenCalled();
});

it.each(['approved', 'rejected', 'more_evidence_required'] as const)('keeps a historical %s review informational without a new final result', async status => {
  const message = status === 'approved' ? 'Your assessment has passed. No invitation has been created.' : 'Please contact admissions about this assessment.';
  vi.mocked(submitApprenticeshipApplication).mockResolvedValue({
    eligible: status === 'rejected' ? false : null, reasons: [], outcome: status === 'rejected' ? 'notEligible' : 'pendingAssessment',
    review: { status, version: 1, message },
  });
  await submitApplication();
  expect(await screen.findByText(message)).toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent(message);
  expect(screen.getByRole('link', { name: 'office@kentbusinesscollege.org' })).toHaveAttribute('href', 'mailto:office@kentbusinesscollege.org');
  expect(screen.queryByRole('heading', { name: /Eligible|pending assessment/i })).not.toBeInTheDocument();
  expect(screen.queryByText(/We have sent an email/)).not.toBeInTheDocument();
});

it('keeps an original pending receipt informational and does not reclassify it', async () => {
  vi.mocked(submitApprenticeshipApplication).mockResolvedValue({ eligible: null, reasons: [], outcome: 'pendingAssessment', assessmentStatus: 'pending_assessment' });
  await submitApplication();
  expect(await screen.findByText(/Your previously submitted application is awaiting assessment/)).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: /Eligible|pending assessment/i })).not.toBeInTheDocument();
});

it('requires a malformed UK residential postcode to be corrected before submission', async () => {
  await open(); await reachResidency();
  type(/^\d+\. Residential Postcode /, 'bad');
  fireEvent.click(screen.getByRole('button', { name: /Submit application/ }));
  expect(await screen.findByText(INVALID_POSTCODE)).toBeInTheDocument();
  expect(screen.getByLabelText(/^\d+\. Residential Postcode /)).toHaveAttribute('aria-invalid', 'true');
  expect(submitApprenticeshipApplication).not.toHaveBeenCalled();
});

it('shows the actual independent screening reason without inventing a workplace failure', async () => {
  const reason = 'Your residency eligibility has not been established.';
  vi.mocked(submitApprenticeshipApplication).mockResolvedValue({ eligible: false, reasons: [reason], outcome: 'notEligible' });
  await submitApplication();
  await heading('You Are Not Currently Eligible');
  expect(screen.getByText(`Reason: ${reason}`)).toBeInTheDocument();
  expect(screen.queryByText(/verified usual workplace.*outside England/)).not.toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'office@kentbusinesscollege.org' })).toHaveAttribute('href', 'mailto:office@kentbusinesscollege.org');
  expect(screen.getByText(/circumstances may qualify for an exception/)).toBeInTheDocument();
});

it('keeps final postcode verification failures retryable without an eligibility result', async () => {
  vi.mocked(submitApprenticeshipApplication).mockRejectedValue(new ApplicationSubmitError('Postcode verification is temporarily unavailable. Please retry.', 503, { employerPostcode: 'Postcode verification is temporarily unavailable. Please retry.' }));
  await submitApplication();
  expect(await screen.findByRole('alert')).toHaveTextContent('temporarily unavailable');
  expect(screen.getByRole('heading', { name: 'Employment' })).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: /Eligible|pending assessment/i })).not.toBeInTheDocument();
});

it('tells the applicant they are not eligible when the server rules them out by postcode', async () => {
  const reasons = ['Your postcode (EH1 1YZ) is outside England (Scotland).'];
  vi.mocked(submitApprenticeshipApplication).mockResolvedValue({ eligible: false, reasons, outcome: 'notEligible' });
  await open();
  await reachResidency();
  fireEvent.click(screen.getByRole('button', { name: /Submit application/ }));

  await vi.waitFor(() => expect(screen.getByRole('heading', { name: 'You Are Not Currently Eligible' })).toHaveFocus());
  expect(screen.getByText(`Reason: ${reasons[0]}`)).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'Application submitted' })).not.toBeInTheDocument();
  expect(submitApprenticeshipApplication).toHaveBeenCalledTimes(1);
});

async function submitApplication() {
  await open();
  await reachResidency();
  fireEvent.click(screen.getByRole('button', { name: /Submit application/ }));
}

it('tells an eligible applicant their set-password invitation has been emailed', async () => {
  vi.mocked(submitApprenticeshipApplication).mockResolvedValue(ELIGIBLE);
  await submitApplication();
  expect(await heading('You Are Eligible to Proceed!')).toHaveFocus();
  expect(screen.getByText(/with a link to create your password/)).toBeInTheDocument();
  expect(screen.getByText('sam@employer.example')).toBeInTheDocument();
});

it('says staff will send the link when the invitation email could not be sent', async () => {
  vi.mocked(submitApprenticeshipApplication).mockResolvedValue({ eligible: true, reasons: [], outcome: 'invitePending' });
  await submitApplication();
  expect(await heading('You Are Eligible to Proceed!')).toBeInTheDocument();
  expect(screen.getByText(/could not send your invitation email just now/)).toBeInTheDocument();
  expect(screen.queryByText(/We have sent an email/)).not.toBeInTheDocument();
});

it('sends an applicant who already has an account to sign in or Forgot password', async () => {
  vi.mocked(submitApprenticeshipApplication).mockResolvedValue({ eligible: true, reasons: [], outcome: 'existingAccount' });
  await submitApplication();
  expect(await heading('You Are Eligible to Proceed!')).toHaveFocus();
  expect(screen.getByRole('link', { name: 'sign in' })).toHaveAttribute('href', '/login');
  expect(screen.getByRole('link', { name: 'Forgot password' })).toHaveAttribute('href', '/forgot-password');
});

it('keeps the form and asks the applicant to try again when the postcodes could not be checked', async () => {
  vi.mocked(submitApprenticeshipApplication).mockResolvedValue({ eligible: null, reasons: [], outcome: 'retryLater' });
  await submitApplication();
  expect(await screen.findByText(/please try again in a few minutes/)).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: /eligible|Application submitted/ })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Submit application/ })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: /Previous/ }));
  await heading('Employment');
  fireEvent.click(screen.getByRole('button', { name: /Previous/ }));
  expect(screen.getByLabelText(/Choose Your Applying Programme/)).toHaveValue('Marketing Manager Level 6');
});

it('keeps an indeterminate legacy response retryable without a third result screen', async () => {
  vi.mocked(submitApprenticeshipApplication).mockResolvedValue({ eligible: null, reasons: [], outcome: null });
  await submitApplication();
  expect(await screen.findByRole('alert')).toHaveTextContent('please try again');
  expect(screen.queryByRole('heading', { name: /Eligible|Application submitted/ })).not.toBeInTheDocument();
});

it('returns to earlier sections with the answers kept', async () => {
  await open();
  fillPersonalDetails();
  next();
  await heading('Employment');
  fireEvent.click(screen.getByRole('button', { name: /Previous/ }));
  expect(screen.getByLabelText(/First Name/)).toHaveValue('Sam');
  expect(screen.getByLabelText(/Date of Birth/)).toHaveValue('1995-04-03');
});

it('does not send a completion date once the answer is changed to No', async () => {
  vi.mocked(submitApprenticeshipApplication).mockResolvedValue(ELIGIBLE);
  await open();
  fillPersonalDetails(); next();
  await heading('Employment');
  fillEmployment(); next();
  await heading('Residency');
  fillResidency();
  choose(/government-funded training programmes/, 'Yes');
  type(/When was it completed/, '2026-03-01');
  choose(/government-funded training programmes/, 'No');
  fireEvent.click(screen.getByRole('button', { name: /Submit application/ }));
  await heading('You Are Eligible to Proceed!');
  expect(vi.mocked(submitApprenticeshipApplication).mock.calls[0][0]).toMatchObject({
    attendedFundedTraining: false, fundedTrainingCompletedOn: null,
  });
});

it('shows a failed submission and sends the applicant to the answer the server refused', async () => {
  vi.mocked(submitApprenticeshipApplication).mockRejectedValue(
    new ApplicationSubmitError('Please correct the highlighted answers.', 400, { workEmail: 'Enter a valid email address.' }),
  );
  await open();
  await reachResidency();
  fireEvent.click(screen.getByRole('button', { name: /Submit application/ }));

  expect(await screen.findByRole('alert')).toHaveTextContent('Please correct the highlighted answers.');
  expect(screen.getByText('Section 1 of 3')).toBeInTheDocument();
  expect(screen.getByLabelText(/Work Email Address/)).toHaveAttribute('aria-invalid', 'true');
  expect(screen.queryByRole('heading', { name: 'Application submitted' })).not.toBeInTheDocument();
});

/** The standard form with the builder's changes: as the public endpoint would return it. */
function publishedLayout(): EligibilityLayout {
  const layout = defaultLayout();
  const [personal, employment, residency] = layout.sections;
  personal.title = 'About you';
  personal.items.find(item => item.key === 'dateOfBirth')!.hidden = true;
  personal.items.push(
    { key: 'cf_line_manager', builtin: false, hidden: false, required: true, label: 'Do you have a line manager?', type: 'yesno', column: 'custom_line_manager' },
    {
      key: 'cf_manager_name', builtin: false, hidden: false, required: true, label: "Your manager's name", type: 'text', column: 'custom_manager_name',
      condition: { field: 'cf_line_manager', values: ['Yes'] },
    },
  );
  employment.hidden = true;
  residency.hidden = true;
  personal.items.find(item => item.key === 'programme')!.options = ['Data Analyst Level 4'];
  layout.texts = {
    'firstName.label': 'Given name',
    'page.title': 'Apply for an apprenticeship',
    'page.intro': 'Assess learner eligibility against the England apprenticeship funding rules. All fields marked with * are required.',
  };
  return layout;
}

it('asks the questions of the published form', async () => {
  vi.mocked(submitApprenticeshipApplication).mockResolvedValue(ELIGIBLE);
  vi.mocked(fetchEligibilityForm).mockResolvedValue({ layout: publishedLayout(), version: 3 });
  await open();

  expect(screen.getByRole('heading', { name: 'Apply for an apprenticeship' })).toBeInTheDocument();
  expect(screen.queryByText('All fields marked with * are required.')).not.toBeInTheDocument();
  expect(screen.getByText('Section 1 of 1')).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'About you' })).toBeInTheDocument();
  expect(screen.getByLabelText(/Date of Birth/)).toHaveAttribute('aria-required', 'true');
  type(/Given name/, 'Sam');
  type(/Surname/, 'Example');
  type(/Title/, 'Mr');
  type(/Work Email Address/, 'sam@employer.example');
  type(/Date of Birth/, '1995-04-03');
  expect(screen.queryByLabelText(/Residential Address Line 1/)).not.toBeInTheDocument();
  expect(screen.queryByLabelText(/^\d+\. Residential Postcode /)).not.toBeInTheDocument();
  // The follow-up is asked only after a Yes, and is then required.
  expect(screen.queryByLabelText(/Your manager's name/)).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText(/Choose Your Applying Programme/), { target: { value: 'Data Analyst Level 4' } });
  choose(/Do you have a line manager/, 'Yes');
  fireEvent.click(screen.getByRole('button', { name: /Submit application/ }));
  expect(await screen.findByText('This field is required.')).toBeInTheDocument();
  type(/Your manager's name/, 'Alex');
  expect(within(screen.getByLabelText(/Choose Your Applying Programme/)).getAllByRole('option').map(o => o.textContent))
    .toEqual(['Please select', 'Data Analyst Level 4']);
  fireEvent.change(screen.getByLabelText(/Choose Your Applying Programme/), { target: { value: 'Data Analyst Level 4' } });
  fireEvent.click(screen.getByRole('button', { name: /Submit application/ }));

  await heading('You Are Eligible to Proceed!');
  expect(vi.mocked(submitApprenticeshipApplication).mock.calls[0][0]).toMatchObject({
    firstName: 'Sam', dateOfBirth: '1995-04-03', programme: 'Data Analyst Level 4',
    custom: { cf_line_manager: true, cf_manager_name: 'Alex' },
    layoutVersion: 3,
  });
});

it('reloads a form republished while it was being filled in, keeping the answers', async () => {
  vi.mocked(submitApprenticeshipApplication).mockRejectedValueOnce(
    new ApplicationSubmitError('This form was updated while you were filling it in.', 409, {}, true),
  );
  vi.mocked(fetchEligibilityForm)
    .mockResolvedValueOnce({ layout: null, version: null })
    .mockResolvedValueOnce({ layout: publishedLayout(), version: 3 });
  await open();
  await reachResidency();
  fireEvent.click(screen.getByRole('button', { name: /Submit application/ }));

  expect(await screen.findByRole('alert')).toHaveTextContent('This form was updated while you were filling it in.');
  expect(fetchEligibilityForm).toHaveBeenCalledTimes(2);
  expect(screen.getByText('Section 1 of 1')).toBeInTheDocument();
  expect(screen.getByLabelText(/Given name/)).toHaveValue('Sam');
  expect(screen.getByRole('radiogroup', { name: /Do you have a line manager/ })).toBeInTheDocument();
});

it('offers to try again when the form cannot be loaded', async () => {
  vi.mocked(fetchEligibilityForm)
    .mockRejectedValueOnce(new Error('The application form is not available just now.'))
    .mockResolvedValueOnce({ layout: null, version: null });
  render(<MemoryRouter><Page /></MemoryRouter>);
  expect(await screen.findByRole('alert')).toHaveTextContent('The application form is not available just now.');
  fireEvent.click(screen.getByRole('button', { name: /Try again/ }));
  expect(await screen.findByText('Section 1 of 3')).toBeInTheDocument();
});

it('is a public route that the signed-in activity recorder skips', () => {
  // Public routes are the top-level entries; everything else sits under RequireAuth.
  expect(routes.some(route => 'path' in route && route.path === '/apprenticeship_application')).toBe(true);
  expect(isRecordedPath('/apprenticeship_application')).toBe(false);
});
