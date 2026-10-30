/**
 * The wizard renders and validates from the published layout (the builder):
 * steps in its order, removed items skipped, required/optional as set, custom
 * fields asked and checked, and follow-ups shown only for the chosen answer.
 * With nothing published the wizard is exactly the standard one.
 */
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const fetchExtendedIlr = vi.fn();
const saveExtendedIlr = vi.fn();
vi.mock('@/api/extendedIlr', () => ({
  fetchExtendedIlr: (...args: unknown[]) => fetchExtendedIlr(...args),
  saveExtendedIlr: (...args: unknown[]) => saveExtendedIlr(...args),
  peekExtendedIlr: () => undefined,
}));
vi.mock('@/api/enrolmentDocuments', () => ({ uploadEnrolmentDocument: vi.fn() }));
vi.mock('@/api/curriculum', () => ({
  fetchKsbProfile: vi.fn().mockResolvedValue({ results: [] }),
  peekKsbProfile: () => undefined,
}));
const fetchWizardLayout = vi.fn();
vi.mock('@/api/wizardLayout', () => ({
  fetchWizardLayout: (...args: unknown[]) => fetchWizardLayout(...args),
  peekWizardLayout: () => undefined,
  fetchCustomUploads: vi.fn().mockResolvedValue([]),
  uploadCustomFile: vi.fn(),
  deleteCustomUpload: vi.fn(),
  getCustomUploadUrl: vi.fn(),
}));

import { ToastProvider } from '@/hooks/useToast';
import { WizardProvider } from '../WizardContext';
import { WizardShell } from '../WizardShell';
import { DEFAULT_LAYOUT, defaultLayout, missingForLayoutStep, resolveLayout, visibleSteps } from '../layout/resolve';
import { missingForStep } from '../validation';
import { WIZARD_STEPS, type EnrolmentBoard, type WizardDraft } from '../../types';
import type { LayoutItem, WizardLayout } from '../layout/types';

const BOARD = {
  user: { id: '20', name: 'Test Learner', reference: 'REF20', owner: '' },
  contact: { email: '', phone: '', dob: '', groupMembership: '', hasMandate: false },
  programme: { name: '', cohort: '', type: '', status: 'Onboarding', startDate: '', endDate: '', enrolledAt: '', enrolledBy: '', onboardingStatus: 'In progress' },
} as unknown as EnrolmentBoard;

const COMPLETE_PD = {
  firstName: 'Test', lastName: 'Learner', email: 'test@example.com', phone: '07123456789',
  address: '1 High Street', dob: '2000-01-01', age: 26, sex: 'Female', signature: 'data:image/png;base64,AAAA',
};

const draftWith = (patch: Partial<WizardDraft>): WizardDraft => ({
  personalDetails: { ...COMPLETE_PD },
  custom: {},
  ...patch,
} as unknown as WizardDraft);

/** The default layout with Personal Details' items replaced. */
function withPersonalDetails(edit: (items: LayoutItem[]) => LayoutItem[]): WizardLayout {
  const layout = defaultLayout();
  const pd = layout.steps.find((s) => s.slug === 'personal-details')!;
  pd.items = edit(pd.items);
  return layout;
}

const SHIFT: LayoutItem = {
  key: 'cf_shift', builtin: false, hidden: false, required: true, label: 'Shift pattern', type: 'dropdown',
  options: ['Day', 'Night'], table: 'Wizard_Personal_Details', column: 'Custom_shift',
};
const NIGHT_NOTE: LayoutItem = {
  key: 'cf_night_note', builtin: false, hidden: false, required: true, label: 'Night allowance details', type: 'text',
  condition: { field: 'cf_shift', values: ['Night'] }, table: 'Wizard_Personal_Details', column: 'Custom_night_note',
};

/** The input or select in the row labelled `label`. */
const box = (label: string) => screen.getByText(label, { selector: 'div' }).parentElement!.querySelector('input, select') as HTMLElement;

beforeAll(() => {
  (globalThis as Record<string, unknown>).AppIcon = ({ className }: { className?: string }) => <i className={className} />;
});

beforeEach(() => {
  vi.clearAllMocks();
  fetchExtendedIlr.mockResolvedValue({ answers: null, draft: null, meta: { updatedAt: '' } });
  saveExtendedIlr.mockResolvedValue({ meta: { updatedAt: '2026-10-03T10:00:00Z' } });
  fetchWizardLayout.mockResolvedValue({ layout: null, version: null, updatedAt: '', updatedBy: '' });
});

