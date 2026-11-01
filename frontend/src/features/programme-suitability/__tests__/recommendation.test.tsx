import { beforeAll, beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RecommendationResult } from '../RecommendationResult';
import { AssessmentDialog } from '../AssessmentDialog';
import { configuration, fresh, result } from './fixtures';
import type { AssessmentState, ProgrammeId } from '../types';

vi.mock('../api', () => ({ saveAssessment: vi.fn() }));
import { saveAssessment } from '../api';

const stateFor = (id: ProgrammeId): AssessmentState => {
  const programme = configuration.programmes.find((p) => p.id === id)!;
  const other = configuration.programmes.find((p) => p.pathway === programme.pathway && p.id !== id)!;
  return { ...fresh(), pathway: programme.pathway, revision: 2, confirmedAt: '2026-10-31T12:00:00Z', completionStatus: 'SUBMITTED',
    selectedProgramme: id, programmeSelectionSource: 'SYSTEM_RECOMMENDATION',
    allowedSelections: programme.level === 6 ? [id, other.id] : [id],
    result: { ...result, outcome: `RECOMMENDED_${id}`, recommendedProgramme: id, scores: { [id]: 83.17, [other.id]: 52.96 } as typeof result.scores,
      strongestEvidence: [{ questionId: 'saved-question', title: 'Actual responsibilities', weight: 10, answered: true, points: { [id]: 10 },
        selectedAnswers: [{ id: 'saved-answer', text: 'I lead the strategic responsibilities recorded in my assessment.' }] }] } };
};

beforeAll(() => { HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); }; });
const confirmed = (state: AssessmentState): AssessmentState => ({ ...state,
  selectionStatus: 'SELECTION_CONFIRMED', selectionConfirmedAt: '2026-11-01T12:00:00Z' });
beforeEach(() => { vi.clearAllMocks(); vi.mocked(saveAssessment).mockImplementation(async (_kind, _id, state) => confirmed(state)); });

function expectNoLearnerScoring() {
  expect(screen.queryByText(/Responsibility Matching Score/i)).not.toBeInTheDocument();
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  expect(screen.queryByText(/83\.17|52\.96|80\.85/)).not.toBeInTheDocument();
  expect(screen.queryByText('Preliminary responsibility matching only; not an eligibility or admissions decision.')).not.toBeInTheDocument();
}

it.each(['ME_L4', 'MM_L6', 'APM_L4', 'PCP_L6'] as ProgrammeId[])('shows the %s recommendation without learner scoring or disclaimer', (id) => {
  const state = stateFor(id); const programme = configuration.programmes.find((p) => p.id === id)!;
  render(<RecommendationResult state={state} />);
  expect(screen.getByRole('heading', { name: `${programme.title} — Level ${programme.level}` })).toBeInTheDocument();
  expectNoLearnerScoring();
  expect(screen.queryByText(/52\.96/)).not.toBeInTheDocument();
  expect(screen.queryByRole('radio')).not.toBeInTheDocument();
  expect(screen.queryByText('Relevant responsibility')).not.toBeInTheDocument();
  expect(screen.queryByText('I lead the strategic responsibilities recorded in my assessment.')).not.toBeInTheDocument();
  expect(screen.getByText('Based on your answers and current job responsibilities.')).toBeInTheDocument();
});

it.each(['ME_L4', 'APM_L4'] as ProgrammeId[])('%s displays its higher-level alternative as a disabled option', async (id) => {
  const selected = vi.fn(); const state = stateFor(id);
  const high = configuration.programmes.find((p) => p.pathway === state.pathway && p.level === 6)!;
  render(<RecommendationResult mode="confirmation" state={state} onSelect={selected} />);
  const button = screen.getByRole('button', { name: `${high.title} L6 — Not Available` });
  expect(button).toBeDisabled(); await userEvent.click(button); expect(selected).not.toHaveBeenCalled();
  expect(screen.queryByRole('button', { name: 'Request Admissions Review' })).not.toBeInTheDocument();
});

