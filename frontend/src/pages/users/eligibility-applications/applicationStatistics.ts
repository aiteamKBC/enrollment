import type { ApplicationsPage } from '@/api/eligibilityApplications';

/** Presentation counts only: a paginated or filtered page is never a global census. */
export function applicationStatistics(data: ApplicationsPage | null, search: string, status: string) {
  const unavailable = { total: null, pending: null, assessed: null };
  if (!data || search !== '' || !Number.isFinite(data.total) || data.total < 0) return unavailable;
  if (status === 'pending_assessment') return { ...unavailable, pending: data.total };
  if (status !== 'all') return unavailable;
  if (data.applications.length !== data.total) return { ...unavailable, total: data.total };
  return {
    total: data.total,
    pending: data.applications.filter(row => row.assessment_status === 'pending_assessment').length,
    assessed: data.applications.filter(row => row.assessment_status === 'provisional_eligible' || row.assessment_status === 'not_eligible').length,
  };
}
