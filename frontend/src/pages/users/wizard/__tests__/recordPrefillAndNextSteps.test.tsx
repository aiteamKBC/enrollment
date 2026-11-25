/**
 * What an apprentice already gave before the wizard opened is offered back, and
 * Next Steps is not ticked off before the learner has reached it.
 *
 * A new apprentice enters their phone, address, date of birth and a reusable
 * signature on their first sign-in (learner_api/first_login_details.py). The
 * board carries those, and Personal Details fills its *blanks* from them —
 * never overwriting an answer already given in the wizard — and the filled-in
 * values are written on the next save rather than only shown.
 */
import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

const fetchExtendedIlr = vi.fn();
const saveExtendedIlr = vi.fn();
const pdfApi = vi.hoisted(() => ({ preview: vi.fn(), import: vi.fn() }));
vi.mock('@/api/plrPdfPreview', () => ({ previewPlrPdf: pdfApi.preview, importPlrPdf: pdfApi.import }));

vi.mock('@/api/extendedIlr', () => ({
  fetchExtendedIlr: (...args: unknown[]) => fetchExtendedIlr(...args),
  saveExtendedIlr: (...args: unknown[]) => saveExtendedIlr(...args),
  peekExtendedIlr: () => undefined,
}));
vi.mock('@/api/enrolmentDocuments', () => ({ uploadEnrolmentDocument: vi.fn() }));
// The Inclusion step's status comes from the Safeguarding database; not answered yet.
vi.mock('@/api/wizardInclusion', () => ({
  fetchInclusionStatus: vi.fn(async () => ({ applicable: true, completed: false, respondedAt: null })),
  SAFEGUARDING_URL: 'https://safeguarding.example/',
}));
const fetchKsbProfile = vi.fn();
vi.mock('@/api/curriculum', () => ({
  fetchKsbProfile: (...args: unknown[]) => fetchKsbProfile(...args),
  peekKsbProfile: () => undefined,
}));

import { ToastProvider } from '@/hooks/useToast';
import { WizardProvider } from '../WizardContext';
import { useWizard } from "../WizardContext.helpers";
import { WizardShell } from '../WizardShell';
import { DEFAULT_LAYOUT, visibleSteps } from '../layout/resolve';
import { missingForStep } from '../validation';
import type { EnrolmentBoard } from '../../types';

const SIGNATURE = 'data:image/png;base64,SAVED';

function board(contact: Partial<EnrolmentBoard['contact']> = {}): EnrolmentBoard {
  return {
    user: { id: '20', name: 'Test Learner', reference: 'REF20', owner: '' },
    contact: { email: 'test@example.com', phone: '', dob: '', groupMembership: '', hasMandate: false, ...contact },
    programme: { name: 'Test Programme', cohort: '', type: '', status: 'Onboarding', startDate: '', endDate: '', enrolledAt: '', enrolledBy: '', onboardingStatus: 'In progress' },
  } as unknown as EnrolmentBoard;
}

const FROM_FIRST_SIGN_IN = {
  phone: '07123 456789',
  address: '1 High Street, Canterbury, Kent, CT1 1AA',
  dob: '15/03/1990',
  savedSignature: SIGNATURE,
  savedSignatureDate: '2026-09-20',
};

let latest: ReturnType<typeof useWizard> | null = null;
function Probe() {
  latest = useWizard();
  return null;
}

function renderWizard(b: EnrolmentBoard, { isCommercial = false, currentIndex = 2, mode = 'learner' as 'learner' | 'staff' } = {}) {
  render(
    <ToastProvider>
      <WizardProvider userId="20" board={b} isCommercial={isCommercial} readOnlyLearnerSteps={mode === 'staff'}>
        <WizardShell currentIndex={currentIndex} mode={mode} onNavigateStep={vi.fn()} onFinish={vi.fn()} />
        <Probe />
      </WizardProvider>
    </ToastProvider>
  );
}

beforeAll(() => {
  (globalThis as Record<string, unknown>).AppIcon = ({ className }: { className?: string }) => <i className={className} />;
});

beforeEach(() => {
  vi.clearAllMocks();
  latest = null;
  fetchExtendedIlr.mockResolvedValue({ answers: null, draft: null, meta: { updatedAt: '' } });
  saveExtendedIlr.mockResolvedValue({ meta: { updatedAt: '2026-09-27T00:00:00Z' } });
  fetchKsbProfile.mockResolvedValue({ results: [] });
});

