/**
 * Compliance documents — pressing Sign opens the signature box and brings it
 * into view: the workspace hides its scrollbars, so a box that opened below the
 * fold looked as if the Sign button had simply vanished.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock("@/hooks/useAuth.helpers", () => ({
  useAuth: () => ({ auth: { user: { fullName: 'Test Learner' }, account: { role: 'learner', subjectType: 'learner', subjectId: 670 } } }),
}));
vi.mock('@/api/savedSignature', () => ({
  fetchSavedSignature: vi.fn(async () => ({ signature: '', savedAt: '' })),
  saveSignature: vi.fn(),
}));

import { DocumentCard } from '../DocumentCard';

const unsigned = { name: '', signed: false, signedAt: null };
const scrollIntoView = vi.fn();
beforeEach(() => {
  vi.clearAllMocks();
  Element.prototype.scrollIntoView = scrollIntoView;
});

function renderCard() {
  render(
    <DocumentCard
      title="Written Agreement" blurb="" signedBy="You, your employer and your training provider"
      issued fullySigned={false} learner={unsigned} other={unsigned} otherLabel="Employer"
      third={unsigned} thirdLabel="Provider" fields={null} signatoryName="Test Learner" busy={false}
      onPreview={vi.fn()} onDownload={vi.fn()} onSign={vi.fn()} fmtDate={() => ''}
    />,
  );
}

describe('compliance document signing', () => {
  it('opens the signature box when Sign is pressed', async () => {
    renderCard();
    await userEvent.click(screen.getByRole('button', { name: /Sign/ }));

    expect(screen.getByText('Sign below to confirm you agree to these details.')).toBeInTheDocument();
    expect(screen.getByText('Signing as')).toBeInTheDocument();
  });

  it('scrolls the signature box into view', async () => {
    renderCard();
    await userEvent.click(screen.getByRole('button', { name: /Sign/ }));

    expect(scrollIntoView).toHaveBeenCalled();
  });
});
