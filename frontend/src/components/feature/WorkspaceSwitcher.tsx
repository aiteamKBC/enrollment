import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { AppIcon } from '@/components/feature/AppIcon';
import { useAuth } from "@/hooks/useAuth.helpers";
import { activeWorkspace, workspacesFor, type PortalWorkspace } from '@/lib/portalWorkspaces';

/* ═══════════════════════════════════════════════════════
   WORKSPACE SWITCHER — how an administrator changes section.

   It replaced a five-card panel on the Super Admin dashboard.
   The panel's problem was not how it looked but where it was:
   only on the dashboard, so changing section meant navigating
   home and then out again. In the header it is one click from
   every page.

   Five sections, so there is no filter box and no group headings:
   both were in an earlier draft of this component and both were
   chrome over a list you can read at a glance. What is left is
   the list, where you are in it, and a way in.

   Navigation only. An administrator already has access to every
   section, so there is no role to assume and nothing here touches
   RBAC state. (The retired RoleSwitcher did call switchRole; that
   was the demo-account concept, not this.)
   ═══════════════════════════════════════════════════════ */

/** Only an administrator sees this: everyone else has exactly one workspace. */
export function WorkspaceSwitcher() {
  const { auth, isAdmin } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const current = activeWorkspace(pathname);
  // Learner is offered only to somebody who has a record of their own, so the
  // rendered list and the keyboard index below are built from the same array.
  //
  // Memoised on the two fields it actually depends on: workspacesFor returns a
  // fresh array each call, and an unmemoised one re-ran the effect that seeds
  // `highlighted` on every render -- which reset the keyboard selection back to
  // the current workspace the moment an arrow key moved it.
  const learnerRecordId = auth.account?.learnerRecordId ?? null;
  const learnerRecordKind = auth.account?.learnerRecordKind ?? null;
  const workspaces = useMemo(
    () => workspacesFor({ learnerRecordId, learnerRecordKind }),
    [learnerRecordId, learnerRecordKind],
  );

  useEffect(() => {
    if (!open) return;
    // Start on the section you are in, so ↓ then ↵ is a deliberate move rather
    // than a jump to the top of the list.
    const index = current ? workspaces.findIndex((w) => w.slug === current.slug) : 0;
    setHighlighted(index < 0 ? 0 : index);
    // There is no search field to take focus now, so the menu takes it itself —
    // otherwise the arrow keys would still be talking to the trigger button.
    menuRef.current?.focus();
  }, [open, current, workspaces]);

  useEffect(() => {
    if (!open) return undefined;
    const handlePointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', handlePointerDown);
    return () => document.removeEventListener('pointerdown', handlePointerDown);
  }, [open]);

  const go = (workspace: PortalWorkspace) => {
    setOpen(false);
    navigate(workspace.path);
  };

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Escape') {
      setOpen(false);
      return;
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setHighlighted((index) => (index + step + workspaces.length) % workspaces.length);
      return;
    }
    if (event.key === 'Enter' || event.key === ' ') {
      const target = workspaces[highlighted];
      if (!open || !target) return;
      event.preventDefault();
      go(target);
    }
  };

  if (!auth.isAuthenticated || !isAdmin) return null;

  return (
    <div className="kbc-workspace-switcher relative shrink-0" ref={rootRef} onKeyDown={handleKeyDown}>
      <button
        onClick={() => setOpen((wasOpen) => !wasOpen)}
        aria-haspopup="menu"
        aria-expanded={open}
        className={`kbc-workspace-switcher-button group flex h-9 cursor-pointer items-center gap-2 rounded-xl border pl-1.5 pr-2 font-bold shadow-sm transition-smooth ${
          open
            ? 'border-primary-300 bg-primary-50 shadow-sm'
            : 'border-foreground-200/70 hover:border-primary-300 hover:bg-primary-50/50'
        }`}
        title="Switch workspace"
      >
        {/* The current section's own icon, so the control reads as "you are
            here" and not just as a menu. */}
        <span
          className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-lg transition-smooth ${
            open ? 'bg-primary-500 text-white' : 'bg-white/20 text-white group-hover:bg-white/30'
          }`}
        >
          <AppIcon className={`${current ? current.icon : 'ri-dashboard-line'} text-[11px]`} />
        </span>
        <span className="hidden max-w-[9rem] truncate text-[12.5px] font-bold !text-white sm:inline">
          Workspaces
        </span>
        <AppIcon
          className={`ri-arrow-down-s-line text-[11px] !text-white transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <div
          ref={menuRef}
          role="menu"
          tabIndex={-1}
          aria-label="Switch workspace"
          className="absolute right-0 top-full z-50 mt-2 w-[18rem] overflow-hidden rounded-2xl border border-background-200 bg-background-50 shadow-2xl shadow-foreground-950/10 focus:outline-none"
        >
          <p className="px-3.5 pb-1.5 pt-3 text-[10px] font-bold uppercase tracking-widest text-foreground-400">
            Switch workspace
          </p>

          <div className="px-1.5 pb-1.5">
            {workspaces.map((workspace, index) => {
              const isCurrent = current?.slug === workspace.slug;
              const isHighlighted = index === highlighted;
              return (
                <button
                  key={workspace.slug}
                  role="menuitem"
                  onClick={() => go(workspace)}
                  onMouseEnter={() => setHighlighted(index)}
                  aria-current={isCurrent ? 'true' : undefined}
                  className={`flex w-full cursor-pointer items-center gap-3 rounded-xl px-2 py-2.5 text-left transition-smooth ${
                    isCurrent
                      ? 'bg-primary-50'
                      : isHighlighted
                        ? 'bg-background-100'
                        : ''
                  }`}
                >
                  <span
                    className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-smooth ${
                      isCurrent ? 'bg-primary-500 text-white' : 'bg-background-100 text-foreground-500'
                    }`}
                  >
                    <AppIcon className={`${workspace.icon} text-[13px]`} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span
                      className={`block truncate text-[13px] font-semibold leading-tight ${
                        isCurrent ? 'text-primary-800' : 'text-foreground-800'
                      }`}
                    >
                      {workspace.label}
                    </span>
                    <span className="mt-0.5 block truncate text-[10.5px] leading-snug text-foreground-400">
                      {workspace.blurb}
                    </span>
                  </span>
                  {isCurrent ? (
                    <AppIcon className="ri-check-line shrink-0 text-sm text-primary-600" />
                  ) : (
                    // Appears on the active row only, so the list stays quiet
                    // until you are actually pointing at something.
                    <AppIcon
                      className={`ri-arrow-right-line shrink-0 text-xs transition-opacity ${
                        isHighlighted ? 'text-foreground-400 opacity-100' : 'opacity-0'
                      }`}
                    />
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
