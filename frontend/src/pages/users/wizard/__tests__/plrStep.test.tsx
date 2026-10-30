/**
 * Personal Learning Record — the step is just "Add" and the table; entries are
 * added and edited in the Add/Edit PLR form, with certificates stored as files.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AppIcon } from '@/components/feature/AppIcon';
import type { PlrRecord, WizardDraft } from '../../types';
import { WIZARD_STEPS } from '../../types';
import { missingForStep } from '../validation';

const api = vi.hoisted(() => ({
  fetchPlrEvidence: vi.fn(),
  uploadPlrEvidence: vi.fn(),
  deletePlrEvidence: vi.fn(),
  getPlrEvidenceUrl: vi.fn(),
}));
vi.mock('@/api/plrEvidence', () => api);
const wizard = vi.hoisted(() => ({ records: [] as PlrRecord[], setSection: vi.fn() }));
vi.mock("../WizardContext.helpers", () => ({
  useWizard: () => ({
    userId: '41',
    isCommercial: false,
    draft: { plr: { uln: '', records: wizard.records } },
    setSection: wizard.setSection,
  }),
}));
vi.mock("@/hooks/useToast.helpers", () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }));

import Plr from '../steps/Plr';

const GCSE: PlrRecord = {
  id: 'rec-1', placeOfStudy: 'Canterbury College', qualificationType: 'GCSE', subject: 'Maths', level: '2',
  startDate: '2018-09-01', endDate: '2020-06-30', awardDate: '2020-08-20', credits: 0, grade: '5', recordType: 'Manual',
};
const dialog = () => screen.getByRole('dialog');
const combos = () => within(dialog()).getAllByRole('combobox');
const box = (label: string) => within(dialog()).getByText(label).closest('label')!.querySelector('input')!;

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('AppIcon', AppIcon);
  wizard.records = [];
  api.fetchPlrEvidence.mockResolvedValue([]);
});

describe('Personal Learning Record step', () => {
  it('shows only Add and the table: no ULN, Get PLR or Export', () => {
    render(<Plr />);

    expect(screen.getByRole('button', { name: 'Add' })).toBeInTheDocument();
    expect(screen.queryByText('ULN')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Get PLR/ })).not.toBeInTheDocument();
    expect(screen.queryByText('Export to CSV')).not.toBeInTheDocument();
    expect(screen.getByText('There are no personal learning records.')).toBeInTheDocument();
    expect(screen.getByText('Certificate / Evidence')).toBeInTheDocument();
  });

  it('is complete without a ULN or any records', () => {
    const step = WIZARD_STEPS.findIndex((s) => s.slug === 'plr');
    expect(missingForStep(step, { plr: { uln: '', records: [] } } as unknown as WizardDraft)).toEqual([]);
  });

  it('will not save an entry with required fields missing', async () => {
    render(<Plr />);
    await userEvent.click(screen.getByRole('button', { name: 'Add' }));
    await userEvent.click(within(dialog()).getByRole('button', { name: 'Save' }));

    expect(wizard.setSection).not.toHaveBeenCalled();
    for (const message of ['Please choose a qualification type.', 'Please enter the place of study.', 'Please choose a subject.',
      'Please enter the level.', 'Please enter the award date.', 'Please enter the grade.']) {
      expect(within(dialog()).getByText(message)).toBeInTheDocument();
    }
  });

  it('adds a manual entry, storing the typed text when Other is chosen', async () => {
    render(<Plr />);
    await userEvent.click(screen.getByRole('button', { name: 'Add' }));
    const [qualType, subject] = combos();
    expect(within(qualType).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Select…', 'Degree', 'Higher Education', 'Access to HE', 'A Level', 'AS Level', 'BTEC', 'GCSE', 'CSE',
      'O-Level', 'NVQ', 'Key Skill', 'Functional Skill', 'Short course', 'Overseas Qualification', 'Basic Skill', 'Other',
    ]);
    expect(within(subject).getAllByRole('option').map((o) => o.textContent)).toEqual(['Select…', 'Maths', 'English', 'ICT', 'Other']);

    await userEvent.selectOptions(qualType, 'GCSE');
    await userEvent.type(box('Place of Study'), 'Canterbury College');
    await userEvent.selectOptions(subject, 'Other');
    await userEvent.click(within(dialog()).getByRole('button', { name: 'Save' }));
    expect(within(dialog()).getByText('Please specify the subject.')).toBeInTheDocument();

    await userEvent.type(box('Please specify the subject'), 'Geography');
    await userEvent.type(box('Level'), '2');
    await userEvent.type(box('Award date'), '2020-08-20');
    await userEvent.type(box('Grade'), '6');
    await userEvent.click(within(dialog()).getByRole('button', { name: 'Save' }));

    expect(wizard.setSection).toHaveBeenCalledWith('plr', expect.objectContaining({
      records: [expect.objectContaining({
        qualificationType: 'GCSE', placeOfStudy: 'Canterbury College', subject: 'Geography', level: '2',
        awardDate: '2020-08-20', grade: '6', recordType: 'Manual',
      })],
    }));
  });

  it('edits an entry in place, reading a free-text subject back as Other', async () => {
    wizard.records = [GCSE, { ...GCSE, id: 'rec-2', subject: 'Business Management (Z0002074)', qualificationType: 'Other', recordType: 'Imported' }];
    render(<Plr />);

    await userEvent.click(screen.getByRole('button', { name: 'Edit Business Management (Z0002074)' }));
    expect(within(dialog()).getByText('Edit PLR')).toBeInTheDocument();
    const [, subject] = combos();
    expect(subject).toHaveValue('Other');
    expect(box('Please specify the subject')).toHaveValue('Business Management (Z0002074)');

    await userEvent.clear(box('Grade'));
    await userEvent.type(box('Grade'), 'Pass');
    await userEvent.click(within(dialog()).getByRole('button', { name: 'Save' }));
    // A free-text qualification type with nothing typed for it must be specified first.
    expect(within(dialog()).getByText('Please specify the qualification type.')).toBeInTheDocument();
    await userEvent.type(box('Please specify the qualification type'), 'Non regulated provision');
    await userEvent.click(within(dialog()).getByRole('button', { name: 'Save' }));

    const records = wizard.setSection.mock.lastCall![1].records as PlrRecord[];
    expect(records.map((r) => r.id)).toEqual(['rec-1', 'rec-2']);
    expect(records[1]).toEqual(expect.objectContaining({ grade: 'Pass', subject: 'Business Management (Z0002074)', recordType: 'Imported' }));
  });

  it('uploads a certificate for the entry, and takes it back if a new entry is cancelled', async () => {
    const saved = { id: 'f1', recordRef: '', filename: 'cert.pdf', contentType: 'application/pdf', sizeBytes: 4, uploadedAt: null };
    api.uploadPlrEvidence.mockImplementation(async (_k, _l, recordRef: string) => ({ ...saved, recordRef }));
    api.deletePlrEvidence.mockResolvedValue(undefined);
    render(<Plr />);
    await userEvent.click(screen.getByRole('button', { name: 'Add' }));

    const file = new File(['%PDF'], 'cert.pdf', { type: 'application/pdf' });
    await userEvent.upload(within(dialog()).getByLabelText('Certificate/Evidence'), file);
    expect(api.uploadPlrEvidence).toHaveBeenCalledWith('apprenticeship', '41', expect.any(String), file);
    expect(await within(dialog()).findByRole('button', { name: 'cert.pdf' })).toBeInTheDocument();

    await userEvent.click(within(dialog()).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(api.deletePlrEvidence).toHaveBeenCalledWith('apprenticeship', '41', 'f1'));
    expect(wizard.setSection).not.toHaveBeenCalled();
  });

  it('lists an entry’s stored certificates in the table', async () => {
    wizard.records = [GCSE];
    api.fetchPlrEvidence.mockResolvedValue([
      { id: 'f9', recordRef: 'rec-1', filename: 'gcse-maths.pdf', contentType: 'application/pdf', sizeBytes: 4, uploadedAt: null },
    ]);
    render(<Plr />);

    expect(await screen.findByRole('button', { name: 'gcse-maths.pdf' })).toBeInTheDocument();
    expect(screen.getByText('20/08/2020')).toBeInTheDocument();
    expect(screen.getByText('1 - 1 of 1 items')).toBeInTheDocument();
  });

  it('deletes an entry from the table', async () => {
    wizard.records = [GCSE];
    render(<Plr />);

    await userEvent.click(screen.getByRole('button', { name: 'Delete Maths' }));
    expect(wizard.setSection).toHaveBeenCalledWith('plr', expect.objectContaining({ records: [] }));
  });
});
