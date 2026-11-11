import { MemoryRouter } from 'react-router-dom';
const { adminIdentity } = vi.hoisted(() => ({ adminIdentity: { id: 114 } }));
vi.mock('@/hooks/useAuth.helpers', () => ({ useAuth: () => ({ auth: { account: { id: adminIdentity.id } } }) }));
import type { ReactNode } from 'react';
vi.mock('@/features/programme-suitability/api', () => ({ prefetchAssessmentSecurity: vi.fn(async () => ({csrfToken:'synthetic-csrf-token'})) }));
import { beforeEach, expect, it, vi } from 'vitest';
import { render as renderWithoutRouter, screen, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Page from '../programme-structure/page';
const { structure } = vi.hoisted(() => ({ structure: { schemaVersion: 2, _draftRevision: 1, programmes: [
  ['ME_L4','Marketing Executive','COHORT-20260917092025244736D2820D958B44',16],
  ['MM_L6','Marketing Manager','COHORT-20261110185121808654D6E19983AD79',16],
  ['APM_L4','Associate Project Manager','COHORT-20261110185125368519EE89CF7CF8DC',28],
  ['PCP_L6','Project Controls Professional','COHORT-20261110185128015800C4494CA47EEB',0],
].map(([id,title,cid,sessions]) => ({ id,title,modules: [],ksbs: [],mappings: [],cohorts: [{id:cid,name:'Feb 2027',programmeId:id,startDate:'2027-02-01',assignmentPeriod:{startMonth:10,startDay:15,endMonth:2,endDay:15},status:'planned',modules:[{id:`${id}-module`,title:`${title} module`,sessions,selection:'optional'}],mappings:[],routes:[]}]})) } }));
vi.mock('@/api/wizardLayout', () => ({ fetchWizardLayout: vi.fn(async () => ({ version:16, layout:{programmeStructure:structure} })) }));
vi.mock('@/pages/admin/_shared/AdminPage', () => ({AdminPage:({children,actions}:any)=><div>{actions}{children}</div>,DataPanel:({children,error}:any)=>error?<p role="alert">{error}</p>:<div>{children}</div>,StatusBadge:()=>null}));
let saved: typeof structure;
let saveStatus: number;
const browserPreferences = new Map<string, string>();
it('creates automatic Module IDs once, preserves KSB links on rename/save/reload and keeps legacy IDs',async()=>{
 let stored={...structuredClone(structure),programmes:[{...structuredClone(structure.programmes[0]),ksbs:[{id:'stable-K1',code:'K1',kind:'Knowledge',description:'Marketing theory',status:'active'}]}]};
 vi.stubGlobal('fetch',vi.fn(async(_url,init)=>{
   if(init?.method==='PUT') stored=JSON.parse(String(init.body)).structure;
   return new Response(JSON.stringify({structure:stored,baseVersion:16,revision:1}));
 }));
 const user=userEvent.setup();const view=render(<Page/>);
 await user.click(await screen.findByRole('button',{name:/Marketing Executive/}));
 await user.click(screen.getByRole('button',{name:'Add module'}));
 expect(screen.queryByLabelText(/Stable module ID/i)).not.toBeInTheDocument();
 await user.type(screen.getByLabelText(/Module name/),'New Module');
 await user.click(screen.getByRole('button',{name:'Create'}));
 const edit=screen.getByRole('button',{name:'Edit New Module'});
 await user.click(within(edit.parentElement!).getByRole('button',{name:'Manage KSB Assignments'}));
 await user.click(within(screen.getByRole('dialog')).getByRole('checkbox'));
 await user.click(screen.getByRole('button',{name:'Apply Changes'}));
 await user.click(screen.getByRole('button',{name:'Save Changes'}));
 await screen.findByText('Changes saved.');
 const cohort=stored.programmes[0].cohorts[0];
 const id=cohort.modules.find(m=>m.title==='New Module')!.id;
 expect(id).toMatch(/^[0-9a-f-]{36}$/i);
 expect(cohort.mappings).toContainEqual(expect.objectContaining({ksbId:'stable-K1',moduleId:id}));
 expect(cohort.modules[0].id).toBe('ME_L4-module');
 await user.click(screen.getByRole('button',{name:'Edit New Module'}));
 const dialog=screen.getByRole('dialog');
 await user.clear(within(dialog).getByLabelText('Name'));
 await user.type(within(dialog).getByLabelText('Name'),'Renamed Module');
 await user.click(within(dialog).getByRole('button',{name:'Save Changes'}));
 await user.click(screen.getByRole('button',{name:'Save Changes'}));
 await screen.findByText('Changes saved.');
 expect(stored.programmes[0].cohorts[0].modules.find(m=>m.title==='Renamed Module')?.id).toBe(id);
 view.unmount();render(<Page/>);
 await user.click(await screen.findByRole('button',{name:/Marketing Executive/}));
 expect(screen.getByText('Renamed Module')).toBeVisible();
 expect(screen.getByText('Modules: Renamed Module')).toBeVisible();
 expect(screen.queryByText(id,{exact:true})).not.toBeInTheDocument();
});
it('edits a saved period only in Edit Cohort, ignores local changes, then saves and reloads',async()=>{
 const user=userEvent.setup();const view=render(<Page/>);
 await user.click(await screen.findByRole('button',{name:/Marketing Executive/}));
 expect(screen.queryByRole('button',{name:'Cohort Calendar'})).not.toBeInTheDocument();
 expect(screen.queryByLabelText('Start Day')).not.toBeInTheDocument();
 await user.click(screen.getByRole('button',{name:'Edit Cohort'}));
 let dialog=screen.getByRole('dialog');
 expect(within(dialog).getByLabelText('Curriculum Status')).toHaveValue('planned');
 expect(within(dialog).getByLabelText('Start Month')).toHaveValue('10');
 expect(within(dialog).getByLabelText('Start Day')).toHaveValue(15);
 fireEvent.change(within(dialog).getByLabelText('Start Day'),{target:{value:'16'}});
 await user.click(within(dialog).getByRole('button',{name:'Save Changes'}));
 expect(vi.mocked(fetch).mock.calls.some(([,init])=>init?.method==='PATCH')).toBe(false);
 await user.click(screen.getByRole('button',{name:'Ignore'}));
 await user.click(screen.getByRole('button',{name:'Edit Cohort'}));
 dialog=screen.getByRole('dialog');
 expect(within(dialog).getByLabelText('Start Day')).toHaveValue(15);
 fireEvent.change(within(dialog).getByLabelText('Start Day'),{target:{value:'16'}});
 await user.click(within(dialog).getByRole('button',{name:'Save Changes'}));
 await user.click(screen.getByRole('button',{name:'Save Changes'}));
 expect(await screen.findByText('Changes saved.')).toBeVisible();
 const cohort=saved.programmes[0].cohorts[0];
 expect(cohort.assignmentPeriod.startDay).toBe(16);
 expect(cohort.id).toBe(structure.programmes[0].cohorts[0].id);
 expect(cohort.modules).toEqual(structure.programmes[0].cohorts[0].modules);
 view.unmount();render(<Page/>);
 await user.click(await screen.findByRole('button',{name:/Marketing Executive/}));
 await user.click(screen.getByRole('button',{name:'Edit Cohort'}));
 expect(within(screen.getByRole('dialog')).getByLabelText('Start Day')).toHaveValue(16);
});

it('copies Feb configuration into the existing empty June Cohort without a Curriculum POST', async()=>{
 const juneId='COHORT-20261110195722888955DB66B4D8CC1C';
 const source=structuredClone(structure);
 source.programmes[0].cohorts.push({...source.programmes[0].cohorts[0],id:juneId,name:'June 2027',startDate:'2027-06-01',modules:[]});
 let persisted=structuredClone(source);
 vi.stubGlobal('fetch',vi.fn(async(url,init)=>{
  if(init?.method==='PUT'){persisted=JSON.parse(String(init.body)).structure;return new Response(JSON.stringify({structure:persisted,baseVersion:16,revision:2}));}
  return new Response(JSON.stringify(String(url).includes('/curriculum_api/')?{results:[{id:juneId,name:'June 2027',programmeId:'PROG-ME-L4',startDate:'2027-06-01',status:'planned'}]}:{structure:persisted,baseVersion:16,revision:1}));
 }));
 const user=userEvent.setup();render(<Page/>);
 await user.click(await screen.findByRole('button',{name:/Marketing Executive/}));
 await user.click(screen.getByRole('button',{name:'Add Cohort'}));
 const dialog=screen.getByRole('dialog');
 await user.type(within(dialog).getByLabelText('Cohort name'),'June 2027');
 expect(within(dialog).queryByLabelText('Existing Cohort')).not.toBeInTheDocument();
 expect(within(dialog).queryByLabelText('Setup method')).not.toBeInTheDocument();
 expect(within(dialog).getByRole('option',{name:'Feb 2027'})).toHaveValue(source.programmes[0].cohorts[0].id);
 await user.selectOptions(within(dialog).getByLabelText('Create Cohort From'),String(source.programmes[0].cohorts[0].id));
 await user.click(within(dialog).getByRole('button',{name:'Add Cohort'}));
 expect(await screen.findByText('June 2027')).toBeVisible();
 expect(screen.getByText('Marketing Executive module')).toBeVisible();
 expect(screen.getByRole('button',{name:'Save Changes'})).toBeVisible();
 expect(vi.mocked(fetch).mock.calls.every(([,init])=>!init?.method)).toBe(true);
 expect(source.programmes[0].cohorts[0].modules[0].id).toBe('ME_L4-module');
 expect(within(dialog).queryByRole('option',{name:'Marketing Manager'})).not.toBeInTheDocument();
 await user.click(screen.getByRole('button',{name:'Save Changes'}));
 expect(await screen.findByText('Changes saved.')).toBeVisible();
 const target=persisted.programmes[0].cohorts.find(c=>c.id===juneId)!;
 expect(target.name).toBe('June 2027');
 expect(target.assignmentPeriod).toEqual(source.programmes[0].cohorts[1].assignmentPeriod);
 expect(target.modules[0].id).not.toBe('ME_L4-module');
 expect(persisted.programmes[0].cohorts[0]).toEqual(source.programmes[0].cohorts[0]);
 expect(vi.mocked(fetch).mock.calls.filter(([,init])=>init?.method==='POST')).toHaveLength(0);
});
it('retries a denied shared save after Curriculum creation without creating June twice, then reloads it', async () => {
  const juneId='COHORT-20261110195722888955DB66B4D8CC1C';
  let stored=structuredClone(structure); let puts=0;
  const fetchMock=vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes('/curriculum_api/') && init?.method==='POST') { expect(JSON.parse(String(init.body))).not.toHaveProperty('startDate'); return new Response(JSON.stringify({cohort:{id:juneId,name:'June 2027',programmeId:'PROG-ME-L4',status:'planned'}}),{status:201}); }
    if (url.includes('/curriculum_api/')) return new Response(JSON.stringify({results:[]}));
    if (init?.method==='PUT') {
      expect(new Headers(init.headers).get('X-CSRFToken')).toBe('synthetic-csrf-token');
      const body=JSON.parse(String(init.body)); expect(body.revision).toBe(1);
      if (++puts===1) return new Response('<!DOCTYPE html><title>CSRF verification failed</title>',{status:403});
      stored=body.structure;
      return new Response(JSON.stringify({structure:stored,baseVersion:16,revision:2}));
    }
    return new Response(JSON.stringify({structure:stored,baseVersion:16,revision:puts>1?2:1}));
  });
  vi.stubGlobal('fetch',fetchMock);
  const user=userEvent.setup(); const view=render(<Page/>);
  await user.click(await screen.findByRole('button',{name:/Marketing Executive/}));
  await user.click(screen.getByRole('button',{name:'Add Cohort'}));
  const dialog=screen.getByRole('dialog');
  await user.type(within(dialog).getByLabelText('Cohort name'),'June 2027');
  expect(within(dialog).queryByLabelText('Start date')).not.toBeInTheDocument();
  await user.click(within(dialog).getByRole('button',{name:'Add Cohort'}));
  expect(await screen.findByText('June 2027')).toBeVisible();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button',{name:'Save Changes'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('CSRF verification failed');
  expect(screen.getByText('June 2027')).toBeVisible();
  await user.click(screen.getByRole('button',{name:'Save Changes'}));
  expect(await screen.findByText('Changes saved.')).toBeVisible();
  expect(fetchMock.mock.calls.filter(([,init])=>init?.method==='POST')).toHaveLength(1);
  expect(stored.programmes[0].cohorts.filter(c=>c.id===juneId)).toHaveLength(1);
  expect(stored.programmes[0].cohorts.find(c=>c.id===juneId)).not.toHaveProperty('assignmentPeriod');
  view.unmount();render(<Page/>);
  await user.click(await screen.findByRole('button',{name:/Marketing Executive/}));
  await user.click(screen.getByRole('button',{name:'Edit Cohort'}));
  expect(screen.getByRole('option',{name:'June 2027'})).toHaveValue(juneId);
});

