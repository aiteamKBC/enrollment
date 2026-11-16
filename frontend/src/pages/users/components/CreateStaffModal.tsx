import { useState } from 'react';
import { DatePickerField } from '@/components/feature/DatePickerField';
import { AppIcon } from '@/components/feature/AppIcon';
import { useToast } from "@/hooks/useToast.helpers";
import { ADMIN_POSITION, createStaffUser } from '@/api/staffUsers';
import type { StaffAccess, StaffUserRow } from '@/api/staffUsers';
import { Modal } from './Modal';
import { inputClass, btnPrimary, btnSecondary } from './ui';

// ============================================================================
// Create staff — the Aptem-shaped "Add user" form.
//
// Mirrors Aptem's admin variant of the Add-user screen: the user type is fixed
// (this form only creates staff), and email is entered twice to catch typos in
// an address the account holder will need to sign in with.
//
// Writes to enrolment."Staff_users" via createStaffUser — staff are not learners,
// so they never touch Enrolment_Users / Commercial_users.
//
// Roles are access grants. One staff member can coach and tutor; super-admin
// already grants every workspace and is therefore selected alone.
// ============================================================================
const ROLE_OPTIONS: { id: StaffAccess; label: string }[] = [
  { id: 'super-admin', label: 'Super Admin' },
  { id: 'coach', label: 'Coach' },
  { id: 'tutor', label: 'Tutor' },
];

type FieldType = 'text' | 'email' | 'tel' | 'date' | 'radio';

interface FieldDef {
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
  { name: 'firstName', label: 'First name', type: 'text', required: true, placeholder: 'firstname' },
  { name: 'lastName', label: 'Surname', type: 'text', required: true, placeholder: 'lastname' },
  { name: 'gender', label: 'Gender', type: 'radio', options: GENDER_OPTIONS },
  { name: 'email', label: 'Email', type: 'email', required: true, placeholder: 'you@youremail.com' },
  { name: 'confirmEmail', label: 'Confirm email', type: 'email', required: true, placeholder: 'you@youremail.com' },
  { name: 'phone', label: 'Mobile', type: 'tel', placeholder: 'NNN NNNN NNNN or +NNN NNN NNN NNNN' },
  { name: 'title', label: 'Title', type: 'text', placeholder: 'title' },
  { name: 'organization', label: 'Organisation', type: 'text', placeholder: 'e.g. Kent Business College' },
];

