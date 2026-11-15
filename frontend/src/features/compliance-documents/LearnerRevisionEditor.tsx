import { useEffect, useState } from 'react';
import { btnPrimary, btnSecondary, inputClass } from '@/pages/users/components/ui';
import { FullPageEditor } from './FullPageEditor';
import { DocumentDesignControl } from './DocumentDesignControl';
import { DocumentPreview } from './DocumentPreview';
import { BlockEditor } from './BlockEditor';
import { Modal } from '@/pages/users/components/Modal';
import { readFields } from './api';
import * as api from './revisionApi';
import type { FieldDefinition, PreviewResponse } from './types';

export function LearnerRevisionEditor({ learnerId, initial, onClose, onChanged }: {
  learnerId: string; initial: api.RevisionDetail; onClose: () => void; onChanged: () => void;
}) {
  const [detail, setDetail] = useState(initial), [content, setContent] = useState(initial.preview.template.content);
  const [historical, setHistorical] = useState(initial.preview.data);
  const [reason, setReason] = useState(initial.revision.reason), [method, setMethod] = useState(initial.revision.signatureMethod);
  const [fields, setFields] = useState<FieldDefinition[]>([]), [selected, select] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [preview, showPreview] = useState<PreviewResponse | null>(null), [reviewed, setReviewed] = useState<PreviewResponse | null>(null);
  const blank = ['blank', 'template', 'learner'].includes(initial.revision.creationMode || '');
  const dirty = JSON.stringify(content) !== JSON.stringify(detail.preview.template.content) || reason !== detail.revision.reason || method !== detail.revision.signatureMethod;
  useEffect(() => { void readFields(content.documentKey).then((v) => setFields(v.results)).catch((e: Error) => setError(e.message)); }, [content.documentKey]);
  useEffect(() => { let active = true; if (!blank && initial.revision.baseId) void api.readRevision(learnerId, initial.revision.baseId).then((v) => { if (active) setHistorical(v.preview.data); }).catch((e: Error) => { if (active) setError(e.message); }); return () => { active = false; }; }, [initial.revision.baseId, learnerId, blank]);
  useEffect(() => { if (!dirty) return; const prevent = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; }; window.addEventListener('beforeunload', prevent); return () => window.removeEventListener('beforeunload', prevent); }, [dirty]);
  const run = async (action: () => Promise<void>) => { setBusy(true); setError(''); try { await action(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } };
  const block = content.blocks.find((b) => b.id === selected);
  const display = { ...detail.preview.data, marks: historical.marks, signatures: historical.signatures, signatureProvenance: initial.revision.provenance };
  if (method === 'sign-again') {
    display.marks = {};
    display.signatures = Object.fromEntries(content.signatureRoles.map((role) => [role, { ...historical.signatures?.[role], signed: false, signature: '', name: '', signedAt: null, position: '' }]));
    display.signatureProvenance = {};
  }
  display.template = { ...detail.preview.template, fields: { ...detail.preview.template.fields } };
  content.signatureRoles.forEach((role) => { display.template.fields[`signature.${role}.name`] = display.signatures?.[role]?.name || ''; display.template.fields[`signature.${role}.date`] = display.signatures?.[role]?.signedAt || ''; });
  return <div role="dialog" aria-modal="true" aria-label="Learner document revision editor" className="fixed inset-0 z-40 overflow-auto bg-background-100 p-4 md:p-8">
    <div className="mx-auto max-w-[100rem] space-y-4 rounded-2xl bg-background-50 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-xl font-semibold">{blank ? `New Document — Version ${detail.revision.number} (Draft)` : content.title}</h2><p className="text-sm">{detail.revision.templateVersionNumber > 0 ? `Template V${detail.revision.templateVersionNumber}.0` : 'Original application template'} / Revision R{detail.revision.number} · Learner-specific draft</p>{blank && <p className="text-sm">Learner: {initial.preview.data.learnerName || initial.preview.data.learnerInformation?.name || initial.preview.template.fields?.['learner.fullName']} · Document type: {content.title}</p>}</div><button className={btnSecondary} onClick={() => { if (!dirty || window.confirm('Close with unsaved edits? Your saved draft remains available.')) onClose(); }}>Close editor</button></div>
      <p className="text-sm text-foreground-600">This copy belongs only to this learner. Original documents and published templates remain available in history.</p>
      <DocumentDesignControl content={content} presentationTheme={detail.preview.template.presentationTheme} onChange={value => { setContent(value); setReviewed(null); }} />
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      <FullPageEditor content={content} fields={fields} data={display} presentationTheme={detail.preview.template.presentationTheme || 'legacy'} onConfigure={select} onChange={(v) => { setContent(v); setReviewed(null); }} />
      {block && <Modal title="Linked field settings" onClose={() => select('')} footer={<button className={btnSecondary} onClick={() => select('')}>Done</button>}><BlockEditor block={block} documentKey={content.documentKey} roles={content.signatureRoles} fields={fields} onChange={(v) => { setContent({ ...content, blocks: content.blocks.map((b) => b.id === v.id ? v : b) }); setReviewed(null); }} /></Modal>}
      <section className="space-y-3 rounded-xl border border-primary-200 p-4"><h3 className="font-semibold">{blank ? 'New document signatures' : 'Signature Options'}</h3>
        {blank ? <p className="text-sm">{initial.revision.creationMode === 'learner' ? 'The latest published template includes current data for this learner. Review the saved PDF before finalising. ' : initial.revision.creationMode === 'template' ? 'The latest published template questions are shown without answers. ' : ''}This new document starts unsigned. Fresh signature requests are issued only after finalisation.</p> : <div className="grid gap-3 md:grid-cols-2">{(['keep', 'sign-again'] as const).map((value) => <div key={value} className="space-y-2 rounded-lg border p-3"><button className={method === value ? btnPrimary : btnSecondary} aria-pressed={method === value} onClick={() => { setMethod(value); setReviewed(null); }}>{value === 'keep' ? 'Keep Existing Signatures' : 'Sign Again'}</button><p className="text-sm">{value === 'keep' ? "Keep the previous document's visible signature images as historical references in this revised copy. This does not record a new signature or approval of the changes." : 'Request fresh signatures on this revised document using the existing signing workflow.'}</p></div>)}</div>}
        {!blank && method === 'sign-again' && <details className="rounded-lg border p-3 text-sm"><summary>Previous signature references</summary>{content.signatureRoles.map((role) => { const state = historical.signatures?.[role], mark = historical.marks?.[role] || state?.signature; return state?.signed ? <div key={role} className="mt-3"><p>{role}: {state.name} · {state.signedAt} · Revision R{initial.revision.provenance[role]?.sourceRevision || initial.revision.number - 1}</p>{typeof mark === 'string' && /^data:image\/(png|jpeg|webp);base64,[a-z0-9+/=]+$/i.test(mark) && <img className="h-16 max-w-64 object-contain" src={mark} alt={`Historical ${role} signature`} />}<p className="text-xs">Historical approval of the earlier revision. A fresh signature is required for this copy.</p></div> : null; })}</details>}
        <label className="block text-sm">{blank ? 'Reason for creating document (optional)' : 'Reason for change (required)'}<textarea className={`${inputClass} mt-1`} rows={3} maxLength={2000} value={reason} onChange={(e) => { setReason(e.target.value); setReviewed(null); }} /></label>
        <div className="flex flex-wrap gap-3"><button className={btnPrimary} disabled={busy || (!blank && !reason.trim())} onClick={() => void run(async () => { const value = await api.saveRevision(learnerId, detail.revision.id, detail.revision.editVersion, content, reason, method); setDetail(value); setContent(value.preview.template.content); setReviewed(null); onChanged(); })}>Save revision draft</button>
          <button className={btnSecondary} disabled={busy || dirty || !method || (!blank && !reason.trim())} onClick={() => void run(async () => showPreview(await api.previewRevision(learnerId, detail.revision.id, detail.revision.editVersion)))}>Preview saved revision</button>
          <button className={btnPrimary} disabled={busy || dirty || !reviewed?.previewToken || !method || (!blank && !reason.trim())} onClick={() => void run(async () => { await api.finalizeRevision(learnerId, detail.revision.id, reviewed!.previewToken!); onChanged(); onClose(); })}>Finalise revision</button></div>
        <p className="text-xs text-foreground-600">Finalisation makes this content read-only and available to the learner. Further edits create the next revision. Preview the saved signature choice first.</p>
      </section>
    </div>
    {preview && <DocumentPreview value={preview} onReady={setReviewed} onClose={() => showPreview(null)} />}
  </div>;
}
