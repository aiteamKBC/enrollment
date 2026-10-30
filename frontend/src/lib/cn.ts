// ============================================================================
// Class name merge.
//
// `clsx` resolves the conditionals, `twMerge` resolves the conflicts: when a
// caller passes `className="p-6"` to a component whose base is `p-4`, the
// caller wins instead of both landing in the attribute and letting stylesheet
// order decide. Every shared component takes a `className` for exactly this
// reason, so it needs to be the last word.
// ============================================================================
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
