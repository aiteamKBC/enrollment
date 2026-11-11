import { useRef, useState } from 'react';
import { btnSecondary, inputClass } from '@/pages/users/components/ui';
import type { FieldDefinition } from './types';

export function TokenText({ label, value, onChange, fields, multiline = true }: { label: string; value: string; onChange: (v: string) => void; fields: FieldDefinition[]; multiline?: boolean }) {
  const ref = useRef<HTMLTextAreaElement | null>(null); const [selected, select] = useState('');
  const insert = () => {
    if (!selected) return;
    const start = ref.current?.selectionStart ?? value.length; const end = ref.current?.selectionEnd ?? start;
    onChange(value.slice(0, start) + '{{' + selected + '}}' + value.slice(end));
    ref.current?.focus();
  };
  return <div className="space-y-2"><label className="block text-sm font-medium">{label}<textarea ref={ref} aria-label={label} rows={multiline ? 4 : 1} value={value} onChange={(e) => onChange(e.target.value)} className={`${inputClass} mt-1 whitespace-pre-wrap`} /></label>
    <div className="flex flex-wrap items-center gap-2"><select aria-label={`Dynamic field for ${label}`} value={selected} onChange={(e) => select(e.target.value)} className={`${inputClass} !w-auto max-w-full !py-1 text-xs`}>
      <option value="">Choose a learner or document field…</option>{[...new Set(fields.map((f) => f.group))].map((group) => <optgroup key={group} label={group}>{fields.filter((f) => f.group === group).map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}</optgroup>)}
    </select><button type="button" className={`${btnSecondary} !py-1`} disabled={!selected} onClick={insert}>Insert field</button></div>
  </div>;
}

