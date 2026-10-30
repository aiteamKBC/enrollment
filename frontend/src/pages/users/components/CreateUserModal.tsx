import { useEffect, useState } from 'react';
import { useToast } from "@/hooks/useToast.helpers";
import { reviewHandoverMessage } from '@/api/reviewHandover';
import { fetchProgrammes, fetchCohorts, fetchGroups } from '@/api/curriculum';
import { fetchCaseOwners } from '@/api/staffUsers';
import {
  ATTENDANCE_TYPE_OPTIONS,
  createEnrolmentUser,
  fetchEnrolmentUserFields,
  updateEnrolmentUser,
  PROGRAMME_STATUS_OPTIONS,
  STATUS_OPTIONS,
  TYPE_OPTIONS,
} from '@/api/enrolmentUsers';
import { listEmployers, type EmployerRow } from '@/api/employers';
import { COUNTRY_OPTIONS as ALL_COUNTRIES, DEFAULT_COUNTRY } from '@/lib/countries';
import type { UserListRow } from '../types';
import { Modal } from './Modal';
import { formatError } from "../wizard/steps/fields.helpers";
import { inputClass, btnPrimary, btnSecondary } from './ui';

// ============================================================================
// Create *and edit* a learner — the Aptem-shaped form opened from the Users list.
//
// One form for both, because it is one record: an edit that lived in its own
// component would drift from the create fields, and the two would disagree
// about what a learner is. `editing` switches the mode; everything else is
// shared. Three fields differ by mode and are marked `phase` below.
//
// The layout mirrors Aptem's own Add-user screen (label column on the left,
// field on the right, grouped into sections) so staff moving between the two
// systems see the same fields in the same order.
//
// Both learner kinds live in the SINGLE enrolment."Enrolment_Users" table. The
// learner type at the foot of the form is written to its "Learner_type" column,
// so there's one endpoint and one destination — no field is lost either way.
// ============================================================================

type LearnerKind = 'apprenticeship' | 'commercial';

type FieldType = 'text' | 'email' | 'tel' | 'date' | 'select' | 'radio' | 'checkbox' | 'textarea';

interface FieldDef {
  name: string;
  label: string;
  type: FieldType;
  required?: boolean;
  placeholder?: string;
  options?: { value: string; label: string }[];
  /** Shown under the field as muted helper text. */
  hint?: string;
  /** Restrict this field to one destination table (default: both). */
  only?: LearnerKind;
  /**
   * Restrict this field to creating or to editing (default: both).
   *
   * Three fields differ. Create asks for a first name and a surname and joins
   * them, because that is how the person is introduced; the column itself holds
   * one name, so editing shows that one name rather than guessing where to
   * split it. And the two statuses are stamped on create — asking would offer a
   * choice that is not really there — but are the whole point of editing.
   */
  phase?: 'create' | 'edit';
  /**
   * Options are loaded from the database rather than given in `options`.
   * The programme/cohort/group trio is a cascade — each level stays disabled
   * until its parent is chosen. `caseOwner` and `employer` are independent,
   * loaded once.
   */
  lookup?: 'programme' | 'cohort' | 'group' | 'caseOwner' | 'employer';
  /**
   * This field's value is filled in from another field rather than typed. Shown
   * read-only, since editing it would just be overwritten by the next change to
   * its source — and a value that disagrees with the employer record would be
   * worse than no value.
   */
  derivedFrom?: 'employer';
}

interface SectionDef {
  title: string;
  icon: string;
  fields: FieldDef[];
}

const opts = (values: string[]) => values.map((v) => ({ value: v, label: v }));

const GENDER_OPTIONS = opts(['Male', 'Female', 'Prefer not to say']);
// The full country list, shared with the employer and organisation forms. Still
// UK-first — see src/lib/countries.ts.
const COUNTRY_OPTIONS = opts(ALL_COUNTRIES);
const PROVIDER_OPTIONS = opts(['Kent Business College']);

