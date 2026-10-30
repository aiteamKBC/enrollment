import { useMemo } from 'react';
import type { LearnerDetail } from '@/api/learnerDetail';
import {
  buildKsbProgress, completedComponentIds, ksbParentCode, recordedKsbEvidenceCodes, type KsbProgress,
} from '@/utils/learnerJourney';

/**
 * The weighted KSB progress array, shared by the full KSB page, the compact
 * "My Progress" overview tab, and the employer portal — one derivation so
 * every surface agrees on which KSBs count as complete.
 */
export function useKsbProgress(real: LearnerDetail | null): KsbProgress[] {
  // Active learners created before the normalized KSB-profile sync can have an
  // empty real.ksbs array even though their live component mappings and saved
  // completion records contain genuine KSB codes. Merge all three live sources
  // so this detail page agrees with the learner overview instead of showing 0/0.
  const programmeKsbs = useMemo(() => {
    const byCode = new Map<string, { code: string; type: string; description: string }>();
    const add = (rawCode: string | null | undefined, description = '', rawType = '') => {
      const code = ksbParentCode(String(rawCode || ''));
      if (!code) return;
      const current = byCode.get(code);
      byCode.set(code, {
        code,
        type: rawType || current?.type || code.charAt(0),
        description: description || current?.description || '',
      });
    };

    for (const ksb of real?.ksbs || []) add(ksb.code, ksb.description, ksb.type);
    for (const component of real?.components || []) {
      for (const mapping of component.ksbMappings || []) add(mapping.code, mapping.description || '');
    }
    for (const activity of [
      ...(real?.quizAttempts || []),
      ...(real?.videoProgress || []),
      ...(real?.componentProgress || []),
    ]) {
      for (const code of activity.ksbs || []) add(code);
    }

    return Array.from(byCode.values()).sort((a, b) => a.code.localeCompare(b.code, undefined, { numeric: true }));
  }, [real]);

  const completedIds = useMemo(() => {
    const ids = completedComponentIds(real);
    const passedQuizIds = new Set(
      (real?.quizAttempts || []).filter((attempt) => attempt.passed).map((attempt) => attempt.quizId),
    );
    for (const component of real?.components || []) {
      if (component.componentId && component.quizMeta?.quizId && passedQuizIds.has(component.quizMeta.quizId)) {
        ids.add(component.componentId);
      }
    }
    return ids;
  }, [real]);

  const evidencedCodes = useMemo(() => recordedKsbEvidenceCodes(real), [real]);

  return useMemo(() => buildKsbProgress({
    ksbs: programmeKsbs,
    components: real?.components ?? [],
    completedComponentIds: completedIds,
    evidencedKsbCodes: evidencedCodes,
  }), [real, programmeKsbs, completedIds, evidencedCodes]);
}
