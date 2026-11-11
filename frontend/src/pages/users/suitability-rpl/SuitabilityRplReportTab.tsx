import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Bot, CheckCircle2, Download, FileCheck2, FileSearch, Loader2, LockKeyhole,
  Plus, RefreshCw, Save, ShieldCheck, Sparkles, Trash2,
} from 'lucide-react';
import type { LearnerKind } from '@/api/learnerDetail';
import {
  fetchSuitabilityRplReport,
  generateSuitabilityRplJustification,
  saveSuitabilityRplReport,
  type SuitabilityRplEvidence,
  type SuitabilityRplReduction,
  type SuitabilityRplReport,
  type SuitabilityRplSavePayload,
} from '@/api/suitabilityRplReport';
import {
  buildSuitabilityRplReportPdf,
  loadSuitabilityRplReportAssets,
  suitabilityRplPopulatedReportFilename,
} from '@/lib/suitabilityRplReportPdf';

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

function radarSummary(evidence: SuitabilityRplEvidence) {
  if (evidence.skillsRadar.summary?.length) {
    const saved = new Map(evidence.skillsRadar.summary.map((group) => [group.label, group]));
    return ['Knowledge', 'Skills', 'Behaviours'].map((label) => saved.get(label) || ({ label, score: null, answered: 0, total: 0 }));
  }
  const preferred: Record<string, string> = { knowledge: 'Knowledge', skill: 'Skills', skills: 'Skills', behaviour: 'Behaviours', behavior: 'Behaviours', behaviours: 'Behaviours', behaviors: 'Behaviours' };
  const groups = new Map<string, { label: string; scoreTotal: number; answered: number; total: number }>();
  evidence.skillsRadar.items.forEach((item) => {
    const label = preferred[item.kind.toLowerCase()] || item.kind || 'Uncategorised';
    const group = groups.get(label) || { label, scoreTotal: 0, answered: 0, total: 0 };
    group.total += 1;
    if (item.score != null) { group.scoreTotal += item.score; group.answered += 1; }
    groups.set(label, group);
  });
  return ['Knowledge', 'Skills', 'Behaviours'].map((label) => {
    const group = groups.get(label) || { label, scoreTotal: 0, answered: 0, total: 0 };
    return { label, score: group.answered ? Math.round((group.scoreTotal / group.answered) * 10) / 10 : null, answered: group.answered, total: group.total };
  });
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
  const [historyCount, setHistoryCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
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
      setHistoryCount(state.history.length);
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
  const decision = decisionOf(form);
  const cvStatus = evidence?.evidence.cv.status || 'not_provided';
  const cvExtractionStatus = evidence?.evidence.cv.extractionStatus || (cvStatus === 'provided' ? 'pending' : 'not_provided');
  const cvNeedsAttention = ['failed', 'unsupported', 'stale'].includes(cvExtractionStatus);
  const modules = evidence?.trainingPlan.modules || [];
  const selectedIds = useMemo(() => new Set(form.reductions.map((row) => row.moduleId)), [form.reductions]);
  const totalReduction = form.reductions.reduce((sum, row) => sum + (Number(row.reducedHours) || 0), 0);
  const adjustedHours = evidence?.trainingPlan.plannedHours == null ? null : evidence.trainingPlan.plannedHours - totalReduction;

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
      setError(cause instanceof Error ? cause.message : 'Could not save the report.');
    } finally {
      setWorking(null);
    }
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

  const makePdf = useCallback(async () => {
    if (!evidence) return null;
    const assets = await loadSuitabilityRplReportAssets();
    const qualification = evidence.evidence.qualifications.items
      .map((item) => `${item.label}${item.source ? ` [${item.source}]` : ''}: ${item.value}`)
      .join('; ');
    return buildSuitabilityRplReportPdf({
      status: report?.status || 'draft',
      learner: {
        fullName: evidence.learner.name || learnerName,
        reference: evidence.learner.reference,
        employer: evidence.learner.employer,
        jobTitle: evidence.learner.jobTitle,
        responsibilities: evidence.learner.responsibilities.join('\n'),
        programme: evidence.programme.name,
        level: evidence.programme.level,
        assessmentDate: evidence.capturedAt,
      },
      programmeUnderstanding: evidence.programmeUnderstanding.map((item) => ({ question: item.question, answer: item.answer })),
      programmeSuitability: {
        pathway: evidence.programmeSuitability.pathway,
        roleEvidence: evidence.learner.responsibilities.join('\n'),
        evidence: evidence.programmeSuitability.responses?.map((item) => `${item.question}: ${item.answers.join(', ')}`).join('\n') || '',
        responses: evidence.programmeSuitability.responses?.map((item) => ({
          group: item.group,
          question: item.question,
          answers: item.answers,
          selectedOptions: item.selectedOptions,
        })),
        outcome: form.suitabilityOutcome,
        justification: form.suitabilityJustification,
        reviewed: form.suitabilityReviewed,
      },
      skillsRadar: evidence.skillsRadar.items.map((item) => ({ label: [item.code, item.description].filter(Boolean).join(': '), score: item.score ?? '' })),
      skillsRadarSummary: radarSummary(evidence),
      evidence: {
        cv: evidence.evidence.cv.status === 'provided' ? evidence.evidence.cv.experience || 'Provided' : 'Not provided',
        qualifications: qualification || (evidence.evidence.qualifications.status === 'provided' ? 'Provided' : 'Not provided'),
        plr: evidence.evidence.plr.message,
      },
      rpl: {
        relevantPriorLearning: form.relevantPriorLearning,
        reductionJustified: form.reductionJustified,
        evidenceReview: form.assessorEvidenceReview,
        noReductionReason: form.noReductionReason,
        justification: form.rplJustification,
        reviewed: form.rplReviewed,
        rows: form.reductions.map((row) => ({ area: row.moduleTitle, ksb: row.ksbCodes.join(', '), evidence: row.evidence, hours: row.reducedHours })),
        originalHours: evidence.trainingPlan.plannedHours ?? '',
        reductionHours: totalReduction || '',
        adjustedHours: adjustedHours ?? '',
      },
      finalAssessment: {
        outcome: form.suitabilityOutcome,
        rationale: form.finalSummary,
        reportReference: report?.reportReference || 'DRAFT - generated on preview',
        version: String(report?.reportVersion || 1),
        approvalDate: report?.finalisedAt || '',
      },
    }, assets);
  }, [adjustedHours, evidence, form, learnerName, report, totalReduction]);

  const preview = async () => {
    setWorking('preview');
    try {
      const pdf = await makePdf();
      if (!pdf) return;
      const url = URL.createObjectURL(pdf.output('blob'));
      window.open(url, '_blank', 'noopener,noreferrer');
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } finally { setWorking(null); }
  };

  const download = async () => {
    setWorking('download');
    try {
      const pdf = await makePdf();
      pdf?.save(suitabilityRplPopulatedReportFilename(evidence?.learner.name || learnerName, report?.reportReference));
    } finally { setWorking(null); }
  };

  if (loading) return <div className={`${panel} flex min-h-52 items-center justify-center gap-3 p-8 text-sm text-foreground-500`}><Loader2 className="animate-spin" size={18} />Loading report evidence…</div>;
  if (!evidence) return <ErrorMessage>{error || 'The report evidence is unavailable.'}</ErrorMessage>;

  return (
    <div className="space-y-4">
      <section className="overflow-hidden rounded-2xl border border-primary-800 bg-primary-900 text-white">
        <div className="grid gap-5 p-5 sm:p-6 lg:grid-cols-[1fr_auto] lg:items-end">
          <div>
            <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-primary-100">
              <span>Assessment record</span><span aria-hidden="true">•</span><span>{evidence.programme.name || 'Programme not selected'}</span>
            </div>
            <h2 className="font-heading text-2xl font-semibold tracking-tight text-white">Suitability &amp; RPL Report</h2>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-primary-100">Review the learner’s frozen evidence, record the human decision, then preview the purple report before finalising.</p>
            <div className="mt-4 flex flex-wrap gap-2 text-xs">
              <span className="rounded-full bg-white/10 px-3 py-1.5 ring-1 ring-white/20">{report ? `Version ${report.reportVersion}` : 'Unsaved draft'}</span>
              <span className="rounded-full bg-white/10 px-3 py-1.5 ring-1 ring-white/20">{report?.status === 'finalised' ? 'Finalised & locked' : 'Draft'}</span>
              {historyCount > 0 && <span className="rounded-full bg-white/10 px-3 py-1.5 ring-1 ring-white/20">{historyCount} report record{historyCount === 1 ? '' : 's'}</span>}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="inline-flex items-center gap-2 rounded-xl bg-white/10 px-3.5 py-2.5 text-sm font-semibold text-white ring-1 ring-white/25 hover:bg-white/15 disabled:opacity-50" onClick={preview} disabled={!!working}><FileSearch size={16} />Preview PDF</button>
            <button type="button" className="inline-flex items-center gap-2 rounded-xl bg-white px-3.5 py-2.5 text-sm font-semibold text-primary-900 hover:bg-primary-50 disabled:opacity-50" onClick={download} disabled={!!working}><Download size={16} />Download draft</button>
          </div>
        </div>
      </section>

      {error && <ErrorMessage>{error}</ErrorMessage>}
      {cvNeedsAttention && <section className={`${panel} border-amber-200 bg-amber-50 p-5`} aria-labelledby="cv-extraction-status"><p className={label}>CV evidence requires attention</p><h3 id="cv-extraction-status" className="mt-1 font-heading text-lg font-semibold text-amber-950">CV extraction: {cvExtractionStatus}</h3>{!locked && <><label className="mt-3 block"><span className={label}>Assessor acknowledgement reason</span><textarea className={`${input} min-h-20`} value={form.cvExtractionAcknowledgementReason || ''} onChange={(event) => setForm((current) => ({ ...current, cvExtractionAcknowledgementReason: event.target.value }))} /></label><label className="mt-3 flex items-start gap-3 text-sm text-amber-950"><input type="checkbox" className="mt-0.5" checked={!!form.cvExtractionAcknowledged} onChange={(event) => setForm((current) => ({ ...current, cvExtractionAcknowledged: event.target.checked }))} /><span>I acknowledge this CV cannot be used and approve proceeding with other evidence.</span></label></>}</section>}
      {notice && <p role="status" className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">{notice}</p>}
      {locked && <div className="flex items-start gap-3 rounded-xl border border-primary-200 bg-primary-50 px-4 py-3 text-sm text-primary-900"><LockKeyhole className="mt-0.5 shrink-0" size={17} /><span>This finalised report is read-only. Its evidence snapshot and approved assessment will not change when learner or programme data changes.</span></div>}

      <div className="grid gap-4 xl:grid-cols-2">
        <section className={`${panel} p-5`}>
          <div className="flex items-start justify-between gap-4">
            <div><p className={label}>Programme decision</p><h3 className="mt-1 font-heading text-lg font-semibold">Suitability assessment</h3></div>
            <ShieldCheck className="text-primary-600" size={22} />
          </div>
          <div className="mt-4 flex items-center justify-between gap-3"><span className={label}>Editable justification · admin decision required</span><button type="button" className={secondaryButton} disabled={locked || !aiEnabled || !!working} title={aiEnabled ? '' : 'AI drafting is disabled in this environment'} onClick={() => generate('suitability')}><Sparkles size={15} />{working === 'ai-suitability' ? 'Drafting…' : 'Generate AI draft'}</button></div>
          {!aiEnabled && <p className="mt-2 text-xs text-foreground-500"><Bot className="mr-1 inline" size={13} />AI drafting is disabled by default. Manual text remains available.</p>}
          <textarea className={`${input} min-h-36 resize-y`} disabled={locked} value={form.suitabilityJustification} onChange={(event) => setForm((current) => ({ ...current, suitabilityJustification: event.target.value, suitabilityReviewed: false }))} />
          <label className="mt-3 flex items-start gap-3 rounded-xl bg-background-100 p-3 text-sm text-foreground-800"><input type="checkbox" className="mt-0.5 h-4 w-4 accent-primary-700" disabled={locked || !form.suitabilityJustification.trim()} checked={form.suitabilityReviewed} onChange={(event) => setForm((current) => ({ ...current, suitabilityReviewed: event.target.checked }))} /><span><strong className="font-semibold">I accept this justification and approve it.</strong><br /><span className="text-xs text-foreground-500">If an AI draft is not acceptable, edit or replace it first. Editing clears approval.</span></span></label>
        </section>

        <section className={`${panel} p-5`}>
          <div className="flex items-start justify-between gap-4"><div><p className={label}>Prior learning decision</p><h3 className="mt-1 font-heading text-lg font-semibold">RPL assessment</h3></div><FileCheck2 className="text-primary-600" size={22} /></div>
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

      {decision === 'relevant-with-reduction' && <section className={`${panel} p-5`}>
        <div className="flex flex-wrap items-end justify-between gap-4"><div><p className={label}>Authoritative source only</p><h3 className="mt-1 font-heading text-lg font-semibold">Training Plan module reductions</h3><p className="mt-1 text-sm text-foreground-500">This records the assessment only. It does not change the Training Plan or funding calculations.</p></div><label><span className={label}>Add module</span><select aria-label="Add Training Plan module" className={`${input} min-w-64`} disabled={locked || !evidence.trainingPlan.available} value="" onChange={(event) => addModule(event.target.value)}><option value="">Select a saved module…</option>{modules.filter((module) => !selectedIds.has(module.moduleId)).map((module) => <option key={module.moduleId} value={module.moduleId}>{module.title} · {module.plannedHours ?? 'Not available'} hours</option>)}</select></label></div>
        {!evidence.trainingPlan.available && <ErrorMessage>{evidence.trainingPlan.message}</ErrorMessage>}
        <div className="mt-4 space-y-3">{form.reductions.map((row, index) => {
          const module = modules.find((item) => item.moduleId === row.moduleId);
          return <article key={row.moduleId} className="rounded-xl border border-foreground-200 bg-background-100 p-4"><div className="flex items-start justify-between gap-3"><div><h4 className="font-semibold text-foreground-900">{module?.title || row.moduleTitle}</h4><p className="mt-1 text-xs text-foreground-500">Planned: {module?.plannedHours ?? row.plannedHours ?? 'Not available'} hours · Module ID: {row.moduleId}</p></div><button type="button" aria-label={`Remove ${module?.title || row.moduleTitle}`} disabled={locked} className="rounded-lg p-2 text-red-600 hover:bg-red-50 disabled:opacity-50" onClick={() => setForm((current) => ({ ...current, reductions: current.reductions.filter((_, i) => i !== index) }))}><Trash2 size={16} /></button></div><div className="mt-3"><label><span className={label}>Hours to reduce</span><input className={input} type="number" min="0.01" step="0.25" max={module?.plannedHours ?? undefined} disabled={locked} value={row.reducedHours} onChange={(event) => updateReduction(index, { reducedHours: event.target.value === '' ? '' : Number(event.target.value) })} /></label></div>{module?.ksbCodes.length ? <fieldset className="mt-3"><legend className={label}>Mapped KSBs (optional)</legend><div className="mt-2 flex flex-wrap gap-2">{module.ksbCodes.map((code) => <label key={code} className="rounded-lg border border-foreground-200 bg-white px-2.5 py-1.5 text-xs"><input type="checkbox" disabled={locked} className="mr-1.5 accent-primary-700" checked={row.ksbCodes.includes(code)} onChange={(event) => updateReduction(index, { ksbCodes: event.target.checked ? [...row.ksbCodes, code] : row.ksbCodes.filter((item) => item !== code) })} />{code}</label>)}</div></fieldset> : null}</article>;
        })}{!form.reductions.length && <div className="rounded-xl border border-dashed border-foreground-300 px-4 py-8 text-center text-sm text-foreground-500"><Plus className="mx-auto mb-2" size={19} />Select one or more modules from the saved Training Plan.</div>}</div>
        <div className="mt-4 grid gap-2 sm:grid-cols-3">{[['Original hours', evidence.trainingPlan.plannedHours ?? 'Not available'], ['Approved reduction', totalReduction], ['Remaining hours', adjustedHours ?? 'Not available']].map(([heading, value]) => <div key={heading} className="rounded-xl bg-primary-50 p-3"><p className={label}>{heading}</p><p className="mt-1 text-lg font-semibold text-primary-900">{value}</p></div>)}</div>
        <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-900"><strong>Programme cost</strong><p className="mt-1">Authorised pricing/funding rules and the original agreed price are not available in this evidence snapshot. Cost adjustment remains pending authorised assessment; no proportional reduction is calculated here.</p></div>
      </section>}

      <section className={`${panel} p-5`}>
        <div className="flex flex-wrap items-center justify-between gap-3"><div><p className={label}>Editable narrative · admin decision remains separate</p><h3 className="mt-1 font-heading text-lg font-semibold">RPL justification</h3></div><button type="button" className={secondaryButton} disabled={locked || !aiEnabled || !report || decision === 'undecided' || !!working} title={aiEnabled ? '' : 'AI drafting is disabled in this environment'} onClick={() => generate('rpl')}><Sparkles size={15} />{working === 'ai-rpl' ? 'Drafting…' : 'Generate AI draft'}</button></div>
        <textarea className={`${input} min-h-32 resize-y`} disabled={locked} value={form.rplJustification} onChange={(event) => setForm((current) => ({ ...current, rplJustification: event.target.value, rplReviewed: false }))} />
        <label className="mt-3 flex items-start gap-3 rounded-xl bg-background-100 p-3 text-sm text-foreground-800"><input type="checkbox" className="mt-0.5 h-4 w-4 accent-primary-700" disabled={locked || !form.rplJustification.trim()} checked={form.rplReviewed} onChange={(event) => setForm((current) => ({ ...current, rplReviewed: event.target.checked }))} /><span><strong className="font-semibold">I accept this RPL justification and approve it.</strong><br /><span className="text-xs text-foreground-500">If an AI draft is not acceptable, edit or replace it first. AI never chooses the RPL outcome, modules or hours.</span></span></label>
      </section>

      <div className="sticky bottom-3 z-10 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-foreground-200 bg-white/95 p-3 shadow-xl backdrop-blur">
        <p className="flex items-center gap-2 text-xs text-foreground-500"><FileCheck2 size={15} />Section 08 includes assessor, learner and employer / line manager signature placeholders.</p>
        <div className="flex flex-wrap gap-2"><button type="button" className={secondaryButton} disabled={!!working} onClick={() => load()}><RefreshCw size={15} />Reload</button>{!locked && <><button type="button" className={secondaryButton} disabled={!!working} onClick={() => save(false)}><Save size={15} />{working === 'save' ? 'Saving…' : 'Save draft'}</button><button type="button" className={primaryButton} disabled={!!working} onClick={() => save(true)}><CheckCircle2 size={16} />{working === 'finalise' ? 'Finalising…' : 'Finalise report'}</button></>}</div>
      </div>
    </div>
  );
}
