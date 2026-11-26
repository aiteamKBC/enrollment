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
const authState = vi.hoisted(() => ({ isAdmin: true }));
vi.mock("@/hooks/useAuth.helpers", () => ({ useAuth: () => ({ isAdmin: authState.isAdmin, auth: { user: { fullName: 'Staff Member' } } }) }));
vi.mock('../components/LearnerHeaderDates', () => ({ LearnerHeaderDates: () => <div>Learner dates</div> }));
vi.mock('../components/FirstSessionReschedule', () => ({ FirstSessionReschedule: () => null }));

const api = vi.hoisted(() => ({
  fetchEnrolmentBoard: vi.fn(),
  fetchCommercialBoard: vi.fn(),
  updateEnrolmentUser: vi.fn(),
  deleteEnrolmentUser: vi.fn(),
  finishEnrolment: vi.fn(),
  updateFunctionalSkillsStatus: vi.fn(),
  uploadEnrolmentDocument: vi.fn(),
  fetchEvidence: vi.fn(async (_kind?: unknown, _id?: unknown, _opts?: { sectionRef?: string }) => []),
  uploadEvidence: vi.fn(),
  getEvidenceDownloadUrl: vi.fn(),
  deleteEvidence: vi.fn(),
}));
vi.mock('@/api/enrolmentUsers', async (original) => ({
  ...(await original<typeof import('@/api/enrolmentUsers')>()),
  fetchEnrolmentBoard: api.fetchEnrolmentBoard,
  updateEnrolmentUser: api.updateEnrolmentUser,
  deleteEnrolmentUser: api.deleteEnrolmentUser,
  finishEnrolment: api.finishEnrolment,
  updateFunctionalSkillsStatus: api.updateFunctionalSkillsStatus,
}));
vi.mock('@/api/commercialUsers', () => ({ fetchCommercialBoard: api.fetchCommercialBoard }));
vi.mock('@/api/evidence', async (original) => ({
  ...(await original<typeof import('@/api/evidence')>()),
  fetchEvidence: api.fetchEvidence,
  uploadEvidence: api.uploadEvidence,
  getEvidenceDownloadUrl: api.getEvidenceDownloadUrl,
  deleteEvidence: api.deleteEvidence,
}));
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
const reviews = vi.hoisted(() => ({ fetchOnboardingReviewSlots: vi.fn(), prepareOnboardingReview: vi.fn(), issueOnboardingReview: vi.fn(), fetchReviewForm: vi.fn() }));
vi.mock('@/api/reviewForm', async (original) => ({ ...(await original<typeof import('@/api/reviewForm')>()), ...reviews }));
vi.mock('@/api/coach', () => ({ fetchLearnerCoach: vi.fn(async () => { throw new Error('not active'); }), updateLearnerCoach: vi.fn() }));
vi.mock('@/api/staffUsers', () => ({ fetchCaseOwners: vi.fn(async () => ['Case Owner']), fetchCoachOptions: vi.fn(async () => []) }));
const rplApi = vi.hoisted(() => ({ fetch: vi.fn(), save: vi.fn(), generate: vi.fn(), issued: vi.fn(async () => ({ documents: [] })) }));
vi.mock('@/api/suitabilityRplReport', () => ({
  fetchSuitabilityRplReport: rplApi.fetch,
  saveSuitabilityRplReport: rplApi.save,
  generateSuitabilityRplJustification: rplApi.generate,
  fetchSuitabilityRplIssuedDocuments: rplApi.issued,
}));

import BoardPage from '../BoardPage';

const NO_FS: FsBlock = { assessments: [], exempt: false, evidence: [] };

function makeBoard(overrides: Partial<EnrolmentBoard> = {}): EnrolmentBoard {
  return {
    user: { id: '20', name: 'Sam Example', reference: 'Example Org', owner: 'Case Owner', employer: 'Example Ltd' },
    learnerTeam: {
      caseOwner: { name: 'Case Owner', email: 'owner@example.test' },
      skillsCoach: { name: 'Skills Coach', email: 'coach@example.test' },
    },
    employmentDetails: {
      organisation: { name: 'Example Ltd', address: '1 Business Street, London, SW1A 1AA' },
      employerRepresentative: { name: 'Robin Representative', email: 'robin@example.test' },
      lineManager: { name: 'Pat Manager', email: 'pat@example.test', phone: '020 7000 0000' },
    },
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
        <Route path="/users/:userId/reviews/:eventKey" element={<Where />} />
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
  authState.isAdmin = true;
  rplApi.fetch.mockResolvedValue({
    report: null,
    aiEnabled: false,
    history: [],
    sourceVersions: {},
    context: { key: 'synthetic', programmeId: 'ME-L4', programmeName: 'Marketing Executive Level 4', enrolmentReference: 'SYN-20', practicalStartDate: '2026-09-01' },
    evidence: {
      learner: { id: '20', name: 'Sam Example', reference: 'SYN-20', employer: 'Example Ltd', jobTitle: 'Marketing Assistant', responsibilities: [] },
      programme: { id: 'ME-L4', name: 'Marketing Executive Level 4', level: '4', startDate: '2026-09-01' },
      programmeUnderstanding: [{ question: 'Question one', answer: 'Answer one' }, { question: 'Question two', answer: 'Answer two' }],
      programmeSuitability: { status: 'available', responses: [] },
      skillsRadar: { status: 'available', scaleMaximum: 9, catalogueVersion: '1', items: [{ id: 'k1', code: 'K1', kind: 'knowledge', description: 'Synthetic score', level: 'capable', score: 5, moduleKey: 'module-a' }] },
      evidence: { cv: { status: 'provided', experience: 'Synthetic experience', documents: [] }, qualifications: { status: 'provided', items: [{ label: 'Highest qualification', value: 'Level 3 Diploma', source: 'ILR learner details' }], documents: [] }, plr: { status: 'not_available', message: 'Not available - integration not implemented' } },
      trainingPlan: { available: true, source: 'authoritative_training_plan', plannedHours: 100, modules: [{ moduleId: 'module-a', title: 'Saved module', plannedHours: 100, ksbCodes: ['K1'] }], message: '' },
      limitations: [], capturedAt: '2026-11-05T12:00:00Z',
    },
  });
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
  api.fetchEvidence.mockResolvedValue([]);
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.restoreAllMocks(); });

