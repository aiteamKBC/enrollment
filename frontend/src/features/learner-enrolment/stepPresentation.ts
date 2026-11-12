import { createContext, useContext } from 'react';
import { parseRich, type RichBlock } from '@/pages/users/wizard/layout/richFormat';

/** Set only around the eight learner content areas, never the shared shell. */
export const LearnerStepPresentationContext = createContext<string | null>(null);
export const useLearnerStepPresentation = () => useContext(LearnerStepPresentationContext);

/** Group published rich text without adding, dropping or rewriting any block. */
export function informationSections(source: string): RichBlock[][] {
  const sections: RichBlock[][] = [];
  for (const block of parseRich(source)) {
    if (!sections.length || block.type === 'heading') sections.push([]);
    sections[sections.length - 1].push(block);
  }
  return sections;
}
