import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CohortModuleConfirmation } from '../modules/CohortModuleConfirmation';
import { ProgrammeModulesCard } from '../modules/ProgrammeModulesCard';
import { mandatoryIds, type ProgrammeModuleSelection, type ModulesConfiguration } from '../modules/configuration';
import published from './pcp-rules-defaults.json';
import { fresh } from './fixtures';
import type { ProgrammeId } from '../types';

const mocks = vi.hoisted(() => ({ request: vi.fn(), wizard: vi.fn(), save: vi.fn() }));
vi.mock('../api', () => ({ request: mocks.request }));
vi.mock('@/pages/users/wizard/WizardContext.helpers', () => ({ useWizard: mocks.wizard }));
vi.mock('../modules/useModuleSelectionSave', () => ({ useModuleSelectionSave: mocks.save }));
vi.mock('../modules/ModuleSchedulingPreview', () => ({ default: () => <div /> }));

const modulesConfiguration = published as ModulesConfiguration;
const path = modulesConfiguration.programmes.find(p => p.id === 'PCP_L6')!.pathways.find(p => p.id === 'chartered')!;
const selection: ProgrammeModuleSelection = { programme: 'PCP_L6', pathway: path.id, configurationVersion: 3,
  modulesByPathway: { [path.id]: [...mandatoryIds(path), 'portfolio'] } };
const ready = { status: 'ready', selectionRevision: 8, savedSelection: selection };
const allocated = { status: 'allocated', selectionRevision: 8, groupId: 'g1', groupName: 'Group 1', cohortId: 'home',
  offerings: selection.modulesByPathway[path.id].map(id => ({ academicModuleId: id, offeringId: `g1-${id}`, cohortId: 'home', groupId: 'g1' })) };
const confirmed = vi.fn(), busy = vi.fn();
const props = { context: { id: '41', kind: 'apprenticeship' as const }, selection, saving: false,
  readOnly: false, onConfirmed: confirmed, onBusy: busy };

beforeEach(() => {
  vi.clearAllMocks();
  mocks.request.mockImplementation((_url, data) => Promise.resolve(data ? allocated : ready));
});

it('confirms once with only the saved revision and refreshes the matching timeline', async () => {
  let finish!: (value: typeof allocated) => void;
  mocks.request.mockImplementation((_url, data) => data ? new Promise(resolve => { finish = resolve; }) : Promise.resolve(ready));
  const listener = vi.fn();
  window.addEventListener('pcp-cohort-modules-confirmed', listener);
  render(<CohortModuleConfirmation {...props} />);
  const button = screen.getByRole('button', { name: 'Confirm My Cohort Modules' });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button); fireEvent.click(button);
  expect(mocks.request.mock.calls.filter(([, data]) => data !== undefined)).toHaveLength(1);
  expect(mocks.request).toHaveBeenLastCalledWith(expect.stringContaining('/apprenticeship/41/cohort-module-confirmation/'), { expectedRevision: 8 });
  finish(allocated);
  expect(await screen.findByText('Your cohort modules are confirmed.')).toBeVisible();
  expect(screen.getByText('Group 1')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Confirm My Cohort Modules' })).toBeNull();
  expect(confirmed).toHaveBeenLastCalledWith(true);
  expect(listener).toHaveBeenCalledOnce();
  window.removeEventListener('pcp-cohort-modules-confirmed', listener);
});

it('loads the persisted group on refresh without another mutation', async () => {
  mocks.request.mockResolvedValue(allocated);
  const view = render(<CohortModuleConfirmation {...props} />);
  await screen.findByText('Your cohort modules are confirmed.');
  view.unmount();
  render(<CohortModuleConfirmation {...props} />);
  expect(await screen.findByText('Group 1')).toBeVisible();
  expect(mocks.request.mock.calls.every(([, data]) => data === undefined)).toBe(true);
});

