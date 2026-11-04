import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link, NavLink, useLocation } from 'react-router-dom';
import { useAuth } from '@/hooks/useAuth.helpers';
import type { SidebarNavItem } from './Sidebar';
import { AppIcon } from './AppIcon';
import { hasOwnEnrolment } from '@/lib/enrollmentAccess';

interface Props {
  children: ReactNode;
  pageTitle: string;
  pageSubtitle?: string;
  headerExtras?: ReactNode;
  role: string;
  roleLabel: string;
  navItems: SidebarNavItem[];
  [key: string]: unknown;
}

export function WorkspaceShell({ children, pageTitle, pageSubtitle, headerExtras }: Props) {
  const { auth, logout } = useAuth();
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const accountRef = useRef<HTMLDivElement>(null);
  const learner = auth.account?.role === 'learner' || Boolean(auth.account && hasOwnEnrolment(auth.account) && pathname.startsWith('/learner'));
  const employer = auth.account?.role === 'employer';
  const commercial = (auth.account?.role === 'learner' ? auth.account.learnerType : auth.account?.learnerRecordKind) === 'commercial';
  const links = learner ? [
    ...(!commercial ? [['/learner/onboarding', 'My enrolment'], ['/learner/onboarding/reviews', 'Reviews'], ['/learner/compliance-documents', 'Compliance documents']] : []),
    ['/learner/first-session', 'First learning session'], ['/lms?self=1', 'Continue to LMS'],
  ] : employer ? [[`/employers/${auth.account?.subjectId}/documents`, 'Documents to sign']] : [
    ['/users', 'Learners & contacts'], ['/users/coach-directory', 'Coach directory'], ['/users/first-sessions', 'First-session bookings'],
    ['/users/eligibility-applications', 'Eligibility applications'], ['/users/eligibility-builder', 'Eligibility form builder'], ['/users/wizard-builder', 'Enrolment form builder'], ['/lms', 'Open LMS'],
    ...(auth.account && hasOwnEnrolment(auth.account) ? [['/learner/home?self=1', 'My enrolment']] : []),
  ];
  const displayName = auth.account?.displayName?.trim() || 'Account';
  const nameParts = displayName.split(/\s+/).filter(Boolean);
  const initials = nameParts.length > 1
    ? `${nameParts[0][0]}${nameParts[nameParts.length - 1][0]}`.toUpperCase()
    : displayName.slice(0, 2).toUpperCase();
  useEffect(() => {
    if (!accountOpen) return undefined;
    const close = (event: MouseEvent) => {
      if (!accountRef.current?.contains(event.target as Node)) setAccountOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setAccountOpen(false);
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', escape);
    };
  }, [accountOpen]);
  return <div className="min-h-screen bg-background-200 text-foreground-900">
    <header className="flex items-center justify-between gap-4 border-b border-foreground-200 bg-background-50 px-5 py-4">
      <Link to="/enrollment" className="font-heading text-lg font-semibold text-primary-700">Enrolment Corner</Link>
      <div className="flex items-center gap-3"><div ref={accountRef} className="relative"><button type="button" aria-label={`Open account menu for ${displayName}`} aria-haspopup="menu" aria-expanded={accountOpen} title="Account menu" onClick={() => setAccountOpen(value => !value)} className="flex h-9 w-9 items-center justify-center rounded-full bg-primary-100 text-xs font-bold text-primary-800 ring-1 ring-primary-200 hover:bg-primary-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500">{initials}</button>{accountOpen && <div role="menu" className="absolute right-0 top-full z-50 mt-2 w-64 overflow-hidden rounded-xl bg-background-50 p-1.5 ring-1 ring-foreground-100 shadow-xl"><div className="px-3 py-2"><p className="truncate text-sm font-semibold text-foreground-900">{displayName}</p><p className="truncate text-xs text-foreground-400">{auth.account?.email}</p></div><div className="border-t border-background-200 pt-1.5"><Link role="menuitem" to={learner ? '/learner/account-settings' : '/profile'} onClick={() => setAccountOpen(false)} className="flex min-h-10 items-center rounded-lg px-3 text-sm font-medium text-foreground-700 hover:bg-background-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400">Account Settings</Link><button type="button" role="menuitem" className="flex min-h-10 w-full items-center rounded-lg px-3 text-left text-sm font-medium text-red-700 hover:bg-red-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400" onClick={() => { setAccountOpen(false); logout(); }}>Sign Out</button></div></div>}</div><span className="hidden text-sm sm:inline">{auth.account?.displayName}</span>
        <button type="button" className="md:hidden" aria-label="Toggle navigation" aria-expanded={open} onClick={() => setOpen(value => !value)}><AppIcon className="ri-menu-line" /></button>
      </div>
    </header>
    <div className="md:flex">
      <nav aria-label="Enrolment navigation" className={`${open ? 'block' : 'hidden'} shrink-0 border-r border-foreground-200 bg-background-50 p-4 md:block md:w-60`}>
        <ul className="space-y-1">{links.map(([to, label]) => <li key={to}><NavLink to={to} end className={({ isActive }) => `block rounded-lg px-4 py-3 text-sm ${isActive ? 'bg-primary-100 font-semibold text-primary-800' : 'hover:bg-background-200'}`}>{label}</NavLink></li>)}</ul>
      </nav>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center justify-between gap-4 px-6 pt-6"><div><h1 className="font-heading text-2xl font-semibold">{pageTitle}</h1>{pageSubtitle && <p className="mt-1 text-sm text-foreground-500">{pageSubtitle}</p>}</div>{headerExtras}</div>
        {children}
      </div>
    </div>
  </div>;
}
