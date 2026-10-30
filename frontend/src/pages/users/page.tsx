import { DELIVERY_STATUS, PLAN_EDITABLE_STATUSES, isLearnerRow, combinedType, fillBlanks, mergeDirectoryRows, differs, rowTypes, matches, type DirectoryRow } from './page.helpers';
import { WorkspaceMetricContent } from '@/components/ui/WorkspaceMetricContent';
import { useMemo, useRef, useState, useEffect, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useLocation, useNavigate } from 'react-router-dom';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { AppIcon } from '@/components/feature/AppIcon';
import { AdminPageHeader } from '@/pages/admin/_shared/AdminPage';
import { useAuth } from "@/hooks/useAuth.helpers";
import { useListQueryState } from '@/hooks/useListQueryState';
import { roleNavMap } from '@/mocks/navigation';
import { fetchEnrolmentUsers, STATUS_OPTIONS, TYPE_OPTIONS, PROGRAMME_STATUS_OPTIONS } from '@/api/enrolmentUsers';
import { fetchStaffUsers, type StaffUserRow } from '@/api/staffUsers';
import { fetchProgrammes, fetchCohorts, fetchGroups } from '@/api/curriculum';
import { listEmployers, type EmployerRow } from '@/api/employers';
import { fetchAttendanceModes, type SavedAttendanceMode } from '@/api/attendanceLectures';
import type { UserListRow, UsersFilter } from './types';
import { StatusBadge, Pagination, inputClass, btnPrimary, btnSecondary } from './components/ui';
import { SendInvitationButton } from './components/SendInvitationButton';
import { CopyInvitationLinkButton } from './components/CopyInvitationLinkButton';
import { CreateUserModal } from './components/CreateUserModal';
import { DownloadLearnerTemplateButton } from './components/DownloadLearnerTemplateButton';
import { ImportLearnersModal } from './components/ImportLearnersModal';
import { CreateStaffModal } from './components/CreateStaffModal';
import { CreateEmployerModal } from './components/CreateEmployerModal';
import { CreateOrganisationModal } from './components/CreateOrganisationModal';
import { EditStaffModal } from './components/EditStaffModal';
import { LearningPlanModal } from './components/LearningPlanModal';
import { ShiftModuleModal } from './components/ShiftModuleModal';
import { BulkAttendanceModeModal } from './components/BulkAttendanceModeModal';
import { TableBodySkeleton } from '@/components/feature/Skeletons';

const enrolmentNav = roleNavMap.apprentice;
const PAGE_SIZE = 8;
// Shifting a learner between modules is for learners already being taught: they
// have a plan and have started on it. Not Delivery — nothing has begun there, so
// the plan itself is still the thing to edit — and not Withdrawn or Completed,
// where there is no remaining delivery to move.
const SHIFT_STATUSES: string[] = ['Active', 'On break'];
type SummaryFilter = 'all' | 'learners' | 'admins' | 'employers' | 'active';

function employerToRow(e: EmployerRow): DirectoryRow {
  return {
    id: e.id,
    uuid: null,
    name: e.name,
    type: 'Employer',
    email: e.email,
    group: e.employerGroupNames.join(', '),
    subscriptionStatus: '',
    subscriptionVerified: false,
    learningPlan: false,
    programmeStatus: '',
    source: 'employer',
    // Kept whole so the edit modal can prefill without a refetch.
    employer: e,
  };
}

const EMPTY_FILTER: UsersFilter = {
  userName: '', groups: [], email: '', statuses: [], type: 'all', programme: '', cohort: '',
  programmeStatus: '', niNumber: '', caseOwner: 'any', referenceNumber: '', attendanceMode: '', page: 1, pageSize: PAGE_SIZE,
};
const DIRECTORY_QUERY_DEFAULTS = {
  search: '', summary: 'all', page: 1, name: '', email: '', groups: '', statuses: '',
  type: 'all', programme: '', cohort: '', programmeStatus: '', caseOwner: 'any', reference: '', attendanceMode: '',
};
const encodeFilterList = (values?: string[]) => (values || []).join('~');
const decodeFilterList = (value: string) => value ? value.split('~').filter(Boolean) : [];

function initials(name: string) {
  return name.split(' ').filter(Boolean).map((w) => w[0]).slice(0, 2).join('').toUpperCase();
}

