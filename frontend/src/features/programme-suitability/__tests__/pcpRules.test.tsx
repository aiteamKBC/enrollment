import { useState } from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import data from './pcp-rules-defaults.json';
import { modulesConfiguration } from './modulesFixtures';
import { ProgrammeModulesView } from '../modules/ProgrammeModulesCard';
import { ModulesAdmin } from '../modules/ModulesAdmin';
import { configurationErrors, mandatoryIds, moduleSelectionComplete, type ModulesConfiguration, type ProgrammeModuleSelection } from '../modules/configuration';
import { applyPcpDefaults } from '../modules/upgrade';

vi.mock('../modules/api', () => ({ getModuleDefaults: vi.fn(), getModulesDraft: vi.fn(), saveModulesDraft: vi.fn() }));
import { getModuleDefaults, getModulesDraft, saveModulesDraft } from '../modules/api';
const config = data as ModulesConfiguration;
const paths = config.programmes[3].pathways;
let edited: ModulesConfiguration;
function Preview() {
  const [selected, select] = useState<ProgrammeModuleSelection>();
  return <><ProgrammeModulesView configuration={config} programmeId="PCP_L6" selection={selected} onChange={select} version={8} />
    <p>{moduleSelectionComplete(config, selected, 8) ? 'Module selection valid' : 'Module selection incomplete'}</p></>;
}
function Admin() {
  const [value, setValue] = useState(structuredClone(config)); edited = value;
  return <ModulesAdmin value={value} version={8} onChange={setValue} />;
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getModuleDefaults).mockResolvedValue({ configuration: structuredClone(config) });
  vi.mocked(getModulesDraft).mockResolvedValue({ configuration: null, baseVersion: null });
  vi.mocked(saveModulesDraft).mockImplementation(async (configuration, baseVersion) => ({ configuration, baseVersion }));
});

it.each(paths.map((path) => [path.id, path.title] as const))('automatically locks mandatory %s modules and requires the exact optional count and six credits', async (_id, title) => {
  render(<Preview />);
  const path = paths.find((p) => p.title === title)!;
  const card = within(screen.getByRole('region', { name: title }));
  expect(card.getByText(path.description)).toBeVisible();
  await userEvent.click(card.getByRole('radio', { name: title }));
  const mandatory = mandatoryIds(path);
  const checked = card.getAllByRole('checkbox').filter((input) => (input as HTMLInputElement).checked);
  expect(checked).toHaveLength(mandatory.length);
  for (const input of checked) expect(input).toBeDisabled();
  expect(card.getAllByText('Mandatory')).toHaveLength(mandatory.length);
  const optional = card.getAllByRole('checkbox').filter((input) => !checked.includes(input));
  for (const input of optional.slice(0, path.requiredOptionalModulesCount!)) await userEvent.click(input);
  expect(screen.getByText('Module selection valid')).toBeInTheDocument();
  expect(card.getByText('Selected credits: 6')).toBeInTheDocument();
  for (const input of optional.slice(path.requiredOptionalModulesCount!)) expect(input).toBeDisabled();
  await userEvent.click(optional[0]);
  expect(screen.getByText('Module selection incomplete')).toBeInTheDocument();
  expect(optional[optional.length - 1]).toBeEnabled();
  if (_id === 'chartered') expect(card.queryByRole('checkbox', { name: 'Certified PMO Professional Level 6' })).toBeNull();
});

it('retains visible notes and only counts modules inside the selected pathway', async () => {
  render(<Preview />);
  for (const path of paths) expect(screen.getByText(path.description)).toBeVisible();
  await userEvent.click(screen.getByRole('radio', { name: 'Operational Pathway' }));
  const operational = within(screen.getByRole('region', { name: 'Operational Pathway' }));
  await userEvent.click(operational.getByRole('checkbox', { name: 'PMI-SP' }));
  await userEvent.click(screen.getByRole('radio', { name: 'Chartered Pathway' }));
  expect(operational.getByRole('checkbox', { name: 'PMI-SP' })).not.toBeChecked();
  expect(within(screen.getByRole('region', { name: 'Chartered Pathway' })).getByText('Selected credits: 5')).toBeInTheDocument();
});

it('edits notes, mandatory flags and optional counts in a private draft and previews the edited note', async () => {
  render(<Admin />);
  await userEvent.selectOptions(screen.getByLabelText('Programme'), 'PCP_L6');
  const note = screen.getByLabelText('Pathway Note');
  await userEvent.clear(note); await userEvent.type(note, 'Edited operational note');
  const risk = within(screen.getByRole('group', { name: 'Edit module Risk Management (APM)' }));
  await userEvent.selectOptions(risk.getByLabelText('Mandatory Module'), 'no');
  const count = screen.getByLabelText('Required Optional Modules Count');
  await userEvent.clear(count); await userEvent.type(count, '3');
  expect(configurationErrors(edited)).toEqual([]);
  await userEvent.click(screen.getByRole('button', { name: 'Save Draft' }));
  await screen.findByText('Modules draft saved. Published modules have not changed.');
  expect(saveModulesDraft).toHaveBeenCalledWith(edited, 8);
  expect(edited.programmes[3].pathways[0].description).toBe('Edited operational note');
  await userEvent.click(screen.getByRole('button', { name: 'Preview Changes' }));
  expect(within(screen.getByRole('region', { name: 'Modules draft preview' })).getByText('Edited operational note')).toBeVisible();
  expect(paths[0].description).not.toBe('Edited operational note');
});

it('blocks impossible rule configurations and keeps parent headings without mandatory controls', async () => {
  render(<Admin />);
  await userEvent.selectOptions(screen.getByLabelText('Programme'), 'PCP_L6');
  const count = screen.getByLabelText('Required Optional Modules Count');
  await userEvent.clear(count); await userEvent.type(count, '4');
  expect(screen.getByRole('button', { name: 'Save Draft' })).toBeDisabled();
  expect(screen.getByRole('alert')).toHaveTextContent('Required optional module count exceeds');
  await userEvent.selectOptions(screen.getByLabelText('PCP pathway'), 'chartered');
  const parent = screen.getByRole('group', { name: 'Edit group Certified PMO Professional Level 6' });
  expect(within(parent).getAllByLabelText('Mandatory Module')).toHaveLength(4);
});

it('upgrades only an explicit admin draft by stable IDs and leaves historical configurations untouched', () => {
  const legacy = structuredClone(modulesConfiguration);
  const original = structuredClone(legacy);
  legacy.programmes[3].pathways[0].modules.reverse();
  const upgraded = applyPcpDefaults(legacy, config);
  expect(mandatoryIds(upgraded.programmes[3].pathways[0]).sort()).toEqual(['ai-project-control', 'pmp', 'risk-apm']);
  expect(upgraded.programmes[3].pathways[2].targetCredits).toBe(6);
  expect(upgraded.programmes[3].pathways[2].description).toBe(paths[2].description);
  expect(original.schemaVersion).toBe(1);
  expect(legacy.programmes[3].pathways[0].description).toBe('');
});
