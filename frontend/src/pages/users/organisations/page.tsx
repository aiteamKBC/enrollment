import { useEffect, useRef, useState, type FormEvent } from 'react';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { AppIcon } from '@/components/feature/AppIcon';
import { AdminPageHeader, DataPanel, Pager, StatusBadge } from '@/pages/admin/_shared/AdminPage';
import { roleNavMap } from '@/mocks/navigation';
import { listOrganisations, type OrganisationPage } from '@/api/employers';
import { CreateOrganisationModal } from '../components/CreateOrganisationModal';
import { btnPrimary, btnSecondary, inputClass } from '../components/ui';

type Filters = { postcode: string; status: string; includeMerged: boolean };
const EMPTY_FILTERS: Filters = { postcode: '', status: '', includeMerged: false };
const sameFilters = (left: Filters, right: Filters) =>
  left.postcode === right.postcode && left.status === right.status && left.includeMerged === right.includeMerged;

export default function OrganisationsPage() {
  const [draft, setDraft] = useState<Filters>(EMPTY_FILTERS);
  const [applied, setApplied] = useState<Filters>(EMPTY_FILTERS);
  const [nameSearch, setNameSearch] = useState('');
  const [searchedName, setSearchedName] = useState('');
  const searchInputRef = useRef<HTMLInputElement>(null);
  const [page, setPage] = useState(1);
  const [reload, setReload] = useState(0);
  const [result, setResult] = useState<OrganisationPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  // Search the full paged directory, not just the currently loaded page.
  // A short debounce avoids a request for every keystroke.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setPage(1);
      setSearchedName(nameSearch.trim());
    }, 200);
    return () => window.clearTimeout(timer);
  }, [nameSearch]);

  useEffect(() => {
    let current = true;
    setLoading(true);
    setError(null);
    listOrganisations({
      search: searchedName,
      postcode: applied.postcode,
      status: applied.status,
      includeMerged: applied.includeMerged,
      page,
    }).then((data) => {
      if (current) setResult(data);
    }).catch((reason: unknown) => {
      if (current) setError(reason instanceof Error ? reason.message : 'Could not load organisations.');
    }).finally(() => {
      if (current) setLoading(false);
    });
    return () => { current = false; };
  }, [applied, searchedName, page, reload]);

  const applyFilters = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const next = { ...draft, postcode: draft.postcode.trim() };
    if (sameFilters(next, applied)) return;
    setPage(1);
    setApplied(next);
  };

  const clearFilters = () => {
    setDraft(EMPTY_FILTERS);
    if (sameFilters(applied, EMPTY_FILTERS)) return;
    setApplied(EMPTY_FILTERS);
    setPage(1);
  };

  const afterCreate = () => {
    setNameSearch('');
    setSearchedName('');
    setDraft(EMPTY_FILTERS);
    setApplied(EMPTY_FILTERS);
    setPage(1);
    setReload((value) => value + 1);
  };

  return (
    <WorkspaceShell
      role="compliance"
      roleLabel={roleNavMap.apprentice.label}
      navItems={roleNavMap.apprentice.items}
      workspaceLabel={roleNavMap.apprentice.workspaceLabel}
      pageTitle="Organisations"
      pageSubtitle="Directory of employing organisations"
    >
      <div className="admin-console-page min-w-0 space-y-4 p-3 md:space-y-5 md:p-6">
        <AdminPageHeader
          title="Organisation Management"
          description="Find employing organisations and create a new organisation profile."
          icon="ri-building-line"
          actions={
            <button type="button" className={btnPrimary} onClick={() => setCreating(true)}>
              <AppIcon className="ri-add-line" />Create organisation
            </button>
          }
        />

        <form onSubmit={applyFilters} className="overflow-hidden rounded-2xl border border-foreground-100 bg-background-50 shadow-sm">
          <div className="flex items-center gap-3 border-b border-foreground-100 px-4 py-3 md:px-5">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-primary-100 bg-primary-50 text-primary-600"><AppIcon className="ri-filter-3-line" /></span>
            <div>
              <h2 className="text-[14px] font-semibold text-foreground-900">Filter organisations</h2>
              <p className="mt-0.5 text-[11px] text-foreground-400">Narrow the list by postcode and status.</p>
            </div>
          </div>
          <div className="grid gap-4 p-4 sm:grid-cols-2 md:p-5">
            <label className="block text-[12px] font-semibold text-foreground-800">
              Postcode
              <input className={`${inputClass} mt-1`} value={draft.postcode} onChange={(event) => setDraft((value) => ({ ...value, postcode: event.target.value }))} placeholder="Search by postcode" />
            </label>
            <label className="block text-[12px] font-semibold text-foreground-800">
              Status
              <select className={`${inputClass} mt-1`} value={draft.status} onChange={(event) => setDraft((value) => ({ ...value, status: event.target.value }))}>
                <option value="">All statuses</option>
                <option value="Confirmed">Confirmed</option>
                <option value="Opportunity">Opportunity</option>
                <option value="Archived">Archived</option>
              </select>
            </label>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3 px-4 pb-4 md:px-5 md:pb-5">
            <label className="flex cursor-pointer items-center gap-2 text-[12px] text-foreground-700">
              <input type="checkbox" className="accent-primary-600" checked={draft.includeMerged} onChange={(event) => setDraft((value) => ({ ...value, includeMerged: event.target.checked }))} />
              Include merged organisations
            </label>
            <div className="flex items-center gap-2">
              <button type="button" className={btnSecondary} onClick={clearFilters}>Clear all</button>
              <button type="submit" className={btnPrimary}><AppIcon className="ri-filter-3-line" />Apply filters</button>
            </div>
          </div>
        </form>

        <div className="overflow-hidden rounded-2xl border border-foreground-100 bg-background-50 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 md:px-5">
            <div className="flex items-center gap-3">
              <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-primary-100 bg-primary-50 text-primary-600"><AppIcon className="ri-building-line" /></span>
              <h2 className="text-[14px] font-semibold text-foreground-900">Organisations ({result?.count ?? 0})</h2>
            </div>
            <div role="search" aria-label="Organisation directory" className="relative w-full sm:ml-auto sm:w-80">
              <label htmlFor="organisations-search" className="sr-only">Search organisation names</label>
              <AppIcon className="ri-search-line pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-foreground-400" />
              <input
                ref={searchInputRef}
                id="organisations-search"
                type="search"
                value={nameSearch}
                onChange={(event) => setNameSearch(event.target.value)}
                placeholder="Search organisation names..."
                aria-controls="organisations-directory-table"
                className={`${inputClass} pl-9 pr-9 [&::-webkit-search-cancel-button]:appearance-none`}
              />
              {nameSearch && (
                <button
                  type="button"
                  aria-label="Clear search"
                  onClick={() => { setNameSearch(''); searchInputRef.current?.focus(); }}
                  className="absolute right-1 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-md text-foreground-400 hover:bg-background-100 hover:text-primary-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-400"
                >
                  <AppIcon className="ri-close-line" />
                </button>
              )}
            </div>
          </div>
          <DataPanel loading={loading} error={error} onRetry={() => setReload((value) => value + 1)} className="rounded-none border-0 shadow-none">
            {result?.results.length ? (
              <div className="overflow-x-auto">
                <table id="organisations-directory-table" className="w-full min-w-[760px] text-left text-[13px]">
                  <thead className="bg-primary-50/60 text-[11px] font-semibold uppercase tracking-wide text-primary-700">
                    <tr>
                      <th scope="col" className="px-5 py-3">Organisation</th>
                      <th scope="col" className="px-4 py-3">Status</th>
                      <th scope="col" className="px-4 py-3">Postcode</th>
                      <th scope="col" className="px-4 py-3">Town/City</th>
                      <th scope="col" className="px-4 py-3">Contact</th>
                      <th scope="col" className="px-4 py-3">Owner</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-foreground-100">
                    {result.results.map((organisation) => (
                      <tr key={organisation.id} className="hover:bg-primary-50/40">
                        <td className="px-5 py-3 font-semibold text-foreground-900">{organisation.name}</td>
                        <td className="px-4 py-3"><StatusBadge status={organisation.mergedIntoOrganisationId ? 'Merged' : organisation.status || 'Unknown'} tone={organisation.mergedIntoOrganisationId ? 'neutral' : organisation.status === 'Confirmed' ? 'ok' : organisation.status === 'Archived' ? 'neutral' : 'warn'} /></td>
                        <td className="px-4 py-3 text-foreground-700">{organisation.postCode || '—'}</td>
                        <td className="px-4 py-3 text-foreground-700">{organisation.cityTown || '—'}</td>
                        <td className="px-4 py-3 text-foreground-700">
                          <span className="block">{organisation.contactName || '—'}</span>
                          {organisation.contactEmail && <span className="block text-[11px] text-foreground-500">{organisation.contactEmail}</span>}
                        </td>
                        <td className="px-4 py-3 text-foreground-700">{organisation.owner || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <p className="border-t border-foreground-100 px-5 py-8 text-center text-[13px] text-foreground-500">No organisations match your search or filters.</p>}
            {result && <Pager page={page} pageSize={result.pageSize} count={result.count} onPage={setPage} />}
          </DataPanel>
        </div>
      </div>
      {creating && <CreateOrganisationModal onClose={() => setCreating(false)} onCreated={afterCreate} />}
    </WorkspaceShell>
  );
}
