/**
 * Regression: a learner who had rated every question on their programme was
 * told "47 competencies not yet rated" and could not move on. Their saved
 * answers also held unrated rows for KSBs no longer on the programme (a changed
 * programme or a re-authored profile), which the step never shows. Only the
 * programme's own KSBs count now; the old rows are kept, not deleted.
 */
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import { ToastProvider } from '@/hooks/useToast';

const fetchExtendedIlr = vi.fn();
const saveExtendedIlr = vi.fn();
vi.mock('@/api/extendedIlr', () => ({
  fetchExtendedIlr: (...args: unknown[]) => fetchExtendedIlr(...args),
  saveExtendedIlr: (...args: unknown[]) => saveExtendedIlr(...args),
  peekExtendedIlr: () => undefined,
}));
vi.mock('@/api/enrolmentDocuments', () => ({ uploadEnrolmentDocument: vi.fn() }));
const fetchKsbProfile = vi.fn();
vi.mock('@/api/curriculum', () => ({
  fetchKsbProfile: (...args: unknown[]) => fetchKsbProfile(...args),
  peekKsbProfile: () => undefined,
}));
vi.mock('@/api/wizardInclusion', () => ({
  fetchInclusionStatus: vi.fn(async () => ({ applicable: true, completed: false, respondedAt: null })),
  SAFEGUARDING_URL: 'https://safeguarding.example/',
}));

import { WizardProvider } from '../WizardContext';
import { useWizard } from "../WizardContext.helpers";
import { WizardShell } from '../WizardShell';
import { BUILTIN_ITEMS } from '../layout/registry';
import type { EnrolmentBoard, KsbAssessment, RagLevel, WizardDraft } from '../../types';

const BOARD = {
  user: { id: '20', name: 'Test Learner', reference: 'REF20', owner: '' },
  contact: { email: '', phone: '', dob: '', groupMembership: '', hasMandate: false },
  programme: { name: 'Marketing Executive Level 4', cohort: '', type: '', status: 'Onboarding', startDate: '', endDate: '', enrolledAt: '', enrolledBy: '', onboardingStatus: 'In progress' },
} as unknown as EnrolmentBoard;

const rated = (ksbId: string, level: RagLevel | null): KsbAssessment => ({ ksbId, level, evidenceFiles: [], actionPlan: null });

/** Both programme KSBs rated, plus two unrated rows for KSBs the programme no longer has. */
const SAVED_ASSESSMENTS: Record<string, KsbAssessment> = {
  k1: rated('k1', 'expert'),
  k2: rated('k2', 'never'),
  'old-1': rated('old-1', null),
  'old-2': rated('old-2', null),
};

let latest: ReturnType<typeof useWizard> | null = null;
function Spy() {
  latest = useWizard();
  return null;
}

function renderWizard() {
  render(
    <ToastProvider>
      <WizardProvider userId="20" board={BOARD}>
        <WizardShell currentIndex={7} mode="learner" onNavigateStep={vi.fn()} onFinish={vi.fn()} />
        <Spy />
      </WizardProvider>
    </ToastProvider>,
  );
}

const skillsRadarMissing = (draft: Partial<WizardDraft>) =>
  BUILTIN_ITEMS['block.skillsRadar'].missing!(draft as WizardDraft, true);

beforeAll(() => {
  (globalThis as Record<string, unknown>).AppIcon = ({ className }: { className?: string }) => <i className={className} />;
});
beforeEach(() => {
  latest = null;
  fetchKsbProfile.mockResolvedValue({ results: [
    { id: 'k1', codes: ['K1'], title: 'Marketing concepts', theme: 'Knowledge', kind: 'Knowledge' },
    { id: 'k2', codes: ['S1'], title: 'Campaigns', theme: 'Skill', kind: 'Skill' },
  ] });
  fetchExtendedIlr.mockResolvedValue({ answers: null, draft: { skillsRadar: { standardId: '', assessments: SAVED_ASSESSMENTS } }, meta: { updatedAt: '' } });
  saveExtendedIlr.mockResolvedValue({ meta: { updatedAt: '2026-10-15T00:00:00Z' } });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe('Skills Radar completeness', () => {
  it('counts only the KSBs on the programme', () => {
    const skillsRadar = { standardId: '', assessments: SAVED_ASSESSMENTS, programmeKsbIds: ['k1', 'k2'] };
    expect(skillsRadarMissing({ skillsRadar })).toEqual([]);
    // A programme KSB with no answer still counts — rated row or not.
    expect(skillsRadarMissing({ skillsRadar: { ...skillsRadar, programmeKsbIds: ['k1', 'k2', 'k3'] } }))
      .toEqual(['1 competency not yet rated']);
    // Until the programme's list is known, every row counts, as before.
    expect(skillsRadarMissing({ skillsRadar: { standardId: '', assessments: SAVED_ASSESSMENTS } }))
      .toEqual(['2 competencies not yet rated']);
  });

  it('lets a learner who rated every question move on, despite rows from an old profile', async () => {
    renderWizard();
    const radar = await screen.findByRole('tab', { name: 'Skills Radar' });
    await waitFor(() => expect(within(radar).getByText('Complete')).toBeInTheDocument());
    expect(latest!.draft.skillsRadar.programmeKsbIds).toEqual(['k1', 'k2']);
    expect(skillsRadarMissing(latest!.draft)).toEqual([]);
  });

  it('still holds a learner back on a programme KSB they have not rated', async () => {
    fetchExtendedIlr.mockResolvedValue({
      answers: null,
      draft: { skillsRadar: { standardId: '', assessments: { ...SAVED_ASSESSMENTS, k2: rated('k2', null) } } },
      meta: { updatedAt: '' },
    });
    renderWizard();
    await waitFor(() => expect(latest!.draft.skillsRadar.programmeKsbIds).toEqual(['k1', 'k2']));
    // One outstanding — not three, which counting the old rows would make it.
    expect(skillsRadarMissing(latest!.draft)).toEqual(['1 competency not yet rated']);
    expect(within(screen.getByRole('tab', { name: 'Skills Radar' })).queryByText('Complete')).toBeNull();
  });

  it('keeps the old rows and never saves the programme list', async () => {
    renderWizard();
    await waitFor(() => expect(latest!.draft.skillsRadar.programmeKsbIds).toEqual(['k1', 'k2']));
    act(() => latest!.setSection('skillsRadar', {
      ...latest!.draft.skillsRadar,
      assessments: { ...latest!.draft.skillsRadar.assessments, k1: rated('k1', 'champion') },
    }));
    await act(() => latest!.saveIlr());
    const [, , , saved] = saveExtendedIlr.mock.calls.at(-1)!;
    expect(saved.skillsRadar).not.toHaveProperty('programmeKsbIds');
    expect(Object.keys(saved.skillsRadar.assessments).sort()).toEqual(['k1', 'k2', 'old-1', 'old-2']);
  });
});
