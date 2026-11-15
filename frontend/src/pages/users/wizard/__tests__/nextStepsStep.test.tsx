/**
 * What Happens Now? — the last step of both the learner's and the staff
 * enrolment wizard: book the compliance meeting, and what to finish before it.
 */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import NextSteps from '../steps/NextSteps';
import { LearnerEnrolmentVisualContext } from '@/features/learner-enrolment/enrolmentPresentation';
import { WizardTextsContext } from '../layout/textsContext';

describe('What Happens Now? step', () => {
  it('preserves all learner text, requirements, booking and email links exactly as in the existing step', () => {
    const old = render(<NextSteps />);
    const text = old.container.textContent;
    const links = [...old.container.querySelectorAll('a')].map(link => ({ label: link.textContent, href: link.getAttribute('href'), target: link.target, rel: link.rel }));
    old.unmount();
    const learner = render(<LearnerEnrolmentVisualContext.Provider value={true}><NextSteps /></LearnerEnrolmentVisualContext.Provider>);
    expect(learner.container.textContent).toBe(text);
    expect([...learner.container.querySelectorAll('a')].map(link => ({ label: link.textContent, href: link.getAttribute('href'), target: link.target, rel: link.rel }))).toEqual(links);
    expect(screen.getAllByRole('listitem')).toHaveLength(3);
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  });

  it('continues to use published wording without adding reference-only text or actions', () => {
    render(<LearnerEnrolmentVisualContext.Provider value={true}><WizardTextsContext.Provider value={{
      'block.nextSteps.title': 'Published next step title',
      'block.nextSteps.body': '## Published meeting heading\nCustom instructions.\n\n[Original booking link](https://example.test/meeting)\n\n- Custom prerequisite\n\n[Support email](mailto:custom@example.test)',
    }}><NextSteps /></WizardTextsContext.Provider></LearnerEnrolmentVisualContext.Provider>);
    expect(screen.getByRole('heading', { name: 'Published next step title' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Original booking link' })).toHaveAttribute('href', 'https://example.test/meeting');
    expect(screen.getByRole('link', { name: 'Support email' })).toHaveAttribute('href', 'mailto:custom@example.test');
    expect(screen.getAllByRole('listitem')).toHaveLength(1);
    expect(screen.queryByRole('link', { name: 'Book Compliance Meeting' })).not.toBeInTheDocument();
  });
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