describe('resolving a layout', () => {
  it('is the standard wizard when nothing is published', () => {
    expect(visibleSteps(resolveLayout(null)).map((s) => s.slug)).toEqual(WIZARD_STEPS.map((s) => s.slug));
  });

  it('puts back built-in steps and items a published layout does not mention, in their default place', () => {
    const layout = resolveLayout({
      steps: [
        { slug: 'personal-details', label: 'About you', builtin: true, hidden: false, items: [{ key: 'pd.sex', builtin: true, hidden: false }] },
      ],
    });
    const pd = layout.steps.find((s) => s.slug === 'personal-details')!;
    expect(pd.label).toBe('About you');
    expect(pd.items.map((i) => i.key)).toEqual([
      'pd.firstName', 'pd.lastName', 'pd.email', 'pd.phone', 'pd.address', 'pd.dob', 'pd.sex', 'pd.signature',
    ]);
    expect(layout.steps).toHaveLength(DEFAULT_LAYOUT.steps.length);
  });

  it('keeps a built-in question on its own step, but lets a block move', () => {
    const layout = resolveLayout({
      steps: [{ slug: 'cv-job', label: 'CV', builtin: true, hidden: false, items: [
        { key: 'pd.email', builtin: true, hidden: false },
        { key: 'block.policies', builtin: true, hidden: false },
      ] }],
    });
    expect(layout.steps.find((s) => s.slug === 'cv-job')!.items.map((i) => i.key)).toContain('block.policies');
    expect(layout.steps.find((s) => s.slug === 'cv-job')!.items.map((i) => i.key)).not.toContain('pd.email');
    expect(layout.steps.find((s) => s.slug === 'personal-details')!.items.map((i) => i.key)).toContain('pd.email');
    expect(layout.steps.find((s) => s.slug === 'policies')!.items).toEqual([]);
  });
});

describe('validation against a layout', () => {
  const step = (layout: WizardLayout) => layout.steps.find((s) => s.slug === 'personal-details')!;

  it('matches the standard rules with the default layout', () => {
    const draft = draftWith({ personalDetails: { ...COMPLETE_PD, address: '' } as WizardDraft['personalDetails'] });
    expect(missingForStep(2, draft)).toEqual(['Address']);
  });

  it('does not require a removed or optional built-in question, but still checks what is typed', () => {
    const layout = withPersonalDetails((items) => items.map((i) =>
      i.key === 'pd.address' ? { ...i, hidden: true } : i.key === 'pd.email' ? { ...i, required: false } : i));
    const blank = draftWith({ personalDetails: { ...COMPLETE_PD, address: '', email: '' } as WizardDraft['personalDetails'] });
    expect(missingForLayoutStep(step(layout), blank, layout)).toEqual([]);
    const badEmail = draftWith({ personalDetails: { ...COMPLETE_PD, email: 'w' } as WizardDraft['personalDetails'] });
    expect(missingForLayoutStep(step(layout), badEmail, layout)[0]).toMatch(/^Email — /);
  });

  it('requires an optional built-in question once it is made mandatory', () => {
    const layout = defaultLayout();
    const ild = layout.steps.find((s) => s.slug === 'ilr-details')!;
    ild.items = ild.items.map((i) => (i.key === 'ild.pronouns' ? { ...i, required: true } : i));
    const draft = { ...draftWith({}), ilrDetails: { pronouns: '' } } as unknown as WizardDraft;
    expect(missingForLayoutStep(ild, draft, layout)).toContain('What pronouns do you use?');
  });

  it('asks a follow-up only for the answer it belongs to', () => {
    const layout = withPersonalDetails((items) => [...items, SHIFT, NIGHT_NOTE]);
    expect(missingForLayoutStep(step(layout), draftWith({}), layout)).toEqual(['Shift pattern']);
    expect(missingForLayoutStep(step(layout), draftWith({ custom: { cf_shift: 'Day' } }), layout)).toEqual([]);
    expect(missingForLayoutStep(step(layout), draftWith({ custom: { cf_shift: 'Night' } }), layout)).toEqual(['Night allowance details']);
  });

  it('checks a number field is a number', () => {
    const years: LayoutItem = { key: 'cf_years', builtin: false, hidden: false, required: false, label: 'Years', type: 'number' };
    const layout = withPersonalDetails((items) => [...items, years]);
    expect(missingForLayoutStep(step(layout), draftWith({ custom: { cf_years: 'ten' } }), layout)).toEqual(['Years — Enter a number']);
    expect(missingForLayoutStep(step(layout), draftWith({ custom: { cf_years: '10' } }), layout)).toEqual([]);
  });
});

