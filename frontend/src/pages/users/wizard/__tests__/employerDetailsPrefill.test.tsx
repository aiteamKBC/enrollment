/**
 * Extended ILR — Employer Details are prefilled from the learner's employer and
 * organisation, filling only blanks and only once the saved answers are in.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import type { IlrForm } from '../../types';

const api = vi.hoisted(() => ({ fetchIlrEmployerDetails: vi.fn() }));
vi.mock('@/api/extendedIlr', () => api);
const wizard = vi.hoisted(() => ({ employer: {} as IlrForm['employer'], hydrated: true, setSection: vi.fn() }));
vi.mock("../WizardContext.helpers", () => ({
  useWizard: () => ({
    userId: '41',
    isCommercial: false,
    hydrated: wizard.hydrated,
    draft: { ilr: { employer: wizard.employer } },
    setSection: wizard.setSection,
  }),
}));

import { useEmployerDetailsPrefill } from '../steps/useEmployerDetailsPrefill';

const EMPTY: IlrForm['employer'] = {
  organisationName: '', postcode: '', address: '', city: '', lineManagerName: '', lineManagerEmail: '', lineManagerPhone: '',
};
const RECORD: IlrForm['employer'] = {
  organisationName: 'Example Ltd', postcode: 'CT1 1AA', address: '1 Office Park', city: 'Canterbury',
  lineManagerName: 'Pat Manager', lineManagerEmail: 'pat@example.com', lineManagerPhone: '',
};

beforeEach(() => {
  vi.clearAllMocks();
  wizard.hydrated = true;
  api.fetchIlrEmployerDetails.mockResolvedValue(RECORD);
});

describe('Employer Details prefill', () => {
  it('fills empty fields from the learner record', async () => {
    wizard.employer = EMPTY;
    renderHook(() => useEmployerDetailsPrefill());

    await waitFor(() => expect(wizard.setSection).toHaveBeenCalledTimes(1));
    expect(api.fetchIlrEmployerDetails).toHaveBeenCalledWith('apprenticeship', '41');
    expect(wizard.setSection).toHaveBeenCalledWith('ilr', { employer: RECORD });
  });

  it('never overwrites what the learner already typed or saved', async () => {
    wizard.employer = { ...EMPTY, organisationName: 'My Own Employer', lineManagerEmail: 'me@example.com' };
    renderHook(() => useEmployerDetailsPrefill());

    await waitFor(() => expect(wizard.setSection).toHaveBeenCalledTimes(1));
    const written = wizard.setSection.mock.calls[0][1].employer;
    expect(written.organisationName).toBe('My Own Employer');
    expect(written.lineManagerEmail).toBe('me@example.com');
    expect(written.postcode).toBe('CT1 1AA');
    expect(written.lineManagerName).toBe('Pat Manager');
  });

  it('waits for the saved answers before filling anything', async () => {
    wizard.employer = EMPTY;
    wizard.hydrated = false;
    const { rerender } = renderHook(() => useEmployerDetailsPrefill());
    await waitFor(() => expect(api.fetchIlrEmployerDetails).toHaveBeenCalled());
    await Promise.resolve();
    expect(wizard.setSection).not.toHaveBeenCalled();

    wizard.hydrated = true;
    rerender();
    await waitFor(() => expect(wizard.setSection).toHaveBeenCalledTimes(1));
  });

  it('writes nothing when every field is already answered', async () => {
    wizard.employer = { ...RECORD, lineManagerPhone: '07000 000000' };
    renderHook(() => useEmployerDetailsPrefill());

    await waitFor(() => expect(api.fetchIlrEmployerDetails).toHaveBeenCalled());
    await Promise.resolve();
    expect(wizard.setSection).not.toHaveBeenCalled();
  });
});
