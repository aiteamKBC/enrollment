import { inputClass } from '../components/ui';
import { fieldTypeOptions, type CustomFieldType } from '../wizard/layout/types';

/** Shared by the wizard's New field editor and document presentation fields. */
export function FieldTypeControl({ id, value, onChange, disabled = false, unavailable = {} }: {
  id: string; value: CustomFieldType; onChange: (value: CustomFieldType) => void; disabled?: boolean;
  unavailable?: Partial<Record<CustomFieldType, string>>;
}) {
  return <label className="block text-[12px] font-medium text-foreground-600" htmlFor={id}>Field type
    <select id={id} aria-label="Field type" className={`${inputClass} mt-1 cursor-pointer disabled:cursor-not-allowed disabled:opacity-60`} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value as CustomFieldType)}>
      {fieldTypeOptions(value).map((t) => <option key={t.value} value={t.value} disabled={!!unavailable[t.value]}>{t.label}{unavailable[t.value] ? ` — ${unavailable[t.value]}` : ''}</option>)}
    </select>
  </label>;
}
