import { canPreviewIlrStep } from '@/lib/localIlrPreview';

export function shouldRedirectLearnerWizardStep({
  ready,
  currentIndex,
  reachable,
  stepSlug,
}: {
  ready: boolean;
  currentIndex: number;
  reachable: number;
  stepSlug?: string;
}): boolean {
  return ready && currentIndex > reachable && !canPreviewIlrStep(stepSlug);
}
