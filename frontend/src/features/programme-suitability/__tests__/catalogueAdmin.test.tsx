import { beforeEach, expect, it, vi } from 'vitest';
import { useState } from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CatalogueAdmin } from '../skills-radar/catalogue/CatalogueAdmin';
import type { CatalogueDraft } from '../skills-radar/catalogue/types';
import { addedCatalogue, catalogueIndex, comparison, draftFor } from './catalogueFixtures';

const api = vi.hoisted(() => ({ index: vi.fn(), draft: vi.fn(), save: vi.fn(), upload: vi.fn() }));
vi.mock('../skills-radar/catalogue/api', () => ({ getCatalogues: api.index, getCatalogueDraft: api.draft, saveCatalogueDraft: api.save, uploadCatalogue: api.upload,
  csvUrl: (pid: string, template: boolean) => `/enrolment_api/skills-radar/catalogues/${pid}/${template ? '?template=1' : ''}` }));
const changed = vi.fn();
const file = () => new File(['synthetic server-validated csv'], 'complete.csv', { type: 'text/csv' });
function Editor() {
  const [value, setValue] = useState<Partial<CatalogueDraft>>({});
  return <CatalogueAdmin layout={{ steps: [], ...value }} version={11} onChange={(config) => { setValue(config); changed(config); }} />;
}
beforeEach(() => {
  vi.clearAllMocks();
  api.index.mockResolvedValue(catalogueIndex()); api.draft.mockResolvedValue({ configuration: null, baseVersion: null });
  const catalogue = addedCatalogue();
  api.upload.mockResolvedValue({ catalogue, comparison: comparison(catalogueIndex().catalogues.ME_L4, catalogue), baseVersion: 11 });
  api.save.mockImplementation(async (configuration, baseVersion) => ({ configuration, baseVersion }));
});

it('shows all four programmes with dynamic counts, current export and blank template links', async () => {
  render(<Editor />);
  await screen.findByRole('article', { name: 'Associate Project Manager L4' });
  expect(screen.getAllByLabelText(/^Upload CSV for/)).toHaveLength(4);
  for (const [title, pid, total, modules, relationships] of [['Project Controls Professional L6', 'PCP_L6', 71, 0, 0], ['Marketing Manager L6', 'MM_L6', 42, 4, 42], ['Marketing Executive L4', 'ME_L4', 33, 3, 33], ['Associate Project Manager L4', 'APM_L4', 62, 2, 62]]) {
    const card = within(screen.getByRole('article', { name: String(title) }));
    expect(card.getByText(`${total} unique KSBs across ${modules} modules and ${relationships} relationships`)).toBeVisible();
    expect(card.getByRole('link', { name: 'Export Current Mapping CSV' })).toHaveAttribute('href', `/enrolment_api/skills-radar/catalogues/${pid}/`);
    expect(card.getByRole('link', { name: 'Download Blank CSV Template' })).toHaveAttribute('href', `/enrolment_api/skills-radar/catalogues/${pid}/?template=1`);
  }
  expect(changed).not.toHaveBeenCalled(); expect(api.save).not.toHaveBeenCalled();
});

