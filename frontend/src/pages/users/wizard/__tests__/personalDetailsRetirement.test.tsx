import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { EnrolmentBoard, WizardDraft } from '../../types';
import { WizardProvider } from '../WizardContext';
import { useWizard, type WizardContextValue } from '../WizardContext.helpers';
import EnrolmentSignature from '../steps/EnrolmentSignature';
import { defaultLayout, layoutForLearner, missingForLayoutStep, resolveLayout, visibleSteps } from '../layout/resolve';
import { signatureDeclaration } from '../signatureDeclaration';

const api = vi.hoisted(() => ({ fetch: vi.fn(), save: vi.fn() }));
vi.mock('@/api/extendedIlr', () => ({ fetchExtendedIlr: api.fetch, saveExtendedIlr: api.save, peekExtendedIlr: () => undefined }));
vi.mock('@/api/wizardLayout', () => ({ fetchWizardLayout: vi.fn(async () => ({ layout: null, version: null })), peekWizardLayout: () => undefined }));
vi.mock('@/api/curriculum', () => ({ fetchKsbProfile: vi.fn(async () => ({ results: [] })), peekKsbProfile: () => undefined }));
vi.mock('@/api/enrolmentDocuments', () => ({ uploadEnrolmentDocument: vi.fn() }));
vi.mock('@/api/wizardInclusion', () => ({ fetchInclusionStatus: vi.fn(async () => ({ applicable: true, completed: false })), SAFEGUARDING_URL: 'https://example.invalid' }));
vi.mock('../steps/SignaturePad', () => ({ SignaturePad: ({ onCommit, onCancel }: { onCommit: (value: string) => void; onCancel: () => void }) => <>
  <button onClick={() => onCommit('data:image/png;base64,DRAWN')}>Commit drawn signature</button><button onClick={onCancel}>Cancel drawing</button>
</> }));

const signature = 'data:image/png;base64,HISTORICAL';
const personal = { firstName: 'Historic', lastName: 'Learner', email: 'historic@example.invalid', phone: '123', address: 'Preserved address', dob: '1991-06-12', sex: 'Female', signature, signatureDate: '2020-04-03' };
const board = { user: { id: '41', name: 'Historic Learner' }, contact: { email: personal.email, phone: personal.phone, dob: personal.dob, savedSignature: signature, savedSignatureDate: personal.signatureDate, titleRequired: false }, programme: { name: '', onboardingStatus: 'In progress' } } as EnrolmentBoard;
let latest: WizardContextValue;
function Probe() { latest = useWizard(); return <EnrolmentSignature />; }
const open = () => render(<WizardProvider userId="41" board={board}><Probe /></WizardProvider>);

beforeEach(() => {
  vi.clearAllMocks();
  api.fetch.mockResolvedValue({ answers: null, draft: { personalDetails: personal }, meta: { updatedAt: 'saved' } });
  api.save.mockResolvedValue({ meta: { updatedAt: 'new-save' } });
});
afterEach(cleanup);

it('retires the apprenticeship page in any publication and moves its stable signature key to ILR last', () => {
  const base = defaultLayout();
  const pd = base.steps.find(step => step.slug === 'personal-details')!;
  pd.hidden = false;
  pd.items.find(item => item.key === 'pd.signature')!.required = false;
  base.steps = [pd, ...base.steps.filter(step => step !== pd)];
  base.texts = { 'pd.signature.help': 'Historic paperwork wording' };
  const before = structuredClone(base);
  const active = layoutForLearner(resolveLayout(base), false);
  expect(visibleSteps(active).some(step => step.slug === 'personal-details')).toBe(false);
  expect(active.steps.find(step => step.slug === 'ilr-details')!.items.at(-1)).toMatchObject({ key: 'pd.signature', required: true, hidden: false });
  expect(active.texts?.['pd.signature.help']).toBe('Historic paperwork wording');
  expect(base).toEqual(before);
  expect(layoutForLearner(active, false)).toEqual(active);
  const commercial = layoutForLearner(resolveLayout(base), true);
  expect(visibleSteps(commercial).some(step => step.slug === 'personal-details')).toBe(true);
  expect(commercial.steps.find(step => step.slug === 'personal-details')!.items.at(-1)!.key).toBe('pd.signature');
  expect(commercial.steps.find(step => step.slug === 'ilr-details')!.items.filter(item =>
    ['ild.niApplied', 'ild.healthConditions', 'ild.primaryHealth', 'ild.additionalSupport', 'ild.workingHours'].includes(item.key)
  ).every(item => item.hidden)).toBe(true);
});

