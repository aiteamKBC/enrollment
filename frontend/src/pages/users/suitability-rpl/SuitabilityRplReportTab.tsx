import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Bot, Check, CheckCircle2, Clock3, Download, FileCheck2, FileSearch, History,
  Loader2, LockKeyhole, Plus, Save, Sparkles, Trash2, UserRound,
} from 'lucide-react';
import type { LearnerKind } from '@/api/learnerDetail';
import {
  fetchSuitabilityRplReport,
  fetchSuitabilityRplIssuedDocuments,
  signSuitabilityRplReport,
  analyseSuitabilityRplEvidence,
  generateSuitabilityRplJustification,
  issueSuitabilityRplReport,
  previewSuitabilityRplReport,
  saveSuitabilityRplReport,
  SuitabilityRplRequestError,
  type SuitabilityRplEvidence,
  type SuitabilityRplReduction,
  type SuitabilityRplReport,
  type SuitabilityRplSavePayload,
  type RplAnalysisRecommendation,
  type RplAnalysisMetadata,
  type SuitabilityRplIssuedDocument,
  type SuitabilityRplState,
} from '@/api/suitabilityRplReport';
import { SignaturePad } from '@/pages/users/wizard/steps/SignaturePad';
import { reportIssueButtonLabel, shouldShowDraftDownload } from './reportActions';

type Decision = 'undecided' | 'none' | 'relevant-with-reduction' | 'legacy-relevant-no-reduction';
type FormState = Omit<SuitabilityRplSavePayload, 'revision' | 'finalise'>;

const initialForm: FormState = {
  relevantPriorLearning: null,
  reductionJustified: null,
  assessorEvidenceReview: '',
  noReductionReason: '',
  suitabilityOutcome: 'not_assessed',
  finalSummary: '',
  suitabilityJustification: '',
  suitabilityReviewed: false,
  rplJustification: '',
  rplReviewed: false,
  cvExtractionAcknowledged: false,
  cvExtractionAcknowledgementReason: '',
  reductions: [],
};

function formFromReport(report: SuitabilityRplReport | null): FormState {
  if (!report) return initialForm;
  return {
    relevantPriorLearning: report.relevantPriorLearning,
    reductionJustified: report.reductionJustified,
    assessorEvidenceReview: report.assessorEvidenceReview,
    noReductionReason: report.noReductionReason,
    suitabilityOutcome: report.suitabilityOutcome,
    finalSummary: report.finalSummary,
    suitabilityJustification: report.suitabilityJustification,
    suitabilityReviewed: report.suitabilityReviewed,
    rplJustification: report.rplJustification,
    rplReviewed: report.rplReviewed,
    cvExtractionAcknowledged: report.cvExtractionAcknowledged,
    cvExtractionAcknowledgementReason: '',
    reductions: report.reductions.map((row) => ({ ...row })),
  };
}

function decisionOf(form: FormState): Decision {
  if (form.relevantPriorLearning === null) return 'undecided';
  if (form.relevantPriorLearning === false) return 'none';
  return form.reductionJustified ? 'relevant-with-reduction' : 'legacy-relevant-no-reduction';
}

function simpleRplOutcome(outcome: RplAnalysisRecommendation['overallOutcome']) {
  if (outcome === 'no-relevant-prior-learning') return 'No relevant prior learning';
  if (outcome === 'insufficient-evidence') return 'Further review required';
  return 'Relevant prior learning';
}

function shortRplReason(value: string) {
  const sentences = value.replace(/\s+/g, ' ').trim().match(/[^.!?]+[.!?]+/g);
  const concise = (sentences || [value.trim()]).slice(0, 2).join(' ').trim();
  const words = concise.split(/\s+/).filter(Boolean);
  return words.length <= 40 ? concise : `${words.slice(0, 40).join(' ').replace(/[,:;]$/, '')}…`;
}

function reportFilename(learnerName: string, reportReference?: string) {
  const stem = [learnerName, reportReference].filter(Boolean).join('-') || 'Draft';
  return `Suitability-and-RPL-${stem.replace(/[^a-z0-9_-]+/gi, '-').replace(/-+/g, '-')}.pdf`;
}

const panel = 'rounded-2xl border border-foreground-200/70 bg-background-50';
const label = 'text-[11px] font-semibold uppercase tracking-[0.12em] text-foreground-500';
const input = 'mt-1.5 w-full rounded-xl border border-foreground-200 bg-white px-3 py-2.5 text-sm text-foreground-900 outline-none focus:border-primary-400 focus:ring-2 focus:ring-primary-100 disabled:bg-background-100 disabled:text-foreground-500';
const secondaryButton = 'inline-flex items-center justify-center gap-2 rounded-xl border border-foreground-200 bg-white px-3.5 py-2.5 text-sm font-semibold text-foreground-700 transition hover:border-primary-300 hover:text-primary-700 disabled:cursor-not-allowed disabled:opacity-50';
const primaryButton = 'inline-flex items-center justify-center gap-2 rounded-xl bg-primary-700 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-primary-800 disabled:cursor-not-allowed disabled:opacity-50';

function ErrorMessage({ children }: { children: string }) {
  return <p role="alert" className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">{children}</p>;
}

