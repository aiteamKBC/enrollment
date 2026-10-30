import type { ImportedReviewField, ImportedReviewSection } from '@/api/reviewHistory';
import type { ReviewFieldDefinition, ReviewSectionDefinition } from '@/api/reviewInstances';

export const keyOf = (value: string) => value.toLowerCase().replace(/[^a-z0-9]/g, '');
export const record = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;

const labels: Record<string, string> = {
  programmestartdate: 'Programme Start Date', programmeplannedenddate: 'Programme Planned End Date',
  learningplannedenddate: 'Learning Planned End Date', apprenticeshipenddate: 'Apprenticeship End Date',
  programmestatus: 'Programme Status', plannedstartdatetime: 'Planned Start Date and Time',
  plannedenddatetime: 'Planned End Date and Time', currentraglevel: 'Current RAG Level',
};

export function displayLabel(value: string): string {
  const label = value.trim().replace(/:$/, '');
  if (labels[keyOf(label)]) return labels[keyOf(label)];
  const spaced = label.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/[_-]+/g, ' ');
  const sentence = spaced === spaced.toUpperCase() && spaced.length > 6 ? spaced.toLowerCase() : spaced;
  return sentence.charAt(0).toUpperCase() + sentence.slice(1);
}

export function isTechnical(label: string): boolean {
  // Explicit metadata only; numeric answers, business codes and assessment levels remain visible.
  return /^(id|.*Id|.*_id)$/.test(label) || /^(id|aptemreviewid|reviewid|learnerid|ownerid|eventkey|formeventkey|sourcekey|source|readonly|hasprevprogress|haspreprogress|progresstype|fundmodel|programtype|color|serializationmetadata)$/i.test(keyOf(label));
}

export function isEmpty(value: unknown): boolean {
  return value == null || (typeof value === 'string' && (!value.trim() || value === 'EMPTY_STRING'))
    || (Array.isArray(value) && value.length === 0) || (record(value) !== null && Object.keys(value as object).length === 0);
}

/** Calendar components are intentionally formatted verbatim: no browser timezone conversion. */
export function calendarLabel(value: string, withTime = false): string | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$/.exec(value.trim());
  if (!match) return null;
  const [, year, month, day, hour, minute] = match;
  const y = Number(year), m = Number(month), d = Number(day);
  const leap = y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0);
  if (m < 1 || m > 12 || d < 1 || d > [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    || (hour && (Number(hour) > 23 || Number(minute) > 59))) return null;
  return `${day}/${month}/${year}${withTime && hour ? ` at ${hour}:${minute}` : ''}`;
}

export function decodeStructured(value: unknown): { value: unknown; malformed: boolean } {
  if (typeof value !== 'string' || !/^\s*(?:\{\s*["}]|\[\s*(?:[\[\{"\]\d-]|true\b|false\b|null\b))/.test(value)) return { value, malformed: false };
  try { return { value: JSON.parse(value), malformed: false }; }
  catch { return { value: null, malformed: true }; }
}

export function numberValue(value: unknown): number | null {
  if (typeof value !== 'number' && !(typeof value === 'string' && value.trim())) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}
const numeric = (item: Record<string, unknown>, ...keys: string[]) => {
  for (const key of keys) { const n = numberValue(item[key]); if (n !== null) return n; }
  return null;
};
export const percentage = (current: number | null, total: number | null) => current !== null && total !== null && total > 0 ? current / total * 100 : null;

export interface ImportedProgressMetric {
  percentage: number | null;
  targetPercentage: number | null;
  variancePercentage: number | null;
}

// Keep full precision in the presentation model; round only visible labels.
const progressMetric = (value: number | null, target: number | null): ImportedProgressMetric => ({
  percentage: value, targetPercentage: target,
  variancePercentage: value !== null && target !== null ? value - target : null,
});
const sourceMetric = (item: Record<string, unknown>) => progressMetric(
  percentage(numeric(item, 'current'), numeric(item, 'max')),
  percentage(numeric(item, 'target', 'minTarget'), numeric(item, 'max')),
);

export type ProgressCard =
  | ({ kind: 'activities'; completed: number | null; submitted: number | null; remaining: number | null; total: number | null; target: number | null } & ImportedProgressMetric)
  | ({ kind: 'standard'; title: string; current: number | null; target: number | null; max: number | null; status: 'Behind' | 'On track' | 'Ahead' | null } & ImportedProgressMetric)
  | ({ kind: 'hours'; minimum: number | null; planned: number | null; completed: number | null; forecast: number | null } & ImportedProgressMetric)
  | ({ kind: 'programme' } & ImportedProgressMetric)
  | { kind: 'timeline'; dates: { label: string; value: string }[] }
  | { kind: 'other'; value: unknown };

export interface ImportedProgressNormalizedData {
  /** Complete decoded capture, including metrics not selected for display. Never mutated. */
  readonly source: unknown;
  readonly cards: ProgressCard[];
}

