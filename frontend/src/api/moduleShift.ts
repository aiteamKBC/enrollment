// ============================================================================
// Module shift API client
// The modules taught alongside one module — the alternatives a learner can be
// moved onto — the week-by-week pairing of their progress, and the move itself.
//
// The move has its own endpoint rather than going through a plan save: the
// programme rule is enforced server-side, a plan's other entries are left exactly
// as stored, and the learner's recorded progress moves in the same transaction.
// ============================================================================
import type { LearningPlanModule } from './learningPlan';

const BASE = '/learner_api/module-shift';

export interface ModuleShiftOptions {
  /** The source module's programme, across all of its cohorts. */
  programme: { id: string; name: string };
  /** The cohort the walk resolved to. Empty when the module has no cohort. */
  cohort: { id: string; name: string };
  /** The programme's groups, so the picker can say where the options came from. */
  groups: string[];
  modules: LearningPlanModule[];
  /** Why the list is empty, when it is. '' when there are options. */
  reason: string;
}

/** What the learner has recorded against one component. */
export interface ComponentProgress {
  entries: number;
  kinds: string[];
  lastAt: string;
  otjHours: number;
  points: number;
}

/** The tutor/coach decision on the reflection a component carries. */
export interface ComponentReview {
  /** e.g. 'submitted_for_tutor_review', 'accepted', 'referred'. */
  status: string;
  feedback: string;
  reviewedBy: string;
  reviewedAt: string;
}

export interface ShiftComponent {
  componentId: string;
  title: string;
  type: string;
  otjHours: number;
  /** Null on every component the learner has not worked through. */
  progress?: ComponentProgress | null;
  /** Null when no reflection was submitted against this component. */
  review?: ComponentReview | null;
}

export interface ShiftWeek {
  weekId: string;
  weekNumber: number | null;
  title: string;
  components: ShiftComponent[];
}

export interface ShiftWeekPair {
  /** 1-based position in the module — what makes the two weeks equivalent. */
  order: number;
  from: ShiftWeek;
  /** Null when the module being joined has no week at this position. */
  to: ShiftWeek | null;
}

export interface ProgressMapping {
  fromComponentId: string;
  toComponentId: string;
}

export interface ModuleShiftProgress {
  weeks: ShiftWeekPair[];
  /** The server's opening offer: same component id, else exact cleaned title + type. */
  suggested: ProgressMapping[];
  /** Why there is nothing to map, when there isn't. '' when there is. */
  reason: string;
  progressedComponents: number;
}

async function parse(res: Response) {
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (body?.error) message = body.error;
    } catch {
      // Non-JSON error body (proxy/HTML error page) — keep the status message.
    }
    throw new Error(message);
  }
  return res.json();
}

export async function fetchModuleShiftOptions(moduleId: string): Promise<ModuleShiftOptions> {
  const options = await parse(await fetch(`${BASE}/options/?moduleId=${encodeURIComponent(moduleId)}`));
  // Older servers return cohort-scoped options without programme metadata.
  // Do not render those as programme-wide choices or crash the whole page.
  if (
    !options?.programme ||
    typeof options.programme.id !== 'string' ||
    typeof options.programme.name !== 'string'
  ) {
    throw new Error('Programme-wide module options are unavailable. Please contact support.');
  }
  return options;
}

/** The weeks of `fromModuleId` holding progress, paired with `toModuleId`. */
export async function fetchModuleShiftProgress(
  learnerId: string | number,
  fromModuleId: string,
  toModuleId: string,
): Promise<ModuleShiftProgress> {
  const query = new URLSearchParams({ from: fromModuleId, to: toModuleId });
  return parse(await fetch(`${BASE}/${learnerId}/progress/?${query}`));
}

/**
 * Move the learner off `fromModuleId` and onto `toModuleId`, carrying the
 * progress named in `progressMappings` — and with it each component's
 * reflection and the tutor or coach decision on it. Progress left out of the
 * mapping is archived: it remains historical evidence but no longer counts in
 * the learner's active progress or achieved hours.
 */
export async function shiftModule(
  learnerId: string | number,
  fromModuleId: string,
  toModuleId: string,
  progressMappings: ProgressMapping[] = [],
): Promise<{
  progressMoved?: number;
  progressArchived?: number;
  reviewsMoved?: number;
  /** Left behind because the component joined already had a review of its own. */
  reviewsKept?: number;
}> {
  return parse(
    await fetch(`${BASE}/${learnerId}/`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fromModuleId, toModuleId, progressMappings }),
    }),
  );
}
