import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DocumentPreview } from '../DocumentPreview';
import { payloadFor } from './fixtures';

vi.mock('../previewPdf', () => ({ previewPdf: () => new Promise(() => {}) }));
vi.mock('@/pages/users/components/Modal', () => ({ Modal: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));

describe('document preview diagnostics', () => {
  it('hides technical diagnostics by default and retains an explicit admin display', () => {
    const value = { ...payloadFor('training-plan', true), sample: false, notice: 'Review the document before signing.',
      missingFields: [], bindingWarnings: [{ field: 'source', code: 'select_denied', message: 'LMS curriculum.groups: select denied' }] };
    const { rerender } = render(<DocumentPreview value={value} onClose={() => {}} />);
    expect(screen.queryByText(/select denied/)).not.toBeInTheDocument();
    rerender(<DocumentPreview showDiagnostics value={value} onClose={() => {}} />);
    expect(screen.getByText(/select denied/)).toBeInTheDocument();
  });
});
