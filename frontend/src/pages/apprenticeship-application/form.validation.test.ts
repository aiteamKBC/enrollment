import { expect, it } from 'vitest';
import { APPLICATION_AGE_NOTICE, dateNotice, formSections, isValidDate, meetsApplicationAge, validateSection, type Answers } from './form';
import { defaultLayout, resolveLayout } from './layout/resolve';

const today = '2026-11-03';
const sections = formSections(defaultLayout(true));
const validate = (slug: string, changes: Answers) => validateSection(sections.find(section => section.slug === slug)!, {
  dateOfBirth: '1995-04-03', employmentStatus: 'In paid employment', residentPrev3Years: true, ...changes,
}, today, sections);

it.each(['2026-02-29', '2026-04-31', '2026-13-01', '2026-00-01', '2026-01-00', '20261101', '2026-W44-7', '2026-2-01', 'bad', '1899-12-31'])(
  'rejects malformed or impossible DOB %s', value => {
    expect(isValidDate(value)).toBe(false);
    expect(validate('personal-details', { dateOfBirth: value }).dateOfBirth).toBe('Enter a valid date.');
  },
);

it('accepts leap days, rejects future DOB and distinguishes under-age screening from date validation', () => {
  expect(validate('personal-details', { dateOfBirth: '2000-02-29' })).not.toHaveProperty('dateOfBirth');
  expect(validate('personal-details', { dateOfBirth: '2026-11-04' }).dateOfBirth).toBe('The date cannot be in the future.');
  expect(validate('personal-details', { dateOfBirth: '2026-11-01' })).not.toHaveProperty('dateOfBirth');
  expect(dateNotice('dateOfBirth', { dateOfBirth: '2026-11-01' }, today)).toBe(APPLICATION_AGE_NOTICE);
  expect(dateNotice('dateOfBirth', { dateOfBirth: '2010-11-03' }, today)).toBeUndefined();
  expect(dateNotice('dateOfBirth', { dateOfBirth: '2010-11-04' }, today)).toBe(APPLICATION_AGE_NOTICE);
});

it.each([
  ['2010-11-04', false], ['2010-11-03', true], ['1995-04-03', true], ['1900-01-01', true], ['2026-11-01', false],
  ['2026-11-04', null], ['2026-02-29', null], ['bad', null],
])('calculates frontend application-age guidance at the birthday boundary for %s', (birth, expected) => {
  expect(meetsApplicationAge(birth, today)).toBe(expected);
});

it('checks the 16th birthday for a leap-day DOB', () => {
  expect(meetsApplicationAge('2008-02-29', '2024-02-28')).toBe(false);
  expect(meetsApplicationAge('2008-02-29', '2024-02-29')).toBe(true);
});

it('checks employment chronology and allows planned future dates with an Admissions notice', () => {
  expect(validate('employment', { startDateWithEmployer: '1995-04-03' }).startDateWithEmployer).toBe('Enter a date after your date of birth.');
  expect(validate('employment', { startDateWithEmployer: '1990-01-01' })).toHaveProperty('startDateWithEmployer');
  expect(validate('employment', { startDateWithEmployer: '2026-02-30' }).startDateWithEmployer).toBe('Enter a valid date.');
  expect(validate('employment', { startDateWithEmployer: '2027-02-28' })).not.toHaveProperty('startDateWithEmployer');
  expect(dateNotice('startDateWithEmployer', { startDateWithEmployer: '2027-02-28' }, today)).toMatch(/confirm the planned employment/);
});

it('checks leaving and funded-training completion dates against DOB only while shown', () => {
  expect(validate('residency', { fullTimeEducationPrior: true, expectedLeavingDate: '1990-01-01' }).expectedLeavingDate).toBe('Enter a date after your date of birth.');
  expect(validate('residency', { attendedFundedTraining: true, fundedTrainingCompletedOn: '1990-01-01' }).fundedTrainingCompletedOn).toBe('Enter a date after your date of birth.');
  expect(validate('residency', { fullTimeEducationPrior: false, expectedLeavingDate: '1990-01-01' })).not.toHaveProperty('expectedLeavingDate');
});

it('checks impossible residency duration without equating nationality, country or immigration answers', () => {
  expect(validate('residency', { dateOfBirth: '2023-11-04' })).toHaveProperty('residentPrev3Years');
  expect(validate('residency', { dateOfBirth: '2023-11-03' })).not.toHaveProperty('residentPrev3Years');
  expect(validate('residency', { dateOfBirth: '2017-11-04', residentPrev3Years: false, yearsInUk: '9' })).toHaveProperty('yearsInUk');
  expect(validate('residency', { dateOfBirth: '2017-11-03', residentPrev3Years: false, yearsInUk: '9' })).not.toHaveProperty('yearsInUk');
  expect(validate('residency', { dateOfBirth: '2016-11-04', residentPrev3Years: false, yearsInUk: '10 or more' })).toHaveProperty('yearsInUk');
  expect(validate('residency', { residentPrev3Years: false, yearsInUk: '10 or more', nationality: 'France', countryOfResidence: 'United Kingdom', livesInUk: true, residencyStatus: 'UK Citizen' })).not.toHaveProperty('yearsInUk');
});

it('restores required DOB from a hidden or optional published field without rewriting the layout', () => {
  const layout = defaultLayout(true);
  layout.sections[0].items.find(item => item.key === 'dateOfBirth')!.hidden = true;
  layout.sections[0].items.find(item => item.key === 'dateOfBirth')!.required = false;
  layout.sections[0].hidden = true;
  const parts = formSections(resolveLayout(layout, true));
  expect(parts[0].items.find(item => item.key === 'dateOfBirth')).toMatchObject({ required: true });
  expect(validateSection(parts[0], { dateOfBirth: '' }, today, parts)).toHaveProperty('dateOfBirth');
  expect(layout.sections[0].hidden).toBe(true);
});
