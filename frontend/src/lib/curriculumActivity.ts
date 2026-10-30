/**
 * Curriculum's door onto the LMS-wide activity recorder.
 *
 * This module used to *be* the recorder, when the Audit Trail was curriculum's
 * alone. It now lives in `activityTrail.ts`, which records every workspace, and
 * this file is the compatibility surface over it: the names curriculum pages
 * already import keep working and keep meaning what they meant.
 *
 * One behavioural difference, and it is the point of the change: these
 * functions no longer ignore a path outside `/curriculum`. A shared component
 * calling `recordCurriculumAction` from another workspace now records there,
 * because the trail covers that workspace too. What is *not* recorded anywhere
 * is the excluded list in `activityTrail.ts` — the signed-out pages and the
 * learner content runner — enforced again server-side in
 * `backend/system_audit/pages.py`.
 *
 * New code should import from `@/lib/activityTrail` directly. Nothing here is
 * deprecated in the sense of going away; it is simply the older spelling.
 */
export {
  flushActivity as flushCurriculumActivity,
  isRecordedPath,
  recordAction as recordCurriculumAction,
  recordPageView as recordCurriculumPageView,
  recordSearch as recordCurriculumSearch,
  resetActivity as resetCurriculumActivity,
} from './activityTrail';

export type {
  ActivityDetail as CurriculumActivityDetail,
  ActivityKind as CurriculumActivityKind,
} from './activityTrail';
