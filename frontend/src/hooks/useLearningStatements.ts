import { useEffect, useRef, useState } from 'react';

export type LearningStatements = { whatYouLearned: string; understood: string; gainedSkills: string };

export function useLearningStatements(answer: string, learnerId: string, learnerKind: string, activityId: string, enabled: boolean,
  current: LearningStatements, apply: (result: Partial<LearningStatements>) => void) {
  const [status, setStatus] = useState('');
  const [phase, setPhase] = useState<'loading' | 'success' | 'error'>('loading');
  const latest = useRef({ current, apply });
  latest.current = { current, apply };
  const requestRef = useRef<AbortController | null>(null);
  const [generating, setGenerating] = useState(false);
  useEffect(() => {
    requestRef.current?.abort(); requestRef.current = null;
    setGenerating(false); setStatus('');
    return () => { requestRef.current?.abort(); requestRef.current = null; };
  }, [answer, learnerId, learnerKind, activityId, enabled]);
  const canGenerate = enabled && answer.trim().split(/\s+/).filter(Boolean).length >= 120 && !generating;
  const generate = async () => {
    if (!canGenerate || requestRef.current) return;
    const snapshot = { ...latest.current.current };
    const controller = new AbortController();
    requestRef.current = controller;
    setGenerating(true); setPhase('loading');
    setStatus('Generating learning statements from your answer?');
    try {
      const response = await fetch('/learner_api/reflection/learning-statements/', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
        body: JSON.stringify({ text: answer, learnerId, learnerKind, activityId }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Generation failed.');
      if (controller.signal.aborted) return;
      const update: Partial<LearningStatements> = {};
      for (const field of Object.keys(snapshot) as (keyof LearningStatements)[]) {
        if (typeof result[field] === 'string' && latest.current.current[field] === snapshot[field]) update[field] = result[field];
      }
      latest.current.apply(update);
      setPhase('success');
      setStatus('Learning statements updated. Review them and add any missing details from your own experience.');
    } catch (error) {
      if (!controller.signal.aborted) {
        setPhase('error');
        setStatus(error instanceof Error ? error.message : 'Generation failed. You can write the statements yourself.');
      }
    } finally {
      if (requestRef.current === controller) { requestRef.current = null; setGenerating(false); }
    }
  };
  return { status, phase, generating, canGenerate, generate };
}