export function normalizeImportedProgressData(raw: unknown): ImportedProgressNormalizedData {
  const decoded = decodeStructured(raw);
  if (decoded.malformed || isEmpty(decoded.value)) return { source: raw, cards: [] };
  const object = record(decoded.value);
  const items = Array.isArray(decoded.value) ? decoded.value : object?.progress && Array.isArray(object.progress) ? object.progress : object ? [object] : [];
  const cards = items.flatMap((value): ProgressCard[] => {
    const item = record(value);
    if (!item) return [{ kind: 'other', value }];
    const type = numberValue(item.progressType);
    if (type === 2 || ('completedCount' in item && 'totalCount' in item)) {
      const completed = numeric(item, 'completedCount');
      const submitted = numeric(item, 'submittedCount', 'submitted');
      const total = numeric(item, 'totalCount', 'max');
      // An absent submitted count is unknown, not zero. The source's remaining
      // activities include everything not completed. Counts support, but never
      // replace, the source current/max progress metric.
      const remaining = numeric(item, 'remainingCount') ?? (total !== null && completed !== null ? Math.max(0, total - completed - (submitted ?? 0)) : null);
      return [{ kind: 'activities', ...sourceMetric(item), completed, submitted, total, remaining, target: numeric(item, 'targetCount', 'target') }];
    }
    if (type === 4) {
      const current = numeric(item, 'current'), target = numeric(item, 'target', 'minTarget');
      return [{ kind: 'standard', ...sourceMetric(item), title: typeof item.title === 'string' ? item.title : 'Standard progress', current, target, max: numeric(item, 'max'),
        status: current === null || target === null ? null : current < target ? 'Behind' : current > target ? 'Ahead' : 'On track' }];
    }
    if (type === 6 || 'completedTime' in item) {
      // Aptem *Time metrics are minutes (57394 / 60 = 956.57h); plannedHours is hours.
      const hours = (key: string) => { const n = numeric(item, key); return n === null ? null : n / 60; };
      const completed = hours('completedTime') ?? hours('current'), planned = numeric(item, 'plannedHours');
      return [{ kind: 'hours', completed, planned, minimum: hours('minimumRequiredTime'), forecast: hours('forecastTime'),
        ...progressMetric(percentage(completed, planned), percentage(numeric(item, 'target'), numeric(item, 'max'))) }];
    }
    if (type === 7 || 'startDate' in item) {
      const dates = [['Programme start', 'startDate'], ['Review snapshot', 'currentDate'], ['Gateway / planned end', 'plannedEndDate'], ['Apprenticeship end', 'expectedEndDate']]
        .flatMap(([label, key]) => typeof item[key] === 'string' && calendarLabel(item[key] as string) ? [{ label, value: item[key] as string }] : []);
      return [{ kind: 'programme', ...sourceMetric(item) }, { kind: 'timeline', dates }];
    }
    return [{ kind: 'other', value: item }];
  });
  return { source: decoded.value, cards };
}

/** Compatibility for consumers of the complete metric list, independent of UI selection. */
export function normalizeImportedProgress(raw: unknown): ProgressCard[] {
  return normalizeImportedProgressData(raw).cards;
}

export type AptemHistoricalProgressPresentation = Exclude<ProgressCard, { kind: 'programme' }>[];

/** The historical web Review selects Activities, Standard, Timeline and OTJ.
 * Type-7 numeric progress remains in the normalized model for other consumers.
 * The same source shapes use this presentation across PR, Skills Radar and MCM.
 */
export function aptemHistoricalProgressPresentation(data: ImportedProgressNormalizedData): AptemHistoricalProgressPresentation {
  const order = { activities: 0, standard: 1, timeline: 2, hours: 3, other: 4 };
  return data.cards.filter((card): card is AptemHistoricalProgressPresentation[number] => card.kind !== 'programme')
    .sort((a, b) => order[a.kind] - order[b.kind]);
}

export interface PresentationField extends ImportedReviewField { required?: boolean; fieldType?: string; preserveEmpty?: boolean }
export interface PresentationSection { name: string; fields: PresentationField[]; tables: unknown[][][]; rawText: string; historicalPresentation?: boolean; preserveRawText?: boolean }

export function hasImportedProgress(sections: ReviewSectionDefinition[]): boolean {
  return sections.some(section => section.enabled && /learning\s*progress/i.test(section.title) && section.fields.some(field =>
    keyOf(field.title) === 'progress' && normalizeImportedProgress(field.answer).some(card =>
      card.kind === 'activities' ? card.percentage !== null || (card.completed !== null && card.total !== null)
        : card.kind === 'standard' || card.kind === 'programme' ? card.percentage !== null
          : card.kind === 'hours' ? card.completed !== null
            : card.kind === 'timeline' && card.dates.length > 0,
    ),
  ));
}

export function adaptHistorySection(section: ImportedReviewSection): PresentationSection {
  return { name: section.name, fields: section.fields.map(field => ({ ...field,
    ...(Object.prototype.hasOwnProperty.call(field, 'displayValue') ? { value: field.displayValue } : {}),
  })), tables: section.tables.map(table => table.rows || []), rawText: section.rawText, historicalPresentation: section.historicalPresentation, preserveRawText: section.preserveRawText };
}

export function adaptDefinitionSection(section: ReviewSectionDefinition, answers: Record<string, unknown>): PresentationSection {
  const fields: PresentationField[] = [], tables: unknown[][][] = [];
  let rawText = '';
  section.fields.forEach((field: ReviewFieldDefinition) => {
    const config = field.configuration;
    if (config.importedTable) {
      const rows = decodeStructured(config.importedTable).value;
      if (Array.isArray(rows)) tables.push(rows);
    } else if (field.id.startsWith('aptem-text:')) {
      rawText = typeof config.description === 'string' ? config.description : '';
    } else {
      const historicalInstruction = section.historicalPresentation && field.fieldType === 'title_description';
      fields.push({ label: field.title, value: Object.prototype.hasOwnProperty.call(answers, field.id) ? answers[field.id] : field.answer ?? (field.fieldType === 'title_description' && !historicalInstruction ? config.description : undefined),
        description: field.fieldType === 'title_description' && !historicalInstruction ? undefined : typeof config.description === 'string' ? config.description : undefined,
        required: field.required, fieldType: field.fieldType, preserveEmpty: config.preserveEmpty === true });
    }
  });
  return { name: section.title, fields, tables, rawText, historicalPresentation: section.historicalPresentation, preserveRawText: section.preserveRawText };
}
