import { DocumentSignatureTarget } from '@/features/pending-signatures/DocumentSignatureTarget';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { useLocation } from 'react-router-dom';
import { useAuth } from '@/hooks/useAuth.helpers';
import { Modal } from '@/pages/users/components/Modal';
import { btnPrimary, btnSecondary } from '@/pages/users/components/ui';
import { SignaturePad } from '@/pages/users/wizard/steps/SignaturePad';
import { DocumentPreview } from './DocumentPreview';
import { LearnerRevisionEditor } from './LearnerRevisionEditor';
import { DeleteDraftDialog, deleteDraftClass } from './DeleteDraftDialog';
import { previewPdf } from './previewPdf';
import * as api from './revisionApi';
import type { DocumentKey, PreviewResponse } from './types';
import type { DocumentHistoryControl } from './DocumentHistory';

const templateLabel = (number: number) => number > 0 ? `Template V${number}.0` : 'Original application template';

export function LearnerDocumentRevisions({ learnerId, documentKey, sourceId, historyControl }: { learnerId: string; documentKey: DocumentKey; sourceId?: string | number; historyControl?: DocumentHistoryControl }) {
  const signatureTarget = new URLSearchParams(useLocation().search).get('signature');
  const { auth } = useAuth(); const role = auth.account?.role; const admin = role === 'staff' || role === 'admin';
  const [rows, setRows] = useState<api.DocumentRevision[]>([]), [setup, setSetup] = useState(false), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [setupInfo, setSetupInfo] = useState<api.RevisionStorageSetup | null>(null);
  const [editor, setEditor] = useState<api.RevisionDetail | null>(null), [preview, showPreview] = useState<PreviewResponse | null>(null);
  const [history, showHistory] = useState(false), [compare, setCompare] = useState<api.DocumentRevision | null>(null);
  const [discarding, setDiscarding] = useState<api.DocumentRevision | null>(null), [success, setSuccess] = useState('');
  const [signing, setSigning] = useState<{ revision: api.DocumentRevision; party: string } | null>(null);
  const creating = useRef(false), createRequest = useRef<string | null>(null);
  const load = useCallback(() => api.revisionHistory(learnerId, documentKey).then((v) => { setRows(v.results); setSetup(v.setupRequired); setSetupInfo(v.setup || null); }), [learnerId, documentKey]);
  useEffect(() => { let live = true; const refresh = () => { void load().catch((e: Error) => { if (live) setError(e.message); }); }; refresh(); window.addEventListener('focus', refresh); const timer = window.setInterval(refresh, 60000); return () => { live = false; window.removeEventListener('focus', refresh); window.clearInterval(timer); }; }, [load, signatureTarget]);
  const run = async (action: () => Promise<unknown>) => { setBusy(true); setError(''); try { await action(); } catch (e) { const failure = e as Error & { details?: { setupRequired?: boolean; setup?: api.RevisionStorageSetup } }; setError(failure.message); if (failure.details?.setupRequired) { setSetup(true); setSetupInfo(failure.details.setup || null); } } finally { setBusy(false); } };
  // Review cards use event keys while revision records preserve numeric source IDs.
  const filtered = sourceId != null && !documentKey.startsWith('review-') ? rows.filter((r) => r.sourceId === String(sourceId)) : rows;
  const latestIssuedNumber = Math.max(0, ...filtered.filter(r => r.state === 'finalized').map(r => r.number));
  const onCountChange = historyControl?.onCountChange;
  useEffect(() => { onCountChange?.(filtered.length); }, [onCountChange, filtered.length]);
  const open = (r: api.DocumentRevision) => void run(async () => showPreview((await api.readRevision(learnerId, r.id)).preview));
  const edit = (base?: api.DocumentRevision) => void run(async () => { const source = base?.sourceId || sourceId; if (source == null) throw new Error('An issued source document is required.'); setEditor(await api.startRevision(learnerId, documentKey, String(source), base?.id)); setSetup(false); setSetupInfo(null); await load(); });
  const create = () => {
    if (creating.current || sourceId == null) return;
    creating.current = true;
    createRequest.current ??= crypto.randomUUID();
    void run(async () => {
      try {
        setEditor(await api.createDocumentFromTemplate(learnerId, documentKey, String(sourceId), createRequest.current!));
        setSetup(false); setSetupInfo(null); await load(); createRequest.current = null;
      } finally { creating.current = false; }
    });
  };
  const download = (r: api.DocumentRevision) => void run(async () => { const value = await api.readRevision(learnerId, r.id); const blob = await previewPdf(value.preview); const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = `${documentKey}-R${r.number}.pdf`; link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 30000); });
  const adminActions = (r: api.DocumentRevision) => admin && <>
    <button className={btnSecondary} disabled={busy} onClick={() => r.state === 'draft' ? void run(async () => setEditor(await api.readRevision(learnerId, r.id))) : edit(r)}>{r.state === 'draft' ? 'Continue editing' : 'Edit'}</button>
    <button className={btnSecondary} disabled={busy} onClick={() => setCompare(r)}>Compare</button>
    {r.state === 'draft' && r.canDiscard === true && <button className={deleteDraftClass} disabled={busy} onClick={() => { setSuccess(''); setDiscarding(r); }}><Trash2 size={14} aria-hidden="true" />Delete</button>}
  </>;
  return <section aria-label={`${documentKey} document revisions`} className="mb-3 space-y-2">
    {admin && sourceId != null && <div className="flex flex-wrap gap-2"><button className={btnSecondary} disabled={busy} onClick={historyControl ? create : () => edit()}>{historyControl ? <><Plus size={14} aria-hidden="true" />Create</> : 'Edit'}</button><button className={btnSecondary} disabled={busy} aria-expanded={historyControl?.expanded} aria-controls={historyControl?.panelId} onClick={() => historyControl && filtered.length ? historyControl.toggle() : showHistory(true)}>History</button></div>}
    {admin && setup && <div role="status" className="space-y-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900"><p>{setupInfo?.message || 'Document revision storage has not been installed. Ask your database administrator to review the document storage setup.'}</p>
      {setupInfo?.missingTables.length ? <p>Missing storage: {setupInfo.missingTables.join(', ')}.</p> : null}
      {Boolean(setupInfo?.disabledSettings.length) && <p>Enable {setupInfo!.disabledSettings.join(', ')} in the backend configuration and restart the backend workers.</p>}
      {(!setupInfo || setupInfo.missingTables.length > 0) && <><p>Review the deployment plan, then apply it to an approved database using a direct connection. Shared/production deployment requires approval.</p>
        <p>Plan: <code>{setupInfo?.planCommand || 'python backend/manage.py apply_document_templates --with-revisions --plan'}</code></p>
        <p>Check: <code>{setupInfo?.checkCommand || 'python backend/manage.py apply_document_templates --with-revisions --check'}</code></p></>}
      <p>After setup, click {historyControl ? 'Create' : 'Edit'} again. The server checks the current storage state before opening the editor.</p>
    </div>}
    {error && <p role="alert" className="text-xs text-red-700">{error}</p>}
    {success && <p role="status" className="text-sm text-emerald-700">{success}</p>}
    <div id={historyControl?.panelId} hidden={historyControl ? !historyControl.expanded : undefined} className="space-y-2">
    {(!historyControl || historyControl.expanded) && <>
    {historyControl && admin && filtered.length > 0 && <button type="button" className="ml-4 rounded px-2 py-1 text-xs text-primary-700 underline underline-offset-2 hover:bg-primary-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500" onClick={() => showHistory(true)}>View history details</button>}
    {filtered.map((r) => <DocumentSignatureTarget key={r.id} documentKey={documentKey} sourceId={r.sourceId} revisionId={r.id}><article className="ml-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-primary-100 bg-primary-50/30 p-3">
      <div><p className="text-sm font-semibold">Version {r.number} — {r.number === 1 ? 'Original' : r.state === 'draft' ? 'Draft' : r.signatureMethod === 'keep' ? 'Updated Copy' : r.status}</p><p className="text-xs text-foreground-600">{templateLabel(r.templateVersionNumber)} / Revision R{r.number} · <span>{r.status}</span></p></div>
      <div className="flex flex-wrap gap-2"><button className={btnSecondary} disabled={busy} onClick={() => open(r)}>View</button><button className={btnSecondary} disabled={busy} onClick={() => download(r)}>Download</button>
        {adminActions(r)}
        {r.state === 'finalized' && r.number === latestIssuedNumber && r.signatureMethod === 'sign-again' && !r.fullySigned && Object.keys(r.signatures).filter((party) => (admin ? ['admin', 'provider'] : role === 'employer' ? ['employer'] : role === 'learner' ? ['learner', 'apprentice'] : []).includes(party)).map((party) => <button key={party} className={btnPrimary} disabled={busy || r.signatures[party].signed} onClick={() => setSigning({ revision: r, party })}>{r.signatures[party].signed ? 'Signed by you' : 'Sign revised document'}</button>)}
      </div>
    </article></DocumentSignatureTarget>)}
    </>}
    </div>
    {editor && <LearnerRevisionEditor learnerId={learnerId} initial={editor} onChanged={() => void load()} onClose={() => setEditor(null)} />}
    {preview && <DocumentPreview value={preview} onClose={() => showPreview(null)} />}
    {signing && <Modal title={`Sign Revision R${signing.revision.number}`} onClose={() => setSigning(null)} footer={<button className={btnSecondary} onClick={() => setSigning(null)}>Close</button>}><p className="mb-3 text-sm">Read this revision before signing. Your signature applies to this revised content.</p><button className={`${btnSecondary} mb-3`} onClick={() => open(signing.revision)}>View revised document</button><SignaturePad onCancel={() => setSigning(null)} onCommit={async (signature) => { await api.signRevision(learnerId, signing.revision.id, signing.party, auth.user?.fullName || '', signature); await load(); setSigning(null); }} /></Modal>}
    {history && <Modal title="Document revision history" dismissible={!discarding} onClose={() => showHistory(false)} footer={<button className={btnSecondary} disabled={Boolean(discarding)} onClick={() => showHistory(false)}>Close history</button>}><div className="space-y-3">{filtered.map((r) => <article key={r.id} className="rounded-lg border p-3 text-sm"><p className="font-semibold">{templateLabel(r.templateVersionNumber)} / Revision R{r.number} · {r.status}</p><p>{r.createdBy} · {new Date(r.createdAt).toLocaleString('en-GB')}</p><p className="whitespace-pre-wrap">{r.reason}</p><p>Signature method: {r.signatureMethod || 'Not selected'}</p>{Object.entries(r.provenance).map(([party, v]) => <p key={party}>Historical {party} signature: {v.name}, {v.signedAt}, from R{v.sourceRevision}. No new approval of revised wording.</p>)}<p className="break-all text-xs">Checksum: {r.checksum}</p><div className="mt-2 flex flex-wrap gap-2"><button className={btnSecondary} disabled={busy} onClick={() => open(r)}>View</button><button className={btnSecondary} disabled={busy} onClick={() => download(r)}>Download</button>{adminActions(r)}</div></article>)}</div></Modal>}
    {compare && <Modal title={`Revision R${compare.number} changes`} onClose={() => setCompare(null)} footer={<button className={btnSecondary} onClick={() => setCompare(null)}>Close comparison</button>}><p className="mb-3 text-sm">{compare.reason}</p><pre className="overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(compare.changes, null, 2)}</pre><div className="mt-3 flex gap-2">{filtered.find((r) => r.id === compare.baseId) && <button className={btnSecondary} onClick={() => open(filtered.find((r) => r.id === compare.baseId)!)}>View previous revision</button>}<button className={btnSecondary} onClick={() => open(compare)}>View revised document</button></div></Modal>}
    {discarding && <DeleteDraftDialog learnerId={learnerId} revision={discarding} onCancel={() => setDiscarding(null)} onDeleted={async () => {
      await load();
      if (editor?.revision.id === discarding.id) setEditor(null);
      if (compare?.id === discarding.id) setCompare(null);
      if (preview?.data.documentRevision?.id === discarding.id) showPreview(null);
      setDiscarding(null); setSuccess('Draft revision deleted.');
    }} />}
  </section>;
}