it.each([['MM_L6', 'ME_L4'], ['PCP_L6', 'APM_L4']] as [ProgrammeId, ProgrammeId][])('%s permits only its Level 4 alternative and retains the original recommendation', async (high, low) => {
  const state = stateFor(high), select = vi.fn();
  const lowProgramme = configuration.programmes.find((p) => p.id === low)!;
  const { rerender } = render(<RecommendationResult mode="confirmation" state={state} onSelect={select} />);
  await userEvent.click(screen.getByRole('button', { name: `Choose ${lowProgramme.title} L4` }));
  expect(select).toHaveBeenCalledWith(low);
  rerender(<RecommendationResult mode="confirmation" state={{ ...state, selectedProgramme: low, programmeSelectionSource: 'LEARNER_ALTERNATIVE' }} onSelect={select} />);
  expect(screen.getByRole('status')).toHaveTextContent(`${lowProgramme.title} Level 4`);
  expect(screen.queryByText(/^You chose /)).not.toBeInTheDocument();
  expectNoLearnerScoring();
  expect(screen.getByRole('button', { name: `Choose ${lowProgramme.title} L4` })).toBeEnabled();
});

it('handles zero evidence with a concise non-blocking message and no large review card', () => {
  const state = stateFor('MM_L6'); state.selectedProgramme = '';
  state.result = { ...state.result!, outcome: 'INSUFFICIENT_INFORMATION', recommendedProgramme: null, scores: { ME_L4: 0, MM_L6: 0 } as typeof result.scores };
  render(<RecommendationResult mode="confirmation" state={state} onSelect={vi.fn()} />);
  expect(screen.getByText(/not enough responsibility evidence/)).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'Admissions Review Required' })).not.toBeInTheDocument();
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});

it('keeps the MM recommendation and allowed alternative without exposing scores or internal review diagnostics', () => {
  const state = stateFor('MM_L6');
  state.result = { ...state.result!, scores: { ME_L4: 52.96, MM_L6: 80.85 } as typeof result.scores,
    admissionsReviewOutcome: 'FURTHER_REVIEW_REQUIRED', admissionsFlags: ['CONTRADICTORY_RESPONSIBILITIES', 'MISSING_CRITICAL_LEVEL_6_EVIDENCE'] };
  render(<RecommendationResult mode="confirmation" state={state} onSelect={vi.fn()} />);
  expectNoLearnerScoring();
  expect(screen.queryByText(/52\.96/)).not.toBeInTheDocument();
  expect(screen.queryByText('Admissions Review Required')).not.toBeInTheDocument();
  expect(screen.queryByText(/required distinct evidence|mixed responsibilities/)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Choose Marketing Executive L4' })).toBeEnabled();
});

it('uses configured programme titles rather than hardcoded learner display names', () => {
  const state = stateFor('ME_L4'); state.configuration = structuredClone(state.configuration!);
  state.configuration.programmes.find((p) => p.id === 'ME_L4')!.title = 'Configured Executive Title';
  render(<RecommendationResult state={state} />);
  expect(screen.getByRole('heading', { name: 'Configured Executive Title — Level 4' })).toBeInTheDocument();
});

it('reopens a confirmed alternative in read-only review without selection controls', async () => {
  const state = confirmed({ ...stateFor('MM_L6'), selectedProgramme: 'ME_L4' as const, programmeSelectionSource: 'LEARNER_ALTERNATIVE' });
  const continued = vi.fn();
  render(<AssessmentDialog initial={state} kind="apprenticeship" learnerId="41" onClose={vi.fn()} onConfirmed={continued} />);
  expect(screen.getByRole('heading', { name: 'Your Recommended Programme' })).toBeInTheDocument();
  expect(screen.getByText('You chose Marketing Executive Level 4 instead of Marketing Manager Level 6.')).toBeInTheDocument();
  expect(continued).not.toHaveBeenCalled();
  expect(screen.queryByRole('button', { name: 'Continue to Next Step' })).not.toBeInTheDocument();
  expect(screen.queryByText(/Interested in/)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Show Answers' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Close assessment' })).toBeInTheDocument();
  expect(saveAssessment).not.toHaveBeenCalled();
});

