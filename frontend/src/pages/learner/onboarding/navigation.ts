export function shouldRedirectLearnerWizardStep({
  ready,
  currentIndex,
  reachable,
}: {
  ready: boolean;
  currentIndex: number;
  reachable: number;
}): boolean {
  return ready && currentIndex > reachable;
}
