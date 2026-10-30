import { useEffect } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import type { SidebarNavItem } from '@/components/feature/Sidebar';

/** Programme status that means "the learner is still filling in their enrolment". */
export const ONBOARDING_STATUS = 'Onboarding';

/** Where an onboarding learner is sent. */
export const ONBOARDING_ROUTE = '/learner/onboarding';

/**
 * The only nav an onboarding learner gets. Every other learner page (training
 * plan, evidence, attendance, clubs…) depends on an enrolled learner with a
 * training plan, so showing them mid-enrolment offers links that lead nowhere.
 */
/** Where an onboarding learner books their three enrolment reviews. */
export const ONBOARDING_REVIEWS_ROUTE = '/learner/onboarding/reviews';

export const ONBOARDING_NAV_ITEMS: SidebarNavItem[] = [
  { id: 'learner-onboarding', label: 'My Enrolment', icon: 'ri-file-user-line', href: ONBOARDING_ROUTE },
  { id: 'learner-onboarding-reviews', label: 'Reviews', icon: 'ri-calendar-check-line', href: ONBOARDING_REVIEWS_ROUTE },
];

/** Why Reviews is locked: the reviews follow a handed-in enrolment. */
export const REVIEWS_LOCKED_REASON = 'Available once you submit your enrolment';

/**
 * Whether the learner has handed in their enrolment wizard — 'Submitted' when
 * they press Submit enrolment, 'Completed' once staff sign it off.
 */
export function isEnrolmentSubmitted(onboardingStatus?: string | null): boolean {
  const status = (onboardingStatus || '').trim().toLowerCase();
  return status === 'submitted' || status === 'completed';
}

/** The onboarding menu, with Reviews locked until the enrolment is submitted. */
export function onboardingNavItems(enrolmentSubmitted: boolean): SidebarNavItem[] {
  if (enrolmentSubmitted) return ONBOARDING_NAV_ITEMS;
  return ONBOARDING_NAV_ITEMS.map((item) =>
    item.href === ONBOARDING_REVIEWS_ROUTE ? { ...item, locked: true, lockedReason: REVIEWS_LOCKED_REASON } : item
  );
}

/**
 * The learner has an account but nobody has started their enrolment yet — the
 * backend's DEFAULT_PROGRAMME_STATUS (learner_api/constants.py).
 *
 * Earlier than 'Onboarding': there is not even a wizard for them to fill in
 * until the enrolment team picks them up, so every learner page — including
 * their own enrolment — has nothing behind it.
 */
export const FRESH_STATUS = 'Fresh user';

/** Where a fresh learner is sent: the overview, which explains the wait. */
export const FRESH_ROUTE = '/workspace/learner/dashboard';

/**
 * A fresh learner's entire menu. Just the overview, because that is the only
 * page with anything to say to them.
 */
export const FRESH_NAV_IDS = ['learner-home', 'learner-overview'];

/** Onboarding is signed off but delivery hasn't started — see the backend's
 *  promote_to_delivery_if_ready. */
export const DELIVERY_STATUS = 'Delivery';

/**
 * A learner at Delivery has finished enrolment but isn't being taught yet: no
 * training plan is running, so no evidence, attendance or progress exists to
 * show. They keep their overview, their submitted enrolment, and the compliance
 * paperwork — the Apprenticeship Agreement is waiting for their signature.
 */
export const DELIVERY_NAV_IDS = [
  'learner-home',
  'learner-overview',
  'learner-onboarding',
  'learner-compliance-documents',
];

/** Where a Delivery apprentice books their first learning session. */
export const FIRST_SESSION_ROUTE = '/learner/first-session';

/** Why it is locked: the session follows the learner's own document signatures. */
export const FIRST_SESSION_LOCKED_REASON = 'Available once you sign all your compliance documents';

/**
 * A Delivery apprentice's First Learning Session tab. Only this stage has it:
 * before Delivery there are no documents to sign, and from Ready to enrol the
 * full-page first-session gate takes over.
 */
