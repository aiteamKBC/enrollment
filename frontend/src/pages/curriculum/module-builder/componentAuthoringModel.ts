import { assignmentTopics, topicHasContent } from '@/lib/assignmentTopics';
export type ModuleStatus = 'draft' | 'review' | 'published' | string;
export type KsbMappingType = 'main' | 'secondary' | 'possible';
export type ModuleComponentType =
  | 'live-session'
  | 'video'
  | 'podcast'
  | 'reading'
  | 'powerpoint'
  | 'quiz'
  | 'monthly-ksb-quiz'
  | 'reflection'
  | 'assignment'
  | 'checkpoint'
  | 'coaching-preparation'
  | 'recording-placeholder'
  | 'workplace-evidence';

export type ComponentSettingValue = string | number | boolean | string[];
export type ComponentSettings = Record<string, ComponentSettingValue>;

export type ComponentCapability =
  | 'preview'
  | 'media'
  | 'rich-text'
  | 'ksb-mapping'
  | 'reflection'
  | 'evidence'
  | 'tutor-validation';

export interface ComponentAuthoringDefinition {
  type: ModuleComponentType;
  label: string;
  icon: string;
  group: 'Live & recorded' | 'Learning materials' | 'Assessment' | 'Monthly cycle';
  tone: string;
  defaultOtjh: number;
  defaultPoints: number;
  reflectionDefault: boolean;
  workplaceEvidenceDefault: boolean;
  tutorValidationDefault: boolean;
  supportedSources: readonly string[];
  requiredSettings: readonly string[];
  capabilities: readonly ComponentCapability[];
  defaultSettings: ComponentSettings;
}

export const MEDIA_SOURCE_TYPES = ['YouTube', 'External Link', 'HTML (MP4)', 'Embed'] as const;
export const PODCAST_SOURCE_TYPES = ['External URL', 'LMS resource', 'Device upload', 'Embed'] as const;
export const READING_SOURCE_TYPES = ['Written in LMS', 'URL', 'LMS resource'] as const;
export const CONTENT_STATUSES = ['Draft', 'Ready for QA', 'Needs changes', 'Approved'] as const;

function advancedDefaults(type: ModuleComponentType): ComponentSettings {
  const completionRules: Partial<Record<ModuleComponentType, string>> = {
    'live-session': 'Attend or watch recording',
    'recording-placeholder': 'Mark complete after watching',
    video: 'Watch video and mark complete',
    podcast: 'Listen and mark complete',
    reading: 'Read the material and confirm completion',
    powerpoint: 'Review slide deck',
    quiz: 'Submit',
    'monthly-ksb-quiz': 'Submit monthly KSB quiz',
    reflection: 'Submit reflection',
    assignment: 'Submit assignment',
    checkpoint: 'Complete checkpoint',
    'coaching-preparation': 'Complete coaching preparation',
  };
  const evidenceRequired: Partial<Record<ModuleComponentType, string>> = {
    'live-session': 'Attendance or recording completion',
    quiz: 'Quiz result',
    'monthly-ksb-quiz': 'Quiz result',
    checkpoint: 'Quiz result',
    reflection: 'Reflection + signature',
    assignment: 'Submission file',
    'coaching-preparation': 'Preparation notes',
  };
  return {
    completionRule: completionRules[type] || 'Mark complete',
    evidenceRequired: evidenceRequired[type] || '-',
    reflectionPrompt: defaultReflectionPrompt(type),
    contentStatus: 'Draft',
    version: '0.1',
  };
}

