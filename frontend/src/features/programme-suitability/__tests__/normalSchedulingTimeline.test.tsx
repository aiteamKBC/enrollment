import { useState } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { WizardContext, type WizardContextValue } from '@/pages/users/wizard/WizardContext.helpers';
import { ProgrammeModulesCard } from '../modules/ProgrammeModulesCard';
import { mandatoryIds, type ModulesConfiguration, type ProgrammeModuleSelection } from '../modules/configuration';
import configuration from './pcp-rules-defaults.json';
import { fresh, result } from './fixtures';

const config = configuration as ModulesConfiguration;
const paths = config.programmes.find(p => p.id === 'PCP_L6')!.pathways;
const chartered = paths.find(p => p.id === 'chartered')!;
const saved: ProgrammeModuleSelection = { programme: 'PCP_L6', pathway: 'chartered', configurationVersion: 24,
  modulesByPathway: { chartered: [...mandatoryIds(chartered), 'portfolio'], operational: ['pmp', 'earned-value', 'planning-control'] } };
const save = vi.fn().mockResolvedValue(undefined);
function Harness({ initial = saved }: { initial?: ProgrammeModuleSelection }) {
  const [choice, change] = useState(initial);
  const value = { userId: 'isolated-timeline', hydrated: true, readOnly: false, isCommercial: false,
    draft: { programmeModules: choice }, setSection: (_key: string, next: ProgrammeModuleSelection) => change(next), saveModules: save,
    suitability: { state: { ...fresh(), result, selectedProgramme: 'PCP_L6', completionStatus: 'SUBMITTED', selectionStatus: 'SELECTION_CONFIRMED', selectionConfirmedAt: '2026-11-01T12:00:00Z' } },
    modules: { data: { configuration: config, configurationVersion: 24, homeCohort: { status: 'resolved', programmeId: 'PCP_L6', cohortId: 'persisted-home', name: 'Saved home cohort', layoutVersion: 24 } }, error: '', retry: vi.fn() },
  } as unknown as WizardContextValue;
  return <WizardContext.Provider value={value}><ProgrammeModulesCard /></WizardContext.Provider>;
}
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllEnvs(); });

it('appears below the normal module selector without a preview query, showing exactly the saved Chartered modules and no fake offerings', async () => {
  const { container } = render(<Harness />);
  const timeline = await screen.findByRole('region', { name: 'Module scheduling timeline' });
  expect(container.querySelector('[data-module-timeline]')?.previousElementSibling).toHaveAttribute('role', 'radiogroup');
  expect(screen.getByRole('region', { name: 'Module scheduling timeline' }).querySelectorAll('[data-academic-module-id]')).toHaveLength(6);
  expect(screen.getByRole('region', { name: 'Module scheduling timeline' }).querySelector('[data-academic-module-id="portfolio"]')).toBeVisible();
  expect(screen.getByRole('region', { name: 'Module scheduling timeline' }).querySelector('[data-academic-module-id="earned-value"]')).toBeNull();
  for (const id of mandatoryIds(chartered)) expect(screen.getByRole('region', { name: 'Module scheduling timeline' }).querySelector(`[data-academic-module-id="${id}"]`)).not.toBeNull();
  expect(within(timeline).getAllByText('Schedule not available yet')).toHaveLength(6);
  expect(screen.getByRole('region', { name: 'Module scheduling timeline' }).querySelector('[data-offering-id]')).toBeNull();
  expect(within(timeline).queryByRole('button', { name: /Select sample/ })).toBeNull();
  expect(within(timeline).queryByText(/SAMPLE|Sample preview/)).toBeNull();
  expect(within(timeline).getAllByText('Saved home cohort')).toHaveLength(1);
  expect(save).not.toHaveBeenCalled();
});

it('adds/removes optional modules immediately from live state without waiting for the academic save', async () => {
  const user = userEvent.setup();
  render(<Harness />);
  const timeline = await screen.findByRole('region', { name: 'Module scheduling timeline' });
  const card = within(screen.getByRole('region', { name: 'Chartered Pathway' }));
  await user.click(card.getByRole('checkbox', { name: 'Portfolio Management (APMG)' }));
  expect(screen.getByRole('region', { name: 'Module scheduling timeline' }).querySelector('[data-academic-module-id="portfolio"]')).toBeNull();
  expect(screen.getByRole('region', { name: 'Module scheduling timeline' }).querySelectorAll('[data-academic-module-id]')).toHaveLength(5);
  await user.click(card.getByRole('checkbox', { name: 'Earned Value Management (APMG)' }));
  expect(screen.getByRole('region', { name: 'Module scheduling timeline' }).querySelector('[data-academic-module-id="earned-value"]')).toBeVisible();
  await user.click(card.getByRole('checkbox', { name: 'Earned Value Management (APMG)' }));
  await user.click(card.getByRole('checkbox', { name: 'Portfolio Management (APMG)' }));
  expect(screen.getByRole('region', { name: 'Module scheduling timeline' }).querySelector('[data-academic-module-id="portfolio"]')).toBeVisible();
  expect(screen.getByRole('region', { name: 'Module scheduling timeline' }).querySelector('[data-academic-module-id="earned-value"]')).toBeNull();
  expect(within(screen.getByRole('region', { name: 'Module scheduling timeline' })).getByText('Saved home cohort')).toBeVisible();
});

it('switches pathways without leaking inactive-path optionals and restores saved choices after remount', async () => {
  const { unmount } = render(<Harness />);
  const timeline = await screen.findByRole('region', { name: 'Module scheduling timeline' });
  await userEvent.click(screen.getByRole('radio', { name: 'Operational Pathway' }));
  expect(screen.getByRole('region', { name: 'Module scheduling timeline' }).querySelector('[data-academic-module-id="portfolio"]')).toBeNull();
  expect(screen.getByRole('region', { name: 'Module scheduling timeline' }).querySelector('[data-academic-module-id="pmp"]')).toBeVisible();
  await userEvent.click(screen.getByRole('radio', { name: 'Chartered Pathway' }));
  expect(screen.getByRole('region', { name: 'Module scheduling timeline' }).querySelector('[data-academic-module-id="portfolio"]')).toBeVisible();
  unmount();
  render(<Harness />);
  await waitFor(() => expect(screen.getByRole('region', { name: 'Module scheduling timeline' }).querySelector('[data-academic-module-id="portfolio"]')).not.toBeNull());
});

it('shows the same safe empty timeline in a production environment', async () => {
  vi.stubEnv('DEV', false);
  render(<Harness />);
  const timeline = await screen.findByRole('region', { name: 'Module scheduling timeline' });
  expect(screen.getByRole('region', { name: 'Module scheduling timeline' }).querySelectorAll('[data-academic-module-id]')).toHaveLength(6);
  expect(screen.getByRole('region', { name: 'Module scheduling timeline' }).querySelector('[data-offering-id]')).toBeNull();
  expect(save).not.toHaveBeenCalled();
});
