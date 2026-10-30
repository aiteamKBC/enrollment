import { useEffect, useState } from 'react';
import { checkMonthlyAssignment, type AssignmentQualityCheck } from '@/api/monthlyAssignment';

const STEP_KEYS = [
  ['answer', 'learning'], ['evidence'], ['ksbs', 'planned', 'declarations', 'hours'],
  ['reflection'], ['benefit', 'impact'], ['action'], ['meeting', 'presentation'],
];

/** Use the submission rules, including calendar and evidence ownership checks. */
export function useAssignmentStepCheck(snapshot: string, step: number, enabled: boolean) {
  const [retry, setRetry] = useState(0);
  const [result, setResult] = useState<{ snapshot: string; step: number; checks: AssignmentQualityCheck[]; error: string } | null>(null);
  useEffect(() => {
    if (!enabled || step >= STEP_KEYS.length) return;
    let current = true;
    const timer = window.setTimeout(async () => {
      try {
        const checks = await checkMonthlyAssignment(JSON.parse(snapshot));
        const keys = STEP_KEYS[step];
        if (!Array.isArray(checks) || keys.some(key => checks.filter(c => c?.key === key && typeof c.passed === 'boolean' && typeof c.label === 'string').length !== 1)) {
          throw new Error('Could not verify this step. Please retry.');
        }
        if (current) setResult({ snapshot, step, checks: checks.filter(c => keys.includes(c.key)), error: '' });
      } catch {
        if (current) setResult({ snapshot, step, checks: [], error: 'Could not verify this step. Your draft is safe. Please retry.' });
      }
    }, 400);
    return () => { current = false; window.clearTimeout(timer); };
  }, [snapshot, step, enabled, retry]);
  const active = result?.snapshot === snapshot && result.step === step ? result : null;
  const missing = active?.checks.filter(c => !c.passed) || [];
  return {
    ready: !enabled || step >= STEP_KEYS.length || (!!active && !active.error && !missing.length),
    pending: enabled && step < STEP_KEYS.length && !active,
    missing, error: active?.error || '',
    retry: () => { setResult(null); setRetry(value => value + 1); },
  };
}
