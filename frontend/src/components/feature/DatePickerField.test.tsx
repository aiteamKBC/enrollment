import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { DatePickerField } from './DatePickerField';

describe('DatePickerField keyboard entry', () => {
  it('formats consecutive keystrokes and ignores letters and excess digits', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<DatePickerField label="Start date" value="" onChange={onChange} />);
    const input = screen.getByRole('combobox', { name: 'Start date' });

    await user.type(input, '07112026abc999');
    expect(input).toHaveValue('07/11/2026');
    expect(onChange).toHaveBeenLastCalledWith('2026-11-07');
  });

  it('keeps only eight digits and displays them as dd/mm/yyyy', () => {
    const onChange = vi.fn();
    render(<DatePickerField label="Date of Birth" value="" onChange={onChange} />);
    const input = screen.getByRole('combobox', { name: 'Date of Birth' });

    fireEvent.change(input, { target: { value: '4445454words5445562026' } });
    expect(input).toHaveValue('44/45/4545');
    expect(screen.getByText('Day must be between 01 and 31')).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: '07112026' } });
    expect(input).toHaveValue('07/11/2026');
    expect(onChange).toHaveBeenLastCalledWith('2026-11-07');
  });

  it('lets an existing date be replaced without retaining the old answer', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<DatePickerField label="End date" value="2026-11-07" onChange={onChange} />);
    const input = screen.getByRole('combobox', { name: 'End date' });

    await user.click(input);
    await user.keyboard('{Control>}a{/Control}');
    await user.keyboard('15122026');
    expect(input).toHaveValue('15/12/2026');
    expect(onChange).toHaveBeenCalledWith('');
    expect(onChange).toHaveBeenLastCalledWith('2026-12-15');
  });

  it('deletes the day digit beside a slash without shifting the month', async () => {
    const user = userEvent.setup();
    render(<DatePickerField label="Date" value="2026-11-07" onChange={vi.fn()} />);
    const input = screen.getByRole('combobox', { name: 'Date' }) as HTMLInputElement;

    await user.click(input);
    input.setSelectionRange(3, 3);
    await user.keyboard('{Backspace}');
    expect(input).toHaveValue('0/11/2026');
  });

  it('reports invalid days, months, and impossible calendar dates while typing', () => {
    render(<DatePickerField label="Session date" value="" onChange={vi.fn()} />);
    const input = screen.getByRole('combobox', { name: 'Session date' });

    fireEvent.change(input, { target: { value: '32' } });
    expect(screen.getByText('Day must be between 01 and 31')).toBeInTheDocument();
    fireEvent.change(input, { target: { value: '31/13' } });
    expect(screen.getByText('Month must be between 01 and 12')).toBeInTheDocument();
    fireEvent.change(input, { target: { value: '31/02/2026' } });
    expect(screen.getByText('Enter a real calendar date')).toBeInTheDocument();
    fireEvent.change(input, { target: { value: '28/02/2026' } });
    expect(input).not.toHaveAttribute('aria-invalid', 'true');
  });

  it('shows a completion error on blur and enforces the date range', () => {
    const onChange = vi.fn();
    render(<DatePickerField label="Date" value="" onChange={onChange} min="2026-11-01" max="2026-11-30" />);
    const input = screen.getByRole('combobox', { name: 'Date' });

    fireEvent.change(input, { target: { value: '07/11/202' } });
    fireEvent.blur(input);
    expect(screen.getByText('Enter a complete date as dd/mm/yyyy')).toBeInTheDocument();
    fireEvent.change(input, { target: { value: '31/10/2026' } });
    expect(screen.getByText('Cannot be before 01/11/2026')).toBeInTheDocument();
    fireEvent.change(input, { target: { value: '07/11/2026' } });
    expect(onChange).toHaveBeenLastCalledWith('2026-11-07');
  });
});
