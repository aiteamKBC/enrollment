/**
 * "Choose your password" — serves both emailed token flows.
 *
 * Mounted twice (see router/config.tsx):
 *   /set-password?token=…    invitation  — the first password for a new account
 *   /reset-password?token=…  reset       — a replacement for a forgotten one
 *
 * The two differ only in which endpoints they call and the words on the page,
 * so they share one component rather than duplicating the form, the strength
 * meter, and the four terminal states.
 *
 * The token is validated on load (a GET with no side effects) so a dead link
 * fails immediately with an explanation, instead of after the person has
 * chosen and typed a password twice.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { BrandLockup } from '@/components/BrandLockup';
import {
  AuthError,
  MIN_PASSWORD_LENGTH,
  apiAcceptInvitation,
  apiInvitationInfo,
  apiResetInfo,
  apiResetPassword,
  describePasswordProblem,
  type TokenInfo,
} from '@/api/auth';

type Mode = 'invitation' | 'reset';

const COPY = {
  invitation: {
    heading: 'Set your password',
    intro: 'Choose a password to activate your account.',
    submit: 'Activate account',
    working: 'Activating…',
    doneHeading: 'Your account is ready',
  },
  reset: {
    heading: 'Choose a new password',
    intro: 'Enter a new password for your account.',
    submit: 'Update password',
    working: 'Updating…',
    doneHeading: 'Password updated',
  },
} as const;

/**
 * A rough strength signal, purely advisory. The rule that actually gates
 * submission is `describePasswordProblem`, which mirrors the server.
 */
function strengthOf(password: string): { score: 0 | 1 | 2 | 3; label: string; className: string } {
  if (password.length < MIN_PASSWORD_LENGTH) {
    return { score: 0, label: 'Too short', className: 'bg-red-400' };
  }
  const variety =
    Number(/[a-z]/.test(password)) +
    Number(/[A-Z]/.test(password)) +
    Number(/[0-9]/.test(password)) +
    Number(/[^A-Za-z0-9]/.test(password));

  if (password.length >= 16 || (password.length >= 12 && variety >= 3)) {
    return { score: 3, label: 'Strong', className: 'bg-emerald-500' };
  }
  if (password.length >= 12 || variety >= 3) {
    return { score: 2, label: 'Good', className: 'bg-amber-400' };
  }
  return { score: 1, label: 'Weak', className: 'bg-orange-400' };
}

/**
 * The card every state of this page renders inside.
 *
 * Must stay at module scope. Defined inside the component it would be a new
 * component *type* on every render, so React would unmount and remount the
 * whole subtree on each keystroke — the password fields would lose focus and
 * drop every character after the first.
 */
function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen flex items-center justify-center bg-background-100 p-6">
      <div className="w-full max-w-[440px]">
        <BrandLockup size="default" className="mb-8" />
        <div className="bg-background-50 border border-background-200 rounded-2xl p-7 shadow-sm">
          {children}
        </div>
      </div>
    </div>
  );
}

