import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import SuitabilityRplReportPage from './page';

const state = vi.hoisted(() => ({
  role: 'admin',
  create: vi.fn(),
  save: vi.fn(),
}));

vi.mock('@/hooks/useAuth.helpers', () => ({
  useAuth: () => ({ auth: { account: { role: state.role, displayName: 'Admin User' } } }),
}));

vi.mock('@/components/feature/WorkspaceShell', () => ({
  WorkspaceShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('@/lib/suitabilityRplReportPdf', () => ({
  suitabilityRplReportFilename: 'Suitability-and-RPL-Blank-Template.pdf',
  createBlankSuitabilityRplReportPdf: state.create,
}));

describe('Suitability & RPL report admin page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.role = 'admin';
    state.create.mockResolvedValue({
      output: () => new Blob(['%PDF-1.4'], { type: 'application/pdf' }),
      save: state.save,
    });
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:report-preview') });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
  });

  it('previews and downloads the generated blank report for an administrator', async () => {
    render(<SuitabilityRplReportPage />);
    expect(await screen.findByTitle('Suitability and Recognition of Prior Learning blank report preview')).toHaveAttribute('src', 'blob:report-preview#view=FitH');
    fireEvent.click(screen.getByRole('button', { name: 'Download blank PDF' }));
    await waitFor(() => expect(state.save).toHaveBeenCalledWith('Suitability-and-RPL-Blank-Template.pdf'));
    expect(screen.getByText('No learner information')).toBeInTheDocument();
  });

  it('fails closed when the component is reached by a non-admin account', () => {
    state.role = 'staff';
    render(<SuitabilityRplReportPage />);
    expect(screen.getByRole('alert')).toHaveTextContent('Administrator access is required');
    expect(state.create).not.toHaveBeenCalled();
  });
});
