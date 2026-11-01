import { useState } from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { WizardContext, type WizardContextValue } from '@/pages/users/wizard/WizardContext.helpers';
import type { WizardDraft } from '@/pages/users/types';
import { ProgrammeModulesCard, ProgrammeModulesView } from '../modules/ProgrammeModulesCard';
import { selectableModules, selectedModuleIds, type ProgrammeModuleSelection } from '../modules/configuration';
import type { AssessmentState, ProgrammeId } from '../types';
import { fresh, result } from './fixtures';
import { PCP_PATHWAYS, PROGRAMME_MODULES, modulesConfiguration } from './modulesFixtures';
vi.mock('../modules/api', () => ({ getModules: vi.fn() }));
import { getModules } from '../modules/api';

const save = vi.fn();
const changed = vi.fn();
function assessment(selected: ProgrammeId = 'PCP_L6', recommended: ProgrammeId = selected): AssessmentState {
  return { ...fresh(), pathway: selected.startsWith('M') ? 'marketing' : 'project-controls',
    result: { ...result, outcome: `RECOMMENDED_${recommended}`, recommendedProgramme: recommended }, selectedProgramme: selected, completionStatus: 'SUBMITTED',
    confirmedAt: '2026-11-01T12:00:00Z', selectionStatus: 'SELECTION_CONFIRMED', selectionConfirmedAt: '2026-11-01T12:01:00Z' };
}
function Harness({ state = assessment(), saved, staff = false }: {
  state?: AssessmentState; saved?: ProgrammeModuleSelection; staff?: boolean;
}) {
  vi.mocked(getModules).mockResolvedValue({ configuration: structuredClone(modulesConfiguration), configurationVersion: 3, selectedProgramme: state.selectedProgramme || 'PCP_L6' });
  const [modules, setModules] = useState(saved);
  const value = { userId: '41', readOnly: staff, hydrated: true, saveIlr: save,
    draft: { programmeModules: modules } as WizardDraft, suitability: { state },
    setSection: (_key: string, next: ProgrammeModuleSelection) => { changed(next); setModules(next); },
  } as unknown as WizardContextValue;
  return <WizardContext.Provider value={value}><ProgrammeModulesCard /></WizardContext.Provider>;
}
beforeEach(() => { vi.clearAllMocks(); save.mockResolvedValue(undefined); });

it.each(['fresh', 'submitted', 'pending'])('does not show modules for an unconfirmed %s assessment', (stage) => {
  const state = stage === 'fresh' ? fresh() : { ...assessment(), selectionStatus: 'SUBMITTED_PENDING_CONFIRMATION' as const,
    selectionConfirmedAt: stage === 'pending' ? null : '2026-11-01T12:00:00Z' };
  render(<Harness state={state} />);
  expect(screen.queryByRole('region', { name: 'Programme Modules' })).toBeNull();
});

it.each([
  ['ME_L4', 'ME_L4'], ['MM_L6', 'MM_L6'], ['APM_L4', 'APM_L4'], ['ME_L4', 'MM_L6'], ['APM_L4', 'PCP_L6'],
] as [ProgrammeId, ProgrammeId][])('shows the confirmed %s modules even when %s was recommended', async (selected, recommended) => {
  render(<Harness state={assessment(selected, recommended)} />);
  const card = await screen.findByRole('region', { name: 'Programme Modules' });
  expect(screen.getAllByRole('region', { name: 'Programme Modules' })).toHaveLength(1);
  const rows = within(card).getAllByRole('row').slice(1);
  expect(rows).toHaveLength(PROGRAMME_MODULES[selected].modules.length);
  PROGRAMME_MODULES[selected].modules.forEach((module, index) => {
    expect(rows[index]).toHaveTextContent(module.title);
    expect(rows[index]).toHaveTextContent(`${module.sessions} Sessions`);
  });
  expect(within(card).queryByRole('radio')).toBeNull();
  expect(within(card).queryByRole('checkbox')).toBeNull();
  expect(save).not.toHaveBeenCalled();
});

it('shows all PCP pathways, sessions placeholders, credits and Chartered submodules with no automatic choices', async () => {
  render(<Harness />);
  await screen.findByRole('region', { name: 'Programme Modules' });
  for (const pathway of PCP_PATHWAYS) {
    const card = screen.getByRole('region', { name: pathway.title });
    expect(within(card).getByText(pathway.subtitle)).toBeVisible();
    expect(within(card).getByRole('columnheader', { name: 'Sessions' })).toBeVisible();
    expect(within(card).getAllByRole('checkbox')).toHaveLength(selectableModules(pathway.modules).length);
    for (const checkbox of within(card).getAllByRole('checkbox')) { expect(checkbox).not.toBeChecked(); expect(checkbox).toBeDisabled(); }
    expect(within(card).getAllByText('—').length).toBeGreaterThan(0);
  }
  expect(screen.getByText('Certified PMO Professional Level 6')).toBeVisible();
  expect(screen.getAllByText('1 credit').length).toBeGreaterThan(0);
  expect(screen.getAllByText('2 credits')).toHaveLength(2);
  expect(save).not.toHaveBeenCalled();
});