describe('legacy PLR enrolment regression', () => {
  it('imports from the preview, fills ULN automatically and preserves pending manual additions', async () => {
    const existing = { id: 'manual', subject: 'Existing manual award', placeOfStudy: 'Synthetic College', qualificationType: 'GCSE', grade: '5', credits: 0, recordType: 'Manual' };
    const imported = { id: 'imported', subject: 'Imported withdrawn event', credits: null, recordType: 'Imported',
      sourceSection: 'Learning Events', completionStatus: 'Withdrawn', outcomeStatus: 'No Achievement' };
    const response = { imported: true, plr: { uln: '0123456789', records: [existing, imported] }, added: 1, skipped: 0,
      message: 'PLR import complete. 1 records imported and 0 records skipped.' };
    fetchExtendedIlr.mockResolvedValue({ answers: null, draft: { plr: { uln: '', records: [existing] } }, meta: { updatedAt: 'saved', canEditUln: true } });
    pdfApi.preview.mockResolvedValue({ previewOnly: true, uln: '0123456789', learnerName: 'Synthetic Learner', dateOfBirth: '1994-03-12',
      identityVerified: true, records: [], counts: { learningEvents: 1, achievements: 0 }, warnings: [] });
    pdfApi.import.mockResolvedValue(response);
    const currentIndex = visibleSteps(DEFAULT_LAYOUT).findIndex((step) => step.slug === 'plr');
    renderWizard(board(), { currentIndex, mode: 'staff' });
    await screen.findByText('Existing manual award');
    act(() => latest!.setSection('plr', { ...latest!.draft.plr, records: [...latest!.draft.plr.records,
      { ...existing, id: 'pending', subject: 'Unsaved manual addition' }] }));
    fireEvent.change(screen.getByLabelText('Select PLR PDF'), { target: { files: [new File(['%PDF'], 'synthetic.pdf', { type: 'application/pdf' })] } });
    const modal = await screen.findByRole('dialog', { name: 'PLR PDF preview' });
    fireEvent.click(within(modal).getByRole('button', { name: 'Import Records' }));
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'ULN' })).toHaveValue('0123456789'));
    expect(await screen.findByText('Imported withdrawn event')).toBeInTheDocument();
    expect(screen.getByText('Learning Events · Withdrawn · No Achievement')).toBeInTheDocument();
    expect(screen.getByText('Unsaved manual addition')).toBeInTheDocument();
    expect(await screen.findByRole('status')).toHaveTextContent('1 records imported');
    expect(saveExtendedIlr).not.toHaveBeenCalled();
    saveExtendedIlr.mockResolvedValue({ draft: { plr: { ...response.plr, records: [...response.plr.records,
      { ...existing, id: 'pending', subject: 'Unsaved manual addition' }] } }, meta: { updatedAt: 'saved-manual' } });
    await act(async () => { await latest!.saveIlr(); });
    expect(saveExtendedIlr.mock.calls[0][3].plr.records.map((record: any) => record.id)).toEqual(['manual', 'pending']);
    expect(saveExtendedIlr.mock.calls[0][4]).not.toHaveProperty('ulnUpdate');
    expect(latest!.draft.plr.records.some((record) => record.id === 'imported')).toBe(true);
  });

  it('does not clear an imported ULN when an older Save progress request is queued', async () => {
    const canonical = { uln: '0123456789', records: [{ id: 'imported', subject: 'Imported award', recordType: 'Imported', credits: null }] };
    fetchExtendedIlr.mockResolvedValue({ answers: null, draft: { plr: { uln: '', records: [] } }, meta: { updatedAt: 'saved', canEditUln: true } });
    let finish!: (value: unknown) => void;
    pdfApi.import.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    saveExtendedIlr.mockResolvedValue({ draft: { plr: canonical }, meta: { updatedAt: 'updated' } });
    const currentIndex = visibleSteps(DEFAULT_LAYOUT).findIndex((step) => step.slug === 'plr');
    renderWizard(board(), { currentIndex, mode: 'staff' });
    await waitFor(() => expect(latest!.canEditUln).toBe(true));
    let imported!: Promise<unknown>; let saving!: Promise<void>;
    act(() => { imported = latest!.importPlrPdf(new File(['synthetic'], 'synthetic.pdf'), '', new AbortController().signal); });
    await waitFor(() => expect(pdfApi.import).toHaveBeenCalled());
    act(() => { saving = latest!.saveIlr(); });
    await act(async () => { finish({ imported: true, plr: canonical, added: 1, skipped: 0, message: 'Imported' }); await imported; await saving; });
    expect(saveExtendedIlr.mock.calls[0][4]).not.toHaveProperty('ulnUpdate');
    expect(latest!.draft.plr.uln).toBe('0123456789');
    expect(latest!.draft.plr.records).toEqual(canonical.records);
  });

  it('keeps ordinary enrolment saving functional after a failed PDF import', async () => {
    fetchExtendedIlr.mockResolvedValue({ answers: null, draft: { plr: { uln: '', records: [] } }, meta: { updatedAt: 'saved', canEditUln: true } });
    pdfApi.import.mockRejectedValueOnce(new Error('Synthetic import failure'));
    const currentIndex = visibleSteps(DEFAULT_LAYOUT).findIndex((step) => step.slug === 'plr');
    renderWizard(board(), { currentIndex, mode: 'staff' });
    await waitFor(() => expect(latest!.canEditUln).toBe(true));
    await expect(latest!.importPlrPdf!(new File(['synthetic'], 'synthetic.pdf'), '', new AbortController().signal)).rejects.toThrow('Synthetic import failure');
    act(() => latest!.setSection('personalDetails', { ...latest!.draft.personalDetails, firstName: 'Still editable' }));
    await act(async () => { await latest!.saveIlr(); });
    expect(saveExtendedIlr).toHaveBeenCalledTimes(1);
    expect(latest!.draft.plr).toMatchObject({ uln: '', records: [] });
    expect(latest!.draft.personalDetails.firstName).toBe('Still editable');
  });
  it('applies explicit Admin edit/delete responses to the shared wizard state and preserves ULN', async () => {
    const record = { id: 'selected', placeOfStudy: 'Synthetic College', qualificationType: 'GCSE', subject: 'Maths',
      level: '2', awardDate: '2022-08-20', startDate: '', endDate: '', credits: 0, grade: '5', recordType: 'Manual' };
    const other = { ...record, id: 'unrelated', subject: 'English' };
    fetchExtendedIlr.mockResolvedValue({ answers: null, draft: { plr: { uln: '0123456789', records: [record, other] } },
      meta: { updatedAt: 'saved', canEditUln: true } });
    saveExtendedIlr.mockImplementation(async (_kind, _id, _answers, draft, options) => {
      const change = options.plrRecordChange;
      const records = change.action === 'delete' ? draft.plr.records.filter((r: any) => r.id !== change.id)
        : draft.plr.records.map((r: any) => r.id === change.id ? change.record : r);
      return { draft: { ...draft, plr: { ...draft.plr, records } }, meta: { updatedAt: 'updated', canEditUln: true } };
    });
    const currentIndex = visibleSteps(DEFAULT_LAYOUT).findIndex((step) => step.slug === 'plr');
    renderWizard(board(), { currentIndex, mode: 'staff' });
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Maths' }));
    const modal = screen.getByRole('dialog', { name: 'Edit PLR' });
    const grade = within(modal).getByText('Grade').closest('label')!.querySelector('input')!;
    expect(grade).toHaveValue('5');
    fireEvent.change(grade, { target: { value: '6' } });
    fireEvent.click(within(modal).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(latest!.draft.plr.records[0].grade).toBe('6');
    expect(latest!.draft.plr.records[1]).toEqual(other);
    fireEvent.click(screen.getByRole('button', { name: 'Delete Maths' }));
    const confirm = screen.getByRole('dialog', { name: 'Delete personal learning record' });
    expect(saveExtendedIlr).toHaveBeenCalledTimes(1);
    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(latest!.draft.plr.records).toEqual([other]));
    expect(latest!.draft.plr.uln).toBe('0123456789');
    expect(screen.queryByText('Maths')).not.toBeInTheDocument();
    expect(screen.getByText('English')).toBeInTheDocument();
  });

  it('shows the Admin ULN, validates it and saves explicit updates through the existing API', async () => {
    fetchExtendedIlr.mockResolvedValue({ answers: null, draft: { plr: { uln: '0123456789', records: [] } },
      meta: { updatedAt: 'saved', canEditUln: true } });
    saveExtendedIlr.mockImplementation(async (_kind, _id, _answers, draft) => ({ draft, meta: { updatedAt: 'updated', canEditUln: true } }));
    const currentIndex = visibleSteps(DEFAULT_LAYOUT).findIndex((step) => step.slug === 'plr');
    renderWizard(board(), { currentIndex, mode: 'staff' });
    const input = await screen.findByRole('textbox', { name: 'ULN' });
    expect(input).toHaveValue('0123456789');
    expect(input).toHaveAttribute('placeholder', 'Enter ULN');
    expect(input).toHaveAttribute('maxlength', '10');
    expect(screen.getByText('There are no personal learning records.')).toBeInTheDocument();
    fireEvent.change(input, { target: { value: '123abc' } });
    expect(input).toHaveValue('123');
    expect(input).toHaveAttribute('aria-invalid', 'true');
    await expect(latest!.saveIlr()).rejects.toThrow('exactly 10 digits');
    expect(saveExtendedIlr).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: '0012345678999' } });
    expect(input).toHaveValue('0012345678');
    await act(async () => { await latest!.saveIlr(); });
    expect(saveExtendedIlr.mock.calls[0][0]).toBe('apprenticeship');
    expect(saveExtendedIlr.mock.calls[0][1]).toBe('20');
    expect(saveExtendedIlr.mock.calls[0][4].ulnUpdate).toEqual({ value: '0012345678', expected: '0123456789' });
    act(() => latest!.setSection('personalDetails', { ...latest!.draft.personalDetails, firstName: 'Unrelated edit' }));
    await act(async () => { await latest!.saveIlr(); });
    expect(saveExtendedIlr.mock.calls[1][4]).not.toHaveProperty('ulnUpdate');
    fireEvent.change(input, { target: { value: '' } });
    await act(async () => { await latest!.saveIlr(); });
    expect(saveExtendedIlr.mock.calls[2][4].ulnUpdate).toEqual({ value: '', expected: '0012345678' });
    expect(missingForStep(currentIndex, latest!.draft)).toEqual([]);
  });

  it('shows an empty Admin ULN without making it or qualifications mandatory', async () => {
    fetchExtendedIlr.mockResolvedValue({ answers: null, draft: { plr: { uln: '', records: [] } },
      meta: { updatedAt: 'saved', canEditUln: true } });
    const currentIndex = visibleSteps(DEFAULT_LAYOUT).findIndex((step) => step.slug === 'plr');
    renderWizard(board(), { currentIndex, mode: 'staff' });
    expect(await screen.findByRole('textbox', { name: 'ULN' })).toHaveValue('');
    expect(missingForStep(currentIndex, latest!.draft)).toEqual([]);
  });

  for (const mode of ['learner', 'staff'] as const) {
    it(`hides the ULN in the ${mode === 'learner' ? 'Student interface even with Admin permission' : 'unauthorized staff interface'}`, async () => {
      fetchExtendedIlr.mockResolvedValue({ answers: null, draft: { plr: { uln: '0123456789', records: [] } },
        meta: { updatedAt: 'saved', canEditUln: mode === 'learner' } });
      const currentIndex = visibleSteps(DEFAULT_LAYOUT).findIndex((step) => step.slug === 'plr');
      renderWizard(board(), { currentIndex, mode });
      await waitFor(() => expect(latest!.hydrated).toBe(true));
      expect(screen.queryByRole('textbox', { name: 'ULN' })).not.toBeInTheDocument();
      expect(screen.getByText('There are no personal learning records.')).toBeInTheDocument();
    });
  }

  for (const mode of ['learner', 'staff'] as const) {
    it(`isolates a PLR outage, protects saves and retries without losing other edits in the ${mode} wizard`, async () => {
      const loadError = 'Personal Learning Record could not be loaded. Please retry.';
      fetchExtendedIlr.mockResolvedValue({
        answers: null, draft: { plr: { loadError }, personalDetails: { firstName: 'Existing', lastName: 'Learner' } },
        meta: { updatedAt: 'saved' },
      });
      const currentIndex = visibleSteps(DEFAULT_LAYOUT).findIndex((step) => step.slug === 'plr');
      renderWizard(board(), { currentIndex, mode });
      expect(await screen.findByRole('alert')).toHaveTextContent(loadError);
      expect(screen.queryByText('There are no personal learning records.')).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Add' })).not.toBeInTheDocument();
      await waitFor(() => expect(latest!.ready).toBe(true));
      expect(latest!.draft.personalDetails.firstName).toBe('Existing');
      expect(missingForStep(currentIndex, latest!.draft)).toEqual([loadError]);
      act(() => latest!.setSection('plr', { uln: '', records: [] }));
      expect(latest!.draft.plr.loadError).toBe(loadError);
      act(() => latest!.setSection('personalDetails', { ...latest!.draft.personalDetails, firstName: 'Edited elsewhere' }));
      await act(async () => { await latest!.saveIlr(); });
      expect(saveExtendedIlr.mock.calls[0][3]).not.toHaveProperty('plr');
      expect(saveExtendedIlr.mock.calls[0][3].personalDetails.firstName).toBe('Edited elsewhere');
      await expect(latest!.saveIlr({ submitted: true })).rejects.toThrow(loadError);
      fetchExtendedIlr.mockRejectedValueOnce(new Error('network unavailable'));
      await act(async () => { latest!.plrCheck!.refresh(); });
      expect(screen.getByRole('alert')).toHaveTextContent(loadError);
      fetchExtendedIlr.mockResolvedValueOnce({ answers: null,
        draft: { plr: { uln: '0123456789', records: [{ id: 'recovered', subject: 'Recovered qualification', credits: 0 }] } },
        meta: { updatedAt: 'recovered' },
      });
      await act(async () => { screen.getByRole('button', { name: 'Retry' }).click(); });
      expect(await screen.findByText('Recovered qualification')).toBeInTheDocument();
      expect(fetchExtendedIlr).toHaveBeenLastCalledWith('apprenticeship', '20', { force: true });
      expect(latest!.draft.personalDetails.firstName).toBe('Edited elsewhere');
      expect(latest!.draft.plr.loadError).toBeUndefined();
      expect(missingForStep(currentIndex, latest!.draft)).toEqual([]);
    });
    it(`loads existing qualifications and saves through Extended ILR in the ${mode} wizard`, async () => {
      const plr = { uln: '0123456789', records: [{
        id: 'saved-qualification', placeOfStudy: 'Synthetic College', qualificationType: 'GCSE',
        subject: 'Synthetic Mathematics', level: '2', awardDate: '2022-08-20',
        credits: 0, grade: '5', recordType: 'Manual',
      }] };
      fetchExtendedIlr.mockResolvedValue({
        answers: null, draft: { plr, personalDetails: { firstName: 'Existing', lastName: 'Learner' } },
        meta: { updatedAt: '2026-09-26T10:00:00Z' },
      });
      const currentIndex = visibleSteps(DEFAULT_LAYOUT).findIndex((step) => step.slug === 'plr');
      expect(currentIndex).toBeGreaterThanOrEqual(0);
      renderWizard(board(), { currentIndex, mode });
      expect(await screen.findByText('Synthetic Mathematics')).toBeInTheDocument();
      expect(screen.queryByText(/Could not refresh PLR/)).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Import PLR PDF' })).not.toBeInTheDocument();
      act(() => latest!.setSection('plr', {
        ...latest!.draft.plr, records: latest!.draft.plr.records.map((r) => ({ ...r, grade: '6' })),
      }));
      await act(async () => { await latest!.saveIlr(); });
      expect(saveExtendedIlr).toHaveBeenCalledTimes(1);
      const saved = saveExtendedIlr.mock.calls[0][3];
      expect(saved.plr).toEqual({ ...plr, records: [{ ...plr.records[0], grade: '6' }] });
      expect(saved.personalDetails.firstName).toBe('Existing');
    });
  }
});

