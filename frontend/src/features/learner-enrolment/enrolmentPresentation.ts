import { createContext, useContext } from 'react';
import { parseRich, type RichBlock } from '@/pages/users/wizard/layout/richFormat';

export const LearnerEnrolmentVisualContext = createContext(false);
export const useLearnerEnrolmentVisuals = () => useContext(LearnerEnrolmentVisualContext);
export const enrolmentAsset = (path: string) => `${import.meta.env.BASE_URL}${path}`;

/** Group existing rich blocks by structure, never by their wording. Every block
 * remains in order and uses the same safe rich-text renderer as the original. */
export function welcomeSections(source: string) {
  const blocks = parseRich(source), sections: RichBlock[][] = [];
  blocks.forEach((block, i) => {
    const boldListLead = block.type === 'paragraph' && block.children.length > 0 && block.children.every(n => n.type === 'bold') && blocks[i + 1]?.type === 'list';
    if (!sections.length || block.type === 'heading' || boldListLead) sections.push([]);
    sections.at(-1)!.push(block);
  });
  const lead = sections.shift() || [], conclusion: RichBlock[] = [];
  const last = sections.at(-1);
  if (last && last.length > 1 && last.at(-1)?.type === 'paragraph' && last.some(b => b.type === 'list')) conclusion.push(last.pop()!);
  return { lead, cards: sections, conclusion };
}
