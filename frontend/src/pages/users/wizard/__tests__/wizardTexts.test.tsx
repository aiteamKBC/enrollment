/**
 * Editable wording: the formatting language, what learners see once wording is
 * published from the builder, and the Extended ILR PDF printing the wording the
 * learner was actually asked.
 */
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

const fetchWizardLayout = vi.fn();
vi.mock('@/api/extendedIlr', () => ({
  fetchExtendedIlr: vi.fn().mockResolvedValue({ answers: null, draft: null, meta: { updatedAt: '' } }),
  saveExtendedIlr: vi.fn(),
  peekExtendedIlr: () => undefined,
}));
vi.mock('@/api/enrolmentDocuments', () => ({ uploadEnrolmentDocument: vi.fn() }));
vi.mock('@/api/curriculum', () => ({ fetchKsbProfile: vi.fn().mockResolvedValue({ results: [] }), peekKsbProfile: () => undefined }));
vi.mock('@/api/wizardLayout', () => ({
  fetchWizardLayout: (...args: unknown[]) => fetchWizardLayout(...args),
  peekWizardLayout: () => undefined,
  fetchCustomUploads: vi.fn().mockResolvedValue([]),
}));

import { ToastProvider } from '@/hooks/useToast';
import { WizardProvider } from '../WizardContext';
import { WizardShell } from '../WizardShell';
import { defaultLayout, resolveLayout } from '../layout/resolve';
import { parseRich, plainText } from '../layout/richFormat';
import { RichText } from '../layout/RichText';
import { TEXT_SLOTS, textFor } from '../layout/texts';
import { buildRows } from '../steps/ilrDocument';
import NextSteps from '../steps/NextSteps';
import type { EnrolmentBoard, IlrForm } from '../../types';

const BOARD = {
  user: { id: '20', name: 'Test Learner', reference: 'REF20', owner: '' },
  contact: { email: '', phone: '', dob: '', groupMembership: '', hasMandate: false },
  programme: { name: '', cohort: '', type: '', status: 'Onboarding', startDate: '', endDate: '', enrolledAt: '', enrolledBy: '', onboardingStatus: 'In progress' },
} as unknown as EnrolmentBoard;

beforeAll(() => {
  (globalThis as Record<string, unknown>).AppIcon = ({ className }: { className?: string }) => <i className={className} />;
});
beforeEach(() => vi.clearAllMocks());

describe('the formatting language', () => {
  it('reads headings, lists, bold, italic and links', () => {
    const blocks = parseRich('## Title\nIntro with **bold** and *italic*\n\n- one\n- [two](https://example.org)');
    expect(blocks.map((b) => b.type)).toEqual(['heading', 'paragraph', 'list']);
    render(<RichText source={'## Title\nSee **this** and [the site](https://example.org) or [mail](mailto:a@b.org)'} />);
    expect(screen.getByRole('heading', { name: 'Title' })).toBeInTheDocument();
    expect(screen.getByText('this', { selector: 'strong' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'the site' })).toHaveAttribute('target', '_blank');
    expect(screen.getByRole('link', { name: 'mail' })).toHaveAttribute('href', 'mailto:a@b.org');
  });

  it('never makes a link of anything but http(s) or mailto, and never renders markup', () => {
    const { container } = render(<RichText source={'[click](javascript:alert(1)) <img src=x onerror=alert(1)>'} />);
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(container.querySelector('img')).toBeNull();
    expect(container.textContent).toContain('<img src=x onerror=alert(1)>');
  });

  it('flattens to plain text for the PDF', () => {
    expect(plainText('**Note:** read [this](https://x.org)\n\n- a\n- b')).toBe('Note: read this\n\n• a\n• b');
  });
});

