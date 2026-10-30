import { useEffect, useState } from 'react';
import { useToast } from "@/hooks/useToast.helpers";
import {
  createOrganisation,
  fetchEmployerOptions,
  type EmployerOptions,
  type OrganisationRow,
  type WorkingHoursSession,
} from '@/api/employers';
import { COUNTRY_OPTIONS, DEFAULT_COUNTRY } from '@/lib/countries';
import { Modal } from './Modal';
import { inputClass, btnPrimary, btnSecondary } from './ui';

// ============================================================================
// Create organisation profile — the employing companies.
//
// Aptem-shaped: label column on the left, control on the right, grouped into
// sections, in the same order as the source screens. Writes to
// enrolment."Organisations" — the table the employer form's "Employer Group"
// picker reads, so an organisation created here is immediately selectable there.
//
// Working hours are a repeater ("Add another session"), not a fixed field, so
// they're held as a list of {day, start, end} and stored as jsonb.
// ============================================================================

type FieldType = 'text' | 'email' | 'tel' | 'url' | 'number' | 'select' | 'textarea';

interface FieldDef {
  name: keyof FormState;
  label: string;
  type: FieldType;
  required?: boolean;
  placeholder?: string;
  options?: string[];
  /** Options loaded from the API rather than hard-coded. */
  lookup?: keyof EmployerOptions;
  /** Muted helper text under the control. */
  hint?: string;
}

interface SectionDef {
  title: string;
  icon: string;
  fields: FieldDef[];
}

interface FormState {
  status: string;
  name: string;
  owner: string;
  edrsErnNumber: string;
  apprenticeshipAgreementId: string;
  postCode: string;
  address1: string;
  address2: string;
  cityTown: string;
  county: string;
  country: string;
  contactName: string;
  contactEmail: string;
  contactTelephone: string;
  contactRole: string;
  website: string;
  referenceNumber: string;
  levyPayer: string;
  approxNoOfEmployees: string;
  healthAndSafety: string;
}

/**
 * Held outside FormState, whose values are all strings. Tri-state on purpose:
 * null means "never touched", which the column distinguishes from a chosen "No".
 */
type HoursEmails = boolean | null;


const SECTIONS: SectionDef[] = [
  {
    title: 'Organisation',
    icon: 'ri-building-line',
    fields: [
      { name: 'status', label: 'Status', type: 'select', lookup: 'status' },
      { name: 'name', label: 'Name', type: 'text', required: true, placeholder: 'organisation name' },
      { name: 'owner', label: 'Owner', type: 'select', lookup: 'owners' },
      {
        name: 'edrsErnNumber',
        label: 'EDRS/ERN number',
        type: 'text',
        hint: 'Missing EDRS/ERN number is a funding issue and may result in learner being removed from the claim',
      },
      { name: 'apprenticeshipAgreementId', label: 'Apprenticeship agreement id', type: 'text' },
    ],
  },
  {
    title: 'Address',
    icon: 'ri-map-pin-line',
    fields: [
      { name: 'postCode', label: 'Post code', type: 'text' },
      { name: 'address1', label: 'Address 1', type: 'text' },
      { name: 'address2', label: 'Address 2', type: 'text' },
      { name: 'cityTown', label: 'City/Town', type: 'text' },
      { name: 'county', label: 'County', type: 'text' },
      { name: 'country', label: 'Country', type: 'select', options: COUNTRY_OPTIONS },
    ],
  },
  {
    title: 'Contact',
    icon: 'ri-user-star-line',
    fields: [
      { name: 'contactName', label: 'Contact name', type: 'text', placeholder: 'unique contact name' },
      { name: 'contactEmail', label: 'Contact email', type: 'email', placeholder: 'you@youremail.com' },
      { name: 'contactTelephone', label: 'Contact telephone', type: 'tel', placeholder: 'NNN NNNN NNNN or +NNN NNN NNN NNNN' },
      { name: 'contactRole', label: 'Contact Role', type: 'text', placeholder: 'Select roles' },
      { name: 'website', label: 'Website', type: 'url' },
      { name: 'referenceNumber', label: 'Reference number', type: 'text' },
    ],
  },
  {
    title: 'Funding & compliance',
    icon: 'ri-shield-check-line',
    fields: [
      { name: 'levyPayer', label: 'Levy payer', type: 'select', lookup: 'levyPayer' },
      { name: 'approxNoOfEmployees', label: 'Approx. no of employees', type: 'number', placeholder: 'integer value' },
      { name: 'healthAndSafety', label: 'Health & Safety', type: 'select', lookup: 'healthAndSafety' },
    ],
  },
];