it('uses the published Personal Details step state in new layouts while keeping old versions unchanged', () => {
  const legacy = resolveLayout(defaultLayout());
  expect(layoutForLearner(legacy, false).steps.find(step => step.slug === 'personal-details')!.hidden).toBe(true);
  const managed = resolveLayout({ ...defaultLayout(), stepVisibilityManaged: true });
  expect(layoutForLearner(managed, false).steps.find(step => step.slug === 'personal-details')!.hidden).toBe(false);
  managed.steps.find(step => step.slug === 'personal-details')!.hidden = true;
  expect(layoutForLearner(managed, false).steps.find(step => step.slug === 'personal-details')!.hidden).toBe(true);
});

it('requires the relocated signature while retired identity questions no longer gate apprenticeships', () => {
  const layout = layoutForLearner(resolveLayout(null), false);
  const ilr = layout.steps.find(step => step.slug === 'ilr-details')!;
  const draft = { personalDetails: {}, ilrDetails: {} } as unknown as WizardDraft;
  const missing = missingForLayoutStep(ilr, draft, layout);
  expect(missing).toContain('Your signature');
  expect(missing).not.toContain('First Name');
  draft.personalDetails.signature = signature;
  expect(missingForLayoutStep(ilr, draft, layout)).toContain('Your signature'); // Old paperwork mark is not new consent.
  draft.personalDetails.signatureDeclaration = { text: signatureDeclaration(), confirmedAt: '2026-11-03T10:00:00Z' };
  expect(missingForLayoutStep(ilr, draft, layout)).not.toContain('Your signature');
  expect(missingForLayoutStep(ilr, draft, layout)).not.toContain('Declaration signature');
});

it('restores Title in older or hidden publications and keeps it out of commercial ILR', () => {
  const saved=defaultLayout();
  const ilr=saved.steps.find(step=>step.slug==='ilr-details')!;
  const title=ilr.items.find(item=>item.key==='ild.title')!;
  title.hidden=true; title.required=false; title.condition={field:'pd.sex',values:['never']};
  const active=layoutForLearner(resolveLayout(saved),false);
  expect(active.steps.find(step=>step.slug==='ilr-details')!.items.filter(item=>item.key==='ild.title'))
    .toEqual([expect.objectContaining({hidden:false,required:true,condition:undefined})]);
  expect(layoutForLearner(resolveLayout(saved),true).steps.find(step=>step.slug==='ilr-details')!.items.find(item=>item.key==='ild.title')!.hidden).toBe(true);
});

it('requires only the combined signature even when a historical layout hid signing or made it optional', () => {
  const base = defaultLayout();
  base.steps.find(step => step.slug === 'personal-details')!.hidden = true;
  const active = layoutForLearner(resolveLayout(base), false);
  expect(active.steps.find(step => step.slug === 'ilr-details')!.items.at(-1)).toMatchObject({ key: 'pd.signature', required: true });
});

it('cannot restore the retired signing box through published visibility, conditions or requiredness', () => {
  const base = defaultLayout();
  const old = base.steps.find(step => step.slug === 'ilr-details')!.items.find(item => item.key === 'ild.signature')!;
  old.required = true;
  old.hidden = true;
  const mark = base.steps.find(step => step.slug === 'personal-details')!.items.find(item => item.key === 'pd.signature')!;
  mark.hidden = true;
  mark.condition = { field: 'pd.sex', values: ['Never shown'] };
  const active = layoutForLearner(resolveLayout(base), false);
  const ilr = active.steps.find(step => step.slug === 'ilr-details')!;
  expect(ilr.items.slice(-2)).toEqual([
    expect.objectContaining({ key: 'ild.signature', hidden: false, required: false, condition: undefined }),
    expect.objectContaining({ key: 'pd.signature', hidden: false, required: true, condition: undefined }),
  ]);
});

