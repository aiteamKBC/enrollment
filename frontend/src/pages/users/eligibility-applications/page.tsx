import { useEffect, useState, type FormEvent } from 'react';
import { Link, useLocation, useParams } from 'react-router-dom';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { AdminPageHeader, DataPanel } from '@/pages/admin/_shared/AdminPage';
import { roleNavMap } from '@/mocks/navigation';
import { useAuth } from '@/hooks/useAuth.helpers';
import { useListQueryState } from '@/hooks/useListQueryState';
import { canManageEnrolment } from '@/lib/enrollmentAccess';
import {
  fetchEligibilityApplication, fetchEligibilityApplications,
  type ApplicationDetail, type ApplicationsPage,
} from '@/api/eligibilityApplications';
import { WORKPLACE_LABELS } from '@/api/apprenticeshipApplication';
import { btnPrimary, btnSecondary, FieldRow, inputClass, Pagination } from '../components/ui';

const BASE = '/users/eligibility-applications';
const DEFAULTS = { search: '', status: 'pending_assessment', page: 1 };
const STATUS_LABELS: Record<string, string> = {
  pending_assessment: 'Pending assessment', provisional_eligible: 'Provisional screening pass', not_eligible: 'Not eligible',
};
const OUTCOME_LABELS: Record<string, string> = {
  pendingAssessment: 'Pending assessment — no invitation', invited: 'Invitation sent',
  invitePending: 'Account created; invitation email not sent', existingAccount: 'Existing account',
  retryLater: 'Technical retry required', notEligible: 'Not eligible — no invitation',
};
const show = (value: unknown) => value == null || value === '' ? 'Not recorded' : typeof value === 'boolean' ? value ? 'Yes' : 'No' : String(value);
const arrangement = (value: string | null) => value && value in WORKPLACE_LABELS
  ? WORKPLACE_LABELS[value as keyof typeof WORKPLACE_LABELS] : show(value);
const dateTime = (value: string | null) => value && Number.isFinite(Date.parse(value))
  ? new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Europe/London' }).format(new Date(value)) : show(value);

function AssessmentBadge({ status }: { status: string | null }) {
  return <span className={`inline-block rounded-lg px-2 py-1 text-xs font-semibold ${status === 'pending_assessment' ? 'bg-amber-50 text-amber-800' : 'bg-background-200 text-foreground-700'}`}>
    {status == null ? 'Legacy / not assessed' : STATUS_LABELS[status] || 'Unrecognised assessment status'}
  </span>;
}