it('leaves the Add Cohort loading state when its GET times out', async () => {
  vi.stubGlobal('fetch',vi.fn(async (url: string) => {
    if (url.includes('/curriculum_api/')) throw new DOMException('Timed out','TimeoutError');
    return new Response(JSON.stringify({structure,baseVersion:16,revision:1}));
  }));
  const user=userEvent.setup();render(<Page/>);
  await user.click(await screen.findByRole('button',{name:/Marketing Executive/}));
  await user.click(screen.getByRole('button',{name:'Add Cohort'}));
  const dialog=screen.getByRole('dialog');
  await user.type(within(dialog).getByLabelText('Cohort name'),'June 2027');
  await user.selectOptions(within(dialog).getByLabelText('Create Cohort From'),String(structure.programmes[0].cohorts[0].id));

  await user.click(within(dialog).getByRole('button',{name:'Add Cohort'}));
   expect(await screen.findByRole('alert')).toHaveTextContent('Cohort lookup timed out');
  expect(within(dialog).getByLabelText('Cohort name')).toHaveValue('June 2027');
  expect(within(dialog).getByLabelText('Create Cohort From')).toHaveValue(structure.programmes[0].cohorts[0].id);
  expect(within(dialog).queryByLabelText('Start Day')).not.toBeInTheDocument();
  expect(within(screen.getByRole('dialog')).getByRole('button',{name:'Cancel'})).toBeEnabled();
  expect(screen.queryByRole('button',{name:'Loading…'})).not.toBeInTheDocument();
});
it('publishes only the saved shared revision and refreshes its version context', async () => {
  const fetchMock = vi.fn(async (url: string) => {
    if (url.includes('/preview/')) return new Response(JSON.stringify({valid:true,publishingEnabled:true,version:'reviewed'}));
    if (url.includes('/publish/')) return new Response(JSON.stringify({version:17,revision:2,structure:{...structure,_draftRevision:2}}));
    return new Response(JSON.stringify({structure,baseVersion:16,revision:1}));
  });
  vi.stubGlobal('fetch', fetchMock);
  const user=userEvent.setup(); render(<Page />);
  await user.click(await screen.findByRole('button',{name:/Marketing Executive/}));
  await user.click(screen.getByRole('button',{name:'Draft Changes'}));
  await user.click(screen.getByRole('button',{name:'Publish Preview'}));
  await user.click(await screen.findByRole('button',{name:/^Publish$/}));
  await user.click(screen.getByRole('button',{name:'Confirm Publish'}));
  expect(await screen.findByText('Programme Structure published.')).toBeVisible();
  const published=fetchMock.mock.calls.find(([url])=>url.includes('/publish/'));
  expect(published).toBeDefined();
  const options=vi.mocked(global.fetch).mock.calls.find(([url])=>String(url).includes('/publish/'))?.[1];
  expect(JSON.parse(String(options?.body))).toEqual({structure,baseVersion:16,revision:1});
  expect(screen.queryByRole('button',{name:'Save Changes'})).not.toBeInTheDocument();
});
beforeEach(() => { browserPreferences.clear(); Object.defineProperty(window, 'localStorage', { configurable: true, value: { getItem: (key: string) => browserPreferences.get(key) ?? null, setItem: (key: string, value: string) => { browserPreferences.set(key, value); }, clear: () => browserPreferences.clear() } }); adminIdentity.id = 114; saved = structuredClone(structure); saveStatus = 200; vi.stubGlobal('fetch',vi.fn(async (_url, init) => { if (init?.method === 'PUT') { if (saveStatus !== 200) return new Response(JSON.stringify({error: 'This draft changed since you loaded it.'}),{status:saveStatus}); saved = JSON.parse(String(init.body)).structure; return new Response(JSON.stringify({structure:saved,baseVersion:16,revision:2})); } return new Response(JSON.stringify({structure:saved,baseVersion:16,revision:1})); })); });
it('shows persisted Feb 2027 for each programme after loading and remounting without creating cohorts', async () => {
 const user=userEvent.setup(); const mounted=render(<Page/>);
 for(const p of structure.programmes){
  await user.click((await screen.findAllByText(String(p.title)))[0]);
  expect(screen.getByRole('combobox',{name:'Cohort'})).toHaveValue(p.cohorts[0].id);
  expect(screen.queryByRole('combobox',{name:'Select Cohort'})).not.toBeInTheDocument();
  expect(screen.queryByText(/Programme-wide legacy structure/)).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'Initialize Feb 2027 Cohort'})).not.toBeInTheDocument();
  expect(within(screen.getByRole('combobox',{name:'Cohort'})).getByRole('option',{name:'Feb 2027'})).toHaveValue(p.cohorts[0].id);
 }
 mounted.unmount(); render(<Page/>);
 await user.click((await screen.findAllByText('Marketing Executive'))[0]);
 expect(screen.getByRole('combobox',{name:'Cohort'})).toHaveValue(structure.programmes[0].cohorts[0].id);
 expect(screen.getByText('Marketing Executive module')).toBeVisible();
 expect(vi.mocked(fetch).mock.calls.every(([url,init])=>String(url).includes('/draft/')&&!init?.method)).toBe(true);
});