const definitions: ComponentAuthoringDefinition[] = [
  {
    type: 'live-session',
    label: 'Live Teams Session',
    icon: 'ri-group-line',
    group: 'Live & recorded',
    tone: 'violet',
    defaultOtjh: 2,
    defaultPoints: 10,
    reflectionDefault: false,
    workplaceEvidenceDefault: false,
    tutorValidationDefault: false,
    supportedSources: [],
    requiredSettings: [],
    capabilities: ['ksb-mapping', 'reflection', 'evidence', 'tutor-validation'],
    defaultSettings: {
      ...advancedDefaults('live-session'),
      sessionPurpose: '',
      sessionDate: '',
      // The weekday `sessionDate` falls on, stamped alongside it by the session
      // planner on both sides. Declared because it is written: a key the code
      // stores but the schema does not list is rejected as unsupported the next
      // time the module is saved, which is a save the author cannot get past.
      sessionDay: '',
      sessionTime: '',
      sessionDateTimeUtc: '',
      durationMinutes: 60,
      // `selectedGroupKeys`/`selectedGroupNames` are deliberately NOT declared
      // here although the backend schema has them: group assignment strips them
      // with `delete`, and a duplicated component has to come back with them
      // absent rather than as an empty default that reads as a real placement.
      liveSessionUrl: '',
      teamsEventId: '',
      teamsLiveSessionId: '',
      // Written when a calendar is created or a plan is stamped. Same reason as
      // `sessionDay`: these are the keys `applyModuleTeamsSeries`, the session
      // planner and the schedule editor actually store on a live session.
      teamsStartDateTimeUtc: '',
      teamsMeetingUrl: '',
      teamsOnlineMeetingId: '',
      teamsCalendarSeries: '',
      // Empty, not `0`, like every other Teams key here. These hold numbers once
      // a calendar exists, but "no meeting" is the empty default a duplicated
      // component is stripped back to, and `0` would read as a real occurrence.
      teamsDurationMinutes: '',
      teamsSessionNumber: '',
      teamsMeetingOptionsUrl: '',
      teamsOrganizerEmail: '',
      teamsAttendees: [],
      teamsPresenters: [],
      teamsCoOrganizers: [],
      teamsProvider: '',
      teamsRepeat: 'none',
      teamsRepeatOccurrences: 1,
      teamsLobbyBypass: 'everyone',
      teamsRecording: 'record-transcribe',
      teamsSpokenLanguage: 'en-GB',
      teamsMeetingType: 'live-session',
      teamsRequestResponses: true,
      teamsAllowTimeProposals: true,
      // Default to not disclosing every invitee's address to every other
      // invitee; a cohort mixes learners with staff from unrelated employers.
      teamsHideAttendees: true,
      preparationInstructions: '',
      reflectionQuestions: '',
      attendanceRequired: true,
      recordingExpected: true,
    },
  },
  {
    // Made beside every new live session and linked to it, or added by hand and
    // linked to any live session -- another week, or another group's module
    // (see shared/entities/liveSessionRecordingLink.ts). Linked, it plays that
    // session's Teams recording and takes its date; unlinked, it is a recording
    // of its own and needs `recordingUrl`. No OTJH or points of its own: the
    // session it records already carries them, and counting both would claim
    // the same hour twice.
    type: 'recording-placeholder',
    label: 'Session Recording',
    icon: 'ri-record-circle-line',
    group: 'Live & recorded',
    tone: 'purple',
    defaultOtjh: 0,
    defaultPoints: 0,
    reflectionDefault: false,
    workplaceEvidenceDefault: false,
    tutorValidationDefault: false,
    supportedSources: [],
    requiredSettings: [],
    capabilities: ['ksb-mapping', 'reflection', 'tutor-validation'],
    // Same keys and defaults as COMPONENT_SETTINGS_SCHEMA['recording-placeholder']
    // in curriculum_api/views.py.
    defaultSettings: {
      ...advancedDefaults('recording-placeholder'),
      recordingPurpose: '',
      source: 'MIS allocation',
      recordingUrl: '',
      expectedAvailability: 'After live session',
      linkedLiveSessionComponentId: '',
      linkedLiveSessionModuleId: '',
    },
  },
  {
    type: 'video',
    label: 'Video',
    icon: 'ri-video-line',
    group: 'Learning materials',
    tone: 'rose',
    defaultOtjh: 0,
    defaultPoints: 10,
    reflectionDefault: false,
    workplaceEvidenceDefault: false,
    tutorValidationDefault: false,
    supportedSources: MEDIA_SOURCE_TYPES,
    requiredSettings: [],
    capabilities: ['media', 'preview', 'rich-text', 'ksb-mapping', 'reflection', 'evidence', 'tutor-validation'],
    defaultSettings: {
      ...advancedDefaults('video'),
      sourceType: 'YouTube',
      provider: 'YouTube',
      videoUrl: '',
      embedCode: '',
      durationMinutes: 10,
      requiredProgressPercentage: 0,
      shortDescription: '',
      learningBrief: '',
      lessonContent: '',
      lessonMaterialLinks: '',
      lessonMaterialsNotes: '',
      postWatchTask: '',
      markersAndQuestions: '',
      qAndA: '',
    },
  },
  {
    type: 'podcast',
    label: 'Podcast',
    icon: 'ri-mic-line',
    group: 'Learning materials',
    tone: 'amber',
    defaultOtjh: 2,
    defaultPoints: 10,
    reflectionDefault: false,
    workplaceEvidenceDefault: false,
    tutorValidationDefault: false,
    supportedSources: PODCAST_SOURCE_TYPES,
    requiredSettings: [],
    capabilities: ['media', 'preview', 'ksb-mapping', 'reflection', 'tutor-validation'],
    defaultSettings: { ...advancedDefaults('podcast'), podcastSource: 'External URL', podcastUrl: '', embedCode: '', shortcode: '', componentFiles: '', uploadedFileName: '', uploadedFileUrl: '', uploadedFileSize: 0, uploadedFileContentType: '', uploadSource: '', durationMinutes: 20, requiredProgressPercentage: 0, listeningFocus: '', podcastReflectionQuestion: '', transcript: '' },
  },
  {
    type: 'reading',
    label: 'Reading Material',
    icon: 'ri-book-open-line',
    group: 'Learning materials',
    tone: 'emerald',
    defaultOtjh: 2,
    defaultPoints: 10,
    reflectionDefault: false,
    workplaceEvidenceDefault: false,
    tutorValidationDefault: false,
    supportedSources: READING_SOURCE_TYPES,
    requiredSettings: [],
    capabilities: ['preview', 'rich-text', 'ksb-mapping', 'reflection', 'tutor-validation'],
    defaultSettings: {
      ...advancedDefaults('reading'),
      difficulty: 'Standard',
      requirement: 'Required',
      readingSource: 'Written in LMS',
      resourceUrl: '',
      componentFiles: '',
      uploadedFileName: '',
      uploadedFileUrl: '',
      uploadedFileSize: 0,
      uploadedFileContentType: '',
      uploadSource: '',
      shortDescription: '',
      readingContent: '',
      mainLearningOutcomes: '',
      ksbEvidenceNotes: '',
      focusSections: '',
      learnerInstruction: '',
      keyPointCount: '0',
      keyPoints: '',
      glossaryTerms: '',
      estimatedReadingTime: 20,
      otjhRationale: '',
      audioEnabled: false,
      audioUrl: '',
      reflectionQuestionCount: '0 qs',
      readingReflectionPrompts: '',
      readingEvidenceRequired: '',
      completionRuleCount: '3 rules',
      completionConfirmationRequired: true,
      linkedActivity: '',
      coachingPrompt: '',
      requiredReading: true,
    },
  },
  {
    type: 'powerpoint',
    label: 'PowerPoint',
    icon: 'ri-file-ppt-2-line',
    group: 'Learning materials',
    tone: 'orange',
    defaultOtjh: 2,
    defaultPoints: 5,
    reflectionDefault: false,
    workplaceEvidenceDefault: false,
    tutorValidationDefault: false,
    supportedSources: ['External URL', 'LMS resource', 'Device upload'],
    requiredSettings: [],
    capabilities: ['preview', 'ksb-mapping', 'reflection', 'tutor-validation'],
    defaultSettings: { ...advancedDefaults('powerpoint'), fileName: '', presentationUrl: '', componentFiles: '', uploadedFileName: '', uploadedFileUrl: '', uploadedFileSize: 0, uploadedFileContentType: '', uploadSource: '', slideRange: '', speakerNotes: '', downloadAllowed: true },
  },
  {
    type: 'quiz',
    label: 'Quiz',
    icon: 'ri-questionnaire-line',
    group: 'Assessment',
    tone: 'sky',
    defaultOtjh: 2,
    defaultPoints: 20,
    reflectionDefault: false,
    workplaceEvidenceDefault: false,
    tutorValidationDefault: false,
    supportedSources: [],
    requiredSettings: [],
    capabilities: ['ksb-mapping', 'reflection', 'tutor-validation'],
    defaultSettings: { ...advancedDefaults('quiz'), buildMode: 'linked', linkedQuizId: '', linkedActivity: '', numberOfQuestions: 10, passMarkPercentage: 70, attemptsAllowed: 2, affectsKsbProgression: true, completionFeedback: '' },
  },
  {
    type: 'assignment',
    label: 'Assignment',
    icon: 'ri-file-list-3-line',
    group: 'Assessment',
    tone: 'purple',
    defaultOtjh: 2,
    defaultPoints: 20,
    reflectionDefault: false,
    workplaceEvidenceDefault: false,
    tutorValidationDefault: true,
    supportedSources: [],
    requiredSettings: [],
    capabilities: ['ksb-mapping', 'reflection', 'evidence', 'tutor-validation'],
    defaultSettings: {
      ...advancedDefaults('assignment'),
      assignmentBrief: '',
      assignmentTopics: '',
      submissionInstructions: '',
      dueTiming: 'End of week',
      markingRubric: '',
      uploadedFileName: '',
      uploadedFileUrl: '',
      uploadedFileSize: 0,
      uploadedFileContentType: '',
      uploadSource: '',
      assignmentFileName: '',
      assignmentFileUrl: '',
      // Which brief tab the assignment is authored on — 'Text' (written brief)
      // or 'File' (uploaded document). Must be an allowed key or normalisation
      // strips it on save/reload, resetting the editor to the written-brief tab
      // and hiding an uploaded file that is actually still stored.
      assignmentSource: 'Text',
      // The title learners see in place of `title` (blank keeps `title`), and
      // whether this is the eight-step monthly assignment. Read by
      // learner_api.assignment_settings; declared in the backend schema too.
      learnerTitle: '',
      isMonthlyAssignment: false,
    },
  },
  {
    type: 'reflection',
    label: 'Reflection',
    icon: 'ri-chat-quote-line',
    group: 'Assessment',
    tone: 'teal',
    defaultOtjh: 0.5,
    defaultPoints: 15,
    reflectionDefault: true,
    workplaceEvidenceDefault: false,
    tutorValidationDefault: true,
    supportedSources: [],
    requiredSettings: ['reflectionPrompt'],
    capabilities: ['ksb-mapping', 'reflection', 'tutor-validation'],
    defaultSettings: { ...advancedDefaults('reflection'), minimumWordCount: 250, learnerGuidance: '', tutorReviewGuidance: '' },
  },
  {
    type: 'checkpoint',
    label: 'Checkpoint Quiz',
    icon: 'ri-checkbox-circle-line',
    group: 'Monthly cycle',
    tone: 'blue',
    defaultOtjh: 2,
    defaultPoints: 20,
    reflectionDefault: false,
    workplaceEvidenceDefault: false,
    tutorValidationDefault: false,
    supportedSources: [],
    requiredSettings: [],
    capabilities: ['ksb-mapping', 'reflection', 'tutor-validation'],
    defaultSettings: { ...advancedDefaults('checkpoint'), checkpointTitle: '', checkpointQuestions: '', progressReviewLinked: true, monthlyCoachingReviewLinked: true },
  },
  {
    type: 'monthly-ksb-quiz',
    label: 'Monthly KSB Quiz',
    icon: 'ri-award-line',
    group: 'Monthly cycle',
    tone: 'violet',
    defaultOtjh: 2,
    defaultPoints: 20,
    reflectionDefault: false,
    workplaceEvidenceDefault: false,
    tutorValidationDefault: false,
    supportedSources: [],
    requiredSettings: [],
    capabilities: ['ksb-mapping', 'reflection', 'tutor-validation'],
    defaultSettings: { ...advancedDefaults('monthly-ksb-quiz'), buildMode: 'linked', linkedQuizId: '', linkedActivity: '', numberOfQuestions: 12, passMarkPercentage: 70, attemptsAllowed: 2, affectsKsbProgression: true, monthFocus: '' },
  },
  {
    type: 'coaching-preparation',
    label: 'Coaching Preparation',
    icon: 'ri-user-heart-line',
    group: 'Monthly cycle',
    tone: 'pink',
    defaultOtjh: 0.5,
    defaultPoints: 10,
    reflectionDefault: true,
    workplaceEvidenceDefault: false,
    tutorValidationDefault: true,
    supportedSources: [],
    requiredSettings: [],
    capabilities: ['ksb-mapping', 'reflection', 'evidence', 'tutor-validation'],
    defaultSettings: { ...advancedDefaults('coaching-preparation'), preparationPrompt: '', evidenceToBring: '', coachDiscussionPoints: '', coachingReviewLinked: true },
  },
];

