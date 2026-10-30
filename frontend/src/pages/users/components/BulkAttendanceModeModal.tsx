import { useEffect, useMemo, useState } from 'react';
// Explicit, not auto-imported: vitest.config.ts leaves unplugin-auto-import out.
import { AppIcon } from '@/components/feature/AppIcon';
import { fetchAttendanceModes, updateAttendanceMode, type AttendanceMode, type SavedAttendanceMode } from '@/api/attendanceLectures';
import type { UserListRow } from '../types';
import { Modal } from './Modal';
import { Pagination, inputClass, btnPrimary, btnSecondary } from './ui';

type Mode = 'live' | 'lazy';
type Outcome = { tone: 'ok' | 'error'; message: string };

const PAGE_SIZE = 25;
// A large selection is sent a few learners at a time rather than all at once.
const CONCURRENCY = 4;

const keyOf = (row: UserListRow) => `${row.source}:${row.id}`;
const kindOf = (row: UserListRow) => (row.source === 'commercial' ? 'commercial' : 'apprenticeship');
const same = (a: string | undefined, b: string) => (a ?? '').trim().toLowerCase() === b.trim().toLowerCase();
const MODE_FILTER_OPTIONS = ['Live Sessions', 'Recorded'];
const distinct = (rows: UserListRow[], pick: (r: UserListRow) => string | undefined) =>
  Array.from(new Set(rows.map(pick).map((v) => (v ?? '').trim()).filter(Boolean))).sort();

/** Reads the saved state back rather than assuming the requested mode took. */
function describeOutcome(requested: Mode, result: AttendanceMode): Outcome {
  if (result.mode !== requested) return { tone: 'error', message: 'Mode was not changed.' };
  return { tone: 'ok', message: requested === 'live' ? 'Live Sessions' : 'Recorded' };
}

/** Same rule as CurrentMode: no saved entry, or only a pending request, is Live Sessions. */
const modeLabelOf = (saved: SavedAttendanceMode | undefined) => (saved?.mode === 'lazy' ? 'Recorded' : 'Live Sessions');

/** The learner's mode as it stands now; no saved entry means Live Sessions. */
function CurrentMode({ saved }: { saved: SavedAttendanceMode | undefined }) {
  if (saved?.mode === 'lazy') {
    return <span className="rounded-full border border-primary-200 bg-primary-50 px-2 py-0.5 text-[11px] font-semibold text-primary-700">Recorded</span>;
  }
  return (
    <span className="inline-flex flex-col gap-0.5">
      <span className="w-fit rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700">Live Sessions</span>
      {saved?.requestedMode === 'lazy' && <span className="text-[11px] text-amber-700">Recorded requested</span>}
    </span>
  );
}

const TONE_CLASS: Record<Outcome['tone'], string> = {
  ok: 'text-emerald-700',
  error: 'text-red-600',
};

/**
 * Sets the attendance mode of many learners at once. Each learner goes through
 * the same per-learner endpoint the attendance page uses, so the server still
 * checks the administrator and audits every change. The change is direct: no
 * manager approval and no email, unlike a learner's own Recorded request.
 */
