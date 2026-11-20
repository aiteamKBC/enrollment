import { describe, expect, it } from 'vitest';
import { weeklySections, weeklyTrainingPlanHtml } from '../weeklyTrainingPlan';
import { type ActivitiesOverview, type Activity } from '../trainingPlanActivities';
import { renderManagedDocument } from '../render';
import { documentPages, pageHtml, readPage } from '../pageDocument';
import { payloadFor } from './fixtures';
import { decorateBrandedContent } from '../brandedEditorTheme';

function fixture(): ActivitiesOverview {
  const a = (id: string, kind: string, hours: number | null, weekId = 'w1'): Activity =>
    ({id, kind, hours, weekId, title: `Full recorded ${id} title`, date:'2027-02-07'});
  return {version:'activities-overview-v5', programme:'Isolated programme', startDate:'2027-02-01', endDate:'2028-01-31',
    deliveryResponsibility:'Kent Business College', deliverySchedule:'Verified sources only.',
    modules:[{academicModuleId:'m1', title:'Confirmed module', description:'Recorded description',
      hours:138, startDate:'2027-02-01', endDate:'2027-06-20', coveredKsbs:[{code:'K1'}],
      weeks:[{sourceId:'w1', weekNumber:1, title:'Marketing Definitions'}, {sourceId:'w2', weekNumber:2, title:'Different actual lesson'},
        {sourceId:'empty', weekNumber:3, title:'An empty actual week'}],
      activities:[a('live','live_session',2.5), a('reading','reading',.5), a('quiz','quiz',0),
        a('assignment','assignment',8), a('zero-video','video',0), a('zero-ppt','powerpoint',0), a('zero-podcast','podcast',0),
        {...a('duplicate','video',0), title:'Same source title'}, {...a('duplicate2','video',0), title:'Same source title'},
        a('paid-video','video',.75), a('missing','podcast',null,'w2'), a('another-zero','powerpoint',0,'w2'),
        a('other','reading',.25,'w2'), { ...a('coaching','meeting',1), reviewTypeCode:'mcm', scheduleKind:'projected' },
        {...a('topic','topic',null), parentComponentId:'assignment'}]}],
    reviews:[{id:'progress', templateId:'pr', title:'Configured Progress Review', date:'2027-04-01',
      hours:0, scheduleKind:'projected', reviewTypeCode:'progress_review', participants:'Advisor, Participant'},
      {id:'coaching-ref', templateId:'mc', title:'Must not repeat coaching here', date:'2027-04-01',
        hours:1, scheduleKind:'projected', reviewTypeCode:'mcm', participants:null}],
    totals:{moduleHours:138, meetingHours:1, totalHours:139},
    warnings:[{code:'denied', message:'PRIVATE_DIAGNOSTIC'}], blockers:[]};
}

