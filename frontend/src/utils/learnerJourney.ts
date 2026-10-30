import type { ComponentKsbMapping, LearnerDetail, LearnerQuizAttempt } from '@/api/learnerDetail';
import { topicHasContent } from '@/lib/assignmentTopics';
export { formatHoursMinutes } from '@/lib/format';

export interface JourneyComponent {
  previousLearning?: import('@/api/learnerDetail').PreviousLearningCompletion;
  title: string;
  expectedOtjh: number | null;
  moduleId?: string | null;
  weekId?: string | null;
  ksbWeightTotal?: number | null;
  ksbMappingCount?: number | null;
  ksbMappings?: ComponentKsbMapping[];
  componentId?: string | null;
  /** Display-only grouping within the authored week; child IDs remain canonical. */
  pointNumber?: number | null;
  type?: string | null;
  description?: string | null;
  assignmentTopics?: import('@/lib/assignmentTopics').AssignmentTopic[];
  /** The author flagged this as the monthly (eight-step) assignment. */
  monthlyAssignment?: boolean;
  assignmentBrief?: string | null;
  assignmentBriefHtml?: string | null;
  videoUrl?: string | null;
  alternativeYouTubeUrl?: string | null;
  audioUrl?: string | null;
  contentHtml?: string | null;
  /** A video's authored lesson content (HTML or plain text), shown under the player. */
  lessonContentHtml?: string | null;
  hasReadingContent?: boolean;
  fileName?: string | null;
  /** Ordered attachments; entry 0 is `resourceUrl`. */
  files?: import('@/lib/componentFiles').ComponentFile[];
  downloadAllowed?: boolean;
  reflectionPrompt?: string | null;
  /** Completion derived from verified Teams attendance; adds no recorded hours. */
  attendanceCompleted?: boolean;
  attendanceCompletedAt?: string | null;
  reflectionRequired?: boolean;
  /** The author sent this activity for coach validation, so finishing it hands
   *  it in rather than completing it. */
  tutorValidationRequired?: boolean;
  reflectionQuestion?: string | null;
  resourceUrl?: string | null;
  liveSessionUrl?: string | null;
  teamsLiveSessionId?: string | null;
  teamsSessionNumber?: number | null;
  recordingSeriesId?: string | null;
  recordingSessionNumber?: number | null;
  recordingDate?: string | null;
  sessionDate?: string | null;
  sessionTime?: string | null;
  sessionDateTimeUtc?: string | null;
  durationMinutes?: number | null;
  isQuiz?: boolean;
  quizMeta?: { quizId: number; questions: number | null; duration: number | null; timeUnit: string | null };
  quizAttempts?: LearnerQuizAttempt[];
  retired?: boolean;
  /** See LearnerComponentEntry.contentAvailable. */
  contentAvailable?: boolean;
}

/** The attempts that belong to a quiz component: those on the quiz it links
 * now, plus earlier ones the server traced to this same component after an
 * author re-pointed it at a different quiz. Quiz ids compare as strings
 * because progress rows store quiz_ref as text. */
export function quizAttemptsFor(
  component: Pick<JourneyComponent, 'componentId' | 'quizMeta'>,
  attempts: LearnerQuizAttempt[] | undefined,
): LearnerQuizAttempt[] {
  const quizId = component.quizMeta?.quizId;
  return (attempts || []).filter((attempt) => {
    // A shifted attempt keeps the quiz that was actually sat but receives an
    // explicit destination component. That identity is authoritative: also
    // falling back to quizId could credit another component reusing the quiz.
    if (attempt.componentId) return !!component.componentId && attempt.componentId === component.componentId;
    return quizId != null && String(attempt.quizId) === String(quizId);
  });
}

/* ═══════════════════════════════════════════════════════
   COMPONENT COMPLETION MODEL
   Which component types a learner can OPEN + complete (with
   the reflection/KSBs/time flow) and how they're rendered.
   Quizzes and videos have their own dedicated flows.
   ═══════════════════════════════════════════════════════ */
export type ContentKind = 'video' | 'audio' | 'reading' | 'slides' | 'reflection' | 'resource';

/** Map a component's type to how its content should be presented. */
export function componentContentKind(type: string | null | undefined): ContentKind {
  const t = (type || '').toLowerCase();
  if (t === 'video') return 'video';
  if (t === 'podcast' || t === 'audio') return 'audio';
  if (t === 'reading') return 'reading';
  if (t === 'powerpoint') return 'slides';
  if (t === 'reflection') return 'reflection';
  return 'resource';
}

