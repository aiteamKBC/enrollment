import { useEffect, useRef, useState, type ReactNode } from 'react';
import { SignaturePad } from '@/pages/users/wizard/steps/SignaturePad';

// ============================================================================
// One compliance document on the learner's page.
//
// Every statutory document behaves the same way from the learner's side: the
// provider issues it, they see the particulars, they sign it in their own name,
// and they can preview or download it.
// Only the fields shown and the other signing party differ — so that varies by
// prop rather than by a second copy of this markup.
// ============================================================================

export interface PartyState {
  signed: boolean;
  name: string;
  signedAt: string | null;
}

interface Props {
  title: string;
  blurb: string;
  /** e.g. "You and your employer" or "You and your training provider". */
  signedBy: string;
  /** Null until the provider issues it. */
  issued: boolean;
  fullySigned: boolean;
  /** The learner's own sign-off state. */
  learner?: PartyState;
  /** The other party (employer or provider), for the status line. */
  other?: PartyState;
  otherLabel: string;
  /** A third signatory, for tripartite documents like the Training Plan. */
  third?: PartyState;
  thirdLabel?: string;
  /** Field rows shown above the signature state. */
  fields: ReactNode;
  /** The learner's name — they sign as themselves, not as the logged-in account. */
  signatoryName?: string;
  busy: boolean;
  onPreview: () => void;
  onDownload: () => void;
  onSign: (mark: string) => void;
  fmtDate: (iso?: string | null) => string;
}

export function DocumentCard({
  title,
  blurb,
  signedBy,
  issued,
  fullySigned,
  learner,
  other,
  otherLabel,
  third,
  thirdLabel,
  fields,
  signatoryName,
  busy,
  onPreview,
  onDownload,
  onSign,
  fmtDate,
}: Props) {
  const [signing, setSigning] = useState(false);
  const signingPanel = useRef<HTMLDivElement>(null);

  // The box opens below the card's buttons — off-screen for the last card, in
  // a workspace that hides its scrollbars, so pressing Sign looked as if the
  // button had just vanished. Bring it into view and move focus to it.
  useEffect(() => {
    if (!signing) return;
    signingPanel.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    signingPanel.current?.focus({ preventScroll: true });
  }, [signing]);

  const sign = (mark: string) => {
    setSigning(false);
    onSign(mark);
  };

  return (
    <section className="rounded-xl border border-foreground-200/60 bg-background-50 overflow-hidden">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-foreground-200/50 p-5">
        <div className="min-w-0">
          <h3 className="text-sm font-heading font-semibold text-foreground-900">{title}</h3>
          <p className="mt-1 max-w-2xl text-[12px] text-foreground-500">{blurb}</p>
          <p className="mt-1 text-[11px] text-foreground-400">Signed by: {signedBy}</p>
        </div>
        <span
          className={`shrink-0 rounded-full px-2.5 py-1 text-[10px] font-semibold ${
            fullySigned
              ? 'bg-emerald-100 text-emerald-700'
              : issued
                ? 'bg-amber-100 text-amber-700'
                : 'bg-background-100 text-foreground-500'
          }`}
        >
          {fullySigned ? 'Fully signed' : issued ? 'Awaiting signatures' : 'Not issued yet'}
        </span>
      </header>

      <dl className="grid gap-x-6 gap-y-3 p-5 sm:grid-cols-2 lg:grid-cols-3">{fields}</dl>

      {!issued && (
        <p className="border-t border-foreground-200/50 px-5 py-3 text-[12px] text-foreground-500">
          <i className="ri-information-line mr-1.5 text-foreground-400" />
          Your training provider will issue this document. You can preview the details above now,
          and you'll be able to sign it once it's issued.
        </p>
      )}

      {issued && (
        <div className="flex flex-wrap items-center gap-4 border-t border-foreground-200/50 px-5 py-3 text-[12px]">
          <SignState label="You" signed={Boolean(learner?.signed)} at={learner?.signedAt} fmtDate={fmtDate} />
          <SignState label={otherLabel} signed={Boolean(other?.signed)} at={other?.signedAt} fmtDate={fmtDate} />
          {thirdLabel && (
            <SignState label={thirdLabel} signed={Boolean(third?.signed)} at={third?.signedAt} fmtDate={fmtDate} />
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 border-t border-foreground-200/50 bg-background-100/40 px-5 py-3">
        <button
          onClick={onPreview}
          disabled={busy}
          className="rounded-lg border border-foreground-200 px-3 py-1.5 text-[12px] font-medium text-foreground-600 hover:border-primary-300 hover:bg-primary-50/60 hover:text-primary-700 cursor-pointer disabled:opacity-50"
        >
          <i className="ri-eye-line mr-1" /> Preview
        </button>

        {issued && (
          <button
            onClick={onDownload}
            disabled={busy}
            className="rounded-lg border border-foreground-200 px-3 py-1.5 text-[12px] font-medium text-foreground-600 hover:border-primary-300 hover:bg-primary-50/60 hover:text-primary-700 cursor-pointer"
          >
            <i className="ri-download-line mr-1" /> Download
          </button>
        )}

        {issued && !learner?.signed && !signing && (
          <button
            onClick={() => setSigning(true)}
            className="rounded-lg bg-primary-500 px-3 py-1.5 text-[12px] font-semibold text-white hover:bg-primary-600 cursor-pointer"
          >
            <i className="ri-quill-pen-line mr-1" /> Sign
          </button>
        )}
      </div>

      {/* Signing produces the learner's own name in a script face — the same
          mark every time, so there is nothing saved to pick from. */}
      {issued && signing && (
        <div
          ref={signingPanel}
          tabIndex={-1}
          role="region"
          aria-label={`Sign ${title}`}
          className="border-t border-foreground-200/50 p-5 space-y-3 scroll-mt-4 outline-none"
        >
          <p className="text-[12px] text-foreground-500">
            Sign below to confirm you agree to these details.
          </p>

          <SignaturePad
            signatoryName={signatoryName}
            onCommit={(dataUrl) => sign(dataUrl)}
            onCancel={() => setSigning(false)}
          />
        </div>
      )}
    </section>
  );
}

export function Field({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <dt className="text-[10px] font-semibold uppercase tracking-wider text-foreground-400">{label}</dt>
      <dd className="mt-0.5 text-[13px] text-foreground-900">
        {value || <span className="text-foreground-300">Not recorded</span>}
      </dd>
      {hint && <p className="text-[10px] text-foreground-400">{hint}</p>}
    </div>
  );
}

function SignState({
  label,
  signed,
  at,
  fmtDate,
}: {
  label: string;
  signed: boolean;
  at?: string | null;
  fmtDate: (iso?: string | null) => string;
}) {
  return (
    <span className="flex items-center gap-1.5">
      <i className={signed ? 'ri-checkbox-circle-fill text-emerald-500' : 'ri-time-line text-foreground-300'} />
      <span className="text-foreground-600">{label}</span>
      <span className="text-foreground-400">
        {signed ? `signed${at ? ` ${fmtDate(at)}` : ''}` : 'not signed'}
      </span>
    </span>
  );
}
