import { beforeEach, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { ModuleTimeline } from '../your-modules/ModuleTimeline';
import { cohortLanes, cohortRows, day, timelineAxis } from '../your-modules/timelineLayout';
import type { Offering, Result, YourModulesData } from '../your-modules/model';

let data: YourModulesData;
let results: Map<string, Result>;
const offer = (id: string, moduleId: string, cohortId: string, start: string, end: string): Offering => ({
  id, moduleId, cohortId, programmeId: 'programme', start, end, home: cohortId === 'home',
  cohortName: cohortId === 'home' ? 'Feb 2027' : cohortId === 'early' ? 'October 2026' : 'June 2027',
  cohortStart: cohortId === 'home' ? '2027-01-01' : cohortId === 'early' ? '2026-10-01' : '2027-06-01',
  groupName: `Group ${id}`, groupId: `group-${id}`, extendsWindow: end > '2027-12-31',
});
beforeEach(() => {
  data = { status: 'ready', programmeId: 'programme', selectedProgramme: 'ME_L4', evaluatedOn: '2026-11-27',
    homeCohort: { id: 'home', name: 'Feb 2027', start: '2027-01-01', end: '2027-12-31' },
    modules: [
      { id: 'planning', name: 'Marketing Impact and Planning', weeks: 13, revisionIds: [] },
      { id: 'social', name: 'Social Media', weeks: 13, revisionIds: [] },
      { id: 'technology', name: 'Marketing Technology (MarTech)', weeks: 13, revisionIds: [] },
    ], offerings: [
      offer('late-social', 'social', 'later', '2027-07-01', '2027-08-31'),
      offer('home-technology', 'technology', 'home', '2027-07-01', '2027-09-30'),
      offer('early-social', 'social', 'early', '2027-11-01', '2028-01-31'),
      offer('home-social', 'social', 'home', '2027-04-01', '2027-06-30'),
      offer('home-planning', 'planning', 'home', '2027-01-01', '2027-03-31'),
      offer('alternative-technology', 'technology', 'later', '2027-09-01', '2027-11-01'),
    ] };
  results = new Map<string, Result>([['planning', 'none'], ['social', 'potential'], ['technology', 'none']]);
});
const rows = () => cohortRows(data, data.modules, results);
const show = () => render(<ModuleTimeline data={data} modules={data.modules} results={results} />);

it('renders a single row per cohort with the home cohort first and alternatives in cohort-start order', () => {
  show();
  const table = screen.getByRole('table', { name: 'Module offerings by cohort' });
  expect(within(table).getAllByRole('rowheader').map(cell => cell.textContent)).toEqual([
    'Feb 2027 (Home cohort)', 'October 2026', 'June 2027',
  ]);
  expect(within(table).getAllByRole('row')).toHaveLength(4);
  const home = screen.getByTestId('cohort-home');
  expect(within(home).getAllByRole('article')).toHaveLength(3);
  expect(within(home).getByRole('rowheader')).not.toHaveTextContent('Marketing');
});

it('sorts the blocks within each cohort by actual module start date without mutating input', () => {
  const before = structuredClone(data);
  expect(rows()[0].offerings.map(item => item.offering.id)).toEqual(['home-planning', 'home-social', 'home-technology']);
  expect(data).toEqual(before);
});

it('shows alternatives for unflagged modules as well when another applicable module has an indication', () => {
  show();
  expect(screen.getByTestId('offering-alternative-technology')).toBeVisible();
  for (const id of ['early', 'later']) {
    const row = screen.getByTestId(`cohort-${id}`);
    expect(within(row).getAllByRole('article')).toHaveLength(id === 'later' ? 2 : 1);
    expect(within(row).getByText('Social Media')).toBeVisible();
    expect(within(row).getAllByText('Alternative option')).toHaveLength(id === 'later' ? 2 : 1);
  }
  expect(within(screen.getByTestId('cohort-home')).queryByText('Alternative option')).not.toBeInTheDocument();
});

it('has one home cohort row when no modules have potential prior learning', () => {
  results.set('social', 'none');
  show();
  expect(screen.getAllByRole('rowheader')).toHaveLength(1);
  expect(screen.getAllByRole('article')).toHaveLength(3);
});

it('does not enable alternatives from unavailable results alone', () => {
  results.set('social', 'unavailable');
  show();
  expect(screen.getAllByRole('rowheader')).toHaveLength(1);
  expect(screen.getAllByRole('article')).toHaveLength(3);
});

it('does not let an indication outside the applicable module selection enable alternatives', () => {
  const applicable = data.modules.filter(module => module.id !== 'social');
  const grouped = cohortRows(data, applicable, results);
  expect(grouped.map(row => row.id)).toEqual(['home']);
  expect(grouped[0].offerings.map(item => item.module.id)).toEqual(['planning', 'technology']);
});

it('retains future-date and home-window limits for alternatives of unflagged modules', () => {
  data.evaluatedOn = '2027-09-01';
  data.offerings!.push(offer('started', 'technology', 'later', '2027-08-31', '2027-10-01'),
    offer('outside', 'technology', 'later', '2028-01-01', '2028-02-01'));
  const grouped = rows();
  const ids = grouped.flatMap(row => row.offerings.map(item => item.offering.id));
  expect(ids).not.toContain('started');
  expect(ids).not.toContain('outside');
  expect(ids).toContain('alternative-technology');
});

it('retains each block name, real dates, genuine group and window annotation', () => {
  show();
  const block = screen.getByTestId('offering-early-social');
  expect(within(block).getByRole('heading', { name: 'Social Media' })).toBeVisible();
  expect(within(block).getByText('01 Nov 2027')).toHaveAttribute('dateTime', '2027-11-01');
  expect(within(block).getByText('31 Jan 2028')).toHaveAttribute('dateTime', '2028-01-31');
  expect(within(block).getByText('Group early-social')).toBeVisible();
  expect(block).toHaveAccessibleName(/Social Media, October 2026.*Alternative option/);
  expect(block).toHaveTextContent('Extends beyond the home-cohort window');
  expect(screen.getByRole('region', { name: 'Scrollable cohort timeline' })).toHaveAttribute('tabIndex', '0');
});

it('fits nonoverlapping module blocks into a single lane in their shared cohort row', () => {
  const grouped = rows();
  const packed = cohortLanes(grouped[0], timelineAxis(grouped, data.homeCohort!));
  expect(packed.count).toBe(1);
  expect(packed.blocks.map(block => block.lane)).toEqual([0, 0, 0]);
});

it('stacks overlapping genuine deliveries inside the same cohort row without merging groups', () => {
  data.offerings!.push(offer('second-group', 'social', 'home', '2027-04-15', '2027-06-20'));
  show();
  const row = screen.getByTestId('cohort-home');
  expect(within(row).getAllByRole('article')).toHaveLength(4);
  expect(screen.getAllByRole('rowheader')).toHaveLength(3);
  expect(screen.getByTestId('offering-home-social')).toHaveAttribute('data-lane', '0');
  expect(screen.getByTestId('offering-second-group')).toHaveAttribute('data-lane', '1');
  expect(within(row).getByText('Group second-group')).toBeVisible();
});

it('also separates visually colliding labels for short, nonoverlapping deliveries while retaining true bar widths', () => {
  data.offerings = [offer('short-a', 'social', 'home', '2027-01-01', '2027-01-01'),
    offer('short-b', 'social', 'home', '2027-01-03', '2027-01-03')];
  const grouped = rows(); const axis = timelineAxis(grouped, data.homeCohort!);
  const packed = cohortLanes(grouped[0], axis);
  expect(packed.count).toBe(2);
  expect(packed.blocks[0].width).toBeCloseTo(axis.width / axis.span);
  expect(packed.blocks[0].labelWidth).toBeGreaterThan(packed.blocks[0].width);
});

it('positions bars proportionally using inclusive end dates and shows actual month/year headings', () => {
  const grouped = rows(); const axis = timelineAxis(grouped, data.homeCohort!);
  const packed = cohortLanes(grouped[0], axis);
  expect(packed.blocks[0].start).toBe(0);
  expect(packed.blocks[0].width).toBeCloseTo((day('2027-04-01') - day('2027-01-01')) / axis.span * axis.width);
  expect(packed.blocks[1].start).toBeCloseTo(packed.blocks[0].width);
  expect(axis.months[0].label).toBe('Jan 2027');
  expect(axis.months.at(-1)!.label).toBe('Jan 2028');
});

it('does not create empty cohort rows or invent groups', () => {
  data.offerings = [];
  show();
  expect(screen.queryByRole('rowheader')).not.toBeInTheDocument();
  expect(screen.getByText(/No published schedule is available/)).toBeVisible();
});

it('omits a missing group label rather than manufacturing a placeholder', () => {
  data.offerings = [{ ...offer('ungrouped-label', 'social', 'home', '2027-01-01', '2027-03-31'), groupName: undefined }];
  show();
  const block = screen.getByRole('article');
  expect(block).not.toHaveTextContent('Group');
  expect(block).not.toHaveAccessibleName(/undefined/);
});

it('does not guess missing cohort start dates from labels or offering dates in older API responses', () => {
  data.offerings!.find(item => item.cohortId === 'early')!.cohortStart = undefined;
  expect(rows().map(row => row.id)).toEqual(['home', 'later', 'early']);
});
