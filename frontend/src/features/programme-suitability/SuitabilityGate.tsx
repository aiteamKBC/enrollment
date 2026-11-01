import { forwardRef, useImperativeHandle, useRef, useState } from 'react';
import type { LearnerKind } from '@/api/extendedIlr';
import { getAssessment } from './api';
import { AssessmentDialog } from './AssessmentDialog';
import { selectionConfirmed } from './useSuitability';
import type { AssessmentState } from './types';

export interface SuitabilityContinuation { open: (after: () => void) => Promise<boolean> }

export const SuitabilityGate = forwardRef<SuitabilityContinuation, { kind: LearnerKind; learnerId: string }>(function SuitabilityGate({ kind, learnerId }, ref) {
  const [state, setState] = useState<AssessmentState | null>(null);
  const after = useRef<() => void>(() => {});
  useImperativeHandle(ref, () => ({ open: async (onConfirmed) => {
    const saved = await getAssessment(kind, learnerId);
    if (!saved.available || selectionConfirmed(saved)) return false;
    after.current = onConfirmed;
    setState(saved);
    return true;
  } }), [kind, learnerId]);
  return state && <AssessmentDialog initial={state} kind={kind} learnerId={learnerId}
    onClose={() => setState(null)} onConfirmed={() => { setState(null); after.current(); }} />;
});