it('saves edits to the default cohort, hides clean actions and reloads the saved sessions', async () => {
 const user=userEvent.setup(); const view=render(<Page/>);
 await user.click((await screen.findAllByText('Marketing Executive'))[0]);
 expect(screen.queryByRole('button',{name:'Save Changes'})).not.toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'Ignore'})).not.toBeInTheDocument();
 await user.click(screen.getByRole('button',{name:'Edit Marketing Executive module'}));
 const dialog=screen.getByRole('dialog');
 const sessions=within(dialog).getAllByLabelText('Sessions')[0];
 await user.clear(sessions); await user.type(sessions,'18');
 await user.click(within(dialog).getByRole('button',{name:'Save Changes'}));
 await user.click(screen.getByRole('button',{name:'Save Changes'}));
 expect(await screen.findByText('Changes saved.')).toBeVisible();
 expect(saved.programmes[0].cohorts[0].modules[0].sessions).toBe(18);
 expect(saved.programmes[0].modules).toEqual([]);
 expect(screen.queryByRole('button',{name:'Save Changes'})).not.toBeInTheDocument();
 expect(vi.mocked(fetch).mock.calls.some(([url])=>String(url).includes('/publish/'))).toBe(false);
 view.unmount(); render(<Page/>);
 await user.click((await screen.findAllByText('Marketing Executive'))[0]);
 expect(screen.getByText(/18 sessions/)).toBeVisible();
});
it('Ignore restores local changes without DELETE, PATCH, POST or PUT requests', async () => {
 const user=userEvent.setup(); render(<Page/>);
 await user.click((await screen.findAllByText('Marketing Executive'))[0]);
 await user.selectOptions(screen.getAllByRole('combobox').find(select => Array.from((select as HTMLSelectElement).options).some(option => option.value === 'required'))!,'required');
 expect(screen.getByRole('button',{name:'Save Changes'})).toBeVisible();
 await user.click(screen.getByRole('button',{name:'Ignore'}));
  expect(screen.getAllByRole('combobox').find(select => Array.from((select as HTMLSelectElement).options).some(option => option.value === 'required'))!).toHaveValue('optional');
 expect(screen.queryByRole('button',{name:'Save Changes'})).not.toBeInTheDocument();
 expect(vi.mocked(fetch).mock.calls.every(([,init])=>!init?.method)).toBe(true);
});
it('preserves unsaved edits and shows a revision conflict without hiding the workspace', async () => {
 saveStatus=409;
 const user=userEvent.setup(); render(<Page/>);
 await user.click((await screen.findAllByText('Marketing Executive'))[0]);
 await user.selectOptions(screen.getAllByRole('combobox').find(select => Array.from((select as HTMLSelectElement).options).some(option => option.value === 'required'))!,'required');
 await user.click(screen.getByRole('button',{name:'Save Changes'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('This draft changed since you loaded it.');
  expect(screen.getAllByRole('combobox').find(select => Array.from((select as HTMLSelectElement).options).some(option => option.value === 'required'))!).toHaveValue('required');
 expect(screen.getByRole('button',{name:'Ignore'})).toBeVisible();
 expect(saved.programmes[0].cohorts[0].modules[0].selection).toBe('optional');
});

it('reuses an existing June Curriculum Cohort and replaces its temporary module only after confirmation', async () => {
 const febId = 'COHORT-20260917092025244736D2820D958B44';
 const juneId = 'COHORT-20261110195722888955DB66B4D8CC1C';
 const original = structuredClone(structure);
 const programme: any = original.programmes[0];
 programme.ksbs = [{id:'stable-K1',code:'K1',kind:'Knowledge',description:'Marketing theory',status:'active'}];
 programme.cohorts[0].modules.push({id:'feb-social',title:'Social Media',sessions:14,selection:'optional'});
 programme.cohorts[0].mappings = [{programmeId:'ME_L4',ksbId:'stable-K1',moduleId:'ME_L4-module',cohortModuleId:'ME_L4-module',status:'active'}];
 programme.cohorts.push({...structuredClone(programme.cohorts[0]),id:juneId,name:'June 2027',modules:[{id:'temporary',title:'test hambola',sessions:null,selection:'optional'}],mappings:[]});
 let stored: any = original;
 vi.stubGlobal('fetch',vi.fn(async (url: string, init?: RequestInit) => {
   if (url.includes('/curriculum_api/curriculum/cohorts/')) return new Response(JSON.stringify({results:[
     {id:febId,name:'Feb 2027',programmeId:'PROG-ME-L4',status:'planned'},
     {id:juneId,name:'June 2027',programmeId:'PROG-ME-L4',status:'planned'},
   ]}));
   if (url.includes('/draft/') && init?.method === 'PUT') {
     stored = JSON.parse(String(init.body)).structure;
     return new Response(JSON.stringify({structure:stored,baseVersion:16,revision:2}));
   }
   return new Response(JSON.stringify({structure:stored,baseVersion:16,revision:1}));
 }));
 const confirm = vi.spyOn(window,'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
 const user = userEvent.setup(); const view = render(<Page/>);
 await user.click((await screen.findAllByText('Marketing Executive'))[0]);
 await user.click(screen.getByRole('button',{name:'Add Cohort'}));
 const dialog = screen.getByRole('dialog');
 await user.type(within(dialog).getByLabelText('Cohort name'),'June 2027');
 await user.selectOptions(within(dialog).getByLabelText('Create Cohort From'),febId);
 await user.click(within(dialog).getByRole('button',{name:'Add Cohort'}));
 expect(await within(dialog).findByRole('alert')).toHaveTextContent('left unchanged');
 expect(screen.queryByRole('button',{name:'Save Changes'})).not.toBeInTheDocument();
 await user.click(within(dialog).getByRole('button',{name:'Add Cohort'}));
 expect(await screen.findByText('Social Media')).toBeVisible();
 expect(screen.queryByText('test hambola')).not.toBeInTheDocument();
 expect(screen.getByRole('button',{name:'Save Changes'})).toBeVisible();
 expect(vi.mocked(fetch).mock.calls.filter(([,init])=>init?.method === 'POST')).toHaveLength(0);
 await user.click(screen.getByRole('button',{name:'Save Changes'}));
 expect(await screen.findByText('Changes saved.')).toBeVisible();
 expect(confirm).toHaveBeenCalledTimes(2);
 const lookups = vi.mocked(fetch).mock.calls.filter(([url])=>String(url).includes('/curriculum_api/curriculum/cohorts/'));
 expect(lookups).toHaveLength(1);
 const lookup = new URL(String(lookups[0][0]),'http://localhost');
 expect(lookup.searchParams.get('view')).toBe('identity');
 expect(lookup.searchParams.get('programme_id')).toBe('PROG-ME-L4');
 expect(lookup.searchParams.get('cohort_id')).toBe(juneId);
 expect(lookup.searchParams.get('name')).toBe('June 2027');
 const feb = stored.programmes[0].cohorts.find((cohort:any)=>cohort.id===febId);
 const june = stored.programmes[0].cohorts.find((cohort:any)=>cohort.id===juneId);
 expect(feb.modules.map((module:any)=>module.title)).toEqual(['Marketing Executive module','Social Media']);
 expect(june.modules.map((module:any)=>module.title)).toEqual(feb.modules.map((module:any)=>module.title));
 expect(june.modules.map((module:any)=>module.sessions)).toEqual([16,14]);
 expect(june.modules.map((module:any)=>module.id)).not.toEqual(feb.modules.map((module:any)=>module.id));
 expect(june.mappings).toEqual([expect.objectContaining({ksbId:'stable-K1',moduleId:june.modules[0].id,cohortModuleId:june.modules[0].id})]);
 view.unmount(); render(<Page/>);
 await user.click((await screen.findAllByText('Marketing Executive'))[0]);
 expect(screen.getByText('Social Media')).toBeVisible();
 expect(screen.queryByText('test hambola')).not.toBeInTheDocument();
});

it('recovers from an identity lookup timeout without duplicating or losing the Add Cohort form', async () => {
 const febId = 'COHORT-20260917092025244736D2820D958B44';
 const juneId = 'COHORT-20261110195722888955DB66B4D8CC1C';
 const fixture: any = structuredClone(structure);
 fixture.programmes[0].cohorts.push({...structuredClone(fixture.programmes[0].cohorts[0]),id:juneId,name:'June 2027',modules:[{id:'temporary',title:'test hambola',sessions:null}],mappings:[]});
 let lookups = 0;
 vi.stubGlobal('fetch',vi.fn(async (url: string) => {
   if (url.includes('/curriculum_api/curriculum/cohorts/')) {
     lookups += 1;
     if (lookups === 1) throw new DOMException('The request timed out.','TimeoutError');
     return new Response(JSON.stringify({results:[{id:juneId,name:'June 2027',programmeId:'PROG-ME-L4',status:'planned'}]}));
   }
   return new Response(JSON.stringify({structure:fixture,baseVersion:16,revision:1}));
 }));
 vi.spyOn(window,'confirm').mockReturnValue(true);
 const user = userEvent.setup(); render(<Page/>);
 await user.click((await screen.findAllByText('Marketing Executive'))[0]);
 await user.click(screen.getByRole('button',{name:'Add Cohort'}));
 const dialog = screen.getByRole('dialog');
 await user.type(within(dialog).getByLabelText('Cohort name'),'June 2027');
 await user.selectOptions(within(dialog).getByLabelText('Create Cohort From'),febId);
 await user.click(within(dialog).getByRole('button',{name:'Add Cohort'}));
 expect(await within(dialog).findByRole('alert')).toHaveTextContent('lookup timed out');
 expect(within(dialog).getByLabelText('Cohort name')).toHaveValue('June 2027');
 expect(within(dialog).getByLabelText('Create Cohort From')).toHaveValue(febId);
 expect(within(dialog).getByRole('button',{name:'Add Cohort'})).toBeEnabled();
 expect(screen.queryByRole('button',{name:'Save Changes'})).not.toBeInTheDocument();
 await user.click(within(dialog).getByRole('button',{name:'Add Cohort'}));
 expect(await screen.findByText('Marketing Executive module')).toBeVisible();
 expect(screen.queryByText('test hambola')).not.toBeInTheDocument();
 expect(lookups).toBe(2);
 expect(vi.mocked(fetch).mock.calls.filter(([,init])=>init?.method === 'POST')).toHaveLength(0);
});

it('refuses duplicate live Curriculum identities instead of creating another Cohort', async () => {
 vi.stubGlobal('fetch',vi.fn(async (url: string) => {
   if (url.includes('/curriculum_api/curriculum/cohorts/')) return new Response(JSON.stringify({results:[
     {id:'COHORT-JUN-A',name:'June 2027',programmeId:'PROG-ME-L4',status:'planned'},
     {id:'COHORT-JUN-B',name:'June 2027',programmeId:'PROG-ME-L4',status:'planned'},
   ]}));
   return new Response(JSON.stringify({structure,baseVersion:16,revision:1}));
 }));
 const user = userEvent.setup(); render(<Page/>);
 await user.click((await screen.findAllByText('Marketing Executive'))[0]);
 await user.click(screen.getByRole('button',{name:'Add Cohort'}));
 const dialog = screen.getByRole('dialog');
 await user.type(within(dialog).getByLabelText('Cohort name'),'June 2027');
 await user.click(within(dialog).getByRole('button',{name:'Add Cohort'}));
 expect(await within(dialog).findByRole('alert')).toHaveTextContent('More than one Curriculum Cohort matches');
 expect(vi.mocked(fetch).mock.calls.filter(([,init])=>init?.method === 'POST')).toHaveLength(0);
 expect(screen.queryByRole('button',{name:'Save Changes'})).not.toBeInTheDocument();
});

it('has no Cohort cloning action and switches independent modules and mappings without writes', async () => {
 const feb=structure.programmes[0].cohorts[0];
 const fixture={...structure,programmes:[{...structure.programmes[0],ksbs:[{id:'st0596-v1-0-K1',code:'K1',kind:'Knowledge',description:'Marketing theory',status:'active'}],cohorts:[
 {...feb,mappings:[{programmeId:'ME_L4',ksbId:'st0596-v1-0-K1',moduleId:'ME_L4-module',status:'active'}]},
 {...feb,id:'COHORT-ME-JUN-TEST',name:'June 2027',modules:[{...feb.modules[0],id:'jun-module',title:'June module',sessions:10}],mappings:[{programmeId:'ME_L4',ksbId:'st0596-v1-0-K1',moduleId:'jun-module',status:'active'}]}
 ]}]};
 vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({structure:fixture,baseVersion:16,revision:1,scope:'shared'}))));
 const user=userEvent.setup(); render(<Page/>);
 await user.click((await screen.findAllByText('Marketing Executive'))[0]);
 expect(screen.queryByRole('button',{name:'Clone Cohort'})).not.toBeInTheDocument();
 expect(screen.getByText('Marketing Executive module')).toBeVisible();
 expect(screen.getByText('Modules: Marketing Executive module')).toBeVisible();
 await user.selectOptions(screen.getByRole('combobox',{name:'Cohort'}),'COHORT-ME-JUN-TEST');
 expect(screen.getByText('June module')).toBeVisible();
 expect(screen.queryByText('Marketing Executive module')).not.toBeInTheDocument();
 expect(screen.getByText('Modules: June module')).toBeVisible();
 expect(screen.queryByRole('button',{name:'Save Changes'})).not.toBeInTheDocument();
 await user.selectOptions(screen.getByRole('combobox',{name:'Cohort'}),String(feb.id));
 expect(screen.getByText('Marketing Executive module')).toBeVisible();
 expect(screen.getByText('Modules: Marketing Executive module')).toBeVisible();
 expect(vi.mocked(fetch).mock.calls.every(([,init])=>!init?.method)).toBe(true);
});
it('remembers each Programme Cohort per Admin after refresh without dirtying the shared draft', async () => {
 const feb = structure.programmes[0].cohorts[0];
 const juneId = 'COHORT-20261110195722888955DB66B4D8CC1C';
 saved = structuredClone(structure);
 saved.programmes[0].cohorts.push({...structuredClone(feb), id:juneId, name:'June 2027', modules:[{id:'jun-only',title:'June only',sessions:10,selection:'optional'}]});
 saved.programmes[1].cohorts.push({...structuredClone(saved.programmes[1].cohorts[0]), id:'MM-JUN', name:'June 2027', modules:[{id:'mm-jun',title:'Manager June',sessions:12,selection:'optional'}]});
 const user = userEvent.setup();
 const choose = async (name:string, id:string) => {
   await user.selectOptions(screen.getByRole('combobox',{name:'Cohort'}),id);
   expect(screen.getByRole('combobox',{name:'Cohort'})).toHaveDisplayValue(name);
   expect(screen.queryByRole('button',{name:'Save Changes'})).not.toBeInTheDocument();
 };
 let view=render(<Page/>);
 await user.click((await screen.findAllByText('Marketing Executive'))[0]);
 await choose('June 2027',juneId);
 expect(screen.getByText('June only')).toBeVisible();
 await user.click((await screen.findAllByText('Marketing Manager'))[0]);
 expect(screen.getByText('Marketing Manager module')).toBeVisible();
 await choose('June 2027','MM-JUN');
 expect(screen.getByText('Manager June')).toBeVisible();
 view.unmount(); view=render(<Page/>);
 await user.click((await screen.findAllByText('Marketing Executive'))[0]);
 expect(screen.getByText('June only')).toBeVisible();
 await choose('Feb 2027',String(feb.id));
 view.unmount(); view=render(<Page/>);
 await user.click((await screen.findAllByText('Marketing Executive'))[0]);
 expect(screen.getByText('Marketing Executive module')).toBeVisible();
 await user.click((await screen.findAllByText('Marketing Manager'))[0]);
 expect(screen.getByText('Manager June')).toBeVisible();
 view.unmount(); adminIdentity.id = 108; view=render(<Page/>);
 await user.click((await screen.findAllByText('Marketing Manager'))[0]);
 expect(screen.getByText('Marketing Manager module')).toBeVisible();
 await user.click((await screen.findAllByText('Marketing Executive'))[0]);
 expect(screen.getByText('Marketing Executive module')).toBeVisible();
 expect(vi.mocked(fetch).mock.calls.every(([,init])=>!init?.method)).toBe(true);
 expect(saved.programmes[0].cohorts[0].id).toBe(feb.id);
 view.unmount();
});
it('falls back to the managed Feb Cohort when a saved stable ID no longer exists', async () => {
 browserPreferences.set('programme-structure:selected-cohort:114:ME_L4', 'removed-cohort-id');
 saved = structuredClone(structure);
 saved.programmes[0].cohorts.unshift({...structuredClone(saved.programmes[0].cohorts[0]), id:'different-cohort', name:'June 2027', modules:[{id:'different-module',title:'Different module',sessions:10,selection:'optional'}]});
 const user = userEvent.setup(); render(<Page/>);
 await user.click((await screen.findAllByText('Marketing Executive'))[0]);
 expect(screen.getByText('Feb 2027')).toBeVisible();
 expect(screen.getByText('Marketing Executive module')).toBeVisible();
 expect(screen.queryByText('Different module')).not.toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'Save Changes'})).not.toBeInTheDocument();
 expect(vi.mocked(fetch).mock.calls.every(([,init])=>!init?.method)).toBe(true);
});
it('links an existing curriculum Cohort without creating or duplicating it', async () => {
 vi.stubGlobal('fetch',vi.fn(async(url)=>new Response(JSON.stringify(String(url).includes('/curriculum_api/')?{results:[{id:'COHORT-ME-JUN-TEST',name:'June 2027',programmeId:'PROG-ME-L4',programme:'Marketing Executive Level 4',status:'planned'}]}:{structure,baseVersion:16,revision:1,scope:'shared'}))));
 const user=userEvent.setup(); render(<Page/>);
 await user.click((await screen.findAllByText('Marketing Executive'))[0]);
 await user.click(screen.getByRole('button',{name:'Add Cohort'}));
 const dialog=await screen.findByRole('dialog');
 await user.type(within(dialog).getByLabelText('Cohort name'),'June 2027');

 await user.click(within(dialog).getByRole('button',{name:'Add Cohort'}));
 expect(await screen.findByText('June 2027')).toBeVisible();
 expect(screen.getByRole('button',{name:'Save Changes'})).toBeVisible();
 expect(vi.mocked(fetch).mock.calls.every(([,init])=>!init?.method)).toBe(true);
});
it('shows draft load errors instead of silently substituting legacy structure', async () => {
 vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({error:'Admissions staff access required.'}),{status:403})));
 render(<Page/>);
 expect(await screen.findByRole('alert')).toHaveTextContent('Admissions staff access required.');
 expect(screen.queryByText('No managed Cohorts yet')).not.toBeInTheDocument();
});

