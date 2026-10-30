import { useState } from 'react';
import { useToast } from "@/hooks/useToast.helpers";
import { updateStaffUser, POSITION_OPTIONS, type StaffUserRow } from '@/api/staffUsers';
import { STATUS_OPTIONS } from '@/api/enrolmentUsers';
import { Modal } from './Modal';
import { inputClass, btnPrimary, btnSecondary } from './ui';

// ============================================================================
// Edit admin / caseowner — the counterpart to CreateStaffModal.
//
// Staff accounts have no learner profile or wizard, so their row in the users
// directory is the only place they exist in the UI. This modal is that row's
// edit surface: the everyday details (name, email, contact, position, status)
// that go stale, PATCHed to /learner_api/staff-users/<id>/ — the same
// Staff_users columns the create form writes.
//
// Only changed fields are sent: a PATCH of every field would rewrite columns
// this form doesn't show (case owner, learning provider, reference number)
// with whatever the row happened to load with.
// ============================================================================

type FieldType = 'text' | 'email' | 'tel' | 'date' | 'radio';

interface FieldDef {
  /** Key in CreateStaffUserInput / the PATCH payload. */
  name: string;
  label: string;
  type: FieldType;
  required?: boolean;
  placeholder?: string;
  options?: { value: string; label: string }[];
}

const GENDER_OPTIONS = [
  { value: 'Male', label: 'Male' },
  { value: 'Female', label: 'Female' },
  { value: 'Prefer not to say', label: 'Prefer not to say' },
];

const FIELDS: FieldDef[] = [
  { name: 'username', label: 'Full name', type: 'text', required: true, placeholder: 'firstname lastname' },
  { name: 'email', label: 'Email', type: 'email', required: true, placeholder: 'you@youremail.com' },
  { name: 'phone', label: 'Mobile', type: 'tel', placeholder: 'NNN NNNN NNNN or +NNN NNN NNN NNNN' },
  { name: 'title', label: 'Title', type: 'text', placeholder: 'title' },
  { name: 'preferredName', label: 'Preferred name', type: 'text', placeholder: 'preferred name' },
  { name: 'gender', label: 'Gender', type: 'radio', options: GENDER_OPTIONS },
  { name: 'dob', label: 'Date of birth', type: 'date' },
  { name: 'organization', label: 'Organisation', type: 'text', placeholder: 'e.g. Kent Business College' },
];

/**
 * Date_of_birth is a free-text column, so a stored value isn't guaranteed to be
 * the YYYY-MM-DD an <input type="date"> can display. Anything else would render
 * as an empty input and then be diffed as "cleared" on the first save, silently
 * wiping the row — so a value the input can't show is dropped from the form and
 * left untouched by the patch.
 */
function displayableDob(raw: string): string {
  return /^\d{4}-\d{2}-\d{2}$/.test(raw.trim()) ? raw.trim() : '';
}

/** Row -> form values. Keys match the PATCH payload, not the row shape. */
function initialValues(row: StaffUserRow): Record<string, string> {
  return {
    username: row.name ?? '',
    email: row.email ?? '',
    phone: row.phone ?? '',
    title: row.title ?? '',
    preferredName: row.preferredName ?? '',
    gender: row.gender ?? '',
    dob: displayableDob(row.dob ?? ''),
    organization: row.organization ?? '',
  };
}

