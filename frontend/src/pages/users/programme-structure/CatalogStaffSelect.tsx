import { useId, useState } from 'react';
import { SelectMenu } from '@/components/feature/SelectField';
import { btnSecondary } from '@/pages/users/components/ui';
import type { Staff, StaffOptions } from './catalogApi';
import { catalogueFocus, catalogueInput } from './catalogControls';

export type StaffDirectory = {
  data: StaffOptions | null;
  loading?: boolean;
  error?: string | null;
  retry?: () => void;
  addPreviewStaff?: (role: keyof StaffOptions, member: Staff) => void;
};

export function CatalogStaffSelect({ id, label, role, directory, value, onChange, required = false }: {
  id: string; label: string; role: keyof StaffOptions; directory: StaffDirectory;
  value: string; onChange: (id: string) => void; required?: boolean;
}) {
  const { data, loading, error, retry, addPreviewStaff } = directory;
  const people = data?.[role] ?? [];
  const selected = people.find(person => person.id === value);
  const assignmentUnavailable = Boolean(value && data && !selected);
  const options = people.map(person => ({ value: person.id, label: person.name, description: person.email }));
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const previewFieldId = useId();
  const roleName = label.toLowerCase();
  const rolePlural = role === 'coaches' ? 'skills coaches' : 'tutors';
  const emailInvalid = email.trim() !== '' && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());
  const nameField = `${previewFieldId}-name`;

  return <>
    <SelectMenu
      id={id} ariaLabel={label} ariaRequired={required || undefined} value={value} onChange={onChange}
      options={assignmentUnavailable ? [
        { value, label: 'Previous assignment (unavailable)', description: '', disabled: true }, ...options,
      ] : options}
      searchable clearable={!required} placeholder={value && !data ? 'Existing assignment' : required ? `Select a ${roleName}` : 'Unassigned'}
      searchPlaceholder={`Search ${roleName} by name or email`}
      emptyMessage={`No matching ${rolePlural}.`}
      disabled={Boolean(loading || !data)} triggerClassName={catalogueFocus}
    />
    {loading ? <p role="status" className="text-xs text-foreground-600">Loading staff options…</p> : error ? (
      <div className="flex flex-wrap items-center gap-2 text-xs text-foreground-700">
        <p role="status">Could not load staff options. Existing assignments are kept.</p>
        <button type="button" className={`${btnSecondary} ${catalogueFocus}`} onClick={retry}>Retry staff directory</button>
      </div>
    ) : <>
      {assignmentUnavailable && <p role="status" className="text-xs text-foreground-700">
        This assignment is inactive or no longer eligible. It will be kept unless you change it.
      </p>}
      {selected?.email && <p className="break-words text-xs text-foreground-700">{selected.email}</p>}
      {!people.length && <p className="text-xs text-foreground-600">
        {addPreviewStaff ? `No preview ${rolePlural} added.` : `No eligible active ${rolePlural}.`}
      </p>}
    </>}
    {addPreviewStaff && (adding ? (
      <div className="space-y-2 rounded-lg border border-background-200 p-3"
        onKeyDown={event => { if (event.key === 'Enter' && event.target instanceof HTMLInputElement) event.preventDefault(); }}>
        <p className="text-xs text-foreground-600">Use sample details. This entry exists only in this preview session.</p>
        <label htmlFor={nameField} className="block font-medium">Preview {roleName} name</label>
        <input id={nameField} className={catalogueInput} value={name} maxLength={200}
          onChange={event => setName(event.currentTarget.value)} />
        <label htmlFor={`${previewFieldId}-email`} className="block font-medium">Preview {roleName} email (optional)</label>
        <input id={`${previewFieldId}-email`} className={catalogueInput} inputMode="email" value={email} maxLength={254}
          aria-invalid={emailInvalid} onChange={event => setEmail(event.currentTarget.value)} />
        {emailInvalid && <p className="text-xs text-red-800">Enter a valid email address.</p>}
        <div className="flex flex-wrap gap-2">
          <button type="button" className={`${btnSecondary} ${catalogueFocus}`} onClick={() => setAdding(false)}>Cancel preview entry</button>
          <button type="button" className={`${btnSecondary} ${catalogueFocus}`} disabled={!name.trim() || emailInvalid}
            onClick={() => {
              addPreviewStaff(role, { id: crypto.randomUUID(), name: name.trim(), email: email.trim() });
              setAdding(false);
              setName('');
              setEmail('');
              document.getElementById(id)?.focus();
            }}>Add to preview staff</button>
        </div>
      </div>
    ) : <button type="button" className={`${btnSecondary} ${catalogueFocus}`} onClick={() => {
      setAdding(true);
      requestAnimationFrame(() => document.getElementById(nameField)?.focus());
    }}>Add preview {roleName}</button>)}
  </>;
}
