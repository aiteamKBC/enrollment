import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { roleNavMap } from '@/mocks/navigation';
import { useMyLearner } from '@/hooks/useMyLearner';
import { useToast } from "@/hooks/useToast.helpers";
import { useAuth } from "@/hooks/useAuth.helpers";
import { invalidateLearnerDetailCache } from '@/api/learnerDetail';
import { markFirstSessionUnlocked } from '@/hooks/useLearnerNavGate';
import { FIRST_SESSION_ROUTE } from '@/hooks/useOnboardingRedirect';
import { formatHoursMinutes } from '@/lib/format';
import {
  fetchAgreement,
  signAgreement,
  fmtAgreementDate,
  type AgreementResponse,
} from '@/api/apprenticeshipAgreement';
import {
  buildApprenticeshipAgreementPdf,
  renderAgreementPdf,
  agreementFilename,
} from '@/lib/apprenticeshipAgreementPdf';
import {
  fetchIlrDocument,
  signIlrDocument,
  type IlrResponse,
} from '@/api/ilrDocument';
import { buildIlrPdf, renderIlrPdf, ilrFilename } from '@/lib/ilrDocumentPdf';
import {
  fetchTrainingPlanDocument,
  signTrainingPlanDocument,
  type TrainingPlanResponse,
} from '@/api/trainingPlanDocument';
import {
  buildTrainingPlanPdf,
  renderTrainingPlanPdf,
  trainingPlanFilename,
} from '@/lib/trainingPlanPdf';
import {
  fetchWrittenAgreement,
  signWrittenAgreement,
  type WrittenAgreementResponse,
} from '@/api/writtenAgreement';
import {
  buildWrittenAgreementPdf,
  renderWrittenAgreementPdf,
  writtenAgreementFilename,
} from '@/lib/writtenAgreementPdf';
import { DocumentCard, Field } from './DocumentCard';
import { RowsSkeleton } from '@/components/feature/Skeletons';

// ============================================================================
// Compliance documents — the learner's statutory paperwork.
//
// Four documents, each with its own table, signatories and lifecycle:
//   * Apprenticeship Agreement — learner + EMPLOYER.
//   * Individual Learner Record — learner + PROVIDER. The employer has no part
//     in an ILR and never sees it.
//   * Training Plan — learner + employer + provider.
//   * Written Agreement — learner + employer + provider.
//
// All are issued by the provider, and each renders its PDF on demand from the
// record so the file always carries the signatures actually on file.
// ============================================================================

const learnerNav = roleNavMap.learner;

