// ============================================================================
// Curriculum lookups for the training-plan builder.
// Read-only cascade sourced from normalized `curriculum` schema tables via the
// Django backend.
// ============================================================================

import { createCachedResource } from './cachedRequest';

const BASE = '/learner_api/curriculum';

export interface CurriculumItem {
  id: string;
  title: string;
}
export interface WeekItem extends CurriculumItem {
  weekNumber: number;
}
export interface ComponentItem extends CurriculumItem {
  type: string;
  expectedOtjh: number | null;
}

async function request<T>(url: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, { headers: { 'Content-Type': 'application/json' } });
  } catch {
    throw new Error('Could not reach the server. Is the backend running on port 8000?');
  }
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data as T;
}

const qs = (params: Record<string, string>) =>
  new URLSearchParams(params).toString();

/** Distinct programmes. */
export async function fetchProgrammes(): Promise<string[]> {
  return (await request<{ results: string[] }>(`${BASE}/programmes/`)).results;
}

/** Cohorts for a programme. */
export async function fetchCohorts(programme: string): Promise<string[]> {
  return (await request<{ results: string[] }>(`${BASE}/cohorts/?${qs({ programme })}`)).results;
}

/** Groups for a programme + cohort. */
export async function fetchGroups(programme: string, cohort: string): Promise<string[]> {
  return (await request<{ results: string[] }>(`${BASE}/groups/?${qs({ programme, cohort })}`)).results;
}

/**
 * The KSB list a learner self-assesses against, resolved from their programme's
 * authored profile in curriculum.ksb_profiles. `standard` is null when the
 * programme has no profile authored yet.
 */
export interface KsbProfileResponse {
  standard: { id: string; label: string } | null;
  results: { id: string; theme: string; kind: 'Knowledge' | 'Skill' | 'Behaviour'; codes: string[]; title: string }[];
}

/**
 * Authored competencies change only when the curriculum is edited, and every
 * learner on a programme gets the identical list — so this is cached for longer
 * than the learner-specific payloads. The wizard asks for it on every mount to
 * seed unrated rows, which was a request per visit for a list that had not moved.
 */
const ksbProfileResource = createCachedResource<KsbProfileResponse>(
  'ksb-profile',
  (programme) => request<KsbProfileResponse>(`${BASE}/ksb-profile/?${qs({ programme })}`),
  5 * 60_000,
);

export async function fetchKsbProfile(programme: string): Promise<KsbProfileResponse> {
  return ksbProfileResource.read(programme);
}

/**
 * Store a profile that another response already carried, so the next
 * fetchKsbProfile resolves from memory instead of opening a request.
 *
 * The wizard bootstrap returns the learner's profile alongside their board (see
 * fetchWizardBootstrap): the profile used to be fetched only after the saved
 * answers had arrived, which put it third in a chain of round-trips and left the
 * Skills Radar with no rows until it landed.
 */
export function primeKsbProfile(programme: string, data: KsbProfileResponse): void {
  ksbProfileResource.prime(programme, data);
}

/**
 * The profile already in memory, or undefined. Lets the Skills Radar step render
 * its competencies on the first frame instead of flashing a spinner for a list it
 * already has — the wizard bootstrap primes this before the step ever mounts.
 */
export function peekKsbProfile(programme: string): KsbProfileResponse | undefined {
  return ksbProfileResource.peek(programme);
}

/** Modules for a programme (independent of cohort/group). */
export async function fetchModules(programme: string): Promise<CurriculumItem[]> {
  return (await request<{ results: CurriculumItem[] }>(`${BASE}/modules/?${qs({ programme })}`)).results;
}

/** Weeks for a module. */
export async function fetchWeeks(moduleId: string): Promise<WeekItem[]> {
  return (await request<{ results: WeekItem[] }>(`${BASE}/weeks/?${qs({ module: moduleId })}`)).results;
}

/** Humanise a component type slug, e.g. 'live_session' -> 'Live session'. */
function humaniseType(type: string): string {
  if (!type) return '';
  const spaced = type.replace(/_/g, ' ');
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/**
 * Components for a week.
 * Imported modules often store an unhelpful component title (e.g. "Week 1"),
 * so we surface the component TYPE and only append the title when it adds info.
 */
export async function fetchComponents(weekId: string): Promise<ComponentItem[]> {
  const results = (await request<{ results: ComponentItem[] }>(`${BASE}/components/?${qs({ week: weekId })}`)).results;
  return results.map((c) => {
    const typeLabel = humaniseType(c.type);
    const showTitle = c.title && c.title.trim().toLowerCase() !== typeLabel.toLowerCase();
    return { ...c, title: showTitle ? `${typeLabel} · ${c.title}` : typeLabel || c.title };
  });
}

export interface LegacyOtjhItem {
  module: string;
  week: string;
  component: string;
}

/**
 * expected_otjh lookup for training plans saved BEFORE the structured-plan
 * format existed — their component/week ids are client-generated, so they
 * can't be looked up by id via fetchComponents(). Resolves by (module, week,
 * component) TITLE instead, matching the same fallback the learner-facing
 * training-plan view uses. Only entries with a match are present in the
 * result map (unmatched legacy titles — e.g. a renamed/re-authored module —
 * simply have no recoverable hours).
 */
export async function fetchLegacyOtjh(items: LegacyOtjhItem[]): Promise<Record<string, number>> {
  if (items.length === 0) return {};
  let res: Response;
  try {
    res = await fetch(`${BASE}/legacy-otjh/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items }),
    });
  } catch {
    return {}; // best-effort — hours simply won't show for legacy items
  }
  if (!res.ok) return {};
  const data = await res.json().catch(() => null);
  return (data && data.results) || {};
}

/** Build the lookup key fetchLegacyOtjh's result map uses for one item. */
export function legacyOtjhKey(module: string, week: string, component: string): string {
  return `${module}|${week}|${component}`;
}
