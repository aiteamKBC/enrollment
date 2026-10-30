import { coachFetch } from '@/lib/coachFetch';

export type CurriculumStatus = 'active' | 'draft' | 'archived' | 'published' | 'planned' | 'completed' | string;

/**
 * Determines if an error is retryable.
 * Retries only transient failures; permanent errors fail immediately.
 */
export function isRetryableError(error: unknown): boolean {
  // Check for AbortError first (DOMException)
  if (error instanceof DOMException && error.name === 'AbortError') {
    return false;
  }

  // Check for HTTP status codes embedded in Error messages from fetchJsonUncached
  // Format: "Curriculum API returned NNN for /path"
  if (error instanceof Error) {
    const msg = error.message.toLowerCase();

    // Extract status code from error message if present
    const statusMatch = error.message.match(/curriculum api returned (\d{3})/i);
    if (statusMatch) {
      const status = parseInt(statusMatch[1], 10);
      // Transient errors (retry)
      if (status === 408 || status === 429 || (status >= 500 && status <= 599)) {
        return true;
      }
      // Permanent errors (don't retry)
      if ((status >= 400 && status <= 499)) {
        return false;
      }
    }

    // A client-side timeout (`fetchJsonOnce`'s own "Curriculum API timed out for
    // …" message) is deliberately excluded here: the request already spent its
    // whole budget once, so retrying it spends that budget again for the same
    // outcome instead of surfacing the failure. This used to read
    // `msg.includes('timeout')`, which never matched the word "timed out" this
    // codebase actually throws -- every timeout fell through to "retry unknown
    // errors" below and got retried up to RETRY_ATTEMPTS times, each attempt
    // paying the full timeout again, which is how one slow endpoint turned into
    // several minutes of retries stacked on top of each other.
    if (msg.includes('timed out')) {
      return false;
    }

    // A network error with no response at all (offline, DNS, connection reset)
    // is the transient case this retry exists for.
    if (msg.includes('network')) {
      return true;
    }
  }

  // If it's a Response object (unlikely in production given error handling above)
  if (error instanceof Response) {
    const status = error.status;
    // Transient errors (retry)
    if (status === 408 || status === 429 || (status >= 500 && status <= 599)) {
      return true;
    }
    // Permanent errors (don't retry)
    if ((status >= 400 && status <= 499)) {
      return false;
    }
  }

  // Default: retry unknown errors (network issues with no captured status)
  return true;
}

interface CurriculumRequestInit {
  method?: string;
  credentials?: RequestCredentials;
  headers?: Record<string, string>;
  body?: string;
  signal?: AbortSignal;
  timeoutMs?: number;
  /**
   * Read past every cache, client *and* server: sends `Cache-Control: no-cache`,
   * which makes the backend rebuild the payload from the database.
   *
   * That rebuild costs seconds -- the curriculum overview takes ~13s of Neon
   * round trips -- so this is only for a read that must reflect a write this
   * client just made. For "show me what exists now" on mount, use `revalidate`.
   */
  skipCache?: boolean;
  /**
   * Ignore the client-side cache, but let the server answer from its own.
   *
   * The same "always current" guarantee as `skipCache` for anything written
   * elsewhere -- another tab, the structure wizard, another user -- because the
   * request still goes to the network and the backend invalidates its payload
   * cache on write. It just does not force the multi-second rebuild. The fresh
   * response is written back to the client cache, so other pages benefit.
   */
  revalidate?: boolean;
  /**
   * Internal: aborted once every caller sharing this GET has gone. A request
   * still waiting in the programme-statistics queue is then dropped instead of
   * being sent; one already on the wire is left to finish. Never sent to fetch.
   */
  abandonSignal?: AbortSignal;
  /** Internal: called when a queued programme-statistics request is sent. */
  onDispatch?: () => void;
}

export class CurriculumApiError extends Error {
  status: number;
  path: string;
  data: unknown;

  constructor(message: string, status: number, path: string, data?: unknown) {
    super(message);
    this.name = 'CurriculumApiError';
    this.status = status;
    this.path = path;
    this.data = data;
    Object.setPrototypeOf(this, CurriculumApiError.prototype);
  }
}

/** One module already holding a slot the save tried to book the same tutor into. */
export interface CurriculumTutorConflict {
  moduleCatalogueId: string;
  moduleName: string;
  programme: string;
  cohort: string;
  group: string;
  startTime: string;
  endTime: string;
  /** Every date the two modules overlap on, not just the first. */
  dates: string[];
}

export interface CurriculumTutorConflictPayload {
  error: string;
  tutorConflicts: CurriculumTutorConflict[];
  tutor: string;
  moduleName: string;
}

/**
 * A save refused because it would have put one tutor in two places at once.
 *
 * Raised by every endpoint that assigns a tutor — the module PATCH, the group
 * modules POST/PATCH, the group and staffing PATCHes and the programme tree save
 * — so a caller can recognise it once rather than per screen.
 */
export function isTutorConflictError(error: unknown): error is CurriculumApiError & { data: CurriculumTutorConflictPayload } {
  return (
    error instanceof CurriculumApiError
    && error.status === 409
    && Boolean(error.data)
    && typeof error.data === 'object'
    && Array.isArray((error.data as CurriculumTutorConflictPayload).tutorConflicts)
  );
}

/**
 * Optimistic concurrency for the records a drawer holds open.
 *
 * The Module Builder's structure save has always sent back the revision it read
 * and been refused if the module moved underneath it. The drawers and the Week
 * Builder had nothing: each sends every field it renders, so whichever save
 * landed second put the other editor's work back to what it was, and the only
 * thing standing in the way was a poll happening to arrive first.
 *
 * These are the client half of the same guard, now on all of them. A surface
 * reads a token, sends it back with its save, and on a refusal rebases its
 * outstanding changes onto the stored record the refusal carries.
 */

/** The record kinds the server will fingerprint. */
export type CurriculumRecordKind = 'programme' | 'cohort' | 'group' | 'module' | 'weekTemplate';

export interface CurriculumRecordRevisions {
  programme?: Record<string, string>;
  cohort?: Record<string, string>;
  group?: Record<string, string>;
  module?: Record<string, string>;
  weekTemplate?: Record<string, string>;
}

/**
 * Current tokens for the records named.
 *
 * Off the cached curriculum overview deliberately: a token minted there would
 * be one more thing to keep fresh, and would go stale exactly when a colleague
 * saved. One indexed read per record instead, when a drawer opens.
 *
 * A record the server cannot fingerprint comes back as '', which a caller
 * should treat as "save unguarded" rather than as a token -- that is the
 * behaviour these screens had before any of this existed, so a fingerprint
 * that cannot be read degrades to it rather than blocking the save.
 */
export function fetchCurriculumRevisions(
  wanted: Partial<Record<CurriculumRecordKind, string[]>>,
  signal?: AbortSignal,
): Promise<CurriculumRecordRevisions> {
  const query = new URLSearchParams();
  (Object.keys(wanted) as CurriculumRecordKind[]).forEach(kind => {
    (wanted[kind] || []).filter(Boolean).forEach(id => query.append(kind, id));
  });
  if (!query.toString()) return Promise.resolve({});
  return fetchJson<{ revisions: CurriculumRecordRevisions }>(
    `/curriculum/revisions/?${query.toString()}`,
    { signal, skipCache: true },
  ).then(payload => payload.revisions || {});
}

/** What a save was refused with, and what it needs to rebase onto. */
export interface CurriculumRecordConflict<TRecord> {
  /** The token the save carried. */
  expectedRevision: string;
  /** The token the record actually holds now; what the retry must send. */
  currentRevision: string;
  /**
   * The stored record, so the editor can fold their outstanding changes into
   * it without a second call. Null when the server could not read the record
   * at all -- nothing was compared, so there is no other version to show.
   */
  record: TRecord | null;
}

/**
 * A save refused because somebody else saved first.
 *
 * Distinguished from the tutor-schedule 409 by the `conflict` flag rather than
 * by the status, since both endpoints raise 409 and a drawer has to be able to
 * tell "you would double-book a tutor" from "rebase and try again".
 */
/**
 * The token a guarded save carries, folded into its body.
 *
 * In the body rather than a separate argument so that adding the guard did not
 * change the shape of a single existing call. Omitted when empty: an absent
 * token means "do not check me", which is what every unguarded caller has
 * always sent and what a surface whose token could not be read falls back to.
 */
export function guardedInput<TInput extends object>(input: TInput, expectedRevision?: string): TInput {
  return expectedRevision ? { ...input, expectedRevision } : input;
}

export function recordConflict<TRecord>(
  error: unknown,
  kind: CurriculumRecordKind,
): CurriculumRecordConflict<TRecord> | null {
  if (!(error instanceof CurriculumApiError) || error.status !== 409) return null;
  const data = error.data as Record<string, unknown> | undefined;
  if (!data || typeof data !== 'object' || data.conflict !== true) return null;
  return {
    expectedRevision: String(data.expectedRevision || ''),
    currentRevision: String(data.currentRevision || ''),
    record: (data[kind] as TRecord | undefined) ?? null,
  };
}

/**
 * The backend's sentence for a clash, or null when the error is something else.
 *
 * `CurriculumApiError.message` wraps it in "Curriculum API returned 409 for
 * /path: …", which is diagnostic rather than something to show a user. Screens
 * that assign a tutor should prefer this and fall back to their own copy.
 */
export function tutorConflictMessage(error: unknown): string | null {
  return isTutorConflictError(error) ? error.data.error : null;
}

/**
 * The backend's own sentence and validation details for a refused write, or `fallback`.
 *
 * Same reasoning as `tutorConflictMessage` above, generalised: every handler
 * answers a refusal with `{ error: '...' }` saying what to do about it, and
 * `CurriculumApiError.message` buries that inside "Curriculum API returned 409
 * for /path: …". A dialog should show the sentence, not the diagnostic.
 */
export function curriculumErrorMessage(error: unknown, fallback: string): string {
  return error instanceof CurriculumApiError ? curriculumPayloadErrorMessage(error.data) || fallback : fallback;
}

function curriculumPayloadErrorMessage(data: unknown): string {
  if (!data || typeof data !== 'object') return '';
  const payload = data as Record<string, unknown>;
  const message = typeof payload.error === 'string' && payload.error.trim() ? payload.error : '';
  const details = [payload.validationErrors, payload.errors]
    .flatMap(errors => Array.isArray(errors) ? errors : [])
    .map((item: unknown) => typeof item === 'string'
      ? item
      : item && typeof item === 'object' ? (item as { message?: unknown }).message : undefined)
    .filter((detail): detail is string => typeof detail === 'string' && Boolean(detail.trim()))
    .map(detail => detail.trim())
    .filter(detail => detail !== message.trim());
  const validation = [...new Set(details)].join('; ');
  return message && validation ? `${message} - ${validation}` : message || validation;
}

export interface CurriculumProgramme {
  id: string;
  sourceId: string;
  name: string;
  standard: string;
  level: string;
  status?: 'active' | 'draft' | 'archived' | string;
  isArchived?: boolean;
  isActive?: boolean;
  modules: number;
  freeComponents?: number;
  weeks: number;
  // ksbMapped and every learnerKsb* field below arrive as 0 from the list
  // endpoints and are filled in afterwards by fetchProgrammeKsbStats(). Each one
  // costs a read of the programme's whole authoring tree, so computing them for
  // a list of thirty programmes held up /overview/, /modules/ and /programmes/
  // alike. A card renders without them and gains them a moment later; nothing
  // reads them as "the programme has none".
  ksbMapped: number;
  ksbTotal: number;
  // Learner-consumed KSB progress across the whole programme, from the Component
  // Progress snapshot (`learner_progress_ksbs`). This answers "what have the
  // learners evidenced", which is a different question from ksbMapped/ksbTotal
  // above — that pair is how much of the standard the design maps.
  learnerKsbProgressPercentage?: number;
  learnerKsbConsumedWeight?: number;
  learnerKsbExpectedWeight?: number;
  learnerKsbLearnerCount?: number;
  learnerKsbCodesStarted?: number;
  learnerKsbCodesComplete?: number;
  learnerKsbCodesTotal?: number;
  learners: number;
  cohorts: number;
  groups?: number;
  lastUpdated: string;
  /** When the record was first written. Blank on rows predating the column. */
  createdAt?: string;
  owner: string;
  color: string;
  description: string;
  structureType?: 'scheduled' | 'free' | string;
  ksbProfileSourceId?: string;
  // Off-the-job hours a learner must complete for the whole programme. null means no
  // target has been set, which is different from a target of zero.
  requiredOtjh?: number | null;
  // Where this card sits in the hand-picked order. 0 means it has never been
  // placed, and those read alphabetically ahead of the placed ones.
  displayOrder?: number;
}

export interface CurriculumWeeklySession {
  day: string;
  startTime: string;
  endTime: string;
}

export interface CurriculumModule {
  id: string;
  // Temporary compatibility: moduleId may be a canonical ID or legacy delivery ID.
  // Prefer moduleCatalogueId for canonical authoring identity and deliveryRowId for allocation identity.
  moduleId?: string;
  moduleCatalogueId?: string;
  deliveryRowId?: number | string;
  deliveryModuleId?: string;
  legacyModuleId?: string;
  invalidModuleCatalogueId?: string;
  structureId?: string;
  sourceId: number | string;
  catalogueId?: string;
  relatedCatalogueIds?: string[];
  name: string;
  programmeId?: string;
  programme: string;
  cohortId?: string;
  cohort?: string;
  groupId?: string;
  group?: string;
  learnerRosterMode?: 'inherited' | 'manual' | string;
  teamsSharedSourceModuleId?: string;
  ksbRemapReport?: {
    status?: 'complete' | 'partial' | 'no-source' | string;
    matchedCount?: number;
    unmatched?: Array<{ location?: string; path?: string; code?: string; description?: string; reason?: string }>;
    [key: string]: unknown;
  } | null;
  isProgrammeDeleted?: boolean;
  /**
   * The authored week count — what the week builder holds and what the UI shows
   * as "N weeks". NOT the number of calendar sessions: a group delivering
   * Mon+Thu runs two sessions per week, so `sessionsNumber` is twice this.
   */
  weeks: number;
  weekStructure?: Array<{
    id: string;
    weekNumber: number;
    title: string;
    displayOrder?: number;
    ksbMappings?: CurriculumComponent['ksbMappings'];
    /** Absent from an outline (see `weekStructureOutline`): not an empty week. */
    components?: CurriculumComponent[];
    /** Set on every week of an outline: its `components` were not sent. */
    componentsOmitted?: boolean;
  }>;
  /**
   * Set when `weekStructure` is an outline -- weeks without their components,
   * from a programme detail read with `omitWeekComponents`. Read the module's
   * own structure before editing or pushing it; the server refuses a save
   * that still carries this flag.
   */
  weekStructureOutline?: boolean;
  /**
   * The calendar session count — `weeks` x the group's delivery days per week.
   * This is what the session dates, the tutor clash check and the Teams series
   * are built from. Kept apart from `weeks` because one field meaning both made
   * every edit round-trip multiply the weeks by the delivery days.
   */
  sessionsNumber?: number;
  weeklySchedule?: CurriculumWeeklySession[];
  sessionHolidays?: CurriculumHoliday[];
  /** Teaching weeks, separate from imported content rows. */
  deliveryWeeks?: number;
  weekDays?: string;
  startTime?: string;
  endTime?: string;
  startDate?: string;
  endDate?: string;
  totalOtjh?: number;
  declaredTotalOtjh?: number;
  ksbCount: number;
  ksbProfileSourceId?: string;
  lessons: number;
  quizzes: number;
  /**
   * Learners currently assigned this module -- computed in bulk once per
   * overview build (see `attach_module_assignment_counts` server-side), not
   * per row, so listing many modules costs one pass over learners, not one
   * query per module.
   */
  assignments?: number;
  status: 'published' | 'draft' | 'review' | string;
  authoringStatus?: 'published' | 'draft' | 'review' | string;
  sourceType?: string;
  deliveryStatus?: string;
  /**
   * Always blank: no module builder has ever set it. Omitted from a
   * `?compact=true` list.
   */
  author?: string;
  tutor?: string;
  coach?: string;
  lastUpdated: string;
  /** When the record was first written. Blank on rows predating the column. */
  createdAt?: string;
  color: string;
  /**
   * Optional module artwork — a pasted image URL, or a data: URL read off the
   * picked file, exactly as a free course stores its cover. Empty means the
   * Module Builder card keeps its generic icon.
   */
  coverImage?: string;
  notes: string;
  /**
   * Every authored week and live-session title on the module.
   *
   * Absent from a `?compact=true` list: a full catalogue of these strings is
   * what dominates that response once `weekStructure` is gone, and nothing
   * renders them from a list read. Take the count from `sessionNamesCount`.
   */
  sessionNames?: string[];
  /** How many `sessionNames` the module has. Compact responses only. */
  sessionNamesCount?: number;
  ksbCodes: string[];
  moduleKsbMappings?: CurriculumComponent['ksbMappings'];
}

export interface CurriculumComponent {
  id: string;
  moduleCatalogueId?: string;
  moduleId?: string;
  weekId?: string;
  title: string;
  type: string;
  displayOrder?: number;
  module: string;
  programme: string;
  week: string;
  weekTitle?: string;
  duration: number;
  expectedOtjh?: number;
  reflectionRequired?: boolean;
  /** What the learner reflects on. Only meaningful while reflection is required. */
  reflectionQuestion?: string;
  workplaceEvidenceRequired?: boolean;
  tutorValidationRequired?: boolean;
  /** Coach sign-off. Absent means on - see ModuleComponent.coachValidationRequired. */
  coachValidationRequired?: boolean;
  ksbRefs: string[];
  ksbMappings?: Array<{
    id: string;
    ksbId: string;
    code: string;
    description: string;
    sourceType?: string;
    sourceId?: string;
    type: string;
    classification?: string;
    weight: number;
    weightClass: 'hard' | 'soft' | 'possible' | string;
    weight_class?: 'hard' | 'soft' | 'possible' | string;
  }>;
  status: 'published' | 'draft' | 'review';
  lastEdited: string;
  contentSections: number;
  quizQuestions?: number | null;
  hasResources: boolean;
  description?: string;
  points?: number;
  isDeleted?: boolean;
  deletedAt?: string | null;
  deletedViaParent?: string;
  settings?: Record<string, unknown>;
  /**
   * The settings keys a `fetchCurriculumComponents({ omitContent: true })` read
   * left out of `settings` -- authored long text such as `readingContent` or
   * `transcript`. Absent means `settings` is complete. A row carrying it is
   * for display only: load the full component before showing that text or
   * saving it (the server refuses a save that still carries this).
   */
  settingsOmitted?: string[];
}

/** Where a reusable component currently lives. */
export type LibraryComponentOrigin = 'active' | 'archived' | 'library';

/**
 * A component offered for reuse. Same shape as a normal component plus its
 * provenance, because the parent module may no longer exist: `originModuleTitle`
 * and `originWeekLabel` are stamped on the row at detach time and are the only
 * labels available for detached content.
 */
export interface LibraryComponent extends CurriculumComponent {
  origin: LibraryComponentOrigin;
  /**
   * The authoring type (`live-session`, `reading`, …). Distinct from `type`,
   * which the component endpoints fill with a human label ("Self-study") that
   * several activity types share and which cannot be copied.
   */
  componentType: string;
  originModuleCatalogueId?: string;
  originModuleTitle?: string;
  originWeekId?: string;
  originWeekLabel?: string;
  detachedAt?: string | null;
  copiedFromId?: string;
}

export interface CurriculumKsbActivity {
  activityType: string;
  weight: number;
}

export interface CurriculumKsbEntry {
  id: string;
  code: string;
  rawCode?: string;
  fullCode?: string;
  parentCode?: string;
  parentId?: string | number | null;
  displayOrder?: number;
  title: string;
  description: string;
  type: 'Knowledge' | 'Skill' | 'Behaviour';
  standard: string;
  activities: CurriculumKsbActivity[];
  modules: string[];
  assessmentMethod: string;
  mappedBy: string;
  status: 'mapped' | 'partial' | 'unmapped';
  lastUpdated: string;
}

export interface CurriculumKsbSet {
  frameworkId?: string;
  profileId?: string | number;
  ksbProfileId?: string;
  /** Skills England standard supplying funding/compliance metadata for this profile. */
  standardSourceId?: string;
  programmeId: string;
  programmeIds?: string[];
  cohortIds?: string[];
  groupIds?: string[];
  moduleCatalogueIds?: string[];
  programmeName: string;
  standard: string;
  notes?: string;
  status?: string;
  ksbs: CurriculumKsbEntry[];
}

/** List metadata; definitions are deliberately absent until a source is opened. */
export type CurriculumKsbSetSummary = Omit<CurriculumKsbSet, 'ksbs'> & {
  ksbs?: never;
  ksbCount: number;
  knowledgeCount: number;
  skillCount: number;
  behaviourCount: number;
};

export type CurriculumKsbDefinition = Pick<CurriculumKsbEntry,
  'id' | 'code' | 'rawCode' | 'fullCode' | 'parentCode' | 'parentId' | 'displayOrder' | 'title' | 'description' | 'type'>;

export type CurriculumKsbSourceDefinitions = Omit<CurriculumKsbSet, 'ksbs'> & { ksbs: CurriculumKsbDefinition[] };

export interface CurriculumKsbFramework {
  id: string;
  profileId?: string | number;
  ksbProfileId?: string;
  /** Explicit parent standard; independent from the programmes using the profile. */
  standardSourceId?: string;
  programmeId?: string;
  programmeIds?: string[];
  cohortIds?: string[];
  groupIds?: string[];
  moduleCatalogueIds?: string[];
  name: string;
  standard: string;
  programmeName?: string;
  notes?: string;
  ifateRef: string;
  level: number;
  totalKsbs: number;
  knowledgeCount: number;
  skillCount: number;
  behaviourCount: number;
  modulesCount: number;
  mapped: number;
  status: 'draft' | 'review' | 'published' | 'archived';
  lastModified: string;
  modifiedBy: string;
  version: string;
  programmes: string[];
}

export interface CurriculumStandardKsb {
  id?: string | number;
  code: string;
  type: 'Knowledge' | 'Skill' | 'Behaviour';
  description: string;
}

export interface CurriculumStandard {
  id: string;
  code: string;
  standardRef: string;
  version: string;
  name: string;
  status: string;
  level: string;
  levelValue: string;
  degree: string;
  route: string;
  duration: string;
  minimumHours: string;
  maxFunding: string;
  larsCode: string;
  eqaProvider: string;
  sourceUrl: string;
  approvedForDelivery: string;
  dateUpdated: string;
  lastSynced: string;
  knowledge: number;
  skills: number;
  behaviours: number;
  total: number;
  sampleKsbs?: CurriculumStandardKsb[];
  ksbs?: CurriculumStandardKsb[];
}

export interface CurriculumKsbTraceMapping {
  mapping_id: string;
  mappingId: string;
  programme_id: string;
  programmeId: string;
  programme_name: string;
  programmeName: string;
  module_id: string;
  moduleId: string;
  module_name: string;
  moduleName: string;
  group_id?: string;
  groupId?: string;
  group_name?: string;
  groupName?: string;
  group?: string;
  groups?: string[];
  week_id: string;
  weekId: string;
  week_name: string;
  weekName: string;
  component_id: string;
  componentId: string;
  component_name: string;
  componentName: string;
  component_type: string;
  componentType: string;
  // Expected OTJH of the component this mapping sits on. Week/module-level
  // mappings are not attached to a single component and report 0.
  component_otjh?: number;
  componentOtjh?: number;
  ksb_id: string;
  ksbId: string;
  code: string;
  description: string;
  source_type: string;
  sourceType: string;
  source_id: string;
  sourceId: string;
  source_name?: string;
  sourceName?: string;
  source_label?: string;
  sourceLabel?: string;
  classification: 'main' | 'secondary' | 'possible' | string;
  weight_class?: 'hard' | 'soft' | 'possible' | string;
  weightClass?: 'hard' | 'soft' | 'possible' | string;
  mapping_level?: 'module' | 'week' | 'component' | string;
  mappingLevel?: 'module' | 'week' | 'component' | string;
  weight: number;
}

export interface CurriculumKsbCoverageItem {
  ksb_id: string;
  ksbId: string;
  coverage_key?: string;
  coverageKey?: string;
  code: string;
  title: string;
  description: string;
  ksb_type: 'knowledge' | 'skill' | 'behaviour' | string;
  ksbType: 'knowledge' | 'skill' | 'behaviour' | string;
  source_type: string;
  sourceType: string;
  source_id: string;
  sourceId: string;
  source_name?: string;
  sourceName?: string;
  source_label?: string;
  sourceLabel?: string;
  raw_total_weight: number;
  rawTotalWeight: number;
  coverage_percentage: number;
  coveragePercentage: number;
  progress_bar_percentage: number;
  progressBarPercentage: number;
  occurrence_count: number;
  occurrenceCount: number;
  mapping_count: number;
  mappingCount: number;
  module_count: number;
  moduleCount: number;
  week_count: number;
  weekCount: number;
  component_count: number;
  componentCount: number;
  classification_summary: Record<string, { count: number; weight: number }>;
  classificationSummary: Record<string, { count: number; weight: number }>;
  mappings: CurriculumKsbTraceMapping[];
}

export interface CurriculumKsbCoverageSummaryBucket {
  required: number;
  // KSBs carrying any weight at all, and the weight summed across the bucket.
  // There is no fully/partially-covered verdict: the weight is the fact.
  mapped: number;
  unmapped: number;
  total_weight: number;
}

/** Where a coverage response read its required-KSB set from. */
export interface CurriculumKsbCoverageSource {
  type: string;
  id: string;
  /**
   * 'programme' (the source stored on the programme row), 'module', 'request',
   * 'programme-name' (matched by name), 'mappings-only', or 'all-profiles' when
   * nothing identified a source and every profile's KSBs stood in.
   */
  origin: 'programme' | 'module' | 'request' | 'programme-name' | 'mappings-only' | 'all-profiles' | string;
  required_count: number;
  requiredCount: number;
  source_name?: string;
  sourceName?: string;
  source_label?: string;
  sourceLabel?: string;
}

