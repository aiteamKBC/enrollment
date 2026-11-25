import { Bookmark, CalendarCheck, CalendarDays, ChartNoAxesColumnIncreasing, ChevronRight, FilePenLine, Files, FileUser, Funnel, GraduationCap, Home, Layers3, UserRound, UsersRound } from 'lucide-react';

/** Users-only presentation. These components have no application state or actions. */
export function UsersPortalFilterIcon() {
  return <Funnel size={18} strokeWidth={1.7} aria-hidden="true" />;
}

export function UsersPortalBrand() {
  return <><span className="up-college-lockup"><img src="/images/Kent%20logo.png" alt="Kent Business College" /></span><span className="up-brand-divider" aria-hidden="true" /></>;
}

export function UsersPortalNavIcon({ path }: { path: string }) {
  const icons = {
    '/users': UsersRound, '/users/eligibility-applications': FileUser,
    '/users/eligibility-builder': FilePenLine, '/users/wizard-builder': CalendarCheck,
    '/users/programme-structure': Layers3, '/users/compliance-documents': Files,
    '/admin/suitability-rpl-report': ChartNoAxesColumnIncreasing,
    '/users/coach-directory': UsersRound, '/users/first-sessions': CalendarDays,
    '/lms': GraduationCap, '/learner/home': Bookmark,
  };
  const Icon = icons[path.split('?')[0] as keyof typeof icons] || UserRound;
  return <Icon size={20} strokeWidth={1.6} aria-hidden="true" />;
}

export function UsersPortalSidebarBrand() {
  return <div className="up-sidebar-brand"><strong>Enrolment Corner</strong><small>Enrolment Operations</small><span className="up-sidebar-brand-accent" aria-hidden="true" /></div>;
}

export function UsersPortalArtwork() {
  return <div className="up-sidebar-art" aria-hidden="true">
    <img src="/images/dashboard-sidebar-building.png" alt="" />
    <div className="up-sidebar-slogan"><span>A Brighter</span><span>Future Together</span></div>
  </div>;
}

export function UsersPortalBreadcrumb() {
  return <div className="up-breadcrumb" aria-label="Breadcrumb"><Home size={13} aria-hidden="true" /><span>Users</span><ChevronRight size={12} aria-hidden="true" /><strong>User Management</strong></div>;
}

export function UsersPortalHeroShape() {
  return <svg className="up-hero-shape" viewBox="0 0 1400 116" preserveAspectRatio="none" aria-hidden="true" focusable="false">
    <defs>
      <linearGradient id="up-purple" x1="0" y1="0" x2="1" y2="0">
        <stop stopColor="#461a5b" />
        <stop offset=".38" stopColor="#57206e" />
        <stop offset=".46" stopColor="#622c7d" stopOpacity=".9" />
        <stop offset=".55" stopColor="#74428c" stopOpacity=".5" />
        <stop offset=".63" stopColor="#9366a9" stopOpacity=".1" />
        <stop offset=".72" stopColor="#9366a9" stopOpacity="0" />
      </linearGradient>
    </defs>
    <rect width="1400" height="116" rx="17" fill="url(#up-purple)" />
  </svg>;
}
