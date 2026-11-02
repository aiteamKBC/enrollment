import { useState } from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import data from './pcp-rules-defaults.json';
import { ProgrammeModulesView } from '../modules/ProgrammeModulesCard';
import { ModulesAdmin } from '../modules/ModulesAdmin';
import { asModuleGroup, assignModuleParent, moduleGroups } from '../modules/editorOps';
import { moduleSelectionComplete, type ModulesConfiguration, type ProgrammeModuleSelection } from '../modules/configuration';

vi.mock('../modules/api', () => ({ getModuleDefaults: vi.fn(), getModulesDraft: vi.fn(), saveModulesDraft: vi.fn() }));
import { getModuleDefaults, getModulesDraft, saveModulesDraft } from '../modules/api';

const config = structuredClone(data) as ModulesConfiguration;
const path = config.programmes[3].pathways.find((item) => item.id === 'chartered')!;
const pmo = path.modules.find((item) => item.id === 'certified-pmo')!;
Object.assign(pmo, asModuleGroup(pmo, pmo.items!));
const independent = ['ai-project-management', 'portfolio', 'earned-value'];
const selected: ProgrammeModuleSelection = { programme: 'PCP_L6', pathway: 'chartered', configurationVersion: 11,
  modulesByPathway: { chartered: [...pmo.items!.map((item) => item.id), 'ai-project-management', 'portfolio'] } };
let edited: ModulesConfiguration;
function Admin() {
  const [value, setValue] = useState(structuredClone(config)); edited = value;
  return <ModulesAdmin value={value} version={11} onChange={setValue} />;
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getModulesDraft).mockResolvedValue({ configuration: null, baseVersion: null });
  vi.mocked(getModuleDefaults).mockResolvedValue({ configuration: structuredClone(config) });
  vi.mocked(saveModulesDraft).mockImplementation(async (configuration, baseVersion) => ({ configuration, baseVersion }));
});

it.each([false, true])('scopes the group heading to four children and separates root modules even after rename/reorder=%s', (reordered) => {
  const source = structuredClone(config);
  const current = source.programmes[3].pathways.find((item) => item.id === 'chartered')!;
  const group = current.modules.find((item) => item.id === 'certified-pmo')!;
  if (reordered) { group.title = 'Renamed mini-programme'; group.order = 50; current.modules.reverse(); }
  const view = render(<ProgrammeModulesView configuration={source} programmeId="PCP_L6" selection={selected} readOnly version={11} />);
  const card = within(screen.getByRole('region', { name: path.title }));
  const body = card.getByRole('rowgroup', { name: group.title });
  expect(within(body).getAllByRole('row')).toHaveLength(5);
  for (const child of group.items!) {
    const input = card.getByRole('checkbox', { name: child.title });
    expect(input.closest('tbody')).toBe(body);
    expect(input.closest('label')).toHaveClass('ml-3', 'border-l-2');
    expect(input).toBeChecked(); expect(input).toBeDisabled();
  }
  for (const id of independent) {
    const item = current.modules.find((module) => module.id === id)!;
    const input = card.getByRole('checkbox', { name: item.title });
    expect(body).not.toContainElement(input);
    expect(input.closest('label')).not.toHaveClass('ml-3');
  }
  if (!reordered) expect(card.getByRole('checkbox', { name: 'AI in Project Management' }).closest('tbody')).toHaveClass('[&>tr:first-child>td]:border-t-2');
  expect(card.getByText('Selected credits: 6')).toBeVisible();
  view.rerender(<ProgrammeModulesView configuration={structuredClone(source)} programmeId="PCP_L6" selection={structuredClone(selected)} readOnly version={11} />);
  expect(within(screen.getByRole('rowgroup', { name: group.title })).getAllByRole('row')).toHaveLength(5);
  expect(moduleSelectionComplete(source, selected, 11)).toBe(true);
});

it('assigns and clears a parent by stable ID without changing module settings, selection or other pathways', async () => {
  const view = render(<Admin />);
  await userEvent.selectOptions(screen.getByLabelText('Programme'), 'PCP_L6');
  await userEvent.selectOptions(screen.getByLabelText('PCP pathway'), 'chartered');
  const parent = screen.getByRole('group', { name: `Edit group ${pmo.title}` });
  for (const child of pmo.items!) expect(within(parent).getByRole('group', { name: `Edit module ${child.title}` })).toBeVisible();
  for (const id of independent) {
    const module = path.modules.find((item) => item.id === id)!;
    const fieldset = screen.getByRole('group', { name: `Edit module ${module.title}` });
    expect(parent).not.toContainElement(fieldset);
    expect(within(fieldset).getByLabelText('Parent group')).toHaveValue('');
  }
  const portfolio = () => within(screen.getByRole('group', { name: 'Edit module Portfolio Management (APMG)' }));
  await userEvent.selectOptions(portfolio().getByLabelText('Parent group'), 'certified-pmo');
  expect(parent).toContainElement(screen.getByRole('group', { name: 'Edit module Portfolio Management (APMG)' }));
  await userEvent.selectOptions(portfolio().getByLabelText('Parent group'), '');
  expect(parent).not.toContainElement(screen.getByRole('group', { name: 'Edit module Portfolio Management (APMG)' }));
  const changed = edited.programmes[3].pathways.find((item) => item.id === 'chartered')!;
  expect(changed.modules.find((item) => item.id === 'certified-pmo')!.items).toHaveLength(4);
  expect(changed.modules.find((item) => item.id === 'portfolio')).toEqual({ ...path.modules.find((item) => item.id === 'portfolio'), order: 4 });
  expect(edited.programmes[3].pathways.slice(0, 2)).toEqual(config.programmes[3].pathways.slice(0, 2));
  expect(moduleSelectionComplete(edited, selected, 11)).toBe(true);
  await userEvent.click(screen.getByRole('button', { name: 'Save Draft' }));
  await screen.findByText('Modules draft saved. Published modules have not changed.');
  const saved = structuredClone(edited);
  expect(saveModulesDraft).toHaveBeenCalledWith(saved, 11);
  view.unmount();
  vi.mocked(getModulesDraft).mockResolvedValue({ configuration: saved, baseVersion: 11 });
  render(<Admin />);
  await userEvent.click(await screen.findByRole('button', { name: 'Load saved modules draft' }));
  await userEvent.selectOptions(screen.getByLabelText('Programme'), 'PCP_L6');
  await userEvent.click(screen.getByRole('button', { name: 'Preview Changes' }));
  expect(within(screen.getByRole('region', { name: 'Modules draft preview' })).getByRole('rowgroup', { name: pmo.title }).querySelectorAll('tr')).toHaveLength(5);
});

it('moves modules from nested groups without duplicate IDs, preserves empty groups, and ignores invalid targets', () => {
  const original = structuredClone(path.modules);
  const child = pmo.items![0];
  const root = assignModuleParent(original, child.id, '');
  expect(root.find((item) => item.id === child.id)).toEqual({ ...child, order: 4 });
  expect(root.find((item) => item.id === pmo.id)!.items).toHaveLength(3);
  expect(original).toEqual(path.modules);
  expect(assignModuleParent(root, child.id, '')).toBe(root);
  expect(assignModuleParent(root, child.id, 'not-found')).toBe(root);
  expect(assignModuleParent(root, pmo.id, pmo.id)).toBe(root);
  let items = structuredClone(path.modules);
  for (const module of pmo.items!) items = assignModuleParent(items, module.id, '');
  expect(moduleGroups(items)).toHaveLength(1);
  expect(moduleGroups(items)[0].items).toEqual([]);
});
