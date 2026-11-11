import { MemoryRouter } from 'react-router-dom';
vi.mock('@/hooks/useAuth.helpers', () => ({ useAuth: () => ({ auth: { account: { id: 114 } } }) }));
import type { ReactNode } from 'react';
vi.mock('@/features/programme-suitability/api', () => ({ prefetchAssessmentSecurity: vi.fn(async () => ({csrfToken:'synthetic-csrf-token'})) }));
import { beforeEach, expect, it, vi } from 'vitest';
import { render as renderWithoutRouter, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ProgrammeStructurePage from '../programme-structure/page';
vi.mock('@/pages/admin/_shared/AdminPage', () => ({ AdminPage: ({ children, actions }: any) => <div>{actions}{children}</div>, DataPanel: ({ children, loading, error }: any) => loading ? <div>loading</div> : error ? <div>{error}</div> : <div>{children}</div>, StatusBadge: ({ status }: any) => <span>{status}</span> }));
vi.mock('@/api/wizardLayout', () => ({ fetchWizardLayout: vi.fn(async () => ({ version: 2, layout: { programmeModules: { programmes: [{ id: 'ME_L4', title: 'Marketing Executive', modules: [{ id: 'm1', title: 'Impact', active: true, isMandatory: true }] }], }, skillsRadarCatalogues: { ME_L4: { records: [{ id: 'ksb-1', code: 'K1', kind: 'Knowledge', description: 'One', moduleKey: 'm1' }] } } } })) }));
let draft: any;
beforeEach(() => { draft = { structure: null, revision: 0 }; vi.stubGlobal('fetch', vi.fn(async (url: string, init?: any) => { if (url.includes('/curriculum_api/curriculum/cohorts/') && init?.method === 'POST') return new Response(JSON.stringify({ cohort: { id: 'COHORT-20260917092025244736D2820D958B44', name: 'Feb 2027', programmeId: 'PROG-ME-L4', status: 'planned' } }), { status: 200 }); if (url.includes('/curriculum_api/curriculum/cohorts/')) return new Response(JSON.stringify({ results: [{ id: 'COHORT-20260917092025244736D2820D958B44', name: 'Feb 2027', programmeId: 'PROG-ME-L4', status: 'planned' }] }), { status: 200 }); if (url.includes('/draft/reconcile/') && init?.method === 'POST') { const corrected = structuredClone(draft.structure); corrected.programmes[0].curriculumProgrammeId = 'PROG-ME-L4'; corrected.programmes[0].cohorts = corrected.programmes[0].cohorts.filter((cohort: any) => cohort.name !== 'hisham of aa7'); return new Response(JSON.stringify({ structure: corrected, baseVersion: 16, revision: draft.revision, persisted: false, removedCohorts: ['COHORT-20261111015402258877CC69FD92DAF3'] }), { status: 200 }); } if (url.includes('/draft/') && init?.method === 'PUT') { draft = { structure: JSON.parse(init.body).structure, revision: draft.revision + 1 }; return new Response(JSON.stringify(draft), { status: 200 }); } if (url.includes('/draft/')) return new Response(JSON.stringify(draft), { status: 200 }); return new Response(JSON.stringify({ valid: true }), { status: 200 }); })); vi.spyOn(window, 'prompt').mockImplementation((label: string) => label.includes('Programme name') ? 'New programme' : label.includes('Stable programme ID') ? 'NEW_L4' : label.includes('Module name') ? 'Second' : label.includes('Stable module ID') ? 'm2' : null); });

it('creates a programme and saves it through the draft API', async () => { const user = userEvent.setup(); const { unmount } = render(<ProgrammeStructurePage />); await screen.findByText('Marketing Executive'); await user.click(screen.getByRole('button', { name: 'Add Programme' })); await user.type(screen.getByLabelText(/Programme name/), 'New programme'); await user.type(screen.getByLabelText(/Stable programme code/), 'NEW_L4'); await user.click(screen.getByRole('button', { name: 'Create Programme' })); expect(screen.getAllByText('New programme').length).toBeGreaterThan(0); await user.click(screen.getByRole('button', { name: 'Save Changes' })); expect(await screen.findByText('Changes saved.')).toBeVisible(); unmount(); render(<ProgrammeStructurePage />); expect((await screen.findAllByText('New programme')).length).toBeGreaterThan(0); });

it('keeps a canonical KSB while mapping it to two managed Cohort modules and saving', async () => {
 draft = {structure:{schemaVersion:1,programmes:[{id:'ME_L4',title:'Marketing Executive',status:'active',modules:[{id:'m1',title:'Impact',active:true,selection:'required'}],ksbs:[{id:'ksb-1',code:'K1',kind:'Knowledge',description:'One',status:'active'}],mappings:[{programmeId:'ME_L4',ksbId:'ksb-1',moduleId:'m1',status:'active'}],cohorts:[{id:'COHORT-20260917092025244736D2820D958B44',name:'Feb 2027',status:'planned',modules:[{id:'m1',title:'Impact',active:true,selection:'required'}],mappings:[{programmeId:'ME_L4',ksbId:'ksb-1',moduleId:'m1',status:'active'}],routes:[]}]}]},revision:1};
 const user=userEvent.setup();render(<ProgrammeStructurePage/>);
 await user.click((await screen.findAllByText('Marketing Executive'))[0]);
 expect(screen.getByRole('combobox',{name:'Cohort'})).toHaveDisplayValue('Feb 2027');
 await user.click(screen.getByRole('button',{name:'Add module'}));
 await user.type(screen.getByLabelText(/Module name/),'Second');
 expect(screen.queryByLabelText(/Stable module ID/)).not.toBeInTheDocument();
 await user.click(screen.getByRole('button',{name:'Create'}));
 await user.click(screen.getByRole('button',{name:'Structure Editor'}));
 await user.click(screen.getAllByRole('button',{name:'K1'}).at(-1)!);
 await user.click(screen.getByRole('button',{name:'Save Changes'}));
 const programme=draft.structure.programmes[0];
 expect(programme.ksbs.filter((ksb:any)=>ksb.code==='K1')).toHaveLength(1);
 expect(programme.cohorts[0].mappings.filter((mapping:any)=>mapping.ksbId==='ksb-1')).toHaveLength(2);
});


it('does not import curriculum history or create cohorts while loading legacy structure', async () => { render(<ProgrammeStructurePage />); const user = userEvent.setup(); await user.click((await screen.findAllByText('Marketing Executive'))[0]); expect(screen.getByText(/No managed Cohorts yet. Use Add Cohort/)).toBeVisible(); expect(screen.queryByRole('button', { name: 'Initialize Feb 2027 Cohort' })).not.toBeInTheDocument(); expect(vi.mocked(fetch).mock.calls.every(([url]) => !String(url).includes('/curriculum_api/'))).toBe(true); });

it('prepares the verified Curriculum correction in Draft Changes and leaves persistence to Save Changes', async () => {
 draft = { structure: { schemaVersion: 2, programmes: [{ id: 'ME_L4', title: 'Marketing Executive', modules: [], ksbs: [], mappings: [], cohorts: [{ id: 'COHORT-20260917092025244736D2820D958B44', name: 'Feb 2027', modules: [], mappings: [] }, { id: 'COHORT-20261111015402258877CC69FD92DAF3', name: 'hisham of aa7', modules: [], mappings: [] }] }] }, revision: 1 };
 const user = userEvent.setup(); render(<ProgrammeStructurePage />); await user.click((await screen.findAllByText('Marketing Executive'))[0]); await user.click(screen.getByRole('button', { name: 'Draft Changes' })); await user.click(screen.getByRole('button', { name: /Apply verified Curriculum correction/ })); expect(await screen.findByText(/Curriculum identities verified/)).toBeVisible(); expect(screen.getByRole('button', { name: 'Save Changes' })).toBeVisible(); expect(draft.structure.programmes[0].cohorts).toHaveLength(2);
});

function render(ui: ReactNode) { return renderWithoutRouter(<MemoryRouter>{ui}</MemoryRouter>); }
