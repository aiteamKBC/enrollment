import { configuredAssignmentTopics } from '@/lib/assignmentTopics';
import { componentFileList, legacyComponentFile } from '@/lib/componentFiles';
import type { JourneyComponent } from '@/utils/learnerJourney';
import { embeddedSrc } from '@/components/feature/VideoPlayer';
import type { ModuleComponent } from './moduleAuthoringData';

export function previewComponent(component: ModuleComponent): JourneyComponent {
  const setting = (key: string) => String(component.settings[key] ?? '');
  // An Embed source holds the provider's whole <iframe> snippet. The learner API
  // serves a plain address here (learner_detail._video_url_from_settings), so the
  // preview has to unwrap it too — the raw markup is not a URL and resolved as a
  // relative route, showing the learner a 404 where the video belongs.
  const embed = embeddedSrc(setting('embedCode'));
  const uploaded = setting('uploadedFileUrl');
  const uploadChosen = /upload/i.test(setting('powerpointSource') || setting('uploadSource'));
  const resourceKeys = uploadChosen ? ['uploadedFileUrl', 'fileUrl', 'resourceUrl', 'presentationUrl', 'externalUrl', 'url']
    : ['resourceUrl', 'presentationUrl', 'externalUrl', 'fileUrl', 'uploadedFileUrl', 'url'];
  const resource = resourceKeys.map(setting).find(Boolean) || setting('assignmentFileUrl') || setting('readingUrl');
  const assignmentContent = setting('assignmentContent');
  const assignmentHtml = /<[a-z][^>]*>/i.test(assignmentContent) ? assignmentContent : '';
  const audio = setting('podcastUrl') || setting('audioUrl') || ((component.type === 'podcast' || /\.(mp3|wav|m4a|ogg|aac|flac)(?:[?#]|$)/i.test(uploaded)) ? uploaded : '');
  return { title: (component.type === 'assignment' && setting('learnerTitle').trim()) || component.title,
    componentId: component.id, type: component.type.replace(/-/g, '_'),
    monthlyAssignment: component.settings.isMonthlyAssignment === true,
    expectedOtjh: component.expectedOtjh, description: component.description,
    videoUrl: setting('videoUrl') || uploaded || embed, audioUrl: audio,
    contentHtml: setting('readingContent'), hasReadingContent: Boolean(setting('readingContent')),
    // Same rule as the learner API: only a video's lesson content is shown.
    lessonContentHtml: component.type === 'video' ? setting('lessonContent') : '',
    resourceUrl: resource,
    fileName: setting('fileName') || setting('uploadedFileName') || setting('assignmentFileName'),
    // The whole ordered set, falling back to the single file the component has
    // always carried -- the same resolution the learner API applies, so the
    // preview shows the attachments in the order the learner will get them.
    files: componentFileList(
      component.settings.componentFiles,
      legacyComponentFile(resource, setting('fileName') || setting('uploadedFileName') || setting('assignmentFileName'),
        component.settings.uploadedFileSize, setting('uploadedFileContentType')),
    ),
    // Learners are offered only the topics an author filled in.
    assignmentTopics: configuredAssignmentTopics(component.settings.assignmentTopics),
    assignmentBrief: setting('assignmentBrief') || (!assignmentHtml ? assignmentContent : ''), assignmentBriefHtml: assignmentHtml,
    downloadAllowed: Boolean(component.settings.downloadAllowed),
    reflectionPrompt: setting('reflectionPrompt') || setting('podcastReflectionQuestion') || setting('readingReflectionPrompts') || component.description,
    reflectionRequired: component.reflectionRequired, reflectionQuestion: component.reflectionQuestion,
    liveSessionUrl: setting('teamsJoinUrl') || setting('liveSessionUrl'), sessionDate: setting('sessionDate'),
    teamsLiveSessionId: setting('teamsLiveSessionId') || null,
    teamsSessionNumber: Number.isInteger(Number(setting('teamsSessionNumber'))) && Number(setting('teamsSessionNumber')) > 0
      ? Number(setting('teamsSessionNumber')) : null,
    sessionTime: setting('sessionTime'), sessionDateTimeUtc: setting('sessionDateTimeUtc'),
    durationMinutes: Number(setting('durationMinutes')) || null };
}