/* ═══════════════════════════════════════════════════════
   COMPLETION CRITERIA
   KSB weights describe curriculum contribution and never block
   a learner from finishing an activity. Assignments remain gated
   because they require an approved evidence upload.
   ═══════════════════════════════════════════════════════ */
/** Assignments are the only components that offer supporting evidence upload.
 * Evidence is optional in the assignment wizard; the three written sections
 * are the completion gate. */
export function componentRequiresEvidence(type: string | null | undefined): boolean {
  return (type || '').trim().toLowerCase().replace(/-/g, '_') === 'assignment';
}

type MonthlyAssignmentCandidate = { type?: string | null; monthlyAssignment?: boolean | null };

/** Has an author flagged any assignment in this learner's plan as the monthly assignment? */
export function monthlyAssignmentFlagsInUse(components: readonly MonthlyAssignmentCandidate[] | null | undefined): boolean {
  return Boolean(components?.some(component => componentRequiresEvidence(component.type) && component.monthlyAssignment === true));
}

/** Is this the assignment learners submit through the eight-step monthly
 * submission (and list under Monthly submission)? Until an author flags one,
 * every assignment is. Once any assignment in the plan is flagged, only the
 * flagged ones are and the others are completed like any other activity.
 * learner_api.assignment_settings applies the same rule on completion. */
export function isMonthlyAssignment(
  component: MonthlyAssignmentCandidate | null | undefined,
  components: readonly MonthlyAssignmentCandidate[] | null | undefined,
): boolean {
  if (!component || !componentRequiresEvidence(component.type)) return false;
  return component.monthlyAssignment === true || !monthlyAssignmentFlagsInUse(components);
}

export interface ComponentCriteria {
  gated: boolean;
  evidenceRequired: boolean;
  evidenceMet: boolean;
  met: boolean;            // overall: safe to complete
}

/** Evidence no longer gates assignment completion; the assignment API checks
 * the three written form sections instead. Keep this result shape for the
 * generic component UI and existing consumers. */
export function componentCriteria(_component: JourneyComponent, _evidenceCount: number): ComponentCriteria {
  const evidenceRequired = false;
  const evidenceMet = true;
  return {
    gated: evidenceRequired,
    evidenceRequired,
    evidenceMet,
    met: evidenceMet,
  };
}

/* ═══════════════════════════════════════════════════════
   KSB PROGRESS (weighted)
   The component rule above, inverted: a component declares
   which KSBs it develops and at what weight, so a KSB's
   progress is the weight it has EARNED from completed
   components over the weight AVAILABLE across the plan.

   Weight is always taken from the component's AUTHORED
   mapping, never from the codes stored on a completion
   record — legacy records hold learner-picked codes with no
   weight behind them (one historic quiz attempt lists 31
   codes at once), which would otherwise wildly overstate
   progress.
   ═══════════════════════════════════════════════════════ */

/** Component mappings use sub-codes ('K3.1', 'S1.2') that are not themselves
 * programme KSB codes. Roll them up to the parent ('K3', 'S1') so the weight
 * lands on the KSB the learner is actually assessed against. */
export function ksbParentCode(code: string): string {
  return String(code || '').trim().toUpperCase().split('.')[0];
}

/** Normalise API labels (Knowledge / Skill / Behaviour) to the compact keys
 * used by the KSB page's category layout. */
export function ksbTypeCode(type: string | null | undefined, code = ''): 'K' | 'S' | 'B' | '?' {
  const value = String(type || code.charAt(0) || '').trim().toUpperCase();
  if (value === 'K' || value.startsWith('KNOWLEDGE')) return 'K';
  if (value === 'S' || value.startsWith('SKILL')) return 'S';
  if (value === 'B' || value.startsWith('BEHAVIOUR') || value.startsWith('BEHAVIOR')) return 'B';
  return '?';
}

export type KsbStatus = 'complete' | 'in-progress' | 'not-started';

export interface KsbContributor {
  componentId: string;
  title: string;
  module: string | null;
  week: string | null;
  type: string | null;
  weight: number;
  classification: string | null;
  done: boolean;
}

