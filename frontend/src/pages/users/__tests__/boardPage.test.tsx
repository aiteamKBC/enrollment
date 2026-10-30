/**
 * The Enrolment Details board: a header and four tabs showing only what is
 * wired — stored data and working actions. Panels whose data nothing writes any
 * more appear only for learners who have some; the hard-coded and dead ones are
 * gone.
 */
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import type { EnrolmentBoard, FsBlock } from '../types';
import { fetchLearnerCoach, updateLearnerCoach } from '@/api/coach';
import { fetchCoachOptions } from '@/api/staffUsers';

vi.mock('@/components/feature/WorkspaceShell', () => ({ WorkspaceShell: ({ children }: { children: ReactNode }) => children }));
const toast = vi.hoisted(() => ({ info: vi.fn(), success: vi.fn(), error: vi.fn() }));
vi.mock("@/hooks/useToast.helpers", () => ({ useToast: () => toast }));
vi.mock("@/hooks/useAuth.helpers", () => ({ useAuth: () => ({ isAdmin: true, auth: { user: { fullName: 'Staff Member' } } }) }));
vi.mock('../components/LearnerHeaderDates', () => ({ LearnerHeaderDates: () => <div>Learner dates</div> }));
vi.mock('../components/FirstSessionReschedule', () => ({ FirstSessionReschedule: () => null }));

const api = vi.hoisted(() => ({
  fetchEnrolmentBoard: vi.fn(),
  fetchCommercialBoard: vi.fn(),
  updateEnrolmentUser: vi.fn(),
  deleteEnrolmentUser: vi.fn(),
  finishEnrolment: vi.fn(),
  uploadEnrolmentDocument: vi.fn(),
}));
vi.mock('@/api/enrolmentUsers', async (original) => ({
  ...(await original<typeof import('@/api/enrolmentUsers')>()),
  fetchEnrolmentBoard: api.fetchEnrolmentBoard,
  updateEnrolmentUser: api.updateEnrolmentUser,
  deleteEnrolmentUser: api.deleteEnrolmentUser,
  finishEnrolment: api.finishEnrolment,
}));
vi.mock('@/api/commercialUsers', () => ({ fetchCommercialBoard: api.fetchCommercialBoard }));
vi.mock('@/api/enrolmentDocuments', async (original) => ({
  ...(await original<typeof import('@/api/enrolmentDocuments')>()),
  fetchEnrolmentDocuments: vi.fn(async () => []),
  getEnrolmentDocumentUrl: vi.fn(),
  uploadEnrolmentDocument: api.uploadEnrolmentDocument,
}));
vi.mock('@/api/apprenticeshipAgreement', () => ({ fetchAgreement: vi.fn(async () => null), issueAgreement: vi.fn() }));
vi.mock('@/api/ilrDocument', () => ({ fetchIlrDocument: vi.fn(async () => null), issueIlrDocument: vi.fn(), signIlrDocument: vi.fn() }));
vi.mock('@/api/trainingPlanDocument', () => ({ fetchTrainingPlanDocument: vi.fn(async () => null), issueTrainingPlanDocument: vi.fn(), signTrainingPlanDocument: vi.fn() }));
vi.mock('@/api/writtenAgreement', () => ({ fetchWrittenAgreement: vi.fn(async () => null), issueWrittenAgreement: vi.fn(), signWrittenAgreement: vi.fn() }));
const reviews = vi.hoisted(() => ({ fetchOnboardingReviewSlots: vi.fn(), issueOnboardingReview: vi.fn(), fetchReviewForm: vi.fn() }));
vi.mock('@/api/reviewForm', async (original) => ({ ...(await original<typeof import('@/api/reviewForm')>()), ...reviews }));
vi.mock('@/api/coach', () => ({ fetchLearnerCoach: vi.fn(async () => { throw new Error('not active'); }), updateLearnerCoach: vi.fn() }));
vi.mock('@/api/staffUsers', () => ({ fetchCoachOptions: vi.fn(async () => []) }));

import BoardPage from '../BoardPage';

const NO_FS: FsBlock = { assessments: [], exempt: false, evidence: [] };