it('displays the revalidated Group 2 rather than guessing Group 1', async () => {
  mocks.request.mockImplementation((_url, data) => Promise.resolve(data ? { ...allocated, groupId: 'g2', groupName: 'Group 2' } : ready));
  render(<CohortModuleConfirmation {...props} />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Confirm My Cohort Modules' })).toBeEnabled());
  await userEvent.click(screen.getByRole('button', { name: 'Confirm My Cohort Modules' }));
  expect(await screen.findByText('Group 2')).toBeVisible();
});

it('blocks unsaved choices and read-only views', async () => {
  const view = render(<CohortModuleConfirmation {...props} saving />);
  expect(screen.getByRole('button', { name: 'Confirm My Cohort Modules' })).toBeDisabled();
  expect(mocks.request).not.toHaveBeenCalled();
  view.rerender(<CohortModuleConfirmation {...props} readOnly />);
  await waitFor(() => expect(mocks.request).toHaveBeenCalled());
  expect(screen.getByRole('button', { name: 'Confirm My Cohort Modules' })).toBeDisabled();
});

it('does not confirm a different saved academic bundle', async () => {
  mocks.request.mockResolvedValue({ ...ready, savedSelection: { ...selection, pathway: 'operational' } });
  render(<CohortModuleConfirmation {...props} />);
  await waitFor(() => expect(mocks.request).toHaveBeenCalled());
  expect(screen.getByRole('button', { name: 'Confirm My Cohort Modules' })).toBeDisabled();
});

it('shows unavailable data without claiming confirmation or making a write', async () => {
  mocks.request.mockRejectedValue(new Error('A complete, unique home-group module schedule must be verified before allocation.'));
  render(<CohortModuleConfirmation {...props} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('complete, unique home-group');
  expect(screen.getByRole('button', { name: 'Confirm My Cohort Modules' })).toBeDisabled();
  expect(confirmed).not.toHaveBeenCalledWith(true);
});

it('retries safely after an uncertain confirmation response', async () => {
  let attempts = 0;
  mocks.request.mockImplementation((_url, data) => !data ? Promise.resolve(ready)
    : ++attempts === 1 ? Promise.reject(new Error('Please retry.')) : Promise.resolve(allocated));
  render(<CohortModuleConfirmation {...props} />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Confirm My Cohort Modules' })).toBeEnabled());
  await userEvent.click(screen.getByRole('button', { name: 'Confirm My Cohort Modules' }));
  await screen.findByRole('alert');
  await userEvent.click(screen.getByRole('button', { name: 'Confirm My Cohort Modules' }));
  expect(await screen.findByText('Your cohort modules are confirmed.')).toBeVisible();
});

it.each(['ME_L4', 'MM_L6', 'APM_L4', 'PCP_L6'] as ProgrammeId[])('shows a confirmation button only for PCP, current programme %s', async programme => {
  mocks.wizard.mockReturnValue({ userId: '41', hydrated: true, isCommercial: false, readOnly: false,
    draft: { programmeModules: selection },
    suitability: { state: { ...fresh(), selectedProgramme: programme, completionStatus: 'SUBMITTED', selectionStatus: 'SELECTION_CONFIRMED', selectionConfirmedAt: '2026-11-17T12:00:00Z' } },
    modules: { data: { configuration: modulesConfiguration, configurationVersion: 3, selectedProgramme: programme } } });
  mocks.save.mockReturnValue({ selection, status: 'saved', error: '', update: vi.fn(), retry: vi.fn() });
  render(<ProgrammeModulesCard />);
  if (programme === 'PCP_L6') expect(await screen.findByRole('button', { name: 'Confirm My Cohort Modules' })).toBeVisible();
  else expect(screen.queryByRole('button', { name: 'Confirm My Cohort Modules' })).toBeNull();
});

it('does not show PCP confirmation until the active academic selection is complete', () => {
  mocks.wizard.mockReturnValue({ userId: '41', hydrated: true, isCommercial: false, readOnly: false, draft: {},
    suitability: { state: { ...fresh(), selectedProgramme: 'PCP_L6', completionStatus: 'SUBMITTED', selectionStatus: 'SELECTION_CONFIRMED', selectionConfirmedAt: '2026-11-17T12:00:00Z' } },
    modules: { data: { configuration: modulesConfiguration, configurationVersion: 3, selectedProgramme: 'PCP_L6' } } });
  mocks.save.mockReturnValue({ selection: { ...selection, modulesByPathway: { chartered: [] } }, status: 'idle' });
  render(<ProgrammeModulesCard />);
  expect(screen.queryByRole('button', { name: 'Confirm My Cohort Modules' })).toBeNull();
});
