import { describe, expect, it } from 'vitest';
import { compactModuleRows, compactTrainingPlanHtml } from '../compactTrainingPlan';
import type { ActivitiesOverview } from '../trainingPlanActivities';
import { trainingPlanActivitiesHtml } from '../trainingPlanActivities';
import { renderManagedDocument } from '../render';
import { payloadFor } from './fixtures';
import { documentPages, pageHtml, readPage } from '../pageDocument';
import { decorateBrandedContent } from '../brandedEditorTheme';

function fixture(): ActivitiesOverview {
  return {version:'activities-overview-v6',programme:'Isolated confirmed programme',startDate:'2027-02-01',endDate:'2028-01-31',
    deliveryResponsibility:'Kent Business College',deliverySchedule:'Existing recorded schedule',
    modules:[{academicModuleId:'academic',title:'Confirmed module',description:'Recorded source description',hours:138,
      startDate:'2027-02-01',endDate:'2027-06-20',coveredKsbs:[{code:'K1'}],
      weeks:[{sourceId:'w1',title:'L1: Actual topic'}, {sourceId:'w2',title:'L2: Actual exercise'}, {sourceId:'w3',title:'Empty recorded week'}],
      weekSummaries:[{id:'w1',title:'Actual topic',kind:'topic',hours:2.5,componentIds:['c1','c2'],assignmentComponentIds:[],dueDates:[]},
        {id:'w2',title:'Actual exercise',kind:'assignment',hours:8.5,componentIds:['c3'],assignmentComponentIds:['c3'],dueDates:['2027-02-19']},
        {id:'w3',title:'Empty recorded week',kind:'topic',hours:0,componentIds:[],assignmentComponentIds:[],dueDates:[]}],
      activities:[{id:'c1',kind:'video',title:'Detailed hidden resource',hours:0,weekId:'w1'},
        {id:'coach',kind:'meeting',title:'Configured coaching name',hours:1,date:'2027-02-20',weekId:'w2',reviewTypeCode:'mcm',scheduleKind:'projected'}]}],
    reviews:[{id:'progress',templateId:'pr',title:'Programme development checkpoint',date:'2027-04-01',hours:0,
      scheduleKind:'projected',reviewTypeCode:'progress_review',participants:'Learner + Coach'},
      {id:'wrong',templateId:'other',title:'Not a progress review',date:'2027-04-02',hours:1,scheduleKind:'projected',reviewTypeCode:'gateway_review',participants:null}],
    totals:{moduleHours:138,meetingHours:1,totalHours:139},warnings:[{code:'denied',message:'PRIVATE_DIAGNOSTIC'}],blockers:[]};
}
const domFor = (v: ActivitiesOverview) => { const dom=document.createElement('div'); dom.innerHTML=trainingPlanActivitiesHtml(v); return dom; };
const pdfFor = (v: ActivitiesOverview) => {const p=payloadFor('training-plan',true);p.template.presentationTheme='kbc-dfe-purple-v4';
  p.data.activitiesOverview=v;return renderManagedDocument(p.template,p.data)!;};

