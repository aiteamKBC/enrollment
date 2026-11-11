import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { contentFor, payloadFor } from './fixtures';
import type { DocumentKey, PreviewResponse } from '../types';
import type { DocumentRevision, RevisionDetail } from '../revisionApi';

vi.mock('@/components/feature/WorkspaceShell', () => ({ WorkspaceShell: ({ children }: { children: ReactNode }) => children }));
vi.mock('@/hooks/useToast.helpers', () => ({ useToast: () => ({ info: vi.fn(), success: vi.fn(), error: vi.fn() }) }));
vi.mock('@/hooks/useAuth.helpers', () => ({ useAuth: () => ({ isAdmin: true, auth: { account: { role: 'staff' }, user: { fullName: 'Synthetic Admin' } } }) }));
vi.mock('@/pages/users/components/LearnerHeaderDates', () => ({ LearnerHeaderDates: () => null }));
vi.mock('@/pages/users/components/FirstSessionReschedule', () => ({ FirstSessionReschedule: () => null }));
const api = vi.hoisted(() => ({ fetchEnrolmentBoard: vi.fn(), fetchAgreement: vi.fn(), fetchIlrDocument: vi.fn() }));
vi.mock('@/api/enrolmentUsers', async (original) => ({ ...(await original<typeof import('@/api/enrolmentUsers')>()), fetchEnrolmentBoard: api.fetchEnrolmentBoard }));
vi.mock('@/api/enrolmentDocuments', async (original) => ({ ...(await original<typeof import('@/api/enrolmentDocuments')>()), fetchEnrolmentDocuments: async () => [] }));
vi.mock('@/api/apprenticeshipAgreement', () => ({ fetchAgreement: api.fetchAgreement, issueAgreement: vi.fn() }));
vi.mock('@/api/ilrDocument', () => ({ fetchIlrDocument: api.fetchIlrDocument, issueIlrDocument: vi.fn(), signIlrDocument: vi.fn() }));
vi.mock('@/api/trainingPlanDocument', () => ({ fetchTrainingPlanDocument: async () => ({ document: null }), issueTrainingPlanDocument: vi.fn(), signTrainingPlanDocument: vi.fn() }));
vi.mock('@/api/writtenAgreement', () => ({ fetchWrittenAgreement: async () => ({ document: null }), issueWrittenAgreement: vi.fn(), signWrittenAgreement: vi.fn() }));
vi.mock('@/api/reviewForm', async (original) => ({ ...(await original<typeof import('@/api/reviewForm')>()), fetchOnboardingReviewSlots: async () => ({ reviews: [] }) }));
vi.mock('@/api/coach', () => ({ fetchLearnerCoach: async () => { throw new Error('No synthetic coach'); }, updateLearnerCoach: vi.fn() }));
vi.mock('@/api/staffUsers', () => ({ fetchCoachOptions: async () => [] }));
// jsdom cannot render PDF iframes. Keep the real profile, revision client and editor.
vi.mock('../DocumentPreview', () => ({ DocumentPreview: ({ value, onClose, onReady }: { value: PreviewResponse; onClose: () => void; onReady?: (value: PreviewResponse) => void }) => <div role="dialog" aria-label="Saved revision preview"><button onClick={() => { onReady?.(value); onClose(); }}>Reviewed PDF</button></div> }));
import BoardPage from '@/pages/users/BoardPage';

const fetchMock = vi.fn();
const result = (value: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(value), json: async () => value });
const setup = { code: 'missing_tables', message: 'Document revision storage has not been installed.', missingTables: ['Learner_Document_Revisions'], disabledSettings: [], checkCommand: 'python backend/manage.py apply_document_templates --with-revisions --check', planCommand: 'python backend/manage.py apply_document_templates --with-revisions --plan', setupCommand: 'python backend/manage.py apply_document_templates --with-revisions' };
let ready = false;
const drafts = new Map<DocumentKey, RevisionDetail>();
const history = new Map<DocumentKey, DocumentRevision[]>();

