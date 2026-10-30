import { useState, type ReactNode } from 'react';
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
  const learner = auth.account?.role === 'learner' || Boolean(auth.account && hasOwnEnrolment(auth.account) && pathname.startsWith('/learner'));
  const employer = auth.account?.role === 'employer';
  const commercial = (auth.account?.role === 'learner' ? auth.account.learnerType : auth.account?.learnerRecordKind) === 'commercial';
  const links = learner ? [
    ...(!commercial ? [['/learner/onboarding', 'My enrolment'], ['/learner/onboarding/reviews', 'Reviews'], ['/learner/compliance-documents', 'Compliance documents']] : []),
    ['/learner/first-session', 'First learning session'], ['/lms?self=1', 'Continue to LMS'],
  ] : employer ? [[`/employers/${auth.account?.subjectId}/documents`, 'Documents to sign']] : [
    ['/users', 'Learners & contacts'], ['/users/coach-directory', 'Coach directory'], ['/users/first-sessions', 'First-session bookings'],
    ['/users/wizard-builder', 'Enrolment form builder'], ['/users/eligibility-builder', 'Eligibility form builder'], ['/lms', 'Open LMS'],
    ...(auth.account && hasOwnEnrolment(auth.account) ? [['/learner/home?self=1', 'My enrolment']] : []),
  ];
  return <div className="min-h-screen bg-background-200 text-foreground-900">
    <header className="flex items-center justify-between gap-4 border-b border-foreground-200 bg-background-50 px-5 py-4">
      <Link to="/enrollment" className="font-heading text-lg font-semibold text-primary-700">Enrolment Corner</Link>
      <div className="flex items-center gap-4"><span className="hidden text-sm sm:inline">{auth.account?.displayName}</span>
        <button type="button" className="rounded-lg border px-3 py-2 text-sm" onClick={logout}>Sign out</button>
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
