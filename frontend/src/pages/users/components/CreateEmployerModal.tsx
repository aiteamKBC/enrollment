import { useCallback, useEffect, useState } from 'react';
import { useToast } from "@/hooks/useToast.helpers";
import {
  createEmployer,
  listOrganisations,
  updateEmployer,
  type EmployerRow,
  type OrganisationRow,
} from '@/api/employers';
import { COUNTRY_OPTIONS, DEFAULT_COUNTRY } from '@/lib/countries';
import { Modal } from './Modal';
import { inputClass, btnPrimary, btnSecondary } from './ui';

// ============================================================================
// Create employer profile — a person at one or more organisations.
//
// Aptem-shaped, same idiom as the other create forms. The "Employer Group"
// control is the link to enrolment."Organisations": a searchable, paged,
// multi-select table of the organisations created by the organisation form, so
// anything added there shows up here immediately.
//
// Only the ids are submitted; the backend resolves and denormalises the names,
// and rejects an id that doesn't exist.
// ============================================================================

type FieldType = 'text' | 'email' | 'tel' | 'select' | 'radio';

interface FieldDef {
  name: keyof FormState;
  label: string;
  type: FieldType;
  required?: boolean;
  placeholder?: string;
  options?: string[];
}

interface SectionDef {
  title: string;
  icon: string;
  fields: FieldDef[];
}

interface FormState {
  firstName: string;
  surname: string;
  gender: string;
  email: string;
  mobile: string;
  postCode: string;
  address1: string;
  address2: string;
  townCity: string;
  county: string;
  country: string;
}

const GENDER_OPTIONS = ['Male', 'Female'];

const SECTIONS: SectionDef[] = [
  {
    title: 'Personal details',
    icon: 'ri-user-line',
    fields: [
      { name: 'firstName', label: 'First name', type: 'text', required: true, placeholder: 'firstname' },
      { name: 'surname', label: 'Surname', type: 'text', required: true, placeholder: 'lastname' },
      { name: 'gender', label: 'Gender', type: 'radio', options: GENDER_OPTIONS },
      // Required: the invitation is sent on save, and an employer with no
      // address cannot be given one — see the Invitation section below.
      { name: 'email', label: 'Email', type: 'email', required: true, placeholder: 'you@youremail.com' },
      { name: 'mobile', label: 'Mobile', type: 'tel', placeholder: 'NNN NNNN NNNN or +NNN NNN NNN NNNN' },
    ],
  },
  {
    title: 'Address',
    icon: 'ri-map-pin-line',
    fields: [
      { name: 'postCode', label: 'Postcode', type: 'text' },
      { name: 'address1', label: 'Address 1', type: 'text' },
      { name: 'address2', label: 'Address 2', type: 'text' },
      { name: 'townCity', label: 'Town/City', type: 'text' },
      { name: 'county', label: 'County', type: 'text' },
      { name: 'country', label: 'Country', type: 'select', options: COUNTRY_OPTIONS },
    ],
  },
];

const EMPTY: FormState = {
  firstName: '', surname: '', gender: '', email: '', mobile: '',
  postCode: '', address1: '', address2: '', townCity: '', county: '', country: DEFAULT_COUNTRY,
};

/**
 * The Employer Group picker: search, page, tick organisations.
 *
 * Selected rows are held by the parent so they survive paging and searching —
 * a checkbox on page 3 stays ticked after a search that hides it, which is what
 * "N record(s) selected" has to keep counting.
 */
