import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { IpcFundRecommendation } from './IpcFundRecommendation';

afterEach(cleanup);

it.each(['PCP_L6', 'Project Controls Professional Level 6'])('recommends IPC for an ineligible %s applicant with safe new-tab CTAs', programme => {
  render(<IpcFundRecommendation programme={programme} ineligible />);
  expect(screen.getByRole('img', { name: 'Institute of Project Controls (IPC) logo' })).toHaveAttribute('src', '/assets/ipc-logo.png');
  expect(screen.getByRole('heading', { name: "Your journey doesn't have to stop here." })).toBeVisible();
  expect(screen.getByText(/Jan\/Feb 2027 Cohort/)).toBeVisible();
  expect(screen.getByText(/independent of the PCP Level 6 apprenticeship/)).toBeVisible();
  expect(screen.getByText(/50%–75%/)).toBeVisible();
  expect(screen.getByText(/IPC Fund Committee/)).toHaveTextContent('not guaranteed');
  expect(screen.getByText(/Apprenticeship ineligibility does not imply eligibility for IPC funding/)).toBeVisible();
  expect(screen.queryByText('Both links open in a new tab.')).not.toBeInTheDocument();
  for (const [name, url] of [
    ['Apply for the IPC Fund', 'https://instituteofprojectcontrols.com/bursary-scholarship-application'],
    ['View IPC Scholarships & Fund Announcements', 'https://instituteofprojectcontrols.com/scholarships'],
  ]) {
    const link = screen.getByRole('link', { name });
    expect(link).toHaveAttribute('href', url);
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  }
});

it.each([
  ['PCP_L6', false], ['Project Controls Professional Level 6', false],
  ['Marketing Manager Level 6', true], ['Marketing Executive Level 4', true],
  ['Associate Project Manager Level 4', true], ['PCP_L4', true], ['', true], [undefined, true],
] as const)('does not recommend for programme %s with ineligible=%s', (programme, ineligible) => {
  const { container } = render(<IpcFundRecommendation programme={programme} ineligible={ineligible} />);
  expect(container).toBeEmptyDOMElement();
});
