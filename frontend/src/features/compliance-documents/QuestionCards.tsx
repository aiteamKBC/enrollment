import { useState } from 'react';
import { btnSecondary, inputClass } from '@/pages/users/components/ui';
import { FieldTypeControl } from '@/pages/users/wizard-builder/FieldTypeControl';
import { isContentFieldType } from '@/pages/users/wizard/layout/types';
import { TokenText } from './TokenText';
import { newQuestion, questionFields, questionProblems, withQuestionFields } from './questionFields';
import type { DocumentBlock, DocumentKey, DocumentQuestion, FieldDefinition } from './types';

const locked = 'This field is system-linked. Wording and help text can be edited, but its data type, source, choices and workflow settings are fixed.';

export function QuestionCards({ block, documentKey, registry, onChange }: {
  block: DocumentBlock; documentKey: DocumentKey; registry: FieldDefinition[]; onChange: (block: DocumentBlock) => void;
}) {
  const fields = questionFields(block, documentKey);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(block.type === 'questions' && fields.length === 1 ? [fields[0].id] : []));
  const update = (next: DocumentQuestion[]) => onChange(withQuestionFields(block, next));
  const patch = (id: string, value: Partial<DocumentQuestion>) => update(fields.map((f) => f.id === id ? { ...f, ...value } : f));
  const move = (index: number, delta: number) => { const next = [...fields]; const target = index + delta; if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]]; update(next); };
  const add = () => { const field = newQuestion(); update([...fields, field]); setExpanded(new Set([...expanded, field.id])); };
  const issues = questionProblems(fields);
  return <div className="space-y-3" aria-label="Question field cards">
    <p className="text-xs text-foreground-600">Document presentation fields use configured values or existing data placeholders. They do not create new learner-answer storage. Mandatory custom values must be supplied before publication.</p>
    {issues.length > 0 && <ul role="alert" className="list-inside list-disc rounded-lg border border-red-200 p-3 text-xs text-red-700">{[...new Set(issues)].map((v) => <li key={v}>{v}</li>)}</ul>}
    {fields.map((field, index) => {
      const open = expanded.has(field.id); const content = isContentFieldType(field.type);
      const controllers = fields.slice(0, index).filter((f) => f.options.length > 0);
      const source = controllers.find((f) => f.id === field.condition?.field);
      const choice = ['dropdown', 'radio', 'checkbox'].includes(field.type);
      return <article key={field.id} className="min-w-0 rounded-xl border border-primary-200 bg-primary-50/30" aria-label={`Question ${index + 1}: ${field.label}`}>
        <header className="flex flex-wrap items-start justify-between gap-2 p-3">
          <button type="button" className="min-w-32 flex-1 text-left" aria-expanded={open} aria-controls={`question-${field.id}`} onClick={() => setExpanded(new Set(open ? [...expanded].filter((id) => id !== field.id) : [...expanded, field.id]))}>
            <span className="block break-words text-sm font-semibold">{field.label || 'Untitled field'}{field.required ? ' *' : ''}</span>
            <span className="text-xs text-foreground-600">{field.type.replaceAll('_', ' ')}{field.systemLinked ? ' · System-linked' : ''} · {open ? 'Collapse' : 'Expand'}</span>
          </button>
          <div className="flex flex-wrap gap-1" role="group" aria-label={`Actions for ${field.label}`}>
            <button type="button" className={btnSecondary} aria-label={`Move question ${index + 1} up`} disabled={index === 0} onClick={() => move(index, -1)}>↑</button>
            <button type="button" className={btnSecondary} aria-label={`Move question ${index + 1} down`} disabled={index === fields.length - 1} onClick={() => move(index, 1)}>↓</button>
            <button type="button" className={btnSecondary} disabled={field.systemLinked} title={field.systemLinked ? 'Duplicating this field would duplicate its fixed data relationship.' : undefined} onClick={() => {
              const copy = { ...structuredClone(field), id: `question-${crypto.randomUUID()}` }; const next = [...fields]; next.splice(index + 1, 0, copy); update(next); setExpanded(new Set([...expanded, copy.id]));
            }}>Duplicate</button>
            <button type="button" className={btnSecondary} disabled={field.systemLinked} title={field.systemLinked ? 'This field is required by the existing document data workflow.' : undefined} onClick={() => {
              if (window.confirm('Delete this field from the draft? Historical answers and issued or signed documents are preserved. Conditional references must be corrected before saving.')) update(fields.filter((f) => f.id !== field.id));
            }}>Delete</button>
          </div>
        </header>
        {open && <div id={`question-${field.id}`} className="space-y-4 border-t border-primary-100 bg-background-50 p-4">
          {field.systemLinked && <p className="rounded-lg bg-primary-50 p-3 text-xs text-primary-800">{locked} Reordering only changes presentation within this section. Duplicate and Delete are unavailable.</p>}
          <TokenText label="Question / Label" value={field.label} fields={registry} onChange={(label) => patch(field.id, { label })} />
          <FieldTypeControl id={`field-type-${field.id}`} value={field.type} disabled={field.systemLinked} unavailable={{ upload: 'use the existing Uploaded Documents workflow' }} onChange={(type) => {
            const choices = ['dropdown', 'radio', 'checkbox'].includes(type);
            patch(field.id, { type, options: choices ? field.options.length ? field.options : [{ id: `option-${crypto.randomUUID()}`, label: 'Option 1' }] : [],
              defaultOptionIds: choices ? type === 'checkbox' ? field.defaultOptionIds : field.defaultOptionIds.slice(0, 1) : [],
              value: type === 'number' ? '' : field.value, min: undefined, max: undefined, required: isContentFieldType(type) ? false : field.required });
          }} />
          <TokenText label="Help text" value={field.helpText} fields={registry} onChange={(helpText) => patch(field.id, { helpText })} />
          {!content && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={field.required} disabled={field.systemLinked} onChange={(e) => patch(field.id, { required: e.target.checked })} />Mandatory{!field.systemLinked && ' — configured document value required before publication'}</label>}
          {!field.systemLinked && !content && !choice && <TokenText label="Configured document value" value={field.value} fields={registry} multiline={field.type === 'multi_line'} onChange={(value) => patch(field.id, { value, ...(field.type === 'number' && value.includes('{{') ? { min: undefined, max: undefined } : {}) })} />}
          {field.type === 'number' && !field.systemLinked && <div><div className="flex gap-3">{(['min', 'max'] as const).map((name) => <label key={name} className="text-sm">{name === 'min' ? 'Minimum' : 'Maximum'}<input type="number" className={inputClass} disabled={field.value.includes('{{')} value={field[name] ?? ''} onChange={(e) => patch(field.id, { [name]: e.target.value === '' ? undefined : Number(e.target.value) })} /></label>)}</div>{field.value.includes('{{') && <p className="mt-1 text-xs">Numeric limits apply to configured constants. Source data keeps its original validation.</p>}</div>}
          {choice && <fieldset className="space-y-2 rounded-lg border p-3"><legend className="px-1 text-sm font-medium">Choice options</legend>
            {field.options.map((option, i) => <div key={option.id} className="flex flex-wrap items-center gap-2">
              <label className="min-w-32 flex-1 text-xs">Option {i + 1}<input className={inputClass} value={option.label} disabled={field.systemLinked} onChange={(e) => patch(field.id, { options: field.options.map((o) => o.id === option.id ? { ...o, label: e.target.value } : o) })} /></label>
              <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={field.defaultOptionIds.includes(option.id)} disabled={field.systemLinked} onChange={(e) => patch(field.id, { defaultOptionIds: e.target.checked ? field.type === 'checkbox' ? [...field.defaultOptionIds, option.id] : [option.id] : field.defaultOptionIds.filter((v) => v !== option.id) })} />Default {i + 1}</label>
              <button className={btnSecondary} disabled={field.systemLinked || i === 0} aria-label={`Move option ${i + 1} up`} onClick={() => { const options = [...field.options]; [options[i - 1], options[i]] = [options[i], options[i - 1]]; patch(field.id, { options }); }}>↑</button>
              <button className={btnSecondary} disabled={field.systemLinked || i === field.options.length - 1} aria-label={`Move option ${i + 1} down`} onClick={() => { const options = [...field.options]; [options[i + 1], options[i]] = [options[i], options[i + 1]]; patch(field.id, { options }); }}>↓</button>
              <button className={btnSecondary} disabled={field.systemLinked} aria-label={`Remove option ${i + 1}`} onClick={() => patch(field.id, { options: field.options.filter((o) => o.id !== option.id), defaultOptionIds: field.defaultOptionIds.filter((v) => v !== option.id) })}>Remove</button>
            </div>)}
            {!field.systemLinked && <button className={btnSecondary} onClick={() => patch(field.id, { options: [...field.options, { id: `option-${crypto.randomUUID()}`, label: '' }] })}>Add option</button>}
          </fieldset>}
          <fieldset className="space-y-3 rounded-lg border p-3"><legend className="px-1 text-sm font-medium">Conditional display</legend>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!field.condition} disabled={field.systemLinked} onChange={(e) => patch(field.id, { condition: e.target.checked ? { field: controllers[0]?.id || '', values: [] } : null })} />Show only when a preceding field matches</label>
            {field.condition && <><label className="block text-sm">Controlling field<select className={inputClass} value={field.condition.field} onChange={(e) => patch(field.id, { condition: { field: e.target.value, values: [] } })}><option value="">Choose a preceding choice field</option>{controllers.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}</select></label>
              {source?.options.map((option) => <label key={option.id} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={field.condition!.values.includes(option.id)} onChange={(e) => patch(field.id, { condition: { field: source.id, values: e.target.checked ? [...field.condition!.values, option.id] : field.condition!.values.filter((v) => v !== option.id) } })} />{option.label}</label>)}
            </>}
          </fieldset>
        </div>}
      </article>;
    })}
    {block.type !== 'field' && <button className={btnSecondary} onClick={add}>Add Field</button>}
  </div>;
}
