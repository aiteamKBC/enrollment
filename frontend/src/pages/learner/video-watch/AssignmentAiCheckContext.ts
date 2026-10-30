import { createContext } from 'react';
import type { LearnerKind } from '@/api/learnerDetail';

export const AssignmentAiCheckContext = createContext<{ learnerId: string; learnerKind: LearnerKind; enabled: boolean } | null>(null);
