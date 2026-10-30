import { useState, useRef, useEffect, useLayoutEffect, useCallback, type ReactNode, type RefObject } from 'react';
import { Link } from 'react-router-dom';
import { createPortal } from 'react-dom';
import { Moon, Sun } from 'lucide-react';
import { LogOut, Menu } from 'lucide-react';
import { useAuth } from "@/hooks/useAuth.helpers";
import { apiForgotPassword } from '@/api/auth';
// Explicit rather than auto-imported: unplugin-auto-import does not run under
// vitest, so an auto-imported AppIcon makes this component untestable.
import { AppIcon } from '@/components/feature/AppIcon';
import { useTheme } from '@/hooks/useTheme';
import { BrandLockup } from '@/components/BrandLockup';
import { WorkspaceSwitcher } from '@/components/feature/WorkspaceSwitcher';
import { PreviousRecordMenuItem } from '@/features/old-otjh/PreviousRecordMenuItem';
import { LEARNER_HEADER_PATTERN_URL } from './learnerShellAssets';

interface HeaderProps {
  accountButtonRef?: RefObject<HTMLButtonElement | null>;
  pageTitle: string;
  pageIcon?: ReactNode;
  pageSubtitle?: string;
  headerExtras?: ReactNode;
  onOpenSearch: () => void;
  userName?: string;
  onToggleMobileSidebar?: () => void;
  mobileSidebarOpen?: boolean;
  mobileMenuButtonRef?: RefObject<HTMLButtonElement | null>;
  role?: string;
  /** Visual workspace chrome, when it intentionally differs from the account role. */
  appearance?: string;
  workspaceLabel?: string;
  personalLearning?: boolean;
  /** The sidebar carries its own Sign out (the learner look), so the floating one would duplicate it. */
  sidebarSignOut?: boolean;
}

/** "Demo Admin" -> "DA". A single word falls back to its first two letters. */
function initialsOf(name: string) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return 'U';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

const MONOCHROME_STORAGE_KEY = 'accessibility-monochrome';

