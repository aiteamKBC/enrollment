import { useMemo, type ReactNode } from 'react';
import { caseFileSectionRead } from '../api/caseFileApi';
import { peekCaseFileCache } from '../cache/caseFileCache';
import { useCoachViewAs } from '@/hooks/useCoachViewAs';

import { CaseFileContext, type CaseFileSession } from './caseFileSessionContext';

export function CaseFileSessionProvider({ learnerId, coach, children }: { learnerId: string; coach: string; children: ReactNode }) {
  const selected = useCoachViewAs();
  const scope = JSON.stringify([coach.toLowerCase(), selected?.adminEmail || '', selected?.email || '']);
  const session = useMemo<CaseFileSession>(() => {
    const read: CaseFileSession['read'] = (section, params, options) => caseFileSectionRead(scope, learnerId, section, params, options);
    const peekWeeklyLearning: CaseFileSession['peekWeeklyLearning'] = week => {
      const query = week ? `?${new URLSearchParams({ week })}` : '';
      return peekCaseFileCache(scope, learnerId, 'weekly-learning', `/coach_api/coach/case-file/${encodeURIComponent(learnerId)}/weekly-learning${query}`);
    };
    return { learnerId, read, peekWeeklyLearning, detail: refresh => read('learning-plan', { resource: 'detail' }, { refresh }) };
  }, [learnerId, scope]);
  return <CaseFileContext.Provider key={`${scope}:${learnerId}`} value={session}>{children}</CaseFileContext.Provider>;
}

