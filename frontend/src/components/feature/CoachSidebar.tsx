import { useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { ChevronDown, PanelLeftClose, PanelLeftOpen, X } from 'lucide-react';
import { useAuth } from "@/hooks/useAuth.helpers";
import { SidebarIcon, type SidebarNavItem } from './Sidebar';
import { LEARNER_LOGO_URL, LEARNER_SIDEBAR_PATTERN_URL } from './learnerShellAssets';
import styles from './CoachSidebar.module.css';

interface CoachSidebarProps {
  navItems: SidebarNavItem[];
  userName: string;
  userRole: string;
  mobileOpen: boolean;
  onCloseMobile: () => void;
  onOpenAccount: () => void;
  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
}

export function CoachSidebar({ navItems, mobileOpen, onCloseMobile, collapsed, onCollapsedChange }: CoachSidebarProps) {
  const { canSeeNavItem } = useAuth();
  const location = useLocation();
  const drawerRef = useRef<HTMLDivElement>(null);
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});
  const items = navItems.filter(item => canSeeNavItem(item.id))
    .map(item => ({ ...item, children: item.children?.filter(child => canSeeNavItem(child.id)) }))
    .filter(item => item.href || item.children?.length);
  const destinations = items.flatMap(item => [item, ...(item.children ?? [])]);
  const active = (item: SidebarNavItem) => {
    const current = `${location.pathname}${location.search}`;
    if (item.href.includes('?')) return item.href === current;
    if (item.matchPaths?.some(path => location.pathname === path || location.pathname.startsWith(`${path}/`))) return true;
    return Boolean(item.href) && (location.pathname === item.href || location.pathname.startsWith(`${item.href}/`))
      && !destinations.some(other => other.href.length > item.href.length
        && (other.href === current || location.pathname.startsWith(`${other.href}/`)));
  };

  useEffect(() => {
    if (!mobileOpen) return;
    const opener = document.activeElement as HTMLElement | null;
    const drawer = drawerRef.current;
    drawer?.querySelector<HTMLElement>('button, a')?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCloseMobile();
      if (event.key !== 'Tab' || !drawer) return;
      const controls = Array.from(drawer.querySelectorAll<HTMLElement>('a[href], button, summary'))
        .filter(element => element.getClientRects().length > 0);
      const first = controls[0];
      const last = controls.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', handleKey);
    return () => { document.removeEventListener('keydown', handleKey); opener?.focus(); };
  }, [mobileOpen, onCloseMobile]);

  const panel = (mobile: boolean) => {
    const compact = !mobile && collapsed;
    return <>
    <div className={styles.pattern} aria-hidden="true" style={{ backgroundImage: `url(${LEARNER_SIDEBAR_PATTERN_URL})` }} />
    <div className={styles.brand}><img src={LEARNER_LOGO_URL} alt="Kent Business College" />
      {mobile && <button type="button" className={styles.close} aria-label="Close navigation" onClick={onCloseMobile}><X size={18} /></button>}
    </div>
    <nav className={styles.nav} aria-label={`Coach ${mobile ? 'mobile menu' : 'primary navigation'}`}>
      {items.map(item => item.children?.length ? <div key={item.id}>
        <button type="button" className={styles.row} aria-expanded={compact ? false : openGroups[item.id] ?? item.children.some(active)}
          aria-controls={`${mobile ? 'mobile-' : ''}${item.id}-links`} data-active={item.children.some(active)}
          aria-label={compact ? item.label : undefined}
          title={compact ? item.label : undefined}
          onClick={() => {
            if (compact) onCollapsedChange(false);
            setOpenGroups(current => ({ ...current, [item.id]: compact || !(current[item.id] ?? item.children.some(active)) }));
          }}>
          <SidebarIcon id={item.id} label={item.label} sourceIcon={item.icon} /><span className={styles.label}>{item.label}</span><ChevronDown className={styles.chevron} size={14} />
        </button>
        {!compact && (openGroups[item.id] ?? item.children.some(active)) && <div className={styles.children} id={`${mobile ? 'mobile-' : ''}${item.id}-links`}>
          {item.children.map(child => <Link key={child.id} to={child.href} className={styles.row} aria-current={active(child) ? 'page' : undefined} onClick={onCloseMobile}>
            <SidebarIcon id={child.id} label={child.label} sourceIcon={child.icon} size={15} /><span className={styles.label}>{child.label}</span>
          </Link>)}
        </div>}
      </div> : item.external ? <a key={item.id} href={item.href} target="_blank" rel="noopener noreferrer" className={styles.row} onClick={onCloseMobile}
        aria-label={compact ? item.label : undefined} title={compact ? item.label : undefined}>
        <SidebarIcon id={item.id} label={item.label} sourceIcon={item.icon} /><span className={styles.label}>{item.label}</span>
      </a> : <Link key={item.id} to={item.href} className={styles.row} aria-current={active(item) ? 'page' : undefined} onClick={onCloseMobile}
        aria-label={compact ? item.label : undefined} title={compact ? item.label : undefined}>
        <SidebarIcon id={item.id} label={item.label} sourceIcon={item.icon} /><span className={styles.label}>{item.label}</span>
      </Link>)}
    </nav>
  </>;
  };

  return <>
    <aside className={styles.desktop} aria-label="Coach sidebar" data-workspace-role="coach" data-collapsed={collapsed}>
      <button type="button" className={styles.collapseToggle} aria-label={collapsed ? 'Expand coach sidebar' : 'Collapse coach sidebar'}
        aria-expanded={!collapsed} onClick={() => onCollapsedChange(!collapsed)}>
        {collapsed ? <PanelLeftOpen aria-hidden="true" /> : <PanelLeftClose aria-hidden="true" />}
      </button>
      {panel(false)}
    </aside>
    {mobileOpen && <div className={styles.backdrop} onClick={onCloseMobile} aria-hidden="true" />}
    <div ref={drawerRef} id="coach-mobile-navigation" className={styles.mobile} data-open={mobileOpen} aria-label="Coach mobile navigation"
      role={mobileOpen ? 'dialog' : undefined} aria-modal={mobileOpen ? true : undefined} aria-hidden={!mobileOpen} inert={!mobileOpen}>{panel(true)}</div>
  </>;
}