export function SuitabilityRplReportTab({ kind, learnerId, learnerName }: {
  kind: LearnerKind;
  learnerId: string;
  learnerName: string;
}) {
  const [evidence, setEvidence] = useState<SuitabilityRplEvidence | null>(null);
  const [report, setReport] = useState<SuitabilityRplReport | null>(null);
  const [form, setForm] = useState<FormState>(initialForm);
  const [aiEnabled, setAiEnabled] = useState(false);
  const [history, setHistory] = useState<SuitabilityRplState['history']>([]);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [issuedDocuments, setIssuedDocuments] = useState<SuitabilityRplIssuedDocument[]>([]);
  const [signingIssue, setSigningIssue] = useState<SuitabilityRplIssuedDocument | null>(null);
  const [signConsent, setSignConsent] = useState(false);
  const [previewedIssueId, setPreviewedIssueId] = useState<string | null>(null);
  const [rplAnalysis, setRplAnalysis] = useState<{ recommendation: RplAnalysisRecommendation; metadata: RplAnalysisMetadata } | null>(null);
  const loadSequence = useRef(0);

  const load = useCallback(async (signal?: AbortSignal) => {
    const sequence = ++loadSequence.current;
    setLoading(true);
    setError('');
    try {
      const state = await fetchSuitabilityRplReport(kind, learnerId, signal);
      if (sequence !== loadSequence.current || signal?.aborted) return;
      setEvidence(state.report?.sourceSnapshot || state.evidence);
      setReport(state.report);
      setForm(formFromReport(state.report));
      setAiEnabled(state.aiEnabled);
      setHistory(state.history);
      setRplAnalysis(null);
      try { setIssuedDocuments((await fetchSuitabilityRplIssuedDocuments(kind, learnerId)).documents); } catch { setIssuedDocuments([]); }
    } catch (cause) {
      if (sequence === loadSequence.current && !(cause instanceof DOMException && cause.name === 'AbortError')) {
        setError(cause instanceof Error ? cause.message : 'Could not load the report.');
      }
    } finally {
      if (sequence === loadSequence.current) setLoading(false);
    }
  }, [kind, learnerId]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const locked = report?.status === 'finalised';
  const issued = !!report && issuedDocuments.some((document) =>
    document.reportId === report.id
    && document.reportVersion === report.reportVersion
    && document.reportRevision === report.revision
  );
  const currentIssued = report ? issuedDocuments.find((document) =>
    document.reportId === report.id
    && document.reportVersion === report.reportVersion
    && document.reportRevision === report.revision
  ) : undefined;
  const decision = decisionOf(form);
  const cvStatus = evidence?.evidence.cv.status || 'not_provided';
  const cvExtractionStatus = evidence?.evidence.cv.extractionStatus || (cvStatus === 'provided' ? 'pending' : 'not_provided');
  const cvNeedsAttention = ['failed', 'unsupported', 'stale'].includes(cvExtractionStatus);
  const modules = evidence?.trainingPlan.modules || [];
  const selectedIds = useMemo(() => new Set(form.reductions.map((row) => row.moduleId)), [form.reductions]);
  const totalReduction = form.reductions.reduce((sum, row) => sum + (Number(row.reducedHours) || 0), 0);
  const adjustedHours = evidence?.trainingPlan.plannedHours == null ? null : evidence.trainingPlan.plannedHours - totalReduction;
  const analysisStale = !!rplAnalysis && !!report && (
    rplAnalysis.metadata.sourceSnapshotHash !== report.sourceSnapshotHash
    || (!!rplAnalysis.metadata.cvEvidenceVersion.sha256 && rplAnalysis.metadata.cvEvidenceVersion.sha256 !== report.cvEvidenceVersion.sha256)
  );
  const reportStateLabel = locked ? 'Finalised & locked' : 'Draft in progress';
  const signatureStateLabel = currentIssued
    ? (currentIssued.fullySigned ? 'Fully signed' : 'Awaiting signatures')
    : (locked ? 'Ready to issue' : 'Signatures available after issue');
  const rplDecisionLabel = decision === 'none'
    ? 'No relevant prior learning'
    : decision === 'relevant-with-reduction'
      ? 'Training reduction required'
      : decision === 'legacy-relevant-no-reduction'
        ? 'Relevant prior learning'
        : 'Decision required';
  const signatureRoles = [
    ['admin', 'Assessor'],
    ['learner', 'Learner'],
    ['employer', 'Employer'],
  ] as const;

  const setDecision = (next: Decision) => {
    if (locked) return;
    setForm((current) => ({
      ...current,
      relevantPriorLearning: next === 'undecided' ? null : next !== 'none',
      reductionJustified: next === 'undecided' ? null : next === 'relevant-with-reduction',
      reductions: next === 'relevant-with-reduction' ? current.reductions : [],
    }));
  };

  const save = async (finalise = false) => {
    if (locked) return;
    if (finalise && !window.confirm('Finalise this report? Its approved snapshot and assessment will become read-only.')) return;
    setWorking(finalise ? 'finalise' : 'save');
    setError('');
    setNotice('');
    try {
      const result = await saveSuitabilityRplReport(kind, learnerId, {
        ...form,
        revision: report?.revision || 0,
        finalise,
      });
      setReport(result.report);
      setEvidence(result.report.sourceSnapshot);
      setForm(formFromReport(result.report));
      setAiEnabled(result.aiEnabled);
      setNotice(finalise ? 'Report finalised. The approved snapshot is now locked.' : 'Draft saved.');
    } catch (cause) {
      if (finalise && cause instanceof SuitabilityRplRequestError && [502, 503, 504].includes(cause.status)) {
        try {
          const state = await fetchSuitabilityRplReport(kind, learnerId);
          if (state.report?.status === 'finalised') {
            setEvidence(state.report.sourceSnapshot || state.evidence);
            setReport(state.report);
            setForm(formFromReport(state.report));
            setAiEnabled(state.aiEnabled);
            setHistory(state.history);
            setNotice('Report finalised. The approved snapshot is now locked.');
            return;
          }
        } catch {
          // Keep the original gateway error when the confirmation request also fails.
        }
      }
      setError(cause instanceof Error ? cause.message : 'Could not save the report.');
    } finally {
      setWorking(null);
    }
  };

  const createAmendedVersion = async () => {
    if (!report || report.status !== 'finalised' || working) return;
    const amendmentReason = window.prompt('Why is an amended version required?')?.trim();
    if (!amendmentReason) return;
    setWorking('amend'); setError(''); setNotice('');
    try {
      const result = await saveSuitabilityRplReport(kind, learnerId, { ...form, revision: 0, newVersion: true, amendmentReason, finalise: false });
      setReport(result.report); setEvidence(result.report.sourceSnapshot); setForm(formFromReport(result.report));
      setAiEnabled(result.aiEnabled); setNotice(`Version ${result.report.reportVersion} created as an editable draft.`);
      try { setIssuedDocuments((await fetchSuitabilityRplIssuedDocuments(kind, learnerId)).documents); } catch { /* Existing issued links remain available from the previous load. */ }
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not create the amended version.'); }
    finally { setWorking(null); }
  };

  const generate = async (type: 'suitability' | 'rpl') => {
    setWorking(`ai-${type}`);
    setError('');
    setNotice('');
    try {
      let saved = report;
      if (!saved) {
        const result = await saveSuitabilityRplReport(kind, learnerId, { ...form, revision: 0, finalise: false });
        saved = result.report;
        setReport(saved);
        setEvidence(saved.sourceSnapshot);
      }
      const result = await generateSuitabilityRplJustification(kind, learnerId, type);
      setReport(result.report);
      setForm(formFromReport(result.report));
      setNotice('AI draft added. Edit it as needed, then explicitly mark it reviewed.');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not generate the AI draft.');
    } finally {
      setWorking(null);
    }
  };

  const analyseRpl = async () => {
    setWorking('rpl-analysis');
    setError('');
    setNotice('');
    try {
      let saved = report;
      if (!saved) {
        const result = await saveSuitabilityRplReport(kind, learnerId, { ...form, revision: 0, finalise: false });
        saved = result.report;
        setReport(saved);
        setEvidence(saved.sourceSnapshot);
      }
      const result = await analyseSuitabilityRplEvidence(kind, learnerId);
      setRplAnalysis(result);
      setNotice('AI recommendation ready. Review the evidence and record the assessor decision separately.');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not analyse the RPL evidence.');
    } finally {
      setWorking(null);
    }
  };

  const addModule = (moduleId: string) => {
    const module = modules.find((item) => item.moduleId === moduleId);
    if (!module || selectedIds.has(moduleId)) return;
    setForm((current) => ({ ...current, reductions: [...current.reductions, {
      moduleId, moduleTitle: module.title, plannedHours: module.plannedHours ?? undefined,
      reducedHours: '', ksbCodes: [], evidence: '', assessorComments: '',
    }] }));
  };

  const updateReduction = (index: number, changes: Partial<SuitabilityRplReduction>) => {
    setForm((current) => ({ ...current, reductions: current.reductions.map((row, i) => i === index ? { ...row, ...changes } : row) }));
  };

  const pdfPayload = useCallback((): SuitabilityRplSavePayload => ({
    ...form,
    revision: report?.revision || 0,
    finalise: false,
  }), [form, report?.revision]);

  const openPdf = (blob: Blob) => {
    const url = URL.createObjectURL(blob);
    window.open(url, '_blank', 'noopener,noreferrer');
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  };

  const preview = async () => {
    if (currentIssued) {
      window.open(currentIssued.pdfUrl, '_blank', 'noopener,noreferrer');
      setPreviewedIssueId(currentIssued.issueId);
      return;
    }
    setWorking('preview');
    setError('');
    try {
      openPdf(await previewSuitabilityRplReport(kind, learnerId, pdfPayload()));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not prepare the report preview.');
    } finally { setWorking(null); }
  };

  const download = async () => {
    setWorking('download');
    setError('');
    try {
      const blob = await previewSuitabilityRplReport(kind, learnerId, pdfPayload());
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = reportFilename(evidence?.learner.name || learnerName, report?.reportReference);
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not prepare the report download.');
    } finally { setWorking(null); }
  };

  const issue = async () => {
    if (!report || report.status !== 'finalised' || issued || working) return;
    setWorking('issue');
    setError('');
    setNotice('');
    try {
      const result = await issueSuitabilityRplReport(kind, learnerId, {
        reportId: report.id,
        reportVersion: report.reportVersion,
        reportRevision: report.revision,
      });
      setNotice(result.alreadyIssued
        ? 'This finalised report is already issued and immutable. The trusted server PDF is available for electronic signing.'
        : 'Finalised report issued as an immutable trusted server PDF. It is ready for electronic signing.');
      setIssuedDocuments((await fetchSuitabilityRplIssuedDocuments(kind, learnerId)).documents);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not issue the finalised report.');
    } finally {
      setWorking(null);
    }
  };

  const signAdmin = async (signature: string) => {
    if (!signingIssue || !signConsent || previewedIssueId !== signingIssue.issueId) return;
    setWorking('sign-admin');
    try {
      await signSuitabilityRplReport(kind, learnerId, signingIssue.issueId, {
        role: 'admin', name: 'Assessor', signature, consent: true,
        consentText: 'I have reviewed the exact issued Suitability & RPL Report PDF and consent to signing this immutable version.',
      });
      setSigningIssue(null); setSignConsent(false); setPreviewedIssueId(null);
      setIssuedDocuments((await fetchSuitabilityRplIssuedDocuments(kind, learnerId)).documents);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not save the assessor signature.');
    } finally { setWorking(null); }
  };

  if (loading) return <div className={`${panel} flex min-h-52 items-center justify-center gap-3 p-8 text-sm text-foreground-500`}><Loader2 className="animate-spin" size={18} />Loading report evidence…</div>;
  if (!evidence) return <ErrorMessage>{error || 'The report evidence is unavailable.'}</ErrorMessage>;

  return (
    <div className="space-y-4">
      {error && <ErrorMessage>{error}</ErrorMessage>}
      {cvNeedsAttention && <section className={`${panel} border-amber-200 bg-amber-50 p-5`} aria-labelledby="cv-extraction-status"><p className={label}>CV evidence requires attention</p><h3 id="cv-extraction-status" className="mt-1 font-heading text-lg font-semibold text-amber-950">CV extraction: {cvExtractionStatus}</h3>{!locked && <><label className="mt-3 block"><span className={label}>Assessor acknowledgement reason</span><textarea className={`${input} min-h-20`} value={form.cvExtractionAcknowledgementReason || ''} onChange={(event) => setForm((current) => ({ ...current, cvExtractionAcknowledgementReason: event.target.value }))} /></label><label className="mt-3 flex items-start gap-3 text-sm text-amber-950"><input type="checkbox" className="mt-0.5" checked={!!form.cvExtractionAcknowledged} onChange={(event) => setForm((current) => ({ ...current, cvExtractionAcknowledged: event.target.checked }))} /><span>I acknowledge this CV cannot be used and approve proceeding with other evidence.</span></label></>}</section>}
      {notice && <p role="status" className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">{notice}</p>}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_19rem] xl:items-start">
        <main className="min-w-0 space-y-4">
          <section className="overflow-hidden rounded-2xl bg-primary-950 text-white shadow-[0_18px_40px_-24px_rgba(40,8,88,0.7)]" aria-labelledby="report-overview-title">
            <div className="p-5 sm:p-6">
              <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-primary-100">{evidence.programme.name || 'Programme not selected'}</p>
              <h2 id="report-overview-title" className="mt-2 font-heading text-2xl font-semibold tracking-tight text-white sm:text-[28px]">Suitability &amp; RPL Report</h2>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-primary-100">Review the learner&apos;s frozen evidence, record the human decision, then preview the report before finalising.</p>
              <div className="mt-4 flex flex-wrap gap-2 text-xs font-medium">
                <span className="rounded-full bg-white/10 px-3 py-1.5 ring-1 ring-white/20">{report ? `Version ${report.reportVersion}` : 'Unsaved draft'}</span>
                <span className="rounded-full bg-white/10 px-3 py-1.5 ring-1 ring-white/20">{reportStateLabel}</span>
                <span className="rounded-full bg-primary-100 px-3 py-1.5 text-primary-950">{signatureStateLabel}</span>
              </div>
              <div className="mt-6 flex flex-wrap items-center gap-2 border-t border-white/15 pt-4">
                <button type="button" className="inline-flex items-center justify-center gap-2 rounded-xl bg-white/10 px-3.5 py-2.5 text-sm font-semibold text-white ring-1 ring-white/25 transition hover:bg-white/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white disabled:opacity-50" onClick={preview} disabled={!!working}><FileSearch size={16} />Preview PDF</button>
                {currentIssued
                  ? <a className="inline-flex items-center justify-center gap-2 rounded-xl bg-white px-3.5 py-2.5 text-sm font-semibold text-primary-950 transition hover:bg-primary-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white" href={currentIssued.pdfUrl} download="Suitability-RPL-Report.pdf"><Download size={16} />Download</a>
                  : shouldShowDraftDownload(report?.status || null) && <button type="button" className="inline-flex items-center justify-center gap-2 rounded-xl bg-white px-3.5 py-2.5 text-sm font-semibold text-primary-950 transition hover:bg-primary-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white disabled:opacity-50" onClick={download} disabled={!!working}><Download size={16} />Download draft</button>}
                {currentIssued?.signingEnabled && !currentIssued.signatures.admin.signed && <button type="button" className="inline-flex items-center justify-center gap-2 rounded-xl bg-primary-600 px-3.5 py-2.5 text-sm font-semibold text-white transition hover:bg-primary-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white" onClick={() => { setSigningIssue(currentIssued); setSignConsent(false); }}><FileCheck2 size={16} />Sign as assessor</button>}
                {locked && !issued && <button type="button" className="inline-flex items-center justify-center gap-2 rounded-xl bg-primary-600 px-3.5 py-2.5 text-sm font-semibold text-white transition hover:bg-primary-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white disabled:opacity-50" onClick={issue} disabled={!!working}><FileCheck2 size={16} />{reportIssueButtonLabel(locked, issued, working === 'issue')}</button>}
                {!locked && <><span className="mx-1 hidden h-7 w-px bg-white/20 sm:block" aria-hidden="true" /><button type="button" className="inline-flex items-center justify-center gap-2 rounded-xl bg-white/10 px-3.5 py-2.5 text-sm font-semibold text-white ring-1 ring-white/25 transition hover:bg-white/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white disabled:opacity-50" disabled={!!working} onClick={() => save(false)}><Save size={15} />{working === 'save' ? 'Saving…' : 'Save draft'}</button><button type="button" className="inline-flex items-center justify-center gap-2 rounded-xl bg-primary-600 px-3.5 py-2.5 text-sm font-semibold text-white transition hover:bg-primary-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white disabled:opacity-50" disabled={!!working} onClick={() => save(true)}><CheckCircle2 size={16} />{working === 'finalise' ? 'Finalising…' : 'Finalise report'}</button></>}
                {locked && <span className="mx-1 hidden h-7 w-px bg-white/20 sm:block" aria-hidden="true" />}
                {locked && <button type="button" className="inline-flex items-center justify-center gap-2 rounded-xl bg-white px-3.5 py-2.5 text-sm font-semibold text-primary-950 transition hover:bg-primary-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white disabled:opacity-50" onClick={() => void createAmendedVersion()} disabled={!!working}><Plus size={16} />Create amended version</button>}
              </div>
            </div>
          </section>

          {signingIssue && currentIssued && signingIssue.issueId === currentIssued.issueId && <section className={`${panel} space-y-4 p-5`} aria-label="Sign issued Suitability and RPL Report">
            <div className="flex items-start justify-between gap-3"><div><h3 className="font-heading text-lg font-semibold text-foreground-900">Sign issued report</h3><p className="mt-1 text-sm text-foreground-500">Review and consent to this exact immutable PDF before adding the assessor signature.</p></div><LockKeyhole className="shrink-0 text-primary-600" size={20} /></div>
            <div className="rounded-xl bg-background-100 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-sm font-semibold text-foreground-900">1. Review the issued PDF</p><p className="mt-1 text-xs text-foreground-500">The consent checkbox is enabled only after this exact issued version has been opened.</p></div><button type="button" className={secondaryButton} onClick={() => { window.open(currentIssued.pdfUrl, '_blank', 'noopener,noreferrer'); setPreviewedIssueId(currentIssued.issueId); }}><FileSearch size={15} />{previewedIssueId === currentIssued.issueId ? 'Open issued PDF again' : 'Preview exact issued PDF'}</button></div>
            </div>
            <div className="rounded-xl bg-background-100 p-4">
              <p className="text-sm font-semibold text-foreground-900">2. Confirm consent</p>
              <label className={`mt-2 flex items-start gap-2 text-sm ${previewedIssueId === currentIssued.issueId ? 'cursor-pointer text-foreground-800' : 'cursor-not-allowed text-foreground-500'}`}><input type="checkbox" aria-describedby="assessor-consent-help" checked={signConsent} disabled={previewedIssueId !== currentIssued.issueId || working === 'sign-admin'} onChange={event => setSignConsent(event.target.checked)} className="mt-0.5 h-4 w-4 accent-primary-700" />I have reviewed this exact report version and consent to signing it.</label>
              <p id="assessor-consent-help" className="mt-2 text-xs text-foreground-500">{previewedIssueId === currentIssued.issueId ? 'The exact issued PDF has been opened. You can now confirm consent.' : 'Open the exact issued PDF above to enable consent.'}</p>
            </div>
            {signConsent ? <div><p className="mb-2 text-sm font-semibold text-foreground-900">3. Add the assessor signature</p><SignaturePad signatoryName="Assessor" onCommit={signAdmin} onCancel={() => { setSigningIssue(null); setSignConsent(false); }} /></div> : <p className="rounded-xl border border-dashed border-foreground-300 px-4 py-3 text-sm text-foreground-500">Confirm consent to continue to signature capture.</p>}
          </section>}

          <div className="grid gap-4 lg:grid-cols-2">
        <section className={`${panel} min-w-0 p-5`}>
          <div className="flex items-start justify-between gap-4">
            <div><p className={label}>Programme decision</p><h3 className="mt-1 font-heading text-lg font-semibold">Suitability assessment</h3></div>
            <span className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-[10px] font-semibold ${form.suitabilityReviewed ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800'}`}>{form.suitabilityReviewed ? <Check size={12} /> : <Clock3 size={12} />}{form.suitabilityReviewed ? 'Approved' : 'Review required'}</span>
          </div>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3"><span className={label}>Assessment justification</span><button type="button" className={secondaryButton} disabled={locked || !aiEnabled || !!working} title={aiEnabled ? '' : 'AI drafting is disabled in this environment'} onClick={() => generate('suitability')}><Sparkles size={15} />{working === 'ai-suitability' ? 'Drafting…' : 'Generate AI draft'}</button></div>
          {!aiEnabled && <p className="mt-2 text-xs text-foreground-500"><Bot className="mr-1 inline" size={13} />AI drafting is disabled by default. Manual text remains available.</p>}
          <textarea className={`${input} min-h-36 resize-y`} disabled={locked} value={form.suitabilityJustification} onChange={(event) => setForm((current) => ({ ...current, suitabilityJustification: event.target.value, suitabilityReviewed: false }))} />
          <label className="mt-3 flex items-start gap-3 rounded-xl bg-background-100 p-3 text-sm text-foreground-800"><input type="checkbox" className="mt-0.5 h-4 w-4 accent-primary-700" disabled={locked || !form.suitabilityJustification.trim()} checked={form.suitabilityReviewed} onChange={(event) => setForm((current) => ({ ...current, suitabilityReviewed: event.target.checked }))} /><span><strong className="font-semibold">I accept this justification and approve it.</strong><br /><span className="text-xs text-foreground-500">If an AI draft is not acceptable, edit or replace it first. Editing clears approval.</span></span></label>
        </section>

        <section className={`${panel} min-w-0 p-5`}>
          <div className="flex flex-wrap items-start justify-between gap-3"><div><p className={label}>Prior learning decision</p><h3 className="mt-1 font-heading text-lg font-semibold">RPL assessment</h3></div><span className={`rounded-full px-2.5 py-1 text-[10px] font-semibold ${decision === 'undecided' ? 'bg-amber-100 text-amber-800' : 'bg-primary-100 text-primary-900'}`}>{rplDecisionLabel}</span></div>
          <div className="mt-4 rounded-xl border border-primary-100 bg-primary-50/70 p-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div><p className={label}>Advisory evidence review</p><p className="mt-1 text-sm font-semibold text-primary-950">AI Recommendation — Assessor Review Required</p></div>
              <button type="button" className={secondaryButton} disabled={locked || !aiEnabled || !!working} title={aiEnabled ? 'Analyse the current evidence before recording the decision' : 'AI drafting is disabled in this environment'} onClick={() => void analyseRpl()}><FileSearch size={15} />{working === 'rpl-analysis' ? 'Analysing…' : 'Analyse RPL Evidence with AI'}</button>
            </div>
            {!aiEnabled && <p className="mt-2 text-xs text-foreground-500">AI recommendations are disabled by default. The assessor decision remains available manually.</p>}
          </div>
          {rplAnalysis && <div className={"mt-3 rounded-xl border p-4 text-sm " + (analysisStale ? "border-amber-200 bg-amber-50 text-amber-950" : "border-foreground-200 bg-background-100 text-foreground-800")}>
            {analysisStale ? <p className="font-semibold">This recommendation is out of date because the evidence version changed. Run the analysis again.</p> : <>
              <p className={label}>Suggested prior learning outcome</p>
              <p className="mt-1 text-base font-semibold text-primary-950">{simpleRplOutcome(rplAnalysis.recommendation.overallOutcome)}</p>
              <p className="mt-3 leading-6">{shortRplReason(rplAnalysis.recommendation.reasoning)}</p>
              <p className={label + " mt-4"}>Modules recommended for removal</p>
              {rplAnalysis.recommendation.modules.filter((item) => item.wholeModuleReductionRecommended).length === 0 ? (
                <p className="mt-1 text-sm">No module reductions recommended</p>
              ) : (
                <ul className="mt-2 list-disc space-y-1 pl-5">{rplAnalysis.recommendation.modules.filter((item) => item.wholeModuleReductionRecommended).map((item) => <li key={item.moduleId} className="font-semibold text-primary-950">{item.moduleName}</li>)}</ul>
              )}
            </>}
          </div>}
          <fieldset className="mt-5 space-y-2" disabled={locked}>
            <legend className={label}>Assessment outcome</legend>
            {[
              ['none', 'No relevant prior learning'],
              ['relevant-with-reduction', 'Relevant prior learning — training reduction required'],
            ].map(([id, text]) => <label key={id} className={`flex cursor-pointer gap-3 rounded-xl border p-3 text-sm ${decision === id ? 'border-primary-300 bg-primary-50 text-primary-950' : 'border-foreground-200 text-foreground-700'}`}><input type="radio" name="rpl-decision" className="mt-0.5 accent-primary-700" checked={decision === id} onChange={() => setDecision(id as Decision)} />{text}</label>)}
          </fieldset>
          {decision === 'legacy-relevant-no-reduction' && <p className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">This historical report used the previous “relevant prior learning, no reduction” outcome. It is preserved unchanged; choose one of the two current outcomes to reassess this draft.</p>}
          {decision === 'none' && <div className="mt-4 rounded-xl border border-primary-100 bg-primary-50 px-4 py-3 text-sm text-primary-950"><strong>Full standard Training Plan</strong><p className="mt-1 text-xs text-primary-800">All authoritative modules remain included. Planned hours, duration and programme cost are unchanged.</p></div>}
        </section>
      </div>

      {decision === 'relevant-with-reduction' && <section className={`${panel} p-5`} aria-labelledby="training-plan-reductions-title">
        <div className="flex flex-wrap items-end justify-between gap-4"><div><p className={label}>Authoritative Training Plan</p><h3 id="training-plan-reductions-title" className="mt-1 font-heading text-lg font-semibold">Module reductions</h3><p className="mt-1 max-w-2xl text-sm text-foreground-500">This records the assessment only. It does not change the Training Plan or funding calculations.</p></div><label className="w-full sm:w-auto"><span className={label}>Add module</span><select aria-label="Add Training Plan module" className={`${input} w-full sm:min-w-64`} disabled={locked || !evidence.trainingPlan.available} value="" onChange={(event) => addModule(event.target.value)}><option value="">Select a saved module…</option>{modules.filter((module) => !selectedIds.has(module.moduleId)).map((module) => <option key={module.moduleId} value={module.moduleId}>{module.title} · {module.plannedHours ?? 'Not available'} hours</option>)}</select></label></div>
        {!evidence.trainingPlan.available && <div className="mt-4"><ErrorMessage>{evidence.trainingPlan.message}</ErrorMessage></div>}
        <div className="mt-4 space-y-3">{form.reductions.map((row, index) => {
          const module = modules.find((item) => item.moduleId === row.moduleId);
          return <article key={row.moduleId} className="rounded-xl bg-background-100 p-4"><div className="flex items-start justify-between gap-3"><div><h4 className="font-semibold text-foreground-900">{module?.title || row.moduleTitle}</h4><p className="mt-1 text-xs text-foreground-500">Planned: {module?.plannedHours ?? row.plannedHours ?? 'Not available'} hours · Module ID: {row.moduleId}</p></div><button type="button" aria-label={`Remove ${module?.title || row.moduleTitle}`} disabled={locked} className="rounded-lg p-2 text-red-600 transition hover:bg-red-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300 disabled:opacity-50" onClick={() => setForm((current) => ({ ...current, reductions: current.reductions.filter((_, i) => i !== index) }))}><Trash2 size={16} /></button></div><div className="mt-3"><label><span className={label}>Hours to reduce</span><input className={input} type="number" min="0.01" step="0.25" max={module?.plannedHours ?? undefined} disabled={locked} value={row.reducedHours} onChange={(event) => updateReduction(index, { reducedHours: event.target.value === '' ? '' : Number(event.target.value) })} /></label></div>{module?.ksbCodes.length ? <fieldset className="mt-3"><legend className={label}>Mapped KSBs (optional)</legend><div className="mt-2 flex flex-wrap gap-2">{module.ksbCodes.map((code) => <label key={code} className="rounded-lg border border-foreground-200 bg-white px-2.5 py-1.5 text-xs"><input type="checkbox" disabled={locked} className="mr-1.5 accent-primary-700" checked={row.ksbCodes.includes(code)} onChange={(event) => updateReduction(index, { ksbCodes: event.target.checked ? [...row.ksbCodes, code] : row.ksbCodes.filter((item) => item !== code) })} />{code}</label>)}</div></fieldset> : null}</article>;
        })}{!form.reductions.length && <div className="rounded-xl border border-dashed border-foreground-300 px-4 py-8 text-center text-sm text-foreground-500"><Plus className="mx-auto mb-2" size={19} />Select one or more modules from the saved Training Plan.</div>}</div>
        <div className="mt-4 grid gap-2 sm:grid-cols-3">{[['Original hours', evidence.trainingPlan.plannedHours ?? 'Not available'], ['Approved reduction', totalReduction], ['Remaining hours', adjustedHours ?? 'Not available']].map(([heading, value]) => <div key={heading} className="rounded-xl bg-primary-50 p-3"><p className={label}>{heading}</p><p className="mt-1 text-lg font-semibold text-primary-900">{value}</p></div>)}</div>
        <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-900"><strong>Programme cost</strong><p className="mt-1">Authorised pricing/funding rules and the original agreed price are not available in this evidence snapshot. Cost adjustment remains pending authorised assessment; no proportional reduction is calculated here.</p></div>
      </section>}

      <section className={`${panel} p-5`}>
        <div className="flex flex-wrap items-center justify-between gap-3"><div><p className={label}>Editable narrative · admin decision remains separate</p><h3 className="mt-1 font-heading text-lg font-semibold">RPL justification</h3></div><button type="button" className={secondaryButton} disabled={locked || !aiEnabled || !report || decision === 'undecided' || !!working} title={aiEnabled ? '' : 'AI drafting is disabled in this environment'} onClick={() => generate('rpl')}><Sparkles size={15} />{working === 'ai-rpl' ? 'Drafting…' : 'Generate AI draft'}</button></div>
        <textarea className={`${input} min-h-32 resize-y`} disabled={locked} value={form.rplJustification} onChange={(event) => setForm((current) => ({ ...current, rplJustification: event.target.value, rplReviewed: false }))} />
        <label className="mt-3 flex items-start gap-3 rounded-xl bg-background-100 p-3 text-sm text-foreground-800"><input type="checkbox" className="mt-0.5 h-4 w-4 accent-primary-700" disabled={locked || !form.rplJustification.trim()} checked={form.rplReviewed} onChange={(event) => setForm((current) => ({ ...current, rplReviewed: event.target.checked }))} /><span><strong className="font-semibold">I accept this RPL justification and approve it.</strong><br /><span className="text-xs text-foreground-500">If an AI draft is not acceptable, edit or replace it first. AI never chooses the RPL outcome, modules or hours.</span></span></label>
      </section>

          {locked && <div className="flex items-start gap-3 rounded-xl border border-primary-200 bg-primary-50 px-4 py-3 text-sm text-primary-950"><LockKeyhole className="mt-0.5 shrink-0" size={17} /><span>This finalised report is read-only. Its evidence snapshot and approved assessment will not change when learner or programme data changes.</span></div>}
        </main>

        <aside className="min-w-0 space-y-4 xl:sticky xl:top-4" aria-label="Report status and version history">
          <section className={`${panel} p-4 sm:p-5`} aria-labelledby="report-status-title">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 id="report-status-title" className="font-heading text-lg font-semibold text-foreground-900">Report status</h3>
              <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold ${locked ? 'bg-primary-100 text-primary-900' : 'bg-amber-100 text-amber-900'}`}>{locked ? <LockKeyhole size={12} /> : <Clock3 size={12} />}{reportStateLabel}</span>
            </div>
            {currentIssued && <p className="mt-2 text-xs leading-5 text-foreground-500">Version {currentIssued.reportVersion} · Revision {currentIssued.reportRevision} · Issued {currentIssued.issuedAt ? new Date(currentIssued.issuedAt).toLocaleDateString('en-GB') : 'date unavailable'}</p>}
            <div className="mt-4">
              <p className={label}>Signatures</p>
              <div className="mt-2 space-y-1.5">
                {signatureRoles.map(([role, roleLabel]) => {
                  const signed = !!currentIssued?.signatures[role].signed;
                  return <div key={role} className="flex items-center justify-between gap-3 rounded-xl bg-background-100 px-3 py-2.5"><span className="flex min-w-0 items-center gap-2 text-sm font-medium text-foreground-800"><UserRound className="shrink-0 text-primary-600" size={16} />{roleLabel}</span><span className={`rounded-full px-2.5 py-1 text-[10px] font-semibold ${signed ? 'bg-emerald-100 text-emerald-800' : currentIssued ? 'bg-amber-100 text-amber-800' : 'bg-foreground-100 text-foreground-600'}`}>{signed ? 'Signed' : currentIssued ? 'Pending' : 'Not issued'}</span></div>;
                })}
              </div>
            </div>
          </section>

          <section className={`${panel} p-4 sm:p-5`} aria-labelledby="version-history-title">
            <div className="flex items-center gap-2"><History className="text-primary-600" size={18} /><h3 id="version-history-title" className="font-heading text-lg font-semibold text-foreground-900">Version history</h3></div>
            <div className="mt-3 space-y-2">
              {history.length === 0 && <p className="rounded-xl bg-background-100 px-3 py-4 text-sm text-foreground-500">No saved versions yet.</p>}
              {history.map((item) => {
                const document = issuedDocuments.find((candidate) => candidate.reportId === item.id || candidate.reportVersion === item.reportVersion);
                const isCurrent = item.id === report?.id;
                return <article key={item.id} aria-current={isCurrent ? 'true' : undefined} className={`rounded-xl border p-3 ${isCurrent ? 'border-primary-200 bg-primary-50/80' : 'border-foreground-200 bg-white'}`}>
                  <div className="flex items-start justify-between gap-2"><div className="min-w-0"><p className="text-sm font-semibold text-foreground-900">Version {item.reportVersion} <span className="font-normal text-foreground-500">{item.status === 'finalised' ? 'Finalised' : 'Draft'}</span></p>{isCurrent && <p className="mt-0.5 text-[10px] font-semibold uppercase tracking-wide text-primary-700">Current version</p>}{item.finalisedAt && <p className="mt-1 text-xs text-foreground-500">Finalised {new Date(item.finalisedAt).toLocaleDateString('en-GB')}</p>}</div>{document && <a className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-foreground-200 bg-white px-2.5 py-2 text-xs font-semibold text-foreground-700 transition hover:border-primary-300 hover:text-primary-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-300" href={document.pdfUrl} target="_blank" rel="noreferrer"><FileSearch size={13} />View PDF</a>}</div>
                  {item.amendmentReason && <p className="mt-2 text-xs leading-5 text-foreground-500">{item.amendmentReason}</p>}
                </article>;
              })}
            </div>
          </section>
        </aside>
      </div>

    </div>
  );
}