export function firstSessionNavItem(unlocked: boolean): SidebarNavItem {
  return {
    id: 'learner-first-session',
    label: 'First Learning Session',
    icon: 'ri-calendar-event-line',
    href: FIRST_SESSION_ROUTE,
    ...(unlocked ? {} : { locked: true, lockedReason: FIRST_SESSION_LOCKED_REASON }),
  };
}

/** Commercial learners have no apprenticeship documents or onboarding reviews. */
function withoutCommercialCompliance(items: SidebarNavItem[]): SidebarNavItem[] {
  const commercialHiddenIds = new Set([
    'learner-onboarding',
    'learner-compliance-documents',
    'learner-group-readiness',
    'learner-group-community',
    'learner-group-help',
  ]);
  return items.flatMap((item) => {
    const searchable = `${item.id} ${item.label}`.toLowerCase();
    if (commercialHiddenIds.has(item.id) || /document|compliance/.test(searchable)) return [];
    if (!item.children?.length) return [item];
    const children = withoutCommercialCompliance(item.children);
    return children.length ? [{ ...item, children }] : [];
  });
}

/** Pages that apply to this learner type, independent of programme stage. */
export function navItemsForLearnerKind(items: SidebarNavItem[], learnerKind?: string): SidebarNavItem[] {
  return learnerKind?.toLowerCase() === 'commercial' ? withoutCommercialCompliance(items) : items;
}

/**
 * Whether the learner is waiting for enrolment to begin.
 *
 * Matches the literal status only. The backend treats an *unset* status as
 * 'Fresh user' for display, but this deliberately does not: `useLearnerNavGate`
 * falls back to `''` when the status lookup fails, so counting empty as fresh
 * would turn one dropped request into "your enrolment hasn't started" for a
 * learner midway through their programme. Locking someone out of their own
 * workspace is the worse error, so an unknown status gets the full workspace.
 */
export function isFreshStatus(programmeStatus?: string | null): boolean {
  return (programmeStatus || '').trim().toLowerCase() === FRESH_STATUS.toLowerCase();
}

export function isOnboardingStatus(programmeStatus?: string | null): boolean {
  return (programmeStatus || '').trim().toLowerCase() === ONBOARDING_STATUS.toLowerCase();
}

export function isDeliveryStatus(programmeStatus?: string | null): boolean {
  return (programmeStatus || '').trim().toLowerCase() === DELIVERY_STATUS.toLowerCase();
}

/** Every enrolment document is signed; the programme has not started yet. */
export const READY_TO_ENROL_STATUS = 'Ready to enrol';

/**
 * Where an apprentice who is not yet Active goes instead of Student Home, or
 * null when Student Home is theirs.
 *
 * Student Home is the entry to a running programme — continue learning,
 * monthly submissions, coaching bookings — so before the programme starts it
 * only offers doors to pages that have nothing behind them. Each enrolment
 * stage has one page that does: the wizard while Onboarding, the overview (with
 * its waiting notice and compliance documents) otherwise. Statuses after the
 * start (Active, On break, Completed, Withdrawn…) keep Student Home, as does an
 * unknown status, for the same lock-out reason as isFreshStatus.
 */
export function apprenticeHomeRedirect(programmeStatus?: string | null): string | null {
  if (isOnboardingStatus(programmeStatus)) return ONBOARDING_ROUTE;
  const status = (programmeStatus || '').trim().toLowerCase();
  if (isFreshStatus(programmeStatus) || isDeliveryStatus(programmeStatus) || status === READY_TO_ENROL_STATUS.toLowerCase()) {
    return FRESH_ROUTE;
  }
  return null;
}

/**
 * The sidebar for a learner at the given programme status.
 *
 * Onboarding and Delivery are both pre-teaching states where most of the
 * workspace would render as empty shells, so each gets a reduced menu. Every
 * other status gets the full nav unchanged.
 */