it('switches one active PCP pathway, saves modules, and restores inactive choices only when that pathway is reselected', async () => {
  render(<Harness />);
  await userEvent.click(await screen.findByRole('radio', { name: 'Operational Pathway' }));
  const operational = screen.getByRole('region', { name: 'Operational Pathway' });
  await userEvent.click(within(operational).getByRole('checkbox', { name: 'Project Management Professional (PMP)' }));
  await screen.findByText('Module choices saved.');
  expect(changed.mock.lastCall![0]).toMatchObject({ programme: 'PCP_L6', pathway: 'operational', modulesByPathway: { operational: ['pmp'] } });
  await userEvent.click(screen.getByRole('radio', { name: 'Strategic Pathway' }));
  expect(await screen.findByRole('radio', { name: 'Operational Pathway' })).not.toBeChecked();
  expect(within(operational).getByRole('checkbox', { name: 'Project Management Professional (PMP)' })).not.toBeChecked();
  expect(within(operational).getByRole('checkbox', { name: 'Project Management Professional (PMP)' })).toBeDisabled();
  const strategic = screen.getByRole('region', { name: 'Strategic Pathway' });
  await userEvent.click(within(strategic).getByRole('checkbox', { name: 'PMI (PMO)' }));
  await screen.findByText('Module choices saved.');
  await userEvent.click(await screen.findByRole('radio', { name: 'Operational Pathway' }));
  expect(within(operational).getByRole('checkbox', { name: 'Project Management Professional (PMP)' })).toBeChecked();
  await screen.findByText('Module choices saved.');
  expect(save).toHaveBeenCalled();
});

it('restores a saved Chartered submodule and staff can review without editing or saving', async () => {
  render(<Harness staff saved={{ programme: 'PCP_L6', pathway: 'chartered', modulesByPathway: { chartered: ['pmo-risk-quality', 'portfolio'] } }} />);
  expect(await screen.findByRole('radio', { name: 'Chartered Pathway' })).toBeChecked();
  const card = screen.getByRole('region', { name: 'Chartered Pathway' });
  expect(within(card).getByRole('checkbox', { name: 'Risk, Quality and Issue Management' })).toBeChecked();
  for (const control of screen.getAllByRole('radio').concat(screen.getAllByRole('checkbox'))) expect(control).toBeDisabled();
  expect(save).not.toHaveBeenCalled();
});

it('shows an actionable save failure and retries without resetting choices', async () => {
  save.mockRejectedValueOnce(new Error('Network unavailable'));
  render(<Harness />);
  await userEvent.click(await screen.findByRole('radio', { name: 'Chartered Pathway' }));
  await screen.findByRole('alert');
  expect(await screen.findByRole('radio', { name: 'Chartered Pathway' })).toBeChecked();
  await userEvent.click(screen.getByRole('button', { name: 'Retry saving module choices' }));
  await waitFor(() => expect(screen.getByText('Module choices saved.')).toBeInTheDocument());
  expect(save).toHaveBeenCalledTimes(2);
});

it('ignores inactive pathways, unknown module ids and duplicate ids in stored drafts', () => {
  const selection: ProgrammeModuleSelection = { programme: 'PCP_L6', pathway: 'operational', modulesByPathway: { operational: ['pmp', 'pmp', 'invented'], strategic: ['pmi-pmo'] } };
  expect(selectedModuleIds(selection, PCP_PATHWAYS.find((p) => p.id === 'operational')!)).toEqual(['pmp']);
  expect(selectedModuleIds(selection, PCP_PATHWAYS.find((p) => p.id === 'strategic')!)).toEqual([]);
});

it('renders updated sessions, order and inactive flags from configuration instead of source constants', () => {
  const config = structuredClone(modulesConfiguration);
  const modules = config.programmes[0].modules;
  modules[0].active = false;
  modules[1].title = 'Published social media'; modules[1].sessions = 18; modules[1].order = 10;
  modules[2].order = 0;
  render(<ProgrammeModulesView configuration={config} programmeId="ME_L4" />);
  const rows = screen.getAllByRole('row').slice(1);
  expect(rows).toHaveLength(2);
  expect(rows[0]).toHaveTextContent('Marketing Technology');
  expect(rows[1]).toHaveTextContent('Published social media');
  expect(rows[1]).toHaveTextContent('18 Sessions');
});

it('supports a new arbitrary pathway identifier and local credit totals without requiring target completion', async () => {
  const config = structuredClone(modulesConfiguration);
  const path = structuredClone(config.programmes[3].pathways[0]);
  path.id = 'delivery-new'; path.title = 'Delivery Pathway'; path.targetCredits = 99;
  config.programmes[3].pathways.push(path);
  function Preview() {
    const [selection, setSelection] = useState<ProgrammeModuleSelection>();
    return <ProgrammeModulesView configuration={config} programmeId="PCP_L6" selection={selection} onChange={setSelection} version={8} />;
  }
  render(<Preview />);
  expect(screen.getAllByRole('radio')).toHaveLength(4);
  await userEvent.click(screen.getByRole('radio', { name: 'Delivery Pathway' }));
  const card = within(screen.getByRole('region', { name: 'Delivery Pathway' }));
  await userEvent.click(card.getByRole('checkbox', { name: 'Project Management Professional (PMP)' }));
  expect(card.getByText('Selected credits: 2')).toBeInTheDocument();
  expect(card.getByText('Target credits: 99')).toBeInTheDocument();
});
