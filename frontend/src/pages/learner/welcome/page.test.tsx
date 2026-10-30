/**
 * The first-sign-in screens: details, then signature, saved together on Finish,
 * then on to the enrolment wizard.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { ReactNode } from 'react';

const account = { role: 'learner', learnerType: 'apprenticeship', subjectId: 41, displayName: 'Test Learner' };
vi.mock("@/hooks/useAuth.helpers", () => ({ useAuth: () => ({ auth: { account } }) }));
vi.mock('@/components/feature/WorkspaceShell', () => ({
  WorkspaceShell: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));
vi.mock('@/lib/typedSignature', () => ({
  createTypedSignature: vi.fn(async () => 'data:image/png;base64,TYPED'),
}));
// jsdom has no canvas; the Draw tab is not what these tests exercise.
vi.mock('signature_pad', () => ({
  default: class {
    addEventListener() {}
    removeEventListener() {}
    off() {}
    clear() {}
    fromData() {}
    isEmpty() { return true; }
    toData() { return []; }
    toDataURL() { return ''; }
  },
}));
const syncLearnerStatus = vi.fn();
vi.mock('@/hooks/useLearnerNavGate', () => ({
  syncLearnerStatus: (...args: unknown[]) => syncLearnerStatus(...args),
}));

const fetchFirstLoginDetails = vi.fn();
const submitFirstLoginDetails = vi.fn();
vi.mock('@/api/firstLoginDetails', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/firstLoginDetails')>()),
  fetchFirstLoginDetails: (...args: unknown[]) => fetchFirstLoginDetails(...args),
  submitFirstLoginDetails: (...args: unknown[]) => submitFirstLoginDetails(...args),
}));

import LearnerWelcomePage from './page';

const EMPTY_DETAILS = {
  title: '', dateOfBirth: '', phone: '', country: 'United Kingdom', postcode: '',
  addressLine1: '', addressLine2: '', townCity: '', county: '',
};

function state(overrides: Record<string, unknown> = {}) {
  return {
    required: true, completedAt: null, signatoryName: 'Test Learner', details: EMPTY_DETAILS,
    hasSavedSignature: false, programmeStatus: 'Fresh user', csrfToken: 'csrf-token', ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/learner/welcome']}>
      <Routes>
        <Route path="/learner/welcome" element={<LearnerWelcomePage />} />
        <Route path="/learner/onboarding" element={<h1>Enrolment wizard</h1>} />
        <Route path="/learner/home" element={<h1>Student home</h1>} />
      </Routes>
    </MemoryRouter>,
  );
}

async function fillDetails(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText(/^Postcode/), 'ct1 1aa');
  await user.type(screen.getByLabelText(/^Address line 1/), '1 High Street');
  await user.type(screen.getByLabelText(/^Town or city/), 'Canterbury');
  await user.selectOptions(screen.getByLabelText(/^Title/), 'Ms');
  await user.type(screen.getByLabelText(/^Date of Birth/), '2000-05-17');
  await user.type(screen.getByLabelText(/^Mobile Number/), '07700 900123');
}

beforeEach(() => {
  vi.clearAllMocks();
  fetchFirstLoginDetails.mockResolvedValue(state());
});

describe('LearnerWelcomePage', () => {
  it('keeps the learner on the details screen until the required answers are given', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole('button', { name: /next/i }));

    expect(screen.getByText('Choose your title.')).toBeInTheDocument();
    expect(screen.getByText('Enter a valid UK postcode, for example CT1 1AA.')).toBeInTheDocument();
    expect(screen.getByText('Enter the first line of your address.')).toBeInTheDocument();
    expect(screen.getByText('Enter your mobile number.')).toBeInTheDocument();
    expect(screen.queryByText('Electronic signature declaration agreement')).not.toBeInTheDocument();
  });

  it('requires a signature before saving anything', async () => {
    const user = userEvent.setup();
    renderPage();
    await fillDetails(user);
    await user.click(screen.getByRole('button', { name: /next/i }));

    await user.click(await screen.findByRole('button', { name: /finish/i }));

    expect(screen.getByText('Your signature is required.')).toBeInTheDocument();
    expect(submitFirstLoginDetails).not.toHaveBeenCalled();
  });

  it('saves both screens together and opens the enrolment wizard', async () => {
    submitFirstLoginDetails.mockResolvedValue(state({ required: false, programmeStatus: 'Onboarding' }));
    const user = userEvent.setup();
    renderPage();
    await fillDetails(user);
    await user.click(screen.getByRole('button', { name: /next/i }));

    await user.click(await screen.findByRole('tab', { name: /choose/i }));
    await user.click(await screen.findByRole('button', { name: /signature of test learner/i }));
    await user.click(screen.getByRole('button', { name: /finish/i }));

    expect(await screen.findByRole('heading', { name: 'Enrolment wizard' })).toBeInTheDocument();
    expect(submitFirstLoginDetails).toHaveBeenCalledWith(
      '41',
      expect.objectContaining({
        title: 'Ms', dateOfBirth: '2000-05-17', phone: '07700 900123', country: 'United Kingdom',
        postcode: 'ct1 1aa', addressLine1: '1 High Street', townCity: 'Canterbury',
      }),
      'data:image/png;base64,TYPED',
      'csrf-token',
    );
    expect(syncLearnerStatus).toHaveBeenCalledWith('apprenticeship', '41', 'Onboarding');
  });

  it('keeps the answers when going back from the signature screen', async () => {
    const user = userEvent.setup();
    renderPage();
    await fillDetails(user);
    await user.click(screen.getByRole('button', { name: /next/i }));

    await user.click(await screen.findByRole('button', { name: /back/i }));

    expect(screen.getByLabelText(/^Address line 1/)).toHaveValue('1 High Street');
    expect(screen.getByLabelText(/^Title/)).toHaveValue('Ms');
  });

  it('sends a learner who has already finished on to their enrolment', async () => {
    fetchFirstLoginDetails.mockResolvedValue(state({ required: false, programmeStatus: 'Onboarding' }));
    renderPage();

    expect(await screen.findByRole('heading', { name: 'Enrolment wizard' })).toBeInTheDocument();
  });

  it('prefills what the enrolment team already holds', async () => {
    fetchFirstLoginDetails.mockResolvedValue(state({
      details: { ...EMPTY_DETAILS, phone: '07700 900999', addressLine1: '2 Castle Row' },
    }));
    renderPage();

    expect(await screen.findByLabelText(/^Mobile Number/)).toHaveValue('07700 900999');
    expect(screen.getByLabelText(/^Address line 1/)).toHaveValue('2 Castle Row');
  });

  it('marks every required answer with a star', async () => {
    renderPage();

    for (const label of [/^Country/, /^Postcode/, /^Title/, /^Date of Birth/, /^Mobile Number/, /^Address line 1/, /^Town or city/]) {
      expect(await screen.findByLabelText(label)).toBeInTheDocument();
      expect(screen.getByLabelText(label).closest('div')?.parentElement?.textContent ?? '').toMatch(/\*|required/);
    }
    expect(screen.getByLabelText(/^Address line 2/)).toHaveAccessibleName('Address line 2 (optional)');
    expect(screen.getByLabelText(/^Title/)).toHaveAccessibleName(/required/);
  });

  it('offers an upload option and refuses a file that is not an image', async () => {
    const user = userEvent.setup({ applyAccept: false });
    renderPage();
    await fillDetails(user);
    await user.click(screen.getByRole('button', { name: /next/i }));

    await user.click(await screen.findByRole('tab', { name: /upload/i }));
    await user.upload(screen.getByLabelText(/upload a picture of your signature/i), new File(['x'], 'notes.pdf', { type: 'application/pdf' }));

    expect(await screen.findByText('Upload your signature as a PNG, JPEG or WebP image.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /finish/i }));
    expect(submitFirstLoginDetails).not.toHaveBeenCalled();
  });
});
