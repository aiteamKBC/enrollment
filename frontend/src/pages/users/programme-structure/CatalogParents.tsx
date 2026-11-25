import type { CatalogRecord, Detail, Programme } from './catalogApi';
import { SelectMenu } from '@/components/feature/SelectField';
import { btnSecondary } from '@/pages/users/components/ui';
import { catalogueFocus, catalogueInput } from './catalogControls';

export type ParentSelection = { programmeId: string; cohortId: string; groupId: string };
export type ParentResource = 'cohorts' | 'groups' | 'deliveries';
export const emptyParents: ParentSelection = { programmeId: '', cohortId: '', groupId: '' };

export function isEditableParent(record?: CatalogRecord) {
  return Boolean(record && !record.archived_at && record.source_system === 'manual');
}

export function matchingParents(detail: Detail | null, selection: ParentSelection) {
  const programme = detail?.programme.id === selection.programmeId && isEditableParent(detail?.programme)
    ? detail.programme : undefined;
  const cohorts = programme ? detail!.cohorts.filter(isEditableParent) : [];
  const cohort = cohorts.find(row => row.id === selection.cohortId);
  const groups = cohort ? detail!.groups.filter(row => row.cohort_id === cohort.id && isEditableParent(row)) : [];
  return { programme, cohorts, cohort, groups, group: groups.find(row => row.id === selection.groupId) };
}

export function hasValidParents(resource: ParentResource, detail: Detail | null, selection: ParentSelection) {
  const { programme, cohort, group } = matchingParents(detail, selection);
  return Boolean(programme && (resource === 'cohorts' || cohort) && (resource !== 'deliveries' || group));
}

export type CatalogParentsProps = {
  resource: ParentResource;
  programmes: Programme[];
  detail: Detail | null;
  selection: ParentSelection;
  loading: boolean;
  error: string | null;
  onChange: (selection: ParentSelection) => void;
  onRetry: () => void;
};

export function CatalogParents({ resource, programmes, detail, selection, loading, error, onChange, onRetry }: CatalogParentsProps) {
  const { programme, cohorts, cohort, groups } = matchingParents(detail, selection);
  const available = programmes.filter(isEditableParent);
  const needsCohort = resource !== 'cohorts';
  const needsGroup = resource === 'deliveries';
  return <div className="space-y-3">
    <p id="catalog-parents-help" className="text-sm text-foreground-600">
      {resource === 'cohorts' ? 'Choose the programme for this cohort.'
        : needsGroup ? 'Choose the programme, cohort and group that will receive this scheduled module.'
          : 'Choose the programme and cohort this group will belong to.'}
    </p>
    <div className={`grid gap-3 ${needsGroup ? 'sm:grid-cols-3' : 'sm:grid-cols-2'}`}>
      <div className="min-w-0 space-y-1">
        <label className="text-sm font-medium" htmlFor="catalog-parent-programme">Programme</label>
        {needsGroup ? <SelectMenu id="catalog-parent-programme" ariaLabel="Programme" ariaRequired
          value={selection.programmeId} onChange={programmeId => {
            if (programmeId !== selection.programmeId) onChange({ programmeId, cohortId: '', groupId: '' });
          }}
          options={available.map(row => ({ value: row.id, label: row.name, description: row.code }))}
          searchable clearable placeholder={available.length ? 'Select a programme' : 'No editable programmes'}
          searchPlaceholder="Search programmes by name or code" emptyMessage="No matching programmes."
          size="sm" disabled={!available.length} triggerClassName={catalogueFocus} />
        : <select required className={catalogueInput} id="catalog-parent-programme" name="parent-programme"
          aria-describedby="catalog-parents-help" disabled={!available.length} value={selection.programmeId}
          onChange={event => onChange({ programmeId: event.currentTarget.value, cohortId: '', groupId: '' })}>
          <option value="">{available.length ? 'Select a programme' : 'No editable programmes'}</option>
          {available.map(row => <option key={row.id} value={row.id}>{row.name} · {row.code}</option>)}
        </select>}
      </div>
      {needsCohort && <div className="min-w-0 space-y-1">
        <label className="text-sm font-medium" htmlFor="catalog-parent-cohort">Cohort</label>
        <select required className={`${catalogueInput} ${needsGroup ? 'min-h-11' : ''}`} id="catalog-parent-cohort" name="parent-cohort"
          aria-describedby="catalog-parents-help" value={selection.cohortId} disabled={loading || !programme || !cohorts.length}
          onChange={event => onChange({ ...selection, cohortId: event.currentTarget.value, groupId: '' })}>
          <option value="">{loading ? 'Loading cohorts…' : !programme ? 'Select a programme first' : !cohorts.length ? 'No editable cohorts' : 'Select a cohort'}</option>
          {cohorts.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}
        </select>
      </div>}
      {needsGroup && <div className="min-w-0 space-y-1">
        <label className="text-sm font-medium" htmlFor="catalog-parent-group">Group</label>
        <select required className={`${catalogueInput} min-h-11`} id="catalog-parent-group" name="parent-group"
          aria-describedby="catalog-parents-help" value={selection.groupId} disabled={loading || !cohort || !groups.length}
          onChange={event => onChange({ ...selection, groupId: event.currentTarget.value })}>
          <option value="">{!cohort ? 'Select a cohort first' : !groups.length ? 'No editable groups' : 'Select a group'}</option>
          {groups.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}
        </select>
      </div>}
    </div>
    {error ? <div role="alert" className="flex flex-wrap items-center gap-2 text-sm text-red-800">
      <p>Could not load this programme: {error}</p>
      <button type="button" className={`${btnSecondary} ${catalogueFocus}`} onClick={onRetry}>Retry loading parents</button>
    </div> : loading ? <p role="status" className="text-sm text-foreground-600">Loading programme records…</p>
      : !available.length ? <p className="text-sm text-foreground-600">Add an editable programme in the catalogue first.</p>
        : selection.programmeId && !programme ? <p role="status" className="text-sm text-foreground-600">This programme is no longer editable. Select another programme.</p>
          : needsCohort && programme && !cohorts.length ? <p className="text-sm text-foreground-600">Add a cohort to this programme before continuing.</p>
            : needsGroup && cohort && !groups.length ? <p className="text-sm text-foreground-600">Add a group to this cohort before scheduling a module.</p> : null}
  </div>;
}
