import { useState } from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useModulesConfiguration } from '../modules/useModulesConfiguration';
import { ProgrammeModulesView } from '../modules/ProgrammeModulesCard';
import type { ModulesState, ProgrammeModuleSelection } from '../modules/configuration';
import { modulesConfiguration } from './modulesFixtures';
import published from './pcp-rules-defaults.json';

vi.mock('../modules/api', () => ({ getModules: vi.fn() }));
import { getModules } from '../modules/api';

function Harness() {
  const { data } = useModulesConfiguration('apprenticeship', '41', true, 9);
  const [selection, change] = useState<ProgrammeModuleSelection>();
  return data ? <><p>Loaded version {data.configurationVersion}</p><ProgrammeModulesView configuration={data.configuration}
    programmeId="PCP_L6" version={data.configurationVersion} selection={selection} onChange={change} /></> : <p>Loading</p>;
}
beforeEach(() => { vi.clearAllMocks(); });

it('reloads applicable published rules on focus and locks mandatory modules/caps from the new configuration', async () => {
  vi.mocked(getModules).mockResolvedValueOnce({ configuration: modulesConfiguration, configurationVersion: 7, selectedProgramme: 'PCP_L6' });
  render(<Harness />);
  await userEvent.click(await screen.findByRole('radio', { name: 'Operational Pathway' }));
  const card = within(screen.getByRole('region', { name: 'Operational Pathway' }));
  expect(card.getAllByRole('checkbox').filter((input) => (input as HTMLInputElement).checked)).toHaveLength(0);
  vi.mocked(getModules).mockResolvedValue({ configuration: published as ModulesState['configuration'], configurationVersion: 9, selectedProgramme: 'PCP_L6' });
  fireEvent(window, new Event('focus'));
  await screen.findByText('Loaded version 9');
  expect(card.getAllByRole('checkbox').filter((input) => (input as HTMLInputElement).checked)).toHaveLength(3);
  expect(card.getByRole('checkbox', { name: 'Project Management Professional (PMP)' })).toBeDisabled();
  await userEvent.click(card.getByRole('checkbox', { name: 'Project Planning and Control (APMG Qualification)' }));
  await userEvent.click(card.getByRole('checkbox', { name: 'PMI-SP' }));
  expect(card.getByRole('checkbox', { name: 'Earned Value Management (EVM)' })).toBeDisabled();
  await userEvent.click(card.getByRole('checkbox', { name: 'PMI-SP' }));
  expect(card.getByRole('checkbox', { name: 'Earned Value Management (EVM)' })).toBeEnabled();
});

it('does not overwrite a newer focus refresh with a slow stale response', async () => {
  let resolveOld!: (data: ModulesState) => void;
  vi.mocked(getModules).mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
  vi.mocked(getModules).mockResolvedValue({ configuration: published as ModulesState['configuration'], configurationVersion: 9, selectedProgramme: 'PCP_L6' });
  render(<Harness />); fireEvent(window, new Event('focus'));
  await screen.findByText('Loaded version 9');
  await act(async () => { resolveOld({ configuration: modulesConfiguration, configurationVersion: 7, selectedProgramme: 'PCP_L6' }); });
  await waitFor(() => expect(screen.queryByText('Loaded version 7')).toBeNull());
  expect(screen.getByText('Loaded version 9')).toBeInTheDocument();
});
