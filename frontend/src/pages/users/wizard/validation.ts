/**
 * Wizard completeness rules — every step must be filled before the enrolment
 * can be submitted.
 *
 * "Required" here means *everything the learner can actually answer*. Purely
 * conditional follow-ups are skipped when their trigger answer wasn't given
 * (asking a UK national for a work permit is a dead end), and the provider
 * declaration is excluded because staff countersign it later — a learner cannot
 * sign it themselves, so requiring it would make submission impossible.
 *
 * The rules themselves live with each item in layout/registry.ts; which items
 * are asked, and whether each is required, comes from the published layout
 * (the wizard builder). With no layout given, the default layout applies — the
 * wizard exactly as it was before the builder existed.
 *
 * Steps are validated by index into the layout's visible steps (for the
 * default layout, the same order as WIZARD_STEPS).
 */
import { ILR_SIGNATURE_LABEL } from './layout/registry';
import { DEFAULT_LAYOUT, missingForLayoutStep, visibleSteps } from './layout/resolve';
import type { WizardLayout } from './layout/types';
import type { WizardDraft } from '../types';

export { ILR_SIGNATURE_LABEL };

/** One unfilled field, named so the UI can point the learner straight at it. */
export interface MissingField {
  /** Index into the layout's visible steps. */
  stepIndex: number;
  /** Human label, as shown on the form. */
  label: string;
}

/** The ILR Learner Details step's outstanding answers. */
export function ilrDetailsMissing(d: WizardDraft, layout: WizardLayout = DEFAULT_LAYOUT): string[] {
  const step = layout.steps.find((s) => s.slug === 'ilr-details');
  return step ? missingForLayoutStep(step, d, layout) : [];
}

/**
 * Unfilled fields for one step, by index into the layout's visible steps.
 * Matched on the step's own items rather than its position, so reordering the
 * steps cannot shift which rules apply to which step. Steps of read-only
 * content (Introduction, Before You Begin, Next Steps) are always complete.
 */
export function missingForStep(stepIndex: number, draft: WizardDraft, layout: WizardLayout = DEFAULT_LAYOUT): string[] {
  const step = visibleSteps(layout)[stepIndex];
  return step ? missingForLayoutStep(step, draft, layout) : [];
}

export function isStepComplete(stepIndex: number, draft: WizardDraft, layout: WizardLayout = DEFAULT_LAYOUT): boolean {
  return missingForStep(stepIndex, draft, layout).length === 0;
}

/**
 * First step that still has unfilled fields, or -1 when the whole wizard is
 * complete. Drives the learner's step gating: they may not run ahead of it.
 */
export function firstIncompleteStep(draft: WizardDraft, stepCount: number, layout: WizardLayout = DEFAULT_LAYOUT): number {
  for (let i = 0; i < stepCount; i++) {
    if (!isStepComplete(i, draft, layout)) return i;
  }
  return -1;
}

/**
 * Furthest step a learner is allowed to open: the first incomplete one (they
 * must be able to reach the step they still have to fill in), or the last step
 * once nothing is outstanding. Steps behind it stay open for review/editing.
 */
export function maxReachableStep(draft: WizardDraft, stepCount: number, layout: WizardLayout = DEFAULT_LAYOUT): number {
  const gap = firstIncompleteStep(draft, stepCount, layout);
  return gap === -1 ? stepCount - 1 : gap;
}

/** Every unfilled field across the whole wizard, in step order. */
export function missingAcrossWizard(draft: WizardDraft, stepCount: number, layout: WizardLayout = DEFAULT_LAYOUT): MissingField[] {
  const out: MissingField[] = [];
  for (let i = 0; i < stepCount; i++) {
    for (const label of missingForStep(i, draft, layout)) out.push({ stepIndex: i, label });
  }
  return out;
}
