import { useWizard } from "../WizardContext.helpers";
import { BUILTIN_STEP_BY_SLUG } from './registry';
import { DEFAULT_LAYOUT } from './resolve';

/** The wizard's layout — the default one where a provider supplies none. */
export function useLayout() {
  return useWizard().layout ?? DEFAULT_LAYOUT;
}

/** The step's heading: the label the builder gave it, else the step's own title. */
export function useStepTitle(slug: string, fallback: string): string {
  const layout = useLayout();
  const step = layout.steps.find((s) => s.slug === slug);
  const builtinLabel = BUILTIN_STEP_BY_SLUG[slug]?.label;
  return step && step.label && step.label !== builtinLabel ? step.label : fallback;
}
