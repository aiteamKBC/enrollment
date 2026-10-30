// ============================================================================
// The workspaces the platform offers as a launcher.
//
// One definition, two consumers:
//   * components/feature/WorkspaceSwitcher — the top-bar switcher an
//                             administrator uses to change section. Replaced a
//                             five-card panel on the Super Admin dashboard,
//                             which could only be reached from the dashboard —
//                             so changing section meant going home first.
//   * pages/home            — the public "Explore your portal" launcher, which
//                             enters each one as its demo account (previewAs).
//
// The two consumers do NOT show the same rows. Super Admin is in the switcher,
// because an administrator moving to it is ordinary navigation, but it has no
// `demoEmail` and so never appears on the public launcher: that launcher signs
// anyone in as the named account with no password, and the one account that
// must not be handed out that way is the one that administers the platform.
// A null demoEmail is the whole rule — see the filter in pages/home.
//
// Kept here rather than in either place so the two cannot drift, and every
// `path` is a real route in router/config.tsx — a launcher offering a section
// that leads nowhere is worse than one that omits it.
//
// Curated, not exhaustive. The sidebar's roleNavMap knows every role the app
// has; this is the set of sections worth switching between. A switcher listing
// everything routable is a directory, not a shortcut.
//
// This list also replaced `components/feature/RoleSwitcher.tsx`, a dead
// component carrying its own copy of the same mapping. A real administrator
// already has access to every section, so moving between them is navigation,
// not a change of role — the switcher never touches RBAC state.
// ============================================================================

export interface PortalWorkspace {
  slug: string;
  label: string;
  icon: string;
  /** Where this workspace lives. A real route. */
  path: string;
  /**
   * Demo account the public launcher signs in as, or null for a workspace the
   * launcher must not offer. Unused by the switcher, which never signs anybody
   * in — it navigates an administrator who is already authenticated.
   */
  demoEmail: string | null;
  /** One line of context, shown in the switcher and on the launcher. */
  blurb: string;
}

export const PORTAL_WORKSPACES: PortalWorkspace[] = [
  {
    // First because it is the hub the others hang off — and because this is the
    // dashboard the switcher itself was lifted out of, which until now was the
    // one section the switcher could not take you back to.
    slug: 'admin',
    label: 'Super Admin',
    icon: 'ri-shield-user-line',
    path: '/workspace/admin',
    demoEmail: null,
    blurb: 'Accounts, access control and platform health',
  },
  {
    slug: 'coach',
    label: 'Coach',
    icon: 'ri-user-heart-line',
    path: '/workspace/coach',
    demoEmail: 'coach@kbc.test',
    blurb: 'Caseload, reviews and evidence validation',
  },
  {
    // The enrolment console is the user directory — its whole sidebar is that
    // one item, so /users is the workspace.
    slug: 'enrolment',
    label: 'Enrolment',
    icon: 'ri-user-add-line',
    path: '/users',
    demoEmail: 'compliance@kbc.test',
    blurb: 'Learner records, enrolment and compliance',
  },
  {
    slug: 'engagement',
    label: 'Engagement',
    icon: 'ri-megaphone-line',
    path: '/workspace/engagement',
    demoEmail: 'compliance@kbc.test',
    blurb: 'Attendance, absence and communications',
  },
  {
    // Tutors deliver the teaching; coaches carry the caseload. Two grants
    // (ACCESS_TUTOR / ACCESS_COACH), two workspaces, listed next to each other.
    slug: 'tutor',
    label: 'Tutor',
    icon: 'ri-presentation-line',
    path: '/workspace/tutor',
    demoEmail: 'tutor@kbc.test',
    blurb: 'Teaching sessions, marking and evidence review',
  },
  {
    slug: 'curriculum',
    label: 'Curriculum',
    icon: 'ri-book-open-line',
    path: '/workspace/curriculum',
    demoEmail: 'tutor@kbc.test',
    blurb: 'Programmes, modules and their content',
  },
  {
    slug: 'support',
    label: 'Support',
    icon: 'ri-customer-service-2-line',
    path: '/support/ticket-queue',
    demoEmail: null,
    blurb: 'Learner tickets and support replies',
  },
  {
    // Every employer contact as a card, each opening their existing side page
    // (/employers/:id). Under /workspace/admin so only a super-admin reaches it,
    // and no demoEmail: the list is the whole employer directory, not a demo
    // account's own view. Before AUDIT so AUDIT stays the last row.
    slug: 'employer',
    label: 'Employer',
    icon: 'ri-briefcase-line',
    path: '/workspace/admin/employers',
    demoEmail: null,
    blurb: 'Employer contacts, their learners and documents',
  },
  {
    slug: 'audit',
    label: 'AUDIT',
    icon: 'ri-file-search-line',
    path: '/workspace/auditor-copy',
    demoEmail: 'auditor@kbc.test',
    blurb: 'Evidence audit and the activity ledger',
  },
  {
    // Last, and only offered to somebody who actually has a learner record --
    // staff and administrators can also study a programme, and when they do
    // they keep the one account they already have rather than a second one
    // (see login/learner_enrolment.py). `learnerWorkspaces` below is what
    // filters this out for everyone else.
    slug: 'learner',
    label: 'Learner',
    icon: 'ri-graduation-cap-line',
    path: '/workspace/learner',
    demoEmail: null,
    blurb: 'Your own modules, progress and off-the-job hours',
  },
];

/** The workspaces to offer this account.
 *
 * Everything in the list except Learner, which is shown only when the person
 * has an enrolment record of their own. Without the gate every administrator
 * would be offered a learner workspace that has nothing behind it. */
export function workspacesFor(
  account: { learnerRecordId?: number | null; learnerRecordKind?: string | null } | null,
): PortalWorkspace[] {
  const recordId = account?.learnerRecordId;
  if (!recordId) return PORTAL_WORKSPACES.filter(w => w.slug !== 'learner');
  // Addressed explicitly, never left to the bare /workspace/learner route.
  // That route resolves the learner from the SESSION, and this person signs in
  // as staff — so with no params it falls through to a remembered or demo
  // learner and would open somebody else's record.
  const kind = account?.learnerRecordKind || 'commercial';
  return PORTAL_WORKSPACES.map(w => (
    w.slug === 'learner' ? { ...w, path: `/workspace/learner/${kind}/${recordId}` } : w
  ));
}

/**
 * Which workspace a path belongs to, so the switcher can show where you are.
 *
 * Longest match wins, which is the whole reason this is a function rather than a
 * `find`: some paths are prefixes of others. `/workspace/auditor-copy` starts
 * with `/workspace/auditor`, and every `/workspace/learner/:kind/:id` starts
 * with `/workspace/learner` — taking the first prefix hit would light up the
 * wrong row, or the right one for the wrong reason.
 *
 * The segment boundary matters too: `/users-report` is not inside `/users`.
 *
 * Returns null anywhere outside these sections — Leadership, an admin settings
 * page such as /admin/roles — which the switcher renders as a neutral
 * "Workspaces" label rather than claiming you are somewhere you are not. Note
 * that /admin/* is not inside /workspace/admin: those pages are reached from the
 * Super Admin sidebar but are not the workspace's own route.
 */
export function activeWorkspace(pathname: string): PortalWorkspace | null {
  let best: PortalWorkspace | null = null;
  for (const workspace of PORTAL_WORKSPACES) {
    const isMatch = pathname === workspace.path || pathname.startsWith(`${workspace.path}/`);
    if (!isMatch) continue;
    if (!best || workspace.path.length > best.path.length) best = workspace;
  }
  return best;
}
