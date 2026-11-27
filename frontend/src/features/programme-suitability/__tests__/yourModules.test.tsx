import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { defaultLayout, layoutForLearner, resolveLayout, visibleSteps } from '@/pages/users/wizard/layout/resolve';
import { bindImpactModules, screenModule, screeningRuleFromImpact, type BoundModule, type ModuleImpact } from '../skills-radar/moduleImpact';
import { moduleResults, permittedOfferings, type Offering, type YourModulesData } from '../your-modules/model';

const mock = vi.hoisted(() => ({ request: vi.fn(), wizard: {} as any }));
vi.mock('../api', () => ({ request: mock.request }));
vi.mock('@/pages/users/wizard/WizardContext.helpers', () => ({ useWizard: () => mock.wizard }));
import YourModules from '@/pages/users/wizard/steps/YourModules';

let data: YourModulesData;
let impact: ModuleImpact;
let bound: BoundModule[];
const offering = (id: string, moduleId: string, cohortId: string, start: string): Offering => ({
  id, moduleId, cohortId, programmeId: 'programme-uuid', cohortName: cohortId === 'home' ? 'February 2027' : 'October 2026',
  groupName: `Genuine group ${id}`, groupId: `group-${id}`, start, end: '2028-02-20',
  home: cohortId === 'home', extendsWindow: true,
});

beforeEach(() => {
  vi.clearAllMocks();
  data = { status: 'ready', version: 7, selectedProgramme: 'ME_L4', programmeId: 'programme-uuid',
    programmeName: 'Confirmed programme', homeCohort: { id: 'home', name: 'February 2027', start: '2027-01-05', end: '2028-01-20' },
    evaluatedOn: '2026-11-27', modules: [
      { id: 'planning', name: 'Planning', weeks: 20, revisionIds: ['revision-planning'] },
      { id: 'social', name: 'Social Media', weeks: 14, revisionIds: ['revision-social'] },
      { id: 'technology', name: 'Technology', weeks: 15, revisionIds: ['revision-technology'] },
    ], offerings: [
      offering('planning-home', 'planning', 'home', '2027-02-01'), offering('planning-alternative', 'planning', 'early', '2027-03-01'),
      offering('social-home', 'social', 'home', '2027-06-01'), offering('social-first', 'social', 'early', '2027-01-05'),
      { ...offering('social-last', 'social', 'later', '2028-01-20'), cohortName: 'June 2027' },
      offering('technology-home', 'technology', 'home', '2027-09-01'), offering('technology-alternative', 'technology', 'later', '2027-10-01'),
    ] };
  impact = { status: 'ready', programmeId: 'ME_L4', minimumQualifyingRating: 5, maximumQualifyingRating: 9,
    minimumHighRatedHardKsbs: 2, savedLevels: { 'question-1': 'mastery', 'question-2': 'consistently' },
    modules: data.modules.map(module => ({ id: `academic-${module.id}`, name: module.name,
      status: 'ready', revisionId: `revision-${module.id}`, ksbs: [1, 2].map(index => ({
        id: `ksb-${index}`, code: `K${index}`, description: `Synthetic ${index}`, kind: 'Knowledge', standard: 'ST0596', standardVersion: '1.0',
        weight: { total_weight: '10', hard_weight: module.id === 'social' ? '10' : '0', hard_count: module.id === 'social' ? 1 : 0, mapping_count: 1 },
      })) })) };
  bound = impact.modules.map(module => ({ id: module.id, title: module.name, impact: module,
    questions: module.ksbs.map((ksb, index) => ({ id: `question-${index + 1}`, codes: [ksb.code] } as any)) }));
  mock.wizard = { userId: '41', isCommercial: false, ilrSavedAt: '', radarImpact: impact, radarBoundModules: bound,
    retryRadarImpact: vi.fn(), suitability: { state: { selectedProgramme: 'ME_L4', recommendedProgramme: 'MM_L6' } },
    setSection: vi.fn(), saveIlr: vi.fn(), saveModules: vi.fn() };
  mock.request.mockResolvedValue(data);
});

it('registers fourteen steps with Your Modules immediately after Skills Radar and before Inclusion', () => {
  const steps = visibleSteps(layoutForLearner(defaultLayout(), false));
  expect(steps).toHaveLength(14);
  expect(steps.slice(8, 11).map(step => step.slug)).toEqual(['skills-radar', 'your-modules', 'inclusion']);
});