it('submits a zero-evidence assessment, shows its concise result and continues without assigning a programme', async () => {
  const pending = { ...stateFor('MM_L6'), selectedProgramme: '' as const, result: { ...result, outcome: 'INSUFFICIENT_INFORMATION', recommendedProgramme: null,
    scores: { ME_L4: 0, MM_L6: 0 } as typeof result.scores, reasons: ['NO_RESPONSIBILITY_EVIDENCE'] } };
  const initial = { ...pending, confirmedAt: null, completionStatus: 'IN_PROGRESS' as const, responses: { marketing: [] } };
  vi.mocked(saveAssessment).mockResolvedValueOnce(pending).mockResolvedValueOnce(confirmed(pending));
  const continued = vi.fn();
  render(<AssessmentDialog initial={initial} kind="apprenticeship" learnerId="41" onClose={vi.fn()} onConfirmed={continued} />);
  await userEvent.click(screen.getByRole('button', { name: 'Submit Assessment' }));
  expect(await screen.findByText(/not enough responsibility evidence/)).toBeInTheDocument();
  expect(continued).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button', { name: 'Continue to Next Step' }));
  await waitFor(() => expect(continued).toHaveBeenCalledWith(expect.objectContaining({ selectedProgramme: '', completionStatus: 'SUBMITTED' })));
});

it('saves the Level 4 alternative and advances only after persistence succeeds', async () => {
  const state = stateFor('MM_L6'); const continued = vi.fn();
  let resolve: (saved: AssessmentState) => void = () => {};
  vi.mocked(saveAssessment).mockImplementation(() => new Promise((done) => { resolve = done; }));
  render(<AssessmentDialog initial={state} kind="apprenticeship" learnerId="41" onClose={vi.fn()} onConfirmed={continued} />);
  await userEvent.click(screen.getByRole('button', { name: 'Choose Marketing Executive L4' }));
  expect(continued).not.toHaveBeenCalled();
  expect(saveAssessment).not.toHaveBeenCalled();
  const reason = 'My current role focuses on practical delivery.';
  await userEvent.type(screen.getByLabelText('Please explain your reason for choosing this programme.'), reason);
  const submit = screen.getByRole('button', { name: 'Submit Reason & Continue' });
  fireEvent.click(submit); fireEvent.click(submit);
  expect(saveAssessment).toHaveBeenCalledTimes(1);
  expect(continued).not.toHaveBeenCalled();
  expect(saveAssessment).toHaveBeenCalledWith('apprenticeship', '41', state, { action: 'select', selectedProgramme: 'ME_L4', alternativeSelectionReason: reason });
  resolve(confirmed({ ...state, selectedProgramme: 'ME_L4', alternativeSelectionReason: reason, programmeSelectionSource: 'LEARNER_ALTERNATIVE' }));
  await waitFor(() => expect(continued).toHaveBeenCalledWith(expect.objectContaining({ selectedProgramme: 'ME_L4', result: state.result })));
});

