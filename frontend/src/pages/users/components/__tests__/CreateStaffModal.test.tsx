/** Staff creation must grant exactly the roles selected by the administrator. */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const createStaffUser = vi.fn();
const toastSuccess = vi.fn();
const toastError = vi.fn();

vi.mock('@/api/staffUsers', async () => {
  const actual = await vi.importActual<typeof import('@/api/staffUsers')>('@/api/staffUsers');
  return { ...actual, createStaffUser: (...args: unknown[]) => createStaffUser(...args) };
});

vi.mock("@/hooks/useToast.helpers", () => ({
  useToast: () => ({ success: toastSuccess, error: toastError }),
}));

const { CreateStaffModal } = await import('../CreateStaffModal');

beforeEach(() => {
  createStaffUser.mockReset();
  createStaffUser.mockResolvedValue({
    name: 'Ada Lovelace',
    invitation: { invited: true, emailSent: true },
  });
  toastSuccess.mockClear();
  toastError.mockClear();
});

/** Fill the four required fields with a valid, matching email. */
async function fillRequired(user: ReturnType<typeof userEvent.setup>, email = 'ada@kbc.test') {
  await user.type(screen.getByLabelText(/^First name/), 'Ada');
  await user.type(screen.getByLabelText(/^Surname/), 'Lovelace');
  await user.type(screen.getByLabelText(/^Email/), email);
  await user.type(screen.getByLabelText(/^Confirm email/), email);
}

const save = () => screen.getByRole('button', { name: /Create/ });
const openRoles = async (user: ReturnType<typeof userEvent.setup>) => user.click(screen.getByText('Select roles'));

describe('CreateStaffModal — roles', () => {
  it('grants both coach and tutor access from one form', async () => {
    const user = userEvent.setup();
    render(<CreateStaffModal onClose={vi.fn()} onCreated={vi.fn()} />);
    await fillRequired(user);
    await openRoles(user);
    await user.click(screen.getByRole('checkbox', { name: 'Coach' }));
    await user.click(screen.getByRole('checkbox', { name: 'Tutor' }));
    await user.click(save());

    expect(createStaffUser).toHaveBeenCalledTimes(1);
    expect(createStaffUser.mock.calls[0][0]).toMatchObject({
      username: 'Ada Lovelace',
      email: 'ada@kbc.test',
      position: 'Admin',
      accesses: ['coach', 'tutor'],
    });
  });

  it('reports the selected role and closes', async () => {
    const onClose = vi.fn();
    const onCreated = vi.fn();
    const user = userEvent.setup();
    render(<CreateStaffModal onClose={onClose} onCreated={onCreated} />);
    await fillRequired(user);
    await openRoles(user);
    await user.click(screen.getByRole('checkbox', { name: 'Tutor' }));
    await user.click(save());

    expect(toastSuccess).toHaveBeenCalledWith(
      'Staff member created and invited',
      expect.stringContaining('Ada Lovelace'),
    );
    expect(onCreated).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });
  it('grants Super Admin exclusively, even after Coach was selected', async () => {
    const user = userEvent.setup();
    render(<CreateStaffModal onClose={vi.fn()} onCreated={vi.fn()} />);
    await fillRequired(user);
    await openRoles(user);
    await user.click(screen.getByRole('checkbox', { name: 'Coach' }));
    await user.click(screen.getByRole('checkbox', { name: 'Super Admin' }));
    await user.click(save());

    const payload = createStaffUser.mock.calls[0][0];
    expect(payload.position).toBe('Admin');
    expect(payload.accesses).toEqual(['super-admin']);
  });
});

describe('CreateStaffModal — shared validation', () => {
  it('refuses a mismatched email confirmation without calling the API', async () => {
    const user = userEvent.setup();
    render(<CreateStaffModal onClose={vi.fn()} onCreated={vi.fn()} />);
    await user.type(screen.getByLabelText(/^First name/), 'Ada');
    await user.type(screen.getByLabelText(/^Surname/), 'Lovelace');
    await user.type(screen.getByLabelText(/^Email/), 'ada@kbc.test');
    await user.type(screen.getByLabelText(/^Confirm email/), 'aba@kbc.test');
    await openRoles(user);
    await user.click(screen.getByRole('checkbox', { name: 'Tutor' }));
    await user.click(save());

    expect(createStaffUser).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalledWith('Emails do not match', expect.any(String));
  });

  it('accepts a confirmation differing only in case', async () => {
    // Addresses are not case-sensitive in practice; capitalisation is not the
    // typo the second field is there to catch.
    const user = userEvent.setup();
    render(<CreateStaffModal onClose={vi.fn()} onCreated={vi.fn()} />);
    await user.type(screen.getByLabelText(/^First name/), 'Ada');
    await user.type(screen.getByLabelText(/^Surname/), 'Lovelace');
    await user.type(screen.getByLabelText(/^Email/), 'ada@kbc.test');
    await user.type(screen.getByLabelText(/^Confirm email/), 'Ada@KBC.test');
    await openRoles(user);
    await user.click(screen.getByRole('checkbox', { name: 'Coach' }));
    await user.click(save());

    expect(createStaffUser).toHaveBeenCalledTimes(1);
  });

  it('names the missing required fields rather than failing silently', async () => {
    const user = userEvent.setup();
    render(<CreateStaffModal onClose={vi.fn()} onCreated={vi.fn()} />);
    await user.click(save());

    expect(createStaffUser).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalledWith('Missing details', expect.stringContaining('First name'));
  });

  it('requires at least one role', async () => {
    const user = userEvent.setup();
    render(<CreateStaffModal onClose={vi.fn()} onCreated={vi.fn()} />);
    await fillRequired(user);
    await user.click(save());

    expect(createStaffUser).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalledWith('Roles required', expect.any(String));
  });

  it('still reports the record as created when the invitation email fails', async () => {
    // The record and the credential fail independently; a mail failure leaves a
    // link that can be re-sent, so it must not read as "nothing happened".
    createStaffUser.mockResolvedValue({
      name: 'Ada Lovelace',
      invitation: { invited: true, emailSent: false, error: 'SMTP refused' },
    });
    const user = userEvent.setup();
    render(<CreateStaffModal onClose={vi.fn()} onCreated={vi.fn()} />);
    await fillRequired(user);
    await openRoles(user);
    await user.click(screen.getByRole('checkbox', { name: 'Tutor' }));
    await user.click(save());

    expect(toastSuccess).toHaveBeenCalledWith('Staff member created', expect.stringContaining('Tutor'));
    expect(toastError).toHaveBeenCalledWith('Invitation email not sent', 'SMTP refused');
  });
});