function detail(key: DocumentKey): RevisionDetail {
  const preview = payloadFor(key, true); preview.sample = false;
  preview.template.id = 'historical-template-v1'; preview.template.number = 1;
  preview.template.fields!['learner.fullName'] = 'Historical Learner';
  preview.template.content.blocks.splice(1, 0, { id: 'historical-only', type: 'paragraph', text: 'Original frozen learner wording' });
  const roles = preview.template.content.signatureRoles;
  preview.data.signatures = Object.fromEntries(roles.map((role) => [role, { signed: true, name: `Historical ${role}`, signedAt: '2030-01-01', signature: 'data:image/png;base64,c3ludGhldGlj' }]));
  const revision: DocumentRevision = { id: `${key}-r2`, issueId: `${key}-issue`, documentKey: key, title: preview.template.content.title, sourceId: key === 'ilr' ? 'signed-ilr' : 'issued-aa', number: 2, baseId: null, state: 'draft', editVersion: 1, signatureMethod: '', status: 'Draft', fullySigned: false, templateVersionId: 'historical-template-v1', templateVersionNumber: 1, createdBy: 'Synthetic Admin', createdAt: '2030-01-02', finalizedAt: null, reason: '', changes: {}, checksum: 'synthetic', provenance: Object.fromEntries(roles.map((role) => [role, { sourceRevision: 1, name: `Historical ${role}`, signedAt: '2030-01-01' }])), signatures: preview.data.signatures };
  return { revision, preview };
}
const mount = () => render(<MemoryRouter initialEntries={['/users/20?tab=documents']}><Routes><Route path="/users/:userId" element={<BoardPage />} /></Routes></MemoryRouter>);

