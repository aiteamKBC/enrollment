import { render, screen, within, cleanup } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { LmsAssignedModules } from '../modules/LmsAssignedModules';
import type { HomeCohort } from '../modules/configuration';

afterEach(cleanup);
const home: HomeCohort = { status: 'resolved', source: 'lms', programmeId: 'ME_L4',
  cohortId: 'actual-cohort', name: 'Live cohort', groupId: 'g1', groupName: 'Live group', layoutVersion: null,
  lmsModules: [{ id: 'live-module', title: 'Live module', sessions: 16, start: null, end: null }] };

it('shows the saved LMS cohort, group and undated modules in a read-only shared panel', () => {
  render(<LmsAssignedModules home={home} />);
  const panel = within(screen.getByRole('region', { name: 'Assigned LMS modules' }));
  expect(panel.getByText('Cohort: Live cohort · Group: Live group')).toBeVisible();
  expect(panel.getByText('Live module')).toBeVisible();
  expect(panel.getAllByText('Pending')).toHaveLength(2);
  expect(panel.queryByRole('button')).toBeNull();
});

it('does not guess groups or modules for an empty assignment', () => {
  render(<LmsAssignedModules home={{ ...home, groupId: null, groupName: null, lmsModules: [] }} />);
  expect(screen.getByText(/Group pending/)).toBeVisible();
  expect(screen.queryByText('Live module')).toBeNull();
});

it('shows an outage without displaying stale LMS records', () => {
  render(<LmsAssignedModules home={{ ...home, status: 'pending', reason: 'lms_unavailable' }} />);
  expect(screen.getByRole('status')).toHaveTextContent('temporarily unavailable');
  expect(screen.queryByText('Live module')).toBeNull();
});

it('preserves deployments using the existing published cohort display', () => {
  const { container } = render(<LmsAssignedModules home={{ ...home, source: undefined }} />);
  expect(container).toBeEmptyDOMElement();
});

it('explains a saved legacy cohort missing from LMS instead of asking the learner to reconfirm', () => {
  render(<LmsAssignedModules home={{ ...home, status: 'pending', reason: 'assigned_cohort_not_in_lms', assignedCohortName: 'Feb 2027' }} />);
  expect(screen.getByRole('status')).toHaveTextContent('Feb 2027');
  expect(screen.getByRole('status')).toHaveTextContent('not available in LMS');
  expect(screen.getByRole('status')).not.toHaveTextContent('needs to be confirmed');
});

it('labels each available group without assigning an unplaced learner to the first one', () => {
  render(<LmsAssignedModules home={{ ...home, groupId: null, groupName: null, lmsModules: [
    { id: 'g1-m1', title: 'Same module', sessions: 16, start: null, end: null, groupName: 'G1' },
    { id: 'g2-m1', title: 'Same module', sessions: 16, start: null, end: null, groupName: 'G2' },
  ] }} />);
  expect(screen.getByText('G1')).toBeVisible();
  expect(screen.getByText('G2')).toBeVisible();
  expect(screen.getByText(/A group has not been assigned/)).toBeVisible();
});

it('shows the LMS week duration without relabelling its stored session count as actual sessions', () => {
  render(<LmsAssignedModules home={{ ...home, lmsModules: [{ id: 'm1', title: 'Martech', sessions: 99, weeks: 20, start: null, end: null }] }} />);
  expect(screen.getByRole('columnheader', { name: 'Duration (weeks)' })).toBeVisible();
  expect(screen.getByText('20')).toBeVisible();
  expect(screen.getByText('Martech')).toBeVisible();
  expect(screen.queryByRole('columnheader', { name: 'Sessions' })).toBeNull();
  expect(screen.queryByText('99')).toBeNull();
});
