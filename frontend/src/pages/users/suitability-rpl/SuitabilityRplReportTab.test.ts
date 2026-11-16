import { describe, expect, it } from 'vitest';
import { reportIssueButtonLabel, shouldShowDraftDownload } from './reportActions';

describe('Suitability & RPL report actions', () => {
  it('uses an immutable issued state label while issuing remains visible for finalised drafts', () => {
    expect(reportIssueButtonLabel(true, true, false)).toBe('Already issued');
    expect(reportIssueButtonLabel(true, false, false)).toBe('Issue finalised report');
    expect(reportIssueButtonLabel(true, false, true)).toBe('Issuing…');
  });

  it('only offers draft download before finalisation', () => {
    expect(shouldShowDraftDownload('draft')).toBe(true);
    expect(shouldShowDraftDownload(null)).toBe(true);
    expect(shouldShowDraftDownload('finalised')).toBe(false);
  });
});
