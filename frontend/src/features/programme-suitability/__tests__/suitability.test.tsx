import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createRef, useState } from 'react';
import type { AssessmentState, Configuration, ProgrammeId } from '../types';
import { configuration, result, fresh } from './fixtures';

vi.mock('../api', () => ({ fetchDefaults: vi.fn(), previewAssessment: vi.fn(), getAssessment: vi.fn(), saveAssessment: vi.fn(), saveReview: vi.fn(), peekAssessment: vi.fn(), primeAssessment: vi.fn(), prefetchAssessmentSecurity: vi.fn() }));
const wizard = vi.hoisted(() => ({ saveIlr: vi.fn(), readOnly: false, cvJob: {} as Record<string, unknown> }));
vi.mock('@/pages/users/wizard/WizardContext.helpers', async () => {
  const { useSuitability } = await import('../useSuitability');
  return { useWizard: () => ({ userId: '41', isCommercial: false, saveIlr: wizard.saveIlr,
    readOnly: wizard.readOnly, draft: { cvJob: wizard.cvJob }, layout: { programmeSuitability: {} },
    suitability: useSuitability('apprenticeship', '41', true, 3),
  }) };
});

import * as api from '../api';
import { AssessmentDialog } from '../AssessmentDialog';
import { SuitabilityAdmin } from '../SuitabilityAdmin';
import { CvSuitabilityPanel } from '../CvSuitabilityPanel';
import { SuitabilityGate, type SuitabilityContinuation } from '../SuitabilityGate';
import { resolveLayout } from '@/pages/users/wizard/layout/resolve';
import { addStep, moveStep, setText, updateStep } from '@/pages/users/wizard-builder/builderOps';


beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
});
beforeEach(() => {
  vi.clearAllMocks(); wizard.readOnly = false; wizard.saveIlr.mockResolvedValue(undefined);
  vi.mocked(api.prefetchAssessmentSecurity).mockResolvedValue({ csrfToken: 'synthetic' });
  vi.mocked(api.getAssessment).mockResolvedValue(fresh());
  vi.mocked(api.fetchDefaults).mockResolvedValue({ configuration: structuredClone(configuration) });
  vi.mocked(api.previewAssessment).mockResolvedValue(result);
  vi.mocked(api.saveAssessment).mockImplementation(async (_kind, _id, state, changes) => {
    const confirmation = { selectionStatus: 'SELECTION_CONFIRMED' as const, selectionConfirmedAt: '2026-11-01T12:00:00Z' };
    if (changes.action === 'continue') return { ...state, ...confirmation, selectedProgramme: state.result?.recommendedProgramme ?? '' };
    const submit = changes.action === 'submit' || changes.confirm;
    return { ...state, ...changes, pathway: changes.pathway ?? state.pathway, revision: state.revision + 1,
      result: changes.action === 'select' ? state.result : result,
      ...(submit ? { confirmedAt: '2026-10-31T12:00:00Z', completionStatus: 'SUBMITTED' as const,
        selectedProgramme: result.recommendedProgramme!, programmeSelectionSource: 'SYSTEM_RECOMMENDATION', reviewStatus: 'PENDING_ADMISSIONS_REVIEW' } : {}),
      ...(changes.action === 'select' ? { programmeSelectionSource: 'LEARNER_ALTERNATIVE', ...confirmation } : {}),
    };
  });
});

