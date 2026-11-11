import { useEffect, useRef, useState, type ReactNode } from 'react';
import { SignaturePad } from '@/pages/users/wizard/steps/SignaturePad';
import { CheckCircle2, Circle, Download, Eye, FileText, Layers3, LockKeyhole, PenLine, Signature, type LucideIcon } from 'lucide-react';
import styles from './compliance.module.css';

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
  canSign?: boolean;
  currentVersion?: number;
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
  /** Presentation only; document issuance and history remain owned by callers. */
  icon?: LucideIcon;
  revisions?: ReactNode;
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
  canSign = true,
  currentVersion,
  learner,
  other,
  otherLabel,
  third,
  thirdLabel,
  fields,
  icon: Icon = FileText,
  revisions,
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
    <section className={styles.documentCard} data-document-status={fullySigned ? 'signed' : issued ? 'awaiting' : 'unissued'}>
      <header className={styles.documentHeader}>
        <span className={styles.documentIcon} aria-hidden="true"><Icon size={32} strokeWidth={1.8} /></span>
        <div className={styles.documentIntro}>
          <h3>{title}</h3>
          {currentVersion && <p className={styles.signedBy}>Current document: Version {currentVersion}</p>}
          <p>{blurb}</p>
          <p className={styles.signedBy}>Signed by: {signedBy}</p>
        </div>
        <span
          className={styles.statusBadge}
        >
          {fullySigned ? <CheckCircle2 size={18} aria-hidden="true" /> : issued ? <Signature size={18} aria-hidden="true" /> : <LockKeyhole size={17} aria-hidden="true" />}
          {fullySigned ? 'Fully signed' : issued ? 'Awaiting signatures' : 'Not issued yet'}
        </span>
      </header>

      <div className={styles.detailPanels}>{fields}</div>

      {!issued && (
        <p className={styles.unissuedNotice}>
          <i className="ri-information-line mr-1.5 text-foreground-400" />
          Your training provider will issue this document. You can preview the details above now,
          and you'll be able to sign it once it's issued.
        </p>
      )}

      <div className={styles.documentFooter}>
      {issued && (
        <div className={styles.signatureStatus}>
          <span className={styles.signatureHeading}><Signature size={20} aria-hidden="true" />Signature status</span>
          <SignState label="You" signed={Boolean(learner?.signed)} at={learner?.signedAt} fmtDate={fmtDate} />
          <SignState label={otherLabel} signed={Boolean(other?.signed)} at={other?.signedAt} fmtDate={fmtDate} />
          {thirdLabel && (
            <SignState label={thirdLabel} signed={Boolean(third?.signed)} at={third?.signedAt} fmtDate={fmtDate} />
          )}
        </div>
      )}

      <div className={styles.actions}>
        <button
          onClick={onPreview}
          disabled={busy}
          className={styles.outlineButton}
        >
          <Eye size={18} aria-hidden="true" /> Preview
        </button>

        {issued && (
          <button
            onClick={onDownload}
            disabled={busy}
            className={styles.outlineButton}
          >
            <Download size={18} aria-hidden="true" /> Download
          </button>
        )}

        {issued && canSign && !learner?.signed && !signing && (
          <button
            onClick={() => setSigning(true)}
            className={styles.signButton}
          >
            <PenLine size={18} aria-hidden="true" /> Sign
          </button>
        )}
      </div>
      </div>

      {revisions && <div className={styles.history}><Layers3 className={styles.historyIcon} size={23} aria-hidden="true" />{revisions}</div>}

      {/* Signing produces the learner's own name in a script face — the same
          mark every time, so there is nothing saved to pick from. */}
      {issued && signing && (
        <div
          ref={signingPanel}
          tabIndex={-1}
          role="region"
          aria-label={`Sign ${title}`}
          className={`border-t border-foreground-200/50 p-5 space-y-3 scroll-mt-4 outline-none ${styles.signingPanel}`}
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
    <div className={styles.field}>
      <dt>{label}</dt>
      <dd>
        {value || <span className={styles.missing}>Not recorded</span>}
      </dd>
      {hint && <p className={styles.fieldHint}>{hint}</p>}
    </div>
  );
}

export function FieldGroup({ title, icon: Icon, children }: { title: string; icon: LucideIcon; children: ReactNode }) {
  return <section className={styles.fieldGroup}>
    <h4><Icon size={19} aria-hidden="true" />{title}</h4>
    <dl>{children}</dl>
  </section>;
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
    <span className={styles.partyState} data-signed={signed || undefined}>
      {signed ? <CheckCircle2 size={17} aria-hidden="true" /> : <Circle size={17} aria-hidden="true" />}
      <span>{label}</span>
      <span>
        {signed ? `signed${at ? ` ${fmtDate(at)}` : ''}` : 'not signed'}
      </span>
    </span>
  );
}
