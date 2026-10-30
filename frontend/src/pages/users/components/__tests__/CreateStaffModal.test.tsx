/**
 * The staff creation form's two variants.
 *
 * One component serves both because they differ only by a position, an access
 * grant and some wording — every field, validation rule and invitation-outcome
 * branch is shared. These tests pin the differences, since they are the whole
 * reason the variant exists, plus the shared validation that a future third
 * variant must not break.
 *
 * The load-bearing assertion is that the tutor variant grants access on save.
 * Without it a tutor is created with no grant and can only ever land on
 * /access-required — an account that looks created and is quietly useless.
 */
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

describe('CreateStaffModal — tutor variant', () => {
  it('grants tutor access on save, so the account is usable immediately', async () => {
    const user = userEvent.setup();
    render(<CreateStaffModal variant="tutor" onClose={vi.fn()} onCreated={vi.fn()} />);
    await fillRequired(user);
    await user.click(save());

    expect(createStaffUser).toHaveBeenCalledTimes(1);
    expect(createStaffUser.mock.calls[0][0]).toMatchObject({
      username: 'Ada Lovelace',
      email: 'ada@kbc.test',
      position: 'Tutor',
      access: 'tutor',
    });
  });

  it('says the account is a Tutor and where it will land', () => {
    render(<CreateStaffModal variant="tutor" onClose={vi.fn()} onCreated={vi.fn()} />);
    expect(screen.getByText('Tutor')).toBeTruthy();
    expect(screen.getByText(/Tutor workspace/)).toBeTruthy();
  });

  it('reports the outcome as a tutor, and closes', async () => {
    const onClose = vi.fn();
    const onCreated = vi.fn();
    const user = userEvent.setup();
    render(<CreateStaffModal variant="tutor" onClose={onClose} onCreated={onCreated} />);
    await fillRequired(user);
    await user.click(save());

    expect(toastSuccess).toHaveBeenCalledWith(
      'Tutor created and invited',
      expect.stringContaining('Ada Lovelace'),
    );
    expect(onCreated).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });
});

describe('CreateStaffModal — admin variant', () => {
  it('sends the Admin position and grants nothing', async () => {
    // Deliberate: 'Admin' is a job title, and what the account may reach is
    // chosen on the Accounts page. Granting here would hand out unasked access.
    const user = userEvent.setup();
    render(<CreateStaffModal variant="admin" onClose={vi.fn()} onCreated={vi.fn()} />);
    await fillRequired(user);
    await user.click(save());

    const payload = createStaffUser.mock.calls[0][0];
    expect(payload.position).toBe('Admin');
    expect(payload.access).toBeUndefined();
  });

  it('does not promise a landing page it cannot know', () => {
    render(<CreateStaffModal variant="admin" onClose={vi.fn()} onCreated={vi.fn()} />);
    expect(screen.queryByText(/will land in/)).toBeNull();
  });
});

describe('CreateStaffModal — shared validation', () => {
  it('refuses a mismatched email confirmation without calling the API', async () => {
    const user = userEvent.setup();
    render(<CreateStaffModal variant="tutor" onClose={vi.fn()} onCreated={vi.fn()} />);
    await user.type(screen.getByLabelText(/^First name/), 'Ada');
    await user.type(screen.getByLabelText(/^Surname/), 'Lovelace');
    await user.type(screen.getByLabelText(/^Email/), 'ada@kbc.test');
    await user.type(screen.getByLabelText(/^Confirm email/), 'aba@kbc.test');
    await user.click(save());

    expect(createStaffUser).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalledWith('Emails do not match', expect.any(String));
  });

  it('accepts a confirmation differing only in case', async () => {
    // Addresses are not case-sensitive in practice; capitalisation is not the
    // typo the second field is there to catch.
    const user = userEvent.setup();
    render(<CreateStaffModal variant="tutor" onClose={vi.fn()} onCreated={vi.fn()} />);
    await user.type(screen.getByLabelText(/^First name/), 'Ada');
    await user.type(screen.getByLabelText(/^Surname/), 'Lovelace');
    await user.type(screen.getByLabelText(/^Email/), 'ada@kbc.test');
    await user.type(screen.getByLabelText(/^Confirm email/), 'Ada@KBC.test');
    await user.click(save());

    expect(createStaffUser).toHaveBeenCalledTimes(1);
  });

  it('names the missing required fields rather than failing silently', async () => {
    const user = userEvent.setup();
    render(<CreateStaffModal variant="tutor" onClose={vi.fn()} onCreated={vi.fn()} />);
    await user.click(save());

    expect(createStaffUser).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalledWith('Missing details', expect.stringContaining('First name'));
  });

  it('still reports the record as created when the invitation email fails', async () => {
    // The record and the credential fail independently; a mail failure leaves a
    // link that can be re-sent, so it must not read as "nothing happened".
    createStaffUser.mockResolvedValue({
      name: 'Ada Lovelace',
      invitation: { invited: true, emailSent: false, error: 'SMTP refused' },
    });
    const user = userEvent.setup();
    render(<CreateStaffModal variant="tutor" onClose={vi.fn()} onCreated={vi.fn()} />);
    await fillRequired(user);
    await user.click(save());

    expect(toastSuccess).toHaveBeenCalledWith('Tutor created', expect.stringContaining('Tutor'));
    expect(toastError).toHaveBeenCalledWith('Invitation email not sent', 'SMTP refused');
  });
});
