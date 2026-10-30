import { useEffect, useState, type ReactNode } from 'react';
import { ActivitySidebar } from '../video-watch/ActivitySidebar';
import type { JourneyComponent } from '@/utils/learnerJourney';
import type { Subject, SubjectEntry } from './learningSummary';
import { continuingLearningWeek, currentLearningWeek, learningDate, learningHref, nativeHref, resolveSubjectWeek, type LearningWeek } from './subjectLearning';
import { combinedActivities } from './combinedActivities';

/** The same activity/week navigation as the native player, backed by the
 * original catalogue identities. Material and attempt handling stay with the
 * existing renderers; these display rows are never sent to a native API. */
export function SubjectActivityWorkspace({ subject, weeks: rawWeeks, requestedWeek, requestedActivity: requestedId, search,
  kind, learnerId, progress, renderActivity, renderWeekProgress,
}: {
  subject: Subject; weeks: LearningWeek[]; requestedWeek?: string; requestedActivity?: string;
  search: string; kind?: string; learnerId?: string; progress: ReactNode;
  renderActivity: (entry: SubjectEntry) => ReactNode;
  renderWeekProgress: (week: LearningWeek) => ReactNode;
}) {
  const combined = combinedActivities(subject.activities);
  const displayEntries = new Map(combined.activities.map(entry => [entry.id, entry]));
  const weeks = rawWeeks.map(week => ({ ...week, activities: week.activities.flatMap(entry => {
    const displayed = displayEntries.get(entry.id);
    return displayed ? [displayed] : [];
  }) })).filter((week, index) => week.activities.length || !rawWeeks[index].activities.length);
  const requestedActivity = combined.aliases.get(requestedId || '') || requestedId;
  const route = `${requestedWeek || ''}:${requestedActivity || ''}`;
  const [selectedWeek, setSelectedWeek] = useState<{ route: string; id?: string }>();
  const [opened, setOpened] = useState<{ route: string; weekId: string; activities: Record<string, string> }>();
  const [visited, setVisited] = useState<string[]>([]);
  const targetedWeek = weeks.find(week => week.activities.some(entry => entry.id === requestedActivity));
  const term = search.trim().toLocaleLowerCase();
  const matches = (entry: SubjectEntry) => !term || subject.title.toLocaleLowerCase().includes(term) || entry.title.toLocaleLowerCase().includes(term);
  const complete = subject.activities.length > 0 && subject.activities.every(entry => entry.completed);
  const recommended = complete ? undefined : continuingLearningWeek(subject);
  const currentId = complete ? undefined : currentLearningWeek(subject)?.id;
  const current = weeks.find(item => item.id === currentId);
  const selected = weeks.find(item => selectedWeek?.route === route && item.id === selectedWeek.id)
    || targetedWeek || resolveSubjectWeek(weeks, requestedWeek)
    || weeks.find(item => opened?.route === route && item.id === opened.weekId)
    || current || weeks.find(item => item.id === recommended?.id) || weeks[0];
  const week = term && !selected?.activities.some(matches) ? weeks.find(item => item.activities.some(matches)) || selected : selected;
  const entries = displayEntries;
  const canOpen = (entry: SubjectEntry) => !!kind && !!learnerId && (entry.legacy
    ? entry.legacy.can_open_material !== false || entry.legacy.catalogue_kind === 'quiz' || !!entry.legacy.source_material_activity_id
    : !!nativeHref(entry, kind, learnerId));
  const available = (week?.activities || []).filter(entry => matches(entry) && canOpen(entry));
  const activity = week?.activities.find(entry => entry.id === requestedActivity && matches(entry))
    || available.find(entry => opened?.route === route && !!week && entry.id === opened.activities[week.id])
    || available.find(entry => !entry.completed) || available[0]
    || week?.activities.find(entry => matches(entry) && !entry.completed)
    || week?.activities.find(matches);
  const activityId = activity?.id;
  const weekId = week?.id;
  useEffect(() => {
    if (activityId && weekId) {
      setVisited(previous => previous.includes(activityId) ? previous : [...previous, activityId]);
      setOpened(previous => ({ route, weekId, activities: { ...(previous?.route === route ? previous.activities : {}), [weekId]: activityId } }));
    }
  }, [activityId, weekId, route]);
  const mounted = new Set([...visited, ...(activity ? [activity.id] : [])]);
  // Completion comes from the unified catalogue, including historical quiz
  // results. Do not fabricate native quiz attempts to paint a completion tick.
  const component = (entry: SubjectEntry): JourneyComponent => ({
    componentId: entry.id, title: entry.title, type: entry.category, expectedOtjh: null,
    pointNumber: entry.native?.pointNumber, moduleId: entry.native?.moduleId, weekId: entry.native?.weekId,
  });
  const weekTitle = (item: LearningWeek) => item.title.toLocaleLowerCase().startsWith(item.label.toLocaleLowerCase())
    ? item.title : `${item.label} · ${item.title}`;
  const sidebarWeeks = weeks.map(item => ({
    key: item.id, week: weekTitle(item),
    count: item.activities.length, completed: item.activities.filter(entry => entry.completed).length,
    active: item.id === week?.id, components: item.activities.map(component),
  }));
  const sidebarWeek = current || week;
  const weekDateLabel = (item?: LearningWeek) => item?.start
    ? `${learningDate(item.start)}${item.end && item.end !== item.start ? ` – ${learningDate(item.end)}` : ''}`
    : undefined;
  const monthFor = (item: LearningWeek) => item.id === 'introduction' ? 'Introduction'
    : item.id.startsWith('extra:') ? 'Extra activities'
      : item.start || item.month ? new Date(`${(item.month || item.start)!.slice(0, 7)}-01T12:00:00Z`)
        .toLocaleDateString('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' }) : 'Undated activities';

  return <div className="grid min-w-0 grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
    <div className="min-w-0 space-y-4">
      <section aria-label="Module progress" className="rounded-xl border border-primary-100 bg-white p-5 shadow-sm">{progress}</section>
      {!!weeks.length && <label className="block text-sm font-semibold text-primary-800 lg:hidden">Select week
        <select aria-label="Select week" value={week?.id || ''} onChange={event => setSelectedWeek({ route, id: event.target.value })}
          className="mt-2 w-full min-w-0 rounded-xl border border-primary-200 bg-white px-3 py-3 text-sm">
          {weeks.map(item => <option key={item.id} value={item.id}>{weekTitle(item)}</option>)}
        </select>
      </label>}
      {weeks.map(item => <section key={item.id} hidden={item.id !== week?.id} aria-label={`${item.label} materials`}
        className="min-w-0 overflow-hidden rounded-xl border border-primary-100 bg-white shadow-sm">
        <header className="border-b border-primary-100 bg-primary-50/50 px-5 py-4">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-primary-600">{monthFor(item)}</p>
          <h3 className="mt-1 text-lg font-bold text-primary-950">{weekTitle(item)}</h3>
          {item.start && <p className="mt-1 text-xs text-foreground-500">{learningDate(item.start)}{item.end ? ` – ${learningDate(item.end)}` : ''}</p>}
          <div className="mt-3">{renderWeekProgress(rawWeeks.find(original => original.id === item.id) || item)}</div>
        </header>
        {/* Keep opened material mounted while browsing weeks/searching so an
            unfinished quiz or reading confirmation isn't silently cleared. */}
        {item.activities.filter(entry => mounted.has(entry.id)).map(entry => <div key={entry.id} hidden={entry.id !== activity?.id}>{renderActivity(entry)}</div>)}
        {!item.activities.length && <p className="p-5 text-sm text-foreground-500">Learning materials will appear here when they are added to this week.</p>}
        {item.activities.length > 0 && !item.activities.some(matches) && <p className="p-5 text-sm text-foreground-500">No activities match your search.</p>}
      </section>)}
      {!weeks.length && <p className="rounded-xl border bg-white p-5 text-sm text-foreground-500">Activities will appear here when they are added to this subject.</p>}
    </div>
    {/* Use the catalogue's dates, rather than fetching a second plan by title. */}
    <ActivitySidebar moduleTitle={subject.title} weekTitle={sidebarWeek ? weekTitle(sidebarWeek) : ''}
      weekDateLabel={weekDateLabel(sidebarWeek)} dateLabelFor={(_, index) => weekDateLabel(weeks[index])}
      weekComponents={(sidebarWeek?.activities || []).map(component)} weeks={sidebarWeeks}
      completedIds={new Set(combined.activities.filter(entry => entry.completed).map(entry => entry.id))}
      currentComponentId={activity?.id} currentWeekKey={current?.id ?? null}
      onSelectWeek={item => setSelectedWeek({ route, id: item.key })}
      monthHeadingFor={(_, index) => index === 0 || monthFor(weeks[index]) !== monthFor(weeks[index - 1]) ? monthFor(weeks[index]) : null}
      canOpen={item => {
        const entry = entries.get(item.componentId || '');
        return !!entry && canOpen(entry);
      }}
      routeFor={item => {
        const entry = entries.get(item.componentId || '')!;
        return nativeHref(entry, kind, learnerId) || `${learningHref('catalogue', kind, learnerId, subject.id)}&activity=${encodeURIComponent(entry.id)}`;
      }} />
  </div>;
}
