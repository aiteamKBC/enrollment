import { prefetchAssessmentSecurity } from '@/features/programme-suitability/api';

export type CatalogRecord = {
  id: string;
  revision: number;
  archived_at: string | null;
  source_system: string;
  external_id: string;
};

export type Programme = CatalogRecord & {
  name: string; code: string; level: number; description: string; status: 'draft' | 'active' | 'archived';
  total_cost: string; epa_cost: string; epao: string;
  funding_band_maximum: string | null; lecture_hours: string;
};
export type ProgrammeCounts = { cohorts: number; groups: number; scheduled_modules: number };
export type ProgrammeListRecord = Programme & { counts?: ProgrammeCounts };
export type Cohort = CatalogRecord & {
  name: string; start_date: string; training_end_date: string; duration_days: number;
  epa_period: number; epa_unit: string; apprenticeship_end_date: string;
  training_duration_months: number | null; apprenticeship_duration_months: number | null;
  automatic_training_end_date: string; training_end_override: string | null; apprenticeship_end_override: string | null;
};
export type Group = CatalogRecord & {
  cohort_id: string; name: string; coach_id: string | null; weekday: number; start_time: string; end_time: string;
};
export type Topic = { id?: string; week: number; content: string };
export type ModuleRevisionRef = { id: string; number: number };
export type KsbMappingWeight = {
  total_weight: string; mapping_count: number;
  hard_count: number; soft_count: number; possible_count: number;
  hard_weight: string; soft_weight: string; possible_weight: string;
  main_count: number; secondary_count: number;
  main_weight: string; secondary_weight: string;
};
export type WeightedKsb = Pick<Ksb, 'id' | 'code' | 'description'> & { mapping_weight?: KsbMappingWeight };
export type PinnedCurriculum = ModuleRevisionRef & {
  name: string; weeks: number; books: number | null; reviews: number | null; lectures: number | null;
  ksbs: WeightedKsb[]; weekly_topics: Topic[];
};
export type CurriculumModule = CatalogRecord & {
  name: string; weeks: number; books: number | null; reviews: number | null; lectures: number | null;
  ksb_ids: string[]; weekly_topics: Topic[];
  ksb_weights?: Record<string, KsbMappingWeight>;
  current_revision: ModuleRevisionRef | null;
};
export type Delivery = CatalogRecord & {
  group_id: string; module_id: string; start_date: string; end_date: string; tutor_id: string | null;
  end_date_override?: string | null;
  curriculum: PinnedCurriculum;
};
export type Ksb = CatalogRecord & {
  code: string; description: string; kind?: string; standard?: string; standard_version?: string;
};
export type Detail = {
  programme: Programme; cohorts: Cohort[]; groups: Group[]; modules: CurriculumModule[]; ksbs: Ksb[]; deliveries: Delivery[];
};
export type Resource = Exclude<keyof Detail, 'programme'>;
export type Staff = { id: string; name: string; email: string };
export type StaffOptions = { coaches: Staff[]; tutors: Staff[] };
export type Pricing = {
  total_cost: string; base_cost: string; total_training: string; training_hours: string | null;
  allocations: Record<'kbc_training' | 'distance_training' | 'assessment' | 'materials' | 'epa', string>;
  counts: Record<'lectures' | 'reviews' | 'books', number | null>;
  rates: Record<'training_hour' | 'review' | 'book', string | null>;
};

const base = '/enrolment_api/programme-structure/catalog/';
export const programmesPath = 'programmes/';
export const programmePath = (id: string) => `programmes/${id}/`;

export class CatalogRequestError extends Error {
  readonly code?: string;

  constructor(message: string, code?: string) {
    super(message);
    this.code = code;
  }
}

function fieldMessages(value: unknown, path = ''): string[] {
  if (typeof value === 'string') return [path ? `${path}: ${value}` : value];
  if (Array.isArray(value)) return value.flatMap((item, index) => fieldMessages(item, typeof item === 'string' ? path : `${path} #${index + 1}`));
  if (value && typeof value === 'object') return Object.entries(value).flatMap(([key, item]) => {
    const label = key.replaceAll('_', ' ');
    return fieldMessages(item, path ? `${path} / ${label}` : label);
  });
  return [];
}

export async function catalogRequest<Result>(
  path: string,
  options: { method?: 'POST' | 'PATCH' | 'DELETE'; data?: object; signal?: AbortSignal; revision?: number } = {},
): Promise<Result> {
  const { method, data, signal, revision } = options;
  const headers = new Headers();
  if (method) {
    headers.set('Content-Type', 'application/json');
    headers.set('X-CSRFToken', (await prefetchAssessmentSecurity()).csrfToken);
  }
  if (revision !== undefined) headers.set('If-Match', `"${revision}"`);
  const response = await fetch(base + path, {
    credentials: 'include', cache: 'no-store', method, headers, signal,
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  if (response.status === 204) return undefined as Result;
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    if (!method && path === programmesPath && response.status === 404) {
      throw new CatalogRequestError('The Programme Catalogue API returned 404. Check that the backend is running the current version and that your account has Enrolment staff access.');
    }
    const details = fieldMessages(payload?.fields);
    throw new CatalogRequestError(details.join('\n') || payload?.error || (
      response.status === 403 ? 'Access was denied. Check your session and try again.'
        : `The catalogue request failed (${response.status}). Try again.`
    ), payload?.code);
  }
  if (!payload) throw new Error('The catalogue did not return valid data.');
  if (!method && path === programmesPath && !Array.isArray(payload.programmes)) {
    throw new Error('The catalogue response did not contain a programme list.');
  }
  return payload as Result;
}