export const componentAuthoringDefinitions = Object.freeze(definitions);
// Only the component types the week builder itself offers are addable here —
// keeps the module builder's "Add component" picker in lockstep with the
// week builder's palette. The others (reflection/checkpoint/monthly-ksb-quiz/
// coaching-preparation) stay defined (existing data may still use them) but
// hidden from the picker. A session recording is offered too: one is made
// with every live session, and an author can add more by hand and link them.
const hiddenComponentTypes = new Set<ModuleComponentType>([
  'reflection',
  'checkpoint',
  'monthly-ksb-quiz',
  'coaching-preparation',
]);
export const componentTypes = componentAuthoringDefinitions
  .filter(({ type }) => !hiddenComponentTypes.has(type))
  .map(({ type, label, icon, group, tone }) => ({ type, label, icon, group, tone }));
export const componentTypeGroups = Array.from(new Set(componentTypes.map(item => item.group)));

export function getComponentDefinition(type: ModuleComponentType | string) {
  return componentAuthoringDefinitions.find(item => item.type === type) || componentAuthoringDefinitions.find(item => item.type === 'reading')!;
}

export function getDefaultComponentSettings(type: ModuleComponentType): ComponentSettings {
  return { ...getComponentDefinition(type).defaultSettings };
}

// Keys the Week Builder's component editor writes that the Module Builder's own
// (now largely superseded) editor never had. Since the Module Builder now
// renders imported weeks with the Week Builder's editor, these must survive
// normalisation on every update — otherwise the editor reads its own keys back
// as empty (missing embed code, uploaded files, assigned groups, checkpoint
// flag, etc.). They're allowed on every type: the group selector and quiz
// checkpoint controls apply broadly, and carrying an unused key costs nothing.
const WEEK_BUILDER_SHARED_KEYS = [
  'selectedGroupKeys',        // assigned-groups multi-select (all component types)
  'selectedGroupNames',
  // Where this component was copied *to* via "Assigned groups" — one entry per
  // group key, parallel arrays (ComponentSettingValue has no object type).
  // Lets deselecting a group warn about, and cascade-delete, the placed copy.
  'placedCopyGroupKeys',
  'placedCopyModuleCatalogueIds',
  'placedCopyWeekIds',
  'placedCopyComponentIds',
  'podcastEmbedCode',         // podcast "Embed" source snippet
  'powerpointSource',         // legacy PowerPoint source toggle; the editor no longer writes it
  'quizAssessmentType',       // quiz/checkpoint flag stored on the component
  'quizProgramme',
  'quizModule',
  'quizWeekId',
  'quizDuration',
  'quizStatus',
  'assignmentSource',         // assignment Brief tab toggle ("Written brief" vs "Uploaded file")
  'assignmentContent',        // assignment written brief (Week Builder key)
  'uploadedFileName',         // reading/assignment uploaded-file metadata
  'uploadedFileUrl',
  'uploadedFileSize',
  'uploadedFileContentType',
  'uploadSource',
] as const;

