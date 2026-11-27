import { beforeEach, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import type { ProgrammeId } from '../types';
import { fresh } from './fixtures';
import { modulesConfiguration } from './modulesFixtures';

const mock = vi.hoisted(() => ({ wizard: {} as any, schedules: vi.fn(), save: vi.fn() }));
vi.mock('@/pages/users/wizard/WizardContext.helpers', () => ({ useWizard: () => mock.wizard }));
vi.mock('../api', () => ({ request: mock.schedules }));
vi.mock('../modules/useModulesConfiguration', () => ({ useModulesConfiguration: () => ({ data: null }) }));
vi.mock('../modules/useModuleSelectionSave', () => ({ useModuleSelectionSave: mock.save }));
import { ProgrammeModulesCard } from '../modules/ProgrammeModulesCard';

beforeEach(() => {
  vi.clearAllMocks();
  mock.wizard = { userId: '41', isCommercial: false, draft: { cvJob: { experienceText: 'Preserved CV' } },
    suitability: { state: { ...fresh(), selectedProgramme: 'ME_L4', completionStatus: 'SUBMITTED',
      programmeSource: 'catalogue', selectionStatus: 'SELECTION_CONFIRMED',
      confirmedAt: '2026-11-01T12:00:00Z', selectionConfirmedAt: '2026-11-01T12:01:00Z' } },
    modules: { data: { source: 'programme_catalogue', configuration: modulesConfiguration, configurationVersion: 3,
      assignedCohortName: 'Feb 2027',
      selectedProgramme: 'ME_L4', selectionComplete: true, catalogueProgramme: {
        id: 'ME_L4', title: 'Current Programme Structure name', pathways: [], modules: [
          { id: 'catalogue-module', title: 'Current Programme Structure module', description: '',
            sessions: 18, weeks: 14, order: 0, active: true },
        ],
      } } },
  };
});

it.each(['ME_L4', 'MM_L6', 'APM_L4', 'PCP_L6'] as ProgrammeId[])('shows only catalogue programme/modules for %s without scheduling or allocation', id => {
  mock.wizard.suitability.state.selectedProgramme = id;
  mock.wizard.modules.data.catalogueProgramme.id = id;
  render(<ProgrammeModulesCard />);
  const card = screen.getByRole('region', { name: 'Programme Modules' });
  expect(within(card).getByText('Current Programme Structure name')).toBeVisible();
  expect(within(card).getByText('· Cohort: Feb 2027')).toBeVisible();
  expect(within(card).getByText('· Cohort: Feb 2027').parentElement).toContainElement(within(card).getByText('Current Programme Structure name'));
  expect(within(card).getByText('Current Programme Structure module')).toBeVisible();
  expect(within(card).getByRole('columnheader', { name: 'Weeks' })).toBeVisible();
  expect(within(card).getByText('14 Weeks')).toBeVisible();
  expect(within(card).queryByText('18 Sessions')).not.toBeInTheDocument();
  expect(screen.queryByText('Module Schedule')).not.toBeInTheDocument();
  expect(screen.queryByRole('region', { name: 'Assigned LMS modules' })).not.toBeInTheDocument();
  expect(screen.queryByText('Cohort pending')).not.toBeInTheDocument();
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  expect(screen.queryByRole('radio')).not.toBeInTheDocument();
  expect(mock.schedules).not.toHaveBeenCalled();
  expect(mock.save).not.toHaveBeenCalled();
  expect(mock.wizard.draft.cvJob.experienceText).toBe('Preserved CV');
});

it('does not invent a cohort when no saved placement is returned', () => {
  mock.wizard.modules.data.assignedCohortName = null;
  render(<ProgrammeModulesCard />);
  expect(screen.getByText('· Cohort: Not assigned')).toBeVisible();
  expect(screen.queryByText('· Cohort: Feb 2027')).not.toBeInTheDocument();
  expect(mock.schedules).not.toHaveBeenCalled();
  expect(mock.save).not.toHaveBeenCalled();
});

it('shows an honest empty catalogue module list without falling back to old published modules', () => {
  mock.wizard.modules.data.catalogueProgramme.modules = [];
  render(<ProgrammeModulesCard />);
  expect(screen.getByText('No modules have been added to this programme yet.')).toBeVisible();
  expect(screen.queryByText('Marketing Impact and Planning')).not.toBeInTheDocument();
  expect(mock.schedules).not.toHaveBeenCalled();
});

it('keeps catalogue load failures visible with Retry instead of showing legacy or empty modules', () => {
  mock.wizard.modules = { error: 'Catalogue could not be loaded', retry: vi.fn() };
  render(<ProgrammeModulesCard />);
  expect(screen.getByRole('alert')).toHaveTextContent('Catalogue could not be loaded');
  expect(screen.getByRole('button', { name: 'Retry loading modules' })).toBeVisible();
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
});

it('does not fall back to LMS modules or scheduling when a catalogue response has no matching programme', () => {
  mock.wizard.modules.data.catalogueProgramme.id = 'MM_L6';
  render(<ProgrammeModulesCard />);
  expect(screen.getByRole('alert')).toHaveTextContent('could not be loaded from Programme Structure');
  expect(screen.getByRole('button', { name: 'Retry loading modules' })).toBeVisible();
  expect(screen.queryByText('Module Schedule')).not.toBeInTheDocument();
  expect(mock.schedules).not.toHaveBeenCalled();
});