describe('compact weekly Training Plan', () => {
  it('keeps the reference week cards and module strips independent of the generic editor skin', () => {
    const value = fixture(), original = structuredClone(value);
    const dom = document.createElement('div'); dom.innerHTML = weeklyTrainingPlanHtml(value);
    decorateBrandedContent(dom);
    expect(dom.querySelector('[data-training-plan-weekly]')).not.toBeNull();
    expect(dom.querySelectorAll('.tp-week-card')).toHaveLength(3);
    expect(dom.querySelector('.tp-module-title')?.hasAttribute('data-branded-section')).toBe(false);
    expect(dom.querySelectorAll('.tp-week-title[data-branded-section]')).toHaveLength(0);
    expect(dom.querySelector('.tp-activities thead')?.textContent).toContain('Date (if applicable)');
    expect(dom.querySelectorAll('.tp-activities tbody .tp-icon')).toHaveLength(7);
    expect(dom.querySelectorAll('.tp-resources')).toHaveLength(2);
    expect(dom.querySelector('.tp-reviews-title')?.textContent).toBe('Reviews Schedule');
    expect(value).toEqual(original);
  });
  it('omits lesson-number prefixes from week headings and topic detail rows without changing source data', () => {
    const value = fixture();
    value.modules[0].weeks![0].title = 'L15: Place-Where and How Customer Buy';
    value.modules[0].weeks![1].title = 'L2 – Marketing Concepts';
    const original = structuredClone(value);
    const dom = document.createElement('div'); dom.innerHTML = weeklyTrainingPlanHtml(value);
    expect(dom.querySelector('.tp-week-title')?.textContent).toBe('Week 1: Place-Where and How Customer Buy');
    expect(dom.querySelectorAll('.tp-week-title')[1].textContent).toBe('Week 2: Marketing Concepts');
    expect(dom.textContent).not.toContain('Topic:');
    expect(dom.textContent).toContain('Assignment: Full recorded assignment title');
    const payload = payloadFor('training-plan', true); payload.template.presentationTheme = 'kbc-dfe-purple-v4';
    payload.data.activitiesOverview = value;
    const pdf = renderManagedDocument(payload.template, payload.data)!.output();
    expect(pdf).not.toContain('L15:');
    expect(pdf).not.toContain('Topic:');
    expect(pdf).toContain('Place-Where and How Customer Buy');
    expect(value).toEqual(original);
    expect(weeklySections(value.modules[0])[0].activities.some(a => a.id === 'topic')).toBe(true);
  });
  it('represents every component exactly once in its original week, including duplicate titles and empty weeks', () => {
    const value = fixture(), original = structuredClone(value);
    const sections = weeklySections(value.modules[0]);
    expect(sections.map(s => s.week?.sourceId)).toEqual(['w1','w2','empty']);
    const ids = sections.flatMap(s => [...s.activities, ...s.resources].map(a => a.id));
    expect(ids.sort()).toEqual(value.modules[0].activities.map(a => a.id).sort());
    expect(new Set(ids).size).toBe(ids.length);
    expect(sections[0].resources.filter(a => a.title === 'Same source title')).toHaveLength(2);
    expect(sections[0].activities.find(a => a.id === 'paid-video')?.hours).toBe(.75);
    expect(sections[1].resources.find(a => a.id === 'missing')?.hours).toBeNull();
    expect(sections[0].activities.every(a => a.weekId === 'w1')).toBe(true);
    expect(value).toEqual(original);
  });

  it('shows full supporting resource names, verified-zero label and explicit missing hours', () => {
    const dom = document.createElement('div'); dom.innerHTML = weeklyTrainingPlanHtml(fixture());
    expect(dom.textContent).toContain('Supporting Resources (0h each)');
    expect(dom.textContent).toContain('Full recorded zero-video title');
    expect(dom.textContent).toContain('Full recorded zero-ppt title');
    expect(dom.textContent).toContain('Full recorded zero-podcast title');
    expect(dom.textContent?.match(/Same source title/g)).toHaveLength(2);
    expect(dom.textContent).toContain('Full recorded missing title [OTJ unavailable]');
    expect(dom.textContent).toContain('No recorded activities in this week.');
    const paid = Array.from(dom.querySelectorAll('tr')).find(r => r.textContent?.includes('Full recorded paid-video title'))!;
    expect(paid.children[1].textContent).toBe('45m');
    expect(dom.textContent).not.toContain('PRIVATE_DIAGNOSTIC');
  });

  it('dates only assignment and coaching and shows one Reviews Schedule without the obsolete signatory table', () => {
    const value = fixture();
    value.unassignedMeetings = [{id:'unassigned',templateId:'mc',title:'Coaching awaiting week verification',
      date:'2027-09-01',hours:1,scheduleKind:'projected',participants:null,reviewTypeCode:'mcm'}];
    const dom = document.createElement('div'); dom.innerHTML = weeklyTrainingPlanHtml(value);
    const rows = Array.from(dom.querySelectorAll('tr'));
    const row = (id: string) => rows.find(r => r.textContent?.includes(`Full recorded ${id} title`))!;
    for (const id of ['live','reading','quiz','paid-video']) expect(row(id).children[2].textContent).toBe('—');
    expect(row('assignment').children[2].textContent).toBe('Due: 07 Feb 2027');
    expect(row('coaching').children[2].textContent).toBe('07 Feb 2027 (projected)');
    expect(dom.textContent?.match(/Configured Progress Review/g)).toHaveLength(1);
    expect(dom.textContent).not.toContain('Must not repeat coaching here');
    expect(dom.textContent?.match(/Coaching awaiting week verification/g)).toHaveLength(1);
    expect(dom.textContent).toContain('139h');
    expect(dom.querySelectorAll('.tp-reviews-title')).toHaveLength(1);
    expect(dom.querySelector('.tp-meetings thead')?.textContent).toBe('MeetingPlanned DateOTJ Hours');
    expect(dom.querySelectorAll('.tp-meetings tbody tr')).toHaveLength(2);
    expect(dom.querySelector('.tp-reviews')).toBeNull();
    expect(dom.textContent).not.toContain('weekly placement unavailable');
    expect(dom.textContent).not.toContain('Required Signatories / Participants');
    expect(dom.textContent).not.toContain('These occurrences are preserved separately');
    const pdfPayload = payloadFor('training-plan', true); pdfPayload.template.presentationTheme = 'kbc-dfe-purple-v4';
    pdfPayload.data.activitiesOverview = value;
    const pdf = renderManagedDocument(pdfPayload.template, pdfPayload.data)!.output();
    expect(pdf.match(/\(Reviews Schedule\) Tj/g)).toHaveLength(1);
    expect(pdf).not.toContain('weekly placement unavailable');
    expect(pdf).not.toContain('Required Signatories');
  });

  it('does not repeat a captured review present in both meeting collections', () => {
    const value = fixture(), original = structuredClone(value);
    value.unclassifiedMeetings = [value.reviews[0]];
    const dom = document.createElement('div'); dom.innerHTML = weeklyTrainingPlanHtml(value);
    expect(dom.querySelectorAll('.tp-meetings tbody tr')).toHaveLength(1);
    expect(dom.textContent?.match(/Configured Progress Review/g)).toHaveLength(1);
    expect(value.reviews).toEqual(original.reviews);
    expect(value.totals).toEqual(original.totals);
  });

  it('preserves protected native editor data and renders all resource names in PDF', () => {
    const p = payloadFor('training-plan', true); p.template.presentationTheme = 'kbc-dfe-purple-v4';
    p.data.activitiesOverview = fixture();
    const page = documentPages(p.template.content).find(p => p.blocks.some(b => b.source === 'learning-plan'))!;
    const dom = document.createElement('div'); dom.innerHTML = pageHtml(page, p.template.content, p.data);
    expect(readPage(dom, page).find(b => b.source === 'learning-plan')).toEqual(page.blocks.find(b => b.source === 'learning-plan'));
    const pdf = renderManagedDocument(p.template, p.data)!.output();
    expect(pdf).toContain('Supporting Resources');
    expect(pdf).toContain('An empty actual week');
    expect(pdf).toContain('OTJ unavailable');
    expect(pdf).not.toContain('PRIVATE_DIAGNOSTIC');
  });

  it('keeps a very long resource list across pages without losing its last name or duplicate', () => {
    const p = payloadFor('training-plan', true); p.template.presentationTheme = 'kbc-dfe-purple-v4';
    const v = fixture();
    v.modules[0].activities = Array.from({length:220}, (_,i) => ({id:`res-${i}`, weekId:'w1',kind:'video',hours:0,
      title:`Recorded complete resource name ${i} with its important remaining descriptive words`}));
    p.data.activitiesOverview = v;
    const pdf = renderManagedDocument(p.template, p.data)!;
    expect(pdf.getNumberOfPages()).toBeGreaterThan(3);
    // Compact padding can move a word onto the next PDF text line. Compare
    // consecutive text operators rather than requiring one unsplit operator.
    const text = [...pdf.output().matchAll(/\(([^)]*)\) Tj/g)].map(m => m[1]).join(' ').replace(/\s+/g,' ');
    expect(text.includes('Recorded complete resource name 219 with its important remaining descriptive words')).toBe(true);
    expect(weeklySections(v.modules[0])[0].resources).toHaveLength(220);
  });
});