function EmployerGroupPicker({
  selected,
  onToggle,
}: {
  selected: OrganisationRow[];
  onToggle: (org: OrganisationRow) => void;
}) {
  const [search, setSearch] = useState('');
  const [applied, setApplied] = useState('');
  const [page, setPage] = useState(1);
  const [rows, setRows] = useState<OrganisationRow[]>([]);
  const [count, setCount] = useState(0);
  const [pageSize, setPageSize] = useState(10);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [selectedOnly, setSelectedOnly] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setErr(null);
    listOrganisations({ search: applied || undefined, page })
      .then((res) => {
        setRows(res.results);
        setCount(res.count);
        setPageSize(res.pageSize || 10);
      })
      .catch((e: Error) => setErr(e.message))
      .finally(() => setLoading(false));
  }, [applied, page]);

  useEffect(load, [load]);

  const totalPages = Math.max(1, Math.ceil(count / pageSize));
  const isSelected = (id: string) => selected.some((s) => s.id === id);
  // "Show selected records only" filters the current selection rather than
  // re-querying — the selection is already in memory and may span pages.
  const visible = selectedOnly ? selected : rows;

  const doSearch = () => { setApplied(search.trim()); setPage(1); };

  return (
    <div className="space-y-2.5">
      <label className="flex items-center gap-2 text-[12px] text-foreground-700 cursor-pointer">
        <input type="checkbox" checked={selectedOnly} onChange={(e) => setSelectedOnly(e.target.checked)} className="accent-primary-500" />
        Show selected records only
      </label>
      <p className="text-[12px] text-foreground-500">{selected.length} record(s) selected.</p>

      {!selectedOnly && (
        <div className="flex items-center gap-2">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); doSearch(); } }}
            placeholder="search"
            className={inputClass}
            aria-label="Search organisations"
          />
          <button type="button" onClick={doSearch} className={btnPrimary}><i className="ri-search-line" />Search</button>
        </div>
      )}

      <div className="border border-foreground-200/70 rounded-lg overflow-hidden">
        <table className="w-full text-[12px]">
          <thead>
            <tr className="bg-background-100/60 border-b border-foreground-200/60">
              <th className="w-10 py-2 px-2" />
              <th className="text-left py-2 px-2 text-[11px] font-semibold uppercase tracking-wider text-foreground-500">Name</th>
              <th className="text-left py-2 px-2 text-[11px] font-semibold uppercase tracking-wider text-foreground-500">Parent name</th>
              <th className="text-left py-2 px-2 text-[11px] font-semibold uppercase tracking-wider text-foreground-500">Group type</th>
            </tr>
          </thead>
          <tbody>
            {loading && !selectedOnly && (
              <tr><td colSpan={4} className="py-6 text-center text-foreground-400"><i className="ri-loader-4-line animate-spin mr-1.5" />Loading organisations…</td></tr>
            )}
            {!loading && err && (
              <tr><td colSpan={4} className="py-6 text-center">
                <p className="text-red-600 mb-2"><i className="ri-error-warning-line mr-1" />{err}</p>
                <button type="button" className={btnSecondary} onClick={load}><i className="ri-refresh-line" />Retry</button>
              </td></tr>
            )}
            {!err && (selectedOnly || !loading) && visible.map((o, i) => (
              <tr key={o.id} className={`border-b border-foreground-100 ${i % 2 === 1 ? 'bg-background-100/20' : ''}`}>
                <td className="py-1.5 px-2">
                  <input
                    type="checkbox"
                    checked={isSelected(o.id)}
                    onChange={() => onToggle(o)}
                    className="accent-primary-500"
                    aria-label={`Select ${o.name}`}
                  />
                </td>
                <td className="py-1.5 px-2 text-primary-700 font-medium">{o.name}</td>
                <td className="py-1.5 px-2 text-foreground-600">{o.parentName}</td>
                <td className="py-1.5 px-2 text-foreground-600">{o.groupType}</td>
              </tr>
            ))}
            {!err && !loading && visible.length === 0 && (
              <tr><td colSpan={4} className="py-6 text-center text-foreground-400">
                {selectedOnly
                  ? 'No organisations selected yet.'
                  : applied
                    ? 'No organisations match that search.'
                    : 'No organisations yet — create one from the Create menu first.'}
              </td></tr>
            )}
          </tbody>
        </table>

        {!selectedOnly && totalPages > 1 && (
          <div className="flex items-center justify-center gap-2 py-2 border-t border-foreground-100 text-[12px]">
            <button type="button" onClick={() => setPage(1)} disabled={page === 1} className="px-1.5 disabled:opacity-40 cursor-pointer" aria-label="First page"><i className="ri-skip-back-mini-line" /></button>
            <button type="button" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1} className="px-1.5 disabled:opacity-40 cursor-pointer" aria-label="Previous page"><i className="ri-arrow-left-s-line" /></button>
            <span className="text-foreground-500">Page {page} of {totalPages}</span>
            <button type="button" onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page === totalPages} className="px-1.5 disabled:opacity-40 cursor-pointer" aria-label="Next page"><i className="ri-arrow-right-s-line" /></button>
            <button type="button" onClick={() => setPage(totalPages)} disabled={page === totalPages} className="px-1.5 disabled:opacity-40 cursor-pointer" aria-label="Last page"><i className="ri-skip-forward-mini-line" /></button>
          </div>
        )}
      </div>
    </div>
  );
}

/** Row -> form values, for the edit mode. */
function initialValues(row: EmployerRow): FormState {
  return {
    firstName: row.firstName ?? '',
    surname: row.surname ?? '',
    gender: row.gender ?? '',
    email: row.email ?? '',
    mobile: row.mobile ?? '',
    postCode: row.postCode ?? '',
    address1: row.address1 ?? '',
    address2: row.address2 ?? '',
    townCity: row.townCity ?? '',
    county: row.county ?? '',
    country: row.country ?? DEFAULT_COUNTRY,
  };
}

