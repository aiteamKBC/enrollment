// ============================================================================
// Where a signed-in account with no access grant lands.
//
// Position no longer grants anything, so a newly created staff account arrives
// with no access and can open nothing. That is the right default — the
// alternative was every created account silently becoming a full platform
// administrator — but it must not look like a broken login. This page says what
// has happened, who can fix it, and offers the one action available: asking.
//
// The request is sent by the server, not a mailto: link, so it works whether or
// not the person has a mail client set up.
// ============================================================================
import { useState } from 'react';
import { useAuth } from "@/hooks/useAuth.helpers";
import { BrandLockup } from '@/components/BrandLockup';
import { requestAccess, type AccessRequestResult } from '@/api/accessRequest';

export default function AccessRequiredPage() {
  const { auth, logout } = useAuth();
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<AccessRequestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const name = auth.account?.displayName || auth.account?.email || '';
  const sent = result?.ok === true;

  async function send() {
    setSending(true);
    setError(null);
    try {
      setResult(await requestAccess());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not send the request.');
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="min-h-screen bg-background-100 flex items-center justify-center p-4">
      <div className="w-full max-w-lg">
        <div className="flex justify-center mb-6">
          <BrandLockup />
        </div>

        <div className="bg-background-50 rounded-2xl border border-foreground-200/60 overflow-hidden shadow-sm">
          <div className="px-6 md:px-9 pt-9 pb-7 text-center">
            <span className="w-16 h-16 rounded-2xl bg-amber-100 text-amber-600 flex items-center justify-center mx-auto mb-5">
              <AppIcon className="ri-shield-keyhole-line text-3xl"></AppIcon>
            </span>
            <h1 className="text-xl md:text-2xl font-heading font-bold text-foreground-900 mb-3">
              You don&apos;t have access yet
            </h1>
            <p className="text-[14px] text-foreground-500 leading-relaxed max-w-md mx-auto">
              Your account has been created{name ? <> for <strong className="text-foreground-700">{name}</strong></> : ''},
              but a system administrator still needs to grant you an access level before you can open
              anything.
            </p>
            <p className="text-[13px] text-foreground-400 leading-relaxed max-w-md mx-auto mt-3">
              Please contact the system administrator to grant you the required access.
            </p>
          </div>

          {/* The one action available from here. */}
          <div className="px-6 md:px-9 py-6 bg-background-100/60 border-t border-foreground-200/60">
            {sent ? (
              <div className="bg-emerald-50 border border-emerald-200/60 rounded-xl p-4 flex items-start gap-3">
                <span className="w-8 h-8 rounded-lg bg-emerald-100 flex items-center justify-center shrink-0">
                  <AppIcon className="ri-check-line text-emerald-600 text-sm"></AppIcon>
                </span>
                <div className="min-w-0">
                  <p className="text-[13px] font-semibold text-emerald-900">
                    {result?.alreadySent ? 'Your request is already with them' : 'Request sent'}
                  </p>
                  <p className="text-[12px] text-emerald-700 mt-0.5 leading-relaxed">
                    {result?.message} Sent to <span className="font-medium break-all">{result?.sentTo}</span>.
                  </p>
                  <p className="text-[11px] text-emerald-600 mt-2">
                    Once your access is granted you can use it straight away — no need to sign in again.
                  </p>
                </div>
              </div>
            ) : (
              <>
                <button
                  onClick={send}
                  disabled={sending}
                  className="w-full px-4 py-3 bg-primary-500 text-white rounded-xl text-[14px] font-semibold hover:bg-primary-600 transition-smooth cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <AppIcon className={`${sending ? 'ri-loader-4-line animate-spin' : 'ri-mail-send-line'} mr-2`}></AppIcon>
                  {sending ? 'Sending your request…' : 'Request access'}
                </button>

                {/* Shown up front, not only on failure: some people would rather
                    write the email themselves. */}
                {(result?.sentTo || !result) && (
                  <p className="text-[11px] text-foreground-400 text-center mt-3">
                    This emails the system administrator
                    {result?.sentTo ? <> at <span className="font-medium">{result.sentTo}</span></> : ''}.
                  </p>
                )}

                {result && result.ok === false && (
                  <div className="mt-4 bg-red-50 border border-red-200/60 rounded-xl p-3.5 flex items-start gap-2.5">
                    <AppIcon className="ri-error-warning-line text-red-600 text-sm mt-0.5 shrink-0"></AppIcon>
                    <p className="text-[12px] text-red-800 leading-relaxed">
                      {result.error}{' '}
                      <a href={`mailto:${result.sentTo}`} className="font-semibold underline break-all">
                        {result.sentTo}
                      </a>
                    </p>
                  </div>
                )}

                {error && (
                  <div className="mt-4 bg-red-50 border border-red-200/60 rounded-xl p-3.5 flex items-start gap-2.5">
                    <AppIcon className="ri-error-warning-line text-red-600 text-sm mt-0.5 shrink-0"></AppIcon>
                    <p className="text-[12px] text-red-800 leading-relaxed">{error}</p>
                  </div>
                )}
              </>
            )}
          </div>
        </div>

        <div className="text-center mt-5">
          <button
            onClick={logout}
            className="text-[12px] text-foreground-400 hover:text-foreground-600 transition-smooth cursor-pointer"
          >
            <AppIcon className="ri-logout-box-line mr-1"></AppIcon>Sign out
          </button>
        </div>
      </div>
    </div>
  );
}
