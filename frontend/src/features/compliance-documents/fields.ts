import data from './seed.json';
import type { DocumentKey, FieldDefinition, IssuedTemplate } from './types';

export function definitions(key: DocumentKey): Record<string, FieldDefinition> { return data.fields[key] as Record<string, FieldDefinition>; }
export function displayValue(raw: unknown, definition?: FieldDefinition, bindingVersion = 1): string {
  if (raw === null || raw === undefined || raw === '') return definition?.fallback || '';
  if (definition?.format === 'yesno' || typeof raw === 'boolean') {
    if(bindingVersion>=2 && ![true,false,'Yes','No'].includes(raw as boolean|string))return String(raw);
    return raw === true || raw === 'Yes' ? 'Yes [X]   No [ ]' : 'Yes [ ]   No [X]';
  }
  if (definition?.format === 'date') {
    const match = String(raw).match(/^(\d{4})-(\d{2})-(\d{2})/); return match ? `${match[3]}/${match[2]}/${match[1]}` : String(raw);
  }
  if (definition?.format === 'weeks') return `${raw} weeks`;
  if (definition?.format === 'hours') return `${raw} hours`;
  return String(raw);
}
export function substitute(text: string, template: IssuedTemplate): string {
  const fields = definitions(template.content.documentKey);
  return text.replace(/\{\{([A-Za-z0-9_.-]+)\}\}/g, (_, key: string) => displayValue(template.fields[key], fields[key],template.bindingVersion));
}
export function reviewLabels(template?: IssuedTemplate, section?: string): Record<string, string> {
  return Object.assign({}, ...(template?.content.blocks.filter((b) => b.source?.startsWith('review:') && (!section || b.source === `review:${section}`)).map((b) => b.labels || {}) ?? []));
}
export function reviewQuestionLabel(template: IssuedTemplate | undefined, section: string, key: string, fallback: string): string {
  if (!template || template.legacyEquivalent) return fallback;
  return substitute(reviewLabels(template, section)[key] || fallback, template);
}
