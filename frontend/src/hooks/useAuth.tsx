import { AuthContext, useAuth, type AuthState, type RbacContextValue } from './useAuth.helpers';
export type { AuthState, RbacContextValue } from './useAuth.helpers';
import { createContext, useContext, useState, useCallback, useMemo, useEffect, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import type { TenantUser } from '@/mocks/users';
import type { RoleDef, PermissionLevel, AccessScope } from '@/mocks/rbac';
import { ALL_ROLES, ALL_PERMISSIONS, PERMISSION_LEVELS, ROUTE_PERMISSIONS, NAV_PERMISSIONS } from '@/mocks/rbac';
import { kbcUsers } from '@/mocks/users';
import { kbcTenant, demoProviderTenant, type Tenant } from '@/mocks/tenant';
import { clearChatSession } from '@/api/chat';
import { apiLogin, apiLogout, apiMe, type AuthUser, type Role } from '@/api/auth';
import { rememberSignedInLearner } from '@/hooks/useMyLearner';
import { syncPersonalLearningAccount } from '@/lib/personalLearning';
import { clearCoachViewAs, syncCoachViewAsAccount } from '@/lib/coachViewAs';
import { clearTutorViewAs, syncTutorViewAsAccount } from '@/lib/tutorViewAs';
import { installSessionExpiryHandler, resetSessionExpiryNotice } from '@/lib/sessionExpiry';
import { useToastOptional } from "@/hooks/useToast.helpers";
import { clearAllCachedResources } from '@/api/cachedRequest';;

// ============================================================
// Helpers
// ============================================================

const AUTH_STORAGE_KEY = 'kbc_auth_email';
let cachedIdentity: string | null = null;

/**
 * Backend role -> RBAC role ids in `@/mocks/rbac`.
 *
 * The server knows four coarse roles (see login/models.py); this UI's RBAC
 * layer has fifteen finer ones. Mapping happens here so the rest of the app
 * keeps using the permission slugs it already uses.
 *
 * `admin` maps to tenant-admin, which `hasWildcardAccess` treats as a bypass —
 * matching the server, where role='admin' carries every permission.
 */
const ROLE_TO_RBAC_IDS: Record<Role, string[]> = {
  admin: ['role_tenant_admin'],
  staff: ['role_compliance'],
  employer: ['role_employer'],
  learner: ['role_learner'],
};

/** Build the local AuthState from a server account record. */
function stateFromAccount(account: AuthUser): AuthState {
  const identity = JSON.stringify([account.id, account.role, account.access, account.subjectType, account.subjectId, account.learnerType]);
  if (identity !== cachedIdentity) clearAllCachedResources();
  cachedIdentity = identity;
  // A learner's own pages (/workspace/learner and the paramless /learner/*
  // routes) resolve through the "remembered learner". Pin it to the account
  // that just signed in — otherwise a learner is shown whichever record was
  // last in localStorage, or the hardcoded demo learner on a fresh browser.
  // Single funnel: every sign-in, session restore and refresh lands here.
  rememberSignedInLearner(account.subjectType, account.subjectId, account.learnerType);
  syncPersonalLearningAccount(account);

  // Same reasoning for the coach workspace: an admin's "view as coach" choice
  // is stored per browser, so it has to be dropped here when the account that
  // resolves is not the admin who made it — otherwise a coach signing in on
  // that browser would request somebody else's caseload and be refused.
  syncCoachViewAsAccount(account);
  // The tutor workspace's picker stores its choice the same way, so it needs the
  // same guard — see lib/tutorViewAs.
  syncTutorViewAsAccount(account);

  const roles = (ROLE_TO_RBAC_IDS[account.role] ?? [])
    .map(id => ALL_ROLES.find(r => r.id === id))
    .filter((r): r is RoleDef => r !== undefined);

  // The app's components read a TenantUser. Project the server account onto
  // that shape rather than looking one up in the mock list — a real account
  // will not be in it.
  const user: TenantUser = {
    id: `acct_${account.id}`,
    tenantId: kbcTenant.id,
    email: account.email,
    fullName: account.displayName || account.email,
    roles: ROLE_TO_RBAC_IDS[account.role] ?? [],
    organisationId: account.organisationIds?.[0] != null ? `org_${account.organisationIds[0]}` : '',
    status: 'active',
    lastLogin: account.lastLoginAt || new Date().toISOString(),
  };

  return { user, tenant: kbcTenant, roles, isAuthenticated: true, account };
}

const SIGNED_OUT: AuthState = {
  user: null,
  tenant: null,
  roles: [],
  isAuthenticated: false,
  account: null,
};

function signedOutState(): AuthState {
  syncPersonalLearningAccount(null);
  rememberSignedInLearner(undefined, undefined);
  return SIGNED_OUT;
}

function getLevelRank(level: PermissionLevel): number {
  const found = PERMISSION_LEVELS.find(l => l.value === level);
  return found?.rank ?? 0;
}

function buildPermissionMap(roles: RoleDef[]): Map<string, { level: PermissionLevel; scope: AccessScope; isAdminBypass: boolean }> {
  const map = new Map<string, { level: PermissionLevel; scope: AccessScope; isAdminBypass: boolean }>();

  for (const role of roles) {
    for (const perm of role.permissions) {
      const existing = map.get(perm.permissionSlug);
      const permDef = ALL_PERMISSIONS.find(p => p.slug === perm.permissionSlug);

      if (existing) {
        const newRank = getLevelRank(perm.level);
        const existingRank = getLevelRank(existing.level);
        if (newRank > existingRank) {
          existing.level = perm.level;
        }
        if (perm.scope === 'global' || (perm.scope === 'tenant' && existing.scope !== 'global')) {
          existing.scope = perm.scope;
        }
      } else {
        map.set(perm.permissionSlug, {
          level: perm.level,
          scope: perm.scope,
          isAdminBypass: permDef?.isAdminBypass ?? false,
        });
      }
    }
  }

  return map;
}

function hasWildcardAccess(roles: RoleDef[]): boolean {
  return roles.some(r => r.slug === 'super-admin' || r.slug === 'tenant-admin');
}

// ============================================================
// Provider
// ============================================================

export function AuthProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  // Optional on purpose: ToastProvider sits above this one in App.tsx, but
  // signing someone out must not depend on a toast being available.
  const toast = useToastOptional();

  const [auth, setAuth] = useState<AuthState>(SIGNED_OUT);
  // Starts false: the session lives in an HttpOnly cookie, which JS cannot
  // read, so the only way to know whether we are signed in is to ask the
  // server. Routes must wait for this rather than briefly rendering as
  // signed-out and bouncing an authenticated user to /login.
  const [isInitialized, setIsInitialized] = useState(false);
  const [initializationError, setInitializationError] = useState<string | null>(null);
  const [initializationAttempt, setInitializationAttempt] = useState(0);
  const retryInitialization = useCallback(() => {
    setIsInitialized(false);
    setInitializationError(null);
    setInitializationAttempt(attempt => attempt + 1);
  }, []);

  // Resolve the session cookie into an identity on mount.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const account = await apiMe();
        if (!cancelled) {
          if (!account) { clearAllCachedResources(); cachedIdentity = null; }
          setAuth(account ? stateFromAccount(account) : signedOutState());
          setInitializationError(null);
        }
      } catch {
        // Keep protected content closed, but let the user retry the existing
        // cookie instead of sending them to sign in during a server outage.
        if (!cancelled) setInitializationError('We could not check your session. Check your connection and try again.');
      } finally {
        if (!cancelled) setIsInitialized(true);
      }
    })();
    return () => { cancelled = true; };
  }, [initializationAttempt]);

  // A 401 from a gated API *suggests* the session this browser holds is gone —
  // expired, revoked, the account deactivated, or signed out elsewhere. Before
  // the API gate existed those requests were answered anyway and a lapsed
  // session went unnoticed; now every panel on the page fails at once, so
  // something has to say why rather than leaving a screen of broken cards.
  //
  // It suggests, and does not prove. A 401 is what the backend answers whenever
  // it cannot identify the caller, and an ended session is only one of the ways
  // that happens: a request that raced the sign-in cookie, an auth lookup that
  // failed against the database, a gate applied to a prefix the page should not
  // have called. Acting on the first 401 unconditionally is what made this feel
  // arbitrary — people were bounced to /login an hour into a five-hour session,
  // with a row in `Login_sessions` that was alive and nowhere near expiring.
  //
  // So the 401 is treated as evidence and checked against `/login_api/me/`,
  // which is the authority on this question and deliberately outside the gate
  // (its own 401 is a normal answer, so it cannot start this loop again). Only
  // `/me` answering "nobody" signs the person out. Every other outcome leaves
  // the session alone: a broken panel is recoverable, a discarded session is
  // not — it costs the person their password and their place on the page.
  //
  // Installed here rather than at a call site because roughly forty modules
  // under api/ and lib/ each own their own fetch. See lib/sessionExpiry.
  useEffect(() => {
    let cancelled = false;
    let rearmTimer: ReturnType<typeof setTimeout> | undefined;

    // Re-arm on a delay, not immediately. The latch inside sessionExpiry is the
    // only thing stopping a panel that 401s on every poll from asking `/me` on
    // every poll as well; a few seconds of quiet bounds that to one check per
    // burst, and still catches a session that really has ended on the next
    // request the page makes.
    const rearmSoon = () => {
      rearmTimer = setTimeout(() => {
        if (!cancelled) resetSessionExpiryNotice();
      }, 10_000);
    };

    const uninstall = installSessionExpiryHandler(() => {
      void (async () => {
        let account: AuthUser | null = null;
        try {
          account = await apiMe();
        } catch {
          // `/me` itself could not be reached. A network or server failure is
          // not evidence of anything about the session, and signing somebody
          // out is the one response to it that cannot be taken back.
          if (!cancelled) rearmSoon();
          return;
        }
        if (cancelled) return;

        if (account) {
          // Still signed in, so that 401 belonged to that one request. Keep the
          // session and refresh the identity while we have it — the answer is
          // current, and a role or access change is exactly the kind of thing
          // that produces a surprising refusal.
          setAuth(stateFromAccount(account));
          rearmSoon();
          return;
        }

        // Re-authentication starts at the account home after the login form.
        localStorage.removeItem(AUTH_STORAGE_KEY);
        clearCoachViewAs();
        clearTutorViewAs();
        clearAllCachedResources();
        cachedIdentity = null;
        setAuth(signedOutState());
        toast?.warning(
          'Your session has ended',
          'Please sign in again to continue.',
        );
        navigate('/login', { replace: true });
      })();
    });

    return () => {
      cancelled = true;
      if (rearmTimer) clearTimeout(rearmTimer);
      uninstall();
    };
  }, [navigate, toast]);

  // Sign-out in another tab should not leave this one showing a console it can
  // no longer load data for. The flag is a same-origin broadcast only — the
  // authority is always the cookie and /me.
  useEffect(() => {
    const handleStorage = (e: StorageEvent) => {
      if (e.key !== AUTH_STORAGE_KEY) return;
      if (e.newValue === null) {
        clearAllCachedResources();
        cachedIdentity = null;
        setAuth(signedOutState());
        return;
      }
      void apiMe()
        .then(account => setAuth(account ? stateFromAccount(account) : signedOutState()))
        .catch(() => undefined);
    };
    window.addEventListener('storage', handleStorage);
    return () => window.removeEventListener('storage', handleStorage);
  }, []);

  const login = useCallback(async (email: string, password: string, remember = false) => {
    // Throws AuthError on failure; the caller renders err.message.
    const account = await apiLogin(email, password, remember);
    // Re-arm the expiry notice: this browser has a live session again.
    resetSessionExpiryNotice();
    setAuth(stateFromAccount(account));
    setInitializationError(null);
    setIsInitialized(true);
    localStorage.setItem(AUTH_STORAGE_KEY, account.email);
    return account;
  }, []);

  // Local-only demo preview — see `previewAs` in RbacContextValue.
  const previewAs = useCallback((email: string) => {
    const foundUser = kbcUsers.find(u => u.email === email);
    if (!foundUser) return;
    syncPersonalLearningAccount(null);
    rememberSignedInLearner(undefined, undefined);

    const userRoles = foundUser.roles
      .map(rId => ALL_ROLES.find(r => r.id === rId))
      .filter((r): r is RoleDef => r !== undefined);

    setAuth({
      user: foundUser,
      tenant: foundUser.tenantId === 't_kbc_001' ? kbcTenant : demoProviderTenant,
      roles: userRoles,
      isAuthenticated: true,
      // No server account: nothing here is backed by a session.
      account: null,
    });
  }, []);

  const logout = useCallback(() => {
    clearAllCachedResources();
    cachedIdentity = null;
    void clearChatSession();
    // Revoke server-side first so the session dies even if this tab is closed
    // before navigation completes; the local state is cleared regardless.
    void apiLogout().catch(() => undefined);
    localStorage.removeItem(AUTH_STORAGE_KEY);
    clearCoachViewAs();
    clearTutorViewAs();
    setAuth(signedOutState());
    navigate('/login');
  }, [navigate]);

  const switchTenant = useCallback((tenantId: string) => {
    setAuth(prev => ({
      ...prev,
      tenant: tenantId === 't_kbc_001' ? kbcTenant : demoProviderTenant,
    }));
  }, []);

  // Role switcher — a demo/testing affordance only. It changes what this
  // browser renders; it does not change the server's view of the account, so
  // any API call still runs with the real role. Kept for the existing
  // role-preview UI.
  const switchRole = useCallback((roleSlug: string) => {
    setAuth(prev => {
      if (!prev.user) return prev;

      // Find the role definition
      const targetRole = ALL_ROLES.find(r => r.slug === roleSlug);
      if (!targetRole) return prev;

      // Find the demo user that matches this role
      const roleEmailMap: Record<string, string> = {
        learner: 'learner@kbc.test',
        coach: 'coach@kbc.test',
        tutor: 'tutor@kbc.test',
        employer: 'employer@kbc.test',
        compliance: 'compliance@kbc.test',
        qa: 'qa@kbc.test',
        mis: 'mis@kbc.test',
        'tenant-admin': 'admin@kbc.test',
        leadership: 'leadership@kbc.test',
        finance: 'finance@kbc.test',
        auditor: 'auditor@kbc.test',
        'programme-manager': 'admin@kbc.test',
        engagement: 'compliance@kbc.test',
        curriculum: 'tutor@kbc.test',
        'super-admin': 'admin@kbc.test',
      };

      const targetEmail = roleEmailMap[roleSlug] || 'admin@kbc.test';
      const targetUser = kbcUsers.find(u => u.email === targetEmail);

      if (!targetUser) return prev;

      const userRoles = targetUser.roles
        .map(rId => ALL_ROLES.find(r => r.id === rId))
        .filter((r): r is RoleDef => r !== undefined);

      // Persist the new email
      localStorage.setItem(AUTH_STORAGE_KEY, targetEmail);

      return {
        ...prev,
        user: targetUser,
        roles: userRoles,
      };
    });
  }, []);

  // Build permission map from current roles
  const permissionMap = useMemo(() => buildPermissionMap(auth.roles), [auth.roles]);
  const isWildcard = useMemo(() => hasWildcardAccess(auth.roles), [auth.roles]);
  const isAdminVal = useMemo(() => auth.roles.some(r => r.slug === 'super-admin' || r.slug === 'tenant-admin'), [auth.roles]);

  const hasPermission = useCallback((permissionSlug: string, minLevel: PermissionLevel = 'view', _scope?: AccessScope): boolean => {
    if (!auth.isAuthenticated) return false;
    if (isWildcard) return true;

    const perm = permissionMap.get(permissionSlug);
    if (!perm) return false;

    const requiredRank = getLevelRank(minLevel);
    const actualRank = getLevelRank(perm.level);
    return actualRank >= requiredRank;
  }, [auth.isAuthenticated, isWildcard, permissionMap]);

  const getPermissionLevel = useCallback((permissionSlug: string): PermissionLevel => {
    if (isWildcard) return 'full_admin';
    return permissionMap.get(permissionSlug)?.level ?? 'none';
  }, [isWildcard, permissionMap]);

  const getPermissionScope = useCallback((permissionSlug: string): AccessScope | null => {
    if (isWildcard) return 'global';
    return permissionMap.get(permissionSlug)?.scope ?? null;
  }, [isWildcard, permissionMap]);

  const canAccessRoute = useCallback((path: string): boolean => {
    if (!auth.isAuthenticated) return path === '/' || path === '/login';

    let bestMatch = ROUTE_PERMISSIONS.find(r => r.path === path);
    if (!bestMatch) {
      bestMatch = ROUTE_PERMISSIONS
        .filter(r => path.startsWith(r.path))
        .sort((a, b) => b.path.length - a.path.length)[0];
    }

    if (!bestMatch) return false;

    if (bestMatch.allowedRoles?.includes('*')) return true;

    if (bestMatch.allowedRoles) {
      const userSlugs = auth.roles.map(r => r.slug);
      return bestMatch.allowedRoles.some(r => userSlugs.includes(r));
    }

    return false;
  }, [auth.isAuthenticated, auth.roles]);

  const canSeeNavItem = useCallback((navId: string): boolean => {
    if (!auth.isAuthenticated) return false;

    const navPerm = NAV_PERMISSIONS.find(n => n.navId === navId);
    if (!navPerm) return true;

    if (navPerm.allowedRoles?.includes('*')) return true;

    if (navPerm.allowedRoles) {
      const userSlugs = auth.roles.map(r => r.slug);
      return navPerm.allowedRoles.some(r => userSlugs.includes(r));
    }

    return true;
  }, [auth.isAuthenticated, auth.roles]);

  const value = useMemo<RbacContextValue>(() => ({
    auth,
    login,
    logout,
    isInitialized,
    initializationError,
    retryInitialization,
    previewAs,
    switchTenant,
    switchRole,
    hasPermission,
    canAccessRoute,
    canSeeNavItem,
    getPermissionLevel,
    getPermissionScope,
    isAdmin: isAdminVal,
  }), [auth, login, logout, isInitialized, initializationError, retryInitialization, previewAs, switchTenant, switchRole, hasPermission, canAccessRoute, canSeeNavItem, getPermissionLevel, getPermissionScope, isAdminVal]);

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
}
