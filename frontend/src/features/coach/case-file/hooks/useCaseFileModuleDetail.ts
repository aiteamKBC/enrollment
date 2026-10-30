import { useCallback, useEffect, useRef, useState } from 'react';
import type { PlanModule } from '@/api/trainingPlanDashboard';
import { useCaseFileSession } from './CaseFileSession';

/** Curriculum descriptions/outcomes are fetched only for the displayed module. */
export function useCaseFileModuleDetail(moduleId?: string) {
  const session = useCaseFileSession();
  const [revision, setRevision] = useState(0);
  const handledRevision = useRef(0);
  const refresh = useCallback(() => setRevision(value => value + 1), []);
  const [state, setState] = useState<{ id: string; module?: PlanModule; error?: string }>();
  useEffect(() => {
    if (!session || !moduleId) return;
    const controller = new AbortController();
    const fresh = handledRevision.current !== revision;
    handledRevision.current = revision;
    setState({ id: moduleId });
    void session.read<{ module: PlanModule }>('learning-plan', { resource: 'module', moduleId }, { signal: controller.signal, refresh: fresh }).then(payload => {
      if (!payload.module || payload.module.id !== moduleId) throw new Error('Module details did not match the requested module.');
      if (!controller.signal.aborted) setState({ id: moduleId, module: payload.module });
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setState({ id: moduleId, error: reason instanceof Error ? reason.message : 'Could not load module details.' });
    });
    return () => controller.abort();
  }, [session, moduleId, revision]);
  return session && moduleId ? { ...(state?.id === moduleId ? state : { id: moduleId }), refresh } : undefined;
}
