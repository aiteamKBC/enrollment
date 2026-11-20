import { describe, expect, it } from 'vitest';
import { renderManagedDocument } from '../render';
import { documentPages, pageHtml, readPage } from '../pageDocument';
import { trainingPlanActivitiesHtml, type ActivitiesOverview } from '../trainingPlanActivities';
import { payloadFor } from './fixtures';

function overview(): ActivitiesOverview {
  return { version: 'activities-overview-v1', programme: 'Isolated confirmed programme', startDate: '2027-01-01',
    endDate: '2028-01-31', deliveryResponsibility: 'Kent Business College', deliverySchedule: 'Recorded source timing only.',
    modules: [{ academicModuleId: 'confirmed', title: 'Confirmed source module', description: 'Recorded description',
      hours: 138, startDate: '2027-02-01', endDate: '2027-06-20', coveredKsbs: [{ code: 'K1' }, { code: 'S4' }],
      activities: [{ id: 'parent', kind: 'assignment', title: 'Recorded assignment', hours: 8, timing: 'End of week' },
        { id: 'child', kind: 'topic', title: 'Recorded child topic', hours: null },
        { id: 'zero', kind: 'topic', title: 'Recorded zero hours', hours: 0 }] }],
    reviews: [{ id: 'review:2027-03-01', templateId: 'review', title: 'Programme coaching', hours: 1,
      date: '2027-03-01', participants: null, scheduleKind: 'projected' }],
    totals: { moduleHours: 138, meetingHours: 1, totalHours: 139 }, warnings: [], blockers: [] };
}