export default function SetPasswordPage({ mode }: { mode: Mode }) {
  const copy = COPY[mode];
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const token = params.get('token') || '';
  const signInPath = params.get('download') === 'android' ? '/login?download=android' : '/login';
  // Arrived from signing in with the shared first-sign-in password.
  const firstSignIn = (useLocation().state as { firstSignIn?: boolean } | null)?.firstSignIn === true;

  const [info, setInfo] = useState<TokenInfo | null>(null);
  const [tokenError, setTokenError] = useState<string>('');
  const [checking, setChecking] = useState(true);

  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState(false);

  // --- validate the token on load -----------------------------------------
  useEffect(() => {
    let cancelled = false;

    if (!token) {
      setTokenError('This link is missing its token. Please use the link from your email.');
      setChecking(false);
      return;
    }

    void (async () => {
      try {
        const fetched = await (mode === 'invitation' ? apiInvitationInfo(token) : apiResetInfo(token));
        if (!cancelled) setInfo(fetched);
      } catch (err) {
        if (!cancelled) {
          setTokenError(
            err instanceof AuthError ? err.message : 'This link could not be verified.',
          );
        }
      } finally {
        if (!cancelled) setChecking(false);
      }
    })();

    return () => { cancelled = true; };
  }, [token, mode]);

  const problem = useMemo(
    () => (password ? describePasswordProblem(password, info?.email) : null),
    [password, info?.email],
  );
  const mismatch = confirm.length > 0 && password !== confirm;
  const canSubmit = !!password && !problem && !mismatch && confirm.length > 0 && !submitting;
  const strength = strengthOf(password);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setError('');
      if (!canSubmit) return;

      setSubmitting(true);
      try {
        if (mode === 'invitation') {
          await apiAcceptInvitation(token, password);
        } else {
          await apiResetPassword(token, password);
        }
        setDone(true);
      } catch (err) {
        setError(err instanceof AuthError ? err.message : 'Could not set your password.');
        setSubmitting(false);
      }
    },
    [canSubmit, mode, token, password],
  );

  if (checking) {
    return (
      <Shell>
        <div className="flex items-center gap-3 text-[14px] text-foreground-500 py-4">
          <AppIcon className="ri-loader-4-line animate-spin text-[18px]" />
          Checking your link…
        </div>
      </Shell>
    );
  }

  if (tokenError) {
    return (
      <Shell>
        <div className="w-11 h-11 rounded-full bg-red-50 border border-red-200 flex items-center justify-center mb-4">
          <AppIcon className="ri-error-warning-line text-[20px] text-red-500" />
        </div>
        <h1 className="text-[20px] font-heading font-semibold text-foreground-950 mb-2">
          This link isn&apos;t valid
        </h1>
        <p className="text-[13px] text-foreground-500 leading-relaxed mb-6">{tokenError}</p>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => navigate('/forgot-password')}
            className="flex-1 py-3 rounded-xl bg-primary-500 text-white text-[13px] font-semibold hover:bg-primary-600 transition-colors cursor-pointer"
          >
            Request a new link
          </button>
          <button
            type="button"
            onClick={() => navigate(signInPath)}
            className="px-4 py-3 rounded-xl border border-background-200 text-[13px] font-medium text-foreground-600 hover:bg-background-100 transition-colors cursor-pointer"
          >
            Sign in
          </button>
        </div>
      </Shell>
    );
  }

  if (done) {
    return (
      <Shell>
        <div className="w-11 h-11 rounded-full bg-emerald-50 border border-emerald-200 flex items-center justify-center mb-4">
          <AppIcon className="ri-check-line text-[20px] text-emerald-600" />
        </div>
        <h1 className="text-[20px] font-heading font-semibold text-foreground-950 mb-2">
          {copy.doneHeading}
        </h1>
        <p className="text-[13px] text-foreground-500 leading-relaxed mb-6">
          Sign in with your new password to continue.
        </p>
        <button
          type="button"
          onClick={() => navigate(signInPath)}
          className="w-full py-3 rounded-xl bg-primary-500 text-white text-[13px] font-semibold hover:bg-primary-600 transition-colors cursor-pointer"
        >
          Go to sign in
        </button>
      </Shell>
    );
  }

  const inputClass =
    'w-full pl-10 pr-10 py-3 rounded-xl border border-background-200 bg-background-50 text-[14px] text-foreground-900 placeholder:text-foreground-300 focus:border-primary-400 focus:ring-2 focus:ring-primary-200/50 transition-all outline-none';

  return (
    <Shell>
      <h1 className="text-[22px] font-heading font-semibold text-foreground-950 mb-1.5">
        {copy.heading}
      </h1>
      <p className="text-[13px] text-foreground-500 mb-1">{copy.intro}</p>
      {firstSignIn && (
        <p role="status" className="mt-2 mb-3 rounded-lg border border-primary-200 bg-primary-50 px-3 py-2 text-[12.5px] text-primary-800">
          You signed in with the first-sign-in password. Choose your own password to finish signing in.
        </p>
      )}
      {info && (
        <p className="text-[13px] text-foreground-700 font-medium mb-6">
          {info.displayName ? `${info.displayName} — ` : ''}
          {info.email}
        </p>
      )}

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label htmlFor="new-password" className="block text-[12px] font-semibold text-foreground-600 mb-2">
            New password
          </label>
          <div className="relative">
            <span className="absolute left-3.5 top-1/2 -translate-y-1/2 text-foreground-400">
              <AppIcon className="ri-lock-line text-[15px]" />
            </span>
            <input
              id="new-password"
              type={showPassword ? 'text' : 'password'}
              value={password}
              onChange={(e) => { setPassword(e.target.value); setError(''); }}
              placeholder={`At least ${MIN_PASSWORD_LENGTH} characters`}
              className={inputClass}
              autoComplete="new-password"
              autoFocus
              required
            />
            <button
              type="button"
              onClick={() => setShowPassword(v => !v)}
              aria-label={showPassword ? 'Hide password' : 'Show password'}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-foreground-400 hover:text-foreground-600 cursor-pointer"
            >
              <AppIcon className={showPassword ? 'ri-eye-off-line text-[15px]' : 'ri-eye-line text-[15px]'} />
            </button>
          </div>

          {password && (
            <div className="mt-2">
              <div className="flex gap-1 mb-1.5">
                {[1, 2, 3].map(step => (
                  <div
                    key={step}
                    className={`h-1 flex-1 rounded-full transition-colors ${
                      strength.score >= step ? strength.className : 'bg-background-200'
                    }`}
                  />
                ))}
              </div>
              <p className={`text-[11px] ${problem ? 'text-red-600' : 'text-foreground-400'}`}>
                {problem || strength.label}
              </p>
            </div>
          )}
        </div>

        <div>
          <label htmlFor="confirm-password" className="block text-[12px] font-semibold text-foreground-600 mb-2">
            Confirm password
          </label>
          <div className="relative">
            <span className="absolute left-3.5 top-1/2 -translate-y-1/2 text-foreground-400">
              <AppIcon className="ri-lock-2-line text-[15px]" />
            </span>
            <input
              id="confirm-password"
              type={showPassword ? 'text' : 'password'}
              value={confirm}
              onChange={(e) => { setConfirm(e.target.value); setError(''); }}
              placeholder="Type it again"
              className={inputClass}
              autoComplete="new-password"
              required
            />
          </div>
          {mismatch && (
            <p className="text-[11px] text-red-600 mt-1.5">Both passwords must match.</p>
          )}
        </div>

        {error && (
          <div className="flex items-start gap-2 px-3.5 py-3 rounded-xl bg-red-50 border border-red-200 text-[13px] text-red-700">
            <AppIcon className="ri-error-warning-line text-sm shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        <button
          type="submit"
          disabled={!canSubmit}
          className="w-full py-3.5 rounded-xl bg-primary-500 text-white text-[14px] font-semibold hover:bg-primary-600 disabled:opacity-40 disabled:cursor-not-allowed transition-all shadow-md shadow-primary-500/15 cursor-pointer"
        >
          {submitting ? (
            <span className="flex items-center justify-center gap-2">
              <AppIcon className="ri-loader-4-line animate-spin" />
              {copy.working}
            </span>
          ) : (
            copy.submit
          )}
        </button>
      </form>
    </Shell>
  );
}