it('keeps old pinned journeys and their progress denominator unchanged until explicitly enabled and published', () => {
  const old = defaultLayout(); old.steps = old.steps.filter(step => step.slug !== 'your-modules');
  const original = structuredClone(old);
  const resolved = resolveLayout(old);
  expect(visibleSteps(resolved)).toHaveLength(13);
  expect(resolved.steps.find(step => step.slug === 'your-modules')?.hidden).toBe(true);
  expect(old).toEqual(original);
  resolved.steps.find(step => step.slug === 'your-modules')!.hidden = false;
  expect(visibleSteps(resolveLayout(resolved))).toHaveLength(14);
});

it('reuses the exact existing module-level calculator with saved answers and configured thresholds', () => {
  const result = moduleResults(data, impact, bound);
  const record = bound[1].impact!;
  const answers = Object.fromEntries(Object.entries(impact.savedLevels!).map(([id, level]) => [id, { ksbId: id, level, evidenceFiles: [], actionPlan: null }])) as any;
  expect(result.results.get('social')).toBe(screenModule(record, answers, new Map([['K1', 'question-1'], ['K2', 'question-2']]), screeningRuleFromImpact(impact)).status);
  expect([...result.results.values()]).toEqual(['none', 'potential', 'none']);
  impact.minimumHighRatedHardKsbs = 3;
  expect(moduleResults(data, impact, bound).results.get('social')).toBe('none');
});

it('preserves a potential indication from Skills Radar even when another saved KSB is unrated', () => {
  bound[1].impact!.ksbs.push({ ...bound[1].impact!.ksbs[0], id: 'unrated-ksb', code: 'K3' });
  expect(moduleResults(data, impact, bound).results.get('social')).toBe('potential');
});

it('shows only home schedules when no module is flagged', () => {
  for (const module of data.modules) expect(permittedOfferings(data, module.id, false).map(row => row.cohortId)).toEqual(['home']);
});

it('allows eligible alternatives for every applicable module when any module has a verified indication', () => {
  const { modules, results } = moduleResults(data, impact, bound);
  const allowAlternatives = modules.some(module => results.get(module.id) === 'potential');
  expect(permittedOfferings(data, 'planning', allowAlternatives).map(row => row.id)).toEqual(['planning-home', 'planning-alternative']);
  expect(permittedOfferings(data, 'social', allowAlternatives).map(row => row.id)).toEqual(['social-home', 'social-first', 'social-last']);
  expect(permittedOfferings(data, 'technology', allowAlternatives).map(row => row.id)).toEqual(['technology-home', 'technology-alternative']);
});

it.each(['2027-01-05', '2027-02-01', '2028-01-20'])('includes an alternative starting on %s using actual inclusive dates, not the cohort label', start => {
  data.offerings = [offering('boundary', 'social', 'alternative', start)];
  expect(permittedOfferings(data, 'social', true)).toHaveLength(1);
});

it.each(['2027-01-04', '2028-01-21'])('excludes alternatives starting outside the home window: %s', start => {
  data.offerings = [offering('outside', 'social', 'alternative', start)];
  expect(permittedOfferings(data, 'social', true)).toEqual([]);
});

it('excludes already-started alternatives but preserves started home teaching', () => {
  data.evaluatedOn = '2027-06-02';
  expect(permittedOfferings(data, 'social', true).map(row => row.id)).toEqual(['social-home', 'social-last']);
});

it('requires stable programme and revision identities rather than similar module names', () => {
  impact.programmeId = 'MM_L6';
  expect(moduleResults(data, impact, bound).results.get('social')).toBe('unavailable');
  impact.programmeId = 'ME_L4';
  data.modules[1].revisionIds = ['unrelated-revision'];
  expect(moduleResults(data, impact, bound).results.get('social')).toBe('unavailable');
  data.offerings!.push({ ...offering('foreign', 'social', 'alternative', '2027-03-01'), programmeId: 'another-programme' });
  expect(permittedOfferings(data, 'social', true).some(row => row.id === 'foreign')).toBe(false);
});

it('does not convert unavailable Skills Radar metadata into a negative indication', () => {
  const result = moduleResults(data, null, []);
  expect([...result.results.values()]).toEqual(['unavailable', 'unavailable', 'unavailable']);
  expect(permittedOfferings(data, 'social', false).map(row => row.id)).toEqual(['social-home']);
});

