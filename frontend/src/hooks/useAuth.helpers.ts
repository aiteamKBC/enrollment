import { createContext, useContext } from 'react';
import type { TenantUser } from '@/mocks/users';
import type { RoleDef, PermissionLevel, AccessScope } from '@/mocks/rbac';
import { type Tenant } from '@/mocks/tenant';
import { type AuthUser } from '@/api/auth';


// ============================================================
// Types
// ============================================================

export interface AuthState {
  user: TenantUser | null;
  tenant: Tenant | null;
  roles: RoleDef[];
  isAuthenticated: boolean;
  /** The server's account record — the authoritative identity. */
  account: AuthUser | null;
}


export interface RbacContextValue {
  auth: AuthState;
  /**
   * Sign in against the backend. Rejects with an `AuthError` carrying the
   * server's message and code (`locked`, `throttled`, …) — callers should
   * await it and surface `err.message`.
   */
  login: (email: string, password: string, remember?: boolean) => Promise<AuthUser>;
  logout: () => void;
  /** False until the initial `/login_api/me/` call has settled. */
  isInitialized: boolean;
  /** A failed lookup does not mean the session has ended. */
  initializationError: string | null;
  retryInitialization: () => void;
  /**
   * Enter a workspace as one of the mock demo accounts, with no server session.
   *
   * This is the landing page's "explore this section" shortcut and is exactly
   * as authoritative as it sounds: it sets local UI state only. Any request to
   * a protected endpoint will still 401, because no cookie is issued. Real
   * sign-in is `login`.
   */
  previewAs: (email: string) => void;
  switchTenant: (tenantId: string) => void;
  switchRole: (roleSlug: string) => void;
  /** Check if current user has a specific permission at the given level */
  hasPermission: (permissionSlug: string, minLevel?: PermissionLevel, scope?: AccessScope) => boolean;
  /** Check if current user can access a given route */
  canAccessRoute: (path: string) => boolean;
  /** Check if current user can see a navigation item */
  canSeeNavItem: (navId: string) => boolean;
  /** Get the effective permission level for a given permission slug */
  getPermissionLevel: (permissionSlug: string) => PermissionLevel;
  /** Get the effective scope for a given permission slug */
  getPermissionScope: (permissionSlug: string) => AccessScope | null;
  /** Check if user has admin bypass (tenant admin or super admin) */
  isAdmin: boolean;
}


// ============================================================
// Context
// ============================================================

export const AuthContext = createContext<RbacContextValue | null>(null);


// ============================================================
// Hook
// ============================================================

export function useAuth(): RbacContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return ctx;
}
