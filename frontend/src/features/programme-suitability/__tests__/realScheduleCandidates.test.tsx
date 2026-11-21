import { afterEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within, waitFor } from '@testing-library/react';
import ModuleSchedulingPreview from '../modules/ModuleSchedulingPreview';
import { request } from '../api';
import { mandatoryIds, type ModulesConfiguration, type ProgrammeModuleSelection } from '../modules/configuration';
import type { ScheduleResponse, ScheduleOffering } from '../modules/useModuleSchedules';
import published from './pcp-rules-defaults.json';

vi.mock('../api', () => ({ request: vi.fn() }));
const programme=(published as ModulesConfiguration).programmes.find(p => p.id==='PCP_L6')!;
const path=programme.pathways.find(p => p.id==='chartered')!;
const selection: ProgrammeModuleSelection={programme:'PCP_L6',pathway:'chartered',modulesByPathway:{chartered:[...mandatoryIds(path),'portfolio'],operational:['earned-value']}};
const home={status:'resolved' as const,programmeId:'PCP_L6' as const,cohortId:'saved-home',name:'February 2027',layoutVersion:24};
const context={kind:'apprenticeship' as const,id:'synthetic'};
function response(mode: 'normal' | 'review' | 'exact' = 'normal'): ScheduleResponse {
  const review=mode==='review'; const exact=mode==='exact';
  const offerings: ScheduleOffering[]=['G1','G2'].map((group,i) => ({id:`test-delivery-${group}`,moduleId:'portfolio',academicTitle:'Portfolio Management (APMG)',lmsTitle:'Portfolio Management',delivery:'delivery-oct',cohortId:'delivery-oct',cohortName:'October 2026',groupId:group,group,start:`2028-06-0${7+i}`,end:i?'2028-09-27':'2028-09-20',approved:false,reviewOnly:review,matchConfidence:exact?'exact_title':'tentative',provisional:!exact,allowLocalSelection:!review}));
  return {programmeId:'PCP_L6',pathway:'chartered',review,asOfDate:'2026-11-14',offeringCount:2,
    modules:[{academicModuleId:'portfolio',academicTitle:'Portfolio Management (APMG)',reason:'Review required',offerings}]};
}
afterEach(() => { vi.clearAllMocks(); window.history.replaceState({}, '', '/'); });

it('refreshes the matching learner timeline to the backend-confirmed group without keeping the eligible-group cache', async () => {
  const data = response('exact');
  const other = data.modules[0].offerings[0];
  data.homeGroup = { status: 'eligible', groupId: 'g1' };
  data.modules[0].offerings.push(
    { ...other, id: 'home-g1', cohortId: home.cohortId, delivery: home.cohortId, groupId: 'g1', group: 'Group 1' },
    { ...other, id: 'home-g2', cohortId: home.cohortId, delivery: home.cohortId, groupId: 'g2', group: 'Group 2' });
  vi.mocked(request).mockResolvedValue(data);
  const view = render(<ModuleSchedulingPreview programme={programme} selection={selection} homeCohort={home} scheduleContext={context} />);
  await waitFor(() => expect(view.container.querySelector('[data-offering-id="home-g1"]')).not.toBeNull());
  const reads = vi.mocked(request).mock.calls.length;
  fireEvent(window, new CustomEvent('pcp-cohort-modules-confirmed', { detail: { ...context, id: 'another-learner' } }));
  expect(request).toHaveBeenCalledTimes(reads);
  vi.mocked(request).mockResolvedValue({ ...data, homeGroup: { status: 'allocated', groupId: 'g2' } });
  fireEvent(window, new CustomEvent('pcp-cohort-modules-confirmed', { detail: context }));
  await waitFor(() => expect(view.container.querySelector('[data-offering-id="home-g2"]')).not.toBeNull());
  expect(view.container.querySelector('[data-offering-id="home-g1"]')).toBeNull();
  expect(view.container.querySelectorAll('[data-cohort-id="delivery-oct"]')).toHaveLength(2);
});