export default function LearnerCompliancePage() {
  const { kind, id } = useMyLearner();
  const isCommercial = kind === 'commercial';
  const toast = useToast();
  const navigate = useNavigate();
  const { auth } = useAuth();

  const [agreementData, setAgreementData] = useState<AgreementResponse | null>(null);
  const [ilrData, setIlrData] = useState<IlrResponse | null>(null);
  const [planData, setPlanData] = useState<TrainingPlanResponse | null>(null);
  const [writtenData, setWrittenData] = useState<WrittenAgreementResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    if (isCommercial) {
      setLoading(false);
      return;
    }
    try {
      const [agreement, ilr, plan, written] = await Promise.all([
        fetchAgreement(id),
        fetchIlrDocument(id),
        fetchTrainingPlanDocument(id),
        fetchWrittenAgreement(id),
      ]);
      setAgreementData(agreement);
      setIlrData(ilr);
      setPlanData(plan);
      setWrittenData(written);
      return { agreement, ilr, plan, written };
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load your compliance documents.');
    } finally {
      setLoading(false);
    }
  }, [id, isCommercial]);

  useEffect(() => {
    void load();
  }, [load]);

  // ---- Apprenticeship Agreement ----
  const agreement = agreementData?.agreement ?? null;
  const agreementName = agreementData?.particulars.apprenticeName || '';
  const agreementPdf = () => {
    if (!agreementData) return null;
    return agreement
      ? renderAgreementPdf(agreement)
      : buildApprenticeshipAgreementPdf(agreementData.particulars);
  };
  const agreementShown = agreement ? agreement.particulars : agreementData?.particulars;

  // ---- Individual Learner Record ----
  const ilr = ilrData?.document ?? null;
  const ilrPdf = () => {
    if (!ilrData) return null;
    return ilr ? renderIlrPdf(ilr) : buildIlrPdf(ilrData.learnerDetails, ilrData.answers);
  };
  const ilrShown = ilr ? ilr.learnerDetails : ilrData?.learnerDetails;

  // ---- Training Plan ----
  const plan = planData?.document ?? null;
  const planPdf = () => {
    if (!planData) return null;
    return plan
      ? renderTrainingPlanPdf(plan)
      : buildTrainingPlanPdf(
          planData.programme,
          planData.employment,
          planData.learningPlan,
          planData.otjh,
          planData.epa,
          planData.contacts,
        );
  };
  const planShown = plan ? plan.programme : planData?.programme;
  const planRows = plan ? plan.learningPlan : planData?.learningPlan ?? [];

  // ---- Written Agreement ----
  const written = writtenData?.document ?? null;
  const writtenPdf = () => {
    if (!writtenData) return null;
    return written
      ? renderWrittenAgreementPdf(written)
      : buildWrittenAgreementPdf(
          writtenData.particulars,
          writtenData.delivery,
          writtenData.epa,
          writtenData.costs,
          writtenData.contacts,
        );
  };
  const writtenShown = written ? written.particulars : writtenData?.particulars;

  const openPdf = (doc: ReturnType<typeof agreementPdf>) => {
    if (doc) window.open(doc.output('bloburl'), '_blank', 'noopener');
  };

  if (isCommercial) {
    return (
      <WorkspaceShell
        role="learner"
        roleLabel={learnerNav.label}
        navItems={learnerNav.items}
        workspaceLabel={learnerNav.workspaceLabel}
        pageTitle="Compliance documents"
        pageSubtitle="Not required for commercial delivery"
        userName="Learner"
        userRole="Learner"
      >
        <main className=" page-container min-w-0 w-full space-y-3 p-3 md:space-y-4 md:p-6">
          <div className="mx-auto max-w-2xl rounded-2xl border border-primary-200 bg-primary-50/40 p-8 text-center">
            <i className="ri-information-line text-3xl text-primary-600" />
            <h2 className="mt-3 text-lg font-heading font-semibold text-foreground-900">No compliance documents are required</h2>
            <p className="mt-2 text-[13px] leading-relaxed text-foreground-600">Commercial learners follow a delivery-only programme and do not complete apprenticeship compliance documents.</p>
          </div>
        </main>
      </WorkspaceShell>
    );
  }

  const sign = async (
    kind: 'agreement' | 'ilr' | 'plan' | 'written',
    mark: string,
    name: string,
    otherParty: string,
  ) => {
    if (!mark) {
      toast.error('Signature required', 'Enter your name to sign.');
      return;
    }
    setBusy(true);
    try {
      if (kind === 'agreement') await signAgreement(id, 'apprentice', name, mark);
      else if (kind === 'plan') await signTrainingPlanDocument(id, 'apprentice', name, mark);
      else if (kind === 'written') await signWrittenAgreement(id, 'learner', name, mark);
      else await signIlrDocument(id, 'learner', name, mark);
      toast.success('Signed', `Your ${otherParty} still needs to sign this document.`);
      invalidateLearnerDetailCache('apprenticeship', id);
      const documents = await load();
      if (auth.account?.role === 'learner' && documents
        && documents.agreement.agreement?.signatures.apprentice.signed
        && documents.ilr.document?.signatures.learner.signed
        && documents.plan.document?.signatures.apprentice.signed
        && documents.written.document?.signatures.learner.signed) {
        // Every document now carries their signature, which is what opens the
        // first learning session booking — so that is where they go next.
        markFirstSessionUnlocked('apprenticeship', id);
        navigate(FIRST_SESSION_ROUTE, { replace: true });
      }
    } catch (err) {
      toast.error('Could not sign', err instanceof Error ? err.message : 'Please try again.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <WorkspaceShell
      role="learner"
      roleLabel={learnerNav.label}
      navItems={learnerNav.items}
      workspaceLabel={learnerNav.workspaceLabel}
      pageTitle="Compliance documents"
      pageSubtitle="Your statutory apprenticeship paperwork"
    >
      <div className=" page-container min-w-0 w-full space-y-3 p-3 md:space-y-4 md:p-6">
        {loading ? (
          <RowsSkeleton rows={5} className="py-2" />
        ) : error ? (
          <div className="rounded-xl border border-foreground-200/60 bg-background-50 p-6 text-center">
            <p className="text-[13px] text-red-600">{error}</p>
            <button
              onClick={() => void load()}
              className="mt-3 rounded-lg border border-foreground-200 px-3 py-1.5 text-[12px] font-medium text-foreground-600 hover:border-primary-300 hover:bg-primary-50/60 hover:text-primary-700 cursor-pointer"
            >
              Try again
            </button>
          </div>
        ) : (
          <>
            <DocumentCard
              title="Apprenticeship Agreement"
              blurb="The agreement between you and your employer. It sets out your apprenticeship standard, the dates it runs, and your planned off-the-job training hours."
              signedBy="You and your employer"
              issued={Boolean(agreement)}
              fullySigned={Boolean(agreement?.fullySigned)}
              learner={agreement?.signatures.apprentice}
              other={agreement?.signatures.employer}
              otherLabel="Employer"
              signatoryName={agreementName}
              busy={busy}
              fmtDate={fmtAgreementDate}
              onPreview={() => openPdf(agreementPdf())}
              onDownload={() => agreementPdf()?.save(agreementFilename(agreementName))}
              onSign={(mark) => void sign('agreement', mark, agreementName, 'employer')}
              fields={
                agreementShown && (
                  <>
                    <Field label="Apprentice" value={agreementShown.apprenticeName} />
                    <Field label="Employer" value={agreementShown.employerName} />
                    <Field label="Apprenticeship standard" value={agreementShown.standard} />
                    <Field label="Start date" value={fmtAgreementDate(agreementShown.startDate)} />
                    <Field label="End date" value={fmtAgreementDate(agreementShown.endDate)} />
                    <Field
                      label="Duration of practical period"
                      value={agreementShown.durationWeeks != null ? `${agreementShown.durationWeeks} weeks` : ''}
                    />
                    <Field
                      label="Planned off-the-job training"
                      value={agreementShown.plannedOtjHours != null ? formatHoursMinutes(agreementShown.plannedOtjHours) : ''}
                      hint={
                        agreementData?.meta.moduleCount
                          ? `from ${agreementData.meta.moduleCount} module${agreementData.meta.moduleCount === 1 ? '' : 's'} on your learning plan`
                          : undefined
                      }
                    />
                  </>
                )
              }
            />

            <DocumentCard
              title="Individual Learner Record"
              blurb="Your enrolment record: your details, eligibility and the information you gave during onboarding. Your training provider confirms they have seen your identity and eligibility evidence."
              signedBy="You and your training provider"
              issued={Boolean(ilr)}
              fullySigned={Boolean(ilr?.fullySigned)}
              learner={ilr?.signatures.learner}
              other={ilr?.signatures.provider}
              otherLabel="Provider"
              signatoryName={ilrData?.learner.name}
              busy={busy}
              fmtDate={fmtAgreementDate}
              onPreview={() => openPdf(ilrPdf())}
              onDownload={() => ilrPdf()?.save(ilrFilename(ilrData?.learner.name || ''))}
              onSign={(mark) => void sign('ilr', mark, ilrData?.learner.name || '', 'training provider')}
              fields={
                ilrShown && (
                  <>
                    <Field
                      label="Name"
                      value={[ilrShown.givenNames, ilrShown.familyName].filter(Boolean).join(' ')}
                    />
                    <Field label="Date of birth" value={fmtAgreementDate(ilrShown.dateOfBirth)} />
                    <Field label="ULN" value={ilrShown.uln ?? ''} />
                    <Field label="National insurance number" value={ilrShown.nationalInsuranceNumber ?? ''} />
                    <Field label="Sex" value={ilrShown.sex ?? ''} />
                    <Field label="Telephone" value={ilrShown.telephone ?? ''} />
                  </>
                )
              }
            />

            <DocumentCard
              title="Training Plan"
              blurb="How you, your employer and your training provider will each support your apprenticeship, together with the learning plan and off-the-job hours that deliver it."
              signedBy="You, your employer and your training provider"
              issued={Boolean(plan)}
              fullySigned={Boolean(plan?.fullySigned)}
              learner={plan?.signatures.apprentice}
              other={plan?.signatures.employer}
              otherLabel="Employer"
              third={plan?.signatures.provider}
              thirdLabel="Provider"
              signatoryName={planData?.learner.name}
              busy={busy}
              fmtDate={fmtAgreementDate}
              onPreview={() => openPdf(planPdf())}
              onDownload={() => planPdf()?.save(trainingPlanFilename(planData?.learner.name || ''))}
              onSign={(mark) => void sign('plan', mark, planData?.learner.name || '', 'employer and provider')}
              fields={
                planShown && (
                  <>
                    <Field label="Programme" value={planShown.programme ?? ''} />
                    <Field label="Start date" value={fmtAgreementDate(planShown.startDate)} />
                    <Field label="End date" value={fmtAgreementDate(planShown.endDate)} />
                    <Field
                      label="Duration of practical period"
                      value={planShown.durationWeeks != null ? `${planShown.durationWeeks} weeks` : ''}
                    />
                    <Field
                      label="ILR planned hours"
                      value={planShown.ilrPlannedHours != null ? `${planShown.ilrPlannedHours} hours` : ''}
                    />
                    <Field
                      label="Learning plan"
                      value={planRows.length ? `${planRows.length} activities` : ''}
                    />
                  </>
                )
              }
            />

            <DocumentCard
              title="Written Agreement"
              blurb="The agreement between your employer and your training provider: what will be delivered, the end-point assessment arrangements, and the costs against the funding band."
              signedBy="You, your employer and your training provider"
              issued={Boolean(written)}
              fullySigned={Boolean(written?.fullySigned)}
              learner={written?.signatures.learner}
              other={written?.signatures.employer}
              otherLabel="Employer"
              third={written?.signatures.provider}
              thirdLabel="Provider"
              signatoryName={writtenData?.learner.name}
              busy={busy}
              fmtDate={fmtAgreementDate}
              onPreview={() => openPdf(writtenPdf())}
              onDownload={() => writtenPdf()?.save(writtenAgreementFilename(writtenData?.learner.name || ''))}
              onSign={(mark) => void sign('written', mark, writtenData?.learner.name || '', 'employer and provider')}
              fields={
                writtenShown && (
                  <>
                    <Field label="Apprentice" value={writtenShown.apprenticeName ?? ''} />
                    <Field label="Apprenticeship title" value={writtenShown.apprenticeshipTitle ?? ''} />
                    <Field label="Main provider" value={writtenShown.mainProvider ?? ''} />
                    <Field label="Start date" value={fmtAgreementDate(writtenShown.startDate)} />
                    <Field label="Planned end date" value={fmtAgreementDate(writtenShown.plannedEndDate)} />
                    <Field
                      label="Planned off-the-job training"
                      value={
                        (written ?? writtenData)?.delivery?.totalOtjHours != null
                          ? formatHoursMinutes((written ?? writtenData)!.delivery.totalOtjHours!)
                          : ''
                      }
                      hint={
                        writtenData?.meta.activityCount
                          ? `across ${writtenData.meta.activityCount} delivery activities`
                          : undefined
                      }
                    />
                  </>
                )
              }
            />
          </>
        )}
      </div>
    </WorkspaceShell>
  );
}
