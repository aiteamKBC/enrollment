import { useState } from 'react';
import { createPortal } from 'react-dom';
import { SignaturePad } from '@/pages/users/wizard/steps/SignaturePad';
import { signReviewForm, type ReviewSignatures } from '@/api/reviewForm';
import type { LearnerKind } from '@/api/learnerDetail';

/**
 * Sign-off dialog for a completed review, shared by both sides.
 *
 * Reuses the enrolment wizard's SignaturePad: the signatory's own name in a
 * script face. The name is fixed to whoever is signing — the learner on their
 * own review, the officer on the provider's side — so a review signature is
 * captured and stored the same way as every other document's.
 */
export default function SignReviewModal({
  kind,
  learnerId,
  eventKey,
  party,
  defaultName,
  signatures,
  onClose,
  onSigned,
}: {
  kind: LearnerKind;
  learnerId: string;
  eventKey: string;
  party: 'learner' | 'admin';
  defaultName: string;
  signatures: ReviewSignatures;
  onClose: () => void;
  onSigned: (signatures: ReviewSignatures) => void;
}) {
  const existing = signatures[party];
  // The name is not chosen here: it is whoever is signing. `defaultName` is the
  // learner's own name on their side, and the signed-in officer's on the
  // provider's.
  const name = (existing.name || defaultName || '').trim();

  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async (signature: string) => {
    if (saving) return;
    setSaving(true);
    setErr(null);
    try {
      const res = await signReviewForm(kind, learnerId, eventKey, {
        party,
        name: signature ? name : '',
        signature,
      });
      onSigned(res.signatures);
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not save the signature.');
    } finally {
      setSaving(false);
    }
  };

  const label = party === 'learner' ? 'Learner signature' : 'Provider signature';

  /* On the enrolment board this dialog is rendered deep inside
     `.stagger-children` / `.animate-fade-in-up`, whose keyframes animate
     `transform`. A transformed ancestor becomes the containing block for
     `position: fixed`, so the overlay was being sized and clipped against the
     accordion section instead of the viewport — the panel bled past its own
     footer and the backdrop only dimmed part of the page. Portalling to <body>
     escapes those ancestors, matching what the users-page Modal does. */
  const dialog = (
    <div className="fixed inset-0 z-[100] flex items-start justify-center p-4 overflow-y-auto" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} aria-hidden="true" />
      {/* items-start + mt: a centred panel re-centres itself as its height
          changes, so opening the signature pad would shift the whole dialog. */}
      <div className="relative w-full max-w-lg mt-[5vh] mb-8 rounded-2xl bg-background-50 shadow-xl max-h-[90vh] flex flex-col overflow-hidden">
        <header className="flex items-center justify-between gap-3 px-5 py-3 border-b border-foreground-100 shrink-0">
          <h3 className="text-[14px] font-heading font-semibold text-foreground-900">Sign this review</h3>
          <button onClick={onClose} className="text-foreground-400 hover:text-foreground-700" aria-label="Close">
            <i className="ri-close-circle-line text-[20px]" />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          <div>
            <p className="text-[12px] text-foreground-700 mb-2">{label} <span className="text-red-500">*</span></p>
            <SignaturePad
              signatoryName={name}
              onCommit={submit}
              onCancel={onClose}
            />
          </div>

          <p className="text-[11px] text-foreground-500 leading-relaxed">
            By signing you confirm the answers recorded in this review are accurate.
            Your name, signature and the date are stored with the review.
          </p>

          {err && <p className="text-[11px] text-red-600"><i className="ri-error-warning-line mr-1" />{err}</p>}
        </div>

        {existing.signed && (
          <footer className="flex items-center gap-2 px-5 py-3 border-t border-foreground-100 shrink-0">
            <button onClick={() => void submit('')} disabled={saving}
              className="text-[12px] font-semibold text-red-600 hover:underline disabled:opacity-60">
              Remove signature
            </button>
          </footer>
        )}
      </div>
    </div>
  );

  // Guard for SSR/tests where document may not exist.
  return typeof document === 'undefined' ? dialog : createPortal(dialog, document.body);
}
