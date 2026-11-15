import { useLayoutEffect, useRef, useState, type CSSProperties } from 'react';
import { ArrowLeft, ArrowRight, CalendarDays, Check, ChevronRight, FlaskConical, Layers3, LockKeyhole, Minus, Plus, RotateCcw } from 'lucide-react';
import { ordered, isModuleGroup, selectedModuleIds, type ProgrammeModules, type ProgrammeModule, type ProgrammeModuleSelection, type HomeCohort } from './configuration';
import { londonDate, sampleOfferings, futureOfferings, timelineMonths, offeringPosition } from './schedulingTimeline';
import styles from './ModuleSchedulingPreview.module.css';
import { useModuleSchedules, type ScheduleContext, type ScheduleOffering } from './useModuleSchedules';
import type { SampleOffering } from './schedulingTimeline';

function academicModules(items: ProgrammeModule[]): ProgrammeModule[] {
  return ordered(items).filter(item => item.active).flatMap(item => isModuleGroup(item) ? academicModules(item.items ?? []) : [item]);
}

/** Shared timeline; real LMS access stays behind Django, and choices stay local. */
export default function ModuleSchedulingPreview({ programme, selection, homeCohort, samplePreview = false, scheduleContext }: {
  programme: ProgrammeModules; selection?: ProgrammeModuleSelection; homeCohort?: HomeCohort; samplePreview?: boolean; scheduleContext?: ScheduleContext;
}) {
  const pcp = programme.id === 'PCP_L6';
  const pathway = pcp && selection?.programme === 'PCP_L6'
    ? programme.pathways.find(path => path.active && path.id === selection.pathway) : undefined;
  const ids = pathway ? selectedModuleIds(selection, pathway) : [];
  const modules = pcp ? pathway ? academicModules(pathway.modules).filter(module => ids.includes(module.id)) : [] : academicModules(programme.modules);
  const homeName = homeCohort?.status === 'resolved' && homeCohort.programmeId === programme.id
    && homeCohort.cohortId && homeCohort.name?.trim() ? homeCohort.name : 'Cohort pending';
  const sample = import.meta.env.DEV && samplePreview;
  const schedules = useModuleSchedules(scheduleContext, pathway?.id, modules.map(m => m.id), sample);
  return <Board key={`${programme.id}:${pathway?.id ?? ''}:${ids.join(',')}:${sample}`} modules={modules} pcp={pcp} pathwayTitle={pathway?.title} homeName={homeName} homeId={homeName === 'Cohort pending' ? undefined : homeCohort?.cohortId} sample={sample} schedules={schedules} />;
}