export interface KsbProgress {
  code: string;
  type: string;                 // K | S | B
  description: string;
  availableWeight: number;      // total weight this KSB can earn from the plan
  earnedWeight: number;         // weight from components already completed
  remainingWeight: number;
  pct: number;                  // earnedWeight / availableWeight (0 when no weight is available)
  status: KsbStatus;
  contributors: KsbContributor[];   // components that develop this KSB
  doneCount: number;
  totalCount: number;
}

export interface KsbProgressSource {
  ksbs: { code: string; type?: string; description?: string }[];
  components: {
    componentId?: string | null;
    component?: string;
    module?: string | null;
    week?: string | null;
    type?: string | null;
    ksbMappings?: { code: string; weight: number; classification?: string | null }[];
  }[];
  completedComponentIds: Iterable<string>;
  evidencedKsbCodes?: Iterable<string>;
}

/** Derive weighted progress for every programme KSB.
 * `completedComponentIds` must already be de-duplicated, so a component
 * completed twice (retake / re-watch) contributes its weight only once. */
export function buildKsbProgress(src: KsbProgressSource): KsbProgress[] {
  const done = new Set(src.completedComponentIds);
  const evidenced = new Set(Array.from(src.evidencedKsbCodes || [], ksbParentCode));
  const byCode = new Map<string, KsbContributor[]>();

  for (const c of src.components || []) {
    const componentId = c.componentId || '';
    if (!componentId) continue;
    for (const m of c.ksbMappings || []) {
      const code = ksbParentCode(m.code);
      if (!code) continue;
      const list = byCode.get(code) || [];
      // A component can map both a parent and its sub-code (e.g. K1 and K1.1);
      // both are genuine authored weight, so both are kept.
      list.push({
        componentId,
        title: c.component || 'Activity',
        module: c.module ?? null,
        week: c.week ?? null,
        type: c.type ?? null,
        weight: Number(m.weight) || 0,
        classification: m.classification ?? null,
        done: done.has(componentId),
      });
      byCode.set(code, list);
    }
  }

  return (src.ksbs || []).map((k) => {
    const code = String(k.code || '').trim().toUpperCase();
    const contributors = (byCode.get(code) || [])
      .slice()
      .sort((a, b) => Number(a.done) - Number(b.done) || b.weight - a.weight);
    const availableWeight = contributors.reduce((s, c) => s + c.weight, 0);
    const recordedEvidence = evidenced.has(code);
    const earnedWeight = recordedEvidence
      ? availableWeight
      : contributors.reduce((s, c) => s + (c.done ? c.weight : 0), 0);
    const doneCount = contributors.filter((c) => c.done).length;
    const pct = recordedEvidence
      ? 100
      : availableWeight > 0
      ? Math.max(0, Math.min(100, Math.round((earnedWeight / availableWeight) * 100)))
      : 0;
    const status: KsbStatus = recordedEvidence || (earnedWeight >= availableWeight && availableWeight > 0)
      ? 'complete'
      : earnedWeight > 0
        ? 'in-progress'
        : 'not-started';
    return {
      code,
      type: ksbTypeCode(k.type, code),
      description: k.description || '',
      availableWeight,
      earnedWeight,
      remainingWeight: Math.max(0, availableWeight - earnedWeight),
      pct,
      status,
      contributors,
      doneCount,
      totalCount: contributors.length,
    };
  });
}

/** Mirrors backend `learner_api/progress_rules.py`: kinds whose row is a graded
 * attempt, where achievement needs an explicit pass. Everything else records a
 * completion, so `passed` stays null and the row itself is the achievement. */
const GRADED_PROGRESS_KINDS = new Set(['quiz']);

/** Does this progress record count as achieved delivery?
 *
 * `passed === false` never counts, whatever the kind — a failed attempt with a
 * valid componentId and valid KSB codes is still a failed attempt. A graded
 * kind must have passed outright.
 */
export function progressCountsAsAchieved(
  record: { kind?: string | null; passed?: boolean | null } | null | undefined,
): boolean {
  if (!record) return false;
  if (record.passed === false) return false;
  if (GRADED_PROGRESS_KINDS.has(String(record.kind || '').trim().toLowerCase())) {
    return record.passed === true;
  }
  return true;
}

/** Component ids the learner has finished, de-duplicated across every
 * completion source (a re-watch or retake must not count twice). A failed
 * attempt is excluded: it is history, not a completion. */