function makeBoard(overrides: Partial<EnrolmentBoard> = {}): EnrolmentBoard {
  return {
    user: { id: '20', name: 'Sam Example', reference: 'Example Org', owner: 'Case Owner', employer: 'Example Ltd' },
    contact: { email: 'sam@example.test', phone: '07000 000000', dob: '01/01/2000', groupMembership: 'Group A', hasMandate: false, address: '1 Example Street' },
    activity: { aptemUsage: '00:00', daysTillNextReporting: 0, logins: 0, tasksAddedByUser: 0, uncompletedTasks: 0, adviceItemsAccessed: 0, actionPlans: 'No plans created' },
    programme: {
      type: 'Delivery', name: 'Marketing Executive Level 4', cohort: 'Cohort A', status: 'Onboarding',
      startDate: '01/09/2026', endDate: '01/09/2027', enrolledAt: '01/08/2026', enrolledBy: 'Staff Member', onboardingStatus: 'In progress',
    },
    subProgrammes: [],
    aims: [{ aimRef: 'FS', qualification: 'Functional Skills — Maths', startDate: '', endDate: '', exempt: false }],
    previousProgrammes: [],
    functionalSkills: { english: NO_FS, maths: NO_FS, ict: NO_FS },
    managedJobs: [],
    tracker: [],
    milestones: [],
    notes: [{ id: 'n1', text: 'Career progression: Marketing manager in two years', administrator: 'Sam Example', dateTime: '01/08/2026' }],
    courseProgress: [],
    contacts: [{ id: 'c1', name: 'Alex Manager', type: 'Line manager', phone: '', email: 'alex@example.test', role: 'Manager', notes: '' }],
    activities: [
      { id: 'a1', date: '01/08/2026', timeAndStatus: 'Completed', event: 'Signed the ILR' },
      { id: 'a2', date: '02/08/2026', timeAndStatus: 'Submitted', event: 'Uploaded a CV' },
    ],
    complianceDocuments: [],
    reviewDocuments: [],
    documents: [],
    competencies: [{ id: 'k1', name: 'K1 — self-assessed', version: 'expert' }],
    subscription: { startDate: '', endDate: '', status: 'Active' },
    auditTrail: [],
    trainingPlan: [],
    ...overrides,
  } as unknown as EnrolmentBoard;
}

let location = '';
function Where() {
  const loc = useLocation();
  location = `${loc.pathname}${loc.search}`;
  return null;
}

