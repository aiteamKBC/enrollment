import type { HomeCohort } from './configuration';

/** Shared live placement display. Academic choices and saved versions stay separate. */
export function LmsAssignedModules({ home }: { home?: HomeCohort | null }) {
  if (home?.source !== 'lms') return null;
  return <section aria-label="Assigned LMS modules" className="my-4 rounded-xl border border-primary-200 bg-background-50 p-4">
    <h3 className="font-semibold">Assigned LMS modules</h3>
    {home.status !== 'resolved' ? <p role="status" className="mt-2 text-sm">{home.reason === 'lms_unavailable'
      ? 'LMS data is temporarily unavailable. Please reload to retry.'
      : home.reason === 'assigned_cohort_not_in_lms'
      ? `Your saved cohort${home.assignedCohortName ? ` (${home.assignedCohortName})` : ''} is not available in LMS. Please contact admissions to review the placement.`
      : ['lms_cohort_ambiguous', 'lms_group_ambiguous', 'programme_assignment_mismatch', 'lms_assignment_ambiguous'].includes(home.reason || '')
      ? 'Your saved programme, cohort or group needs admissions review before LMS modules can be shown.'
      : 'Your cohort assignment needs to be confirmed before LMS modules can be shown.'}</p> : <>
      <p className="mt-2 text-sm">Cohort: {home.name} · Group: {home.groupName || 'Group pending'}</p>
      {!home.groupId && Boolean(home.lmsModules?.length) && <p className="mt-1 text-xs text-foreground-600">Showing this cohort’s available modules by group. A group has not been assigned to you yet.</p>}
      {home.lmsModules?.length ? <div className="mt-3 overflow-x-auto"><table className="w-full text-left text-sm">
        <thead><tr className="border-b border-primary-100"><th className="p-2">Module</th><th className="p-2">Group</th><th className="p-2">Duration (weeks)</th><th className="p-2">Start</th><th className="p-2">End</th></tr></thead>
        <tbody>{home.lmsModules.map(module => <tr key={module.id} className="border-b border-primary-100 last:border-0">
          <td className="p-2">{module.title}</td><td className="p-2">{module.groupName || home.groupName || '—'}</td><td className="p-2">{module.weeks != null && module.weeks > 0 ? module.weeks : '—'}</td>
          <td className="p-2">{module.start || 'Pending'}</td><td className="p-2">{module.end || 'Pending'}</td>
        </tr>)}</tbody>
      </table></div> : <p className="mt-2 text-sm">No LMS modules are available for this saved cohort and group yet.</p>}
    </>}
  </section>;
}