describe('published wording', () => {
  it('keeps the standard wording for anything not edited, and ignores unknown or blank edits', () => {
    const layout = resolveLayout({ ...defaultLayout(), texts: { 'pd.address.label': 'Home address', 'nope.key': 'x', 'pd.email.label': '  ' } });
    expect(layout.texts).toEqual({ 'pd.address.label': 'Home address' });
    expect(textFor(layout.texts, 'pd.email.label')).toBe('Email');
  });

  it('every information page renders its standard wording outside a wizard', () => {
    render(<NextSteps />);
    expect(screen.getByRole('heading', { name: 'What Happens Now?' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Book Compliance Meeting' })).toBeInTheDocument();
  });

  it('shows learners the edited page text and question labels', async () => {
    const layout = defaultLayout();
    fetchWizardLayout.mockResolvedValue({
      layout: { ...layout, texts: { 'block.introduction.title': 'Hello and welcome', 'block.introduction.body': '## Our college\nWe are **glad** you are here.', 'pd.address.label': 'Home address' } },
      version: 2, updatedAt: '', updatedBy: '',
    });
    const { unmount } = render(
      <ToastProvider>
        <WizardProvider userId="20" board={BOARD}>
          <WizardShell currentIndex={0} mode="staff" onNavigateStep={vi.fn()} onFinish={vi.fn()} />
        </WizardProvider>
      </ToastProvider>
    );
    expect(await screen.findByRole('heading', { name: 'Hello and welcome' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Our college' })).toBeInTheDocument();
    expect(screen.queryByText(/Shaping Tomorrow/)).not.toBeInTheDocument();
    unmount();

    render(
      <ToastProvider>
        <WizardProvider userId="20" board={BOARD}>
          <WizardShell currentIndex={2} mode="staff" onNavigateStep={vi.fn()} onFinish={vi.fn()} />
        </WizardProvider>
      </ToastProvider>
    );
    await waitFor(() => expect(screen.getByText('Home address', { selector: 'div' })).toBeInTheDocument());
  });
});

describe('the Extended ILR PDF', () => {
  const ilr = {
    contact: { byPost: true, byPhone: null, byEmail: null },
    nextOfKin: { fullName: 'A', relationship: '', email: '', phone: '', sameAddressAsLearner: true },
    eligibility: { employedInEngland: null, countryOfResidence: '', ukEeaNational: null, nationality: '', residentPrev3Years: null, requiresWorkPermit: null, evidenceDescription: '', evidenceFiles: [] },
    employer: { organisationName: '', postcode: '', address: '', city: '', lineManagerName: '', lineManagerEmail: '', lineManagerPhone: '' },
    otherTraining: { attended12m: null, completedWhen: '' },
    circumstances: { caringResponsibilities: '', other: '', careLeaver: null },
    understanding: { programmeUnderstanding: '', careerProgression: '' },
    additionalInformation: { jobRoleRelevance: '', residenceNotForFullTimeEducation: '', ehcp: '', otherNames: '' },
    declarations: { over50PercentEngland: null, wageRateBand: '', knownByOtherName: null, plrAccessAware: null },
  } as unknown as IlrForm;
  const labels = (texts?: Record<string, string>) =>
    buildRows(ilr, BOARD, texts).map((r) => ('label' in r ? r.label : 'title' in r ? r.title : 'text' in r ? r.text : ''));

  it('prints its own wording when nothing is edited', () => {
    const rows = labels();
    expect(rows).toContain('By post');
    expect(rows).toContain('Country of residence');
  });

  it('prints edited wording as the learner was asked it', () => {
    const rows = labels({ 'xilr.contactPost.label': 'By letter', 'xilr.ehcp.sharing': 'New **EHCP** note', 'xilr.h.contact.title': 'How to reach you' });
    expect(rows).toContain('By letter');
    expect(rows).not.toContain('By post');
    expect(rows).toContain('New EHCP note');
    expect(rows).toContain('How to reach you');
  });

  it('only prints wording that the PDF prints', () => {
    // Every slot the PDF reads is plain text, so nothing printed loses formatting.
    const printed = ['xilr.contactPost.label', 'xilr.ehcp.sharing', 'xilr.plrAccessAware.sharing', 'xilr.h.contact.title'];
    for (const key of printed) expect(TEXT_SLOTS[key].kind).not.toBe('rich');
  });
});