const EMPTY: FormState = {
  status: 'Confirmed', name: '', owner: '',
  edrsErnNumber: '', apprenticeshipAgreementId: '', postCode: '', address1: '', address2: '',
  cityTown: '', county: '', country: DEFAULT_COUNTRY, contactName: '', contactEmail: '',
  contactTelephone: '', contactRole: '', website: '', referenceNumber: '',
  levyPayer: 'Not selected', approxNoOfEmployees: '', healthAndSafety: 'Not known',
};

const DAY_OPTIONS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

export function CreateOrganisationModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (row: OrganisationRow) => void;
}) {
  const { success, error } = useToast();
  const [form, setForm] = useState<FormState>(EMPTY);
  const [hoursEmails, setHoursEmails] = useState<HoursEmails>(null);
  const [sessions, setSessions] = useState<WorkingHoursSession[]>([{ day: 'Monday', start: '09:00', end: '17:00' }]);
  const [options, setOptions] = useState<EmployerOptions | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // The option lists (and the owner list) are server-owned so the form can't
  // offer a value the API would reject.
  useEffect(() => {
    let live = true;
    fetchEmployerOptions()
      .then((o) => { if (live) setOptions(o); })
      .catch(() => { /* selects fall back to their current value only */ });
    return () => { live = false; };
  }, []);

  const set = (name: keyof FormState, value: string) => setForm((f) => ({ ...f, [name]: value }));

  const setSession = (index: number, patch: Partial<WorkingHoursSession>) =>
    setSessions((rows) => rows.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  const addSession = () => setSessions((rows) => [...rows, { day: '', start: '', end: '' }]);
  const removeSession = (index: number) => setSessions((rows) => rows.filter((_, i) => i !== index));

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!form.name.trim()) {
      error('Missing details', 'Please enter the organisation name.');
      return;
    }
    setSubmitting(true);
    try {
      const row = await createOrganisation({
        ...form,
        // Sent as a number-or-null; the API rejects anything non-numeric.
        approxNoOfEmployees: form.approxNoOfEmployees.trim() ? Number(form.approxNoOfEmployees) : null,
        // Half-filled repeater rows are dropped server-side too, but there's no
        // reason to send them.
        workingHours: sessions.filter((s) => s.day || s.start || s.end),
        sendHoursVerificationEmails: hoursEmails,
      });
      success('Organisation created', `${row.name} was saved.`);
      onCreated(row);
      onClose();
    } catch (err) {
      error('Could not create organisation', err instanceof Error ? err.message : 'Unexpected error');
    } finally {
      setSubmitting(false);
    }
  };

  /** One Aptem-style row: label on the left, control on the right. */
  const renderField = (field: FieldDef, index: number) => {
    const value = String(form[field.name] ?? '');
    const list = field.lookup ? options?.[field.lookup] ?? [] : field.options ?? [];
    return (
      <div
        key={field.name}
        className={`grid grid-cols-1 sm:grid-cols-[minmax(160px,240px)_1fr] gap-1 sm:gap-4 px-4 py-2.5 items-start ${
          index % 2 === 1 ? 'bg-background-100/40' : ''
        }`}
      >
        <label htmlFor={`co-${field.name}`} className="text-[12px] font-medium text-foreground-600 sm:py-2 leading-snug">
          {field.label}
          {field.required ? <span className="text-red-500 ml-0.5">*</span> : <span className="text-foreground-300 ml-1">(O)</span>}
        </label>
        <div className="min-w-0">
          {field.type === 'select' ? (
            <select
              id={`co-${field.name}`}
              value={value}
              onChange={(e) => set(field.name, e.target.value)}
              className={`${inputClass} cursor-pointer`}
            >
              <option value="">--Select--</option>
              {list.map((opt) => <option key={opt} value={opt}>{opt}</option>)}
            </select>
          ) : (
            <input
              id={`co-${field.name}`}
              type={field.type}
              value={value}
              placeholder={field.placeholder}
              onChange={(e) => set(field.name, e.target.value)}
              className={inputClass}
            />
          )}
          {field.hint && <p className="text-[11px] text-amber-700 mt-1 leading-snug">{field.hint}</p>}
        </div>
      </div>
    );
  };

  return (
    <Modal
      title="Add organisation"
      size="max-w-3xl"
      onClose={onClose}
      footer={
        <>
          <button type="button" className={btnSecondary} onClick={onClose} disabled={submitting}>Close</button>
          <button type="button" className={btnPrimary} onClick={() => handleSubmit()} disabled={submitting}>
            {submitting ? <><i className="ri-loader-4-line animate-spin" />Saving…</> : <><i className="ri-building-line" />Create</>}
          </button>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-5">
        {SECTIONS.map((section) => (
          <section key={section.title} className="rounded-xl border border-foreground-200/70 overflow-hidden">
            <header className="flex items-center gap-2 px-4 py-2.5 bg-background-100/70 border-b border-foreground-200/60">
              <i className={`${section.icon} text-primary-500`} />
              <h3 className="text-[12px] font-semibold uppercase tracking-wider text-foreground-600">{section.title}</h3>
            </header>
            <div className="divide-y divide-foreground-100">
              {section.fields.map(renderField)}
              {/* Tri-state, and not a FormState string, so it's rendered here
                  rather than through renderField — it belongs with Levy payer
                  and Health & Safety, as in the source screen. */}
              {section.title === 'Funding & compliance' && (
                <div className="grid grid-cols-1 sm:grid-cols-[minmax(160px,240px)_1fr] gap-1 sm:gap-4 px-4 py-2.5 items-start bg-background-100/40">
                  <span className="text-[12px] font-medium text-foreground-600 sm:py-2 leading-snug">
                    Send hours verification emails<span className="text-foreground-300 ml-1">(O)</span>
                  </span>
                  <div className="flex flex-wrap items-center gap-4 sm:py-2">
                    {[
                      { label: 'Yes', value: true },
                      { label: 'No', value: false },
                    ].map((opt) => (
                      <label key={opt.label} className="flex items-center gap-1.5 text-[13px] text-foreground-700 cursor-pointer">
                        <input
                          type="radio"
                          name="co-sendHoursVerificationEmails"
                          checked={hoursEmails === opt.value}
                          onChange={() => setHoursEmails(opt.value)}
                          className="accent-primary-500"
                        />
                        {opt.label}
                      </label>
                    ))}
                    {hoursEmails !== null && (
                      <button
                        type="button"
                        onClick={() => setHoursEmails(null)}
                        className="text-[11px] text-primary-600 hover:underline cursor-pointer"
                      >
                        Clear
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          </section>
        ))}

        {/* Working hours — a repeater, so it gets its own section rather than a row. */}
        <section className="rounded-xl border border-foreground-200/70 overflow-hidden">
          <header className="flex items-center justify-between gap-2 px-4 py-2.5 bg-background-100/70 border-b border-foreground-200/60">
            <span className="flex items-center gap-2">
              <i className="ri-time-line text-primary-500" />
              <h3 className="text-[12px] font-semibold uppercase tracking-wider text-foreground-600">Working hours</h3>
            </span>
            <button type="button" onClick={addSession} className="text-[12px] text-primary-600 hover:underline inline-flex items-center gap-1 cursor-pointer">
              <i className="ri-add-line" />Add another session
            </button>
          </header>
          <div className="p-4 space-y-2">
            {sessions.length === 0 && <p className="text-[12px] text-foreground-400">No sessions added.</p>}
            {sessions.map((s, i) => (
              <div key={i} className="flex items-center gap-2 flex-wrap">
                <select
                  value={s.day}
                  onChange={(e) => setSession(i, { day: e.target.value })}
                  className={`${inputClass} cursor-pointer max-w-[160px]`}
                  aria-label={`Session ${i + 1} day`}
                >
                  <option value="">--Day--</option>
                  {DAY_OPTIONS.map((d) => <option key={d} value={d}>{d}</option>)}
                </select>
                <input
                  type="time" value={s.start} onChange={(e) => setSession(i, { start: e.target.value })}
                  className={`${inputClass} max-w-[130px]`} aria-label={`Session ${i + 1} start`}
                />
                <span className="text-[12px] text-foreground-400">to</span>
                <input
                  type="time" value={s.end} onChange={(e) => setSession(i, { end: e.target.value })}
                  className={`${inputClass} max-w-[130px]`} aria-label={`Session ${i + 1} end`}
                />
                <button
                  type="button" onClick={() => removeSession(i)}
                  className="text-foreground-400 hover:text-red-600 cursor-pointer p-1"
                  aria-label={`Remove session ${i + 1}`}
                >
                  <i className="ri-delete-bin-line" />
                </button>
              </div>
            ))}
          </div>
        </section>

        {/* allow Enter-to-submit without a visible duplicate button */}
        <button type="submit" className="hidden" aria-hidden="true" tabIndex={-1} />
      </form>
    </Modal>
  );
}
