import { type CurriculumModule, type CurriculumWeeklySession } from '@/lib/curriculumApi';
import { cleanText } from './model';


export interface ModuleFormDeliveryRef {
  /**
   * This delivery's own module row id -- the id `/curriculum/modules/<id>` and
   * the tutor-email ledger both use. A module delivered to three groups is
   * three rows, and the tick needs to ask about all of them.
   */
  deliveryModuleId?: string;
  programmeId?: string;
  programme?: string;
  cohortId?: string;
  cohort?: string;
  groupId?: string;
  group?: string;
  tutor?: string;
  startDate?: string;
  endDate?: string;
  sessions?: number;
  weeklySchedule?: CurriculumWeeklySession[];
  weekDays?: string;
  startTime?: string;
  endTime?: string;
}


/**
 * The module being edited, reduced to what this form reads and writes. Declared
 * rather than reusing `CurriculumModule` because the Module Builder holds its
 * modules as authoring catalogue items â€” same records, different field names â€”
 * and both call sites must end up patching the same canonical id.
 */
export interface ModuleFormTarget {
  /** Canonical module catalogue id: what PATCH /curriculum/modules/<id>/ takes. */
  id: string;
  name: string;
  programmeId?: string;
  programme?: string;
  cohortId?: string;
  groupId?: string;
  sessionsNumber?: number;
  weeklySchedule?: CurriculumWeeklySession[];
  sessionHolidays?: CurriculumModule['sessionHolidays'];
  deliveryWeeks?: number;
  weekDays?: string;
  startTime?: string;
  endTime?: string;
  weeks?: number;
  startDate?: string;
  /** The stored end date, shown until the session-plan preview returns its own. */
  endDate?: string;
  tutor?: string;
  status?: string;
  notes?: string;
  color?: string;
  /** The stored cover image: a URL, or a data: URL for an uploaded file. */
  coverImage?: string;
  deliveryUsages?: ModuleFormDeliveryRef[];
}


/**
 * A saved module reduced to what this form reads, so re-opening it edits the
 * record rather than starting a second one. It lives here, beside the shape it
 * builds, because every door onto this form needs it — the structure wizard
 * stepping back to its module step, and a programme workspace opening one of
 * its module rows.
 */
export function moduleFormTarget(module: CurriculumModule | undefined | null): ModuleFormTarget | null {
  if (!module) return null;
  const id = cleanText(module.moduleCatalogueId) || cleanText(module.catalogueId) || cleanText(module.moduleId) || cleanText(module.id);
  if (!id) return null;
  return {
    id,
    name: module.name || '',
    programmeId: module.programmeId,
    programme: module.programme,
    cohortId: module.cohortId,
    groupId: module.groupId,
    sessionsNumber: module.sessionsNumber,
    weeklySchedule: module.weeklySchedule,
    sessionHolidays: module.sessionHolidays,
    deliveryWeeks: module.deliveryWeeks,
    weekDays: module.weekDays,
    startTime: module.startTime,
    endTime: module.endTime,
    weeks: module.weeks,
    startDate: module.startDate,
    endDate: module.endDate,
    tutor: module.tutor,
    status: module.status,
    notes: module.notes,
    color: module.color,
    coverImage: module.coverImage,
  };
}