it('reviews a complete snapshot, saves a private draft and previews dynamic modules without publishing', async () => {
  render(<Editor />);
  await userEvent.upload(await screen.findByLabelText('Upload CSV for Marketing Executive L4'), file());
  const region = await screen.findByRole('region', { name: 'CSV import comparison' });
  expect(within(region).getByText('Current KSBs: 33 · Imported total: 34')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Save Skills Radar Draft' })).toBeDisabled();
  expect(api.save).not.toHaveBeenCalled(); expect(changed).not.toHaveBeenCalled();
  await userEvent.click(screen.getByLabelText(/I confirm this CSV is the complete/));
  await userEvent.click(screen.getByRole('button', { name: 'Save Skills Radar Draft' }));
  await screen.findByRole('region', { name: 'Skills Radar draft preview' });
  expect(api.save).toHaveBeenCalledWith(draftFor(), 11);
  expect(changed).toHaveBeenCalledWith(draftFor());
  expect(screen.getByText(/34 KSBs. Test choices/)).toBeVisible();
  expect(screen.getByText('33 unique KSBs across 3 modules and 33 relationships')).toBeVisible();
  expect(screen.getByRole('navigation', { name: 'Selected modules' })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Publish changes' })).toBeNull();
});

it('requires removal and category review as well as complete snapshot confirmation before draft save', async () => {
  const catalogue = addedCatalogue();
  const diff = comparison(catalogueIndex().catalogues.ME_L4, catalogue);
  Object.assign(diff, { removed: 10, categories: 1, removedCodes: ['K1', 'K2'], categoryCodes: ['S1'], largeRemoval: true });
  api.upload.mockResolvedValue({ catalogue, comparison: diff, baseVersion: 11 });
  render(<Editor />);
  await userEvent.upload(await screen.findByLabelText('Upload CSV for Marketing Executive L4'), file());
  await screen.findByText(/Warning: at least 25%/);
  await userEvent.click(screen.getByLabelText(/I confirm this CSV is the complete/));
  expect(screen.getByRole('button', { name: 'Save Skills Radar Draft' })).toBeDisabled();
  await userEvent.click(screen.getByLabelText(/I confirm removal of all listed codes/));
  expect(screen.getByRole('button', { name: 'Save Skills Radar Draft' })).toBeDisabled();
  await userEvent.click(screen.getByLabelText(/I have reviewed the old and new categories/));
  await userEvent.click(screen.getByRole('button', { name: 'Save Skills Radar Draft' }));
  await waitFor(() => expect(api.save).toHaveBeenCalled());
  expect(api.save.mock.calls[0][0].skillsRadarImportApprovals.ME_L4).toMatchObject({ removedCodes: ['K1', 'K2'], categoryCodes: ['S1'], completeSnapshot: true });
});

it('cancel and invalid imports leave the draft and published mapping untouched and show row errors', async () => {
  render(<Editor />);
  await userEvent.upload(await screen.findByLabelText('Upload CSV for Marketing Executive L4'), file());
  await screen.findByRole('region', { name: 'CSV import comparison' });
  await userEvent.click(screen.getByRole('button', { name: 'Cancel import' }));
  expect(screen.queryByRole('region', { name: 'CSV import comparison' })).toBeNull();
  api.upload.mockRejectedValue(Object.assign(new Error('CSV validation failed'), { errors: [{ row: 5, code: 'K1', message: 'Unknown active module key' }] }));
  await userEvent.upload(screen.getByLabelText('Upload CSV for Marketing Executive L4'), file());
  await screen.findByText('Row 5 (K1): Unknown active module key');
  expect(api.save).not.toHaveBeenCalled(); expect(changed).not.toHaveBeenCalled();
});

it('identical imports do not write a draft or dirty the wizard, and stale saved drafts cannot be loaded', async () => {
  const catalogue = catalogueIndex().catalogues.ME_L4;
  api.upload.mockResolvedValue({ catalogue, comparison: { ...comparison(catalogue, catalogue), added: 0 }, baseVersion: 11 });
  api.draft.mockResolvedValue({ configuration: draftFor(), baseVersion: 10 });
  render(<Editor />);
  expect(await screen.findByRole('button', { name: 'Load saved Skills Radar draft' })).toBeDisabled();
  await userEvent.upload(screen.getByLabelText('Upload CSV for Marketing Executive L4'), file());
  await screen.findByRole('region', { name: 'CSV import comparison' });
  await userEvent.click(screen.getByLabelText(/I confirm this CSV is the complete/));
  await userEvent.click(screen.getByRole('button', { name: 'Save Skills Radar Draft' }));
  await screen.findByText(/matches the published catalogue exactly/);
  expect(api.save).not.toHaveBeenCalled(); expect(changed).not.toHaveBeenCalled();
});

it('reimporting the published CSV can explicitly revert an already loaded private draft', async () => {
  api.draft.mockResolvedValue({ configuration: draftFor(), baseVersion: 11 });
  const current = catalogueIndex().catalogues.ME_L4;
  api.upload.mockResolvedValue({ catalogue: current, comparison: { ...comparison(current, current), added: 0 }, baseVersion: 11 });
  render(<Editor />);
  await userEvent.click(await screen.findByRole('button', { name: 'Load saved Skills Radar draft' }));
  await userEvent.upload(screen.getByLabelText('Upload CSV for Marketing Executive L4'), file());
  await screen.findByRole('region', { name: 'CSV import comparison' });
  await userEvent.click(screen.getByLabelText(/I confirm this CSV is the complete/));
  await userEvent.click(screen.getByRole('button', { name: 'Save Skills Radar Draft' }));
  await waitFor(() => expect(api.save).toHaveBeenCalledTimes(1));
  const saved = api.save.mock.calls[0][0];
  expect(saved.skillsRadarCatalogues.ME_L4.records).toHaveLength(33);
  expect(saved.skillsRadarImportApprovals).not.toHaveProperty('ME_L4');
});
