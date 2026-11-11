import { describe, expect, it } from 'vitest';
import jsPDF from 'jspdf';
import { renderAgreementPdf } from '@/lib/apprenticeshipAgreementPdf';
import { renderIlrPdf } from '@/lib/ilrDocumentPdf';
import { renderTrainingPlanPdf } from '@/lib/trainingPlanPdf';
import { renderWrittenAgreementPdf } from '@/lib/writtenAgreementPdf';
import { buildReviewPdf } from '@/pages/learner/onboarding/reviews/reviewDocument';
import { LEARNER_ACKNOWLEDGEMENT, REVIEW_QUESTION_LABELS, ULN_PRIVACY_INTRO, ULN_PRIVACY_POINTS, ULN_PURPOSES } from '@/pages/learner/onboarding/reviews/questions';
import { renderManagedDocument } from '../render';
import { displayValue, reviewLabels, substitute } from '../fields';
import { contentFor, keys, payloadFor, versionFor } from './fixtures';
import type { DocumentKey } from '../types';

function render(key: DocumentKey, payload: any) {
  switch (key) {
    case 'apprenticeship-agreement': return renderAgreementPdf(payload);
    case 'ilr': return renderIlrPdf(payload);
    case 'training-plan': return renderTrainingPlanPdf(payload);
    case 'written-agreement': return renderWrittenAgreementPdf(payload);
    default: return buildReviewPdf(payload, REVIEW_QUESTION_LABELS);
  }
}
function stable(pdf: jsPDF) {
  pdf.setCreationDate(new Date('2030-01-01T00:00:00Z')); pdf.setFileId('A'.repeat(32));
  return pdf.output();
}

