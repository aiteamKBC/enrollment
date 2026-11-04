import { act, cleanup, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { EnrolmentBoard } from '../../types';
import { WizardProvider } from '../WizardContext';
import { useWizard, type WizardContextValue } from '../WizardContext.helpers';

const api=vi.hoisted(()=>({fetch:vi.fn(),save:vi.fn()}));
vi.mock('@/api/extendedIlr',()=>({fetchExtendedIlr:api.fetch,saveExtendedIlr:api.save,peekExtendedIlr:()=>undefined}));
vi.mock('@/api/wizardLayout',()=>({fetchWizardLayout:async()=>({layout:null,version:null}),peekWizardLayout:()=>undefined}));
vi.mock('@/api/curriculum',()=>({fetchKsbProfile:async()=>({results:[]}),peekKsbProfile:()=>undefined}));
vi.mock('@/api/enrolmentDocuments',()=>({uploadEnrolmentDocument:vi.fn()}));
vi.mock('@/api/wizardInclusion',()=>({fetchInclusionStatus:async()=>({applicable:true,completed:false}),SAFEGUARDING_URL:'https://example.invalid'}));

const board={user:{id:'812',name:'Synthetic Learner'},contact:{email:'test@example.invalid',phone:'123',dob:'1995-04-03',eligibilityLinked:true,savedSignature:'data:image/png;base64,FIRSTLOGIN'},programme:{name:'',onboardingStatus:'In progress'}} as EnrolmentBoard;
const prefill={applicationId:'41',draft:{personalDetails:{firstName:'Synthetic',lastName:'Learner',dob:'1995-04-03'},ilrDetails:{jobTitle:'Analyst',employmentStatus:'In paid employment',employmentStartDate:'2020-01-02'}},answers:{eligibility:{countryOfResidence:'France',nationality:'France',residentPrev3Years:false},employer:{organisationName:'Company',postcode:'SA1 1AD',address:'2 Company Road, Building B, Wales',city:'Swansea'}},employerRepresentative:{name:'Representative',email:'rep@example.invalid'}};
let latest:WizardContextValue;
function Probe(){latest=useWizard();return null;}
const open=()=>render(<WizardProvider userId="812" board={board}><Probe/></WizardProvider>);
beforeEach(()=>{vi.clearAllMocks();api.fetch.mockResolvedValue({answers:null,draft:null,prefill,meta:{updatedAt:''}});api.save.mockResolvedValue({meta:{updatedAt:'saved'}});});
afterEach(cleanup);

it('prefills factual fields while programme, declarations, signatures and line manager stay independent',async()=>{
  open();await waitFor(()=>expect(latest.hydrated).toBe(true));
  expect(latest.draft.ilrDetails.jobTitle).toBe('Analyst');
  expect(latest.draft.ilr.employer.postcode).toBe('SA1 1AD');
  expect(latest.draft.ilr.employer.organisationName).toBe('Company');
  expect(latest.draft.ilr.employer.address).toBe('2 Company Road, Building B, Wales');
  expect(latest.draft.ilr.employer.city).toBe('Swansea');
  expect(latest.draft.ilr.eligibility.nationality).toBe('France');
  expect(latest.draft.ilr.employer.lineManagerName).toBe('');
  expect(latest.draft.personalDetails.signature).toBeUndefined();
  expect(latest.draft.ilrDetails.signature).toBeNull();
  expect(latest.draft.ilr.learnerSignature.signatureUrl).toBeUndefined();
  expect(latest.draft.ilr.declarations.over50PercentEngland).toBeNull();
  expect(latest.draft.ilr.additionalInformation.residenceNotForFullTimeEducation).toBe('');
  expect(latest.draft.programmeSuitability?.selectedProgramme).toBeUndefined();
  expect(latest.board.programme.name).toBe('');
});

it('seeds Title from the existing profile without requiring a historical missing value', async()=>{
  const view=render(<WizardProvider userId="812" board={{...board,contact:{...board.contact,title:'Ms'}}}><Probe/></WizardProvider>);
  await waitFor(()=>expect(latest.hydrated).toBe(true));
  expect(latest.draft.personalDetails.title).toBe('Ms'); view.unmount();
  render(<WizardProvider userId="812" board={{...board,contact:{...board.contact,title:'',titleRequired:false}}}><Probe/></WizardProvider>);
  await waitFor(()=>expect(latest.hydrated).toBe(true));
  expect(latest.draft.personalDetails.title).toBeUndefined();
});

it('keeps saved edits and deliberate blanks across save and reopening',async()=>{
  let saved:Record<string,unknown>|null=null;
  api.fetch.mockImplementation(async()=>({answers:saved?.answers??null,draft:saved?.draft??null,prefill,meta:{updatedAt:''}}));
  api.save.mockImplementation(async(_kind,_id,answers,draft)=>{saved={answers,draft};return{meta:{updatedAt:'saved'}};});
  const view=open();await waitFor(()=>expect(latest.hydrated).toBe(true));
  act(()=>latest.setSection('ilrDetails',{...latest.draft.ilrDetails,jobTitle:'Learner edited job'}));
  act(()=>latest.setSection('ilr',{...latest.draft.ilr,employer:{...latest.draft.ilr.employer,address:'Edited address',city:''}}));
  await act(async()=>{await latest.saveIlr();});
  view.unmount();open();await waitFor(()=>expect(latest.hydrated).toBe(true));
  expect(latest.draft.ilrDetails.jobTitle).toBe('Learner edited job');
  expect(latest.draft.ilr.employer.address).toBe('Edited address');
  expect(latest.draft.ilr.employer.city).toBe('');
});

it('does not overwrite early edits with a late bootstrap response',async()=>{
  let resolve!:(value:unknown)=>void;
  api.fetch.mockReturnValue(new Promise(done=>{resolve=done;}));
  open();
  act(()=>latest.setSection('ilrDetails',{...latest.draft.ilrDetails,jobTitle:'Typed while loading'}));
  await act(async()=>resolve({answers:null,draft:null,prefill,meta:{updatedAt:''}}));
  await waitFor(()=>expect(latest.hydrated).toBe(true));
  expect(latest.draft.ilrDetails.jobTitle).toBe('Typed while loading');
  expect(latest.draft.ilr.employer.postcode).toBe('SA1 1AD');
  await act(async()=>{await latest.saveIlr();});
  expect(api.save).toHaveBeenCalled();
  expect(api.save.mock.calls[0][3].ilrDetails.jobTitle).toBe('Typed while loading');
});

it('preserves saved blank identity answers and an independently chosen programme',async()=>{
  api.fetch.mockResolvedValue({answers:null,draft:{personalDetails:{phone:'',address:'',dob:''}},prefill,meta:{updatedAt:''}});
  const independent={...board,programme:{...board.programme,name:'Independently chosen standard'}};
  render(<WizardProvider userId="812" board={independent}><Probe/></WizardProvider>);
  await waitFor(()=>expect(latest.hydrated).toBe(true));
  expect(latest.draft.personalDetails.phone).toBe('');
  expect(latest.draft.personalDetails.address).toBe('');
  expect(latest.draft.personalDetails.dob).toBe('');
  expect(latest.board.programme.name).toBe('Independently chosen standard');
});

it('never overwrites an entirely cleared saved employer section with source defaults',async()=>{
  api.fetch.mockResolvedValue({answers:{employer:{organisationName:'',postcode:'',address:'',city:'',lineManagerName:'',lineManagerEmail:'',lineManagerPhone:''}},draft:null,prefill,meta:{updatedAt:''}});
  open();await waitFor(()=>expect(latest.hydrated).toBe(true));
  expect(Object.values(latest.draft.ilr.employer)).toEqual(['','','','','','','']);
});

it('keeps company address lines one two four separate from city without populating a line manager',async()=>{
  const same={...prefill,answers:{...prefill.answers,employer:{organisationName:'Company',postcode:'SA1 1AD',address:'2 Company Road, Building B, Wales',city:'Swansea'}}};
  api.fetch.mockResolvedValue({answers:null,draft:null,prefill:same,meta:{updatedAt:''}});
  open();await waitFor(()=>expect(latest.hydrated).toBe(true));
  expect(latest.draft.ilr.employer.address).toBe('2 Company Road, Building B, Wales');
  expect(latest.draft.ilr.employer.city).toBe('Swansea');
  expect(latest.draft.ilr.employer.lineManagerName).toBe('');
});