export interface CurriculumKsbCoverageResponse {
  scope: string;
  identifier: string;
  source?: CurriculumKsbCoverageSource;
  summary: {
    overall: CurriculumKsbCoverageSummaryBucket;
    knowledge: CurriculumKsbCoverageSummaryBucket;
    skills: CurriculumKsbCoverageSummaryBucket;
    behaviours: CurriculumKsbCoverageSummaryBucket;
  };
  items: CurriculumKsbCoverageItem[];
  heatmap: {
    modules: Array<{ module_id: string; moduleId: string; module_name: string; moduleName: string }>;
    rows: Array<{
      ksb_id: string;
      ksbId: string;
      coverage_key?: string;
      coverageKey?: string;
      code: string;
      title: string;
      ksb_type: string;
      ksbType: string;
      source_type?: string;
      sourceType?: string;
      source_id?: string;
      sourceId?: string;
      source_name?: string;
      sourceName?: string;
      source_label?: string;
      sourceLabel?: string;
      total: number;
      modules: Array<{ module_id: string; moduleId: string; module_name: string; moduleName: string; weight: number; mappings: CurriculumKsbTraceMapping[] }>;
    }>;
  };
}

// ---------------------------------------------------------------------------
// Scope achievement — Programme -> Cohort -> Group -> Module -> Week.
//
// Curriculum plans; learners consume. Every level of the hierarchy answers the
// same two questions about itself and nothing else: who is assigned here, and
// what have they actually achieved here. One response shape, one set of rules,
// so a cohort's number and its programme's number mean the same thing.
// ---------------------------------------------------------------------------

export type CurriculumLearnerScope = 'programme' | 'cohort' | 'group' | 'module' | 'week' | 'component';

/** Where a scope sits in the hierarchy, resolved server-side. */
export interface CurriculumScopeLineage {
  scope: CurriculumLearnerScope | string;
  identifier: string;
  /** Whether the scope record itself exists. An empty scope is not a missing one. */
  found: boolean;
  programmeId: string;
  programmeName: string;
  cohortId: string;
  cohortName: string;
  groupId: string;
  groupName: string;
  moduleCatalogueId: string;
  moduleTitle: string;
  weekId: string;
  weekTitle: string;
  componentId: string;
  componentTitle: string;
  /**
   * Which level's roster this scope reports. A module/week/component has none of
   * its own: the learners who meet it are the ones enrolment placed in the group
   * that delivers it.
   */
  placementBasis: 'programme' | 'cohort' | 'group' | string;
}

/** One learner's OTJH inside one scope. */
export interface CurriculumScopeOtjhLearner {
  learnerId: number | string;
  learnerName: string;
  email: string;
  cohort: string;
  group: string;
  /** What the modules of *this learner's group* add up to in this scope. */
  plannedOtjh: number;
  /**
   * Where `plannedOtjh` came from. 'group' is the normal case. 'none' means no
   * module in this scope is delivered to this learner's group. 'scope' means no
   * placement matched any delivering group, so the whole scope stood in.
   */
  plannedBasis?: 'group' | 'none' | 'scope' | string;
  /** Credited: declared hours where a reflection exists, expected hours otherwise. */
  achievedOtjh: number;
  /** The learner's own declared hours only. */
  declaredOtjh: number;
  completedActivityCount: number;
  reflectionCount: number;
  progressPercentage: number;
  /** Whole-programme figures from `Learner.learners`, never a scope subtotal. */
  programmeCompletedHours?: number | null;
  programmeTargetHours?: number | null;
}

export interface CurriculumScopeOtjhAchievement {
  componentCount: number;
  learnerCount: number;
  /**
   * Everything this scope authored, counted once. Not a per-learner figure: a
   * module belongs to one group, so a cohort running two groups holds two module
   * instances and no learner is assigned both.
   */
  authoredTotal: number;
  /** The average of what its learners are each assigned. */
  plannedPerLearner: number;
  /** The sum of what its learners are each assigned — the real denominator. */
  plannedTotal: number;
  /** The headline: hours actually achieved in this scope, across its learners. */
  achievedTotal: number;
  declaredTotal: number;
  creditedFromExpectedTotal: number;
  achievedPerLearnerAverage: number;
  progressPercentage: number;
  completedActivityCount: number;
  reflectionCount: number;
  learners: CurriculumScopeOtjhLearner[];
  sources: Record<string, string>;
}

/** One KSB's achieved weight inside one scope — a row of the achieved heatmap. */
export interface CurriculumScopeKsbAchievementRow {
  code: string;
  title: string;
  /** The standard's wording, shown under the code in the achievement table. */
  description?: string;
  ksbType: string;
  /**
   * 'K' | 'S' | 'B'. `ksbType` is a word whose spelling varies by import
   * ('Skill'/'Skills'/'skill'), so anything grouping or filtering by family
   * uses this instead.
   */
  ksbTypeCode?: 'K' | 'S' | 'B' | string;
  sourceType: string;
  sourceId: string;
  sourceLabel: string;
  /** Authored weight for this KSB across this scope, counted once. */
  plannedWeight: number;
  /**
   * What this scope's learners are between them assigned: the weight authored in
   * each learner's own group, summed. The honest denominator, and not the same
   * as `plannedWeight × learnerCount` once a scope spans several groups.
   */
  expectedWeightTotal: number;
  achievedWeightTotal: number;
  /** Each learner capped at the planned weight. What a percentage is taken of. */
  cappedAchievedWeightTotal: number;
  declaredReflectionWeightTotal: number;
  /** How many learners this KSB is actually authored for in this scope. */
  learnerCount: number;
  learnersAchievedCount: number;
  learnersCompleteCount: number;
  achievementPercentage: number;
  /**
   * 'unmapped' — required by the KSB source but taught nowhere in this scope (a
   * coverage gap). 'unplanned' — a learner consumed it but the scope never
   * authored it at all. Two different facts.
   */
  status: 'complete' | 'in_progress' | 'not_started' | 'unmapped' | 'unplanned' | string;
}

/**
 * One of Knowledge / Skills / Behaviours, rolled up. The per-KSB table answers
 * "how is S4 going"; this answers the question asked before it — how each
 * family is going, and which of them nothing has touched.
 */
export interface CurriculumScopeKsbTypeSummary {
  type: 'knowledge' | 'skill' | 'behaviour' | string;
  letter: 'K' | 'S' | 'B' | string;
  label: string;
  ksbCount: number;
  /** This scope's own KSBs of this family — excludes 'unplanned'. */
  requiredCount: number;
  mappedCount: number;
  /** Required by the KSB source but taught nowhere here: a curriculum gap. */
  unmappedCount: number;
  /** Earned by a learner but authored nowhere here. */
  unplannedCount: number;
  startedCount: number;
  completeCount: number;
  notStartedCount: number;
  /** Required here, and no learner has earned any of it yet. */
  missingCount: number;
  learnersAchievedTotal: number;
  plannedWeightTotal: number;
  expectedWeightTotal: number;
  achievedWeightTotal: number;
  cappedAchievedWeightTotal: number;
  progressPercentage: number;
}

export interface CurriculumScopeKsbAchievement {
  learnerCount: number;
  requiredCount: number;
  ksbCount: number;
  mappedCount: number;
  unmappedCount: number;
  unplannedCount: number;
  startedCount: number;
  completeCount: number;
  notStartedCount: number;
  /** This scope's own KSBs that no learner has earned any of yet. */
  missingCount: number;
  /** Knowledge / Skills / Behaviours, always all three and always in that order. */
  byType: CurriculumScopeKsbTypeSummary[];
  plannedWeightTotal: number;
  expectedWeightTotal: number;
  achievedWeightTotal: number;
  cappedAchievedWeightTotal: number;
  declaredReflectionWeightTotal: number;
  progressPercentage: number;
  learnersWithAchievement: number;
  rows: CurriculumScopeKsbAchievementRow[];
  sources: Record<string, string>;
}

export interface CurriculumScopeStructureCounts {
  moduleCount: number;
  weekCount: number;
  componentCount: number;
  ksbMappingCount: number;
  /** Above one, the scope's authored totals and its per-learner totals differ. */
  groupCount: number;
}

export interface CurriculumProgrammeAssignedLearner {
  id: number | string;
  /** EnrolmentUser primary key used by learner-assignment writes. */
  enrolmentId?: number | string;
  name: string;
  email: string;
  programme: string;
  programmeStatus: string;
  cohort: string;
  group: string;
  /**
   * The stable keys behind `cohort` and `group`. Enrolment captures a placement
   * as free text and curriculum owns the records that text names; these are the
   * records it resolved to, so a renamed cohort or group no longer takes its
   * learners off the rosters that used to find them by comparing names.
   *
   * Empty when the name never resolved to exactly one live record. Readers fall
   * back to the name for those rows, which is what every row did before.
   */
  cohortId?: string;
  groupId?: string;
  lifecycleStatus: string;
  coachName?: string;
  coachEmail?: string;
  completedHours?: number;
  plannedHours?: number;
  targetHours?: number;
  progressHours?: number;
  progressVariance?: string | number | null;
  otjhStatus?: string;
  // Reflection-declared OTJH, reported as its own fields. They do not replace
  // `completedHours` / `plannedHours`, which cover the whole programme.
  reflectionActualOtjh?: number | null;
  reflectionExpectedOtjh?: number | null;
  reflectionCount?: number;
}

export interface CurriculumKsbAchievementFilters {
  cohortId?: string;
  groupId?: string;
  moduleId?: string;
  learnerId?: string;
  componentId?: string;
  ksbType?: string;
  ksbId?: string;
  search?: string;
  dateFrom?: string;
  dateTo?: string;
  page?: number;
  pageSize?: number;
  ksbPage?: number;
  ksbPageSize?: number;
}

export interface CurriculumKsbAchievementItem {
  ksbDefinitionId: string;
  code: string;
  type: string;
  description: string;
  sourceType: string;
  sourceId: string;
  learnerCount: number;
  consumptionCount: number;
  componentCount: number;
  firstConsumedAt?: string | null;
  lastConsumedAt?: string | null;
}

export interface CurriculumKsbConsumption {
  effectiveConsumptionId: string;
  learner: { id: string | number; name: string };
  cohort: { id: string; name: string };
  group: { id: string; name: string };
  module: { id: string; title: string };
  week: { id: string; title: string };
  component: { id: string; title: string; type: string };
  ksb: CurriculumKsbAchievementItem;
  consumedAt: string;
  weight: number;
}

export interface CurriculumKsbAchievementResponse {
  scope: { programmeId: string; cohortId: string | null; groupId: string | null; moduleId: string | null };
  appliedSource: { type: string; id: string; label: string; definitionCount: number };
  summary: { appliedKsbCount: number; consumedKsbCount: number; learnerCount: number; consumptionCount: number };
  items: CurriculumKsbAchievementItem[];
  ksbPagination: { page: number; pageSize: number; total: number; hasNext: boolean };
  consumptionLog: { items: CurriculumKsbConsumption[]; page: number; pageSize: number; total: number; hasNext: boolean };
}

export interface CurriculumKsbAchievementConsumptionResponse {
  ksb: CurriculumKsbAchievementItem | null;
  items: CurriculumKsbConsumption[];
  page: number;
  pageSize: number;
  total: number;
  hasNext: boolean;
}

/** The identifier accepted by curriculum learner-assignment writes. */
export function curriculumLearnerAssignmentId(
  learner: Pick<CurriculumProgrammeAssignedLearner, 'id' | 'enrolmentId'>,
) {
  return String(learner.enrolmentId ?? learner.id);
}

export interface CurriculumLearnerKsbConsumptionItem {
  code: string;
  expectedWeight: number;
  consumedWeight: number;
  cappedConsumedWeight: number;
  // What the learner's reflection declared for the same activity. Supplementary
  // evidence, reported next to `consumedWeight` and never inside it.
  declaredReflectionWeight?: number;
  progressPercentage: number;
  rawProgressPercentage: number;
  status: 'complete' | 'in_progress' | 'not_started' | string;
}

export interface CurriculumLearnerKsbConsumption {
  learnerId: number | string;
  learnerName: string;
  email: string;
  cohort: string;
  group: string;
  consumedWeightTotal: number;
  consumedWeightSource?: string;
  declaredReflectionWeightTotal?: number;
  expectedWeightTotal: number;
  cappedConsumedWeightTotal: number;
  progressPercentage: number;
  ksbs: CurriculumLearnerKsbConsumptionItem[];
}

export interface CurriculumLearnerActivityKsb {
  code: string;
  weight: number;
  weightClass?: string;
  countsTowardAchievement: boolean;
}

export interface CurriculumLearnerActivityReflection {
  submissionId: string | null;
  status: string;
  submittedAt: string;
  dateCompleted: string;
  text: string;
  qualityScore: number | null;
  actualOtjh: number | null;
  actualOtjhSource: string;
  declaredPlannedOtjh: number | null;
  progressLinkStatus: 'linked' | 'unlinked' | string;
  learnerResolution: string;
  submissionLearnerId: string;
  ksbRole: string;
  countsTowardAchievement: false;
}

export interface CurriculumLearnerActivityEvidence {
  evidenceId: string;
  fileName: string;
  status: string;
  scanResult: string;
  sectionRef: string;
  componentId: string;
  contentType: string;
  sizeBytes: number | null;
  uploadedAt: string;
}

/** One Progress activity, keyed on `learner_progress_entries.id`.
 *
 * The single identifier Learner, Coach and Curriculum share, so the same
 * activity can be looked up in each layer instead of matched on titles, emails
 * or enrolment source ids. `ksbSnapshot` is the canonical achieved weight;
 * `declaredReflectionKsbs` is what the reflection claimed for the same activity.
 * They are separate lists on purpose — summing both double-counts the activity.
 */
export interface CurriculumLearnerActivity {
  progressId: number;
  learnerId: number | string | null;
  kind: string;
  componentId: string;
  componentTitle: string;
  componentType: string;
  /** Resolved live against the catalogue, not the label stored on the progress row. */
  module: string;
  /**
   * Whether that module is still in the catalogue. 'deleted' means it was
   * removed; 'unknown' means the component no longer resolves to one at all.
   * Empty for an activity with no progress row behind it.
   */
  moduleStatus?: 'live' | 'deleted' | 'unknown' | string;
  moduleCatalogueId?: string;
  week: string;
  /** Whether the activity belongs to the scope being reported on. */
  scopeStatus?: 'in_scope' | 'out_of_scope' | 'unattributed' | string;
  /**
   * How it was placed there. 'component' — it is in this scope's live content.
   * 'lineage' — that component is gone (deleted or re-authored), but the
   * progress row was stamped with this scope when the learner completed it, so
   * the work is still credited here rather than nowhere.
   */
  scopeBasis?: 'component' | 'lineage' | 'unfiltered' | 'none' | string;
  /**
   * Why an in-scope, completed activity still does not count. Currently only
   * 'repeat_completion': the same component was completed before, and its hours
   * and KSB weight were earned once.
   */
  exclusionReason?: 'repeat_completion' | string;
  submittedAt: string;
  progressStatus: 'achieved' | 'failed' | 'incomplete' | string;
  passed: boolean | null;
  countsTowardAchievement: boolean | null;
  expectedOtjh: number | null;
  expectedOtjhSource: string;
  actualOtjh: number | null;
  actualOtjhSource: string;
  ksbSnapshot: CurriculumLearnerActivityKsb[];
  achievedKsbWeightTotal: number;
  declaredReflectionKsbs: CurriculumLearnerActivityKsb[];
  declaredReflectionKsbWeightTotal: number;
  reflection: CurriculumLearnerActivityReflection | null;
  evidence: CurriculumLearnerActivityEvidence[];
  evidenceCount: number;
}

/** Authored activity for one assigned learner, including not-started rows. */
export interface CurriculumLearnerActivityPlanItem {
  lectureDate?: string | null;
  logState?: CurriculumActivityLogState;
  canChangeStatus?: boolean;
  activityPlanId: string;
  learnerId: number | string;
  learnerName: string;
  email: string;
  cohort: string;
  group: string;
  componentId: string;
  componentTitle: string;
  componentType: string;
  moduleCatalogueId: string;
  module: string;
  weekId: string;
  week: string;
  displayOrder: number;
  expectedOtjh: number | null;
  points: number;
  calculatedStatus: 'completed' | 'in_progress' | 'not_started' | string;
  displayStatus: 'completed' | 'in_progress' | 'not_started' | string;
  statusSource: 'calculated' | string;
  progressId: number | null;
  progressIds: Array<number | string>;
  progressStatus: string;
  submittedAt: string;
  countsTowardAchievement: boolean | null;
  source: 'curriculum_component' | string;
}

export interface CurriculumActivityLogState {
  revision: number;
  snapshotHash: string;
  displayStatus: string | null;
  calculatedStatus: string;
  statusSource: string;
  actualOtjh: number | null;
  acceptedOtjh: number | null;
  unallocatedAcceptedRecords?: number;
  hoursSource: string;
  startedAt: string | null;
  endedAt: string | null;
  progressId: number | null;
  accepted: boolean | null;
  multipleRecordedAllocations?: boolean;
  timeEntryMode?: 'input' | 'timestamp';
  activityDate?: string | null;
  attendanceMode?: 'attended' | 'recording' | null;
  lectureDate?: string | null;
}

export type CurriculumActivityCompletionTime = { durationSeconds: number; attendanceMode?: 'attended' | 'recording' } & (
  { timeEntryMode: 'input'; activityDate: string }
  | { timeEntryMode: 'timestamp'; startedAt: string }
);

export interface CurriculumActivityStatusHistory {
  revision: number; actor_id: number; reason: string; created_at: string; displayStatus: string;
}

export async function fetchCurriculumActivityStatusHistory(moduleId: string, learnerId: string, componentId: string, signal?: AbortSignal): Promise<{ history: CurriculumActivityStatusHistory[] }> {
  const path = `/curriculum/modules/${encodeURIComponent(moduleId)}/learners/${encodeURIComponent(learnerId)}/activities/${encodeURIComponent(componentId)}/status/`;
  const response = await coachFetch(`${API_BASE_URL}${path}`, { signal });
  const data = await response.json();
  if (!response.ok) throw new CurriculumApiError(data?.error || 'Unable to load status history.', response.status, path, data);
  return data;
}

