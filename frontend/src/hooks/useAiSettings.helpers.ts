import { createContext, useContext } from 'react';
import type { AiSettings } from '@/mocks/ai-settings';


// ============================================================
// Types
// ============================================================

export type AiFeatureSlug =
  | 'proofreading'
  | 'reflection-quality'
  | 'ksb-suggestions'
  | 'evidence-checker'
  | 'revision-suggestions'
  | 'marking-suggestions'
  | 'otjh-risk'
  | 'coaching-summaries'
  | 'progress-review-drafts'
  | 'coaching-agenda'
  | 'report-summaries'
  | 'employer-summaries'
  | 'ofsted-summaries'
  | 'risk-patterns'
  | 'sar-qip'
  | 'quiz-generation'
  | 'xml-quiz';


export interface AiSettingsContextValue {
  /** The full AI settings configuration for the current tenant */
  settings: AiSettings;
  /** Is the master AI switch enabled at tenant level? */
  isAiGloballyEnabled: boolean;
  /** Is the user's session AI mode toggled on? */
  isUserAiOn: boolean;
  /** Combined: is AI actually available right now? (tenant enabled AND user toggled on) */
  isAiActive: boolean;
  /** Update the full settings object (admin use) */
  updateSettings: (partial: Partial<AiSettings>) => void;
  /** Toggle the master AI switch */
  toggleMasterAi: () => void;
  /** Toggle a specific AI feature on/off */
  toggleFeature: (slug: AiFeatureSlug) => void;
  /** Set user session AI mode */
  setUserAiMode: (on: boolean) => void;
  /** Check if a specific AI feature is currently usable */
  isFeatureUsable: (slug: AiFeatureSlug) => boolean;
  /** Get the label for an AI feature */
  getFeatureLabel: (slug: AiFeatureSlug) => string;
  /** Get the never-allowed actions list */
  neverAllowedActions: string[];
  /** Is the require-human-approval rule active? */
  requireHumanApproval: boolean;
}


// ============================================================
// Context
// ============================================================

export const AiSettingsContext = createContext<AiSettingsContextValue | null>(null);


// ============================================================
// Hook
// ============================================================

export function useAiSettings(): AiSettingsContextValue {
  const ctx = useContext(AiSettingsContext);
  if (!ctx) {
    throw new Error('useAiSettings must be used within an AiSettingsProvider');
  }
  return ctx;
}