describe('Personal Details pre-fill from the learner record', () => {
  it('fills a new apprentice’s phone, address, date of birth and signature', async () => {
    renderWizard(board(FROM_FIRST_SIGN_IN));
    await waitFor(() => expect(fetchExtendedIlr).toHaveBeenCalled());

    const pd = latest!.draft.personalDetails;
    expect(pd.phone).toBe('07123 456789');
    expect(pd.address).toBe('1 High Street, Canterbury, Kent, CT1 1AA');
    expect(pd.dob).toBe('1990-03-15');
    expect(pd.age).toBeGreaterThan(30);
    expect(pd.signature).toBe(SIGNATURE);
    expect(pd.signatureDate).toBe('2026-09-20');
  });

  it('fills only the blanks of a saved draft, keeping what was answered in the wizard', async () => {
    fetchExtendedIlr.mockResolvedValue({
      answers: null,
      draft: {
        personalDetails: {
          firstName: 'Test', lastName: 'Learner', email: 'test@example.com',
          phone: '07999 000111', address: '', dob: '', sex: 'Female',
        },
      },
      meta: { updatedAt: '2026-09-26T10:00:00Z' },
    });
    renderWizard(board(FROM_FIRST_SIGN_IN));

    await waitFor(() => expect(latest!.draft.personalDetails.sex).toBe('Female'));
    const pd = latest!.draft.personalDetails;
    expect(pd.phone).toBe('07999 000111');
    expect(pd.address).toBe('1 High Street, Canterbury, Kent, CT1 1AA');
    expect(pd.dob).toBe('1990-03-15');
    expect(pd.signature).toBe(SIGNATURE);
  });

  it('writes the filled-in values on the next save instead of treating them as already saved', async () => {
    fetchExtendedIlr.mockResolvedValue({
      answers: null,
      draft: { personalDetails: { firstName: 'Test', lastName: 'Learner', email: 'test@example.com', phone: '', address: '', dob: '', sex: 'Female' } },
      meta: { updatedAt: '2026-09-26T10:00:00Z' },
    });
    renderWizard(board(FROM_FIRST_SIGN_IN));
    await waitFor(() => expect(latest!.draft.personalDetails.address).not.toBe(''));

    await latest!.saveIlr();

    expect(saveExtendedIlr).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(saveExtendedIlr.mock.calls[0])).toContain('1 High Street, Canterbury, Kent, CT1 1AA');
    expect(JSON.stringify(saveExtendedIlr.mock.calls[0])).toContain(SIGNATURE);
  });

  it('leaves a commercial learner’s form alone', async () => {
    renderWizard(board({ address: '1 High Street', savedSignature: SIGNATURE }), { isCommercial: true });
    await waitFor(() => expect(fetchExtendedIlr).toHaveBeenCalled());

    expect(latest!.draft.personalDetails.address).toBe('');
    expect(latest!.draft.personalDetails.signature).toBeUndefined();
  });
});

