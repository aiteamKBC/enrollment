import { useCallback, useEffect, useMemo, useState } from 'react';
import { SignaturePad } from '@/pages/users/wizard/steps/SignaturePad';
import {
  fetchSuitabilityRplIssuedDocuments,
  fetchSuitabilityRplSignatures,
  signSuitabilityRplReport,
  type SuitabilityRplIssuedDocument,
} from '@/api/suitabilityRplReport';
import type { LearnerKind } from '@/api/learnerDetail';
import { DocumentSignatureTarget } from '@/features/pending-signatures/DocumentSignatureTarget';

function formatDate(value: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString('en-GB');
}

function Party({ label, signed, at }: { label: string; signed: boolean; at: string | null }) {
  return <span className="inline-flex items-center gap-1.5 text-xs text-foreground-600">
    <i className={signed ? 'ri-checkbox-circle-fill text-emerald-500' : 'ri-time-line text-foreground-300'} />
    {label}: {signed ? `signed${at ? ` ${formatDate(at)}` : ''}` : 'pending'}
  </span>;
}

export function SuitabilityRplIssuedCard({ kind, learnerId, signatoryName }: {
  kind: LearnerKind;
  learnerId: string;
  signatoryName?: string;
}) {
  const [documents, setDocuments] = useState<SuitabilityRplIssuedDocument[] | null>(null);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<SuitabilityRplIssuedDocument | null>(null);
  const [previewed, setPreviewed] = useState(false);
  const [consent, setConsent] = useState(false);
  const [signatureOpen, setSignatureOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setError('');
      setDocuments((await fetchSuitabilityRplIssuedDocuments(kind, learnerId)).documents);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not load the issued Suitability & RPL Report.');
      setDocuments([]);
    }
  }, [kind, learnerId]);
  useEffect(() => { void load(); }, [load]);

  const latest = documents?.[0] || null;
  const history = useMemo(() => documents?.slice(1) || [], [documents]);

  const preview = (document: SuitabilityRplIssuedDocument) => {
    setSelected(document);
    setPreviewed(true);
    window.open(document.pdfUrl, '_blank', 'noopener,noreferrer');
  };

  const sign = async (signature: string) => {
    if (!selected || !consent || !previewed) return;
    setBusy(true);
    try {
      await signSuitabilityRplReport(kind, learnerId, selected.issueId, {
        role: 'learner', name: signatoryName || 'Learner', signature,
        consent: true,
        consentText: 'I have reviewed the exact issued Suitability & RPL Report PDF and consent to signing this immutable version.',
      });
      setSignatureOpen(false);
      setConsent(false);
      setPreviewed(false);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not save your signature.');
    } finally {
      setBusy(false);
    }
  };

  if (documents === null) return <p role="status" className="text-[12px] text-foreground-400"><i className="ri-loader-4-line animate-spin mr-1.5" />Loading issued reports…</p>;
  if (error) return <p role="alert" className="text-[12px] text-red-600">{error}</p>;
  if (!latest) return null;

  return <DocumentSignatureTarget documentKey="suitability-rpl-report" sourceId={latest.issueId}><section className="rounded-xl border border-foreground-200/60 bg-background-50 overflow-hidden">
    <header className="flex flex-wrap items-start justify-between gap-3 border-b border-foreground-200/50 p-5">
      <div><h3 className="text-sm font-heading font-semibold text-foreground-900">Suitability &amp; RPL Report</h3>
        <p className="mt-1 text-[12px] text-foreground-500">Issued {formatDate(latest.issuedAt)} · Version {latest.reportVersion} · Revision {latest.reportRevision}</p>
      </div>
      <span className={`rounded-full px-2.5 py-1 text-[10px] font-semibold ${latest.fullySigned ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'}`}>{latest.fullySigned ? 'Fully signed' : 'Awaiting signatures'}</span>
    </header>
    <div className="flex flex-wrap gap-4 border-b border-foreground-200/50 px-5 py-3">
      <Party label="Assessor" signed={latest.signatures.admin.signed} at={latest.signatures.admin.signedAt} />
      <Party label="You" signed={latest.signatures.learner.signed} at={latest.signatures.learner.signedAt} />
      <Party label="Employer" signed={latest.signatures.employer.signed} at={latest.signatures.employer.signedAt} />
    </div>
    <div className="flex flex-wrap items-center gap-2 bg-background-100/40 px-5 py-3">
      <button type="button" onClick={() => preview(latest)} className="rounded-lg border border-foreground-200 px-3 py-1.5 text-[12px] font-medium text-foreground-600 hover:border-primary-300 hover:bg-primary-50/60"><i className="ri-eye-line mr-1" />Preview</button>
      <a href={latest.pdfUrl} download="Suitability-RPL-Report.pdf" className="rounded-lg border border-foreground-200 px-3 py-1.5 text-[12px] font-medium text-foreground-600 hover:border-primary-300 hover:bg-primary-50/60"><i className="ri-download-line mr-1" />Download</a>
      {latest.signingEnabled && !latest.signatures.learner.signed && <button type="button" onClick={() => { setSelected(latest); setSignatureOpen(true); }} className="rounded-lg bg-primary-500 px-3 py-1.5 text-[12px] font-semibold text-white hover:bg-primary-600"><i className="ri-quill-pen-line mr-1" />Sign</button>}
    </div>
    {signatureOpen && selected && <div className="border-t border-foreground-200/50 p-5 space-y-3" role="region" aria-label="Sign Suitability and RPL Report">
      {!previewed && <p className="text-[12px] text-amber-800">Preview the exact issued PDF before signing.</p>}
      <button type="button" onClick={() => preview(selected)} className="rounded-lg border border-primary-200 px-3 py-1.5 text-[12px] font-medium text-primary-700"><i className="ri-eye-line mr-1" />Preview issued PDF</button>
      <label className="flex items-start gap-2 text-[12px] text-foreground-700"><input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)} disabled={!previewed || busy} className="mt-0.5" />I have reviewed this exact report version and consent to signing it.</label>
      <SignaturePad signatoryName={signatoryName} onCommit={sign} onCancel={() => setSignatureOpen(false)} />
    </div>}
    {history.length > 0 && <details className="border-t border-foreground-200/50 px-5 py-3"><summary className="cursor-pointer text-[12px] font-semibold text-foreground-700">History ({history.length})</summary><div className="mt-2 space-y-2">{history.map(item => <div key={item.issueId} className="flex items-center justify-between gap-3 text-[12px] text-foreground-500"><span>Version {item.reportVersion} · Revision {item.reportRevision} · Issued {formatDate(item.issuedAt)}</span><button type="button" onClick={() => preview(item)} className="text-primary-700 hover:underline">Preview</button></div>)}</div></details>}
  </section></DocumentSignatureTarget>;
}
