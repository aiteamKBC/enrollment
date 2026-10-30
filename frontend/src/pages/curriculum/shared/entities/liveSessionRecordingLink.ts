// A session recording and the live session it records.
//
// Adding a live session adds a "Session Recording" component directly after
// it, linked. An author can also add a recording by hand and link it to any
// live session: another week of the same module, or another group's module.
//
// The link lives on the recording only, in two settings:
//   linkedLiveSessionComponentId  the live session's component id ('' = unlinked)
//   linkedLiveSessionModuleId     the module holding it ('' = this module)
// The live session itself carries nothing, so there is one place to read and
// one place to clear. A link naming a component that is gone, or is not a live
// session, reads as unlinked everywhere (the learner API applies the same rule).
//
// Linked, the recording plays the session's Teams recording and takes its
// date. Linked within the SAME module, the session and its recordings are one
// unit: moving either to another week moves all of them there, from whatever
// weeks they were in; a drag keeps a same-week recording after its session;
// deleting the session asks about its recordings. Unlinked, each moves alone.
// A link into ANOTHER module never moves anything there: that module belongs
// to another group, so only this module's component moves, still linked.
import type { ModuleComponent, ModuleWeek } from '@/pages/curriculum/module-builder/moduleAuthoringData';

export const LINKED_LIVE_SESSION_SETTING = 'linkedLiveSessionComponentId';
export const LINKED_LIVE_SESSION_MODULE_SETTING = 'linkedLiveSessionModuleId';
export const SESSION_RECORDING_TYPE = 'recording-placeholder';

export interface RecordingLinkTarget {
  componentId: string;
  /** '' = the recording's own module. */
  moduleId: string;
}

export function isSessionRecording(component: Pick<ModuleComponent, 'type'>): boolean {
  return component.type === SESSION_RECORDING_TYPE;
}

/** Where this recording's link points, whether or not it still resolves. */
export function recordingLinkTarget(component: ModuleComponent, ownModuleId = ''): RecordingLinkTarget | null {
  if (!isSessionRecording(component)) return null;
  const componentId = String(component.settings?.[LINKED_LIVE_SESSION_SETTING] || '').trim();
  if (!componentId) return null;
  const moduleId = String(component.settings?.[LINKED_LIVE_SESSION_MODULE_SETTING] || '').trim();
  return { componentId, moduleId: moduleId && moduleId !== ownModuleId ? moduleId : '' };
}

/** The live-session id this recording names, whether or not it still resolves. */
export function recordingLinkId(component: ModuleComponent): string {
  return recordingLinkTarget(component)?.componentId || '';
}

/** True when the recording points at a session in another module. */
export function isLinkedToAnotherModule(component: ModuleComponent, ownModuleId = ''): boolean {
  return Boolean(recordingLinkTarget(component, ownModuleId)?.moduleId);
}

/**
 * The live session in `components` (this module's, a whole module or a single
 * week) this recording is linked to. A link into another module never
 * resolves here -- those components are not in this module's list.
 */
export function linkedLiveSessionOf(components: ModuleComponent[], recording: ModuleComponent, ownModuleId = ''): ModuleComponent | null {
  const target = recordingLinkTarget(recording, ownModuleId);
  if (!target || target.moduleId) return null;
  return components.find(component => component.id === target.componentId && component.type === 'live-session') || null;
}

/** The recordings in `components` linked to this live session (same module). */
export function linkedRecordingsOf(components: ModuleComponent[], liveSession: ModuleComponent, ownModuleId = ''): ModuleComponent[] {
  if (liveSession.type !== 'live-session') return [];
  return components.filter(component => {
    const target = recordingLinkTarget(component, ownModuleId);
    return Boolean(target && !target.moduleId && target.componentId === liveSession.id);
  });
}

/**
 * Settings for a copy whose session is not coming with it into the same
 * module: the copy starts unlinked.
 */
