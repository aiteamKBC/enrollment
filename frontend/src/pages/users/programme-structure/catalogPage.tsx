import { useEffect, useLayoutEffect, useRef, useState, type MouseEvent, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ArrowLeft, ArrowRight, BookOpen, ChevronRight, FlaskConical, Plus, Search } from 'lucide-react';
import { AdminPage, DataPanel } from '@/pages/admin/_shared/AdminPage';
import { btnPrimary, btnSecondary } from '@/pages/users/components/ui';
import { CatalogEditor, editorTitle, recordLabels, weekdays, type EditorState } from './CatalogEditor';
import { CurriculumContent } from './CatalogCurriculumContent';
import { CatalogKsbMapping } from './CatalogKsbMapping';
import { hasValidParents, isEditableParent, type ParentSelection } from './CatalogParents';
import { PricingWorkspace } from './CatalogPricing';
import { KsbLibraryCsv } from './KsbLibraryCsv';
import { CatalogLmsPanel } from './CatalogLmsPanel';
import { CatalogModuleCountsEditor } from './CatalogModuleCountsEditor';
import { SkillsRadarThreshold } from './SkillsRadarThreshold';
import {
  CatalogRequestError, catalogRequest, programmePath, programmesPath,
  type CatalogRecord, type Cohort, type CurriculumModule, type Detail, type Programme, type ProgrammeCounts, type ProgrammeListRecord, type Resource, type StaffOptions,
} from './catalogApi';
import { calculatePricing, gbp, inclusiveDays, monthCount, showDate, showDateRange, showTime } from './catalogMath';
import { catalogueFocus as focus, catalogueInput } from './catalogControls';
import { archivePreview, importPreviewKsbs, savePreview, type PreviewCatalogue } from './catalogPreviewStore';
import { validateDraft, validateModuleKsbs, type CatalogValues } from './catalogValidation';
import type { KsbFields } from './ksbCsv';
import { useCatalogueResource } from './useCatalogueResource';
import { catalogueSearch, curriculumTabs, sections, useCatalogueNavigation, type CatalogView, type SectionId } from './catalogNavigation';

const surface = 'rounded-xl border border-background-200 bg-background-50';
const statusFilters = ['all', 'active', 'draft', 'archived'] as const;
const action = `${btnSecondary} ${focus} disabled:cursor-not-allowed disabled:opacity-50`;
const activeRows = <Row extends CatalogRecord>(rows: Row[], includeArchived: boolean) => rows.filter(row => includeArchived || !row.archived_at);
const locked = (row?: CatalogRecord) => Boolean(row && (row.archived_at || row.source_system !== 'manual'));
const staffName = (id: string | null, people: StaffOptions['coaches'] | undefined) =>
  id ? people?.find(member => member.id === id)?.name ?? 'Assigned staff unavailable' : 'Unassigned';
function recordName(record: Programme | Detail[Resource][number]) {
  if ('curriculum' in record) return record.curriculum.name;
  return 'name' in record ? record.name : record.code;
}
const plannedDuration = (cohort: Cohort) => cohort.training_duration_months == null
  ? 'Not recorded' : monthCount(cohort.training_duration_months);
function totalApprenticeshipDuration(cohort: Cohort) {
  if (cohort.apprenticeship_duration_months != null && !cohort.training_end_override && !cohort.apprenticeship_end_override) {
    return monthCount(cohort.apprenticeship_duration_months);
  }
  const days = inclusiveDays(cohort.start_date, cohort.apprenticeship_end_date);
  return days == null ? 'Not calculated' : `${days} days`;
}
function AddButton({ label, onClick, disabled = false }: { label: string; onClick: () => void; disabled?: boolean }) {
  return <button type="button" className={`${btnPrimary} ${focus}`} onClick={onClick} disabled={disabled}><Plus size={16} aria-hidden />{label}</button>;
}

function Heading({ id, title, description, actions }: { id?: string; title: string; description?: ReactNode; actions?: ReactNode }) {
  return <div className="flex flex-wrap items-start justify-between gap-3">
    <div className="min-w-0"><h2 id={id} className="break-words text-xl font-semibold tracking-tight text-foreground-900 sm:text-2xl">{title}</h2>
      {description && <p className="mt-1 max-w-2xl text-sm leading-relaxed text-foreground-600">{description}</p>}</div>
    {actions}
  </div>;
}

function Stats({ items }: { items: [string, ReactNode][] }) {
  return <dl className={`${surface} grid grid-cols-2 gap-x-5 gap-y-4 p-4 sm:p-5 lg:grid-cols-4`}>
    {items.map(([label, value]) => <div key={label} className="min-w-0">
      <dt className="text-xs font-medium text-foreground-600">{label}</dt>
      <dd className="mt-1 break-words text-sm font-semibold tabular-nums">{value}</dd>
    </div>)}
  </dl>;
}