it('keeps the result open if saving the alternative fails, then advances after Retry succeeds', async () => {
  const state = stateFor('PCP_L6'); const continued = vi.fn();
  const reason = 'I prefer project delivery in my current role.';
  vi.mocked(saveAssessment).mockRejectedValueOnce(new Error('Could not save the programme selection.'))
    .mockResolvedValueOnce(confirmed({ ...state, selectedProgramme: 'APM_L4', alternativeSelectionReason: reason, programmeSelectionSource: 'LEARNER_ALTERNATIVE' }));
  render(<AssessmentDialog initial={state} kind="apprenticeship" learnerId="41" onClose={vi.fn()} onConfirmed={continued} />);
  await userEvent.click(screen.getByRole('button', { name: 'Choose Associate Project Manager L4' }));
  await userEvent.type(screen.getByLabelText('Please explain your reason for choosing this programme.'), reason);
  await userEvent.click(screen.getByRole('button', { name: 'Submit Reason & Continue' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not save');
  expect(continued).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await waitFor(() => expect(continued).toHaveBeenCalledWith(expect.objectContaining({ selectedProgramme: 'APM_L4' })));
  expect(saveAssessment).toHaveBeenLastCalledWith('apprenticeship', '41', state, expect.objectContaining({ alternativeSelectionReason: reason }));
});

it.each(['ME_L4', 'MM_L6', 'APM_L4', 'PCP_L6'] as ProgrammeId[])('keeps %s read-only after a refreshed confirmed result', async (id) => {
  const state = confirmed(stateFor(id));
  const { unmount } = render(<RecommendationResult mode="confirmation" state={state} onSelect={vi.fn()} />);
  expect(screen.queryByText(/Interested in/)).not.toBeInTheDocument();
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
  expect(screen.queryByText(/^You chose /)).not.toBeInTheDocument();
  expect(screen.queryByText('Your Reason')).not.toBeInTheDocument();
  expectNoLearnerScoring();
  unmount();
  render(<AssessmentDialog initial={structuredClone(state)} kind="apprenticeship" learnerId="41" onClose={vi.fn()} onConfirmed={vi.fn()} />);
  expectNoLearnerScoring();
  expect(screen.queryByRole('button', { name: /Continue|Choose|Edit|Restart|Submit/ })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Show Answers' }));
  expect(screen.getByText(`Pathway: ${state.pathway === 'marketing' ? 'Marketing' : 'Project Controls'}`)).toBeInTheDocument();
  const question = state.configuration!.questions.find((q) => q.pathway === state.pathway)!;
  expect(screen.getByText(question.title)).toBeInTheDocument();
  expect(screen.getByText(question.description)).toBeInTheDocument();
  for (const answer of question.answers) expect(screen.getByLabelText(answer.text)).toBeDisabled();
  await userEvent.click(screen.getByRole('button', { name: 'Back to Result' }));
  expect(screen.getByRole('heading', { name: 'Your Recommended Programme' })).toBeInTheDocument();
  expectNoLearnerScoring();
  expect(screen.queryByRole('button', { name: 'Continue to Next Step' })).not.toBeInTheDocument();
  expect(saveAssessment).not.toHaveBeenCalled();
});

it('shows the exact saved options and selections with no way to edit before confirmation', async () => {
  const state = stateFor('MM_L6');
  state.configuration = structuredClone(state.configuration!);
  const question = state.configuration.questions.find((q) => q.pathway === 'marketing')!;
  question.title = 'Original submitted question'; question.description = 'Original submitted question text';
  question.answers[0].text = 'Original first option';
  state.responses = { marketing: ['marketing_A', 'marketing_D'] };
  render(<AssessmentDialog initial={state} kind="apprenticeship" learnerId="41" onClose={vi.fn()} onConfirmed={vi.fn()} />);
  expect(screen.queryByRole('button', { name: 'Edit Answers' })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Show Answers' }));
  expect(screen.getByText('Original submitted question')).toBeInTheDocument();
  expect(screen.getByText('Original submitted question text')).toBeInTheDocument();
  const first = screen.getByLabelText('Original first option');
  expect(first).toBeChecked(); expect(first).toBeDisabled();
  await userEvent.click(first); expect(first).toBeChecked();
  expect(screen.getByLabelText('marketing answer B')).not.toBeChecked();
  expect(screen.getByLabelText('marketing answer D')).toBeChecked();
  expect(screen.queryByRole('button', { name: /Submit|Restart|Back to pathways/ })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Back to Result' }));
  expect(screen.getByRole('button', { name: 'Continue to Next Step' })).toBeInTheDocument();
  expect(saveAssessment).not.toHaveBeenCalled();
});

it('requires a persisted confirmation rather than just a submitted response before navigating', async () => {
  const state = stateFor('MM_L6'), continued = vi.fn();
  vi.mocked(saveAssessment).mockResolvedValue(state);
  render(<AssessmentDialog initial={state} kind="apprenticeship" learnerId="41" onClose={vi.fn()} onConfirmed={continued} />);
  await userEvent.click(screen.getByRole('button', { name: 'Continue to Next Step' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('not confirmed your programme selection');
  expect(continued).not.toHaveBeenCalled();
});

it.each(['ME_L4', 'MM_L6', 'APM_L4', 'PCP_L6'] as ProgrammeId[])('never renders alternatives in the default summary for pending %s', (id) => {
  render(<RecommendationResult state={stateFor(id)} />);
  expect(screen.queryByText(/Interested in/)).not.toBeInTheDocument();
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
  expectNoLearnerScoring();
});

it('does not offer programme selection for an unsubmitted result even in confirmation mode', () => {
  render(<RecommendationResult mode="confirmation" state={{ ...stateFor('MM_L6'), completionStatus: 'IN_PROGRESS', confirmedAt: null }} onSelect={vi.fn()} />);
  expect(screen.queryByText(/Interested in/)).not.toBeInTheDocument();
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});

it.each([['MM_L6', 'ME_L4'], ['PCP_L6', 'APM_L4']] as [ProgrammeId, ProgrammeId][])('shows the confirmed %s alternative choice inside the original purple recommendation card', (high, low) => {
  const state = confirmed({ ...stateFor(high), selectedProgramme: low });
  const recommended = configuration.programmes.find((p) => p.id === high)!;
  const selected = configuration.programmes.find((p) => p.id === low)!;
  render(<RecommendationResult state={state} />);
  const heading = screen.getByRole('heading', { name: `${recommended.title} — Level ${recommended.level}` });
  const message = screen.getByText(`You chose ${selected.title} Level ${selected.level} instead of ${recommended.title} Level ${recommended.level}.`);
  expect(heading.parentElement).toContainElement(message);
  expect(message).toHaveClass('font-semibold', 'text-base');
  expectNoLearnerScoring();
  expect(screen.queryByText(/Selected programme:/)).not.toBeInTheDocument();
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});

it('does not describe a saved preference as an alternative when there is no recommendation', () => {
  const state = confirmed({ ...stateFor('MM_L6'), selectedProgramme: 'ME_L4' });
  state.result = { ...state.result!, outcome: 'INSUFFICIENT_INFORMATION', recommendedProgramme: null };
  render(<RecommendationResult state={state} />);
  expect(screen.queryByText(/^You chose /)).not.toBeInTheDocument();
});

it.each([['MM_L6', 'ME_L4'], ['PCP_L6', 'APM_L4']] as [ProgrammeId, ProgrammeId][])('opens the mandatory reason dialog for %s and cancels without saving %s', async (high, low) => {
  const state = stateFor(high), continued = vi.fn(), close = vi.fn();
  const recommended = configuration.programmes.find((p) => p.id === high)!;
  const selected = configuration.programmes.find((p) => p.id === low)!;
  render(<AssessmentDialog initial={state} kind="apprenticeship" learnerId="41" onClose={close} onConfirmed={continued} />);
  await userEvent.click(screen.getByRole('button', { name: `Choose ${selected.title} L4` }));
  expect(screen.getByRole('dialog', { name: 'Reason for Choosing an Alternative Programme' })).toBeInTheDocument();
  expect(screen.getByText(`Why would you prefer ${selected.title} Level ${selected.level} instead of the recommended ${recommended.title} Level ${recommended.level}?`)).toBeInTheDocument();
  expect(screen.getByRole('textbox')).toHaveAttribute('maxlength', '1000');
  await userEvent.type(screen.getByRole('textbox'), 'An unsaved explanation');
  await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(screen.getByRole('button', { name: 'Continue to Next Step' })).toBeInTheDocument();
  expectNoLearnerScoring();
  expect(saveAssessment).not.toHaveBeenCalled(); expect(continued).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button', { name: `Choose ${selected.title} L4` }));
  fireEvent(screen.getByRole('dialog'), new Event('cancel', { cancelable: true }));
  expect(screen.getByRole('button', { name: 'Continue to Next Step' })).toBeInTheDocument();
  expect(close).not.toHaveBeenCalled(); expect(saveAssessment).not.toHaveBeenCalled();
});

it.each(['', ' \n\t ', 'x'.repeat(1001)])('refuses an invalid alternative reason without saving or confirming (length %s)', async (reason) => {
  const continued = vi.fn();
  render(<AssessmentDialog initial={stateFor('MM_L6')} kind="apprenticeship" learnerId="41" onClose={vi.fn()} onConfirmed={continued} />);
  await userEvent.click(screen.getByRole('button', { name: 'Choose Marketing Executive L4' }));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: reason } });
  await userEvent.click(screen.getByRole('button', { name: 'Submit Reason & Continue' }));
  expect(screen.getByRole('alert')).toHaveTextContent('Please explain your reason');
  expect(saveAssessment).not.toHaveBeenCalled(); expect(continued).not.toHaveBeenCalled();
});

it('renders a saved multiline reason safely as text inside the original recommendation card', () => {
  const reason = '  I prefer practical delivery.\n<script>alert("unsafe")</script><img src=x onerror=alert(1)>  ';
  const state = confirmed({ ...stateFor('PCP_L6'), selectedProgramme: 'APM_L4', alternativeSelectionReason: reason });
  const { container } = render(<RecommendationResult state={state} />);
  expect(screen.getByText('Your Confirmed Programme Selection')).toBeInTheDocument();
  expect(screen.getByText('Your Reason')).toBeInTheDocument();
  expect(screen.getByText((_, node) => node?.tagName === 'P' && node.textContent === reason)).toHaveClass('whitespace-pre-wrap');
  expect(container.querySelector('script, img')).toBeNull();
});

it('does not invent a reason for a legacy confirmed alternative', () => {
  render(<RecommendationResult state={confirmed({ ...stateFor('MM_L6'), selectedProgramme: 'ME_L4' })} />);
  expect(screen.getByText(/^You chose /)).toBeInTheDocument();
  expect(screen.queryByText('Your Reason')).not.toBeInTheDocument();
});

it('does not show a reason when the learner keeps the recommended programme', () => {
  render(<RecommendationResult state={confirmed({ ...stateFor('MM_L6'), alternativeSelectionReason: 'Old unrelated reason' })} />);
  expect(screen.queryByText(/^You chose /)).not.toBeInTheDocument();
  expect(screen.queryByText('Your Reason')).not.toBeInTheDocument();
  expect(screen.queryByText('Old unrelated reason')).not.toBeInTheDocument();
});

it.each(['ME_L4', 'MM_L6', 'APM_L4', 'PCP_L6'] as ProgrammeId[])('hides score and disclaimer in the first submitted result popup for %s', async (id) => {
  const submitted = stateFor(id);
  const programme = configuration.programmes.find((p) => p.id === id)!;
  const initial = { ...submitted, completionStatus: 'IN_PROGRESS' as const, confirmedAt: null,
    responses: { [programme.pathway]: [`${programme.pathway}_D`] } };
  vi.mocked(saveAssessment).mockResolvedValue(submitted);
  render(<AssessmentDialog initial={initial} kind="apprenticeship" learnerId="41" onClose={vi.fn()} onConfirmed={vi.fn()} />);
  await userEvent.click(screen.getByRole('button', { name: 'Submit Assessment' }));
  expect(await screen.findByRole('heading', { name: `${programme.title} — Level ${programme.level}` })).toBeInTheDocument();
  expect(screen.getByText('Based on your answers and current job responsibilities.')).toBeInTheDocument();
  expectNoLearnerScoring();
  expect(screen.getByRole('button', { name: 'Continue to Next Step' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Show Answers' })).toBeInTheDocument();
  expect(submitted.result!.scores[id]).toBe(83.17);
});