it('requires fresh explicit confirmation if the published accuracy declaration changes', () => {
  const layout = layoutForLearner(resolveLayout(null), false);
  const ilr = layout.steps.find(step => step.slug === 'ilr-details')!;
  const draft = { personalDetails: { ...personal, signatureDeclaration: { text: signatureDeclaration(), confirmedAt: '2026-11-03T10:00:00Z' } }, ilrDetails: {} } as unknown as WizardDraft;
  expect(missingForLayoutStep(ilr, draft, layout)).not.toContain('Your signature');
  layout.texts = { 'ild.signature.certify': 'Updated accuracy declaration' };
  expect(missingForLayoutStep(ilr, draft, layout)).toContain('Your signature');
  expect(draft.personalDetails.signatureDate).toBe(personal.signatureDate);
});

it('keeps a historical signature and its date without requiring a new mark', async () => {
  open(); await screen.findByRole('img', { name: 'Your signature' });
  expect(latest.draft.personalDetails).toMatchObject(personal);
  expect(latest.draft.ilrDetails.signature).toBeNull(); // No silent certification.
  expect(api.save).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Confirm signature for this declaration' })).toBeInTheDocument();
});

it('leaves commercial paperwork signing and validation unchanged', async () => {
  render(<WizardProvider userId="41" board={board} isCommercial><Probe /></WizardProvider>);
  await screen.findByRole('img', { name: 'Your signature' });
  expect(screen.queryByText(/confirm the ILR accuracy declaration and authorise/)).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Confirm signature for this declaration' })).not.toBeInTheDocument();
  const layout = layoutForLearner(resolveLayout(null), true);
  const pd = layout.steps.find(step => step.slug === 'personal-details')!;
  expect(missingForLayoutStep(pd, latest.draft, layout)).not.toContain('Your signature');
  fireEvent.click(screen.getByRole('button', { name: 'Clear' }));
  fireEvent.click(screen.getByRole('button', { name: 'Click to sign' }));
  fireEvent.click(screen.getByRole('button', { name: 'Commit drawn signature' }));
  expect(latest.draft.personalDetails.signatureDeclaration).toBeUndefined();
});

it('does not copy an old ILR-only certification into the remaining signature', async () => {
  api.fetch.mockResolvedValue({ answers: null, draft: { personalDetails: { ...personal, signature: '', signatureDate: '' },
    ilrDetails: { signature: 'data:image/png;base64,ILR-ONLY', signatureDate: '2019-01-02' } }, meta: { updatedAt: 'saved' } });
  open(); await waitFor(() => expect(latest.hydrated).toBe(true));
  expect(screen.queryByRole('img', { name: 'Your signature' })).not.toBeInTheDocument();
  expect(latest.draft.ilrDetails.signature).toBe('data:image/png;base64,ILR-ONLY');
  expect(latest.draft.personalDetails.signature).toBe('');
});

it('confirms a historical mark explicitly, retaining its date and the separate historical certification on save/reopen', async () => {
  const certification = { signature: 'data:image/png;base64,ILR-HISTORICAL', signatureDate: '2019-01-02' };
  let saved: Partial<WizardDraft> = { personalDetails: personal, ilrDetails: certification as WizardDraft['ilrDetails'] };
  api.fetch.mockImplementation(async () => ({ answers: null, draft: saved, meta: { updatedAt: 'saved' } }));
  api.save.mockImplementation(async (_kind, _id, _answers, draft) => { saved = draft; return { meta: { updatedAt: 'new-save' } }; });
  const view = open(); await screen.findByRole('img', { name: 'Your signature' });
  fireEvent.click(screen.getByRole('button', { name: 'Confirm signature for this declaration' }));
  expect(latest.draft.personalDetails).toMatchObject({ ...personal, signatureDeclaration: { text: signatureDeclaration(), confirmedAt: expect.any(String) } });
  expect(latest.draft.ilrDetails).toMatchObject(certification);
  const event = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(event);
  expect(event.defaultPrevented).toBe(true);
  await act(async () => { await latest.saveIlr(); });
  view.unmount(); open(); await screen.findByRole('img', { name: 'Your signature' });
  expect(screen.queryByRole('button', { name: 'Confirm signature for this declaration' })).not.toBeInTheDocument();
  expect(latest.draft.personalDetails.signatureDate).toBe(personal.signatureDate);
  expect(latest.draft.ilrDetails).toMatchObject(certification);
});