describe('the wizard with a published layout', () => {
  function renderWizard(layout: WizardLayout, currentIndex: number, mode: 'learner' | 'staff' = 'learner', onNavigateStep = vi.fn()) {
    fetchWizardLayout.mockResolvedValue({ layout, version: 3, updatedAt: '', updatedBy: '' });
    render(
      <ToastProvider>
        <WizardProvider userId="20" board={BOARD}>
          <WizardShell currentIndex={currentIndex} mode={mode} onNavigateStep={onNavigateStep} onFinish={vi.fn()} />
        </WizardProvider>
      </ToastProvider>
    );
    return onNavigateStep;
  }

  it('lists the steps in the published order, without hidden ones, under their new names', async () => {
    const layout = defaultLayout();
    const [intro, before, pd, ...rest] = layout.steps;
    before.hidden = true;
    pd.label = 'About you';
    layout.steps = [pd, intro, before, ...rest];
    renderWizard(layout, 0, 'staff');

    await waitFor(() => expect(screen.getAllByRole('tab')[0]).toHaveAccessibleName('About you'));
    const tabs = screen.getAllByRole('tab').map((t) => t.getAttribute('aria-label'));
    expect(tabs.slice(0, 2)).toEqual(['About you', 'Welcome']);
    expect(tabs).not.toContain('Before You Begin');
    // The renamed step's own heading follows its new name.
    expect(screen.getByRole('heading', { name: 'About you' })).toBeInTheDocument();
  });

  it('renders custom fields where placed and blocks Next until a mandatory one is answered', async () => {
    const layout = withPersonalDetails((items) => [...items.map((i) => (i.key === 'pd.address' ? { ...i, hidden: true } : i)), SHIFT, NIGHT_NOTE]);
    fetchExtendedIlr.mockResolvedValue({ answers: null, draft: { personalDetails: COMPLETE_PD }, meta: { updatedAt: '' } });
    const onNavigateStep = renderWizard(layout, 2);

    await screen.findByText('Shift pattern');
    const shift = box('Shift pattern');
    // The removed question is gone, and so is the follow-up until its answer is chosen.
    expect(screen.queryByText('Address')).not.toBeInTheDocument();
    expect(screen.queryByText('Night allowance details')).not.toBeInTheDocument();

    await waitFor(() => expect(fetchExtendedIlr).toHaveBeenCalled());
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(onNavigateStep).not.toHaveBeenCalled();
    expect(within(screen.getByText('Please complete this step before continuing').closest('div')!).getByText('Shift pattern')).toBeInTheDocument();

    await userEvent.selectOptions(shift, 'Night');
    // The follow-up appears — and joins the step's outstanding list, since it is mandatory.
    expect(await screen.findByText('Night allowance details', { selector: 'div' })).toBeInTheDocument();
    await userEvent.type(box('Night allowance details'), 'Paid at time and a half');
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(onNavigateStep).toHaveBeenCalledWith(3);

    // The answers travel with the draft, for the server to put in their columns.
    await waitFor(() => expect(saveExtendedIlr).toHaveBeenCalled());
    const rest = saveExtendedIlr.mock.calls.at(-1)![3] as WizardDraft;
    expect(rest.custom).toEqual({ cf_shift: 'Night', cf_night_note: 'Paid at time and a half' });
  });

  it('falls back to the standard wizard when the layout cannot be loaded', async () => {
    fetchWizardLayout.mockRejectedValue(new Error('offline'));
    render(
      <ToastProvider>
        <WizardProvider userId="20" board={BOARD}>
          <WizardShell currentIndex={2} mode="staff" onNavigateStep={vi.fn()} onFinish={vi.fn()} />
        </WizardProvider>
      </ToastProvider>
    );
    await waitFor(() => expect(fetchWizardLayout).toHaveBeenCalled());
    expect(screen.getAllByRole('tab')).toHaveLength(WIZARD_STEPS.length);
    expect(screen.getByText('Address')).toBeInTheDocument();
  });
});
