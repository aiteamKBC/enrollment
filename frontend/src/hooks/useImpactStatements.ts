import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import type { MonthlyAssignment } from '@/api/monthlyAssignment';

const impactFields = ['careerImpact', 'jobImpact', 'employerImpact', 'businessImpact'] as const;
export function useImpactStatements(enabled: boolean, learnerId: string, activityId: string, question: string,
  answer: string, learned: string, data: MonthlyAssignment, businessImpact: string,
  onChange: Dispatch<SetStateAction<MonthlyAssignment>>, onBusinessImpact: (value: string) => void, mode: 'impact' | 'action' = 'impact',
  activities: { title: string; date: string; reflection: string; ksbs: string[] }[] = []) {
  const [status, setStatus] = useState('');
  const [phase, setPhase] = useState<'idle' | 'loading' | 'success' | 'info' | 'error'>('idle');
  const latest = useRef({ data, businessImpact, onChange, onBusinessImpact });
  latest.current = { data, businessImpact, onChange, onBusinessImpact };
  const key = JSON.stringify({ learnerId, mode, context: { activityId, month: data.month, question, answer,
    learned, understood: data.understood, gainedSkills: data.gainedSkills,
    activities: activities.filter(activity => activity.date.startsWith(`${data.month}-`)),
    lmsReflection: data.lmsReflection, integratedReflection: data.integratedReflection,
    ...(mode === 'action' ? { careerImpact: data.careerImpact, jobImpact: data.jobImpact, employerImpact: data.employerImpact, businessImpact } : {}) } });
  useEffect(() => {
    setStatus(''); setPhase('idle');
    if (!enabled) return;
    const snapshot = { ...latest.current.data, businessImpact: latest.current.businessImpact };
    const fields = mode === 'action' ? ['actionPlan', 'epaPreparedness'] as const : impactFields;
    const targets = fields.filter(k => !snapshot[k].trim());
    if (!targets.length) return;
    if (!JSON.parse(key).context.answer.trim()) { setPhase('info'); setStatus('Write your assignment answer first to generate these drafts.'); return; }
    const controller = new AbortController();
    setPhase('loading');
    setStatus(mode === 'action' ? 'Please wait while we draft your next-month action plan and EPA preparation.' : 'Please wait while we draft the impact of your learning on your career, work and employer.');
    void (async () => {
      try {
        const response = await fetch('/learner_api/reflection/monthly-reflections/', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: key, signal: controller.signal,
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Could not generate these drafts. You can write them yourself.');
        if (controller.signal.aborted) return;
        const valid = targets.filter(k => typeof result[k] === 'string' && result[k].trim().split(/\s+/).length >= 20);
        latest.current.onChange(current => {
          if (controller.signal.aborted) return current;
          const next = { ...current };
          for (const k of valid) if (k !== 'businessImpact' && !current[k].trim()) next[k] = result[k];
          return next;
        });
        if (valid.includes('businessImpact') && !latest.current.businessImpact.trim()) latest.current.onBusinessImpact(result.businessImpact);
        setPhase(valid.length ? 'success' : 'info');
        setStatus(mode === 'action' ? (valid.length ? 'Drafts are ready. Review the proposed actions and EPA reflection, and adjust them to your own goals. Any fields left blank need more details.' : 'There is not enough information to draft your action plan or EPA reflection. Add details below.') : valid.length ? 'Drafts are ready to review. Check that every statement reflects your experience and distinguish planned benefits from actual results. Any fields left blank need more details.' : 'There is not enough information to draft the impact. Add details from your own experience below.');
      } catch (error) {
        if (!controller.signal.aborted) {
          setPhase('error');
          setStatus(error instanceof Error ? error.message : 'Generation failed. Your existing work is preserved.');
        }
      }
    })();
    return () => controller.abort();
  }, [enabled, key, mode]);
  return { status, phase };
}