// The form, section by section, in Aptem's order. There is no Invitation
// section: enrolling a learner always invites them, so the question was a way
// of creating somebody who could never sign in.
const SECTIONS: SectionDef[] = [
  {
    title: 'Identity',
    icon: 'ri-user-line',
    fields: [
      { name: 'firstName', label: 'First name', type: 'text', required: true, placeholder: 'firstname', phase: 'create' },
      { name: 'lastName', label: 'Surname', type: 'text', required: true, placeholder: 'lastname', phase: 'create' },
      { name: 'username', label: 'Name', type: 'text', required: true, placeholder: 'firstname lastname', phase: 'edit' },
      { name: 'preferredName', label: 'Preferred name', type: 'text', placeholder: 'e.g. Soph' },
      { name: 'title', label: 'Title', type: 'text', placeholder: 'title' },
      { name: 'gender', label: 'Gender', type: 'radio', options: GENDER_OPTIONS },
      { name: 'email', label: 'Email', type: 'email', required: true, placeholder: 'you@youremail.com' },
      { name: 'phone', label: 'Mobile', type: 'tel', placeholder: 'NNN NNNN NNNN or +NNN NNN NNN NNNN' },
      { name: 'dob', label: 'Date of birth', type: 'date' },
      { name: 'niNumber', label: 'National Insurance number', type: 'text', placeholder: 'AB123456A' },
    ],
  },
  {
    title: 'Referral',
    icon: 'ri-share-forward-line',
    fields: [
      { name: 'referrer', label: 'Referrer', type: 'text', placeholder: 'e.g. Jobcentre Plus' },
      { name: 'referrerAddress', label: 'Referrer address', type: 'text' },
      { name: 'referrerContact', label: 'Referrer contact', type: 'text' },
    ],
  },
  {
    title: 'Programme',
    icon: 'ri-book-open-line',
    // Programme -> Cohort -> Group is a live cascade off the `curriculum` schema
    // (programmes / cohorts / groups), so only real authored combinations can be
    // picked. Options are loaded at render time — see the `lookup` fields below.
    fields: [
      { name: 'programme', label: 'Programme', type: 'select', lookup: 'programme' },
      { name: 'cohort', label: 'Cohort', type: 'select', lookup: 'cohort' },
      { name: 'group', label: 'Group', type: 'select', lookup: 'group' },
    ],
  },
  {
    // All three are stamped on create (Type: User, Status: FullUser) and only
    // become a question afterwards. Programme status is also editable on the
    // learner's own board header — the same field and the same write, reached
    // from the record instead of the directory.
    title: 'Type & status',
    icon: 'ri-shield-check-line',
    fields: [
      {
        name: 'type',
        label: 'Type',
        type: 'select',
        phase: 'edit',
        options: opts(TYPE_OPTIONS),
        hint: 'What this person is to the college. A label on the record — it does not change what they can reach, which follows from being a learner.',
      },
      {
        name: 'status',
        label: 'Subscription status',
        type: 'select',
        phase: 'edit',
        options: opts(STATUS_OPTIONS),
        hint: 'FullUser is a verified subscription; every other value shows as unverified in the directory.',
      },
      {
        name: 'programmeStatus',
        label: 'Programme status',
        type: 'select',
        phase: 'edit',
        options: opts(PROGRAMME_STATUS_OPTIONS),
        hint: 'Where the learner sits in the enrolment-to-delivery flow.',
      },
    ],
  },
  {
    title: 'Address',
    icon: 'ri-map-pin-line',
    fields: [
      { name: 'postcode', label: 'Postcode', type: 'text', placeholder: 'CT1 1AA' },
      { name: 'addressLine1', label: 'Address 1', type: 'text' },
      { name: 'addressLine2', label: 'Address 2', type: 'text' },
      { name: 'townCity', label: 'Town/City', type: 'text' },
      { name: 'county', label: 'County', type: 'text' },
      { name: 'country', label: 'Country', type: 'select', options: COUNTRY_OPTIONS },
    ],
  },
  {
    title: 'Delivery & employer',
    icon: 'ri-building-line',
    fields: [
      // Required: the first session below is booked with this person, and a
      // learner cannot be enrolled without someone to hold it.
      { name: 'caseOwner', label: 'Case owner', type: 'select', lookup: 'caseOwner', required: true },
      { name: 'learningProvider', label: 'Learning provider', type: 'select', options: PROVIDER_OPTIONS },
      { name: 'attendanceType', label: 'Attendance type', type: 'select', options: opts(ATTENDANCE_TYPE_OPTIONS) },
      // Picked from enrolment."Employers" — choosing one auto-fills the
      // organisation below it from that employer's Employer Group.
      { name: 'employer', label: 'Employer', type: 'select', lookup: 'employer' },
      { name: 'employerAddress', label: 'Employer address', type: 'text' },
      { name: 'lineManager', label: 'Manager', type: 'text', placeholder: 'e.g. Jane Smith' },
      { name: 'mentor', label: 'Mentor', type: 'text' },
      {
        name: 'organization',
        label: 'Organisation',
        type: 'text',
        placeholder: 'e.g. Kent Business College',
        derivedFrom: 'employer',
        hint: 'Filled from the selected employer’s organisation.',
      },
      { name: 'referenceNumber', label: 'Reference number', type: 'text', placeholder: 'refnumber' },
      { name: 'extendedBreak', label: 'Extended break', type: 'text' },
    ],
  },
];

