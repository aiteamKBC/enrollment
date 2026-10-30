import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AuthError, apiForgotPassword } from '@/api/auth';
import loginStyles from '../login/page.module.css';
import styles from './page.module.css';

// The sign-in page's artwork, so reset and sign-in read as one screen.
const BOOK_IMAGE_URL = '/login-open-book.png';
const SIDEBAR_LOGO_URL = 'https://jokdxsdbxorzciulkdyl.supabase.co/storage/v1/object/public/images/16480272afc94729b2911a62d1bbf85d.webp';

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [sending, setSending] = useState(false);
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (!email.trim() || !email.includes('@')) {
      setError('Please enter a valid email address');
      return;
    }

    setSending(true);
    try {
      // Succeeds whether or not the address has an account — the backend does
      // not disclose which, so this screen must not either. An enrolled learner
      // who has never set a password is emailed the set-password link instead.
      setMessage(await apiForgotPassword(email.trim()));
      setSubmitted(true);
    } catch (err) {
      setError(
        err instanceof AuthError ? err.message : 'Could not send the reset email. Please try again.',
      );
    } finally {
      setSending(false);
    }
  };

  return (
    <main className={loginStyles.page}>
      <section className={loginStyles.card} aria-labelledby="forgot-password-heading">
        <div className={loginStyles.bookStage}>
          <div className={loginStyles.patterns} aria-hidden="true">
            <span className={loginStyles.patternTopLeft} />
            <span className={loginStyles.patternBottomRight} />
          </div>

          <img
            className={loginStyles.bookImage}
            src={BOOK_IMAGE_URL}
            alt="Kent Business College learning journey illustration"
          />

          <div className={loginStyles.logoOverlay}>
            <img src={SIDEBAR_LOGO_URL} alt="KENT logo" className={loginStyles.logo} />
          </div>

          <div className={loginStyles.formPanel}>
            <div className={loginStyles.formContent}>
              <header className={loginStyles.intro}>
                <h1 id="forgot-password-heading">{submitted ? 'Check your email' : 'Reset your password'}</h1>
                <p>
                  {submitted
                    ? 'Follow the link we sent to continue'
                    : 'Enter your email and we will send you a link to reset your password — or to set one if you are new'}
                </p>
              </header>

              {submitted ? (
                <div className={styles.successContent}>
                  <div className={styles.successMessage} role="status" aria-live="polite">
                    <AppIcon className="ri-mail-check-line" aria-hidden="true" />
                    <span>{message || 'If that address is registered, we have emailed it a link to reset or set your password.'}</span>
                  </div>
                  <ul className={styles.steps}>
                    <li>
                      <AppIcon className="ri-key-2-line" aria-hidden="true" />
                      <span>Already have a password? The reset link works once and expires in an hour.</span>
                    </li>
                    <li>
                      <AppIcon className="ri-user-add-line" aria-hidden="true" />
                      <span>New learner? The set-password link opens your account and expires in 7 days.</span>
                    </li>
                    <li>
                      <AppIcon className="ri-spam-2-line" aria-hidden="true" />
                      <span>Nothing after a few minutes? Check your junk or spam folder.</span>
                    </li>
                  </ul>
                  <button type="button" onClick={() => navigate('/login')} className={loginStyles.primaryButton}>
                    Back to Sign in
                  </button>
                  <button
                    type="button"
                    className={styles.textButton}
                    onClick={() => { setSubmitted(false); setMessage(''); }}
                  >
                    Use a different email address
                  </button>
                </div>
              ) : (
                <form onSubmit={handleSubmit} className={loginStyles.form}>
                  <div className={loginStyles.fieldGroup}>
                    <label htmlFor="email">Email address</label>
                    <div className={loginStyles.inputShell}>
                      <AppIcon className={`ri-mail-line ${loginStyles.inputIcon}`} aria-hidden="true" />
                      <input
                        id="email"
                        type="email"
                        value={email}
                        onChange={(e) => { setEmail(e.target.value); setError(''); }}
                        placeholder="your.email@kbc.test"
                        autoComplete="email"
                        aria-invalid={!!error}
                        aria-describedby={error ? 'forgot-password-error' : undefined}
                        required
                      />
                    </div>
                  </div>

                  {error && (
                    <div id="forgot-password-error" className={loginStyles.error} role="alert" aria-live="polite">
                      <AppIcon className="ri-error-warning-line" aria-hidden="true" />
                      <span>{error}</span>
                    </div>
                  )}

                  <button type="submit" disabled={!email || sending} className={loginStyles.primaryButton}>
                    {sending ? (
                      <span className={loginStyles.loadingLabel}>
                        <AppIcon className="ri-loader-4-line animate-spin" aria-hidden="true" />
                        Sending…
                      </span>
                    ) : (
                      'Send Reset Link'
                    )}
                  </button>
                </form>
              )}

              <footer className={`${loginStyles.secureFooter} ${styles.returnFooter}`}>
                <p>
                  Remember your password?{' '}
                  <button type="button" onClick={() => navigate('/login')}>
                    Sign in
                  </button>
                </p>
              </footer>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
