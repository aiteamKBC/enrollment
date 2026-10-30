import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import type { MonthlyAssignment } from '@/api/monthlyAssignment';

export function useAssignedKsbExplanations(enabled: boolean, context: {
  learnerId: string; learnerKind: string; activityId: string; question: string; answer: string;
  mappings: { code: string; description?: string | null }[];
}, data: MonthlyAssignment, onChange: Dispatch<SetStateAction<MonthlyAssignment>>) {
  const [status, setStatus] = useState('');
  const latest = useRef({ data, onChange });
  latest.current = { data, onChange };
  const key = JSON.stringify({ ...context, evidence: data.evidence });
  useEffect(() => {
    setStatus('');
    if (!enabled) return;
    const input = JSON.parse(key);
    const targets = input.mappings.filter((m: { code: string }) => latest.current.data.claims.some(c => c.code === m.code && !c.explanation.trim()));
    if (!targets.length) return;
    if (!input.answer.trim()) { setStatus('Write your assignment answer first to draft assigned KSB explanations.'); return; }
    const controller = new AbortController();
    setStatus('Drafting your assigned KSB explanations from your answer, with any available readable evidence…');
    void (async () => {
      try {
        const response = await fetch('/learner_api/reflection/ksb-explanations/', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
          body: JSON.stringify({ ...input, mappings: targets }),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Could not draft KSB explanations. You can write them yourself.');
        if (controller.signal.aborted) return;
        latest.current.onChange(current => ({ ...current, claims: current.claims.map(claim => {
          if (claim.explanation.trim() || !targets.some((m: { code: string }) => m.code === claim.code)) return claim;
          const draft = result.claims.find((c: { code: string }) => c.code === claim.code);
          if (!draft?.explanation?.trim()) return claim;
          return { ...claim, explanation: draft.explanation, evidenceIds: claim.evidenceIds.length ? claim.evidenceIds : draft.evidenceIds.filter((id: string) => current.evidence.some(e => e.id === id)) };
        }) }));
        setStatus('Drafts are ready to review. Check the explanations and supporting evidence. Any fields left blank need more details from you. ' + (result.notices || []).join(' '));
      } catch (error) {
        if (!controller.signal.aborted) setStatus(error instanceof Error ? error.message : 'Generation failed. You can write the explanations yourself.');
      }
    })();
    return () => controller.abort();
  }, [enabled, key]);
  return status;
}