it('uses the cohort LMS title and week duration consistently in the row and timeline bar', async () => {
  const me = (published as ModulesConfiguration).programmes.find(p => p.id === 'ME_L4')!;
  const module = me.modules.find(m => m.id === 'martech')!;
  const payload: ScheduleResponse = { programmeId: 'ME_L4', pathway: null, review: false, asOfDate: '2026-11-17', offeringCount: 1,
    homeGroup: { status: 'assigned', groupId: 'g1' },
    modules: [{ academicModuleId: module.id, academicTitle: module.title, reason: '', offerings: [{
      id: 'live-martech', moduleId: module.id, academicTitle: module.title, lmsTitle: 'Martech',
      delivery: 'home-me', cohortId: 'home-me', cohortName: 'Feb 2027', groupId: 'g1', group: 'Group 1',
      start: '2028-02-01', end: '2028-06-20', weeks: 20, approved: false, reviewOnly: false,
      matchConfidence: 'confirmed_alias', provisional: false, allowLocalSelection: false,
    }] }] };
  vi.mocked(request).mockResolvedValue(payload);
  const { container } = render(<ModuleSchedulingPreview programme={{ ...me, modules: [module] }}
    homeCohort={{ status: 'resolved', programmeId: 'ME_L4', cohortId: 'home-me', name: 'Feb 2027', layoutVersion: null }} scheduleContext={context} />);
  await screen.findByText(/Confirmed LMS module match/);
  const bar = container.querySelector('[data-offering-id="live-martech"]')!;
  expect(within(bar as HTMLElement).getByRole('heading', { name: 'Martech' })).toBeVisible();
  const row = bar.closest('[data-academic-module-id]') as HTMLElement;
  expect(within(row).getByText('Fixed module · 20 weeks')).toBeVisible();
  expect(within(row).queryByText(module.title)).toBeNull();
  expect(bar.className).not.toContain('unverified');
  expect(request).toHaveBeenCalledTimes(1);
});

it('displays exact candidate LMS titles, actual dates and distinct groups for staff review without Select buttons', async () => {
  window.history.replaceState({}, '', '/?moduleScheduleReview=1');
  vi.mocked(request).mockResolvedValue(response('review'));
  const { container }=render(<ModuleSchedulingPreview programme={programme} selection={selection} homeCohort={home} scheduleContext={context} />);
  await screen.findByText('UNVERIFIED LMS MATCH — REVIEW ONLY', {exact:true});
  expect(container.querySelectorAll('[data-offering-id]')).toHaveLength(2);
  expect(screen.getAllByText('LMS: Portfolio Management')).toHaveLength(2);
  expect(screen.queryByRole('button', {name:/Select approved|Select sample/})).toBeNull();
  expect(screen.getByRole('button', {name:'Jump to next available session'})).toBeVisible();
  const months=container.querySelector('[class*="years"]'); expect(months).toHaveTextContent('2028'); expect(months).not.toHaveTextContent('2027');
  expect(screen.getByText('February 2027')).toBeVisible();
  expect(container.querySelector('[data-academic-module-id="earned-value"]')).toBeNull();
  expect(request).toHaveBeenCalledTimes(1);
  expect(vi.mocked(request).mock.calls[0]).toHaveLength(1); // GET only, no request body.
});

