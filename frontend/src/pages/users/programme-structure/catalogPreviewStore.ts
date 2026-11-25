import type { CatalogRecord, Cohort, CurriculumModule, Delivery, Detail, Group, Ksb, PinnedCurriculum, Programme, Topic } from './catalogApi';
import type { EditorState } from './CatalogEditor';
import { moduleEnd, savedDeliveryEndOverride } from './catalogMath';
import { normalizeKsbFields, type KsbFields } from './ksbCsv';
import { cohortScheduleForSave, type CatalogValues } from './catalogValidation';

export type PreviewCatalogue = Detail[];
const current = (record?: CatalogRecord) => record && !record.archived_at;
const numeric = (input: unknown) => input == null || input === '' ? null : Number(input);

function snapshot(module: CurriculumModule, detail: Detail): PinnedCurriculum {
  const { name, weeks, books, lectures, reviews, weekly_topics } = module;
  return {
    ...module.current_revision!, name, weeks, books, lectures, reviews, weekly_topics,
    ksbs: detail.ksbs.filter(ksb => module.ksb_ids.includes(ksb.id)).map(({ id, code, description }) => ({ id, code, description })),
  };
}

function metadata(previous?: CatalogRecord): CatalogRecord {
  return { id: previous?.id ?? crypto.randomUUID(), revision: (previous?.revision ?? 0) + 1, archived_at: null, source_system: 'manual', external_id: '' };
}

function saveTemplate(values: CatalogValues, meta: CatalogRecord, previous?: CurriculumModule): CurriculumModule {
  const updates = { ...previous, ...values };
  const weekly_topics = values.weekly_topics === undefined ? previous?.weekly_topics ?? []
    : (values.weekly_topics as Topic[]).map(topic => ({
      ...topic, id: previous?.weekly_topics.find(row => row.week === topic.week)?.id ?? crypto.randomUUID(),
    })).sort((left, right) => left.week - right.week);
  return {
    ...updates, ...meta, weeks: Number(updates.weeks), weekly_topics,
    books: numeric(updates.books), reviews: numeric(updates.reviews), lectures: numeric(updates.lectures),
    current_revision: { id: crypto.randomUUID(), number: meta.revision },
  } as CurriculumModule;
}

const replace = <Row extends CatalogRecord>(rows: Row[], row: Row) => rows.some(value => value.id === row.id)
  ? rows.map(value => value.id === row.id ? row : value) : [...rows, row];

function checkedCode(value: unknown, rows: Pick<Ksb, 'id' | 'code'>[], id: string, max: number) {
  const code = String(value ?? '').trim().toUpperCase();
  if (!code || code.length > max) throw new Error(`Enter a code of no more than ${max} characters.`);
  if (rows.some(row => row.id !== id && row.code === code)) throw new Error('This code is already in use.');
  return code;
}