function readMonochromePreference() {
  if (typeof window === 'undefined') return false;

  try {
    return window.localStorage.getItem(MONOCHROME_STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
}

/** The same avatar in the trigger, the menu and the dialog, so the three read
    as one object being carried between them. */
function AccountAvatar({ initials, className = '', textClassName = 'text-[11px]' }: { initials: string; className?: string; textClassName?: string }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-primary-400 via-primary-500 to-primary-700 font-bold uppercase tracking-tight text-white ring-1 ring-inset ring-white/25 ${className}`}
      aria-hidden="true"
    >
      <span className={textClassName}>{initials}</span>
    </span>
  );
}

function AccessibilityBadge({ className = '' }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="12" cy="12" r="10.2" stroke="currentColor" strokeWidth="1.8" />
      <circle cx="12" cy="6.8" r="1.5" fill="currentColor" />
      <path d="M5.6 9.4 12 10.2l6.4-.8" stroke="currentColor" strokeWidth="2.15" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M12 10.3v4.8m0 0-3.2 5m3.2-5 3.2 5" stroke="currentColor" strokeWidth="2.15" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function SignOutConfirmModal({
  displayName,
  email,
  onClose,
  onConfirm,
}: {
  displayName: string;
  email: string;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const confirmRef = useRef<HTMLButtonElement>(null);
  const initials = initialsOf(displayName);

  // Escape closes, the confirm button takes focus on open and hands it back to
  // whatever opened the dialog on close, and the page behind cannot scroll.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    confirmRef.current?.focus();

    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
      }
    }
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('keydown', handleKey);
      document.body.style.overflow = previousOverflow;
      opener?.focus?.();
    };
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-labelledby="signout-title">
      <div className="dialog-backdrop-in absolute inset-0 bg-foreground-950/45 backdrop-blur-[3px]" onClick={onClose}></div>

      <div className="dialog-card-in relative w-full max-w-[26rem] overflow-hidden rounded-2xl bg-background-50 ring-1 ring-foreground-100 shadow-[0_28px_70px_-28px_oklch(var(--foreground-950)/0.45)]">
        {/* A hairline of danger colour along the top edge carries the tone of the
            action without turning the whole card into an error state. */}
        <div className="h-1 w-full bg-gradient-to-r from-red-400 via-red-500 to-red-400"></div>

        <div className="px-6 pt-5">
          <div className="flex items-start gap-3.5">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-red-50 ring-1 ring-red-100">
              <AppIcon className="ri-logout-box-line text-xl text-red-500"></AppIcon>
            </span>
            <div className="min-w-0 pt-0.5">
              <h2 id="signout-title" className="font-heading text-lg font-bold leading-tight text-foreground-900">Sign out?</h2>
              <p className="mt-1.5 text-[0.8125rem] leading-relaxed text-foreground-400">
                You will need to sign in again to get back to your workspace, and anything unsaved will be lost.
              </p>
            </div>
          </div>

          {/* Which account is about to be signed out — the one thing the old
              dialog left the user to remember. */}
          <div className="mt-4 flex items-center gap-3 rounded-xl bg-background-100 px-3 py-2.5 ring-1 ring-background-200/80">
            <AccountAvatar initials={initials} className="h-8 w-8" />
            <div className="min-w-0">
              <p className="truncate text-[0.8125rem] font-semibold leading-tight text-foreground-800">{displayName}</p>
              <p className="truncate text-[0.75rem] leading-tight text-foreground-400">{email}</p>
            </div>
          </div>
        </div>

        <div className="mt-5 flex flex-col-reverse gap-2 border-t border-background-200/70 bg-background-100/40 px-6 py-4 sm:flex-row sm:justify-end">
          <button
            onClick={onClose}
            className="cursor-pointer whitespace-nowrap rounded-xl bg-background-50 px-4 py-2.5 text-[0.8125rem] font-semibold text-foreground-600 ring-1 ring-background-300 transition-smooth hover:bg-background-100 hover:text-foreground-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
          >
            Stay signed in
          </button>
          <button
            ref={confirmRef}
            onClick={onConfirm}
            className="flex cursor-pointer items-center justify-center gap-2 whitespace-nowrap rounded-xl bg-red-500 px-4 py-2.5 text-[0.8125rem] font-semibold text-white shadow-lg shadow-red-500/25 transition-smooth hover:bg-red-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-400 focus-visible:ring-offset-2"
          >
            <AppIcon className="ri-logout-box-line text-base"></AppIcon> Sign out
          </button>
        </div>
      </div>
    </div>
  );
}

// Notification sound
export function Header({ accountButtonRef, pageTitle, pageIcon, pageSubtitle, headerExtras, onOpenSearch, userName = 'Sarah Mitchell', onToggleMobileSidebar, mobileSidebarOpen = false, mobileMenuButtonRef, role, appearance, workspaceLabel, personalLearning = false, sidebarSignOut = false }: HeaderProps) {
  const { auth, logout } = useAuth();
  const { theme, toggleTheme } = useTheme();
  // Profile is the only dropdown left in the header, so the state that used to
  // coordinate six of them is gone along with them — as are the hard-coded
  // notification, task and message lists that fed their badge counts.
  const [profileOpen, setProfileOpen] = useState(false);
  const [signOutOpen, setSignOutOpen] = useState(false);
  const [accessibilityOpen, setAccessibilityOpen] = useState(false);
  // Sending the reset email is an action with an outcome, so the item reports
  // it in place rather than closing the menu and leaving the person guessing
  // whether anything happened.
  const [resetState, setResetState] = useState<'idle' | 'sending' | 'sent' | 'failed'>('idle');
  const [resetError, setResetError] = useState('');
  const [monochrome, setMonochrome] = useState(readMonochromePreference);

  const profileRef = useRef<HTMLDivElement>(null);

  /** Email this person a password-reset link.
   *
   * Uses the same endpoint the sign-in page's "Forgot password?" uses, so there
   * is one reset flow rather than a second one that could drift. That endpoint
   * deliberately does not disclose whether an account exists, and its uniform
   * message is what gets shown back.
   */
  async function sendPasswordReset(address: string) {
    if (!address || resetState === 'sending') return;
    setResetState('sending');
    setResetError('');
    try {
      await apiForgotPassword(address);
      setResetState('sent');
    } catch (error) {
      setResetState('failed');
      setResetError(error instanceof Error ? error.message : 'Could not send the email.');
    }
  }

  // Black-and-white mode is deliberately scoped to Light Mode. The preference
  // is kept while Dark Mode is active so returning to Light Mode restores the
  // user's choice without altering the dark palette.
  useLayoutEffect(() => {
    const shouldApplyMonochrome = theme === 'light' && monochrome;
    if (shouldApplyMonochrome) {
      document.documentElement.dataset.accessibility = 'monochrome';
    } else {
      delete document.documentElement.dataset.accessibility;
    }

    try {
      window.localStorage.setItem(MONOCHROME_STORAGE_KEY, String(monochrome));
    } catch {
      // Storage can be unavailable in privacy-restricted browser contexts.
    }
  }, [monochrome, theme]);

  // Kept as a no-arg close so the Profile button's handler reads the same as
  // before; there is no longer anything else to close.
  const closeOthers = useCallback((_except: string) => {}, []);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      const target = e.target as Node;
      if (profileRef.current && !profileRef.current.contains(target)) setProfileOpen(false);
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  // Escape closes the menu — it opens from a button, so the keyboard has to be
  // able to get back out of it.
  useEffect(() => {
    if (!profileOpen) return;
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setProfileOpen(false);
    }
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [profileOpen]);

  const displayName = auth.user?.fullName || userName;
  const email = auth.user?.email || '';
  // Same source the sidebar labels the account with; `account` is the server
  // record, so a real staff Position wins over the coarse RBAC role name.
  const roleLabel = auth.account?.position || auth.roles?.[0]?.name || '';
  const initials = initialsOf(displayName);
  const hasLearnerProfile = role === 'learner' && auth.account?.subjectType === 'learner' && Boolean(auth.account.subjectId);
  const headerLook = appearance ?? role;
  const usesLearnerHeader = headerLook === 'learner' || headerLook === 'coach' || headerLook === 'curriculum';

  return (
    <>
    {/* Every workspace shares the same frame as its icon rail. */}
    <header className={`kbc-workspace-topbar workspace-topbar relative mx-2 mb-2 mt-2 flex h-16 shrink-0 items-center gap-2 overflow-visible rounded-[20px] border-b border-foreground-100 bg-background-50 px-2 sm:px-3 md:gap-3 md:px-4 lg:ml-0 lg:mr-3 lg:mt-3 lg:gap-4 lg:px-5 [&_.kbc-workspace-switcher-button]:h-10 [&_.kbc-workspace-switcher-button]:gap-2.5 [&_.kbc-workspace-switcher-button]:px-3 [&_.kbc-workspace-switcher-button]:focus-visible:outline-none [&_.kbc-workspace-switcher-button]:focus-visible:ring-2 [&_.kbc-workspace-switcher-button]:focus-visible:ring-white/80 ${role === 'coach' ? 'z-40' : ''} ${usesLearnerHeader ? 'kbc-learner-header' : ''}`}>
      {usesLearnerHeader && (
        <div
          aria-hidden="true"
          className="kbc-learner-header-pattern pointer-events-none absolute inset-0"
          style={{ backgroundImage: `url(${LEARNER_HEADER_PATTERN_URL})` }}
        />
      )}
      {/* Labelled navigation controls, with the action matching the screen size. */}
      {onToggleMobileSidebar && (
        <div className="shrink-0 lg:hidden">
        <button
          type="button"
          ref={mobileMenuButtonRef}
          onClick={onToggleMobileSidebar}
          className={`kbc-navigation-menu ${usesLearnerHeader ? 'min-h-11 min-w-11 px-2' : ''}`}
          title={mobileSidebarOpen ? 'Close navigation menu' : 'Open navigation menu'}
          aria-label={mobileSidebarOpen ? 'Close navigation menu' : 'Open navigation menu'}
          aria-expanded={mobileSidebarOpen}
          aria-controls={`${role || 'workspace'}-mobile-navigation`}
        >
          <Menu size={18} aria-hidden="true" /><span>Menu</span>
        </button>
        </div>
      )}
      {/* Provider logo — below lg only. From lg up the sidebar carries the
          brand, and showing it twice was the duplication that read as clutter. */}
      <Link to="/" className={`${usesLearnerHeader ? 'hidden' : 'flex'} shrink-0 lg:hidden`} aria-label="Kent Business College home">
        <BrandLockup size="compact" className="max-w-16 sm:max-w-none" />
      </Link>

      {/* Where the page says what it is. These props were being passed by every
          page and thrown away, which is what left the bar looking empty. */}
      <div className={`${usesLearnerHeader ? 'flex' : 'hidden lg:flex'} min-w-0 flex-1`}>
        <div className="flex min-w-0 items-center gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white/10 text-white ring-1 ring-inset ring-white/10" aria-hidden="true">
            {pageIcon ?? <AppIcon className="ri-dashboard-line h-5 w-5" />}
          </span>
          <div className="min-w-0">
            <p className="kbc-topbar-title truncate font-heading text-[15px] font-bold leading-tight tracking-tight text-foreground-900">{pageTitle}</p>
            {pageSubtitle && (
              <p className={`${usesLearnerHeader ? 'hidden sm:block' : ''} kbc-topbar-subtitle mt-1 truncate text-[11.5px] leading-tight text-foreground-400`}>{pageSubtitle}</p>
            )}
          </div>
        </div>
      </div>

      {/* Below lg the title has no room, so the actions simply push right. */}
      <div className={`${usesLearnerHeader ? 'hidden' : 'flex-1'} lg:hidden`}></div>

      {/* The shared workspace controls stay visible on every desktop page so
          the dashboard and its child pages have the same navigation chrome. */}

      {/* Keep the workspace switcher available in the shared top bar so
          administrators can return to the workspace list from any page. */}
      {headerExtras && <div className="kbc-header-extras min-w-0 shrink-0">{headerExtras}</div>}
      {!personalLearning && <WorkspaceSwitcher />}

      {/* Profile and its existing account actions. */}
      <div className="flex shrink-0 items-center gap-0.5 lg:border-l lg:border-white/15 lg:pl-4">
        <div className="relative" ref={profileRef}>
          <button
            ref={accountButtonRef}
            onClick={() => { closeOthers('profile'); setProfileOpen(!profileOpen); }}
            aria-haspopup="menu"
            aria-expanded={profileOpen}
            aria-label="Account menu"
            className={`kbc-topbar-profile flex min-h-10 cursor-pointer items-center gap-2.5 rounded-xl p-1 ring-1 transition-smooth focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/80 sm:pr-2 ${
              profileOpen
                ? 'bg-primary-50 ring-primary-200'
                : 'ring-transparent hover:bg-primary-50/60 hover:ring-primary-100'
            }`}
          >
            <AccountAvatar initials={initials} className="h-8 w-8 shadow-sm shadow-primary-900/25" />
            <span className="kbc-topbar-user-name hidden max-w-[8rem] truncate text-xs font-semibold text-white xl:inline">{role === 'learner' ? displayName : workspaceLabel || roleLabel || displayName}</span>
            <AppIcon
              className={`ri-arrow-down-s-line hidden text-xs text-foreground-400 transition-transform duration-200 sm:inline ${profileOpen ? 'rotate-180' : ''}`}
            ></AppIcon>
          </button>
          {profileOpen && (
            <div
              role="menu"
              className="account-menu-pop absolute right-0 top-full z-50 mt-2 w-[16.5rem] overflow-hidden rounded-2xl bg-background-50 ring-1 ring-foreground-100 shadow-[0_20px_44px_-20px_oklch(var(--foreground-950)/0.32)]"
            >
              {/* Identity block. The tinted panel gives the name and email a
                  surface of their own instead of floating on bare white. */}
              <div className="flex items-center gap-3 bg-gradient-to-br from-primary-50 to-background-50 px-4 py-3.5">
                <AccountAvatar initials={initials} className="h-10 w-10" textClassName="text-[0.8125rem]" />
                <div className="min-w-0">
                  <p className="truncate text-[0.875rem] font-semibold leading-tight text-foreground-900">{displayName}</p>
                  {email && <p className="truncate text-[0.75rem] leading-tight text-foreground-400">{email}</p>}
                  {roleLabel && (
                    <span className="mt-1.5 inline-flex items-center rounded-full bg-primary-100/70 px-2 py-0.5 text-[0.6875rem] font-semibold uppercase tracking-wide text-primary-700">
                      {roleLabel}
                    </span>
                  )}
                </div>
              </div>

              {/* Appearance is kept next to the existing account action so the
                  preference is available anywhere the profile menu is available. */}
              <div className="border-t border-background-200/70 p-1.5">
                {role === 'learner' && <PreviousRecordMenuItem onNavigate={() => setProfileOpen(false)} />}
                {hasLearnerProfile && (
                  <Link
                    role="menuitem"
                    to="/learner/profile"
                    onClick={() => setProfileOpen(false)}
                    className="account-theme-row group flex w-full items-center gap-3 rounded-xl px-2.5 py-2 text-left transition-smooth hover:bg-background-100 focus:outline-none focus-visible:bg-background-100"
                  >
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-background-100 text-foreground-500 transition-smooth group-hover:bg-background-200 group-hover:text-foreground-700" aria-hidden="true">
                      <AppIcon className="ri-user-line text-base" />
                    </span>
                    <span className="flex-1 text-[0.8125rem] font-semibold text-foreground-700 transition-smooth group-hover:text-foreground-900">My Profile</span>
                    <AppIcon className="ri-arrow-right-s-line text-sm text-foreground-200" />
                  </Link>
                )}
                <button
                  type="button"
                  role="menuitemcheckbox"
                  aria-label="Toggle dark mode"
                  aria-checked={theme === 'dark'}
                  onClick={toggleTheme}
                  className="account-theme-row group flex w-full cursor-pointer items-center gap-3 rounded-xl px-2.5 py-2 text-left transition-smooth hover:bg-background-100 focus:outline-none focus-visible:bg-background-100"
                >
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-background-100 text-foreground-500 transition-smooth group-hover:bg-background-200 group-hover:text-foreground-700" aria-hidden="true">
                    {theme === 'dark' ? <Sun size={15} strokeWidth={2.2} /> : <Moon size={15} strokeWidth={2.2} />}
                  </span>
                  <span className="flex-1 text-[0.8125rem] font-semibold text-foreground-700 transition-smooth group-hover:text-foreground-900">Dark mode</span>
                  <span className={`account-theme-switch relative h-5 w-9 shrink-0 rounded-full p-0.5 transition-colors duration-200 ${theme === 'dark' ? 'bg-primary-500' : 'bg-background-300'}`} aria-hidden="true">
                    <span className={`block h-4 w-4 rounded-full bg-background-50 shadow-sm transition-transform duration-200 ${theme === 'dark' ? 'translate-x-4' : 'translate-x-0'}`}></span>
                  </span>
                </button>

                <button
                  type="button"
                  role="menuitem"
                  aria-expanded={accessibilityOpen}
                  onClick={() => setAccessibilityOpen((open) => !open)}
                  className="account-theme-row group mt-1 flex w-full cursor-pointer items-center gap-3 rounded-xl px-2.5 py-2 text-left transition-smooth hover:bg-background-100 focus:outline-none focus-visible:bg-background-100"
                >
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white text-black transition-smooth group-hover:bg-background-50" aria-hidden="true">
                    <AccessibilityBadge className="h-5 w-5" />
                  </span>
                  <span className="flex-1 text-[0.8125rem] font-semibold text-foreground-700 transition-smooth group-hover:text-foreground-900">Accessibility</span>
                  <AppIcon className={`ri-arrow-down-s-line text-xs text-foreground-400 transition-transform duration-200 ${accessibilityOpen ? 'rotate-180' : ''}`} aria-hidden="true"></AppIcon>
                </button>

                {accessibilityOpen && (
                  <div className="mx-1 mb-1 rounded-xl bg-background-100/70 p-2 ring-1 ring-background-200/70" role="group" aria-label="Accessibility settings">
                    <div className="flex items-center gap-2.5">
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-background-50 text-foreground-500" aria-hidden="true">
                        <AppIcon className="ri-contrast-2-line text-base"></AppIcon>
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="text-[0.75rem] font-semibold text-foreground-800">Black and white</p>
                        <p className="text-[0.6875rem] leading-snug text-foreground-400">
                          {theme === 'dark' ? 'Available in Light Mode only' : 'Remove colour from the interface'}
                        </p>
                      </div>
                      <button
                        type="button"
                        role="switch"
                        aria-label="Toggle black and white mode"
                        aria-checked={theme === 'light' && monochrome}
                        disabled={theme === 'dark'}
                        title={theme === 'dark' ? 'Switch to Light Mode to use black and white mode' : 'Toggle black and white mode'}
                        onClick={() => setMonochrome((enabled) => !enabled)}
                        className={`relative h-5 w-9 shrink-0 rounded-full p-0.5 transition-colors duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 focus-visible:ring-offset-1 ${
                          theme === 'dark'
                            ? 'cursor-not-allowed bg-background-200 opacity-50'
                            : monochrome
                              ? 'bg-foreground-900'
                              : 'bg-background-300'
                        }`}
                      >
                        <span className={`block h-4 w-4 rounded-full bg-background-50 shadow-sm transition-transform duration-200 ${theme === 'light' && monochrome ? 'translate-x-4' : 'translate-x-0'}`}></span>
                      </button>
                    </div>
                  </div>
                )}

                {/* Reset by email rather than in place: the person may not
                    remember the current password, and the emailed link is the
                    one flow that proves they still control the address. */}
                {email && (
                  <button
                    type="button"
                    role="menuitem"
                    disabled={resetState === 'sending' || resetState === 'sent'}
                    onClick={() => void sendPasswordReset(email)}
                    className="group flex w-full cursor-pointer items-center gap-3 rounded-xl px-2.5 py-2 text-left transition-smooth hover:bg-background-100/70 focus:outline-none focus-visible:bg-background-100/70 disabled:cursor-default"
                  >
                    <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-smooth ${
                      resetState === 'sent'
                        ? 'bg-emerald-100 text-emerald-600'
                        : 'bg-background-100 text-foreground-400 group-hover:bg-primary-100 group-hover:text-primary-600'
                    }`}>
                      <AppIcon className={`text-base ${
                        resetState === 'sending' ? 'ri-loader-4-line animate-spin'
                          : resetState === 'sent' ? 'ri-mail-check-line'
                          : 'ri-lock-password-line'
                      }`}></AppIcon>
                    </span>
                    <span className="flex-1 min-w-0">
                      <span className="block text-[0.8125rem] font-semibold text-foreground-700 transition-smooth group-hover:text-foreground-900">
                        {resetState === 'sending' ? 'Sending…'
                          : resetState === 'sent' ? 'Reset email sent'
                          : 'Reset password'}
                      </span>
                      {/* The address is named: somebody with two accounts needs
                          to know which inbox to open. */}
                      <span className="block truncate text-[0.6875rem] text-foreground-400">
                        {resetState === 'sent'
                          ? `Check ${email} for the link`
                          : resetState === 'failed'
                            ? resetError
                            : `We will email a link to ${email}`}
                      </span>
                    </span>
                  </button>
                )}

                <button
                  type="button"
                  role="menuitem"
                  onClick={() => { setProfileOpen(false); setSignOutOpen(true); }}
                  className="group mt-1 flex w-full cursor-pointer items-center gap-3 rounded-xl border-t border-background-200/70 px-2.5 pb-2 pt-2 text-left transition-smooth hover:bg-red-50 focus:outline-none focus-visible:bg-red-50"
                >
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-background-100 text-foreground-400 transition-smooth group-hover:bg-red-100 group-hover:text-red-600">
                    <AppIcon className="ri-logout-box-line text-base"></AppIcon>
                  </span>
                  <span className="flex-1 text-[0.8125rem] font-semibold text-foreground-700 transition-smooth group-hover:text-red-600">Sign out</span>
                  <AppIcon className="ri-arrow-right-s-line text-sm text-foreground-200 transition-smooth group-hover:text-red-400"></AppIcon>
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </header>

    {role && role !== 'coach' && role !== 'learner' && !sidebarSignOut && createPortal(
      <button
        type="button"
        onClick={() => { setProfileOpen(false); setSignOutOpen(true); }}
        aria-label="Sign out"
        title="Sign out"
        className="fixed bottom-8 left-8 z-50 hidden h-12 w-12 items-center justify-center rounded-xl bg-white/10 text-white/80 transition-colors hover:bg-white/20 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/80 lg:flex"
      >
        <LogOut className="h-5 w-5" aria-hidden="true" />
      </button>,
      document.body,
    )}

    {/* Sign Out Confirmation Modal — portalled to the body so no ancestor of the
        header can clip it or outrank it in the stacking order. */}
    {signOutOpen && createPortal(
      <SignOutConfirmModal
        displayName={displayName}
        email={email || 'Signed in'}
        onClose={() => setSignOutOpen(false)}
        onConfirm={() => { setSignOutOpen(false); logout(); }}
      />,
      document.body,
    )}
    </>
  );
}

// Reusable dropdown panel wrapper
