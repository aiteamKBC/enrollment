import { createContext, useContext } from 'react';
import type { CaseFileReadOptions } from '../api/caseFileApi';
import type { LearnerDetail } from '@/api/learnerDetail';

export type CaseFileSession = {
  learnerId: string;
  peekWeeklyLearning?: <T>(week?: string) => T | undefined;
  read: <T>(section: string, params?: Record<string, string>, options?: CaseFileReadOptions) => Promise<T>;
  detail: (refresh?: boolean) => Promise<LearnerDetail>;
};
export const CaseFileContext = createContext<CaseFileSession | null>(null);

export function useCaseFileSession() { return useContext(CaseFileContext); }
