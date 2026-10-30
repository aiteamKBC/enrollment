// ============================================================================
// Send somebody their platform invitation, from the user directory.
//
// Creating a person no longer emails them — they get an account, and an
// administrator sends the invitation once the record has been checked. The
// Accounts page has a button for accounts that already exist, but somebody who
// was created before that change, or whose account provisioning failed, has no
// account row at all and so never appears there.
//
// This closes that gap. It posts to /accounts/invite/, which provisions the
// account *and* emails the link in one step, with the same authorisation the
// creation forms use — notably "only an admin may invite another admin", which
// a role check on the route alone would not enforce.
// ============================================================================
import { useState } from 'react';
import { apiInviteAccount, type SubjectType } from '@/api/auth';

export function SendInvitationButton({
  subjectType,
  subjectId,
  name,
  email,
  onSent,
}: {
  subjectType: SubjectType;
  subjectId: number;
  name: string;
  /** Without one there is nowhere to send it; the button says so rather than
   *  failing on submit. */
  email?: string | null;
  onSent?: () => void;
}) {
  const [state, setState] = useState<'idle' | 'sending' | 'sent'>('idle');
  const [error, setError] = useState<string | null>(null);

  const hasEmail = !!(email || '').trim();

  async function send() {
    if (!hasEmail || state === 'sending') return;
    setState('sending');
    setError(null);
    try {
      const result = await apiInviteAccount(subjectType, subjectId);
      // The account can be created while the email itself fails — a mail
      // outage, say. Reported as an error so it is not mistaken for success,
      // but the account exists and the send can be retried from Accounts.
      if (result.emailSent === false) {
        setError(result.emailError || 'The account exists but the email did not go out.');
        setState('idle');
        return;
      }
      setState('sent');
      onSent?.();
    } catch (sendError) {
      setError(sendError instanceof Error ? sendError.message : 'Could not send the invitation.');
      setState('idle');
    }
  }

  if (state === 'sent') {
    return (
      <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-[12px] font-medium text-emerald-700">
        <i className="ri-check-line text-[13px]" />
        Invitation sent
      </span>
    );
  }

  return (
    <span className="inline-flex flex-col items-start gap-0.5">
      <button
        onClick={() => void send()}
        disabled={!hasEmail || state === 'sending'}
        title={
          hasEmail
            ? `Email ${name} a link to set their password`
            : `${name} has no email address, so there is nowhere to send an invitation`
        }
        className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-primary-200 bg-primary-50 px-2.5 py-1 text-[12px] font-medium text-primary-700 transition-smooth hover:border-primary-300 hover:bg-primary-100 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <i className={`text-[13px] ${state === 'sending' ? 'ri-loader-4-line animate-spin' : 'ri-mail-send-line'}`} />
        {state === 'sending' ? 'Sending…' : 'Send invitation'}
      </button>
      {error && <span className="max-w-[220px] text-[11px] text-red-600">{error}</span>}
    </span>
  );
}