function StateBadge({ value }: { value: string }) {
  return <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium capitalize ${value === 'active' ? 'bg-emerald-100 text-emerald-800' : 'bg-background-200 text-foreground-700'}`}>{value}</span>;
}

function ListSearch({ label, query, onSearch }: { label: string; query: string; onSearch: (value: string) => void }) {
  return <label className="block w-full max-w-md space-y-1 text-sm font-medium">
    <span>{label}</span>
    <span className="relative block"><Search className="pointer-events-none absolute left-3 top-3 text-foreground-500" size={16} aria-hidden />
      <input className={`${catalogueInput} pl-9`} type="search" value={query} onChange={event => onSearch(event.currentTarget.value)} />
    </span>
  </label>;
}

function deliveryCounts(detail: Detail): ProgrammeCounts {
  const { programme } = detail;
  const cohorts = programme.archived_at ? [] : detail.cohorts.filter(row => !row.archived_at);
  const cohortIds = new Set(cohorts.map(row => row.id));
  const groups = detail.groups.filter(row => !row.archived_at && cohortIds.has(row.cohort_id));
  const groupIds = new Set(groups.map(row => row.id));
  const modules = detail.deliveries.filter(row => !row.archived_at && groupIds.has(row.group_id));
  return { cohorts: cohorts.length, groups: groups.length, scheduled_modules: modules.length };
}

function ProgrammeOverview({ detail, preview }: { detail: Detail; preview: boolean }) {
  const { programme } = detail;
  const quote = calculatePricing(programme, [], preview);
  const counts = deliveryCounts(detail);
  const templates = programme.archived_at ? [] : detail.modules.filter(row => !row.archived_at);
  return <div className="space-y-4">
    <section aria-label="Programme summary">
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {([
          ['Total per apprentice', gbp(programme.total_cost, preview)],
          ['EPA cost', gbp(programme.epa_cost, preview)],
          ['Base cost excluding EPA', gbp(quote?.base_cost ?? null, preview)],
        ]).map(([label, value]) => <div className={`${surface} min-w-0 p-3 sm:p-4`} key={label}>
          <dt className="text-xs font-medium text-foreground-600">{label}</dt>
          <dd className="mt-1 break-words text-lg font-semibold tabular-nums sm:text-xl">{value}</dd>
        </div>)}
      </dl>
    </section>
    <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <section className={`${surface} min-w-0 p-4 sm:p-5`}>
        <h3 className="font-semibold">Programme details</h3>
        <dl className="mt-3 space-y-3 text-sm">
          <div><dt className="font-medium">Description</dt><dd className="mt-1 max-w-prose whitespace-pre-wrap break-words text-foreground-600">{programme.description || 'No description added.'}</dd></div>
          <div className="border-t border-background-200 pt-3"><dt className="font-medium">End-point assessment organisation</dt><dd className="mt-1 break-words text-foreground-600">{programme.epao || 'Not confirmed'}</dd></div>
        </dl>
      </section>
      <section className={`${surface} p-4 sm:p-5`}>
        <h3 className="font-semibold">Delivery at a glance</h3>
        <p className="mt-1 text-sm text-foreground-600">Current records only. Archived records are excluded.</p>
        <dl className="mt-3 divide-y divide-background-200 text-sm">
          {([['Cohorts', counts.cohorts], ['Groups', counts.groups], ['Scheduled modules', counts.scheduled_modules], ['Module templates', templates.length]] as const)
            .map(([label, value]) => <div key={label} className="flex justify-between gap-3 py-2">
              <dt>{label}</dt><dd className="font-semibold tabular-nums">{value}</dd>
            </div>)}
        </dl>
      </section>
    </div>
  </div>;
}

type ListRow = { id: string; name: string; caption?: string; cells: ReactNode[]; open?: () => void };
function RecordList({ labels, rows, empty, onClearSearch, clearLabel = 'Clear search' }: {
  labels: string[]; rows: ListRow[]; empty: string; onClearSearch?: () => void; clearLabel?: string;
}) {
  if (!rows.length) return <div className={`${surface} space-y-3 px-4 py-6 text-sm text-foreground-600`}>
    <p>{empty}</p>
    {onClearSearch && <button type="button" className={action} onClick={onClearSearch}>{clearLabel}</button>}
  </div>;
  const columns = { gridTemplateColumns: `minmax(9rem, 1.8fr) repeat(${labels.length}, minmax(0, 1fr))` };
  return <div className={surface}>
    <div className="hidden gap-3 rounded-t-xl border-b border-background-200 bg-background-100 px-4 py-2.5 text-xs font-medium text-foreground-600 lg:grid" style={columns} aria-hidden>
      <span>Name</span>{labels.map(label => <span key={label}>{label}</span>)}
    </div>
    <ul className="divide-y divide-background-200">
      {rows.map(row => <li key={row.id} className="px-4 py-3 lg:grid lg:items-center lg:gap-3" style={columns}>
        <div className="min-w-0">
          {row.open ? <button type="button" className={`inline-flex max-w-full items-center gap-1.5 text-left text-sm font-semibold text-primary-700 hover:underline ${focus}`} onClick={row.open}>
            <span className="min-w-0 break-words">{row.name}</span><ArrowRight size={14} className="shrink-0" aria-hidden />
          </button> : <p className="break-words text-sm font-semibold">{row.name}</p>}
          {row.caption && <p className="mt-0.5 break-words text-xs text-foreground-600">{row.caption}</p>}
        </div>
        <dl className="mt-3 grid grid-cols-2 gap-2 text-sm lg:contents">
          {row.cells.map((value, index) => <div key={labels[index]} className="min-w-0">
            <dt className="text-xs text-foreground-600 lg:sr-only">{labels[index]}</dt>
            <dd className="break-words">{value}</dd>
          </div>)}
        </dl>
      </li>)}
    </ul>
  </div>;
}

export default function ProgrammeCatalogPage({ preview = false }: { preview?: boolean } = {}) {
  const [memory, setMemory] = useState<PreviewCatalogue>([]);
  const [previewStaff, setPreviewStaff] = useState<StaffOptions>({ coaches: [], tutors: [] });
  const [staffRevision, setStaffRevision] = useState(0);
  const [revision, setRevision] = useState(0);
  const [showArchived, setShowArchived] = useState(false);
  const [statusFilter, setStatusFilter] = useState<typeof statusFilters[number]>('all');
  const [query, setQuery] = useState('');
  const [cohortQuery, setCohortQuery] = useState('');
  const [parentDraft, setParentDraft] = useState<{ key: string; selection: ParentSelection }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState(false);
  const [notice, setNotice] = useState<{ text: string; search: string }>();
  const content = useRef<HTMLDivElement>(null);
  const navigation = useCatalogueNavigation(content, busy);
  const { location, panel, view, restore, reset } = navigation;
  const { programmeId = '', section = 'overview', cohortId = '', groupId = '', deliveryId = '', templateId = '', curriculumTab = 'templates' } = view;
  const resetPreview = useRef(preview && Boolean(location.search || panel));
  const refresh = () => setRevision(value => value + 1);
  const listing = useCatalogueResource<{ programmes: ProgrammeListRecord[] }>(preview ? null : programmesPath, revision);
  const liveCatalogueAvailable = !preview && Boolean(listing.data);
  const detailRequest = useCatalogueResource<Detail>(liveCatalogueAvailable && programmeId ? programmePath(programmeId) : null, revision);
  const needsStaff = programmeId || panel?.resource === 'groups' || panel?.resource === 'deliveries';
  const staffRequest = useCatalogueResource<StaffOptions>(liveCatalogueAvailable && needsStaff ? 'staff/' : null, staffRevision);
  const staff = preview ? previewStaff : staffRequest.data;
  const detail = preview ? memory.find(row => row.programme.id === programmeId) ?? null : detailRequest.data;
  const panelRecord = panel?.recordId && detail ? panel.resource === 'programme'
    ? detail.programme : detail[panel.resource].find(row => row.id === panel.recordId) : undefined;
  const panelTarget = panel && (!panel.recordId || panelRecord) ? {
    resource: panel.resource, record: panelRecord, parentId: panel.parentId,
  } : null;
  const editor = panel?.kind === 'form' ? panelTarget : null;
  const archiveTarget = panel?.kind === 'archive' && panelTarget?.record ? {
    ...panelTarget, record: panelTarget.record, label: recordName(panelTarget.record),
  } : null;
  const creationParents = parentDraft?.key === location.key ? parentDraft.selection : {
    programmeId,
    cohortId: panel?.resource === 'groups' ? panel.parentId ?? '' : panel?.resource === 'deliveries' ? cohortId : '',
    groupId: panel?.resource === 'deliveries' ? panel.parentId ?? '' : '',
  };
  const parentResource = editor && !editor.record && (editor.resource === 'cohorts' || editor.resource === 'groups' || editor.resource === 'deliveries')
    ? editor.resource : null;
  const selectedProgrammeId = parentResource ? creationParents.programmeId : '';
  const parentRequest = useCatalogueResource<Detail>(
    liveCatalogueAvailable && selectedProgrammeId && selectedProgrammeId !== programmeId ? programmePath(selectedProgrammeId) : null, revision,
  );
  const selectedRequest = selectedProgrammeId === programmeId ? detailRequest : parentRequest;
  const editorDetail = !parentResource ? detail : !selectedProgrammeId ? null : preview
    ? memory.find(row => row.programme.id === selectedProgrammeId) ?? null : selectedRequest.data;
  const effectiveEditor = editor && parentResource ? {
    ...editor, parentId: parentResource === 'groups' ? creationParents.cohortId : parentResource === 'deliveries' ? creationParents.groupId : undefined,
  } : editor;
  const programme = detail?.programme;
  const cohort = detail?.cohorts.find(row => row.id === cohortId);
  const group = detail?.groups.find(row => row.id === groupId && row.cohort_id === cohortId);
  const delivery = detail?.deliveries.find(row => row.id === deliveryId && row.group_id === groupId);
  const template = detail?.modules.find(row => row.id === templateId);
  const viewMissing = Boolean(programmeId && !programme || cohortId && !cohort || groupId && !group ||
    deliveryId && !delivery || templateId && !template || panel?.recordId && !panelRecord);
  const programmeLocked = locked(programme);
  const cohortLocked = programmeLocked || locked(cohort);
  const groupLocked = cohortLocked || locked(group);
  const allProgrammes = preview ? memory.map(row => ({ ...row.programme, counts: deliveryCounts(row) })) : listing.data?.programmes ?? [];
  const selectableProgrammes = allProgrammes.filter(isEditableParent);
  const catalogueReady = !listing.loading && !listing.error;
  const emptyCatalogue = catalogueReady && !allProgrammes.length;
  const matching = allProgrammes.filter(row => (statusFilter === 'all' || row.status === statusFilter) &&
    `${row.name} ${row.code} ${row.status} level ${row.level}`.toLowerCase().includes(query.trim().toLowerCase()));
  const matchingCohorts = activeRows(detail?.cohorts ?? [], showArchived).filter(row =>
    `${row.name} ${row.start_date} ${row.training_end_date} ${showDate(row.start_date)} ${showDate(row.training_end_date)}`.toLowerCase().includes(cohortQuery.trim().toLowerCase()));

  useLayoutEffect(() => {
    if (!detailRequest.loading && !listing.loading) restore();
  }, [detailRequest.loading, listing.loading, restore]);

  useEffect(() => {
    if (!resetPreview.current) return;
    resetPreview.current = false;
    reset();
  }, [reset]);

  useEffect(() => {
    setError(''); setConflict(false);
    if (!panel) setParentDraft(undefined);
  }, [location.key, panel]);
  useEffect(() => { setCohortQuery(''); }, [programmeId]);

  const programmeView = { programmeId, section, curriculumTab: section === 'curriculum' ? curriculumTab : undefined };
  const cohortView = { ...programmeView, cohortId };
  const groupView = { ...cohortView, groupId };
  function navigateProgramme(id: string, next: SectionId = 'overview') { navigation.go({ programmeId: id, section: next }); }
  function navigateCohort(id: string) { navigation.go({ ...programmeView, cohortId: id }); }
  function navigateGroup(id: string) { navigation.go({ ...cohortView, groupId: id }); }
  function editRecord(next: EditorState) { setNotice(undefined); navigation.openPanel('form', next); }

  const recordPath = ({ resource, record }: EditorState, scopeId = programmeId) => resource === 'programme'
    ? record ? programmePath(record.id) : programmesPath
    : `${programmePath(scopeId)}${resource}/${record ? `${record.id}/` : ''}`;

  function failureMessage(failure: unknown) {
    setConflict(failure instanceof CatalogRequestError && failure.code === 'catalog_revision_conflict');
    setError(failure instanceof Error ? failure.message : 'Could not complete the change.');
  }

  function reloadAfterConflict() { navigation.finish(view); refresh(); }
  function cancelPanel() {
    if (navigation.closePanel() && conflict) refresh();
  }

  function persistRecord(selectedEditor: EditorState, scopeId: string, data: CatalogValues, expectedRevision?: number) {
    if (preview) {
      const saved = savePreview(memory, scopeId, selectedEditor, data);
      setMemory(saved.catalogue);
      return saved.record;
    }
    return catalogRequest<CatalogRecord>(recordPath(selectedEditor, scopeId), {
      method: selectedEditor.record ? 'PATCH' : 'POST', revision: expectedRevision, data,
    }).then(record => {
      refresh();
      return record;
    });
  }

  async function saveAssignments(module: CurriculumModule, ids: string[]) {
    if (busy || conflict || !detail || programmeLocked) return;
    setError(''); setBusy(true);
    try {
      const latest = detail.modules.find(row => row.id === module.id);
      if (!isEditableParent(latest)) throw new Error('This module template is no longer editable.');
      if (preview && latest?.revision !== module.revision) {
        throw new CatalogRequestError('Another change was saved. Reload the latest module before trying again.', 'catalog_revision_conflict');
      }
      validateModuleKsbs(ids, detail, module.ksb_ids);
      await persistRecord({ resource: 'modules', record: module }, programmeId, { ksb_ids: ids }, module.revision);
      navigation.finish(view);
      setNotice({ search: catalogueSearch(view), text: preview ? 'KSB assignments updated in preview.' : 'KSB assignments saved.' });
    } catch (failure) { failureMessage(failure); }
    finally { setBusy(false); }
  }

  async function importKsbRows(rows: KsbFields[]) {
    if (preview) setMemory(importPreviewKsbs(memory, programmeId, rows));
    else {
      await catalogRequest(recordPath({ resource: 'ksbs' }), { method: 'POST', data: { ksbs: rows } });
      setNotice({ search: catalogueSearch(view), text: `${rows.length} ${rows.length === 1 ? 'KSB' : 'KSBs'} imported.` });
      refresh();
    }
  }

  async function save(data: CatalogValues) {
    if (!effectiveEditor || busy || conflict) return;
    const selectedEditor = effectiveEditor;
    const scopeId = parentResource ? selectedProgrammeId : programmeId;
    setError(''); setBusy(true);
    try {
      if (parentResource && (selectedRequest.loading || selectedRequest.error || !hasValidParents(parentResource, editorDetail, creationParents))) {
        throw new Error('Select valid parent records before continuing.');
      }
      if (!(selectedEditor.resource === 'modules' && selectedEditor.record?.source_system === 'lms')) {
        validateDraft(selectedEditor, data, editorDetail, preview);
      }
      const result = persistRecord(selectedEditor, scopeId, data, panel?.revision);
      const record = result instanceof Promise ? await result : result;
      let target = view;
      if (!selectedEditor.record) {
        const parent = { programmeId: scopeId, section: scopeId === programmeId ? section : 'cohorts' as const };
        switch (selectedEditor.resource) {
          case 'programme': target = { programmeId: record.id, section: 'overview' }; break;
          case 'cohorts': target = { ...parent, cohortId: record.id }; break;
          case 'groups': target = { ...parent, cohortId: creationParents.cohortId, groupId: record.id }; break;
          case 'deliveries': target = { ...parent, cohortId: creationParents.cohortId, groupId: creationParents.groupId, deliveryId: record.id }; break;
          case 'modules': if (!groupId) target = { programmeId, section: 'curriculum', templateId: record.id }; break;
        }
      }
      navigation.finish(target);
      setNotice({ search: catalogueSearch(target), text: `${recordLabels[selectedEditor.resource]} ${preview
        ? selectedEditor.record ? 'updated in preview' : 'added to preview' : 'saved'}.` });
    } catch (failure) { failureMessage(failure); }
    finally { setBusy(false); }
  }

  async function archive() {
    if (!archiveTarget || busy || conflict) return;
    setError(''); setBusy(true);
    try {
      if (preview) setMemory(archivePreview(memory, programmeId, archiveTarget));
      else {
        await catalogRequest(recordPath(archiveTarget), { method: 'DELETE', revision: panel?.revision });
        refresh();
      }
      const target = archiveTarget.resource === 'programme' ? {} : archiveTarget.resource === 'groups' ? cohortView
        : archiveTarget.resource === 'deliveries' ? groupView
          : archiveTarget.resource === 'ksbs' ? view : programmeView;
      navigation.finish(target);
      setNotice({ search: catalogueSearch(target), text: `${recordLabels[archiveTarget.resource]} ${preview ? 'removed from preview' : 'archived'}.` });
    } catch (failure) { failureMessage(failure); }
    finally { setBusy(false); }
  }

  function recordActions(resource: EditorState['resource'], record: CatalogRecord, label: string, parentLocked: boolean, parentId?: string) {
    if (parentLocked || locked(record)) return null;
    return <div className="flex flex-wrap gap-2">
      <button className={action} disabled={detailRequest.loading} aria-label={`Edit ${label}`} onClick={() => editRecord({ resource, record, parentId })}>Edit</button>
      <button className={action} disabled={detailRequest.loading} aria-label={`${preview ? 'Remove' : 'Archive'} ${label}`}
        onClick={() => { setNotice(undefined); navigation.openPanel('archive', { resource, record, parentId }); }}>{preview ? 'Remove' : 'Archive'}</button>
    </div>;
  }

  const crumbs: [string, CatalogView][] = [['Programme catalogue', {}]];
  if (programme) crumbs.push([programme.name, programmeView]);
  if (cohort) crumbs.push([cohort.name, cohortView]);
  if (group) crumbs.push([group.name, groupView]);
  if (delivery) crumbs.push([delivery.curriculum.name, view]);
  if (template) crumbs.push([template.name, view]);
  const currentCrumb = panel ? panel.kind === 'archive' ? preview ? 'Remove' : 'Archive'
    : panel.kind === 'assignments' ? 'Edit assignments' : editorTitle(panel.resource, Boolean(panel.recordId)) : undefined;
  const back = crumbs.at(panel ? -1 : -2);

  function follow(event: MouseEvent<HTMLAnchorElement>, target: CatalogView) {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigation.go(target);
  }

  return <AdminPage compact title="Programme Structure" subtitle="Manage programmes, cohorts, groups and scheduled modules" icon="ri-node-tree"
    heroTitle="Programme catalogue" heroBlurb="">
    {!preview && !liveCatalogueAvailable ? <section aria-label="Programme Structure availability" className="mx-auto max-w-screen-2xl space-y-4">
      {!listing.loading && listing.errorCode === 'catalog_schema_not_ready' ? (
        <div className={`${surface} space-y-3 p-4 sm:p-5`}>
          <div role="status">
            <h2 className="font-semibold">Catalogue setup required</h2>
            <p className="mt-1 max-w-prose text-sm text-foreground-700">
              An administrator needs to apply the Programme Catalogue migrations (0013–0018) and any pending prerequisites to this environment’s Enrolment database before programmes can be listed or saved.
            </p>
          </div>
          {import.meta.env.DEV && <p id="catalogue-preview-note" className="max-w-prose text-sm text-foreground-700">
            Try the interactive preview with temporary data. Changes stay on this page and are cleared when you reload.
          </p>}
          <div className="flex flex-wrap items-center gap-2">
            {import.meta.env.DEV && <Link to="/users/programme-structure/preview" aria-describedby="catalogue-preview-note" className={`${btnPrimary} ${focus}`}>
              Open interactive preview
            </Link>}
            <button type="button" className={action} onClick={refresh}>Check again</button>
          </div>
        </div>
      ) : (
        <DataPanel loading={listing.loading} error={listing.error} onRetry={refresh}
          skeleton={<p role="status" className="text-sm text-foreground-600">Loading programmes…</p>}>{null}</DataPanel>
      )}
    </section> :
    <div className="mx-auto max-w-screen-2xl space-y-4">
        {(programmeId || panel) && <nav aria-label="Breadcrumb" className="min-w-0">
          <ol className="flex flex-wrap items-center gap-1 text-sm">
            {crumbs.map(([label, target], index) => <li key={index} className="flex min-w-0 items-center gap-1">
              {index > 0 && <ChevronRight size={14} className="shrink-0 text-foreground-500" aria-hidden />}
              {index === crumbs.length - 1 && !currentCrumb ? <span aria-current="page" className="break-words font-semibold">{label}</span>
                : <Link data-catalogue-navigation="true" to={navigation.destination(target)} onClick={event => follow(event, target)}
                  className={`break-words text-primary-700 hover:underline ${focus}`}>{label}</Link>}
            </li>)}
            {currentCrumb && <li className="flex min-w-0 items-center gap-1">
              <ChevronRight size={14} className="shrink-0 text-foreground-500" aria-hidden />
              <span aria-current="page" className="break-words font-semibold">{currentCrumb}</span>
            </li>}
          </ol>
        </nav>}
      {preview && <div role="note" className="flex items-start gap-2 rounded-lg border border-primary-200 bg-primary-50 px-3 py-2.5 text-sm text-foreground-800">
        <FlaskConical size={16} className="mt-0.5 shrink-0 text-primary-700" aria-hidden />
        <p><strong>Interactive Preview</strong> — Changes are temporary and will not be saved.</p>
      </div>}
      <div ref={content} tabIndex={-1} className="space-y-4 outline-none">
        {back && <button type="button" className={action} disabled={busy}
          onClick={() => panel ? cancelPanel() : navigation.go(back[1])}>
          <ArrowLeft size={16} aria-hidden /> Back <span className="sr-only">to {back[0]}</span>
        </button>}
        <p role="status" className={!panel && notice?.search === catalogueSearch(view) ? 'text-sm font-medium text-foreground-700' : 'sr-only'}>
          {!panel && notice?.search === catalogueSearch(view) ? notice.text : ''}
        </p>
        {viewMissing && !detailRequest.loading && !detailRequest.error ? <div className={`${surface} space-y-2 p-4`}>
          <p role="status" className="text-sm text-foreground-700">{preview ? 'This record is unavailable in the current preview session.' : 'This record could not be found in the selected programme.'}</p>
          <button type="button" className={action} onClick={() => navigation.go(programme ? programmeView : {})}>
            {programme ? 'Open programme' : 'Open programme catalogue'}
          </button>
        </div> : effectiveEditor?.resource === 'modules' && effectiveEditor.record?.source_system === 'lms'
          ? <CatalogModuleCountsEditor key={location.key} module={effectiveEditor.record as CurriculumModule}
            busy={busy} error={error} onSave={data => void save(data)} onCancel={cancelPanel}
            onReload={conflict ? reloadAfterConflict : undefined} onDirtyChange={navigation.setDirty} />
        : effectiveEditor ? <CatalogEditor key={location.key}
          editor={effectiveEditor} detail={editorDetail} staff={staff} busy={busy} error={error} preview={preview}
          staffLoading={staffRequest.loading} staffError={staffRequest.error} retryStaff={() => setStaffRevision(value => value + 1)}
          addPreviewStaff={preview ? (role, member) => setPreviewStaff(current => ({ ...current, [role]: [...current[role], member] })) : undefined}
          parents={parentResource ? {
            resource: parentResource, programmes: selectableProgrammes, detail: editorDetail, selection: creationParents,
            loading: selectedRequest.loading, error: selectedRequest.error, onRetry: refresh,
            onChange: selection => { setParentDraft({ key: location.key, selection }); setError(''); },
          } : undefined}
          onReload={conflict ? reloadAfterConflict : undefined} onSave={data => void save(data)}
          onDirtyChange={navigation.setDirty} onCancel={cancelPanel} onClearError={() => setError('')} />
        : archiveTarget ? <section className={`${surface} space-y-3 p-5`}>
          <Heading title={`${preview ? 'Remove' : 'Archive'} ${archiveTarget.label}?`}
            description={preview ? 'This removes the record from the active preview only.' : 'The record and its history will be kept. Archived records aren’t available for new delivery.'} />
          {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
          <div className="flex flex-wrap gap-2">
            <button className={btnPrimary} disabled={busy || conflict} onClick={() => void archive()}>
              {preview ? 'Remove from preview' : 'Confirm archive'}
            </button>
            {conflict && <button className={action} onClick={reloadAfterConflict}>Reload latest</button>}
            <button className={action} disabled={busy} onClick={cancelPanel}>Cancel</button>
          </div>
        </section>
        : !programmeId ? <>
          {!preview && <SkillsRadarThreshold />}
          <section aria-label="Programme filters" className={`${surface} space-y-4 p-4 sm:p-5`}>
            <div className="flex flex-wrap items-end justify-between gap-3">
              <ListSearch label="Search programmes" query={query} onSearch={setQuery} />
              {!emptyCatalogue && <AddButton label="Add programme" onClick={() => editRecord({ resource: 'programme' })} disabled={!catalogueReady} />}
            </div>
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="Programme status">
              {statusFilters.map(status => <button key={status} type="button" aria-pressed={statusFilter === status}
                className={`min-h-10 rounded-lg border px-3 text-sm font-medium capitalize ${focus} ${statusFilter === status
                  ? 'border-primary-200 bg-primary-50 text-primary-800' : 'border-transparent text-foreground-700 hover:bg-background-100'}`}
                onClick={() => setStatusFilter(status)}>{status}</button>)}
            </div>
          </section>
          {!preview && <CatalogLmsPanel onImported={id => { refresh(); navigateProgramme(id); }} onUpdated={refresh} />}
          {catalogueReady && selectableProgrammes.length > 0 && <div className="flex flex-wrap gap-2" role="group" aria-label="Create catalogue records">
            <button type="button" className={action} onClick={() => editRecord({ resource: 'cohorts' })}>Add cohort</button>
            <button type="button" className={action} onClick={() => editRecord({ resource: 'groups' })}>Add group</button>
            <button type="button" className={action} onClick={() => editRecord({ resource: 'deliveries' })}>Add module</button>
          </div>}
          <DataPanel loading={listing.loading} error={listing.error} onRetry={refresh}>
            {emptyCatalogue ? <section className={`${surface} flex flex-col items-center space-y-3 px-4 py-9 text-center sm:py-12`}>
              <span className="grid size-11 place-items-center rounded-lg bg-primary-50 text-primary-700"><BookOpen size={21} aria-hidden /></span>
              <h2 className="font-semibold">No programmes to show</h2>
              <p className="max-w-md text-sm text-foreground-600">Add a programme to set its costs, then create cohorts and schedule delivery.</p>
              <AddButton label="Add programme" onClick={() => editRecord({ resource: 'programme' })} />
            </section> : <RecordList labels={['Status', 'Level', 'Cost per apprentice', 'Current delivery']} rows={matching.map(row => ({
              id: row.id, name: row.name, caption: row.code,
              cells: [<StateBadge value={row.status} />, row.level, gbp(row.total_cost, preview), row.counts
                ? <ul className="space-y-0.5 text-xs tabular-nums">
                  <li>{row.counts.cohorts} {row.counts.cohorts === 1 ? 'cohort' : 'cohorts'}</li>
                  <li>{row.counts.groups} {row.counts.groups === 1 ? 'group' : 'groups'}</li>
                  <li>{row.counts.scheduled_modules} scheduled {row.counts.scheduled_modules === 1 ? 'module' : 'modules'}</li>
                </ul>
                : <span aria-label="Delivery counts unavailable">Unavailable</span>],
              open: () => navigateProgramme(row.id),
            }))} empty={query.trim() ? 'No programmes match your search.' : 'No programmes match this status.'}
              clearLabel={statusFilter === 'all' ? 'Clear search' : 'Clear filters'}
              onClearSearch={query.trim() || statusFilter !== 'all' ? () => { setQuery(''); setStatusFilter('all'); } : undefined} />}
          </DataPanel>
        </> : <DataPanel loading={detailRequest.loading} error={detailRequest.error} onRetry={refresh}>
          {programme && detail && <div className="space-y-4">
            {!cohort && !template && <div className={`${surface} space-y-3 px-4 pt-4 sm:px-5 sm:pt-5`}>
              <Heading title={programme.name} description={<><span translate="no">{programme.code}</span> · Level {programme.level} · <StateBadge value={programme.status} />{programme.source_system !== 'manual' && ` · ${programme.source_system} (read-only)`}</>}
                actions={!programmeLocked && <div className="flex flex-wrap items-center gap-2">
                  {recordActions('programme', programme, programme.name, false)}
                  {section === 'overview' && <AddButton label="Add cohort" onClick={() => editRecord({ resource: 'cohorts' })} />}
                </div>} />
              <nav aria-label="Programme workspace" className="grid grid-cols-2 gap-x-3 gap-y-1 sm:flex sm:flex-wrap sm:gap-x-4">
                {sections.map(([key, label]) => <button key={key} type="button" aria-current={section === key ? 'page' : undefined}
                  className={`border-b-2 px-1.5 py-2 text-sm font-medium ${focus} ${section === key ? 'border-primary-600 text-primary-700' : 'border-transparent text-foreground-600 hover:text-primary-700'}`}
                  onClick={() => navigateProgramme(programme.id, key)}>{label}</button>)}
              </nav>
              <label className="inline-flex items-center gap-2 pb-3 text-sm">
                <input type="checkbox" checked={showArchived} className="accent-primary-600" onChange={event => setShowArchived(event.target.checked)} />
                Show archived delivery and curriculum records
              </label>
            </div>}
            {!preview && !cohort && !template && programme.source_system === 'lms' && <CatalogLmsPanel
              programme={programme} onImported={id => { refresh(); navigateProgramme(id); }} onUpdated={refresh} />}
            {!cohort && !template && section === 'overview' && <ProgrammeOverview detail={detail} preview={preview} />}
            {!cohort && !template && section === 'pricing' && <PricingWorkspace key={`${programme.id}:${programme.revision}`} detail={detail} preview={preview} revision={revision} />}
            {section === 'cohorts' && !template && !cohort && <>
              <Heading title="Cohorts" description="Set a start date, planned duration and EPA period for each intake."
                actions={!programmeLocked && <AddButton label="Add cohort" onClick={() => editRecord({ resource: 'cohorts' })} />} />
              <ListSearch label="Search cohorts" query={cohortQuery} onSearch={setCohortQuery} />
              <RecordList labels={['Training dates', 'Planned duration', 'EPA period', 'Apprenticeship end', 'Groups']}
                rows={matchingCohorts.map(row => ({
                  id: row.id, name: row.name, caption: row.archived_at ? 'Archived' : undefined,
                  cells: [showDateRange(row.start_date, row.training_end_date), plannedDuration(row), `${row.epa_period} ${row.epa_unit}`, showDate(row.apprenticeship_end_date), activeRows(detail.groups, showArchived).filter(item => item.cohort_id === row.id).length],
                  open: () => navigateCohort(row.id),
                }))} empty={cohortQuery.trim() ? 'No cohorts match your search.' : 'No cohorts yet. Add a cohort before creating groups.'}
                onClearSearch={cohortQuery.trim() ? () => setCohortQuery('') : undefined} />
            </>}
            {cohort && !group && <>
              <Heading title={cohort.name} description={`Cohort · ${programme.name}${cohort.archived_at ? ' · Archived' : ''}`}
                actions={recordActions('cohorts', cohort, cohort.name, programmeLocked)} />
              <Stats items={[
                ['Training dates', showDateRange(cohort.start_date, cohort.training_end_date)],
                ['Planned duration', plannedDuration(cohort)], ['EPA period', `${cohort.epa_period} ${cohort.epa_unit}`],
                ...(cohort.training_duration_months == null || cohort.training_end_override
                  ? [['Training days', cohort.duration_days] as [string, ReactNode]] : []),
                ['Apprenticeship end', showDate(cohort.apprenticeship_end_date)], ['Total apprenticeship duration', totalApprenticeshipDuration(cohort)],
              ]} />
              <Heading title="Groups" description="Groups belong to this cohort and have their own delivery schedule."
                actions={!cohortLocked && <AddButton label="Add group" onClick={() => editRecord({ resource: 'groups', parentId: cohort.id })} />} />
              <RecordList labels={['Skills Coach', 'Delivery day', 'Start time', 'End time']} rows={activeRows(detail.groups, showArchived).filter(row => row.cohort_id === cohort.id).map(row => ({
                id: row.id, name: row.name, caption: row.archived_at ? 'Archived' : undefined,
                cells: [staffName(row.coach_id, staff?.coaches), weekdays[row.weekday - 1], showTime(row.start_time), showTime(row.end_time)],
                open: () => navigateGroup(row.id),
              }))} empty="No groups in this cohort. Add a group to schedule modules." />
            </>}
            {group && cohort && !delivery && <>
              <Heading title={group.name} description={`Group · ${cohort.name}${group.archived_at ? ' · Archived' : ''}`}
                actions={recordActions('groups', group, group.name, cohortLocked, cohort.id)} />
              <Stats items={[
                ['Programme', programme.name], ['Cohort', cohort.name], ['Skills Coach', staffName(group.coach_id, staff?.coaches)],
                ['Weekly schedule', `${weekdays[group.weekday - 1]} · ${showTime(group.start_time)}–${showTime(group.end_time)}`],
              ]} />
              <Heading title="Scheduled modules" description="Choose or create a template when scheduling a module for this group."
                actions={!groupLocked && <div className="flex flex-wrap gap-2">
                  <AddButton label="Add module" onClick={() => editRecord({ resource: 'deliveries', parentId: group.id })} />
                  <button className={action} onClick={() => navigateProgramme(programme.id, 'curriculum')}>Open module library</button>
                </div>} />
              <RecordList labels={['Start date', 'End date', 'Tutor', 'Sessions', 'Group', 'Actions']} rows={activeRows(detail.deliveries, showArchived).filter(row => row.group_id === group.id).map(row => ({
                id: row.id, name: row.curriculum.name, caption: `${row.archived_at ? 'Archived · ' : ''}Pinned v${row.curriculum.number}`,
                cells: [showDate(row.start_date), showDate(row.end_date), staffName(row.tutor_id, staff?.tutors),
                  row.curriculum.lectures ?? 'Not set', group.name, groupLocked || locked(row) ? 'Read-only' :
                    <button type="button" className={action} aria-label={`Edit ${row.curriculum.name}`}
                      disabled={detailRequest.loading} onClick={() => editRecord({ resource: 'deliveries', record: row, parentId: group.id })}>Edit</button>],
                open: () => navigation.go({ ...groupView, deliveryId: row.id }),
              }))} empty="No modules scheduled. Create or select a reusable curriculum template to start." />
            </>}
            {delivery && group && <>
              <Heading title={delivery.curriculum.name} description={`Scheduled module · Pinned revision v${delivery.curriculum.number}${delivery.archived_at ? ' · Archived' : ''}`}
                actions={recordActions('deliveries', delivery, delivery.curriculum.name, groupLocked, group.id)} />
              <Stats items={[
                ['Start date', showDate(delivery.start_date)], ['End date', showDate(delivery.end_date)], ['Group', group.name],
                ['Tutor', staffName(delivery.tutor_id, staff?.tutors)], ['Revision', `v${delivery.curriculum.number} (pinned)`],
                ['Sessions', delivery.curriculum.lectures ?? 'Not set'],
                ['Progress reviews', delivery.curriculum.reviews ?? 'Not set'], ['Books', delivery.curriculum.books ?? 'Not set'],
              ]} />
              <a className={`inline-flex items-center gap-2 text-sm font-semibold text-primary-700 hover:underline ${focus}`}
                href="#pinned-curriculum" onClick={event => {
                  event.preventDefault();
                  const pinned = document.getElementById('pinned-curriculum');
                  pinned?.focus({ preventScroll: true });
                  pinned?.scrollIntoView({ block: 'start', behavior: 'instant' });
                }}>View pinned curriculum <ArrowRight size={14} aria-hidden /></a>
              <section id="pinned-curriculum" tabIndex={-1} className="scroll-mt-24 space-y-3" aria-labelledby="pinned-title">
                <Heading id="pinned-title" title={`Pinned curriculum v${delivery.curriculum.number}`}
                  description="This is the curriculum snapshot used for this delivery." />
                <CurriculumContent topics={delivery.curriculum.weekly_topics} ksbs={delivery.curriculum.ksbs} />
              </section>
            </>}
            {section === 'curriculum' && !cohort && !template && <>
              <div role="tablist" aria-label="Curriculum sections" className={`${surface} flex flex-wrap gap-2 p-2`}>
                {curriculumTabs.map((tab, index) => {
                  const selected = curriculumTab === tab;
                  const label = tab === 'templates' ? `Module Templates (${activeRows(detail.modules, showArchived).length})`
                    : tab === 'ksbs' ? `KSB Library (${activeRows(detail.ksbs, showArchived).length})` : 'KSB Mapping';
                  return <button key={tab} type="button" role="tab" id={`curriculum-tab-${tab}`}
                    aria-controls="catalog-curriculum-panel" aria-selected={selected} tabIndex={selected ? 0 : -1}
                    className={`min-h-11 rounded-lg border px-3 text-sm font-medium ${focus} ${selected
                      ? 'border-primary-200 bg-primary-50 text-primary-800' : 'border-transparent text-foreground-700 hover:bg-background-100'}`}
                    onClick={() => { if (!selected) navigation.go({ ...programmeView, curriculumTab: tab }); }}
                    onKeyDown={event => {
                      const next = event.key === 'Home' ? 0 : event.key === 'End' ? curriculumTabs.length - 1
                        : event.key === 'ArrowRight' ? (index + 1) % curriculumTabs.length
                          : event.key === 'ArrowLeft' ? (index - 1 + curriculumTabs.length) % curriculumTabs.length : -1;
                      if (next < 0) return;
                      event.preventDefault();
                      const target = curriculumTabs[next];
                      if (target !== tab && navigation.go({ ...programmeView, curriculumTab: target })) {
                        requestAnimationFrame(() => document.getElementById(`curriculum-tab-${target}`)?.focus());
                      }
                    }}>{label}</button>;
                })}
              </div>
              <div role="tabpanel" id="catalog-curriculum-panel" aria-labelledby={`curriculum-tab-${curriculumTab}`} className="min-w-0 space-y-4">
              {curriculumTab === 'templates' && <>
              <Heading title="Module templates" description="Manage reusable modules and their revisions. Scheduled modules keep their pinned revision."
                actions={!programmeLocked && <AddButton label="Add module template" onClick={() => editRecord({ resource: 'modules' })} />} />
              <RecordList labels={preview ? ['Latest revision', 'Sessions', 'Progress reviews', 'Books'] : ['Latest revision', 'Weeks', 'Lectures', 'Reviews', 'Books']}
                rows={activeRows(detail.modules, showArchived).map(row => ({
                  id: row.id, name: row.name, caption: row.archived_at ? 'Archived' : undefined,
                  cells: [row.current_revision ? `v${row.current_revision.number}` : 'Not set', ...(preview ? [] : [row.weeks]),
                    row.lectures ?? 'Not set', row.reviews ?? 'Not set', row.books ?? 'Not set'],
                  open: () => navigation.go({ programmeId, section: 'curriculum', templateId: row.id }),
                }))} empty="No templates yet. Add one to define curriculum, topics and delivery counts." />
              </>}
              {curriculumTab === 'ksbs' && <>
              <Heading title="KSB library" description="Add the knowledge, skills and behaviours that can be linked to templates."
                actions={!programmeLocked && <AddButton label="Add KSB" onClick={() => editRecord({ resource: 'ksbs' })} />} />
              <KsbLibraryCsv key={programmeId} programmeCode={programme.code} rows={detail.ksbs} readOnly={programmeLocked}
                preview={preview} onImport={importKsbRows} />
              <RecordList labels={['Description', 'Actions']} rows={activeRows(detail.ksbs, showArchived).map(row => ({
                id: row.id, name: row.code, caption: row.archived_at ? 'Archived' : undefined,
                cells: [row.description, recordActions('ksbs', row, row.code, programmeLocked) ?? 'Read-only'],
              }))} empty="No KSBs yet. Add them here before linking them to a template." />
              </>}
              {curriculumTab === 'mapping' && <CatalogKsbMapping key={programmeId} programmeCode={programme.code}
                modules={activeRows(detail.modules, showArchived)} ksbs={activeRows(detail.ksbs, showArchived)} allKsbs={detail.ksbs}
                editable={!programmeLocked}
                onEdit={() => { setNotice(undefined); navigation.openPanel('assignments', { resource: 'modules' }); }}
                assignments={panel?.kind === 'assignments' ? {
                  busy, error, onSave: (module, ids) => void saveAssignments(module, ids),
                  onCancel: cancelPanel, onReload: conflict ? reloadAfterConflict : undefined, onDirtyChange: navigation.setDirty,
                } : undefined} />}
              </div>
            </>}
            {template && <>
              <Heading title={template.name} description={`Reusable template${template.current_revision ? ` · Latest revision v${template.current_revision.number}` : ''}`}
                actions={programme.source_system === 'lms' && template.source_system === 'lms' && !template.archived_at
                  ? <button className={action} onClick={() => editRecord({ resource: 'modules', record: template })}>Edit Progress Reviews and Books</button>
                  : recordActions('modules', template, template.name, programmeLocked)} />
              <Stats items={[
                ...(preview ? [] : [['Duration', `${template.weeks} weeks`] as [string, ReactNode]]),
                [preview ? 'Sessions' : 'Lectures', template.lectures ?? 'Not set'],
                [preview ? 'Progress reviews' : 'Reviews', template.reviews ?? 'Not set'], ['Books', template.books ?? 'Not set'],
              ]} />
              <CurriculumContent topics={template.weekly_topics} ksbs={detail.ksbs.filter(row => template.ksb_ids.includes(row.id))
                .map(row => ({ ...row, ...(template.ksb_weights?.[row.id] ? { mapping_weight: template.ksb_weights[row.id] } : {}) }))} />
            </>}
          </div>}
        </DataPanel>}
      </div>
    </div>}
  </AdminPage>;
}