describe('original document output and versioned rendering', () => {
  it('renders an available Training Plan R3 with its revised wording and partial fresh signatures', () => {
    const value = payloadFor('training-plan', true);
    value.template.legacyEquivalent = false;
    value.data.documentRevision = { number: 3, signatureMethod: 'sign-again' };
    value.data.fullySigned = false;
    value.data.signatures.provider = { signed: true, name: 'R3 Provider Only', signedAt: '2030-01-02T10:00:00Z' };
    value.data.signatures.apprentice = { signed: false, name: '', signedAt: null };
    value.data.signatures.employer = { signed: false, name: '', signedAt: null };
    const before = structuredClone(value);
    const pdf = renderTrainingPlanPdf(value.data as any);
    expect(pdf.output()).toContain('R3 Provider Only');
    expect(pdf.output()).toContain('Approved changed wording for SYNTHETIC Learner.');
    expect(pdf.output('blob').type).toBe('application/pdf');
    expect(value).toEqual(before);
  });
  it.each(['apprenticeship-agreement', 'ilr', 'training-plan', 'written-agreement'] as const)('%s signed legacy snapshot keeps the original renderer, values and signature marks', key => {
    const { data } = payloadFor(key);
    const mark = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAKAAAAAyCAIAAABUA0cyAAAB2UlEQVR4nO2a247DIAxE41X//5eptKgRSgKYq53RnIcqahNiPIxx2koI4SC4/FkHQNZCgcGhwOBQYHAoMDgUGBwKDA4FBocCg0OBwaHA4FBgcCgwOJ/uK0UkHvD3KEAHn+pejglUiaZ3MQWOlo3qxleaGL/JatJYfhymyH8A5mH4Eji1b+Q8VmbKSUIlUddJSH4d/LrNWFw+AqxYZ9I0w7t99Z+m55yY5Fdu6moiXx1Mlb7w+p+Dc4hkF02aWauqKHnvFiJfFEMuksdLChkrjNAwpSaDPp52GcHEN5K56YainSo08S7ldTnZwQVremhkJL+kFhWVDVtSxXLKWza57X7yoz8GHdxaUUXh0SlFxUOf0ebgvmnfBZg11TOD+lRKSwXu2Ixdibq2yUrLXczUxNJXbpFym5yo1b1ErgxGObgJ2ml0RF+df0fZ11vw/maY1E4u6pXe5OBI6t2RRHQMcjlTGktujDy96hVm7RHY8BuAiY8uYaCPGx/ElsrSHhe47B7Dp9Iq2773MHPwFPt2t6PmyQ3WAXjfg1vxIy0SHw+7L6XFdzBdu1XgnfaltEtBaBRJAf7xHRwKDA4FBocCg0OBwaHA4FBgcCgwOBQYHAoMDgUGhwIf2HwBUvYjiNlMxu4AAAAASUVORK5CYII=';
    data.particulars.apprenticeName = 'Saved original learner';
    data.learnerDetails.givenNames = 'Saved original'; data.learnerDetails.familyName = 'learner';
    data.programme.apprenticeName = 'Saved original learner';
    for (const party of Object.keys(data.signatures)) {
      data.signatures[party] = { ...data.signatures[party], signed: true, name: 'Saved historical signer', signedAt: '2030-01-02T10:00:00Z' };
      data.marks[party] = mark;
    }
    data.documentRevision = { number: 1, signatureMethod: 'original' };
    const before = structuredClone(data);
    const output = stable(render(key, data));
    expect(output).toBe(stable(render(key, { ...data, template: undefined })));
    expect(output).toContain('Saved historical signer');
    expect(output).toContain('/Subtype /Image');
    expect(data).toEqual(before);
  });
  it.each(keys)('%s unchanged publication exactly retains the original PDF', (key) => {
    const { data } = payloadFor(key);
    expect(stable(render(key, data))).toBe(stable(render(key, { ...data, template: undefined })));
  });
  it.each(keys)('%s edited wording reaches its existing PDF entry point', (key) => {
    const { data } = payloadFor(key, true);
    const pdf = render(key, data).output();
    expect(pdf).toContain('Approved changed wording for SYNTHETIC Learner.');
    expect(pdf).toContain('SYNTHETIC Learner');
    expect(pdf).not.toContain('{{learner.fullName}}');
  });
  it('keeps the full original ULN notice and declaration in editable seed blocks', () => {
    const text = JSON.stringify(contentFor('review-uln').blocks);
    for (const original of [ULN_PRIVACY_INTRO, ...ULN_PRIVACY_POINTS, ...ULN_PURPOSES, LEARNER_ACKNOWLEDGEMENT]) expect(text).toContain(JSON.stringify(original).slice(1, -1));
  });
  it('paginates long paragraphs, wide tables and explicit page breaks', () => {
    const template = versionFor('ilr', true);
    template.content.blocks = [
      { id: 'paragraph', type: 'paragraph', text: 'Long safe content for page wrapping. '.repeat(600) },
      { id: 'page', type: 'page-break' },
      { id: 'table', type: 'table', columns: ['Column A', 'Column B'], rows: Array.from({ length: 80 }, (_, i) => [`Row ${i}`, 'Wrapped table content. '.repeat(10)]) },
    ];
    const pdf = renderManagedDocument(template, {})!;
    expect(pdf.getNumberOfPages()).toBeGreaterThan(6);
    expect(pdf.output()).toContain('Row 79');
  });
  it('keeps saved native review answers literal when they contain token-like text', () => {
    const value = payloadFor('review-health-safety', true);
    const block = value.template.content.blocks.find((b) => b.labels)!;
    const key = Object.keys(block.labels!)[0];
    value.data.answers.healthSafetyVetting = { [key]: '{{learner.fullName}}' };
    const pdf = render('review-health-safety', value.data).output();
    expect(pdf).toContain('{{learner.fullName}}');
    expect(value.data.answers.healthSafetyVetting[key]).toBe('{{learner.fullName}}');
  });
  it('renders changed review wording without changing the saved answer identifier', () => {
    const value = payloadFor('review-health-safety', true);
    const block = value.template.content.blocks.find((b) => b.labels)!;
    const key = Object.keys(block.labels!)[0]; block.labels![key] = 'Approved synthetic question';
    value.data.answers.healthSafetyVetting = { [key]: 'Yes' };
    const pdf = render('review-health-safety', value.data).output();
    expect(pdf).toContain('Approved synthetic question'); expect(pdf).toContain('Yes');
    expect(Object.keys(value.data.answers.healthSafetyVetting)).toEqual([key]);
  });
  it('preserves an old version when another version is edited', () => {
    const old = payloadFor('ilr', true); const before = stable(render('ilr', old.data));
    const next = structuredClone(old); next.template.content.title = 'Later version'; next.template.number = 2;
    next.template.content.blocks.push({ id: 'later', type: 'paragraph', text: 'Later publication only' });
    expect(stable(render('ilr', old.data))).toBe(before);
    expect(render('ilr', old.data).output()).not.toContain('Later publication only');
  });
  it('uses existing marks and signing parties without a new signature system', () => {
    const value = payloadFor('ilr', true);
    value.data.signatures.learner = { signed: true, name: 'Original Signer', signedAt: '2030-01-02T10:00:00Z' };
    value.data.marks.learner = 'data:image/png;corrupt-mark';
    const pdf = render('ilr', value.data).output();
    expect(pdf).toContain('Original Signer'); expect(pdf).toContain('02/01/2030');
    expect(value.data.marks.learner).toBe('data:image/png;corrupt-mark');
  });
  it('formats controlled date/yes-no values and never recursively interprets learner text', () => {
    expect(displayValue('2030-01-02', { key: 'x', group: 'x', label: 'x', format: 'date' })).toBe('02/01/2030');
    expect(displayValue(false)).toBe('Yes [ ]   No [X]');
    const template = versionFor('ilr'); template.fields['learner.fullName'] = '{{programme.name}}';
    expect(substitute('Hello {{learner.fullName}}', template)).toBe('Hello {{programme.name}}');
    expect(reviewLabels(versionFor('written-agreement'))).toEqual({});
  });
});
