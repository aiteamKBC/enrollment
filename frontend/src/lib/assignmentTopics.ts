export interface AssignmentTopicResource {
  fileName: string;
  url: string;
  size: number;
  contentType: string;
  /** A web page listed under Resources rather than previewed as a file. */
  link?: boolean;
}

export interface AssignmentTopic {
  id: string;
  name: string;
  question: string;
  instructions: string;
  resources: AssignmentTopicResource[];
}

export interface AssignmentResourceLink {
  label: string;
  url: string;
}

export const ASSIGNMENT_RESOURCE_ACCEPT = '.pdf,.doc,.docx,.txt,.rtf,.odt,.ppt,.pptx,.mp4,.webm,.mov,.m4v';

/** The builder's three topic slots, empty ones included. */
export function assignmentTopics(value: unknown): AssignmentTopic[] {
  let parsed = value;
  if (typeof parsed === 'string') {
    try { parsed = JSON.parse(parsed); } catch { return []; }
  }
  if (!Array.isArray(parsed)) return [];
  return ['1', '2', '3'].map(id => {
    const source = parsed.find(item => item && String(item.id) === id);
    return { id, name: typeof source?.name === 'string' ? source.name : '',
      question: typeof source?.question === 'string' ? source.question : '',
      instructions: typeof source?.instructions === 'string' ? source.instructions : '',
      resources: Array.isArray(source?.resources) ? source.resources.filter((file: AssignmentTopicResource) =>
        file && typeof file.fileName === 'string' && safeAssignmentResourceUrl(file.url)) : [] };
  });
}

/** The topics learners are offered: only slots an author filled in, ids kept
 * (submissions are saved against them). Mirrors learner_api.assignment_topics. */
export function configuredAssignmentTopics(value: unknown): AssignmentTopic[] {
  return assignmentTopics(value).filter(topicHasContent);
}

export function safeAssignmentResourceUrl(url: unknown): url is string {
  return typeof url === 'string' && (/^https?:\/\//i.test(url) || /^\/curriculum_api\/curriculum\/uploads\//.test(url));
}

export function topicLabel(topic: AssignmentTopic): string {
  return `Topic ${topic.id}${topic.name.trim() ? ` — ${topic.name.trim()}` : ''}`;
}

export function hasHtmlText(value: string | null | undefined): boolean {
  return Boolean((value || '').replace(/<[^>]*>/g, '').replace(/&nbsp;|&#160;/gi, ' ').trim());
}

export function topicHasContent(topic: AssignmentTopic): boolean {
  return Boolean(hasHtmlText(topic.question) || hasHtmlText(topic.instructions) || topic.resources.length);
}

const LEGACY_MATERIALS_HEADING = '<p><strong>Additional materials</strong></p>';
// Exactly what import_mba_curriculum.extra_materials_html writes, so authored
// text that happens to mention "Additional materials" is never reinterpreted.
const LEGACY_MATERIALS_LIST = /^<ul>((?:<li><a href="[^"]*" target="_blank" rel="noreferrer">[^<]*<\/a><\/li>)+)<\/ul>$/;
const LEGACY_MATERIALS_ITEM = /<li><a href="([^"]*)" target="_blank" rel="noreferrer">([^<]*)<\/a><\/li>/g;

function decodeEntities(value: string): string {
  return new DOMParser().parseFromString(value, 'text/html').documentElement.textContent || '';
}

/** The legacy-LMS import appended the old course's extra files and links to an
 * assignment's instructions as an "Additional materials" list. Those are
 * resources, not instructions: return the authored text before the list and
 * the links separately. Anything else is returned unchanged. */
export function splitLegacyMaterials(html: string | null | undefined): { instructions: string; links: AssignmentResourceLink[] } {
  const value = html || '';
  const index = value.lastIndexOf(LEGACY_MATERIALS_HEADING);
  const list = index < 0 ? null : LEGACY_MATERIALS_LIST.exec(value.slice(index + LEGACY_MATERIALS_HEADING.length).trim());
  if (!list) return { instructions: value, links: [] };
  const links = [...list[1].matchAll(LEGACY_MATERIALS_ITEM)].map(([, url, label]) => {
    const href = decodeEntities(url).trim();
    return { url: href, label: decodeEntities(label).trim() || href };
  }).filter(link => safeAssignmentResourceUrl(link.url));
  return { instructions: value.slice(0, index).trim(), links };
}

export interface TopicPresentation {
  question: string;
  /** Authored instructions only; '' when there are none to show. */
  instructions: string;
  /** Documents and videos to preview. */
  files: AssignmentTopicResource[];
  /** Web links listed under Resources. */
  links: AssignmentResourceLink[];
}

/** What a learner is shown for a topic: its question and instructions as
 * separate sections, with imported link lists moved under Resources. */
export function presentTopic(topic: AssignmentTopic): TopicPresentation {
  const legacy = splitLegacyMaterials(topic.instructions);
  const links = [...topic.resources.filter(file => file.link).map(file => ({ label: file.fileName || file.url, url: file.url })), ...legacy.links];
  return {
    question: topic.question,
    instructions: hasHtmlText(legacy.instructions) ? legacy.instructions : '',
    files: topic.resources.filter(file => !file.link),
    links: links.filter((link, index) => links.findIndex(other => other.url === link.url) === index),
  };
}