function Board({ modules, pcp, pathwayTitle, homeName, homeId, sample, schedules }: { modules: ProgrammeModule[]; pcp: boolean; pathwayTitle?: string; homeName: string; homeId?: string; sample: boolean; schedules: ReturnType<typeof useModuleSchedules> }) {
  const [choices, setChoices] = useState<Record<string, string>>({});
  const scroll = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(100);
  const centreDay = useRef<number | null>(null);
  const pixelsPerDay = 4 * zoom / 100;
  const changeZoom = (next: number) => {
    if (next === zoom || next < 60 || next > 140) return;
    const view = scroll.current;
    if (view) {
      const labelWidth = view.querySelector('[data-timeline-label]')?.getBoundingClientRect().width ?? 0;
      centreDay.current = (view.scrollLeft + (view.clientWidth - labelWidth) / 2) / pixelsPerDay;
    }
    setZoom(next);
  };
  useLayoutEffect(() => {
    const view = scroll.current;
    if (!view || centreDay.current === null) return;
    const labelWidth = view.querySelector('[data-timeline-label]')?.getBoundingClientRect().width ?? 0;
    view.scrollTo({ left: Math.max(0, centreDay.current * pixelsPerDay - (view.clientWidth - labelWidth) / 2), behavior: 'instant' });
    centreDay.current = null;
  }, [pixelsPerDay]);
  const today = londonDate();
  const review = !sample && schedules.reviewRequested && schedules.data?.review === true;
  const real = (schedules.data?.modules ?? []).flatMap(m => m.offerings).filter(o => modules.some(m => m.id === o.moduleId) && (o.approved || o.matchConfidence === 'exact_title' || o.matchConfidence === 'tentative' || review));
  const offerings: (SampleOffering & Partial<ScheduleOffering>)[] = futureOfferings(sample && pcp ? sampleOfferings(modules.map(m => m.id), today) : real, today);
  const homeHasSchedule = offerings.some(o => !sample && o.cohortId === homeId);
  const reason = (id: string) => schedules.data?.modules.find(m => m.academicModuleId === id)?.reason || (schedules.error || (schedules.loading ? 'Reading schedule availability...' : 'No matching future LMS delivery is currently available.'));
  const deliveryName = (delivery: string) => sample ? `Sample delivery ${delivery}` : offerings.find(o => o.delivery === delivery)?.cohortName || delivery;
  const timeline = timelineMonths(offerings, today);
  const years = [...new Set(timeline.months.map(month => month.year))];
  const date = (value: string) => new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(new Date(`${value}T12:00:00Z`));
  const stripes = <div className={styles.stripes} aria-hidden="true">{timeline.months.map((month, index) => <span key={month.start} className={index % 2 ? styles.altMonth : ''} style={{ left: `${month.left}%`, width: `${month.width}%` }} />)}</div>;
  const info = (module: ProgrammeModule) => <><strong>{module.title}</strong><small>{pcp ? module.isMandatory ? 'Mandatory' : 'Confirmed optional' : 'Fixed module'}{module.credits != null ? ` · ${module.credits} ${module.credits === 1 ? 'credit' : 'credits'}` : module.sessions != null ? ` · ${module.sessions} sessions` : ''}</small></>;
  return <div className={styles.board} data-zoom={zoom} role="region" aria-label={sample ? 'Module scheduling preview' : 'Module scheduling timeline'}>
    {sample && <div className={styles.notice}><FlaskConical size={17} aria-hidden="true" /><div><strong>Sample preview — not live LMS data</strong><span>Illustrative dates and groups. Choices stay on this page and are not saved.</span></div><span className={styles.previewTag}>DESIGN PREVIEW</span></div>}
    {review && <div className={styles.notice}><FlaskConical size={17} aria-hidden="true" /><div><strong>UNVERIFIED LMS MATCH — REVIEW ONLY</strong><span>Real recorded LMS dates. Academic equivalence is unapproved; no session can be confirmed or saved.</span></div></div>}
    {schedules.error && <p role="alert" className={styles.readError}>{schedules.error} <button type="button" onClick={schedules.retry}>Retry schedule read</button></p>}
    <div className={styles.heading}><div className={styles.headingIcon}><CalendarDays size={23} aria-hidden="true" /></div><div><p className={styles.eyebrow}>YOUR LEARNING TIMELINE</p><h4>{sample ? 'Available Module Sessions' : 'Module Schedule'}</h4><p>{review ? `${offerings.length} real future candidate offerings · academic approval required` : sample && pcp ? 'Choose a delivery session. Your home cohort always stays the same.' : 'Choose a delivery date. Possible matches are provisional; nothing is booked.'}</p></div></div>
    <div className={styles.toolbar}><span><Layers3 size={15} aria-hidden="true" />{pcp ? pathwayTitle ?? 'No confirmed pathway' : 'Fixed programme modules'}<small>{modules.length} modules</small></span><div><span className={styles.scrollHint}>Scroll to explore</span><div className={styles.zoomControls} role="group" aria-label="Timeline zoom"><button type="button" aria-label="Zoom out timeline" disabled={zoom === 60} onClick={() => changeZoom(zoom - 20)}><Minus size={15} aria-hidden="true" /></button><button type="button" aria-label="Reset timeline zoom to 100%" title="Reset to 100%" onClick={() => changeZoom(100)}>{zoom}%</button><button type="button" aria-label="Zoom in timeline" disabled={zoom === 140} onClick={() => changeZoom(zoom + 20)}><Plus size={15} aria-hidden="true" /></button></div>{offerings.length > 0 && <button type="button" className={styles.jump} onClick={() => { const view = scroll.current; if (!view) return; const bar = view.querySelector('[data-offering-id]'); const top = bar ? bar.getBoundingClientRect().top - view.getBoundingClientRect().top + view.scrollTop - 190 : 0; view.scrollTo({ left: Math.max(0, parseFloat(offeringPosition(offerings[0], timeline.start, timeline.totalDays).left) / 100 * timeline.totalDays * pixelsPerDay - 12), top: Math.max(0, top), behavior: 'smooth' }); }}>Jump to next available session</button>}<button type="button" aria-label="Scroll timeline earlier" onClick={() => scroll.current?.scrollBy({ left: -420, behavior: 'smooth' })}><ArrowLeft size={15} /></button><button type="button" aria-label="Scroll timeline later" onClick={() => scroll.current?.scrollBy({ left: 420, behavior: 'smooth' })}><ArrowRight size={15} /></button></div></div>
    <div ref={scroll} className={styles.viewport} role="region" aria-label="Scrollable module timeline" tabIndex={0}>
      <div className={styles.canvas} style={{ '--timeline-width': `${timeline.totalDays * pixelsPerDay}px` } as CSSProperties}>
        <div className={styles.timelineHeader}>
          <div className={styles.corner} data-timeline-label><CalendarDays size={16} aria-hidden="true" /><span>COHORT / MODULE<small>{sample ? 'Dates shown are samples' : review ? 'Recorded LMS dates · review only' : offerings.length ? 'Recorded LMS dates' : 'Future dates pending'}</small></span></div>
          <div className={styles.calendar}><div className={styles.years}>{years.map(year => <span key={year} style={{ width: `${timeline.months.filter(m => m.year === year).reduce((sum, m) => sum + m.width, 0)}%` }}><strong>{year}</strong></span>)}</div><div className={styles.months}>{timeline.months.map(month => <span key={month.start} style={{ width: `${month.width}%` }}>{month.name}</span>)}</div></div>
        </div>
        <div className={`${styles.row} ${styles.homeRow}`} data-timeline-row="home">
          <div className={`${styles.label} ${styles.homeLabel}`}><span className={styles.homeIcon}><LockKeyhole size={17} aria-hidden="true" /></span><div><strong>Your Assigned Cohort</strong><span data-home-cohort>{homeName}</span><small>PERMANENT HOME · NOT EDITABLE</small></div></div>
          <div className={`${styles.lane} ${styles.homeLane}`}>{stripes}<div className={styles.homeEmpty}><LockKeyhole size={17} aria-hidden="true" /><span><strong>{homeHasSchedule ? 'Home-cohort delivery dates are shown below' : 'No future home-cohort delivery yet'}</strong><small>Your assignment stays fixed. No dates have been assumed.</small></span></div></div>
        </div>

        {[...new Set(offerings.map(o => o.delivery))].map(delivery => {
          const deliveries = offerings.filter(o => o.delivery === delivery);
          if (!deliveries.length) return null;
          return <div key={delivery}>
            <div className={`${styles.row} ${styles.deliveryRow}`} data-timeline-row="delivery"><div className={styles.label}><span><ChevronRight size={14} aria-hidden="true" />Delivery Cohort</span><strong>{deliveryName(delivery)}</strong></div><div className={styles.lane}>{stripes}<span className={styles.deliveryNote}>{sample ? 'ILLUSTRATIVE DELIVERY' : 'RECORDED LMS DELIVERY'} · DOES NOT CHANGE YOUR HOME COHORT</span></div></div>
            {deliveries.map(offering => {
              const module = modules.find(m => m.id === offering.moduleId)!;
              const selected = choices[module.id] === offering.id;
              const selectable = pcp && (sample || offering.approved === true || (!review && offering.allowLocalSelection === true));
              return <section key={offering.id} className={styles.row} aria-label={`${module.title} ${offering.group} sessions`} data-timeline-row="offering"><div className={styles.label}>{info(module)}{offering.group && <span className={styles.labelGroup}>{offering.group}</span>}</div><div className={styles.lane}>{stripes}<article tabIndex={0} aria-label={`${module.title}. ${date(offering.start)} to ${date(offering.end)}. ${offering.group}. ${sample ? 'Sample' : offering.matchConfidence === 'tentative' ? 'Possible LMS match' : 'Exact LMS title match'}. Delivery cohort: ${deliveryName(delivery)}`} title={`${module.title} | ${date(offering.start)} - ${date(offering.end)} | ${offering.group} | ${deliveryName(delivery)}`} className={`${styles.bar} ${selected ? styles.selected : ''} ${!sample && (review || offering.matchConfidence === 'tentative') && !offering.approved ? styles.unverified : ''}`} style={offeringPosition(offering, timeline.start, timeline.totalDays)} data-offering-id={offering.id} data-confidence={offering.matchConfidence} data-approved={offering.approved === true} data-start={offering.start} data-end={offering.end}>
                <span className={styles.barGroup}>{sample ? 'SAMPLE' : review ? 'UNVERIFIED LMS MATCH — REVIEW ONLY' : offering.matchConfidence === 'tentative' ? 'Possible LMS match' : 'Exact LMS title match'}{offering.group ? ` · ${offering.group}` : ''}</span>
                {selectable && <button type="button" className={styles.selectButton} aria-pressed={selected} aria-label={`Select ${sample ? 'sample' : offering.approved ? 'approved' : offering.matchConfidence === 'tentative' ? 'provisional' : 'exact'} ${offering.group} session for ${module.title}`} onClick={() => setChoices(current => ({ ...current, [module.id]: offering.id }))}>{selected && <Check size={12} aria-hidden="true" />}{selected ? 'Selected' : 'Select'}</button>}
                <h5 title={module.title}>{module.title}</h5>
                {!sample && <span className={styles.lmsTitle} title={offering.lmsTitle}>LMS: {offering.lmsTitle}</span>}
                <p><time dateTime={offering.start}>{date(offering.start)}</time><ArrowRight size={12} aria-hidden="true" /><time dateTime={offering.end}>{date(offering.end)}</time></p><small>{selected ? (offering.provisional ? 'Provisional choice - not saved or booked' : 'Selected locally - not saved or booked') : `Delivery Cohort: ${deliveryName(delivery)}`}</small>
              </article></div></section>;
            })}
          </div>;
        })}
        {modules.filter(module => !offerings.some(o => o.moduleId === module.id)).map(module => <section key={module.id} className={styles.row} aria-label={`${module.title} sessions`} data-timeline-row={pcp ? 'academic' : 'fixed'} data-academic-module-id={module.id}><div className={styles.label}>{info(module)}</div><div className={styles.lane}>{stripes}<div className={styles.noSchedule}><CalendarDays size={18} aria-hidden="true" /><span><strong>Schedule not available yet</strong><small>{reason(module.id)}</small></span></div></div></section>)}
        {modules.length === 0 && <div className={styles.row}><div className={styles.label}><strong>No confirmed modules</strong></div><div className={styles.lane}><p className={styles.noSchedule}>Confirm your pathway and modules in the normal view first.</p></div></div>}
      </div>
    </div>
    {pcp && (sample || offerings.some(o => o.approved || o.allowLocalSelection)) && modules.length > 0 && <div className={styles.footer}><p role="status" aria-live="polite"><Check size={16} aria-hidden="true" /><strong>{Object.keys(choices).length} of {modules.length}</strong> {sample ? 'sample sessions selected' : 'sessions selected locally'} <span>· Not saved or booked</span></p><button type="button" disabled={!Object.keys(choices).length} onClick={() => setChoices({})}><RotateCcw size={14} aria-hidden="true" />{sample ? 'Reset preview' : 'Reset choices'}</button></div>}
    <p className={styles.footnote}><LockKeyhole size={12} aria-hidden="true" />One fixed home cohort. Session choices never change your programme, pathway or academic credits.</p>
  </div>;
}
