import { canPreviewIlrStep } from '@/lib/localIlrPreview';
import { canPreviewFinalEnrolmentStep } from '@/lib/localEnrolmentDesignPreview';

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
  return ready && currentIndex > reachable && !canPreviewIlrStep(stepSlug) && !canPreviewFinalEnrolmentStep(stepSlug);
}