// These are persisted per occurrence by the Teams attachment path. Keeping
// them only in legacySettings disconnects previews and subsequent saves.
// `teamsMeetingScope` is which calendar runs the session (main or additional);
// the Teams dialog sets it and the server keeps the stored one on every save.
const LIVE_SESSION_TRACKING_SETTING_KEYS = [
  'teamsOccurrenceId', 'teamsSessionNumber', 'teamsOnlineMeetingId',
  'teamsMeetingUrl', 'teamsWebLink', 'teamsStartDateTimeUtc',
  'teamsDurationMinutes', 'sessionDay', 'sessionRescheduled',
  'teamsMeetingScope',
  // Explicit bulk link replacements are authoring overrides. They preserve
  // the Teams identity and occurrence metadata while keeping the chosen link
  // through the next module-structure save.
  'liveSessionLinkOverride',
  'teamsCalendarBinding',
  'teamsMainLinkInherited',
] as const;

// These fields route a recording to its dated component, independently of
// which Teams occurrence currently stores the file.
const RECORDING_SOURCE_SETTING_KEYS = ['recordingSeriesId', 'recordingSessionNumber', 'recordingDate'] as const;

/**
 * The one-off meeting booked onto a single week, under its own keys.
 *
 * Deliberately not `liveSessionUrl`/`teamsMeetingUrl`: those two name the
 * MODULE's calendar, and the backend rewrites them on every live-session
 * component each time a module with a calendar is saved. An extra meeting
 * stored there would be replaced by the module's link without a word. Here it
 * survives, and the module's own calendar behaves exactly as it always did.
 */
export const ADDITIONAL_TEAMS_MEETING_SETTING_KEYS = [
  'extraTeamsMeetingUrl', 'extraTeamsLiveSessionId', 'extraTeamsEventId',
  'extraTeamsOnlineMeetingId', 'extraTeamsWebLink', 'extraTeamsMeetingOptionsUrl',
  'extraTeamsOrganizerEmail', 'extraTeamsAttendees', 'extraTeamsPresenters',
  'extraTeamsCoOrganizers', 'extraTeamsStartDateTimeUtc', 'extraTeamsDurationMinutes',
  'extraTeamsSubject',
] as const;

export function allowedSettingKeysForType(type: ModuleComponentType) {
  const definition = getComponentDefinition(type);
  return new Set([
    ...Object.keys(definition.defaultSettings),
    ...WEEK_BUILDER_SHARED_KEYS,
    ...(['live-session', 'video', 'recording-placeholder'].includes(type) ? RECORDING_SOURCE_SETTING_KEYS : []),
    ...(type === 'live-session' ? [...LIVE_SESSION_TRACKING_SETTING_KEYS, ...ADDITIONAL_TEAMS_MEETING_SETTING_KEYS] : []),
    'legacySettings',
    'legacySourceType',
    'legacyUnsupportedSource',
    'shortcode',
  ]);
}

