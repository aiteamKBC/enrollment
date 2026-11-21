import { useEffect, useRef, useState } from 'react';
import { request } from '../api';
import type { LearnerKind } from '@/api/extendedIlr';

export interface ScheduleOffering {
  id: string; moduleId: string; academicTitle: string; lmsTitle: string;
  delivery: string; cohortId: string; cohortName: string; groupId: string | null;
  group: string; start: string; end: string; approved: boolean; reviewOnly: boolean;
  weeks?: number | null;
  matchConfidence?: 'exact_title' | 'confirmed_alias' | 'tentative'; confidenceReason?: string;
  provisional?: boolean; allowLocalSelection?: boolean;
  informationOnly?: boolean;
}
export interface ScheduleResponse {
  programmeId: string; pathway: string | null; review: boolean; asOfDate: string; offeringCount: number;
  modules: { academicModuleId: string; academicTitle: string; reason: string; offerings: ScheduleOffering[] }[];
  homeGroup?: { status: 'assigned' | 'allocated' | 'eligible' | 'unavailable'; groupId: string | null;
    groupName?: string | null; message?: string; code?: string } | null;
}
export interface ScheduleContext { id: string; kind: LearnerKind }

export function useModuleSchedules(context: ScheduleContext | undefined, pathway: string | undefined, ids: string[], sample: boolean) {
  const review = typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('moduleScheduleReview') === '1';
  const query = new URLSearchParams({ ...(review ? { review: '1' } : {}), ...(pathway ? { pathway, modules: [...ids].sort().join(',') } : {}) });
  const url = context && !sample && ids.length ? `/enrolment_api/programme-suitability/${context.kind}/${encodeURIComponent(context.id)}/module-schedules/?${query}` : '';
  const [snapshot, setSnapshot] = useState<{ url: string; data?: ScheduleResponse; error?: string }>();
  const [attempt, setAttempt] = useState(0);
  // Component-scoped cache: no data survives logout/unmount or crosses accounts.
  const reads = useRef({ cache: new Map<string, { data: ScheduleResponse; at: number }>(),
    pending: new Map<string, Promise<ScheduleResponse>>() });
  useEffect(() => {
    if (!url) return;
    let active = true;
    let sequence = 0;
    let lastFocus = 0;
    const load = async (force = false) => {
      const current = ++sequence;
      try {
        const cached = reads.current.cache.get(url);
        let data: ScheduleResponse;
        const cacheHit = !force && cached && Date.now() - cached.at < 15_000;
        if (cacheHit) data = cached.data;
        else {
          let pending = reads.current.pending.get(url);
          if (!pending) {
            pending = Promise.resolve(request<ScheduleResponse>(url));
            reads.current.pending.set(url, pending);
            pending.finally(() => { reads.current.pending.delete(url); }).catch(() => {});
          }
          data = await pending;
        }
        if (!data || !Array.isArray(data.modules)) throw new Error('The schedule response is unavailable. Retry the read.');
        if (!cacheHit) reads.current.cache.set(url, { data, at: Date.now() });
        if (active && current === sequence) setSnapshot({ url, data });
      } catch (error) {
        if (active && current === sequence) setSnapshot({ url, error: error instanceof Error ? error.message : 'Could not read module schedules.' });
      }
    };
    // Coalesce fast optional-module toggles, while academic rows update instantly.
    const timer = window.setTimeout(() => void load(attempt > 0), 120);
    const refresh = () => {
      if (document.visibilityState === 'hidden' || Date.now() - lastFocus < 1000) return;
      lastFocus = Date.now();
      void load(true); // A return to focus always checks current LMS data.
    };
    window.addEventListener('focus', refresh);
    const confirmed = (event: Event) => {
      const detail = (event as CustomEvent<ScheduleContext>).detail;
      if (detail?.id === context?.id && detail?.kind === context?.kind) {
        reads.current.cache.delete(url);
        reads.current.pending.delete(url);
        void load(true);
      }
    };
    window.addEventListener('pcp-cohort-modules-confirmed', confirmed);
    document.addEventListener('visibilitychange', refresh);
    return () => { active = false; window.clearTimeout(timer); window.removeEventListener('focus', refresh); window.removeEventListener('pcp-cohort-modules-confirmed', confirmed); document.removeEventListener('visibilitychange', refresh); };
  }, [url, attempt, context?.id, context?.kind]);
  return { data: snapshot?.url === url ? snapshot.data : undefined, error: snapshot?.url === url ? snapshot.error : '',
    loading: Boolean(url && snapshot?.url !== url), reviewRequested: review,
    retry: () => { setSnapshot(undefined); setAttempt(n => n + 1); } };
}