export function navItemsForStatus(
  programmeStatus: string | null | undefined,
  fullNav: SidebarNavItem[],
  learnerKind?: string,
  hasPreviousLearning = false,
  readyForLearning = false,
  // Unknown counts as submitted, for the same lock-out reason as isFreshStatus.
  enrolmentSubmitted = true,
  // Unknown leaves the tab open; the page itself checks with the server.
  firstSessionUnlocked = true,
): SidebarNavItem[] {
  const commercial = learnerKind?.toLowerCase() === 'commercial';
  const availableNav = navItemsForLearnerKind(fullNav, learnerKind);
  // Filtered from the real nav, not re-declared, so labels/icons/hrefs stay in
  // one place and cannot drift.
  const pick = (ids: string[]) =>
    ids
      .map((id) => availableNav.find((item) => item.id === id))
      .filter((item): item is SidebarNavItem => Boolean(item));

  if (isFreshStatus(programmeStatus)) return pick(FRESH_NAV_IDS);
  if (isOnboardingStatus(programmeStatus)) return commercial ? pick(FRESH_NAV_IDS) : onboardingNavItems(enrolmentSubmitted);
  if (isDeliveryStatus(programmeStatus)) {
    if (commercial && readyForLearning) return availableNav;
    const ids = commercial ? FRESH_NAV_IDS : DELIVERY_NAV_IDS;
    const items = pick(hasPreviousLearning ? [...ids, 'learner-my-learning'] : ids);
    return commercial ? items : [...items, firstSessionNavItem(firstSessionUnlocked)];
  }
  return availableNav;
}

/**
 * Send a learner whose enrolment has not started back to their overview.
 *
 * The reduced sidebar stops them *navigating* elsewhere, but a bookmark or an
 * old link still resolves, and every one of those pages reads a training plan
 * that does not exist yet — which is how a learner ends up looking at a raw
 * database error where their progress should be. The overview is the one page
 * that has something to tell them, so everything else lands there.
 *
 * Pass `enabled: false` while the record is loading, so an undefined status is
 * never mistaken for 'Fresh user'.
 */
export function useFreshUserRedirect(programmeStatus: string | undefined, enabled = true): boolean {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const isFresh = enabled && isFreshStatus(programmeStatus);
  // Both the self dashboard and an explicitly selected learner dashboard show
  // the waiting state. Keep the selected learner when already on either one.
  const alreadyThere = /^\/workspace\/learner\/(?:dashboard|(?:commercial|apprenticeship)\/[^/]+\/dashboard)\/?$/.test(pathname);

  useEffect(() => {
    // Never redirect away from the destination itself, or it could never render.
    if (isFresh && !alreadyThere) {
      navigate(FRESH_ROUTE, { replace: true });
    }
  }, [isFresh, alreadyThere, navigate]);

  return isFresh;
}

/**
 * Send a learner whose programme status is 'Onboarding' to their own enrolment
 * wizard instead of the page they asked for.
 *
 * Until enrolment is done there is no training plan, progress or attendance to
 * show, so the normal learner pages would render as empty shells — the wizard is
 * the only thing they can act on. Staff moving the learner off 'Onboarding'
 * restores normal navigation with no further change.
 *
 * Pass `enabled: false` while the learner record is still loading, so an
 * undefined status is never mistaken for "not onboarding".
 */
export function useOnboardingRedirect(
  programmeStatus: string | undefined,
  enabled = true,
  learnerKind?: string,
): boolean {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const shouldRedirect = enabled && learnerKind?.toLowerCase() !== 'commercial' && isOnboardingStatus(programmeStatus);

  useEffect(() => {
    // Never redirect away from the onboarding route itself, or the wizard could
    // not be displayed at all.
    if (shouldRedirect && !pathname.startsWith(ONBOARDING_ROUTE)) {
      // `replace` so Back doesn't bounce between the two pages.
      navigate(ONBOARDING_ROUTE, { replace: true });
    }
  }, [shouldRedirect, pathname, navigate]);

  return shouldRedirect;
}