export function normaliseComponentSettings(type: ModuleComponentType, settings: ComponentSettings = {}): ComponentSettings {
  let storedLegacy: ComponentSettings = {};
  const rawLegacy: unknown = settings.legacySettings;
  if (rawLegacy && typeof rawLegacy === 'object' && !Array.isArray(rawLegacy)) {
    storedLegacy = rawLegacy as ComponentSettings;
  } else if (typeof rawLegacy === 'string') {
    try {
      const parsed = JSON.parse(rawLegacy);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) storedLegacy = parsed as ComponentSettings;
    } catch {
      storedLegacy = {};
    }
  }
  const source: ComponentSettings = { ...storedLegacy, ...settings };
  // The Week Builder editor (now the live editor in the Module Builder too) and
  // the older Module Builder vocabulary use different keys for the same data. We
  // keep BOTH populated and never drop the Week Builder key, so the editor reads
  // its keys back intact while learner-facing/backend consumers still find the
  // Module Builder key. For the two keys that clash on VALUE (readingSource,
  // podcastSource) the Week Builder value is canonical, since it's the only
  // editor rendering these now — we translate the old Module values forward.
  // Each pair below is a one-time migration for a record saved under the old
  // key before the Week Builder key existed — never a live sync. It has to
  // run on *undefined* (the key was never written), not on falsy: this
  // function re-runs on every keystroke via `recalculateModule`, and a falsy
  // check cannot tell "never authored" from "the person just deleted it back
  // to empty" — so it was copying the stale old value straight back over an
  // intentional clear, on the very same update, before the empty field ever
  // painted. That is what made a field read as impossible to clear.
  const migrateKey = (fromKey: string, toKey: string) => {
    if (source[toKey] === undefined && source[fromKey] !== undefined) source[toKey] = source[fromKey];
  };
  if (type === 'podcast') {
    migrateKey('podcastEmbedCode', 'embedCode');
    migrateKey('embedCode', 'podcastEmbedCode');
    if (!source.shortcode && source.podcastShortcode) source.shortcode = source.podcastShortcode;
    if (source.podcastSource === 'Device upload') source.podcastSource = 'Audio File';
    if (source.podcastSource === 'External URL') source.podcastSource = 'External Link';
    if (source.podcastSource === 'Audio File' && !source.podcastUrl && source.uploadedFileUrl) source.podcastUrl = source.uploadedFileUrl;
  }
  if (type === 'reading') {
    if (source.readingSource === 'Written in LMS') source.readingSource = 'Text';
    if (source.readingSource === 'LMS resource') source.readingSource = 'File';
    migrateKey('uploadedFileUrl', 'resourceUrl');
    migrateKey('resourceUrl', 'uploadedFileUrl');
  }
  if (type === 'assignment') {
    // Recover assignments saved before `assignmentSource` was persisted: an
    // uploaded document with no written brief was authored on the File tab, so
    // reopen it there instead of the (default) written-brief tab.
    if (!source.assignmentSource && (source.uploadedFileUrl || source.assignmentFileUrl) && !source.assignmentContent && !source.assignmentBrief) {
      source.assignmentSource = 'File';
    }
    migrateKey('assignmentContent', 'assignmentBrief');
    migrateKey('assignmentBrief', 'assignmentContent');
    migrateKey('uploadedFileName', 'assignmentFileName');
    migrateKey('assignmentFileName', 'uploadedFileName');
    migrateKey('uploadedFileUrl', 'assignmentFileUrl');
    migrateKey('assignmentFileUrl', 'uploadedFileUrl');
  }
  const defaults = getDefaultComponentSettings(type);
  const allowed = allowedSettingKeysForType(type);
  const next: ComponentSettings = { ...defaults };
  const legacySettings: Record<string, unknown> = Object.create(null);
  Object.entries(source).forEach(([key, value]) => {
    if (value === undefined) return;
    if (key === 'legacySettings') return;
    if (allowed.has(key)) {
      if (value !== null) next[key] = value;
    } else {
      // Quarantined JSON is opaque authored data, including nested values/null.
      // Ordinary editor saves must not discard fields this editor does not own.
      legacySettings[key] = value;
    }
  });
  if (Object.keys(legacySettings).length) next.legacySettings = JSON.stringify(legacySettings);
  if (type === 'video' && (source.sourceType === 'Shortcode' || source.provider === 'Shortcode')) {
    next.legacySourceType = 'Shortcode';
    next.legacyUnsupportedSource = true;
  }
  return next;
}

export function normaliseVideoSourceType(value: string) {
  const clean = String(value || '').trim();
  if ((MEDIA_SOURCE_TYPES as readonly string[]).includes(clean)) return clean;
  // Older source labels are kept readable while the current source list stays
  // deliberately small. HTML (MP4) is a supported source in the builder and
  // must remain distinct so the editor can show the MP4-specific field label.
  if (clean === 'Upload file') return 'HTML (MP4)';
  if (clean === 'Vimeo' || clean === 'External link') return 'External Link';
  if (clean === 'Shortcode') return 'YouTube';
  return 'YouTube';
}

export function providerForVideoSourceType(sourceType: string) {
  if (sourceType === 'HTML (MP4)') return 'Upload file';
  if (sourceType === 'External Link') return 'External link';
  return sourceType;
}

export function defaultCompletionRule(type: ModuleComponentType) {
  return String(advancedDefaults(type).completionRule || 'Mark complete');
}

export function defaultEvidenceRequired(type: ModuleComponentType) {
  return String(advancedDefaults(type).evidenceRequired || '-');
}