function ApplicationsList() {
  const { state, setValues } = useListQueryState(DEFAULTS);
  const { search } = useLocation();
  const [draft, setDraft] = useState(String(state.search));
  const [data, setData] = useState<ApplicationsPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const appliedSearch = String(state.search), status = String(state.status), page = Number(state.page);
  useEffect(() => { setDraft(appliedSearch); }, [appliedSearch]);
  useEffect(() => {
    let cancelled = false;
    setLoading(true); setError(null); setData(null);
    fetchEligibilityApplications({ search: appliedSearch, status, page })
      .then(result => { if (!cancelled) setData(result); })
      .catch(reason => { if (!cancelled) setError(reason instanceof Error ? reason.message : 'Could not load applications.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [appliedSearch, status, page, attempt]);
  const submitSearch = (event: FormEvent) => { event.preventDefault(); setValues({ search: draft.trim() }, { resetPage: true }); };
  return <>
    <AdminPageHeader title="Eligibility applications" description="Review saved screening applications. Pending assessment is separate from technical verification or invitation failures." icon="ri-file-search-line" eyebrow="Enrolment" />
    <form onSubmit={submitSearch} className="flex flex-wrap items-end gap-3 rounded-2xl border border-foreground-200 bg-background-50 p-4">
      <div className="min-w-60 flex-1"><label className="mb-1 block text-xs font-semibold" htmlFor="application-search">Search applications</label>
        <input id="application-search" className={inputClass} value={draft} maxLength={200} onChange={e => setDraft(e.target.value)} placeholder="Name, email, employer or postcode" /></div>
      <div><label className="mb-1 block text-xs font-semibold" htmlFor="assessment-filter">Assessment status</label>
        <select id="assessment-filter" className={inputClass} value={status} onChange={e => setValues({ status: e.target.value }, { resetPage: true })}>
          <option value="pending_assessment">Pending assessment</option><option value="all">All applications</option>
          <option value="provisional_eligible">Provisional screening pass</option><option value="not_eligible">Not eligible</option>
          <option value="legacy">Legacy / not assessed</option>
        </select></div>
      <button type="submit" className={btnPrimary}>Search</button>
    </form>
    <DataPanel loading={loading} error={error} empty={data?.applications.length === 0} emptyMessage="No applications match these filters." onRetry={() => setAttempt(value => value + 1)}>
      {data && <><p className="mb-3 text-sm text-foreground-500" aria-live="polite">{data.total} application{data.total === 1 ? '' : 's'}</p>
        <div className="overflow-x-auto rounded-xl border border-foreground-200 bg-background-50"><table className="w-full text-left text-sm">
          <caption className="sr-only">Eligibility applications</caption>
          <thead className="bg-background-100 text-xs text-foreground-600"><tr>{['Applicant', 'Email', 'Submitted (UK time)', 'Employer', 'Employer postcode', 'Main workplace postcode', 'Workplace arrangement', 'Assessment status', 'Assessment reason'].map(label => <th scope="col" className="px-4 py-3" key={label}>{label}</th>)}</tr></thead>
          <tbody>{data.applications.map(row => <tr key={row.id} className="border-t border-foreground-100 align-top">
            <td className="px-4 py-3"><Link className="font-semibold text-primary-700 hover:underline" to={`${BASE}/${row.id}${search}`}>{[row.first_name, row.surname].filter(Boolean).join(' ') || 'Unnamed applicant'}</Link></td>
            {[row.work_email, dateTime(row.submitted_at), row.employer_name, row.employer_postcode, row.main_workplace_postcode, arrangement(row.workplace_arrangement)].map((value, i) => <td key={i} className="px-4 py-3">{show(value)}</td>)}
            <td className="px-4 py-3"><AssessmentBadge status={row.assessment_status} /></td>
            <td className="min-w-64 whitespace-pre-wrap px-4 py-3">{show(row.assessment_reason)}</td>
          </tr>)}</tbody>
        </table></div>
        <Pagination page={data.page} totalPages={Math.max(1, Math.ceil(data.total / data.pageSize))} onChange={next => setValues({ page: next })} />
      </>}
    </DataPanel>
  </>;
}

const DECLARATIONS: [string, [string, string][]][] = [
  ['Applicant information — declared', [['first_name', 'First name'], ['surname', 'Surname'], ['work_email', 'Email'], ['phone_number', 'Phone'], ['date_of_birth', 'Date of birth'], ['postcode', 'Residential postcode']]],
  ['Employment and workplace — declared', [['employment_status', 'Employment status'], ['start_date_with_employer', 'Start date with employer'], ['job_title', 'Job title'], ['employer_name', 'Employer name'], ['employer_postcode', 'Employer postcode'], ['workplace_arrangement', 'Workplace arrangement'], ['majority_hours_in_england', 'Expects at least 50% of working hours in England'], ['representative_name', 'Employer representative'], ['representative_email', 'Representative email']]],
  ['Residency — declared', [['lives_in_uk', 'UK/EEA national'], ['country_of_residence', 'Country of residence'], ['nationality', 'Nationality'], ['residency_status', 'Residency / immigration status'], ['resident_prev_3_years', 'Resident in the UK for the previous three years'], ['years_in_uk', 'Full years lived in the UK'], ['residence_not_for_education', 'Residence not mainly for full-time education'], ['full_time_education_prior', 'Previously in full-time education'], ['expected_leaving_date', 'Expected leaving date']]],
  ['Programme — declared', [['programme', 'Programme'], ['attended_funded_training', 'Previously attended funded training'], ['funded_training_completed_on', 'Funded training completed on'], ['not_previously_completed_programme', 'Has not previously completed this programme']]],
];

function DetailPanel({ title, fields, application }: { title: string; fields: [string, string][]; application: ApplicationDetail }) {
  return <section className="rounded-2xl border border-foreground-200 bg-background-50 p-5"><h2 className="mb-2 font-semibold">{title}</h2>
    {fields.map(([key, label]) => <FieldRow key={key} label={label} readonly value={<span className="whitespace-pre-wrap break-words">{key === 'workplace_arrangement' ? arrangement(application.workplace_arrangement) : show(application[key])}</span>} />)}
  </section>;
}

function AddressPanel({ application, employer }: { application: ApplicationDetail; employer: boolean }) {
  const prefix = employer ? 'employer' : 'residential';
  const fields: [string, string][] = [['address_line1', 'Address Line 1'], ['address_line2', 'Address Line 2'],
    ['town_city', 'Address Line 3 (Town or City)'], ['county', 'Address Line 4 (County / Country)'],
    ['address_line3', 'Additional address details (legacy)']];
  const structured = fields.some(([suffix]) => Boolean(application[`${prefix}_${suffix}`]));
  return <section className="rounded-2xl border border-foreground-200 bg-background-50 p-5">
    <h2 className="mb-2 font-semibold">{employer ? 'Employer address — declared' : 'Residential address — declared'}</h2>
    {structured ? fields.filter(([suffix]) => suffix !== 'address_line3' || application[`${prefix}_${suffix}`]).map(([suffix, label]) => <FieldRow key={suffix} label={label} readonly
      value={<span className="whitespace-pre-wrap break-words">{show(application[`${prefix}_${suffix}`])}</span>} />) : <>
      <p className="mb-2 text-sm text-foreground-500">No structured address was recorded. Historical full-address text is shown without splitting.</p>
      <FieldRow label="Legacy full address" readonly value={<span className="whitespace-pre-wrap break-words">{show(application[employer ? 'employer_address' : 'full_address'])}</span>} />
    </>}
  </section>;
}

function ApplicationDetails({ id }: { id: string }) {
  const { search } = useLocation();
  const [application, setApplication] = useState<ApplicationDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setLoading(true); setError(null); setApplication(null);
    fetchEligibilityApplication(id).then(result => { if (!cancelled) setApplication(result.application); })
      .catch(reason => { if (!cancelled) setError(reason instanceof Error ? reason.message : 'Could not load the application.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [id, attempt]);
  return <>
    <AdminPageHeader title="Eligibility application details" description="Applicant declarations and geography recorded during backend postcode verification are shown separately." icon="ri-file-search-line" eyebrow="Enrolment" actions={<Link to={`${BASE}${search}`} className={btnSecondary}>Back to applications</Link>} />
    <DataPanel loading={loading} error={error} onRetry={() => setAttempt(value => value + 1)}>
      {application && <div className="space-y-4">
        <section className="rounded-2xl border border-foreground-200 bg-background-50 p-5"><h2 className="text-lg font-semibold">{[application.first_name, application.surname].filter(Boolean).join(' ') || 'Unnamed applicant'}</h2>
          <p className="mb-3 text-sm text-foreground-500">Application #{application.id} · Submitted {dateTime(application.submitted_at)}</p>
          <AssessmentBadge status={application.assessment_status} />
          <FieldRow label="Stored screening result" readonly value={application.eligible == null ? 'Not determined' : application.eligible ? 'Preliminary screening pass' : 'Did not pass screening'} />
          <FieldRow label="Server-generated assessment reason" readonly value={show(application.assessment_reason)} />
          <FieldRow label="Stored ineligibility reason" readonly value={show(application.ineligible_reason)} />
          <FieldRow label="Recorded submission / invitation outcome" readonly value={OUTCOME_LABELS[String(application.invitation_outcome)] || show(application.invitation_outcome)} />
          <FieldRow label="Submitted layout version" readonly value={show(application.layout_version)} />
        </section>
        {DECLARATIONS.map(([title, fields]) => <DetailPanel key={title} title={title} fields={fields} application={application} />)}
        <AddressPanel application={application} employer />
        <AddressPanel application={application} employer={false} />
        <section className="space-y-4 rounded-2xl border border-foreground-200 bg-background-50 p-5"><h2 className="font-semibold">Verified postcode geography — recorded at submission</h2>
          <p className="text-sm text-foreground-500">These are saved server lookup results. Missing historical values are not inferred or re-verified when this page opens.</p>
          {([['postcode', 'Residential'], ['employer_postcode', 'Employer company'], ['main_workplace', 'Effective workplace']] as const).map(([prefix, label]) => <div key={prefix}>
            <h3 className="font-semibold">{label}</h3>
            <FieldRow label="Postcode" readonly value={show(application[prefix === 'main_workplace' ? 'main_workplace_postcode' : prefix])} />
            {['country', 'region', 'district'].map(suffix => <FieldRow key={suffix} label={suffix[0].toUpperCase() + suffix.slice(1)} readonly value={show(application[`${prefix}_${suffix}`])} />)}
          </div>)}
        </section>
      </div>}
    </DataPanel>
  </>;
}

export default function EligibilityApplicationsPage() {
  const { auth } = useAuth();
  const { applicationId } = useParams();
  if (!auth.account || !canManageEnrolment(auth.account)) return <p role="alert" className="p-6">Your account does not have access to eligibility applications.</p>;
  const nav = roleNavMap.apprentice;
  return <WorkspaceShell role="compliance" roleLabel={nav.label} navItems={nav.items} workspaceLabel={nav.workspaceLabel} pageTitle="Eligibility applications" pageSubtitle="Screening results">
    <div className="admin-console-page min-w-0 space-y-4 p-3 md:p-6">
      <p className="rounded-xl border border-primary-100 bg-primary-50 p-4 text-sm text-primary-800">Screening evidence does not confirm government funding eligibility. Evidence uploads are not available here.</p>
      {applicationId ? <ApplicationDetails id={applicationId} /> : <ApplicationsList />}
    </div>
  </WorkspaceShell>;
}
