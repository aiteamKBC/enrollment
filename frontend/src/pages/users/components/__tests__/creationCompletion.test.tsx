import * as React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CreateUserModal } from '../CreateUserModal';
import { CreateEmployerModal } from '../CreateEmployerModal';

const mocks = vi.hoisted(() => ({ createLearner: vi.fn(), createEmployer: vi.fn(), success: vi.fn(), error: vi.fn() }));
vi.mock('@/api/enrolmentUsers', async () => ({ ...await vi.importActual('@/api/enrolmentUsers'), createEnrolmentUser: mocks.createLearner }));
vi.mock('@/api/curriculum', () => ({ fetchProgrammes: async () => [], fetchCohorts: async () => [], fetchGroups: async () => [] }));
vi.mock('@/api/staffUsers', () => ({ fetchCaseOwners: async () => ['Test Coach'] }));
vi.mock('@/api/employers', () => ({ createEmployer: mocks.createEmployer, listEmployers: async () => ({ results: [] }),
  listOrganisations: async () => ({ results: [{ id: 'org-1', name: 'Test organisation' }], count: 1 }) }));
vi.mock("@/hooks/useToast.helpers", () => ({ useToast: () => ({ success: mocks.success, error: mocks.error }) }));
vi.mock('../Modal', () => ({ Modal: ({ children, footer }: { children: React.ReactNode; footer: React.ReactNode }) => <div>{children}{footer}</div> }));

beforeEach(() => {
  vi.clearAllMocks(); vi.stubGlobal('React', React); vi.stubGlobal('AppIcon', () => null);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('successful creation awaiting a manual invitation', () => {
  it.each(['learner', 'employer'])('reports the saved %s and updates the directory instead of showing an error', async kind => {
    const row = { id: '125', name: 'New Person', invitation: { awaitingInvitation: true, invited: false, emailSent: false } };
    mocks.createLearner.mockResolvedValue(row); mocks.createEmployer.mockResolvedValue(row);
    const onCreated = vi.fn(); const onClose = vi.fn();
    render(kind === 'learner' ? <CreateUserModal onCreated={onCreated} onClose={onClose} /> : <CreateEmployerModal onCreated={onCreated} onClose={onClose} />);
    fireEvent.change(screen.getByLabelText(/^First name/), { target: { value: 'New' } });
    fireEvent.change(screen.getByLabelText(/^Surname/), { target: { value: 'Person' } });
    fireEvent.change(screen.getByLabelText(/^Email/), { target: { value: 'new@example.test' } });
    if (kind === 'employer') fireEvent.click(await screen.findByRole('checkbox', { name: 'Select Test organisation' }));
    if (kind === 'learner') {
      // A learner is now enrolled with a case owner and a booked first session,
      // so the form cannot be submitted without them.
      // The first session is no longer arranged here: the learner books it
      // themselves after signing in. A case owner is still required, because
      // that is who they will book with.
      fireEvent.change(await screen.findByLabelText(/^Case owner/), { target: { value: 'Test Coach' } });
    }
    fireEvent.click(screen.getByRole('button', { name: /^Create/ }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(row));
    expect(onClose).toHaveBeenCalledOnce();
    expect(mocks.error).not.toHaveBeenCalled();
    expect(mocks.success).toHaveBeenCalledWith(expect.stringContaining(kind === 'learner' ? 'learner' : 'Employer'), expect.stringContaining('Send their invitation from Accounts'));
  });
});
