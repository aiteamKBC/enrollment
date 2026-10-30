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
const updateEnrolmentUser = vi.fn();
const fetchProgrammes = vi.fn();
const fetchCohorts = vi.fn();
const fetchGroups = vi.fn();
const fetchFreeProgrammeModules = vi.fn();

vi.mock('@/api/learningPlan', async () => {
  const actual = await vi.importActual<typeof import('@/api/learningPlan')>('@/api/learningPlan');
  return { ...actual, fetchLearningPlan, saveLearningPlan };
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