describe('Next Steps in the step rail', () => {
  it('is locked and not counted as complete before the earlier steps are done', async () => {
    renderWizard(board());
    const tab = await screen.findByRole('tab', { name: 'What Happens Now?' });

    await waitFor(() => expect(within(tab).getByText('Locked')).toBeInTheDocument());
    expect(within(tab).queryByText('Complete')).toBeNull();
    expect(tab.querySelector('.ri-check-line')).toBeNull();
    // Only the Introduction, Before You Begin, the Personal Learning Record
    // (nothing required) and the Skills Radar (no competencies on this
    // programme), plus unconfigured suitability, count so far — Next Steps
    // would make it 6. The Inclusion
    // assessment is not answered.
    expect(screen.getByText('6 of 13 steps complete')).toBeInTheDocument();
  });

  it('shows its number rather than a tick for staff, who are not gated', async () => {
    renderWizard(board(), { mode: 'staff' });
    const tab = await screen.findByRole('tab', { name: 'What Happens Now?' });

    await waitFor(() => expect(within(tab).getByText('Not started')).toBeInTheDocument());
    expect(within(tab).getByText('13')).toBeInTheDocument();
    expect(tab.querySelector('.ri-check-line')).toBeNull();
  });

  it('does not count as complete just by being opened while earlier steps are unfinished', async () => {
    renderWizard(board(), { mode: 'staff', currentIndex: 12 });
    const tab = await screen.findByRole('tab', { name: 'What Happens Now?' });

    await waitFor(() => expect(within(tab).getByText('In progress')).toBeInTheDocument());
    expect(tab.querySelector('.ri-check-line')).toBeNull();
  });
});