it('stages Cohort metadata without PATCH and preserves its stable ID', async () => {
 vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({structure,baseVersion:16,revision:1,scope:'shared'}))));
 const user=userEvent.setup(); render(<Page/>);
 await user.click((await screen.findAllByText('Marketing Executive'))[0]);
 await user.click(screen.getByRole('button',{name:'Edit Cohort'}));
 const dialog=screen.getByRole('dialog');
 const name=within(dialog).getByLabelText('Cohort name');
 expect(name).toHaveValue('Feb 2027');
 await user.clear(name); await user.type(name,'February 2027');
 await user.click(within(dialog).getByRole('button',{name:'Save Changes'}));
 expect(await screen.findByText('February 2027')).toBeVisible();
 expect(vi.mocked(fetch).mock.calls.find(([,init])=>init?.method==='PATCH')).toBeUndefined();
 expect(screen.getByRole('button',{name:'Save Changes'})).toBeVisible();
 expect(screen.queryByRole('button',{name:'Clone Cohort'})).not.toBeInTheDocument();
});

it('keeps an empty Programme honest and offers Add Cohort without showing legacy modules', async () => {
 saved = structuredClone(structure);
 saved.programmes[0].cohorts = [];
 saved.programmes[0].modules = [{id:'legacy-only',title:'Historical legacy module',sessions:16,selection:'optional'}];
 const user=userEvent.setup();render(<Page/>);
 await user.click((await screen.findAllByText('Marketing Executive'))[0]);
 expect(screen.getByText(/No managed Cohorts yet. Use Add Cohort/)).toBeVisible();
 expect(screen.queryByRole('combobox',{name:'Cohort'})).not.toBeInTheDocument();
 expect(screen.queryByText('Historical legacy module')).not.toBeInTheDocument();
 expect(screen.getByRole('button',{name:'Add Cohort'})).toBeEnabled();
 expect(screen.getByRole('button',{name:'Edit Cohort'})).toBeDisabled();
 expect(screen.getByRole('button',{name:'Add module'})).toBeDisabled();
 expect(vi.mocked(fetch).mock.calls.every(([,init])=>!init?.method)).toBe(true);
});

