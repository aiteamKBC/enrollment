import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { useToast } from "@/hooks/useToast.helpers";
import { roleNavMap } from '@/mocks/navigation';
import { fetchEnrolmentBoard, updateEnrolmentUser, deleteEnrolmentUser, finishEnrolment, PROGRAMME_STATUS_OPTIONS } from '@/api/enrolmentUsers';
import { fetchCommercialBoard } from '@/api/commercialUsers';
import { fetchEnrolmentDocuments, getEnrolmentDocumentUrl, uploadEnrolmentDocument, DOC_SIGNING_PARTIES, type EnrolmentDocument, type EnrolmentDocType } from '@/api/enrolmentDocuments';
import { fetchAgreement, issueAgreement, type Agreement } from '@/api/apprenticeshipAgreement';
import { renderAgreementPdf, agreementFilename } from '@/lib/apprenticeshipAgreementPdf';
import { fetchIlrDocument, issueIlrDocument, signIlrDocument, type IlrDocument } from '@/api/ilrDocument';
import { SignaturePad } from './wizard/steps/SignaturePad';
import { LearnerHeaderDates } from './components/LearnerHeaderDates';
import { FirstSessionReschedule } from './components/FirstSessionReschedule';
import { useAuth } from "@/hooks/useAuth.helpers";
import { renderIlrPdf, ilrFilename } from '@/lib/ilrDocumentPdf';
import {
  fetchTrainingPlanDocument,
  issueTrainingPlanDocument,
  signTrainingPlanDocument,
  type TrainingPlanDocument,
} from '@/api/trainingPlanDocument';
import { renderTrainingPlanPdf, trainingPlanFilename } from '@/lib/trainingPlanPdf';
import {
  fetchWrittenAgreement,
  issueWrittenAgreement,
  signWrittenAgreement,
  type WrittenAgreementDocument,
} from '@/api/writtenAgreement';
import { renderWrittenAgreementPdf, writtenAgreementFilename } from '@/lib/writtenAgreementPdf';
import { fetchLearnerCoach, updateLearnerCoach } from '@/api/coach';
import { reviewHandoverMessage } from '@/api/reviewHandover';
import { fetchCoachOptions, type CoachOption } from '@/api/staffUsers';

/** Render the Written Agreement from its record and save it. */
function downloadWrittenAgreement(document: WrittenAgreementDocument) {
  renderWrittenAgreementPdf(document).save(
    writtenAgreementFilename(document.particulars.apprenticeName || ''),
  );
}

/** Render the Training Plan from its record and save it. */
function downloadTrainingPlan(document: TrainingPlanDocument) {
  renderTrainingPlanPdf(document).save(
    trainingPlanFilename(document.programme.apprenticeName || ''),
  );
}

/** Render the agreement from its record and save it. */
function downloadAgreement(agreement: Agreement) {
  renderAgreementPdf(agreement).save(agreementFilename(agreement.particulars.apprenticeName));
}

/** Likewise for the ILR. */
function downloadIlr(document: IlrDocument) {
  const { givenNames, familyName } = document.learnerDetails;
  renderIlrPdf(document).save(ilrFilename([givenNames, familyName].filter(Boolean).join(' ')));
}
import { fetchOnboardingReviewSlots, fetchReviewForm, issuedLabel, issueOnboardingReview, type OnboardingReviewSlot, type ReviewFormResponse, type ReviewSignatures } from '@/api/reviewForm';
import SignReviewModal from '@/pages/learner/onboarding/reviews/SignReviewModal';
import { downloadReviewPdf } from '@/pages/learner/onboarding/reviews/reviewDocument';
import { REVIEW_QUESTION_LABELS } from '@/pages/learner/onboarding/reviews/questions';
import type { LearnerKind } from '@/api/extendedIlr';
import type { EnrolmentBoard, FsBlock } from './types';
import { competenceMeta } from '@/mocks/enrolment-console';
import {
  AlertTriangle, Award, Briefcase, Calculator, ClipboardList, Contact, CreditCard, Download, ExternalLink, Flag,
  FolderOpen, GraduationCap, History, LayoutGrid, ListChecks, MessageSquareText, Network, PenLine, Search,
  ShieldCheck, Target, Trash2, Upload, UserX, Users, Wand2, Wrench, type LucideIcon,
} from 'lucide-react';
import { Table, EmptyState, ActionLink, StatusBadge, Pagination, btnSecondary } from './components/ui';

const enrolmentNav = roleNavMap.apprentice;

/**
 * Compliance document types, mirroring DOC_TYPES in
 * backend/enrolment_api/documents.py — that registry validates the upload, so a
 * value missing there is rejected on save.
 */
const DOC_TYPE_LABELS: Record<EnrolmentDocType, string> = {
  'extended-ilr': 'Extended ILR',
  'training-plan': 'Training Plan',
  'commitment-statement': 'Commitment Statement',
  'apprenticeship-agreement': 'Apprenticeship Agreement',
  'contract-of-services': 'Contract of Services',
  'initial-assessment': 'Initial Assessment',
  'learning-agreement': 'Learning Agreement',
  'privacy-notice': 'Privacy Notice',
};

/**
 * Download rows as a CSV file.
 *
 * Values are quoted and inner quotes doubled per RFC 4180, and a UTF-8 BOM is
 * prepended so Excel doesn't mangle names with accents.
 */
function downloadCsv(filename: string, headers: string[], rows: (string | number)[][]) {
  const cell = (v: string | number) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const csv = [headers, ...rows].map((r) => r.map(cell).join(',')).join('\r\n');
  const url = URL.createObjectURL(new Blob([`﻿${csv}`], { type: 'text/csv;charset=utf-8' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).map((w) => w[0]).slice(0, 2).join('').toUpperCase();
}

/** A labelled fact in the learner summary. */
function MetaItem({ icon, label, value }: { icon: string; label: string; value: string }) {
  return (
    <div className="min-w-0">
      <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-foreground-400">{label}</p>
      <p className="flex items-start gap-2 text-[14px] font-medium text-foreground-800">
        <i className={`${icon} mt-0.5 shrink-0 text-primary-600`} aria-hidden="true" />
        <span className="min-w-0 break-words">{value}</span>
      </p>
    </div>
  );
}

/** Learner identity, key details, and the enrolment controls and actions. */
function LearnerHeader({
  name,
  reference,
  ownerSlot,
  programme,
  employer,
  cohort,
  onboardingStatus,
  dates,
  status,
  actions,
}: {
  name: string;
  reference: string;
  ownerSlot: ReactNode;
  programme: string;
  employer?: string;
  cohort?: string;
  onboardingStatus: string;
  dates: ReactNode;
  status: ReactNode;
  actions: ReactNode;
}) {
  return (
    <section aria-label="Learner overview" className="overflow-hidden rounded-2xl border border-foreground-200/60 bg-background-50 shadow-sm">
      <div className="p-5 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex min-w-0 items-center gap-4">
            <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl bg-primary-100 text-lg font-bold text-primary-700 ring-1 ring-inset ring-primary-200">
              {initials(name) || <i className="ri-user-3-line text-xl" aria-hidden="true" />}
            </span>
            <div className="min-w-0">
              <p className="mb-1 text-[11px] font-semibold uppercase tracking-widest text-foreground-400">Enrolment</p>
              <h2 className="break-words text-2xl font-heading font-bold leading-tight tracking-tight text-foreground-900">{name}</h2>
              {reference && <p className="mt-1 break-all text-[12px] text-foreground-500">{reference}</p>}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">{actions}</div>
        </div>
        <div className="mt-5 grid grid-cols-1 gap-5 border-t border-foreground-100 pt-5 sm:grid-cols-2 lg:grid-cols-4">
          {ownerSlot}
          <MetaItem icon="ri-book-open-line" label="Programme" value={programme || '—'} />
          <MetaItem icon="ri-team-line" label="Cohort" value={cohort || '—'} />
          <MetaItem icon="ri-briefcase-line" label="Employer" value={employer || '—'} />
        </div>
      </div>
      <div className="flex flex-wrap items-end justify-between gap-4 border-t border-foreground-100 bg-background-100/60 px-5 py-4 sm:px-6">
        <div className="flex flex-wrap items-start gap-x-6 gap-y-4">
          {status}
          {dates}
        </div>
        {onboardingStatus && (
          <span className="inline-flex max-w-full items-center gap-2 rounded-full border border-primary-200 bg-primary-50 px-3 py-1.5 text-[12px] font-medium text-primary-700">
            <i className="ri-road-map-line shrink-0" aria-hidden="true" />
            <span>Onboarding: {onboardingStatus}</span>
          </span>
        )}
      </div>
    </section>
  );
}

/**
 * The learner's coach, picked from the staff directory and saved to
 * "Learner"."learners" (columns coach_name / coach_email).
 *
 * Replaces the read-only Owner cell. The options are the Caseowner/Admin staff
 * in enrolment."Staff_users" — the same set the create form's Case owner
 * dropdown offers — and the pick carries both name and email so the two columns
 * are always written together and cannot disagree about who the coach is. That
 * matters because the calendar and absence reports address the coach by
 * `coach_email` while the UI shows `coach_name`.
 *
 * Keyed by email, not name: two staff can share a display name, and the email is
 * the value the rest of the system actually routes on.
 *
 * Only Active learners have a "Learner"."learners" row, so the GET 404s before
 * then. That is the normal state for a learner still in enrolment, not an
 * error — it falls back to showing the enrolment record's case owner as plain
 * text, exactly as this cell did before.
 *
 * The pick is held locally until Save, matching HeroProgrammeStatus, so a
 * mis-click never writes to the learner's record.
 */
function HeroCoach({ learnerId, fallbackOwner }: { learnerId: string; fallbackOwner: string }) {
  const [options, setOptions] = useState<CoachOption[]>([]);
  // '' = no coach set. Otherwise the chosen staff member's email.
  const [val, setVal] = useState('');
  const [saved, setSaved] = useState('');
  // The stored coach_name, kept so a coach who is not in the staff list (a
  // legacy or free-text value) can still be shown rather than silently
  // rendering as "no coach set".
  const [savedName, setSavedName] = useState('');
  const [editable, setEditable] = useState(false);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      // The staff list and the current coach are independent reads; a failure to
      // load options must not hide a coach that is already set.
      const [opts, current] = await Promise.all([
        fetchCoachOptions().catch(() => [] as CoachOption[]),
        fetchLearnerCoach(learnerId).then(
          (c) => c,
          // 404 = not Active yet, so there is nothing to edit.
          () => null,
        ),
      ]);
      if (cancelled) return;
      setOptions(opts);
      setEditable(current !== null);
      const email = current?.coachEmail?.trim() || '';
      setVal(email);
      setSaved(email);
      setSavedName(current?.coachName?.trim() || '');
    })();
    return () => { cancelled = true; };
  }, [learnerId]);

  const dirty = val !== saved;

  const save = async () => {
    if (!dirty || saving) return;
    const picked = options.find((o) => o.email === val);
    // Clearing the field is a legitimate edit — it unassigns the coach.
    if (val && !picked) return;
    setSaving(true);
    setErr(null);
    try {
      // Both columns in one PATCH: writing only one would leave the name and
      // the email pointing at different people.
      const next = await updateLearnerCoach(learnerId, {
        coachName: picked ? picked.name : '',
        coachEmail: picked ? picked.email : '',
      });
      const email = next.coachEmail?.trim() || '';
      setVal(email);
      setSaved(email);
      setSavedName(next.coachName?.trim() || '');
      setErr(reviewHandoverMessage(next.reviewHandover));
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not save');
    } finally {
      setSaving(false);
    }
  };

  // Not Active yet: no row to write to, so show what the enrolment record says.
  if (!editable) {
    return <MetaItem icon="ri-user-star-line" label="Owner" value={fallbackOwner || 'Unassigned'} />;
  }

  // A stored coach who is not among the staff options — keep them visible
  // rather than dropping to a blank select that misreads as "unassigned".
  // Also covers a name recorded with no email (the endpoint used to accept one
  // field at a time): there is no email to key on, so the option carries the
  // empty value and simply shows who is currently recorded.
  const unlisted = saved
    ? !options.some((o) => o.email === saved)
    : Boolean(savedName);

  return (
    <div className="min-w-0">
      <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-foreground-400">Owner</p>
      <div className="flex items-center gap-2 min-w-0">
        <select
          value={val}
          onChange={(e) => setVal(e.target.value)}
          aria-label="Coach"
          className="min-w-0 flex-1 px-2.5 py-1.5 text-[13px] font-medium bg-background-50 border border-foreground-200 rounded-lg text-foreground-800 outline-none cursor-pointer hover:border-primary-400 focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2 transition-smooth max-w-[220px] [&>option]:text-foreground-900 [&>option]:bg-background-50"
        >
          <option value="">— Unassigned —</option>
          {unlisted && saved && <option value={saved}>{savedName || saved}</option>}
          {unlisted && !saved && <option value="" disabled>{savedName} (no email on record)</option>}
          {options.map((o) => (
            <option key={o.email} value={o.email}>{o.name}</option>
          ))}
        </select>
        {dirty && (
          <button
            onClick={save}
            disabled={saving}
            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 bg-primary-100 text-primary-700 rounded-lg text-[12px] font-semibold hover:bg-primary-200 transition-smooth cursor-pointer disabled:opacity-60 shrink-0"
          >
            {saving ? <i className="ri-loader-4-line animate-spin" /> : <><i className="ri-save-line" />Save</>}
          </button>
        )}
      </div>
      {err && <span className="block text-[11px] text-red-700 mt-1"><i className="ri-error-warning-line mr-1" />{err}</span>}
    </div>
  );
}

