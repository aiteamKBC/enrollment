import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type {
  SuitabilityRplEvidence,
  SuitabilityRplIssuedDocument,
  SuitabilityRplReport,
  SuitabilityRplState,
} from '@/api/suitabilityRplReport';

const api = vi.hoisted(() => ({
  fetchReport: vi.fn(),
  fetchIssued: vi.fn(),
  save: vi.fn(),
  sign: vi.fn(),
}));

vi.mock('@/api/suitabilityRplReport', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/suitabilityRplReport')>()),
  fetchSuitabilityRplReport: api.fetchReport,
  fetchSuitabilityRplIssuedDocuments: api.fetchIssued,
  saveSuitabilityRplReport: api.save,
  signSuitabilityRplReport: api.sign,
}));

vi.mock('@/pages/users/wizard/steps/SignaturePad', () => ({
  SignaturePad: ({ onCommit, onCancel }: { onCommit: (value: string) => void; onCancel: () => void }) => <div>
    <button type="button" onClick={() => onCommit('data:image/png;base64,assessor-signature')}>Complete drawn signature</button>
    <button type="button" onClick={onCancel}>Cancel signature</button>
  </div>,
}));

import { SuitabilityRplReportTab } from './SuitabilityRplReportTab';

const evidence: SuitabilityRplEvidence = {
  learner: { id: '20', name: 'Sam Example', reference: 'SYN-20', employer: 'Example Ltd', jobTitle: 'Assistant', responsibilities: [] },
  programme: { id: 'ME-L4', name: 'Marketing Executive Level 4', level: '4', startDate: '2026-09-01' },
  programmeUnderstanding: [],
  programmeSuitability: { status: 'available', responses: [] },
  skillsRadar: { status: 'available', scaleMaximum: 9, catalogueVersion: '1', items: [] },
  evidence: {
    cv: { status: 'provided', experience: 'Marketing experience', documents: [] },
    qualifications: { status: 'not_provided', items: [], documents: [] },
    plr: { status: 'not_available', message: 'Not available' },
  },
  trainingPlan: { available: true, source: 'authoritative_training_plan', plannedHours: 100, modules: [], message: '' },
  limitations: [],
  capturedAt: '2026-11-15T12:00:00Z',
};

const report: SuitabilityRplReport = {
  id: 'report-2', status: 'finalised', reportReference: 'RPL-20-2', reportVersion: 2, revision: 3,
  parentReportId: 'report-1', amendmentReason: 'Updated assessment', learnerKind: 'apprenticeship', learnerId: '20',
  contextKey: 'synthetic', programmeId: 'ME-L4', programmeName: 'Marketing Executive Level 4', enrolmentReference: 'SYN-20', practicalStartDate: '2026-09-01',
  relevantPriorLearning: false, reductionJustified: false, assessorEvidenceReview: 'Reviewed', noReductionReason: 'No verified prior learning',
  suitabilityOutcome: 'suitable', finalSummary: '', suitabilityJustification: 'Suitable for the programme.', suitabilityReviewed: true,
  suitabilityReviewedAt: '2026-11-15T10:00:00Z', suitabilityReviewedBy: 'Assessor', rplJustification: 'No relevant prior learning.', rplReviewed: true,
  rplReviewedAt: '2026-11-15T10:00:00Z', rplReviewedBy: 'Assessor', cvExtractionAcknowledged: false, cvExtractionAcknowledgedBy: null,
  cvExtractionAcknowledgedAt: null, cvEvidenceVersion: { documentId: '', container: '', blobName: '', sha256: '', extractorVersion: '' },
  sourceSnapshot: evidence, sourceVersions: {}, sourceSnapshotHash: 'snapshot-2',
  trainingHours: { originalHours: 100, reductionHours: 0, remainingHours: 100 },
  reductions: [], createdAt: '2026-11-15T09:00:00Z',
  updatedAt: '2026-11-15T10:00:00Z', finalisedAt: '2026-11-15T10:00:00Z',
};

const issued: SuitabilityRplIssuedDocument = {
  issueId: 'issue-2', title: 'Suitability & RPL Report', issuedAt: '2026-11-15T11:00:00Z', reportId: report.id,
  reportVersion: report.reportVersion, reportRevision: report.revision, pdfSha256: 'trusted-sha256', pdfUrl: '/issued/issue-2/pdf/',
  requiredRoles: ['admin', 'learner', 'employer'],
  signatures: {
    admin: { signed: false, name: '', signedAt: null, method: '' },
    learner: { signed: false, name: '', signedAt: null, method: '' },
    employer: { signed: false, name: '', signedAt: null, method: '' },
  },
  fullySigned: false, signingEnabled: true, signingStatus: 'awaiting_signatures',
};

