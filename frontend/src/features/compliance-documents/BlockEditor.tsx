import { TokenText } from './TokenText';
import { QuestionCards } from './QuestionCards';
import { questionFields } from './questionFields';
import { btnSecondary, inputClass } from '@/pages/users/components/ui';
import type { DocumentBlock, DocumentKey, FieldDefinition } from './types';


export function BlockEditor({ block, fields, roles, documentKey, onChange }: { block: DocumentBlock; fields: FieldDefinition[]; roles: string[]; documentKey: DocumentKey; onChange: (block: DocumentBlock) => void }) {
  const patch = (values: Partial<DocumentBlock>) => onChange({ ...block, ...values });
  const text = (label: string, value: string, update: (value: string) => void, multiline = true) => <TokenText label={label} value={value} onChange={update} fields={fields} multiline={multiline} />;
  return <div className="min-w-0 space-y-4" aria-label="Selected content block editor">
    {['heading', 'paragraph', 'declaration', 'date'].includes(block.type) && text('Block text', block.text || '', (value) => patch({ text: value, ...(block.runs ? { runs: [{ text: value }] } : {}) }))}
    {block.type === 'heading' && <label className="block text-sm">Heading size<select className={inputClass} value={block.level || 2} onChange={(e) => patch({ level: Number(e.target.value) })}><option value={1}>Document heading</option><option value={2}>Section heading</option><option value={3}>Small heading</option></select></label>}
    {(block.type === 'field' || block.type === 'questions') && <QuestionCards block={block} documentKey={documentKey} registry={fields} onChange={onChange} />}
    {block.type === 'signature' && <>
      <p className="rounded-lg bg-primary-50 p-3 text-sm">This placeholder uses the existing signature workflow. It does not create a new signatory or change who is allowed to sign.</p>
      <label className="block text-sm">Signing party<select className={inputClass} value={block.role} onChange={(e) => patch({ role: e.target.value })}>{roles.map((role) => <option key={role} value={role}>{role === 'admin' ? 'Provider' : role.charAt(0).toUpperCase() + role.slice(1)}</option>)}</select></label>
      {text('Signature label', block.label || '', (label) => patch({ label }), false)}
      {block.condition === 'employer-required' && <p className="text-xs text-foreground-600">Shown only when the existing review requires an employer signature.</p>}
    </>}
    {block.type === 'page-break' && <p className="text-sm">The next content block starts on a new A4 page.</p>}
    {(block.type === 'table' || block.type === 'native') && <>
      {block.type === 'native' && <><p className="rounded-lg bg-primary-50 p-3 text-sm">This section uses the existing issued document’s curriculum, costs or saved review answers. Its data links and calculations stay fixed; the displayed wording can be edited here.</p>{text('Data block name (editor only)', block.label || '', (label) => patch({ label }), false)}</>}
      {block.columns && !questionFields(block, documentKey).length && <div className="space-y-2"><p className="text-sm font-medium">Columns</p>{block.columns.map((column, i) => <div key={i} className="flex gap-2"><input className={inputClass} aria-label={`Column ${i + 1}`} value={column} onChange={(e) => patch({ columns: block.columns!.map((v, j) => j === i ? e.target.value : v) })} />
        {block.type === 'table' && <button className={btnSecondary} disabled={block.columns!.length < 2} onClick={() => patch({ columns: block.columns!.filter((_, j) => j !== i), rows: block.rows!.map((row) => row.filter((_, j) => j !== i)) })}>Remove column {i + 1}</button>}</div>)}
        {block.type === 'table' && <button className={btnSecondary} disabled={block.columns.length >= 12} onClick={() => patch({ columns: [...block.columns!, 'New column'], rows: block.rows!.map((r) => [...r, '']) })}>Add column</button>}
      </div>}
      {block.type === 'table' && !questionFields(block, documentKey).length && <div className="space-y-3"><p className="text-sm font-medium">Rows</p>{block.rows?.map((row, i) => <div key={i} className="space-y-3 rounded-lg border p-3"><p className="text-xs font-semibold">Row {i + 1}</p>{row.map((cell, j) => <div key={j}>{text(`Row ${i + 1}, ${block.columns?.[j] || `column ${j + 1}`}`, cell, (value) => patch({ rows: block.rows!.map((r, k) => k === i ? r.map((v, l) => l === j ? value : v) : r) }), false)}</div>)}
        <button className={btnSecondary} onClick={() => patch({ rows: block.rows!.filter((_, j) => j !== i) })}>Remove row {i + 1}</button></div>)}<button className={btnSecondary} onClick={() => patch({ rows: [...(block.rows || []), (block.columns || []).map(() => '')] })}>Add row</button></div>}
      {(block.labels || questionFields(block, documentKey).length > 0) && <QuestionCards block={block} documentKey={documentKey} registry={fields} onChange={onChange} />}
    </>}
    {['heading', 'paragraph', 'declaration', 'date'].includes(block.type) && <fieldset className="flex flex-wrap items-end gap-3 rounded-lg border p-3"><legend className="px-1 text-sm font-medium">Text formatting</legend>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={block.style?.bold ?? (block.type === 'heading')} onChange={(e) => patch({ style: { ...block.style, bold: e.target.checked } })} />Bold</label>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!block.style?.italic} onChange={(e) => patch({ style: { ...block.style, italic: e.target.checked } })} />Italic</label>
      <label className="text-sm">Alignment<select className={inputClass} value={block.style?.align || 'left'} onChange={(e) => patch({ style: { ...block.style, align: e.target.value as 'left' } })}><option>left</option><option>center</option><option>right</option></select></label>
      <label className="text-sm">Font size<input type="number" min={7} max={24} className={inputClass} value={block.style?.fontSize || (block.type === 'heading' ? block.level === 1 ? 18 : block.level === 3 ? 10.5 : 12 : 9.5)} onChange={(e) => patch({ style: { ...block.style, fontSize: Number(e.target.value) } })} /></label>
      <label className="text-sm">Space after (mm)<input type="number" min={0} max={20} className={inputClass} value={block.style?.spaceAfter ?? 3} onChange={(e) => patch({ style: { ...block.style, spaceAfter: Number(e.target.value) } })} /></label>
    </fieldset>}
  </div>;
}