it('stages Cohort status, ignores it without a write, then saves it only to the shared draft', async () => {
 const user=userEvent.setup();render(<Page/>);
 await user.click((await screen.findAllByText('Marketing Executive'))[0]);
 await user.click(screen.getByRole('button',{name:'Edit Cohort'}));
 await user.selectOptions(within(screen.getByRole('dialog')).getByLabelText('Curriculum Status'),'active');
 await user.click(within(screen.getByRole('dialog')).getByRole('button',{name:'Save Changes'}));
 expect(vi.mocked(fetch).mock.calls.find(([,init])=>init?.method==='PATCH')).toBeUndefined();
 expect(screen.getByRole('button',{name:'Save Changes'})).toBeVisible();
 await user.click(screen.getByRole('button',{name:'Ignore'}));
 expect(screen.queryByRole('button',{name:'Save Changes'})).not.toBeInTheDocument();
 expect(vi.mocked(fetch).mock.calls.find(([,init])=>init?.method==='PUT')).toBeUndefined();
 await user.click(screen.getByRole('button',{name:'Edit Cohort'}));
 await user.selectOptions(within(screen.getByRole('dialog')).getByLabelText('Curriculum Status'),'active');
 await user.click(within(screen.getByRole('dialog')).getByRole('button',{name:'Save Changes'}));
 await user.click(screen.getByRole('button',{name:'Save Changes'}));
 expect(saved.programmes[0].cohorts[0].status).toBe('active');
 expect(saved.programmes[0].cohorts[0].id).toBe(structure.programmes[0].cohorts[0].id);
});

