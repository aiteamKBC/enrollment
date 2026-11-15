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

  it('explains an unpublished assessment instead of showing an empty step or an unusable start button', async () => {
    vi.mocked(api.getAssessment).mockResolvedValue({ ...fresh(), available: false, configuration: null, required: false });
    render(<CvSuitabilityPanel />);
    expect(await screen.findByText(/assessment is not currently available/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Start Assessment' })).not.toBeInTheDocument();
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

function AdminHarness({ initial = configuration }: { initial?: Configuration }) {
  const [config, setConfig] = useState<Configuration | undefined>(structuredClone(initial));
  return <><SuitabilityAdmin value={config} onChange={setConfig} /><output aria-label="Draft JSON">{JSON.stringify(config)}</output></>;
}
const draft = () => JSON.parse(screen.getByLabelText('Draft JSON').textContent!) as Configuration;

describe('wizard administration', () => {
  it.each(['Marketing Assessment', 'Project Controls Assessment'])('keeps removals and legacy inactive items excluded without deleting their data in %s', async (tab) => {
    const source = structuredClone(configuration);
    const q = source.questions[tab === 'Marketing Assessment' ? 0 : 1];
    const inactive = { ...structuredClone(q), id: `${q.id}-inactive`, slug: `${q.slug}-inactive`, title: 'Legacy removed question', active: false,
      answers: q.answers.map((a) => ({ ...structuredClone(a), id: `${a.id}-inactive` })) };
    source.questions.push(inactive);
    q.answers[1].active = false;
    const legacyAnswer = structuredClone(q.answers[1]);
    render(<AdminHarness initial={source} />);
    await userEvent.click(screen.getByRole('tab', { name: tab }));
    expect(screen.queryByText('Legacy removed question')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
    expect(screen.queryByLabelText('Active question')).toBeNull();
    expect(screen.queryByLabelText('Active answer')).toBeNull();
    expect(screen.queryByDisplayValue(legacyAnswer.text)).toBeNull();
    await userEvent.click(screen.getAllByRole('button', { name: 'Move answer down' })[0]);
    await userEvent.click(screen.getByRole('button', { name: 'Add answer' }));
    let edited = draft().questions.find((item) => item.id === q.id)!;
    expect(edited.answers.find((a) => a.id === legacyAnswer.id)).toEqual(legacyAnswer);
    expect(edited.answers.at(-1)).toMatchObject({ text: 'New answer', active: true });
    const removedText = screen.getAllByLabelText('Answer text')[0] as HTMLTextAreaElement;
    const text = removedText.value;
    await userEvent.click(screen.getAllByRole('button', { name: 'Remove answer' })[0]);
    expect(screen.queryByDisplayValue(text)).toBeNull();
    expect(draft().questions.find((item) => item.id === q.id)!.answers.find((a) => a.text === text)!.active).toBe(false);
    await userEvent.click(screen.getByRole('button', { name: 'Add question' }));
    expect(draft().questions.at(-1)).toMatchObject({ active: true, pathway: q.pathway });
    expect(draft().questions.at(-1)!.answers.every((a) => a.active)).toBe(true);
    const existing = within(screen.getByRole('region', { name: `Question 1: ${q.title}` }));
    await userEvent.click(existing.getByRole('button', { name: 'Move question down' }));
    expect(draft().questions.find((item) => item.id === inactive.id)).toEqual(inactive);
    edited = draft().questions.find((item) => item.id === q.id)!;
    await userEvent.click(existing.getByRole('button', { name: 'Remove question' }));
    expect(screen.queryByText(q.title)).toBeNull();
    expect(draft().questions.find((item) => item.id === q.id)).toEqual({ ...edited, active: false });
    expect(source.questions.find((item) => item.id === q.id)!.active).toBe(true);
    expect(api.saveAssessment).not.toHaveBeenCalled();
  });

  it('keeps advanced configuration collapsed and preserves all values across the four main tabs', async () => {
    render(<AdminHarness />);
    expect(screen.getByRole('tab', { name: 'Marketing Assessment' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.queryByRole('tab', { name: 'General Settings' })).toBeNull();
    expect(screen.queryByRole('tab', { name: 'Scoring Rules' })).toBeNull();
    const advanced = screen.getByText('Advanced configuration').closest('details')!;
    expect(advanced.open).toBe(false);
    expect(screen.getByLabelText('Enable assessment after publishing')).not.toBeVisible();
    expect(screen.getByLabelText('Minimum match index (0–100)')).not.toBeVisible();
    expect(within(screen.getByRole('tabpanel')).queryByLabelText('Learner introduction')).toBeNull();
    for (const tab of ['Project Controls Assessment', 'Programme Modules', 'Preview & Test', 'Marketing Assessment']) {
      await userEvent.click(screen.getByRole('tab', { name: tab }));
      expect(draft()).toEqual(configuration);
    }
    await userEvent.click(screen.getByText('Advanced configuration'));
    expect(screen.getByLabelText('Enable assessment after publishing')).toBeChecked();
    expect(screen.getByLabelText('Learner introduction')).toHaveValue(configuration.introduction);
    await userEvent.click(screen.getByLabelText('Enable assessment after publishing'));
    await userEvent.type(screen.getByLabelText('Learner introduction'), ' Updated introduction');
    expect(draft()).toEqual({ ...configuration, enabled: false, introduction: `${configuration.introduction} Updated introduction` });
    expect(api.previewAssessment).not.toHaveBeenCalled();
    expect(api.saveAssessment).not.toHaveBeenCalled();
  });

  it.each(['Marketing Assessment', 'Project Controls Assessment'])('edits inline scoring once per item and retains stable IDs after reordering in %s', async (tab) => {
    render(<AdminHarness />);
    const original = configuration.questions[tab === 'Marketing Assessment' ? 0 : 1];
    const programmes = configuration.programmes.filter((p) => p.pathway === original.pathway);
    await userEvent.click(screen.getByRole('tab', { name: tab }));
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const advanced = screen.getByText('Advanced Settings').closest('details')!;
    expect(advanced.open).toBe(false);
    expect(within(advanced).queryByLabelText('Question weight')).toBeNull();
    expect(within(advanced).queryByRole('spinbutton')).toBeNull();
    const weights = screen.getAllByLabelText('Question weight');
    expect(weights).toHaveLength(1);
    expect(weights[0]).toBeVisible();
    expect(weights[0]).toHaveValue(original.weight);
    const row = within(screen.getByRole('group', { name: 'Answer 1' }));
    for (const programme of programmes) {
      const label = `${programme.title} affinity (0–1)`;
      expect(screen.getAllByLabelText(label)).toHaveLength(original.answers.length);
      expect(row.getByLabelText(label)).toBeVisible();
      expect(row.getByLabelText(label)).toHaveValue(original.answers[0].affinities[programme.id] ?? 0);
    }
    const affinity = row.getByLabelText(`${programmes[0].title} affinity (0–1)`);
    await userEvent.clear(weights[0]); await userEvent.type(weights[0], '22');
    await userEvent.clear(affinity); await userEvent.type(affinity, '0.7');
    await userEvent.click(row.getByRole('button', { name: 'Move answer down' }));
    const changed = draft().questions.find((q) => q.id === original.id)!;
    expect(changed.answers.find((answer) => answer.id === original.answers[0].id)).toMatchObject({
      order: 1, affinities: { ...original.answers[0].affinities, [programmes[0].id]: 0.7 },
    });
    expect(within(screen.getByRole('group', { name: 'Answer 2' })).getByLabelText(`${programmes[0].title} affinity (0–1)`)).toHaveValue(0.7);
    await userEvent.click(screen.getByRole('button', { name: 'Add question' }));
    const originalRow = within(screen.getByRole('region', { name: `Question 1: ${original.title}` }));
    await userEvent.click(originalRow.getByRole('button', { name: 'Move question down' }));
    const moved = draft().questions.find((q) => q.id === original.id)!;
    expect(moved).toEqual({ ...changed, order: 1 });
    expect(moved.weight).toBe(22);
    expect(api.saveAssessment).not.toHaveBeenCalled();
    expect(api.previewAssessment).not.toHaveBeenCalled();
  });

  it('retires critical controls while retaining legacy fields and unrelated scoring settings', async () => {
    render(<AdminHarness />);
    await userEvent.click(screen.getByText('Advanced configuration'));
    await userEvent.click(screen.getByText('Advanced Scoring Settings'));
    expect(screen.queryByLabelText('Minimum distinct critical Level 6 indicators')).toBeNull();
    expect(screen.queryByLabelText('Minimum answer strength for a critical indicator')).toBeNull();
    expect(screen.getByLabelText('Per-programme strength defining mixed evidence')).toHaveValue(configuration.rules.mixedAffinityThreshold);
    expect(draft().rules).toEqual(configuration.rules);
    for (const tab of ['Marketing Assessment', 'Project Controls Assessment']) {
      await userEvent.click(screen.getByRole('tab', { name: tab }));
      await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
      await userEvent.click(screen.getByText('Advanced Settings'));
      expect(screen.queryByLabelText('Critical Level 6 indicator')).toBeNull();
      expect(screen.queryByLabelText('Critical indicator target')).toBeNull();
      expect(screen.getByLabelText('Question weight')).toHaveValue(10);
      expect(screen.getByLabelText('Allow multiple selected answers')).toBeChecked();
    }
    expect(draft()).toEqual(configuration);
  });

  it.each(['Marketing Assessment', 'Project Controls Assessment'])('keeps %s compact and preserves scoring and programme data through ordinary edits', async (tab) => {
    render(<AdminHarness />);
    await userEvent.click(screen.getByRole('tab', { name: tab }));
    expect(screen.queryByLabelText('Question title')).toBeNull();
    const index = tab === 'Marketing Assessment' ? 0 : 1;
    expect(screen.getByText(configuration.questions[index].description)).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const advanced = screen.getByText('Advanced Settings').closest('details')!;
    expect(advanced.open).toBe(false);
    expect(screen.getByLabelText('Question weight')).toBeVisible();
    await userEvent.type(screen.getByLabelText('Question title'), ' updated');
    await userEvent.type(screen.getAllByLabelText('Answer text')[0], ' updated');
    await userEvent.click(screen.getByLabelText('Required question'));
    expect(draft().programmes).toEqual(configuration.programmes);
    expect(draft().rules).toEqual(configuration.rules);
    expect(draft().questions[index]).toMatchObject({
      id: configuration.questions[index].id, weight: configuration.questions[index].weight,
      critical: configuration.questions[index].critical, criticalProgramme: configuration.questions[index].criticalProgramme,
      required: false,
    });
    expect(draft().questions[index].answers.map((answer) => [answer.id, answer.affinities])).toEqual(
      configuration.questions[index].answers.map((answer) => [answer.id, answer.affinities]));
    await userEvent.click(screen.getByRole('button', { name: 'Close editor' }));
    expect(screen.queryByLabelText('Question title')).toBeNull();
  });

  it('shows only the selected section and keeps modules available without assessment defaults', async () => {
    render(<SuitabilityAdmin modulesEditor={<p>Existing modules editor</p>} onChange={vi.fn()} />);
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Marketing Assessment', 'Project Controls Assessment', 'Programme Modules', 'Preview & Test',
    ]);
    expect(screen.getByText('Existing modules editor')).not.toBeVisible();
    await userEvent.click(screen.getByRole('tab', { name: 'Programme Modules' }));
    expect(screen.getByText('Existing modules editor')).toBeVisible();
    expect(screen.queryByRole('button', { name: /Add the initial/ })).toBeNull();
    await userEvent.click(screen.getByRole('tab', { name: 'Marketing Assessment' }));
    expect(screen.getByText('Existing modules editor')).not.toBeVisible();
    expect(api.fetchDefaults).not.toHaveBeenCalled();
  });

  it('retains catalogue mapping edits in general settings without exposing programme content editing', async () => {
    render(<AdminHarness />);
    await userEvent.click(screen.getByText('Advanced configuration'));
    await userEvent.click(screen.getByText('Advanced programme associations'));
    await userEvent.type(screen.getByLabelText('Marketing Executive: existing catalogue ID (optional reference)'), 'catalogue-42');
    expect(draft().programmes[0]).toEqual({ ...configuration.programmes[0], catalogueId: 'catalogue-42' });
    expect(screen.queryByLabelText('Programme title')).toBeNull();
  });

  it('edits questions, weights and answer affinities as local drafts', async () => {
    render(<AdminHarness />);
    await userEvent.click(screen.getByRole('tab', { name: 'Marketing Assessment' }));
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
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
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
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

  it('preserves guidance while editing scoring rules and calculates a backend preview without a learner save', async () => {
    render(<AdminHarness />);
    expect(screen.queryByRole('tab', { name: 'Programme Suitability Content' })).toBeNull();
    expect(draft().programmes).toEqual(configuration.programmes);
    await userEvent.click(screen.getByText('Advanced configuration'));
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
    expect(screen.getByText('You chose Marketing Executive Level 4 instead of Marketing Manager Level 6.')).toBeInTheDocument();
    expect(screen.getByText('Your Reason')).toBeInTheDocument();
    expect(screen.getByText('My current job focuses on practical delivery.')).toBeInTheDocument();
    expect(screen.queryByText('Critical Level 6 evidence')).toBeNull();
  });
});
