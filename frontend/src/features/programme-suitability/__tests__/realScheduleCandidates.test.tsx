import { afterEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
  await screen.findByRole('button',{name:'Select provisional G1 session for Portfolio Management (APMG)'});
  expect(container.querySelectorAll('[data-offering-id]')).toHaveLength(2);
  expect(screen.getAllByText(/Possible LMS match/)).toHaveLength(2);
  unmount();
  window.history.replaceState({}, '', '/?moduleScheduleReview=1');
  vi.mocked(request).mockRejectedValue(new Error('Staff sign-in is required to review unverified LMS matches.'));
  render(<ModuleSchedulingPreview programme={programme} selection={selection} homeCohort={home} scheduleContext={context} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Staff sign-in is required');
  expect(screen.queryByText('LMS: Portfolio Management')).toBeNull();
});

it('exact title offers can be selected locally, one per module, without changing home cohort or booking', async () => {
  vi.mocked(request).mockResolvedValue(response('exact'));
  const before=JSON.stringify(selection);
  render(<ModuleSchedulingPreview programme={programme} selection={selection} homeCohort={home} scheduleContext={context} />);
  const first=await screen.findByRole('button',{name:'Select exact G1 session for Portfolio Management (APMG)'});
  const second=screen.getByRole('button',{name:'Select exact G2 session for Portfolio Management (APMG)'});
  await userEvent.click(first); await userEvent.click(second);
  expect(first).toHaveAttribute('aria-pressed','false'); expect(second).toHaveAttribute('aria-pressed','true');
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
  await screen.findByRole('button',{name:'Select provisional G1 session for Portfolio Management (APMG)'});
  expect(container.querySelectorAll('[data-offering-id]')).toHaveLength(2);
  fireEvent(window,new Event('focus'));
  await waitFor(()=>expect(container.querySelectorAll('[data-offering-id]')).toHaveLength(3));
  expect(container.querySelector('[data-offering-id="new-lms-delivery"]')).not.toBeNull();
  fireEvent(window,new Event('focus')); fireEvent(document,new Event('visibilitychange'));
  expect(request).toHaveBeenCalledTimes(2);
  expect(vi.mocked(request).mock.calls.every(call=>call.length===1)).toBe(true);
});