export function withoutRecordingLink<T extends Record<string, unknown>>(settings: T): T {
  if (!(LINKED_LIVE_SESSION_SETTING in settings) && !(LINKED_LIVE_SESSION_MODULE_SETTING in settings)) return settings;
  return { ...settings, [LINKED_LIVE_SESSION_SETTING]: '', [LINKED_LIVE_SESSION_MODULE_SETTING]: '' };
}

/** This recording linked to `liveSessionId` (in `moduleId`, '' = its own), or unlinked with ''. */
export function withRecordingLink(recording: ModuleComponent, liveSessionId: string, moduleId = ''): ModuleComponent {
  return {
    ...recording,
    settings: {
      ...recording.settings,
      [LINKED_LIVE_SESSION_SETTING]: liveSessionId,
      [LINKED_LIVE_SESSION_MODULE_SETTING]: liveSessionId ? moduleId : '',
    },
  };
}

/**
 * Everything in `components` that moves as one with `component`: a live
 * session and every recording linked to it. Pass the whole module's components
 * and the group spans its weeks; pass one week and it is that week's part.
 * Only stored links count -- sharing a week links nothing -- and a link into
 * another module never resolves here, so such a recording moves alone.
 */
export function linkedGroupOf(components: ModuleComponent[], component: ModuleComponent, ownModuleId = ''): ModuleComponent[] {
  const liveSession = component.type === 'live-session' ? component : linkedLiveSessionOf(components, component, ownModuleId);
  if (!liveSession) return [component];
  const recordings = linkedRecordingsOf(components, liveSession, ownModuleId);
  if (!recordings.length) return [component];
  const ids = new Set([liveSession.id, ...recordings.map(item => item.id)]);
  return components.filter(item => ids.has(item.id));
}

/**
 * Turn a blank recording component into the one made beside `liveSession`.
 * The caller makes the blank (`createEmptyComponent`), so its id, week and
 * defaults come from the same factory as every other component.
 */
export function prepareLinkedRecording(blank: ModuleComponent, liveSession: ModuleComponent): ModuleComponent {
  return withRecordingLink({ ...blank, weekId: liveSession.weekId, title: 'Session Recording', description: '' }, liveSession.id);
}

/**
 * Insert a linked recording directly after each new live session in `added`
 * that does not have one yet. `makeBlank` mints the blank recording component.
 * A recording the author picked in the same batch stays as picked: unlinked.
 */
export function withLinkedRecordings(
  added: ModuleComponent[],
  makeBlank: (liveSession: ModuleComponent) => ModuleComponent,
): ModuleComponent[] {
  return added.flatMap(component => {
    if (component.type !== 'live-session' || linkedRecordingsOf(added, component).length) return [component];
    return [component, prepareLinkedRecording(makeBlank(component), component)];
  });
}

/**
 * After a reorder, put each live session's same-week linked recordings
 * straight after it, keeping their own order. Everything else stays exactly
 * where the drag left it.
 */
export function keepRecordingsWithTheirSession(components: ModuleComponent[]): ModuleComponent[] {
  const attached = new Map<string, ModuleComponent[]>();
  components.forEach(component => {
    const liveSession = linkedLiveSessionOf(components, component);
    if (liveSession) attached.set(liveSession.id, [...(attached.get(liveSession.id) || []), component]);
  });
  if (!attached.size) return components;
  const following = new Set(Array.from(attached.values()).flat().map(item => item.id));
  return components.flatMap(component => {
    if (following.has(component.id)) return [];
    return [component, ...(attached.get(component.id) || [])];
  });
}

/**
 * Re-point recording links inside a set of copied components. `copies[i]` is
 * the copy of `sources[i]` with a fresh id.
 *
 * A link to a session copied along with it now names that session's copy.
 * A link to anything else is kept when the copies stay in the SAME module (the
 * session they name is still there), and cleared when they go to another
 * module, so a copy never reaches back into the delivery it came from.
 */