describe('new compact module Training Plan presentation', () => {
  it('renders one table per module, each real Week once, no individual Week or resource sections', () => {
    const v=fixture(), before=structuredClone(v), dom=domFor(v);
    decorateBrandedContent(dom);
    expect(dom.querySelectorAll('.tpc-activities')).toHaveLength(1);
    expect(dom.querySelectorAll('.tpc-activities tbody tr')).toHaveLength(4);
    expect(dom.querySelectorAll('.tp-week-card, .tp-resources')).toHaveLength(0);
    expect(dom.textContent).not.toMatch(/Week 1:|Supporting Resources|Detailed hidden resource|L1:/);
    expect(dom.textContent).toContain('Empty recorded week');
    expect(dom.textContent).not.toContain('PRIVATE_DIAGNOSTIC');
    expect(v).toEqual(before);
  });
  it('bolds just the Topic/Assignment prefixes and the entire coaching label', () => {
    const dom=domFor(fixture()), rows=dom.querySelectorAll('.tpc-activities tbody tr');
    expect(rows[0].querySelector('strong')?.textContent).toBe('Topic:');
    expect(rows[0].children[0].childNodes[1].textContent).toBe(' Actual topic');
    expect(rows[1].querySelector('strong')?.textContent).toBe('Assignment:');
    expect(rows[2].children[0].innerHTML).toBe('<strong>Monthly Coaching Meeting (MCS)</strong>');
    expect(rows[2].children[2].textContent).toBe('20 Feb 2027 (projected)');
  });
  it('uses captured display hours and dates without recomputing module or meeting totals', () => {
    const v=fixture(), dom=domFor(v), rows=dom.querySelectorAll('.tpc-activities tbody tr');
    expect(rows[0].children[1].textContent).toBe('2h 30m');
    expect(rows[0].children[2].textContent).toBe('—');
    expect(rows[1].children[1].textContent).toBe('8h 30m');
    expect(rows[1].children[2].textContent).toBe('19 Feb 2027');
    expect(dom.querySelector('.tpc-module-title')?.textContent).toContain('138h');
    expect(dom.querySelector('.tpc-totals')?.textContent).toContain('139h');
    v.modules[0].weekSummaries![0].hours=null;
    expect(domFor(v).querySelector('.tpc-activities tbody tr')?.children[1].textContent).toBe('—');
  });
  it('does not blindly classify every fourth week as an assignment', () => {
    const v=fixture();v.modules[0].weekSummaries!.push({id:'w4',kind:'topic',title:'Normal fourth topic',hours:0,
      componentIds:[],assignmentComponentIds:[],dueDates:[]});
    expect(compactModuleRows(v.modules[0]).at(-1)?.prefix).toBe('Topic:');
  });
  it('deduplicates real coaching and preserves chronological order relative to an assignment deadline', () => {
    const v=fixture(), m=v.modules[0];m.activities.push(structuredClone(m.activities[1]));
    let rows=compactModuleRows(m);
    expect(rows.map(r=>r.id)).toEqual(['w1','w2','coach','w3']);
    m.activities[1].date='2027-02-18';m.activities[2].date='2027-02-18';
    rows=compactModuleRows(m);
    expect(rows.map(r=>r.id)).toEqual(['w1','coach','w2','w3']);
  });
  it('only includes progress reviews in the final schedule with captured roles and projected distinction', () => {
    const v=fixture();v.reviews.push(structuredClone(v.reviews[0]));
    const dom=domFor(v), schedule=dom.querySelector('.tpc-reviews')!;
    expect(schedule.querySelectorAll('tbody tr')).toHaveLength(1);
    expect(schedule.textContent).toContain('Learner + Coach');
    expect(schedule.textContent).toContain('01 Apr 2027 (projected)');
    expect(schedule.textContent).not.toMatch(/Monthly|Not a progress review/);
    expect(dom.querySelector('.tpc-activities')?.textContent).not.toContain('Programme development checkpoint');
  });
  it('displays remaining real coaching once in the final module table without changing source relationships', () => {
    const v=fixture();const coach={id:'pending',templateId:'mc',title:'Real coaching',hours:1,date:'2027-07-01',
      scheduleKind:'projected' as const,reviewTypeCode:'mcm',participants:null};
    v.unassignedMeetings=[coach,coach];
    v.modules.push({...structuredClone(v.modules[0]),academicModuleId:'last',title:'Final module',activities:[]});
    const before=structuredClone(v), dom=domFor(v);
    expect(dom.querySelector('.tpc-pending-coaching')).toBeNull();
    expect(dom.textContent).not.toContain('Planned Monthly Coaching');
    expect(dom.querySelector('.tpc-reviews')?.textContent).not.toContain('Monthly Coaching');
    const tables=dom.querySelectorAll('.tpc-activities');
    expect(tables[0].querySelectorAll('tbody tr')).toHaveLength(4);
    expect(tables[1].querySelectorAll('tbody tr')).toHaveLength(4);
    const row=tables[1].querySelector('[data-week-or-meeting-id="pending"]')!;
    expect(row.querySelector('strong')?.textContent).toBe('Monthly Coaching Meeting (MCS)');
    expect(row.textContent).toContain('01 Jul 2027 (projected)');
    expect(v).toEqual(before);
    const pdf=pdfFor(v).output();
    expect(pdf).not.toContain('Planned Monthly Coaching');
  });
  it('renders through existing protected native editor and PDF entry points', () => {
    const p=payloadFor('training-plan',true);p.template.presentationTheme='kbc-dfe-purple-v4';p.data.activitiesOverview=fixture();
    const page=documentPages(p.template.content).find(p=>p.blocks.some(b=>b.source==='learning-plan'))!;
    const dom=document.createElement('div');dom.innerHTML=pageHtml(page,p.template.content,p.data);
    expect(dom.querySelector('[data-training-plan-summary]')).not.toBeNull();
    expect(readPage(dom,page).find(b=>b.source==='learning-plan')).toEqual(page.blocks.find(b=>b.source==='learning-plan'));
    const pdf=pdfFor(fixture()).output();
    expect(pdf).toContain('Actual topic');expect(pdf).toContain('Monthly Coaching Meeting');
    expect(pdf).not.toContain('Supporting Resources');
  });
  it('repeats activity headers across pages and retains the final Week without clipping', () => {
    const v=fixture();v.modules[0].weekSummaries=Array.from({length:150},(_,i)=>({id:'w'+i,kind:'topic' as const,
      title:`Actual curriculum topic ${i} with a full description that wraps naturally across the activity column`,
      hours:2.5,componentIds:['c'+i],assignmentComponentIds:[],dueDates:[]}));
    const pdf=pdfFor(v), text=pdf.output();
    expect(pdf.getNumberOfPages()).toBeGreaterThan(3);
    expect((text.match(/\(Learning Activity\) Tj/g) || []).length).toBeGreaterThan(2);
    expect(text).toContain('Actual curriculum topic 149');
    expect(compactModuleRows(v.modules[0]).filter(r=>!r.coaching)).toHaveLength(150);
  });
  it('does not upgrade the frozen v5 weekly renderer or mutate prior snapshots', () => {
    const v=fixture();v.version='activities-overview-v5';const before=structuredClone(v);
    expect(trainingPlanActivitiesHtml(v)).toContain('data-training-plan-weekly');
    expect(trainingPlanActivitiesHtml(v)).not.toContain('data-training-plan-summary');
    expect(v).toEqual(before);
    expect(compactTrainingPlanHtml(fixture())).toContain('data-training-plan-summary');
  });
});