beforeEach(() => {
  ready = false; drafts.clear(); history.clear(); fetchMock.mockReset(); vi.stubGlobal('fetch', fetchMock);
  vi.stubGlobal('AppIcon', ({ className }: { className?: string }) => <i className={className} />);
  const emptyFs = { assessments: [], exempt: false, evidence: [] };
  api.fetchEnrolmentBoard.mockResolvedValue({ user: { id: '20', name: 'Historical Learner' }, contact: {}, activity: {}, programme: { name: 'Marketing Executive Level 4', status: 'Onboarding' }, subProgrammes: [], aims: [], previousProgrammes: [], functionalSkills: { english: emptyFs, maths: emptyFs, ict: emptyFs }, managedJobs: [], tracker: [], milestones: [], notes: [], courseProgress: [], contacts: [], activities: [], complianceDocuments: [], reviewDocuments: [], documents: [], competencies: [], subscription: {}, auditTrail: [], trainingPlan: [] });
  const aa = detail('apprenticeship-agreement'); const ilr = detail('ilr');
  for (const value of [aa, ilr]) history.set(value.revision.documentKey, [{ ...value.revision, id: `${value.revision.documentKey}-r1`, number: 1, state: 'original', signatureMethod: 'original', status: 'Signed', fullySigned: true }]);
  api.fetchAgreement.mockResolvedValue({ agreement: { id: 'issued-aa', ...aa.preview.data, particulars: aa.preview.data.particulars, fullySigned: true } });
  api.fetchIlrDocument.mockResolvedValue({ document: { id: 'signed-ilr', ...ilr.preview.data, fullySigned: true } });
  fetchMock.mockImplementation(async (url: string, options: RequestInit = {}) => {
    if (url.includes('enrollment-csrf')) return result({ csrfToken: 'synthetic-csrf' });
    if (url.includes('/document-templates/fields/')) return result({ results: [] });
    const key = (url.includes('/ilr/') || url.includes('/ilr-r2/')) ? 'ilr' : 'apprenticeship-agreement';
    if (url.endsWith('/history/')) return result(ready ? { results: history.get(key) || [], setupRequired: false } : { results: [], setupRequired: true, setup });
    if (url.endsWith('/drafts/')) {
      if (!ready) return result({ error: setup.message, setupRequired: true, setup }, 503);
      const data = JSON.parse(options.body as string);
      const value = drafts.get(key) || detail(key);
      if (data.mode === 'learner' && !drafts.has(key)) {
        value.revision.creationMode = 'learner'; value.revision.signatureMethod = 'sign-again'; value.revision.provenance = {};
        value.preview.template.content = contentFor(key);
        value.preview.template.content.blocks.unshift({ id: 'latest-question', type: 'field', label: 'Latest contract question?', value: '' });
        value.preview.template.fields = { 'document.particulars.apprenticeName': 'Current learner name' };
        value.preview.data.signatures = {}; value.preview.data.marks = {}; value.preview.data.learnerInformation = { name: 'Historical Learner' };
      }
      drafts.set(key, value); history.set(key, [...history.get(key)!.filter(r => r.number === 1), value.revision]); return result(value, 201);
    }
    const value = drafts.get(key)!;
    if (url.endsWith('/preview/')) return result({ ...value.preview, previewToken: 'reviewed-pdf' });
    if (url.endsWith('/finalize/')) { value.revision.state = 'finalized'; value.revision.status = 'Updated Copy'; return result(value); }
    if (options.method === 'PUT') {
      const data = JSON.parse(options.body as string); value.revision = { ...value.revision, editVersion: value.revision.editVersion + 1, reason: data.reason, signatureMethod: data.signatureMethod }; value.preview.template.content = data.content; history.set(key, [...history.get(key)!.filter(r => r.number === 1), value.revision]);
    }
    return result(value);
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

describe('Edit on the actual learner profile Documents tab', () => {
  it('keeps issued headers visible, toggles only their histories without writes, and resets on reopening Documents', async () => {
    ready = true;
    for (const key of ['apprenticeship-agreement', 'ilr'] as const) {
      const draft = detail(key).revision;
      history.set(key, [{ ...draft, id: `${key}-r1`, number: 1, state: 'original', status: 'Signed', fullySigned: true }, draft]);
    }
    mount();
    const aa = await screen.findByRole('button', { name: 'Expand version history for Apprenticeship Agreement' });
    const ilr = await screen.findByRole('button', { name: 'Expand version history for Individual Learner Record' });
    expect(aa).toHaveAttribute('aria-expanded', 'false');
    expect(ilr).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('Version 1 — Original')).not.toBeInTheDocument();
    expect(screen.getAllByTitle(/signed/).length).toBeGreaterThan(0);
    fireEvent.click(aa); fireEvent.click(ilr);
    expect(screen.getAllByText('Version 1 — Original')).toHaveLength(2);
    const card = screen.getByRole('region', { name: 'ilr document revisions' });
    fireEvent.click(within(card).getByRole('button', { name: 'History' }));
    expect(ilr).toHaveAttribute('aria-expanded', 'false');
    expect(aa).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(screen.getByRole('tab', { name: 'Overview' }));
    fireEvent.click(screen.getByRole('tab', { name: 'Documents' }));
    const reopened = await screen.findByRole('button', { name: 'Expand version history for Apprenticeship Agreement' });
    expect(reopened).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('Version 1 — Original')).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([, options]) => options?.method && options.method !== 'GET')).toHaveLength(0);
  });

  it('reports missing storage, then retries Create and opens latest questions with current learner data', async () => {
    mount(); const card = await screen.findByRole('region', { name: 'apprenticeship-agreement document revisions' });
    await within(card).findByText(/Missing storage: Learner_Document_Revisions/);
    const edit = within(card).getByRole('button', { name: 'Create' }); expect(edit).toBeEnabled();
    fireEvent.click(edit); expect(await within(card).findByRole('alert')).toHaveTextContent(setup.message);
    expect(screen.queryByRole('dialog', { name: 'Learner document revision editor' })).not.toBeInTheDocument();
    ready = true; await waitFor(() => expect(edit).toBeEnabled()); fireEvent.click(edit);
    const editor = await screen.findByRole('dialog', { name: 'Learner document revision editor' });
    expect(editor).toHaveTextContent('New Document — Version 2 (Draft)');
    await waitFor(() => expect(editor).toHaveTextContent('Latest contract question?'));
    expect(editor).toHaveTextContent('Current learner name');
    expect(editor).not.toHaveTextContent('Original frozen learner wording');
    expect(within(editor).queryByRole('button', { name: 'Keep Existing Signatures' })).not.toBeInTheDocument();
    expect(within(card).queryByText(/Missing storage:/)).not.toBeInTheDocument();
    const writes = fetchMock.mock.calls.filter(([url]) => url.endsWith('/drafts/'));
    expect(writes).toHaveLength(2); expect(JSON.parse(writes[1][1].body)).toEqual({ sourceId: 'issued-aa', mode: 'learner', requestId: expect.any(String) });
    expect(JSON.parse(writes[0][1].body).requestId).toBe(JSON.parse(writes[1][1].body).requestId);
    expect(writes[1][0]).toBe('/enrolment_api/document-revisions/learners/20/apprenticeship-agreement/drafts/');
  });

  it.each(['apprenticeship-agreement', 'ilr'] as const)('opens %s, saves/reopens the draft and finalises Keep with working History', async (key) => {
    ready = true; const view = mount(); const card = await screen.findByRole('region', { name: `${key} document revisions` });
    await screen.findByRole('button', { name: `Expand version history for ${key === 'ilr' ? 'Individual Learner Record' : 'Apprenticeship Agreement'}` });
    fireEvent.click(within(card).getByRole('button', { name: 'History' }));
    const original = await within(card).findByRole('article');
    fireEvent.click(within(original).getByRole('button', { name: 'Edit' }));
    let editor = await screen.findByRole('dialog', { name: 'Learner document revision editor' });
    expect(editor).toHaveTextContent('Template V1.0 / Revision R2'); expect(editor).toHaveTextContent('Original frozen learner wording');
    fireEvent.click(within(editor).getByRole('button', { name: 'Keep Existing Signatures' }));
    fireEvent.change(within(editor).getByLabelText('Reason for change (required)'), { target: { value: 'Learner-specific correction' } });
    fireEvent.click(within(editor).getByRole('button', { name: 'Save revision draft' }));
    await waitFor(() => expect(within(editor).getByRole('button', { name: 'Preview saved revision' })).toBeEnabled());
    view.unmount(); mount();
    const reopened = await screen.findByRole('region', { name: `${key} document revisions` });
    await screen.findByRole('button', { name: `Expand version history for ${key === 'ilr' ? 'Individual Learner Record' : 'Apprenticeship Agreement'}` });
    fireEvent.click(within(reopened).getByRole('button', { name: 'History' }));
    fireEvent.click(await within(reopened).findByRole('button', { name: 'Continue editing' }));
    editor = await screen.findByRole('dialog', { name: 'Learner document revision editor' });
    expect(within(editor).getByLabelText('Reason for change (required)')).toHaveValue('Learner-specific correction');
    expect(within(editor).getByRole('button', { name: 'Keep Existing Signatures' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(editor).getByRole('button', { name: 'Sign Again' })).toBeEnabled();
    fireEvent.click(within(editor).getByRole('button', { name: 'Preview saved revision' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Reviewed PDF' }));
    fireEvent.click(within(editor).getByRole('button', { name: 'Finalise revision' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Learner document revision editor' })).not.toBeInTheDocument());
    await within(reopened).findByText('Version 2 — Updated Copy');
    fireEvent.click(within(reopened).getByRole('button', { name: 'View history details' }));
    expect(await screen.findByRole('dialog', { name: 'Document revision history' })).toHaveTextContent('Template V1.0 / Revision R2');
    expect(drafts.size).toBe(1);
    const firstWrite = fetchMock.mock.calls.find(([url]) => url.endsWith('/drafts/'))!;
    expect(JSON.parse(firstWrite[1].body)).toEqual({ sourceId: key === 'ilr' ? 'signed-ilr' : 'issued-aa', baseRevisionId: `${key}-r1` });
    expect(fetchMock.mock.calls.filter(([url, options]) => url.includes('/document-templates/') && options.method === 'PUT')).toHaveLength(0);
  });
});