export function completedComponentIds(real: {
  videoProgress?: { componentId: string; kind?: string; passed?: boolean | null }[];
  componentProgress?: { componentId: string; kind?: string; passed?: boolean | null }[];
} | null): Set<string> {
  const ids = new Set<string>();
  for (const v of real?.videoProgress || []) if (v.componentId && progressCountsAsAchieved(v)) ids.add(v.componentId);
  for (const c of real?.componentProgress || []) if (c.componentId && progressCountsAsAchieved(c)) ids.add(c.componentId);
  return ids;
}

/** Has the learner finished this plan row? A quiz counts once it has been
 * passed at all — a later failed retake is history and never revokes the
 * achievement, the same rule `progressCountsAsAchieved` applies to every other
 * completion source. Non-quiz rows are looked up in `completedIds`, which
 * callers build with `completedComponentIds`. */
export function isComponentComplete(c: JourneyComponent, completedIds: Set<string>): boolean {
  if (c.previousLearning?.completed === true) return true;
  if (c.type?.replace(/-/g, '_') === 'live_session' && c.attendanceCompleted === true) return true;
  if (c.isQuiz) return (c.quizAttempts || []).some((a) => a.passed);
  return !!c.componentId && completedIds.has(c.componentId);
}

/** KSBs backed by a genuine completed learner activity. Failed attempts are
 * deliberately excluded — some legacy quiz attempts attach an entire KSB
 * profile even when the learner scored zero, and the same must hold for a
 * component or video recorded as not passed. Video/component completions remain
 * valid evidence when a curriculum refresh has changed their authored id.
 */
export function recordedKsbEvidenceCodes(real: LearnerDetail | null): Set<string> {
  const codes = new Set<string>();
  const records = [
    // quizAttempts is the graded bucket by construction — pre-'kind' rows land
    // there without one, so name the kind rather than relying on the field.
    ...(real?.quizAttempts || []).filter((attempt) => progressCountsAsAchieved({ kind: 'quiz', passed: attempt.passed })),
    ...(real?.videoProgress || []).filter((entry) => progressCountsAsAchieved(entry)),
    ...(real?.componentProgress || []).filter((entry) => progressCountsAsAchieved(entry)),
  ];
  for (const record of records) {
    for (const rawCode of record.ksbs || []) {
      const code = ksbParentCode(rawCode);
      if (code) codes.add(code);
    }
  }
  return codes;
}

/** Programme KSB target codes, normalised to the same parent-code level used
 * by the coach caseload. */
export function targetKsbCodes(real: Pick<LearnerDetail, 'ksbs'> | null): Set<string> {
  return new Set(
    (real?.ksbs || [])
      .map((ksb) => ksbParentCode(ksb.code))
      .filter(Boolean),
  );
}

/** Evidenced KSBs that are actually part of the learner's target profile.
 * Completion records can contain historical/raw KSB codes from old mappings;
 * this keeps overview counts from showing impossible values such as 6 of 2. */
export function evidencedTargetKsbCodes(real: LearnerDetail | null): Set<string> {
  const targetCodes = targetKsbCodes(real);
  const evidencedCodes = recordedKsbEvidenceCodes(real);
  if (targetCodes.size === 0) return evidencedCodes;

  return new Set(
    Array.from(evidencedCodes)
      .map(ksbParentCode)
      .filter((code) => targetCodes.has(code)),
  );
}

/** Short noun used in the reflection copy ("this podcast", "this reading…"). */
export function componentNoun(type: string | null | undefined): string {
  const t = (type || '').toLowerCase();
  return ({
    video: 'video', podcast: 'podcast', reading: 'reading', powerpoint: 'slide deck',
    reflection: 'reflection', activity: 'activity', workplace_evidence: 'evidence task',
    'workplace-evidence': 'evidence task', live_session: 'session',
    recording_placeholder: 'recording', 'recording placeholder': 'recording',
  } as Record<string, string>)[t] || 'activity';
}

/** Does this activity contain something the learner can actually consume?
 * An id/title alone is not content and must never lead to an empty runner. */
