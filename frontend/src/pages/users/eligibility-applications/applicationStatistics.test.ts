import { describe, expect, it } from 'vitest';
import type { ApplicationsPage, EligibilityApplication } from '@/api/eligibilityApplications';
import { applicationStatistics } from './applicationStatistics';

function response(statuses: EligibilityApplication['assessment_status'][], total = statuses.length): ApplicationsPage {
  return {
    total, page: 1, pageSize: 25,
    applications: statuses.map((assessment_status, index) => ({
      id: String(index), assessment_status, first_name: 'Preview', surname: 'Applicant',
      work_email: null, submitted_at: null, employer_name: null, employer_postcode: null,
      main_workplace_postcode: null, workplace_arrangement: null, assessment_reason: null,
    })),
  };
}

describe('Presentation of available application counts', () => {
  it('uses the global API total but never treats one page as a complete status census', () => {
    expect(applicationStatistics(response(['pending_assessment', 'not_eligible'], 100), '', 'all'))
      .toEqual({ total: 100, pending: null, assessed: null });
  });

  it('counts statuses only when the complete unfiltered set is available, excluding legacy from assessed', () => {
    expect(applicationStatistics(response(['pending_assessment', 'provisional_eligible', 'not_eligible', null]), '', 'all'))
      .toEqual({ total: 4, pending: 1, assessed: 2 });
  });

  it('uses a paginated pending-filter total only for the pending card', () => {
    expect(applicationStatistics(response(['pending_assessment'], 51), '', 'pending_assessment'))
      .toEqual({ total: null, pending: 51, assessed: null });
  });

  it('does not turn an empty filtered queue into three global zeros', () => {
    expect(applicationStatistics(response([]), '', 'pending_assessment'))
      .toEqual({ total: null, pending: 0, assessed: null });
  });

  it('can show all zeros when the complete unfiltered response is empty', () => {
    expect(applicationStatistics(response([]), '', 'all'))
      .toEqual({ total: 0, pending: 0, assessed: 0 });
  });

  it.each(['all', 'pending_assessment', 'not_eligible', 'provisional_eligible', 'legacy'])('does not label a search total as a global total for %s', status => {
    expect(applicationStatistics(response(['pending_assessment']), 'Preview', status))
      .toEqual({ total: null, pending: null, assessed: null });
  });

  it('does not extrapolate combined assessed totals from a single outcome filter', () => {
    expect(applicationStatistics(response(['not_eligible'], 34), '', 'not_eligible'))
      .toEqual({ total: null, pending: null, assessed: null });
  });

  it('leaves counts unavailable before a valid response arrives', () => {
    expect(applicationStatistics(null, '', 'all')).toEqual({ total: null, pending: null, assessed: null });
  });
});