export function defaultReflectionPrompt(type: ModuleComponentType) {
  if (type === 'quiz' || type === 'monthly-ksb-quiz' || type === 'checkpoint') return 'Which questions or topics do you need to revisit after this activity?';
  return 'What did you learn? How will you apply this at work? Which KSBs did this develop?';
}

// The one-line blurb the module builder's own type picker shows under each
// type ("Tutor-led session via Teams", …) and stamps straight onto a
// component's `description` when it is added from there (`createNamedComponent`
// in module-builder/page.tsx). The week rail's inline "Add component" leaves
// `description` empty instead -- so a component created from the OTHER add
// button would otherwise read as authored from the moment it exists, purely
// because of which button made it. `componentLooksUnedited` treats this text
// the same way it treats an empty description: not something an author typed.
const COMPONENT_TYPE_DESCRIPTIONS: Record<ModuleComponentType, string> = {
  'live-session': 'Tutor-led session via Teams',
  video: 'Upload or link a video',
  podcast: 'Upload audio or podcast link',
  reading: 'PDF, Word, or typed text',
  powerpoint: 'Slide deck for the week',
  quiz: 'Short weekly check',
  assignment: 'Monthly submission task',
  reflection: 'Learner written reflection',
  checkpoint: 'End-of-month KSB check',
  'monthly-ksb-quiz': 'Tracks KSB progression',
  'coaching-preparation': 'Monthly coaching meeting prep',
  'recording-placeholder': 'Teams recording placeholder',
  'workplace-evidence': 'Workplace evidence upload',
};

export function componentTypeDescription(type: ModuleComponentType) {
  return COMPONENT_TYPE_DESCRIPTIONS[type] || 'Add a component';
}

export type ComponentValidationTarget = {
  title: string;
  type: ModuleComponentType;
  expectedOtjh: number;
  points: number;
  reflectionRequired: boolean;
  reflectionQuestion?: string;
  workplaceEvidenceRequired: boolean;
  settings: ComponentSettings;
};

export type ModuleValidationTarget = {
  title: string;
  weekStructure: Array<{
    title: string;
    components: ComponentValidationTarget[];
  }>;
};

export type ValidationIssue = { path: string; message: string };

export function validateComponentAuthoring(component: ComponentValidationTarget, pathPrefix = 'component'): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  const settings = component.settings || {};
  const allowed = allowedSettingKeysForType(component.type);
  const status = String(settings.contentStatus || 'Draft');
  if (!String(component.title || '').trim()) issues.push({ path: `${pathPrefix}.title`, message: 'Component title is required.' });
  if (!Number.isFinite(Number(component.expectedOtjh)) || Number(component.expectedOtjh) < 0) issues.push({ path: `${pathPrefix}.expectedOtjh`, message: 'Expected OTJH cannot be negative.' });
  if (!Number.isFinite(Number(component.points)) || Number(component.points) < 0) issues.push({ path: `${pathPrefix}.points`, message: 'Points cannot be negative.' });
  if (!CONTENT_STATUSES.includes(status as typeof CONTENT_STATUSES[number])) issues.push({ path: `${pathPrefix}.settings.contentStatus`, message: 'Status must be Draft, Ready for QA, Needs changes, or Approved.' });
  if (!/^\d+(?:\.\d+){0,2}$/.test(String(settings.version || '0.1'))) issues.push({ path: `${pathPrefix}.settings.version`, message: 'Version must use numbers such as 0.1 or 1.2.0.' });
  // The question moved out of `settings` into its own field; a component saved
  // before that still carries the text in the legacy key, so both count.
  const reflectionQuestion = String(component.reflectionQuestion || settings.reflectionPrompt || '').trim();
  if (component.reflectionRequired && !reflectionQuestion) issues.push({ path: `${pathPrefix}.reflectionQuestion`, message: 'A reflection question is required when reflection is enabled.' });
  // Draft components must remain saveable while the author is still building
  // the module. The question becomes a hard validation requirement only when
  // the assignment is promoted into the QA/publishing workflow.
  if (component.type === 'assignment' && status !== 'Draft') {
    const assignmentQuestion = String(settings.assignmentContent || settings.assignmentBrief || '')
      .replace(/<[^>]*>/g, '')
      .replace(/&nbsp;|&#160;/gi, ' ')
      .trim();
    const topics = assignmentTopics(settings.assignmentTopics);
    if (String(settings.assignmentTopics || '').trim() ? !topics.some(topicHasContent) : !assignmentQuestion) issues.push({ path: `${pathPrefix}.settings.assignmentContent`, message: 'An assignment question is required.' });
  }
  Object.keys(settings).forEach(key => {
    if (!allowed.has(key)) issues.push({ path: `${pathPrefix}.settings.${key}`, message: `Unsupported setting "${key}" for ${component.type}.` });
  });

  if (component.type === 'video') {
    const rawSourceType = String(settings.sourceType || settings.provider || 'YouTube');
    if (rawSourceType === 'Shortcode') {
      if (status !== 'Draft') {
        issues.push({
          path: `${pathPrefix}.settings.sourceType`,
          message: 'Legacy Shortcode sources are preserved but cannot be marked ready or approved in the new Module Builder.',
        });
      }
      return issues;
    }
    const sourceType = normaliseVideoSourceType(rawSourceType);
    const url = String(settings.videoUrl || '').trim();
    const progress = Number(settings.requiredProgressPercentage || 0);
    if (!Number.isFinite(progress) || progress < 0 || progress > 100) issues.push({ path: `${pathPrefix}.settings.requiredProgressPercentage`, message: 'Required progress must be between 0 and 100.' });
    if (url && sourceType === 'YouTube' && !isYouTubeUrl(url)) issues.push({ path: `${pathPrefix}.settings.videoUrl`, message: 'Enter a valid YouTube URL.' });
    if (url && sourceType === 'External Link' && !isHttpUrl(url)) issues.push({ path: `${pathPrefix}.settings.videoUrl`, message: 'Enter a valid URL.' });
    if (sourceType === 'Embed' && status !== 'Draft' && !String(settings.embedCode || '').trim()) issues.push({ path: `${pathPrefix}.settings.embedCode`, message: 'Embed content is required before QA or approval.' });
    if (sourceType !== 'Embed' && status !== 'Draft' && !url) issues.push({ path: `${pathPrefix}.settings.videoUrl`, message: 'Video URL is required before QA or approval.' });
  }

  if (component.type === 'podcast') {
    const sourceType = String(settings.podcastSource || 'External URL');
    const url = String(settings.podcastUrl || '').trim();
    const embedCode = String(settings.embedCode || '').trim();
    const progress = Number(settings.requiredProgressPercentage || 0);
    if (!Number.isFinite(progress) || progress < 0 || progress > 100) issues.push({ path: `${pathPrefix}.settings.requiredProgressPercentage`, message: 'Required progress must be between 0 and 100.' });
    if (!['Embed', 'Shortcode'].includes(sourceType) && url && !isResourceUrl(url)) issues.push({ path: `${pathPrefix}.settings.podcastUrl`, message: 'Enter a valid podcast URL.' });
    if (status !== 'Draft' && sourceType === 'Embed' && !embedCode) issues.push({ path: `${pathPrefix}.settings.embedCode`, message: 'Embed content is required before QA or approval.' });
  }

  if (component.type === 'reading') {
    const url = String(settings.resourceUrl || '').trim();
    if (url && !isResourceUrl(url)) issues.push({ path: `${pathPrefix}.settings.resourceUrl`, message: 'Enter a valid reading URL.' });
    const audioUrl = String(settings.audioUrl || '').trim();
    if (audioUrl && !isHttpUrl(audioUrl)) issues.push({ path: `${pathPrefix}.settings.audioUrl`, message: 'Enter a valid audio URL.' });
  }

  if (component.type === 'powerpoint') {
    const url = String(settings.presentationUrl || '').trim();
    if (url && !isResourceUrl(url)) issues.push({ path: `${pathPrefix}.settings.presentationUrl`, message: 'Enter a valid presentation URL.' });
  }

  return issues;
}