export function BulkAttendanceModeModal({ rows, onClose }: { rows: UserListRow[]; onClose: () => void }) {
  // Same rule as the directory's isLearnerRow: staff and employer contacts have
  // no attendance record.
  const learners = useMemo(() => rows.filter((r) => r.source !== 'staff' && r.source !== 'employer'), [rows]);
  const [caseOwner, setCaseOwner] = useState('');
  const [programme, setProgramme] = useState('');
  const [cohort, setCohort] = useState('');
  const [group, setGroup] = useState('');
  const [search, setSearch] = useState('');
  const [modeFilter, setModeFilter] = useState('');
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [mode, setMode] = useState<Mode>('live');
  const [confirming, setConfirming] = useState(false);
  const [running, setRunning] = useState(false);
  const [outcomes, setOutcomes] = useState<Record<string, Outcome>>({});
  // null while loading; the column shows a placeholder rather than guessing.
  const [modes, setModes] = useState<Record<string, SavedAttendanceMode> | null>(null);
  const [modesError, setModesError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    fetchAttendanceModes(controller.signal)
      .then((result) => {
        if (result.available) setModes(result.modes);
        else setModesError('Attendance mode is not available yet.');
      })
      .catch((e: Error) => { if (!controller.signal.aborted) setModesError(e.message || 'Could not load attendance modes.'); });
    return () => controller.abort();
  }, []);

  // Cohorts narrow to the chosen programme and groups to the chosen cohort, so
  // the lists only offer combinations some learner is actually on.
  const caseOwnerOptions = useMemo(() => distinct(learners, (r) => r.caseOwner), [learners]);
  const programmeOptions = useMemo(() => distinct(learners, (r) => r.programme), [learners]);
  const cohortOptions = useMemo(
    () => distinct(programme ? learners.filter((r) => same(r.programme, programme)) : learners, (r) => r.cohort),
    [learners, programme],
  );
  const groupOptions = useMemo(
    () => distinct(
      learners.filter((r) => (!programme || same(r.programme, programme)) && (!cohort || same(r.cohort, cohort))),
      (r) => r.group,
    ),
    [learners, programme, cohort],
  );

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return learners.filter((r) =>
      (!caseOwner || same(r.caseOwner, caseOwner))
      && (!programme || same(r.programme, programme))
      && (!cohort || same(r.cohort, cohort))
      && (!group || same(r.group, group))
      && (!modeFilter || !modes || modeLabelOf(modes[r.id]) === modeFilter)
      && (!needle || r.name.toLowerCase().includes(needle)));
  }, [learners, caseOwner, programme, cohort, group, modeFilter, modes, search]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const visible = filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);
  const allFilteredSelected = filtered.length > 0 && filtered.every((r) => selected.has(keyOf(r)));
  const selectedRows = learners.filter((r) => selected.has(keyOf(r)));

  const resetPage = () => setPage(1);
  const changeProgramme = (v: string) => { setProgramme(v); setCohort(''); setGroup(''); resetPage(); };
  const changeCohort = (v: string) => { setCohort(v); setGroup(''); resetPage(); };

  const toggle = (row: UserListRow) => {
    setConfirming(false);
    setSelected((prev) => {
      const next = new Set(prev);
      const key = keyOf(row);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };
  const toggleAllFiltered = () => {
    setConfirming(false);
    setSelected((prev) => {
      const next = new Set(prev);
      filtered.forEach((r) => (allFilteredSelected ? next.delete(keyOf(r)) : next.add(keyOf(r))));
      return next;
    });
  };

  const apply = async () => {
    setConfirming(false);
    setRunning(true);
    const queue = [...selectedRows];
    setOutcomes((prev) => {
      const next = { ...prev };
      queue.forEach((r) => { delete next[keyOf(r)]; });
      return next;
    });
    const worker = async () => {
      for (let row = queue.shift(); row; row = queue.shift()) {
        let outcome: Outcome;
        try {
          const result = await updateAttendanceMode(kindOf(row), row.id, mode, { direct: true });
          outcome = describeOutcome(mode, result);
          // Keep the column in step with what the server saved.
          setModes((prev) => (prev ? { ...prev, [row.id]: { mode: result.mode, requestedMode: result.requestedMode } } : prev));
        } catch (e) {
          // A non-JSON error page surfaces as a SyntaxError; don't show that text.
          const message = e instanceof Error && !(e instanceof SyntaxError) ? e.message : 'Could not update attendance mode.';
          outcome = { tone: 'error', message };
        }
        const key = keyOf(row);
        setOutcomes((prev) => ({ ...prev, [key]: outcome }));
      }
    };
    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, queue.length) }, worker));
    setRunning(false);
  };

  const results = Object.values(outcomes);
  const failed = results.filter((o) => o.tone === 'error').length;
  const modeLabel = mode === 'live' ? 'Live Sessions' : 'Recorded';

  const footer = confirming ? (
    <>
      <p className="mr-auto text-[12px] text-foreground-600">
        Set {selectedRows.length} learner{selectedRows.length === 1 ? '' : 's'} to {modeLabel}?
      </p>
      <button type="button" className={btnSecondary} onClick={() => setConfirming(false)}>Cancel</button>
      <button type="button" className={btnPrimary} onClick={apply}>Confirm</button>
    </>
  ) : (
    <>
      <button type="button" className={btnSecondary} onClick={onClose} disabled={running}>Close</button>
      <button type="button" className={btnPrimary} disabled={running || selectedRows.length === 0} onClick={() => setConfirming(true)}>
        {running ? <><AppIcon className="ri-loader-4-line animate-spin" />Updating…</> : <>Apply to {selectedRows.length} selected</>}
      </button>
    </>
  );

  return (
    <Modal title="Bulk actions — attendance mode" onClose={running ? () => undefined : onClose} dismissible={!running} size="max-w-5xl" footer={footer}>
      <div className="space-y-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-6">
          <Select label="Case owner" value={caseOwner} options={caseOwnerOptions} onChange={(v) => { setCaseOwner(v); resetPage(); }} />
          <Select label="Programme" value={programme} options={programmeOptions} onChange={changeProgramme} />
          <Select label="Cohort" value={cohort} options={cohortOptions} onChange={changeCohort} />
          <Select label="Group" value={group} options={groupOptions} onChange={(v) => { setGroup(v); resetPage(); }} />
          {/* Disabled until the saved modes load, so the list never filters on a guess. */}
          <Select label="Attendance mode" value={modeFilter} options={MODE_FILTER_OPTIONS} disabled={!modes} onChange={(v) => { setModeFilter(v); resetPage(); }} />
          <div>
            <label htmlFor="bulk-learner-search" className="mb-1 block text-[12px] font-semibold text-foreground-800">Learner name</label>
            <input id="bulk-learner-search" className={inputClass} placeholder="Search learners" value={search} onChange={(e) => { setSearch(e.target.value); resetPage(); }} />
          </div>
        </div>

        <fieldset className="flex flex-wrap items-center gap-4 rounded-xl border border-foreground-100 bg-background-100/60 px-4 py-3" disabled={running}>
          <legend className="sr-only">Attendance mode</legend>
          <span className="text-[12px] font-semibold text-foreground-800">Set attendance mode to</span>
          {(['live', 'lazy'] as const).map((m) => (
            <label key={m} className="inline-flex cursor-pointer items-center gap-2 text-[13px] text-foreground-700">
              <input type="radio" name="bulk-attendance-mode" value={m} checked={mode === m} onChange={() => { setMode(m); setConfirming(false); }} />
              {m === 'live' ? 'Live Sessions' : 'Recorded'}
            </label>
          ))}
        </fieldset>

        <div className="flex flex-wrap items-center justify-between gap-2 text-[12px] text-foreground-500">
          <span>
            {filtered.length} learner{filtered.length === 1 ? '' : 's'} shown · {selectedRows.length} selected
            {modesError && <span className="ml-2 text-red-600">{modesError}</span>}
          </span>
          {results.length > 0 && !running && (
            <span className={failed ? 'text-red-600' : 'text-emerald-700'}>
              {results.length - failed} updated{failed ? `, ${failed} need attention` : ''}
            </span>
          )}
        </div>

        <div className="overflow-x-auto rounded-xl border border-foreground-100">
          <table className="w-full text-left text-[13px]">
            <thead className="bg-background-100 text-[11px] uppercase tracking-wide text-foreground-500">
              <tr>
                <th className="w-10 px-3 py-2">
                  <input type="checkbox" aria-label="Select all shown learners" checked={allFilteredSelected} onChange={toggleAllFiltered} disabled={running || filtered.length === 0} />
                </th>
                {['Learner', 'Case owner', 'Programme', 'Cohort', 'Group', 'Attendance mode', 'Result'].map((h) => <th key={h} className="px-3 py-2 font-semibold">{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {visible.length === 0 ? (
                <tr><td colSpan={8} className="px-3 py-6 text-center text-foreground-400">No learners match these filters.</td></tr>
              ) : visible.map((row) => {
                const key = keyOf(row);
                const outcome = outcomes[key];
                return (
                  <tr key={key} className="border-t border-foreground-100">
                    <td className="px-3 py-2">
                      <input type="checkbox" aria-label={`Select ${row.name}`} checked={selected.has(key)} onChange={() => toggle(row)} disabled={running} />
                    </td>
                    <td className="px-3 py-2 font-medium text-foreground-900">{row.name}</td>
                    <td className="px-3 py-2 text-foreground-600">{row.caseOwner || '—'}</td>
                    <td className="px-3 py-2 text-foreground-600">{row.programme || '—'}</td>
                    <td className="px-3 py-2 text-foreground-600">{row.cohort || '—'}</td>
                    <td className="px-3 py-2 text-foreground-600">{row.group || '—'}</td>
                    <td className="px-3 py-2">
                      {modes ? <CurrentMode saved={modes[row.id]} /> : <span className="text-foreground-300">{modesError ? '—' : '…'}</span>}
                    </td>
                    <td className={`px-3 py-2 text-[12px] ${outcome ? TONE_CLASS[outcome.tone] : 'text-foreground-300'}`}>{outcome?.message ?? ''}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {totalPages > 1 && <Pagination page={currentPage} totalPages={totalPages} onChange={setPage} />}
      </div>
    </Modal>
  );
}

function Select({ label, value, options, disabled, onChange }: { label: string; value: string; options: string[]; disabled?: boolean; onChange: (v: string) => void }) {
  const id = `bulk-${label.toLowerCase().replace(/\s+/g, '-')}`;
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-[12px] font-semibold text-foreground-800">{label}</label>
      <select id={id} className={`${inputClass} cursor-pointer disabled:cursor-not-allowed`} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
        <option value="">--All--</option>
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </div>
  );
}
