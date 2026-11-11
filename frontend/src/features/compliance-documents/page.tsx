import { useCallback, useEffect, useState } from 'react';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { Modal } from '@/pages/users/components/Modal';
import { btnPrimary, btnSecondary, inputClass } from '@/pages/users/components/ui';
import { BlockEditor } from './BlockEditor';
import { FullPageEditor } from './FullPageEditor';
import { DocumentPreview } from './DocumentPreview';
import * as api from './api';
import seedData from './seed.json';
import { newQuestion, questionFields, questionProblems } from './questionFields';
import { designDraft } from './documentTheme';
import { DocumentDesignControl } from './DocumentDesignControl';
import type { DocumentBlock, DocumentTemplate, FieldDefinition, PreviewResponse, ProgrammeId, TemplateIndex, TemplateVersion } from './types';

const clone = <T,>(value: T): T => structuredClone(value);

export default function ComplianceDocumentsBuilder() {
  const [index, setIndex] = useState<TemplateIndex | null>(null); const [query, search] = useState('');
  const [content, setContent] = useState<DocumentTemplate | null>(null); const [saved, setSaved] = useState('');
  const [baseVersion, setBaseVersion] = useState(''); const [revision, setRevision] = useState(0);
  const [fields, setFields] = useState<FieldDefinition[]>([]); const [active, selectBlock] = useState('');
  const [error, setError] = useState(''); const [message, setMessage] = useState(''); const [busy, setBusy] = useState(false);
  const [preview, showPreview] = useState<PreviewResponse | null>(null); const [reviewedPreview, setReviewedPreview] = useState<PreviewResponse | null>(null);
  const [history, setHistory] = useState<{ id: string; versions: TemplateVersion[] } | null>(null);
  const [learnerId, setLearnerId] = useState(''); const [realPreview, setRealPreview] = useState(false);
  const [confirmed, confirm] = useState(false); const [approvalNote, setApprovalNote] = useState('');
  const [variant, setVariant] = useState<ProgrammeId[] | null>(null);
  const [nativeSource, setNativeSource] = useState('');
  const refresh = useCallback(() => api.listTemplates().then(setIndex), []);
  useEffect(() => { void refresh().catch((e: Error) => setError(e.message)); }, [refresh]);
  const dirty = Boolean(content && JSON.stringify(content) !== saved);
  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', handler); return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);
  const edit = (next: DocumentTemplate) => { setContent(next); setReviewedPreview(null); confirm(false); setMessage(''); };
  const open = async (id: string, discardConfirmed = false) => {
    if (dirty && !discardConfirmed && !window.confirm('Discard unsaved template edits? Saved drafts remain available.')) return;
    setBusy(true); setError(''); setMessage('');
    try {
      const detail = await api.readTemplate(id); const value = detail.draft || detail.published!.content;
      setContent(designDraft(clone(value))); setSaved(JSON.stringify(value)); setBaseVersion(detail.baseVersion); setRevision(detail.revision);
      selectBlock(''); setReviewedPreview(null); confirm(false);
      setFields((await api.readFields(value.documentKey)).results);
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not open document template.'); }
    finally { setBusy(false); }
  };
  const save = async () => {
    if (!content) return;
    const problems = content.blocks.flatMap((b) => questionProblems(questionFields(b, content.documentKey)));
    if (problems.length) { setError(problems[0]); return; }
    setBusy(true); setError('');
    try { const result = await api.saveTemplate(content, baseVersion, revision); setContent(result.content); setSaved(JSON.stringify(result.content)); setRevision(result.revision); setBaseVersion(result.baseVersion); setReviewedPreview(null); confirm(false); await refresh(); setMessage('Draft saved. Published templates and existing issued documents have not changed.'); }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not save draft.'); }
    finally { setBusy(false); }
  };
  const previewSaved = async (id = content?.templateId, version?: string) => {
    if (!id) return;
    setBusy(true); setError('');
    try {
      if (realPreview && (!/^\d+$/.test(learnerId) || Number(learnerId) < 1)) throw new Error('Enter an existing learner identifier for an authorised real-data preview.');
      showPreview(await api.previewTemplate(id, revision, realPreview ? Number(learnerId) : undefined, version));
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not preview document.'); }
    finally { setBusy(false); }
  };
  const acceptPreview = useCallback((value: PreviewResponse) => { if (value.previewToken) setReviewedPreview(value); }, []);
  const publish = async () => {
    if (!content || !reviewedPreview?.previewToken || !confirmed) return;
    setBusy(true); setError('');
    try {
      const result = await api.publishTemplate(content.templateId, revision, reviewedPreview.previewToken, confirmed, approvalNote);
      setContent(result.published.content); setSaved(JSON.stringify(result.published.content)); setBaseVersion(result.published.id); setRevision(0); setReviewedPreview(null); confirm(false); await refresh();
      setMessage(`Published version ${result.published.number}. Future eligible issuance uses it. Existing issued and signed documents keep their original version.`);
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not publish template.'); }
    finally { setBusy(false); }
  };
  const block = content?.blocks.find((b) => b.id === active);
  const scalarIndex = block?.type === 'field' ? content!.blocks.indexOf(block) : -1;
  let groupStart = scalarIndex, groupEnd = scalarIndex;
  if (scalarIndex >= 0) {
    while (groupStart > 0 && content!.blocks[groupStart - 1].type === 'field') groupStart--;
    while (groupEnd + 1 < content!.blocks.length && content!.blocks[groupEnd + 1].type === 'field') groupEnd++;
  }
  const editorBlock: DocumentBlock | undefined = scalarIndex >= 0 ? { id: content!.blocks[groupStart].id, type: 'questions',
    fields: content!.blocks.slice(groupStart, groupEnd + 1).flatMap((b) => questionFields(b, content!.documentKey)) } : block;
  const updateSelected = (updated: DocumentBlock) => {
    if (!content) return;
    if (scalarIndex >= 0) {
      const blocks = [...content.blocks]; blocks.splice(groupStart, groupEnd - groupStart + 1, updated);
      edit({ ...content, blocks }); selectBlock(updated.id);
    } else edit({ ...content, blocks: content.blocks.map((b) => b.id === updated.id ? updated : b) });
  };
  const missingRoles = content?.signatureRoles.filter((role) => !content.blocks.some((b) => b.type === 'signature' && b.role === role)) || [];
  const add = (type: DocumentBlock['type']) => {
    if (!content) return;
    const original = seedData.templates[content.documentKey] as DocumentTemplate;
    const firstNative = original.blocks.find((b) => b.type === 'native' && (!nativeSource || b.source === nativeSource)) || original.blocks.find((b) => b.type === 'native');
    const role = missingRoles[0] || content.signatureRoles[0];
    const next: DocumentBlock = { id: `block-${crypto.randomUUID()}`, type,
      ...(['heading', 'paragraph', 'declaration', 'date'].includes(type) ? { text: type === 'heading' ? 'New heading' : '' } : {}),
      ...(type === 'heading' ? { level: 2 } : {}), ...(type === 'table' ? { columns: ['Column 1', 'Column 2'], rows: [['', '']] } : {}),
      ...(type === 'questions' ? { fields: [newQuestion()] } : {}),
      ...(type === 'signature' ? { role, label: `${role === 'admin' ? 'Provider' : role} signature` } : {}),
      ...(type === 'native' && firstNative ? { ...clone(firstNative), id: `block-${crypto.randomUUID()}` } : {}) };
    const position = content.blocks.findIndex((b) => b.id === active); const blocks = [...content.blocks]; blocks.splice(position < 0 ? blocks.length : position + 1, 0, next);
    edit({ ...content, blocks }); selectBlock(next.id);
  };
  const filtered = index?.results.filter((t) => `${t.title} ${t.category} ${t.programmes.map((p) => index.programmes[p]).join(' ')}`.toLowerCase().includes(query.toLowerCase())) || [];
  const nativeBlocks = content ? (seedData.templates[content.documentKey].blocks as DocumentBlock[]).filter((b) => b.type === 'native') : [];
  const selectedNative = nativeBlocks.find((b) => b.source === nativeSource) || nativeBlocks[0];
  const nativeAlreadyPresent = Boolean(selectedNative && content?.blocks.some((b) => b.type === 'native' && b.source === selectedNative.source && questionFields(b, content.documentKey).length));
  return <WorkspaceShell role="compliance" roleLabel="Enrolment" navItems={[]} pageTitle="Compliance Documents Builder" pageSubtitle="Manage versioned Compliance and Review document templates">
    <div className="min-w-0 space-y-4 p-3 md:p-6">
      <p className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">These documents may contain contractual or compliance wording. Obtain the appropriate organisational review before publishing. Publishing a template does not certify legal compliance. Previously issued and signed documents retain their original content.</p>
      {error && <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}
      {message && <p role="status" className="rounded-lg border border-primary-200 bg-primary-50 p-3 text-sm">{message}</p>}
      {!index && !error && <p role="status">Loading document templates…</p>}
      {index?.setupRequired && <p role="alert" className="rounded-lg border border-amber-200 p-3 text-sm">Document template storage has not been deployed. Current templates can be previewed. Ask the deployment owner to run <code>python manage.py apply_document_templates</code> before saving or publishing.</p>}
      <section aria-label="Document templates" className="rounded-xl border bg-background-50 p-4">
        <label className="block max-w-xl text-sm font-medium">Search document templates<input className={`${inputClass} mt-1`} value={query} onChange={(e) => search(e.target.value)} placeholder="Title, category or programme" /></label>
        <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[780px] text-left text-sm"><thead><tr><th className="p-2">Document title</th><th>Category</th><th>Programmes</th><th>Published version</th><th>Draft</th><th>Last updated</th><th>Actions</th></tr></thead><tbody>
          {filtered.map((t) => <tr key={t.templateId} className="border-t align-top"><th className="p-2">{t.title}</th><td className="p-2">{t.category}</td><td className="p-2">{t.programmes.length ? t.programmes.map((p) => index!.programmes[p]).join(', ') : 'Shared — all programmes'}</td><td className="p-2">{t.publishedVersion ? t.publishedVersion.number ? `v${t.publishedVersion.number}` : 'Original' : 'Not published'}</td><td className="p-2">{t.draft ? 'Saved draft' : '—'}</td><td className="p-2">{t.updatedAt ? new Date(t.updatedAt).toLocaleString('en-GB') : 'Original template'}</td><td className="p-2"><div className="flex flex-wrap gap-2">
            <button className={btnSecondary} disabled={busy} aria-label={`Edit ${t.title}`} onClick={() => void open(t.templateId)}>Edit</button>
            <button className={btnSecondary} disabled={busy || !t.publishedVersion} aria-label={`Preview ${t.title}`} onClick={() => void previewSaved(t.templateId, t.publishedVersion?.id)}>Preview</button>
            <button className={btnSecondary} disabled={busy} aria-label={`Version history for ${t.title}`} onClick={() => { setBusy(true); void api.templateHistory(t.templateId).then((data) => setHistory({ id: t.templateId, versions: data.results })).catch((e: Error) => setError(e.message)).finally(() => setBusy(false)); }}>Version history</button>
          </div></td></tr>)}
        </tbody></table>{index && !filtered.length && <p className="p-3 text-sm">No matching templates.</p>}</div>
      </section>
      {content && <section aria-label="Document template editor" className="min-w-0 space-y-4 rounded-xl border bg-background-50 p-4">
        <div className="flex flex-wrap items-end gap-4"><label className="min-w-60 flex-1 text-sm font-medium">Document title<input className={`${inputClass} mt-1`} value={content.title} onChange={(e) => edit({ ...content, title: e.target.value })} /></label>
          <p className="text-sm">{content.category} · {content.programmes.length ? content.programmes.map((p) => index!.programmes[p]).join(', ') : 'Shared across all programmes'}</p>
          <DocumentDesignControl content={content} onChange={edit} />
          <button className={btnSecondary} disabled={busy} onClick={() => setVariant([])}>Create programme variant</button></div>
        {(content.designTheme === 'lavender-v1' || content.designTheme === 'purple-reference-v2') && <p className="text-xs text-foreground-600">{content.designTheme === 'purple-reference-v2' ? 'The KBC & DfE purple branded design' : 'Light-purple design'} is applied to this editable draft. Save, preview and publish a new version to use it for future issuance. Existing issued documents and published versions retain their original design. Individual formatting overrides are preserved.</p>}
        {content.templateId !== content.documentKey && <fieldset className="flex flex-wrap gap-3 rounded-lg border p-3"><legend className="px-1 text-sm font-medium">Applicable programmes</legend>{Object.entries(index?.programmes || {}).map(([pid, title]) => <label key={pid} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={content.programmes.includes(pid as ProgrammeId)} onChange={(e) => edit({ ...content, programmes: e.target.checked ? [...content.programmes, pid as ProgrammeId] : content.programmes.filter((p) => p !== pid) })} />{title}</label>)}</fieldset>}
        <div className="flex flex-wrap items-center gap-2"><button className={btnPrimary} disabled={busy || index?.setupRequired} onClick={() => void save()}>Save Draft</button>
          <button className={btnSecondary} disabled={busy || dirty || !revision} onClick={() => void previewSaved()}>Preview saved draft</button>
          <button className={btnSecondary} disabled={busy || (!dirty && !revision)} onClick={() => { if (window.confirm('Discard this saved draft and local edits? Published and issued documents are unchanged.')) { setBusy(true); void (revision ? api.deleteDraft(content.templateId, baseVersion, revision) : Promise.resolve()).then(async () => { await refresh(); if (content.templateId === content.documentKey || index?.results.find((t) => t.templateId === content.templateId)?.publishedVersion) await open(content.templateId, true); else setContent(null); }).catch((e: Error) => setError(e.message)).finally(() => setBusy(false)); } }}>Discard draft</button>
          <span className="text-sm text-foreground-600">{dirty ? 'Unsaved changes' : revision ? `Saved draft revision ${revision}` : 'Published content'}</span></div>
        <div className="flex flex-wrap items-end gap-3 rounded-lg bg-background-100 p-3"><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={realPreview} onChange={(e) => setRealPreview(e.target.checked)} />Use authorised real learner data for preview</label>{realPreview ? <label className="text-sm">Existing learner ID<input className={inputClass} value={learnerId} inputMode="numeric" onChange={(e) => setLearnerId(e.target.value)} /></label> : <span className="text-xs text-foreground-600">Preview uses clearly labelled sample values.</span>}</div>
        {missingRoles.length > 0 && <p role="alert" className="text-sm text-red-700">Restore required signature placeholders before publishing: {missingRoles.join(', ')}.</p>}
        <FullPageEditor key={content.templateId} content={content} fields={fields} onChange={edit} onConfigure={selectBlock} />
        {editorBlock && <Modal title="Linked section and field settings" onClose={() => selectBlock('')} footer={<button className={btnSecondary} onClick={() => selectBlock('')}>Done</button>}><BlockEditor key={editorBlock.id} documentKey={content.documentKey} block={editorBlock} roles={content.signatureRoles} fields={fields} onChange={updateSelected} /></Modal>}
        <div className="flex flex-wrap gap-2">{seedData.templates[content.documentKey].blocks.some((b) => b.type === 'native') && <label className="text-sm">Existing data section<select className={inputClass} value={nativeSource} onChange={(e) => setNativeSource(e.target.value)}><option value="">Choose a section to insert</option>{seedData.templates[content.documentKey].blocks.filter((b) => b.type === 'native').map((b) => <option key={b.id} value={(b as DocumentBlock).source}>{(b as DocumentBlock).label}</option>)}</select></label>}{(['questions', 'heading', 'paragraph', 'declaration', 'table', 'date', 'signature', 'page-break', ...(seedData.templates[content.documentKey].blocks.some((b) => b.type === 'native') ? ['native'] : [])] as DocumentBlock['type'][]).map((type) => <button key={type} className={btnSecondary} disabled={(type === 'signature' && !missingRoles.length) || (type === 'native' && nativeAlreadyPresent)} title={type === 'native' && nativeAlreadyPresent ? 'This system-linked section is already present. Edit its field cards instead of duplicating its data relationship.' : undefined} onClick={() => add(type)}>{type === 'questions' ? 'Add Field' : `Add ${type === 'native' ? 'existing data section' : type}`}</button>)}</div>
        <div className="space-y-3 rounded-lg border border-amber-200 p-4"><label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={confirmed} onChange={(e) => confirm(e.target.checked)} />I confirm this wording has received the appropriate organisational review for publication.</label>
          <label className="block text-sm">Publication / approval note<textarea className={`${inputClass} mt-1`} rows={2} maxLength={2000} value={approvalNote} onChange={(e) => setApprovalNote(e.target.value)} /></label>
          <button className={btnPrimary} disabled={busy || dirty || !reviewedPreview?.previewToken || !confirmed || missingRoles.length > 0} onClick={() => void publish()}>Publish new version</button>
          <p className="text-xs text-foreground-600">Save and preview this draft before publishing. Publishing affects future eligible issuance; existing signatures, PDFs and uploads are retained.</p></div>
      </section>}
      {variant && content && <Modal title="Create programme-specific template" onClose={() => setVariant(null)} footer={<><button className={btnSecondary} onClick={() => setVariant(null)}>Cancel</button><button className={btnPrimary} disabled={!variant.length} onClick={() => { const next = { ...clone(content), templateId: `${content.documentKey}@${crypto.randomUUID()}`, programmes: variant }; setContent(next); setSaved(''); setBaseVersion('new'); setRevision(0); setReviewedPreview(null); confirm(false); setVariant(null); setMessage('Programme variant created locally. Save, preview and publish it; the shared template remains unchanged.'); }}>Create draft variant</button></>}><p className="mb-3 text-sm">The shared template remains available to other programmes. Published programme variants cannot overlap.</p><div className="space-y-3">{Object.entries(index?.programmes || {}).map(([pid, title]) => <label key={pid} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={variant.includes(pid as ProgrammeId)} onChange={(e) => setVariant(e.target.checked ? [...variant, pid as ProgrammeId] : variant.filter((p) => p !== pid))} />{title}</label>)}</div></Modal>}
      {history && <Modal title="Document template version history" onClose={() => setHistory(null)} footer={<button className={btnSecondary} onClick={() => setHistory(null)}>Close history</button>}><div className="space-y-3">{history.versions.map((v) => <article key={v.id} className="rounded-lg border p-3 text-sm"><p className="font-semibold">{v.number ? `Version ${v.number}` : 'Original application template'} — {v.content.title}</p><p>{v.publishedAt ? new Date(v.publishedAt).toLocaleString('en-GB') : 'Original wording'} · {v.publishedBy}</p>{v.approvalNote && <p className="mt-1 whitespace-pre-wrap">{v.approvalNote}</p>}<button className={`${btnSecondary} mt-2`} disabled={busy} onClick={() => void previewSaved(history.id, v.id)}>Preview historical PDF</button></article>)}{!history.versions.length && <p>No published versions yet.</p>}</div></Modal>}
      {preview && <DocumentPreview value={preview} onClose={() => showPreview(null)} onReady={acceptPreview} />}
    </div>
  </WorkspaceShell>;
}
