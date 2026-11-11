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
