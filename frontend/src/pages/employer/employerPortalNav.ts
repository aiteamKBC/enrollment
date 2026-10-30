import type { SidebarNavItem } from '@/components/feature/Sidebar';
import { useAuth } from "@/hooks/useAuth.helpers";
import { useLocation } from 'react-router-dom';

/**
 * One destination per menu item, so the sidebar's highlight and the content
 * agree. `My learners` and `Progress reviews` were query strings on the home
 * route that only scrolled the single dashboard, which left all three items
 * rendering the same screen; they are now their own paths and own views.
 */
export function employerPortalNav(employerId: string): SidebarNavItem[] {
  return [
    { id: 'employer-portal-home', label: 'Home', icon: 'ri-home-line', href: `/employers/${employerId}` },
    { id: 'employer-portal-learners', label: 'My learners', icon: 'ri-group-line', href: `/employers/${employerId}/learners` },
    { id: 'employer-portal-reviews', label: 'Progress reviews', icon: 'ri-calendar-check-line', href: `/employers/${employerId}/reviews` },
    { id: 'employer-portal-documents', label: 'Documents', icon: 'ri-draft-line', href: `/employers/${employerId}/documents` },
  ];
}

/**
 * WorkspaceShell props for the employer pages.
 *
 * Two audiences reach them: the employer after signing in, and staff opening
 * an employer from the Users directory. Both get the employer menu and the
 * learner workspace's look; only the name in the header follows the session.
 */
export function useEmployerPortalChrome(employerId: string, employerName?: string) {
  const { auth } = useAuth();
  const { pathname, search, state } = useLocation();
  const root = `/employers/${employerId}`;
  const path = pathname.replace(/\/+$/, '');
  const learnerTab = new URLSearchParams(search).get('tab');
  const employerReturnTo = (state as { employerReturnTo?: unknown } | null)?.employerReturnTo;
  const returnState = typeof employerReturnTo === 'string' ? { employerReturnTo } : undefined;
  // A learner's tabs share one pathname. Keep the global menu destinations
  // fixed while selecting the section represented by the current tab.
  const section = path.startsWith(`${root}/learner/`)
    ? learnerTab === 'reviews' || learnerTab === 'documents' ? learnerTab : 'learners'
    : path === `${root}/reviews` ? 'reviews'
    : path === `${root}/documents` ? 'documents'
    : path === `${root}/learners` ? 'learners' : 'home';
  const isEmployerViewer = auth.account?.role === 'employer';
  return {
    isEmployerViewer,
    shell: {
      role: isEmployerViewer ? 'employer' : 'compliance',
      appearance: 'learner',
      roleLabel: 'Employer',
      workspaceLabel: 'Employer Workspace',
      navItems: employerPortalNav(employerId).map(item => ({ ...item, active: item.id === `employer-portal-${section}`, state: returnState })),
      userName: isEmployerViewer ? auth.account?.displayName || employerName || 'Employer' : 'Enrolment Officer',
      userRole: isEmployerViewer ? 'Employer' : 'Enrolment Officer',
    },
  };
}