export function EditStaffModal({
  row,
  onClose,
  onSaved,
}: {
  row: StaffUserRow;
  onClose: () => void;
  onSaved: (updated: StaffUserRow) => void;
}) {
  const { success, error } = useToast();
  const [formData, setFormData] = useState<Record<string, string>>(() => initialValues(row));
  const [position, setPosition] = useState(row.position || row.type || '');
  const [status, setStatus] = useState(row.subscriptionStatus || 'FullUser');
  const [submitting, setSubmitting] = useState(false);

  const setField = (name: string, value: string) => setFormData((prev) => ({ ...prev, [name]: value }));

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    const missing = FIELDS.filter((f) => f.required && !formData[f.name]?.trim());
    if (missing.length > 0) {
      error('Missing details', `Please complete: ${missing.map((f) => f.label).join(', ')}`);
      return;
    }
    if (!position) {
      error('Position required', 'Please choose a position for this account.');
      return;
    }

    const base = initialValues(row);
    const patch: Record<string, string> = {};
    for (const key of Object.keys(base)) {
      const next = (formData[key] ?? '').trim();
      if (next !== (base[key] ?? '')) patch[key] = next;
    }
    if (position !== (row.position || row.type || '')) patch.position = position;
    if (status !== (row.subscriptionStatus || '')) patch.status = status;

    if (Object.keys(patch).length === 0) {
      onClose();
      return;
    }

    setSubmitting(true);
    try {
      const updated = await updateStaffUser(row.id, patch);
      success('Changes saved', `${updated.name || row.name} was updated.`);
      onSaved(updated);
      onClose();
    } catch (err) {
      error('Could not save changes', err instanceof Error ? err.message : 'Unexpected error');
    } finally {
      setSubmitting(false);
    }
  };

  /** One Aptem-style row: label on the left, control on the right. */
  const renderField = (field: FieldDef, index: number) => {
    const value = formData[field.name] ?? '';
    return (
      <div
        key={field.name}
        className={`grid grid-cols-1 sm:grid-cols-[minmax(160px,240px)_1fr] gap-1 sm:gap-4 px-4 py-2.5 items-start ${
          index % 2 === 1 ? 'bg-background-100/40' : ''
        }`}
      >
        <label htmlFor={`es-${field.name}`} className="text-[12px] font-medium text-foreground-600 sm:py-2 leading-snug">
          {field.label}
          {field.required ? <span className="text-red-500 ml-0.5">*</span> : <span className="text-foreground-300 ml-1">(O)</span>}
        </label>
        <div className="min-w-0">
          {field.type === 'radio' ? (
            <div className="flex flex-wrap items-center gap-4 sm:py-2">
              {field.options?.map((opt) => (
                <label key={opt.value} className="flex items-center gap-1.5 text-[13px] text-foreground-700 cursor-pointer">
                  <input
                    type="radio"
                    name={`es-${field.name}`}
                    checked={value === opt.value}
                    onChange={() => setField(field.name, opt.value)}
                    className="accent-primary-500"
                  />
                  {opt.label}
                </label>
              ))}
            </div>
          ) : (
            <input
              id={`es-${field.name}`}
              type={field.type}
              value={value}
              placeholder={field.placeholder}
              onChange={(e) => setField(field.name, e.target.value)}
              className={inputClass}
            />
          )}
        </div>
      </div>
    );
  };

  return (
    <Modal
      title={<>Edit user — <span className="text-primary-700">{row.name}</span></>}
      size="max-w-3xl"
      onClose={onClose}
      footer={
        <>
          <button type="button" className={btnSecondary} onClick={onClose} disabled={submitting}>Cancel</button>
          <button type="button" className={btnPrimary} onClick={() => handleSubmit()} disabled={submitting}>
            {submitting ? <><i className="ri-loader-4-line animate-spin" />Saving…</> : <><i className="ri-save-line" />Save changes</>}
          </button>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-5">
        <section className="rounded-xl border border-foreground-200/70 overflow-hidden">
          <header className="flex items-center gap-2 px-4 py-2.5 bg-background-100/70 border-b border-foreground-200/60">
            <i className="ri-shield-user-line text-primary-500" />
            <h3 className="text-[12px] font-semibold uppercase tracking-wider text-foreground-600">Identity</h3>
          </header>
          <div className="divide-y divide-foreground-100">{FIELDS.map(renderField)}</div>
        </section>

        <section className="rounded-xl border border-foreground-200/70 overflow-hidden">
          <header className="flex items-center gap-2 px-4 py-2.5 bg-background-100/70 border-b border-foreground-200/60">
            <i className="ri-check-double-line text-primary-500" />
            <h3 className="text-[12px] font-semibold uppercase tracking-wider text-foreground-600">Account status</h3>
          </header>
          <div className="grid grid-cols-1 sm:grid-cols-[minmax(160px,240px)_1fr] gap-1 sm:gap-4 px-4 py-2.5 items-start">
            <label htmlFor="es-status" className="text-[12px] font-medium text-foreground-600 sm:py-2">Subscription status</label>
            <div className="min-w-0">
              <select id="es-status" value={status} onChange={(e) => setStatus(e.target.value)} className={`${inputClass} cursor-pointer`}>
                {STATUS_OPTIONS.map((opt) => <option key={opt} value={opt}>{opt}</option>)}
              </select>
            </div>
          </div>
        </section>

        <section className="rounded-xl border-2 border-primary-200 overflow-hidden">
          <header className="flex items-center gap-2 px-4 py-2.5 bg-primary-50 border-b border-primary-200/60">
            <i className="ri-briefcase-line text-primary-500" />
            <h3 className="text-[12px] font-semibold uppercase tracking-wider text-primary-700">
              Position<span className="text-red-500 ml-0.5">*</span>
            </h3>
          </header>
          <div className="p-4 space-y-2.5">
            <p className="text-[12px] text-foreground-500">This account’s position. Caseowner and Admin can own learner cases.</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {POSITION_OPTIONS.map((opt) => (
                <label
                  key={opt}
                  className={`flex items-center gap-2.5 p-3 rounded-lg border cursor-pointer transition-smooth ${
                    position === opt ? 'border-primary-400 bg-primary-50/60' : 'border-foreground-200 hover:bg-background-100/60'
                  }`}
                >
                  <input
                    type="radio"
                    name="es-position"
                    checked={position === opt}
                    onChange={() => setPosition(opt)}
                    className="accent-primary-500"
                  />
                  <span className="text-[13px] font-medium text-foreground-800">{opt}</span>
                </label>
              ))}
            </div>
          </div>
        </section>

        {/* allow Enter-to-submit without a visible duplicate button */}
        <button type="submit" className="hidden" aria-hidden="true" tabIndex={-1} />
      </form>
    </Modal>
  );
}
