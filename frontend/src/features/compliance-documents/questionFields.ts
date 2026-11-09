import seed from './seed.json';
import questionTypes from './question_types.json';
import { definitions, displayValue, substitute } from './fields';
import type { DocumentBlock, DocumentKey, DocumentQuestion, IssuedTemplate } from './types';

export function questionFields(block: DocumentBlock, key: DocumentKey): DocumentQuestion[] {
  if (block.fields) return block.fields;
  const result: DocumentQuestion[] = [];
  const append = (id: string, label: string, type: DocumentQuestion['type'], binding: DocumentQuestion['binding']) => result.push({
    id, label, type, helpText: '', required: false, condition: null, value: binding ? '' : block.value || '',
    options: type === 'radio' ? [{ id: 'Yes', label: 'Yes' }, { id: 'No', label: 'No' }] : [], defaultOptionIds: [], systemLinked: !!binding, binding,
  });
  const valueType = (value: string) => {
    const ref = value.match(/\{\{([A-Za-z0-9_.-]+)\}\}/)?.[1]; const format = ref ? definitions(key)[ref]?.format : undefined;
    return format === 'yesno' ? 'radio' : ['number', 'hours', 'weeks'].includes(format || '') ? 'number' : 'single_line';
  };
  if (block.type === 'native' && block.labels) for (const [name, label] of Object.entries(block.labels)) {
    const source = block.source!; const review = source.startsWith('review:');
    const type = (questionTypes as Record<string, Record<string, DocumentQuestion['type']>>)[source]?.[name] || (review ? 'radio' : 'number');
    append(`${block.id}--${name}`, label, type, { kind: review ? 'review' : 'cost', source, key: name });
  }
  else if (block.type === 'field') append(block.id, block.label || '', valueType(block.value || ''), /\{\{/.test(block.value || '') ? { kind: 'value', value: block.value } : null);
  else if (block.type === 'table' && block.columns?.length === 2 && block.rows?.length && block.rows.every((r) => /\{\{/.test(r[1]))) {
    const original = (seed.templates[key].blocks as DocumentBlock[]).find((b) => b.id === block.id);
    const used = new Set<string>();
    block.rows.forEach((row, i) => { const oldIndex = original?.rows?.findIndex((r) => r[1] === row[1]) ?? -1;
      let suffix = `row-${oldIndex < 0 ? i : oldIndex}`; if (used.has(suffix)) suffix += `-repeat-${i}`; used.add(suffix);
      append(`${block.id}--${suffix}`, row[0], valueType(row[1]), { kind: 'value', value: row[1] }); });
  }
  return result;
}

export function newQuestion(): DocumentQuestion {
  return { id: `question-${crypto.randomUUID()}`, label: 'New question', type: 'single_line', helpText: '', required: false,
    condition: null, options: [], value: '', defaultOptionIds: [], systemLinked: false, binding: null };
}

export function withQuestionFields(block: DocumentBlock, fields: DocumentQuestion[]): DocumentBlock {
  // Keep the original v1 label/value structures available to existing consumers.
  const labels = block.labels ? Object.fromEntries(fields.filter((f) => f.systemLinked).map((f) => [f.binding!.key!, f.label])) : undefined;
  return { ...block, fields, ...(labels ? { labels } : {}),
    ...(block.type === 'field' && fields[0] ? { label: fields[0].label } : {}) };
}

export function questionValue(field: DocumentQuestion, template: IssuedTemplate, data: Record<string, any>): unknown {
  const binding = field.binding;
  if (binding?.kind === 'review') return data.answers?.[binding.source!.slice(7)]?.[binding.key!];
  if (binding?.kind === 'cost') {
    const costs = data.costs || {}, key = binding.key!;
    return key.startsWith('cost-') ? costs.items?.[Number(key.slice(5))]?.price : costs[{ total: 'total', maximum: 'fundingBandMaximum', balance: 'balanceDue' }[key] || key];
  }
  if (binding?.kind === 'value') {
    const ref = binding.value?.match(/^\{\{([A-Za-z0-9_.-]+)\}\}$/)?.[1];
    return ref ? field.options.length ? template.fields[ref] : displayValue(template.fields[ref], definitions(template.content.documentKey)[ref],template.bindingVersion) : substitute(binding.value || '', template);
  }
  return field.options.length ? field.defaultOptionIds : substitute(field.value, template);
}

export function questionVisible(field: DocumentQuestion, preceding: DocumentQuestion[], template: IssuedTemplate, data: Record<string, any>): boolean {
  if (!field.condition) return true;
  const source = preceding.find((f) => f.id === field.condition!.field);
  if (!source || !questionVisible(source, preceding.slice(0, preceding.indexOf(source)), template, data)) return false;
  const raw = questionValue(source, template, data); const values = Array.isArray(raw) ? raw : [raw === true ? 'Yes' : raw === false ? 'No' : String(raw ?? '')];
  return field.condition.values.some((v) => values.includes(v));
}

export function questionProblems(fields: DocumentQuestion[]): string[] {
  const problems: string[] = [];
  fields.forEach((field, index) => {
    if (!field.label.trim() && field.type !== 'divider') problems.push('Provide a question label.');
    if (['dropdown', 'radio', 'checkbox'].includes(field.type) && (!field.options.length || field.options.some((o) => !o.label.trim()) || new Set(field.options.map((o) => o.label.trim().toLowerCase())).size !== field.options.length)) problems.push(`Correct the choices for ${field.label}.`);
    if (field.condition) {
      const source = fields.slice(0, index).find((f) => f.id === field.condition!.field);
      if (!source || !field.condition.values.length || field.condition.values.some((v) => !source.options.some((o) => o.id === v))) problems.push(`Correct the conditional rule for ${field.label}: choose a preceding field and its current options.`);
    }
  });
  return problems;
}