describe('the enrolment board', () => {
  it('shows the active badge only for an active learner and uses the saved enrolment date in the hero', async () => {
    const board = makeBoard();
    api.fetchEnrolmentBoard.mockResolvedValue(makeBoard({ programme: { ...board.programme, status: 'Active', enrolledAt: '18/07/2026' } }));
    renderBoard();
    await screen.findByRole('heading', { name: 'Sam Example' });
    const header = screen.getByRole('region', { name: 'Learner overview' });
    expect(within(header).getByText('Active learner')).toBeInTheDocument();
    expect(within(header).getByText('18/07/2026')).toBeInTheDocument();
    expect(header.querySelector('.ep-hero-artwork')).toHaveAttribute('src', '/images/enrolment-details/enrolment-details-hero.png');
  });

  it('does not infer an active badge or an enrolment date from cohort dates', async () => {
    const board = makeBoard();
    api.fetchEnrolmentBoard.mockResolvedValue(makeBoard({ programme: { ...board.programme, enrolledAt: '' } }));
    renderBoard();
    await screen.findByRole('heading', { name: 'Sam Example' });
    const header = screen.getByRole('region', { name: 'Learner overview' });
    expect(within(header).queryByText('Active learner')).not.toBeInTheDocument();
    const enrolment = within(header).getByText('Enrolment').parentElement;
    expect(enrolment).toHaveTextContent('\u2014');
    expect(enrolment).not.toHaveTextContent(board.programme.startDate);
  });

  it('does not flash a report-evidence error while the initial report request is pending', async () => {
    let resolveReport!: (value: unknown) => void;
    rplApi.fetch.mockReturnValueOnce(new Promise((resolve) => { resolveReport = resolve; }));
    renderBoard('/users/20?tab=suitability-rpl');
    await screen.findByRole('heading', { name: 'Sam Example' });
    expect(screen.getByText(/Loading report evidence/)).toBeVisible();
    expect(screen.queryByText('The report evidence is unavailable.')).not.toBeInTheDocument();
    resolveReport({ report: null, aiEnabled: false, history: [], sourceVersions: {}, context: { key: 'synthetic', programmeId: 'ME-L4', programmeName: 'Marketing Executive Level 4', enrolmentReference: 'SYN-20', practicalStartDate: '2026-09-01' }, evidence: { learner: { id: '20', name: 'Sam Example', reference: 'SYN-20', employer: '', jobTitle: '', responsibilities: [] }, programme: { id: 'ME-L4', name: 'Marketing Executive Level 4', level: '4', startDate: '' }, programmeUnderstanding: [], programmeSuitability: { status: 'available', responses: [] }, skillsRadar: { status: 'available', scaleMaximum: 9, catalogueVersion: '', items: [] }, evidence: { cv: { status: 'not_provided', experience: '', documents: [] }, qualifications: { status: 'not_provided', items: [], documents: [] }, plr: { status: 'not_available', message: 'Not available' } }, trainingPlan: { available: false, source: 'authoritative_training_plan', plannedHours: null, modules: [], message: '' }, limitations: [], capturedAt: '' } });
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Suitability assessment' })).toBeVisible());
    expect(screen.queryByText('The report evidence is unavailable.')).not.toBeInTheDocument();
  });

  it('reports a blocked Review handover after saving the new Coach assignment', async () => {
    vi.mocked(fetchLearnerCoach).mockResolvedValueOnce({ coachName: 'Previous Coach', coachEmail: 'old@example.invalid' });
    vi.mocked(fetchCoachOptions).mockResolvedValueOnce([{ name: 'New Coach', email: 'new@example.invalid' }]);
    vi.mocked(updateLearnerCoach).mockResolvedValueOnce({ coachName: 'New Coach', coachEmail: 'new@example.invalid',
      reviewHandover: { transferred: [], unchanged: [], historical: [], uninitialized: [],
        blocked: [{ reviewId: 'imported-review:synthetic', code: 'booking_handover_required' }] } });
    renderBoard();
    fireEvent.change(await screen.findByRole('combobox', { name: 'Skills Coach' }), { target: { value: 'new@example.invalid' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText(/Coach assignment saved.*Existing meeting bookings need a separate handover/)).toBeVisible();
    expect(screen.getByRole('combobox', { name: 'Skills Coach' })).toHaveValue('new@example.invalid');
    expect(updateLearnerCoach).toHaveBeenCalledWith('20', {
      coachName: 'New Coach', coachEmail: 'new@example.invalid',
    });
    expect(vi.mocked(updateLearnerCoach).mock.calls[0][1]).not.toHaveProperty('caseOwner');
  });

  it('opens on the overview with the wired panels only', async () => {
    renderBoard();
    expect(await screen.findByRole('heading', { name: 'Sam Example' })).toBeInTheDocument();
    expect(tab(/Overview/)).toHaveAttribute('aria-selected', 'true');
    for (const name of ['Contact details', 'Programme', 'Learner Team', 'Employer & Manager', 'Contacts', 'Functional Skills']) {
      expect(screen.getByRole('region', { name })).toBeInTheDocument();
    }
    const header = screen.getByRole('region', { name: 'Learner overview' });
    expect(within(header).getAllByText('Case Owner')).toHaveLength(2);
    expect(within(header).getAllByText('Skills Coach')).toHaveLength(2);
    expect(within(header).queryByText('Employer')).not.toBeInTheDocument();
    expect(within(screen.getByRole('region', { name: 'Learner Team' })).getByRole('combobox', { name: 'Case Owner' })).toHaveValue('Case Owner');
    expect(within(screen.getByRole('region', { name: 'Learner Team' })).getAllByText('Skills Coach').length).toBeGreaterThan(0);
    const employment = screen.getByRole('region', { name: 'Employer & Manager' });
    expect(within(employment).getByText('Example Ltd')).toBeInTheDocument();
    expect(within(employment).getByText('1 Business Street, London, SW1A 1AA')).toBeInTheDocument();
    expect(within(employment).getByText('Robin Representative')).toBeInTheDocument();
    expect(within(employment).getByText('Pat Manager')).toBeInTheDocument();
    expect(within(screen.getByRole('region', { name: 'Contact details' })).queryByText('Employer')).not.toBeInTheDocument();
    expect(screen.getByText('English')).toBeInTheDocument();
    expect(screen.getByText('Maths')).toBeInTheDocument();
    expect(screen.getAllByText('No assessments recorded')).toHaveLength(2);
    // Hard-coded, always-empty or dead: gone.
    for (const gone of [/Activity Summary/i, /Course progress/i, /Audit trail/i, /mandate/i, /add new note/i, /add contact/i, /Previous programmes/i, /Number of logins/i]) {
      expect(screen.queryByText(gone)).not.toBeInTheDocument();
    }
    // Subscription status alone is the learner's record status, not subscription data.
    expect(screen.queryByRole('region', { name: 'Subscription' })).not.toBeInTheDocument();
  });

  it('shows both missing learner-team assignments as Not assigned', async () => {
    api.fetchEnrolmentBoard.mockResolvedValue(makeBoard({ learnerTeam: { caseOwner: null, skillsCoach: null } }));
    renderBoard();
    const header = await screen.findByRole('region', { name: 'Learner overview' });
    expect(within(header).getAllByText('Not assigned')).toHaveLength(2);
    const team = await screen.findByRole('region', { name: 'Learner Team' });
    expect(within(team).getAllByText('Not assigned').length).toBeGreaterThanOrEqual(2);
  });

  it('displays Case Owner and Skills Coach independently in the header', async () => {
    api.fetchEnrolmentBoard.mockResolvedValue(makeBoard({
      learnerTeam: {
        caseOwner: { name: 'Alex Administrator', email: 'alex.admin@example.test' },
        skillsCoach: { name: 'Jamie Coach', email: 'jamie.coach@example.test' },
      },
    }));
    renderBoard();
    const header = await screen.findByRole('region', { name: 'Learner overview' });
    expect(within(header).getByText('Alex Administrator')).toBeInTheDocument();
    expect(within(header).getByText('Jamie Coach')).toBeInTheDocument();
  });

  it('renders explicit empty employment values without inventing employer data', async () => {
    api.fetchEnrolmentBoard.mockResolvedValue(makeBoard({
      user: { id: '20', name: 'Sam Example', reference: 'Example Org', owner: '', employer: 'Legacy Header Employer' },
      employmentDetails: { organisation: null, employerRepresentative: null, lineManager: null },
    }));
    renderBoard();
    const employment = await screen.findByRole('region', { name: 'Employer & Manager' });
    expect(within(employment).getAllByText('Not recorded')).toHaveLength(7);
    expect(screen.queryByText('Legacy Header Employer')).not.toBeInTheDocument();
  });

  it('does not repeat the authoritative manager in Contacts when identity matches safely', async () => {
    api.fetchEnrolmentBoard.mockResolvedValue(makeBoard({
      contacts: [
        { id: 'manager', name: 'Pat Manager', type: 'Line manager', phone: '020 7000 0000', email: 'pat@example.test', role: 'Manager', notes: '' },
        { id: 'kin', name: 'Alex Relative', type: 'Next of kin', phone: '', email: 'alex@example.test', role: 'Sibling', notes: '' },
      ],
    }));
    renderBoard();
    const contacts = await screen.findByRole('region', { name: 'Contacts' });
    expect(within(contacts).queryByText('Pat Manager')).not.toBeInTheDocument();
    expect(within(contacts).getByText('Alex Relative')).toBeInTheDocument();
  });

  it('updates the Case Owner without submitting Skills Coach fields', async () => {
    api.updateEnrolmentUser.mockResolvedValue(makeBoard({
      learnerTeam: {
        caseOwner: { name: 'New Owner', email: 'new-owner@example.test' },
        skillsCoach: { name: 'Skills Coach', email: 'coach@example.test' },
      },
    }));
    renderBoard();
    const team = await screen.findByRole('region', { name: 'Learner Team' });
    fireEvent.change(within(team).getByRole('combobox', { name: 'Case Owner' }), { target: { value: '' } });
    fireEvent.click(within(team).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.updateEnrolmentUser).toHaveBeenCalledWith('20', { caseOwner: '' }));
    expect(api.updateEnrolmentUser.mock.calls[0][1]).not.toHaveProperty('coachName');
  });

  it('renders saved BKSB result levels and assessment dates in the Overview', async () => {
    api.fetchEnrolmentBoard.mockResolvedValue(makeBoard({
      functionalSkills: {
        english: { assessments: [{ id: 'b1', name: 'English', level: 'Level 2', result: 'Pass', date: '2026-11-01', status: 'Completed' }], exempt: false, evidence: [] },
        maths: { assessments: [{ id: 'b2', name: 'Maths', level: 'Level 2', result: 'Pass', date: '2026-11-01', status: 'Completed' }], exempt: false, evidence: [] },
        ict: NO_FS,
      },
    }));
    renderBoard();
    const fs = await screen.findByRole('region', { name: 'Functional Skills' });
    expect(within(fs).getAllByText(/Level 2/)).toHaveLength(2);
    expect(within(fs).getAllByText(/01\/11\/2026/)).toHaveLength(2);
    expect(within(fs).getAllByText(/Completed/)).toHaveLength(2);
    expect(within(fs).queryByText(/2026-11-01/)).not.toBeInTheDocument();
  });

  it('switches tabs from the keyboard and keeps the tab in the address', async () => {
    renderBoard();
    await screen.findByRole('heading', { name: 'Sam Example' });
    tab(/Overview/).focus();
    fireEvent.keyDown(tab(/Overview/), { key: 'ArrowRight' });
    expect(tab(/^Modules$/)).toHaveAttribute('aria-selected', 'true');
    expect(tab(/^Modules$/)).toHaveFocus();
    expect(location).toBe('/users/20?tab=modules');
    fireEvent.keyDown(tab(/^Modules$/), { key: 'ArrowRight' });
    expect(tab(/Suitability & RPL Report/)).toHaveAttribute('aria-selected', 'true');
    expect(tab(/Suitability & RPL Report/)).toHaveFocus();
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

  it('shows the dynamic report tab only to administrators', async () => {
    renderBoard('/users/20?tab=suitability-rpl');
    const heading = await screen.findByRole('heading', { name: 'Suitability & RPL Report' });
    expect(heading).toBeVisible();
    expect(heading).toHaveClass('text-white');
    expect(rplApi.fetch).toHaveBeenCalledWith('apprenticeship', '20', expect.any(AbortSignal));
    expect(screen.queryByText('Learner and programme evidence')).not.toBeInTheDocument();
    expect(screen.getByRole('complementary', { name: 'Report status and version history' })).toBeVisible();
    expect(screen.getByRole('heading', { name: 'Report status' })).toBeVisible();
    expect(screen.getByRole('heading', { name: 'Version history' })).toBeVisible();
    expect(screen.getByText('Signatures available after issue')).toBeVisible();
    expect(screen.getAllByText('Not issued')).toHaveLength(3);
    expect(screen.getByRole('button', { name: 'Preview PDF' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Download draft' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Save draft' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Finalise report' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: /Sign as assessor/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Create amended version/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reload' })).not.toBeInTheDocument();
    expect(screen.queryByText(/Section 06 includes assessor, learner and employer \/ line manager/)).not.toBeInTheDocument();
    expect(screen.getAllByText(/If an AI draft is not acceptable, edit or replace it first/)).toHaveLength(2);
    cleanup();

    authState.isAdmin = false;
    renderBoard('/users/20?tab=suitability-rpl');
    await screen.findByRole('heading', { name: 'Sam Example' });
    expect(screen.queryByRole('tab', { name: /Suitability & RPL Report/ })).not.toBeInTheDocument();
    expect(tab(/Overview/)).toHaveAttribute('aria-selected', 'true');
  });

  it('loads the report with the commercial learner context', async () => {
    renderBoard('/users/20?source=commercial&tab=suitability-rpl');
    expect(await screen.findByRole('heading', { name: 'Suitability & RPL Report' })).toBeVisible();
    expect(rplApi.fetch).toHaveBeenCalledWith('commercial', '20', expect.any(AbortSignal));
  });

  it('shows older imported data only for learners who have it', async () => {
    api.fetchEnrolmentBoard.mockResolvedValue(makeBoard({
      milestones: [{ id: 'm1', programme: 'Marketing', description: 'Gateway', date: '01/06/2027', empWksLeft: '4', alwWksLeft: '2', status: 'Due', claimed: 'No' }],
      functionalSkills: { english: NO_FS, maths: { assessments: [], exempt: true, evidence: [] }, ict: NO_FS },
      subscription: { startDate: '01/09/2026', endDate: '01/09/2027', status: 'Active' },
    }));
    renderBoard('/users/20?tab=skills');
    expect(await screen.findByRole('region', { name: 'Milestones' })).toHaveTextContent('Gateway');
    fireEvent.click(tab(/Overview/));
    const fs = screen.getByRole('region', { name: 'Functional Skills' });
    expect(within(fs).getByText('Maths')).toBeInTheDocument();
    expect(within(fs).getAllByText('Exempt').length).toBeGreaterThan(0);
    expect(within(fs).getByText('English')).toBeInTheDocument();
    expect(within(fs).getAllByRole('combobox', { name: 'Exemption status' })).toHaveLength(2);
    expect(screen.getByRole('region', { name: 'Subscription' })).toBeInTheDocument();
  });

  it('keeps status changes local until Save changes and restores them with Cancel', async () => {
    renderBoard();
    const fs = await screen.findByRole('region', { name: 'Functional Skills' });
    const statuses = within(fs).getAllByRole('combobox', { name: 'Exemption status' });
    fireEvent.change(statuses[0], { target: { value: 'EXEMPT' } });
    expect(api.updateFunctionalSkillsStatus).not.toHaveBeenCalled();
    expect(within(fs).getByRole('button', { name: 'Save changes' })).toBeInTheDocument();
    fireEvent.click(within(fs).getByRole('button', { name: 'Cancel' }));
    expect(statuses[0]).toHaveValue('NOT_EXEMPT');
    expect(within(fs).queryByRole('button', { name: 'Save changes' })).not.toBeInTheDocument();
  });

  it('uploads draft evidence without automatically saving the selected status', async () => {
    const evidence = { id: 'e6', filename: 'maths-certificate.pdf', contentType: 'application/pdf', sizeBytes: 10, status: 'approved', scanResult: 'clean', sectionRef: 'functional-skills:english', uploadedAt: '2026-11-03T12:00:00Z', trainingPlanDetails: null };
    let englishFetches = 0;
    api.fetchEvidence.mockImplementation(async (_kind, _id, opts) => opts?.sectionRef === 'functional-skills:english' && englishFetches++ > 0 ? [evidence] : []);
    api.uploadEvidence.mockResolvedValue({ id: 'e6', status: 'approved', scanResult: 'clean', filename: evidence.filename, sectionRef: evidence.sectionRef });
    renderBoard();
    const fs = await screen.findByRole('region', { name: 'Functional Skills' });
    const status = within(fs).getAllByRole('combobox', { name: 'Exemption status' })[0];
    fireEvent.change(status, { target: { value: 'EXEMPT' } });
    const file = new File(['certificate'], evidence.filename, { type: 'application/pdf' });
    fireEvent.change(within(fs).getByLabelText('Add file'), { target: { files: [file] } });
    await waitFor(() => expect(api.uploadEvidence).toHaveBeenCalledWith('apprenticeship', '20', file, 'functional-skills:english'));
    expect(api.updateFunctionalSkillsStatus).not.toHaveBeenCalled();
    expect(await within(fs).findByText(evidence.filename)).toBeInTheDocument();
    expect(within(fs).getByRole('button', { name: 'Save changes' })).toBeEnabled();
  });

  it('preserves the draft and keeps Save changes available when status saving fails', async () => {
    api.updateFunctionalSkillsStatus.mockRejectedValue(new Error('Status service unavailable'));
    api.fetchEnrolmentBoard.mockResolvedValueOnce(makeBoard({ functionalSkills: { english: { assessments: [], exempt: true, status: 'EXEMPT', evidence: [] }, maths: NO_FS, ict: NO_FS } }));
    renderBoard();
    const fs = await screen.findByRole('region', { name: 'Functional Skills' });
    const status = within(fs).getAllByRole('combobox', { name: 'Exemption status' })[0];
    fireEvent.change(status, { target: { value: 'NOT_EXEMPT' } });
    fireEvent.click(within(fs).getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(within(fs).getByText('Status service unavailable')).toBeInTheDocument());
    expect(status).toHaveValue('NOT_EXEMPT');
    expect(within(fs).getByRole('button', { name: 'Save changes' })).toBeInTheDocument();
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

  it('prepares an enrolment review without issuing it from Review documents', async () => {
    reviews.prepareOnboardingReview.mockResolvedValue({
      programme: 'Marketing Executive Level 4', allIssued: false, allCompleted: false,
      reviews: [
        { type: 'workspace', label: 'RPL And Experience', issued: false, draft: true, eventKey: 'review:apprenticeship:20:workspace', started: false, completed: false, sectionsDone: 0, sectionsTotal: 4, signatures: SIGNATURES },
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
    await waitFor(() => expect(reviews.prepareOnboardingReview).toHaveBeenCalledWith('apprenticeship', '20', 'workspace'));
    expect(reviews.prepareOnboardingReview).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(location).toBe('/users/20/reviews/review%3Aapprenticeship%3A20%3Aworkspace?admin=1'));
    // Nothing to book: no dates, times or booking controls.
    expect(within(panel).queryByText(/book/i)).not.toBeInTheDocument();
  });

  it.each(['Continue', 'View'])('keeps the learner identity when opening an issued review with %s', async label => {
    renderBoard('/users/20?tab=documents');
    const panel = await screen.findByRole('region', { name: 'Review documents' });
    await within(panel).findByText('in progress 2/6');
    fireEvent.click(within(panel).getByRole('button', { name: label }));
    const type = label === 'Continue' ? 'eligibility-review' : 'uln-privacy';
    expect(location).toBe(`/users/20/reviews/${encodeURIComponent(`review:apprenticeship:20:${type}`)}?admin=1`);
  });

  it.each(['eligibility-review', 'workspace', 'training-plan', 'uln-privacy'])(
    'shows exactly one Continue for an unfinished %s draft and preserves its destination', async type => {
      const eventKey = `review:apprenticeship:20:${type}`;
      reviews.fetchOnboardingReviewSlots.mockResolvedValue({
        programme: '', allIssued: false, allCompleted: false,
        reviews: [{ type, label: 'Selected Review', issued: false, draft: true, completed: false, eventKey }],
      });
      renderBoard('/users/20?tab=documents');
      const panel = await screen.findByRole('region', { name: 'Review documents' });
      await within(panel).findByText('Selected Review in preparation');
      const actions = within(panel).getAllByRole('button', { name: 'Continue' });
      expect(actions).toHaveLength(1);
      expect(within(panel).queryByRole('button', { name: /Issue review/i })).not.toBeInTheDocument();
      fireEvent.click(actions[0]);
      expect(location).toBe(`/users/20/reviews/${encodeURIComponent(eventKey)}?admin=1`);
      expect(reviews.prepareOnboardingReview).not.toHaveBeenCalled();
      expect(reviews.issueOnboardingReview).not.toHaveBeenCalled();
    },
  );

  it('keeps Continue and Issue Review as separate actions for a completed draft', async () => {
    reviews.fetchOnboardingReviewSlots.mockResolvedValue({
      programme: '', allIssued: false, allCompleted: false,
      reviews: [{ type: 'workspace', label: 'Selected Review', issued: false, draft: true, completed: true, eventKey: 'ready-review' }],
    });
    renderBoard('/users/20?tab=documents');
    const panel = await screen.findByRole('region', { name: 'Review documents' });
    await within(panel).findByText('Selected Review ready to issue');
    expect(within(panel).getAllByRole('button', { name: 'Continue' })).toHaveLength(1);
    expect(within(panel).getByRole('button', { name: 'Issue Review' })).toBeInTheDocument();
  });

  it('shows the same live LMS assignment on the learner profile', async () => {
    const board = makeBoard();
    board.programme.homeCohort = { status: 'resolved', source: 'lms', programmeId: 'ME_L4',
      cohortId: 'lms-home', name: 'Live cohort', groupId: 'g1', groupName: 'Live group', layoutVersion: null,
      lmsModules: [{ id: 'lms-m1', title: 'Live module', sessions: 16, start: null, end: null }] };
    api.fetchEnrolmentBoard.mockResolvedValue(board);
    renderBoard('/users/20');
    await screen.findByRole('heading', { name: 'Sam Example' });
    expect(screen.getAllByRole('tab').map(t => t.textContent?.replace(/\d+$/, '').trim())).toEqual([
      'Overview', 'Modules', 'Suitability & RPL Report', 'Documents', 'Skills & progress', 'Activity',
    ]);
    expect(screen.queryByRole('region', { name: 'Assigned LMS modules' })).not.toBeInTheDocument();
    fireEvent.click(tab(/^Modules$/));
    const panel = await screen.findByRole('region', { name: 'Assigned LMS modules' });
    expect(screen.getAllByRole('region', { name: 'Assigned LMS modules' })).toHaveLength(1);
    expect(location).toBe('/users/20?tab=modules');
    expect(within(panel).getByText('Live module')).toBeVisible();
    expect(within(panel).getByText('Cohort: Live cohort · Group: Live group')).toBeVisible();
    expect(api.fetchEnrolmentBoard).toHaveBeenCalledTimes(1);
    fireEvent.click(tab(/^Overview$/));
    expect(screen.queryByRole('region', { name: 'Assigned LMS modules' })).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Contact details' })).toBeVisible();
  });

  it.each(['ME_L4', 'MM_L6', 'APM_L4', 'PCP_L6'] as const)('opens Modules directly using only the current learner\'s returned %s assignment', async programmeId => {
    const board = makeBoard();
    board.programme.homeCohort = { status: 'resolved', source: 'lms', programmeId,
      cohortId: 'saved-home', name: 'Saved cohort', groupId: 'saved-group', groupName: 'Assigned group', layoutVersion: null,
      lmsModules: [{ id: 'confirmed-offering', title: 'Confirmed learner module', weeks: 16, sessions: 99,
        groupName: 'Assigned group', start: '2027-02-01', end: '2027-06-20' }] };
    api.fetchEnrolmentBoard.mockResolvedValue(board);
    renderBoard('/users/20?tab=modules');
    const panel = await screen.findByRole('region', { name: 'Assigned LMS modules' });
    expect(tab(/^Modules$/)).toHaveAttribute('aria-selected', 'true');
    expect(panel).toHaveTextContent('Confirmed learner module');
    expect(panel).toHaveTextContent('Cohort: Saved cohort · Group: Assigned group');
    expect(within(panel).getAllByRole('columnheader').map(h => h.textContent)).toEqual(['Module', 'Group', 'Duration (weeks)', 'Start', 'End']);
    expect(within(panel).getByText('16')).toBeVisible();
    expect(within(panel).getByText('2027-02-01')).toBeVisible();
    expect(within(panel).getByText('2027-06-20')).toBeVisible();
    expect(api.fetchEnrolmentBoard).toHaveBeenCalledWith('20');
    expect(within(panel).queryByRole('button')).toBeNull();
  });

  it.each(['empty', 'unavailable'])('preserves the existing %s module state in the new tab', async state => {
    const board = makeBoard();
    board.programme.homeCohort = { status: state === 'empty' ? 'resolved' : 'pending', source: 'lms', programmeId: 'ME_L4',
      cohortId: 'saved-home', name: 'Saved cohort', groupId: 'g1', groupName: 'Group 1', layoutVersion: null,
      reason: state === 'unavailable' ? 'lms_unavailable' : undefined, lmsModules: [] };
    api.fetchEnrolmentBoard.mockResolvedValue(board);
    renderBoard('/users/20?tab=modules');
    const panel = await screen.findByRole('region', { name: 'Assigned LMS modules' });
    expect(panel).toHaveTextContent(state === 'empty' ? 'No LMS modules are available' : 'LMS data is temporarily unavailable');
    expect(within(panel).queryByRole('table')).toBeNull();
  });

  it('does not render the removed destructive actions', async () => {
    renderBoard();
    await screen.findByRole('heading', { name: 'Sam Example' });
    expect(screen.queryByRole('button', { name: 'Withdraw from programme' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Delete user account' })).not.toBeInTheDocument();
  });

  it('treats an empty Functional Skills evidence response as an empty state', async () => {
    renderBoard();
    const fs = await screen.findByRole('region', { name: 'Functional Skills' });
    expect(await within(fs).findAllByText('No evidence uploaded')).toHaveLength(2);
    expect(within(fs).queryByText('Could not load evidence.')).not.toBeInTheDocument();
    expect(within(fs).queryByText('+ Add file')).not.toBeInTheDocument();
  });

  it('loads English and Maths evidence independently', async () => {
    const english = { id: 'e3', filename: 'english.pdf', contentType: 'application/pdf', sizeBytes: 10, status: 'approved', scanResult: 'clean', sectionRef: 'functional-skills:english', uploadedAt: '2026-11-01T12:00:00Z', trainingPlanDetails: null };
    const maths = { id: 'e4', filename: 'maths.pdf', contentType: 'application/pdf', sizeBytes: 10, status: 'approved', scanResult: 'clean', sectionRef: 'functional-skills:maths', uploadedAt: '2026-11-02T12:00:00Z', trainingPlanDetails: null };
    api.fetchEvidence.mockImplementation(async (_kind, _id, opts) => opts?.sectionRef === 'functional-skills:english' ? [english] : [maths]);
    renderBoard();
    const fs = await screen.findByRole('region', { name: 'Functional Skills' });
    expect(await within(fs).findByText('english.pdf')).toBeInTheDocument();
    expect(await within(fs).findByText('maths.pdf')).toBeInTheDocument();
    expect(api.fetchEvidence).toHaveBeenCalledWith('apprenticeship', '20', { sectionRef: 'functional-skills:english' });
    expect(api.fetchEvidence).toHaveBeenCalledWith('apprenticeship', '20', { sectionRef: 'functional-skills:maths' });
  });

  it('keeps existing evidence visible when a status changes to Not Exempt', async () => {
    const english = { id: 'e5', filename: 'certificate.pdf', contentType: 'application/pdf', sizeBytes: 10, status: 'approved', scanResult: 'clean', sectionRef: 'functional-skills:english', uploadedAt: '2026-11-01T12:00:00Z', trainingPlanDetails: null };
    api.fetchEvidence.mockImplementation(async (_kind, _id, opts) => opts?.sectionRef === 'functional-skills:english' ? [english] : []);
    api.fetchEnrolmentBoard.mockResolvedValueOnce(makeBoard({ functionalSkills: { english: { assessments: [], exempt: true, status: 'EXEMPT', evidence: [] }, maths: NO_FS, ict: NO_FS } }));
    api.updateFunctionalSkillsStatus.mockResolvedValue({});
    renderBoard();
    const fs = await screen.findByRole('region', { name: 'Functional Skills' });
    await within(fs).findByText('certificate.pdf');
    const statuses = within(fs).getAllByRole('combobox', { name: 'Exemption status' });
    fireEvent.change(statuses[0], { target: { value: 'NOT_EXEMPT' } });
    expect(api.updateFunctionalSkillsStatus).not.toHaveBeenCalled();
    fireEvent.click(within(fs).getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(api.updateFunctionalSkillsStatus).toHaveBeenCalledWith('20', 'english', 'NOT_EXEMPT', []));
    expect(within(fs).getByText('certificate.pdf')).toBeInTheDocument();
    expect(within(fs).getByText('No evidence uploaded')).toBeInTheDocument();
    expect(within(fs).getByText('Status saved.')).toBeInTheDocument();
    expect(api.fetchEnrolmentBoard).toHaveBeenCalledTimes(1);
  });

  it('confirms and refreshes after deleting evidence under Not Exempt', async () => {
    const evidence = { id: 'e1', filename: 'maths.pdf', contentType: 'application/pdf', sizeBytes: 10, status: 'approved', scanResult: 'clean', sectionRef: 'functional-skills:maths', uploadedAt: '2026-11-01T12:00:00Z', trainingPlanDetails: null };
    let deleted = false;
    api.fetchEvidence.mockImplementation(async (_kind, _id, opts) => opts?.sectionRef === 'functional-skills:maths' && !deleted ? [evidence] : []);
    api.deleteEvidence.mockImplementation(async () => { deleted = true; });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderBoard();
    const fs = await screen.findByRole('region', { name: 'Functional Skills' });
    const del = (await within(fs).findAllByRole('button', { name: 'Delete maths.pdf' }))[0];
    fireEvent.click(del);
    await waitFor(() => expect(api.deleteEvidence).toHaveBeenCalledWith('apprenticeship', '20', 'e1'));
    expect(window.confirm).toHaveBeenCalledWith('Delete maths.pdf?');
    await waitFor(() => expect(within(fs).queryByText('maths.pdf')).not.toBeInTheDocument());
  });

  it('keeps the file visible when evidence deletion fails', async () => {
    const evidence = { id: 'e7', filename: 'keep-me.pdf', contentType: 'application/pdf', sizeBytes: 10, status: 'approved', scanResult: 'clean', sectionRef: 'functional-skills:maths', uploadedAt: '2026-11-01T12:00:00Z', trainingPlanDetails: null };
    api.fetchEvidence.mockImplementation(async (_kind, _id, opts) => opts?.sectionRef === 'functional-skills:maths' ? [evidence] : []);
    api.deleteEvidence.mockRejectedValue(new Error('Delete failed'));
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderBoard();
    const fs = await screen.findByRole('region', { name: 'Functional Skills' });
    fireEvent.click((await within(fs).findAllByRole('button', { name: 'Delete keep-me.pdf' }))[0]);
    await waitFor(() => expect(within(fs).getByText('Delete failed')).toBeInTheDocument());
    expect(within(fs).getByText('keep-me.pdf')).toBeInTheDocument();
  });

  it('prevents deleting the last required evidence file', async () => {
    const evidence = { id: 'e2', filename: 'english.pdf', contentType: 'application/pdf', sizeBytes: 10, status: 'approved', scanResult: 'clean', sectionRef: 'functional-skills:english', uploadedAt: '2026-11-01T12:00:00Z', trainingPlanDetails: null };
    api.fetchEvidence.mockResolvedValue([evidence]);
    api.fetchEnrolmentBoard.mockResolvedValueOnce(makeBoard({ functionalSkills: { english: { assessments: [], exempt: true, status: 'EXEMPT', evidence: [] }, maths: NO_FS, ict: NO_FS } }));
    renderBoard('/users/20');
    const fs = await screen.findByRole('region', { name: 'Functional Skills' });
    const del = (await within(fs).findAllByRole('button', { name: 'Delete english.pdf' }))[0];
    fireEvent.click(del);
    expect(api.deleteEvidence).not.toHaveBeenCalled();
    expect(within(fs).getByText(/Change the exemption status/)).toBeInTheDocument();
  });
});
