import { useEffect, useState } from 'react';
import { request } from '@/features/programme-suitability/api';
import { moduleResults, type YourModulesData } from '@/features/programme-suitability/your-modules/model';
import { ModuleTimeline } from '@/features/programme-suitability/your-modules/ModuleTimeline';
import { useWizard } from '../WizardContext.helpers';

export default function YourModules() {
  const { userId, isCommercial, ilrSavedAt, radarImpact, radarBoundModules, retryRadarImpact, suitability } = useWizard();
  const [state, setState] = useState<{ data?: YourModulesData; error?: string }>({});
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const refresh = () => setRetry(value => value + 1);
    window.addEventListener('focus', refresh);
    return () => window.removeEventListener('focus', refresh);
  }, []);
  useEffect(() => {
    let cancelled = false;
    setState({});
    request<YourModulesData>(`/enrolment_api/programme-suitability/${isCommercial ? 'commercial' : 'apprenticeship'}/${userId}/your-modules/`)
      .then(data => { if (!cancelled) setState({ data }); })
      .catch((error: Error) => { if (!cancelled) setState({ error: error.message }); });
    return () => { cancelled = true; };
  }, [userId, isCommercial, retry, ilrSavedAt, suitability?.state?.selectedProgramme]);
  const reload = () => { setRetry(value => value + 1); retryRadarImpact?.(); };
  const retryButton = <button type="button" className="mt-2 rounded-lg border border-purple-300 px-3 py-1 text-sm text-purple-900" onClick={reload}>Retry</button>;
  if (state.error) return <section aria-label="Your Modules"><h2 className="text-xl font-semibold">Your Modules</h2><p role="alert" className="mt-3 text-red-700">{state.error}</p>{retryButton}</section>;
  if (!state.data) return <p role="status">Loading Your Modules…</p>;
  const data = state.data;
  if (data.status !== 'ready') return <section aria-label="Your Modules"><h2 className="text-xl font-semibold">Your Modules</h2><p role="status" className="mt-3">{data.message}</p>{retryButton}</section>;
  const { modules, results, selectionKnown } = moduleResults(data, radarImpact, radarBoundModules ?? []);
  if (!selectionKnown) return <section aria-label="Your Modules"><h2 className="text-xl font-semibold">Your Modules</h2><p role="status" className="mt-3">Your saved PCP pathway and module choices cannot yet be verified against published Programme Structure module identities. Please contact admissions.</p>{retryButton}</section>;
  const unknown = modules.some(module => results.get(module.id) === 'unavailable');
  return <section aria-label="Your Modules">
    <h2 className="text-xl font-semibold text-purple-950">Your Modules</h2>
    <p className="mt-3 text-sm font-medium">{data.programmeName} · Cohort: {data.homeCohort?.name}</p>
    <p className="mt-2 text-xs">Potential Prior Learning is an assessment indication, not an approved RPL decision. All programme requirements remain applicable.</p>
    {unknown && <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm" role="status">
      Some saved Skills Radar module results could not be verified. An unavailable result is not a negative assessment. Alternative schedules are shown for all applicable modules when at least one module has a verified Potential Prior Learning indication.
      <div>{retryButton}</div>
    </div>}
    {!modules.length ? <p className="mt-4">No academic modules are published for this programme.</p> : <>
      <div className="mt-4 overflow-x-auto rounded-xl border border-purple-200">
        <table className="w-full text-left text-sm">
          <thead className="bg-purple-100"><tr><th className="p-3">Module</th><th className="p-3">Duration</th><th className="p-3">Skills Radar Result</th></tr></thead>
          <tbody>{modules.map(module => <tr key={module.id} className={`border-t border-purple-100 ${results.get(module.id) === 'potential' ? 'bg-purple-100' : ''}`}>
            <td className="p-3">{module.name}</td><td className="whitespace-nowrap p-3">{module.weeks} {module.weeks === 1 ? 'Week' : 'Weeks'}</td>
            <td className="p-3">{results.get(module.id) === 'potential' ? <><strong className="text-purple-900">Potential Prior Learning</strong><p className="mt-1 text-xs">You may have potential prior learning in this module based on your Skills Radar assessment.</p></> : results.get(module.id) === 'none' ? 'No prior-learning indication' : 'Skills Radar result unavailable'}</td>
          </tr>)}</tbody>
        </table>
      </div>
      <ModuleTimeline data={data} modules={modules} results={results} />
    </>}
  </section>;
}
