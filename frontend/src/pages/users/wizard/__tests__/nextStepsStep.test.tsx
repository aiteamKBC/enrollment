/**
 * What Happens Now? — the last step of both the learner's and the staff
 * enrolment wizard: book the compliance meeting, and what to finish before it.
 */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import NextSteps from '../steps/NextSteps';

describe('What Happens Now? step', () => {
  it('links to the Student Support booking page in a new tab', () => {
    render(<NextSteps />);

    const link = screen.getByRole('link', { name: 'Book Compliance Meeting' });
    expect(link).toHaveAttribute('href', 'https://outlook.office.com/book/StudentSupport1@kentbusinesscollege.com/s/EmOovIV5H0GGd131KDuFJQ2');
    expect(link).toHaveAttribute('target', '_blank');
    expect(screen.getByText(/select “Compliance Meeting”/)).toBeInTheDocument();
  });

  it('lists what must be done before the meeting and who to contact', () => {
    render(<NextSteps />);

    expect(screen.getByRole('heading', { name: 'What Happens Now?' })).toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(3);
    expect(screen.getByText(/Digital Apprenticeship Service \(DAS\)/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'office@kentbusinesscollege.com' })).toHaveAttribute('href', 'mailto:office@kentbusinesscollege.com');
    expect(screen.queryByText(/ibisconsultancy/)).not.toBeInTheDocument();
  });
});
