import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { useLocation, useNavigate, useNavigationType } from 'react-router-dom';
import type { EditorState } from './CatalogEditor';

export const sections = [
  ['overview', 'Overview'], ['pricing', 'Pricing breakdown'], ['cohorts', 'Cohorts'], ['curriculum', 'Curriculum'],
] as const;
export type SectionId = typeof sections[number][0];
export const curriculumTabs = ['templates', 'ksbs', 'mapping'] as const;
export type CurriculumTab = typeof curriculumTabs[number];

export type CatalogView = {
  programmeId?: string; section?: SectionId; cohortId?: string; groupId?: string;
  deliveryId?: string; templateId?: string; curriculumTab?: CurriculumTab;
};

export function catalogueView(search: string): CatalogView {
  const params = new URLSearchParams(search);
  const programmeId = params.get('programme') || undefined;
  if (!programmeId) return {};
  const cohortId = params.get('cohort') || undefined;
  const groupId = cohortId ? params.get('group') || undefined : undefined;
  const templateId = cohortId ? undefined : params.get('template') || undefined;
  const tab = sections.find(([id]) => id === params.get('tab'))?.[0];
  const curriculumTab = tab === 'curriculum' && !cohortId && !templateId
    ? curriculumTabs.find(id => id === params.get('curriculum')) : undefined;
  return {
    programmeId, section: tab ?? (cohortId ? 'cohorts' : templateId ? 'curriculum' : 'overview'),
    cohortId, groupId, deliveryId: groupId ? params.get('delivery') || undefined : undefined, templateId, curriculumTab,
  };
}

export function catalogueSearch({ programmeId, section, cohortId, groupId, deliveryId, templateId, curriculumTab }: CatalogView) {
  if (!programmeId) return '';
  const params = new URLSearchParams({ programme: programmeId });
  if (section) params.set('tab', section);
  if (cohortId) params.set('cohort', cohortId);
  if (groupId) params.set('group', groupId);
  if (deliveryId) params.set('delivery', deliveryId);
  if (templateId) params.set('template', templateId);
  if (section === 'curriculum' && !templateId && !cohortId && curriculumTab && curriculumTab !== 'templates') params.set('curriculum', curriculumTab);
  return `?${params}`;
}

type Panel = {
  kind: 'form' | 'archive' | 'assignments'; resource: EditorState['resource']; recordId?: string; parentId?: string;
  revision?: number; origin: string;
};

export const discardMessage = 'Discard your unsaved changes?';
function warnOnReload(event: BeforeUnloadEvent) {
  event.preventDefault();
  event.returnValue = '';
}

export function useCatalogueNavigation(content: RefObject<HTMLDivElement | null>, busy: boolean) {
  const nextLocation = useLocation();
  const navigationType = useNavigationType();
  const navigate = useNavigate();
  const [location, setLocation] = useState(nextLocation);
  const completedPanels = useRef(new Set<string>());
  const panel = completedPanels.current.has(location.key) ? undefined : (location.state as { cataloguePanel?: Panel } | null)?.cataloguePanel;
  const dirty = useRef(false);
  const approvedReturn = useRef(false);
  const scrollPositions = useRef(new Map<string, number>());
  const scrollKey = panel ? location.key : location.search;
  const setDirty = useCallback((value: boolean) => {
    dirty.current = value;
    if (value) window.addEventListener('beforeunload', warnOnReload);
    else window.removeEventListener('beforeunload', warnOnReload);
  }, []);
  useEffect(() => () => window.removeEventListener('beforeunload', warnOnReload), []);

  function canLeave() {
    return !busy && (!dirty.current || window.confirm(discardMessage));
  }

  useLayoutEffect(() => {
    if (nextLocation.key === location.key) return;
    // A form is pushed immediately after its origin. Undo a declined Back while its draft is still mounted.
    if (navigationType === 'POP' && panel?.origin === nextLocation.key && !approvedReturn.current && !canLeave()) {
      navigate(1);
      return;
    }
    approvedReturn.current = false;
    setDirty(false);
    scrollPositions.current.set(scrollKey, document.scrollingElement?.scrollTop ?? 0);
    setLocation(nextLocation);
  });

  useEffect(() => {
    function checkLink(event: MouseEvent) {
      if ((!dirty.current && !busy) || event.defaultPrevented || event.button !== 0 ||
        event.ctrlKey || event.altKey || event.metaKey || event.shiftKey) return;
      const link = event.target instanceof Element ? event.target.closest<HTMLAnchorElement>('a[href]') : null;
      if (!link || link.dataset.catalogueNavigation || (link.target && link.target !== '_self') || link.hasAttribute('download')) return;
      const destination = new URL(link.href);
      if (destination.hash && destination.pathname === window.location.pathname && destination.search === window.location.search) return;
      if (!canLeave()) { event.preventDefault(); event.stopPropagation(); }
      else setDirty(false);
    }
    const previousRestoration = window.history.scrollRestoration;
    window.history.scrollRestoration = 'manual';
    document.addEventListener('click', checkLink, true);
    return () => {
      window.history.scrollRestoration = previousRestoration;
      document.removeEventListener('click', checkLink, true);
    };
  }, [busy, setDirty]);

  const initialKey = useRef(location.key);
  const restore = useCallback(() => {
    if (location.key === initialKey.current && !scrollPositions.current.has(scrollKey)) return;
    content.current?.focus({ preventScroll: true });
    const scroller = document.scrollingElement;
    if (scroller) scroller.scrollTop = scrollPositions.current.get(scrollKey) ?? 0;
  }, [content, location.key, scrollKey]);

  function destination(view: CatalogView) {
    return { pathname: location.pathname, search: catalogueSearch(view) };
  }

  function go(view: CatalogView) {
    const target = destination(view);
    const sameView = target.search === catalogueSearch(catalogueView(location.search));
    if (!panel && sameView) return false;
    if (!canLeave()) return false;
    setDirty(false);
    if (panel && sameView) navigate(-1);
    else navigate(target, { replace: Boolean(panel) });
    return true;
  }

  function openPanel(kind: Panel['kind'], next: EditorState) {
    if (!canLeave()) return;
    navigate({ pathname: location.pathname, search: location.search }, { state: { cataloguePanel: {
      kind, resource: next.resource, recordId: next.record?.id, revision: next.record?.revision,
      parentId: next.parentId, origin: location.key,
    } satisfies Panel } });
  }

  function closePanel() {
    if (!canLeave()) return false;
    setDirty(false);
    navigate(-1);
    return true;
  }

  function finish(view: CatalogView) {
    setDirty(false);
    const target = destination(view);
    if (target.search === catalogueSearch(catalogueView(location.search))) {
      approvedReturn.current = true;
      completedPanels.current.add(location.key);
      navigate(-1);
    } else navigate(target, { replace: true });
  }

  const reset = useCallback(() => navigate({ pathname: location.pathname, search: '' }, { replace: true, state: null }), [location.pathname, navigate]);
  return { location, panel, view: catalogueView(location.search), destination, go, openPanel, closePanel, finish, setDirty, restore, reset };
}