/**
 * Create or edit an employer contact.
 *
 * Passing `row` switches it to edit mode: the fields prefill, the existing
 * Employer Group selection is rehydrated, and saving PATCHes instead of POSTing.
 * One component for both so the group picker isn't duplicated.
 */
export function CreateEmployerModal({
  row,
  onClose,
  onCreated,
}: {
  row?: EmployerRow;
  onClose: () => void;
  onCreated: (row: EmployerRow) => void;
}) {
  const { success, error } = useToast();
  const editing = Boolean(row);
  const [form, setForm] = useState<FormState>(() => (row ? initialValues(row) : EMPTY));
  const [groups, setGroups] = useState<OrganisationRow[]>([]);
  // The employer row carries group ids and names but not whole organisations, so
  // in edit mode the current selection is resolved from the organisation list.
  // Until it arrives the picker would show nothing ticked, which would silently
  // drop the existing groups on save — so saving is blocked while it loads.
  const [groupsReady, setGroupsReady] = useState(!editing);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!row) return;
    let live = true;
    const ids = new Set(row.employerGroupIds);
    if (ids.size === 0) { setGroupsReady(true); return; }
    listOrganisations()
      .then((res) => {
        if (!live) return;
        setGroups(res.results.filter((o) => ids.has(o.id)));
        setGroupsReady(true);
      })
      .catch(() => {
        if (!live) return;
        error('Could not load employer groups', 'Reopen the row to try again — saving is disabled to avoid clearing them.');
      });
    return () => { live = false; };
    // Mount-only: `row` is fixed for the lifetime of an open modal, and `error`
    // is a new function each render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const set = (name: keyof FormState, value: string) => setForm((f) => ({ ...f, [name]: value }));

  const toggleGroup = (org: OrganisationRow) =>
    setGroups((prev) => (prev.some((s) => s.id === org.id) ? prev.filter((s) => s.id !== org.id) : [...prev, org]));

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    const missing = SECTIONS.flatMap((s) => s.fields).filter((f) => f.required && !form[f.name]?.trim());
    if (missing.length > 0) {
      error('Missing details', `Please complete: ${missing.map((f) => f.label).join(', ')}`);
      return;
    }
    if (groups.length === 0) {
      error('Employer group required', 'Please select at least one organisation for this employer.');
      return;
    }
    setSubmitting(true);
    try {
      const payload = {
        ...form,
        // Ids only — the backend resolves the names and rejects unknown ids.
        employerGroupIds: groups.map((g) => g.id),
      };
      const saved = row
        ? await updateEmployer(row.id, payload)
        : await createEmployer(payload);

      // Create always invites; editing never does, so the edit path keeps its
      // plain "saved" message. On create the employer is saved whether or not
      // the invitation worked, and the failures differ — "no account" means
      // nobody can sign in, a mail failure leaves a link that can be re-sent.
      const invite = saved.invitation;
      if (editing || !invite) {
        success(
          row ? 'Changes saved' : 'Employer created',
          `${saved.name} was ${row ? 'updated' : 'saved'}.`,
        );
      } else if (invite.forbidden) {
        success('Employer created', `${saved.name} was saved.`);
        error(
          'Not permitted to invite',
          invite.error || 'You do not have permission to invite this person.',
        );
      } else if (invite.awaitingInvitation) {
        // The account exists but nobody has been emailed. That is the intended
        // outcome of creating somebody: an administrator sends the invitation
        // from Accounts once the record has been checked.
        success(
          'Employer created',
          `${saved.name} was saved. Send their invitation from Accounts in the Super Admin workspace when you are ready.`,
        );
      } else if (!invite.invited) {
        success('Employer created', `${saved.name} was saved.`);
        error(
          'No account created',
          invite.error || 'They have no sign-in account yet, so they cannot log in.',
        );
      } else if (!invite.emailSent) {
        success('Employer created', `${saved.name} was saved.`);
        error(
          'Invitation email not sent',
          invite.error || 'The invitation exists and can be re-sent, but the email did not go out.',
        );
      } else {
        success('Employer created and invited', `${saved.name} was emailed a link to set their password.`);
      }
      onCreated(saved);
      onClose();
    } catch (err) {
      error(
        row ? 'Could not save changes' : 'Could not create employer',
        err instanceof Error ? err.message : 'Unexpected error',
      );
    } finally {
      setSubmitting(false);
    }
  };

  /** One Aptem-style row: label on the left, control on the right. */
  const renderField = (field: FieldDef, index: number) => {
    const value = form[field.name] ?? '';
    return (
      <div
        key={field.name}
        className={`grid grid-cols-1 sm:grid-cols-[minmax(160px,240px)_1fr] gap-1 sm:gap-4 px-4 py-2.5 items-start ${
          index % 2 === 1 ? 'bg-background-100/40' : ''
        }`}
      >
        <label htmlFor={`ce-${field.name}`} className="text-[12px] font-medium text-foreground-600 sm:py-2 leading-snug">
          {field.label}
          {field.required ? <span className="text-red-500 ml-0.5">*</span> : <span className="text-foreground-300 ml-1">(O)</span>}
        </label>
        <div className="min-w-0">
          {field.type === 'radio' ? (
            <div className="flex flex-wrap items-center gap-4 sm:py-2">
              {field.options?.map((opt) => (
                <label key={opt} className="flex items-center gap-1.5 text-[13px] text-foreground-700 cursor-pointer">
                  <input
                    type="radio"
                    name={`ce-${field.name}`}
                    checked={value === opt}
                    onChange={() => set(field.name, opt)}
                    className="accent-primary-500"
                  />
                  {opt}
                </label>
              ))}
            </div>
          ) : field.type === 'select' ? (
            <select
              id={`ce-${field.name}`}
              value={value}
              onChange={(e) => set(field.name, e.target.value)}
              className={`${inputClass} cursor-pointer`}
            >
              <option value="">--Select--</option>
              {field.options?.map((opt) => <option key={opt} value={opt}>{opt}</option>)}
            </select>
          ) : (
            <input
              id={`ce-${field.name}`}
              type={field.type}
              value={value}
              placeholder={field.placeholder}
              onChange={(e) => set(field.name, e.target.value)}
              className={inputClass}
            />
          )}
        </div>
      </div>
    );
  };

  return (
    <Modal
      title={editing ? <>Edit employer — <span className="text-primary-700">{row?.name}</span></> : 'Add employer'}
      size="max-w-3xl"
      onClose={onClose}
      footer={
        <>
          <button type="button" className={btnSecondary} onClick={onClose} disabled={submitting}>{editing ? 'Cancel' : 'Close'}</button>
          {/* Disabled until the existing group selection has loaded, so a save
              can't silently clear it. */}
          <button type="button" className={btnPrimary} onClick={() => handleSubmit()} disabled={submitting || !groupsReady}>
            {submitting
              ? <><i className="ri-loader-4-line animate-spin" />Saving…</>
              : editing
                ? <><i className="ri-save-line" />Save changes</>
                : <><i className="ri-briefcase-line" />Create</>}
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
            <div className="divide-y divide-foreground-100">{section.fields.map(renderField)}</div>
          </section>
        ))}

        {/* Employer Group — the link to the organisation profiles. */}
        <section className="rounded-xl border-2 border-primary-200 overflow-hidden">
          <header className="flex items-center gap-2 px-4 py-2.5 bg-primary-50 border-b border-primary-200/60">
            <i className="ri-building-line text-primary-500" />
            <h3 className="text-[12px] font-semibold uppercase tracking-wider text-primary-700">
              Employer Group<span className="text-red-500 ml-0.5">*</span>
            </h3>
          </header>
          <div className="p-4">
            <p className="text-[12px] text-foreground-500 mb-2.5">
              The organisation(s) this employer belongs to. Create organisation profiles from the Create menu to add to this list.
            </p>
            <EmployerGroupPicker selected={groups} onToggle={toggleGroup} />
          </div>
        </section>

        {/* Invitation — create only, and always sent. On edit the person already
            has an account, and re-inviting is a deliberate action from the
            account list, not a side effect of correcting their address. */}
        {!editing && (
          <section className="rounded-xl border border-foreground-200/70 overflow-hidden">
            <header className="flex items-center gap-2 px-4 py-2.5 bg-background-100/70 border-b border-foreground-200/60">
              <i className="ri-mail-send-line text-primary-500" />
              <h3 className="text-[12px] font-semibold uppercase tracking-wider text-foreground-600">
                Invitation
              </h3>
            </header>
            <div className="p-4">
              <p className="text-[13px] font-medium text-foreground-800">
                They will get a platform account when you save.
              </p>
              <p className="text-[12px] text-foreground-500 mt-0.5">
                A single-use link to set their own password, giving them access to the employer
                portal and the documents they need to sign.
              </p>
            </div>
          </section>
        )}

        {/* allow Enter-to-submit without a visible duplicate button */}
        <button type="submit" className="hidden" aria-hidden="true" tabIndex={-1} />
      </form>
    </Modal>
  );
}
