import { afterEach, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { WizardContext, type WizardContextValue } from '@/pages/users/wizard/WizardContext.helpers';
import ModuleSchedulingPreview from '../modules/ModuleSchedulingPreview';
import { ProgrammeModulesCard } from '../modules/ProgrammeModulesCard';
import { schedulingPreviewEnabled } from '../modules/schedulingPreviewMode';
import { mandatoryIds, type ProgrammeModuleSelection, type HomeCohort, type ModulesConfiguration, type ProgrammeModules } from '../modules/configuration';
import published from './pcp-rules-defaults.json';
import { fresh, result } from './fixtures';
import { londonDate, futureOfferings, sampleOfferings, timelineMonths, offeringPosition } from '../modules/schedulingTimeline';

const modulesConfiguration = published as ModulesConfiguration;
const PROGRAMME_MODULES = Object.fromEntries(modulesConfiguration.programmes.map(p => [p.id, p])) as Record<string, ProgrammeModules>;
const operational = PROGRAMME_MODULES.PCP_L6.pathways.find(p => p.id === 'operational')!;
const selection: ProgrammeModuleSelection = { programme: 'PCP_L6', pathway: 'operational', configurationVersion: 24,
  modulesByPathway: { operational: [...mandatoryIds(operational), 'planning-control', 'earned-value'], strategic: ['portfolio', 'pmi-pmo'] } };
afterEach(() => { vi.unstubAllEnvs(); window.history.replaceState({}, '', '/'); });

it('zooms one shared date scale with stacked informational lanes and anchors the visible centre date', async () => {
  const user = userEvent.setup();
  const { container } = render(<ModuleSchedulingPreview samplePreview programme={PROGRAMME_MODULES.PCP_L6} selection={selection}
    homeCohort={{ status: 'resolved', programmeId: 'PCP_L6', cohortId: 'home', name: 'Saved home', layoutVersion: 24 }} />);
  const view = screen.getByRole('region', { name: 'Scrollable module timeline' });
  Object.defineProperty(view, 'clientWidth', { value: 600 });
  view.scrollLeft = 400;
  const scrollTo = vi.fn(); view.scrollTo = scrollTo;
  const canvas = view.firstElementChild as HTMLElement;
  const baseWidth = parseFloat(canvas.style.getPropertyValue('--timeline-width'));
  const bar = container.querySelector<HTMLElement>('[data-offering-id]')!;
  const position = bar.style.cssText;
  const dates = [bar.dataset.start, bar.dataset.end];
  expect(screen.queryByRole('button', { name: /Select sample/ })).toBeNull();
  const minus = screen.getByRole('button', { name: 'Zoom out timeline' });
  const plus = screen.getByRole('button', { name: 'Zoom in timeline' });
  const reset = screen.getByRole('button', { name: 'Reset timeline zoom to 100%' });
  await user.click(minus);
  expect(reset).toHaveTextContent('80%');
  expect(scrollTo).toHaveBeenLastCalledWith({ left: 260, behavior: 'instant' });
  await user.click(minus);
  expect(minus).toBeDisabled(); expect(reset).toHaveTextContent('60%');
  expect(parseFloat(canvas.style.getPropertyValue('--timeline-width'))).toBeCloseTo(baseWidth * .6);
  expect(bar.style.cssText).toBe(position);
  expect([bar.dataset.start, bar.dataset.end]).toEqual(dates);
  expect(screen.queryByRole('button', { name: /Select sample/ })).toBeNull();
  expect(screen.getByText('Saved home')).toBeVisible();
  await user.click(reset);
  expect(canvas.style.getPropertyValue('--timeline-width')).toBe(`${baseWidth}px`);
  await user.click(plus); await user.click(plus);
  expect(plus).toBeDisabled(); expect(reset).toHaveTextContent('140%');
  expect(parseFloat(canvas.style.getPropertyValue('--timeline-width'))).toBeCloseTo(baseWidth * 1.4);
  expect(screen.queryByRole('button', { name: /Select sample/ })).toBeNull();
});

it('requires explicit opt-in on localhost and cannot be enabled in production', () => {
  expect(schedulingPreviewEnabled()).toBe(false);
  window.history.replaceState({}, '', '/?moduleSchedulePreview=1');
  expect(schedulingPreviewEnabled()).toBe(true);
  vi.stubEnv('DEV', false);
  expect(schedulingPreviewEnabled()).toBe(false);
});

it.each(['ME_L4', 'MM_L6', 'APM_L4'] as const)('%s retains fixed academics and offers no selectors or invented dates', programme => {
  render(<ModuleSchedulingPreview samplePreview programme={PROGRAMME_MODULES[programme]} />);
  expect(screen.getAllByText('Schedule not available yet')).toHaveLength(PROGRAMME_MODULES[programme].modules.length);
  expect(screen.queryByRole('radio')).toBeNull();
  expect(screen.queryByRole('combobox')).toBeNull();
  expect(screen.queryByText('START DATE')).toBeNull();
});

it('uses active-path mandatory and confirmed optional modules with one sticky title and separate offering lanes', () => {
  const saved = JSON.stringify(selection);
  const { container } = render(<ModuleSchedulingPreview samplePreview programme={PROGRAMME_MODULES.PCP_L6} selection={selection} />);
  expect(screen.getAllByRole('heading', { name: 'Project Planning and Control (APMG Qualification)' })).toHaveLength(2);
  expect(screen.queryByRole('heading', { name: 'Portfolio Management (APMG)' })).toBeNull();
  expect(screen.queryByRole('heading', { name: 'PMI (PMO)' })).toBeNull();
  expect(screen.queryByRole('heading', { name: 'PMI-SP' })).toBeNull();
  expect(container.querySelectorAll('[data-module-label="pmp"]')).toHaveLength(1);
  expect(container.querySelector('[data-academic-module-id="pmp"]')?.querySelectorAll('[data-offering-id]')).toHaveLength(2);
  expect(screen.queryByRole('button', { name: /Select sample/ })).toBeNull();
  expect(JSON.stringify(selection)).toBe(saved);
  expect(screen.queryByRole('button', { name: 'Reset preview' })).toBeNull();
});

it('changes academic rows when the active pathway changes without offering selectors', () => {
  const { rerender } = render(<ModuleSchedulingPreview samplePreview programme={PROGRAMME_MODULES.PCP_L6} selection={selection} />);
  expect(screen.queryByRole('button', { name: /Select sample/ })).toBeNull();
  rerender(<ModuleSchedulingPreview samplePreview programme={PROGRAMME_MODULES.PCP_L6} selection={{ ...selection, pathway: 'strategic' }} />);
  expect(screen.queryByRole('button', { name: /Select sample/ })).toBeNull();
  expect(screen.queryByRole('heading', { name: 'Earned Value Management (EVM)' })).toBeNull();
});

it('integrates with the real card using one persisted home cohort, with no save callbacks or scheduling requests', async () => {
  window.history.replaceState({}, '', '/?moduleSchedulePreview=1');
  const save = vi.fn(); const setSection = vi.fn(); const fetch = vi.spyOn(window, 'fetch');
  const homeCohort: HomeCohort = { programmeId: 'PCP_L6', status: 'resolved', cohortId: 'saved-home-id', name: 'Saved February cohort', layoutVersion: 24 };
  const value = { userId: 'synthetic-preview', isCommercial: false, hydrated: true, readOnly: false, saveModules: save, setSection,
    draft: { programmeModules: selection }, suitability: { state: { ...fresh(), result, selectedProgramme: 'PCP_L6', completionStatus: 'SUBMITTED', selectionStatus: 'SELECTION_CONFIRMED', selectionConfirmedAt: '2026-11-01T12:00:00Z' } },
    modules: { data: { configuration: modulesConfiguration, configurationVersion: 24, homeCohort }, error: '', retry: vi.fn() },
  } as unknown as WizardContextValue;
  const before = JSON.stringify(value.draft);
  const { unmount } = render(<WizardContext.Provider value={value}><ProgrammeModulesCard /></WizardContext.Provider>);
  await screen.findByRole('region', { name: 'Module scheduling preview' });
  expect(screen.getAllByText('Saved February cohort')).toHaveLength(1);
  expect(screen.queryByRole('radiogroup', { name: /cohort/i })).toBeNull();
  expect(screen.queryByRole('combobox')).toBeNull();
  expect(screen.queryByRole('button', { name: /Select sample G2/ })).toBeNull();
  expect(screen.getByText('Saved February cohort')).toBeVisible();
  expect(JSON.stringify(value.draft)).toBe(before);
  unmount();
  expect(save).not.toHaveBeenCalled(); expect(setSection).not.toHaveBeenCalled(); expect(fetch).not.toHaveBeenCalled();
  fetch.mockRestore();
});

it('keeps the persisted home identity as the first row without fabricated bars, including pending assignments', () => {
  const { container, rerender } = render(<ModuleSchedulingPreview samplePreview programme={PROGRAMME_MODULES.PCP_L6} selection={selection}
    homeCohort={{ status: 'resolved', programmeId: 'PCP_L6', cohortId: 'real-saved-id', name: 'Saved cohort', layoutVersion: 24 }} />);
  const first = container.querySelector('[data-timeline-row]');
  expect(first).toHaveAttribute('data-timeline-row', 'home');
  expect(first).toHaveTextContent('Saved cohort');
  expect(first?.querySelector('[data-offering-id]')).toBeNull();
  expect(screen.getByText('No future home-cohort delivery yet')).toBeVisible();
  rerender(<ModuleSchedulingPreview samplePreview programme={PROGRAMME_MODULES.PCP_L6} selection={selection} />);
  expect(screen.getAllByText('Cohort pending')).toHaveLength(1);
});

it('renders one rectangle for a single-offering module and positions all rectangles using dates', () => {
  const { container } = render(<ModuleSchedulingPreview samplePreview programme={PROGRAMME_MODULES.PCP_L6} selection={selection} />);
  const all = [...container.querySelectorAll<HTMLElement>('[data-offering-id]')];
  expect(all.filter(el => el.dataset.offeringId?.includes('earned-value'))).toHaveLength(1);
  expect(all.filter(el => el.dataset.offeringId?.includes('pmp'))).toHaveLength(2);
  const today = londonDate(); const offerings = sampleOfferings([...mandatoryIds(operational), 'planning-control', 'earned-value'], today);
  const domain = timelineMonths(offerings, today);
  for (const el of all) {
    const offering = offerings.find(o => o.id === el.dataset.offeringId)!;
    const expected = offeringPosition(offering, domain.start, domain.totalDays);
    expect(parseFloat(el.style.left)).toBeCloseTo(parseFloat(expected.left));
    expect(parseFloat(el.style.width)).toBeCloseTo(parseFloat(expected.width));
    expect(offering.start > today).toBe(true);
  }
});

it('uses the UK calendar day, excludes already-started offerings, and spans year boundaries', () => {
  expect(londonDate(new Date('2026-06-15T23:30:00Z'))).toBe('2026-06-16');
  const items = sampleOfferings(['a', 'b', 'c', 'd', 'e'], '2026-11-14');
  const date = items[0].start;
  expect(futureOfferings(items, date).some(o => o.start === date)).toBe(false);
  expect(futureOfferings(items, date).every(o => o.start > date)).toBe(true);
  const months = timelineMonths(items, '2026-11-14').months;
  expect(new Set(months.map(m => m.year)).size).toBeGreaterThan(1);
  expect(months.reduce((sum, m) => sum + m.width, 0)).toBeCloseTo(100);
});