export function validateModuleAuthoringStructure(module: ModuleValidationTarget): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  if (!String(module.title || '').trim()) issues.push({ path: 'module.title', message: 'Module title is required.' });
  module.weekStructure.forEach((week, weekIndex) => {
    if (!String(week.title || '').trim()) issues.push({ path: `weekStructure.${weekIndex}.title`, message: `Week ${weekIndex + 1} needs a title.` });
    week.components.forEach((component, componentIndex) => {
      const location = `${String(week.title || '').trim() || `Week ${weekIndex + 1}`} / ${String(component.title || '').trim() || `Component ${componentIndex + 1}`}`;
      issues.push(...validateComponentAuthoring(component, `weekStructure.${weekIndex}.components.${componentIndex}`).map(issue => ({
        ...issue,
        message: `${location}: ${issue.message}`,
      })));
    });
  });
  return issues;
}

/**
 * What an author has to have supplied for a component to count as authored.
 *
 * Everything here is a field `createEmptyComponent` seeds, so "unedited" means
 * exactly one thing: nothing on this component differs from what adding it
 * produced. Deliberately structural rather than a stored `touched` flag --
 * components authored before any flag existed, imported from a week template or
 * copied from another module would all read as untouched forever, and a hint
 * that lies about old content is worse than no hint.
 */
export type ComponentAuthoringSnapshot = {
  type: ModuleComponentType;
  title?: string;
  description?: string;
  expectedOtjh?: number;
  /** Accepted and deliberately not read -- see `componentLooksUnedited`. */
  points?: number;
  reflectionRequired?: boolean;
  reflectionQuestion?: string;
  workplaceEvidenceRequired?: boolean;
  tutorValidationRequired?: boolean;
  ksbMappings?: Array<unknown>;
  settings?: ComponentSettings;
};

/**
 * Settings the app writes on its own -- opening a component is enough, no
 * authoring required -- so a non-empty value here is not evidence anyone
 * touched the component.
 *
 * `selectedGroupKeys`/`selectedGroupNames` are the concrete case:
 * `AssignedGroupsSection`'s self-heal effect (week-builder/page.tsx) stamps the
 * component's own locked delivery group into these the moment its editor
 * mounts, so every component in a group-scoped week picks them up on the very
 * first open. `placedCopy*` mirrors that -- it is written when a *different*
 * component's group toggle places a copy of this one, never by editing this
 * one directly.
 *
 * `sessionDate`/`sessionDay`/`sessionTime`/`sessionDateTimeUtc`/
 * `teamsStartDateTimeUtc` are a live session's own case: the scheduling
 * planner (`applyModuleWeekSessionPlan`) stamps these onto every live-session
 * component the moment the module's session plan loads -- which can be right
 * after adding one, since a new live session changes the module's session
 * count and re-triggers the plan fetch. No control in either editor lets an
 * author type into these directly; the session runs on its week's date until
 * something is actually booked (`teamsMeetingUrl` etc., which IS authored and
 * still clears the hint).
 */
const AUTO_MANAGED_SETTING_KEYS = new Set([
  'selectedGroupKeys',
  'selectedGroupNames',
  'placedCopyGroupKeys',
  'placedCopyModuleCatalogueIds',
  'placedCopyWeekIds',
  'placedCopyComponentIds',
  'sessionDate',
  'sessionDay',
  'sessionTime',
  'sessionDateTimeUtc',
  'teamsStartDateTimeUtc',
]);

