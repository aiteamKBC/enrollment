import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import type { MonthlyAssignment } from '@/api/monthlyAssignment';

const fields = ['lmsReflection', 'integratedReflection'] as const;
export function useMonthlyReflections(enabled: boolean, learnerId: string, context: {
  month: string; answer: string; question: string;
  learning: { learned: string; understood: string; skills: string };
  activities: { title: string; date: string; reflection: string; ksbs: string[] }[];
}, data: MonthlyAssignment, onChange: Dispatch<SetStateAction<MonthlyAssignment>>) {
  const [status, setStatus] = useState('');
  const latest = useRef({ data, onChange });
  latest.current = { data, onChange };
  const key = JSON.stringify({ learnerId, context });
  useEffect(() => {
    setStatus('');
    if (!enabled) return;
    const targets = fields.filter(k => !latest.current.data[k].trim());
    if (!targets.length) return;
    const input = JSON.parse(key);
    if (!input.context.answer.trim() && !input.context.activities.length) {
      setStatus('Add your assignment answer or recorded activities to draft these reflections.'); return;
    }
    const controller = new AbortController();
    setStatus('Generating your monthly reflections from your assignment answer and activities…');
    void (async () => {
      try {
        const response = await fetch('/learner_api/reflection/monthly-reflections/', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: key, signal: controller.signal,
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Could not generate reflections. You can write them yourself.');
        if (controller.signal.aborted) return;
        const valid = targets.filter(k => typeof result[k] === 'string' && result[k].trim().split(/\s+/).length >= 20);
        latest.current.onChange(current => {
          if (controller.signal.aborted) return current;
          const next = { ...current };
          for (const k of valid) if (!current[k].trim()) next[k] = result[k];
          return next;
        });
        setStatus(valid.length ? 'Drafts are ready. Review and edit them to reflect your experience. Any fields left blank need more details from you.' : 'There is not enough detail to draft these reflections. Describe your learning in the fields below.');
      } catch (error) {
        if (!controller.signal.aborted) setStatus(error instanceof Error ? error.message : 'Generation failed. You can write these reflections yourself.');
      }
    })();
    return () => controller.abort();
  }, [enabled, key]);
  return status;
}