/**
 * Programme status, set from the page header.
 *
 * Replaced the separate "Enrolled learners" delivery list, which re-listed the
 * same learners this page already covers. The pick is held locally
 * until Save, so a mis-click never writes to the learner's record.
 */
function HeroProgrammeStatus({ learnerId, initial }: { learnerId: string; initial: string }) {
  const [val, setVal] = useState(initial);
  const [saved, setSaved] = useState(initial);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { setVal(initial); setSaved(initial); }, [initial]);

  const dirty = val !== saved;

  const save = async () => {
    if (!dirty || saving) return;
    setSaving(true);
    setErr(null);
    try {
      // One endpoint for both learner kinds — they share one table.
      await updateEnrolmentUser(learnerId, { programmeStatus: val });
      setSaved(val);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not save');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col items-start gap-1.5">
      <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-400">Programme status</span>
      <div className="flex items-center gap-2">
        <select
          value={val}
          onChange={(e) => setVal(e.target.value)}
          aria-label="Programme status"
          className="px-3 py-2 text-[13px] font-medium bg-background-50 border border-foreground-200 rounded-lg text-foreground-800 outline-none cursor-pointer hover:border-primary-400 focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2 transition-smooth max-w-[200px] [&>option]:text-foreground-900 [&>option]:bg-background-50"
        >
          <option value="">— Set status —</option>
          {PROGRAMME_STATUS_OPTIONS.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        {dirty && (
          <button
            onClick={save}
            disabled={saving}
            className="inline-flex items-center gap-1.5 px-3 py-2 bg-primary-100 text-primary-700 rounded-lg text-[12px] font-semibold hover:bg-primary-200 transition-smooth cursor-pointer disabled:opacity-60 shrink-0"
          >
            {saving ? <><i className="ri-loader-4-line animate-spin" />Saving…</> : <><i className="ri-save-line" />Save</>}
          </button>
        )}
      </div>
      {err && <span className="text-[11px] text-red-700"><i className="ri-error-warning-line mr-1" />{err}</span>}
    </div>
  );
}

/**
 * Finish enrolment — the one gate out of enrolment."Created_users".
 *
 * Every learner the console creates lives only as an enrolment record. Pressing
 * this promotes them into the live learner tables (same id), sets them Active
 * and starts their journey, so an in-progress enrolment never shows up as a
 * live learner. Confirmed first because it is not a routine status tweak.
 */
function FinishEnrolment({ learnerId, status, onFinished }: { learnerId: string; status: string; onFinished: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  // Already promoted — the learner is live, so the gate is spent.
  const done = status.trim().toLowerCase() === 'active';

  const run = async () => {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      await finishEnrolment(learnerId);
      setConfirming(false);
      onFinished();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not finish enrolment');
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <span className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl bg-emerald-50 border border-emerald-200 text-[12px] font-semibold text-emerald-800">
        <i className="ri-check-double-line" />Enrolment complete
      </span>
    );
  }

  return (
    <div className="flex flex-col items-start gap-1.5">
      {confirming ? (
        <div className="flex items-center gap-2">
          <button
            onClick={run}
            disabled={busy}
            className="inline-flex items-center gap-1.5 px-3 py-2.5 bg-primary-500 text-white rounded-xl text-[12px] font-semibold hover:bg-primary-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2 transition-smooth cursor-pointer disabled:opacity-60 shadow-sm"
          >
            {busy ? <><i className="ri-loader-4-line animate-spin" />Finishing…</> : <><i className="ri-check-line" />Confirm</>}
          </button>
          <button
            onClick={() => { setConfirming(false); setErr(null); }}
            disabled={busy}
            className="px-3 py-2.5 rounded-xl text-[12px] font-semibold text-foreground-600 hover:text-foreground-900 hover:bg-background-200 transition-smooth cursor-pointer disabled:opacity-60"
          >
            Cancel
          </button>
        </div>
      ) : (
        <button
          onClick={() => setConfirming(true)}
          title="Check whether this learner is now eligible for automatic activation"
          className="inline-flex items-center gap-1.5 px-4 py-2.5 bg-primary-500 text-white rounded-xl text-[13px] font-semibold hover:bg-primary-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2 transition-smooth cursor-pointer shadow-sm"
        >
          <i className="ri-refresh-line" />Check enrolment status
        </button>
      )}
      {confirming && !err && <span className="text-[11px] text-foreground-500 max-w-[220px]">Activation happens automatically after all documents are signed and the start date arrives.</span>}
      {err && <span className="text-[11px] text-red-700 max-w-[220px]"><i className="ri-error-warning-line mr-1" />{err}</span>}
    </div>
  );
}

/**
 * One Functional Skills subject, read-only. These come from columns an older
 * import filled in; nothing in the LMS writes them now, so there is nothing to
 * edit here — the exemption switch and file controls that used to sit on this
 * card only changed what the screen showed.
 */
function FunctionalSkill({ subject, block }: { subject: string; block: FsBlock }) {
  return (
    <div className="rounded-xl border border-foreground-100 p-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[13px] font-semibold text-foreground-800">{subject}</p>
        <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${block.exempt ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-foreground-200 bg-background-100 text-foreground-500'}`}>
          {block.exempt ? 'Exempt' : 'Not exempt'}
        </span>
      </div>
      {block.assessments.length === 0 ? (
        <p className="mt-2 text-[12px] text-foreground-400">No assessments recorded</p>
      ) : (
        <ul className="mt-2 space-y-1 text-[12px] text-foreground-700">
          {block.assessments.map((a) => <li key={a.id}>{a.name} — {a.level} ({a.date})</li>)}
        </ul>
      )}
      {block.evidence.length > 0 && (
        <div className="mt-3 border-t border-foreground-100 pt-2">
          <p className="text-[11px] text-foreground-400">Exemption evidence</p>
          <ul className="mt-1 space-y-0.5 text-[12px]">
            {block.evidence.map((d) => (
              <li key={d.id}>
                {d.url
                  ? <a href={d.url} target="_blank" rel="noopener noreferrer" className="text-primary-600 hover:underline">{d.fileName ?? d.description}</a>
                  : <span className="text-foreground-700">{d.fileName ?? d.description}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

const fsFilled = (block: FsBlock) => block.exempt || block.assessments.length > 0 || block.evidence.length > 0;

/**
 * Generated compliance paperwork for this learner (Extended ILR today, the other
 * seven document types as they are built). Rows come from
 * enrolment."Enrolment_Documents"; opening one mints a short-lived SAS URL so the
 * Azure container itself stays private.
 */
/**
 * Who has signed a generated document: one chip per party it needs, ticked when
 * they have. Reads the same way as SignatureParties on the reviews panel.
 *
 * Only the parties the doc type actually asks for are shown — rendering an
 * unsigned "Employer" cell on a learner-only document would imply a signature
 * was expected and missing.
 */
function DocumentParties({ doc }: { doc: EnrolmentDocument }) {
  const required = DOC_SIGNING_PARTIES[doc.docType] ?? ['employer'];
  const state: Record<string, { signed: boolean; name?: string; at?: string | null }> = {
    learner: { signed: Boolean(doc.learner?.signed), name: doc.learner?.name, at: doc.learner?.signedAt },
    employer: { signed: Boolean(doc.employer?.signed), name: doc.employer?.name, at: doc.employer?.signedAt },
  };
  const labels: Record<string, { label: string; icon: string }> = {
    learner: { label: 'Learner', icon: 'ri-user-line' },
    employer: { label: 'Employer', icon: 'ri-briefcase-line' },
  };

  return (
    <span className="flex items-center gap-1.5">
      {required.map((party) => {
        const { signed, name, at } = state[party];
        const { label, icon } = labels[party];
        return (
          <span
            key={party}
            title={
              signed
                ? `${label} signed${name ? ` by ${name}` : ''}${at ? ` on ${new Date(at).toLocaleDateString('en-GB')}` : ''}`
                : `${label} has not signed yet`
            }
            className={`inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-md border whitespace-nowrap ${
              signed
                ? 'bg-emerald-50 text-emerald-700 border-emerald-200/60'
                : 'bg-background-100 text-foreground-400 border-foreground-200/60'
            }`}
          >
            <i className={`${signed ? 'ri-check-line' : icon} text-[11px]`} />
            {label}
          </span>
        );
      })}
    </span>
  );
}

/** Who has signed the Apprenticeship Agreement: apprentice and employer only. */
function AgreementParties({ agreement }: { agreement: Agreement }) {
  const parties = [
    { key: 'apprentice', label: 'Learner', icon: 'ri-user-line', state: agreement.signatures.apprentice },
    { key: 'employer', label: 'Employer', icon: 'ri-briefcase-line', state: agreement.signatures.employer },
  ];
  return (
    <span className="flex items-center gap-1.5">
      {parties.map((p) => (
        <span
          key={p.key}
          title={
            p.state.signed
              ? `${p.label} signed${p.state.name ? ` by ${p.state.name}` : ''}${p.state.signedAt ? ` on ${new Date(p.state.signedAt).toLocaleDateString('en-GB')}` : ''}`
              : `${p.label} has not signed yet`
          }
          className={`inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-md border whitespace-nowrap ${
            p.state.signed
              ? 'bg-emerald-50 text-emerald-700 border-emerald-200/60'
              : 'bg-background-100 text-foreground-400 border-foreground-200/60'
          }`}
        >
          <i className={`${p.state.signed ? 'ri-check-line' : p.icon} text-[11px]`} />
          {p.label}
        </span>
      ))}
    </span>
  );
}

/** Who has signed the ILR: the learner and the provider. No employer party. */
function IlrParties({ document }: { document: IlrDocument }) {
  const parties = [
    { key: 'learner', label: 'Learner', icon: 'ri-user-line', state: document.signatures.learner },
    { key: 'provider', label: 'Provider', icon: 'ri-shield-user-line', state: document.signatures.provider },
  ];
  return (
    <span className="flex items-center gap-1.5">
      {parties.map((p) => (
        <span
          key={p.key}
          title={
            p.state.signed
              ? `${p.label} signed${p.state.name ? ` by ${p.state.name}` : ''}${p.state.signedAt ? ` on ${new Date(p.state.signedAt).toLocaleDateString('en-GB')}` : ''}`
              : `${p.label} has not signed yet`
          }
          className={`inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-md border whitespace-nowrap ${
            p.state.signed
              ? 'bg-emerald-50 text-emerald-700 border-emerald-200/60'
              : 'bg-background-100 text-foreground-400 border-foreground-200/60'
          }`}
        >
          <i className={`${p.state.signed ? 'ri-check-line' : p.icon} text-[11px]`} />
          {p.label}
        </span>
      ))}
    </span>
  );
}

/** Who has signed the Training Plan: all three parties. */
function TrainingPlanParties({ document }: { document: TrainingPlanDocument }) {
  const parties = [
    { key: 'apprentice', label: 'Learner', icon: 'ri-user-line', state: document.signatures.apprentice },
    { key: 'employer', label: 'Employer', icon: 'ri-briefcase-line', state: document.signatures.employer },
    { key: 'provider', label: 'Provider', icon: 'ri-shield-user-line', state: document.signatures.provider },
  ];
  return (
    <span className="flex items-center gap-1.5">
      {parties.map((p) => (
        <span
          key={p.key}
          title={
            p.state.signed
              ? `${p.label} signed${p.state.name ? ` by ${p.state.name}` : ''}`
              : `${p.label} has not signed yet`
          }
          className={`inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-md border whitespace-nowrap ${
            p.state.signed
              ? 'bg-emerald-50 text-emerald-700 border-emerald-200/60'
              : 'bg-background-100 text-foreground-400 border-foreground-200/60'
          }`}
        >
          <i className={`${p.state.signed ? 'ri-check-line' : p.icon} text-[11px]`} />
          {p.label}
        </span>
      ))}
    </span>
  );
}

/** Who has signed the Written Agreement: learner, employer and provider. */
function WrittenAgreementParties({ document }: { document: WrittenAgreementDocument }) {
  const parties = [
    { key: 'learner', label: 'Learner', icon: 'ri-user-line', state: document.signatures.learner },
    { key: 'employer', label: 'Employer', icon: 'ri-briefcase-line', state: document.signatures.employer },
    { key: 'provider', label: 'Provider', icon: 'ri-shield-user-line', state: document.signatures.provider },
  ];
  return (
    <span className="flex items-center gap-1.5">
      {parties.map((p) => (
        <span
          key={p.key}
          title={p.state.signed ? `${p.label} signed${p.state.name ? ` by ${p.state.name}` : ''}` : `${p.label} has not signed yet`}
          className={`inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-md border whitespace-nowrap ${
            p.state.signed
              ? 'bg-emerald-50 text-emerald-700 border-emerald-200/60'
              : 'bg-background-100 text-foreground-400 border-foreground-200/60'
          }`}
        >
          <i className={`${p.state.signed ? 'ri-check-line' : p.icon} text-[11px]`} />
          {p.label}
        </span>
      ))}
    </span>
  );
}

function ComplianceDocuments({ kind, learnerId, programme }: { kind: LearnerKind; learnerId: string; programme: string }) {
  const [docs, setDocs] = useState<EnrolmentDocument[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [opening, setOpening] = useState<string | null>(null);
  // `null` on each document below means two different things — still fetching, or
  // fetched and not issued — and the "not issued yet" rows render on the latter.
  // The five fetches settle independently, so gating those rows on their own
  // value alone flashed every placeholder in the instant a fetch resolved, then
  // swapped each to the real document as the rest landed. `loaded` holds the
  // whole list behind the spinner until every fetch has settled, so the section
  // goes straight from "Loading…" to its final state with no flicker.
  const [loaded, setLoaded] = useState(false);

  // The Apprenticeship Agreement lives in its own table, so it is fetched
  // separately and shown alongside the generic documents as one list.
  const [agreement, setAgreement] = useState<Agreement | null>(null);
  // The ILR likewise has its own table. Signed by the learner and the provider;
  // never shown to the employer.
  const [ilr, setIlr] = useState<IlrDocument | null>(null);
  // The Training Plan is tripartite: apprentice, employer and provider all sign.
  const [plan, setPlan] = useState<TrainingPlanDocument | null>(null);
  const [written, setWritten] = useState<WrittenAgreementDocument | null>(null);
  // Whoever is signed in signs the provider declaration in their own name.
  const { auth } = useAuth();
  const providerName = auth.user?.fullName || 'Enrolment Officer';

  useEffect(() => {
    let cancelled = false;
    // Re-fetching (learner switched): drop back to the spinner rather than
    // showing the previous learner's documents while the new ones load.
    setLoaded(false);
    const all = [
      fetchEnrolmentDocuments(kind, learnerId)
        .then((r) => !cancelled && setDocs(r))
        .catch((e: Error) => !cancelled && setErr(e.message)),
      fetchAgreement(learnerId)
        .then((r) => !cancelled && setAgreement(r.agreement))
        .catch(() => { /* No agreement yet is normal, not an error worth showing. */ }),
      fetchIlrDocument(learnerId)
        .then((r) => !cancelled && setIlr(r.document))
        .catch(() => { /* Likewise for the ILR. */ }),
      fetchTrainingPlanDocument(learnerId)
        .then((r) => !cancelled && setPlan(r.document))
        .catch(() => { /* And the Training Plan. */ }),
      fetchWrittenAgreement(learnerId)
        .then((r) => !cancelled && setWritten(r.document))
        .catch(() => { /* And the Written Agreement. */ }),
    ];
    // Every fetch settles its own state above; this only flips the gate once
    // they all have, so the "not issued yet" placeholders never show mid-load.
    Promise.all(all).then(() => { if (!cancelled) setLoaded(true); });
    return () => { cancelled = true; };
  }, [kind, learnerId]);

  const open = async (id: string) => {
    setOpening(id);
    try {
      window.open(await getEnrolmentDocumentUrl(kind, learnerId, id), '_blank', 'noopener');
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not open the document');
    } finally {
      setOpening(null);
    }
  };

  // Issuing is the provider's job: the agreement must exist before either the
  // learner or the employer can sign it, and neither of them should be the one
  // to create it. Freezes the current particulars onto a new record.
  const [issuing, setIssuing] = useState<'agreement' | 'ilr' | 'plan' | 'written' | null>(null);
  const issue = async () => {
    setIssuing('agreement');
    setErr(null);
    try {
      await issueAgreement(learnerId);
      setAgreement((await fetchAgreement(learnerId)).agreement);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not issue the agreement');
    } finally {
      setIssuing(null);
    }
  };

  const issueIlr = async () => {
    setIssuing('ilr');
    setErr(null);
    try {
      await issueIlrDocument(learnerId);
      setIlr((await fetchIlrDocument(learnerId)).document);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not issue the ILR');
    } finally {
      setIssuing(null);
    }
  };

  const issuePlan = async () => {
    setIssuing('plan');
    setErr(null);
    try {
      await issueTrainingPlanDocument(learnerId);
      setPlan((await fetchTrainingPlanDocument(learnerId)).document);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not issue the training plan');
    } finally {
      setIssuing(null);
    }
  };

  const issueWritten = async () => {
    setIssuing('written');
    setErr(null);
    try {
      await issueWrittenAgreement(learnerId);
      setWritten((await fetchWrittenAgreement(learnerId)).document);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not issue the written agreement');
    } finally {
      setIssuing(null);
    }
  };

  const [signingWritten, setSigningWritten] = useState(false);
  const signWrittenAsProvider = async (mark: string) => {
    setErr(null);
    try {
      setWritten(await signWrittenAgreement(learnerId, 'provider', providerName || 'Provider', mark));
      setSigningWritten(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not save the signature');
    }
  };

  const [signingPlan, setSigningPlan] = useState(false);
  const signPlanAsProvider = async (mark: string) => {
    setErr(null);
    try {
      setPlan(await signTrainingPlanDocument(learnerId, 'provider', providerName || 'Provider', mark));
      setSigningPlan(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not save the signature');
    }
  };

  // The provider's side of the ILR — the Provider/Sub-contractor declaration.
  const [signingIlr, setSigningIlr] = useState(false);
  const signIlrAsProvider = async (mark: string) => {
    setErr(null);
    try {
      setIlr(await signIlrDocument(learnerId, 'provider', providerName || 'Provider', mark));
      setSigningIlr(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not save the signature');
    }
  };

  return (
    <>
      <p className="text-[11px] font-semibold text-foreground-500 uppercase tracking-wider mb-2">{programme || 'Programme Name'}</p>
      {err && <p className="text-[12px] text-red-600 mb-2"><i className="ri-error-warning-line mr-1" />{err}</p>}
      {!loaded && !err && <p className="text-[12px] text-foreground-400 py-2"><i className="ri-loader-4-line animate-spin mr-1.5" />Loading documents…</p>}
      {loaded && docs !== null && docs.length === 0 && !agreement && !ilr && !plan && !written && <EmptyState text="No documents" />}
      {/* Until the agreement is issued neither party can sign it, and it does
          not appear in the employer's portal — so offer it here. */}
      {loaded && agreement === null && (
        <div className="flex items-center justify-between gap-3 px-3 py-2 mb-2 border border-dashed border-foreground-200 rounded-lg">
          <span className="text-[12px] text-foreground-500 inline-flex items-center gap-1.5 min-w-0">
            <i className="ri-file-add-line text-foreground-400 shrink-0" />
            <span className="truncate">Apprenticeship Agreement not issued yet</span>
          </span>
          <ActionLink
            label={issuing === 'agreement' ? 'Issuing…' : 'Issue agreement'}
            icon="ri-quill-pen-line"
            onClick={() => { void issue(); }}
          />
        </div>
      )}
      {agreement && (
        <div className="divide-y divide-foreground-100 border border-foreground-100 rounded-lg mb-2">
          <div className="flex items-center justify-between gap-3 px-3 py-2">
            <span className="text-[12px] text-foreground-700 inline-flex items-center gap-1.5 min-w-0">
              <i className="ri-file-pdf-line text-red-500 shrink-0" />
              <span className="truncate">Apprenticeship Agreement</span>
              {agreement.fullySigned && (
                <span className="text-[10px] font-semibold text-emerald-700 bg-emerald-50 border border-emerald-200/50 px-2 py-0.5 rounded-full shrink-0">signed</span>
              )}
            </span>
            <span className="flex items-center gap-3 shrink-0">
              {/* Apprentice and employer only — the provider does not sign it. */}
              <AgreementParties agreement={agreement} />
              <span className="text-[11px] text-foreground-400">
                {agreement.createdAt ? new Date(agreement.createdAt).toLocaleString('en-GB') : ''}
              </span>
              {/* Rendered from the agreement record on demand rather than
                  downloaded from storage, so it always carries the signatures
                  actually on record and the particulars that were signed. */}
              <ActionLink
                label="Download"
                icon="ri-download-line"
                onClick={() => downloadAgreement(agreement)}
              />
            </span>
          </div>
        </div>
      )}
      {/* The Individual Learner Record. Signed by the learner and the provider;
          the employer has no part in it and never sees it. */}
      {loaded && ilr === null && (
        <div className="flex items-center justify-between gap-3 px-3 py-2 mb-2 border border-dashed border-foreground-200 rounded-lg">
          <span className="text-[12px] text-foreground-500 inline-flex items-center gap-1.5 min-w-0">
            <i className="ri-file-add-line text-foreground-400 shrink-0" />
            <span className="truncate">Individual Learner Record not issued yet</span>
          </span>
          <ActionLink
            label={issuing === 'ilr' ? 'Issuing…' : 'Issue ILR'}
            icon="ri-quill-pen-line"
            onClick={() => { void issueIlr(); }}
          />
        </div>
      )}
      {ilr && (
        <div className="divide-y divide-foreground-100 border border-foreground-100 rounded-lg mb-2">
          <div className="flex items-center justify-between gap-3 px-3 py-2">
            <span className="text-[12px] text-foreground-700 inline-flex items-center gap-1.5 min-w-0">
              <i className="ri-file-pdf-line text-red-500 shrink-0" />
              <span className="truncate">Individual Learner Record</span>
              {ilr.fullySigned && (
                <span className="text-[10px] font-semibold text-emerald-700 bg-emerald-50 border border-emerald-200/50 px-2 py-0.5 rounded-full shrink-0">signed</span>
              )}
            </span>
            <span className="flex items-center gap-3 shrink-0">
              <IlrParties document={ilr} />
              <span className="text-[11px] text-foreground-400">
                {ilr.createdAt ? new Date(ilr.createdAt).toLocaleString('en-GB') : ''}
              </span>
              {!ilr.signatures.provider.signed && (
                <ActionLink
                  label="Sign as provider"
                  icon="ri-quill-pen-line"
                  onClick={() => setSigningIlr(true)}
                />
              )}
              <ActionLink
                label="Download"
                icon="ri-download-line"
                onClick={() => downloadIlr(ilr)}
              />
            </span>
          </div>
          {/* The Provider/Sub-contractor declaration: confirms identity,
              immigration permission and eligibility evidence were seen. */}
          {signingIlr && (
            <div className="px-3 py-3 space-y-2">
              <p className="text-[11px] text-foreground-500">
                Signing confirms you have seen evidence to verify the learner's identity,
                immigration permission (if applicable) and eligibility for this funding.
              </p>
              <SignaturePad
                defaultName={providerName}
                onCommit={(dataUrl) => signIlrAsProvider(dataUrl)}
                onCancel={() => setSigningIlr(false)}
              />
            </div>
          )}
        </div>
      )}
      {/* The tripartite Training Plan: apprentice, employer and provider. */}
      {loaded && plan === null && (
        <div className="flex items-center justify-between gap-3 px-3 py-2 mb-2 border border-dashed border-foreground-200 rounded-lg">
          <span className="text-[12px] text-foreground-500 inline-flex items-center gap-1.5 min-w-0">
            <i className="ri-file-add-line text-foreground-400 shrink-0" />
            <span className="truncate">Training Plan not issued yet</span>
          </span>
          <ActionLink
            label={issuing === 'plan' ? 'Issuing…' : 'Issue training plan'}
            icon="ri-quill-pen-line"
            onClick={() => { void issuePlan(); }}
          />
        </div>
      )}
      {plan && (
        <div className="divide-y divide-foreground-100 border border-foreground-100 rounded-lg mb-2">
          <div className="flex items-center justify-between gap-3 px-3 py-2">
            <span className="text-[12px] text-foreground-700 inline-flex items-center gap-1.5 min-w-0">
              <i className="ri-file-pdf-line text-red-500 shrink-0" />
              <span className="truncate">Training Plan</span>
              {plan.fullySigned && (
                <span className="text-[10px] font-semibold text-emerald-700 bg-emerald-50 border border-emerald-200/50 px-2 py-0.5 rounded-full shrink-0">signed</span>
              )}
            </span>
            <span className="flex items-center gap-3 shrink-0">
              <TrainingPlanParties document={plan} />
              <span className="text-[11px] text-foreground-400">
                {plan.createdAt ? new Date(plan.createdAt).toLocaleString('en-GB') : ''}
              </span>
              {!plan.signatures.provider.signed && (
                <ActionLink
                  label="Sign as provider"
                  icon="ri-quill-pen-line"
                  onClick={() => setSigningPlan(true)}
                />
              )}
              <ActionLink
                label="Download"
                icon="ri-download-line"
                onClick={() => downloadTrainingPlan(plan)}
              />
            </span>
          </div>
          {signingPlan && (
            <div className="px-3 py-3 space-y-2">
              <p className="text-[11px] text-foreground-500">
                Signing commits the provider to the delivery and support set out in this plan.
              </p>
              <SignaturePad
                defaultName={providerName}
                onCommit={(dataUrl) => signPlanAsProvider(dataUrl)}
                onCancel={() => setSigningPlan(false)}
              />
            </div>
          )}
        </div>
      )}
      {/* The Written Agreement: learner, employer and provider all sign. */}
      {loaded && written === null && (
        <div className="flex items-center justify-between gap-3 px-3 py-2 mb-2 border border-dashed border-foreground-200 rounded-lg">
          <span className="text-[12px] text-foreground-500 inline-flex items-center gap-1.5 min-w-0">
            <i className="ri-file-add-line text-foreground-400 shrink-0" />
            <span className="truncate">Written Agreement not issued yet</span>
          </span>
          <ActionLink
            label={issuing === 'written' ? 'Issuing…' : 'Issue written agreement'}
            icon="ri-quill-pen-line"
            onClick={() => { void issueWritten(); }}
          />
        </div>
      )}
      {written && (
        <div className="divide-y divide-foreground-100 border border-foreground-100 rounded-lg mb-2">
          <div className="flex items-center justify-between gap-3 px-3 py-2">
            <span className="text-[12px] text-foreground-700 inline-flex items-center gap-1.5 min-w-0">
              <i className="ri-file-pdf-line text-red-500 shrink-0" />
              <span className="truncate">Written Agreement</span>
              {written.fullySigned && (
                <span className="text-[10px] font-semibold text-emerald-700 bg-emerald-50 border border-emerald-200/50 px-2 py-0.5 rounded-full shrink-0">signed</span>
              )}
            </span>
            <span className="flex items-center gap-3 shrink-0">
              <WrittenAgreementParties document={written} />
              <span className="text-[11px] text-foreground-400">
                {written.createdAt ? new Date(written.createdAt).toLocaleString('en-GB') : ''}
              </span>
              {!written.signatures.provider.signed && (
                <ActionLink
                  label="Sign as provider"
                  icon="ri-quill-pen-line"
                  onClick={() => setSigningWritten(true)}
                />
              )}
              <ActionLink
                label="Download"
                icon="ri-download-line"
                onClick={() => downloadWrittenAgreement(written)}
              />
            </span>
          </div>
          {signingWritten && (
            <div className="px-3 py-3 space-y-2">
              <p className="text-[11px] text-foreground-500">
                Signing confirms the provider's agreement to the content and costings in this document.
              </p>
              <SignaturePad
                defaultName={providerName}
                onCommit={(dataUrl) => signWrittenAsProvider(dataUrl)}
                onCancel={() => setSigningWritten(false)}
              />
            </div>
          )}
        </div>
      )}
      {docs !== null && docs.length > 0 && (
        <div className="divide-y divide-foreground-100 border border-foreground-100 rounded-lg">
          {docs.map((d) => (
            <div key={d.id} className="flex items-center justify-between gap-3 px-3 py-2">
              <span className="text-[12px] text-foreground-700 inline-flex items-center gap-1.5 min-w-0">
                <i className="ri-file-pdf-line text-red-500 shrink-0" />
                <span className="truncate">{d.docLabel}</span>
                {d.signed && <span className="text-[10px] font-semibold text-emerald-700 bg-emerald-50 border border-emerald-200/50 px-2 py-0.5 rounded-full shrink-0">signed</span>}
              </span>
              <span className="flex items-center gap-3 shrink-0">
                {/* Who has signed and who hasn't — the same read as the reviews
                    panel below, so a part-signed document is visible at a glance
                    instead of just looking unsigned. */}
                <DocumentParties doc={d} />
                <span className="text-[11px] text-foreground-400">{d.generatedAt ? new Date(d.generatedAt).toLocaleString('en-GB') : ''}</span>
                <ActionLink label={opening === d.id ? 'Opening…' : 'Download'} icon="ri-download-line" onClick={() => open(d.id)} />
              </span>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

/**
 * Reviews the learner has started or finished, from
 * enrolment."Enrolment_Reviews". A merely-booked review is not listed: there is
 * no document until someone opens the form.
 *
 * "Open" goes to the same form the learner uses — staff and learner share it.
 */
/**
 * Who has signed a review: one cell per party, ticked when they have.
 *
 * The employer cell is omitted on reviews that don't ask for an employer
 * signature (`required` is false) — an empty "Employer ✗" would read as an
 * outstanding signature rather than one that was never needed.
 *
 * Signed cells carry the signatory's name in a tooltip, since three names would
 * not fit on the row.
 */
function SignatureParties({ signatures }: { signatures: ReviewSignatures }) {
  const parties: { key: string; label: string; icon: string; signed: boolean; name: string; at: string | null }[] = [
    {
      key: 'learner', label: 'Learner', icon: 'ri-user-line',
      signed: signatures.learner.signed,
      name: signatures.learner.name,
      at: signatures.learner.signedAt,
    },
    {
      key: 'admin', label: 'Admin', icon: 'ri-shield-user-line',
      signed: signatures.admin.signed,
      name: signatures.admin.name,
      at: signatures.admin.signedAt,
    },
  ];
  if (signatures.employer?.required) {
    parties.push({
      key: 'employer', label: 'Employer', icon: 'ri-briefcase-line',
      signed: signatures.employer.signed,
      name: signatures.employer.name,
      at: signatures.employer.signedAt,
    });
  }

  return (
    <span className="flex items-center gap-1.5">
      {parties.map((p) => {
        const when = p.at ? new Date(p.at).toLocaleDateString('en-GB') : '';
        const title = p.signed
          ? `${p.label} signed${p.name ? ` — ${p.name}` : ''}${when ? ` on ${when}` : ''}`
          : `${p.label} has not signed yet`;
        return (
          <span
            key={p.key}
            title={title}
            className={`inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-md border whitespace-nowrap ${
              p.signed
                ? 'bg-emerald-50 text-emerald-700 border-emerald-200/60'
                : 'bg-background-100 text-foreground-400 border-foreground-200/60'
            }`}
          >
            <i className={`${p.signed ? 'ri-check-line' : p.icon} text-[11px]`} />
            <span className="hidden sm:inline">{p.label}</span>
          </span>
        );
      })}
    </span>
  );
}

/**
 * The learner's four onboarding reviews. The enrolment team issues each one
 * from here — there is no booking — and the learner then completes and signs
 * it from their own Reviews page. Issued reviews show their progress and who
 * has signed, with sign-off and the PDF once finished.
 */
function ReviewDocuments({ kind, learnerId, programme }: { kind: LearnerKind; learnerId: string; programme: string }) {
  const navigate = useNavigate();
  const [reviews, setReviews] = useState<OnboardingReviewSlot[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  // Signing and exporting both need the full review (signature images, answers),
  // which the list omits — so it is fetched for the row being acted on.
  const [signing, setSigning] = useState<ReviewFormResponse | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchOnboardingReviewSlots(kind, learnerId)
      .then((r) => !cancelled && setReviews(r.reviews))
      .catch((e: Error) => !cancelled && setErr(e.message));
    return () => { cancelled = true; };
  }, [kind, learnerId, reload]);

  const withReview = async (eventKey: string, action: (r: ReviewFormResponse) => void) => {
    setBusy(eventKey);
    setErr(null);
    try {
      action(await fetchReviewForm(kind, learnerId, eventKey));
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not open the review.');
    } finally {
      setBusy(null);
    }
  };

  const issue = async (review: OnboardingReviewSlot) => {
    if (busy) return;
    setBusy(review.type);
    setErr(null);
    try {
      setReviews((await issueOnboardingReview(kind, learnerId, review.type)).reviews);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not issue the review.');
      // Someone else may have issued it meanwhile: show what is there now.
      setReload((n) => n + 1);
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <p className="text-[11px] font-semibold text-foreground-500 uppercase tracking-wider mb-2">{programme || 'Programme Name'}</p>
      {err && <p role="alert" className="text-[12px] text-red-600 mb-2"><i className="ri-error-warning-line mr-1" />{err}</p>}
      {reviews === null && !err && <p className="text-[12px] text-foreground-400 py-2"><i className="ri-loader-4-line animate-spin mr-1.5" />Loading reviews…</p>}
      {reviews !== null && reviews.length === 0 && <EmptyState text="No enrolment reviews for this learner" />}
      {reviews !== null && reviews.length > 0 && (
        <div className="space-y-2">
          {reviews.map((r) => r.issued && r.eventKey ? (
            <div key={r.type} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-foreground-100 px-3 py-2">
              <span className="text-[12px] text-foreground-700 inline-flex items-center gap-1.5 min-w-0">
                <i className="ri-file-list-3-line text-foreground-400 shrink-0" />
                <span className="truncate">{r.label}</span>
                {r.completed ? (
                  <span className="text-[10px] font-semibold text-emerald-700 bg-emerald-50 border border-emerald-200/50 px-2 py-0.5 rounded-full shrink-0">completed</span>
                ) : r.started ? (
                  <span className="text-[10px] font-semibold text-amber-700 bg-amber-50 border border-amber-200/50 px-2 py-0.5 rounded-full shrink-0">
                    in progress {r.sectionsDone}/{r.sectionsTotal}
                  </span>
                ) : (
                  <span className="text-[10px] font-semibold text-primary-700 bg-primary-50 border border-primary-200/50 px-2 py-0.5 rounded-full shrink-0">issued</span>
                )}
                {r.issuedAt && (
                  <span className="text-[11px] text-foreground-400 shrink-0" title="When this review was issued (UK time)">
                    <i className="ri-time-line mr-1" aria-hidden="true" />Issued {issuedLabel(r.issuedAt)}
                  </span>
                )}
                {r.completed && r.completedAt && (
                  <span className="text-[11px] text-emerald-700 shrink-0" title="When this review was completed (UK time)">
                    <i className="ri-check-double-line mr-1" aria-hidden="true" />Completed {issuedLabel(r.completedAt)}
                  </span>
                )}
              </span>
              <span className="flex items-center gap-3 shrink-0">
                {/* Who has signed, one cell per party. */}
                {r.signatures && <SignatureParties signatures={r.signatures} />}
                {r.reviewedBy && <span className="text-[11px] text-foreground-400 hidden xl:inline">{r.reviewedBy}</span>}
                {/* Sign-off and export are only meaningful on a finished review. */}
                {r.completed && (
                  <>
                    <ActionLink
                      label={busy === r.eventKey ? 'Opening…' : r.signatures?.admin.signed ? 'Signed' : 'Sign'}
                      icon={r.signatures?.admin.signed ? 'ri-check-line' : 'ri-pen-nib-line'}
                      onClick={() => withReview(r.eventKey!, setSigning)}
                    />
                    <ActionLink
                      label="PDF"
                      icon="ri-file-pdf-line"
                      onClick={() => withReview(r.eventKey!, (form) => downloadReviewPdf(form, REVIEW_QUESTION_LABELS))}
                    />
                  </>
                )}
                <ActionLink
                  label={r.completed ? 'View' : r.started ? 'Continue' : 'Open'}
                  icon="ri-external-link-line"
                  onClick={() => navigate(`/learner/onboarding/reviews/${encodeURIComponent(r.eventKey!)}`)}
                />
              </span>
            </div>
          ) : (
            <div key={r.type} className="flex items-center justify-between gap-3 px-3 py-2 border border-dashed border-foreground-200 rounded-lg">
              <span className="text-[12px] text-foreground-500 inline-flex items-center gap-1.5 min-w-0">
                <i className="ri-file-add-line text-foreground-400 shrink-0" />
                <span className="truncate">{r.label} not issued yet</span>
              </span>
              <ActionLink
                label={busy === r.type ? 'Issuing…' : 'Issue review'}
                icon="ri-quill-pen-line"
                onClick={() => { void issue(r); }}
              />
            </div>
          ))}
        </div>
      )}

      {signing && (
        <SignReviewModal
          kind={kind}
          learnerId={learnerId}
          eventKey={signing.eventKey}
          party="admin"
          defaultName={signing.reviewedBy}
          signatures={signing.signatures}
          onClose={() => setSigning(null)}
          onSigned={() => setReload((n) => n + 1)}
        />
      )}
    </>
  );
}

export default function BoardPage() {
  const { userId = '' } = useParams();
  const [search] = useSearchParams();
  // Ids overlap between the two learner tables, so the row's source travels
  // in the URL and decides which record this board reads.
  const isCommercial = search.get('source') === 'commercial';
  const key = `${isCommercial ? 'commercial' : 'apprenticeship'}:${userId}`;
  const [loaded, setLoaded] = useState<{ key: string; board: EnrolmentBoard } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    setError(null);
    (isCommercial ? fetchCommercialBoard(userId) : fetchEnrolmentBoard(userId))
      .then((board) => setLoaded({ key, board }))
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, [userId, isCommercial, key]);

  // Only this learner's board: another learner's never shows while theirs loads.
  const board = loaded?.key === key ? loaded.board : null;

  if (!board) {
    return (
      <WorkspaceShell role="compliance" roleLabel={enrolmentNav.label} navItems={enrolmentNav.items} workspaceLabel={enrolmentNav.workspaceLabel} pageTitle="Enrolment Details" pageSubtitle="" userName="Enrolment Officer" userRole="Enrolment Officer">
        <div className="p-6 max-w-6xl mx-auto">
          {loading && <div className="py-20 text-center text-[13px] text-foreground-400"><i className="ri-loader-4-line animate-spin mr-2" />Loading profile…</div>}
          {!loading && error && (
            <div className="py-20 text-center text-[13px]">
              <p className="text-red-600 mb-3"><i className="ri-error-warning-line mr-1.5" />{error}</p>
              <button className={btnSecondary} onClick={load}><i className="ri-refresh-line" />Retry</button>
            </div>
          )}
        </div>
      </WorkspaceShell>
    );
  }

  // Reloads (after an upload, a withdrawal, …) keep the board on screen, and
  // with it the open tab, rather than dropping back to the loading state.
  return <BoardView key={key} board={board} onReload={load} refreshing={loading} refreshError={error} />;
}

// --------------------------------------------------------------------------- //
// The board: a header, then tabs. Only what is wired is shown — fields backed  //
// by stored data and actions backed by an endpoint. Panels whose data nothing  //
// writes any more appear only for learners who have some.                     //
// --------------------------------------------------------------------------- //

type TabId = 'overview' | 'documents' | 'skills' | 'activity';

/** A titled card, with an optional count and actions. */
function Card({
  title,
  icon: Icon,
  count,
  actions,
  className = '',
  children,
}: {
  title: string;
  icon: LucideIcon;
  count?: number;
  actions?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section aria-label={title} className={`min-w-0 rounded-2xl border border-foreground-200/60 bg-background-50 shadow-sm ${className}`}>
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-foreground-100 px-4 py-3 sm:px-5">
        <h3 className="flex min-w-0 items-center gap-2.5 text-[14px] font-heading font-semibold text-foreground-900">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary-50 text-primary-600">
            <Icon size={16} aria-hidden="true" />
          </span>
          <span className="truncate">{title}</span>
          {count !== undefined && (
            <span className="rounded-full bg-background-200 px-2 py-0.5 text-[11px] font-semibold text-foreground-600">{count}</span>
          )}
        </h3>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </header>
      <div className="p-4 sm:p-5">{children}</div>
    </section>
  );
}

/** A card action: a small outlined button. */
function CardButton({ icon: Icon, label, onClick, disabled, danger }: { icon: LucideIcon; label: string; onClick: () => void; disabled?: boolean; danger?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[12px] font-medium transition-smooth cursor-pointer disabled:cursor-not-allowed disabled:opacity-50 ${
        danger
          ? 'border-red-200 text-red-700 hover:bg-red-50'
          : 'border-foreground-200 text-foreground-700 hover:border-primary-300 hover:text-primary-700'
      }`}
    >
      <Icon size={14} aria-hidden="true" />{label}
    </button>
  );
}

/** Label/value pairs. A blank value reads as a dash rather than vanishing. */
function Facts({ items }: { items: { label: string; value: ReactNode; wide?: boolean }[] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
      {items.map((f) => (
        <div key={f.label} className={`min-w-0 ${f.wide ? 'sm:col-span-2' : ''}`}>
          <dt className="text-[11px] font-semibold uppercase tracking-wider text-foreground-400">{f.label}</dt>
          <dd className="mt-1 break-words text-[13px] text-foreground-800">
            {f.value === null || f.value === undefined || f.value === '' ? <span className="text-foreground-300">—</span> : f.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

const cell = 'py-2 px-3 text-foreground-700';

/** The board's tabs, as an ARIA tab list: arrow keys move between them. */
function BoardTabs({ tabs, active, onChange }: { tabs: { id: TabId; label: string; icon: LucideIcon; count?: number }[]; active: TabId; onChange: (id: TabId) => void }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const move = (from: number, step: number) => {
    const next = (from + step + tabs.length) % tabs.length;
    onChange(tabs[next].id);
    refs.current[next]?.focus();
  };
  return (
    <div role="tablist" aria-label="Learner sections" className="flex gap-1 overflow-x-auto rounded-2xl border border-foreground-200/60 bg-background-100 p-1.5">
      {tabs.map((t, i) => {
        const selected = t.id === active;
        return (
          <button
            key={t.id}
            ref={(el) => { refs.current[i] = el; }}
            type="button"
            role="tab"
            id={`board-tab-${t.id}`}
            aria-selected={selected}
            aria-controls={`board-panel-${t.id}`}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(t.id)}
            onKeyDown={(e) => {
              if (e.key === 'ArrowRight') { e.preventDefault(); move(i, 1); }
              if (e.key === 'ArrowLeft') { e.preventDefault(); move(i, -1); }
            }}
            className={`flex shrink-0 items-center gap-2 rounded-xl px-3.5 py-2 text-[13px] font-semibold transition-smooth cursor-pointer ${
              // Selected: the workspace theme's raised white tab (index.css), stated here too.
              selected ? 'bg-background-50 text-primary-700 shadow-sm' : 'text-foreground-600 hover:bg-background-50/70 hover:text-foreground-900'
            }`}
          >
            <t.icon size={15} aria-hidden="true" />
            {t.label}
            {t.count ? (
              <span className={`rounded-full px-1.5 text-[11px] ${selected ? 'bg-primary-100 text-primary-700' : 'bg-background-200 text-foreground-600'}`}>{t.count}</span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

function BoardView({
  board,
  onReload,
  refreshing,
  refreshError,
}: {
  board: EnrolmentBoard;
  onReload: () => void;
  refreshing: boolean;
  refreshError: string | null;
}) {
  const navigate = useNavigate();
  const [search, setSearch] = useSearchParams();
  const { info, success, error } = useToast();
  const userId = board.user.id;
  // Carry the learner's source through to the wizard — ids overlap across tables.
  const isCommercial = search.get('source') === 'commercial';
  const kind: LearnerKind = isCommercial ? 'commercial' : 'apprenticeship';
  const suffix = isCommercial ? '?source=commercial' : '';
  const showWizard = () => navigate(`/users/${userId}/wizard${suffix}`);

  // ---- tabs: kept in the URL, so a reload or a shared link reopens the same one ----
  const tabs: { id: TabId; label: string; icon: LucideIcon; count?: number }[] = [
    { id: 'overview', label: 'Overview', icon: LayoutGrid },
    // The compliance paperwork, reviews and uploads are apprenticeship-only.
    ...(isCommercial ? [] : [{ id: 'documents' as const, label: 'Documents', icon: FolderOpen }]),
    { id: 'skills', label: 'Skills & progress', icon: Target, count: board.competencies.length },
    { id: 'activity', label: 'Activity', icon: History, count: board.activities.length },
  ];
  const requested = search.get('tab');
  const tab: TabId = tabs.find((t) => t.id === requested)?.id ?? 'overview';
  const selectTab = (id: TabId) => {
    const next = new URLSearchParams(search);
    if (id === 'overview') next.delete('tab');
    else next.set('tab', id);
    setSearch(next, { replace: true });
  };

  // ---- overview: contacts table paging ----
  const CONTACTS_PER_PAGE = 5;
  const [contactPage, setContactPage] = useState(1);
  const contactPages = Math.max(1, Math.ceil(board.contacts.length / CONTACTS_PER_PAGE));
  const contactRows = board.contacts.slice((contactPage - 1) * CONTACTS_PER_PAGE, contactPage * CONTACTS_PER_PAGE);

  // ---- activity: filters over the rows already on the board ----
  const [activityQuery, setActivityQuery] = useState('');
  const [activityStatus, setActivityStatus] = useState('');
  const activityStatuses = Array.from(new Set(board.activities.map((a) => a.timeAndStatus).filter(Boolean))).sort();
  const shownActivities = board.activities.filter((a) => {
    if (activityStatus && a.timeAndStatus !== activityStatus) return false;
    if (!activityQuery) return true;
    return `${a.event} ${a.date} ${a.timeAndStatus}`.toLowerCase().includes(activityQuery.toLowerCase());
  });

  /**
   * Withdraw the learner from their programme.
   *
   * Destructive and outward-facing (it changes what the learner sees on their
   * own landing page), so it confirms first and names the learner.
   */
  const [cancelling, setCancelling] = useState(false);
  const cancelUser = async () => {
    if (cancelling) return;
    const ok = window.confirm(
      `Withdraw ${board.user.name} from ${board.programme.name || 'their programme'}?\n\n` +
      'Their programme status becomes "Withdrawn". You can set it back from the status picker at the top of this page.',
    );
    if (!ok) return;
    setCancelling(true);
    try {
      await updateEnrolmentUser(userId, { programmeStatus: 'Withdrawn' });
      success('Learner withdrawn', `${board.user.name} is now Withdrawn.`);
      onReload();
    } catch (e) {
      error('Could not withdraw learner', e instanceof Error ? e.message : 'Unexpected error');
    } finally {
      setCancelling(false);
    }
  };

  const [deleting, setDeleting] = useState(false);
  const deleteUser = async () => {
    if (deleting) return;
    const ok = window.confirm(
      `Delete ${board.user.name}'s user account?\n\n` +
      'This permanently removes the enrolment record, login account and sign-in access, and the linked learner profile with its learning plan and progress. This action cannot be undone.',
    );
    if (!ok) return;
    setDeleting(true);
    try {
      await deleteEnrolmentUser(userId);
      success('User account deleted', `${board.user.name}'s account has been permanently deleted.`);
      navigate('/users');
    } catch (e) {
      error('Could not delete user account', e instanceof Error ? e.message : 'Unexpected error');
    } finally {
      setDeleting(false);
    }
  };

  /**
   * Upload a signed compliance document.
   *
   * The store behind this is the compliance-document container, which is keyed
   * to a fixed doc-type registry and accepts PDFs only — it isn't a general
   * file drop. So the picker asks which document this is, and the accept
   * attribute matches what the backend will actually take.
   */
  const [uploadType, setUploadType] = useState<EnrolmentDocType>('extended-ilr');
  const [uploading, setUploading] = useState(false);
  const uploadRef = useRef<HTMLInputElement>(null);
  const onUploadPicked = async (file?: File) => {
    if (!file) return;
    if (file.type !== 'application/pdf') {
      error('PDFs only', 'The compliance document store accepts PDF files.');
      return;
    }
    setUploading(true);
    try {
      await uploadEnrolmentDocument(kind, userId, uploadType, file, file.name, {
        signed: true,
        learnerName: board.user.name,
      });
      success('Document uploaded', `${file.name} was filed as ${DOC_TYPE_LABELS[uploadType]}.`);
      onReload();
    } catch (e) {
      error('Upload failed', e instanceof Error ? e.message : 'Unexpected error');
    } finally {
      setUploading(false);
    }
  };

  const fileStem = board.user.name.replace(/\s+/g, '-').toLowerCase();
  const exportCompetencies = () => {
    if (board.competencies.length === 0) { info('Nothing to export', 'This learner has no skills-radar ratings yet.'); return; }
    downloadCsv(
      `competencies-${fileStem}.csv`,
      ['KSB', 'Self-assessed level'],
      board.competencies.map((c) => [c.name, competenceMeta(c.version)?.label ?? c.version]),
    );
  };
  const exportActivities = () => {
    if (board.activities.length === 0) { info('Nothing to export', 'This learner has no recorded activity yet.'); return; }
    downloadCsv(
      `activities-${fileStem}.csv`,
      ['Date', 'Time and status', 'Event'],
      board.activities.map((a) => [a.date, a.timeAndStatus, a.event]),
    );
  };

  const signature = board.contact.signatureUrl || board.contact.savedSignature;
  const withdrawn = (board.programme.status || '').trim().toLowerCase() === 'withdrawn';
  // Older imports' data: shown only for the learners who have some.
  const fs = board.functionalSkills;
  const hasFunctionalSkills = fsFilled(fs.english) || fsFilled(fs.maths) || fsFilled(fs.ict);
  const hasSubscription = Boolean(board.subscription.startDate || board.subscription.endDate);
  const hasProgress = board.competencies.length > 0 || board.tracker.length > 0 || board.aims.length > 0
    || board.subProgrammes.length > 0 || board.managedJobs.length > 0 || hasFunctionalSkills || board.milestones.length > 0;

  return (
    <WorkspaceShell role="compliance" roleLabel={enrolmentNav.label} navItems={enrolmentNav.items} workspaceLabel={enrolmentNav.workspaceLabel} pageTitle="Enrolment Details" pageSubtitle={board.user.name} userName="Enrolment Officer" userRole="Enrolment Officer">
      <div className="mx-auto max-w-6xl space-y-4 p-3 sm:p-6">
        <div className="animate-fade-in-up">
          <LearnerHeader
            name={board.user.name}
            reference={board.user.reference}
            ownerSlot={<HeroCoach learnerId={userId} fallbackOwner={board.user.owner} />}
            programme={board.programme.name}
            employer={board.user.employer}
            cohort={board.programme.cohort}
            onboardingStatus={board.programme.onboardingStatus}
            dates={
              <div className="flex flex-wrap items-start gap-x-6 gap-y-3">
                <LearnerHeaderDates learnerId={userId} startDate={board.programme.learnerStartDate} endDate={board.programme.learnerEndDate} />
                {/* Renders nothing unless this learner has a booked first
                    session. Moving it rewrites the programme start date, hence
                    the reload. */}
                <FirstSessionReschedule kind={kind} learnerId={userId} onMoved={onReload} />
              </div>
            }
            status={<HeroProgrammeStatus learnerId={userId} initial={board.programme.status || ''} />}
            actions={
              <>
                <button type="button" onClick={showWizard} className={btnSecondary}>
                  <Wand2 size={15} aria-hidden="true" />Show wizard
                </button>
                <FinishEnrolment learnerId={userId} status={board.programme.status || ''} onFinished={onReload} />
              </>
            }
          />
        </div>

        {(refreshing || refreshError) && (
          <p role="status" className={`text-[12px] ${refreshError ? 'text-red-700' : 'text-foreground-400'}`}>
            {refreshError ? (
              <>
                <i className="ri-error-warning-line mr-1" />Could not refresh: {refreshError}{' '}
                <button type="button" className="font-semibold underline cursor-pointer" onClick={onReload}>Try again</button>
              </>
            ) : (
              <><i className="ri-loader-4-line mr-1 animate-spin" />Refreshing…</>
            )}
          </p>
        )}

        <BoardTabs tabs={tabs} active={tab} onChange={selectTab} />

        <div role="tabpanel" id={`board-panel-${tab}`} aria-labelledby={`board-tab-${tab}`} className="space-y-4">
          {tab === 'overview' && (
            <>
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <Card
                  title="Contact details"
                  icon={Contact}
                  actions={
                    <>
                      <CardButton icon={PenLine} label="Edit in wizard" onClick={showWizard} />
                      <CardButton icon={ExternalLink} label="Learner workspace" onClick={() => navigate(`/workspace/learner/${kind}/${userId}`)} />
                    </>
                  }
                >
                  <Facts items={[
                    { label: 'Email', value: board.contact.email ? <a href={`mailto:${board.contact.email}`} className="text-primary-600 hover:underline">{board.contact.email}</a> : '' },
                    { label: 'Phone', value: board.contact.phone ? <a href={`tel:${board.contact.phone}`} className="text-primary-600 hover:underline">{board.contact.phone}</a> : '' },
                    { label: 'Date of birth', value: board.contact.dob },
                    { label: 'Group', value: board.contact.groupMembership },
                    { label: 'Home address', value: board.contact.address, wide: true },
                    { label: 'Employer', value: board.user.employer },
                    {
                      label: 'Signature',
                      value: signature
                        ? <img src={signature} alt={`${board.user.name}'s signature`} className="h-12 rounded border border-foreground-100 bg-white" />
                        : <span className="text-foreground-400">Not captured yet</span>,
                    },
                  ]} />
                </Card>

                <Card
                  title="Programme"
                  icon={GraduationCap}
                  actions={
                    <>
                      <CardButton icon={Wrench} label="Build training plan" onClick={() => navigate(`/training-plan/${kind}/${userId}`)} />
                      <CardButton icon={ListChecks} label="Learner's plan" onClick={() => navigate(`/learner/training-plan/${kind}/${userId}`)} />
                    </>
                  }
                >
                  <Facts items={[
                    { label: 'Programme', value: board.programme.name, wide: true },
                    { label: 'Type', value: board.programme.type },
                    { label: 'Cohort', value: board.programme.cohort },
                    { label: 'Cohort start', value: board.programme.startDate },
                    { label: 'Cohort end', value: board.programme.endDate },
                    { label: 'Enrolled', value: [board.programme.enrolledAt, board.programme.enrolledBy && `by ${board.programme.enrolledBy}`].filter(Boolean).join(' ') },
                    { label: 'Onboarding', value: board.programme.onboardingStatus ? <StatusBadge status={board.programme.onboardingStatus} /> : '' },
                    { label: 'Onboarding completed', value: board.programme.onboardingCompletedAt },
                  ]} />
                </Card>
              </div>

              {board.contacts.length > 0 && (
                <Card title="Contacts" icon={Users} count={board.contacts.length}>
                  <Table headers={['Name', 'Type', 'Phone', 'Email', 'Role', 'Notes']}>
                    {contactRows.map((c) => (
                      <tr key={c.id} className="border-b border-foreground-100 last:border-0">
                        <td className={cell}>{c.name}</td>
                        <td className={cell}>{c.type}</td>
                        <td className={cell}>{c.phone || '—'}</td>
                        <td className={cell}>{c.email ? <a href={`mailto:${c.email}`} className="text-primary-600 hover:underline">{c.email}</a> : '—'}</td>
                        <td className={cell}>{c.role || '—'}</td>
                        <td className={cell}>{c.notes || '—'}</td>
                      </tr>
                    ))}
                  </Table>
                  {contactPages > 1 && <div className="mt-2 border-t border-foreground-100"><Pagination page={contactPage} totalPages={contactPages} onChange={setContactPage} /></div>}
                </Card>
              )}

              {board.notes.length > 0 && (
                <Card title="In the learner's own words" icon={MessageSquareText} count={board.notes.length}>
                  <ul className="space-y-3">
                    {board.notes.map((n) => {
                      // "Label: answer", as the enrolment wizard asked it.
                      const at = n.text.indexOf(': ');
                      const [label, text] = at > 0 ? [n.text.slice(0, at), n.text.slice(at + 2)] : ['', n.text];
                      return (
                        <li key={n.id} className="rounded-xl border border-foreground-100 p-3">
                          {label && <p className="text-[11px] font-semibold uppercase tracking-wider text-foreground-400">{label}</p>}
                          <p className="mt-1 whitespace-pre-line text-[13px] text-foreground-800">{text}</p>
                          <p className="mt-1.5 text-[11px] text-foreground-400">{[n.administrator, n.dateTime].filter(Boolean).join(' · ')}</p>
                        </li>
                      );
                    })}
                  </ul>
                </Card>
              )}

              {hasSubscription && (
                <Card title="Subscription" icon={CreditCard}>
                  <Facts items={[
                    { label: 'Start date', value: board.subscription.startDate },
                    { label: 'End date', value: board.subscription.endDate },
                    { label: 'Status', value: board.subscription.status },
                  ]} />
                </Card>
              )}

              <Card title="Danger zone" icon={AlertTriangle} className="border-red-200/70">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="text-[13px] text-foreground-600">
                    <p><span className="font-semibold text-foreground-800">Withdraw</span> sets the programme status to Withdrawn; it can be set back from the status picker.</p>
                    <p className="mt-1"><span className="font-semibold text-foreground-800">Delete</span> permanently removes the enrolment record, login and learner profile.</p>
                  </div>
                  <div className="flex shrink-0 flex-wrap gap-2">
                    <CardButton icon={UserX} label={withdrawn ? 'Withdrawn' : cancelling ? 'Withdrawing…' : 'Withdraw from programme'} onClick={cancelUser} disabled={withdrawn || cancelling} danger />
                    <CardButton icon={Trash2} label={deleting ? 'Deleting…' : 'Delete user account'} onClick={deleteUser} disabled={deleting} danger />
                  </div>
                </div>
              </Card>
            </>
          )}

          {tab === 'documents' && !isCommercial && (
            <>
              <Card title="Compliance documents" icon={ShieldCheck}>
                <ComplianceDocuments kind="apprenticeship" learnerId={userId} programme={board.programme.name} />
              </Card>
              <Card title="Review documents" icon={ClipboardList}>
                <ReviewDocuments kind="apprenticeship" learnerId={userId} programme={board.programme.name} />
              </Card>
              <Card title="Uploaded documents" icon={FolderOpen} count={board.documents.length}>
                {/* The store is keyed to a doc-type registry, so the type is
                    chosen before picking the file rather than guessed from its name. */}
                <div className="mb-4 flex flex-wrap items-end gap-2">
                  <label className="text-[11px] font-semibold uppercase tracking-wider text-foreground-400">
                    File as
                    <select
                      value={uploadType}
                      onChange={(e) => setUploadType(e.target.value as EnrolmentDocType)}
                      className="mt-1 block cursor-pointer rounded-lg border border-foreground-200 bg-background-50 px-3 py-1.5 text-[12px] normal-case tracking-normal text-foreground-800 outline-none focus:border-primary-400"
                    >
                      {(Object.keys(DOC_TYPE_LABELS) as EnrolmentDocType[]).map((t) => (
                        <option key={t} value={t}>{DOC_TYPE_LABELS[t]}</option>
                      ))}
                    </select>
                  </label>
                  <button type="button" className={btnSecondary} onClick={() => uploadRef.current?.click()} disabled={uploading}>
                    {uploading ? <><i className="ri-loader-4-line animate-spin" />Uploading…</> : <><Upload size={15} aria-hidden="true" />Upload signed PDF</>}
                  </button>
                  <input
                    ref={uploadRef}
                    type="file"
                    accept="application/pdf"
                    className="hidden"
                    aria-label="Signed PDF to upload"
                    onChange={(e) => {
                      void onUploadPicked(e.target.files?.[0]);
                      // Reset so re-picking the same file fires change again.
                      e.target.value = '';
                    }}
                  />
                </div>
                {board.documents.length === 0 ? <EmptyState text="No documents uploaded yet" /> : (
                  <Table headers={['Uploaded', 'Document']}>
                    {board.documents.map((d) => (
                      <tr key={d.id} className="border-b border-foreground-100 last:border-0">
                        <td className={`${cell} whitespace-nowrap`}>{d.uploaded}</td>
                        <td className={cell}>
                          {d.url
                            ? <a href={d.url} target="_blank" rel="noopener noreferrer" className="text-primary-600 hover:underline">{d.fileName || d.description}</a>
                            : d.fileName || d.description}
                        </td>
                      </tr>
                    ))}
                  </Table>
                )}
              </Card>
            </>
          )}

          {tab === 'skills' && (
            <>
              <Card
                title="Competencies"
                icon={Target}
                count={board.competencies.length}
                actions={
                  <>
                    <CardButton icon={Download} label="Export CSV" onClick={exportCompetencies} />
                    <CardButton icon={ExternalLink} label="Open Skills Radar" onClick={() => navigate(`/users/${userId}/wizard/skills-radar${suffix}`)} />
                  </>
                }
              >
                {board.competencies.length === 0 ? <EmptyState text="No Skills Radar ratings yet" /> : (
                  <ul className="divide-y divide-foreground-100 rounded-xl border border-foreground-100">
                    {board.competencies.map((c) => {
                      const meta = competenceMeta(c.version);
                      return (
                        <li key={c.id} className="flex items-center justify-between gap-3 px-3 py-2.5">
                          <span className="min-w-0 text-[13px] text-foreground-800">{c.name}</span>
                          <span className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${meta ? `${meta.tintBg} ${meta.tintBorder} ${meta.tintText}` : 'border-foreground-200 text-foreground-600'}`}>
                            {meta ? <>{meta.label}<span aria-hidden="true">·</span>{meta.score}</> : c.version}
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </Card>

              {board.tracker.length > 0 && (
                <Card title="Onboarding tracker" icon={ListChecks} count={board.tracker.length}>
                  <Table headers={['Type', 'Status', 'Programme', 'Description', 'Documents']}>
                    {board.tracker.map((t) => (
                      <tr key={t.id} className="border-b border-foreground-100 last:border-0">
                        <td className={cell}>{t.type}</td>
                        <td className={cell}>{t.status ? <StatusBadge status={t.status} /> : '—'}</td>
                        <td className={cell}>{t.programme}</td>
                        <td className={cell}>{t.description}</td>
                        <td className={cell}>{t.documents}</td>
                      </tr>
                    ))}
                  </Table>
                </Card>
              )}

              {board.aims.length > 0 && (
                <Card title="Aims and qualifications" icon={Award} count={board.aims.length}>
                  <Table headers={['Aim ref', 'Qualification', 'Start', 'End', 'Exempt']}>
                    {board.aims.map((a, i) => (
                      <tr key={i} className="border-b border-foreground-100 last:border-0">
                        <td className={cell}>{a.aimRef}</td>
                        <td className={cell}>{a.qualification}</td>
                        <td className={`${cell} whitespace-nowrap`}>{a.startDate}</td>
                        <td className={`${cell} whitespace-nowrap`}>{a.endDate}</td>
                        <td className={cell}>{a.exempt ? 'Yes' : 'No'}</td>
                      </tr>
                    ))}
                  </Table>
                </Card>
              )}

              {board.subProgrammes.length > 0 && (
                <Card title="Sub-programmes" icon={Network} count={board.subProgrammes.length}>
                  <Table headers={['Sub-programme', 'Start', 'End']}>
                    {board.subProgrammes.map((sp, i) => (
                      <tr key={i} className="border-b border-foreground-100 last:border-0">
                        <td className={cell}>{sp.name}</td>
                        <td className={`${cell} whitespace-nowrap`}>{sp.startDate}</td>
                        <td className={`${cell} whitespace-nowrap`}>{sp.endDate}</td>
                      </tr>
                    ))}
                  </Table>
                </Card>
              )}

              {board.managedJobs.length > 0 && (
                <Card title="Placements" icon={Briefcase} count={board.managedJobs.length}>
                  <Table headers={['Employer', 'Title', 'Categories', 'From', 'To', 'Planned / logged', 'Status', 'Date', 'Notes']}>
                    {board.managedJobs.map((j) => (
                      <tr key={j.id} className="border-b border-foreground-100 last:border-0">
                        <td className={cell}>{j.employer}</td>
                        <td className={cell}>{j.title}</td>
                        <td className={cell}>{j.categories}</td>
                        <td className={`${cell} whitespace-nowrap`}>{j.availableFrom}</td>
                        <td className={`${cell} whitespace-nowrap`}>{j.availableTo}</td>
                        <td className={`${cell} whitespace-nowrap`}>{j.hoursPlanned} / {j.hoursLogged}</td>
                        <td className={cell}>{j.status ? <StatusBadge status={j.status} /> : '—'}</td>
                        <td className={`${cell} whitespace-nowrap`}>{j.date}</td>
                        <td className={cell}>{j.comments}</td>
                      </tr>
                    ))}
                  </Table>
                </Card>
              )}

              {hasFunctionalSkills && (
                <Card title="Functional Skills" icon={Calculator}>
                  <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                    {fsFilled(fs.english) && <FunctionalSkill subject="English" block={fs.english} />}
                    {fsFilled(fs.maths) && <FunctionalSkill subject="Maths" block={fs.maths} />}
                    {fsFilled(fs.ict) && <FunctionalSkill subject="ICT" block={fs.ict} />}
                  </div>
                </Card>
              )}

              {board.milestones.length > 0 && (
                <Card title="Milestones" icon={Flag} count={board.milestones.length}>
                  <Table headers={['Programme', 'Description', 'Date', 'Emp wks left', 'Alw wks left', 'Status', 'Claimed']}>
                    {board.milestones.map((m) => (
                      <tr key={m.id} className="border-b border-foreground-100 last:border-0">
                        <td className={cell}>{m.programme}</td>
                        <td className={cell}>{m.description}</td>
                        <td className={`${cell} whitespace-nowrap`}>{m.date}</td>
                        <td className={cell}>{m.empWksLeft}</td>
                        <td className={cell}>{m.alwWksLeft}</td>
                        <td className={cell}>{m.status}</td>
                        <td className={cell}>{m.claimed}</td>
                      </tr>
                    ))}
                  </Table>
                </Card>
              )}

              {!hasProgress && <p className="text-[12px] text-foreground-400">Nothing else has been recorded for this learner yet.</p>}
            </>
          )}

          {tab === 'activity' && (
            <Card
              title="Activity timeline"
              icon={History}
              count={board.activities.length}
              actions={<CardButton icon={Download} label="Export CSV" onClick={exportActivities} />}
            >
              {board.activities.length === 0 ? <EmptyState text="No activity recorded yet" /> : (
                <>
                  <div className="mb-3 flex flex-wrap items-center gap-2">
                    <label className="relative">
                      <span className="sr-only">Search activities</span>
                      <Search size={14} aria-hidden="true" className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-foreground-400" />
                      <input
                        value={activityQuery}
                        onChange={(e) => setActivityQuery(e.target.value)}
                        placeholder="Search activities…"
                        className="w-56 rounded-lg border border-foreground-200 bg-background-50 py-1.5 pl-8 pr-3 text-[12px] outline-none focus:border-primary-400"
                      />
                    </label>
                    <select
                      value={activityStatus}
                      onChange={(e) => setActivityStatus(e.target.value)}
                      aria-label="Activity type"
                      className="cursor-pointer rounded-lg border border-foreground-200 bg-background-50 px-3 py-1.5 text-[12px] outline-none focus:border-primary-400"
                    >
                      <option value="">All types</option>
                      {activityStatuses.map((st) => <option key={st} value={st}>{st}</option>)}
                    </select>
                    {(activityQuery || activityStatus) && (
                      <button type="button" className="cursor-pointer rounded-lg bg-background-100 px-3 py-1.5 text-[12px] text-foreground-600" onClick={() => { setActivityQuery(''); setActivityStatus(''); }}>
                        Reset
                      </button>
                    )}
                    <span className="text-[11px] text-foreground-400">{shownActivities.length} of {board.activities.length}</span>
                  </div>
                  {shownActivities.length === 0 ? <EmptyState text="No activities match your filters" /> : (
                    <Table headers={['Date', 'Time and status', 'Event']}>
                      {shownActivities.map((a) => (
                        <tr key={a.id} className="border-b border-foreground-100 last:border-0">
                          <td className={`${cell} whitespace-nowrap`}>{a.date}</td>
                          <td className={cell}>{a.timeAndStatus}</td>
                          <td className={cell}>{a.event}</td>
                        </tr>
                      ))}
                    </Table>
                  )}
                </>
              )}
            </Card>
          )}
        </div>
      </div>
    </WorkspaceShell>
  );
}
