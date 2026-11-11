import { describe, expect, it } from 'vitest';
import { renderManagedDocument } from '../render';
import { newQuestion, questionFields, questionValue, questionVisible } from '../questionFields';
import { payloadFor, contentFor } from './fixtures';

describe('saved field structure and actual document rendering', () => {
  it('leaves unanswered new-document OTJ totals blank without changing saved zero or historical output', () => {
    const value = payloadFor('written-agreement', true);
    value.template.content.blocks = [value.template.content.blocks.find(b => b.source === 'delivery')!];
    value.data.delivery = {}; value.data.unansweredTemplate = true;
    expect(renderManagedDocument(value.template, value.data)!.output()).not.toContain('training: 0h');
    value.data.delivery.totalOtjHours = 0;
    expect(renderManagedDocument(value.template, value.data)!.output()).toContain('training: 0h');
    value.data.delivery = {}; delete value.data.unansweredTemplate;
    expect(renderManagedDocument(value.template, value.data)!.output()).toContain('training: 0h');
  });
  it('keeps legacy question text/types/bindings unchanged when adapting for the editor', () => {
    const block = contentFor('review-eligibility').blocks.find((b) => b.source === 'review:extendedIlr')!;
    const before = JSON.stringify(block); const fields = questionFields(block, 'review-eligibility');
    expect(fields[2].type).toBe('multi_line'); expect(fields[0].type).toBe('radio'); expect(JSON.stringify(block)).toBe(before);
  });
  it('preserves existing presentation repeats with unique card IDs', () => {
    const block = contentFor('apprenticeship-agreement').blocks.find((b) => b.type === 'table')!;
    block.rows!.push([...block.rows![0]]);
    const fields = questionFields(block, 'apprenticeship-agreement');
    expect(new Set(fields.map((f) => f.id)).size).toBe(fields.length);
    expect(fields.at(-1)!.binding).toEqual(fields[0].binding);
  });
  it('prints reordered questions using original answer keys, not their new array indexes', () => {
    const value = payloadFor('review-eligibility', true);
    const block = value.template.content.blocks.find((b) => b.source === 'review:extendedIlr')!;
    block.fields = questionFields(block, 'review-eligibility').reverse();
    block.fields[0].label = 'First displayed question'; block.fields[0].helpText = 'Help for {{learner.fullName}}';
    block.fields[1].label = 'Second displayed question';
    value.data.answers.extendedIlr = { minimumWage: 'Yes', fiftyPercentEngland: 'No' };
    const before = structuredClone(value.data.answers); const pdf = renderManagedDocument(value.template, value.data)!.output();
    expect(pdf.indexOf('First displayed question')).toBeLessThan(pdf.indexOf('Second displayed question'));
    expect(pdf).toContain('Help for SYNTHETIC Learner'); expect(pdf).toContain('[X] Yes'); expect(pdf).toContain('[X] No');
    expect(value.data.answers).toEqual(before);
  });
  it('renders all supported custom field types and stable selected choices', () => {
    const value = payloadFor('ilr', true);
    value.template.content.blocks = [{ id: 'custom', type: 'questions', fields:
      ['single_line', 'multi_line', 'number', 'dropdown', 'radio', 'checkbox', 'heading', 'description', 'divider'].map((type, index) => ({
        ...newQuestion(), id: `field-${index}`, label: `Field label ${index}`, type: type as any,
        value: type === 'number' ? '42' : 'Configured document value',
        options: ['dropdown', 'radio', 'checkbox'].includes(type) ? [{ id: 'selected-option', label: 'Selected choice' }] : [],
        defaultOptionIds: ['dropdown', 'radio', 'checkbox'].includes(type) ? ['selected-option'] : [],
      })) }];
    const pdf = renderManagedDocument(value.template, value.data)!.output();
    for (let i = 0; i < 8; i++) expect(pdf).toContain(`Field label ${i}`);
    expect(pdf).toContain('[X] Selected choice'); expect(pdf).toContain('42');
  });
  it('honours conditions and hidden controllers using stable IDs after labels change', () => {
    const value = payloadFor('ilr', true);
    const first = { ...newQuestion(), id: 'first', label: 'Controller', type: 'radio' as const,
      options: [{ id: 'yes', label: 'Renamed yes' }, { id: 'no', label: 'Renamed no' }], defaultOptionIds: ['no'] };
    const second = { ...newQuestion(), id: 'second', label: 'Only shown for No', condition: { field: 'first', values: ['no'] } };
    value.template.content.blocks = [{ id: 'custom', type: 'questions', fields: [first, second] }];
    expect(renderManagedDocument(value.template, value.data)!.output()).toContain('Only shown for No');
    first.defaultOptionIds = ['yes'];
    expect(renderManagedDocument(value.template, value.data)!.output()).not.toContain('Only shown for No');
    expect(questionVisible(second, [first], value.template, value.data)).toBe(false);
  });
  it('keeps cost values and totals on their original source keys after display reordering', () => {
    const value = payloadFor('written-agreement', true);
    const block = value.template.content.blocks.find((b) => b.source === 'costs')!;
    block.fields = questionFields(block, 'written-agreement').reverse();
    value.data.costs = { items: [{ price: 123 }], total: 456, fundingBandMaximum: 789, balanceDue: 333 };
    const before = structuredClone(value.data.costs); const fields = block.fields;
    expect(questionValue(fields.find((f) => f.binding?.key === 'cost-0')!, value.template, value.data)).toBe(123);
    expect(questionValue(fields.find((f) => f.binding?.key === 'total')!, value.template, value.data)).toBe(456);
    expect(renderManagedDocument(value.template, value.data)!.output()).toContain('GBP 456'); expect(value.data.costs).toEqual(before);
  });
});