function renderBoard(path = '/users/20') {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/users/:userId" element={<><BoardPage /><Where /></>} />
        <Route path="/users" element={<p>Users directory</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

const tab = (name: RegExp) => screen.getByRole('tab', { name });

beforeAll(() => {
  (globalThis as Record<string, unknown>).AppIcon = ({ className }: { className?: string }) => <i className={className} />;
});
const unsigned = { signature: '', name: '', signedAt: null, signed: false };
const SIGNATURES = { learner: unsigned, admin: unsigned, signable: false };
const notIssued = (type: string, label: string) => ({ type, label, issued: false });

beforeEach(() => {
  reviews.fetchOnboardingReviewSlots.mockResolvedValue({
    programme: 'Marketing Executive Level 4', allIssued: false, allCompleted: false,
    reviews: [
      { type: 'eligibility-review', label: 'Eligibility Review & FS Discussion', issued: true, eventKey: 'review:apprenticeship:20:eligibility-review', started: true, completed: false, sectionsDone: 2, sectionsTotal: 6, signatures: SIGNATURES },
      { type: 'uln-privacy', label: 'ULN Privacy Notice & Learner Acknowledgement', issued: true, issuedAt: '2026-10-15T12:52:00Z', completedAt: '2026-10-15T13:05:00Z', eventKey: 'review:apprenticeship:20:uln-privacy', started: true, completed: true, sectionsDone: 2, sectionsTotal: 2, signatures: SIGNATURES },
      notIssued('workspace', 'RPL And Experience'),
    ],
  });
  api.fetchEnrolmentBoard.mockResolvedValue(makeBoard());
  api.fetchCommercialBoard.mockResolvedValue(makeBoard());
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.restoreAllMocks(); });

describe('the enrolment board', () => {
  it('reports a blocked Review handover after saving the new Coach assignment', async () => {
    vi.mocked(fetchLearnerCoach).mockResolvedValueOnce({ coachName: 'Previous Coach', coachEmail: 'old@example.invalid' });
    vi.mocked(fetchCoachOptions).mockResolvedValueOnce([{ name: 'New Coach', email: 'new@example.invalid' }]);
    vi.mocked(updateLearnerCoach).mockResolvedValueOnce({ coachName: 'New Coach', coachEmail: 'new@example.invalid',
      reviewHandover: { transferred: [], unchanged: [], historical: [], uninitialized: [],
        blocked: [{ reviewId: 'imported-review:synthetic', code: 'booking_handover_required' }] } });
    renderBoard();
    fireEvent.change(await screen.findByRole('combobox', { name: 'Coach' }), { target: { value: 'new@example.invalid' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText(/Coach assignment saved.*Existing meeting bookings need a separate handover/)).toBeVisible();
    expect(screen.getByRole('combobox', { name: 'Coach' })).toHaveValue('new@example.invalid');
  });

  it('opens on the overview with the wired panels only', async () => {
    renderBoard();
    expect(await screen.findByRole('heading', { name: 'Sam Example' })).toBeInTheDocument();
    expect(tab(/Overview/)).toHaveAttribute('aria-selected', 'true');
    for (const name of ['Contact details', 'Programme', 'Contacts', "In the learner's own words", 'Danger zone']) {
      expect(screen.getByRole('region', { name })).toBeInTheDocument();
    }
    expect(screen.getByText('Marketing manager in two years')).toBeInTheDocument();
    // Hard-coded, always-empty or dead: gone.
    for (const gone of [/Activity Summary/i, /Course progress/i, /Audit trail/i, /mandate/i, /add new note/i, /add contact/i, /Previous programmes/i, /Number of logins/i]) {
      expect(screen.queryByText(gone)).not.toBeInTheDocument();
    }
    // Subscription status alone is the learner's record status, not subscription data.
    expect(screen.queryByRole('region', { name: 'Subscription' })).not.toBeInTheDocument();
  });

  it('switches tabs from the keyboard and keeps the tab in the address', async () => {
    renderBoard();
    await screen.findByRole('heading', { name: 'Sam Example' });
    tab(/Overview/).focus();
    fireEvent.keyDown(tab(/Overview/), { key: 'ArrowRight' });
    expect(tab(/Documents/)).toHaveAttribute('aria-selected', 'true');
    expect(tab(/Documents/)).toHaveFocus();
    fireEvent.click(tab(/Skills & progress/));
    expect(location).toBe('/users/20?tab=skills');

    const competencies = screen.getByRole('region', { name: 'Competencies' });
    expect(within(competencies).getByText('K1 — self-assessed')).toBeInTheDocument();
    expect(within(competencies).getByText(/Expert/)).toHaveTextContent('Expert·7');
    expect(screen.getByRole('region', { name: 'Aims and qualifications' })).toBeInTheDocument();
    // Nothing recorded, so not shown.
    for (const name of ['Milestones', 'Functional Skills', 'Placements', 'Onboarding tracker', 'Sub-programmes']) {
      expect(screen.queryByRole('region', { name })).not.toBeInTheDocument();
    }
  });

  it('shows older imported data only for learners who have it', async () => {
    api.fetchEnrolmentBoard.mockResolvedValue(makeBoard({
      milestones: [{ id: 'm1', programme: 'Marketing', description: 'Gateway', date: '01/06/2027', empWksLeft: '4', alwWksLeft: '2', status: 'Due', claimed: 'No' }],
      functionalSkills: { english: NO_FS, maths: { assessments: [], exempt: true, evidence: [] }, ict: NO_FS },
      subscription: { startDate: '01/09/2026', endDate: '01/09/2027', status: 'Active' },
    }));
    renderBoard('/users/20?tab=skills');
    expect(await screen.findByRole('region', { name: 'Milestones' })).toHaveTextContent('Gateway');
    const fs = screen.getByRole('region', { name: 'Functional Skills' });
    expect(within(fs).getByText('Maths')).toBeInTheDocument();
    expect(within(fs).getByText('Exempt')).toBeInTheDocument();
    // Only the subject with data; read-only, no switch to flip.
    expect(within(fs).queryByText('English')).not.toBeInTheDocument();
    expect(within(fs).queryByRole('button')).not.toBeInTheDocument();
    fireEvent.click(tab(/Overview/));
    expect(screen.getByRole('region', { name: 'Subscription' })).toBeInTheDocument();
  });

  it('has no Documents tab for a commercial learner', async () => {
    renderBoard('/users/20?source=commercial');
    await screen.findByRole('heading', { name: 'Sam Example' });
    expect(api.fetchCommercialBoard).toHaveBeenCalledWith('20');
    expect(screen.queryByRole('tab', { name: /Documents/ })).not.toBeInTheDocument();
  });

  it('filters the activity timeline', async () => {
    renderBoard('/users/20?tab=activity');
    await screen.findByRole('region', { name: 'Activity timeline' });
    expect(screen.getByText('2 of 2')).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('Search activities…'), { target: { value: 'cv' } });
    expect(screen.getByText('1 of 2')).toBeInTheDocument();
    expect(screen.getByText('Uploaded a CV')).toBeInTheDocument();
    expect(screen.queryByText('Signed the ILR')).not.toBeInTheDocument();
  });

  it('uploads a signed PDF and stays on the Documents tab while the board reloads', async () => {
    api.uploadEnrolmentDocument.mockResolvedValue({});
    renderBoard('/users/20?tab=documents');
    await screen.findByRole('region', { name: 'Uploaded documents' });
    const file = new File(['%PDF'], 'signed.pdf', { type: 'application/pdf' });
    await act(async () => {
      fireEvent.change(screen.getByLabelText('Signed PDF to upload'), { target: { files: [file] } });
    });
    await waitFor(() => expect(api.uploadEnrolmentDocument).toHaveBeenCalledWith(
      'apprenticeship', '20', 'extended-ilr', file, 'signed.pdf', { signed: true, learnerName: 'Sam Example' },
    ));
    await waitFor(() => expect(api.fetchEnrolmentBoard).toHaveBeenCalledTimes(2));
    expect(tab(/Documents/)).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('region', { name: 'Uploaded documents' })).toBeInTheDocument();
  });

  it('issues an enrolment review from Review documents', async () => {
    reviews.issueOnboardingReview.mockResolvedValue({
      programme: 'Marketing Executive Level 4', allIssued: false, allCompleted: false,
      reviews: [
        { type: 'workspace', label: 'RPL And Experience', issued: true, issuedAt: '2026-10-15T13:32:00Z', eventKey: 'review:apprenticeship:20:workspace', started: false, completed: false, sectionsDone: 0, sectionsTotal: 4, signatures: SIGNATURES },
      ],
    });
    renderBoard('/users/20?tab=documents');
    const panel = await screen.findByRole('region', { name: 'Review documents' });
    expect(await within(panel).findByText('in progress 2/6')).toBeInTheDocument();
    expect(within(panel).getByText('RPL And Experience not issued yet')).toBeInTheDocument();
    // A finished review shows when it was completed (UK time).
    expect(within(panel).getByText('Completed 15 Oct 2026, 14:05')).toBeInTheDocument();
    expect(within(panel).getAllByText(/^Completed /)).toHaveLength(1);
    fireEvent.click(within(panel).getByRole('button', { name: /Issue review/ }));
    await waitFor(() => expect(reviews.issueOnboardingReview).toHaveBeenCalledWith('apprenticeship', '20', 'workspace'));
    expect(await within(panel).findByText('issued')).toBeInTheDocument();
    // When it was issued, in UK time (13:32 UTC is 14:32 BST).
    expect(within(panel).getByText('Issued 15 Oct 2026, 14:32')).toBeInTheDocument();
    expect(within(panel).queryByText('RPL And Experience not issued yet')).not.toBeInTheDocument();
    // Nothing to book: no dates, times or booking controls.
    expect(within(panel).queryByText(/book/i)).not.toBeInTheDocument();
  });

  it('withdraws the learner after confirming', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    api.updateEnrolmentUser.mockResolvedValue({});
    renderBoard();
    fireEvent.click(await screen.findByRole('button', { name: 'Withdraw from programme' }));
    await waitFor(() => expect(api.updateEnrolmentUser).toHaveBeenCalledWith('20', { programmeStatus: 'Withdrawn' }));
    await waitFor(() => expect(api.fetchEnrolmentBoard).toHaveBeenCalledTimes(2));
  });

  it('deletes the account only after confirming, then returns to the directory', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
    api.deleteEnrolmentUser.mockResolvedValue(undefined);
    renderBoard();
    const del = await screen.findByRole('button', { name: 'Delete user account' });
    fireEvent.click(del);
    expect(api.deleteEnrolmentUser).not.toHaveBeenCalled();
    fireEvent.click(del);
    await waitFor(() => expect(api.deleteEnrolmentUser).toHaveBeenCalledWith('20'));
    expect(await screen.findByText('Users directory')).toBeInTheDocument();
    expect(confirm).toHaveBeenCalledTimes(2);
  });
});