it('keeps technical history collapsed in review and never shows it during normal editing',async()=>{
 vi.stubGlobal('fetch',vi.fn(async(url)=>new Response(JSON.stringify(String(url).includes('/preview/')?{valid:true,publishingEnabled:false,version:'internal-reference',changes:[{programmeName:'Marketing Executive',cohortName:'Feb 2027',type:'cohort-modified'}]}:{structure,baseVersion:16,revision:1}))));
 const user=userEvent.setup();render(<Page/>);
 await user.click(await screen.findByRole('button',{name:/Marketing Executive/}));
 expect(screen.queryByText(/Published baseline/)).not.toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'Save Changes'})).not.toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'Ignore'})).not.toBeInTheDocument();
 await user.click(screen.getByRole('button',{name:'Draft Changes'}));
 await user.click(screen.getByRole('button',{name:'Publish Preview'}));
 expect(await screen.findByText('Marketing Executive / Feb 2027: cohort modified')).toBeVisible();
 expect(screen.getByText(/Published baseline/)).not.toBeVisible();
 expect(screen.getByText(/Configuration reference/)).not.toBeVisible();
 await user.click(screen.getByText('History and recovery details'));
 expect(screen.getByText(/Published baseline/)).toBeVisible();
 await user.click(screen.getByRole('button',{name:'Structure Editor'}));
 expect(screen.queryByRole('region',{name:'Publish Preview'})).not.toBeInTheDocument();
});

