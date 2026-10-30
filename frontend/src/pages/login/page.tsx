import { useState, useEffect } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from "@/hooks/useAuth.helpers";
import { AuthError, PasswordSetupRequired, apiMicrosoftStart, type AuthUser } from '@/api/auth';
import { AppIcon } from '@/components/feature/AppIcon';
// Shared with RequireAuth, so the page you are sent to after signing in and
// the page you are sent back to when refused are decided by one definition.
import { postLoginRouteFor } from '@/lib/routeAccess';
import styles from './page.module.css';
import AndroidInstaller from './AndroidInstaller';

const BOOK_IMAGE_URL = '/login-open-book.png';
const SIDEBAR_LOGO_URL = 'https://jokdxsdbxorzciulkdyl.supabase.co/storage/v1/object/public/images/16480272afc94729b2911a62d1bbf85d.webp';

// UI visibility only. The download endpoint checks the current server account
// and the coach's live staff grants before serving any bytes.
function canDownloadAndroid(account: AuthUser | null) {
  return !!account && (account.role === 'learner' || account.role === 'admin'
    || (account.role === 'staff' && account.subjectType === 'staff'
      && (account.access === 'coach' || account.accesses?.includes('coach'))));
}

function destinationFor(account: AuthUser, lectureFeedbackIntent: boolean) {
  return lectureFeedbackIntent && account.role === 'learner' && account.subjectType === 'learner'
    ? '/learner/feedback?category=lecture'
    : postLoginRouteFor(account);
}