export function CreateStaffModal({ onClose, onCreated }: {
  onClose: () => void;
  onCreated: (row: StaffUserRow) => void;
}) {
  const { success, error } = useToast();
  const [formData, setFormData] = useState<Record<string, string>>({});
  const [roles, setRoles] = useState<StaffAccess[]>([]);
  const [submitting, setSubmitting] = useState(false);

  const setField = (name: string, value: string) => setFormData((prev) => ({ ...prev, [name]: value }));
  const toggleRole = (role: StaffAccess) => setRoles((current) => {
    if (current.includes(role)) return current.filter((selected) => selected !== role);
    return role === 'super-admin' ? [role] : [...current.filter((selected) => selected !== 'super-admin'), role];
  });

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    const missing = FIELDS.filter((f) => f.required && !formData[f.name]?.trim());
    if (missing.length > 0) {
      error('Missing details', `Please complete: ${missing.map((f) => f.label).join(', ')}`);
      return;
    }
    if (roles.length === 0) {
      error('Roles required', 'Choose at least one role for this staff member.');
      return;
    }
    // Compared case-insensitively: addresses aren't case-sensitive in practice,
    // and a capitalisation difference isn't the typo this check is for.
    const email = (formData.email ?? '').trim();
    if (email.toLowerCase() !== (formData.confirmEmail ?? '').trim().toLowerCase()) {
      error('Emails do not match', 'Please check the email and confirmation match.');
      return;
    }

    const name = `${formData.firstName ?? ''} ${formData.lastName ?? ''}`.trim();
    setSubmitting(true);
    try {
      const row = await createStaffUser({
        username: name,
        email,
        position: ADMIN_POSITION,
        accesses: roles,
        // Staff share the learner tables' Type/Status vocabulary; "Admin" marks
        // the row as a non-learner. The directory's Type column shows the
        // position; the selected access grants determine their permissions.
        type: 'Admin',
        status: 'FullUser',
        phone: formData.phone,
        title: formData.title,
        gender: formData.gender,
        organization: formData.organization,
      });
      // Creating a colleague always invites them. The API reports the
      // invitation's outcome separately from the record's creation: the person
      // exists either way, and the failures differ — "no account" means nobody
      // can sign in at all, while a mail failure leaves a link that can still
      // be re-sent.
      const invite = row.invitation;
      const label = 'Staff member created';
      const created = `${row.name || name} was created with ${roles.map((role) => ROLE_OPTIONS.find((option) => option.id === role)?.label).join(' and ')} access.`;
      if (!invite) {
        success(label, created);
      } else if (invite.forbidden) {
        // No login account exists. Say so plainly rather than implying a
        // transient mail problem — somebody with the right role must re-invite.
        success(label, created);
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
          `${created} Send their invitation from Accounts in the Super Admin workspace when you are ready.`,
        );
      } else if (!invite.invited) {
        success(label, created);
        error(
          'No account created',
          invite.error || 'They have no sign-in account yet, so they cannot log in.',
        );
      } else if (!invite.emailSent) {
        success(label, created);
        error(
          'Invitation email not sent',
          invite.error || 'The invitation exists and can be re-sent, but the email did not go out.',
        );
      } else {
        success(
          'Staff member created and invited',
          `${row.name || name} was emailed a link to set their password.`,
        );
      }
      onCreated(row);
      onClose();
    } catch (err) {
      error(
        'Could not create staff member',
        err instanceof Error ? err.message : 'Unexpected error',
      );
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
        <label htmlFor={`cs-${field.name}`} className="text-[12px] font-medium text-foreground-600 sm:py-2 leading-snug">
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
                    name={`cs-${field.name}`}
                    checked={value === opt.value}
                    onChange={() => setField(field.name, opt.value)}
                    className="accent-primary-500"
                  />
                  {opt.label}
                </label>
              ))}
            </div>
          ) : field.type === 'date' ? (
            <DatePickerField label={field.label} hideLabel inputId={`cs-${field.name}`} value={value} onChange={next => setField(field.name, next)} required={field.required} />
          ) : (
            <input
              id={`cs-${field.name}`}
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
      title="Create staff member"
      size="max-w-3xl"
      onClose={onClose}
      footer={
        <>
          <button type="button" className={btnSecondary} onClick={onClose} disabled={submitting}>Close</button>
          <button type="button" className={btnPrimary} onClick={() => handleSubmit()} disabled={submitting}>
            {submitting ? <><AppIcon className="ri-loader-4-line animate-spin" />Saving…</> : <><AppIcon className="ri-shield-user-line" />Create</>}
          </button>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-5">
        <section className="rounded-xl border border-foreground-200/70 overflow-hidden">
          <header className="flex items-center gap-2 px-4 py-2.5 bg-background-100/70 border-b border-foreground-200/60">
            <AppIcon className="ri-shield-user-line text-primary-500" />
            <h3 className="text-[12px] font-semibold uppercase tracking-wider text-foreground-600">Identity</h3>
          </header>
          <div className="divide-y divide-foreground-100">
            <div className="grid grid-cols-1 sm:grid-cols-[minmax(160px,240px)_1fr] gap-1 sm:gap-4 px-4 py-2.5 items-start">
              <span className="text-[12px] font-medium text-foreground-600 sm:py-2">User type</span>
              <p className="text-[13px] font-semibold text-primary-700 sm:py-2">Staff member</p>
            </div>
            {FIELDS.map(renderField)}
            <fieldset className="grid grid-cols-1 sm:grid-cols-[minmax(160px,240px)_1fr] gap-1 sm:gap-4 px-4 py-2.5 items-start">
              <legend className="sr-only">Roles</legend>
              <span className="text-[12px] font-medium text-foreground-600 sm:py-2">Roles <span className="text-red-500">*</span></span>
              <details className="min-w-0">
                <summary aria-label="Choose roles" className={`${inputClass} cursor-pointer list-none flex items-center justify-between gap-2`}>
                  <span>{roles.length ? roles.map((role) => ROLE_OPTIONS.find((option) => option.id === role)?.label).join(', ') : 'Select roles'}</span>
                  <AppIcon className="ri-arrow-down-s-line" />
                </summary>
                <div className="mt-1 rounded-lg border border-foreground-200 bg-background-50 p-3 space-y-2">
                  {ROLE_OPTIONS.map(({ id, label }) => (
                    <label key={id} className="flex items-center gap-2 text-[13px] text-foreground-700 cursor-pointer">
                      <input type="checkbox" checked={roles.includes(id)} onChange={() => toggleRole(id)} className="accent-primary-500" />
                      {label}
                    </label>
                  ))}
                  <p className="text-[12px] text-foreground-500">Coach and Tutor can be combined. Super Admin includes all access.</p>
                </div>
              </details>
            </fieldset>
          </div>
        </section>

        {/* Invitation — always sent on save. Stated, not asked: an account with
            no credential is a record nobody can sign in as. */}
        <section className="rounded-xl border border-foreground-200 overflow-hidden">
          <header className="flex items-center gap-2 px-4 py-2.5 bg-background-100 border-b border-foreground-200/60">
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
            <p className="text-[12px] text-foreground-500 mt-2">Their selected roles give them access to the corresponding workspaces.</p>
          </div>
        </section>

        {/* allow Enter-to-submit without a visible duplicate button */}
        <button type="submit" className="hidden" aria-hidden="true" tabIndex={-1} />
      </form>
    </Modal>
  );
}