describe('new official Training Plan Activities Overview', () => {
  it('keeps diagnostics outside v4 PDF and document HTML without changing data or historical rendering', () => {
    const value = overview(); value.version = 'activities-overview-v4';
    value.warnings = [{code:'select_denied', message:'LMS curriculum.groups: select denied. SECRET_DIAGNOSTIC'}];
    value.blockers = [{code:'missing_source', message:'SECRET_BLOCKER component could not be read'}];
    const before = structuredClone(value);
    const payload = payloadFor('training-plan', true);
    payload.template.presentationTheme = 'kbc-dfe-purple-v4'; payload.data.activitiesOverview = value;
    const pdf = renderManagedDocument(payload.template, payload.data)!.output();
    const html = trainingPlanActivitiesHtml(value);
    for (const output of [pdf, html]) {
      expect(output).not.toContain('SECRET_DIAGNOSTIC');
      expect(output).not.toContain('SECRET_BLOCKER');
      expect(output).not.toContain('select denied');
      expect(output).toContain('Reviews Schedule');
      expect(output).toContain('Plan Total');
    }
    expect(value).toEqual(before);
    expect(trainingPlanActivitiesHtml({...value, version:'activities-overview-v3'})).toContain('SECRET_DIAGNOSTIC');
  });
  it('v3 restores the complete schedule and original Participants / Format heading', () => {
    const value = overview(); value.version = 'activities-overview-v3';
    value.reviews.push({...value.reviews[0], id:'progress', title:'Progress Review'},
      {...value.reviews[0], id:'gateway', title:'Gateway Review'},
      {...value.reviews[0], id:'support', title:'Personal Support Plan'});
    const html = trainingPlanActivitiesHtml(value);
    expect(html).toContain('Participants / Format');
    expect(html).not.toContain('Required Signatories / Participants');
    for (const title of ['Programme coaching', 'Progress Review', 'Gateway Review', 'Personal Support Plan']) expect(html).toContain(title);
    expect(html).toContain('their OTJ hours count once');
    expect(html).toContain('139h');
    expect(html).not.toContain('lists Progress Reviews only');
  });
  it('v2 dates only assignments and coaching and labels verified required roles without implying signatures', () => {
    const value = overview(); value.version = 'activities-overview-v2';
    const module = value.modules[0];
    module.activities = [
      { id: 'v', kind: 'video', title: 'Recorded video', hours: 0, date: '2027-03-09' },
      { id: 'missing', kind: 'podcast', title: 'Missing OTJ', hours: null, timing: '2027-03-09' },
      { id: 'due', kind: 'assignment', title: 'Due assignment', hours: .5, date: '2027-03-14' },
      { id: 'coaching', kind: 'meeting', reviewTypeCode: 'mcm', title: 'Monthly Coaching', hours: 1,
        date: '2027-03-10', scheduleKind: 'projected' },
    ];
    value.reviews = [{ ...value.reviews[0], title: 'Configured Progress Review', participants: 'Advisor, Participant' }];
    const dom = document.createElement('div'); dom.innerHTML = trainingPlanActivitiesHtml(value);
    const row = (title: string) => Array.from(dom.querySelectorAll('tr')).find(r => r.textContent?.includes(title))!;
    expect(row('Recorded video').children[1].textContent).toBe('0h');
    expect(row('Recorded video').children[2].textContent).toBe('—');
    expect(row('Missing OTJ').children[1].textContent).toBe('—');
    expect(row('Due assignment').children[2].textContent).toBe('14 Mar 2027');
    expect(row('Due assignment').children[1].textContent).toBe('30m');
    expect(row('Monthly Coaching').children[2].textContent).toBe('10 Mar 2027 (projected)');
    expect(dom.textContent).toContain('Required Signatories / Participants');
    expect(row('Configured Progress Review').children[2].textContent).toBe('Advisor, Participant');
    expect(dom.textContent).toContain('Required roles do not indicate completed signatures.');
    expect(overview().version).toBe('activities-overview-v1');
  });
  it('renders the same confirmed snapshot in the PDF and protected editor data block', () => {
    const value = payloadFor('training-plan', true);
    value.template.presentationTheme = 'kbc-dfe-purple-v4';
    value.data.activitiesOverview = overview();
    const before = structuredClone(value);
    const pdf = renderManagedDocument(value.template, value.data)!;
    expect(pdf.output()).toContain('Confirmed source module');
    expect(pdf.output()).toContain('Reviews Schedule');
    const pages = documentPages(value.template.content);
    const learningPage = pages.find(p => p.blocks.some(b => b.source === 'learning-plan'))!;
    const html = pageHtml(learningPage, value.template.content, value.data);
    expect(html).toContain('Main Covered KSBs: K1, S4');
    expect(html).toContain('Recorded description');
    expect(html).toContain('Recorded child topic');
    const dom = document.createElement('div');
    dom.innerHTML = html;
    const read = readPage(dom, learningPage);
    expect(read.find(b => b.source === 'learning-plan')).toEqual(learningPage.blocks.find(b => b.source === 'learning-plan'));
    expect(value).toEqual(before);
  });

  it('never distributes assignment hours to topics or adds displayed activity hours to plan totals', () => {
    const html = trainingPlanActivitiesHtml(overview());
    const dom = document.createElement('div'); dom.innerHTML = html;
    const topic = Array.from(dom.querySelectorAll('tr')).find(r => r.textContent?.includes('Recorded child topic'))!;
    expect(topic.children[1].textContent).toBe('—');
    const zero = Array.from(dom.querySelectorAll('tr')).find(r => r.textContent?.includes('Recorded zero hours'))!;
    expect(zero.children[1].textContent).toBe('0h');
    expect(html).toContain('139h');
    expect(html).toContain('(projected)');
    expect(html).toContain('Not recorded / unavailable');
  });

  it('paginates long recorded activity lists without truncating their last row', () => {
    const value = payloadFor('training-plan', true);
    value.template.presentationTheme = 'kbc-dfe-purple-v4';
    const data = overview();
    data.modules[0].activities = Array.from({ length: 160 }, (_, i) => ({ id: String(i), kind: 'reading',
      title: `Recorded lengthy activity ${i}`, hours: 1 }));
    value.data.activitiesOverview = data;
    const pdf = renderManagedDocument(value.template, value.data)!;
    expect(pdf.getNumberOfPages()).toBeGreaterThan(3);
    expect(pdf.output()).toContain('Recorded lengthy activity 159');
  });

  it('does not add new activities to unmarked historical document snapshots', () => {
    const value = payloadFor('training-plan', true);
    value.template.presentationTheme = 'kbc-dfe-purple-v4';
    const pdf = renderManagedDocument(value.template, value.data)!;
    expect(pdf.output()).not.toContain('Main Covered KSBs');
    expect(value.data.activitiesOverview).toBeUndefined();
  });
});