export default function LoginPage() {
  const { login, auth, isInitialized } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const androidDownload = new URLSearchParams(location.search).get('download') === 'android';
  const lectureFeedbackIntent = !androidDownload && new URLSearchParams(location.search).get('lectureFeedback') === '1';
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [rememberMe, setRememberMe] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [ssoLoading, setSsoLoading] = useState(false);

  // A refused Microsoft sign-in comes back with ?sso_error=... . Lift it into
  // the form's error box, then strip it so a refresh cannot resurrect it.
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const ssoError = params.get('sso_error');
    if (!ssoError) return;
    setError(ssoError);
    params.delete('sso_error');
    const rest = params.toString();
    navigate({ pathname: location.pathname, search: rest ? `?${rest}` : '' }, { replace: true });
  }, [location.search, location.pathname, navigate]);

  // Bounce an already-signed-in visitor to their console after the server
  // session has been resolved.
  //
  // Gated on `account` — the server's record — rather than `isAuthenticated`, to
  // match RequireAuth. The two must agree on what "signed in" means: a local
  // preview identity satisfies `isAuthenticated` with no session behind it, so
  // sending it to a protected route would have it bounced straight back here,
  // and round again.
  useEffect(() => {
    if (!isInitialized || !auth.account) return;
    if (androidDownload && canDownloadAndroid(auth.account)) return;
    navigate(destinationFor(auth.account, lectureFeedbackIntent), { replace: true });
  }, [isInitialized, auth.account, androidDownload, lectureFeedbackIntent, navigate]);

  const handleLogin = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    // Read the displayed values at submission: password managers can fill the
    // inputs without dispatching change events to React.
    const fields = new FormData(e.currentTarget);
    const email = String(fields.get('email') || '').trim();
    const password = String(fields.get('password') || '');
    setError('');

    if (!email) {
      setError('Please enter your email address');
      return;
    }
    if (!password) {
      setError('Please enter your password');
      return;
    }

    setIsLoading(true);
    try {
      const account = await login(email, password, rememberMe);
      if (androidDownload && canDownloadAndroid(account)) {
        setIsLoading(false);
        return;
      }
      navigate(destinationFor(account, lectureFeedbackIntent), { replace: true });
    } catch (err) {
      if (err instanceof PasswordSetupRequired) {
        const path = androidDownload ? `${err.setPasswordPath}&download=android` : err.setPasswordPath;
        navigate(path, { replace: true, state: { firstSignIn: true } });
        return;
      }
      setError(
        err instanceof AuthError
          ? err.message
          : 'Something went wrong signing in. Please try again.',
      );
      setIsLoading(false);
    }
  };

  const handleMicrosoftLogin = async () => {
    setError('');
    setSsoLoading(true);
    try {
      // This is a full navigation so Microsoft can return to the callback that
      // creates the server session cookie.
      window.location.href = await (androidDownload ? apiMicrosoftStart('android') : lectureFeedbackIntent ? apiMicrosoftStart(undefined, 'lecture-feedback') : apiMicrosoftStart());
    } catch (err) {
      setError(
        err instanceof AuthError
          ? err.message
          : 'Could not start sign-in with Microsoft. Please try again.',
      );
      setSsoLoading(false);
    }
  };

  if (androidDownload && isInitialized && canDownloadAndroid(auth.account)) {
    return (
      <main className={styles.downloadPage}>
        <section className={styles.downloadCard} aria-labelledby="download-heading">
          <img src={SIDEBAR_LOGO_URL} alt="Kent Business College" width="88" />
          <h1 id="download-heading">Get KBC Learn for Android</h1>
          <p>You are signed in. Download the test app, open the downloaded file and follow your phone’s installation steps.</p>
          <AndroidInstaller />
          <p>After installation, sign in to the app with your usual LMS account.</p>
          {auth.account?.role === 'admin' && <p>Administrator download access does not add a learner or coach role. Use an existing learner or coach account to test those app screens.</p>}
          <button className={styles.microsoftButton} onClick={() => navigate(postLoginRouteFor(auth.account!), { replace: true })}>
            Go to my workspace
          </button>
        </section>
      </main>
    );
  }

  return (
    <main className={styles.page}>
      <section className={styles.card} aria-labelledby="login-heading">
        <div className={styles.bookStage}>
          <div className={styles.patterns} aria-hidden="true">
            <span className={styles.patternTopLeft} />
            <span className={styles.patternBottomRight} />
          </div>

          <img
            className={styles.bookImage}
            src={BOOK_IMAGE_URL}
            alt="Kent Business College learning journey illustration"
          />

          <div className={styles.logoOverlay}>
            <img
              src={SIDEBAR_LOGO_URL}
              alt="KENT logo"
              className={styles.logo}
            />
          </div>

          <div className={styles.formPanel}>
            <div className={styles.formContent}>

            <header className={styles.intro}>
              <h1 id="login-heading">Welcome back</h1>
              <p>{androidDownload ? 'Sign in to download KBC Learn for Android' : 'Sign in to your workspace'}</p>
            </header>

            <form onSubmit={handleLogin} className={styles.form} noValidate={false}>
              <div className={styles.fieldGroup}>
                <label htmlFor="email">Email address</label>
                <div className={styles.inputShell}>
                  <AppIcon className={`ri-mail-line ${styles.inputIcon}`} aria-hidden="true" />
                  <input
                    id="email"
                    name="email"
                    type="email"
                    onChange={() => setError('')}
                    placeholder="your.email@kbc.test"
                    autoComplete="email"
                    aria-invalid={!!error}
                    aria-describedby={error ? 'login-error' : undefined}
                    required
                  />
                </div>
              </div>

              <div className={styles.fieldGroup}>
                <div className={styles.labelRow}>
                  <label htmlFor="password">Password</label>
                  <button type="button" onClick={() => navigate('/forgot-password')} className={styles.forgotButton}>
                    Forgot password?
                  </button>
                </div>
                <div className={styles.inputShell}>
                  <AppIcon className={`ri-lock-line ${styles.inputIcon}`} aria-hidden="true" />
                  <input
                    id="password"
                    name="password"
                    type={showPassword ? 'text' : 'password'}
                    onChange={() => setError('')}
                    placeholder="Enter your password"
                    autoComplete="current-password"
                    aria-invalid={!!error}
                    aria-describedby={error ? 'login-error' : undefined}
                    required
                  />
                  <button
                    type="button"
                    className={styles.passwordToggle}
                    onClick={() => setShowPassword((visible) => !visible)}
                    aria-label={showPassword ? 'Hide password' : 'Show password'}
                    aria-pressed={showPassword}
                  >
                    <AppIcon className={showPassword ? 'ri-eye-off-line' : 'ri-eye-line'} aria-hidden="true" />
                  </button>
                </div>
              </div>

              <div className={styles.rememberGroup}>
                <button
                  type="button"
                  className={styles.rememberRow}
                  aria-pressed={rememberMe}
                  aria-describedby="remember-hint"
                  onClick={() => setRememberMe((remembered) => !remembered)}
                >
                  <span className={styles.checkboxVisual} aria-hidden="true">
                    <AppIcon className="ri-check-line" />
                  </span>
                  <span>Remember me</span>
                </button>
                {/* Spelled out because it stopped being guessable. The box used
                    to choose between a shorter and a longer sign-in, both of
                    which survived closing the browser; now leaving it off ends
                    the session with the browser. Unexplained, that reads as a
                    bug rather than the thing the box was asking about. */}
                <p id="remember-hint" className={styles.rememberHint}>
                  Stay signed in on this device. Leave it off and closing your
                  browser signs you out.
                </p>
              </div>

              {error && (
                <div id="login-error" className={styles.error} role="alert" aria-live="polite">
                  <AppIcon className="ri-error-warning-line" aria-hidden="true" />
                  <span>{error}</span>
                </div>
              )}

              <button
                type="submit"
                disabled={isLoading}
                className={styles.primaryButton}
              >
                {isLoading ? (
                  <span className={styles.loadingLabel}>
                    <AppIcon className="ri-loader-4-line animate-spin" aria-hidden="true" />
                    Signing in...
                  </span>
                ) : (
                  'Sign in to Workspace'
                )}
              </button>
            </form>

              {/* Always offered. On a deployment without a Microsoft app
                  registration, the click reports "not configured" in the
                  error box above rather than the button silently vanishing. */}
                <div className={styles.ssoBlock}>
                <div className={styles.divider} aria-hidden="true">
                  <span />
                  <strong>OR</strong>
                  <span />
                </div>

                <button
                  type="button"
                  onClick={handleMicrosoftLogin}
                  disabled={ssoLoading || isLoading}
                  className={styles.microsoftButton}
                >
                  {ssoLoading ? (
                    <>
                      <AppIcon className="ri-loader-4-line animate-spin" aria-hidden="true" />
                      Redirecting to Microsoft...
                    </>
                  ) : (
                    <>
                      <svg className={styles.microsoftMark} viewBox="0 0 21 21" aria-hidden="true">
                        <rect x="1" y="1" width="9" height="9" fill="#F25022" />
                        <rect x="11" y="1" width="9" height="9" fill="#7FBA00" />
                        <rect x="1" y="11" width="9" height="9" fill="#00A4EF" />
                        <rect x="11" y="11" width="9" height="9" fill="#FFB900" />
                      </svg>
                      Sign in with Microsoft
                    </>
                  )}
                </button>

                <p className={styles.ssoHint}>
                  Use your work account. You must already have access to this platform.
                </p>
                </div>

            <footer className={styles.secureFooter}>
              <span>
                <AppIcon className="ri-shield-check-line" aria-hidden="true" />
                Secure sign-in
              </span>
            </footer>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