export function relinkCopiedComponents(
  sources: ModuleComponent[],
  copies: ModuleComponent[],
  options: { sameModule?: boolean } = {},
): ModuleComponent[] {
  const copyIdBySourceId = new Map(sources.map((source, index) => [source.id, copies[index]?.id || '']));
  return copies.map((copy, index) => {
    const source = sources[index];
    if (!source || !isSessionRecording(copy)) return copy;
    const target = recordingLinkTarget(source);
    if (!target) return recordingLinkId(copy) ? withRecordingLink(copy, '') : copy;
    const copiedSession = !target.moduleId ? copyIdBySourceId.get(target.componentId) : '';
    if (copiedSession) return withRecordingLink(copy, copiedSession);
    return options.sameModule ? withRecordingLink(copy, target.componentId, target.moduleId) : withRecordingLink(copy, '');
  });
}

/**
 * Move `componentId` to `targetWeekId` with its whole linked group from this
 * module (`linkedGroupOf` over every week), in one pass so the group never
 * ends up half moved. The session goes first and its recordings straight
 * after it; when the session is already in the target week, the recordings
 * join it there. Settings are untouched, so every link survives the move; a
 * booked session keeps its Teams identity and date, and the rail's existing
 * date notice says when that no longer matches its week.
 */
export function moveComponentToWeek(weeks: ModuleWeek[], componentId: string, targetWeekId: string, ownModuleId = ''): ModuleWeek[] {
  const all = weeks.flatMap(week => week.components);
  const component = all.find(item => item.id === componentId);
  const targetWeek = weeks.find(week => week.id === targetWeekId);
  if (!component || !targetWeek) return weeks;
  const group = linkedGroupOf(all, component, ownModuleId);
  const liveSession = group.find(item => item.type === 'live-session');
  const ordered = liveSession ? [liveSession, ...group.filter(item => item !== liveSession)] : group;
  const arriving = ordered.filter(item => !targetWeek.components.some(existing => existing.id === item.id));
  if (!arriving.length) return weeks;
  const movingIds = new Set(arriving.map(item => item.id));
  const moved = arriving.map(item => ({ ...item, weekId: targetWeekId }));
  return weeks.map(week => {
    if (week.id !== targetWeekId) {
      return week.components.some(item => movingIds.has(item.id))
        ? { ...week, components: week.components.filter(item => !movingIds.has(item.id)) }
        : week;
    }
    // The session already sits here: its recordings follow it (and any of
    // its recordings already here), rather than landing at the end.
    if (liveSession && !movingIds.has(liveSession.id)) {
      const anchorIndex = Math.max(...ordered.map(item => week.components.findIndex(existing => existing.id === item.id)));
      const components = [...week.components];
      components.splice(anchorIndex + 1, 0, ...moved);
      return { ...week, components };
    }
    // The session arrives: the group lands at the end in order, gathering
    // any of its recordings that were already here behind it.
    const groupIds = new Set(ordered.map(item => item.id));
    const gathered = ordered.map(item => moved.find(m => m.id === item.id) || week.components.find(existing => existing.id === item.id)!);
    return { ...week, components: [...week.components.filter(item => !groupIds.has(item.id)), ...gathered] };
  });
}

/**
 * The week after removing `componentId`. Removing a live session with
 * `withRecordings` removes its same-week linked recordings too; otherwise they
 * stay and are unlinked, so none is left naming a session that no longer
 * exists. (A recording in another week names a missing session afterwards and
 * reads as unlinked; its own editor says so.)
 */
export function removeComponentFromWeek(components: ModuleComponent[], componentId: string, withRecordings: boolean): ModuleComponent[] {
  const removed = components.find(component => component.id === componentId);
  if (!removed) return components;
  const recordings = linkedRecordingsOf(components, removed);
  const dropping = new Set([componentId, ...(withRecordings ? recordings.map(item => item.id) : [])]);
  return components
    .filter(component => !dropping.has(component.id))
    .map(component => (linkedLiveSessionOf(components, component)?.id === componentId ? withRecordingLink(component, '') : component));
}

/** One week of a module, as the recording-link pickers list it. */
export interface RecordingLinkWeek {
  id: string;
  label: string;
  components: ModuleComponent[];
}

