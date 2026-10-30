import { useMemo } from 'react';
import DOMPurify from 'dompurify';
import { hasHtmlText, presentTopic, topicLabel, type AssignmentResourceLink, type TopicPresentation } from '@/lib/assignmentTopics';
import { AppIcon } from '@/components/feature/AppIcon';
import type { JourneyComponent } from '@/utils/learnerJourney';

export const briefLabelClass = 'text-[10px] font-black uppercase tracking-[0.14em] text-primary-600';

/** Authored assignment text: rich text is sanitised, plain text keeps its line
 * breaks. The innerHTML object is memoised so a parent timer tick does not
 * recreate embedded media. */
export function AssignmentRichText({ value, className = '' }: { value: string; className?: string }) {
  const inner = useMemo(() => /<[a-zA-Z][^>]*>/.test(value) ? { __html: DOMPurify.sanitize(value) } : null, [value]);
  return inner
    ? <div className={`rich-text-surface max-w-none [&_p]:mb-3 [&_ul]:list-disc [&_ul]:pl-5 ${className}`} dangerouslySetInnerHTML={inner} />
    : <p className={`whitespace-pre-line ${className}`}>{value}</p>;
}

/** The instructions and web resources that accompany an assignment question,
 * each under its own heading. Renders nothing when there are neither. */
export function AssignmentGuidance({ instructions, links = [] }: {
  instructions?: string | null; links?: readonly AssignmentResourceLink[];
}) {
  return <>
    {instructions && <section aria-label="Instructions">
      <p className={briefLabelClass}>Instructions</p>
      <AssignmentRichText value={instructions} className="mt-2 text-sm leading-6 text-foreground-800" />
    </section>}
    {links.length > 0 && <section aria-label="Resources">
      <p className={briefLabelClass}>Resources</p>
      <ul className="mt-2 space-y-1.5 text-sm">{links.map(link => <li key={link.url}>
        <a href={link.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 break-all font-semibold text-primary-700 underline">
          <AppIcon className="ri-external-link-line" />{link.label}
        </a>
      </li>)}</ul>
    </section>}
  </>;
}

/** The brief of an assignment completed like an ordinary activity (one that
 * is not the monthly assignment): its question(s), instructions and resources.
 * The primary attachment is already previewed by the activity itself. */
export function AssignmentBriefPanel({ component }: {
  component: Pick<JourneyComponent, 'assignmentTopics' | 'assignmentBrief' | 'assignmentBriefHtml' | 'resourceUrl'>;
}) {
  const topics = component.assignmentTopics || [];
  const brief = component.assignmentBriefHtml || component.assignmentBrief || '';
  const parts: (TopicPresentation & { id: string; heading: string })[] = topics.length
    ? topics.map(topic => ({ id: topic.id, heading: topics.length > 1 ? topicLabel(topic) : '', ...presentTopic(topic) }))
    : [{ id: 'brief', heading: '', question: brief, instructions: '', files: [], links: [] }];
  if (!parts.some(part => hasHtmlText(part.question) || part.instructions || part.links.length || part.files.length)) return null;
  return <section aria-label="Assignment brief" className="mt-4 space-y-5 rounded-2xl border border-background-300 bg-white p-6">
    {parts.length > 1 && <p className="text-sm text-foreground-600">Choose one of these options.</p>}
    {parts.map(part => <div key={part.id} className="space-y-4">
      {part.heading && <h3 className="font-bold text-foreground-900">{part.heading}</h3>}
      {hasHtmlText(part.question) && <section aria-label="Assignment question">
        <p className={briefLabelClass}>Assignment question</p>
        <AssignmentRichText value={part.question} className="mt-2 text-sm leading-6 text-foreground-800" />
      </section>}
      <AssignmentGuidance instructions={part.instructions} links={[
        ...part.files.filter(file => file.url !== component.resourceUrl).map(file => ({ label: file.fileName || 'Assignment file', url: file.url })),
        ...part.links,
      ]} />
    </div>)}
  </section>;
}
