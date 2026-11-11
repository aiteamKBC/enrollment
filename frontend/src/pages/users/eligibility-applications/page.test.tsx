import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { ApplicationDetail, EligibilityApplication } from '@/api/eligibilityApplications';

vi.mock('@/components/feature/WorkspaceShell', () => ({ WorkspaceShell: ({ children }: { children: ReactNode }) => children }));
let account: { role: 'admin' | 'staff' | 'learner' | 'employer'; access: string } | null;
vi.mock('@/hooks/useAuth.helpers', () => ({ useAuth: () => ({ auth: { account } }) }));
const list = vi.fn(), detail = vi.fn();
vi.mock('@/api/eligibilityApplications', () => ({
  fetchEligibilityApplications: (...args: unknown[]) => list(...args),
  fetchEligibilityApplication: (...args: unknown[]) => detail(...args),
}));
import EligibilityApplicationsPage from './page';

const row: EligibilityApplication = {
  id: '42', first_name: 'Sam', surname: 'Example', work_email: 'sam@example.test',
  submitted_at: '2026-11-03T10:00:00Z', employer_name: 'Example Ltd',
  employer_postcode: 'EH1 1YZ', main_workplace_postcode: null,
  workplace_arrangement: 'remote_hybrid_multiple', assessment_status: 'pending_assessment',
  assessment_reason: 'There is insufficient verified workplace-location evidence.',
};
const application: ApplicationDetail = {
  ...row, majority_hours_in_england: false, postcode: 'CF10 1EP', postcode_country: 'Wales',
  employer_postcode_country: 'Scotland', main_workplace_country: null,
  residency_status: 'Pre-settled Status', eligible: null, invitation_outcome: 'pendingAssessment',
};
beforeAll(() => { vi.stubGlobal('AppIcon', () => null); });
beforeEach(() => {
  account = { role: 'staff', access: 'enrolment' };
  list.mockReset().mockImplementation(async options => ({ applications: [row], total: 51, page: options.page, pageSize: 25 }));
  detail.mockReset().mockResolvedValue({ application });
});
afterEach(cleanup);

function open(path = '/users/eligibility-applications') {
  return render(<MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/users/eligibility-applications" element={<EligibilityApplicationsPage />} />
    <Route path="/users/eligibility-applications/:applicationId" element={<EligibilityApplicationsPage />} />
  </Routes></MemoryRouter>);
}

it('defaults to pending assessment and displays the required queue columns', async () => {
  open();
  expect(await screen.findByRole('link', { name: 'Sam Example' })).toBeInTheDocument();
  expect(list).toHaveBeenCalledWith({ search: '', status: 'pending_assessment', page: 1 });
  const table = screen.getByRole('table');
  for (const label of ['Applicant', 'Email', 'Submitted (UK time)', 'Employer', 'Employer postcode', 'Main workplace postcode', 'Workplace arrangement', 'Assessment status', 'Assessment reason']) {
    expect(within(table).getByRole('columnheader', { name: label })).toBeInTheDocument();
  }
  expect(within(table).queryByRole('columnheader', { name: 'Staff review' })).not.toBeInTheDocument();
  expect(within(table).getByText(row.assessment_reason!)).toBeInTheDocument();
  expect(within(table).getByText('I work remotely, hybrid, or across multiple locations.')).toBeInTheDocument();
  expect(screen.getByText(/Screening evidence does not confirm government funding eligibility/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /approve|reject|invitation|upload/i })).not.toBeInTheDocument();
});

it('applies search explicitly and resets pagination when filters change', async () => {
  open('/users/eligibility-applications?page=2');
  await screen.findByRole('link', { name: 'Sam Example' });
  fireEvent.change(screen.getByLabelText('Search applications'), { target: { value: ' Example Ltd ' } });
  expect(list).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  await waitFor(() => expect(list).toHaveBeenLastCalledWith({ search: 'Example Ltd', status: 'pending_assessment', page: 1 }));
  fireEvent.change(screen.getByLabelText('Assessment status'), { target: { value: 'legacy' } });
  await waitFor(() => expect(list).toHaveBeenLastCalledWith({ search: 'Example Ltd', status: 'legacy', page: 1 }));
});

