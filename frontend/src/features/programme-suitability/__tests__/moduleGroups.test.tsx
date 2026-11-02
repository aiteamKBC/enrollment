import { useState } from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import data from './pcp-rules-defaults.json';
import { ProgrammeModulesView } from '../modules/ProgrammeModulesCard';
import { ModulesAdmin } from '../modules/ModulesAdmin';
import { asModuleGroup } from '../modules/editorOps';
import { configurationErrors, moduleSelectionComplete, selectableModules, type ModulesConfiguration, type ProgrammeModuleSelection } from '../modules/configuration';

vi.mock('../modules/api', () => ({ getModuleDefaults: vi.fn(), getModulesDraft: vi.fn(), saveModulesDraft: vi.fn() }));
import { getModuleDefaults, getModulesDraft, saveModulesDraft } from '../modules/api';

const config = structuredClone(data) as ModulesConfiguration;
const path = config.programmes[3].pathways[2];
path.modules[0] = asModuleGroup(path.modules[0], path.modules[0].items!);
const group = path.modules[0];
let edited: ModulesConfiguration;
function Admin() {
  const [value, setValue] = useState(structuredClone(config)); edited = value;
  return <ModulesAdmin value={value} version={10} onChange={setValue} />;
}
function Learner({ initial = config }: { initial?: ModulesConfiguration }) {
  const [selection, select] = useState<ProgrammeModuleSelection>();
  return <><ProgrammeModulesView configuration={initial} programmeId="PCP_L6" selection={selection} onChange={select} version={10} />
    <p>{moduleSelectionComplete(initial, selection, 10) ? 'Valid selection' : 'Incomplete selection'}</p></>;
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getModuleDefaults).mockResolvedValue({ configuration: structuredClone(config) });
  vi.mocked(getModulesDraft).mockResolvedValue({ configuration: null, baseVersion: null });
  vi.mocked(saveModulesDraft).mockImplementation(async (configuration, baseVersion) => ({ configuration, baseVersion }));
});

it.each(['GROUP', 'legacy'] as const)('renders %s parent as a full-width heading with only real child modules counted', async (kind) => {
  const source = structuredClone(config);
  const parent = source.programmes[3].pathways[2].modules[0];
  if (kind === 'legacy') { delete parent.itemType; parent.selectable = true; parent.credits = 99; }
  render(<Learner initial={source} />);
  const card = within(screen.getByRole('region', { name: path.title }));
  const header = card.getByRole('rowheader', { name: group.title });
  expect(header).toHaveAttribute('colspan', '2');
  expect(header.closest('tr')?.children).toHaveLength(1);
  expect(header).not.toHaveTextContent(/Sessions|credits|Mandatory|—/);
  expect(within(header).queryByRole('checkbox')).toBeNull();
  const rows = card.getAllByRole('row');
  const parentIndex = rows.indexOf(header.closest('tr')!);
  await userEvent.click(card.getByRole('radio', { name: path.title }));
  for (const [index, child] of group.items!.entries()) {
    expect(rows[parentIndex + index + 1]).toHaveTextContent(child.title);
    expect(rows[parentIndex + index + 1]).toHaveTextContent('1 credit');
    expect(rows[parentIndex + index + 1].firstElementChild).toHaveClass('pl-5');
    expect(card.getByRole('checkbox', { name: child.title })).toBeChecked();
    expect(card.getByRole('checkbox', { name: child.title })).toBeDisabled();
  }
  expect(card.getByRole('checkbox', { name: 'AI in Project Management' })).toBeDisabled();
  expect(card.getByRole('checkbox', { name: 'AI in Project Management' })).toBeChecked();
  expect(card.getByText('Selected credits: 5')).toBeVisible();
  await userEvent.click(card.getByRole('checkbox', { name: 'Portfolio Management (APMG)' }));
  expect(card.getByRole('checkbox', { name: 'Earned Value Management (APMG)' })).toBeDisabled();
  expect(card.getByText('Selected credits: 6')).toBeVisible();
  expect(screen.getByText('Valid selection')).toBeVisible();
  expect(selectableModules([parent])).toHaveLength(4);
});

it('edits group metadata and child fields independently, saves/reloads a private draft and previews the same heading', async () => {
  const view = render(<Admin />);
  await userEvent.selectOptions(screen.getByLabelText('Programme'), 'PCP_L6');
  await userEvent.selectOptions(screen.getByLabelText('PCP pathway'), 'chartered');
  const parent = screen.getByRole('group', { name: `Edit group ${group.title}` });
  const ownLabels = Array.from(parent.querySelectorAll('label')).filter((label) => label.querySelector('input,textarea,select')?.closest('fieldset') === parent).map((label) => label.textContent);
  expect(ownLabels).toEqual(['Group name', 'Display order', 'Active group', 'Description']);
  const name = within(parent).getByLabelText('Group name');
  await userEvent.clear(name); await userEvent.type(name, 'Renamed PMO group');
  const child = within(screen.getByRole('group', { name: `Edit module ${group.items![0].title}` }));
  await userEvent.type(child.getByLabelText('Sessions'), '8');
  await userEvent.clear(child.getByLabelText('Module name')); await userEvent.type(child.getByLabelText('Module name'), 'Renamed child');
  expect(child.getByLabelText('Credits')).toHaveValue(1);
  expect(child.getByLabelText('Mandatory Module')).toHaveValue('yes');
  expect(edited.programmes[3].pathways.slice(0, 2)).toEqual(config.programmes[3].pathways.slice(0, 2));
  const changed = edited.programmes[3].pathways[2].modules[0];
  expect(changed).toMatchObject({ id: 'certified-pmo', itemType: 'GROUP', title: 'Renamed PMO group' });
  for (const field of ['sessions', 'credits', 'selectable', 'isMandatory']) expect(changed).not.toHaveProperty(field);
  expect(changed.items![0]).toMatchObject({ title: 'Renamed child', sessions: 8, credits: 1, isMandatory: true });
  await userEvent.click(screen.getByRole('button', { name: 'Save Draft' }));
  await screen.findByText('Modules draft saved. Published modules have not changed.');
  const saved = structuredClone(edited);
  expect(saveModulesDraft).toHaveBeenCalledWith(saved, 10);
  view.unmount();
  vi.mocked(getModulesDraft).mockResolvedValue({ configuration: saved, baseVersion: 10 });
  render(<Admin />);
  await userEvent.click(await screen.findByRole('button', { name: 'Load saved modules draft' }));
  await userEvent.selectOptions(screen.getByLabelText('Programme'), 'PCP_L6');
  await userEvent.click(screen.getByRole('button', { name: 'Preview Changes' }));
  expect(within(screen.getByRole('region', { name: 'Modules draft preview' })).getByRole('rowheader', { name: 'Renamed PMO group' })).toHaveAttribute('colspan', '2');
  expect(config.programmes[3].pathways[2].modules[0]).toEqual(group);
});

it('rejects assigning module settings to groups, even when renamed or reordered', () => {
  for (const extra of [{ sessions: 0 }, { credits: 0 }, { isMandatory: true }, { selectable: true }]) {
    const source = structuredClone(config);
    const parent = source.programmes[3].pathways[2].modules[0];
    Object.assign(parent, extra, { title: 'Different title', order: 99 });
    expect(configurationErrors(source).join(' ')).toContain('Module groups cannot have');
  }
});