export function hasComponentContent(c: JourneyComponent): boolean {
  // Compact rows carry the server's verdict of the rule below (same fixture).
  if (typeof c.contentAvailable === 'boolean') return c.contentAvailable;
  const hasText = (value?: string | null) => Boolean(value?.replace(/<[^>]*>/g, '').replace(/&nbsp;|\s/g, '').trim());
  const hasUrl = (value?: string | null) => Boolean(value?.trim());
  if (c.isQuiz) return c.quizMeta?.quizId != null && (c.quizMeta.questions ?? 0) > 0;

  const type = (c.type || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
  if (type === 'quiz') return false; // Unlinked builder placeholders have no quiz to attempt.
  const hasDescription = hasText(c.description);
  if (type === 'video') return hasUrl(c.videoUrl) || Boolean(c.recordingSeriesId && c.recordingSessionNumber && c.recordingDate);
  // A session recording linked to its live session plays that session's recording.
  if (type === 'recording_placeholder' && c.recordingSeriesId && c.recordingSessionNumber && c.recordingDate) return true;
  if (type === 'podcast' || type === 'audio') return hasUrl(c.audioUrl) || hasUrl(c.resourceUrl) || hasDescription;
  if (type === 'reading') return c.hasReadingContent === true || hasText(c.contentHtml) || hasUrl(c.resourceUrl) || hasUrl(c.audioUrl) || hasDescription;
  if (type === 'powerpoint' || type === 'presentation' || type === 'slides') return hasUrl(c.resourceUrl) || hasDescription;
  // A reflection's content *is* the reflection form the runner always renders,
  // so it stays openable even with nothing authored around it. (The API now
  // drops the boilerplate prompt every component is seeded with, which would
  // otherwise have made an unedited reflection look empty.)
  if (type === 'reflection') return true;
  if (type === 'live_session') {
    return hasUrl(c.liveSessionUrl) || hasText(c.sessionDateTimeUtc) || hasText(c.sessionDate) || Boolean(c.teamsLiveSessionId) || hasDescription;
  }
  // Builder rows can retain a default reflection question even when reflection
  // is disabled. That follow-up question is not the assignment's task/brief.
  if (type === 'assignment') {
    return Boolean(c.assignmentTopics?.some(topicHasContent)) || hasText(c.assignmentBrief) || hasText(c.assignmentBriefHtml)
      || hasUrl(c.resourceUrl) || hasText(c.contentHtml)
      || c.hasReadingContent === true || hasDescription;
  }
  return hasUrl(c.resourceUrl)
    || hasText(c.reflectionPrompt)
    || hasText(c.reflectionQuestion)
    || hasText(c.contentHtml)
    || c.hasReadingContent === true
    || hasUrl(c.audioUrl)
    // An assignment carries no description column — its brief is the only text
    // it has, so a brief-only assignment is still something to consume.
    || hasText(c.assignmentBrief)
    || hasText(c.assignmentBriefHtml)
    || hasDescription;
}

/** Can the learner open + complete this non-quiz component? */
export function isOpenableComponent(c: JourneyComponent): boolean {
  if (c.isQuiz || !c.componentId) return false;
  return hasComponentContent(c);
}
export interface JourneyWeek {
  week: string;
  otjh: number;
  components: JourneyComponent[];
}
export interface JourneyModule {
  /** Canonical My Learning subject identity, when provided by that source. */
  id?: string;
  module: string;
  weeks: JourneyWeek[];
}

export interface TrainingPlanWeekPosition {
  current: number | null;
  total: number;
  state: 'upcoming' | 'active' | 'complete';
}

function parsePlanDate(value?: string): Date | null {
  const text = String(value || '').trim();
  if (!text) return null;
  const isoDate = text.match(/^(\d{4})-(\d{2})-(\d{2})/);
  const parsed = isoDate
    ? new Date(Number(isoDate[1]), Number(isoDate[2]) - 1, Number(isoDate[3]))
    : new Date(text);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/** Calendar position in the learner's complete module/week training plan. */
export function trainingPlanWeekPosition(
  real: LearnerDetail | null,
  today = new Date(),
): TrainingPlanWeekPosition | null {
  if (!real) return null;
  const total = buildLearnerJourney(real).reduce((count, module) => count + module.weeks.length, 0);
  const start = parsePlanDate(real.programmeStartDate || real.cohortStartDate);
  if (!start || total === 0 || Number.isNaN(today.getTime())) return null;

  const startDay = Date.UTC(start.getFullYear(), start.getMonth(), start.getDate());
  const todayDay = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
  const elapsedDays = Math.floor((todayDay - startDay) / 86_400_000);
  if (elapsedDays < 0) return { current: null, total, state: 'upcoming' };
  if (elapsedDays >= total * 7) return { current: total, total, state: 'complete' };
  return {
    current: Math.min(total, Math.floor(elapsedDays / 7) + 1),
    total,
    state: 'active',
  };
}

/**
 * Visual metadata for a component, derived from its title. Real learner data
 * carries no component "type" field — titles arrive as "Type · Detail" (or just
 * "Type"), so we key off the segment before the "·" to pick an icon/colour that
 * matches the mock training-plan's ACTIVITY_TYPE_META styling.
 */
export interface ComponentTypeMeta {
  label: string;
  detail: string | null;
  icon: string;
  bg: string;
  color: string;
}

export interface ResourceTypeMeta {
  icon: string;
  bg: string;
  color: string;
}

const RESOURCE_TYPE_META: Record<string, ResourceTypeMeta> = {
  reading: { icon: 'ri-book-open-line', bg: 'resource-icon-reading-bg', color: 'resource-icon-reading' },
  video: { icon: 'ri-play-circle-line', bg: 'resource-icon-video-bg', color: 'resource-icon-video' },
  podcast: { icon: 'ri-podcast-line', bg: 'resource-icon-podcast-bg', color: 'resource-icon-podcast' },
  quiz: { icon: 'ri-questionnaire-line', bg: 'resource-icon-quiz-bg', color: 'resource-icon-quiz' },
  assignment: { icon: 'ri-file-check-line', bg: 'resource-icon-assignment-bg', color: 'resource-icon-assignment' },
  document: { icon: 'ri-file-text-line', bg: 'resource-icon-document-bg', color: 'resource-icon-document' },
  reflection: { icon: 'ri-brain-line', bg: 'resource-icon-reading-bg', color: 'resource-icon-reading' },
  powerpoint: { icon: 'ri-slideshow-line', bg: 'resource-icon-document-bg', color: 'resource-icon-document' },
  'live session': { icon: 'ri-vidicon-line', bg: 'resource-icon-video-bg', color: 'resource-icon-video' },
  recording: { icon: 'ri-record-circle-line', bg: 'resource-icon-video-bg', color: 'resource-icon-video' },
  'recording placeholder': { icon: 'ri-record-circle-line', bg: 'resource-icon-video-bg', color: 'resource-icon-video' },
  evidence: { icon: 'ri-file-check-line', bg: 'resource-icon-assignment-bg', color: 'resource-icon-assignment' },
  'workplace evidence': { icon: 'ri-file-check-line', bg: 'resource-icon-assignment-bg', color: 'resource-icon-assignment' },
  activity: { icon: 'ri-tools-line', bg: 'resource-icon-assignment-bg', color: 'resource-icon-assignment' },
};

const RESOURCE_TYPE_ALIASES: Record<string, string> = {
  audio: 'podcast',
  pdf: 'document',
  file: 'document',
  word: 'document',
  ppt: 'document',
  text: 'document',
  resource: 'document',
};

const DEFAULT_TYPE_META: ResourceTypeMeta = {
  icon: 'ri-file-text-line',
  bg: 'resource-icon-document-bg',
  color: 'resource-icon-document',
};

export function resourceTypeMeta(type: string | null | undefined): ResourceTypeMeta {
  const key = String(type || '').trim().toLowerCase().replace(/[\s_-]+/g, ' ');
  const canonical = RESOURCE_TYPE_ALIASES[key] || key;
  if (RESOURCE_TYPE_META[canonical]) return RESOURCE_TYPE_META[canonical];

  // LMS records may provide a MIME type or a longer display value instead of
  // the short resource label used by learner-plan components.
  if (/podcast|audio/.test(canonical)) return RESOURCE_TYPE_META.podcast;
  if (/video|recording|live session/.test(canonical)) return RESOURCE_TYPE_META.video;
  if (/quiz|questionnaire/.test(canonical)) return RESOURCE_TYPE_META.quiz;
  if (/assignment|evidence/.test(canonical)) return RESOURCE_TYPE_META.assignment;
  if (/reading|book|article/.test(canonical)) return RESOURCE_TYPE_META.reading;
  if (/document|file|pdf|word|powerpoint|presentation|text|plain/.test(canonical)) return RESOURCE_TYPE_META.document;

  return DEFAULT_TYPE_META;
}

/** Split a "Type · Detail" component title into styled parts. */
export function componentTypeMeta(title: string): ComponentTypeMeta {
  const [rawLabel, ...rest] = title.split('·').map((s) => s.trim());
  const label = rawLabel || title;
  const detail = rest.length ? rest.join(' · ') : null;
  const meta = resourceTypeMeta(label);
  return { label, detail, ...meta };
}

/** Grade -> numeric percent (0-100). Accepts the new 0-1 decimal form (0.9 ->
 * 90), a legacy "30%" string, or a legacy whole-number percent. */
export function gradePercent(grade: string | number | undefined): number {
  if (typeof grade === 'number') return grade <= 1 ? Math.round(grade * 100) : grade;
  const m = String(grade ?? '').match(/-?\d+(\.\d+)?/);
  if (!m) return 0;
  const n = parseFloat(m[0]);
  return n <= 1 ? Math.round(n * 100) : n;
}

/** First number found in a free-text time ("about 25 minutes" -> 25). null if none. */
function parseMinutes(text: string | undefined): number | null {
  const m = String(text ?? '').match(/\d+(\.\d+)?/);
  return m ? parseFloat(m[0]) : null;
}

/** Parse a stored hours string ("1.5", "48") to a number. 0 on garbage. */
export function parseHours(value: string | number | null | undefined): number {
  if (typeof value === 'number') return value;
  const n = parseFloat(String(value ?? ''));
  return Number.isFinite(n) ? n : 0;
}

/** For each quiz, the learner's BEST attempt = highest grade%, ties -> most recent. */
function bestAttemptPerQuiz(attempts: LearnerQuizAttempt[]): LearnerQuizAttempt[] {
  const byQuiz = new Map<number, LearnerQuizAttempt>();
  for (const a of attempts) {
    const cur = byQuiz.get(a.quizId);
    if (!cur) { byQuiz.set(a.quizId, a); continue; }
    const better = gradePercent(a.grade) > gradePercent(cur.grade)
      || (gradePercent(a.grade) === gradePercent(cur.grade) && (a.submittedAt || '') > (cur.submittedAt || ''));
    if (better) byQuiz.set(a.quizId, a);
  }
  return Array.from(byQuiz.values());
}

export interface QuizAggregateStats {
  quizzesTaken: number;       // distinct quizzes with at least one attempt
  totalMinutes: number;       // summed chosen time of each quiz's best attempt
  totalHours: number;         // totalMinutes / 60, rounded to 1dp
  ksbCodes: string[];         // union of KSB codes across best attempts
  ksbCount: number;           // distinct KSB count
}

/** Best watch per video component: highest reportedTime, ties -> most recent. */
function bestWatchPerVideo(videos: LearnerDetail['videoProgress']): NonNullable<LearnerDetail['videoProgress']> {
  const byComp = new Map<string, NonNullable<LearnerDetail['videoProgress']>[number]>();
  for (const v of videos || []) {
    const cur = byComp.get(v.componentId);
    const mins = parseMinutes(v.reportedTime) ?? 0;
    if (!cur) { byComp.set(v.componentId, v); continue; }
    const curMins = parseMinutes(cur.reportedTime) ?? 0;
    if (mins > curMins || (mins === curMins && (v.submittedAt || '') > (cur.submittedAt || ''))) {
      byComp.set(v.componentId, v);
    }
  }
  return Array.from(byComp.values());
}

/**
 * Aggregate a learner's progress into the overview-card figures.
 * KSBs: the union of KSB codes from each quiz's BEST attempt AND each video
 * component's best watch — retakes/re-watches never double-count a KSB.
 * `totalMinutes`/`totalHours`: a best-attempt-per-activity time rollup (the
 * user-facing OTJ hours come from the backend `completedHours`, which sums ALL
 * attempts — this figure is only a secondary KSB-time summary).
 */
export function quizAggregateStats(real: LearnerDetail | null): QuizAggregateStats {
  const empty: QuizAggregateStats = { quizzesTaken: 0, totalMinutes: 0, totalHours: 0, ksbCodes: [], ksbCount: 0 };
  if (!real) return empty;

  const bestQuizzes = bestAttemptPerQuiz(real.quizAttempts || []);
  const bestVideos = bestWatchPerVideo(real.videoProgress);
  if (bestQuizzes.length === 0 && bestVideos.length === 0) return empty;

  let totalMinutes = 0;
  const ksbSet = new Set<string>();
  for (const r of [...bestQuizzes, ...bestVideos]) {
    const mins = parseMinutes(r.reportedTime);
    if (mins != null) totalMinutes += mins;
    for (const code of r.ksbs || []) ksbSet.add(code);
  }
  return {
    quizzesTaken: bestQuizzes.length,
    totalMinutes,
    totalHours: Math.round((totalMinutes / 60) * 10) / 10,
    ksbCodes: Array.from(ksbSet),
    ksbCount: ksbSet.size,
  };
}

/** Group a learner's flat week/components arrays into module -> week -> components. */
/** The plan fields a journey is built from; the compact Weekly read carries them too. */
export type LearnerJourneySource = Pick<LearnerDetail, 'modules' | 'week' | 'components' | 'quizAttempts'>;

export function buildLearnerJourney(real: LearnerJourneySource | null): JourneyModule[] {
  if (!real) return [];
  const groups = [...new Set(real.modules)].flatMap((moduleTitle) => {
    const weeks = real.week.filter((week) => week.module === moduleTitle);
    const ids = [...new Set(weeks.map((week) => week.moduleId || ''))];
    return (ids.length ? ids : ['']).map((moduleId) => ({ moduleTitle, weeks: weeks.filter((week) => (week.moduleId || '') === moduleId) }));
  });
  return groups.map(({ moduleTitle, weeks: weeksForModule }) => {
    return {
      module: moduleTitle,
      weeks: weeksForModule.map((w) => {
        const components = real.components
          .filter((c) => c.module === moduleTitle && (w.moduleId ? c.moduleId === w.moduleId : true)
            && (w.weekId ? c.weekId === w.weekId : c.week === w.week))
          .map((c) => ({
            title: c.component, expectedOtjh: c.expectedOtjh, isQuiz: c.isQuiz, quizMeta: c.quizMeta,
            moduleId: c.moduleId, weekId: c.weekId, pointNumber: c.pointNumber,
            ksbWeightTotal: c.ksbWeightTotal, ksbMappingCount: c.ksbMappingCount,
            ksbMappings: c.ksbMappings,
            componentId: c.componentId, type: c.type, description: c.description,
            assignmentBrief: c.assignmentBrief, assignmentBriefHtml: c.assignmentBriefHtml, assignmentTopics: c.assignmentTopics,
            monthlyAssignment: c.monthlyAssignment,
            videoUrl: c.videoUrl, durationMinutes: c.durationMinutes,
            audioUrl: c.audioUrl, alternativeYouTubeUrl: c.alternativeYouTubeUrl, contentHtml: c.contentHtml, fileName: c.fileName, files: c.files,
            hasReadingContent: c.hasReadingContent, lessonContentHtml: c.lessonContentHtml,
            downloadAllowed: c.downloadAllowed, reflectionPrompt: c.reflectionPrompt,
            attendanceCompleted: c.attendanceCompleted, attendanceCompletedAt: c.attendanceCompletedAt,
            previousLearning: c.previousLearning,
            reflectionRequired: c.reflectionRequired,
            tutorValidationRequired: c.tutorValidationRequired,
            reflectionQuestion: c.reflectionQuestion,
            resourceUrl: c.resourceUrl,
            liveSessionUrl: c.liveSessionUrl, sessionDate: c.sessionDate, sessionTime: c.sessionTime,
            teamsLiveSessionId: c.teamsLiveSessionId,
            teamsSessionNumber: c.teamsSessionNumber,
            recordingSeriesId: c.recordingSeriesId,
            recordingSessionNumber: c.recordingSessionNumber,
            recordingDate: c.recordingDate,
            sessionDateTimeUtc: c.sessionDateTimeUtc,
            quizAttempts: c.isQuiz && c.quizMeta
              // Normalised progress rows store quiz_ref as text, while the
              // curriculum API exposes quiz ids as numbers. Compare their
              // canonical string values so saved attempts still decorate the
              // learner-facing quiz card with its Passed/Attempted status.
              ? quizAttemptsFor(c, real.quizAttempts)
              : undefined,
          }));
        return {
          week: w.week,
          // Quiz duration remains available to the quiz runner and is credited
          // as actual learner time after submission. It is not planned OTJH.
          otjh: components.reduce(
            (n, c) => n + (c.isQuiz || (c.type || '').toLowerCase() === 'quiz' ? 0 : (c.expectedOtjh || 0)),
            0,
          ),
          components,
        };
      }),
    };
  });
}