it('uses the existing pagination controls', async () => {
  open();
  await screen.findByRole('link', { name: 'Sam Example' });
  expect(screen.getByRole('button', { name: 'Previous page' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
  await waitFor(() => expect(list).toHaveBeenLastCalledWith({ search: '', status: 'pending_assessment', page: 2 }));
  await waitFor(() => expect(screen.getByLabelText('Page number')).toHaveValue(2));
});

it('preserves filters and page when opening details and returning to the queue', async () => {
  open('/users/eligibility-applications?status=all&search=Sam&page=2');
  fireEvent.click(await screen.findByRole('link', { name: 'Sam Example' }));
  await screen.findByRole('heading', { name: 'Verified postcode geography — recorded at submission' });
  expect(detail).toHaveBeenCalledWith('42');
  fireEvent.click(screen.getByRole('link', { name: 'Back to applications' }));
  await screen.findByRole('link', { name: 'Sam Example' });
  expect(list).toHaveBeenLastCalledWith({ search: 'Sam', status: 'all', page: 2 });
});

it('shows an empty state without inventing pending applications', async () => {
  list.mockResolvedValue({ applications: [], total: 0, page: 1, pageSize: 25 });
  open();
  expect(await screen.findByText('No applications match these filters.')).toBeInTheDocument();
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
});

it('offers retry after a queue error', async () => {
  list.mockRejectedValueOnce(new Error('Applications are temporarily unavailable.'));
  open();
  expect(await screen.findByText('Applications are temporarily unavailable.')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /Try again/i }));
  expect(await screen.findByRole('link', { name: 'Sam Example' })).toBeInTheDocument();
});

it('separates declarations, recorded geography, assessment reasons and outcomes in details', async () => {
  open('/users/eligibility-applications/42');
  const geography = (await screen.findByRole('heading', { name: 'Verified postcode geography — recorded at submission' })).closest('section')!;
  expect(within(geography).getByText('Wales')).toBeInTheDocument();
  expect(within(geography).getByText('Scotland')).toBeInTheDocument();
  expect(within(geography).getByRole('heading', { name: 'Effective workplace' })).toBeInTheDocument();
  const employment = screen.getByRole('heading', { name: 'Employment and workplace — declared' }).closest('section')!;
  expect(within(employment).getByText('Expects at least 50% of working hours in England').parentElement).toHaveTextContent('No');
  expect(screen.getByText('Server-generated assessment reason').parentElement).toHaveTextContent(row.assessment_reason!);
  expect(screen.queryByRole('heading', { name: 'Staff review decision' })).not.toBeInTheDocument();
  expect(screen.getByText('Stored screening result').parentElement).toHaveTextContent('Not determined');
  expect(screen.getByText('Pending assessment — no invitation')).toBeInTheDocument();
  expect(screen.getByText('Pre-settled Status')).toBeInTheDocument();
  expect(list).not.toHaveBeenCalled();
});

it('keeps legacy records and technical retry outcomes distinct from pending assessment', async () => {
  detail.mockResolvedValue({ application: { ...application, assessment_status: null, assessment_reason: null, workplace_arrangement: null, invitation_outcome: 'retryLater' } });
  open('/users/eligibility-applications/42');
  expect(await screen.findByText('Legacy / not assessed')).toBeInTheDocument();
  expect(screen.getByText('Technical retry required')).toBeInTheDocument();
  expect(screen.queryByText('Pending assessment — no invitation')).not.toBeInTheDocument();
  expect(screen.getAllByText('Not recorded').length).toBeGreaterThan(0);
});

it('shows a missing-detail error and no applicant information', async () => {
  detail.mockRejectedValue(new Error('Application not found.'));
  open('/users/eligibility-applications/999');
  expect(await screen.findByText('Application not found.')).toBeInTheDocument();
  expect(screen.queryByText('sam@example.test')).not.toBeInTheDocument();
});

it('shows structured employer and residential addresses without repeating composed full addresses', async () => {
  detail.mockResolvedValue({ application: { ...application,
    employer_address_line1: '2 Business Park', employer_address_line2: 'Building B', employer_address_line3: 'Floor 3', employer_town_city: 'Swansea', employer_county: 'West Glamorgan',
    residential_address_line1: '10 Rue Exemple', residential_town_city: 'Paris', residential_county: 'Île-de-France',
    employer_address: 'COMPOSED EMPLOYER COPY', full_address: 'COMPOSED RESIDENTIAL COPY', country_of_residence: 'France',
  } });
  open('/users/eligibility-applications/42');
  const employer = (await screen.findByRole('heading', { name: 'Employer address — declared' })).closest('section')!;
  const residential = screen.getByRole('heading', { name: 'Residential address — declared' }).closest('section')!;
  for (const value of ['2 Business Park', 'Building B', 'Floor 3', 'Swansea', 'West Glamorgan']) expect(within(employer).getByText(value)).toBeInTheDocument();
  expect(within(employer).getByText('Address Line 3 (Town or City)').parentElement).toHaveTextContent('Swansea');
  expect(within(employer).getByText('Address Line 4 (County / Country)').parentElement).toHaveTextContent('West Glamorgan');
  expect(within(employer).getByText('Additional address details (legacy)').parentElement).toHaveTextContent('Floor 3');
  expect(within(residential).getByText('10 Rue Exemple')).toBeInTheDocument();
  expect(within(residential).getByText('Paris')).toBeInTheDocument();
  expect(within(residential).queryByText('Additional address details (legacy)')).not.toBeInTheDocument();
  expect(screen.queryByText(/COMPOSED .* COPY/)).not.toBeInTheDocument();
});

it('falls back to historical free-text addresses without splitting or inventing components', async () => {
  detail.mockResolvedValue({ application: { ...application, employer_address: 'Historical company full address', full_address: 'Historical residential full address' } });
  open('/users/eligibility-applications/42');
  expect(await screen.findByText('Historical company full address')).toBeInTheDocument();
  expect(screen.getByText('Historical residential full address')).toBeInTheDocument();
  expect(screen.getAllByText(/Historical full-address text is shown without splitting/)).toHaveLength(2);
  expect(screen.queryByText('Address Line 1')).not.toBeInTheDocument();
});

it.each([
  null, { role: 'learner' as const, access: '' }, { role: 'employer' as const, access: '' },
  { role: 'staff' as const, access: 'curriculum' },
])('does not request applicant data for an unauthorised identity: %s', identity => {
  account = identity;
  open('/users/eligibility-applications/42');
  expect(screen.getByRole('alert')).toHaveTextContent('does not have access');
  expect(list).not.toHaveBeenCalled();
  expect(detail).not.toHaveBeenCalled();
});
