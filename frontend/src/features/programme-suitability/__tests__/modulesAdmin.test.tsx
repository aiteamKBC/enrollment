import { useState } from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ModulesAdmin } from '../modules/ModulesAdmin';
import { modulesConfiguration } from './modulesFixtures';
import type { ModulesConfiguration } from '../modules/configuration';

vi.mock('../modules/api', () => ({ getModuleDefaults: vi.fn(), getModulesDraft: vi.fn(), saveModulesDraft: vi.fn() }));
import { getModuleDefaults, getModulesDraft, saveModulesDraft } from '../modules/api';
let edited: ModulesConfiguration;
function Harness({ initial = structuredClone(modulesConfiguration) }: { initial?: ModulesConfiguration }) {
  const [value, setValue] = useState(initial);
  edited = value;
  return <ModulesAdmin value={value} version={7} onChange={setValue} />;
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getModuleDefaults).mockResolvedValue({ configuration: structuredClone(modulesConfiguration) });
  vi.mocked(getModulesDraft).mockResolvedValue({ configuration: null, baseVersion: null });
  vi.mocked(saveModulesDraft).mockImplementation(async (configuration, baseVersion) => ({ configuration, baseVersion }));
});

it.each(['ME_L4', 'MM_L6', 'APM_L4'])('edits names, sessions, credits and descriptions for %s without changing published source data', async (id) => {
  const original = structuredClone(modulesConfiguration);
  render(<Harness />);
  await userEvent.selectOptions(screen.getByLabelText('Programme'), id);
  const programme = edited.programmes.find((p) => p.id === id)!;
  const row = within(screen.getByRole('group', { name: `Edit module ${programme.modules[0].title}` }));
  const name = row.getByLabelText('Module name');
  await userEvent.clear(name); await userEvent.type(name, 'Edited module');
  const sessions = row.getByLabelText('Sessions'); await userEvent.clear(sessions); await userEvent.type(sessions, '16');
  const credits = row.getByLabelText('Credits'); await userEvent.type(credits, '0.5');
  await userEvent.type(row.getByLabelText('Description'), 'Edited description');
  const saved = edited.programmes.find((p) => p.id === id)!.modules[0];
  expect(saved).toMatchObject({ title: 'Edited module', sessions: 16, credits: 0.5, description: 'Edited description' });
  expect(saved.id).toBe(original.programmes.find((p) => p.id === id)!.modules[0].id);
  expect(modulesConfiguration).toEqual(original);
  expect(saveModulesDraft).not.toHaveBeenCalled();
});

it('adds, deactivates and reorders modules; preview uses the draft without learner API writes', async () => {
  render(<Harness />);
  await userEvent.click(screen.getByRole('button', { name: 'Add Module' }));
  const newRow = within(screen.getByRole('group', { name: 'Edit module New module' }));
  await userEvent.click(newRow.getByRole('button', { name: 'Move module up' }));
  expect(edited.programmes[0].modules[2].title).toBe('New module');
  await userEvent.click(newRow.getByRole('button', { name: 'Deactivate module' }));
  expect(edited.programmes[0].modules[2].active).toBe(false);
  await userEvent.click(screen.getByRole('button', { name: 'Preview Changes' }));
  const preview = within(screen.getByRole('region', { name: 'Modules draft preview' }));
  expect(preview.queryByText('New module', { exact: true })).toBeNull();
  expect(preview.getAllByRole('row')).toHaveLength(4);
  expect(saveModulesDraft).not.toHaveBeenCalled();
});

it('edits PCP paths and nested Chartered modules, and supports adding a fourth pathway', async () => {
  render(<Harness />);
  await userEvent.selectOptions(screen.getByLabelText('Programme'), 'PCP_L6');
  await userEvent.selectOptions(screen.getByLabelText('PCP pathway'), 'chartered');
  const parent = within(screen.getByRole('group', { name: 'Edit module Certified PMO Professional Level 6' }));
  await userEvent.click(parent.getAllByRole('button', { name: 'Add submodule' })[0]);
  const pcp = edited.programmes.find((p) => p.id === 'PCP_L6')!;
  expect(pcp.pathways.find((p) => p.id === 'chartered')!.modules[0].items).toHaveLength(5);
  await userEvent.type(screen.getByLabelText('Recognition / accreditation information'), 'Provided recognition wording');
  await userEvent.click(screen.getByRole('button', { name: 'Add Pathway' }));
  expect(edited.programmes.find((p) => p.id === 'PCP_L6')!.pathways).toHaveLength(4);
  await userEvent.click(screen.getByRole('button', { name: 'Add Module' }));
  const row = within(screen.getByRole('group', { name: 'Edit module New module' }));
  await userEvent.click(row.getByLabelText('Learner-selectable'));
  await userEvent.type(row.getByLabelText('Credits'), '2.5');
  await userEvent.type(row.getByLabelText('Sessions'), '8');
  const path = edited.programmes.find((p) => p.id === 'PCP_L6')!.pathways[3];
  expect(path.modules[0]).toMatchObject({ selectable: true, credits: 2.5, sessions: 8 });
  await userEvent.clear(screen.getByLabelText('Target credits'));
  await userEvent.type(screen.getByLabelText('Target credits'), '2.5');
  await userEvent.clear(screen.getByLabelText('Required Optional Modules Count'));
  await userEvent.type(screen.getByLabelText('Required Optional Modules Count'), '1');
  await userEvent.click(screen.getByRole('button', { name: 'Preview Changes' }));
  const preview = within(screen.getByRole('region', { name: 'Modules draft preview' }));
  await userEvent.click(preview.getByRole('radio', { name: 'New pathway' }));
  await userEvent.click(preview.getByRole('checkbox', { name: 'New module' }));
  expect(preview.getByText('Selected credits: 2.5')).toBeInTheDocument();
  expect(saveModulesDraft).not.toHaveBeenCalled();
});

it('saves and reloads a private server draft without publishing it', async () => {
  const first = render(<Harness />);
  const field = within(screen.getByRole('group', { name: 'Edit module Social Media' })).getByLabelText('Sessions');
  await userEvent.clear(field); await userEvent.type(field, '18');
  await userEvent.click(screen.getByRole('button', { name: 'Save Draft' }));
  await screen.findByText('Modules draft saved. Published modules have not changed.');
  expect(saveModulesDraft).toHaveBeenCalledWith(edited, 7);
  const draft = structuredClone(edited);
  first.unmount();
  vi.mocked(getModulesDraft).mockResolvedValue({ configuration: draft, baseVersion: 7 });
  render(<Harness />);
  await userEvent.click(await screen.findByRole('button', { name: 'Load saved modules draft' }));
  expect(within(screen.getByRole('group', { name: 'Edit module Social Media' })).getByLabelText('Sessions')).toHaveValue(18);
});

it('rejects negative/fractional sessions locally and keeps nullable sessions blank', async () => {
  render(<Harness />);
  const field = within(screen.getByRole('group', { name: 'Edit module Social Media' })).getByLabelText('Sessions');
  await userEvent.clear(field); await userEvent.type(field, '-1');
  expect(screen.getByRole('button', { name: 'Save Draft' })).toBeDisabled();
  expect(screen.getByRole('alert')).toHaveTextContent('Sessions must be non-negative integers');
  await userEvent.clear(field); await userEvent.type(field, '1.5');
  expect(screen.getByRole('button', { name: 'Save Draft' })).toBeDisabled();
  await userEvent.clear(field);
  expect(screen.getByRole('button', { name: 'Save Draft' })).toBeEnabled();
});
