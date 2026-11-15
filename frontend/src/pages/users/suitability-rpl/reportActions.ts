import type { SuitabilityRplReport } from '@/api/suitabilityRplReport';

export function reportIssueButtonLabel(locked: boolean, issued: boolean, working: boolean) {
  if (!locked) return null;
  if (working) return 'Issuing…';
  return issued ? 'Already issued' : 'Issue finalised report';
}

export function shouldShowDraftDownload(status: SuitabilityRplReport['status'] | null) {
  return status !== 'finalised';
}