export async function saveCurriculumActivityStatus(moduleId: string, learnerId: string, componentId: string, body: {
  displayStatus: 'completed' | 'not_started'; expectedRevision: number; snapshotHash: string;
  durationSeconds?: number; startedAt?: string; timeEntryMode?: 'input' | 'timestamp'; activityDate?: string;
  attendanceMode?: 'attended' | 'recording';
}): Promise<{ state: CurriculumActivityLogState }> {
  const path = `/curriculum/modules/${encodeURIComponent(moduleId)}/learners/${encodeURIComponent(learnerId)}/activities/${encodeURIComponent(componentId)}/status/`;
  mutationEpoch += 1;
  applyMutationInvalidation(path);
  const response = await coachFetch(`${API_BASE_URL}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok) throw new CurriculumApiError(data?.error || 'Unable to save activity status.', response.status, path, data);
  clearCurriculumGetCache();
  return data;
}

// Learner placements are owned by the enrolment team. Curriculum reads this
// roster to show who is arriving in a cohort/group and never writes to it.
export interface CurriculumScopeLearnerRosterResponse {
  scope: CurriculumLearnerScope | string;
  identifier: string;
  lineage: CurriculumScopeLineage;
  placementBasis: CurriculumScopeLineage['placementBasis'];
  source: string;
  editable: boolean;
  assignedLearnerCount: number;
  assignedLearners: CurriculumProgrammeAssignedLearner[];
  countsByCohort: Record<string, number>;
  countsByGroup: Record<string, number>;
}

/** Retained name: the programme roster is the same read at the top scope. */
export type CurriculumProgrammeLearnerRosterResponse = CurriculumScopeLearnerRosterResponse;

export interface CurriculumScopeLearnerKsbImpactResponse {
  activityLogOtjh?: {
    learners: Array<{ learnerId: number | string; plannedOtjh: number; achievedOtjh: number | null; unallocatedAcceptedRecords?: number }>;
    achievedTotal: number | null; plannedTotal: number; source: string;
    unallocatedAcceptedRecords?: number;
  };
  scope: CurriculumLearnerScope | string;
  identifier: string;
  lineage: CurriculumScopeLineage;
  placementBasis: CurriculumScopeLineage['placementBasis'];
  structure: CurriculumScopeStructureCounts;
  assignedLearnerCount: number;
  assignedLearners: CurriculumProgrammeAssignedLearner[];
  /** Retained name; `coverage` is the scope-neutral alias for the same payload. */
  programmeCoverage: CurriculumKsbCoverageResponse;
  coverage: CurriculumKsbCoverageResponse;
  /** Hours actually achieved in this scope — the OTJH roll-up. */
  otjhAchievement: CurriculumScopeOtjhAchievement;
  /** KSB weight actually earned in this scope — the achieved heatmap. */
  ksbAchievement: CurriculumScopeKsbAchievement;
  learnerKsbConsumption: CurriculumLearnerKsbConsumption[];
  learnerActivities?: CurriculumLearnerActivity[];
  learnerActivityCount?: number;
  learnerActivityPlan?: CurriculumLearnerActivityPlanItem[];
  learnerActivityPlanCount?: number;
  ksbAchievementPolicy?: {
    achievedWeightSource: string;
    reflectionKsbRole: string;
    reflectionKsbCountsTowardAchievedWeight: boolean;
    reflectionLearnerResolution: string;
    expectedOtjhSource: string;
    actualOtjhSource: string;
    scopeAttribution?: string;
    /** Whether activity with no component reference counted. Programme only. */
    unattributedActivityCounts?: boolean;
    expectedWeightBasis?: string;
    plannedOtjhBasis?: string;
  };
  consumptionSources: {
    // Achieved delivery only. Failed activity and unresolved graded attempts are
    // reported separately below so they stay visible without being summed as
    // achievement — see backend learner_api/progress_rules.py.
    progress: Array<Record<string, unknown>>;
    // Resolved through `progress_entry_id`. Every row carries
    // `countsTowardAchievement: false`: a reflection is evidence about an
    // activity, not a second source of achieved KSB weight.
    learningReflectionSubmissions: Array<Record<string, unknown>>;
    excludedProgress?: Array<Record<string, unknown>>;
    excludedLearningReflectionSubmissions?: Array<Record<string, unknown>>;
    // Reflections on a component in this programme with no progress link, so no
    // learner can be resolved without guessing. Reported as a visible gap.
    unlinkedLearningReflectionSubmissions?: Array<Record<string, unknown>>;
    // Achieved activity belonging to a different scope in the same programme.
    // Reported so the gap between this scope's total and the learner's programme
    // total is inspectable rather than an unexplained shortfall.
    outOfScopeProgress?: Array<Record<string, unknown>>;
    evidenceByProgressEntry?: Record<string, CurriculumLearnerActivityEvidence[]>;
  };
}

/** Retained name: the programme impact is the same read at the top scope. */
export type CurriculumProgrammeLearnerKsbImpactResponse = CurriculumScopeLearnerKsbImpactResponse;

export interface CurriculumReadinessIssue {
  severity: 'warning' | 'error' | string;
  code: string;
  raw_weight: number;
  rawWeight: number;
  message: string;
}

export interface CurriculumReadinessResponse {
  ready: boolean;
  canSaveDraft: boolean;
  issues: CurriculumReadinessIssue[];
  summary?: CurriculumKsbCoverageResponse['summary'];
}

export type CurriculumKsbMappingInput = {
  id?: string;
  ksbId?: string;
  code: string;
  description?: string;
  sourceType: 'standard' | 'framework' | string;
  sourceId: string;
  classification: 'main' | 'secondary' | 'possible' | string;
  type?: 'main' | 'secondary' | 'possible' | string;
  weight: number;
  weightClass: 'hard' | 'soft' | 'possible' | string;
  weight_class?: 'hard' | 'soft' | 'possible' | string;
};

export interface CurriculumComponentKsbMappingsResponse {
  componentId: string;
  count: number;
  results: CurriculumKsbTraceMapping[];
}

export type CurriculumKsbItemInput = {
  id?: string | number;
  type: 'K' | 'S' | 'B';
  code: string;
  parentCode?: string;
  title: string;
  description?: string;
  displayOrder?: number;
};

export type CurriculumKsbFrameworkInput = {
  name?: string;
  ksbProfileId?: string;
  standardSourceId?: string;
  programmeId?: string;
  programmeIds?: string[];
  cohortIds?: string[];
  groupIds?: string[];
  moduleCatalogueIds?: string[];
  programmeName?: string;
  programme?: string;
  description?: string;
  notes?: string;
  isActive?: boolean;
  ksbItems?: CurriculumKsbItemInput[];
  knowledgeCodes?: string[];
  skillCodes?: string[];
  behaviourCodes?: string[];
};

export interface CurriculumCohort {
  id: string;
  name: string;
  programme: string;
  programmeId: string;
  startDate: string;
  /** End of the practical period. practicalEndDate is the same date under the name the apprenticeship model uses. */
  endDate: string;
  practicalEndDate?: string;
  /** End Point Assessment window in whole months. null means none recorded, which is not the same as 0. */
  epaMonths?: number | null;
  /** The date the apprenticeship ends: apprenticeshipEndOverride when set, otherwise practical end date plus epaMonths. */
  apprenticeshipEndDate?: string;
  /** Manually authored apprenticeship end date. Empty when the date is calculated. */
  apprenticeshipEndOverride?: string;
  durationMonths?: string | number;
  /**
   * The duration rule alone (start plus duration, less a day), before the
   * holiday extension the practical end date already includes. Compare the two
   * to tell whether holidays moved this cohort's dates.
   */
  baseEndDate?: string;
  status: 'active' | 'planned' | 'completed' | 'archived' | string;
  learners: number;
  groups: string[];
  modules: string[];
  sessions: number;
  color: string;
  progress: number;
  attendance: number;
  /** The holidays that actually apply: every one in the cohort's period, less excludedHolidayIds. */
  holidayIds?: Array<string | number>;
  /** The holidays a human unticked in the cohort drawer, from the same in-period list. */
  excludedHolidayIds?: Array<string | number>;
  /** When the record was first written. Blank on rows predating the column. */
  createdAt?: string;
  updatedAt?: string;
}

export interface CurriculumGroup {
  id: string;
  name: string;
  cohortId: string;
  cohort: string;
  programme: string;
  programmeId?: string;
  learners: number;
  coach: string;
  tutor: string;
  startDate: string;
  endDate: string;
  status: string;
  weekDays?: string;
  startTime?: string;
  endTime?: string;
  schedule: string;
  color?: string;
  mode: string;
  moduleIds?: string[];
  modules: string[];
  sessions: number;
  /** When the record was first written. Blank on rows predating the column. */
  createdAt?: string;
  updatedAt?: string;
}

export interface CurriculumSession {
  timeZone?: string;
  id: string;
  trainingPlanId: number | string;
  programmeId?: string;
  programmeSourceId?: string;
  cohortId?: string;
  groupId?: string;
  moduleId?: string;
  moduleCatalogueId?: string;
  weekId?: string;
  deliveryRowId?: number | string;
  // Temporary compatibility: prefer moduleCatalogueId when present.
  deliveryModuleId?: string;
  legacyModuleId?: string;
  invalidModuleCatalogueId?: string;
  componentId?: string;
  /** The live-session component's own title, when the caller matched one. Display only. */
  componentTitle?: string;
  title: string;
  type: string;
  date: string;
  day: string;
  startTime: string;
  endTime: string;
  tutor: string;
  group: string;
  cohort: string;
  programme: string;
  venue: string;
  module: string;
  week: number;
  skippedHolidays?: string[];
  scheduleWarnings?: string[];
  status: 'scheduled' | 'completed' | 'cancelled' | 'pending' | string;
  ksbCodes: string[];
}

/**
 * Teams meeting state for one module, from `curriculum.live_sessions`.
 *
 * The meeting is never stored on the module row — it lives in its own table,
 * keyed by module. Listing pages read this instead of pulling every module's
 * full week structure just to inspect a live-session component's settings.
 */
export interface CurriculumTeamsMeetingSummary {
  meetingType?: string;
  calendarManagement?: 'external' | 'lms';
  timeZone?: string;
  moduleCatalogueId: string;
  liveSessionId: string;
  status: string;
  moduleTitle?: string;
  joinUrl: string;
  webLink?: string;
  meetingOptionsUrl?: string;
  eventId?: string;
  onlineMeetingId?: string;
  organizerEmail: string;
  presenters?: string[];
  /** People who run the meeting alongside the organizer: they can manage the
   *  recording, the lobby and the meeting options without owning the calendar. */
  coOrganizers?: string[];
  attendees?: string[];
  repeatPattern: string;
  startDateTime: string;
  durationMinutes: number;
  occurrenceCount: number;
  upcomingCount: number;
  syncedCount: number;
  nextOccurrence: string;
  updatedAt: string;
  /** Only present when asked for: the dates Teams currently holds, in order. */
  occurrenceDates?: string[];
  /**
   * Only present when `occurrenceDates` was asked for: the backend's own
   * verdict on whether the tracked Teams occurrences still match the module's
   * *current* plan — computed against the authoring rows, not the (possibly
   * cached) session list this page also reads. `'unverified'` means the
   * comparison could not be made safely (a Neon read failed, the module row is
   * gone, nothing is tracked yet) and must never be shown as agreement.
   */
  syncState?: 'in-sync' | 'out-of-sync' | 'no-sessions' | 'unverified';
  syncReasons?: string[];
  /** Saved / pending / failed for the meeting options; read with readMicrosoftUpdate. */
  microsoftUpdate?: unknown;
  expectedOccurrenceCount?: number;
  differingOccurrenceCount?: number;
  missingFromTeams?: string[];
  extraInTeams?: string[];
}

/** One scheduled instance of a live-session series. `status` is authored by the
 *  Graph artifact-sync service — never derived from a date on the client. */
export interface CurriculumLiveSessionOccurrence {
  occurrenceId: string;
  liveSessionId: string;
  moduleCatalogueId: string;
  sessionNumber: number;
  status: string; // 'scheduled' | 'completed' | 'cancelled' | ...
  scheduledStart: string;
  scheduledEnd: string;
  actualStart: string;
  actualEnd: string;
  participantCount: number;
  joinUrl: string;
  attendanceReportId: string;
  artifactsSyncedAt: string;
}

export interface CurriculumLiveSessionSeries {
  liveSessionId: string;
  moduleCatalogueId: string;
  moduleTitle?: string;
  status: string;
  joinUrl: string;
  provider?: string;
}

export interface CurriculumLiveSessionOccurrencesResponse {
  series: CurriculumLiveSessionSeries[];
  occurrences: CurriculumLiveSessionOccurrence[];
}

/** A completed occurrence's captured artifacts (transcript / recording). */
export interface LiveSessionArtifact {
  id: string;
  occurrence_id: string;
  artifact_type: string; // 'transcript' | 'recording'
  graph_artifact_id?: string;
  content_url?: string;
  created_datetime?: string;
  end_datetime?: string;
  metadata?: Record<string, unknown>;
}

/** One attendee row for a completed occurrence. */
export interface LiveSessionAttendance {
  id: string;
  occurrence_id: string;
  email?: string;
  display_name?: string;
  role?: string;
  total_attendance_seconds?: number;
  intervals?: Array<Record<string, unknown>>;
  raw_data?: Record<string, unknown>;
  expected?: boolean;
  attended?: boolean;
  join_count?: number;
  attendance_report_id?: string;
  attendance_report_start?: string;
  attendance_report_end?: string;
}

/** An occurrence enriched with its attendance + artifacts (from the per-series
 *  artifacts endpoint), loaded lazily when a completed row is expanded. */
export interface LiveSessionArtifactOccurrence {
  id: string;
  live_session_id: string;
  session_number?: number;
  status?: string;
  scheduled_start?: string;
  scheduled_end?: string;
  actual_start?: string;
  actual_end?: string;
  participant_count?: number;
  join_url?: string;
  attendance?: LiveSessionAttendance[];
  artifacts?: LiveSessionArtifact[];
}

export interface LiveSessionArtifactsResponse {
  series: Record<string, unknown>;
  occurrences: LiveSessionArtifactOccurrence[];
}

/**
 * Occurrence statuses that no longer stand for a meeting on the calendar.
 *
 * Shrinking a series marks its leftover rows cancelled rather than deleting
 * them, so those rows are the record of what Teams *used* to hold. Counted as
 * current they shift a session-by-session comparison by a slot, which is what
 * made "Teams still holds ..." survive every press of Update Teams calendar.
 * The same set the backend's sync verdict has always excluded.
 */
const OFF_CALENDAR_OCCURRENCE_STATUSES = new Set([
  'cancelled', 'canceled', 'declined', 'deleted', 'removed',
  // "Not in plan": a session a new plan left on Teams. Not cancelled -- its
  // Teams slot stands until somebody cancels it -- but not a planned session.
  'superseded',
]);

/** The tracked occurrences that still stand for a meeting on the calendar. */
export function onCalendarOccurrences<T extends { status?: string }>(
  occurrences: T[] | undefined,
): T[] {
  return (occurrences || []).filter(item => (
    !OFF_CALENDAR_OCCURRENCE_STATUSES.has(String(item?.status || '').trim().toLowerCase())
  ));
}

/**
 * One holiday on the curriculum calendar, from either of its two sources.
 *
 * `/curriculum/holidays/` serves both in this one shape:
 *
 * - `source: 'gov.uk'` — an England and Wales bank holiday, mirrored from
 *   https://www.gov.uk/bank-holidays.json. A single day, so `startDate` and
 *   `endDate` are equal. Read-only: it changes when GOV.UK changes it.
 * - `source: 'authored'` — one of this college's own closure periods, added on
 *   the Holidays page. These usually span a *range*, which is why every holiday
 *   carries a start and an end.
 *
 * Everything downstream measures the period and does not care which it is: a day
 * is closed or it is not.
 */
export interface CurriculumHoliday {
  id: string | number;
  label: string;
  startDate: string;
  endDate: string;
  type?: string;
  color?: string;
  /** Which of the two calendars this came from. Only an authored one can be edited. */
  source?: 'gov.uk' | 'authored';
  /** GOV.UK rows only, and only ever 'Substitute day' — the holiday moved off a weekend. */
  notes?: string;
  /** GOV.UK rows only: whether the day is marked as a flag-flying one. */
  bunting?: boolean;
}

/** The fields the Holidays page writes. Only an authored holiday accepts them. */
export type CurriculumHolidayInput = Partial<Pick<CurriculumHoliday, 'label' | 'startDate' | 'endDate' | 'type' | 'color'>>;

/** One holiday as a recorded GOV.UK check reports it. */
export interface EnglandHolidayChange {
  id: string;
  title: string;
  date: string;
  notes?: string;
  bunting?: boolean;
  /** Moves only: the date this holiday used to fall on. */
  previousDate?: string;
  /** Changes only: the title, note and bunting flag as they were before. */
  previous?: { title?: string; notes?: string; bunting?: boolean };
}

/**
 * One recorded check against GOV.UK — the answer to "what changed on the site?".
 *
 * A check that found nothing is recorded too, and matters: without it the last
 * real change reads as the last time anyone looked.
 */
export interface EnglandHolidaySync {
  id: string;
  checkedAt: string;
  /** 'auto' (the background refresh), 'manual' (the button), 'command' (the CLI). */
  source: string;
  status: 'ok' | 'error' | string;
  feedCount: number;
  added: EnglandHolidayChange[];
  changed: EnglandHolidayChange[];
  /** Holidays GOV.UK moved to a different date. */
  moved: EnglandHolidayChange[];
  /** Holidays GOV.UK withdrew. They are removed here too. */
  withdrawn: EnglandHolidayChange[];
  /** Dates that fell off the back of GOV.UK's rolling window. Kept, not removed. */
  agedOut: EnglandHolidayChange[];
  /** Why a failed check failed. */
  message: string;
}

/** How current the GOV.UK mirror is, and when it refreshes itself next. */
export interface EnglandHolidaySyncStatus {
  autoSync: boolean;
  intervalHours: number;
  lastCheckedAt: string;
  lastSuccessAt: string;
  nextCheckDueAt: string;
  source: string;
}

// GOV.UK's published bank holidays, mirrored into the curriculum schema. Read
// only: these are not authored here, unlike CurriculumHoliday above.
export interface EnglandHoliday {
  id: string;
  division: string;
  title: string;
  date: string;
  // Only ever 'Substitute day' — the holiday moved because the real date fell
  // on a weekend.
  notes: string;
  bunting: boolean;
  fetchedAt?: string;
}

export interface CurriculumCohortAuthoringDetail {
  cohortId: string;
  cohortName: string;
  programmeId: string;
  programmeName: string;
  startDate: string;
  endDate: string;
  practicalEndDate?: string;
  epaMonths?: number | null;
  apprenticeshipEndDate?: string;
  apprenticeshipEndOverride?: string;
  durationMonths: number;
  color: string;
  status: string;
  moduleCatalogueIds?: string[];
  trainingPlanIds?: string[];
  groupIds: string[];
  moduleNames: string[];
  holidayIds: string[];
  selectedHolidays: CurriculumHoliday[];
  holidaysInRange: CurriculumHoliday[];
  excludedHolidayIds: string[];
  holidaySummary: {
    global?: number;
    inRange?: number;
    selected?: number;
  };
  notes?: string;
  sourceType?: string;
  sourceId?: string;
  updatedAt?: string;
}

export interface CurriculumStaffProfile {
  id?: string | number;
  role?: 'coach' | 'tutor' | string;
  name?: string;
  email?: string;
  phone?: string;
  jobTitle?: string;
  status?: string;
  specialisms?: string[];
  assignedModuleIds?: string[];
  assignedGroupIds?: string[];
  assignedModules?: Array<Pick<CurriculumModule, 'id' | 'moduleId' | 'moduleCatalogueId' | 'deliveryRowId' | 'name' | 'programmeId' | 'programme' | 'cohortId' | 'cohort' | 'groupId' | 'group' | 'startDate' | 'endDate' | 'status'>>;
  inProgressModules?: Array<Pick<CurriculumModule, 'id' | 'moduleId' | 'moduleCatalogueId' | 'deliveryRowId' | 'name' | 'programmeId' | 'programme' | 'cohortId' | 'cohort' | 'groupId' | 'group' | 'startDate' | 'endDate' | 'status'>>;
  groupCount?: number;
  moduleCount?: number;
  inProgressCount?: number;
  notes?: string;
  [key: string]: unknown;
}

export type CurriculumAuditAction =
  | 'created'
  | 'updated'
  | 'archived'
  | 'restored'
  | 'deleted'
  | 'moved'
  | 'reordered'
  /** Already existed when history started; its real author is unknown. */
  | 'recorded'
  | 'file_uploaded'
  | 'file_replaced'
  | 'file_removed'
  /** The system worked this out from something else that moved. */
  | 'recalculated'
  | 'imported';

/**
 * How a save arrived. Never an action: an auto-saved edit is still an edit.
 * These are the stored machine-readable values; `sourceLabel` is the words.
 */
export type CurriculumAuditSource =
  | 'manual' | 'auto-save' | 'module-builder' | 'tree-save' | 'import' | 'upload'
  | 'duplicate' | 'wizard' | 'recalculation' | 'scheduled-job' | 'system' | 'api' | '';

/**
 * Who acted. `system` is the LMS on its own account - a cascade, a
 * recalculation - and is never a claim about intent, only the honest reading of
 * a write no person directly made.
 */
export type CurriculumAuditActorType = 'user' | 'system' | 'integration' | 'job' | '';

/** One field that moved, ready to render: `label` is what a reader should see. */
export interface CurriculumAuditChange {
  field: string;
  label: string;
  before: string;
  after: string;
  truncated?: boolean;
}

export interface CurriculumAuditEvent {
  id: string;
  /**
   * Where the event comes from: the revision log saw the save happen, or it
   * was recovered from a timestamp the record keeps, from before its record
   * type's revision history began. Recovered events never carry a before and
   * after, and name an author only where the record itself recorded one.
   */
  provenance?: 'revision' | 'timestamps';
  provenanceLabel?: string;
  /** ISO stamp of the write itself, not a display date. */
  at: string;
  action: CurriculumAuditAction;
  /** "Edited", "Archived" - what the row leads with, instead of the event name. */
  actionLabel: string;
  entity: 'programme' | 'module' | 'week' | 'component' | 'cohort' | 'group' | string;
  entityLabel: string;
  /** Workspace owning the record, or the page workspace that made the save. */
  workspace?: string;
  workspaceLabel?: string;
  entityId: string;
  revisionNo: number;
  title: string;
  /** The record's ancestry as one line, from the names it was saved with. */
  context: string;
  /** Those same ancestors as ids and names, as they were at the time. */
  parents: Record<string, string>;
  moduleCatalogueId: string;
  parentId: string;
  versionLabel: string;
  contentStatus: string;
  /** The signed-in account that made the change. Empty when none was recorded. */
  actorName: string;
  actorEmail: string;
  actorType: CurriculumAuditActorType;
  /** "Person", "System", "Scheduled job" - `actorType` in words. */
  actorTypeLabel: string;
  /**
   * Who caused a SYSTEM action. Empty for a person's own edit - they are the
   * actor, not the trigger - and empty for a scheduled run, where there is no
   * person and naming one would be a fabrication.
   */
  triggeredByEmail: string;
  triggeredByName: string;
  /** `auto-save`, `tree-save`, ... - what KIND of save, never the action itself. */
  source: CurriculumAuditSource;
  /** The same thing in words, for display. */
  sourceLabel: string;
  /**
   * Small descriptive context about the write: the file an upload attached, the
   * batch an import belonged to. Allowlisted server-side, so it never carries
   * content, credentials or a signed URL.
   */
  metadata: Record<string, string | number | boolean | null>;
  /**
   * The write handler's code (`component-delete`). It is NOT a person; the
   * person is `actorName`.
   */
  reason: string;
  /**
   * `reason` as a phrase that completes "... as part of" ("a cleanup sweep").
   * Empty when the handler code has no mapped meaning - the row then says only
   * what it knows, rather than dressing the code up as an explanation.
   */
  causeLabel?: string;
  /** The recorded page's name. Empty when the write had no page. */
  pageLabel?: string;
  /** Only the fields that actually moved. Empty for a create. */
  changes: CurriculumAuditChange[];
  /**
   * The stored record, carried inline only for a create (where it is the
   * "after") and a delete (where it is the "before", and the only copy left).
   */
  snapshot: Record<string, unknown> | null;
  viaParent?: string;
  href: string;
  /**
   * Only set by the per-person activity read: true when the backend could
   * place this save on a page the person was recorded as having open. The
   * change feed never sets it, because it has no pages to place against.
   */
  placed?: boolean;
}

/** Someone who changed something in the window, for the actor filter. */
export interface CurriculumAuditActor {
  email: string;
  name: string;
  changes: number;
}

export interface CurriculumAuditTrail {
  workspaces?: { value: string; label: string }[];
  /** Every workspace this response can speak for, whichever reading covers it. */
  changeWorkspaces?: string[];
  /** Covered by the revision log: who saved, and what each field held before and after. */
  revisionWorkspaces?: string[];
  /** Covered only by the records' own timestamps: what moved and when, rarely who. */
  derivedWorkspaces?: string[];
  /** Nothing in this database records what changed there. */
  uncoveredWorkspaces?: string[];
  /** How many events in the whole window came from each reading. */
  provenanceCounts?: { revision: number; timestamps: number };
  /** Whether history from before the revision log was recovered into this feed. */
  recoveredHistory?: boolean;
  /** The recovered history could not be read this time; the log is shown alone. */
  recoveryFailed?: boolean;
  /** Where each record type's revision history begins; null when it has none yet. */
  revisionStartedAt?: Record<string, string | null>;
  generatedAt: string;
  windowDays: number;
  since: string;
  limit: number;
  /** Everything that matched the filters, across every page. */
  total: number;
  /** Which page this is, 1-based. Clamped to the last page when asked for more. */
  page: number;
  pageSize: number;
  /** How many pages the filtered window holds. Never below 1. */
  pages: number;
  truncated: boolean;
  /** Counted over the whole window, not the page, so paging does not move them. */
  actionCounts: Record<CurriculumAuditAction, number>;
  entityCounts: Record<string, number>;
  /** Entities whose table could not be read, so the page can name the gap. */
  unreadable: string[];
  /**
   * True when the trail is read from the revision log, which records the
   * signed-in account on every write. False when it falls back to reading record
   * timestamps, which cannot name anyone — the page says which it is looking at
   * rather than showing an empty column.
   */
  authorRecorded: boolean;
  /** `revisions` (who + before/after) or `timestamps` (what + when only). */
  source: 'revisions' | 'timestamps';
  /**
   * True once the audit metadata lives in its own columns rather than being
   * parsed out of the legacy `reason` string. The page only offers the source
   * and actor-type filters when this is true, because before the Phase 2 SQL
   * has run there is no column to filter on.
   */
  structuredMetadata: boolean;
  /** The source and actor-type values the server recognises, for the filters. */
  sources: CurriculumAuditSource[];
  actorTypes: CurriculumAuditActorType[];
  /**
   * The record types this workspace actually audits, named by the server.
   * Named there rather than listed here because the answer differs per
   * workspace and grows as each one is wired up — a list held in the browser
   * was the curriculum's ten types, so the system-wide trail offered a filter
   * that could not name a learner, a coaching meeting or an employer.
   */
  entityTypes?: { value: string; label: string }[];
  actors: CurriculumAuditActor[];
  events: CurriculumAuditEvent[];
}

/**
 * Who used Curriculum Studio, as opposed to what they changed.
 *
 * Three sources sit behind these shapes and the page has to be able to tell
 * them apart, which is why each is flagged separately rather than merged into
 * one silent total:
 *
 * * `visitsRecorded` - `curriculum.activity_events` exists, so pages opened and
 *   read actions are being recorded. False means the table has not been created
 *   yet and the reading half of the trail is simply not there. It is never
 *   retroactive: nothing was recorded before the table existed.
 * * `changesRecorded` - the revision log exists, so saves can be named.
 * * `signInsRecorded` - `login."Login_audit"` could be read. Sign-ins are
 *   account-wide, not curriculum-only, and are labelled as such.
 */
export interface CurriculumActivityPerson {
  email: string;
  name: string;
  /** Their role at the time the activity was recorded. Empty if unknown. */
  role: string;
  firstSeen: string;
  lastSeen: string;
  /** Distinct browser sittings in the window. */
  visits: number;
  pageViews: number;
  readActions: number;
  /** How many DIFFERENT curriculum pages they opened, not how many times. */
  pagesOpened: number;
  /** Saves recorded against them in the revision log. */
  changes: number;
  signIns: number;
  lastPageKey: string;
  lastPageLabel: string;
  /** The workspace of the last page they opened. */
  lastWorkspace: string;
  /**
   * Which workspaces they were in over the window, most recent first.
   * Empty when page opens are not being recorded: it is a fact about visits,
   * so with no visits recorded there is nothing to say rather than nowhere
   * to have been.
   */
  workspaces: { workspace: string; label: string; hits: number; lastAt: string }[];
}

export interface CurriculumActivityPeople {
  generatedAt: string;
  windowDays: number;
  since: string;
  /** The workspace this response was scoped to; empty means all of them. */
  workspace: string;
  /** Every workspace the server recognises, for the filter. */
  workspaces: { value: string; label: string }[];
  /**
   * True once each row carries its own workspace column. False means the
   * workspace is derived from the stored path on read -- the same answer,
   * reached without an index.
   */
  workspaceRecorded: boolean;
  /**
   * The workspaces whose saves the Changes feed can actually name. The
   * reading half covers every workspace; the writing half covers the ones
   * wired into a revision log, and the page says which rather than letting an
   * empty feed read as "nobody changed anything".
   */
  changeWorkspaces: string[];
  visitsRecorded: boolean;
  changesRecorded: boolean;
  signInsRecorded: boolean;
  truncated: boolean;
  /** How many rows this response carries; `totals.people` counts everyone who matched. */
  shown: number;
  /** The server-side cap that `truncated` reports against. */
  limit: number;
  /** Which page this is, 1-based. Clamped to the last page when asked for more. */
  page: number;
  pageSize: number;
  /** How many pages the filtered window holds. Never below 1. */
  pages: number;
  /** Everyone who matched the filters, across every page. */
  total: number;
  /**
   * The roles held by the people in this window, for the Role filter. Read
   * from the whole window rather than from the page on screen, which can only
   * name the roles of the fifty people it carries.
   */
  roles: string[];
  /** Whether anyone in this window has no role recorded. */
  rolesIncludeBlank: boolean;
  totals: {
    people: number;
    visits: number;
    pageViews: number;
    readActions: number;
    changes: number;
    signIns: number;
  };
  people: CurriculumActivityPerson[];
}

/** One thing done on a page that was not a save. */
export interface CurriculumActivityAction {
  id: string;
  at: string;
  kind: string;
  /** "Searched", "Exported" - `kind` in words, resolved server-side. */
  label: string;
  targetType: string;
  targetId: string;
  targetLabel: string;
  /** Short descriptive context: the term searched, the filter changed. */
  detail: Record<string, string>;
}

/** One page opened during a visit, and everything that happened on it. */
export interface CurriculumActivityPage {
  id: string;
  at: string;
  /** When the last thing on this page happened, not when it was closed. */
  endedAt: string;
  path: string;
  pageKey: string;
  pageLabel: string;
  targetType: string;
  targetId: string;
  targetLabel: string;
  /** How long it was open. Null when the tab closed before it could be sent. */
  durationMs: number | null;
  actions: CurriculumActivityAction[];
  /** Recorded saves that happened while this page was open. */
  changes: CurriculumAuditEvent[];
}

/** One sitting in one browser tab. */
export interface CurriculumActivityVisit {
  id: string;
  startedAt: string;
  endedAt: string;
  ip: string;
  userAgent: string;
  pages: CurriculumActivityPage[];
  pageCount: number;
  actionCount: number;
  changeCount: number;
}

export interface CurriculumActivitySignIn {
  at: string;
  ip: string;
  userAgent: string;
}

export interface CurriculumPersonActivity {
  accountEventsRecorded?: boolean;
  accountEventsTruncated?: boolean;
  accountEvents?: { id: number; at: string; event: 'login' | 'logout'; succeeded: boolean }[];
  generatedAt: string;
  windowDays: number;
  since: string;
  visitsRecorded: boolean;
  changesRecorded: boolean;
  signInsRecorded: boolean;
  person: {
    email: string;
    name: string;
    role: string;
    firstSeen: string;
    lastSeen: string;
  };
  counts: {
    visits: number;
    pageViews: number;
    readActions: number;
    changes: number;
    /**
     * How many of their changes could be placed on a page they were recorded
     * as having open. The rest are still listed - a save whose navigation was
     * never recorded is still a save.
     */
    changesOnAPage: number;
    signIns: number;
  };
  visits: CurriculumActivityVisit[];
  signIns: CurriculumActivitySignIn[];
  changes: CurriculumAuditEvent[];
}

export type CurriculumVersionEntityType = 'module' | 'week' | 'component';
export type CurriculumRevisionAction = 'created' | 'updated' | 'archived' | 'restored';

/** One field that moved between two revisions. Long values are cut server-side. */
export interface CurriculumFieldChange {
  field: string;
  from: string;
  to: string;
  truncated?: boolean;
}

export interface CurriculumRevision {
  id: number;
  entityType: CurriculumVersionEntityType | string;
  entityId: string;
  revisionNo: number;
  action: CurriculumRevisionAction | string;
  moduleCatalogueId: string;
  parentId: string;
  title: string;
  versionLabel: string;
  contentStatus: string;
  changedFields: CurriculumFieldChange[];
  /** The signed-in account that saved. Empty for a write with no session. */
  actorName: string;
  actorEmail: string;
  /** Only set on an archive: the write handler's reason code. */
  reason: string;
  at: string;
}

/** A revision an author named — a component moving to 0.2, a module published. */
export interface CurriculumNamedVersion {
  id: number;
  versionLabel: string;
  revisionNo: number;
  contentStatus: string;
  note: string;
  actorName: string;
  at: string;
}

export interface CurriculumVersionEntity {
  entityType: CurriculumVersionEntityType | string;
  entityId: string;
  title: string;
  moduleCatalogueId: string;
  parentId: string;
  versionLabel: string;
  contentStatus: string;
  action: string;
  revisions: number;
  namedVersions: number;
  lastChangeAt: string;
  firstChangeAt: string;
  lastActorName: string;
}

export interface CurriculumVersionIndex {
  /** False when the history tables have not been created; `reason` says so. */
  available: boolean;
  reason?: string;
  generatedAt?: string;
  totalRevisions: number;
  totalNamedVersions: number;
  count: number;
  entities: CurriculumVersionEntity[];
}

export interface CurriculumRecordHistory {
  available: boolean;
  reason?: string;
  entityType: string;
  entityId: string;
  title: string;
  moduleCatalogueId?: string;
  versions: CurriculumNamedVersion[];
  revisions: CurriculumRevision[];
  /** Populated only when a revision number was asked for: its stored content. */
  snapshot: { revisionNo: number; at: string; content: Record<string, unknown> } | null;
}

export interface CurriculumOverview {
  schema: string;
  stats: {
    programmes: number;
    activeProgrammes: number;
    cohorts: number;
    groups: number;
    modules: number;
    ksbFrameworks: number;
    sessions: number;
  };
  programmes: CurriculumProgramme[];
  modules: CurriculumModule[];
  ksbFrameworks: CurriculumKsbFramework[];
  ksbSets: CurriculumKsbSet[];
  cohorts: CurriculumCohort[];
  groups: CurriculumGroup[];
  sessions: CurriculumSession[];
  components?: CurriculumComponent[];
  holidays?: CurriculumHoliday[];
  cohortAuthoringDetails?: CurriculumCohortAuthoringDetail[];
  tutors?: CurriculumStaffProfile[];
  coaches?: CurriculumStaffProfile[];
}

export interface FreeProgrammeComponent {
  id: string;
  moduleId?: string;
  weekId?: string;
  displayOrder: number;
  type: string;
  title: string;
  description: string;
  expectedOtjh: number;
  points: number;
  reflectionRequired: boolean;
  workplaceEvidenceRequired: boolean;
  tutorValidationRequired: boolean;
  settings?: Record<string, unknown>;
}

export interface FreeProgrammeModule {
  id: string;
  courseId?: string;
  weekId?: string;
  weekNumber?: number;
  weekTitle?: string;
  programmeId?: string;
  programmeName?: string;
  courseName?: string;
  title: string;
  description: string;
  coverImageUrl?: string;
  weekBuilderWeekIds?: string[];
  displayOrder: number;
  componentCount: number;
  totalOtjh: number;
  components: FreeProgrammeComponent[];
}

type ExpandedCurriculumGroup = Omit<CurriculumGroup, 'modules'> & { modules: CurriculumModule[] };

export interface CurriculumProgrammeDetail {
  schema: string;
  programme: CurriculumProgramme;
  cohorts: Array<Omit<CurriculumCohort, 'groups'> & { groups: ExpandedCurriculumGroup[] }>;
  flat: {
    cohorts: CurriculumCohort[];
    groups: CurriculumGroup[];
    groupIds: string[];
    modules: CurriculumModule[];
    sessions: CurriculumSession[];
    components?: CurriculumComponent[];
    weekTemplates?: unknown[];
  };
}

/** The programme with its cohorts and groups -- the detail document up to the modules. */
export interface CurriculumProgrammeDetailShell {
  schema: string;
  programme: CurriculumProgramme;
  flat: Pick<CurriculumProgrammeDetail['flat'], 'cohorts' | 'groups' | 'groupIds' | 'weekTemplates'>;
}

/** The programme's modules (each weekStructure an outline) and planned sessions. */
export interface CurriculumProgrammeDetailModules {
  schema: string;
  flat: Pick<CurriculumProgrammeDetail['flat'], 'modules' | 'sessions'>;
}

export interface CurriculumSessionPlanPreview {
  sessions: Array<{ sessionNumber: number; date: string; day: string; startTime?: string; endTime?: string; durationMinutes?: number; skippedHolidays: string[] }>;
  skippedHolidays: string[];
  finalEndDate: string;
  warnings: string[];
}

/** One holiday's share of a cohort's end-date extension. */
export interface CurriculumHolidayExtension {
  label: string;
  /** The first and last day of this holiday that fell inside the cohort's period. */
  startDate: string;
  endDate: string;
  days: number;
}

export interface CurriculumCohortEndDatePreview {
  endDate: string;
  practicalEndDate?: string;
  /** What the duration rule gives plus the holiday extension, regardless of any practical end date sent in. */
  calculatedEndDate?: string;
  /** The duration rule alone, before holidays. The holiday picker's window. */
  baseEndDate?: string;
  /** Days the selected holidays take out of the cohort's base period. */
  holidayExtensionDays?: number;
  /** Which holiday took which days, for an editor that names them. */
  holidayExtensions?: CurriculumHolidayExtension[];
  /** The contracted duration in months, unchanged by holidays. */
  durationMonths?: number;
  /** How long the cohort actually runs once the holidays are in. */
  effectiveDurationMonths?: number;
  /** True when the practical end date sent in differs from the calculated one. */
  practicalEndIsManual?: boolean;
  epaMonths?: number | null;
  apprenticeshipEndDate?: string;
  apprenticeshipEndOverride?: string;
  autoCalculated: boolean;
  rule: string;
  epaRule?: string;
  apprenticeshipRule?: string;
  warnings: string[];
}

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/curriculum_api';

interface CurriculumCollection<T> {
  schema: string;
  count: number;
  results: T[];
}

// Multi-tier cache: hot cache for recent requests, cold cache for longer TTLs
interface CacheEntry<T> {
  value: T;
  expiresAt: number;
  hitCount: number;
  createdAt: number;
  entityType?: string; // 'programme', 'cohort', 'module', etc. for targeted invalidation
  entityId?: string;   // the programme/cohort/module ID for invalidation scoping
}

// Cache tier configuration: TTL and size limits by data category
const CACHE_TIERS = {
  // Reference data: frameworks, standards, component types (rarely change)
  metadata: { ttlMs: 300_000, maxEntries: 200 }, // 5 minutes
  // Programme trees: structure with cohorts/groups/modules (changes with save)
  programmeTree: { ttlMs: 60_000, maxEntries: 50 }, // 1 minute (per programme)
  // Module structures: week/component hierarchies (large, changes with authoring)
  moduleStructure: { ttlMs: 120_000, maxEntries: 100 }, // 2 minutes
  // KSB coverage and impact analysis (computed, medium volatility)
  ksbAnalysis: { ttlMs: 90_000, maxEntries: 50 }, // 1.5 minutes
  // Dynamic lists: tutors, coaches, sessions (may change frequently)
  dynamic: { ttlMs: 45_000, maxEntries: 100 }, // 45 seconds
  // Default tier for other GET requests
  default: { ttlMs: 30_000, maxEntries: 200 }, // 30 seconds
} as const;

// Cache statistics for observability
interface CacheStats {
  hits: number;
  misses: number;
  evictions: number;
  totalSize: number;
  entries: number;
}

class MultiTierCache {
  private caches = new Map<keyof typeof CACHE_TIERS, Map<string, CacheEntry<unknown>>>();
  private inFlightRequests = new Map<string, Promise<unknown>>();
  private inFlightEpochs = new Map<string, number>();
  private stats = new Map<keyof typeof CACHE_TIERS, CacheStats>();

  constructor() {
    for (const tier of Object.keys(CACHE_TIERS) as Array<keyof typeof CACHE_TIERS>) {
      this.caches.set(tier, new Map());
      this.stats.set(tier, { hits: 0, misses: 0, evictions: 0, totalSize: 0, entries: 0 });
    }
  }

  private getTier(path: string): keyof typeof CACHE_TIERS {
    if (path.includes('/standards/') || path.includes('/frameworks/') || path.includes('/component-types')) {
      return 'metadata';
    }
    if (path.includes('/programmes/') && path.includes('/detail/')) {
      return 'programmeTree';
    }
    if (path.includes('/modules/') && !path.includes('/ksb-') && !path.includes('/coverage')) {
      return 'moduleStructure';
    }
    if (path.includes('/ksb-') || path.includes('/coverage') || path.includes('/impact')) {
      return 'ksbAnalysis';
    }
    if (path.includes('/tutors') || path.includes('/coaches') || path.includes('/holidays') || path.includes('/sessions')) {
      return 'dynamic';
    }
    return 'default';
  }

  private estimateSize(value: unknown): number {
    if (typeof value === 'string') return value.length * 2;
    if (typeof value === 'number') return 8;
    if (typeof value === 'boolean') return 1;
    if (value === null) return 0;
    if (Array.isArray(value)) {
      return 48 + value.reduce((sum, v) => sum + this.estimateSize(v), 0);
    }
    if (typeof value === 'object') {
      try {
        return JSON.stringify(value).length * 2;
      } catch {
        return 1024; // Conservative estimate for unserializable objects
      }
    }
    return 0;
  }

  get<T>(path: string, options?: { skipCache?: boolean }): CacheEntry<T> | null {
    if (options?.skipCache) return null;
    const tier = this.getTier(path);
    const cache = this.caches.get(tier);
    const entry = cache?.get(path) as CacheEntry<T> | undefined;

    if (!entry) {
      const stats = this.stats.get(tier);
      if (stats) stats.misses += 1;
      return null;
    }

    if (entry.expiresAt > Date.now()) {
      entry.hitCount += 1;
      const stats = this.stats.get(tier);
      if (stats) stats.hits += 1;
      return entry;
    }

    // Expired entry
    cache?.delete(path);
    const stats = this.stats.get(tier);
    if (stats) {
      stats.evictions += 1;
      stats.misses += 1;
    }
    return null;
  }

  set<T>(path: string, value: T, entityType?: string, entityId?: string): void {
    const tier = this.getTier(path);
    const cache = this.caches.get(tier);
    if (!cache) return;

    const tierConfig = CACHE_TIERS[tier];
    const size = this.estimateSize(value);

    // Evict LRU if cache full
    if (cache.size >= tierConfig.maxEntries) {
      let oldest: [string, CacheEntry<unknown>] | null = null;
      for (const [key, entry] of cache) {
        if (!oldest || entry.createdAt < oldest[1].createdAt) {
          oldest = [key, entry];
        }
      }
      if (oldest) {
        cache.delete(oldest[0]);
        const stats = this.stats.get(tier);
        if (stats) stats.evictions += 1;
      }
    }

    const entry: CacheEntry<T> = {
      value,
      expiresAt: Date.now() + tierConfig.ttlMs,
      hitCount: 0,
      createdAt: Date.now(),
      entityType,
      entityId,
    };

    cache.set(path, entry as CacheEntry<unknown>);
    const stats = this.stats.get(tier);
    if (stats) {
      stats.entries = cache.size;
      stats.totalSize += size;
    }
  }

  // Invalidate cache entries by entity (e.g., save programme A → invalidate only programme A's tree)
  invalidateByEntity(entityType: string, entityId?: string): number {
    let count = 0;
    for (const cache of this.caches.values()) {
      for (const [key, entry] of cache) {
        if (entry.entityType === entityType && (!entityId || entry.entityId === entityId)) {
          cache.delete(key);
          count += 1;
        }
      }
    }
    return count;
  }

  // Invalidate cache entries matching a pattern
  invalidateByPattern(pattern: string | RegExp): number {
    let count = 0;
    for (const cache of this.caches.values()) {
      for (const key of cache.keys()) {
        const matches = typeof pattern === 'string' ? key.includes(pattern) : pattern.test(key);
        if (matches) {
          cache.delete(key);
          count += 1;
        }
      }
    }
    return count;
  }

  clear(): void {
    for (const cache of this.caches.values()) {
      cache.clear();
    }
    this.inFlightRequests.clear();
    this.inFlightEpochs.clear();
    for (const stats of this.stats.values()) {
      stats.hits = 0;
      stats.misses = 0;
      stats.evictions = 0;
      stats.entries = 0;
      stats.totalSize = 0;
    }
  }

  getInFlight(key: string): Promise<unknown> | undefined {
    return this.inFlightRequests.get(key);
  }

  // The mutation epoch a still-running GET was started in. A skipCache caller
  // uses this to tell "already in flight, but started before my write landed"
  // (must not be reused) from "in flight right now, after the write" (safe to
  // share).
  getInFlightEpoch(key: string): number | undefined {
    return this.inFlightEpochs.get(key);
  }

  setInFlight(key: string, promise: Promise<unknown>, epoch: number): void {
    this.inFlightRequests.set(key, promise);
    this.inFlightEpochs.set(key, epoch);
  }

  clearInFlight(key: string): void {
    this.inFlightRequests.delete(key);
    this.inFlightEpochs.delete(key);
  }

  getStats() {
    const result: Record<string, CacheStats> = {};
    for (const [tier, stats] of this.stats) {
      result[tier] = { ...stats };
    }
    return result;
  }
}

const multiTierCache = new MultiTierCache();

// Combines several abort signals into one. Prefers the native implementation and
// falls back to a manual relay for older browsers.
function anySignal(signals: AbortSignal[]): AbortSignal {
  const nativeAny = (AbortSignal as unknown as { any?: (list: AbortSignal[]) => AbortSignal }).any;
  if (typeof nativeAny === 'function') return nativeAny.call(AbortSignal, signals);
  const controller = new AbortController();
  const forward = (signal: AbortSignal) => {
    if (signal.aborted) {
      controller.abort(signal.reason);
      return;
    }
    signal.addEventListener('abort', () => controller.abort(signal.reason), { once: true });
  };
  signals.forEach(forward);
  return controller.signal;
}

// Incremented on every mutation. GETs record the epoch they started in so a
// skipCache caller can tell whether an in-flight request already reflects the
// most recent write (see fetchJson).
let mutationEpoch = 0;

// Shares an in-flight GET between concurrent callers. GETs are intentionally
// not tied to component cleanup signals: React StrictMode can unmount/remount
// immediately in development, and aborting those requests fills DevTools with
// noisy "(cancelled)" rows even though the next mount needs the same data.
/**
 * The `/curriculum/preview/*` endpoints answer a question about what the form is
 * currently holding -- where these session dates land, which tutors are free in
 * this slot -- and write nothing. They are POSTs only because the question does
 * not fit in a URL: the unsaved holidays, delivery days and dates go in a body.
 *
 * So they get a read's treatment, keyed on the question rather than on the path:
 * the same body asked twice is one request, and the answer stands for a short
 * while. Their URL is identical every time, which is why the multi-tier cache
 * above -- keyed on path alone -- cannot hold them.
 */
const inFlightReads = new Map<string, Promise<unknown>>();
const completedReads = new Map<string, { value: unknown; expiresAt: number }>();
const READ_POST_TTL_MS = 30_000;
const READ_POST_MAX_ENTRIES = 60;

function isReadOnlyPost(method: string, path: string): boolean {
  return method === 'POST' && (
    path.startsWith('/curriculum/preview/')
    || path === '/curriculum/migrated-review-templates/preview/'
  );
}

export function clearCurriculumGetCache() {
  multiTierCache.clear();
  clearPreviewReads();
}

/**
 * Throw away the preview answers.
 *
 * These are the only cached reads not held by `multiTierCache`, so every
 * invalidation path has to say so twice or one of them keeps serving pre-write
 * answers. The one that matters is tutor availability: it exists to stop a save
 * being refused for a clash, and an answer cached from before somebody else took
 * the slot tells the reader the tutor is free right up until the 409 says
 * otherwise.
 */
function clearPreviewReads(): void {
  completedReads.clear();
  inFlightReads.clear();
}

export function invalidateCurriculumCacheByEntity(entityType: string, entityId?: string): number {
  return multiTierCache.invalidateByEntity(entityType, entityId);
}

export function getCurriculumCacheStats() {
  return multiTierCache.getStats();
}

/**
 * A cached, de-duplicated GET for callers that hold their own response types.
 *
 * The module authoring layer had its own bare `fetch` for the structure
 * endpoint, so its reads shared nothing: a workspace and a drawer open on the
 * same module -- and each of them mounted twice under StrictMode -- each opened
 * a separate request for identical bytes, and the slowest were still in flight
 * when their 15s timeout aborted them. Going through `fetchJson` puts them on
 * the one in-flight promise and the `moduleStructure` cache tier.
 */
export function fetchCurriculumJson<T>(path: string, init?: CurriculumRequestInit): Promise<T> {
  return fetchJson<T>(path, init);
}

// Drops the cache entries a write to `path` makes wrong. Called both for this
// tab's own mutations and for a mutation another tab broadcast, so the two tabs
// throw away exactly the same entries.
function applyMutationInvalidation(path: string): void {
  // Every branch below drops these too. A preview is computed from the same
  // rows a write just changed -- where these sessions land, which tutors are
  // free -- and there is no path pattern that separates the ones a given write
  // spoils from the ones it does not. They cost one small POST to rebuild and
  // live 30s, so throwing the lot away is both correct and cheap.
  clearPreviewReads();
  // For mutations, invalidate cache selectively based on the endpoint
  if (path.includes('/programmes/tree/')) {
    // Tree save: invalidate all programme trees and related data
    multiTierCache.invalidateByPattern(/\/overview\//);
    multiTierCache.invalidateByPattern(/\/programmes\/.*\/detail\//);
    multiTierCache.invalidateByEntity('programme');
    multiTierCache.invalidateByEntity('cohort');
    multiTierCache.invalidateByEntity('group');
    multiTierCache.invalidateByEntity('module');
  } else if (path.includes('/programmes/')) {
    multiTierCache.invalidateByPattern(/\/overview\//);
    multiTierCache.invalidateByEntity('programme');
    multiTierCache.invalidateByEntity('cohort');
    multiTierCache.invalidateByEntity('group');
    multiTierCache.invalidateByEntity('module');
  } else if (path.includes('/cohorts/') || path.includes('/groups/') || path.includes('/modules/')) {
    // The overview payload carries programmes, cohorts, groups AND modules in
    // one document, and a programme's detail tree carries the same structure.
    // Neither is tagged with an entity type, so an entity-scoped invalidation
    // left both in place: creating a group refreshed the page you were on (that
    // reload asks for fresh data explicitly) but the next page you opened read
    // the pre-write overview out of cache and showed no new group until a full
    // browser refresh threw the cache away.
    multiTierCache.invalidateByPattern(/\/overview\//);
    multiTierCache.invalidateByPattern(/\/programmes\/.*\/detail\//);
    multiTierCache.invalidateByEntity('cohort');
    multiTierCache.invalidateByEntity('group');
    multiTierCache.invalidateByEntity('module');
  } else if (path.includes('/ksb-')) {
    multiTierCache.invalidateByEntity('ksb');
  } else {
    // Fallback: clear all caches for unknown mutations
    multiTierCache.clear();
  }
}

/** Retained for existing hook imports; remote curriculum tracking is disabled. */
export const UNKNOWN_WRITE_PATH = '*';

/**
 * Remote refresh is disabled application-wide by product request. Keep the
 * subscription interface for existing screens without polling, timers or
 * cross-tab listeners. Local saves still invalidate their own caches below.
 */
export function subscribeCurriculumRemoteWrites(_listener: (path: string) => void): () => void {
  return () => {};
}

async function fetchJson<T>(path: string, init?: CurriculumRequestInit): Promise<T> {
  const method = (init?.method || 'GET').toUpperCase();
  // A preview read, answered before the mutation branch below can treat it as a
  // write. It used to fall through to that branch's `else`, so every one of them
  // cleared the whole cache and bumped the mutation epoch -- and the module
  // drawer fires two of them (session plan, tutor availability) on a 300ms
  // debounce as the weeks or the start date are typed. Each keystroke therefore
  // threw away the overview, the tutors, the coaches and the holidays, and the
  // page spent the next few seconds fetching back data that had not changed.
  if (isReadOnlyPost(method, path)) {
    const key = `${path}|${typeof init?.body === 'string' ? init.body : ''}`;
    const cached = completedReads.get(key);
    if (cached && !init?.skipCache && !init?.revalidate) {
      if (cached.expiresAt > Date.now()) {
        return settleWithCallerAbort(Promise.resolve(cached.value as T), init?.signal);
      }
      completedReads.delete(key);
    }
    const existingRead = inFlightReads.get(key) as Promise<T> | undefined;
    // The epoch this question was asked in. Clearing the cache cannot reach a
    // request that is already on its way, so a preview in flight when a write
    // lands would otherwise come back afterwards and install a pre-write answer
    // as though it were current -- the same trap the GET path tags for.
    const askedAt = mutationEpoch;
    const readPending = existingRead || fetchJsonUncached<T>(
      path,
      init?.signal ? { ...init, signal: undefined } : init,
    ).then(value => {
      if (askedAt !== mutationEpoch) return value;
      if (completedReads.size >= READ_POST_MAX_ENTRIES) {
        const oldest = completedReads.keys().next();
        if (!oldest.done) completedReads.delete(oldest.value);
      }
      completedReads.set(key, { value, expiresAt: Date.now() + READ_POST_TTL_MS });
      return value;
    });
    if (!existingRead) {
      inFlightReads.set(key, readPending as Promise<unknown>);
      const clearRead = () => {
        if (inFlightReads.get(key) === (readPending as Promise<unknown>)) inFlightReads.delete(key);
      };
      readPending.then(clearRead, clearRead);
    }
    return settleWithCallerAbort(readPending, init?.signal);
  }
  if (method !== 'GET') {
    // Any GET started before this point predates the write, so skipCache callers
    // must not reuse it.
    mutationEpoch += 1;
    applyMutationInvalidation(path);
    return fetchJsonUncached<T>(path, init);
  }

  // GET: check multi-tier cache. `revalidate` skips this read like `skipCache`
  // does, but unlike `skipCache` it still stores the response below -- the point
  // of a revalidation is to refresh the entry, not to stop using it.
  if (!init?.skipCache && !init?.revalidate) {
    const cached = multiTierCache.get<T>(path, init);
    if (cached) {
      return settleWithCallerAbort(Promise.resolve(cached.value), init?.signal);
    }
  }

  // Check in-flight deduplication. skipCache means "do not read a stale cached
  // value", not "do not share a request that is already going to the network
  // right now": a fresh in-flight GET satisfies the caller's freshness need just
  // as well as a new one. Several independent hooks refresh the same endpoints
  // concurrently after a save (tutors/coaches/programme detail), and giving each
  // its own request produced 5-6 duplicate calls per endpoint.
  //
  // The one case skipCache must still bypass is an in-flight request that was
  // started *before* this caller's mutation landed, since that response predates
  // the write. Requests are therefore tagged with the epoch they began in, and a
  // skipCache caller only joins an in-flight GET from the current epoch.
  const sharedInit = init?.signal ? { ...init, signal: undefined } : init;
  const inFlight = multiTierCache.getInFlight(path) as Promise<T> | undefined;
  const inFlightIsFresh = inFlight !== undefined
    && multiTierCache.getInFlightEpoch(path) === mutationEpoch
    // A queued read every caller has left is dropped unsent (see
    // fetchJsonUncached), so a newcomer starts its own instead of joining it.
    && !readWillBeDropped(inFlightInterest.get(inFlight));
  const existing = inFlightIsFresh ? inFlight : undefined;
  const askedAt = mutationEpoch;
  // Only a read that can be dropped needs its callers counted; every other GET
  // is shared exactly as before.
  const interest = existing
    ? inFlightInterest.get(existing)
    : (isProgrammeStatsRead(method, path) ? newReadInterest() : undefined);
  const uncachedInit = interest
    ? { ...sharedInit, abandonSignal: interest.controller.signal, onDispatch: () => { interest.sent = true; } }
    : sharedInit;
  const pending = existing || fetchJsonUncached<T>(path, uncachedInit).then(value => {
    if (!init?.skipCache && askedAt === mutationEpoch) {
      // Tag cache entries with entity type for targeted invalidation
      let entityType: string | undefined;
      let entityId: string | undefined;
      if (path.includes('/programmes/') && path.includes('/detail/')) {
        entityType = 'programme';
        const match = path.match(/\/programmes\/([^/]+)\/detail/);
        entityId = match ? decodeURIComponent(match[1]) : undefined;
      } else if (path.includes('/programmes/')) {
        entityType = 'programme';
      } else if (path.includes('/cohorts/')) {
        entityType = 'cohort';
      } else if (path.includes('/groups/') || path.includes('/delivery-scope/')) {
        // Scope is a cohort/group list; the same writes must expire it.
        entityType = 'group';
      } else if (path.includes('/modules/')) {
        entityType = 'module';
      } else if (path.includes('/ksb-') || path.includes('/coverage') || path.includes('/impact')) {
        entityType = 'ksb';
      }
      multiTierCache.set(path, value, entityType, entityId);
    }
    return value;
  });

  // Register even skipCache requests so concurrent refreshes of the same endpoint
  // collapse onto one network call. The cache *write* above still respects
  // skipCache, so nothing stale is stored.
  if (!existing) {
    multiTierCache.setInFlight(path, pending as Promise<unknown>, mutationEpoch);
    if (interest) inFlightInterest.set(pending as Promise<unknown>, interest);
    const clearInFlight = () => {
      inFlightInterest.delete(pending as Promise<unknown>);
      if (multiTierCache.getInFlight(path) === (pending as Promise<unknown>)) {
        multiTierCache.clearInFlight(path);
      }
    };
    pending.then(clearInFlight, clearInFlight);
  }
  joinReadInterest(interest, init?.signal);

  return settleWithCallerAbort(pending, init?.signal);
}

/**
 * Who is still waiting on a shared programme-statistics GET. A caller without
 * a signal can never leave, so it pins the request; each caller with one is
 * counted until it aborts. When the count reaches zero with nothing pinning
 * it, `controller` aborts -- the cue fetchJsonUncached reads to drop a request
 * still queued. `sent` is set once it is on the wire, after which it is never
 * dropped and a newcomer may join it.
 */
interface ReadInterest {
  controller: AbortController;
  waiting: number;
  pinned: boolean;
  sent: boolean;
}

const inFlightInterest = new WeakMap<Promise<unknown>, ReadInterest>();

function newReadInterest(): ReadInterest {
  return { controller: new AbortController(), waiting: 0, pinned: false, sent: false };
}

function readWillBeDropped(interest: ReadInterest | undefined): boolean {
  return Boolean(interest && interest.controller.signal.aborted && !interest.sent);
}

function isProgrammeStatsRead(method: string, path: string): boolean {
  return method === 'GET' && /\/programmes\/[^/]+\/ksb-stats\//.test(path);
}

function joinReadInterest(interest: ReadInterest | undefined, signal?: AbortSignal): void {
  if (!interest) return;
  if (!signal) {
    interest.pinned = true;
    return;
  }
  const leave = () => {
    interest.waiting -= 1;
    if (interest.waiting <= 0 && !interest.pinned) interest.controller.abort();
  };
  interest.waiting += 1;
  if (signal.aborted) leave();
  else signal.addEventListener('abort', leave, { once: true });
}

/**
 * How many further attempts a failed request is worth, and the wait before the
 * first of them. Two is sized for the thing this exists for -- a worker being
 * replaced under a deploy, unavailable for a moment and then not -- and stops
 * well short of turning a backend that is genuinely down into a burst of load.
 */
const RETRY_ATTEMPTS = 2;
const RETRY_BASE_DELAY_MS = 300;

/**
 * Whether this request can be sent a second time without changing the outcome.
 *
 * A retry is only ever correct when the server cannot tell one delivery from
 * two. Reads qualify by definition. Writes do not, and not for a theoretical
 * reason: a 502 from the proxy is returned just as readily *after* the write
 * committed as before it, so a retried POST creates the record twice and a
 * retried PATCH re-applies an edit over whatever landed in between. Making
 * those safe needs an idempotency key the backend honours, which is a contract
 * between the two sides rather than something this file can decide alone.
 *
 * The preview POSTs are the exception, and only because they are reads wearing
 * a POST so the question fits in a body.
 */
function isRetryableRequest(method: string, path: string): boolean {
  return method === 'GET' || method === 'HEAD' || isReadOnlyPost(method, path);
}

function retryDelay(attempt: number): Promise<void> {
  // Jittered, so a page that lost six parallel reads to one restart does not
  // send all six back at the same instant.
  const wait = RETRY_BASE_DELAY_MS * (2 ** attempt) + Math.floor(Math.random() * 100);
  return new Promise(resolve => { setTimeout(resolve, wait); });
}

/**
 * One network call, with a retry for the failures that are worth one.
 *
 * Without this a single 502 -- which is what a deploy looks like from a browser
 * -- blanked whichever page happened to be reading at that second, and the only
 * cure was the manual refresh this whole area exists to stop people needing.
 * `isRetryableError` decides what counts: 408/429/5xx and network failures yes,
 * every 4xx and any caller abort no.
 */
// Serialize expensive programme statistics across all consumers in this tab.
// This runs below GET deduplication, so identical callers share one queued read.
// The timeout starts only when a request reaches the front of the queue.
let programmeStatsTail: Promise<unknown> = Promise.resolve();

function fetchJsonUncached<T>(path: string, init?: CurriculumRequestInit): Promise<T> {
  if (isProgrammeStatsRead((init?.method || 'GET').toUpperCase(), path)) {
    // A page that queued a card's numbers and was then left would otherwise
    // still send them, one after another, ahead of the next page's own: the
    // Archive waited behind the Programmes list's leftovers this way. Dropped
    // only while still queued; a request already sent finishes and warms the
    // server cache for whoever asks next.
    const pending = programmeStatsTail.then(() => {
      if (init?.abandonSignal?.aborted) return Promise.reject(abortError());
      init?.onDispatch?.();
      return fetchJsonWithRetries<T>(path, init);
    });
    programmeStatsTail = pending.then(() => undefined, () => undefined);
    return pending;
  }
  return fetchJsonWithRetries<T>(path, init);
}

async function fetchJsonWithRetries<T>(path: string, init?: CurriculumRequestInit): Promise<T> {
  const method = (init?.method || 'GET').toUpperCase();
  if (!isRetryableRequest(method, path)) return fetchJsonOnce<T>(path, init);
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await fetchJsonOnce<T>(path, init);
    } catch (error) {
      // A caller's own abort never reaches here -- `fetchJson` strips the signal
      // before this point and settles the caller separately -- and a timeout
      // arrives as its own message that `isRetryableError` declines, so a slow
      // endpoint cannot have its budget spent three times over.
      if (attempt >= RETRY_ATTEMPTS || !isRetryableError(error)) throw error;
      await retryDelay(attempt);
    }
  }
}

async function fetchJsonOnce<T>(path: string, init?: CurriculumRequestInit): Promise<T> {
  const timeoutController = init?.timeoutMs ? new AbortController() : null;
  const timeout = timeoutController && init?.timeoutMs
    ? window.setTimeout(() => timeoutController.abort(), init.timeoutMs)
    : null;
  // `revalidate` is destructured out with the other non-fetch options: it is a
  // client-cache directive only and must not reach the backend, or it would
  // force the same rebuild `skipCache` does.
  const { timeoutMs: _timeoutMs, signal: callerSignal, skipCache: _skipCache, revalidate: _revalidate, abandonSignal: _abandonSignal, onDispatch: _onDispatch, ...fetchInit } = init || {};
  const signal = callerSignal && timeoutController
    ? anySignal([callerSignal, timeoutController.signal])
    : callerSignal || timeoutController?.signal;
  try {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...fetchInit,
    ...(init?.skipCache ? { cache: 'no-store' as const } : {}),
    signal,
    headers: {
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...(init?.skipCache ? { 'Cache-Control': 'no-cache' } : {}),
      ...(init?.headers || {}),
    },
  });
  if (!response.ok) {
    let detail = '';
    let payload: unknown;
    try {
      payload = await response.json();
      const errorText = curriculumPayloadErrorMessage(payload);
      detail = errorText ? `: ${errorText}` : '';
    } catch {
      detail = '';
    }
    throw new CurriculumApiError(`Curriculum API returned ${response.status} for ${path}${detail}`, response.status, path, payload);
  }
  return response.json();
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      // Only a timeout becomes a generic error; a caller-initiated abort must stay
      // an AbortError so effect cleanups can tell cancellation from failure.
      if (timeoutController?.signal.aborted && !callerSignal?.aborted) {
        throw new Error(`Curriculum API timed out for ${path}`);
      }
    }
    throw error;
  } finally {
    if (timeout !== null) window.clearTimeout(timeout);
  }
}

function abortError(): DOMException {
  return new DOMException('The operation was aborted.', 'AbortError');
}

function settleWithCallerAbort<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return promise;
  if (signal.aborted) return Promise.reject(abortError());
  return new Promise<T>((resolve, reject) => {
    const abort = () => reject(abortError());
    signal.addEventListener('abort', abort, { once: true });
    promise.then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
  });
}

async function fetchCollection<T>(path: string, init?: CurriculumRequestInit): Promise<T[]> {
  const payload = await fetchJson<CurriculumCollection<T>>(path, init);
  return payload.results;
}

// `compact` asks the server for a whitelist of fields rather than the whole
// module: see COMPACT_MODULE_LIST_FIELDS in backend/curriculum_api/views.py for
// the set, and COMPACT_MODULE_LIST_DROPPED beside it for why each omission is
// safe. The whitelist was traced from this function's callers, so adding a
// caller that reads a field outside it means widening the set server-side --
// the field will not simply appear. Dropped today: weekStructure and its nested
// components, sessionNames (use `sessionNamesCount`), deliveryMetadata,
// qualityScore, author, assignments, sourceType, deliveryRowId, legacyModuleId
// and invalidModuleCatalogueId. Anything that reads one of those must take the
// full response.
export function fetchCurriculumModules(signal?: AbortSignal, options: {
  compact?: boolean;
  programmeId?: string;
  cohortId?: string;
  groupId?: string;
  status?: string;
  page?: number;
  pageSize?: number;
  skipCache?: boolean;
  revalidate?: boolean;
} = {}): Promise<CurriculumModule[]> {
  const query = new URLSearchParams();
  if (options.compact) query.set('compact', 'true');
  if (options.programmeId) query.set('programme_id', options.programmeId);
  if (options.cohortId) query.set('cohort_id', options.cohortId);
  if (options.groupId) query.set('group_id', options.groupId);
  if (options.status) query.set('status', options.status);
  if (options.page) query.set('page', String(options.page));
  if (options.pageSize) query.set('page_size', String(options.pageSize));
  const suffix = query.toString() ? `?${query.toString()}` : '';
  // A cold curriculum cache can need to rebuild the module catalogue and its
  // authoring summaries. Give that read a minute, rather than reporting a
  // misleading failure at 30 seconds, while still preventing a stuck request
  // from leaving the catalogue loading forever.
  return fetchCollection<CurriculumModule>(`/curriculum/modules/${suffix}`, { signal, skipCache: options.skipCache, revalidate: options.revalidate, timeoutMs: 60000 });
}

// `omitContent` leaves each component's authored long text out of `settings`
// and names what was left out in `settingsOmitted`; see CurriculumComponent.
export function fetchCurriculumComponents(signal?: AbortSignal, options: { moduleCatalogueIds?: string[]; page?: number; pageSize?: number; omitContent?: boolean; skipCache?: boolean; revalidate?: boolean } = {}): Promise<CurriculumComponent[]> {
  const query = new URLSearchParams();
  const moduleCatalogueIds = (options.moduleCatalogueIds || []).filter(Boolean);
  if (moduleCatalogueIds.length) query.set('module_catalogue_ids', moduleCatalogueIds.join(','));
  if (options.omitContent) query.set('omit', 'content');
  if (options.page) query.set('page', String(options.page));
  if (options.pageSize) query.set('page_size', String(options.pageSize));
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return fetchCollection<CurriculumComponent>(`/curriculum/components/${suffix}`, { signal, skipCache: options.skipCache, revalidate: options.revalidate });
}

/**
 * The components of a set of modules, counted rather than listed: one row per
 * module/week bucket, carrying the same matching fields every component in it
 * has on `CurriculumComponent`, how many there are, how many are published, and
 * their Expected OTJH as `max(0, Math.round(expectedOtjh * 60))` summed. The
 * programme Overview reads this instead of `fetchCurriculumComponents`.
 */
export interface CurriculumComponentSummaryRow {
  moduleCatalogueId: string;
  moduleId: string;
  weekId: string;
  week: string;
  weekTitle: string;
  module: string;
  programme: string;
  count: number;
  publishedCount: number;
  otjhMinutes: number;
  /** The bucket's first component, by display order then title. */
  displayOrder: number | null;
  title: string;
}

export function fetchCurriculumComponentSummary(signal?: AbortSignal, options: { moduleCatalogueIds?: string[]; skipCache?: boolean; revalidate?: boolean } = {}): Promise<CurriculumComponentSummaryRow[]> {
  const moduleCatalogueIds = (options.moduleCatalogueIds || []).filter(Boolean);
  if (!moduleCatalogueIds.length) return Promise.resolve([]);
  const query = new URLSearchParams({ module_catalogue_ids: moduleCatalogueIds.join(',') });
  return fetchCollection<CurriculumComponentSummaryRow>(`/curriculum/components/summary/?${query.toString()}`, { signal, skipCache: options.skipCache, revalidate: options.revalidate });
}

/**
 * Search components available for reuse, across all programmes.
 *
 * Returns list rows only: no `settings`, no `ksbMappings`. Those are 24 MB
 * across the components table, the picker does not show them, and fetching
 * them for a whole page would dominate the request. Call
 * `fetchComponentLibraryDetail` for the handful actually being copied.
 *
 * Deliberately not cached: a filtered search changes with every keystroke,
 * unlike the stable snapshot `fetchCurriculumComponents` serves.
 */
export function fetchComponentLibrary(options: {
  search?: string;
  types?: string[];
  programmeIds?: string[];
  origins?: LibraryComponentOrigin[];
  page?: number;
  pageSize?: number;
  /**
   * Force a server-side rebuild. Only a caller that has just written a
   * component needs it; the modal opening is not that caller, and paying for a
   * rebuild there cost seconds on a list nobody had just changed. Left off,
   * the read still revalidates -- this tab's cache is ignored and the server
   * answers from its own, which is current for anything written through it.
   */
  skipCache?: boolean;
  revalidate?: boolean;
} = {}, signal?: AbortSignal): Promise<LibraryComponent[]> {
  const query = new URLSearchParams();
  const search = (options.search || '').trim();
  if (search) query.set('search', search);
  const types = (options.types || []).filter(Boolean);
  if (types.length) query.set('types', types.join(','));
  const programmeIds = (options.programmeIds || []).filter(Boolean);
  if (programmeIds.length) query.set('programme_ids', programmeIds.join(','));
  const origins = (options.origins || []).filter(Boolean);
  if (origins.length) query.set('origins', origins.join(','));
  if (options.page) query.set('page', String(options.page));
  if (options.pageSize) query.set('page_size', String(options.pageSize));
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return fetchCollection<LibraryComponent>(
    `/curriculum/components/library/${suffix}`,
    { signal, skipCache: options.skipCache, revalidate: options.revalidate ?? !options.skipCache },
  );
}

/**
 * Full authoring detail for specific components — `settings` and `ksbMappings`
 * included. Call this with the ids being copied, never with a whole page.
 */
export function fetchComponentLibraryDetail(
  ids: string[],
  signal?: AbortSignal,
  options: { skipCache?: boolean; revalidate?: boolean } = {},
): Promise<LibraryComponent[]> {
  const wanted = ids.filter(Boolean);
  if (!wanted.length) return Promise.resolve([]);
  const query = new URLSearchParams({ ids: wanted.join(',') });
  return fetchCollection<LibraryComponent>(
    `/curriculum/components/library/?${query.toString()}`,
    { signal, skipCache: options.skipCache, revalidate: options.revalidate ?? !options.skipCache },
  );
}

export function fetchCurriculumStats(signal?: AbortSignal): Promise<CurriculumOverview['stats']> {
  return fetchJson<CurriculumOverview['stats']>('/curriculum/stats/', { signal });
}

export function fetchCurriculumProgrammes(signal?: AbortSignal, options: { skipCache?: boolean; revalidate?: boolean; visibility?: 'all' | 'operational'; timeoutMs?: number } = {}): Promise<CurriculumProgramme[]> {
  const query = new URLSearchParams();
  if (options.visibility === 'all') query.set('visibility', 'all');
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return fetchCollection<CurriculumProgramme>(`/curriculum/programmes/${suffix}`, { signal, skipCache: options.skipCache, revalidate: options.revalidate, timeoutMs: options.timeoutMs });
}

/** The KSB numbers a programme card shows, for one programme. See CurriculumProgramme.ksbMapped. */
export interface ProgrammeKsbStats {
  programmeId: string;
  ksbMapped: number;
  ksbTotal: number;
  learnerKsbProgressPercentage: number;
  learnerKsbConsumedWeight: number;
  learnerKsbExpectedWeight: number;
  learnerKsbLearnerCount: number;
  learnerKsbCodesStarted: number;
  learnerKsbCodesComplete: number;
  learnerKsbCodesTotal: number;
}

export function fetchProgrammeKsbStats(id: string, signal?: AbortSignal, options: { visibility?: 'all' | 'operational'; revalidate?: boolean } = {}): Promise<ProgrammeKsbStats> {
  const query = new URLSearchParams();
  if (options.visibility === 'all') query.set('visibility', 'all');
  const suffix = query.toString() ? `?${query.toString()}` : '';
  // A cold statistics calculation against the remote database can exceed 30s.
  // Keep a finite limit, measured from dispatch rather than time in the queue.
  return fetchJson<ProgrammeKsbStats>(`/curriculum/programmes/${encodeURIComponent(id)}/ksb-stats/${suffix}`, { signal, revalidate: options.revalidate, timeoutMs: 120000 });
}

export function fetchCurriculumGroups(signal?: AbortSignal): Promise<CurriculumGroup[]> {
  return fetchCollection<CurriculumGroup>('/curriculum/groups/', { signal });
}

export function fetchCurriculumKsbFrameworks(signal?: AbortSignal): Promise<CurriculumKsbFramework[]> {
  return fetchCollection<CurriculumKsbFramework>('/curriculum/ksb-frameworks/', { signal });
}

export function fetchCurriculumKsbSets(signal?: AbortSignal, options: { all?: boolean } = {}): Promise<CurriculumKsbSet[]> {
  const query = new URLSearchParams();
  if (options.all) query.set('visibility', 'all');
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return fetchCollection<CurriculumKsbSet>(`/curriculum/ksb-sets/${suffix}`, { signal });
}

export function fetchCurriculumStandards(signal?: AbortSignal): Promise<CurriculumStandard[]> {
  return fetchCollection<CurriculumStandard>('/curriculum/standards/', { signal });
}

export function fetchCurriculumStandardDetail(id: string, signal?: AbortSignal): Promise<CurriculumStandard> {
  return fetchJson<CurriculumStandard>(`/curriculum/standards/${encodeURIComponent(id)}/`, { signal });
}

export function fetchCurriculumKsbCoverage(params: { sourceType?: string; sourceId?: string } = {}, signal?: AbortSignal): Promise<CurriculumKsbCoverageResponse> {
  const query = new URLSearchParams();
  if (params.sourceType) query.set('source_type', params.sourceType);
  if (params.sourceId) query.set('source_id', params.sourceId);
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return fetchJson<CurriculumKsbCoverageResponse>(`/curriculum/ksb-coverage/${suffix}`, { signal, timeoutMs: 60000 });
}

export function fetchCurriculumProgrammeKsbCoverage(programmeId: string, params: { sourceType?: string; sourceId?: string; actualMappings?: boolean; revalidate?: boolean } = {}, signal?: AbortSignal): Promise<CurriculumKsbCoverageResponse> {
  const query = new URLSearchParams();
  if (params.sourceType) query.set('source_type', params.sourceType);
  if (params.sourceId) query.set('source_id', params.sourceId);
  if (params.actualMappings) query.set('actual_mappings', '1');
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return fetchJson<CurriculumKsbCoverageResponse>(`/curriculum/programmes/${encodeURIComponent(programmeId)}/ksb-coverage/${suffix}`, { signal, revalidate: params.revalidate, timeoutMs: 60000 });
}

// Kept as named entry points because the Programme workspace reads them by
// name; both are the scope reads at the top of the hierarchy.
export function fetchCurriculumProgrammeLearnerKsbImpact(programmeId: string, params: { sourceType?: string; sourceId?: string; learnerStatus?: string } = {}, signal?: AbortSignal): Promise<CurriculumProgrammeLearnerKsbImpactResponse> {
  return fetchCurriculumScopeLearnerKsbImpact('programme', programmeId, params, signal);
}

export function fetchCurriculumProgrammeLearnerRoster(programmeId: string, params: { cohort?: string; group?: string; learnerStatus?: string } = {}, signal?: AbortSignal): Promise<CurriculumProgrammeLearnerRosterResponse> {
  return fetchCurriculumScopeLearnerRoster('programme', programmeId, params, signal);
}

// One route per level of Programme -> Cohort -> Group -> Module -> Week, so a
// page asks for its own scope and nothing wider. `component` has no path route
// of its own and goes through the query form.
const SCOPE_ROSTER_PATHS: Record<string, (id: string) => string> = {
  programme: id => `/curriculum/programmes/${encodeURIComponent(id)}/learner-roster/`,
  cohort: id => `/curriculum/cohorts/${encodeURIComponent(id)}/learner-roster/`,
  group: id => `/curriculum/groups/${encodeURIComponent(id)}/learner-roster/`,
  module: id => `/curriculum/modules/${encodeURIComponent(id)}/learner-roster/`,
  week: id => `/curriculum/weeks/${encodeURIComponent(id)}/learner-roster/`,
};

const SCOPE_IMPACT_PATHS: Record<string, (id: string) => string> = {
  programme: id => `/curriculum/programmes/${encodeURIComponent(id)}/learner-ksb-impact/`,
  cohort: id => `/curriculum/cohorts/${encodeURIComponent(id)}/learner-ksb-impact/`,
  group: id => `/curriculum/groups/${encodeURIComponent(id)}/learner-ksb-impact/`,
  module: id => `/curriculum/modules/${encodeURIComponent(id)}/learner-ksb-impact/`,
  week: id => `/curriculum/weeks/${encodeURIComponent(id)}/learner-ksb-impact/`,
};

function scopePath(
  paths: Record<string, (id: string) => string>,
  fallback: string,
  scope: CurriculumLearnerScope,
  identifier: string,
  query: URLSearchParams,
) {
  const builder = paths[scope];
  if (builder) {
    const suffix = query.toString() ? `?${query.toString()}` : '';
    return `${builder(identifier)}${suffix}`;
  }
  query.set('scope', scope);
  query.set('identifier', identifier);
  return `${fallback}?${query.toString()}`;
}

/**
 * The learners enrolment placed inside one curriculum scope.
 *
 * A module/week/component reports the roster of the group that delivers it —
 * `placementBasis` on the response says which level the roster came from.
 */
export function fetchCurriculumScopeLearnerRoster(
  scope: CurriculumLearnerScope,
  identifier: string,
  params: { cohort?: string; group?: string; learnerStatus?: string; revalidate?: boolean } = {},
  signal?: AbortSignal,
  options: { timeoutMs?: number; compact?: boolean } = {},
): Promise<CurriculumScopeLearnerRosterResponse> {
  const query = new URLSearchParams();
  if (params.cohort) query.set('cohort', params.cohort);
  if (params.group) query.set('group', params.group);
  if (params.learnerStatus) query.set('learnerStatus', params.learnerStatus);
  if (options.compact) query.set('compact', 'true');
  return fetchJson<CurriculumScopeLearnerRosterResponse>(
    scopePath(SCOPE_ROSTER_PATHS, '/curriculum/learner-roster/', scope, identifier, query),
    { signal, revalidate: params.revalidate, timeoutMs: options.timeoutMs ?? 60000 },
  );
}

/**
 * What the learners in one curriculum scope have actually achieved there.
 *
 * `otjhAchievement` is hours, `ksbAchievement` is the achieved KSB heatmap, and
 * both are computed from the components inside this scope alone — a cohort is
 * never handed the whole programme's consumption.
 */
export function fetchCurriculumScopeLearnerKsbImpact(
  scope: CurriculumLearnerScope,
  identifier: string,
  params: { sourceType?: string; sourceId?: string; learnerStatus?: string; moduleProgress?: boolean } = {},
  signal?: AbortSignal,
): Promise<CurriculumScopeLearnerKsbImpactResponse> {
  const query = new URLSearchParams();
  if (params.sourceType) query.set('source_type', params.sourceType);
  if (params.sourceId) query.set('source_id', params.sourceId);
  if (params.learnerStatus) query.set('learnerStatus', params.learnerStatus);
  if (params.moduleProgress) query.set('moduleProgress', '1');
  return fetchJson<CurriculumScopeLearnerKsbImpactResponse>(
    scopePath(SCOPE_IMPACT_PATHS, '/curriculum/learner-ksb-impact/', scope, identifier, query),
    { signal },
  );
}

export interface CurriculumKsbCoverageSummaryResponse {
  scope: string;
  identifier: string;
  source?: CurriculumKsbCoverageSource;
  view: 'summary';
  items: Array<{
    code: string; title: string; description?: string; coverage_key: string;
    source_type: string; source_id: string; source_name?: string; source_label?: string;
    raw_total_weight: number; occurrence_count: number;
  }>;
}

export function fetchCurriculumProgrammeKsbCoverageSummary(programmeId: string, params: { sourceType?: string; sourceId?: string; revalidate?: boolean } = {}, signal?: AbortSignal): Promise<CurriculumKsbCoverageSummaryResponse> {
  const query = new URLSearchParams({ view: 'summary' });
  if (params.sourceType) query.set('source_type', params.sourceType);
  if (params.sourceId) query.set('source_id', params.sourceId);
  return fetchJson<CurriculumKsbCoverageSummaryResponse>(`/curriculum/programmes/${encodeURIComponent(programmeId)}/ksb-coverage/?${query}`, { signal, revalidate: params.revalidate, timeoutMs: 60000 });
}

export function fetchCurriculumDeliveryScope(signal?: AbortSignal): Promise<Pick<CurriculumOverview, 'cohorts' | 'groups'>> {
  return fetchJson<Pick<CurriculumOverview, 'cohorts' | 'groups'>>('/curriculum/delivery-scope/', { signal, timeoutMs: 30000 });
}

export function fetchCurriculumKsbSetSummaries(signal?: AbortSignal): Promise<CurriculumKsbSetSummary[]> {
  return fetchCollection<CurriculumKsbSetSummary>('/curriculum/ksb-sets/?view=summary&visibility=all', { signal });
}

export function fetchCurriculumKsbSourceDefinitions(id: string, signal?: AbortSignal): Promise<CurriculumKsbSourceDefinitions> {
  return fetchJson<CurriculumKsbSourceDefinitions>(`/curriculum/ksb-sets/${encodeURIComponent(id)}/definitions/?visibility=all`, { signal });
}

function ksbAchievementQuery(params: CurriculumKsbAchievementFilters = {}) {
  const query = new URLSearchParams();
  const values: Array<[string, string | number | undefined]> = [
    ['cohort_id', params.cohortId], ['group_id', params.groupId], ['module_id', params.moduleId],
    ['learner_id', params.learnerId], ['component_id', params.componentId], ['ksb_type', params.ksbType],
    ['ksb_id', params.ksbId], ['search', params.search], ['date_from', params.dateFrom], ['date_to', params.dateTo],
    ['page', params.page], ['page_size', params.pageSize], ['ksb_page', params.ksbPage], ['ksb_page_size', params.ksbPageSize],
  ];
  values.forEach(([key, value]) => { if (value !== undefined && value !== '') query.set(key, String(value)); });
  return query.toString() ? `?${query.toString()}` : '';
}

export function fetchCurriculumKsbAchievement(programmeId: string, params: CurriculumKsbAchievementFilters = {}, signal?: AbortSignal) {
  return fetchJson<CurriculumKsbAchievementResponse>(`/curriculum/programmes/${encodeURIComponent(programmeId)}/ksb-achievement/${ksbAchievementQuery(params)}`, { signal });
}

export function fetchCurriculumKsbAchievementConsumptions(programmeId: string, ksbDefinitionId: string, params: CurriculumKsbAchievementFilters = {}, signal?: AbortSignal) {
  return fetchJson<CurriculumKsbAchievementConsumptionResponse>(`/curriculum/programmes/${encodeURIComponent(programmeId)}/ksb-achievement/${encodeURIComponent(ksbDefinitionId)}/consumptions/${ksbAchievementQuery(params)}`, { signal });
}

export function fetchCurriculumGroupKsbCoverage(groupId: string, params: { sourceType?: string; sourceId?: string } = {}, signal?: AbortSignal): Promise<CurriculumKsbCoverageResponse> {
  const query = new URLSearchParams();
  if (params.sourceType) query.set('source_type', params.sourceType);
  if (params.sourceId) query.set('source_id', params.sourceId);
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return fetchJson<CurriculumKsbCoverageResponse>(`/curriculum/groups/${encodeURIComponent(groupId)}/ksb-coverage/${suffix}`, { signal });
}

export function fetchCurriculumModuleKsbCoverage(moduleId: string, params: { sourceType?: string; sourceId?: string } = {}, signal?: AbortSignal): Promise<CurriculumKsbCoverageResponse> {
  const query = new URLSearchParams();
  if (params.sourceType) query.set('source_type', params.sourceType);
  if (params.sourceId) query.set('source_id', params.sourceId);
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return fetchJson<CurriculumKsbCoverageResponse>(`/curriculum/modules/${encodeURIComponent(moduleId)}/ksb-coverage/${suffix}`, { signal });
}

export function fetchCurriculumWeekKsbCoverage(weekId: string, params: { sourceType?: string; sourceId?: string } = {}, signal?: AbortSignal): Promise<CurriculumKsbCoverageResponse> {
  const query = new URLSearchParams();
  if (params.sourceType) query.set('source_type', params.sourceType);
  if (params.sourceId) query.set('source_id', params.sourceId);
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return fetchJson<CurriculumKsbCoverageResponse>(`/curriculum/weeks/${encodeURIComponent(weekId)}/ksb-coverage/${suffix}`, { signal });
}

export function fetchCurriculumCohortKsbCoverage(cohortId: string, params: { sourceType?: string; sourceId?: string } = {}, signal?: AbortSignal): Promise<CurriculumKsbCoverageResponse> {
  const query = new URLSearchParams();
  if (params.sourceType) query.set('source_type', params.sourceType);
  if (params.sourceId) query.set('source_id', params.sourceId);
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return fetchJson<CurriculumKsbCoverageResponse>(`/curriculum/cohorts/${encodeURIComponent(cohortId)}/ksb-coverage/${suffix}`, { signal });
}

export function fetchCurriculumKsbTrace(ksbId: string, params: { sourceType?: string; sourceId?: string } = {}, signal?: AbortSignal): Promise<CurriculumKsbCoverageItem> {
  const query = new URLSearchParams();
  if (params.sourceType) query.set('source_type', params.sourceType);
  if (params.sourceId) query.set('source_id', params.sourceId);
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return fetchJson<CurriculumKsbCoverageItem>(`/curriculum/ksb-coverage/trace/${encodeURIComponent(ksbId)}/${suffix}`, { signal });
}

export function fetchCurriculumReadiness(params: { scope?: string; identifier?: string; sourceType?: string; sourceId?: string } = {}, signal?: AbortSignal): Promise<CurriculumReadinessResponse> {
  const query = new URLSearchParams();
  if (params.scope) query.set('scope', params.scope);
  if (params.identifier) query.set('identifier', params.identifier);
  if (params.sourceType) query.set('source_type', params.sourceType);
  if (params.sourceId) query.set('source_id', params.sourceId);
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return fetchJson<CurriculumReadinessResponse>(`/curriculum/readiness/ksb-coverage/${suffix}`, { signal });
}

export function fetchComponentKsbMappings(componentId: string, signal?: AbortSignal): Promise<CurriculumComponentKsbMappingsResponse> {
  return fetchJson<CurriculumComponentKsbMappingsResponse>(`/curriculum/components/${encodeURIComponent(componentId)}/ksb-mappings/`, { signal });
}

export function createComponentKsbMapping(componentId: string, input: CurriculumKsbMappingInput | { mappings: CurriculumKsbMappingInput[] }) {
  return postJson<CurriculumComponentKsbMappingsResponse & { created: boolean }>(`/curriculum/components/${encodeURIComponent(componentId)}/ksb-mappings/`, input);
}

export function updateComponentKsbMapping(mappingId: string, input: Partial<CurriculumKsbMappingInput>) {
  return patchJson<{ updated: boolean; mapping: CurriculumKsbTraceMapping }>(`/curriculum/ksb-mappings/${encodeURIComponent(mappingId)}/`, input);
}

export function deleteComponentKsbMapping(mappingId: string) {
  return deleteJson<{ deleted: boolean; id: string }>(`/curriculum/ksb-mappings/${encodeURIComponent(mappingId)}/`);
}

export function createCurriculumKsbFramework(input: CurriculumKsbFrameworkInput) {
  return postJson<{ created: boolean; framework: CurriculumKsbFramework }>('/curriculum/ksb-frameworks/', input);
}

export function updateCurriculumKsbFramework(id: string, input: CurriculumKsbFrameworkInput) {
  return patchJson<{ updated: boolean; id: string }>(`/curriculum/ksb-frameworks/${encodeURIComponent(id)}/`, input);
}

export function deleteCurriculumKsbFramework(id: string) {
  return deleteJson<{ deleted: boolean; id: string }>(`/curriculum/ksb-frameworks/${encodeURIComponent(id)}/`);
}

export interface ImportedMeetingPerson {
  name: string;
  email: string;
}

type ImportedPeopleStatus = 'available' | 'partial' | 'access_denied' | 'not_connected' | 'unavailable' | 'ambiguous';

export interface ImportedMeetingParticipants {
  checkedAt: string;
  organizer?: {
    status: ImportedPeopleStatus;
    message: string;
    source?: 'online_meeting' | 'saved_microsoft_records';
    person?: ImportedMeetingPerson;
    objectId?: string;
    verifiedAt?: string;
    evidenceAt?: string;
  };
  calendar: {
    status: ImportedPeopleStatus;
    message: string;
    source?: 'organizer_calendar' | 'attendee_calendar';
    organizer?: ImportedMeetingPerson;
    attendees?: ImportedMeetingPerson[];
    hideAttendees?: boolean;
  };
  roles: {
    status: ImportedPeopleStatus;
    message: string;
    organizer?: ImportedMeetingPerson;
    presenters?: ImportedMeetingPerson[];
    coOrganizers?: ImportedMeetingPerson[];
    allowedPresenters?: string;
  };
}

/** Read Microsoft metadata without publishing invitations or changing the saved series. */
export function fetchImportedMeetingParticipants(liveSessionId: string, signal?: AbortSignal) {
  return fetchJson<ImportedMeetingParticipants>(
    `/curriculum/teams-meetings/${encodeURIComponent(liveSessionId)}/imported-participants/`,
    { signal, skipCache: true, timeoutMs: 75000 },
  );
}

export interface ImportedCalendarPreview {
  status: 'ready' | 'blocked' | 'up_to_date' | 'verification_pending';
  message: string;
  code?: string;
  previewToken?: string;
  currentEndDate?: string;
  proposedEndDate?: string;
  timeZone?: string;
  addedSessions: { start: string; end: string }[];
  existingInviteeCount?: number;
  organizer?: ImportedMeetingPerson;
  joinUrl?: string;
  operationId?: string;
  calendarChangeRequired?: boolean;
  notificationScope?: string;
  connectionRequired?: boolean;
  connectionKind?: 'group' | 'user';
}

export interface ImportedCalendarUpdateResult {
  status: 'confirmed' | 'verification_pending' | 'blocked';
  message: string;
  code?: string;
  operationId?: string;
  addedSessions?: number;
  importedSessions?: number;
  connectionRequired?: boolean;
  connectionKind?: 'group' | 'user';
}

export interface ImportedCalendarConnection {
  connected: boolean;
  configured?: boolean;
  expiresAt?: string;
  message?: string;
}

/** Calendar writes require the session's CSRF token and must never auto-retry. */
async function importedCalendarPost<T>(liveSessionId: string, action: string, body: unknown, timeoutMs = 90000): Promise<T> {
  const path = `/curriculum/teams-meetings/${encodeURIComponent(liveSessionId)}/imported-calendar/${action}/`;
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await coachFetch(`${API_BASE_URL}${path}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal: controller.signal,
    });
    const data = await response.json();
    if (!response.ok) throw new CurriculumApiError(data?.error || data?.message || 'The calendar request could not be completed.', response.status, path, data);
    if (action === 'confirm' || action === 'status') {
      mutationEpoch += 1;
      clearCurriculumGetCache();
    }
    return data;
  } finally {
    window.clearTimeout(timer);
  }
}

export async function fetchImportedCalendarConnection(liveSessionId: string): Promise<ImportedCalendarConnection> {
  const path = `/curriculum/teams-meetings/${encodeURIComponent(liveSessionId)}/imported-calendar/connection/`;
  const response = await coachFetch(`${API_BASE_URL}${path}`, { cache: 'no-store' });
  const data = await response.json();
  if (!response.ok) throw new CurriculumApiError(data?.error || 'The calendar connection could not be read.', response.status, path, data);
  return data;
}

export function connectImportedCalendar(liveSessionId: string, calendarKind: 'group' | 'user') {
  return importedCalendarPost<{ authorizationUrl: string; callbackOrigin: string }>(liveSessionId, 'connect', { calendarKind });
}

export function previewImportedCalendarUpdate(liveSessionId: string) {
  return importedCalendarPost<ImportedCalendarPreview>(liveSessionId, 'preview', {});
}

export function confirmImportedCalendarUpdate(liveSessionId: string, previewToken: string, acknowledgeNotifications: boolean) {
  return importedCalendarPost<ImportedCalendarUpdateResult>(liveSessionId, 'confirm', { previewToken, acknowledgeNotifications }, 120000);
}

/** Recover a result by reading Microsoft; never repeats the calendar update. */
export function checkImportedCalendarUpdate(liveSessionId: string, operationId?: string) {
  return importedCalendarPost<ImportedCalendarUpdateResult>(liveSessionId, 'status', { operationId });
}

export function fetchCurriculumTeamsMeetingSummaries(
  signal?: AbortSignal,
  options: { moduleCatalogueIds?: string[]; occurrenceDates?: boolean; skipCache?: boolean; revalidate?: boolean } = {},
): Promise<CurriculumTeamsMeetingSummary[]> {
  const ids = (options.moduleCatalogueIds || []).filter(Boolean);
  const query = new URLSearchParams();
  if (ids.length) query.set('module_catalogue_ids', ids.join(','));
  // Opt-in: the occurrence dates are only needed by the page that compares them
  // against the module's stored session plan, and they are the bulk of the payload.
  if (options.occurrenceDates) query.set('occurrence_dates', '1');
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return fetchCollection<CurriculumTeamsMeetingSummary>(
    `/curriculum/teams-meetings/summary/${suffix}`,
    { signal, skipCache: options.skipCache, revalidate: options.revalidate },
  );
}

/** Real per-occurrence live-session state (status/dates/attendance counts) for a
 *  set of modules and/or series. Feeds the Sessions tab's scheduled/completed
 *  badges — the status comes from the sync service, not a client date guess.
 *  Light by design: attendance rows and transcript/recording artifacts are NOT
 *  included here (load them lazily with `fetchLiveSessionArtifacts`). */
export function fetchCurriculumLiveSessionOccurrences(
  options: { moduleCatalogueIds?: string[]; liveSessionIds?: string[]; skipCache?: boolean; signal?: AbortSignal } = {},
): Promise<CurriculumLiveSessionOccurrencesResponse> {
  const moduleIds = (options.moduleCatalogueIds || []).filter(Boolean);
  const sessionIds = (options.liveSessionIds || []).filter(Boolean);
  if (!moduleIds.length && !sessionIds.length) {
    return Promise.resolve({ series: [], occurrences: [] });
  }
  const query = new URLSearchParams();
  if (moduleIds.length) query.set('module_catalogue_ids', moduleIds.join(','));
  if (sessionIds.length) query.set('live_session_ids', sessionIds.join(','));
  return fetchJson<CurriculumLiveSessionOccurrencesResponse>(
    `/curriculum/live-sessions/occurrences/?${query.toString()}`,
    { signal: options.signal, skipCache: options.skipCache },
  );
}

/** A completed live session's captured artifacts: every occurrence with its
 *  attendance list and transcript/recording artifacts. Keyed by the series id
 *  (`teamsLiveSessionId` on the component). Heavier than the occurrences read, so
 *  the Sessions tab only calls this when a completed row is expanded. */
export function fetchLiveSessionArtifacts(
  liveSessionId: string,
  options: { skipCache?: boolean; signal?: AbortSignal } = {},
): Promise<LiveSessionArtifactsResponse> {
  return fetchJson<LiveSessionArtifactsResponse>(
    `/curriculum/teams-meetings/${encodeURIComponent(liveSessionId)}/artifacts/`,
    { signal: options.signal, skipCache: options.skipCache },
  );
}

/** The meeting ID and passcode Teams prints under `link`, one of the series'
 *  own join links. Read from Microsoft on first ask, then stored; blanks when
 *  Microsoft cannot say. Staff only. */
export type TeamsMeetingDialIn = { joinUrl: string; meetingId: string; passcode: string };

export function fetchTeamsMeetingDialIn(
  liveSessionId: string,
  link: string,
  options: { signal?: AbortSignal } = {},
): Promise<TeamsMeetingDialIn> {
  return fetchJson<TeamsMeetingDialIn>(
    `/curriculum/teams-meetings/${encodeURIComponent(liveSessionId)}/dial-in/?link=${encodeURIComponent(link)}`,
    { signal: options.signal, skipCache: true },
  );
}

/** Absolute URL that streams a transcript/recording artifact's content (proxied
 *  from Graph server-side). Same-origin + cookie auth, so it can be used directly
 *  as an `href` / `download` / media `src`. */
export function liveSessionArtifactContentUrl(liveSessionId: string, artifactId: string): string {
  return `${API_BASE_URL}/curriculum/teams-meetings/${encodeURIComponent(liveSessionId)}/artifacts/${encodeURIComponent(artifactId)}/content/`;
}

/** Same-origin redirect that records which Week/occurrence launched the shared Teams meeting. */
export function liveSessionJoinUrl(liveSessionId: string, occurrenceId: string): string {
  return `${API_BASE_URL}/curriculum/teams-meetings/${encodeURIComponent(liveSessionId)}/occurrences/${encodeURIComponent(occurrenceId)}/join/`;
}

/** Inline media/text response. Without this flag the backend deliberately sends
 * Content-Disposition: attachment so the same endpoint is a real download. */
export function liveSessionArtifactPreviewUrl(liveSessionId: string, artifactId: string): string {
  return `${liveSessionArtifactContentUrl(liveSessionId, artifactId)}?preview=1`;
}

export function fetchCurriculumSessions(
  signal?: AbortSignal,
  options: { skipCache?: boolean; revalidate?: boolean; moduleCatalogueId?: string; visibility?: 'operational' | 'all' } = {},
): Promise<CurriculumSession[]> {
  // Sessions live in the 45s "dynamic" cache tier, so a caller that has just
  // scheduled a module (or opens straight after) can otherwise read a stale
  // snapshot that predates its session dates. skipCache lets those callers read
  // the current plan instead of waiting out the TTL. `revalidate` is for the
  // background re-read after somebody else's write: past this tab's cache, but
  // answered from the server's.
  //
  // `moduleCatalogueId` asks for that one module's sessions, which the server
  // derives fresh from its own rows every time. Without it, current dates for
  // one module meant skipCache forcing a rebuild of every session there is.
  const queryParams = new URLSearchParams();
  if (options.moduleCatalogueId) queryParams.set('module_catalogue_id', options.moduleCatalogueId);
  if (options.visibility === 'all') queryParams.set('visibility', 'all');
  const query = queryParams.toString() ? `?${queryParams.toString()}` : '';
  return fetchCollection<CurriculumSession>(`/curriculum/sessions/${query}`, { signal, skipCache: options.skipCache, revalidate: options.revalidate });
}

// 30s, not 15s: a forced rebuild of the curriculum payload takes ~13s, so a 15s
// budget aborted these on a cold cache while the backend was still succeeding.
export function fetchCurriculumTutors(signal?: AbortSignal, options: { skipCache?: boolean; revalidate?: boolean } = {}): Promise<CurriculumStaffProfile[]> {
  return fetchCollection<CurriculumStaffProfile>('/curriculum/tutors/', { signal, skipCache: options.skipCache, revalidate: options.revalidate, timeoutMs: 30000 });
}

export function fetchCurriculumCoaches(signal?: AbortSignal, options: { skipCache?: boolean; revalidate?: boolean } = {}): Promise<CurriculumStaffProfile[]> {
  return fetchCollection<CurriculumStaffProfile>('/curriculum/coaches/', { signal, skipCache: options.skipCache, revalidate: options.revalidate, timeoutMs: 30000 });
}

export function fetchCurriculumHolidays(signal?: AbortSignal, options: { skipCache?: boolean; revalidate?: boolean } = {}): Promise<CurriculumHoliday[]> {
  return fetchCollection<CurriculumHoliday>('/curriculum/holidays/', { signal, skipCache: options.skipCache, revalidate: options.revalidate });
}

export function fetchEnglandHolidays(signal?: AbortSignal, options: { skipCache?: boolean; revalidate?: boolean } = {}): Promise<EnglandHoliday[]> {
  return fetchCollection<EnglandHoliday>('/curriculum/england-holidays/', { signal, skipCache: options.skipCache, revalidate: options.revalidate });
}

// `compact` skips the collections a structure read does not need (sessions,
// holidays, staff, authoring details) and, on each module, drops `sessionNames`
// in favour of `sessionNamesCount` -- the same projection
// `fetchCurriculumModules` applies. Nothing that reads this endpoint renders
// those titles; the module workspace derives its own from the authored week
// structure. A caller that needs them must take the full response.
export function fetchCurriculumOverview(signal?: AbortSignal, options: { compact?: boolean; skipCache?: boolean; revalidate?: boolean; timeoutMs?: number; visibility?: 'operational' | 'all' } = {}): Promise<CurriculumOverview> {
  // Capped so a slow backend fails into its own error+Retry banner instead of
  // hanging on the browser's multi-minute default. 90s, not 30s: a forced rebuild
  // is ~13s on its own, and behind the browser's six-per-host queue on a busy
  // page it was being cut off at 30s before the server had answered.
  const query = new URLSearchParams();
  if (options.compact) query.set('compact', 'true');
  if (options.visibility === 'all') query.set('visibility', 'all');
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return fetchJson<CurriculumOverview>(`/curriculum/overview/${suffix}`, { signal, skipCache: options.skipCache, revalidate: options.revalidate, timeoutMs: options.timeoutMs ?? 90000 });
}

// `omitComponents` leaves `flat.components` out of the response, for a caller
// that reads the components through fetchCurriculumComponents anyway.
// `omitTree` leaves out the nested `cohorts` view, which repeats every module of
// `flat.modules`; with it the response has no `cohorts` at all, so only a caller
// that reads `flat` alone may ask for it. On a large programme each of the two
// is a large share of the document. `omitWeekComponents` sends every module's
// `weekStructure` as an outline -- weeks without components, flagged
// `weekStructureOutline` -- for a caller that reads week titles and order only.
// Parameters are sent in camelCase; the server still accepts the old snake_case
// spellings.
export function fetchCurriculumProgrammeDetail(id: string, signal?: AbortSignal, options: { visibility?: 'all' | 'operational'; deferStats?: boolean; omitComponents?: boolean; omitTree?: boolean; omitWeekComponents?: boolean; skipCache?: boolean; revalidate?: boolean; timeoutMs?: number } = {}): Promise<CurriculumProgrammeDetail> {
  const query = new URLSearchParams();
  if (options.visibility === 'all') query.set('visibility', 'all');
  if (options.deferStats) query.set('deferStats', '1');
  const omit = [
    options.omitComponents ? 'components' : '',
    options.omitTree ? 'tree' : '',
    options.omitWeekComponents ? 'weekComponents' : '',
  ].filter(Boolean);
  if (omit.length) query.set('omit', omit.join(','));
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return fetchJson<CurriculumProgrammeDetail>(`/curriculum/programmes/${encodeURIComponent(id)}/detail/${suffix}`, { signal, skipCache: options.skipCache, revalidate: options.revalidate, timeoutMs: options.timeoutMs ?? 60000 });
}

// The detail document in two parts, for a page that draws the programme, its
// cohorts and groups before the modules -- the slowest part of the build --
// arrive. Both leave out the KSB stats (see fetchProgrammeKsbStats) and the
// components; the modules' weekStructure is an outline, as with
// `omitWeekComponents`.
//
// `readAfterWrite` is for the read straight after this client's own curriculum
// save. It revalidates and sends `Cache-Control: max-age=0`, which has the
// server check the database for the save's generation instead of trusting a
// poll up to 3s old, without the full rebuild `skipCache` forces.
type ProgrammeDetailSectionOptions = { visibility?: 'all' | 'operational'; skipCache?: boolean; revalidate?: boolean; readAfterWrite?: boolean; timeoutMs?: number };

function programmeDetailSectionPath(id: string, section: 'shell' | 'modules', visibility?: 'all' | 'operational') {
  const suffix = visibility === 'all' ? '?visibility=all' : '';
  return `/curriculum/programmes/${encodeURIComponent(id)}/detail/${section}/${suffix}`;
}

function programmeDetailSectionInit(signal: AbortSignal | undefined, options: ProgrammeDetailSectionOptions): CurriculumRequestInit {
  return {
    signal,
    skipCache: options.skipCache,
    revalidate: options.revalidate || options.readAfterWrite,
    timeoutMs: options.timeoutMs ?? 60000,
    ...(options.readAfterWrite && !options.skipCache ? { headers: { 'Cache-Control': 'max-age=0' } } : {}),
  };
}

export function fetchCurriculumProgrammeDetailShell(id: string, signal?: AbortSignal, options: ProgrammeDetailSectionOptions = {}): Promise<CurriculumProgrammeDetailShell> {
  return fetchJson<CurriculumProgrammeDetailShell>(programmeDetailSectionPath(id, 'shell', options.visibility), programmeDetailSectionInit(signal, options));
}

export function fetchCurriculumProgrammeDetailModules(id: string, signal?: AbortSignal, options: ProgrammeDetailSectionOptions & { view?: 'workspace' } = {}): Promise<CurriculumProgrammeDetailModules> {
  let path = programmeDetailSectionPath(id, 'modules', options.visibility);
  if (options.view === 'workspace') path += `${path.includes('?') ? '&' : '?'}view=workspace`;
  return fetchJson<CurriculumProgrammeDetailModules>(path, programmeDetailSectionInit(signal, options));
}

export { fetchCurriculumOverview as fetchCurriculumOverviewBundle };

/**
 * Versioned curriculum records, most recently changed first. Answers
 * `available: false` — not an error — when the history tables do not exist.
 */
export function fetchCurriculumVersions(
  options: { module?: string; entityType?: string; search?: string; limit?: number; signal?: AbortSignal; skipCache?: boolean } = {},
): Promise<CurriculumVersionIndex> {
  const query = new URLSearchParams();
  if (options.module) query.set('module', options.module);
  if (options.entityType && options.entityType !== 'all') query.set('entity_type', options.entityType);
  if (options.search) query.set('search', options.search);
  if (options.limit) query.set('limit', String(options.limit));
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return fetchJson<CurriculumVersionIndex>(`/curriculum/quality/versions/${suffix}`, {
    signal: options.signal,
    skipCache: options.skipCache,
    timeoutMs: 30000,
  });
}

/**
 * One record's timeline. Pass `snapshot` to also get the stored content of that
 * revision — what the record actually held, which is what a version number is
 * looked up in order to see.
 */
export function fetchCurriculumRecordHistory(
  entityType: string,
  entityId: string,
  options: { snapshot?: number; limit?: number; signal?: AbortSignal; skipCache?: boolean } = {},
): Promise<CurriculumRecordHistory> {
  const query = new URLSearchParams();
  if (options.snapshot) query.set('snapshot', String(options.snapshot));
  if (options.limit) query.set('limit', String(options.limit));
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return fetchJson<CurriculumRecordHistory>(
    `/curriculum/quality/versions/${encodeURIComponent(entityType)}/${encodeURIComponent(entityId)}/${suffix}`,
    { signal: options.signal, skipCache: options.skipCache, timeoutMs: 30000 },
  );
}

/**
 * Record activity across the curriculum authoring tables: what was created,
 * edited or archived, newest first. Derived from created_at/updated_at/
 * deleted_at on the records themselves - Quality owns no log of its own.
 */
export function fetchCurriculumAuditTrail(
  options: {
    days?: number;
    limit?: number;
    /** 1-based. Past the last page is answered with the last page. */
    page?: number;
    entity?: string;
    action?: string;
    search?: string;
    /** Email of one person, to see only their changes. */
    actor?: string;
    /** How the save arrived: `auto-save`, `import`, `recalculation`, ... */
    source?: string;
    /** `user` for people only, `system` for what the LMS did on its own. */
    actorType?: string;
    /** Narrow to one branch of the curriculum, as it was at the time. */
    scope?: 'programme' | 'cohort' | 'group' | 'module';
    scopeId?: string;
    /**
     * Which workspace's saves to read; '' or omitted reads every one.
     *
     * One revision log now holds the whole LMS, so the scoped door has to
     * ask for its own workspace -- otherwise /curriculum/audit-trail would
     * start showing learner and coaching saves the day those were wired in.
     */
    workspace?: string;
    signal?: AbortSignal;
    skipCache?: boolean;
    revalidate?: boolean;
  } = {},
): Promise<CurriculumAuditTrail> {
  const query = new URLSearchParams();
  if (options.days) query.set('days', String(options.days));
  if (options.limit) query.set('limit', String(options.limit));
  if (options.entity && options.entity !== 'all') query.set('entity', options.entity);
  if (options.action && options.action !== 'all') query.set('action', options.action);
  if (options.search) query.set('search', options.search);
  if (options.actor) query.set('actor', options.actor);
  if (options.source && options.source !== 'all') query.set('source', options.source);
  if (options.actorType && options.actorType !== 'all') query.set('actorType', options.actorType);
  if (options.workspace) query.set('workspace', options.workspace);
  if (options.page && options.page > 1) query.set('page', String(options.page));
  if (options.scope && options.scopeId) {
    query.set('scope', options.scope);
    query.set('scopeId', options.scopeId);
  }
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return fetchJson<CurriculumAuditTrail>(`/curriculum/quality/audit-trail/${suffix}`, {
    signal: options.signal,
    skipCache: options.skipCache,
    revalidate: options.revalidate,
    timeoutMs: 30000,
  });
}

/**
 * Everyone who used Curriculum Studio in the window, one row each.
 *
 * Cached briefly like other read-only curriculum data. The page can request a
 * network revalidation when the user presses Refresh without discarding the
 * response for the next visit.
 */
export function fetchActivityPeople(
  options: {
    days?: number;
    search?: string;
    workspace?: string;
    /** One role, or `__none__` for the people with no role recorded. */
    role?: string;
    /** 1-based. Past the last page is answered with the last page. */
    page?: number;
    pageSize?: number;
    signal?: AbortSignal;
    skipCache?: boolean;
    revalidate?: boolean;
  } = {},
): Promise<CurriculumActivityPeople> {
  const query = new URLSearchParams();
  if (options.days) query.set('days', String(options.days));
  if (options.search) query.set('search', options.search);
  if (options.workspace) query.set('workspace', options.workspace);
  if (options.role) query.set('role', options.role);
  if (options.page && options.page > 1) query.set('page', String(options.page));
  if (options.pageSize) query.set('pageSize', String(options.pageSize));
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return fetchJson<CurriculumActivityPeople>(`/activity/people/${suffix}`, {
    signal: options.signal,
    skipCache: options.skipCache,
    revalidate: options.revalidate,
    timeoutMs: 30000,
  });
}

/** One person: their visits, the pages in each, and what they did there. */
export function fetchPersonActivity(
  email: string,
  options: { days?: number; workspace?: string; signal?: AbortSignal; skipCache?: boolean; revalidate?: boolean } = {},
): Promise<CurriculumPersonActivity> {
  const query = new URLSearchParams();
  if (options.days) query.set('days', String(options.days));
  if (options.workspace) query.set('workspace', options.workspace);
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return fetchJson<CurriculumPersonActivity>(
    `/activity/people/${encodeURIComponent(email)}/${suffix}`,
    {
      signal: options.signal,
      skipCache: options.skipCache,
      revalidate: options.revalidate,
      timeoutMs: 30000,
    },
  );
}

function postJson<T>(path: string, body: unknown): Promise<T> {
  return fetchJson<T>(path, { method: 'POST', body: JSON.stringify(body) });
}

function patchJson<T>(path: string, body: unknown): Promise<T> {
  return fetchJson<T>(path, { method: 'PATCH', body: JSON.stringify(body) });
}

function deleteJson<T>(path: string): Promise<T> {
  return fetchJson<T>(path, { method: 'DELETE' });
}

export type CurriculumProgrammeInput = Partial<Pick<CurriculumProgramme, 'name' | 'standard' | 'level' | 'owner' | 'color' | 'description' | 'structureType' | 'ksbProfileSourceId' | 'status'>> & {
  /**
   * Off-the-job hours target. A string is accepted because form inputs produce
   * one and the backend parses either; null clears a stored target, and omitting
   * the key leaves it untouched.
   */
  requiredOtjh?: number | string | null;
};
export type CurriculumModuleInput = Partial<Pick<CurriculumModule, 'name' | 'weeks' | 'color' | 'notes' | 'coverImage'>> & {
  programmeId?: string;
  programmeName?: string;
  programme?: string;
  ksbProfileSourceId?: string;
  cohortId?: string;
  cohortName?: string;
  cohort?: string;
  groupId?: string;
  groupName?: string;
  group?: string;
  startDate?: string;
  endDate?: string;
  tutor?: string;
  coach?: string;
  weeklySchedule?: CurriculumWeeklySession[];
  weekDays?: string;
  startTime?: string;
  endTime?: string;
  ksbMappings?: unknown[];
  // Both are already honoured by PATCH /curriculum/modules/<id>/ — declared here
  // so the module workspace can send them without casting.
  status?: string;
  /** Calendar sessions (weeks x delivery days per week). See `CurriculumModule`. */
  sessionsNumber?: number;
  /** Authored weeks. Sent alongside `sessionsNumber`, never instead of it. */
  weeks?: number;
  /**
   * Book a slot the tutor already holds. Without it the save is refused with a
   * 409 carrying `tutorConflicts` — see `isTutorConflictError`.
   */
  allowTutorConflict?: boolean;
};
export type CurriculumComponentInput = Partial<Omit<CurriculumComponent, 'lastEdited'>>;
export type CurriculumCohortInput = { id?: string; cohortId?: string; name?: string; programme?: string; programmeId?: string; startDate?: string; endDate?: string; durationMonths?: number; epaMonths?: number | null; /** null clears the manual apprenticeship end date and restores the calculated one. */ apprenticeshipEndOverride?: string | null; color?: string; moduleName?: string; sessionsNumber?: number; /** The ticked ids -- the holidays this cohort's module sessions must skip. */ holidayIds?: Array<string | number>; /** The unticked ids, from the same in-period list. Sent alongside `holidayIds` so an explicit empty array ("Select all") is never mistaken for the key being omitted. */ excludedHolidayIds?: Array<string | number> };
export type CurriculumGroupInput = { id?: string; groupId?: string; name?: string; cohortId?: string; programmeId?: string; tutor?: string; coach?: string; color?: string; weekDays?: string; startTime?: string; endTime?: string; startDate?: string; endDate?: string; moduleName?: string; sessionsNumber?: number; /** Honoured by PATCH /curriculum/groups/<id>/ only. */ status?: string; /** See CurriculumModuleInput.allowTutorConflict. */ allowTutorConflict?: boolean };
export type CurriculumSessionInput = Partial<Pick<CurriculumSession, 'date' | 'startTime' | 'endTime' | 'tutor'>>;
export type CurriculumStaffingInput = { groupId?: string; tutor?: string; coach?: string; /** See CurriculumModuleInput.allowTutorConflict. */ allowTutorConflict?: boolean };
export type CurriculumModuleAttachmentInput = {
  moduleName: string;
  programmeId?: string;
  cohortId?: string;
  groupId?: string;
  catalogueId?: string | number;
  color?: string;
  /** See CurriculumModule.coverImage. */
  coverImage?: string;
  startDate?: string;
  endDate?: string;
  coach?: string;
  tutor?: string;
  weeklySchedule?: CurriculumWeeklySession[];
  weekDays?: string;
  startTime?: string;
  endTime?: string;
  sessionsNumber?: number;
  weeks?: number;
  notes?: string;
  holidays?: unknown[];
  linkedHolidays?: unknown[];
  /** See CurriculumModuleInput.allowTutorConflict. */
  allowTutorConflict?: boolean;
};
export type FreeProgrammeComponentInput = Partial<FreeProgrammeComponent> & {
  id: string;
  type: string;
  settings?: Record<string, unknown>;
};
export type FreeProgrammeModuleInput = {
  id?: string;
  courseId?: string;
  weekId?: string;
  weekNumber?: number;
  weekTitle?: string;
  courseName?: string;
  title: string;
  description?: string;
  coverImageUrl?: string;
  components?: FreeProgrammeComponentInput[];
};

export function createCurriculumProgramme(input: CurriculumProgrammeInput) {
  return postJson<{ created: boolean; programme: CurriculumProgramme }>('/curriculum/programmes/', input);
}

/**
 * Save the order the programme cards were dragged into. `order` is the ids top-left
 * first; the server renumbers exactly those programmes from 1 upward.
 */
export function saveCurriculumProgrammeOrder(order: string[]) {
  return postJson<{ saved: boolean; order: string[] }>('/curriculum/programmes/reorder/', { order });
}

export function updateCurriculumProgramme(id: string, input: CurriculumProgrammeInput) {
  return patchJson<{ updated: boolean; programme: CurriculumProgramme; revision?: string }>(`/curriculum/programmes/${encodeURIComponent(id)}/`, input);
}

/**
 * What a programme DELETE answers with — an archive, or a permanent removal.
 *
 * `removed` counts the curriculum rows a permanent delete took with it, keyed by
 * kind; `learners` is how many learner plans referenced it and were left alone.
 * See curriculum_api.views.curriculum_programme_detail.
 */
export type CurriculumProgrammeDeleteResult = {
  deleted: boolean;
  permanent: boolean;
  archived?: boolean;
  reason?: string;
  message?: string;
  id: string;
  removed?: Record<string, number>;
  learners?: number;
};

/** What still hangs off a programme, when a permanent delete is refused. */
export type CurriculumProgrammeDependencyReport = {
  blocked: boolean;
  counts: Record<string, number>;
  total: number;
  programme?: {
    id?: string;
    sourceId?: string;
    name?: string;
  };
  cleanupStartStep?: string;
  message?: string;
};

/**
 * A refused delete. `blockers` names what still depends on the programme —
 * learner delivery, which is never deleted with a programme — so the caller can
 * say what has to be cleared first rather than only that it failed.
 */
export type CurriculumProgrammeDependencyError = {
  error?: string;
  reason?: 'programme-has-dependencies' | 'programme-has-learner-delivery' | 'programme-not-archived' | string;
  deleted?: false;
  permanent?: false;
  id?: string;
  blockers?: Record<string, number>;
  dependencyReport?: CurriculumProgrammeDependencyReport;
  message?: string;
};

export function deleteCurriculumProgramme(id: string, options: { permanent?: boolean } = {}) {
  const suffix = options.permanent ? '?permanent=true' : '';
  return fetchJson<CurriculumProgrammeDeleteResult>(
    `/curriculum/programmes/${encodeURIComponent(id)}/${suffix}`,
    { method: 'DELETE', timeoutMs: 60000 },
  );
}

export const archiveCurriculumProgramme = (id: string) => deleteCurriculumProgramme(id);
export const permanentlyDeleteCurriculumProgramme = (id: string) => deleteCurriculumProgramme(id, { permanent: true });

export type CurriculumProgrammeRestoreResult = {
  restored: boolean;
  id: string;
  programme?: CurriculumProgramme | null;
  details?: {
    programmeRestored?: boolean;
    cohorts?: number;
    groups?: number;
    modules?: number;
    weeks?: number;
    components?: number;
    ksbMappings?: number;
  };
  message?: string;
};

export function restoreCurriculumProgramme(id: string) {
  return postJson<CurriculumProgrammeRestoreResult>(
    `/curriculum/programmes/${encodeURIComponent(id)}/restore/`,
    {},
  );
}

export function createCurriculumCohort(input: CurriculumCohortInput) {
  return postJson<{ created: boolean; cohort: CurriculumCohort }>('/curriculum/cohorts/', input);
}

export function createProgrammeCohort(programmeId: string, input: Omit<CurriculumCohortInput, 'programme'>) {
  return postJson<{ created: boolean; cohort: CurriculumCohort }>(`/curriculum/programmes/${encodeURIComponent(programmeId)}/cohorts/`, input);
}

export function updateCurriculumCohort(id: string, input: CurriculumCohortInput) {
  return patchJson<{ updated: boolean; id: string; revision?: string }>(`/curriculum/cohorts/${encodeURIComponent(id)}/`, input);
}

export function archiveCurriculumCohort(id: string) {
  return deleteJson(`/curriculum/cohorts/${encodeURIComponent(id)}/`);
}

export function createCurriculumGroup(input: CurriculumGroupInput) {
  return postJson<{ created: boolean; group: CurriculumGroup }>('/curriculum/groups/', input);
}

export function createCohortGroup(cohortId: string, input: Omit<CurriculumGroupInput, 'cohortId'>) {
  return postJson<{ created: boolean; group: CurriculumGroup }>(`/curriculum/cohorts/${encodeURIComponent(cohortId)}/groups/`, input);
}

export function updateCurriculumGroup(id: string, input: CurriculumGroupInput) {
  return patchJson<{ updated: boolean; id: string; revision?: string; teamsCalendarsToUpdate?: StaleTeamsCalendar[] }>(`/curriculum/groups/${encodeURIComponent(id)}/`, input);
}

export function archiveCurriculumGroup(id: string) {
  return deleteJson(`/curriculum/groups/${encodeURIComponent(id)}/`);
}

// ============================================================================
// The cohort/group archive.
//
// `archiveCurriculumCohort`/`archiveCurriculumGroup` above soft-delete: the row
// keeps its place in the database and only leaves the active lists. These are
// the two things that were missing on the other side of that — reading what is
// archived, and either putting one back or removing it for good — and they
// mirror the programme archive (`restoreCurriculumProgramme` /
// `permanentlyDeleteCurriculumProgramme`) call for call.
// ============================================================================

/** How a record came to be archived, as the archive list reports it. */
export interface CurriculumArchiveStamp {
  /** When the archive happened, ISO. */
  archivedAt: string;
  /**
   * The write handler that archived it — 'cohort-delete', 'group-delete',
   * 'programme-delete'. Never a person: no authoring table records who.
   */
  archivedBy: string;
  /**
   * The parent whose own archive took this row down, when that is what
   * happened. Restoring that parent is what brings this record back.
   */
  archivedViaParent: string;
}

export interface CurriculumArchivedCohort extends CurriculumArchiveStamp {
  id: string;
  name: string;
  programme: string;
  programmeId: string;
  startDate: string;
  endDate: string;
  practicalEndDate: string;
  apprenticeshipEndDate: string;
  epaMonths: number | null;
  durationMonths: number;
  color: string;
  status: 'archived';
  /** Archived groups under it — what a restore returns and a delete removes. */
  groups: number;
  /** Live learners still placed here. Any at all blocks a permanent delete. */
  learners: number;
}

export interface CurriculumArchivedGroup extends CurriculumArchiveStamp {
  id: string;
  name: string;
  cohortId: string;
  cohort: string;
  programmeId: string;
  programme: string;
  coach: string;
  weekDays: string;
  startTime: string;
  endTime: string;
  schedule: string;
  color: string;
  status: 'archived';
  learners: number;
  /**
   * Its cohort is archived too, so this group has nothing to come back to. The
   * restore endpoint refuses it and says to restore the cohort instead, which
   * brings back every group archived with it.
   */
  cohortArchived: boolean;
}

export interface CurriculumArchivedModule extends CurriculumArchiveStamp {
  id: string;
  catalogueId: string;
  title: string;
  programmeId: string;
  programme: string;
  cohortId: string;
  cohort: string;
  groupId: string;
  group: string;
  tutor: string;
  /** What a restore brings back, and a permanent delete destroys. */
  weeks: number;
  sessions: number;
  components: number;
  status: 'archived';
  /**
   * Its programme is archived too, so the module has nothing to come back to:
   * every catalogue list is scoped by programme, and a module restored under an
   * archived one returns invisible. Restore the programme instead.
   */
  programmeArchived: boolean;
}

/**
 * `revalidate` on both reads below: the archive is opened in order to act on it,
 * and the very next thing the reader does is restore or delete something, so a
 * cached list would keep showing the row that was just dealt with. Not
 * `skipCache` — these two endpoints read the tables directly rather than through
 * the cached overview payload, so there is no server-side build to force.
 */
// A finite wait, so a request that never answers ends in the archive's error
// and Retry rather than in a skeleton that never leaves.
export const ARCHIVE_READ_TIMEOUT_MS = 90_000;

export function fetchArchivedCurriculumCohorts(signal?: AbortSignal): Promise<CurriculumArchivedCohort[]> {
  return fetchCollection<CurriculumArchivedCohort>('/curriculum/cohorts/archived/', { signal, revalidate: true, timeoutMs: ARCHIVE_READ_TIMEOUT_MS });
}

export function fetchArchivedCurriculumGroups(signal?: AbortSignal): Promise<CurriculumArchivedGroup[]> {
  return fetchCollection<CurriculumArchivedGroup>('/curriculum/groups/archived/', { signal, revalidate: true, timeoutMs: ARCHIVE_READ_TIMEOUT_MS });
}

export function fetchArchivedCurriculumModules(signal?: AbortSignal): Promise<CurriculumArchivedModule[]> {
  return fetchCollection<CurriculumArchivedModule>('/curriculum/modules/archived/', { signal, revalidate: true, timeoutMs: ARCHIVE_READ_TIMEOUT_MS });
}

/** An archived programme: the programme list's shape plus its archive stamp. */
export type CurriculumArchivedProgramme = CurriculumProgramme & CurriculumArchiveStamp;

export type CurriculumArchiveKind = 'programme' | 'cohort' | 'group' | 'module';

/** One archived record, in the shape its own archive list returns. */
export type CurriculumArchiveRecord =
  | { kind: 'programme'; key: string; record: CurriculumArchivedProgramme }
  | { kind: 'cohort'; key: string; record: CurriculumArchivedCohort }
  | { kind: 'group'; key: string; record: CurriculumArchivedGroup }
  | { kind: 'module'; key: string; record: CurriculumArchivedModule };

/**
 * A record on the archive page. `children` are the records its own archive took
 * down with it (only when the page is not filtered to one type);
 * `archivedWith` is the key of the listed parent that took this one down.
 */
export type CurriculumArchiveEntry = CurriculumArchiveRecord & {
  archivedWith?: string | null;
  children: Array<CurriculumArchiveRecord & { archivedWith?: string | null }>;
};

export interface CurriculumArchivePage {
  page: number;
  pageSize: number;
  pages: number;
  /** Entries on this filter -- what the pager counts. */
  total: number;
  /** Records per type under the programme filter and search, for the tabs. */
  counts: Record<CurriculumArchiveKind, number>;
  /** Every record in the archive, whatever the filters. */
  archiveTotal: number;
  /** Records per type in the whole archive, whatever the filters. */
  totals: Record<CurriculumArchiveKind, number>;
  programmes: Array<{ value: string; label: string }>;
  results: CurriculumArchiveEntry[];
}

/**
 * GET /curriculum/archive/ -- one page of the whole archive, built on the
 * server from a single set of reads. Revalidated for the reason the three lists
 * above are: the next thing the reader does is restore or delete.
 */
export function fetchCurriculumArchivePage(
  params: { page: number; pageSize: number; type?: string; programme?: string; q?: string },
  signal?: AbortSignal,
): Promise<CurriculumArchivePage> {
  const query = new URLSearchParams({ page: String(params.page), pageSize: String(params.pageSize) });
  if (params.type) query.set('type', params.type);
  if (params.programme) query.set('programme', params.programme);
  if (params.q?.trim()) query.set('q', params.q.trim());
  return fetchJson<CurriculumArchivePage>(`/curriculum/archive/?${query.toString()}`, {
    signal, revalidate: true, timeoutMs: ARCHIVE_READ_TIMEOUT_MS,
  });
}

/** One archived record by key ('module:ID'), wherever it sits; null when it is not archived. */
export async function fetchCurriculumArchiveRecord(key: string, signal?: AbortSignal): Promise<CurriculumArchiveRecord | null> {
  const payload = await fetchJson<{ result: CurriculumArchiveRecord | null }>(
    `/curriculum/archive/?key=${encodeURIComponent(key)}`,
    { signal, revalidate: true, timeoutMs: ARCHIVE_READ_TIMEOUT_MS },
  );
  return payload.result;
}

/**
 * A component inside an archived module's week, as the archive reads it back.
 *
 * Deliberately a subset of the builder's own component shape: this is a read,
 * and everything an editor needs (settings, sources, completion rules) is not
 * what a reader deciding whether to restore the module is looking at. The
 * backend returns the full component either way -- narrowing it here is what
 * stops the archive growing a second, drifting copy of the authoring model.
 */
export interface CurriculumArchivedModuleComponent {
  id: string;
  type: string;
  title: string;
  description: string;
  expectedOtjh: number;
  points: number;
  reflectionRequired: boolean;
  workplaceEvidenceRequired: boolean;
  tutorValidationRequired: boolean;
  coachValidationRequired: boolean;
  ksbMappings?: Array<{ code?: string }>;
  settings?: Record<string, unknown>;
}

export interface CurriculumArchivedModuleWeek {
  id: string;
  weekNumber: number;
  title: string;
  summary: string;
  /** The planned delivery date, when the module had a schedule to plan from. */
  sessionDate?: string;
  sessionDay?: string;
  sessionStartTime?: string;
  components: CurriculumArchivedModuleComponent[];
  ksbMappings?: Array<{ code?: string }>;
}

export interface CurriculumArchivedModuleStructure {
  catalogueId: string;
  title: string;
  description: string;
  programmeName: string;
  cohort: string;
  group: string;
  tutor: string;
  status: string;
  weeks: number;
  sessionsNumber: number;
  totalOtjh: number;
  lessonCount: number;
  quizCount: number;
  weekStructure: CurriculumArchivedModuleWeek[];
}

/**
 * An archived module's weeks and the components inside them.
 *
 * Its own endpoint rather than `/structure/`, because that one filters its
 * children through the active-row predicates: an archived module read through it
 * comes back with an empty week list, since the archive cascade soft-deleted
 * every week and component under it. This one returns the set a restore would
 * bring back. Read-only -- there is no PATCH beside it.
 *
 * `revalidate` by default, matching the three archived-list reads above: the
 * archive is opened in order to act on it, so this tab's cache must not answer
 * -- but this endpoint reads its tables directly rather than through the cached
 * overview payload, so there is no server-side build for `skipCache` to force.
 * A caller that has just written may still ask for one.
 */
export function fetchArchivedModuleStructure(
  id: string,
  signal?: AbortSignal,
  options: { skipCache?: boolean } = {},
): Promise<CurriculumArchivedModuleStructure> {
  return fetchJson<CurriculumArchivedModuleStructure>(
    `/curriculum/modules/${encodeURIComponent(id)}/archived-structure/`,
    { signal, skipCache: options.skipCache, revalidate: !options.skipCache, timeoutMs: 30000 },
  );
}

export type CurriculumRestoreResult = {
  restored: boolean;
  id: string;
  /** Only the cohort restore reports one: the groups it brought back with it. */
  details?: { groups?: number };
  message?: string;
};

/**
 * Restoring a cohort brings back the groups its own archive took down; a group
 * archived on its own keeps its own state, exactly as a programme restore works.
 *
 * Refused with 409 when the parent is still archived — restore that instead —
 * and the refusal carries the sentence to show.
 */
export function restoreCurriculumCohort(id: string) {
  return postJson<CurriculumRestoreResult>(`/curriculum/cohorts/${encodeURIComponent(id)}/restore/`, {});
}

/**
 * Brings back the weeks, components and KSB mappings archived with the module --
 * matched on the marker its own archive stamped, so a component deleted by hand
 * beforehand stays deleted. Refused with 409 while its programme is archived.
 *
 * Quizzes are the one thing it cannot return: archiving a module sends the
 * quizzes only it owned to the Quiz Archive, which is the Quiz Workspace's own
 * state. The response says so in `message`.
 */
export function restoreCurriculumModule(id: string) {
  return postJson<CurriculumRestoreResult>(`/curriculum/modules/${encodeURIComponent(id)}/restore/`, {});
}

export function restoreCurriculumGroup(id: string) {
  return postJson<CurriculumRestoreResult>(`/curriculum/groups/${encodeURIComponent(id)}/restore/`, {});
}

export type CurriculumPermanentDeleteResult = {
  deleted: boolean;
  permanent: boolean;
  id: string;
  /** Rows the delete removed, keyed by table. */
  removed?: Record<string, number>;
  message?: string;
};

/**
 * Only an already-archived record can be removed for good, and only while
 * nothing live still depends on it: learners placed in it, or — for a cohort —
 * a group that is not archived. Either refusal comes back as a 409 naming what
 * to clear first. Module content is never deleted with the delivery record.
 */
export function permanentlyDeleteCurriculumCohort(id: string) {
  return deleteJson<CurriculumPermanentDeleteResult>(`/curriculum/cohorts/${encodeURIComponent(id)}/?permanent=true`);
}

export function permanentlyDeleteCurriculumGroup(id: string) {
  return deleteJson<CurriculumPermanentDeleteResult>(`/curriculum/groups/${encodeURIComponent(id)}/?permanent=true`);
}

/**
 * Unlike the cohort and group deletes above, this one does destroy authoring:
 * the module row and every week, component, KSB mapping, completion rule and
 * advanced detail under it. There is nothing left to restore afterwards.
 */
export function permanentlyDeleteCurriculumModule(id: string) {
  return deleteJson<CurriculumPermanentDeleteResult>(`/curriculum/modules/${encodeURIComponent(id)}/?permanent=true`);
}

export function fetchFreeProgrammeModules(programmeId: string, signal?: AbortSignal): Promise<FreeProgrammeModule[]> {
  return fetchCollection<FreeProgrammeModule>(`/curriculum/free-programmes/${encodeURIComponent(programmeId)}/modules/`, { signal });
}

export function saveFreeProgrammeModules(programmeId: string, input: { programmeName?: string; modules: FreeProgrammeModuleInput[] }) {
  return patchJson<{ saved: boolean; programmeId: string; modules: FreeProgrammeModule[] }>(`/curriculum/free-programmes/${encodeURIComponent(programmeId)}/modules/`, input);
}

export interface ConvertFreeCourseInput {
  courseId: string;
  mode: 'clone' | 'move';
  /** The EXISTING group the course is injected into as a new module. */
  groupId: string;
}

export interface ConvertFreeCourseResult {
  mode: 'clone' | 'move';
  programmeId: string;
  programmeName: string;
  cohortId: string;
  groupId: string;
  moduleCatalogueId: string;
}

/**
 * Inject one free course as a new module into an EXISTING programme group.
 * `clone` leaves the free course in place; `move` also deletes it and removes it
 * from any learner it was assigned to. Returns the ids so the caller can
 * deep-link into the Module Builder to add KSBs/hours.
 */
export function injectFreeCourseIntoGroup(programmeId: string, input: ConvertFreeCourseInput) {
  return postJson<ConvertFreeCourseResult>(`/curriculum/free-programmes/${encodeURIComponent(programmeId)}/convert/`, input);
}

export interface ImportModuleInput {
  moduleCatalogueId: string;
  /** 'clone' keeps the module in its programme; 'move' also archives it. */
  mode: 'clone' | 'move';
}

export interface ImportModuleResult {
  mode: 'clone' | 'move';
  freeCourseId: string;
  courseName: string;
  moduleCatalogueId: string;
  /** The full, updated free-course module list, so the caller can refresh. */
  modules: FreeProgrammeModule[];
}

/**
 * Convert an existing programme module into a NEW free course (appended to the
 * catalogue). `clone` leaves the module live in its programme; `move` also
 * archives the source module. Returns the updated free-course module list.
 */
export function importModuleToFreeCourses(programmeId: string, input: ImportModuleInput) {
  return postJson<ImportModuleResult>(`/curriculum/free-programmes/${encodeURIComponent(programmeId)}/import-module/`, input);
}

export function createGroupModule(groupId: string, input: CurriculumModuleAttachmentInput) {
  return postJson(`/curriculum/groups/${encodeURIComponent(groupId)}/modules/`, input);
}

export function fetchGroupModules(groupId: string, signal?: AbortSignal): Promise<CurriculumModule[]> {
  return fetchCollection<CurriculumModule>(`/curriculum/groups/${encodeURIComponent(groupId)}/modules/`, { signal });
}

/**
 * The canonical practical-end / EPA / apprenticeship-end calculation, run by the
 * backend. Editors call this while the user types rather than reimplementing the
 * date rules client-side, so the preview and the saved value cannot disagree.
 */
export function previewCohortEndDate(input: {
  startDate?: string;
  durationMonths?: number;
  /** An authored practical end date. Overrides the duration rule for this preview. */
  practicalEndDate?: string | null;
  epaMonths?: number | null;
  apprenticeshipEndDate?: string;
  apprenticeshipEndOverride?: string | null;
  /**
   * The holidays applied to this cohort. Days these take out of the cohort's own
   * period push the practical end date out, which carries the apprenticeship end
   * date with it via the EPA rule.
   */
  holidays?: { label?: string; startDate: string; endDate: string }[];
}) {
  return postJson<CurriculumCohortEndDatePreview>('/curriculum/preview/cohort-end-date/', input);
}

export function previewModuleSessionPlan(input: { startDate?: string; numberOfSessions?: number; sessionsNumber?: number; weeklySchedule?: CurriculumWeeklySession[]; weekDays?: string | string[]; deliveryDays?: string | string[]; holidays?: unknown[] }) {
  return postJson<CurriculumSessionPlanPreview>('/curriculum/preview/module-session-plan/', input);
}

/** The slot a tutor is being asked about. Matches the module's own schedule fields. */
export interface CurriculumTutorAvailabilityInput {
  startDate?: string;
  sessionsNumber?: number | string;
  weeklySchedule?: CurriculumWeeklySession[];
  weekDays?: string;
  startTime?: string;
  endTime?: string;
  /** Ask about one person. Omit to get a verdict for every tutor in one call. */
  tutor?: string;
  /** The module being edited, so it is not reported as blocking its own slot. */
  moduleCatalogueId?: string;
  /**
   * The parent cohort, so the slot is dated the way the calendar dates it — its
   * ticked holidays move a session onto the next delivery day, and a clash is
   * about the day the session actually runs on.
   */
  cohortId?: string;
  /**
   * The ticked holidays themselves, for a form previewing a cohort choice that
   * is not saved yet. Sent, they win over what `cohortId` would have read back.
   */
  holidays?: CurriculumHoliday[];
}

export interface CurriculumTutorAvailabilityVerdict {
  tutor: string;
  available: boolean;
  conflicts: CurriculumTutorConflict[];
  /** The same sentence the save would have refused with, or '' when free. */
  message: string;
}

export interface CurriculumTutorAvailabilitySlot {
  sessionDates: string[];
  startTime: string;
  endTime: string;
  /** False when the slot books nothing, so "everyone is free" is not an all-clear. */
  bookable: boolean;
}

export type CurriculumTutorAvailability = CurriculumTutorAvailabilitySlot & CurriculumTutorAvailabilityVerdict;

export type CurriculumTutorAvailabilityRoster = CurriculumTutorAvailabilitySlot & {
  results: CurriculumTutorAvailabilityVerdict[];
  availableCount: number;
  busyCount: number;
};

/**
 * Whether a tutor is already teaching in a proposed slot — asked *before* saving.
 *
 * Runs the same rule the save enforces, so a screen can warn while the tutor is
 * still being chosen instead of letting the person fill in a whole form and
 * discover the clash from a refused save.
 */
export function previewTutorAvailability(input: CurriculumTutorAvailabilityInput & { tutor: string }) {
  return postJson<CurriculumTutorAvailability>('/curriculum/preview/tutor-availability/', input);
}

/** Every tutor's verdict for one slot, for annotating a tutor picker. */
export function previewTutorAvailabilityRoster(input: CurriculumTutorAvailabilityInput) {
  return postJson<CurriculumTutorAvailabilityRoster>('/curriculum/preview/tutor-availability/', input);
}

export function createCurriculumModule(input: CurriculumModuleInput) {
  return postJson<{ created: boolean; module: CurriculumModule }>('/curriculum/modules/', input);
}

/**
 * A module whose Teams calendar a schedule edit has just left behind. The saves
 * that move dates name these back so the drawer can say so on the spot: the
 * calendar itself is only ever written from the Teams Meetings page, so nothing
 * about a date change reaches Teams on its own.
 */
export interface StaleTeamsCalendar {
  moduleCatalogueId: string;
  moduleName: string;
}

export function updateCurriculumModule(id: string, input: CurriculumModuleInput) {
  return patchJson<{ updated: boolean; module: CurriculumModule; revision?: string; teamsCalendarsToUpdate?: StaleTeamsCalendar[] }>(`/curriculum/modules/${encodeURIComponent(id)}/`, input);
}

/**
 * Tell a module's tutor they have been put on it.
 *
 * One mail covers every id in the list, because one drawer save can attach the
 * module to several groups at once -- one delivery per group, all carrying the
 * same tutor -- and that is one decision, not three. Every id must therefore
 * name a module with the same tutor; the backend refuses a mixed list rather
 * than guessing who to write to.
 *
 * Only ever called because somebody ticked the box: assigning a tutor does not
 * mail them on its own (see curriculum_api/tutor_notifications.py).
 */
export interface TutorAssignmentEmailStatus {
  tutor: { name: string; hasEmail: boolean } | null;
  /** How many deliveries were asked about. */
  total: number;
  /** How many of them this tutor has actually been emailed about. */
  emailed: number;
  /** The most recent successful send across them, ISO, or null. */
  lastSentAt: string | null;
  deliveries: { moduleId: string; emailed: boolean; lastSent: { status: string | null; at: string | null } | null }[];
}

/**
 * Whether this tutor has already been told about these deliveries.
 *
 * The answer comes from the assignment ledger, which is keyed on (tutor,
 * module) -- so it is per tutor by construction, and a module whose tutor
 * changed reports nothing for the new name. `tutorName` asks about a tutor the
 * module does not carry yet, which is what the drawer needs while somebody is
 * still choosing one.
 *
 * A read, so it must not disturb the caches a write would: see `isReadOnlyPost`
 * for the same idea on the POST side.
 */
export function fetchTutorAssignmentEmailStatus(moduleIds: string[], tutorName: string, signal?: AbortSignal) {
  const params = new URLSearchParams({ moduleIds: moduleIds.join(',') });
  if (tutorName) params.set('tutor', tutorName);
  return tutorEmailJson<TutorAssignmentEmailStatus>(`${tutorEmailUrl()}?${params.toString()}`, { signal });
}

export function sendTutorAssignmentEmail(moduleIds: string[]) {
  return tutorEmailJson<{ sent: boolean; tutor: string; modules: number }>(tutorEmailUrl(), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ moduleIds }),
  });
}

function tutorEmailUrl() {
  return `${API_BASE_URL}/curriculum/modules/tutor-email/`;
}

/**
 * The two tutor-email calls go through `coachFetch`, not this module's own
 * `fetchJson`, and that is not an oversight.
 *
 * Almost every curriculum write is `@csrf_exempt` (61 of them in views.py), so
 * `fetchJson` never had to send a CSRF token. `tutor_notifications` is not
 * exempt -- it is a view that sends real mail to a real person, and it keeps the
 * protection -- so a POST from `fetchJson` is rejected by Django's CSRF
 * middleware before the view runs, which surfaced as a bare "could not be sent".
 * `coachFetch` fetches and attaches the token, which is exactly why the module
 * workspace's own "Email tutor" button has always used it.
 *
 * The right fix is to send the token, never to exempt the view.
 */
async function tutorEmailJson<T>(url: string, init?: globalThis.RequestInit): Promise<T> {
  const response = await coachFetch(url, init);
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    // The server's own sentence when it has one -- "Amira has no email address
    // in the staff directory" tells the reader what to do; "502" does not.
    throw new Error(
      (data && typeof data.error === 'string' && data.error)
      || `The tutor email request failed (${response.status}).`,
    );
  }
  return data as T;
}

export function updateCurriculumModuleCover(id: string, coverImage: string) {
  return patchJson<{ updated: boolean; coverImage: string }>(`/curriculum/modules/${encodeURIComponent(id)}/`, { coverImage });
}

export function archiveCurriculumModule(id: string) {
  return deleteJson<{ archived: boolean; id: string }>(`/curriculum/modules/${encodeURIComponent(id)}/`);
}

export function createCurriculumComponent(input: CurriculumComponentInput) {
  return postJson<{ created: boolean; component: CurriculumComponent }>('/curriculum/components/', input);
}

export function updateCurriculumComponent(id: string, input: CurriculumComponentInput) {
  return patchJson<{ updated: boolean; component: CurriculumComponent }>(`/curriculum/components/${encodeURIComponent(id)}/`, input);
}

export function deleteCurriculumComponent(id: string) {
  return deleteJson<{ deleted: boolean; id: string }>(`/curriculum/components/${encodeURIComponent(id)}/`);
}

export function createCurriculumSession(input: CurriculumSessionInput) {
  return postJson('/curriculum/sessions/', input);
}

export function updateCurriculumSession(id: string, input: CurriculumSessionInput) {
  return patchJson(`/curriculum/sessions/${encodeURIComponent(id)}/`, input);
}

export function archiveCurriculumSession(id: string) {
  return deleteJson(`/curriculum/sessions/${encodeURIComponent(id)}/`);
}

export function createStaffingAssignment(input: CurriculumStaffingInput) {
  return postJson('/curriculum/staffing/', input);
}

export function updateStaffingAssignment(id: string, input: CurriculumStaffingInput) {
  return patchJson(`/curriculum/staffing/${encodeURIComponent(id)}/`, input);
}

export function deleteStaffingAssignment(id: string) {
  return deleteJson(`/curriculum/staffing/${encodeURIComponent(id)}/`);
}

// Holidays: the authored half of the calendar is written through these three.
// A GOV.UK bank holiday is not — the endpoint answers 405 for one, because it
// changes when GOV.UK changes it and not before. See `refreshEnglandHolidays`.

export function createCurriculumHoliday(input: CurriculumHolidayInput) {
  return postJson('/curriculum/holidays/', input);
}

export function updateCurriculumHoliday(id: string | number, input: CurriculumHolidayInput) {
  return patchJson(`/curriculum/holidays/${encodeURIComponent(String(id))}/`, input);
}

export function archiveCurriculumHoliday(id: string | number) {
  return deleteJson(`/curriculum/holidays/${encodeURIComponent(String(id))}/`);
}

/**
 * Every recent check against GOV.UK, newest first, with the current state.
 *
 * The England Holidays page reads this to show what the site changed and when it
 * was last looked at. Deliberately a separate call from the holidays themselves:
 * it is a log, it is only read on that one page, and it must never be cached
 * alongside the dates.
 *
 * `revalidate` rather than `skipCache` by default: the log has to be current,
 * which means not answering from this tab's cache -- but it is read straight
 * from its table, so forcing a server rebuild bought nothing and made opening
 * the page wait for one. The caller that has just pressed "check GOV.UK now"
 * passes `skipCache` for itself.
 */
export function fetchEnglandHolidaySyncs(
  signal?: AbortSignal,
  limit = 20,
  options: { skipCache?: boolean } = {},
): Promise<{
  status: EnglandHolidaySyncStatus;
  results: EnglandHolidaySync[];
}> {
  return fetchJson(`/curriculum/england-holidays/syncs/?limit=${encodeURIComponent(String(limit))}`, {
    signal,
    skipCache: options.skipCache,
    revalidate: !options.skipCache,
  });
}

/**
 * Check GOV.UK now and report what moved.
 *
 * The mirror refreshes itself on an interval; this is the "I have just read that
 * a bank holiday changed and need it today" path. `dryRun` reports without
 * writing.
 */
export function refreshEnglandHolidays(options: { dryRun?: boolean } = {}): Promise<{
  summary: EnglandHolidaySync & { applied: boolean; unchanged: number };
  status: EnglandHolidaySyncStatus;
}> {
  return postJson('/curriculum/england-holidays/refresh/', { dryRun: Boolean(options.dryRun) });
}

// ---------------------------------------------------------------------------
// Programme Reviews ("Reviews ID" tab)
//
// A Review here is a reusable *template* belonging to one Programme -- its
// recurrence, eligibility and Form Builder questions. It is not a learner's
// completed review; that is a future, separate domain. Review ids are opaque
// strings minted by the backend (REV-...), the same id style as every other
// curriculum entity (MOD-..., PROG-...) -- never parsed here, never generated
// here.

/**
 * The canonical Programme status values (learner_api/constants.py
 * PROGRAMME_STATUS_CHOICES). No frontend copy of this existed before Reviews;
 * kept in sync by hand since curriculum_api has no read endpoint for it yet.
 */
export const CURRICULUM_PROGRAMME_STATUSES = [
  'Fresh user', 'Onboarding', 'Delivery', 'Ready to enrol', 'Active', 'Withdrawn', 'On break', 'Completed',
] as const;

export type ReviewRecurrenceUnit = 'days' | 'weeks' | 'months';

export type ReviewFieldType =
  | 'text'
  | 'boolean'
  | 'numeric'
  | 'date'
  | 'list_item'
  | 'boolean_case_block'
  | 'email'
  | 'phone'
  | 'postcode_address'
  | 'title_description'
  | 'text_multiline'
  | 'action_button';

export const REVIEW_FIELD_TYPE_LABELS: Record<ReviewFieldType, string> = {
  text: 'Text',
  boolean: 'Boolean',
  numeric: 'Numeric',
  date: 'Date',
  list_item: 'List item',
  boolean_case_block: 'Boolean with case block',
  email: 'Email',
  phone: 'Phone number',
  postcode_address: 'Post code and address',
  title_description: 'Title & description',
  text_multiline: 'Text (multiline)',
  action_button: 'Action button',
};

export const REVIEW_FIELD_TYPES: ReviewFieldType[] = Object.keys(REVIEW_FIELD_TYPE_LABELS) as ReviewFieldType[];

/** Field types that carry a Boolean with case block's IF YES / IF NO children. */
export const CONDITIONAL_FIELD_TYPE: ReviewFieldType = 'boolean_case_block';

/** Field types with no learner-entered answer -- Required/Optional is not shown for these. */
export const DISPLAY_ONLY_FIELD_TYPES: ReviewFieldType[] = ['title_description', 'action_button'];

export type ReviewParticipantRole = 'advisor' | 'employer' | 'participant' | 'referrer';

export type ReviewConditionValue = 'yes' | 'no';

/** Non-advisor roles a Field's own configuration may opt into answering it.
 * The advisor (coach) can always answer every field regardless of this list --
 * see reviews.FIELD_RESPONDENT_ROLES on the backend. */
export type ReviewFieldRespondentRole = 'participant' | 'employer';

/** Carried by every answerable field's configuration alongside its
 * type-specific shape (options, description, ...) -- never present on a
 * display-only field (title_description/action_button). */
export interface RespondentRolesConfiguration {
  respondentRoles?: ReviewFieldRespondentRole[];
}

export interface ListItemConfiguration extends RespondentRolesConfiguration {
  options: string[];
  /** Marks this question as the one carrying a defined business meaning, so
   * readers find it by the template's own stable marker instead of matching a
   * question title. 'rag_status' is the Progress Review's RAG question -- see
   * curriculum_api.review_instances.RAG_SEMANTIC_KEY. */
  semanticKey?: string;
}

export interface TitleDescriptionConfiguration {
  description: string;
}

/** Discriminated by fieldType at the call site; falls back to a generic bag for
 * types (text, boolean, numeric, date, email, phone, postcode_address, ...)
 * that carry no field-specific configuration today. */
export type ReviewFieldConfiguration = ListItemConfiguration | TitleDescriptionConfiguration | (RespondentRolesConfiguration & Record<string, unknown>);

export interface ReviewField {
  id: string;
  reviewId: string;
  sectionId: string;
  /** Set only on a conditional child -- the id of its Boolean-with-case-block parent. */
  parentFieldId: string | null;
  /** Set only on a conditional child -- which branch of its parent it belongs to. */
  conditionValue: ReviewConditionValue | null;
  title: string;
  fieldType: ReviewFieldType;
  required: boolean;
  displayOrder: number;
  configuration: ReviewFieldConfiguration;
  createdAt: string;
  updatedAt: string;
  /** Present (possibly empty) only when fieldType is 'boolean_case_block'. */
  yesFields?: ReviewField[];
  /** Present (possibly empty) only when fieldType is 'boolean_case_block'. */
  noFields?: ReviewField[];
}

export interface ReviewFieldInput {
  id?: string;
  title: string;
  fieldType: ReviewFieldType;
  required: boolean;
  configuration?: ReviewFieldConfiguration;
  /** boolean_case_block only. */
  yesFields?: ReviewFieldInput[];
  /** boolean_case_block only. */
  noFields?: ReviewFieldInput[];
}

export interface ReviewSection {
  id: string;
  reviewId: string;
  title: string;
  estimatedMinutes: number;
  displayOrder: number;
  enabled: boolean;
  fields: ReviewField[];
}

export interface ReviewSectionInput {
  id?: string;
  title: string;
  estimatedMinutes: number;
  enabled: boolean;
  fields: ReviewFieldInput[];
}

export interface ReviewRoleFlags {
  advisor: boolean;
  employer: boolean;
  participant: boolean;
  referrer: boolean;
}

export interface ReviewNotificationFlags {
  employer: boolean;
  participant: boolean;
}

/** A Review's classification, chosen in the Review editor's General tab.
 *
 *  Type is NOT name and NOT schedule: a Review named "Monthly Learner
 *  Catch-up" of type Monthly Coaching Meeting still filters as MCM on the
 *  coach calendar, and it may recur every 6 weeks -- the type never implies a
 *  recurrence. `code` is generated once from the name and frozen, so routing
 *  survives a rename; `id` is the only thing a Review ever stores. */
export interface ReviewType {
  id: string;
  name: string;
  /** Stable routing code. 'mcm' and 'progress_review' are the system types. */
  code: string;
  /** System types can never be renamed or deleted. */
  isSystem: boolean;
  isActive: boolean;
}

export interface ReviewApplicability {
  scope: 'programme' | 'cohort' | 'group';
  ids: string[];
}

export interface ReviewSummary {
  id: string;
  programmeId: string;
  name: string;
  enabled: boolean;
  recurrence: { interval: number; unit: ReviewRecurrenceUnit };
  /** The date the first occurrence is calculated from -- see the Review Schedule below. */
  scheduleAnchorDate: string;
  /** How many times this review recurs before it stops; null/undefined means unlimited. */
  occurrenceCount: number | null;
  /** The Review Type this review is classified as -- a review_types.id, the
   *  stable identity every downstream consumer keys on. */
  reviewTypeId: string;
  /** Echoed for display and calendar routing; never written by a client. */
  reviewTypeCode: string;
  reviewTypeName: string;
  applicableStatuses: string[];
  applicability?: ReviewApplicability;
  fieldCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface ReviewDetail extends ReviewSummary {
  signatures: ReviewRoleFlags;
  visibleTo: ReviewRoleFlags;
  recordTimeSpent: boolean;
  /** Hours one occurrence of this review is expected to take. */
  expectedOtjh: number;
  /** Whether completing an occurrence adds expectedOtjh to the learner's total OTJH. */
  countsTowardsOtjh: boolean;
  allowEditingPriorDays: number;
  notifications: ReviewNotificationFlags;
  incompleteMarker: string;
  createdBy: string;
  updatedBy: string;
  /** Canonical Form Builder structure: Section -> Field -> optional conditional children. */
  sections: ReviewSection[];
  /** Flattened convenience view (every top-level field, across every section) -- see reviews.py. */
  fields: ReviewField[];
}

export interface CreateReviewInput {
  name: string;
  enabled: boolean;
  recurrence: { interval: number; unit: ReviewRecurrenceUnit };
  /** Optional -- defaults to today on the backend when omitted. */
  scheduleAnchorDate?: string;
  /** Optional -- omit or leave null for a review that recurs indefinitely. */
  occurrenceCount?: number | null;
  /** Required -- a Review cannot be saved without a Review Type. */
  reviewTypeId: string;
  applicableStatuses: string[];
  applicability?: ReviewApplicability;
  signatures: ReviewRoleFlags;
  visibleTo: ReviewRoleFlags;
  recordTimeSpent: boolean;
  expectedOtjh: number;
  countsTowardsOtjh: boolean;
  allowEditingPriorDays: number;
  notifications: ReviewNotificationFlags;
  incompleteMarker: string;
  sections: ReviewSectionInput[];
}

export type UpdateReviewInput = Partial<CreateReviewInput>;

export interface CloneReviewRequest {
  sourceProgrammeId: string;
  reviewIds: string[];
}

export interface CloneReviewResponse {
  cloned: boolean;
  sourceProgrammeId: string;
  programmeId: string;
  reviewIds: string[];
  reviews: ReviewSummary[];
}

export interface FetchReviewsOptions {
  signal?: AbortSignal;
  skipCache?: boolean;
  revalidate?: boolean;
}

/** Every active Review Type, system types first. */
export function fetchReviewTypes(options: FetchReviewsOptions = {}) {
  return fetchCollection<ReviewType>('/curriculum/review-types/', {
    signal: options.signal,
    skipCache: options.skipCache,
    revalidate: options.revalidate,
  });
}

/** Create a custom Review Type. The name is the ONLY thing a user supplies:
 *  the backend generates the id, the stable code, and the system/active
 *  flags, so none of them appear in the create UI. */
export async function createReviewType(name: string) {
  const payload = await postJson<{ created: boolean; reviewType: ReviewType }>(
    '/curriculum/review-types/', { name },
  );
  return payload.reviewType;
}

/** Deactivate a custom Review Type. Never a hard delete -- Reviews already
 *  classified with it keep working and keep classifying. System types are
 *  refused by the backend. */
export function archiveReviewType(reviewTypeId: string) {
  return deleteJson<{ deleted: boolean; archived: boolean; id: string; reviewType: ReviewType }>(
    `/curriculum/review-types/${encodeURIComponent(reviewTypeId)}/`,
  );
}

export function fetchProgrammeReviews(programmeId: string, options: FetchReviewsOptions = {}) {
  return fetchCollection<ReviewSummary>(`/curriculum/programmes/${encodeURIComponent(programmeId)}/reviews/`, {
    signal: options.signal,
    skipCache: options.skipCache,
    revalidate: options.revalidate,
  });
}

export async function fetchReviewDetail(reviewId: string, options: FetchReviewsOptions = {}) {
  const payload = await fetchJson<{ review: ReviewDetail }>(`/curriculum/reviews/${encodeURIComponent(reviewId)}/`, {
    signal: options.signal,
    skipCache: options.skipCache,
    revalidate: options.revalidate,
  });
  return payload.review;
}

export async function createReviewTemplate(programmeId: string, input: CreateReviewInput) {
  const payload = await postJson<{ created: boolean; review: ReviewDetail }>(
    `/curriculum/programmes/${encodeURIComponent(programmeId)}/reviews/`, input,
  );
  return payload.review;
}

export async function updateReviewTemplate(reviewId: string, input: UpdateReviewInput) {
  const payload = await patchJson<{ updated: boolean; review: ReviewDetail }>(
    `/curriculum/reviews/${encodeURIComponent(reviewId)}/`, input,
  );
  return payload.review;
}

export function archiveReviewTemplate(reviewId: string) {
  return deleteJson<{ deleted: boolean; permanent: boolean; archived: boolean; id: string }>(
    `/curriculum/reviews/${encodeURIComponent(reviewId)}/`,
  );
}

export function cloneReviewTemplates(programmeId: string, input: CloneReviewRequest) {
  return postJson<CloneReviewResponse>(`/curriculum/programmes/${encodeURIComponent(programmeId)}/reviews/clone/`, input);
}

// ---------------------------------------------------------------------------
// Review Schedule / clash resolution
//
// A Programme can have several recurring Review templates whose occurrences
// land in the same calendar month (e.g. a Progress Review every 12 weeks and
// a Monthly Coaching Meeting every month). This previews each template's
// projected occurrences, groups them by month, and lets staff decide -- per
// month -- which occurrences stay scheduled. "Skip" removes one occurrence
// from the effective schedule; it never disables or deletes the recurring
// template, so future months are unaffected.

export type ReviewScheduleOccurrenceStatus = 'scheduled' | 'skipped';
export type ReviewScheduleResolutionStatus = 'none' | 'unresolved' | 'resolved' | 'kept_all';
export type ReviewClashDecisionAction = 'skip' | 'keep';

export interface ReviewScheduleOccurrence {
  reviewId: string;
  reviewName: string;
  occurrenceDate: string;
  recurrenceLabel: string;
  status: ReviewScheduleOccurrenceStatus;
  skippedBy: string | null;
  skippedAt: string | null;
  reason: string | null;
}

export interface ReviewScheduleMonth {
  /** 'YYYY-MM' */
  month: string;
  hasClash: boolean;
  resolutionStatus: ReviewScheduleResolutionStatus;
  clashSignature: string | null;
  occurrences: ReviewScheduleOccurrence[];
}

export interface ReviewScheduleResponse {
  programmeId: string;
  windowStart: string;
  windowEnd: string;
  monthsPreviewed: number;
  months: ReviewScheduleMonth[];
}

export interface ReviewClashDecisionItem {
  reviewId: string;
  occurrenceDate: string;
  action: ReviewClashDecisionAction;
  reason?: string;
}

export interface ReviewClashResolutionInput {
  month: string;
  occurrences: ReviewClashDecisionItem[];
}

export interface ReviewClashResolutionResponse {
  resolved: boolean;
  programmeId: string;
  month: ReviewScheduleMonth;
}

export interface FetchReviewScheduleOptions extends FetchReviewsOptions {
  months?: number;
}

export function fetchReviewSchedule(programmeId: string, options: FetchReviewScheduleOptions = {}) {
  const query = options.months ? `?months=${encodeURIComponent(String(options.months))}` : '';
  return fetchJson<ReviewScheduleResponse>(`/curriculum/programmes/${encodeURIComponent(programmeId)}/reviews/schedule/${query}`, {
    signal: options.signal,
    skipCache: options.skipCache,
    revalidate: options.revalidate,
  });
}

export function resolveReviewClash(programmeId: string, input: ReviewClashResolutionInput) {
  return postJson<ReviewClashResolutionResponse>(
    `/curriculum/programmes/${encodeURIComponent(programmeId)}/reviews/clashes/resolve/`, input,
  );
}