/** The live sessions of a module, each with the week it sits in. */
export function liveSessionsByWeek(weeks: RecordingLinkWeek[]): { week: RecordingLinkWeek; liveSession: ModuleComponent }[] {
  return weeks.flatMap(week => week.components
    .filter(component => component.type === 'live-session')
    .map(liveSession => ({ week, liveSession })));
}

/**
 * What a recording/live-session editor and the rail need to show and change
 * links beyond one week. The module builder passes its whole module and the
 * other modules it can link to; a week template passes its single week.
 */
/** A module a recording may link into, with where it is delivered so the picker can filter by it. */
export interface LinkableModule {
  id: string;
  label: string;
  programmeId?: string;
  programme?: string;
  cohortId?: string;
  cohort?: string;
  groupId?: string;
  group?: string;
}

export interface RecordingLinkContext {
  /** This module's catalogue id ('' for a week template). */
  moduleId: string;
  weeks: RecordingLinkWeek[];
  /** Other modules a recording may link into, and how to read their weeks. */
  otherModules?: LinkableModule[];
  loadModuleWeeks?: (moduleId: string) => Promise<RecordingLinkWeek[]>;
  /** Set (or clear, with '') the link on the recording `recordingId`, wherever it sits. */
  setLink: (recordingId: string, liveSessionId: string, moduleId: string) => void;
  /** Add a recording linked to this live session, straight after it. */
  addLinkedRecording?: (liveSession: ModuleComponent) => void;
  /** Remove these components from whichever weeks of this module hold them. */
  removeComponents?: (componentIds: string[]) => void;
}

/**
 * The recordings in this module linked to `liveSession`, split by whether they
 * sit in `weekId` (the rail edits that week itself) or in another week (which
 * only the module-wide context can change).
 */
export function linkedRecordingsByPlace(context: Pick<RecordingLinkContext, 'moduleId' | 'weeks'>, liveSession: ModuleComponent, weekId: string) {
  const sameWeek: ModuleComponent[] = [];
  const otherWeeks: { week: RecordingLinkWeek; recording: ModuleComponent }[] = [];
  context.weeks.forEach(week => linkedRecordingsOf(week.components, liveSession, context.moduleId).forEach(recording => {
    if (week.id === weekId) sameWeek.push(recording);
    else otherWeeks.push({ week, recording });
  }));
  return { sameWeek, otherWeeks };
}

/**
 * What moving `component` takes with it, for the Move menu to say before
 * anything moves: the other members of its linked group in this module, each
 * with the week it is in now. Empty when it moves alone.
 */
export function movingPartnersOf(component: ModuleComponent, context: Pick<RecordingLinkContext, 'moduleId' | 'weeks'>): { week: RecordingLinkWeek; component: ModuleComponent }[] {
  const all = context.weeks.flatMap(week => week.components);
  return linkedGroupOf(all, component, context.moduleId)
    .filter(item => item.id !== component.id)
    .map(item => ({ week: context.weeks.find(week => week.components.some(c => c.id === item.id))!, component: item }));
}

/** "Linked to …" for the rail and editors; '' for a component with no link to describe. */
export function describeRecordingLink(component: ModuleComponent, context: Pick<RecordingLinkContext, 'moduleId' | 'weeks' | 'otherModules'>, weekId: string): string {
  if (isSessionRecording(component)) {
    const target = recordingLinkTarget(component, context.moduleId);
    if (!target) return 'Not linked';
    if (target.moduleId) {
      const module = context.otherModules?.find(item => item.id === target.moduleId);
      return `Linked to a session in ${module?.label || 'another module'}`;
    }
    const found = liveSessionsByWeek(context.weeks).find(item => item.liveSession.id === target.componentId);
    if (!found) return 'Linked session missing';
    const title = found.liveSession.title || 'live session';
    return found.week.id === weekId ? `Linked to ${title}` : `Linked to ${title} · ${found.week.label}`;
  }
  if (component.type !== 'live-session') return '';
  const count = context.weeks.reduce((total, week) => total + linkedRecordingsOf(week.components, component, context.moduleId).length, 0);
  return count ? (count === 1 ? 'Recording linked' : `${count} recordings linked`) : '';
}
