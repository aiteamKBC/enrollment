import type { LearnerDetail } from '@/api/learnerDetail';
import type { MonthlyAssignment } from '@/api/monthlyAssignment';

export function monthlyPresentation(title: string, answer: string, learned: string, businessImpact: string,
  monthly: MonthlyAssignment, detail: LearnerDetail, question: string) {
  const sections = [
    { title: 'MCM Meeting', body: `Assignment: ${title}\nSubmission month: ${monthly.month}\n\nAssignment question\n${question}\n\nAssignment answer\n${answer}` },
    { title: 'Learning and understanding', body: `I learned\n${learned}\n\nI understood\n${monthly.understood}\n\nSkills gained\n${monthly.gainedSkills}` },
    { title: 'Evidence and KSBs', body: [...monthly.evidence.map(e => `${e.name}${e.url ? `\n${e.url}` : ''}${e.points ? `\nAnswer points: ${e.points}` : ''}`), ...monthly.claims.map(c => `${c.code}: ${c.explanation}\nSupporting evidence: ${c.evidenceIds.map(id => monthly.evidence.find(e => e.id === id)?.name || id).join(', ')}`)].join('\n\n') || 'No evidence or KSB claims recorded.' },
    { title: 'Full-month reflection', body: `LMS reflection\n${monthly.lmsReflection}\n\nAdditional activities\n${monthly.extraActivities || 'None recorded'}\n\nHow the learning fits together\n${monthly.integratedReflection}` },
    { title: 'Impact and employer benefit', body: `Career\n${monthly.careerImpact}\n\nJob performance\n${monthly.jobImpact}\n\nEmployer performance\n${monthly.employerImpact}\n\nBusiness outcomes\n${businessImpact}` },
    { title: 'Action plan and EPA', body: `Next-month action plan\n${monthly.actionPlan}\n\nEPA preparation\n${monthly.epaPreparedness}` },
  ];
  const activities = (detail.activityFeed || []).filter(a => a.at.slice(0, 7) === monthly.month);
  sections.splice(1, 0, { title: 'Monthly activity overview', body: `${activities.length} recorded activities in ${monthly.month}.\n\n${activities.map(a => `${a.at.slice(0, 10)} | ${a.title} | ${a.detail || a.action}`).join('\n') || 'No LMS activities recorded for this month.'}` });
  for (const a of activities) {
    const records = a.kind === 'quiz' ? detail.quizAttempts || [] : a.kind === 'video' ? detail.videoProgress || [] : detail.componentProgress || [];
    const progress = records.find(p => p.submittedAt === a.at && (a.componentId ? p.componentId === a.componentId : a.kind === 'quiz' && 'quizId' in p && p.quizId === a.quizId));
    sections.push({ title: a.title, body: `Date: ${a.at.slice(0, 10)}\nResult: ${a.detail || a.action}\nTime recorded: ${progress?.reportedTime || progress?.timeTaken || 'Not recorded'}\nKSBs: ${progress?.ksbs?.join(', ') || 'Not recorded'}\n\nLearning and impact\n${progress?.feedback || 'Not recorded'}` });
  }
  const slides = sections.flatMap(section => {
    const chunks = section.body.match(/[\s\S]{1,11000}/g) || ['Not recorded'];
    return chunks.map((body, i) => ({ title: section.title + (i ? ' (continued)' : ''), body }));
  });
  if (slides.length > 200) throw new Error('This month needs more than 200 content sections. Please reduce the content before exporting.');
  return slides;
}

export function slideIssue(slide: { title: string; body: string }): string {
  if (!slide.title?.trim()) return 'Add a slide title.';
  if (!slide.body?.trim()) return 'Add content, regenerate the presentation from your completed answers, or remove this slide.';
  if (slide.body.trim().length > 12000) return 'Split this content across more slides (maximum 12,000 characters per slide).';
  return '';
}

/** Repair empty generated sections only; never replace existing slide text. */
export function fillEmptyPresentationSlides(monthly: MonthlyAssignment, learned: string, businessImpact: string): MonthlyAssignment {
  const join = (parts: [string, string][]) => parts.filter(([, value]) => value?.trim()).map(([label, value]) => `${label}\n${value.trim()}`).join('\n\n');
  const sources: Record<string, string> = {
    'Learning and understanding': join([['I learned', learned], ['I understood', monthly.understood], ['Skills gained', monthly.gainedSkills]]),
    'Full-month reflection': join([['LMS reflection', monthly.lmsReflection], ['Additional activities', monthly.extraActivities], ['How the learning fits together', monthly.integratedReflection]]),
    'Impact and employer benefit': join([['Career', monthly.careerImpact], ['Job performance', monthly.jobImpact], ['Employer performance', monthly.employerImpact], ['Business outcomes', businessImpact]]),
    'Action plan and EPA': join([['Next-month action plan', monthly.actionPlan], ['EPA preparation', monthly.epaPreparedness]]),
  };
  let changed = false;
  let remaining = 200 - monthly.slides.length;
  const slides = monthly.slides.flatMap(slide => {
    const key = slide.title.trim();
    const body = Object.hasOwn(sources, key) ? sources[key] : '';
    if (slide.body?.trim() || !body) return [slide];
    const chunks = body.match(/[\s\S]{1,11000}/g)!;
    if (chunks.length - 1 > remaining) return [slide];
    remaining -= chunks.length - 1;
    changed = true;
    return chunks.map((content, index) => ({ ...slide, title: slide.title + (index ? ' (continued)' : ''), body: content }));
  });
  return changed ? { ...monthly, slides, presentationReviewed: false, presentationToken: '' } : monthly;
}