const state: SuitabilityRplState = {
  report, evidence, sourceVersions: {},
  context: { key: report.contextKey, programmeId: report.programmeId, programmeName: report.programmeName, enrolmentReference: report.enrolmentReference, practicalStartDate: report.practicalStartDate },
  history: [{ id: report.id, reportReference: report.reportReference, programmeName: report.programmeName, status: report.status, reportVersion: report.reportVersion, amendmentReason: report.amendmentReason || '', parentReportId: report.parentReportId || null, finalisedAt: report.finalisedAt }],
  aiEnabled: false,
};

describe('Suitability & RPL assessor signing consent', () => {
  beforeEach(() => {
    api.fetchReport.mockResolvedValue(state);
    api.fetchIssued.mockResolvedValue({ documents: [issued] });
    api.sign.mockResolvedValue({ status: { issueId: issued.issueId } });
    vi.spyOn(window, 'open').mockReturnValue({} as Window);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    vi.restoreAllMocks();
  });

  it('requires the exact issued PDF preview before consent and completes signing afterwards', async () => {
    render(<SuitabilityRplReportTab kind="apprenticeship" learnerId="20" learnerName="Sam Example" />);

    fireEvent.click(await screen.findByRole('button', { name: 'Sign as assessor' }));
    const consent = screen.getByRole('checkbox', { name: /I have reviewed this exact report version/ });
    expect(consent).toBeDisabled();
    expect(consent).not.toBeChecked();
    expect(screen.getByText('Open the exact issued PDF above to enable consent.')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Complete drawn signature' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Preview exact issued PDF' }));
    expect(window.open).toHaveBeenCalledWith(issued.pdfUrl, '_blank', 'noopener,noreferrer');
    expect(consent).toBeEnabled();
    expect(screen.getByText('The exact issued PDF has been opened. You can now confirm consent.')).toBeVisible();

    fireEvent.click(consent);
    expect(consent).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: 'Complete drawn signature' }));

    await waitFor(() => expect(api.sign).toHaveBeenCalledWith('apprenticeship', '20', issued.issueId, {
      role: 'admin',
      name: 'Assessor',
      signature: 'data:image/png;base64,assessor-signature',
      consent: true,
      consentText: 'I have reviewed the exact issued Suitability & RPL Report PDF and consent to signing this immutable version.',
    }));
    await waitFor(() => expect(api.fetchIssued).toHaveBeenCalledTimes(2));
  });

  it('recognises a preview from the overview only for the current issued document', async () => {
    render(<SuitabilityRplReportTab kind="apprenticeship" learnerId="20" learnerName="Sam Example" />);

    fireEvent.click(await screen.findByRole('button', { name: 'Preview PDF' }));
    fireEvent.click(screen.getByRole('button', { name: 'Sign as assessor' }));

    expect(screen.getByRole('checkbox', { name: /I have reviewed this exact report version/ })).toBeEnabled();
    expect(screen.getByText('The exact issued PDF has been opened. You can now confirm consent.')).toBeVisible();
  });

  it('keeps exact historical PDFs visible without selecting a report in an ambiguous context', async () => {
    const contextError = 'Multiple saved enrolment contexts match this learner. Confirm the learner reference.';
    const older = { ...state.history[0], id: 'other-report', amendmentReason: 'Earlier enrolment' };
    const unissued = { ...state.history[0], id: 'unissued-report', amendmentReason: 'Unissued enrolment' };
    const mismatched = { ...state.history[0], id: 'different-version', amendmentReason: 'Wrong issued version' };
    api.fetchReport.mockResolvedValue({ ...state, report: null, contextError, history: [older, state.history[0], unissued, mismatched] });
    api.fetchIssued.mockResolvedValue({ documents: [
      issued,
      { ...issued, reportId: older.id, issueId: 'older-issue', pdfUrl: '/issued/older-issue/pdf/', signingEnabled: false },
      { ...issued, reportId: mismatched.id, reportVersion: 3, issueId: 'wrong-version', pdfUrl: '/issued/wrong-version/pdf/' },
    ] });

    render(<SuitabilityRplReportTab kind="apprenticeship" learnerId="20" learnerName="Sam Example" />);

    expect(await screen.findByRole('alert')).toHaveTextContent(contextError);
    const [olderRow, currentRow, unissuedRow, mismatchedRow] = within(screen.getByRole('region', { name: 'Version history' })).getAllByRole('article');
    expect(within(olderRow).getByRole('link', { name: 'View PDF' })).toHaveAttribute('href', '/issued/older-issue/pdf/');
    expect(within(olderRow).getByText('Electronic signing unavailable')).toBeVisible();
    expect(within(currentRow).getByRole('link', { name: 'View PDF' })).toHaveAttribute('href', issued.pdfUrl);
    expect(within(unissuedRow).queryByRole('link', { name: 'View PDF' })).not.toBeInTheDocument();
    expect(within(mismatchedRow).queryByRole('link', { name: 'View PDF' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Preview PDF' })).not.toBeInTheDocument();
  });

  it('updates version history after saving a new draft and after finalising it', async () => {
    const draft = { ...report, status: 'draft' as const, finalisedAt: null };
    const draftHistory = [{ ...state.history[0], status: 'draft', finalisedAt: null }];
    api.fetchReport.mockResolvedValue({ ...state, report: null, history: [] });
    api.fetchIssued.mockResolvedValue({ documents: [] });
    api.save.mockResolvedValueOnce({ report: draft, aiEnabled: false, history: draftHistory })
      .mockResolvedValueOnce({ report, aiEnabled: false, history: state.history });
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(<SuitabilityRplReportTab kind="apprenticeship" learnerId="20" learnerName="Sam Example" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Save draft' }));

    const history = screen.getByRole('region', { name: 'Version history' });
    const row = await within(history).findByRole('article');
    expect(row).toHaveAttribute('aria-current', 'true');
    expect(within(row).getByText('Draft')).toBeVisible();
    expect(within(row).queryByRole('link', { name: 'View PDF' })).not.toBeInTheDocument();

    const finalise = screen.getByRole('button', { name: 'Finalise report' });
    await waitFor(() => expect(finalise).toBeEnabled());
    fireEvent.click(finalise);
    expect(await within(history).findByText('Finalised')).toBeVisible();
  });

  it('keeps the exact historical PDF and refreshes its signing status after creating an amended version', async () => {
    const amended = { ...report, id: 'report-3', reportVersion: 3, revision: 1, parentReportId: report.id,
      status: 'draft' as const, amendmentReason: 'New evidence', finalisedAt: null };
    const amendedHistory = { ...state.history[0], id: amended.id, reportVersion: 3, status: 'draft',
      parentReportId: report.id, amendmentReason: 'New evidence', finalisedAt: null };
    api.save.mockResolvedValue({ report: amended, aiEnabled: false, history: [amendedHistory, ...state.history] });
    api.fetchIssued.mockResolvedValueOnce({ documents: [issued] })
      .mockResolvedValueOnce({ documents: [{ ...issued, fullySigned: true, signingStatus: 'signed' }] });
    vi.spyOn(window, 'prompt').mockReturnValue('New evidence');

    render(<SuitabilityRplReportTab kind="apprenticeship" learnerId="20" learnerName="Sam Example" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Create amended version' }));

    const history = screen.getByRole('region', { name: 'Version history' });
    expect(await within(history).findByText('Fully signed')).toBeVisible();
    const [draftRow, signedRow] = within(history).getAllByRole('article');
    expect(draftRow).toHaveAttribute('aria-current', 'true');
    expect(within(draftRow).getByText(/Version 3/)).toBeVisible();
    expect(within(draftRow).queryByRole('link', { name: 'View PDF' })).not.toBeInTheDocument();
    expect(within(signedRow).getByRole('link', { name: 'View PDF' })).toHaveAttribute('href', issued.pdfUrl);
    expect(api.save).toHaveBeenCalledWith('apprenticeship', '20', expect.objectContaining({
      newVersion: true, amendmentReason: 'New evidence', finalise: false,
    }));
  });

  it('displays programme totals calculated by the server for recorded reductions', async () => {
    const reduced = {
      ...report,
      relevantPriorLearning: true,
      reductionJustified: true,
      sourceSnapshot: { ...evidence, trainingPlan: { ...evidence.trainingPlan, plannedHours: 200.35 } },
      trainingHours: { originalHours: 200.35, reductionHours: 0.3, remainingHours: 200.05 },
      reductions: [
        { moduleId: 'a', reducedHours: 0.1, ksbCodes: [], evidence: '', assessorComments: '' },
        { moduleId: 'b', reducedHours: 0.2, ksbCodes: [], evidence: '', assessorComments: '' },
      ],
    };
    api.fetchReport.mockResolvedValue({ ...state, report: reduced });

    render(<SuitabilityRplReportTab kind="apprenticeship" learnerId="20" learnerName="Sam Example" />);
    const summary = await screen.findByRole('region', { name: 'Module reductions' });
    for (const hours of ['200.35', '0.3', '200.05']) {
      expect(within(summary).getByText(hours)).toBeVisible();
    }
  });
});