function MultiSelect({ label, placeholder, options, selected, onChange, disabled = false }: { label: string; placeholder: string; options: string[]; selected: string[]; onChange: (next: string[]) => void; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onDoc = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);
  // A filter that becomes disabled while its list is open would otherwise leave
  // the panel hanging over the card with nothing behind it.
  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);
  const toggle = (opt: string) => onChange(selected.includes(opt) ? selected.filter((s) => s !== opt) : [...selected, opt]);
  return (
    <div>
      <label className="block text-[12px] font-semibold text-foreground-800 mb-1">{label}</label>
      <div className="relative" ref={ref}>
        <button
          type="button"
          disabled={disabled}
          onClick={() => setOpen((o) => !o)}
          className={`${inputClass} text-left flex items-center justify-between ${disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}`}
        >
          <span className={selected.length ? 'text-foreground-900 truncate' : 'text-foreground-300'}>{selected.length ? selected.join(', ') : placeholder}</span>
          <AppIcon className="ri-arrow-down-s-line text-foreground-400 shrink-0" />
        </button>
        {open && !disabled && (
          <div className="absolute z-20 mt-1 w-full bg-background-50 border border-foreground-200 rounded-lg shadow-lg max-h-56 overflow-y-auto py-1">
            {options.length === 0 && <p className="px-3 py-1.5 text-[12px] text-foreground-300">No options</p>}
            {options.map((opt) => (
              <label key={opt} className="flex items-center gap-2 px-3 py-1.5 text-[12px] text-foreground-700 hover:bg-background-100 cursor-pointer">
                <input type="checkbox" checked={selected.includes(opt)} onChange={() => toggle(opt)} className="accent-primary-500" />{opt}
              </label>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function TextFilter({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <div>
      <label className="block text-[12px] font-semibold text-foreground-800 mb-1">{label}</label>
      <input className={inputClass} placeholder={placeholder} value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

function SelectFilter({ label, value, options, onChange, disabled = false }: { label: string; value: string; options: { value: string; label: string }[]; onChange: (v: string) => void; disabled?: boolean }) {
  return (
    <div>
      <label className="block text-[12px] font-semibold text-foreground-800 mb-1">{label}</label>
      <select className={`${inputClass} ${disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}`} disabled={disabled} value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </div>
  );
}

function UserMetricCard({ icon, label, value, detail, onClick, active = false }: { icon: string; label: string; value: ReactNode; detail: string; onClick?: () => void; active?: boolean }) {
  const surface = `card-premium flex min-h-[88px] items-center gap-3 rounded-xl border border-foreground-200/60 bg-background-50 px-3.5 py-3 text-left transition ${active ? 'ring-2 ring-primary-300' : 'hover:bg-primary-100/60'} ${onClick ? 'cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400' : ''}`;
  const content = <WorkspaceMetricContent label={label} value={value} note={detail} icon={icon} />;

  return onClick ? <button type="button" onClick={onClick} aria-pressed={active} className={surface}>{content}</button> : <div className={surface}>{content}</div>;
}

function ActiveProgrammeCard({ active, total, onClick, selected = false }: { active: number; total: number; onClick?: () => void; selected?: boolean }) {
  const percentage = total > 0 ? Math.round((active / total) * 100) : 0;

  const content = (
    <>
      <div className="min-w-0">
        <p className="text-[13px] font-semibold text-foreground-800">Active on programme</p>
        <p className="mt-3 text-[28px] font-semibold leading-none tracking-tight text-foreground-950">{active}</p>
        <p className="mt-1 text-[12px] text-foreground-500">{percentage}% of learners</p>
      </div>
      <div className="flex shrink-0 items-center">
        <div
          className="flex h-16 w-16 items-center justify-center rounded-full"
          style={{ background: `conic-gradient(#35b98a ${percentage}%, var(--enrolment-ring-track, #ebeaf1) 0)` }}
        >
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-white text-[16px] font-semibold text-foreground-700">
            {percentage}%
          </div>
        </div>
      </div>
    </>
  );

  const surface = `flex min-h-[112px] items-center justify-between gap-3 rounded-xl border border-primary-200/40 bg-primary-50/60 p-4 text-left transition ${selected ? 'ring-2 ring-primary-300' : 'hover:bg-primary-100/60'} ${onClick ? 'cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400' : ''}`;
  return onClick ? <button type="button" onClick={onClick} aria-pressed={selected} className={surface}>{content}</button> : <div className={surface}>{content}</div>;
}

/** Distinct non-empty values of one row column, sorted. */
function distinct(from: DirectoryRow[], pick: (r: DirectoryRow) => string | undefined): string[] {
  return Array.from(new Set(from.map(pick).filter(Boolean) as string[])).sort();
}

/** A learner with no saved attendance mode is on Live Sessions. */
function rowAttendanceMode(modes: Record<string, SavedAttendanceMode>, row: Pick<DirectoryRow, 'id'>): 'live' | 'lazy' {
  return modes[row.id]?.mode === 'lazy' ? 'lazy' : 'live';
}

export default function UsersListPage() {
  const { isAdmin } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const { state: query, setValues: setQueryValues, reset: resetQuery } = useListQueryState(DIRECTORY_QUERY_DEFAULTS);
  const applied = useMemo<UsersFilter>(() => ({
    ...EMPTY_FILTER,
    userName: String(query.name), email: String(query.email),
    groups: decodeFilterList(String(query.groups)), statuses: decodeFilterList(String(query.statuses)),
    type: String(query.type) as UsersFilter['type'], programme: String(query.programme),
    cohort: String(query.cohort), programmeStatus: String(query.programmeStatus),
    caseOwner: String(query.caseOwner), referenceNumber: String(query.reference),
    attendanceMode: String(query.attendanceMode) as UsersFilter['attendanceMode'],
  }), [query.attendanceMode, query.caseOwner, query.cohort, query.email, query.groups, query.name, query.programme, query.programmeStatus, query.reference, query.statuses, query.type]);
  const [draft, setDraft] = useState<UsersFilter>(applied);
  const quickSearch = String(query.search);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const page = Number(query.page);
  const [createOpen, setCreateOpen] = useState(false);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [importModalOpen, setImportModalOpen] = useState(false);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [createAdminOpen, setCreateAdminOpen] = useState(false);
  const [createTutorOpen, setCreateTutorOpen] = useState(false);
  const [createEmployerOpen, setCreateEmployerOpen] = useState(false);
  const [createOrgOpen, setCreateOrgOpen] = useState(false);
  const createRef = useRef<HTMLDivElement>(null);
  const createBtnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  // Viewport coords for the portalled Create menu, measured from the button.
  const [menuPos, setMenuPos] = useState<{ top: number; right: number } | null>(null);

  const [rows, setRows] = useState<DirectoryRow[]>([]);
  // Saved attendance modes keyed by learner id; a learner with no entry is on
  // Live Sessions. null while loading; 'failed' shows a dash, not a guess.
  const [attendanceModes, setAttendanceModes] = useState<Record<string, SavedAttendanceMode> | 'failed' | null>(null);
  const summaryFilter = String(query.summary) as SummaryFilter;
  // The curriculum lookups behind the programme -> cohort -> group filters. They
  // come from curriculum.cohort_authoring_details, the same source the create
  // form picks from, so a filter can only offer combinations that really exist.
  const [programmes, setProgrammes] = useState<string[]>([]);
  const [cohorts, setCohorts] = useState<string[]>([]);
  const [cascadeGroups, setCascadeGroups] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // The staff row currently being edited, if any.
  const [editStaff, setEditStaff] = useState<StaffUserRow | null>(null);
  // The learner whose details form is open. Staff and employers each have their
  // own edit modal already; this is the learner's, and it is the same form that
  // creates one.
  const [editLearner, setEditLearner] = useState<UserListRow | null>(null);
  // Likewise for employer contacts.
  const [editEmployer, setEditEmployer] = useState<EmployerRow | null>(null);
  // The learner whose learning plan is open, if any. Only offered once a learner
  // reaches Delivery — before that their modules aren't settled.
  const [planFor, setPlanFor] = useState<UserListRow | null>(null);
  // The learner being shifted between modules, if any. Gated like editing the
  // plan: it rewrites the plan, so it is a Delivery-stage action.
  const [shiftFor, setShiftFor] = useState<UserListRow | null>(null);

  // One directory for the whole enrolment section: every learner — both
  // apprenticeship and commercial — comes from the single Enrolment_Users table
  // in one call, each row carrying its own `learnerType`/`source`. Staff/admin
  // accounts come from Staff_users, employer contacts from Employers.
  //
  // Only the learner call is allowed to fail the page: it's the bulk of the
  // directory. The other two are swallowed so a missing table or a 502 on either
  // one leaves the learners listed instead of blanking the whole screen.
  // Without the skeleton the rows stay mounted, so a row's open panel (a
  // just-issued set-password link) survives the reload.
  const fetchRows = (showSkeleton: boolean) => {
    if (showSkeleton) setLoading(true);
    setError(null);
    Promise.all([
      fetchEnrolmentUsers(),
      fetchStaffUsers().catch(() => [] as StaffUserRow[]),
      listEmployers().then((r) => r.results).catch(() => [] as EmployerRow[]),
    ])
      .then(([learners, staff, employers]) => {
        // Merged, not concatenated: somebody who is both staff and a learner
        // has a record in two of these lists and would otherwise appear twice.
        setRows(mergeDirectoryRows([
          ...learners,
          // Staff rows already arrive in UserListRow shape from to_staff_row.
          ...staff.map((r) => ({ ...r, source: 'staff' as const })),
          ...employers.map(employerToRow),
        ]));
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  };
  const load = () => fetchRows(true);
  const refreshInPlace = () => fetchRows(false);

  useEffect(load, []);

  // Fetched beside the directory, not inside it: a failure only blanks this
  // column. Reloaded after the bulk window so its changes show at once.
  const loadAttendanceModes = () => {
    fetchAttendanceModes()
      .then((result) => setAttendanceModes(result.available ? result.modes : 'failed'))
      .catch(() => setAttendanceModes('failed'));
  };
  useEffect(loadAttendanceModes, []);

  useEffect(() => setDraft(applied), [applied]);

  // A failed lookup isn't worth an error banner over the whole directory: the
  // dropdown falls back to the programmes/cohorts the loaded rows already carry.
  useEffect(() => {
    let cancelled = false;
    fetchProgrammes()
      .then((rows) => { if (!cancelled) setProgrammes(rows); })
      .catch(() => { if (!cancelled) setProgrammes([]); });
    return () => { cancelled = true; };
  }, []);

  // Cohorts belong to a programme and groups to a cohort, so each level is
  // refetched when its parent changes — and cleared first, so the list can never
  // show a moment of the previous programme's cohorts.
  useEffect(() => {
    let cancelled = false;
    setCohorts([]);
    if (!draft.programme) return;
    fetchCohorts(draft.programme)
      .then((rows) => { if (!cancelled) setCohorts(rows); })
      .catch(() => { if (!cancelled) setCohorts([]); });
    return () => { cancelled = true; };
  }, [draft.programme]);

  useEffect(() => {
    let cancelled = false;
    setCascadeGroups([]);
    if (!draft.programme || !draft.cohort) return;
    fetchGroups(draft.programme, draft.cohort)
      .then((rows) => { if (!cancelled) setCascadeGroups(rows); })
      .catch(() => { if (!cancelled) setCascadeGroups([]); });
    return () => { cancelled = true; };
  }, [draft.programme, draft.cohort]);

  // The menu lives in a portal, so an outside-click test has to exempt it too.
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node;
      if (createRef.current?.contains(t) || menuRef.current?.contains(t)) return;
      setCreateOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);

  // Anchor the portalled menu under the button, and keep it there while the
  // page scrolls or resizes (fixed positioning doesn't follow the anchor).
  useEffect(() => {
    if (!createOpen) { setMenuPos(null); return; }
    const place = () => {
      const r = createBtnRef.current?.getBoundingClientRect();
      if (r) setMenuPos({ top: r.bottom + 6, right: window.innerWidth - r.right });
    };
    place();
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    return () => {
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
    };
  }, [createOpen]);

  // The curriculum list is the source of truth, but rows created before a
  // programme was renamed (or imported from elsewhere) can carry a name that
  // isn't in it — union so those learners stay reachable from the filter.
  const programmeOptions = useMemo(
    () => Array.from(new Set([...programmes, ...distinct(rows, (r) => r.programme)])).sort(),
    [programmes, rows],
  );
  // The case owners who actually own somebody, read off the loaded rows. This
  // used to be a fixed list of four names in mocks/enrolment-console: two of
  // them owned no learners at all, and every owner since was missing, so the
  // filter could not reach most of the directory.
  const caseOwnerOptions = useMemo(() => distinct(rows, (r) => r.caseOwner), [rows]);
  const cohortOptions = useMemo(
    () => Array.from(new Set([
      ...cohorts,
      ...distinct(draft.programme ? rows.filter((r) => !differs(r.programme, draft.programme!)) : [], (r) => r.cohort),
    ])).sort(),
    [cohorts, rows, draft.programme],
  );
  // Groups hang off a programme the same way cohorts do, so there is nothing to
  // offer until one is chosen — and offering everything meant employers' company
  // names (their stand-in for Group, see employerToRow) sat in the list beside
  // real cohort groups. Below that the list narrows again: the curriculum's own
  // groups once a cohort is picked, otherwise the groups of the rows still in
  // scope for the programme.
  const groupOptions = useMemo(() => {
    if (!draft.programme) return [];
    const inScope = rows.filter(
      (r) => !differs(r.programme, draft.programme!)
        && (!draft.cohort || !differs(r.cohort, draft.cohort)),
    );
    return Array.from(new Set([...cascadeGroups, ...distinct(inScope, (r) => r.group)])).sort();
  }, [cascadeGroups, rows, draft.programme, draft.cohort]);
  // Staff rows report their position (Curriculum team, Operations team, ...) as
  // their type, so the filter list is derived from the loaded rows rather than
  // the fixed TYPE_OPTIONS — otherwise staff would be unfilterable.
  const typeOptions = useMemo(
    // Each role offered separately, not the combined "Admin · User" string —
    // that string is not a role anyone would filter by, and only one of the
    // people it could match would ever need it.
    () => Array.from(new Set([...TYPE_OPTIONS, ...rows.flatMap(rowTypes)])).sort(),
    [rows],
  );

  const searchTerm = quickSearch.trim().toLowerCase();
  const filtered = useMemo(() => rows.filter((r) => {
    if (searchTerm && ![r.name, r.email, r.group, r.programme]
      .some((value) => (value ?? '').toLowerCase().includes(searchTerm))) return false;
    if (!matches(r, applied)) return false;
    // Kept out of matches(): the modes come from their own request. Until they
    // load, or if they fail, no learner can be shown as matching either mode.
    if (applied.attendanceMode && (!isLearnerRow(r) || attendanceModes === null || attendanceModes === 'failed'
      || rowAttendanceMode(attendanceModes, r) !== applied.attendanceMode)) return false;
    if (summaryFilter === 'learners') return isLearnerRow(r);
    if (summaryFilter === 'admins') return r.source === 'staff';
    if (summaryFilter === 'employers') return r.source === 'employer';
    if (summaryFilter === 'active') return r.programmeStatus === 'Active';
    return true;
  }), [rows, applied, summaryFilter, searchTerm, attendanceModes]);
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const pageRows = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const showingStart = filtered.length ? (page - 1) * PAGE_SIZE + 1 : 0;
  const showingEnd = filtered.length ? Math.min(page * PAGE_SIZE, filtered.length) : 0;

  // By source, like the two counts below it: a learner whose Type has been set
  // to Referrer or Caseowner is still a learner, and dropping them out of this
  // count made the three tiles stop adding up to the table.
  const learners = rows.filter(isLearnerRow).length;
  // Counted by source, not "not a learner": employers are in the table too now,
  // and lumping them under Admins would misreport both.
  const admins = rows.filter((r) => r.source === 'staff').length;
  const employerCount = rows.filter((r) => r.source === 'employer').length;
  const active = rows.filter((r) => r.programmeStatus === 'Active').length;

  // Choosing a programme or cohort invalidates the levels below it, so a stale
  // cohort/group can't survive into a search for a different programme.
  const set = (patch: Partial<UsersFilter>) =>
    setDraft((d) => ({
      ...d,
      ...('programme' in patch ? { cohort: '', groups: [] } : {}),
      ...('cohort' in patch ? { groups: [] } : {}),
      ...patch,
    }));
  const search = () => setQueryValues({
    name: draft.userName || '', email: draft.email || '', groups: encodeFilterList(draft.groups),
    statuses: encodeFilterList(draft.statuses), type: draft.type || 'all', programme: draft.programme || '',
    cohort: draft.cohort || '', programmeStatus: draft.programmeStatus || '',
    caseOwner: draft.caseOwner || 'any', reference: draft.referenceNumber || '',
    attendanceMode: draft.attendanceMode || '',
  }, { resetPage: true });
  const updateQuickSearch = (value: string) => setQueryValues({ search: value }, { resetPage: true });
  const reset = () => { setDraft(EMPTY_FILTER); resetQuery(); };
  const selectSummary = (next: SummaryFilter) => setQueryValues({ summary: next }, { resetPage: true });
  // Commercial and apprenticeship ids come from different tables and overlap,
  // so every row action carries the row's source.
  const q = (row: UserListRow) => (row.source === 'commercial' ? '?source=commercial' : '');
  // Staff and employers live in their own tables and have no learner
  // profile/wizard, so their rows aren't links — routing one to /users/<id>
  // would read a learner record with a colliding id.
  const isNonLearner = (row: UserListRow) => !isLearnerRow(row);

  const openUser = (row: UserListRow) => {
    if (isNonLearner(row)) return;
    navigate(`/users/${row.id}${q(row)}`);
  };

  // Staff enter the same Dashboard that learners use as their home page.
  const openLearnerPage = (row: UserListRow) => {
    if (isNonLearner(row)) return;
    const kind = row.source === 'commercial' ? 'commercial' : 'apprenticeship';
    navigate(`/workspace/learner/${kind}/${row.id}`);
  };

  // Staff/admin rows have no profile page — editing their details in place is
  // the only way to correct an email, phone or position after creation.
  const openStaffEdit = (row: DirectoryRow) => setEditStaff(row as StaffUserRow);

  // Patch the edited row in place rather than refetching the whole directory,
  // so the table doesn't flash back to its loading state on every save.
  const applyStaffUpdate = (updated: StaffUserRow) => {
    setRows((prev) =>
      prev.map((r) =>
        r.source === 'staff' && r.id === updated.id ? { ...updated, source: 'staff' as const } : r,
      ),
    );
  };

  // Same in-place patch for a learner. `load()` follows behind, because the
  // server derives things this row cannot: Subscription verified follows
  // Status, and moving a learner's programme, cohort or group re-stamps their
  // delivery window and can advance their programme status.
  const applyLearnerUpdate = (updated: UserListRow) => {
    setRows((prev) =>
      prev.map((r) => (r.source === updated.source && r.id === updated.id ? { ...r, ...updated } : r)),
    );
    load();
  };

  const applyLearnerImport = (imported: UserListRow[]) => {
    // Keep the saved rows in local state while refreshing server-derived fields.
    setRows(previous => mergeDirectoryRows([...imported, ...previous]));
    setQueryValues({ page: 1 });
    load();
  };

  // Employers have no profile page either, so the row's Edit action is their
  // only edit surface — same arrangement as staff.
  const applyEmployerUpdate = (updated: EmployerRow) => {
    setRows((prev) =>
      prev.map((r) => (r.source === 'employer' && r.id === updated.id ? employerToRow(updated) : r)),
    );
  };

  return (
    <WorkspaceShell role="compliance" roleLabel={enrolmentNav.label} navItems={enrolmentNav.items} workspaceLabel={enrolmentNav.workspaceLabel} pageTitle="Users" pageSubtitle="Directory of learners, administrators and employers">
      <div className="admin-console-page enrolment-users-page min-w-0 space-y-4 p-3 md:space-y-5 md:p-6">
        <AdminPageHeader
          title="User Management"
          description="Manage learners, administrators and employers across all programmes."
          icon="ri-group-line"
          eyebrow={isAdmin ? 'Administration' : 'Enrolment'}
          actions={
            <div className="flex flex-wrap items-start gap-2">
              <button type="button" className={btnSecondary} onClick={() => navigate('/users/wizard-builder')}>
                <AppIcon className="ri-magic-line" />Edit apprenticeship wizard
              </button>
              <button type="button" className={btnSecondary} onClick={() => navigate('/users/eligibility-builder')}>
                <AppIcon className="ri-task-line" />Edit eligibility form
              </button>
              <DownloadLearnerTemplateButton />
              <button type="button" className={btnSecondary} onClick={() => setImportModalOpen(true)}>
                <AppIcon className="ri-upload-2-line" />Upload learners
              </button>
              {/* Admin-only: the attendance mode endpoint rejects writes from any other role. */}
              {isAdmin && (
                <button type="button" className={btnSecondary} onClick={() => setBulkOpen(true)} disabled={loading}>
                  <AppIcon className="ri-checkbox-multiple-line" />Bulk actions
                </button>
              )}
            <div ref={createRef} className="relative">
              <button
                type="button"
                ref={createBtnRef}
                onClick={() => setCreateOpen((o) => !o)}
                className={btnPrimary}
              >
                <AppIcon className="ri-add-line" />Create user<AppIcon className="ri-arrow-down-s-line" />
              </button>
              {createOpen && menuPos && createPortal(
                <div
                  ref={menuRef}
                  style={{ top: menuPos.top, right: menuPos.right }}
                  className="fixed z-[200] w-56 rounded-xl border border-foreground-200 bg-background-50 py-1.5 shadow-xl"
                >
                  <button className="w-full cursor-pointer px-3 py-2 text-left text-[13px] text-foreground-700 hover:bg-background-100" onClick={() => { setCreateOpen(false); setCreateModalOpen(true); }}><AppIcon className="ri-user-add-line mr-2 text-foreground-400" />Create user</button>
                  <button className="w-full cursor-pointer px-3 py-2 text-left text-[13px] text-foreground-700 hover:bg-background-100" onClick={() => { setCreateOpen(false); setCreateAdminOpen(true); }}><AppIcon className="ri-shield-user-line mr-2 text-foreground-400" />Create admin</button>
                  <button className="w-full cursor-pointer px-3 py-2 text-left text-[13px] text-foreground-700 hover:bg-background-100" onClick={() => { setCreateOpen(false); setCreateTutorOpen(true); }}><AppIcon className="ri-presentation-line mr-2 text-foreground-400" />Create tutor</button>
                  <button className="w-full cursor-pointer px-3 py-2 text-left text-[13px] text-foreground-700 hover:bg-background-100" onClick={() => { setCreateOpen(false); setCreateEmployerOpen(true); }}><AppIcon className="ri-briefcase-line mr-2 text-foreground-400" />Create employer profile</button>
                  <button className="w-full cursor-pointer px-3 py-2 text-left text-[13px] text-foreground-700 hover:bg-background-100" onClick={() => { setCreateOpen(false); setCreateOrgOpen(true); }}><AppIcon className="ri-building-line mr-2 text-foreground-400" />Create organisation profile</button>
                </div>,
                document.body,
              )}
            </div>
            </div>
          }
        />

        <div aria-label="User totals" className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3 min-[1600px]:grid-cols-5">
          <UserMetricCard icon="ri-group-line" label="Total users" value={rows.length} detail="Across all roles" active={summaryFilter === 'all'} onClick={() => selectSummary('all')} />
          <UserMetricCard icon="ri-graduation-cap-line" label="Learners" value={learners} detail={`${rows.length ? Math.round((learners / rows.length) * 100) : 0}% of total`} active={summaryFilter === 'learners'} onClick={() => selectSummary('learners')} />
          <UserMetricCard icon="ri-user-line" label="Admins" value={admins} detail={`${rows.length ? Math.round((admins / rows.length) * 100) : 0}% of total`} active={summaryFilter === 'admins'} onClick={() => selectSummary('admins')} />
          <UserMetricCard icon="ri-briefcase-line" label="Employers" value={employerCount} detail={`${rows.length ? Math.round((employerCount / rows.length) * 100) : 0}% of total`} active={summaryFilter === 'employers'} onClick={() => selectSummary('employers')} />
          <ActiveProgrammeCard active={active} total={rows.length} selected={summaryFilter === 'active'} onClick={() => selectSummary('active')} />
        </div>

        {/* Filter card */}
        <div className="overflow-hidden rounded-2xl border border-foreground-100 bg-background-50 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 md:px-5">
            <div className="flex items-center gap-3">
              <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-primary-100 bg-primary-50 text-primary-600"><AppIcon className="ri-filter-3-line" /></span>
              <div>
                <h2 className="text-[14px] font-semibold text-foreground-900">Filter users</h2>
                <p className="mt-0.5 text-[11px] text-foreground-400">Use filters to refine the user list.</p>
              </div>
            </div>
            <button type="button" className="hidden items-center gap-1.5 rounded-lg border border-foreground-100 px-3 py-2 text-[12px] font-medium text-foreground-600 transition hover:border-primary-200 hover:text-primary-700 sm:inline-flex"><AppIcon className="ri-star-line" />Save view</button>
          </div>
          <div className="border-t border-foreground-100 p-4 md:p-5">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <TextFilter label="Name" placeholder="Search by name..." value={draft.userName ?? ''} onChange={(v) => set({ userName: v })} />
            {/* Programme -> cohort -> group, same gate as the Cohort filter: a
                group only means something inside a programme. */}
            <MultiSelect
              label="Group"
              placeholder={
                !draft.programme
                  ? 'Select a programme first'
                  : draft.cohort
                    ? 'Select groups in this cohort'
                    : 'Select groups in this programme'
              }
              options={groupOptions}
              selected={draft.groups ?? []}
              onChange={(v) => set({ groups: v })}
              disabled={!draft.programme}
            />
            <TextFilter label="Email" placeholder="Enter email" value={draft.email ?? ''} onChange={(v) => set({ email: v })} />
            <MultiSelect label="Status" placeholder="Select statuses" options={STATUS_OPTIONS} selected={draft.statuses ?? []} onChange={(v) => set({ statuses: v })} />
            <SelectFilter label="Type" value={draft.type ?? 'all'} onChange={(v) => set({ type: v as UsersFilter['type'] })} options={[{ value: 'all', label: '--All--' }, ...typeOptions.map((t) => ({ value: t, label: t }))]} />
            <SelectFilter label="Programme" value={draft.programme ?? ''} onChange={(v) => set({ programme: v })} options={[{ value: '', label: '--All--' }, ...programmeOptions.map((p) => ({ value: p, label: p }))]} />
            {/* Single-select on purpose: the group lookup takes one programme and
                one cohort, so this is what narrows the Group list below. */}
            <SelectFilter
              label="Cohort"
              value={draft.cohort ?? ''}
              onChange={(v) => set({ cohort: v })}
              disabled={!draft.programme}
              options={draft.programme
                ? [{ value: '', label: '--All--' }, ...cohortOptions.map((c) => ({ value: c, label: c }))]
                : [{ value: '', label: 'Select a programme first' }]}
            />
            <SelectFilter label="Programme status" value={draft.programmeStatus ?? ''} onChange={(v) => set({ programmeStatus: v })} options={[{ value: '', label: '--All--' }, ...PROGRAMME_STATUS_OPTIONS.map((s) => ({ value: s, label: s }))]} />
            <TextFilter label="NI number" value={draft.niNumber ?? ''} onChange={(v) => set({ niNumber: v })} />
            <SelectFilter label="Case owner" value={draft.caseOwner ?? 'any'} onChange={(v) => set({ caseOwner: v })} options={[{ value: 'any', label: 'Any' }, ...caseOwnerOptions.map((c) => ({ value: c, label: c }))]} />
            <TextFilter label="Reference number" value={draft.referenceNumber ?? ''} onChange={(v) => set({ referenceNumber: v })} />
            <SelectFilter label="Attendance mode" value={draft.attendanceMode ?? ''} onChange={(v) => set({ attendanceMode: v as UsersFilter['attendanceMode'] })} options={[{ value: '', label: '--All--' }, { value: 'live', label: 'Live Sessions' }, { value: 'lazy', label: 'Recorded' }]} />
          </div>
          <div className="mt-5 flex items-center justify-end gap-4 lg:col-span-4">
            <button type="button" className="text-[12px] font-medium text-foreground-600 transition hover:text-primary-700" onClick={reset}>Clear all</button>
            <button type="button" className={btnPrimary} onClick={search}><AppIcon className="ri-filter-3-line" />Apply filters</button>
          </div>
          </div>
        </div>

        {/* Results table */}
        <div className="admin-cool-table overflow-hidden rounded-2xl border border-foreground-100 bg-background-50 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 md:px-5">
            <div className="flex items-center gap-3">
              <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-primary-100 bg-primary-50 text-primary-600"><AppIcon className="ri-group-line" /></span>
              <div className="flex items-center gap-2">
                <h2 className="text-[15px] font-semibold text-foreground-900">Users ({filtered.length})</h2>
              </div>
            </div>
            <div role="search" aria-label="User directory" className="relative order-last w-full sm:order-none sm:ml-auto sm:w-80">
              <label htmlFor="users-search" className="sr-only">Search users</label>
              <AppIcon className="ri-search-line pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-foreground-400" />
              <input
                ref={searchInputRef}
                id="users-search"
                type="search"
                value={quickSearch}
                onChange={(event) => updateQuickSearch(event.target.value)}
                placeholder="Search name, email, group or programme..."
                aria-controls="users-directory-table"
                className={`${inputClass} pl-9 pr-9 [&::-webkit-search-cancel-button]:appearance-none`}
              />
              {quickSearch && (
                <button
                  type="button"
                  aria-label="Clear search"
                  onClick={() => { updateQuickSearch(''); searchInputRef.current?.focus(); }}
                  className="absolute right-1 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-md text-foreground-400 hover:bg-background-100 hover:text-primary-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-400"
                >
                  <AppIcon className="ri-close-line" />
                </button>
              )}
            </div>
            <div className="flex items-center gap-2">
              <button type="button" className="hidden items-center gap-1.5 rounded-lg border border-foreground-100 px-3 py-2 text-[12px] font-medium text-foreground-600 transition hover:border-primary-200 hover:text-primary-700 sm:inline-flex"><AppIcon className="ri-layout-column-line" />Columns</button>
              <button type="button" className="inline-flex items-center gap-1.5 rounded-lg border border-foreground-100 px-3 py-2 text-[12px] font-medium text-foreground-600 transition hover:border-primary-200 hover:text-primary-700"><AppIcon className="ri-download-line" />Export<AppIcon className="ri-arrow-down-s-line" /></button>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table id="users-directory-table" className="w-full text-[13px]">
              <thead>
                <tr className="border-b border-foreground-200/70 bg-background-100/50">
                  {['User', 'Type', 'Email', 'Group', 'Programme', 'Cohort', 'Subscription status', 'Learning plan', 'Programme status', 'Attendance mode', 'Actions'].map((h) => (
                    <th key={h} className="text-left py-3 px-3 text-[11px] font-semibold text-foreground-500 uppercase tracking-wider whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {/* Skeleton rows, not a spinner in a merged cell: the table keeps
                    its column widths and height, so the header stops jumping when
                    the first page of users lands. */}
                {loading && <TableBodySkeleton rows={6} columns={11} />}
                {!loading && error && (
                  <tr><td colSpan={11} className="py-10 text-center text-[13px]">
                    <p className="text-red-600 mb-2"><AppIcon className="ri-error-warning-line mr-1.5" />{error}</p>
                    <button className={btnSecondary} onClick={load}><AppIcon className="ri-refresh-line" />Retry</button>
                  </td></tr>
                )}
                {!loading && !error && pageRows.map((row, i) => {
                  const isLearner = isLearnerRow(row);
                  const isStaff = row.source === 'staff';
                  const isEmployer = row.source === 'employer';
                  // Neither has a learner record, so their name never routes to
                  // one. Staff open their edit modal in place; an employer opens
                  // their own side page, which is where their learners and the
                  // documents they must sign live.
                  const openInPlace = isStaff
                    ? () => openStaffEdit(row)
                    : isEmployer
                      ? () => navigate(`/employers/${row.id}`, { state: { employerReturnTo: `${location.pathname}${location.search}${location.hash}` } })
                      : null;
                  return (
                  <tr key={`${row.source ?? 'apprenticeship'}-${row.id}`} className={`border-b border-foreground-100 hover:bg-primary-50/30 transition-smooth ${i % 2 === 1 ? 'bg-background-100/20' : ''}`}>
                    <td className="py-2.5 px-3">
                      {openInPlace ? (
                        <button onClick={openInPlace} className="flex items-center gap-2.5 cursor-pointer group text-left">
                          <span className="w-8 h-8 rounded-lg flex items-center justify-center text-[11px] font-semibold shrink-0 bg-secondary-100 text-secondary-700">{initials(row.name)}</span>
                          <span className="text-secondary-700 group-hover:underline font-medium">{row.name}</span>
                        </button>
                      ) : (
                        <button onClick={() => openUser(row)} className="flex items-center gap-2.5 cursor-pointer group text-left">
                          <span className={`w-8 h-8 rounded-lg flex items-center justify-center text-[11px] font-semibold shrink-0 ${isLearner ? 'bg-primary-100 text-primary-700' : 'bg-secondary-100 text-secondary-700'}`}>{initials(row.name)}</span>
                          <span className="text-primary-600 group-hover:text-primary-700 group-hover:underline font-medium">{row.name}</span>
                        </button>
                      )}
                    </td>
                    <td className="py-2.5 px-3">
                      <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${isLearner ? 'bg-primary-50 text-primary-700 border-primary-200/50' : 'bg-secondary-50 text-secondary-700 border-secondary-200/50'}`}>{row.type}</span>
                    </td>
                    <td className="py-2.5 px-3 text-foreground-600 max-w-[220px] break-words">{row.email}</td>
                    <td className="py-2.5 px-3 text-foreground-600">{row.group}</td>
                    {/* Learners only: staff, admins and employers have no
                        programme, so a dash is more honest than an empty cell. */}
                    <td className="py-2.5 px-3 text-foreground-600 max-w-[180px] break-words">
                      {isLearner && row.programme
                        ? row.programme
                        : <span className="text-foreground-300">—</span>}
                    </td>
                    {/* Learners only, same as Programme: the cohort hangs off
                        the programme, so a row without one has no cohort. */}
                    <td className="py-2.5 px-3 text-foreground-600 whitespace-nowrap">
                      {isLearner && row.cohort
                        ? row.cohort
                        : <span className="text-foreground-300">—</span>}
                    </td>
                    <td className="py-2.5 px-3 whitespace-nowrap">
                      <span className="text-foreground-700">{row.subscriptionStatus}</span>
                      {row.subscriptionStatus ? (row.subscriptionVerified ? <AppIcon className="ri-checkbox-circle-fill text-emerald-500 ml-1.5 align-middle" title="Verified" /> : <AppIcon className="ri-close-circle-fill text-red-500 ml-1.5 align-middle" title="Unverified" />) : null}
                    </td>
                    <td className="py-2.5 px-3 text-center">
                      <span className="flex flex-wrap items-center justify-center gap-2">
                        {isLearner && PLAN_EDITABLE_STATUSES.includes(row.programmeStatus || '') ? (
                          <button
                            onClick={() => setPlanFor(row)}
                            title={`${row.hasLearningPlan ? 'Edit' : 'Add'} ${row.name}'s learning plan`}
                            className="inline-flex max-w-full items-center gap-1 rounded-lg border border-foreground-200 px-2 py-1 text-[11px] font-medium text-foreground-600 transition-smooth hover:border-primary-300 hover:bg-primary-50/60 hover:text-primary-700 cursor-pointer whitespace-nowrap"
                          >
                            <AppIcon className={`ri-${row.hasLearningPlan ? 'edit' : 'add'}-line shrink-0 text-[12px]`} />
                            <span className="truncate">{row.hasLearningPlan ? 'Edit learning plan' : 'Add learning plan'}</span>
                          </button>
                        ) : isLearner && row.learningPlan ? (
                          // Past planning — the plan is fixed, so this opens the
                          // same modal read-only rather than routing to the
                          // learner's record, which is not their plan.
                          <button
                            onClick={() => setPlanFor(row)}
                            title={`View ${row.name}'s learning plan`}
                            className="text-primary-600 hover:underline cursor-pointer"
                          >
                            Learning plan
                          </button>
                        ) : null}
                        {/* Which modules a learner takes is settled in the plan;
                            moving them off one part-way through delivery is a
                            separate job, so it is a separate action — and only
                            once they are being taught. */}
                        {isLearner && row.hasLearningPlan
                          && SHIFT_STATUSES.includes(row.programmeStatus || '') && (
                          <button
                            onClick={() => setShiftFor(row)}
                            title={`Shift ${row.name} from one module to another`}
                            className="inline-flex items-center gap-1.5 rounded-lg border border-foreground-200 px-2.5 py-1 text-[12px] font-medium text-foreground-600 transition-smooth hover:border-primary-300 hover:bg-primary-50/60 hover:text-primary-700 cursor-pointer whitespace-nowrap"
                          >
                            <AppIcon className="ri-arrow-left-right-line" />
                            Shift module
                          </button>
                        )}
                        {/* Creating somebody no longer emails them, so the
                            invitation is sent from here (or from Accounts once
                            they have an account row). Hidden once they have
                            signed in — at that point a password reset is the
                            applicable action, not an invitation. */}
                        {!row.hasSignedIn && (
                          <SendInvitationButton
                            subjectType={isStaff ? 'staff' : isEmployer ? 'employer' : 'learner'}
                            subjectId={Number(row.id)}
                            name={row.name}
                            email={row.email}
                            onSent={load}
                          />
                        )}
                        {/* For learners whose employer blocks our email: staff
                            send the same single-use link another way. */}
                        {!row.hasSignedIn && isLearner && (
                          <CopyInvitationLinkButton subjectId={Number(row.id)} name={row.name} onIssued={refreshInPlace} />
                        )}
                      </span>
                    </td>
                    <td className="py-2.5 px-3">{isLearner && row.programmeStatus ? <StatusBadge status={row.programmeStatus} /> : null}</td>
                    <td className="py-2.5 px-3 whitespace-nowrap">
                      {!isLearner || attendanceModes === 'failed'
                        ? <span className="text-foreground-300">—</span>
                        : attendanceModes === null
                          ? <span className="text-foreground-300">…</span>
                          : rowAttendanceMode(attendanceModes, row) === 'lazy'
                            ? <span className="rounded-full border border-primary-200 bg-primary-50 px-2 py-0.5 text-[11px] font-semibold text-primary-700">Recorded</span>
                            : <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700">Live Sessions</span>}
                    </td>
                    <td className="py-2.5 px-3 text-center">
                      {openInPlace ? (
                        <span className="flex items-center justify-center gap-2">
                          <button
                            onClick={openInPlace}
                            title={isEmployer ? `Open ${row.name}'s employer page` : `Edit ${row.name}'s details`}
                            className="inline-flex items-center gap-1.5 rounded-lg border border-foreground-200 px-2.5 py-1 text-[12px] font-medium text-foreground-600 transition-smooth hover:border-primary-300 hover:bg-primary-50/60 hover:text-primary-700 cursor-pointer whitespace-nowrap"
                          >
                            {isEmployer
                              ? <><AppIcon className="ri-external-link-line text-[13px]" />View</>
                              : <><AppIcon className="ri-edit-line text-[13px]" />Edit</>}
                          </button>
                          {/* An employer's own details are still editable — the
                              primary action is now their page, so this is a
                              secondary link rather than the main button. */}
                          {isEmployer && row.employer && (
                            <button
                              onClick={() => setEditEmployer(row.employer!)}
                              title={`Edit ${row.name}'s details`}
                              className="text-[12px] text-foreground-400 hover:text-primary-600 hover:underline cursor-pointer whitespace-nowrap"
                            >
                              Edit
                            </button>
                          )}
                        </span>
                      ) : isLearner ? (
                        <span className="flex items-center justify-center gap-2">
                          <button
                            onClick={() => openLearnerPage(row)}
                            title={`Open ${row.name}'s learner page`}
                            className="inline-flex items-center gap-1.5 rounded-lg border border-foreground-200 px-2.5 py-1 text-[12px] font-medium text-foreground-600 transition-smooth hover:border-primary-300 hover:bg-primary-50/60 hover:text-primary-700 cursor-pointer whitespace-nowrap"
                          >
                            <AppIcon className="ri-external-link-line text-[13px]" />View
                          </button>
                          {/* The columns of this table — group, programme and
                              both statuses — are fields on the learner record,
                              so they are corrected in the record's own form
                              rather than in the cells. Secondary to View, which
                              is where the learner's work lives. */}
                          <button
                            onClick={() => setEditLearner(row)}
                            title={`Edit ${row.name}'s details, placement and status`}
                            className="text-[12px] text-foreground-400 hover:text-primary-600 hover:underline cursor-pointer whitespace-nowrap"
                          >
                            Edit
                          </button>
                        </span>
                      ) : null}
                    </td>
                  </tr>
                  );
                })}
                {!loading && !error && pageRows.length === 0 && <tr><td colSpan={11} className="py-10 text-center text-[13px] text-foreground-400">{rows.length === 0 ? 'No users yet. Use “Create” to add the first learner.' : applied.attendanceMode && attendanceModes === 'failed' ? 'Attendance modes could not be loaded, so the attendance mode filter cannot be applied.' : applied.attendanceMode && attendanceModes === null ? 'Loading attendance modes…' : 'No users match your filters.'}</td></tr>}
              </tbody>
            </table>
          </div>
          <div className="flex flex-col items-center justify-between gap-3 border-t border-foreground-100 px-5 py-2.5 sm:flex-row">
            <p className="text-[11px] text-foreground-500">Showing {showingStart} to {showingEnd} of {filtered.length} users</p>
            <Pagination page={page} totalPages={totalPages} onChange={nextPage => setQueryValues({ page: nextPage }, { replace: false })} />
            <label className="hidden items-center gap-2 text-[11px] text-foreground-500 sm:flex">
              Rows per page
              <select className="rounded-lg border border-foreground-200 bg-background-50 px-2 py-1 text-[11px] text-foreground-700" value={PAGE_SIZE} aria-label="Rows per page" onChange={() => undefined}>
                <option value={PAGE_SIZE}>{PAGE_SIZE}</option>
              </select>
            </label>
          </div>
        </div>
      </div>

      {createModalOpen && <CreateUserModal onClose={() => setCreateModalOpen(false)} onCreated={load} />}
      {importModalOpen && <ImportLearnersModal onClose={() => setImportModalOpen(false)} onImported={applyLearnerImport} />}
      {bulkOpen && <BulkAttendanceModeModal rows={rows} onClose={() => { setBulkOpen(false); loadAttendanceModes(); }} />}
      {createAdminOpen && <CreateStaffModal variant="admin" onClose={() => setCreateAdminOpen(false)} onCreated={load} />}
      {createTutorOpen && <CreateStaffModal variant="tutor" onClose={() => setCreateTutorOpen(false)} onCreated={load} />}
      {editStaff && <EditStaffModal row={editStaff} onClose={() => setEditStaff(null)} onSaved={applyStaffUpdate} />}
      {/* The create form again, in edit mode — one form per record, so the
          fields cannot drift apart between adding a learner and correcting one. */}
      {editLearner && (
        <CreateUserModal
          editing={editLearner}
          onClose={() => setEditLearner(null)}
          onCreated={load}
          onSaved={applyLearnerUpdate}
        />
      )}
      {shiftFor && (
        <ShiftModuleModal
          learnerId={shiftFor.id}
          learnerName={shiftFor.name}
          onClose={() => setShiftFor(null)}
          onSaved={load}
        />
      )}
      {planFor && (
        <LearningPlanModal
          learnerId={planFor.id}
          learnerName={planFor.name}
          readOnly={!PLAN_EDITABLE_STATUSES.includes(planFor.programmeStatus || '')}
          // An active learner already has progress against these modules, so
          // the modal warns before a removal orphans it.
          hasStarted={planFor.programmeStatus !== DELIVERY_STATUS}
          onClose={() => setPlanFor(null)}
          onSaved={load}
        />
      )}
      {/* Organisations are companies rather than people, so they don't belong in
          a directory of users — creating one just confirms and closes. Employers
          are people and do get listed. */}
      {createEmployerOpen && <CreateEmployerModal onClose={() => setCreateEmployerOpen(false)} onCreated={load} />}
      {createOrgOpen && <CreateOrganisationModal onClose={() => setCreateOrgOpen(false)} onCreated={() => {}} />}
      {editEmployer && (
        <CreateEmployerModal
          row={editEmployer}
          onClose={() => setEditEmployer(null)}
          onCreated={applyEmployerUpdate}
        />
      )}
    </WorkspaceShell>
  );
}