/**
 * `durationMinutes` is genuinely authored content on every other type -- a
 * typed number, never touched again -- but on a live session it is planner
 * territory until Teams books it: the same `applyModuleWeekSessionPlan` pass
 * that stamps the date re-derives it every time and overwrites whatever was
 * there (see the duration check in `moduleAuthoringData.ts`), and
 * `teamsDurationMinutes`/`sessionRescheduled` are its own tracking mirrors of
 * that, never written by a control in either editor. Scoped to live-session
 * alone so a real duration typed on a video or podcast still counts.
 */
const LIVE_SESSION_AUTO_MANAGED_SETTING_KEYS = new Set([
  'durationMinutes',
  'teamsDurationMinutes',
  'sessionRescheduled',
]);

function settingValueEmpty(value: unknown) {
  if (value === undefined || value === null) return true;
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === 'string') return value.trim() === '';
  return false;
}

function settingValuesMatch(left: unknown, right: unknown) {
  if (Array.isArray(left) || Array.isArray(right)) return JSON.stringify(left ?? null) === JSON.stringify(right ?? null);
  if (typeof left === 'number' || typeof right === 'number') return Number(left) === Number(right);
  if (typeof left === 'boolean' || typeof right === 'boolean') return Boolean(left) === Boolean(right);
  return String(left ?? '') === String(right ?? '');
}

/**
 * True when the component still carries only the values adding it produced: the
 * auto-generated title ("Video 3"), no description, no KSBs, and no setting that
 * differs from its type's default. `points` is not read -- it is stamped from the
 * programme's points rules at creation, so a component nobody has opened can
 * already hold a number the definition does not.
 */
export function componentLooksUnedited(component: ComponentAuthoringSnapshot, typeLabel?: string | readonly string[]): boolean {
  const definition = getComponentDefinition(component.type);
  const title = String(component.title || '').trim();
  // The auto-generated title an author never touched depends on where and when
  // the component was added: the module builder stamps `definition.label`
  // ("Video 3"), while the week builder's rail -- which both surfaces share --
  // stamps its own display label, which for `video` was "Recorded Session 1"
  // until that type took the shared model's own name (see `weekTypeLabel` and
  // `weekAutoTitleLabels`). A caller using other label conventions passes them
  // in, one or several; otherwise this falls back to the definition's own.
  const labels = typeLabel === undefined ? [] : typeof typeLabel === 'string' ? [typeLabel] : typeLabel;
  const autoTitlePattern = (text: string) => new RegExp(`^${text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?:\\s+\\d+)?$`, 'i');
  const matchesAutoTitle = [definition.label, ...labels].some(text => Boolean(text) && autoTitlePattern(text).test(title));
  if (title && !matchesAutoTitle) return false;
  const description = String(component.description || '').trim();
  // Same story as the title: one add button leaves this empty, the other
  // stamps the type's own boilerplate blurb -- neither is something an author
  // wrote.
  if (description && description !== componentTypeDescription(component.type)) return false;
  if ((component.ksbMappings || []).length > 0) return false;
  if (component.expectedOtjh !== undefined && Number(component.expectedOtjh) !== definition.defaultOtjh) return false;
  if (component.reflectionRequired !== undefined && component.reflectionRequired !== definition.reflectionDefault) return false;
  if (component.workplaceEvidenceRequired !== undefined && component.workplaceEvidenceRequired !== definition.workplaceEvidenceDefault) return false;
  if (component.tutorValidationRequired !== undefined && component.tutorValidationRequired !== definition.tutorValidationDefault) return false;
  const rawDefaults = definition.defaultSettings;
  // `normaliseComponentSettings` rewrites a handful of legacy-named default
  // values the moment the module first recalculates -- which happens right
  // after any add, from either button -- e.g. podcast's
  // `podcastSource: 'External URL'` becomes `'External Link'`, reading's
  // `readingSource: 'Written in LMS'` becomes `'Text'`. That is
  // canonicalisation, not authoring, so a component can genuinely be caught in
  // either state depending on whether a recalculation has run yet: both count
  // as untouched.
  const recalculatedDefaults = normaliseComponentSettings(component.type, rawDefaults);
  const reflectionQuestion = String(component.reflectionQuestion || '').trim();
  if (reflectionQuestion && reflectionQuestion !== String(rawDefaults.reflectionPrompt || '').trim()) return false;
  const settings = component.settings || {};
  return !Object.keys(settings).some(key => {
    if (AUTO_MANAGED_SETTING_KEYS.has(key)) return false;
    if (component.type === 'live-session' && LIVE_SESSION_AUTO_MANAGED_SETTING_KEYS.has(key)) return false;
    const value = settings[key];
    // An empty value is nothing an author typed, whether or not the type
    // declares the key: a blank `videoUrl` and a missing one say the same thing.
    if (settingValueEmpty(value)) return false;
    return !settingValuesMatch(value, rawDefaults[key]) && !settingValuesMatch(value, recalculatedDefaults[key]);
  });
}

export function firstValidationMessage(issues: ValidationIssue[]) {
  if (!issues.length) return '';
  if (issues.length === 1) return issues[0].message;
  return `${issues.length} issues: ${issues.map(issue => issue.message).join(' • ')}`;
}

function isHttpUrl(value: string) {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol);
  } catch {
    return false;
  }
}

function isResourceUrl(value: string) {
  const text = String(value || '').trim();
  return text.startsWith('/curriculum_api/curriculum/uploads/') || isHttpUrl(text);
}

function isYouTubeUrl(value: string) {
  if (!isHttpUrl(value)) return false;
  const host = new URL(value).hostname.replace(/^www\./, '').toLowerCase();
  return ['youtube.com', 'youtu.be', 'm.youtube.com'].includes(host) || host.endsWith('.youtube.com');
}

