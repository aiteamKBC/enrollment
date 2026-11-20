import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// ---------------------------------------------------------------------------
// A learning plan is built from the modules a group is taught, so a learner with
// no programme, cohort or group has nothing to plan. These cover the gate: an
// unplaced learner is asked where they sit first, and what that answer is used
// for — the learner's record, and then the plan reloaded around it.
// ---------------------------------------------------------------------------

const fetchLearningPlan = vi.fn();
const saveLearningPlan = vi.fn();
const saveConfirmedProgrammePlan = vi.fn();
const updateEnrolmentUser = vi.fn();
const fetchProgrammes = vi.fn();
const fetchCohorts = vi.fn();
const fetchGroups = vi.fn();
const fetchFreeProgrammeModules = vi.fn();

vi.mock('@/api/learningPlan', async () => {
  const actual = await vi.importActual<typeof import('@/api/learningPlan')>('@/api/learningPlan');
  return { ...actual, fetchLearningPlan, saveLearningPlan, saveConfirmedProgrammePlan };
});
vi.mock('@/api/enrolmentUsers', () => ({ updateEnrolmentUser }));
vi.mock('@/api/curriculum', () => ({ fetchProgrammes, fetchCohorts, fetchGroups }));
vi.mock('@/lib/curriculumApi', async () => {
  const actual = await vi.importActual<typeof import('@/lib/curriculumApi')>('@/lib/curriculumApi');
  return { ...actual, fetchFreeProgrammeModules };
});
vi.mock("@/hooks/useToast.helpers", () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }),
}));
// The real Modal portals and traps focus; neither is what these assert.
vi.mock('../Modal', () => ({
  Modal: ({ children, footer }: { children: React.ReactNode; footer?: React.ReactNode }) => (
    <div>{children}{footer}</div>
  ),
}));

const { LearningPlanModal } = await import('../LearningPlanModal');

const planResponse = (learner: Record<string, string>, plan: unknown[] = []) => ({
  learner: { id: '91', name: 'Aya Aya Test', programmeId: '', ...learner },
  plan,
  preset: [],
  available: [],
  saved: false,
  totals: { moduleCount: plan.length, totalHours: 0 },
});

const UNPLACED = { programme: '', cohort: '', group: '' };
const PLACED = { programme: 'Final Test', cohort: 'Final Cohort', group: 'Aya Group' };

/**
 * Choose all three, waiting for each list to arrive first. Each one is fetched
 * when the one above it is picked, so selecting without waiting races the
 * request that fills the options.
 */
async function choosePlacement(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole('option', { name: 'Final Test' });
  await user.selectOptions(screen.getByLabelText(/Programme/), 'Final Test');
  await screen.findByRole('option', { name: 'Final Cohort' });
  await user.selectOptions(screen.getByLabelText(/Cohort/), 'Final Cohort');
  await screen.findByRole('option', { name: 'Aya Group' });
  await user.selectOptions(screen.getByLabelText(/Group/), 'Aya Group');
}

