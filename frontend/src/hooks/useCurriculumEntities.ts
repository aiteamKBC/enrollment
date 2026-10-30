import { useCallback, useEffect, useRef, useState } from 'react';
import {
  fetchCurriculumCoaches,
  fetchCurriculumHolidays,
  fetchCurriculumOverview,
  fetchCurriculumTeamsMeetingSummaries,
  fetchCurriculumTutors,
  type CurriculumCohort,
  type CurriculumGroup,
  type CurriculumHoliday,
  type CurriculumModule,
  type CurriculumProgramme,
  type CurriculumStaffProfile,
  type CurriculumTeamsMeetingSummary,
} from '@/lib/curriculumApi';
import { useLiveRefresh } from '@/hooks/useRefreshOnReturn';

/**
 * The entity pages (Programmes / Cohorts / Groups / Modules) all read the same
 * four collections and derive their parent chains locally, so they share one
 * load instead of each inventing its own. The compact overview carries
 * programmes, cohorts, groups and modules in a single cached request; staff,
 * holidays and Teams state are opt-in because only some pages need them.
 *
 * This deliberately does NOT hold write logic: every page saves through the
 * canonical endpoints in `lib/curriculumApi` and then calls `reload({ silent })`.
 * That refresh is not instant, so two things go with it: `refreshing` says a
 * background load is in flight (the list on screen is real but a beat behind),
 * and `applyLocal` lets a page drop the record the write just returned straight
 * into the collections. Without them a create looked like it had done nothing
 * until the refresh landed seconds later.
 */
export interface CurriculumEntitiesOptions {
  includeStaff?: boolean;
  includeHolidays?: boolean;
  includeTeams?: boolean;
}

export interface CurriculumEntities {
  programmes: CurriculumProgramme[];
  cohorts: CurriculumCohort[];
  groups: CurriculumGroup[];
  modules: CurriculumModule[];
  tutors: CurriculumStaffProfile[];
  coaches: CurriculumStaffProfile[];
  holidays: CurriculumHoliday[];
  teamsMeetings: CurriculumTeamsMeetingSummary[];
}

const EMPTY: CurriculumEntities = {
  programmes: [],
  cohorts: [],
  groups: [],
  modules: [],
  tutors: [],
  coaches: [],
  holidays: [],
  teamsMeetings: [],
};

// `revalidate` ignores this tab's cache but lets the server answer from its own,
// which is what a re-read after somebody else's write needs: current rows
// without the multi-second rebuild `skipCache` forces out of the backend.
type LoadOptions = { silent?: boolean; skipCache?: boolean; revalidate?: boolean };

export function useCurriculumEntities(options: CurriculumEntitiesOptions = {}) {
  const { includeStaff = false, includeHolidays = false, includeTeams = false } = options;
  const [entities, setEntities] = useState<CurriculumEntities>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestIdRef = useRef(0);

  const load = useCallback(async (signal?: AbortSignal, loadOptions: LoadOptions = {}) => {
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    if (loadOptions.silent) setRefreshing(true);
    else setLoading(true);
    try {
      // All five reads are started together. Nothing here depends on anything
      // else here: the compact overview carries programmes, cohorts, groups and
      // modules, while staff come from the enrolment directory, holidays from
      // the calendar and Teams state from its own summary endpoint. Awaiting
      // the overview first -- which is what this used to do -- held the other
      // four behind the slowest request on the page for no reason.
      const overviewPending = fetchCurriculumOverview(signal, {
        compact: true,
        skipCache: loadOptions.skipCache,
        revalidate: loadOptions.revalidate,
      });
      const tutorsPending = includeStaff
        ? fetchCurriculumTutors(signal, { skipCache: loadOptions.skipCache, revalidate: loadOptions.revalidate }).catch(() => [])
        : Promise.resolve([]);
      const coachesPending = includeStaff
        ? fetchCurriculumCoaches(signal, { skipCache: loadOptions.skipCache, revalidate: loadOptions.revalidate }).catch(() => [])
        : Promise.resolve([]);
      const holidaysPending = includeHolidays
        ? fetchCurriculumHolidays(signal, { skipCache: loadOptions.skipCache, revalidate: loadOptions.revalidate }).catch(() => [])
        : Promise.resolve([]);
      const teamsPending = includeTeams
        ? fetchCurriculumTeamsMeetingSummaries(signal, { skipCache: loadOptions.skipCache, revalidate: loadOptions.revalidate }).catch(() => [])
        : Promise.resolve([]);

      const overview = await overviewPending;
      if (signal?.aborted || requestId !== requestIdRef.current) return null;

      // Paint the structure first; the optional collections are additive and a
      // failure in any of them must not blank the page that already has its
      // programmes, cohorts, groups and modules.
      const base: CurriculumEntities = {
        ...EMPTY,
        programmes: overview.programmes || [],
        cohorts: overview.cohorts || [],
        groups: overview.groups || [],
        modules: overview.modules || [],
      };
      setEntities(previous => ({
        ...base,
        tutors: previous.tutors,
        coaches: previous.coaches,
        holidays: previous.holidays,
        teamsMeetings: previous.teamsMeetings,
      }));
      setError(null);
      setLoading(false);
      setLoaded(true);
      // The structure has landed, so the table is already correct; the optional
      // collections below are additive and must not keep the bar running.
      setRefreshing(false);

      const [tutors, coaches, holidays, teamsMeetings] = await Promise.all([
        tutorsPending, coachesPending, holidaysPending, teamsPending,
      ]);
      if (signal?.aborted || requestId !== requestIdRef.current) return null;

      const next: CurriculumEntities = { ...base, tutors, coaches, holidays, teamsMeetings };
      setEntities(next);
      return next;
    } catch (err) {
      if (signal?.aborted || requestId !== requestIdRef.current) return null;
      setError(err instanceof Error ? err.message : 'Unable to load curriculum data.');
      return null;
    } finally {
      if (!signal?.aborted && requestId === requestIdRef.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [includeHolidays, includeStaff, includeTeams]);

  /**
   * Write a record a save has already committed into the collections on screen,
   * ahead of the refresh that will replace them with the server's copy. Only
   * ever called with what an endpoint returned, so the row is real — it is early,
   * not invented, and the reload immediately behind it is the source of truth.
   */
  const applyLocal = useCallback(
    (updater: (previous: CurriculumEntities) => CurriculumEntities) => setEntities(updater),
    [],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  // The entity pages sit open while records are created elsewhere, so they
  // re-read when the reader returns to the tab and when another tab writes.
  // Silent, so the table stays on screen rather than collapsing to skeletons.
  useLiveRefresh(() => {
    void load(undefined, { silent: true, revalidate: true });
  });

  return {
    ...entities,
    entities,
    loading,
    /** A background refresh is in flight behind a list that is already painted. */
    refreshing,
    applyLocal,
    /** True once a structure payload has landed — used to tell "empty" from "not here yet". */
    loaded,
    error,
    reload: (loadOptions?: LoadOptions) => load(undefined, { skipCache: true, ...loadOptions }),
  };
}