it('keeps saved PCP pathway scope and does not invent optional module choices', () => {
  data.requiresModuleSelection = true;
  impact.modules = impact.modules.slice(0, 2);
  const result = moduleResults(data, impact, [bound[0], bound[1]]);
  expect(result.selectionKnown).toBe(true);
  expect(result.modules.map(module => module.id)).toEqual(['planning', 'social']);
  expect(moduleResults(data, null, []).selectionKnown).toBe(false);
});

it('renders the actual table and separate delivery bars with no allocation or LMS requests', async () => {
  const before = structuredClone(data);
  render(<YourModules />);
  const moduleTable = await screen.findByRole('table', { name: '' });
  expect(moduleTable).toBeVisible();
  const social = within(moduleTable).getByText('Social Media').closest('tr')!;
  expect(within(social).getByText('Potential Prior Learning')).toBeVisible();
  expect(screen.getAllByText('No prior-learning indication')).toHaveLength(2);
  expect(screen.getByTestId('offering-social-first').closest('tr')).toHaveTextContent('October 2026');
  expect(screen.getByTestId('offering-social-last').closest('tr')).toHaveTextContent('June 2027');
  expect(screen.getByTestId('offering-social-last')).toHaveTextContent('20 Feb 2028');
  expect(screen.getByTestId('offering-social-last')).toHaveTextContent('Extends beyond');
  expect(screen.getByTestId('offering-planning-alternative')).toBeVisible();
  expect(screen.getByTestId('offering-technology-alternative')).toBeVisible();
  expect(mock.request.mock.calls).toEqual([['/enrolment_api/programme-suitability/apprenticeship/41/your-modules/']]);
  expect(mock.wizard.setSection).not.toHaveBeenCalled();
  expect(mock.wizard.saveIlr).not.toHaveBeenCalled();
  expect(mock.wizard.saveModules).not.toHaveBeenCalled();
  expect(data).toEqual(before);
});

it('updates from refreshed saved Skills Radar answers without changing programme or cohort', async () => {
  const view = render(<YourModules />);
  expect(await screen.findByTestId('offering-social-first')).toBeVisible();
  mock.wizard.radarImpact = { ...impact, savedLevels: { 'question-1': 'never', 'question-2': 'never' } };
  mock.wizard.ilrSavedAt = '2026-11-27T12:00:00Z';
  view.rerender(<YourModules />);
  await waitFor(() => expect(screen.getAllByText('No prior-learning indication')).toHaveLength(3));
  expect(screen.queryByTestId('offering-social-first')).not.toBeInTheDocument();
  expect(screen.queryByTestId('offering-planning-alternative')).not.toBeInTheDocument();
  expect(screen.queryByTestId('offering-technology-alternative')).not.toBeInTheDocument();
  expect(screen.getByTestId('offering-social-home')).toBeVisible();
});

it('keeps individual results accurate when one verified indication opens alternatives for an unavailable module', async () => {
  impact.modules[2].status = 'unavailable';
  render(<YourModules />);
  const table = await screen.findByRole('table', { name: '' });
  expect(within(table).getByText('Technology').closest('tr')).toHaveTextContent('Skills Radar result unavailable');
  expect(within(table).getAllByText('Potential Prior Learning')).toHaveLength(1);
  expect(screen.getByTestId('offering-technology-alternative')).toBeVisible();
  expect(screen.getByText(/An unavailable result is not a negative assessment/)).toBeVisible();
});

it('shows accurate missing schedules and unpublished prerequisites without fake data', async () => {
  data.offerings = [];
  const view = render(<YourModules />);
  expect(await screen.findAllByText(/No published schedule is available/)).toHaveLength(1);
  view.unmount();
  mock.request.mockResolvedValue({ status: 'unavailable', modules: [], message: 'Your permanent home cohort has not been saved.' });
  render(<YourModules />);
  expect(await screen.findByText('Your permanent home cohort has not been saved.')).toBeVisible();
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
});

it('isolates load errors with Retry and supports Admin and Student learner contexts', async () => {
  mock.request.mockRejectedValueOnce(new Error('Synthetic Programme Structure failure'));
  render(<YourModules />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Synthetic Programme Structure failure');
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByRole('table', { name: '' })).toBeVisible();
  expect(mock.wizard.retryRadarImpact).toHaveBeenCalledOnce();
});

it('rejects an unverified question binding through the existing binder', () => {
  const guarded = bindImpactModules(bound, [], impact, 'ME_L4', 'standard:st0596-v1-0');
  expect(moduleResults(data, impact, guarded).results.get('social')).toBe('unavailable');
});