export function savePreview(catalogue: PreviewCatalogue, programmeId: string, editor: EditorState, values: CatalogValues) {
  const meta = metadata(editor.record);
  const existing = catalogue.find(row => row.programme.id === programmeId);
  if (editor.resource === 'programme') {
    const code = checkedCode(values.code, catalogue.map(row => row.programme), meta.id, 80);
    const programme = { ...values, ...meta, code, level: Number(values.level) } as Programme;
    const next = existing && editor.record ? { ...existing, programme }
      : { programme, cohorts: [], groups: [], modules: [], ksbs: [], deliveries: [] };
    return { catalogue: editor.record ? catalogue.map(row => row.programme.id === programmeId ? next : row) : [...catalogue, next], record: programme };
  }
  if (!existing || !current(existing.programme)) throw new Error('Choose a current programme.');
  const detail = { ...existing };
  let record: CatalogRecord;
  switch (editor.resource) {
    case 'cohorts': {
      const dates = cohortScheduleForSave(values, detail, editor.record as Cohort | undefined);
      const cohort = { ...dates, ...meta, name: String(values.name) };
      detail.cohorts = replace(detail.cohorts, cohort);
      record = cohort;
      break;
    }
    case 'groups': {
      const group = { coach_id: null, ...editor.record, ...values, ...meta, cohort_id: editor.parentId, weekday: Number(values.weekday) } as Group;
      if (!detail.cohorts.some(row => row.id === group.cohort_id && current(row))) throw new Error('Choose a current cohort.');
      detail.groups = replace(detail.groups, group);
      record = group;
      break;
    }
    case 'ksbs': {
      const fields = normalizeKsbFields(values.code, values.description);
      const code = checkedCode(fields.code, detail.ksbs, meta.id, 40);
      const ksb: Ksb = { ...meta, ...fields, code };
      detail.ksbs = replace(detail.ksbs, ksb);
      detail.modules = detail.modules.map(module => {
        if (!current(module) || !module.ksb_ids.includes(ksb.id)) return module;
        return { ...module, revision: module.revision + 1, current_revision: { id: crypto.randomUUID(), number: module.revision + 1 } };
      });
      record = ksb;
      break;
    }
    case 'modules': {
      const module = saveTemplate(values, meta, editor.record as CurriculumModule | undefined);
      detail.modules = replace(detail.modules, module);
      record = module;
      break;
    }
    case 'deliveries': {
      const prior = editor.record as Delivery | undefined;
      const template = values.new_template as CatalogValues | undefined;
      const module = template ? saveTemplate(template, metadata()) : detail.modules.find(row => row.id === values.module_id);
      const curriculum = prior && prior.curriculum.id === values.module_revision_id ? prior.curriculum : snapshot(module!, detail);
      if (template && module) detail.modules = [...detail.modules, module];
      const start_date = String(values.start_date ?? prior?.start_date ?? '');
      const end_date_override = 'end_date' in values ? values.end_date as string | null : savedDeliveryEndOverride(prior);
      const delivery: Delivery = {
        ...meta, group_id: prior?.group_id ?? editor.parentId!, module_id: module?.id ?? prior!.module_id, curriculum,
        start_date, end_date_override, end_date: end_date_override ?? moduleEnd(start_date, curriculum.weeks)!,
        tutor_id: 'tutor_id' in values ? values.tutor_id as string | null : prior?.tutor_id ?? null,
      };
      detail.deliveries = replace(detail.deliveries, delivery);
      record = delivery;
      break;
    }
  }
  return { catalogue: catalogue.map(row => row.programme.id === programmeId ? detail : row), record };
}

export function importPreviewKsbs(catalogue: PreviewCatalogue, programmeId: string, rows: KsbFields[]): PreviewCatalogue {
  const detail = catalogue.find(row => row.programme.id === programmeId && current(row.programme));
  if (!detail) throw new Error('Choose a current programme.');
  const codes = new Set(detail.ksbs.map(row => row.code));
  const imported = rows.map(({ code, description }) => {
    const fields = normalizeKsbFields(code, description);
    if (codes.has(fields.code)) throw new Error(`KSB code ${fields.code} is already in use.`);
    codes.add(fields.code);
    return { ...metadata(), ...fields };
  });
  return catalogue.map(row => row === detail ? { ...row, ksbs: [...row.ksbs, ...imported] } : row);
}

export function archivePreview(catalogue: PreviewCatalogue, programmeId: string, editor: EditorState) {
  const target = editor.record!;
  const archive = (row: CatalogRecord) => ({ ...row, archived_at: new Date().toISOString(), revision: row.revision + 1 });
  return catalogue.map(detail => {
    if (detail.programme.id !== programmeId) return detail;
    if (editor.resource === 'programme') return { ...detail, programme: { ...archive(detail.programme), status: 'archived' as const } as Programme };
    if (editor.resource === 'modules' && detail.deliveries.some(row => current(row) && row.module_id === target.id &&
      detail.groups.some(group => current(group) && group.id === row.group_id && detail.cohorts.some(cohort => current(cohort) && cohort.id === group.cohort_id)))) {
      throw new Error('Remove the active deliveries before removing this template.');
    }
    return { ...detail, [editor.resource]: detail[editor.resource].map(row => row.id === target.id ? { ...row, ...archive(row) } : row) };
  });
}