it('normal mode automatically displays tentative candidate bars; legacy staff review denial is explicit', async () => {
  vi.mocked(request).mockResolvedValue(response());
  const { container, unmount }=render(<ModuleSchedulingPreview programme={programme} selection={selection} homeCohort={home} scheduleContext={context} />);
  await waitFor(() => expect(container.querySelectorAll('[data-offering-id]')).toHaveLength(2));
  expect(container.querySelectorAll('[data-offering-id]')).toHaveLength(2);
  expect(screen.getAllByText(/Possible LMS match/)).toHaveLength(2);
  unmount();
  window.history.replaceState({}, '', '/?moduleScheduleReview=1');
  vi.mocked(request).mockRejectedValue(new Error('Staff sign-in is required to review unverified LMS matches.'));
  render(<ModuleSchedulingPreview programme={programme} selection={selection} homeCohort={home} scheduleContext={context} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Staff sign-in is required');
  expect(screen.queryByText('LMS: Portfolio Management')).toBeNull();
});

it('exact title offers stay informational, stacked under one academic label without booking controls', async () => {
  vi.mocked(request).mockResolvedValue(response('exact'));
  const before=JSON.stringify(selection);
  const { container } = render(<ModuleSchedulingPreview programme={programme} selection={selection} homeCohort={home} scheduleContext={context} />);
  await waitFor(() => expect(container.querySelectorAll('[data-offering-id]')).toHaveLength(2));
  expect(container.querySelectorAll('[data-academic-module-id="portfolio"]')).toHaveLength(1);
  const row = container.querySelector('[data-academic-module-id="portfolio"]')!;
  expect(row.querySelectorAll('[data-timeline-row="offering"]')).toHaveLength(2);
  expect(row.querySelector('[data-module-label]')).toHaveTextContent('Portfolio Management (APMG)');
  expect(screen.queryByRole('button', { name: /Select.*session/ })).toBeNull();
  expect(screen.getByText('February 2027')).toBeVisible(); expect(JSON.stringify(selection)).toBe(before);
  expect(request).toHaveBeenCalledTimes(1);
  expect(within(screen.getByRole('region',{name:'Module scheduling timeline'})).queryByRole('combobox')).toBeNull();
});

it('refetches on return to focus and includes newly available offerings without polling or a code change', async () => {
  const initial=response(); const next=response();
  next.modules[0].offerings.push({...next.modules[0].offerings[0],id:'new-lms-delivery',group:'G3',groupId:'group-3',start:'2028-07-01'});
  next.offeringCount=3;
  vi.mocked(request).mockResolvedValueOnce(initial).mockResolvedValueOnce(next);
  const { container }=render(<ModuleSchedulingPreview programme={programme} selection={selection} homeCohort={home} scheduleContext={context} />);
  await waitFor(() => expect(container.querySelectorAll('[data-offering-id]')).toHaveLength(2));
  expect(container.querySelectorAll('[data-offering-id]')).toHaveLength(2);
  fireEvent(window,new Event('focus'));
  await waitFor(()=>expect(container.querySelectorAll('[data-offering-id]')).toHaveLength(3));
  expect(container.querySelector('[data-offering-id="new-lms-delivery"]')).not.toBeNull();
  fireEvent(window,new Event('focus')); fireEvent(document,new Event('visibilitychange'));
  expect(request).toHaveBeenCalledTimes(2);
  expect(vi.mocked(request).mock.calls.every(call=>call.length===1)).toBe(true);
});

it.each(['assigned', 'allocated', 'eligible'] as const)('HOME shows one %s group while OTHER cohort keeps G1 and G2', async status => {
  const payload = response();
  const other = payload.modules[0].offerings;
  payload.modules[0].offerings = [...other, ...other.map(o => ({ ...o, id: `home-${o.group}`, delivery: home.cohortId, cohortId: home.cohortId, cohortName: home.name }))];
  payload.homeGroup = { status, groupId: 'G2', groupName: 'G2', message: 'Group shown only; no seat is reserved.' };
  vi.mocked(request).mockResolvedValue(payload);
  const { container } = render(<ModuleSchedulingPreview programme={programme} selection={selection} homeCohort={home} scheduleContext={context} />);
  await waitFor(() => expect(container.querySelectorAll('[data-offering-id]')).toHaveLength(3));
  expect(container.querySelector('[data-offering-id="home-G1"]')).toBeNull();
  expect(container.querySelector('[data-offering-id="home-G2"]')).not.toBeNull();
  expect(container.querySelector('[data-offering-id="test-delivery-G1"]')).not.toBeNull();
  expect(container.querySelector('[data-offering-id="test-delivery-G2"]')).not.toBeNull();
  expect(container.querySelector('[data-timeline-row="home"]')).toHaveTextContent(home.name);
  expect(screen.queryByRole('button', { name: /Select|Confirm My Cohort|Check availability/i })).toBeNull();
  expect(vi.mocked(request).mock.calls.every(call => call.length === 1)).toBe(true);
});

it('unavailable home group does not borrow other groups or hide other-cohort information', async () => {
  const payload = response();
  payload.homeGroup = { status: 'unavailable', groupId: null, message: 'Home-group schedules and existing memberships need admissions review.' };
  payload.modules[0].offerings.push({ ...payload.modules[0].offerings[0], id: 'unallocated-home', cohortId: home.cohortId });
  vi.mocked(request).mockResolvedValue(payload);
  const { container } = render(<ModuleSchedulingPreview programme={programme} selection={selection} homeCohort={home} scheduleContext={context} />);
  expect(await screen.findByText(payload.homeGroup.message!)).toBeVisible();
  await waitFor(() => expect(container.querySelectorAll('[data-offering-id]')).toHaveLength(2));
  expect(container.querySelector('[data-offering-id="unallocated-home"]')).toBeNull();
});

it('preserves Marketing Impact and Planning for an existing saved group without capacity storage', async () => {
  const me = (published as ModulesConfiguration).programmes.find(p => p.id === 'ME_L4')!;
  const payload: ScheduleResponse = { programmeId: 'ME_L4', pathway: null, review: false, asOfDate: '2026-11-17', offeringCount: me.modules.length,
    modules: me.modules.map(m => ({ academicModuleId: m.id, academicTitle: m.title, reason: '', offerings: [{
      id: `saved-${m.id}`, moduleId: m.id, academicTitle: m.title, lmsTitle: m.title, groupId: 'saved-g1', group: 'Group 1',
      cohortId: 'me-home', cohortName: 'Feb 2027', delivery: 'me-home', start: '2027-02-01', end: '2027-06-20',
      weeks: 20, approved: false, reviewOnly: false, matchConfidence: 'exact_title' }] })) };
  vi.mocked(request).mockResolvedValue(payload);
  const { container } = render(<ModuleSchedulingPreview programme={me} homeCohort={{status: 'resolved', source: 'lms', programmeId: 'ME_L4', cohortId: 'me-home', name: 'Feb 2027', layoutVersion: null, groupId: 'saved-g1'}} scheduleContext={context} />);
  await waitFor(() => expect(container.querySelector('[data-offering-id="saved-marketing-impact"]')).not.toBeNull());
  expect(container.querySelectorAll('[data-academic-module-id="marketing-impact"]')).toHaveLength(1);
  expect(screen.queryByText(/allocation storage|Check availability/i)).toBeNull();
});

it.each(['ME_L4', 'MM_L6', 'APM_L4'] as const)('%s independently rejects foreign cohorts and other home groups in an API payload', async id => {
  const fixed = (published as ModulesConfiguration).programmes.find(p => p.id === id)!;
  const module = fixed.modules[0];
  const valid: ScheduleOffering = { id: 'own-delivery', moduleId: module.id, academicTitle: module.title, lmsTitle: module.title,
    delivery: 'fixed-home', cohortId: 'fixed-home', cohortName: 'Feb 2027', groupId: 'g1', group: 'Group 1',
    start: '2028-02-01', end: '2028-06-01', approved: false, reviewOnly: false, matchConfidence: 'exact_title' };
  const payload: ScheduleResponse = { programmeId: id, pathway: null, review: false, asOfDate: '2026-11-17', offeringCount: 3,
    homeGroup: { status: 'allocated', groupId: 'g1' },
    modules: [{ academicModuleId: module.id, academicTitle: module.title, reason: '', offerings: [valid,
      { ...valid, id: 'wrong-cohort', delivery: 'foreign-cohort', cohortId: 'foreign-cohort', cohortName: 'Foreign cohort', approved: true, informationOnly: true },
      { ...valid, id: 'wrong-group', groupId: 'g2', group: 'Group 2' }] }] };
  vi.mocked(request).mockResolvedValue(payload);
  const { container } = render(<ModuleSchedulingPreview programme={fixed} homeCohort={{ status: 'resolved', source: 'lms',
    programmeId: id, cohortId: 'fixed-home', name: 'Feb 2027', groupId: 'g1', layoutVersion: null }} scheduleContext={context} />);
  await waitFor(() => expect(container.querySelectorAll('[data-offering-id]')).toHaveLength(1));
  expect(container.querySelector('[data-offering-id="own-delivery"]')).not.toBeNull();
  expect(container.querySelector('[data-offering-id="wrong-cohort"]')).toBeNull();
  expect(container.querySelector('[data-offering-id="wrong-group"]')).toBeNull();
  expect(screen.queryByText(/Foreign cohort|Other cohorts are informational/)).toBeNull();
  expect(screen.queryByRole('button', { name: /Select.*session/ })).toBeNull();
});

it.each(['ME_L4', 'MM_L6', 'APM_L4'] as const)('%s never substitutes a foreign offering or capacity-eligible group for a missing placement', async id => {
  const fixed = (published as ModulesConfiguration).programmes.find(p => p.id === id)!;
  const module = fixed.modules[0];
  const delivery: ScheduleOffering = { id: 'foreign-only', moduleId: module.id, academicTitle: module.title, lmsTitle: module.title,
    delivery: 'foreign', cohortId: 'foreign', cohortName: 'Foreign cohort', groupId: 'g1', group: 'Group 1',
    start: '2028-02-01', end: '2028-06-01', approved: true, reviewOnly: false, matchConfidence: 'exact_title' };
  vi.mocked(request).mockResolvedValue({ programmeId: id, pathway: null, review: false, asOfDate: '2026-11-17', offeringCount: 2,
    homeGroup: { status: 'eligible', groupId: 'g1' }, modules: [{ academicModuleId: module.id, academicTitle: module.title,
      reason: 'Assigned group pending', offerings: [delivery, { ...delivery, id: 'eligible-not-allocated', cohortId: 'fixed-home' }] }] });
  const { container } = render(<ModuleSchedulingPreview programme={fixed} homeCohort={{status: 'resolved', programmeId: id,
    cohortId: 'fixed-home', name: 'Feb 2027', layoutVersion: null}} scheduleContext={context} />);
  await screen.findByText('Assigned group pending');
  expect(container.querySelectorAll('[data-offering-id]')).toHaveLength(0);
  expect(screen.queryByText(/Foreign cohort/)).toBeNull();
});