it('captures and clears using the existing component and persists the same address/signature contract across refresh', async () => {
  let saved = personal;
  api.fetch.mockImplementation(async () => ({ answers: null, draft: { personalDetails: saved }, meta: { updatedAt: 'saved' } }));
  api.save.mockImplementation(async (_kind, _id, _answers, draft) => { saved = draft.personalDetails; return { meta: { updatedAt: 'new-save' } }; });
  let view = open(); await screen.findByRole('img', { name: 'Your signature' });
  fireEvent.click(within(screen.getByRole('region', { name: 'Enrolment paperwork signature' })).getByRole('button', { name: 'Clear' }));
  expect(latest.draft.personalDetails).toMatchObject({ signature: '', signatureDate: '', address: personal.address });
  expect(latest.draft.personalDetails.signatureDeclaration).toBeNull();
  await act(async () => { await latest.saveIlr(); });
  view.unmount(); view = open();
  await waitFor(() => expect(latest.hydrated).toBe(true));
  expect(screen.queryByRole('img', { name: 'Your signature' })).not.toBeInTheDocument();
  expect(latest.draft.personalDetails.signature).toBe(''); // No refill from record's old saved signature.
  fireEvent.click(screen.getByRole('button', { name: 'Click to sign' }));
  fireEvent.click(screen.getByRole('button', { name: 'Commit drawn signature' }));
  await act(async () => { await latest.saveIlr(); });
  expect(saved).toMatchObject({ signature: 'data:image/png;base64,DRAWN', address: personal.address });
  expect(saved.signatureDate).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  expect(latest.draft.personalDetails.signatureDeclaration?.text).toBe(signatureDeclaration());
  view.unmount(); open();
  await waitFor(() => expect(screen.getByRole('img', { name: 'Your signature' })).toHaveAttribute('src', 'data:image/png;base64,DRAWN'));
  expect(latest.draft.personalDetails.signatureDate).toBe(saved.signatureDate);
});

it('serializes whole-draft writes so a late older save cannot erase a newer signature', async () => {
  open(); await screen.findByRole('img', { name: 'Your signature' });
  let release!: () => void;
  api.save.mockImplementationOnce(() => new Promise(resolve => { release = () => resolve({ meta: { updatedAt: 'first' } }); }));
  act(() => latest.setSection('personalDetails', { ...latest.draft.personalDetails, signature: 'first' }));
  const first = latest.saveIlr();
  await waitFor(() => expect(api.save).toHaveBeenCalledTimes(1));
  act(() => latest.setSection('personalDetails', { ...latest.draft.personalDetails, signature: 'second' }));
  const second = latest.saveIlr();
  expect(api.save).toHaveBeenCalledTimes(1);
  await act(async () => { release(); await first; await second; });
  expect(api.save).toHaveBeenCalledTimes(2);
  expect(api.save.mock.calls[1][3].personalDetails.signature).toBe('second');
});

it('does not overwrite historical answers if their initial read failed', async () => {
  api.fetch.mockRejectedValue(new Error('read failed'));
  open(); await waitFor(() => expect(latest.hydrated).toBe(true));
  await expect(latest.saveIlr()).rejects.toThrow('Refresh and try again');
  expect(api.save).not.toHaveBeenCalled();
});

it('warns before refresh only while the signature has unsaved changes', async () => {
  open(); await screen.findByRole('img', { name: 'Your signature' });
  let event = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(event);
  expect(event.defaultPrevented).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: 'Clear' }));
  event = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(event);
  expect(event.defaultPrevented).toBe(true);
  await act(async () => { await latest.saveIlr(); });
  event = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(event);
  expect(event.defaultPrevented).toBe(false);
});

it('does not invent a signing date when the record only holds an old signature image', async () => {
  api.fetch.mockResolvedValue({ answers: null, draft: null, meta: { updatedAt: '' } });
  render(<WizardProvider userId="41" board={{ ...board, contact: { ...board.contact, savedSignatureDate: undefined } }}><Probe /></WizardProvider>);
  await waitFor(() => expect(latest.hydrated).toBe(true));
  expect(latest.draft.personalDetails.signature).toBe(signature);
  expect(latest.draft.personalDetails.signatureDate).toBeUndefined();
});