describe('learner suitability journey', () => {
  it.each(['Marketing', 'Project Controls'])('loads the selected %s pathway and persists multiple answers', async (label) => {
    render(<AssessmentDialog initial={fresh()} kind="apprenticeship" learnerId="41" onClose={vi.fn()} onConfirmed={vi.fn()} />);
    await userEvent.click(screen.getByRole('button', { name: label }));
    const pathway = label === 'Marketing' ? 'marketing' : 'project-controls';
    await userEvent.click(await screen.findByLabelText(`${pathway} answer A`));
    await waitFor(() => expect(screen.getByLabelText(`${pathway} answer A`)).toBeChecked());
    await userEvent.click(screen.getByLabelText(`${pathway} answer B`));
    await waitFor(() => expect(screen.getByLabelText(`${pathway} answer B`)).toBeChecked());
    expect(api.saveAssessment).toHaveBeenLastCalledWith('apprenticeship', '41', expect.anything(), expect.objectContaining({
      responses: { [pathway]: [`${pathway}_A`, `${pathway}_B`] },
    }));
    expect(screen.queryByLabelText(pathway === 'marketing' ? 'project-controls answer A' : 'marketing answer A')).not.toBeInTheDocument();
  });

  it('validates missing questions and accepts an explicit none-apply response', async () => {
    render(<AssessmentDialog initial={{ ...fresh(), pathway: 'marketing' }} kind="apprenticeship" learnerId="41" onClose={vi.fn()} onConfirmed={vi.fn()} />);
    await userEvent.click(screen.getByRole('button', { name: 'Submit Assessment' }));
    expect(screen.getByText('Select an answer or choose none apply.')).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText('None of these responsibilities applies to me.'));
    await waitFor(() => expect(api.saveAssessment).toHaveBeenLastCalledWith('apprenticeship', '41', expect.anything(), expect.objectContaining({ responses: { marketing: [] } }))); 
  });

  it('restores saved answers after refresh and when returning from comparison', async () => {
    const state = { ...fresh(), pathway: 'marketing' as const, responses: { marketing: ['marketing_D'] }, result };
    render(<AssessmentDialog initial={state} kind="apprenticeship" learnerId="41" onClose={vi.fn()} onConfirmed={vi.fn()} />);
    expect(screen.getByLabelText('marketing answer D')).toBeChecked();
    await userEvent.click(screen.getByRole('button', { name: 'Submit Assessment' }));
    expect(screen.queryByRole('button', { name: 'Edit Answers' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Show Answers' }));
    expect(screen.getByLabelText('marketing answer D')).toBeChecked();
    expect(screen.getByLabelText('marketing answer D')).toBeDisabled();
  });

  it('shows the submitted result first and advances after an allowed L4 alternative is saved', async () => {
    const continued = vi.fn();
    render(<AssessmentDialog initial={{ ...fresh(), pathway: 'marketing', responses: { marketing: ['marketing_D'] }, result }}
      kind="apprenticeship" learnerId="41" onClose={vi.fn()} onConfirmed={continued} />);
    await userEvent.click(screen.getByRole('button', { name: 'Submit Assessment' }));
    expect(await screen.findByRole('heading', { name: 'Your Recommended Programme' })).toBeInTheDocument();
    expect(continued).not.toHaveBeenCalled();
    expect(screen.queryByRole('radio')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Choose Marketing Executive L4' }));
    expect(api.saveAssessment).toHaveBeenCalledTimes(1);
    await userEvent.type(screen.getByLabelText('Please explain your reason for choosing this programme.'), 'This matches my current delivery role.');
    await userEvent.click(screen.getByRole('button', { name: 'Submit Reason & Continue' }));
    expect(await screen.findByText('You chose Marketing Executive Level 4 instead of Marketing Manager Level 6.')).toBeInTheDocument();
    await waitFor(() => expect(continued).toHaveBeenCalledWith(expect.objectContaining({ selectedProgramme: 'ME_L4',
      result: expect.objectContaining({ recommendedProgramme: 'MM_L6' }), completionStatus: 'SUBMITTED' })));
  });

  it.each([['CV uploaded', { cvFile: 'cv.pdf' }], ['no CV', {}], ['experience entered', { experienceText: 'Campaign responsibilities' }], ['experience empty', { experienceText: '' }], ['existing saved CV', { cvFile: 'existing.pdf', experienceText: 'Existing experience' }]])('opens independently of CV saving: %s', async (_label, cvJob) => {
    wizard.cvJob = cvJob as Record<string, unknown>;
    wizard.saveIlr.mockImplementation(() => new Promise(() => {}));
    render(<CvSuitabilityPanel />);
    await userEvent.click(await screen.findByRole('button', { name: 'Start Assessment' }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(wizard.saveIlr).not.toHaveBeenCalled();
    expect(api.getAssessment).toHaveBeenCalledTimes(1);
  });

  it('opens a responsive loading modal immediately and offers Retry after a failed questionnaire load', async () => {
    vi.mocked(api.getAssessment).mockRejectedValueOnce(new Error('Could not load your questionnaire.'));
    render(<CvSuitabilityPanel />);
    await userEvent.click(screen.getByRole('button', { name: 'Start Assessment' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('alert')).toHaveTextContent('Could not load');
    vi.mocked(api.getAssessment).mockResolvedValue(fresh());
    await userEvent.click(within(dialog).getByRole('button', { name: 'Retry' }));
    expect(await within(dialog).findByRole('button', { name: 'Marketing' })).toBeInTheDocument();
    expect(wizard.saveIlr).not.toHaveBeenCalled();
  });

  it('stays on the questionnaire after a failed submit and shows the result after a successful Retry', async () => {
    const continued = vi.fn();
    render(<AssessmentDialog initial={{ ...fresh(), pathway: 'marketing', responses: { marketing: ['marketing_D'] }, result }}
      kind="apprenticeship" learnerId="41" onClose={vi.fn()} onConfirmed={continued} />);
    vi.mocked(api.saveAssessment).mockRejectedValueOnce(new Error('Network unavailable. Please retry.'));
    await userEvent.click(screen.getByRole('button', { name: 'Submit Assessment' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Network unavailable');
    expect(continued).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('heading', { name: 'Your Recommended Programme' })).toBeInTheDocument();
    expect(continued).not.toHaveBeenCalled();
  });

  it('prevents duplicate in-flight submissions and never navigates merely after submission', async () => {
    const continued = vi.fn(), submitted = vi.fn(); let resolve: (saved: AssessmentState) => void = () => {};
    vi.mocked(api.saveAssessment).mockImplementation(() => new Promise((done) => { resolve = done; }));
    const initial = { ...fresh(), pathway: 'marketing' as const, responses: { marketing: ['marketing_D'] }, result };
    render(<AssessmentDialog initial={initial} kind="apprenticeship" learnerId="41" onClose={vi.fn()} onConfirmed={continued} onSubmitted={submitted} />);
    const submit = screen.getByRole('button', { name: 'Submit Assessment' });
    fireEvent.click(submit); fireEvent.click(submit);
    expect(api.saveAssessment).toHaveBeenCalledTimes(1); expect(continued).not.toHaveBeenCalled();
    resolve({ ...initial, selectedProgramme: 'MM_L6', revision: 2, confirmedAt: '2026-10-31T12:00:00Z', completionStatus: 'SUBMITTED' });
    await waitFor(() => expect(submitted).toHaveBeenCalledTimes(1));
    expect(continued).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Continue to Next Step' })).toBeInTheDocument();
  });

  it('keeps saved draft answers when the learner closes and reopens without submitting', async () => {
    render(<CvSuitabilityPanel />);
    await userEvent.click(await screen.findByRole('button', { name: 'Start Assessment' }));
    await userEvent.click(screen.getByRole('button', { name: 'Marketing' }));
    await userEvent.click(await screen.findByLabelText('marketing answer A'));
    await userEvent.click(screen.getByRole('button', { name: 'Close assessment' }));
    expect(screen.queryByText('Assessment Completed')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Resume Assessment' }));
    expect(screen.getByLabelText('marketing answer A')).toBeChecked();
    expect(api.getAssessment).toHaveBeenCalledTimes(1);
  });

  it('does not open a modal or delay continuation when no assessment is published', async () => {
    vi.mocked(api.getAssessment).mockResolvedValue({ ...fresh(), available: false, configuration: null });
    const ref = createRef<SuitabilityContinuation>();
    render(<SuitabilityGate ref={ref} kind="apprenticeship" learnerId="41" />);
    expect(await ref.current!.open(vi.fn())).toBe(false);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('preserves a submitted pending result across closing and reopening until selection is confirmed', async () => {
    const state: AssessmentState = { ...fresh(), pathway: 'marketing', responses: { marketing: ['marketing_D'] }, result,
      revision: 1, confirmedAt: '2026-11-01T11:00:00Z', completionStatus: 'SUBMITTED',
      selectionStatus: 'SUBMITTED_PENDING_CONFIRMATION', selectionConfirmedAt: null, selectedProgramme: 'MM_L6',
      allowedSelections: ['MM_L6', 'ME_L4'] };
    vi.mocked(api.getAssessment).mockResolvedValue(state);
    const { unmount } = render(<CvSuitabilityPanel />);
    await userEvent.click(await screen.findByRole('button', { name: 'Preview Assessment' }));
    expect(within(screen.getByRole('dialog')).getByRole('button', { name: 'Continue to Next Step' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Close assessment' }));
    await userEvent.click(screen.getByRole('button', { name: 'Preview Assessment' }));
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Continue to Next Step' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.queryByText(/Interested in/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Preview Assessment' })).toBeInTheDocument();
    // Simulate a fresh mount after a new session: persisted response determines review mode.
    unmount(); vi.mocked(api.getAssessment).mockResolvedValue({ ...state,
      selectionStatus: 'SELECTION_CONFIRMED', selectionConfirmedAt: '2026-11-01T12:00:00Z' });
    render(<CvSuitabilityPanel />);
    await userEvent.click(await screen.findByRole('button', { name: 'Preview Assessment' }));
    expect(screen.queryByText(/Interested in/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Continue to Next Step' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit Answers' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Show Answers' })).toBeInTheDocument();
  });

  it.each(['ME_L4', 'MM_L6', 'APM_L4', 'PCP_L6'] as ProgrammeId[])('restricts %s alternatives to the pending confirmation popup across remounts', async (id) => {
    const programme = configuration.programmes.find((p) => p.id === id)!;
    const other = configuration.programmes.find((p) => p.pathway === programme.pathway && p.id !== id)!;
    const state: AssessmentState = { ...fresh(), pathway: programme.pathway,
      responses: { [programme.pathway]: [`${programme.pathway}_D`] },
      result: { ...result, outcome: `RECOMMENDED_${id}`, recommendedProgramme: id,
        scores: { [id]: 83.17, [other.id]: 52.96 } as typeof result.scores },
      revision: 1, confirmedAt: '2026-11-01T11:00:00Z', completionStatus: 'SUBMITTED',
      selectionStatus: 'SUBMITTED_PENDING_CONFIRMATION', selectionConfirmedAt: null, selectedProgramme: id,
      allowedSelections: programme.level === 6 ? [id, other.id] : [id] };
    vi.mocked(api.getAssessment).mockResolvedValue(state);
    const { unmount } = render(<CvSuitabilityPanel />);
    const preview = await screen.findByRole('button', { name: 'Preview Assessment' });
    expect(screen.queryByText(/Interested in/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Choose|Not Available|Review Assessment|Resume Programme Selection|Edit Assessment/ })).not.toBeInTheDocument();
    expect(api.saveAssessment).not.toHaveBeenCalled();
    await userEvent.click(preview);
    const popup = within(screen.getByRole('dialog'));
    expect(popup.getByText(/Interested in/)).toBeInTheDocument();
    expect(popup.getByRole('button', { name: 'Show Answers' })).toBeInTheDocument();
    const alternativeReason = 'My current role focuses on delivery.\nI want to strengthen that foundation.';
    if (programme.level === 6) {
      await userEvent.click(popup.getByRole('button', { name: `Choose ${other.title} L4` }));
      expect(api.saveAssessment).not.toHaveBeenCalled();
      fireEvent.change(screen.getByRole('textbox'), { target: { value: alternativeReason } });
      await userEvent.click(screen.getByRole('button', { name: 'Submit Reason & Continue' }));
    }
    else {
      expect(popup.getByRole('button', { name: /Not Available/ })).toBeDisabled();
      await userEvent.click(popup.getByRole('button', { name: 'Continue to Next Step' }));
    }
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.queryByText(/Interested in/)).not.toBeInTheDocument();
    const saved = await vi.mocked(api.saveAssessment).mock.results[0].value;
    expect(saved.selectedProgramme).toBe(programme.level === 6 ? other.id : id);
    expect(saved.selectionStatus).toBe('SELECTION_CONFIRMED');
    expect(saved.result?.recommendedProgramme).toBe(id);
    const assertAlternativeMessage = (scope: ReturnType<typeof within> = screen) => {
      if (programme.level === 6) {
        expect(scope.getByText(`You chose ${other.title} Level ${other.level} instead of ${programme.title} Level ${programme.level}.`)).toBeInTheDocument();
        expect(scope.getByText('Your Reason')).toBeInTheDocument();
        expect(scope.getByText((_, node) => node?.tagName === 'P' && node.textContent === alternativeReason)).toBeInTheDocument();
      } else {
        expect(scope.queryByText(/^You chose /)).not.toBeInTheDocument();
        expect(scope.queryByText('Your Reason')).not.toBeInTheDocument();
      }
    };
    assertAlternativeMessage();
    // A fresh component session restores the backend-confirmed record.
    unmount(); vi.mocked(api.getAssessment).mockResolvedValue(saved);
    render(<CvSuitabilityPanel />);
    const restoredPreview = await screen.findByRole('button', { name: 'Preview Assessment' });
    assertAlternativeMessage();
    await userEvent.click(restoredPreview);
    assertAlternativeMessage(within(screen.getByRole('dialog')));
    expect(screen.queryByText(/Interested in/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Choose|Continue to Next Step|Edit Answers|Resume Programme Selection/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Submit Reason & Continue' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Show Answers' }));
    expect(screen.getByLabelText(`${programme.pathway} answer D`)).toBeChecked();
    expect(screen.getByLabelText(`${programme.pathway} answer D`)).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Back to Result' }));
    expect(api.saveAssessment).toHaveBeenCalledTimes(1);
  });
});

function AdminHarness() {
  const [config, setConfig] = useState<Configuration | undefined>(structuredClone(configuration));
  return <><SuitabilityAdmin value={config} onChange={setConfig} /><output aria-label="Draft JSON">{JSON.stringify(config)}</output></>;
}
const draft = () => JSON.parse(screen.getByLabelText('Draft JSON').textContent!) as Configuration;

describe('wizard administration', () => {
  it('edits questions, weights and answer affinities as local drafts', async () => {
    render(<AdminHarness />);
    await userEvent.click(screen.getByRole('tab', { name: 'Marketing Assessment' }));
    await userEvent.click(screen.getByText('1. marketing responsibilities'));
    const weight = screen.getByLabelText('Question weight');
    await userEvent.clear(weight); await userEvent.type(weight, '20');
    const affinity = screen.getAllByLabelText('Marketing Executive affinity (0–1)')[0];
    await userEvent.clear(affinity); await userEvent.type(affinity, '0.5');
    expect(draft().questions[0].weight).toBe(20);
    expect(draft().questions[0].answers[0].affinities.ME_L4).toBe(0.5);
    expect(api.previewAssessment).not.toHaveBeenCalled();
    expect(api.saveAssessment).not.toHaveBeenCalled();
  });

  it('adds unlimited-within-validation answers and soft-deactivates items', async () => {
    render(<AdminHarness />);
    await userEvent.click(screen.getByRole('tab', { name: 'Marketing Assessment' }));
    await userEvent.click(screen.getByText('1. marketing responsibilities'));
    await userEvent.click(screen.getByRole('button', { name: 'Add answer' }));
    expect(draft().questions[0].answers).toHaveLength(5);
    await userEvent.click(screen.getAllByRole('button', { name: 'Remove answer' })[0]);
    expect(draft().questions[0].answers[0].active).toBe(false);
    await userEvent.click(screen.getByRole('button', { name: 'Remove question' }));
    expect(draft().questions[0].active).toBe(false);
    await userEvent.click(screen.getByRole('button', { name: 'Add question' }));
    expect(draft().questions).toHaveLength(3);
    expect(draft().questions[2].answers).toHaveLength(4);
  });

  it('edits guidance and scoring rules and calculates a backend preview without a learner save', async () => {
    render(<AdminHarness />);
    await userEvent.click(screen.getByRole('tab', { name: 'Programme Suitability Content' }));
    const summary = screen.getAllByLabelText('Suitability summary')[0];
    await userEvent.clear(summary); await userEvent.type(summary, 'New role guidance');
    expect(draft().programmes[0].summary).toBe('New role guidance');
    await userEvent.click(screen.getByRole('tab', { name: 'Scoring Rules' }));
    const threshold = screen.getByLabelText('Minimum match index (0–100)');
    await userEvent.clear(threshold); await userEvent.type(threshold, '70');
    await userEvent.click(screen.getByRole('tab', { name: 'Preview & Test' }));
    await userEvent.click(screen.getByLabelText('marketing answer D'));
    await userEvent.click(screen.getByRole('button', { name: 'Calculate preview' }));
    await screen.findByRole('table');
    expect(within(screen.getByRole('region', { name: 'Programme comparison' })).getByText('100')).toBeInTheDocument();
    expect(api.previewAssessment).toHaveBeenCalledWith(expect.objectContaining({ rules: expect.objectContaining({ minimumScore: 70 }) }), 'marketing', { marketing: ['marketing_D'] });
    expect(api.saveAssessment).not.toHaveBeenCalled();
  });

  it('preserves suitability through layout resolution so wizard publishing includes the draft', () => {
    const resolved = resolveLayout({ steps: [], programmeSuitability: configuration });
    expect(resolved.programmeSuitability).toEqual(configuration);
  });

  it('preserves assessment drafts while editing other wizard fields, text and step order', () => {
    let layout = resolveLayout({ steps: [], programmeSuitability: configuration });
    layout = updateStep(layout, 'cv-job', { label: 'CV and suitability' });
    layout = moveStep(layout, 'cv-job', 1);
    layout = setText(layout, 'cv.h.experience.title', 'Experience');
    layout = addStep(layout, 'Another step').layout;
    expect(layout.programmeSuitability).toEqual(configuration);
    expect(layout.texts?.['cv.h.experience.title']).toBe('Experience');
  });

  it('records a manual staff review separately from the learner choice', async () => {
    wizard.readOnly = true;
    const state: AssessmentState = { ...fresh(), pathway: 'marketing', responses: { marketing: ['marketing_D'] },
      result, selectedProgramme: 'ME_L4', originalPreference: 'ME_L4', revision: 2, reviewStatus: 'PENDING_ADMISSIONS_REVIEW',
      completionStatus: 'SUBMITTED', selectionStatus: 'SELECTION_CONFIRMED', selectionConfirmedAt: '2026-11-01T12:00:00Z',
      alternativeSelectionReason: 'My current job focuses on practical delivery.' };
    vi.mocked(api.getAssessment).mockResolvedValue(state);
    vi.mocked(api.saveReview).mockResolvedValue({ ...state, reviewStatus: 'REVIEW_COMPLETED' });
    render(<CvSuitabilityPanel />);
    const review = await screen.findByRole('group', { name: 'Admissions review' });
    await userEvent.selectOptions(within(review).getByLabelText('Review status'), 'REVIEW_COMPLETED');
    await userEvent.selectOptions(within(review).getByLabelText('Manual programme recommendation'), 'MM_L6');
    await userEvent.type(within(review).getByLabelText('Reason for review / override'), 'Reviewed strategic job responsibilities');
    await userEvent.click(within(review).getByRole('button', { name: 'Save admissions review' }));
    await waitFor(() => expect(api.saveReview).toHaveBeenCalledWith('apprenticeship', '41', 2, 'REVIEW_COMPLETED', 'MM_L6', 'Reviewed strategic job responsibilities'));
    expect(screen.getByText(/Learner preference: Marketing Executive Level 4/)).toBeInTheDocument();
    expect(screen.getByText('Learner-provided alternative programme reason')).toBeInTheDocument();
    expect(screen.getByText('My current job focuses on practical delivery.')).toBeInTheDocument();
    expect(screen.getByText('Critical Level 6 evidence')).toBeInTheDocument();
  });
});