describe('learning plan placement step', () => {
  beforeEach(() => {
    // reset, not clear: one case queues two `mockResolvedValueOnce` responses,
    // and clearAllMocks leaves an unconsumed queue behind for the next case —
    // which then read a placed learner and found no chooser.
    vi.resetAllMocks();
    fetchProgrammes.mockResolvedValue(['Final Test', 'MBA']);
    fetchCohorts.mockResolvedValue(['Final Cohort']);
    fetchGroups.mockResolvedValue(['Aya Group', 'Final Group']);
    fetchFreeProgrammeModules.mockResolvedValue([]);
  });

  it('asks an unplaced learner for a programme, cohort and group', async () => {
    fetchLearningPlan.mockResolvedValue(planResponse(UNPLACED));

    render(<LearningPlanModal learnerId="91" learnerName="Aya Aya Test" onClose={vi.fn()} />);

    expect(await screen.findByText(/is not on a programme yet/)).toBeTruthy();
    expect(screen.getByLabelText(/Programme/)).toBeTruthy();
    expect(screen.getByLabelText(/Cohort/)).toBeTruthy();
    expect(screen.getByLabelText(/Group/)).toBeTruthy();
    // The plan itself is not offered until there is something to plan against.
    expect(screen.queryByText('Add a module')).toBeNull();
  });

  it('goes straight to the plan for a learner who is already placed', async () => {
    fetchLearningPlan.mockResolvedValue(planResponse(PLACED));

    render(<LearningPlanModal learnerId="91" learnerName="Aya Aya Test" onClose={vi.fn()} />);

    await waitFor(() => expect(screen.queryByText(/is not on a programme yet/)).toBeNull());
    expect(screen.getByText('Add a module')).toBeTruthy();
  });

  it('shows confirmed fixed academic modules while cohort/group scheduling is pending', async () => {
    fetchLearningPlan.mockResolvedValue({ ...planResponse({ programme: 'Marketing Executive Level 4', cohort: '', group: '' }, [{
      moduleId: 'academic:ME_L4:marketing-impact', academicModuleId: 'marketing-impact',
      moduleTitle: 'Marketing Impact and Planning', programmeId: 'PROG-ME-L4',
      programmeName: 'Marketing Executive Level 4', groupName: '', hours: 0,
      startDate: '', endDate: '', assignmentSource: 'programme_suitability_fixed', schedulingStatus: 'pending',
    }]), saved: true });
    render(<LearningPlanModal learnerId="91" learnerName="Aya Aya Test" onClose={vi.fn()} readOnly />);
    expect(await screen.findByText('Marketing Impact and Planning')).toBeVisible();
    expect(screen.getByText('Schedule pending')).toBeVisible();
    expect(screen.queryByText(/is not on a programme yet/)).toBeNull();
    expect(screen.queryByText('Not in catalogue')).toBeNull();
    expect(fetchProgrammes).not.toHaveBeenCalled();
    expect(fetchGroups).not.toHaveBeenCalled();
    expect(saveLearningPlan).not.toHaveBeenCalled();
  });

  it('shows persisted PCP academic modules without a legacy learner group or catalogue', async () => {
    fetchLearningPlan.mockResolvedValue({ ...planResponse({ programme: 'Project Controls Professional Level 6', cohort: 'Feb 2027', group: '' }, [{
      moduleId: 'home-g1-portfolio', academicModuleId: 'portfolio', lmsOfferingId: 'home-g1-portfolio',
      moduleTitle: 'Portfolio Management (APMG)', programmeId: 'PROG-PCP-L6',
      programmeName: 'Project Controls Professional Level 6', groupName: 'Group 1', hours: 105.75,
      startDate: '2027-02-01', endDate: '2027-06-20', assignmentSource: 'programme_suitability_pcp', schedulingStatus: 'scheduled',
    }]), saved: true });
    render(<LearningPlanModal learnerId="91" learnerName="PCP fixture" onClose={vi.fn()} readOnly />);
    expect(await screen.findByText('Portfolio Management (APMG)')).toBeVisible();
    expect(screen.getByText('Feb 2027')).toBeVisible();
    expect(screen.getAllByText('105h 45m')[0]).toBeVisible();
    expect(screen.queryByText(/is not on a programme yet/)).toBeNull();
    expect(screen.queryByText('Not in catalogue')).toBeNull();
    expect(fetchGroups).not.toHaveBeenCalled();
    expect(saveLearningPlan).not.toHaveBeenCalled();
  });

  it('saves the prepared programme plan in view-only mode without editing placement', async () => {
    const user = userEvent.setup();
    const response = { ...planResponse(PLACED, [{ moduleId: 'fixed-impact', academicModuleId: 'marketing-impact', moduleTitle: 'Marketing Impact and Planning', groupName: 'Group 1', hours: 138, assignmentSource: 'programme_suitability_fixed' }]),
      prefillSource: 'confirmed-programme', confirmedPlanPreviewToken: 'snapshot-token' };
    fetchLearningPlan.mockResolvedValue(response);
    saveConfirmedProgrammePlan.mockResolvedValue({ ...response, saved: true, confirmedPlanPreviewToken: null });
    const onSaved = vi.fn();
    const onClose = vi.fn();
    render(<LearningPlanModal learnerId="91" learnerName="vv bb" readOnly onSaved={onSaved} onClose={onClose} />);
    await user.click(await screen.findByRole('button', { name: 'Save learning plan' }));
    await waitFor(() => expect(saveConfirmedProgrammePlan).toHaveBeenCalledWith('91', 'snapshot-token'));
    expect(await screen.findByText('Saved', { exact: true })).toBeVisible();
    expect(screen.queryByText(/not saved yet/)).toBeNull();
    expect(screen.queryByRole('button', { name: 'Save learning plan' })).toBeNull();
    expect(updateEnrolmentUser).not.toHaveBeenCalled();
    expect(saveLearningPlan).not.toHaveBeenCalled();
    expect(onSaved).toHaveBeenCalledOnce();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('shows a safe conflict if the prepared plan changed before saving', async () => {
    const user = userEvent.setup();
    fetchLearningPlan.mockResolvedValue({ ...planResponse(PLACED, [{ moduleId: 'fixed-impact', moduleTitle: 'Marketing Impact and Planning', hours: 138 }]), prefillSource: 'confirmed-programme', confirmedPlanPreviewToken: 'snapshot-token' });
    saveConfirmedProgrammePlan.mockRejectedValue(new Error('The prepared modules changed. Close and reopen the Learning Plan before saving.'));
    render(<LearningPlanModal learnerId="91" learnerName="vv bb" readOnly onClose={vi.fn()} />);
    await user.click(await screen.findByRole('button', { name: 'Save learning plan' }));
    expect(await screen.findByText(/The prepared modules changed/)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Save learning plan' })).toBeEnabled();
    expect(saveLearningPlan).not.toHaveBeenCalled();
  });

  it('identifies previously confirmed programme modules instead of the legacy group preset', async () => {
    fetchLearningPlan.mockResolvedValue({ ...planResponse(PLACED, [
      { moduleId: 'lms-impact', academicModuleId: 'marketing-impact', moduleTitle: 'Marketing Impact and Planning', groupName: 'Group 1', hours: 0, assignmentSource: 'programme_suitability_fixed' },
      { moduleId: 'lms-social', academicModuleId: 'social-media', moduleTitle: 'Social Media', groupName: 'Group 1', hours: 0, assignmentSource: 'programme_suitability_fixed' },
      { moduleId: 'lms-martech', academicModuleId: 'martech', moduleTitle: 'Martech', groupName: 'Group 1', hours: 0, assignmentSource: 'programme_suitability_fixed' },
    ]), saved: false, prefillSource: 'confirmed-programme' });
    render(<LearningPlanModal learnerId="91" learnerName="Aya Aya Test" onClose={vi.fn()} readOnly />);
    expect(await screen.findByText('Marketing Impact and Planning')).toBeVisible();
    expect(screen.getByText('Social Media')).toBeVisible();
    expect(screen.getByText('Martech')).toBeVisible();
    expect(screen.getByText('Confirmed programme modules — not saved yet')).toBeVisible();
    expect(screen.queryByText('Pre-filled from group — not saved yet')).toBeNull();
    expect(screen.queryByText('Impact& Planning')).toBeNull();
    expect(saveLearningPlan).not.toHaveBeenCalled();
  });

  it('shows exact LMS module hours, fractional minutes and their total', async () => {
    fetchLearningPlan.mockResolvedValue({ ...planResponse(PLACED, [
      { moduleId: 'impact', moduleTitle: 'Marketing Impact and Planning', hours: 138, hoursPending: false },
      { moduleId: 'social', moduleTitle: 'Social Media', hours: 103, hoursPending: false },
      { moduleId: 'martech', moduleTitle: 'Martech', hours: 105.75, hoursPending: false },
    ]), saved: false, prefillSource: 'confirmed-programme' });
    render(<LearningPlanModal learnerId="91" learnerName="Aya Aya Test" onClose={vi.fn()} readOnly />);
    expect(await screen.findByText('138h')).toBeVisible();
    expect(screen.getByText('103h')).toBeVisible();
    expect(screen.getByText('105h 45m')).toBeVisible();
    expect(screen.getAllByText('346h 45m').length).toBeGreaterThan(0);
    expect(screen.queryByText('0h')).toBeNull();
  });

  it('shows projected programme reviews in the existing Meetings table without adding their hours twice', async () => {
    fetchLearningPlan.mockResolvedValue({ ...planResponse(PLACED, [
      { moduleId: 'saved', moduleTitle: 'Confirmed module', hours: 138 },
    ]), saved: true, meetings: [
      { reviewTemplateId: 'coaching', name: 'Monthly Coaching Meeting', hoursEach: 2.5, occurrences: 2,
        hours: 5, recurrenceLabel: 'Every 1 month', startDate: '2027-02-01', endDate: '2027-03-01',
        plannedDates: ['2027-02-01', '2027-03-01'], scheduleKind: 'projected' },
      { reviewTemplateId: 'support', name: 'Personal Support Plan', hoursEach: 0, occurrences: 2,
        hours: 0, recurrenceLabel: 'Every 1 month', startDate: '2027-02-01', endDate: '2027-03-01', scheduleKind: 'projected' },
    ] });
    render(<LearningPlanModal learnerId="91" learnerName="Aya Aya Test" onClose={vi.fn()} readOnly />);
    expect(await screen.findByText('Monthly Coaching Meeting')).toBeVisible();
    expect(screen.getByText('Personal Support Plan')).toBeVisible();
    expect(screen.getByText(/these are not booked appointments/)).toBeVisible();
    expect(screen.getAllByText('143h').length).toBeGreaterThan(0);
    expect(saveLearningPlan).not.toHaveBeenCalled();
  });

  it('shows exact review source permission blockers without hiding the learner modules', async () => {
    fetchLearningPlan.mockResolvedValue({ ...planResponse(PLACED, [
      { moduleId: 'saved', moduleTitle: 'Confirmed module', hours: 138 },
    ]), saved: true, meetings: [], meetingsReason: 'unavailable', meetingsBlockers: [
      { code: 'select_denied', message: 'LMS curriculum.review_templates: select denied. Columns: name.' },
    ] });
    render(<LearningPlanModal learnerId="91" learnerName="Aya Aya Test" onClose={vi.fn()} readOnly />);
    expect(await screen.findByText('Confirmed module')).toBeVisible();
    expect(screen.getByRole('status')).toHaveTextContent('curriculum.review_templates: select denied');
  });

  it('shows a verified replacement in a saved plan without requiring another save', async () => {
    fetchLearningPlan.mockResolvedValue({ ...planResponse(PLACED, [
      { moduleId: 'new-social', moduleTitle: 'Social Media', hours: 110.5, replacementStatus: 'resolved',
        assignmentSource: 'programme_suitability_fixed',
        replacesLmsOfferingId: 'old-social', replacementNotice: 'Using the replacement LMS module in the same home cohort and group.' },
    ]), saved: true, assignmentMode: 'explicit' });
    render(<LearningPlanModal learnerId="91" learnerName="Aya Aya Test" onClose={vi.fn()} readOnly />);
    expect(await screen.findByText('Social Media')).toBeVisible();
    expect(screen.getByText('Using the replacement LMS module in the same home cohort and group.')).toBeVisible();
    expect(screen.getByText('Saved')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Save learning plan' })).toBeNull();
    expect(saveLearningPlan).not.toHaveBeenCalled();
    expect(saveConfirmedProgrammePlan).not.toHaveBeenCalled();
  });

  it('marks unreadable module hours pending rather than claiming zero hours', async () => {
    fetchLearningPlan.mockResolvedValue(planResponse(PLACED, [
      { moduleId: 'pending', moduleTitle: 'Pending module', hours: 0, hoursPending: true },
    ]));
    render(<LearningPlanModal learnerId="91" learnerName="Aya Aya Test" onClose={vi.fn()} readOnly />);
    expect(await screen.findByText('Pending')).toBeVisible();
    expect(screen.getAllByText('0h + pending hours').length).toBeGreaterThan(0);
  });

  it('cannot be saved until all three are chosen', async () => {
    fetchLearningPlan.mockResolvedValue(planResponse(UNPLACED));
    const user = userEvent.setup();

    render(<LearningPlanModal learnerId="91" learnerName="Aya Aya Test" onClose={vi.fn()} />);
    await screen.findByText(/is not on a programme yet/);

    const save = screen.getByRole('button', { name: 'Save and continue' });
    expect(save.hasAttribute('disabled')).toBe(true);

    await screen.findByRole('option', { name: 'Final Test' });
    await user.selectOptions(screen.getByLabelText(/Programme/), 'Final Test');
    await screen.findByRole('option', { name: 'Final Cohort' });
    await user.selectOptions(screen.getByLabelText(/Cohort/), 'Final Cohort');
    // Two of three is still not a placement.
    expect(save.hasAttribute('disabled')).toBe(true);

    await screen.findByRole('option', { name: 'Aya Group' });
    await user.selectOptions(screen.getByLabelText(/Group/), 'Aya Group');
    expect(save.hasAttribute('disabled')).toBe(false);
  });

  it('saves the placement to the learner and reopens on their plan', async () => {
    fetchLearningPlan
      .mockResolvedValueOnce(planResponse(UNPLACED))
      .mockResolvedValueOnce(planResponse(PLACED));
    updateEnrolmentUser.mockResolvedValue({});
    const onSaved = vi.fn();
    const user = userEvent.setup();

    render(<LearningPlanModal learnerId="91" learnerName="Aya Aya Test" onClose={vi.fn()} onSaved={onSaved} />);
    await screen.findByText(/is not on a programme yet/);

    await choosePlacement(user);
    await user.click(screen.getByRole('button', { name: 'Save and continue' }));

    await waitFor(() => expect(updateEnrolmentUser).toHaveBeenCalledWith('91', {
      programme: 'Final Test',
      cohort: 'Final Cohort',
      group: 'Aya Group',
    }));
    // Re-read, so the plan opens on the group's preset rather than nothing.
    await waitFor(() => expect(fetchLearningPlan).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('Add a module')).toBeTruthy();
    // The directory lists programme and group in their own columns.
    expect(onSaved).toHaveBeenCalled();
  });

  it('shows a loading state for the free-course picker, then its options', async () => {
    // A placed learner goes straight to the plan, where the free-course picker
    // lives. Hold the catalogue fetch open so the loading state is observable.
    fetchLearningPlan.mockResolvedValue(planResponse(PLACED));
    let resolveFree: (value: unknown[]) => void = () => {};
    fetchFreeProgrammeModules.mockReturnValue(new Promise((resolve) => { resolveFree = resolve; }));

    render(<LearningPlanModal learnerId="91" learnerName="Aya Aya Test" onClose={vi.fn()} />);

    // While the fetch is in flight the dropdown says so, rather than reading as
    // "no courses" (the bug this fixes).
    expect(await screen.findByRole('option', { name: 'Loading free courses…' })).toBeTruthy();
    expect(screen.queryByRole('option', { name: 'First Aid' })).toBeNull();

    // Once it resolves, the option appears in place — no reopen needed.
    resolveFree([
      { id: 'w1', courseId: 'FREECOURSE-1', courseName: 'First Aid', title: 'wk', description: '', displayOrder: 0, componentCount: 0, totalOtjh: 0, components: [] },
    ] as unknown[]);
    expect(await screen.findByRole('option', { name: 'First Aid' })).toBeTruthy();
    expect(screen.queryByRole('option', { name: 'Loading free courses…' })).toBeNull();
  });

  it('does not offer the chooser to a reader who cannot edit', async () => {
    fetchLearningPlan.mockResolvedValue(planResponse(UNPLACED));

    render(<LearningPlanModal learnerId="91" learnerName="Aya Aya Test" onClose={vi.fn()} readOnly />);

    expect(await screen.findByText(/has to be set on their record/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Save and continue' })).toBeNull();
    expect(fetchProgrammes).not.toHaveBeenCalled();
  });
});
