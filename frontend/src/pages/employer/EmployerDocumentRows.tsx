import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { SignableItem } from '@/api/employerPortal';
import { SignaturePad } from '@/pages/users/wizard/steps/SignaturePad';
import { LearnerReviewInstanceForm } from '@/pages/learner/reviews/LearnerReviewInstanceForm';

// ============================================================================
// The employer's document rows and sign-off dialog.
//
// Shared by a learner's Documents tab and the All documents page, so a document
// reads and signs the same way wherever the employer meets it. Signing reuses
// the admin side's flow exactly: the same SignaturePad, and for reviews the same
// sign endpoint with party="employer" (see useEmployerSigning).
// ============================================================================

function fmt(value: string | null | undefined) {
  if (!value) return '—';
  // ISO timestamps render as a plain UK date; anything else is passed through.
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString('en-GB');
}

/**
 * The sign-off dialog for one document.
 *
 * The employer signs in their own name, set in a script face — there is nothing
 * to draw and nothing saved to pick from, because the same name always produces
 * the same mark.
 */
export function SignModal({
  item,
  employerName,
  reviewDefinition,
  onClose,
  onSign,
  onSaveReviewAnswers,
}: {
  item: SignableItem;
  employerName: string;
  reviewDefinition?: any;
  onClose: () => void;
  onSign: (name: string, signature: string) => Promise<void>;
  onSaveReviewAnswers?: (answers: Record<string, unknown>) => Promise<any>;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const hasReviewForm = item.kind === 'review' && Boolean(item.reviewInstanceId || item.migratedForm || reviewDefinition?.template);

  const submit = async (signature: string) => {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      await onSign(signature ? employerName : '', signature);
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not save the signature.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-3 sm:p-6" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose} aria-hidden="true" />
      <div className={`relative w-full ${hasReviewForm ? 'max-w-6xl' : 'max-w-lg'} rounded-2xl bg-background-50 shadow-2xl max-h-[92dvh] flex flex-col overflow-hidden`}>
        <header className="flex items-start justify-between gap-3 px-5 py-4 border-b border-foreground-100 shrink-0">
          <div className="min-w-0">
            <h3 className="text-[15px] font-heading font-semibold text-foreground-900">Sign as employer</h3>
            <p className="text-[12px] text-foreground-500 truncate" title={item.label}>{item.label}</p>
          </div>
          <button onClick={onClose} aria-label="Close" className="w-8 h-8 rounded-lg flex items-center justify-center text-foreground-400 hover:bg-background-100 shrink-0">
            <i className="ri-close-line text-[18px]" />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-6 space-y-4">
          {reviewDefinition?.template ? <LearnerReviewInstanceForm definition={reviewDefinition} viewerRole="employer" onSaveAnswers={onSaveReviewAnswers} /> : null}
          <SignaturePad
            signatoryName={employerName}
            onCommit={submit}
            onCancel={onClose}
          />

          <p className="text-[11px] text-foreground-500 leading-relaxed">
            By signing you confirm the details recorded in this document are accurate.
            Your name, signature and the date are stored with it.
          </p>

          {err && <p className="text-[11px] text-red-600"><i className="ri-error-warning-line mr-1" />{err}</p>}

          {item.signed && (
            <button
              onClick={() => void submit('')}
              disabled={busy}
              className="text-[12px] font-semibold text-red-600 hover:underline disabled:opacity-60"
            >
              Remove signature
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Who has signed: one chip per party, ticked when they have.
 *
 * Mirrors the admin board's SignatureParties so both sides read the same way. A
 * party is omitted entirely when undefined rather than shown unsigned — a
 * compliance PDF only tracks the employer's signature, and rendering an empty
 * "Learner" cell there would imply a signature was expected and missing.
 */
function PartyChips({
  learner,
  admin,
  employer,
}: {
  learner?: boolean;
  admin?: boolean;
  employer?: boolean;
}) {
  const parties = [
    { key: 'learner', label: 'Learner', icon: 'ri-user-line', signed: learner },
    { key: 'admin', label: 'Provider', icon: 'ri-shield-user-line', signed: admin },
    { key: 'employer', label: 'You', icon: 'ri-briefcase-line', signed: employer },
  ].filter((p) => p.signed !== undefined);

  return (
    <span className="flex items-center gap-1.5">
      {parties.map((p) => (
        <span
          key={p.key}
          title={p.signed ? `${p.label} signed` : `${p.label} has not signed yet`}
          className={`inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-md border whitespace-nowrap ${
            p.signed
              ? 'bg-emerald-50 text-emerald-700 border-emerald-200/60'
              : 'bg-background-100 text-foreground-400 border-foreground-200/60'
          }`}
        >
          <i className={`${p.signed ? 'ri-check-line' : p.icon} text-[11px]`} />
          <span className="hidden sm:inline">{p.label}</span>
        </span>
      ))}
    </span>
  );
}

/**
 * One row in a documents list.
 *
 * `owner` names the learner the item belongs to, for lists that span several
 * learners (All documents); a single learner's own tab leaves it out.
 */
export function DocumentRow({
  item,
  onSign,
  onShow,
  opening,
  owner,
}: {
  item: SignableItem;
  onSign: () => void;
  onShow: () => void;
  opening: boolean;
  owner?: { name: string; href: string };
}) {
  return (
    <div className="flex items-center justify-between gap-3 px-4 py-3 flex-wrap">
      <span className="flex items-center gap-2.5 min-w-0">
        <i className={`${item.kind === 'review' ? 'ri-file-list-3-line' : 'ri-file-pdf-line'} text-foreground-400 shrink-0`} />
        <span className="min-w-0">
          <span className="text-[13px] text-foreground-800 font-medium block truncate" title={item.label}>{item.label}</span>
          {owner && (
            <Link to={owner.href} className="text-[12px] font-medium text-primary-700 hover:underline block truncate" title={owner.name}>
              {owner.name}
            </Link>
          )}
          <span className="text-[11px] text-foreground-400">
            {item.kind === 'review'
              ? item.scheduledDate ? `Review · ${item.scheduledDate}` : 'Review'
              : `Document · ${fmt(item.generatedAt)}`}
            {item.signed && item.signedName ? ` · signed by ${item.signedName} on ${fmt(item.signedAt)}` : ''}
          </span>
        </span>
      </span>
      <span className="flex items-center gap-2 shrink-0">
        {/* Every party the item needs, so the employer can see they aren't the
            only one outstanding. Reviews report all three; a compliance PDF
            reports the parties its own doc type asks for — the Apprenticeship
            Agreement is learner + employer, with no provider signature. */}
        {item.kind === 'review' ? (
          <PartyChips
            learner={item.learnerSigned}
            admin={item.adminSigned}
            employer={item.signed}
          />
        ) : (
          <PartyChips
            learner={item.parties?.includes('learner') ? Boolean(item.learnerSigned) : undefined}
            admin={item.parties?.includes('provider') ? Boolean(item.providerSigned) : undefined}
            employer={item.parties?.includes('employer') !== false ? item.signed : undefined}
          />
        )}
        {item.kind === 'review' && item.migratedForm && item.signed && !item.completed ? (
          <span className="text-[11px] text-foreground-500">Signed · awaiting final completion</span>
        ) : item.signed ? (
          <>
            {/* Opens the saved document — the signed artefact, carrying every
                party's signature. Not the sign dialog: this row is done, and
                re-opening the pad here invited an accidental re-sign. */}
            <button
              onClick={onShow}
              disabled={opening}
              className="inline-flex items-center gap-1.5 rounded-lg border border-foreground-200 px-2.5 py-1 text-[12px] font-medium text-foreground-600 transition-smooth hover:border-primary-300 hover:bg-primary-50/60 hover:text-primary-700 cursor-pointer whitespace-nowrap disabled:opacity-60"
            >
              {opening
                ? <><i className="ri-loader-4-line animate-spin" />Opening…</>
                : <><i className="ri-file-text-line text-[13px]" />Show document</>}
            </button>
          </>
        ) : item.signable ? (
          <button
            onClick={onSign}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-[12px] font-semibold text-white hover:bg-primary-700 transition-smooth cursor-pointer"
          >
            <i className="ri-pen-nib-line" />Sign
          </button>
        ) : (
          // A review whose questionnaire isn't finished can't be signed by
          // anyone yet — saying so beats an inert button.
          <span className="text-[11px] text-foreground-400 italic">Awaiting completion by the learner</span>
        )}
      </span>
    </div>
  );
}