// Type/Status aren't asked for here — they're stamped on submit.
const INITIAL: Record<string, string> = {
  learningProvider: 'Kent Business College',
  country: DEFAULT_COUNTRY,
};

/** Whether a field belongs on the form as it is currently being used. */
function shows(field: FieldDef, kind: LearnerKind, phase: 'create' | 'edit'): boolean {
  if (field.only && field.only !== kind) return false;
  if (field.phase && field.phase !== phase) return false;
  return true;
}

/** Fields visible for the chosen destination table and mode. */
function visibleFields(kind: LearnerKind, phase: 'create' | 'edit'): FieldDef[] {
  return SECTIONS.flatMap((s) => s.fields).filter((f) => shows(f, kind, phase));
}

export function CreateUserModal({ onClose, onCreated, editing, onSaved }: {
  onClose: () => void;
  onCreated: (row: UserListRow) => void;
  /** The learner being edited. Omitted to create a new one. */
  editing?: UserListRow;
  /** Called with the edited row so the directory repaints without a refetch. */
  onSaved?: (row: UserListRow) => void;
}) {
  const { success, error, warning } = useToast();
  const phase: 'create' | 'edit' = editing ? 'edit' : 'create';
  const [formData, setFormData] = useState<Record<string, string>>(INITIAL);
  // Commercial is the default kind on create. When editing, the record's own
  // kind replaces it once the fields load.
  const [kind, setKind] = useState<LearnerKind>('commercial');
  const [submitting, setSubmitting] = useState(false);
  // Editing starts empty and fills in, so the form must say it is still loading
  // rather than presenting blank fields as though the learner had none.
  const [hydrating, setHydrating] = useState(Boolean(editing));
  const [hydrateError, setHydrateError] = useState<string | null>(null);

  // Live curriculum cascade: programmes load once, then cohorts follow the chosen
  // programme and groups follow the chosen cohort.
  const [programmes, setProgrammes] = useState<string[]>([]);
  const [cohorts, setCohorts] = useState<string[]>([]);
  const [groups, setGroups] = useState<string[]>([]);
  // Case owners come from Staff_users (positions Caseowner / Admin) — independent
  // of the programme cascade, so loaded once alongside programmes.
  const [caseOwners, setCaseOwners] = useState<string[]>([]);
  // Whole employer records, not just names: picking one has to read its
  // organisation to auto-fill the Organisation field below.
  const [employers, setEmployers] = useState<EmployerRow[]>([]);
  const [lookupError, setLookupError] = useState<string | null>(null);
  const [staffError, setStaffError] = useState<string | null>(null);
  const [employerError, setEmployerError] = useState<string | null>(null);

  const programme = formData.programme ?? '';
  const cohort = formData.cohort ?? '';

  const setField = (name: string, value: string) =>
    setFormData((prev) => {
      const next = { ...prev, [name]: value };
      // Picking a new programme/cohort invalidates the levels below it.
      if (name === 'programme') { next.cohort = ''; next.group = ''; }
      if (name === 'cohort') { next.group = ''; }
      // The employer select carries the record's id (names repeat, ids don't),
      // so the stored employer name and the derived organisation are both looked
      // up from it. Clearing the employer clears the organisation with it, rather
      // than stranding a company the chosen employer has nothing to do with.
      if (name === 'employerId') {
        const picked = employers.find((e) => e.id === value);
        next.employer = picked?.name ?? '';
        next.organization = picked ? picked.employerGroupNames.join(', ') : '';
      }
      return next;
    });

  // Prefill from the record. Written straight into formData rather than through
  // setField, because setField's job is to clear a cohort when its programme
  // changes — running that over a hydration would wipe the placement we are
  // loading. Booleans arrive as real booleans and are stored as the 'true' /
  // 'false' strings the checkbox inputs read.
  useEffect(() => {
    if (!editing) return;
    let cancelled = false;
    setHydrating(true);
    setHydrateError(null);
    fetchEnrolmentUserFields(editing.id)
      .then((fields) => {
        if (cancelled) return;
        const next: Record<string, string> = {};
        for (const [key, value] of Object.entries(fields)) {
          if (value === null || value === undefined) continue;
          next[key] = typeof value === 'boolean' ? String(value) : String(value);
        }
        setFormData(next);
        setKind(fields.learnerType === 'apprenticeship' ? 'apprenticeship' : 'commercial');
      })
      .catch((e: Error) => { if (!cancelled) setHydrateError(e.message); })
      .finally(() => { if (!cancelled) setHydrating(false); });
    return () => { cancelled = true; };
  }, [editing]);

  useEffect(() => {
    let cancelled = false;
    fetchProgrammes()
      .then((rows) => { if (!cancelled) setProgrammes(rows); })
      .catch((e: Error) => { if (!cancelled) setLookupError(e.message); });
    // Failure here is reported separately from the programme cascade, whose error
    // renders under the Programme section.
    fetchCaseOwners()
      .then((rows) => { if (!cancelled) setCaseOwners(rows); })
      .catch((e: Error) => { if (!cancelled) setStaffError(e.message); });
    // A failure here doesn't block the form — but it must not be silent either.
    // Reported separately so "the lookup broke" can't be mistaken for "no
    // employers exist yet", which is what the empty-state hint means.
    listEmployers()
      .then((res) => { if (!cancelled) setEmployers(res.results); })
      .catch((e: Error) => { if (!cancelled) setEmployerError(e.message); });
    return () => { cancelled = true; };
  }, []);

  // Changing a parent clears its descendants, so a stale cohort/group can never
  // be submitted alongside a newly chosen programme.
  useEffect(() => {
    let cancelled = false;
    setCohorts([]);
    setGroups([]);
    if (!programme) return;
    fetchCohorts(programme)
      .then((rows) => { if (!cancelled) setCohorts(rows); })
      .catch((e: Error) => { if (!cancelled) setLookupError(e.message); });
    return () => { cancelled = true; };
  }, [programme]);

  useEffect(() => {
    let cancelled = false;
    setGroups([]);
    if (!programme || !cohort) return;
    fetchGroups(programme, cohort)
      .then((rows) => { if (!cancelled) setGroups(rows); })
      .catch((e: Error) => { if (!cancelled) setLookupError(e.message); });
    return () => { cancelled = true; };
  }, [programme, cohort]);

  /**
   * Options + disabled state for a database-backed field.
   *
   * `employer` is absent on purpose: its options are id/label pairs rather than
   * plain strings, so renderField handles that select directly.
   */
  const lookupState = (level: Exclude<NonNullable<FieldDef['lookup']>, 'employer'>) => {
    if (level === 'programme') return { values: programmes, disabled: false, waitingFor: '', empty: 'None available' };
    if (level === 'cohort') return { values: cohorts, disabled: !programme, waitingFor: 'programme', empty: 'None available' };
    if (level === 'group') return { values: groups, disabled: !cohort, waitingFor: 'cohort', empty: 'None available' };
    return {
      values: caseOwners,
      disabled: false,
      waitingFor: '',
      // Case owners are staff accounts, so an empty list means none exist yet
      // rather than a bad selection upstream.
      empty: 'No case owners — create an admin first',
    };
  };

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    const missing = visibleFields(kind, phase).filter((f) => f.required && !formData[f.name]?.trim());
    if (missing.length > 0) {
      error('Missing details', `Please complete: ${missing.map((f) => f.label).join(', ')}`);
      return;
    }
    // Typed fields must also be well-formed — `type="email"`/`type="tel"` don't
    // enforce anything on their own, so a single letter would otherwise be saved
    // as someone's email address.
    const malformed = visibleFields(kind, phase)
      .map((f) => {
        const msg = f.type === 'email' || f.type === 'tel'
          ? formatError(f.type, formData[f.name] ?? '')
          : '';
        return msg ? `${f.label} — ${msg}` : '';
      })
      .filter(Boolean);
    if (malformed.length > 0) {
      error('Check these details', malformed.join('; '));
      return;
    }
    // Create composes the name from the two fields it asked for; edit shows the
    // stored column itself, so there is nothing to join.
    const name = editing
      ? (formData.username ?? '').trim()
      : `${formData.firstName ?? ''} ${formData.lastName ?? ''}`.trim();

    // Every Aptem field the two endpoints share. Booleans are sent as real
    // booleans; the backend coerces and stores them in boolean columns.
    const shared = {
      username: name,
      email: formData.email ?? '',
      phone: formData.phone,
      dob: formData.dob,
      // Type and Status are no longer asked for on this form, so both are
      // stamped rather than left null: the directory reads Type for its
      // learner-vs-admin styling and the Learning-plan link, and Status for its
      // Subscription-status column and status filter.
      type: formData.type || 'User',
      // Stamped on create, where asking would offer a choice that isn't real.
      // On edit the form owns both, so the record's own values are sent back.
      status: editing ? (formData.status ?? '') : 'FullUser',
      ...(editing ? { programmeStatus: formData.programmeStatus ?? '' } : {}),
      // Programme / cohort / group come from the live curriculum cascade.
      programme: formData.programme,
      cohort: formData.cohort,
      group: formData.group,
      employer: formData.employer,
      // The employer's record id, alongside the display name above: the name is
      // what listings show, this is what reaches the rest of that employer's
      // data. Empty string -> null so "no employer" clears the reference rather
      // than failing the unknown-id check.
      employerId: formData.employerId ? Number(formData.employerId) : null,
      lineManager: formData.lineManager,
      organization: formData.organization,
      title: formData.title,
      preferredName: formData.preferredName,
      gender: formData.gender,
      niNumber: formData.niNumber,
      referrer: formData.referrer,
      referrerAddress: formData.referrerAddress,
      referrerContact: formData.referrerContact,
      postcode: formData.postcode,
      addressLine1: formData.addressLine1,
      addressLine2: formData.addressLine2,
      townCity: formData.townCity,
      county: formData.county,
      country: formData.country,
      caseOwner: formData.caseOwner,
      learningProvider: formData.learningProvider,
      employerAddress: formData.employerAddress,
      mentor: formData.mentor,
      referenceNumber: formData.referenceNumber,
      extendedBreak: formData.extendedBreak,
      // Optional: "Select…" is sent as null, which clears it on edit and leaves
      // it unset on create.
      attendanceType: formData.attendanceType || null,
    };

    setSubmitting(true);
    if (editing) {
      try {
        // learnerType is deliberately not sent: switching an apprenticeship
        // learner to commercial would drop their funded-ILR compliance trail,
        // which is not an edit to make from a details form.
        const updated = await updateEnrolmentUser(editing.id, shared);
        // The row repaints from what was just saved rather than from the board
        // the PATCH returns, which describes the programme rather than this
        // table's columns. The directory reloads behind the toast for anything
        // the server derived — Subscription verified follows Status, and
        // placing a learner re-stamps their delivery window.
        onSaved?.({
          ...editing,
          name,
          type: (shared.type || 'User') as UserListRow['type'],
          email: shared.email,
          group: shared.group ?? '',
          programme: shared.programme ?? '',
          cohort: shared.cohort ?? '',
          subscriptionStatus: shared.status ?? '',
          subscriptionVerified: (shared.status ?? '').toLowerCase() === 'fulluser',
          programmeStatus: (formData.programmeStatus ?? '') as UserListRow['programmeStatus'],
        });
        const handoverMessage = reviewHandoverMessage(updated.reviewHandover);
        if (handoverMessage) warning('Coach assignment saved; handover incomplete', handoverMessage);
        else success('Learner updated', `${name}'s record was saved.`);
        onClose();
      } catch (err) {
        error('Could not save changes', err instanceof Error ? err.message : 'Unexpected error');
      } finally {
        setSubmitting(false);
      }
      return;
    }
    try {
      // One table, one endpoint: the learner-type switch is just a field now, so
      // both kinds take the same path and the response already carries the row's
      // learnerType/source.
      // The first session is sent on create only: the server books it with the
      // case owner and sets the learner's start date from it. An existing
      // learner's session is changed from their calendar, not from this form.
      const row = await createEnrolmentUser({
        ...shared,
        learnerType: kind,
      });
      const label = kind === 'commercial' ? 'Commercial learner' : 'Apprenticeship learner';

      // Every learner is invited now, so the invitation's fate is always worth
      // reporting — and the three ways it can fail are not the same thing. "No
      // account" leaves somebody who cannot sign in at all; a mail failure
      // leaves a link that still exists and can be re-sent.
      const invite = row.invitation;
      const saved = `${row.name || name} was saved.`;
      if (!invite) {
        success(`${label} created`, saved);
      } else if (invite.forbidden) {
        success(`${label} created`, saved);
        error(
          'Not permitted to invite',
          invite.error || 'You do not have permission to invite this person.',
        );
      } else if (invite.awaitingInvitation) {
        // The account exists but nobody has been emailed. That is the intended
        // outcome of creating somebody: an administrator sends the invitation
        // from Accounts once the record has been checked.
        success(
          label,
          `${saved} Send their invitation from Accounts in the Super Admin workspace when you are ready.`,
        );
      } else if (!invite.invited) {
        success(`${label} created`, saved);
        error(
          'No account created',
          invite.error || 'The learner has no sign-in account yet, so they cannot log in.',
        );
      } else if (!invite.emailSent) {
        success(`${label} created`, saved);
        error(
          'Invitation email not sent',
          invite.error || 'The invitation exists and can be re-sent, but the email did not go out.',
        );
      } else {
        success(`${label} created and invited`, `${row.name || name} was emailed a link to set their password.`);
      }
      onCreated(row);
      onClose();
    } catch (err) {
      error('Could not create user', err instanceof Error ? err.message : 'Unexpected error');
    } finally {
      setSubmitting(false);
    }
  };

  /** One Aptem-style row: label on the left, control on the right. */
  const renderField = (field: FieldDef, index: number) => {
    if (!shows(field, kind, phase)) return null;
    const value = formData[field.name] ?? '';
    return (
      <div
        key={field.name}
        className={`grid grid-cols-1 sm:grid-cols-[minmax(160px,240px)_1fr] gap-1 sm:gap-4 px-4 py-2.5 items-start ${
          index % 2 === 1 ? 'bg-background-100/40' : ''
        }`}
      >
        <label htmlFor={`cu-${field.name}`} className="text-[12px] font-medium text-foreground-600 sm:py-2 leading-snug">
          {field.label}
          {field.required ? <span className="text-red-500 ml-0.5">*</span> : <span className="text-foreground-300 ml-1">(O)</span>}
        </label>
        <div className="min-w-0">
          {field.lookup === 'employer' ? (
            /* Keyed on the record id, not the name: two employers can share a
               name, and picking the wrong record would auto-fill the wrong
               organisation. `employer` itself still stores the name, which is
               what the learner column holds. */
            <select
              id={`cu-${field.name}`}
              value={formData.employerId ?? ''}
              onChange={(e) => setField('employerId', e.target.value)}
              className={`${inputClass} cursor-pointer`}
            >
              <option value="">
                {employerError
                  ? 'Could not load employers — see below'
                  : employers.length === 0
                    ? 'No employers — create an employer profile first'
                    : 'Select…'}
              </option>
              {employers.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.employerGroupNames.length ? `${e.name} — ${e.employerGroupNames.join(', ')}` : e.name}
                </option>
              ))}
            </select>
          ) : field.derivedFrom ? (
            /* Read-only: it mirrors the employer's organisation, so a typed value
               would be silently replaced on the next employer change. */
            <input
              id={`cu-${field.name}`}
              type="text"
              value={value}
              readOnly
              placeholder={formData.employerId ? '—' : 'Select an employer first…'}
              className={`${inputClass} bg-background-100/60 text-foreground-600 cursor-not-allowed`}
            />
          ) : field.lookup ? (
            (() => {
              const { values, disabled, waitingFor, empty } = lookupState(field.lookup);
              return (
                <select
                  id={`cu-${field.name}`}
                  value={value}
                  disabled={disabled}
                  onChange={(e) => setField(field.name, e.target.value)}
                  className={`${inputClass} ${disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}`}
                >
                  <option value="">
                    {disabled ? `Select a ${waitingFor} first…` : values.length === 0 ? empty : 'Select…'}
                  </option>
                  {values.map((v) => <option key={v} value={v}>{v}</option>)}
                </select>
              );
            })()
          ) : field.type === 'select' ? (
            <select
              id={`cu-${field.name}`}
              value={value}
              onChange={(e) => setField(field.name, e.target.value)}
              className={`${inputClass} cursor-pointer`}
            >
              <option value="">Select…</option>
              {field.options?.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          ) : field.type === 'radio' ? (
            <div className="flex flex-wrap items-center gap-4 sm:py-2">
              {field.options?.map((opt) => (
                <label key={opt.value} className="flex items-center gap-1.5 text-[13px] text-foreground-700 cursor-pointer">
                  <input
                    type="radio"
                    name={`cu-${field.name}`}
                    checked={value === opt.value}
                    onChange={() => setField(field.name, opt.value)}
                    className="accent-primary-500"
                  />
                  {opt.label}
                </label>
              ))}
            </div>
          ) : field.type === 'checkbox' ? (
            <div className="sm:py-2">
              <input
                id={`cu-${field.name}`}
                type="checkbox"
                checked={value === 'true'}
                onChange={(e) => setField(field.name, e.target.checked ? 'true' : 'false')}
                className="accent-primary-500 w-4 h-4 cursor-pointer"
              />
            </div>
          ) : field.type === 'textarea' ? (
            <textarea
              id={`cu-${field.name}`}
              value={value}
              rows={3}
              placeholder={field.placeholder}
              onChange={(e) => setField(field.name, e.target.value)}
              className={inputClass}
            />
          ) : (
            <input
              id={`cu-${field.name}`}
              type={field.type}
              value={value}
              placeholder={field.placeholder}
              onChange={(e) => setField(field.name, e.target.value)}
              className={inputClass}
            />
          )}
          {field.hint && <p className="text-[11px] text-foreground-400 mt-1">{field.hint}</p>}
        </div>
      </div>
    );
  };

  return (
    <Modal
      title={editing ? `Edit ${editing.name || 'user'}` : 'Add user'}
      size="max-w-3xl"
      onClose={onClose}
      footer={
        <>
          <button type="button" className={btnSecondary} onClick={onClose} disabled={submitting}>Close</button>
          {/* Saving is held back until the record has loaded: submitting a form
              that is still filling in would write the blanks over what is
              there. */}
          <button
            type="button"
            className={btnPrimary}
            onClick={() => handleSubmit()}
            disabled={submitting || hydrating || Boolean(hydrateError)}
          >
            {submitting
              ? <><AppIcon className="ri-loader-4-line animate-spin" />Saving…</>
              : editing
                ? <><AppIcon className="ri-save-line" />Save changes</>
                : <><AppIcon className="ri-user-add-line" />Create</>}
          </button>
        </>
      }
    >
      {hydrateError && (
        <div className="mb-4 rounded-xl border border-red-200/70 bg-red-50 px-4 py-3">
          <p className="text-[12px] text-red-700">
            <AppIcon className="ri-error-warning-line mr-1" />
            Could not load this learner&apos;s details: {hydrateError}
          </p>
          <p className="mt-1 text-[11px] text-red-600">
            Nothing is editable until they load, so a blank field cannot be saved over a real value.
          </p>
        </div>
      )}
      {hydrating && !hydrateError && (
        <p className="mb-4 text-[12px] text-foreground-500">
          <AppIcon className="ri-loader-4-line animate-spin mr-1" />Loading {editing?.name || 'this learner'}&apos;s details…
        </p>
      )}
      <form onSubmit={handleSubmit} className="space-y-5">
        {SECTIONS.map((section) => {
          const fields = section.fields.filter((f) => shows(f, kind, phase));
          if (fields.length === 0) return null;
          return (
            <section key={section.title} className="rounded-xl border border-foreground-200/70 overflow-hidden">
              <header className="flex items-center gap-2 px-4 py-2.5 bg-background-100/70 border-b border-foreground-200/60">
                <AppIcon className={`${section.icon} text-primary-500`} />
                <h3 className="text-[12px] font-semibold uppercase tracking-wider text-foreground-600">{section.title}</h3>
              </header>
              <div className="divide-y divide-foreground-100">{fields.map(renderField)}</div>
              {section.title === 'Programme' && lookupError && (
                <p className="px-4 py-2 text-[11px] text-red-600 border-t border-foreground-100">
                  <AppIcon className="ri-error-warning-line mr-1" />Could not load curriculum options: {lookupError}
                </p>
              )}
              {section.title === 'Delivery & employer' && staffError && (
                <p className="px-4 py-2 text-[11px] text-red-600 border-t border-foreground-100">
                  <AppIcon className="ri-error-warning-line mr-1" />Could not load case owners: {staffError}
                </p>
              )}
              {section.title === 'Delivery & employer' && employerError && (
                <p className="px-4 py-2 text-[11px] text-red-600 border-t border-foreground-100">
                  <i className="ri-error-warning-line mr-1" />Could not load employers: {employerError}. The
                  dropdown is empty because the lookup failed, not because no employers exist — if the backend was
                  started before this feature was added, restart it.
                </p>
              )}
            </section>
          );
        })}

        {/* Destination table. Last in the form, as requested — this is the switch
            that decides which table the row is written to.

            Read-only once the record exists. Both kinds share the table, so the
            switch is one column — but an apprenticeship learner turned
            commercial loses their funded-ILR compliance trail, and a details
            form is not where that decision belongs. */}
        <section className="rounded-xl border-2 border-primary-200 overflow-hidden">
          <header className="flex items-center gap-2 px-4 py-2.5 bg-primary-50 border-b border-primary-200/60">
            <AppIcon className="ri-git-branch-line text-primary-500" />
            <h3 className="text-[12px] font-semibold uppercase tracking-wider text-primary-700">Learner type</h3>
          </header>
          <div className="p-4 space-y-2.5">
            <p className="text-[12px] text-foreground-500">
              {editing
                ? 'Set when the learner was created. Changing it would drop or invent a funded-ILR compliance trail, so it is not editable here.'
                : 'Both kinds are stored in the same learner table; this sets which kind the record is.'}
            </p>
            {([
              {
                value: 'apprenticeship' as LearnerKind,
                label: 'Apprenticeship learner',
                detail: 'Full ILR, compliance documents and the enrolment wizard.',
                disabled: Boolean(editing),
              },
              {
                value: 'commercial' as LearnerKind,
                label: 'Commercial learner',
                detail: 'Commercial delivery, no funded-ILR compliance trail.',
                disabled: Boolean(editing),
              },
            ]).map((opt) => (
              <label
                key={opt.value}
                className={`flex items-start gap-3 p-3 rounded-lg border transition-smooth ${
                  // The record's own kind stays highlighted while editing: it is
                  // fixed, not unavailable, and greying it out would read as
                  // though the learner had no kind at all.
                  kind === opt.value
                    ? `border-primary-400 bg-primary-50/60 ${opt.disabled ? 'cursor-not-allowed' : 'cursor-pointer'}`
                    : opt.disabled
                      ? 'border-foreground-200 bg-background-100/40 opacity-60 cursor-not-allowed'
                      : 'border-foreground-200 hover:bg-background-100/60 cursor-pointer'
                }`}
              >
                <input
                  type="radio"
                  name="cu-learner-kind"
                  checked={kind === opt.value}
                  disabled={opt.disabled}
                  onChange={() => setKind(opt.value)}
                  className="accent-primary-500 mt-0.5 disabled:cursor-not-allowed"
                />
                <span className="min-w-0">
                  <span className="block text-[13px] font-medium text-foreground-800">
                    {opt.label}
                    {opt.disabled && (
                      <span className="ml-2 align-middle rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide bg-foreground-200/70 text-foreground-500">
                        Unavailable
                      </span>
                    )}
                  </span>
                  <span className="block text-[11px] text-foreground-400 mt-0.5">{opt.detail}</span>
                </span>
              </label>
            ))}
          </div>
        </section>

        {/* Invitation — always sent on save. Stated rather than asked: a learner
            with no account is a record nobody can sign in as. */}
        <section className="rounded-xl border border-foreground-200/70 overflow-hidden">
          <header className="flex items-center gap-2 px-4 py-2.5 bg-background-100/70 border-b border-foreground-200/60">
            <AppIcon className="ri-mail-send-line text-primary-500" />
            <h3 className="text-[12px] font-semibold uppercase tracking-wider text-foreground-600">
              Invitation
            </h3>
          </header>
          <div className="p-4">
            <p className="text-[13px] font-medium text-foreground-800">
              They will get a platform account when you save.
            </p>
            <p className="text-[12px] text-foreground-500 mt-0.5">
              Send their invitation from Accounts in the Super Admin workspace when the record is ready — that emails a single-use link to set their own password. They cannot sign in until they do.
            </p>
          </div>
        </section>

        {/* allow Enter-to-submit without a visible duplicate button */}
        <button type="submit" className="hidden" aria-hidden="true" tabIndex={-1} />
      </form>
    </Modal>
  );
}