it('explains changed published settings without technical version numbers',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({structure,baseVersion:8,revision:7}))));
 render(<Page/>);
 const alert=await screen.findByRole('alert');
 expect(alert).toHaveTextContent('Published settings have changed. Your saved work is safe');
 expect(alert).not.toHaveTextContent(/version|revision|8|16/);
 expect(screen.queryByRole('button',{name:'Save Changes'})).not.toBeInTheDocument();
});

function render(ui: ReactNode) { return renderWithoutRouter(<MemoryRouter>{ui}</MemoryRouter>); }

it('rejects incomplete Edit Cohort periods before any Curriculum or draft write',async()=>{
 const user=userEvent.setup();render(<Page/>);
 await user.click(await screen.findByRole('button',{name:/Marketing Executive/}));
 await user.click(screen.getByRole('button',{name:'Edit Cohort'}));
 const dialog=screen.getByRole('dialog');
 fireEvent.change(within(dialog).getByLabelText('End Month'),{target:{value:'2'}});
 fireEvent.change(within(dialog).getByLabelText('End Day'),{target:{value:'30'}});
 await user.click(within(dialog).getByRole('button',{name:'Save Changes'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('valid assignment period');
 expect(screen.getByRole('dialog')).toBeVisible();
 expect(vi.mocked(fetch).mock.calls.every(([,init])=>!init?.method)).toBe(true);
});

it('edits only the Cohort selected by the filter and opening or cancelling Edit does not switch it',async()=>{
 const data=structuredClone(structure);
 const feb=data.programmes[0].cohorts[0];
 data.programmes[0].cohorts.push({...structuredClone(feb),id:'real-june',name:'June 2027',status:'active',assignmentPeriod:{startMonth:2,startDay:16,endMonth:6,endDay:15},modules:[{id:'june-module',title:'June module',sessions:10,selection:'optional'}]});
 vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({structure:data,baseVersion:20,revision:3}))));
 const user=userEvent.setup();render(<Page/>);
 await user.click(await screen.findByRole('button',{name:/Marketing Executive/}));
 expect(screen.queryByLabelText('Select Cohort')).not.toBeInTheDocument();
 await user.click(screen.getByRole('button',{name:'Edit Cohort'}));
 const dialog=screen.getByRole('dialog');
 expect(within(dialog).queryByLabelText('Select Cohort')).not.toBeInTheDocument();
 expect(within(dialog).getByLabelText('Cohort name')).toHaveValue('Feb 2027');
 expect(screen.getByText('Marketing Executive module')).toBeVisible();
 await user.click(within(dialog).getByRole('button',{name:'Close editor'}));
 expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
 expect(screen.getByText('Marketing Executive module')).toBeVisible();
 await user.selectOptions(screen.getByRole('combobox',{name:'Cohort'}),'real-june');
 expect(screen.getByText('June module')).toBeVisible();
 await user.click(screen.getByRole('button',{name:'Edit Cohort'}));
 const juneDialog=screen.getByRole('dialog');
 expect(within(juneDialog).getByLabelText('Cohort name')).toHaveValue('June 2027');
 expect(within(juneDialog).getByLabelText('Curriculum Status')).toHaveValue('active');
 expect(within(juneDialog).getByLabelText('Start Day')).toHaveValue(16);
 await user.click(within(juneDialog).getByRole('button',{name:'Close editor'}));
 expect(screen.getByText('June module')).toBeVisible();
 expect(screen.queryByRole('button',{name:'Ignore'})).not.toBeInTheDocument();
 expect(vi.mocked(fetch).mock.calls.every(([,init])=>!init?.method)).toBe(true);
});
